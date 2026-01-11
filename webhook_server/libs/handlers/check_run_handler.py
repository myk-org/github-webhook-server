import asyncio
from typing import TYPE_CHECKING, Any

from github.CheckRun import CheckRun
from github.CommitStatus import CommitStatus
from github.PullRequest import PullRequest
from github.Repository import Repository

from webhook_server.libs.handlers.labels_handler import LabelsHandler
from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
from webhook_server.utils.constants import (
    AUTOMERGE_LABEL_STR,
    BUILD_CONTAINER_STR,
    CAN_BE_MERGED_STR,
    CHERRY_PICKED_LABEL_PREFIX,
    CONVENTIONAL_TITLE_STR,
    FAILURE_STR,
    IN_PROGRESS_STR,
    PYTHON_MODULE_INSTALL_STR,
    QUEUED_STR,
    SUCCESS_STR,
    TOX_STR,
    VERIFIED_LABEL_STR,
)
from webhook_server.utils.helpers import strip_ansi_codes

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook
    from webhook_server.utils.context import WebhookContext


class CheckRunHandler:
    def __init__(self, github_webhook: "GithubWebhook", owners_file_handler: OwnersFileHandler | None = None):
        self.github_webhook = github_webhook
        self.ctx: WebhookContext | None = github_webhook.ctx
        self.owners_file_handler = owners_file_handler
        self.hook_data = self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix: str = self.github_webhook.log_prefix
        self.repository: Repository = self.github_webhook.repository
        self._repository_private: bool | None = None
        self._branch_required_status_checks: list[str] | None = None
        self._all_required_status_checks: list[str] | None = None
        if isinstance(self.owners_file_handler, OwnersFileHandler):
            self.labels_handler = LabelsHandler(
                github_webhook=self.github_webhook, owners_file_handler=self.owners_file_handler
            )

    async def process_pull_request_check_run_webhook_data(self, pull_request: PullRequest | None = None) -> bool:
        """Return True if check_if_can_be_merged need to run"""
        if self.ctx:
            self.ctx.start_step("check_run_handler")

        _check_run: dict[str, Any] = self.hook_data["check_run"]
        check_run_name: str = _check_run["name"]

        if self.hook_data.get("action", "") != "completed":
            self.logger.debug(
                f"{self.log_prefix} check run {check_run_name} action is "
                f"{self.hook_data.get('action', 'N/A')} and not completed, skipping"
            )
            if self.ctx:
                self.ctx.complete_step("check_run_handler")
            return False

        check_run_status: str = _check_run["status"]
        check_run_conclusion: str = _check_run["conclusion"]
        self.logger.debug(
            f"{self.log_prefix} processing check_run - Name: {check_run_name} "
            f"Status: {check_run_status} Conclusion: {check_run_conclusion}"
        )

        if check_run_name == CAN_BE_MERGED_STR:
            if getattr(self, "labels_handler", None) and pull_request and check_run_conclusion == SUCCESS_STR:
                if await self.labels_handler.label_exists_in_pull_request(
                    label=AUTOMERGE_LABEL_STR, pull_request=pull_request
                ):
                    try:
                        await asyncio.to_thread(pull_request.merge, merge_method="SQUASH")
                        self.logger.info(
                            f"{self.log_prefix} Successfully auto-merged pull request #{pull_request.number}"
                        )
                        if self.ctx:
                            self.ctx.complete_step("check_run_handler")
                        return False
                    except Exception as ex:
                        self.logger.error(
                            f"{self.log_prefix} Failed to auto-merge pull request #{pull_request.number}: {ex}"
                        )
                        if self.ctx:
                            self.ctx.complete_step("check_run_handler")
                        return True

            else:
                self.logger.debug(f"{self.log_prefix} check run is {CAN_BE_MERGED_STR}, skipping")
                if self.ctx:
                    self.ctx.complete_step("check_run_handler")
                return False

        if self.ctx:
            self.ctx.complete_step("check_run_handler")
        return True

    async def set_verify_check_queued(self) -> None:
        return await self.set_check_run_status(check_run=VERIFIED_LABEL_STR, status=QUEUED_STR)

    async def set_verify_check_success(self) -> None:
        return await self.set_check_run_status(check_run=VERIFIED_LABEL_STR, conclusion=SUCCESS_STR)

    async def set_merge_check_queued(self, output: dict[str, Any] | None = None) -> None:
        return await self.set_check_run_status(check_run=CAN_BE_MERGED_STR, status=QUEUED_STR, output=output)

    async def set_merge_check_in_progress(self) -> None:
        return await self.set_check_run_status(check_run=CAN_BE_MERGED_STR, status=IN_PROGRESS_STR)

    async def set_merge_check_success(self) -> None:
        return await self.set_check_run_status(check_run=CAN_BE_MERGED_STR, conclusion=SUCCESS_STR)

    async def set_merge_check_failure(self, output: dict[str, Any]) -> None:
        return await self.set_check_run_status(check_run=CAN_BE_MERGED_STR, conclusion=FAILURE_STR, output=output)

    async def set_cherry_pick_in_progress(self) -> None:
        return await self.set_check_run_status(check_run=CHERRY_PICKED_LABEL_PREFIX, status=IN_PROGRESS_STR)

    async def set_cherry_pick_success(self, output: dict[str, Any]) -> None:
        return await self.set_check_run_status(
            check_run=CHERRY_PICKED_LABEL_PREFIX, conclusion=SUCCESS_STR, output=output
        )

    async def set_cherry_pick_failure(self, output: dict[str, Any]) -> None:
        return await self.set_check_run_status(
            check_run=CHERRY_PICKED_LABEL_PREFIX, conclusion=FAILURE_STR, output=output
        )

    async def set_check_queued(self, name: str) -> None:
        """Set check run to queued status.

        Generic method for setting any check run (built-in or custom) to queued status.

        Args:
            name: The name of the check run (e.g., TOX_STR, PRE_COMMIT_STR, or custom check name)
        """
        await self.set_check_run_status(check_run=name, status=QUEUED_STR)

    async def set_check_in_progress(self, name: str) -> None:
        """Set check run to in_progress status.

        Generic method for setting any check run (built-in or custom) to in_progress status.

        Args:
            name: The name of the check run (e.g., TOX_STR, PRE_COMMIT_STR, or custom check name)
        """
        await self.set_check_run_status(check_run=name, status=IN_PROGRESS_STR)

    async def set_check_success(self, name: str, output: dict[str, Any] | None = None) -> None:
        """Set check run to success.

        Generic method for setting any check run (built-in or custom) to success status.

        Args:
            name: The name of the check run (e.g., TOX_STR, PRE_COMMIT_STR, or custom check name)
            output: Optional output dictionary with title, summary, and text fields
        """
        await self.set_check_run_status(check_run=name, conclusion=SUCCESS_STR, output=output)

    async def set_check_failure(self, name: str, output: dict[str, Any] | None = None) -> None:
        """Set check run to failure.

        Generic method for setting any check run (built-in or custom) to failure status.

        Args:
            name: The name of the check run (e.g., TOX_STR, PRE_COMMIT_STR, or custom check name)
            output: Optional output dictionary with title, summary, and text fields
        """
        await self.set_check_run_status(check_run=name, conclusion=FAILURE_STR, output=output)

    async def set_check_run_status(
        self,
        check_run: str,
        status: str = "",
        conclusion: str = "",
        output: dict[str, Any] | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {"name": check_run, "head_sha": self.github_webhook.last_commit.sha}

        if status:
            kwargs["status"] = status

        if conclusion:
            kwargs["conclusion"] = conclusion

        if output:
            kwargs["output"] = output

        msg: str = f"{self.log_prefix} check run {check_run} status: {status or conclusion}"

        try:
            self.logger.debug(f"{self.log_prefix} Set check run status with {kwargs}")
            await asyncio.to_thread(self.github_webhook.repository_by_github_app.create_check_run, **kwargs)
            if conclusion in (SUCCESS_STR, IN_PROGRESS_STR):
                self.logger.info(msg)
            return

        except Exception as ex:
            self.logger.debug(f"{self.log_prefix} Failed to set {check_run} check to {status or conclusion}, {ex}")
            kwargs["conclusion"] = FAILURE_STR
            await asyncio.to_thread(self.github_webhook.repository_by_github_app.create_check_run, **kwargs)

    def get_check_run_text(self, err: str, out: str) -> str:
        # Strip ANSI escape codes from output to prevent scrambled characters in GitHub UI
        err_clean = strip_ansi_codes(err)
        out_clean = strip_ansi_codes(out)

        # GitHub limit is 65535 characters, but we use 65534 to be safe
        # We reserve space for the markdown wrapper: ```\n{err}\n\n{out}\n```
        # Wrapper overhead = len("```\n\n\n```") = 10 characters
        MAX_LEN = 65534
        WRAPPER_OVERHEAD = 10

        # Prepare error part first - we want to preserve it as much as possible
        # If error itself is huge, we might need to truncate it too, but usually it's small
        # If error + wrapper > MAX_LEN, we truncate error
        if len(err_clean) + WRAPPER_OVERHEAD > MAX_LEN:
            err_clean = err_clean[: MAX_LEN - WRAPPER_OVERHEAD - 3] + "..."
            out_clean = ""  # No space for output

        # Calculate remaining space for output
        current_len = len(err_clean) + WRAPPER_OVERHEAD
        remaining_space = MAX_LEN - current_len

        if len(out_clean) > remaining_space:
            # Truncate output: keep start and end
            truncation_msg = "\n...[TRUNCATED]...\n"
            msg_len = len(truncation_msg)

            if remaining_space <= msg_len:
                # Very little space, just take head
                out_clean = out_clean[:remaining_space]
            else:
                # Keep head and tail
                available_content = remaining_space - msg_len
                head_len = available_content // 2
                tail_len = available_content - head_len
                out_clean = out_clean[:head_len] + truncation_msg + out_clean[-tail_len:]

        _output = f"```\n{err_clean}\n\n{out_clean}\n```"

        _hased_str = "*****"

        if self.github_webhook.pypi and self.github_webhook.pypi.get("token"):
            _output = _output.replace(self.github_webhook.pypi["token"], _hased_str)

        if getattr(self.github_webhook, "container_repository_username", None):
            _output = _output.replace(self.github_webhook.container_repository_username, _hased_str)

        if getattr(self.github_webhook, "container_repository_password", None):
            _output = _output.replace(self.github_webhook.container_repository_password, _hased_str)

        if self.github_webhook.token:
            _output = _output.replace(self.github_webhook.token, _hased_str)

        return _output

    async def is_check_run_in_progress(self, check_run: str) -> bool:
        if self.github_webhook.last_commit:
            for run in await asyncio.to_thread(self.github_webhook.last_commit.get_check_runs):
                if run.name == check_run and run.status == IN_PROGRESS_STR:
                    self.logger.debug(f"{self.log_prefix} Check run {check_run} is in progress.")
                    return True
        return False

    async def required_check_failed_or_no_status(
        self,
        pull_request: PullRequest,
        last_commit_check_runs: list[CheckRun],
        last_commit_statuses: list[CommitStatus],
        check_runs_in_progress: list[str],
    ) -> str:
        failed_check_runs: list[str] = []

        # Find required checks that are missing entirely from check runs list
        required_checks = set(await self.all_required_status_checks(pull_request=pull_request))
        existing_check_names = {cr.name for cr in last_commit_check_runs}
        missing_required_checks = required_checks - existing_check_names

        # Add missing checks to no_status list (these haven't been created yet)
        no_status_check_runs: list[str] = list(missing_required_checks)

        # Add commit statuses (legacy API) to existing checks
        status_check_names = {status.context for status in last_commit_statuses}
        existing_check_names = existing_check_names | status_check_names

        # Recalculate missing checks after adding statuses
        missing_required_checks = required_checks - existing_check_names
        no_status_check_runs = list(missing_required_checks)

        # Check commit statuses for failures/pending
        self.logger.debug(f"{self.log_prefix} Status details: {[(s.context, s.state) for s in last_commit_statuses]}")

        # Filter to latest status per context (highest ID = most recent)
        status_by_context: dict[str, CommitStatus] = {}
        for status in last_commit_statuses:
            if status.context not in status_by_context or status.id > status_by_context[status.context].id:
                status_by_context[status.context] = status

        latest_statuses = list(status_by_context.values())
        self.logger.debug(
            f"{self.log_prefix} Filtered {len(last_commit_statuses)} statuses to {len(latest_statuses)} latest statuses"
        )

        for status in latest_statuses:
            if status.context not in required_checks:
                continue  # Not a required check

            if status.state == "success":
                continue  # Passed

            if status.state == "pending":
                # Skip if already marked as in-progress (to avoid duplicate reporting)
                if status.context in check_runs_in_progress:
                    continue
                if status.context not in no_status_check_runs:
                    no_status_check_runs.append(status.context)
            elif status.state in ("failure", "error"):
                if status.context not in failed_check_runs:
                    failed_check_runs.append(status.context)

        for check_run in last_commit_check_runs:
            # Skip check runs that have a corresponding success status
            status_contexts = {status.context for status in latest_statuses if status.state == "success"}
            if check_run.name in status_contexts:
                continue

            if (
                check_run.name == CAN_BE_MERGED_STR
                or check_run.conclusion == SUCCESS_STR
                or check_run.name not in await self.all_required_status_checks(pull_request=pull_request)
            ):
                continue

            if check_run.conclusion is None:
                if check_run.name not in no_status_check_runs:
                    no_status_check_runs.append(check_run.name)

            else:
                if check_run.name not in failed_check_runs:
                    failed_check_runs.append(check_run.name)

        self.logger.debug(f"{self.log_prefix} no_status_check_runs after processing check runs: {no_status_check_runs}")
        self.logger.debug(f"{self.log_prefix} failed_check_runs after processing check runs: {failed_check_runs}")

        msg = ""

        if failed_check_runs:
            exclude_in_progress = [
                failed_check_run
                for failed_check_run in failed_check_runs
                if failed_check_run not in check_runs_in_progress
            ]
            msg += f"Some check runs failed: {', '.join(exclude_in_progress)}\n"
        self.logger.debug(f"{self.log_prefix} failed_check_runs: {failed_check_runs}")

        if no_status_check_runs:
            msg += f"Some check runs not started: {', '.join(no_status_check_runs)}\n"
        self.logger.debug(f"{self.log_prefix} no_status_check_runs: {no_status_check_runs}")

        return msg

    async def all_required_status_checks(self, pull_request: PullRequest) -> list[str]:
        # Cache to avoid repeated processing
        if self._all_required_status_checks is not None:
            return self._all_required_status_checks

        all_required_status_checks: list[str] = []
        branch_required_status_checks = await self.get_branch_required_status_checks(pull_request=pull_request)

        if self.github_webhook.tox:
            all_required_status_checks.append(TOX_STR)

        if self.github_webhook.verified_job:
            all_required_status_checks.append(VERIFIED_LABEL_STR)

        if self.github_webhook.build_and_push_container:
            all_required_status_checks.append(BUILD_CONTAINER_STR)

        if self.github_webhook.pypi:
            all_required_status_checks.append(PYTHON_MODULE_INSTALL_STR)

        if self.github_webhook.conventional_title:
            all_required_status_checks.append(CONVENTIONAL_TITLE_STR)

        # Add mandatory custom checks only (default is mandatory=true for backward compatibility)
        # Note: custom checks are validated in GithubWebhook._validate_custom_check_runs()
        # so name is guaranteed to exist
        for custom_check in self.github_webhook.custom_check_runs:
            if custom_check.get("mandatory", True):  # Default to True for backward compatibility
                check_name = custom_check["name"]
                all_required_status_checks.append(check_name)

        _all_required_status_checks = branch_required_status_checks + all_required_status_checks
        self.logger.debug(f"{self.log_prefix} All required status checks: {_all_required_status_checks}")
        self._all_required_status_checks = _all_required_status_checks
        return _all_required_status_checks

    async def get_branch_required_status_checks(self, pull_request: PullRequest) -> list[str]:
        # Check if private repo first (cache to avoid repeated API calls)
        if self._repository_private is None:
            self._repository_private = await asyncio.to_thread(lambda: self.repository.private)

        if self._repository_private:
            self.logger.info(
                f"{self.log_prefix} Repository is private, skipping getting branch protection required status checks"
            )
            return []

        # Cache branch protection settings in instance variable to avoid repeated API calls
        if self._branch_required_status_checks is not None:
            return self._branch_required_status_checks

        pull_request_branch = await asyncio.to_thread(self.repository.get_branch, pull_request.base.ref)
        branch_protection = await asyncio.to_thread(pull_request_branch.get_protection)
        branch_required_status_checks = await asyncio.to_thread(
            lambda: branch_protection.required_status_checks.contexts
        )
        self.logger.debug(f"{self.log_prefix} branch_required_status_checks: {branch_required_status_checks}")
        self._branch_required_status_checks = branch_required_status_checks
        return self._branch_required_status_checks

    async def required_check_in_progress(
        self,
        pull_request: PullRequest,
        last_commit_check_runs: list[CheckRun],
    ) -> tuple[str, list[str]]:
        self.logger.debug(f"{self.log_prefix} Check if any required check runs in progress.")

        check_runs_in_progress = [
            check_run.name
            for check_run in last_commit_check_runs
            if check_run.status == IN_PROGRESS_STR
            and check_run.name != CAN_BE_MERGED_STR
            and check_run.name in await self.all_required_status_checks(pull_request=pull_request)
        ]

        # Note: Status API doesn't have an "in_progress" state - only pending (queued),
        # success, failure, and error. We only check Check Runs for in-progress status.

        if check_runs_in_progress:
            self.logger.debug(
                f"{self.log_prefix} Some required check runs in progress {check_runs_in_progress}, "
                f"skipping check if {CAN_BE_MERGED_STR}."
            )
            return f"Some required check runs in progress {', '.join(check_runs_in_progress)}\n", check_runs_in_progress
        return "", []
