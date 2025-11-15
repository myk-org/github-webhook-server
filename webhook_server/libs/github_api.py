from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shlex
import shutil
import tempfile
from typing import Any

import github
import requests
from github import GithubException
from github.Commit import Commit
from github.PullRequest import PullRequest
from github.Repository import Repository
from starlette.datastructures import Headers

from webhook_server.libs.config import Config
from webhook_server.libs.exceptions import RepositoryNotFoundInConfigError
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
    OTHER_MAIN_BRANCH,
    PRE_COMMIT_STR,
    PYTHON_MODULE_INSTALL_STR,
    TOX_STR,
)
from webhook_server.utils.github_repository_settings import (
    get_repository_github_app_api,
)
from webhook_server.utils.helpers import (
    _redact_secrets,
    format_task_fields,
    get_api_with_highest_rate_limit,
    get_apis_and_tokes_from_config,
    get_github_repo_api,
    prepare_log_prefix,
    run_command,
)


class GithubWebhook:
    def __init__(self, hook_data: dict[Any, Any], headers: Headers, logger: logging.Logger) -> None:
        logger.name = "GithubWebhook"
        self.logger = logger
        self.hook_data = hook_data
        self.repository_name: str = hook_data["repository"]["name"]
        self.repository_full_name: str = hook_data["repository"]["full_name"]
        self._bg_tasks: set[asyncio.Task] = set()
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
        self.github_api: github.Github | None = None
        self.initial_rate_limit_remaining: int | None = None

        if not self.config.repository_data:
            raise RepositoryNotFoundInConfigError(f"Repository {self.repository_name} not found in config file")

        # Get config without .github-webhook-server.yaml data
        self._repo_data_from_config(repository_config={})
        github_api, self.token, self.api_user = get_api_with_highest_rate_limit(
            config=self.config, repository_name=self.repository_name
        )

        if github_api and self.token:
            self.github_api = github_api
            # Track initial rate limit for token spend calculation
            # Note: log_prefix not set yet, so we can't use it in error messages here
            try:
                initial_rate_limit = github_api.get_rate_limit()
                self.initial_rate_limit_remaining = initial_rate_limit.rate.remaining
            except Exception as ex:
                self.logger.debug(f"Failed to get initial rate limit: {ex}")
            self.repository = get_github_repo_api(github_app_api=github_api, repository=self.repository_full_name)
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
        self.clone_repo_dir: str = tempfile.mkdtemp(prefix=f"github-webhook-{self.repository_name}-")
        self._repo_cloned: bool = False  # Track if repository has been cloned
        # Initialize auto-verified users from API users
        self.add_api_users_to_auto_verified_and_merged_users()

        self.current_pull_request_supported_retest = self._current_pull_request_supported_retest
        self.issue_url_for_welcome_msg: str = (
            "Report bugs in [Issues](https://github.com/myakove/github-webhook-server/issues)"
        )

    async def _get_token_metrics(self) -> str:
        """Get token metrics (API rate limit consumption) for this webhook.

        Returns:
            str: Formatted token metrics string for logging, or empty string if unavailable.
        """
        if not self.github_api or self.initial_rate_limit_remaining is None:
            return ""

        try:
            final_rate_limit = await asyncio.to_thread(self.github_api.get_rate_limit)
            final_remaining = final_rate_limit.rate.remaining

            # Calculate token spend (handle case where rate limit reset between checks)
            # If final > initial, rate limit reset occurred, so we can't calculate accurately
            if final_remaining > self.initial_rate_limit_remaining:
                # Rate limit reset happened - log as 0 since we can't determine actual spend
                token_spend = 0
                return (
                    f"token {self.token[:8]}... {token_spend} API calls "
                    f"(rate limit reset occurred - initial: {self.initial_rate_limit_remaining}, "
                    f"final: {final_remaining})"
                )
            else:
                token_spend = self.initial_rate_limit_remaining - final_remaining
                # Return token spend with structured format for parsing
                return (
                    f"token {self.token[:8]}... {token_spend} API calls "
                    f"(initial: {self.initial_rate_limit_remaining}, "
                    f"final: {final_remaining}, remaining: {final_remaining})"
                )
        except Exception as ex:
            self.logger.debug(f"{self.log_prefix} Failed to get token metrics: {ex}")
            return ""

    async def _clone_repository_for_pr(self, pull_request: PullRequest) -> None:
        """Clone repository once for all PR handlers to use with worktrees.

        Clones the repository to self.clone_repo_dir with PR fetch configuration.
        Handlers create isolated worktrees from this single clone for their operations.

        Args:
            pull_request: PullRequest object to get base branch

        Raises:
            RuntimeError: If clone fails (aborts webhook processing)
        """
        if self._repo_cloned:
            self.logger.debug(f"{self.log_prefix} Repository already cloned")
            return

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('webhook_processing', 'repo_clone', 'started')} "
            "Cloning repository for handler worktrees"
        )

        try:
            github_token = self.token
            clone_url_with_token = self.repository.clone_url.replace("https://", f"https://{github_token}@")

            rc, _, err = await run_command(
                command=f"git clone {clone_url_with_token} {self.clone_repo_dir}",
                log_prefix=self.log_prefix,
                redact_secrets=[github_token],
                mask_sensitive=self.mask_sensitive,
            )

            def redact_output(value: str) -> str:
                return _redact_secrets(value or "", [github_token], mask_sensitive=self.mask_sensitive)

            if not rc:
                redacted_err = redact_output(err)
                self.logger.error(
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'repo_clone', 'failed')} "
                    f"Failed to clone repository: {redacted_err}"
                )
                raise RuntimeError(f"Failed to clone repository: {redacted_err}")

            # Configure git user
            git_cmd = f"git -C {self.clone_repo_dir}"
            rc, _, _ = await run_command(
                command=f"{git_cmd} config user.name '{self.repository.owner.login}'",
                log_prefix=self.log_prefix,
                mask_sensitive=self.mask_sensitive,
            )
            if not rc:
                self.logger.warning(f"{self.log_prefix} Failed to configure git user.name")

            rc, _, _ = await run_command(
                command=f"{git_cmd} config user.email '{self.repository.owner.login}@users.noreply.github.com'",
                log_prefix=self.log_prefix,
                mask_sensitive=self.mask_sensitive,
            )
            if not rc:
                self.logger.warning(f"{self.log_prefix} Failed to configure git user.email")

            # Configure PR fetch to enable origin/pr/* checkouts
            rc, _, _ = await run_command(
                command=(
                    f"{git_cmd} config --local --add remote.origin.fetch +refs/pull/*/head:refs/remotes/origin/pr/*"
                ),
                log_prefix=self.log_prefix,
                mask_sensitive=self.mask_sensitive,
            )
            if not rc:
                self.logger.warning(f"{self.log_prefix} Failed to configure PR fetch refs")

            # Fetch all refs including PRs
            rc, _, _ = await run_command(
                command=f"{git_cmd} remote update",
                log_prefix=self.log_prefix,
                mask_sensitive=self.mask_sensitive,
            )
            if not rc:
                self.logger.warning(f"{self.log_prefix} Failed to fetch remote refs")

            # Checkout base branch (for OWNERS files and default state)
            base_branch = await asyncio.to_thread(lambda: pull_request.base.ref)
            rc, _, err = await run_command(
                command=f"{git_cmd} checkout {base_branch}",
                log_prefix=self.log_prefix,
                mask_sensitive=self.mask_sensitive,
            )
            if not rc:
                redacted_err = redact_output(err)
                self.logger.error(
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'repo_clone', 'failed')} "
                    f"Failed to checkout base branch {base_branch}: {redacted_err}"
                )
                raise RuntimeError(f"Failed to checkout base branch {base_branch}: {redacted_err}")

            self._repo_cloned = True
            self.logger.success(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'repo_clone', 'completed')} "
                f"Repository cloned to {self.clone_repo_dir} (branch: {base_branch})"
            )

        except Exception as ex:
            self.logger.exception(
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'repo_clone', 'failed')} "
                f"Exception during repository clone: {ex}"
            )
            raise RuntimeError(f"Repository clone failed: {ex}") from ex

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
            token_metrics = await self._get_token_metrics()
            self.logger.success(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} "
                f"Webhook processing completed successfully: ping - {token_metrics}",
            )
            return {"status": requests.codes.ok, "message": "pong"}

        if self.github_event == "push":
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                f"Processing push event",
            )
            self.logger.debug(f"{self.log_prefix} {event_log}")
            await PushHandler(github_webhook=self).process_push_webhook_data()
            token_metrics = await self._get_token_metrics()
            self.logger.success(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} "
                f"Webhook processing completed successfully: push - {token_metrics}",
            )
            return None

        pull_request = await self.get_pull_request()
        if pull_request:
            # Log how we got the pull request (for workflow tracking)
            if self.github_event == "pull_request":
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                    f"Initializing pull request from webhook payload",
                )
            else:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                    f"Fetched pull request data via API (event: {self.github_event})",
                )

            self.log_prefix = self.prepare_log_prefix(pull_request=pull_request)
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                f"Processing pull request event: {event_log}",
            )
            self.logger.debug(f"{self.log_prefix} {event_log}")

            if await asyncio.to_thread(lambda: pull_request.draft):
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                    f"Pull request is draft, skipping processing",
                )
                self.logger.debug(f"{self.log_prefix} Pull request is draft, doing nothing")
                token_metrics = await self._get_token_metrics()
                self.logger.success(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} "
                    f"Webhook processing completed successfully: draft PR (skipped) - {token_metrics}",
                )
                return None

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                f"Initializing pull request data",
            )
            self.last_commit = await self._get_last_commit(pull_request=pull_request)
            self.parent_committer = pull_request.user.login
            self.last_committer = getattr(self.last_commit.committer, "login", self.parent_committer)

            # Clone repository for local file processing (OWNERS, changed files)
            await self._clone_repository_for_pr(pull_request=pull_request)

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
                token_metrics = await self._get_token_metrics()
                self.logger.success(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} "
                    f"Webhook processing completed successfully: issue_comment - {token_metrics}",
                )
                return None

            elif self.github_event == "pull_request":
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
                token_metrics = await self._get_token_metrics()
                self.logger.success(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} "
                    f"Webhook processing completed successfully: pull_request - {token_metrics}",
                )
                return None

            elif self.github_event == "pull_request_review":
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
                token_metrics = await self._get_token_metrics()
                self.logger.success(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('webhook_processing', 'webhook_routing', 'completed')} "
                    f"Webhook processing completed successfully: pull_request_review - {token_metrics}",
                )
                return None

            elif self.github_event == "check_run":
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
                handled = await CheckRunHandler(
                    github_webhook=self, owners_file_handler=owners_file_handler
                ).process_pull_request_check_run_webhook_data(pull_request=pull_request)
                if handled:
                    if self.hook_data["check_run"]["name"] != CAN_BE_MERGED_STR:
                        self.logger.step(  # type: ignore[attr-defined]
                            f"{self.log_prefix} "
                            f"{format_task_fields('webhook_processing', 'webhook_routing', 'processing')} "
                            f"Checking if pull request can be merged after check run",
                        )
                        await PullRequestHandler(
                            github_webhook=self, owners_file_handler=owners_file_handler
                        ).check_if_can_be_merged(pull_request=pull_request)
                # Log completion regardless of whether check run was processed or skipped
                token_metrics = await self._get_token_metrics()
                self.logger.success(  # type: ignore[attr-defined]
                    f"{self.log_prefix} "
                    f"{format_task_fields('webhook_processing', 'webhook_routing', 'completed')} "
                    f"Webhook processing completed successfully: check_run - {token_metrics}",
                )
                return None

        else:
            # Log warning when no PR found
            self.logger.warning(
                f"{self.log_prefix} "
                f"{format_task_fields('webhook_processing', 'webhook_routing', 'skipped')} "
                f"No pull request found for {self.github_event} event - skipping processing"
            )
            token_metrics = await self._get_token_metrics()
            self.logger.success(  # type: ignore[attr-defined]
                f"{self.log_prefix} "
                f"{format_task_fields('webhook_processing', 'webhook_routing', 'completed')} "
                f"Webhook processing completed: no PR found - {token_metrics}"
            )
            return None

    def add_api_users_to_auto_verified_and_merged_users(self) -> None:
        apis_and_tokens = get_apis_and_tokes_from_config(config=self.config)
        for _api, _ in apis_and_tokens:
            if _api.rate_limiting[-1] == 60:
                self.logger.warning(
                    f"{self.log_prefix} API has rate limit set to 60 which indicates an invalid token, skipping"
                )
                continue

            self.auto_verified_and_merged_users.append(_api.get_user().login)

    def prepare_log_prefix(self, pull_request: PullRequest | None = None) -> str:
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
            _build_args = self.build_and_push_container.get("build-args", [])
            _cmd_args = self.build_and_push_container.get("args", [])
            # Normalize to lists
            if isinstance(_build_args, str):
                _build_args = [a for a in shlex.split(_build_args) if a]
            elif not isinstance(_build_args, list):
                _build_args = []
            if isinstance(_cmd_args, str):
                _cmd_args = [a for a in shlex.split(_cmd_args) if a]
            elif not isinstance(_cmd_args, list):
                _cmd_args = []
            self.container_build_args: list[str] = [str(a) for a in _build_args]
            self.container_command_args: list[str] = [str(a) for a in _cmd_args]
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

        self.mask_sensitive = self.config.get_value("mask-sensitive-data", return_on_none=True)

    async def get_pull_request(self, number: int | None = None) -> PullRequest | None:
        if number:
            self.logger.debug(f"{self.log_prefix} Attempting to get PR by number: {number}")
            return await asyncio.to_thread(self.repository.get_pull, number)

        # Try to get PR number from hook_data
        self.logger.debug(f"{self.log_prefix} Attempting to get PR from webhook payload")
        pr_data = self.hook_data.get("pull_request") or self.hook_data.get("issue", {})
        if pr_data and isinstance(pr_data, dict):
            pr_number = pr_data.get("number")
            if pr_number:
                self.logger.debug(f"{self.log_prefix} Found PR number in payload: {pr_number}")
                try:
                    return await asyncio.to_thread(self.repository.get_pull, pr_number)
                except GithubException as ex:
                    self.logger.debug(f"{self.log_prefix} Failed to get PR {pr_number} from payload: {ex}")
            else:
                self.logger.debug(f"{self.log_prefix} No PR number found in payload")
        else:
            self.logger.debug(f"{self.log_prefix} No PR data in webhook payload")

        commit: dict[str, Any] = self.hook_data.get("commit", {})
        if commit:
            self.logger.debug(f"{self.log_prefix} Attempting to get PR from commit SHA: {commit.get('sha', 'unknown')}")
            commit_obj = await asyncio.to_thread(self.repository.get_commit, commit["sha"])
            with contextlib.suppress(Exception):
                _pulls = await asyncio.to_thread(commit_obj.get_pulls)
                if _pulls:
                    self.logger.debug(f"{self.log_prefix} Found PR from commit SHA: {_pulls[0].number}")
                    return _pulls[0]
            self.logger.debug(f"{self.log_prefix} No PR found for commit SHA")
        else:
            self.logger.debug(f"{self.log_prefix} No commit data in webhook payload")

        if self.github_event == "check_run":
            head_sha = self.hook_data["check_run"]["head_sha"]
            self.logger.debug(f"{self.log_prefix} Searching open PRs for check_run head SHA: {head_sha}")
            for _pull_request in await asyncio.to_thread(self.repository.get_pulls, state="open"):
                if _pull_request.head.sha == head_sha:
                    self.logger.debug(
                        f"{self.log_prefix} Found pull request {_pull_request.title} "
                        f"[{_pull_request.number}] for check run "
                        f"{self.hook_data['check_run']['name']}"
                    )
                    return _pull_request
            self.logger.debug(f"{self.log_prefix} No open PR found matching check_run head SHA")

        self.logger.debug(f"{self.log_prefix} All PR lookup strategies exhausted, no PR found")
        return None

    async def _get_last_commit(self, pull_request: PullRequest) -> Commit:
        _commits = await asyncio.to_thread(pull_request.get_commits)
        return list(_commits)[-1]

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

    def __del__(self) -> None:
        """Remove the shared clone directory when the webhook object is destroyed.

        GithubWebhook now creates a single clone via tempfile.mkdtemp() and individual
        handlers operate on worktrees created by git_worktree_checkout, which already
        clean up their own directories. Only the base clone directory must be removed
        here to prevent accumulating stale repositories on disk.
        """
        if hasattr(self, "clone_repo_dir") and os.path.exists(self.clone_repo_dir):
            try:
                shutil.rmtree(self.clone_repo_dir, ignore_errors=True)
                if hasattr(self, "logger"):
                    self.logger.debug(f"Cleaned up temp directory: {self.clone_repo_dir}")
            except Exception:
                pass  # Ignore errors during cleanup
