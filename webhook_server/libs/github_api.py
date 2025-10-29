from __future__ import annotations

import logging
import os
import shutil
import tempfile
from typing import Any

import requests
from github.Commit import Commit
from github.Repository import Repository

# GraphQL wrappers provide PyGithub-compatible interface
from starlette.datastructures import Headers

from webhook_server.libs.config import Config
from webhook_server.libs.exceptions import RepositoryNotFoundInConfigError
from webhook_server.libs.graphql.graphql_wrappers import CommitWrapper, PullRequestWrapper
from webhook_server.libs.graphql.unified_api import UnifiedGitHubAPI
from webhook_server.libs.handlers.check_run_handler import CheckRunHandler
from webhook_server.libs.handlers.issue_comment_handler import IssueCommentHandler
from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
from webhook_server.libs.handlers.pull_request_handler import PullRequestHandler
from webhook_server.libs.handlers.pull_request_review_handler import PullRequestReviewHandler
from webhook_server.libs.handlers.push_handler import PushHandler
from webhook_server.utils.constants import (
    BUILD_CONTAINER_STR,
    CAN_BE_MERGED_STR,
    CONVENTIONAL_TITLE_STR,
    PRE_COMMIT_STR,
    PYTHON_MODULE_INSTALL_STR,
    TOX_STR,
)
from webhook_server.utils.github_repository_settings import (
    get_repository_github_app_api,
)
from webhook_server.utils.helpers import (
    format_task_fields,
    get_api_with_highest_rate_limit,
    get_apis_and_tokes_from_config,
    get_github_repo_api,
    prepare_log_prefix,
)


class GithubWebhook:
    def __init__(self, hook_data: dict[Any, Any], headers: Headers, logger: logging.Logger) -> None:
        logger.name = "GithubWebhook"
        self.logger = logger
        self.hook_data = hook_data
        self.repository_name: str = hook_data["repository"]["name"]
        self.repository_full_name: str = hook_data["repository"]["full_name"]
        self.parent_committer: str = ""
        self.x_github_delivery: str = headers.get("X-GitHub-Delivery", "")
        self.github_event: str = headers["X-GitHub-Event"]
        self.config = Config(repository=self.repository_name, logger=self.logger)

        # Type annotations for conditionally assigned attributes
        self.repository: Repository
        self.repository_by_github_app: Repository
        self.token: str
        self.api_user: str
        self.last_commit: Commit | CommitWrapper
        self.last_committer: str
        self.current_pull_request_supported_retest: list[str] = []
        if not self.config.repository_data:
            raise RepositoryNotFoundInConfigError(f"Repository {self.repository_name} not found in config file")

        # Get config without .github-webhook-server.yaml data
        self._repo_data_from_config(repository_config={})
        github_api, self.token, self.api_user = get_api_with_highest_rate_limit(
            config=self.config, repository_name=self.repository_name
        )

        if github_api and self.token:
            self.repository = get_github_repo_api(github_app_api=github_api, repository=self.repository_full_name)
            # Initialize UnifiedGitHubAPI for GraphQL operations
            self.unified_api: UnifiedGitHubAPI = UnifiedGitHubAPI(token=self.token, logger=self.logger)
            # Once we have a repository, we can get the config from .github-webhook-server.yaml
            local_repository_config = self.config.repository_local_data(
                github_api=github_api, repository_full_name=self.repository_full_name
            )
            # Call _repo_data_from_config() again to update self args from .github-webhook-server.yaml
            self._repo_data_from_config(repository_config=local_repository_config)

        else:
            self.logger.error(f"Failed to get GitHub API and token for repository {self.repository_name}.")
            return

        self.log_prefix: str = self.prepare_log_prefix()

        github_app_api = get_repository_github_app_api(config_=self.config, repository_name=self.repository_full_name)

        if not github_app_api:
            self.logger.error(
                (
                    f"{self.log_prefix} not found by manage-repositories-app, "
                    "make sure the app installed (https://github.com/apps/manage-repositories-app)"
                ),
            )
            return

        self.repository_by_github_app = get_github_repo_api(
            github_app_api=github_app_api, repository=self.repository_full_name
        )

        if not (self.repository or self.repository_by_github_app):
            self.logger.error(f"{self.log_prefix} Failed to get repository.")
            return

        # Create unique temp directory to avoid collisions and security issues
        # Format: /tmp/tmp{random}/github-webhook-{repo_name}
        # This prevents predictable paths and ensures isolation between concurrent webhook handlers
        self.clone_repo_dir: str = tempfile.mkdtemp(prefix=f"github-webhook-{self.repository.name}-")
        # Populate auto-verified and auto-merged users from API users
        self.add_api_users_to_auto_verified_and_merged_users()

        self.current_pull_request_supported_retest = self._current_pull_request_supported_retest
        self.issue_url_for_welcome_msg: str = (
            "Report bugs in [Issues](https://github.com/myakove/github-webhook-server/issues)"
        )

    async def process(self) -> Any:
        event_log: str = f"Event type: {self.github_event}. event ID: {self.x_github_delivery}"
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'started')} "
            f"Starting webhook processing: {event_log}",
        )

        if self.github_event == "ping":
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                f"Processing ping event",
            )
            self.logger.debug(f"{self.log_prefix} {event_log}")
            self.logger.success(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} "
                f"Webhook processing completed successfully: ping event",
            )
            return {"status": requests.codes.ok, "message": "pong"}

        if self.github_event == "push":
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                f"Processing push event",
            )
            self.logger.debug(f"{self.log_prefix} {event_log}")
            await PushHandler(github_webhook=self).process_push_webhook_data()
            self.logger.success(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} "
                f"Webhook processing completed successfully: push event",
            )
            return None

        owner, repo = self.repository_full_name.split("/")

        # Optimization: For pull_request events, construct PullRequestWrapper directly from webhook data
        # This eliminates redundant API calls since webhook already contains complete PR data
        pull_request: PullRequestWrapper | None
        if self.github_event == "pull_request" and "pull_request" in self.hook_data:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                f"Initializing pull request from webhook payload",
            )
            pr_data = self.hook_data["pull_request"]

            # Construct PullRequestWrapper directly from webhook payload
            pull_request = PullRequestWrapper(
                data=pr_data,  # GraphQL-style data from webhook
                owner=owner,
                repo_name=repo,
                webhook_data=pr_data,  # Ensures accurate user.login for bots
            )

            # Extract last commit from webhook data (eliminates second API call)
            head_sha = pr_data["head"]["sha"]
            # GitHub webhook provides commit data in head object
            # CommitWrapper expects committer.user structure, so wrap the user data properly
            head_user = pr_data["head"].get("user", {})
            self.last_commit = CommitWrapper({
                "oid": head_sha,
                # Webhook doesn't provide full commit metadata, but we have enough for most operations
                # If more commit details are needed, they can be fetched later lazily
                "committer": {"user": head_user} if head_user else {},
                "author": {"user": head_user} if head_user else {},
            })

            self.logger.debug(
                f"{self.log_prefix} Initialized pull request #{pull_request.number} from webhook payload "
                f"(commit {head_sha[:7]})"
            )
        else:
            # For other events (check_run, issue_comment, etc.), use API calls as before
            pull_request = await self.unified_api.get_pull_request(
                owner,
                repo,
                self.hook_data,
                self.github_event,
                self.logger,
                self.x_github_delivery,
            )
            if not pull_request:
                return None

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                f"Fetched pull request data via API (event: {self.github_event})",
            )
            self.last_commit = await self.unified_api.get_last_commit(owner, repo, pull_request, pull_request.number)

        # Fetch comprehensive repository data once per webhook (static data)
        # This eliminates N+1 query pattern - reduces 10+ API calls to 1 GraphQL query
        # If fetch fails, exception propagates and webhook processing aborts (fail-fast)
        try:
            self.repository_data: dict[str, Any] = await self.unified_api.get_comprehensive_repository_data(owner, repo)
            self.logger.info(
                f"{self.log_prefix} Fetched repository data: "
                f"{len(self.repository_data['collaborators']['edges'])} collaborators, "
                f"{len(self.repository_data['mentionableUsers']['nodes'])} contributors, "
                f"{len(self.repository_data['issues']['nodes'])} open issues, "
                f"{len(self.repository_data['pullRequests']['nodes'])} open PRs"
            )
        except Exception:
            self.logger.exception(f"{self.log_prefix} Failed to fetch repository data - aborting webhook processing")
            raise

        if pull_request:
            self.log_prefix = self.prepare_log_prefix(pull_request=pull_request)
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                f"Processing pull request event: {event_log}",
            )
            self.logger.debug(f"{self.log_prefix} {event_log}")

            if pull_request.draft:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                    f"Pull request is draft, skipping processing",
                )
                self.logger.debug(f"{self.log_prefix} Pull request is draft, doing nothing")
                return None

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                f"Initializing pull request data",
            )
            self.parent_committer = pull_request.user.login
            self.last_committer = getattr(self.last_commit.committer, "login", self.parent_committer)

            if self.github_event == "issue_comment":
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                    f"Initializing OWNERS file handler for issue comment",
                )
                owners_file_handler = OwnersFileHandler(github_webhook=self)
                owners_file_handler = await owners_file_handler.initialize(pull_request=pull_request)

                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                    f"Processing issue comment with IssueCommentHandler",
                )
                await IssueCommentHandler(
                    github_webhook=self, owners_file_handler=owners_file_handler
                ).process_comment_webhook_data(pull_request=pull_request)
                self.logger.success(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} "
                    f"Webhook processing completed successfully: issue_comment event",
                )
                return None

            if self.github_event == "pull_request":
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                    f"Initializing OWNERS file handler for pull request",
                )
                owners_file_handler = OwnersFileHandler(github_webhook=self)
                owners_file_handler = await owners_file_handler.initialize(pull_request=pull_request)

                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                    f"Processing pull request with PullRequestHandler",
                )
                await PullRequestHandler(
                    github_webhook=self, owners_file_handler=owners_file_handler
                ).process_pull_request_webhook_data(pull_request=pull_request)
                self.logger.success(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} "
                    f"Webhook processing completed successfully: pull_request event",
                )
                return None

            if self.github_event == "pull_request_review":
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                    f"Initializing OWNERS file handler for pull request review",
                )
                owners_file_handler = OwnersFileHandler(github_webhook=self)
                owners_file_handler = await owners_file_handler.initialize(pull_request=pull_request)

                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                    f"Processing pull request review with PullRequestReviewHandler",
                )
                await PullRequestReviewHandler(
                    github_webhook=self, owners_file_handler=owners_file_handler
                ).process_pull_request_review_webhook_data(
                    pull_request=pull_request,
                )
                self.logger.success(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} "
                    f"Webhook processing completed successfully: pull_request_review event",
                )
                return None

            if self.github_event == "check_run":
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                    f"Initializing OWNERS file handler for check run",
                )
                owners_file_handler = OwnersFileHandler(github_webhook=self)
                owners_file_handler = await owners_file_handler.initialize(pull_request=pull_request)
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                    f"Processing check run with CheckRunHandler",
                )
                if await CheckRunHandler(
                    github_webhook=self, owners_file_handler=owners_file_handler
                ).process_pull_request_check_run_webhook_data(pull_request=pull_request):
                    if self.hook_data["check_run"]["name"] != CAN_BE_MERGED_STR:
                        self.logger.step(  # type: ignore[attr-defined]
                            f"{self.log_prefix} "
                            f"{format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                            f"Checking if pull request can be merged after check run",
                        )
                        await PullRequestHandler(
                            github_webhook=self, owners_file_handler=owners_file_handler
                        ).check_if_can_be_merged(pull_request=pull_request)
                        self.logger.success(  # type: ignore[attr-defined]
                            f"{self.log_prefix} "
                            f"{format_task_fields('webhook_processing', 'webhook_routing', 'completed')} "
                            f"Webhook processing completed successfully: check_run event",
                        )
                        return None
                self.logger.success(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} "
                    f"Webhook processing completed successfully: check_run event",
                )
                return None

        return None

    def __del__(self) -> None:
        """Cleanup temporary clone directory on object destruction.

        This ensures the base temp directory created by tempfile.mkdtemp() is removed
        when the webhook handler is destroyed, preventing temp directory leaks.
        The subdirectories (created with -uuid4() suffix) are cleaned up by
        _prepare_cloned_repo_dir context manager in handlers.
        """
        if hasattr(self, "clone_repo_dir") and os.path.exists(self.clone_repo_dir):
            try:
                shutil.rmtree(self.clone_repo_dir, ignore_errors=True)
                if hasattr(self, "logger"):
                    self.logger.debug(f"Cleaned up temp directory: {self.clone_repo_dir}")
            except Exception:
                # Silently ignore cleanup errors in destructor to avoid issues during shutdown
                pass

    def add_api_users_to_auto_verified_and_merged_users(self) -> None:
        apis_and_tokens = get_apis_and_tokes_from_config(config=self.config)
        for _api, _ in apis_and_tokens:
            if _api.rate_limiting[-1] == 60:
                self.logger.warning(
                    f"{self.log_prefix} API has rate limit set to 60 which indicates an invalid token, skipping"
                )
                continue

            self.auto_verified_and_merged_users.append(_api.get_user().login)

    @property
    def repository_id(self) -> str:
        """Get repository GraphQL node ID from webhook payload.

        Returns:
            GraphQL node ID for the repository (e.g., "MDEwOlJlcG9zaXRvcnk...")

        Note:
            Avoids unnecessary API call to get_repository() when only ID is needed.
            Webhook always provides this data in repository.node_id field.
        """
        return self.hook_data["repository"]["node_id"]

    @property
    def repository_numeric_id(self) -> int:
        """Get repository numeric ID from webhook payload.

        Returns:
            Numeric repository ID (e.g., 123456789)

        Note:
            Avoids unnecessary API call to get_repository() when only numeric ID is needed.
            Webhook always provides this data in repository.id field.
        """
        return self.hook_data["repository"]["id"]

    def _normalize_container_args(self, args: str | list[str] | dict[str, str] | None) -> list[str]:
        """
        Normalize container build args to list format.

        Supports:
        - str: Single string (legacy format) or space-separated args
        - list[str]: Already in correct format
        - dict[str, str]: Key-value pairs converted to KEY=VALUE format
        - None: Returns empty list

        Returns:
            List of argument strings
        """
        if not args:
            return []

        if isinstance(args, list):
            return args

        if isinstance(args, dict):
            return [f"{key}={value}" for key, value in args.items()]

        # String - split on whitespace for backward compatibility
        # (schema says array, but legacy configs may have strings)
        return args.split()

    def prepare_log_prefix(self, pull_request: PullRequestWrapper | None = None) -> str:
        return prepare_log_prefix(
            event_type=self.github_event,
            delivery_id=self.x_github_delivery,
            repository_name=self.repository_name,
            api_user=self.api_user,
            pr_number=pull_request.number if pull_request else None,
            data_dir=self.config.data_dir,
        )

    def _repo_data_from_config(self, repository_config: dict[str, Any]) -> None:
        self.logger.debug(f"Read config for repository {self.repository_name}")

        self.github_app_id: str = self.config.get_value(value="github-app-id", extra_dict=repository_config)
        self.pypi: dict[str, str] = self.config.get_value(value="pypi", extra_dict=repository_config)
        self.verified_job: bool = self.config.get_value(
            value="verified-job", return_on_none=True, extra_dict=repository_config
        )
        self.tox: dict[str, str] = self.config.get_value(value="tox", extra_dict=repository_config)
        self.tox_python_version: str = self.config.get_value(value="tox-python-version", extra_dict=repository_config)
        self.slack_webhook_url: str = self.config.get_value(value="slack-webhook-url", extra_dict=repository_config)

        self.build_and_push_container: dict[str, Any] = self.config.get_value(
            value="container", return_on_none={}, extra_dict=repository_config
        )
        if self.build_and_push_container:
            self.container_repository_username: str = self.build_and_push_container["username"]
            self.container_repository_password: str = self.build_and_push_container["password"]
            self.container_repository: str = self.build_and_push_container["repository"]
            self.dockerfile: str = self.build_and_push_container.get("dockerfile", "Dockerfile")
            self.container_tag: str = self.build_and_push_container.get("tag", "latest")
            # Support str | list[str] for build-args (schema says array, but may be string in legacy configs)
            self.container_build_args: list[str] = self._normalize_container_args(
                self.build_and_push_container.get("build-args", [])
            )
            self.container_command_args: list[str] = self._normalize_container_args(
                self.build_and_push_container.get("args", [])
            )
            self.container_release: bool = self.build_and_push_container.get("release", False)

        self.pre_commit: bool = self.config.get_value(
            value="pre-commit", return_on_none=False, extra_dict=repository_config
        )

        self.auto_verified_and_merged_users: list[str] = self.config.get_value(
            value="auto-verified-and-merged-users", return_on_none=[], extra_dict=repository_config
        )
        self.auto_verify_cherry_picked_prs: bool = self.config.get_value(
            value="auto-verify-cherry-picked-prs", return_on_none=True, extra_dict=repository_config
        )
        self.can_be_merged_required_labels = self.config.get_value(
            value="can-be-merged-required-labels", return_on_none=[], extra_dict=repository_config
        )
        self.conventional_title: str = self.config.get_value(value="conventional-title", extra_dict=repository_config)
        self.set_auto_merge_prs: list[str] = self.config.get_value(
            value="set-auto-merge-prs", return_on_none=[], extra_dict=repository_config
        )
        self.minimum_lgtm: int = self.config.get_value(
            value="minimum-lgtm", return_on_none=0, extra_dict=repository_config
        )
        # Load global create_issue_for_new_pr setting as fallback
        global_create_issue_for_new_pr: bool = self.config.get_value(
            value="create-issue-for-new-pr", return_on_none=True
        )
        # Repository-specific setting overrides global setting
        self.create_issue_for_new_pr: bool = self.config.get_value(
            value="create-issue-for-new-pr", return_on_none=global_create_issue_for_new_pr, extra_dict=repository_config
        )

    @property
    def _current_pull_request_supported_retest(self) -> list[str]:
        current_pull_request_supported_retest: list[str] = []

        if self.tox:
            current_pull_request_supported_retest.append(TOX_STR)

        if self.build_and_push_container:
            current_pull_request_supported_retest.append(BUILD_CONTAINER_STR)

        if self.pypi:
            current_pull_request_supported_retest.append(PYTHON_MODULE_INSTALL_STR)

        if self.pre_commit:
            current_pull_request_supported_retest.append(PRE_COMMIT_STR)

        if self.conventional_title:
            current_pull_request_supported_retest.append(CONVENTIONAL_TITLE_STR)
        return current_pull_request_supported_retest
