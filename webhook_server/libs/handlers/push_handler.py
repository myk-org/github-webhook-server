import asyncio
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from github.PullRequest import PullRequest
from github.Repository import Repository

from webhook_server.libs.handlers.check_run_handler import CheckRunHandler
from webhook_server.libs.handlers.runner_handler import RunnerHandler
from webhook_server.utils.helpers import format_task_fields, run_command
from webhook_server.utils.notification_utils import send_slack_message

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook


class PushHandler:
    def __init__(self, github_webhook: "GithubWebhook"):
        self.github_webhook = github_webhook

        self.hook_data = self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix: str = self.github_webhook.log_prefix
        self.repository: Repository = self.github_webhook.repository
        self.check_run_handler = CheckRunHandler(github_webhook=self.github_webhook)
        self.runner_handler = RunnerHandler(github_webhook=self.github_webhook)

    async def process_push_webhook_data(self) -> None:
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'started')} "
            f"Starting push webhook processing",  # pragma: allowlist secret
        )
        tag = re.search(r"^refs/tags/(.+)$", self.hook_data["ref"])
        if tag:
            tag_name = tag.group(1)
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'processing')} "
                f"Processing tag push: {tag_name}",
            )
            self.logger.info(f"{self.log_prefix} Processing push for tag: {tag.group(1)}")
            self.logger.debug(f"{self.log_prefix} Tag: {tag_name}")
            if self.github_webhook.pypi:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'started')} "
                    f"Starting PyPI upload for tag: {tag_name}",
                )
                self.logger.info(f"{self.log_prefix} Processing upload to pypi for tag: {tag_name}")
                try:
                    await self.upload_to_pypi(tag_name=tag_name)
                except Exception:
                    self.logger.step(  # type: ignore[attr-defined]
                        f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'failed')} "
                        f"PyPI upload failed with exception",
                    )
                    self.logger.exception(f"{self.log_prefix} PyPI upload failed")

            if self.github_webhook.build_and_push_container and self.github_webhook.container_release:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'started')} "
                    f"Starting container build and push for tag: {tag_name}",
                )
                self.logger.info(f"{self.log_prefix} Processing build and push container for tag: {tag_name}")
                try:
                    await self.runner_handler.run_build_container(push=True, set_check=False, tag=tag_name)
                    # Note: run_build_container logs completion/failure internally
                except Exception as ex:
                    self.logger.step(  # type: ignore[attr-defined]
                        f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'failed')} "
                        f"Container build and push failed with exception",
                    )
                    self.logger.exception(f"{self.log_prefix} Container build and push failed: {ex}")
        else:
            # Non-tag push - check if this is a push to a branch that could be a base for PRs
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'processing')} "
                f"Processing branch push event",
            )

            # Check if retrigger is enabled (not None or empty list)
            retrigger_config = self.github_webhook.retrigger_checks_on_base_push
            if not retrigger_config:
                self.logger.debug(f"{self.log_prefix} retrigger-checks-on-base-push not configured, skipping")
            else:
                # Extract branch name from ref (refs/heads/main -> main)
                branch_match = re.search(r"^refs/heads/(.+)$", self.hook_data["ref"])
                if branch_match:
                    branch_name = branch_match.group(1)
                    self.logger.info(f"{self.log_prefix} Branch push detected: {branch_name}")
                    await self._retrigger_checks_for_prs_targeting_branch(branch_name=branch_name)
                else:
                    self.logger.debug(
                        f"{self.log_prefix} Could not extract branch name from ref: {self.hook_data['ref']}"
                    )

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'completed')} "
                f"Branch push processing completed",
            )

    async def _retrigger_checks_for_prs_targeting_branch(self, branch_name: str) -> None:
        """Re-trigger CI checks for PRs targeting the updated branch that are behind or blocked.

        Args:
            branch_name: The branch that was pushed to (e.g., 'main')
        """
        time_sleep = 30
        self.logger.info(f"{self.log_prefix} Waiting {time_sleep}s for GitHub to update merge states")
        await asyncio.sleep(time_sleep)

        # Get all open PRs targeting this branch
        def get_pulls() -> list[PullRequest]:
            return list(self.repository.get_pulls(state="open", base=branch_name))

        pulls = await asyncio.to_thread(get_pulls)

        if not pulls:
            self.logger.info(f"{self.log_prefix} No open PRs targeting branch {branch_name}")
            return

        self.logger.info(f"{self.log_prefix} Found {len(pulls)} open PRs targeting {branch_name}")

        for pull_request in pulls:
            # pr.number is in-memory data from get_pulls() result - no wrapping needed
            pr_number = pull_request.number
            # mergeable_state triggers API call - must wrap to avoid blocking

            # Use default parameter to capture current iteration's pull_request (closure pattern)
            # This ensures each lambda captures the correct PR object, not the loop variable
            def get_merge_state(pr: PullRequest = pull_request) -> str | None:
                return pr.mergeable_state

            # Handle None/unknown merge states with retry
            max_retries = 5
            retry_delay = 10  # seconds
            merge_state: str | None = None

            for attempt in range(1, max_retries + 1):
                merge_state = await asyncio.to_thread(get_merge_state)
                self.logger.debug(f"{self.log_prefix} PR #{pr_number} merge state: {merge_state}")

                if merge_state not in (None, "unknown"):
                    break

                if attempt < max_retries:
                    self.logger.info(
                        f"{self.log_prefix} PR #{pr_number} merge state is '{merge_state}' - "
                        f"waiting {retry_delay}s for GitHub to calculate (attempt {attempt}/{max_retries})"
                    )
                    await asyncio.sleep(retry_delay)
            else:
                # Loop completed without break - merge_state is still None or "unknown"
                self.logger.warning(
                    f"{self.log_prefix} PR #{pr_number} merge state still '{merge_state}' after "
                    f"{max_retries} attempts, skipping"
                )
                continue

            # Only re-trigger for PRs that are behind or blocked
            if merge_state in ("behind", "blocked"):
                # Skip if PR was updated very recently (likely already has fresh checks)
                def get_updated_at(pr: PullRequest = pull_request) -> datetime:
                    return pr.updated_at

                pr_updated_at = await asyncio.to_thread(get_updated_at)
                time_since_update = (datetime.now(UTC) - pr_updated_at).total_seconds()
                if time_since_update < 60:  # Skip if updated within last minute
                    self.logger.debug(
                        f"{self.log_prefix} PR #{pr_number} was updated {time_since_update:.0f}s ago, "
                        "skipping retrigger to avoid duplicate work"
                    )
                    continue

                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('retrigger_checks', 'push_processing', 'processing')} "
                    f"Re-triggering checks for out-of-date PR #{pr_number} (state: {merge_state})",
                )

                try:
                    checks_triggered = await self.runner_handler.run_retests_from_config(pull_request=pull_request)
                    if checks_triggered:
                        self.logger.info(f"{self.log_prefix} Successfully re-triggered checks for PR #{pr_number}")
                    else:
                        self.logger.debug(f"{self.log_prefix} No checks triggered for PR #{pr_number}")
                except Exception as e:
                    # Re-raise CancelledError immediately to allow cooperative cancellation
                    if isinstance(e, asyncio.CancelledError):
                        raise
                    # Log all other exceptions and continue processing other PRs
                    self.logger.exception(f"{self.log_prefix} Failed to re-trigger checks for PR #{pr_number}")
                    # Continue processing other PRs
            else:
                self.logger.debug(
                    f"{self.log_prefix} PR #{pr_number} merge state is '{merge_state}', not re-triggering"
                )

    async def upload_to_pypi(self, tag_name: str) -> None:
        async def _issue_on_error(_error: str) -> None:
            # Sanitize title: replace newlines, remove backticks, strip whitespace, truncate
            sanitized_title = _error.replace("\n", " ").replace("`", "").replace("\r", "").strip()
            # Truncate to safe length (GitHub issue title limit is ~256 chars, use 250 for safety)
            if len(sanitized_title) > 250:
                sanitized_title = sanitized_title[:247] + "..."
            await asyncio.to_thread(
                self.repository.create_issue,
                title=sanitized_title,
                body=f"""
Publish to PYPI failed: `{_error}`
""",
            )

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'started')} "
            f"Starting PyPI upload process for tag: {tag_name}",
        )
        self.logger.info(f"{self.log_prefix} Start uploading to pypi")

        async with self.runner_handler._checkout_worktree(checkout=tag_name) as (success, worktree_path, out, err):
            uv_cmd_dir = f"--directory {worktree_path}"
            _dist_dir: str = f"{worktree_path}/pypi-dist"
            self.logger.debug(f"{self.log_prefix} Worktree path: {worktree_path}")

            if not success:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'failed')} "
                    f"PyPI upload failed: repository preparation failed",
                )
                _error = self.check_run_handler.get_check_run_text(out=out, err=err)
                await _issue_on_error(_error=_error)
                return

            rc, out, err = await run_command(
                command=f"uv {uv_cmd_dir} build --sdist --out-dir {_dist_dir}", log_prefix=self.log_prefix
            )
            if not rc:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'failed')} "
                    f"PyPI upload failed: build command failed",
                )
                _error = self.check_run_handler.get_check_run_text(out=out, err=err)
                await _issue_on_error(_error=_error)
                return

            rc, tar_gz_file, err = await run_command(command=f"ls {_dist_dir}", log_prefix=self.log_prefix)
            if not rc:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'failed')} "
                    f"PyPI upload failed: listing dist directory failed",
                )
                _error = self.check_run_handler.get_check_run_text(out=tar_gz_file, err=err)
                await _issue_on_error(_error=_error)
                return

            tar_gz_file = tar_gz_file.strip()

            pypi_token = self.github_webhook.pypi["token"]
            commands: list[str] = [
                f"uvx {uv_cmd_dir} twine check {_dist_dir}/{tar_gz_file}",
                f"uvx {uv_cmd_dir} twine upload --username __token__ "
                f"--password {pypi_token} "
                f"{_dist_dir}/{tar_gz_file} --skip-existing",
            ]

            for cmd in commands:
                rc, out, err = await run_command(command=cmd, log_prefix=self.log_prefix, redact_secrets=[pypi_token])
                if not rc:
                    self.logger.step(  # type: ignore[attr-defined]
                        f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'failed')} "
                        f"PyPI upload failed: command execution failed",
                    )
                    _error = self.check_run_handler.get_check_run_text(out=out, err=err)
                    await _issue_on_error(_error=_error)
                    return

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'completed')} "
                f"PyPI upload completed successfully for tag: {tag_name}",
            )
            self.logger.info(f"{self.log_prefix} Publish to pypi finished")
            if self.github_webhook.slack_webhook_url:
                message: str = f"""
```
{self.github_webhook.repository_name} Version {tag_name} published to PYPI.
```
"""
                send_slack_message(
                    message=message,
                    webhook_url=self.github_webhook.slack_webhook_url,
                    logger=self.logger,
                    log_prefix=self.log_prefix,
                )
