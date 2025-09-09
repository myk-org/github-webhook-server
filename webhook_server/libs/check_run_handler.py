import asyncio
from typing import TYPE_CHECKING, Any

from github.CheckRun import CheckRun
from github.PullRequest import PullRequest
from github.Repository import Repository

from webhook_server.libs.labels_handler import LabelsHandler
from webhook_server.libs.owners_files_handler import OwnersFileHandler
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
        if isinstance(self.owners_file_handler, OwnersFileHandler):
            self.labels_handler = LabelsHandler(
                github_webhook=self.github_webhook, owners_file_handler=self.owners_file_handler
            )

    async def process_pull_request_check_run_webhook_data(self, pull_request: PullRequest | None = None) -> bool:
        """Return True if check_if_can_be_merged need to run"""

        _check_run: dict[str, Any] = self.hook_data["check_run"]
        check_run_name: str = _check_run["name"]

        self.logger.step(f"{self.log_prefix} Processing check run: {check_run_name}")  # type: ignore

        if self.hook_data.get("action", "") != "completed":
            self.logger.debug(
                f"{self.log_prefix} check run {check_run_name} action is {self.hook_data.get('action', 'N/A')} and not completed, skipping"
            )
            return False

        check_run_status: str = _check_run["status"]
        check_run_conclusion: str = _check_run["conclusion"]
        self.logger.debug(
            f"{self.log_prefix} processing check_run - Name: {check_run_name} Status: {check_run_status} Conclusion: {check_run_conclusion}"
        )

        if check_run_name == CAN_BE_MERGED_STR:
            if getattr(self, "labels_handler", None) and pull_request and check_run_conclusion == SUCCESS_STR:
                if await self.labels_handler.label_exists_in_pull_request(
                    label=AUTOMERGE_LABEL_STR, pull_request=pull_request
                ):
                    try:
                        self.logger.step(f"{self.log_prefix} Executing auto-merge for PR #{pull_request.number}")  # type: ignore
                        await asyncio.to_thread(pull_request.merge, merge_method="SQUASH")
                        self.logger.step(f"{self.log_prefix} Auto-merge completed successfully")  # type: ignore
                        self.logger.info(
                            f"{self.log_prefix} Successfully auto-merged pull request #{pull_request.number}"
                        )
                        return False
                    except Exception as ex:
                        self.logger.error(
                            f"{self.log_prefix} Failed to auto-merge pull request #{pull_request.number}: {ex}"
                        )
                        # Continue processing to allow manual intervention
                        return True

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
        kwargs: dict[str, Any] = {"name": check_run, "head_sha": self.github_webhook.last_commit.sha}

        if status:
            kwargs["status"] = status

        if conclusion:
            kwargs["conclusion"] = conclusion

        if output:
            kwargs["output"] = output

        msg: str = f"{self.log_prefix} check run {check_run} status: {status or conclusion}"

        # Log workflow steps for check run status changes
        if status == QUEUED_STR:
            self.logger.step(f"{self.log_prefix} Setting {check_run} check to queued")  # type: ignore
        elif status == IN_PROGRESS_STR:
            self.logger.step(f"{self.log_prefix} Setting {check_run} check to in-progress")  # type: ignore
        elif conclusion == SUCCESS_STR:
            self.logger.step(f"{self.log_prefix} Setting {check_run} check to success")  # type: ignore
        elif conclusion == FAILURE_STR:
            self.logger.step(f"{self.log_prefix} Setting {check_run} check to failure")  # type: ignore

        try:
            self.logger.debug(f"{self.log_prefix} Set check run status with {kwargs}")
            await asyncio.to_thread(self.github_webhook.repository_by_github_app.create_check_run, **kwargs)
            if conclusion in (SUCCESS_STR, IN_PROGRESS_STR):
                self.logger.success(msg)  # type: ignore
            return

        except Exception as ex:
            self.logger.debug(f"{self.log_prefix} Failed to set {check_run} check to {status or conclusion}, {ex}")
            kwargs["conclusion"] = FAILURE_STR
            await asyncio.to_thread(self.github_webhook.repository_by_github_app.create_check_run, **kwargs)

    def get_check_run_text(self, err: str, out: str) -> str:
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
            for run in await asyncio.to_thread(self.github_webhook.last_commit.get_check_runs):
                if run.name == check_run and run.status == IN_PROGRESS_STR:
                    self.logger.debug(f"{self.log_prefix} Check run {check_run} is in progress.")
                    return True
        return False

    async def required_check_failed_or_no_status(
        self, pull_request: PullRequest, last_commit_check_runs: list[CheckRun], check_runs_in_progress: list[str]
    ) -> str:
        failed_check_runs: list[str] = []
        no_status_check_runs: list[str] = []

        for check_run in last_commit_check_runs:
            self.logger.debug(f"{self.log_prefix} Check if {check_run.name} failed or do not have status.")
            if (
                check_run.name == CAN_BE_MERGED_STR
                or check_run.conclusion == SUCCESS_STR
                or check_run.name not in await self.all_required_status_checks(pull_request=pull_request)
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

    async def all_required_status_checks(self, pull_request: PullRequest) -> list[str]:
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
        return _all_required_status_checks

    async def get_branch_required_status_checks(self, pull_request: PullRequest) -> list[str]:
        if self.repository.private:
            self.logger.info(
                f"{self.log_prefix} Repository is private, skipping getting branch protection required status checks"
            )
            return []

        pull_request_branch = await asyncio.to_thread(self.repository.get_branch, pull_request.base.ref)
        branch_protection = await asyncio.to_thread(pull_request_branch.get_protection)
        branch_required_status_checks = branch_protection.required_status_checks.contexts
        self.logger.debug(f"branch_required_status_checks: {branch_required_status_checks}")
        return branch_required_status_checks

    async def required_check_in_progress(
        self, pull_request: PullRequest, last_commit_check_runs: list[CheckRun]
    ) -> tuple[str, list[str]]:
        self.logger.debug(f"{self.log_prefix} Check if any required check runs in progress.")

        check_runs_in_progress = [
            check_run.name
            for check_run in last_commit_check_runs
            if check_run.status == IN_PROGRESS_STR
            and check_run.name != CAN_BE_MERGED_STR
            and check_run.name in await self.all_required_status_checks(pull_request=pull_request)
        ]
        if check_runs_in_progress:
            self.logger.debug(
                f"{self.log_prefix} Some required check runs in progress {check_runs_in_progress}, "
                f"skipping check if {CAN_BE_MERGED_STR}."
            )
            return f"Some required check runs in progress {', '.join(check_runs_in_progress)}\n", check_runs_in_progress
        return "", []
