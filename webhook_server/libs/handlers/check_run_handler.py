from typing import TYPE_CHECKING, Any

from github.CheckRun import CheckRun
from github.GithubException import GithubException
from github.Repository import Repository

from webhook_server.libs.graphql.graphql_client import GraphQLError
from webhook_server.libs.graphql.webhook_data import PullRequestWrapper
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
    PRE_COMMIT_STR,
    PYTHON_MODULE_INSTALL_STR,
    QUEUED_STR,
    SUCCESS_STR,
    TOX_STR,
    VERIFIED_LABEL_STR,
)
from webhook_server.utils.helpers import format_task_fields, strip_ansi_codes

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook


class CheckRunHandler:
    def __init__(self, github_webhook: "GithubWebhook", owners_file_handler: OwnersFileHandler | None = None):
        self.github_webhook = github_webhook
        self.owners_file_handler = owners_file_handler
        self.hook_data = self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix: str = self.github_webhook.log_prefix
        self.repository: Repository = self.github_webhook.repository
        self.unified_api = self.github_webhook.unified_api
        if isinstance(self.owners_file_handler, OwnersFileHandler):
            self.labels_handler = LabelsHandler(
                github_webhook=self.github_webhook, owners_file_handler=self.owners_file_handler
            )
        # Cache for all_required_status_checks per handler invocation
        self._required_checks_cache: dict[str, list[str]] = {}

    async def process_pull_request_check_run_webhook_data(self, pull_request: PullRequestWrapper | None = None) -> bool:
        """Return True if check_if_can_be_merged need to run"""

        _check_run: dict[str, Any] = self.hook_data["check_run"]
        check_run_name: str = _check_run["name"]

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('check_run', 'ci_check', 'processing')} "
            f"Processing check run: {check_run_name}",
        )

        if self.hook_data.get("action", "") != "completed":
            self.logger.debug(
                f"{self.log_prefix} check run {check_run_name} action is "
                f"{self.hook_data.get('action', 'N/A')} and not completed, skipping"
            )
            return False

        check_run_status: str = _check_run["status"]
        check_run_conclusion: str = _check_run["conclusion"]
        self.logger.debug(
            f"{self.log_prefix} processing check_run - Name: {check_run_name} "
            f"Status: {check_run_status} Conclusion: {check_run_conclusion}"
        )

        # Log completion at appropriate level based on conclusion
        if check_run_conclusion == SUCCESS_STR:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('check_run', 'ci_check', 'completed')} "
                f"Check run {check_run_name} completed with SUCCESS",
            )
        elif check_run_conclusion == FAILURE_STR:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('check_run', 'ci_check', 'failed')} "
                f"Check run {check_run_name} completed with FAILURE",
            )
        elif check_run_conclusion:  # Other conclusions (cancelled, skipped, etc.)
            self.logger.info(f"{self.log_prefix} Check run {check_run_name} completed with {check_run_conclusion}")

        if check_run_name == CAN_BE_MERGED_STR:
            if getattr(self, "labels_handler", None) and pull_request and check_run_conclusion == SUCCESS_STR:
                if await self.labels_handler.label_exists_in_pull_request(
                    label=AUTOMERGE_LABEL_STR, pull_request=pull_request
                ):
                    try:
                        self.logger.step(  # type: ignore[attr-defined]
                            f"{self.log_prefix} {format_task_fields('check_run', 'ci_check', 'processing')} "
                            f"Executing auto-merge for PR #{pull_request.number}",
                        )
                        owner, repo_name = self.github_webhook.owner_and_repo
                        await self.unified_api.merge_pull_request(
                            owner, repo_name, pull_request.number, merge_method="SQUASH"
                        )
                        self.logger.step(  # type: ignore[attr-defined]
                            f"{self.log_prefix} {format_task_fields('check_run', 'ci_check', 'completed')} "
                            f"Auto-merge completed successfully",
                        )
                        self.logger.info(
                            f"{self.log_prefix} Successfully auto-merged pull request #{pull_request.number}"
                        )
                        return False
                    except (GraphQLError, GithubException):
                        # Log full exception with traceback for debugging
                        self.logger.exception(
                            f"{self.log_prefix} Failed to auto-merge pull request #{pull_request.number}"
                        )
                        # Send sanitized message to PR (no sensitive exception details)
                        failure_msg = (
                            f"⚠️ **Auto-merge failed**\n\n"
                            f"The PR has the `{AUTOMERGE_LABEL_STR}` label and all checks passed, "
                            f"but auto-merge encountered an error.\n\n"
                            f"Please merge manually or contact the repository maintainers for assistance."
                        )
                        await self.github_webhook.unified_api.add_pr_comment(pull_request, failure_msg)
                        return False

            else:
                self.logger.debug(f"{self.log_prefix} check run is {CAN_BE_MERGED_STR}, skipping")
                return False

        return True

    async def set_verify_check_queued(self) -> None:
        return await self.set_check_run_status(check_run=VERIFIED_LABEL_STR, status=QUEUED_STR)

    async def set_verify_check_success(self) -> None:
        return await self.set_check_run_status(check_run=VERIFIED_LABEL_STR, conclusion=SUCCESS_STR)

    async def set_run_tox_check_queued(self) -> None:
        if not self.github_webhook.tox:
            self.logger.debug(f"{self.log_prefix} tox is not configured, skipping.")
            return

        return await self.set_check_run_status(check_run=TOX_STR, status=QUEUED_STR)

    async def set_run_tox_check_in_progress(self) -> None:
        return await self.set_check_run_status(check_run=TOX_STR, status=IN_PROGRESS_STR)

    async def set_run_tox_check_failure(self, output: dict[str, Any]) -> None:
        return await self.set_check_run_status(check_run=TOX_STR, conclusion=FAILURE_STR, output=output)

    async def set_run_tox_check_success(self, output: dict[str, Any]) -> None:
        return await self.set_check_run_status(check_run=TOX_STR, conclusion=SUCCESS_STR, output=output)

    async def set_run_pre_commit_check_queued(self) -> None:
        if not self.github_webhook.pre_commit:
            self.logger.debug(f"{self.log_prefix} pre-commit is not configured, skipping.")
            return

        return await self.set_check_run_status(check_run=PRE_COMMIT_STR, status=QUEUED_STR)

    async def set_run_pre_commit_check_in_progress(self) -> None:
        return await self.set_check_run_status(check_run=PRE_COMMIT_STR, status=IN_PROGRESS_STR)

    async def set_run_pre_commit_check_failure(self, output: dict[str, Any] | None = None) -> None:
        return await self.set_check_run_status(check_run=PRE_COMMIT_STR, conclusion=FAILURE_STR, output=output)

    async def set_run_pre_commit_check_success(self, output: dict[str, Any] | None = None) -> None:
        return await self.set_check_run_status(check_run=PRE_COMMIT_STR, conclusion=SUCCESS_STR, output=output)

    async def set_merge_check_queued(self, output: dict[str, Any] | None = None) -> None:
        return await self.set_check_run_status(check_run=CAN_BE_MERGED_STR, status=QUEUED_STR, output=output)

    async def set_merge_check_in_progress(self) -> None:
        return await self.set_check_run_status(check_run=CAN_BE_MERGED_STR, status=IN_PROGRESS_STR)

    async def set_merge_check_success(self) -> None:
        return await self.set_check_run_status(check_run=CAN_BE_MERGED_STR, conclusion=SUCCESS_STR)

    async def set_merge_check_failure(self, output: dict[str, Any]) -> None:
        return await self.set_check_run_status(check_run=CAN_BE_MERGED_STR, conclusion=FAILURE_STR, output=output)

    async def set_container_build_queued(self) -> None:
        if not self.github_webhook.build_and_push_container:
            self.logger.debug(f"{self.log_prefix} build_and_push_container is not configured, skipping.")
            return

        return await self.set_check_run_status(check_run=BUILD_CONTAINER_STR, status=QUEUED_STR)

    async def set_container_build_in_progress(self) -> None:
        return await self.set_check_run_status(check_run=BUILD_CONTAINER_STR, status=IN_PROGRESS_STR)

    async def set_container_build_success(self, output: dict[str, Any]) -> None:
        return await self.set_check_run_status(check_run=BUILD_CONTAINER_STR, conclusion=SUCCESS_STR, output=output)

    async def set_container_build_failure(self, output: dict[str, Any]) -> None:
        return await self.set_check_run_status(check_run=BUILD_CONTAINER_STR, conclusion=FAILURE_STR, output=output)

    async def set_python_module_install_queued(self) -> None:
        if not self.github_webhook.pypi:
            self.logger.debug(f"{self.log_prefix} pypi is not configured, skipping.")
            return

        return await self.set_check_run_status(check_run=PYTHON_MODULE_INSTALL_STR, status=QUEUED_STR)

    async def set_python_module_install_in_progress(self) -> None:
        return await self.set_check_run_status(check_run=PYTHON_MODULE_INSTALL_STR, status=IN_PROGRESS_STR)

    async def set_python_module_install_success(self, output: dict[str, Any]) -> None:
        return await self.set_check_run_status(
            check_run=PYTHON_MODULE_INSTALL_STR, conclusion=SUCCESS_STR, output=output
        )

    async def set_python_module_install_failure(self, output: dict[str, Any]) -> None:
        return await self.set_check_run_status(
            check_run=PYTHON_MODULE_INSTALL_STR, conclusion=FAILURE_STR, output=output
        )

    async def set_conventional_title_queued(self) -> None:
        return await self.set_check_run_status(check_run=CONVENTIONAL_TITLE_STR, status=QUEUED_STR)

    async def set_conventional_title_in_progress(self) -> None:
        return await self.set_check_run_status(check_run=CONVENTIONAL_TITLE_STR, status=IN_PROGRESS_STR)

    async def set_conventional_title_success(self, output: dict[str, Any]) -> None:
        return await self.set_check_run_status(check_run=CONVENTIONAL_TITLE_STR, conclusion=SUCCESS_STR, output=output)

    async def set_conventional_title_failure(self, output: dict[str, Any]) -> None:
        return await self.set_check_run_status(check_run=CONVENTIONAL_TITLE_STR, conclusion=FAILURE_STR, output=output)

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

    async def set_check_run_status(
        self,
        check_run: str,
        status: str = "",
        conclusion: str = "",
        output: dict[str, str] | None = None,
    ) -> None:
        # Guard against missing last_commit
        if not self.github_webhook.last_commit:
            self.logger.debug(f"{self.log_prefix} No last_commit available, skipping check run status update")
            return

        kwargs: dict[str, Any] = {"name": check_run, "head_sha": self.github_webhook.last_commit.sha}

        if status:
            kwargs["status"] = status

        if conclusion:
            kwargs["conclusion"] = conclusion

        if output:
            kwargs["output"] = output

        msg: str = f"{self.log_prefix} check run {check_run} status: {status or conclusion}"

        if status == QUEUED_STR:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('check_run', 'ci_check', 'processing')} "
                f"Setting {check_run} check to queued",
            )
        elif status == IN_PROGRESS_STR:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('check_run', 'ci_check', 'processing')} "
                f"Setting {check_run} check to in-progress",
            )
        elif conclusion == SUCCESS_STR:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('check_run', 'ci_check', 'processing')} "
                f"Setting {check_run} check to success",
            )
        elif conclusion == FAILURE_STR:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('check_run', 'ci_check', 'processing')} "
                f"Setting {check_run} check to failure",
            )

        try:
            self.logger.debug(f"{self.log_prefix} Set check run status with {kwargs}")
            await self.unified_api.create_check_run(self.github_webhook.repository_by_github_app, **kwargs)
        except (GraphQLError, GithubException) as ex:
            # Check if error is auth/permission/rate-limit (don't retry these)
            error_str = str(ex).lower()
            is_critical_error = any(
                keyword in error_str
                for keyword in ["auth", "permission", "forbidden", "rate limit", "unauthorized", "401", "403"]
            )

            if is_critical_error:
                self.logger.exception(
                    f"{self.log_prefix} Failed to set {check_run} check to {status or conclusion}. "
                    "Not retrying due to auth/permission/rate-limit error."
                )
                raise  # Don't hide auth/permission/rate-limit errors
            else:
                # For transient errors, log the failure without attempting retry
                # Retrying here could cause cascading failures if the same error occurs again
                self.logger.exception(
                    f"{self.log_prefix} Failed to set {check_run} check to {status or conclusion}. "
                    "Check run may be in inconsistent state."
                )
        except Exception:
            # Handle non-GraphQL errors (e.g., network issues, PyGithub errors)
            self.logger.exception(f"{self.log_prefix} Failed to set {check_run} check to {status or conclusion}")
            # Don't retry for unknown errors to prevent cascading failures
        else:
            # Success log only after successful check run creation
            if conclusion == SUCCESS_STR:
                self.logger.success(msg)  # type: ignore[attr-defined]
            elif status in (IN_PROGRESS_STR, QUEUED_STR):
                self.logger.info(msg)

    def get_check_run_text(self, err: str, out: str) -> str:
        # Strip ANSI escape codes first to prevent scrambled output in GitHub check-runs
        err = strip_ansi_codes(err)
        out = strip_ansi_codes(out)

        total_len: int = len(err) + len(out)

        if total_len > 65534:  # GitHub limit is 65535 characters
            _output = f"```\n{err}\n\n{out}\n```"[:65534]
        else:
            _output = f"```\n{err}\n\n{out}\n```"

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
            owner, repo_name = self.github_webhook.owner_and_repo
            for run in await self.unified_api.get_commit_check_runs(self.github_webhook.last_commit, owner, repo_name):
                if run.name == check_run and run.status == IN_PROGRESS_STR:
                    self.logger.debug(f"{self.log_prefix} Check run {check_run} is in progress.")
                    return True
        return False

    async def required_check_failed_or_no_status(
        self,
        pull_request: PullRequestWrapper,
        last_commit_check_runs: list[CheckRun],
        check_runs_in_progress: list[str],
    ) -> str:
        failed_check_runs: list[str] = []
        no_status_check_runs: list[str] = []

        # Cache required status checks to reduce API calls
        required_checks = await self.all_required_status_checks(pull_request=pull_request)

        for check_run in last_commit_check_runs:
            self.logger.debug(f"{self.log_prefix} Check if {check_run.name} failed or do not have status.")
            if (
                check_run.name == CAN_BE_MERGED_STR
                or check_run.conclusion == SUCCESS_STR
                or check_run.name not in required_checks
            ):
                self.logger.debug(f"{self.log_prefix} {check_run.name} is success or not required, skipping.")
                continue

            if check_run.conclusion is None:
                no_status_check_runs.append(check_run.name)

            else:
                failed_check_runs.append(check_run.name)

        msg = ""

        if failed_check_runs:
            exclude_in_progress = [
                failed_check_run
                for failed_check_run in failed_check_runs
                if failed_check_run not in check_runs_in_progress
            ]
            msg += f"Some check runs failed: {', '.join(exclude_in_progress)}\n"
        self.logger.debug(f"failed_check_runs: {failed_check_runs}")

        if no_status_check_runs:
            msg += f"Some check runs not started: {', '.join(no_status_check_runs)}\n"
        self.logger.debug(f"no_status_check_runs: {no_status_check_runs}")

        return msg

    async def all_required_status_checks(self, pull_request: PullRequestWrapper) -> list[str]:
        # Cache key based on PR base ref (branch name)
        cache_key = pull_request.base.ref

        if cache_key in self._required_checks_cache:
            self.logger.debug(f"{self.log_prefix} Using cached required status checks for branch {cache_key}")
            return self._required_checks_cache[cache_key]

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

        _all_required_status_checks = branch_required_status_checks + all_required_status_checks
        self.logger.debug(f"{self.log_prefix} All required status checks: {_all_required_status_checks}")

        self._required_checks_cache[cache_key] = _all_required_status_checks

        return _all_required_status_checks

    async def get_branch_required_status_checks(self, pull_request: PullRequestWrapper) -> list[str]:
        if self.repository.private:
            self.logger.info(
                f"{self.log_prefix} Repository is private, skipping getting branch protection required status checks"
            )
            return []

        owner, repo_name = self.github_webhook.owner_and_repo

        try:
            branch_protection = await self.unified_api.get_branch_protection(owner, repo_name, pull_request.base.ref)
        except GithubException as ex:
            if ex.status == 404:
                # Branch protection not configured
                self.logger.debug(
                    f"{self.log_prefix} No branch protection configured for branch {pull_request.base.ref}"
                )
                return []
            # Re-raise other GithubException errors (auth, permission, rate-limit, etc.)
            raise

        # Guard against None - PyGithub may return None for required_status_checks if not configured
        if branch_protection.required_status_checks is None:
            self.logger.debug(
                f"{self.log_prefix} No required status checks configured for branch {pull_request.base.ref}"
            )
            return []

        # Guard against None contexts - may be None even when required_status_checks exists
        branch_required_status_checks = branch_protection.required_status_checks.contexts or []
        self.logger.debug(f"branch_required_status_checks: {branch_required_status_checks}")
        return branch_required_status_checks

    async def required_check_in_progress(
        self, pull_request: PullRequestWrapper, last_commit_check_runs: list[CheckRun]
    ) -> tuple[str, list[str]]:
        self.logger.debug(f"{self.log_prefix} Check if any required check runs in progress.")

        required_checks = await self.all_required_status_checks(pull_request=pull_request)
        check_runs_in_progress = [
            check_run.name
            for check_run in last_commit_check_runs
            if check_run.status == IN_PROGRESS_STR
            and check_run.name != CAN_BE_MERGED_STR
            and check_run.name in required_checks
        ]
        if check_runs_in_progress:
            self.logger.debug(
                f"{self.log_prefix} Some required check runs in progress {check_runs_in_progress}, "
                f"skipping check if {CAN_BE_MERGED_STR}."
            )
            return f"Some required check runs in progress {', '.join(check_runs_in_progress)}\n", check_runs_in_progress
        return "", []
