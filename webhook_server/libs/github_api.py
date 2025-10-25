from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests
from github import GithubException
from github.Commit import Commit
from github.PullRequest import PullRequest
from github.Repository import Repository

# GraphQL wrappers provide PyGithub-compatible interface
from gql.transport.exceptions import TransportConnectionFailed, TransportQueryError, TransportServerError
from webhook_server.libs.graphql.graphql_client import (
    GraphQLAuthenticationError,
    GraphQLError,
    GraphQLRateLimitError,
)
from webhook_server.libs.graphql.graphql_wrappers import CommitWrapper, PullRequestWrapper
from webhook_server.libs.graphql.unified_api import UnifiedGitHubAPI
from starlette.datastructures import Headers

from webhook_server.libs.config import Config
from webhook_server.libs.handlers.check_run_handler import CheckRunHandler
from webhook_server.libs.exceptions import RepositoryNotFoundInConfigError, UnifiedAPINotInitializedError
from webhook_server.libs.handlers.issue_comment_handler import IssueCommentHandler
from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
from webhook_server.libs.handlers.pull_request_handler import PullRequestHandler
from webhook_server.libs.handlers.pull_request_review_handler import PullRequestReviewHandler
from webhook_server.libs.handlers.push_handler import PushHandler
from webhook_server.utils.constants import (
    BUILD_CONTAINER_STR,
    CAN_BE_MERGED_STR,
    CONVENTIONAL_TITLE_STR,
    OTHER_MAIN_BRANCH,
    PRE_COMMIT_STR,
    PYTHON_MODULE_INSTALL_STR,
    TOX_STR,
)
from webhook_server.utils.github_repository_settings import (
    get_repository_github_app_api,
)
from webhook_server.utils.helpers import (
    extract_key_from_dict,
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

        self.clone_repo_dir: str = os.path.join("/tmp", f"{self.repository.name}")
        self.add_api_users_to_auto_verified_and_merged_users

        self.current_pull_request_supported_retest = self._current_pull_request_supported_retest
        self.issue_url_for_welcome_msg: str = (
            "Report bugs in [Issues](https://github.com/myakove/github-webhook-server/issues)"
        )

    async def process(self) -> Any:
        event_log: str = f"Event type: {self.github_event}. event ID: {self.x_github_delivery}"
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'started')} Starting webhook processing: {event_log}",
        )

        if self.github_event == "ping":
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} Processing ping event",
            )
            self.logger.debug(f"{self.log_prefix} {event_log}")
            self.logger.success(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} Webhook processing completed successfully: ping event",
            )
            return {"status": requests.codes.ok, "message": "pong"}

        if self.github_event == "push":
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} Processing push event",
            )
            self.logger.debug(f"{self.log_prefix} {event_log}")
            await PushHandler(github_webhook=self).process_push_webhook_data()
            self.logger.success(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} Webhook processing completed successfully: push event",
            )
            return None

        if pull_request := await self.get_pull_request():
            self.log_prefix = self.prepare_log_prefix(pull_request=pull_request)
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} Processing pull request event: {event_log}",
            )
            self.logger.debug(f"{self.log_prefix} {event_log}")

            if pull_request.draft:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} Pull request is draft, skipping processing",
                )
                self.logger.debug(f"{self.log_prefix} Pull request is draft, doing nothing")
                return None

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} Initializing pull request data",
            )
            self.last_commit = await self._get_last_commit(pull_request=pull_request)
            self.parent_committer = pull_request.user.login
            self.last_committer = getattr(self.last_commit.committer, "login", self.parent_committer)

            if self.github_event == "issue_comment":
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} Initializing OWNERS file handler for issue comment",
                )
                owners_file_handler = OwnersFileHandler(github_webhook=self)
                owners_file_handler = await owners_file_handler.initialize(pull_request=pull_request)

                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} Processing issue comment with IssueCommentHandler",
                )
                await IssueCommentHandler(
                    github_webhook=self, owners_file_handler=owners_file_handler
                ).process_comment_webhook_data(pull_request=pull_request)
                self.logger.success(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} Webhook processing completed successfully: issue_comment event",
                )
                return None

            elif self.github_event == "pull_request":
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} Initializing OWNERS file handler for pull request",
                )
                owners_file_handler = OwnersFileHandler(github_webhook=self)
                owners_file_handler = await owners_file_handler.initialize(pull_request=pull_request)

                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} Processing pull request with PullRequestHandler",
                )
                await PullRequestHandler(
                    github_webhook=self, owners_file_handler=owners_file_handler
                ).process_pull_request_webhook_data(pull_request=pull_request)
                self.logger.success(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} Webhook processing completed successfully: pull_request event",
                )
                return None

            elif self.github_event == "pull_request_review":
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} Initializing OWNERS file handler for pull request review",
                )
                owners_file_handler = OwnersFileHandler(github_webhook=self)
                owners_file_handler = await owners_file_handler.initialize(pull_request=pull_request)

                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} Processing pull request review with PullRequestReviewHandler",
                )
                await PullRequestReviewHandler(
                    github_webhook=self, owners_file_handler=owners_file_handler
                ).process_pull_request_review_webhook_data(
                    pull_request=pull_request,
                )
                self.logger.success(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} Webhook processing completed successfully: pull_request_review event",
                )
                return None

            elif self.github_event == "check_run":
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} Initializing OWNERS file handler for check run",
                )
                owners_file_handler = OwnersFileHandler(github_webhook=self)
                owners_file_handler = await owners_file_handler.initialize(pull_request=pull_request)
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} Processing check run with CheckRunHandler",
                )
                if await CheckRunHandler(
                    github_webhook=self, owners_file_handler=owners_file_handler
                ).process_pull_request_check_run_webhook_data(pull_request=pull_request):
                    if self.hook_data["check_run"]["name"] != CAN_BE_MERGED_STR:
                        self.logger.step(  # type: ignore[attr-defined]
                            f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} Checking if pull request can be merged after check run",
                        )
                        await PullRequestHandler(
                            github_webhook=self, owners_file_handler=owners_file_handler
                        ).check_if_can_be_merged(pull_request=pull_request)
                        self.logger.success(  # type: ignore[attr-defined]
                            f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} Webhook processing completed successfully: check_run event",
                        )
                        return None
                self.logger.success(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} Webhook processing completed successfully: check_run event",
                )
                return None

        return None

    @property
    def add_api_users_to_auto_verified_and_merged_users(self) -> None:
        apis_and_tokens = get_apis_and_tokes_from_config(config=self.config)
        for _api, _ in apis_and_tokens:
            if _api.rate_limiting[-1] == 60:
                self.logger.warning(
                    f"{self.log_prefix} API has rate limit set to 60 which indicates an invalid token, skipping"
                )
                continue

            self.auto_verified_and_merged_users.append(_api.get_user().login)

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

    def prepare_log_prefix(self, pull_request: PullRequest | PullRequestWrapper | None = None) -> str:
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

    async def get_pull_request(self, number: int | None = None) -> PullRequest | PullRequestWrapper | None:
        """
        Get pull request using GraphQL or REST API.

        Returns:
            PullRequestWrapper for GraphQL queries (when PR number is available)
            PullRequest (REST) for commit-based lookups or check_run events
            None if no PR found
        """
        if not self.unified_api:
            self.logger.error(f"{self.log_prefix} UnifiedAPI not initialized")
            raise UnifiedAPINotInitializedError("UnifiedAPI must be initialized before use")

        # Extract owner and repo name from repository_full_name
        owner, repo_name = self.repository_full_name.split("/")

        # Try to get PR number from various sources
        pr_number = number
        if not pr_number:
            for _number in extract_key_from_dict(key="number", _dict=self.hook_data):
                pr_number = _number
                break

        # If we have a PR number, use GraphQL
        if pr_number:
            # Fetch PR with commits and labels (commonly needed data)
            pr_data = await self.unified_api.get_pull_request(
                owner, repo_name, pr_number, include_commits=True, include_labels=True
            )
            return PullRequestWrapper(pr_data, owner, repo_name)

        # For commit-based lookups or check_run events, use REST via unified_api
        # (GraphQL doesn't have efficient commit->PR lookup)
        commit: dict[str, Any] = self.hook_data.get("commit", {})
        if commit:
            owner, repo_name = self.repository_full_name.split("/")
            try:
                # Get PRs associated with this commit SHA (unified_api handles REST internally)
                _pulls = await self.unified_api.get_pulls_from_commit_sha(owner, repo_name, commit["sha"])
                if _pulls:
                    return _pulls[0]
                self.logger.warning(f"{self.log_prefix} No PRs found for commit {commit['sha']}")
            except (GraphQLError, GithubException, IndexError) as ex:
                self.logger.warning(f"{self.log_prefix} Failed to get PR from commit {commit['sha']}: {ex}")
            # Don't suppress authentication or connection errors

        if self.github_event == "check_run":
            owner, repo_name = self.repository_full_name.split("/")
            for _pull_request in await self.unified_api.get_open_pull_requests(owner, repo_name):
                if _pull_request.head.sha == self.hook_data["check_run"]["head_sha"]:
                    self.logger.debug(
                        f"{self.log_prefix} Found pull request {_pull_request.title} [{_pull_request.number}] for check run {self.hook_data['check_run']['name']}"
                    )
                    return _pull_request

        return None

    async def _get_last_commit(self, pull_request: PullRequest | PullRequestWrapper) -> Commit | CommitWrapper:
        """Get last commit from pull request (supports both REST and GraphQL PR types)."""
        # Handle PyGithub PullRequest (REST) - use GraphQL with REST fallback
        if isinstance(pull_request, PullRequest):
            owner = pull_request.base.repo.owner.login
            repo_name = pull_request.base.repo.name
            # Try GraphQL first for performance
            try:
                pr_data = await self.unified_api.get_pull_request(
                    owner, repo_name, pull_request.number, include_commits=True
                )
                # Extract commits from GraphQL response
                commits_nodes = pr_data.get("commits", {}).get("nodes", [])
                if not commits_nodes:
                    raise ValueError(f"No commits found in PR {pull_request.number}")
                # Return last commit (wrapped)
                last_commit_data = commits_nodes[-1].get("commit", {})
                return CommitWrapper(last_commit_data)
            except (GraphQLError, GraphQLRateLimitError, TransportQueryError) as ex:
                # Fallback to REST API if GraphQL fails
                self.logger.warning(f"{self.log_prefix} GraphQL failed to get commits, falling back to REST: {ex}")
                rest_commits = await self.unified_api.get_pr_commits_rest(pull_request)
                if not rest_commits:
                    raise ValueError(f"No commits found in PR {pull_request.number}") from ex
                return rest_commits[-1]

        # Handle PullRequestWrapper (GraphQL)
        commits = pull_request.get_commits()
        if commits:
            return commits[-1]
        # If no commits in wrapper, fetch PR with commits
        self.logger.warning(f"{self.log_prefix} No commits in GraphQL wrapper, fetching with include_commits=True")
        owner, repo_name = self.repository_full_name.split("/")
        pr_data = await self.unified_api.get_pull_request(owner, repo_name, pull_request.number, include_commits=True)
        commits_nodes = pr_data.get("commits", {}).get("nodes", [])
        if not commits_nodes:
            raise ValueError(f"No commits found in PR {pull_request.number}")
        last_commit_data = commits_nodes[-1].get("commit", {})
        return CommitWrapper(last_commit_data)

    async def add_pr_comment(self, pull_request: PullRequest | PullRequestWrapper, body: str) -> None:
        """Add comment to PR via unified_api (supports both REST and GraphQL PRs)."""
        try:
            # Handle PyGithub PullRequest (REST) - convert to GraphQL
            if isinstance(pull_request, PullRequest):
                owner = pull_request.base.repo.owner.login
                repo = pull_request.base.repo.name
                self.logger.debug(
                    f"{self.log_prefix} Getting PR node ID for GraphQL mutation, pr={pull_request.number}"
                )
                # Get PR data via GraphQL to obtain node ID
                pr_data = await self.unified_api.get_pull_request(owner, repo, pull_request.number)
                pr_id = pr_data["id"]
                self.logger.debug(
                    f"{self.log_prefix} Adding PR comment via GraphQL, pr_id={pr_id}, body length={len(body)}"
                )
                await self.unified_api.add_comment(pr_id, body)
            else:
                # Handle PullRequestWrapper (GraphQL)
                pr_id = pull_request.id
                self.logger.debug(f"{self.log_prefix} Adding PR comment with pr_id={pr_id}, body length={len(body)}")
                await self.unified_api.add_comment(pr_id, body)
            self.logger.info(f"{self.log_prefix} Successfully added PR comment")
        except Exception:
            self.logger.exception(f"{self.log_prefix} Failed to add PR comment")
            raise

    async def update_pr_title(self, pull_request: PullRequest | PullRequestWrapper, title: str) -> None:
        """Update PR title via unified_api (supports both REST and GraphQL PRs)."""
        # Handle PyGithub PullRequest (REST) - use REST API with asyncio.to_thread wrapper
        if isinstance(pull_request, PullRequest):
            await self.unified_api.edit_pull_request_rest(pull_request, title=title)
            self.logger.info(f"{self.log_prefix} Updated PR #{pull_request.number} title via REST API")
        else:
            # Handle PullRequestWrapper (GraphQL)
            pr_id = pull_request.id
            await self.unified_api.update_pull_request(pr_id, title=title)

    async def enable_pr_automerge(self, pull_request: PullRequestWrapper, merge_method: str = "SQUASH") -> None:
        """Enable automerge on PR via unified_api."""
        pr_id = pull_request.id
        await self.unified_api.enable_pull_request_automerge(pr_id, merge_method)

    async def request_pr_reviews(self, pull_request: PullRequestWrapper, reviewers: list[str]) -> None:
        """Request reviews on PR via unified_api."""
        pr_id = pull_request.id
        reviewer_ids = []
        for reviewer in reviewers:
            # (1) Accept numeric reviewer IDs directly
            if isinstance(reviewer, int):
                reviewer_ids.append(str(reviewer))
                continue

            # (2) Normalize reviewer to username string (keep existing normalization logic)
            username = None
            reviewer_id = None

            if isinstance(reviewer, str):
                # Check if it's already a node ID format
                if reviewer.startswith("U_"):
                    reviewer_ids.append(reviewer)
                    continue
                username = reviewer
            elif hasattr(reviewer, "login"):
                username = reviewer.login
                # Try to extract id if available
                if hasattr(reviewer, "id"):
                    reviewer_id = reviewer.id
            elif hasattr(reviewer, "user") and hasattr(reviewer.user, "login"):
                username = reviewer.user.login
                if hasattr(reviewer.user, "id"):
                    reviewer_id = reviewer.user.id
            elif isinstance(reviewer, dict):
                username = reviewer.get("login") or (reviewer.get("user") or {}).get("login")
                # (3) Try to extract 'id' from dict
                if not username and reviewer.get("id"):
                    reviewer_ids.append(str(reviewer["id"]))
                    continue
                reviewer_id = reviewer.get("id")

            if not username:
                self.logger.warning(f"{self.log_prefix} Could not resolve username from reviewer: {reviewer}")
                continue

            # Try GraphQL first
            try:
                user_id = await self.unified_api.get_user_id(username)
                reviewer_ids.append(user_id)
            except (GraphQLAuthenticationError, GraphQLRateLimitError):
                # Re-raise auth/rate-limit errors - don't waste time trying REST
                raise
            except (GraphQLError, TransportConnectionFailed, TransportQueryError, TransportServerError) as ex:
                # (3) If GraphQL fails (user not found or other GraphQL/transport error), try to use extracted id from original reviewer
                if reviewer_id:
                    self.logger.debug(f"{self.log_prefix} Using extracted id {reviewer_id} for {username}")
                    reviewer_ids.append(str(reviewer_id))
                else:
                    # (4) Final fallback: try REST API via unified_api
                    try:
                        user_id = await self.unified_api.get_user_id_rest(username)
                        reviewer_ids.append(user_id)
                    except (GraphQLAuthenticationError, GraphQLRateLimitError):
                        # Re-raise auth/rate-limit errors - these are critical
                        raise
                    except (
                        GraphQLError,
                        TransportConnectionFailed,
                        TransportQueryError,
                        TransportServerError,
                        GithubException,
                    ) as rest_ex:
                        # Log API-layer errors for diagnostics but continue
                        self.logger.warning(
                            f"{self.log_prefix} Failed to get ID for {username} via GraphQL ({ex}) and REST ({rest_ex})"
                        )

        # Deduplicate reviewer_ids before calling request_reviews
        if reviewer_ids:
            unique_reviewer_ids = list(dict.fromkeys(reviewer_ids))  # Preserve order while deduplicating
            await self.unified_api.request_reviews(pr_id, unique_reviewer_ids)

    async def add_pr_assignee(self, pull_request: PullRequest | PullRequestWrapper, assignee: str) -> None:
        """Add assignee to PR via unified_api."""
        try:
            if isinstance(pull_request, PullRequestWrapper):
                pr_id = pull_request.id
                user_id = await self.unified_api.get_user_id(assignee)
                await self.unified_api.add_assignees(pr_id, [user_id])
            else:
                owner, repo_name = self.repository_full_name.split("/")
                await self.unified_api.add_assignees_by_login(owner, repo_name, pull_request.number, [assignee])
        except (GraphQLError, GithubException, ValueError) as ex:
            self.logger.warning(f"{self.log_prefix} Failed to add assignee {assignee}: {ex}")

    @staticmethod
    def _comment_with_details(title: str, body: str) -> str:
        return f"""
<details>
<summary>{title}</summary>
    {body}
</details>
        """

    def container_repository_and_tag(
        self, is_merged: bool = False, tag: str = "", pull_request: PullRequest | None = None
    ) -> str | None:
        if not tag:
            if not pull_request:
                return None

            if is_merged:
                pull_request_branch = pull_request.base.ref
                tag = (
                    pull_request_branch
                    if pull_request_branch not in (OTHER_MAIN_BRANCH, "main")
                    else self.container_tag
                )
            else:
                tag = f"pr-{pull_request.number}"

        if tag:
            self.logger.debug(f"{self.log_prefix} container tag is: {tag}")
            return f"{self.container_repository}:{tag}"

        self.logger.error(f"{self.log_prefix} container tag not found")
        return None

    def send_slack_message(self, message: str, webhook_url: str) -> None:
        slack_data: dict[str, str] = {"text": message}
        self.logger.info(f"{self.log_prefix} Sending message to slack: {message}")
        response: requests.Response = requests.post(
            webhook_url,
            data=json.dumps(slack_data),
            headers={"Content-Type": "application/json"},
        )
        if response.status_code != 200:
            raise ValueError(
                f"Request to slack returned an error {response.status_code} with the following message: {response.text}"
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
