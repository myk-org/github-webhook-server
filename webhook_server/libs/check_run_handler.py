import functools
from typing import Any

from github.CheckRun import CheckRun

from webhook_server.utils.constants import (
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


class CheckRunHandler:
    def __init__(self, github_webhook: Any):
        self.github_webhook = github_webhook
        self.hook_data = self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix = self.github_webhook.log_prefix
        self.repository = self.github_webhook.repository
        self.pull_request = getattr(self.github_webhook, "pull_request", None)

    def process_pull_request_check_run_webhook_data(self) -> None:
        _check_run: dict[str, Any] = self.hook_data["check_run"]
        check_run_name: str = _check_run["name"]

        if self.hook_data.get("action", "") != "completed":
            self.logger.debug(
                f"{self.log_prefix} check run {check_run_name} action is {self.hook_data.get('action', 'N/A')} and not completed, skipping"
            )
            return

        check_run_status: str = _check_run["status"]
        check_run_conclusion: str = _check_run["conclusion"]
        self.logger.debug(
            f"{self.log_prefix} processing check_run - Name: {check_run_name} Status: {check_run_status} Conclusion: {check_run_conclusion}"
        )

        if check_run_name == CAN_BE_MERGED_STR:
            self.logger.debug(f"{self.log_prefix} check run is {CAN_BE_MERGED_STR}, skipping")
            return

        return self.github_webhook.check_if_can_be_merged()

    def set_verify_check_queued(self) -> None:
        return self.set_check_run_status(check_run=VERIFIED_LABEL_STR, status=QUEUED_STR)

    def set_verify_check_success(self) -> None:
        return self.set_check_run_status(check_run=VERIFIED_LABEL_STR, conclusion=SUCCESS_STR)

    def set_run_tox_check_queued(self) -> None:
        if not self.github_webhook.tox:
            return

        return self.set_check_run_status(check_run=TOX_STR, status=QUEUED_STR)

    def set_run_tox_check_in_progress(self) -> None:
        return self.set_check_run_status(check_run=TOX_STR, status=IN_PROGRESS_STR)

    def set_run_tox_check_failure(self, output: dict[str, Any]) -> None:
        return self.set_check_run_status(check_run=TOX_STR, conclusion=FAILURE_STR, output=output)

    def set_run_tox_check_success(self, output: dict[str, Any]) -> None:
        return self.set_check_run_status(check_run=TOX_STR, conclusion=SUCCESS_STR, output=output)

    def set_run_pre_commit_check_queued(self) -> None:
        if not self.github_webhook.pre_commit:
            return

        return self.set_check_run_status(check_run=PRE_COMMIT_STR, status=QUEUED_STR)

    def set_run_pre_commit_check_in_progress(self) -> None:
        return self.set_check_run_status(check_run=PRE_COMMIT_STR, status=IN_PROGRESS_STR)

    def set_run_pre_commit_check_failure(self, output: dict[str, Any] | None = None) -> None:
        return self.set_check_run_status(check_run=PRE_COMMIT_STR, conclusion=FAILURE_STR, output=output)

    def set_run_pre_commit_check_success(self, output: dict[str, Any] | None = None) -> None:
        return self.set_check_run_status(check_run=PRE_COMMIT_STR, conclusion=SUCCESS_STR, output=output)

    def set_merge_check_queued(self, output: dict[str, Any] | None = None) -> None:
        return self.set_check_run_status(check_run=CAN_BE_MERGED_STR, status=QUEUED_STR, output=output)

    def set_merge_check_in_progress(self) -> None:
        return self.set_check_run_status(check_run=CAN_BE_MERGED_STR, status=IN_PROGRESS_STR)

    def set_merge_check_success(self) -> None:
        return self.set_check_run_status(check_run=CAN_BE_MERGED_STR, conclusion=SUCCESS_STR)

    def set_merge_check_failure(self, output: dict[str, Any]) -> None:
        return self.set_check_run_status(check_run=CAN_BE_MERGED_STR, conclusion=FAILURE_STR, output=output)

    def set_container_build_queued(self) -> None:
        if not self.github_webhook.build_and_push_container:
            return

        return self.set_check_run_status(check_run=BUILD_CONTAINER_STR, status=QUEUED_STR)

    def set_container_build_in_progress(self) -> None:
        return self.set_check_run_status(check_run=BUILD_CONTAINER_STR, status=IN_PROGRESS_STR)

    def set_container_build_success(self, output: dict[str, Any]) -> None:
        return self.set_check_run_status(check_run=BUILD_CONTAINER_STR, conclusion=SUCCESS_STR, output=output)

    def set_container_build_failure(self, output: dict[str, Any]) -> None:
        return self.set_check_run_status(check_run=BUILD_CONTAINER_STR, conclusion=FAILURE_STR, output=output)

    def set_python_module_install_queued(self) -> None:
        if not self.github_webhook.pypi:
            return

        return self.set_check_run_status(check_run=PYTHON_MODULE_INSTALL_STR, status=QUEUED_STR)

    def set_python_module_install_in_progress(self) -> None:
        return self.set_check_run_status(check_run=PYTHON_MODULE_INSTALL_STR, status=IN_PROGRESS_STR)

    def set_python_module_install_success(self, output: dict[str, Any]) -> None:
        return self.set_check_run_status(check_run=PYTHON_MODULE_INSTALL_STR, conclusion=SUCCESS_STR, output=output)

    def set_python_module_install_failure(self, output: dict[str, Any]) -> None:
        return self.set_check_run_status(check_run=PYTHON_MODULE_INSTALL_STR, conclusion=FAILURE_STR, output=output)

    def set_conventional_title_queued(self) -> None:
        return self.set_check_run_status(check_run=CONVENTIONAL_TITLE_STR, status=QUEUED_STR)

    def set_conventional_title_in_progress(self) -> None:
        return self.set_check_run_status(check_run=CONVENTIONAL_TITLE_STR, status=IN_PROGRESS_STR)

    def set_conventional_title_success(self, output: dict[str, Any]) -> None:
        return self.set_check_run_status(check_run=CONVENTIONAL_TITLE_STR, conclusion=SUCCESS_STR, output=output)

    def set_conventional_title_failure(self, output: dict[str, Any]) -> None:
        return self.set_check_run_status(check_run=CONVENTIONAL_TITLE_STR, conclusion=FAILURE_STR, output=output)

    def set_cherry_pick_in_progress(self) -> None:
        return self.set_check_run_status(check_run=CHERRY_PICKED_LABEL_PREFIX, status=IN_PROGRESS_STR)

    def set_cherry_pick_success(self, output: dict[str, Any]) -> None:
        return self.set_check_run_status(check_run=CHERRY_PICKED_LABEL_PREFIX, conclusion=SUCCESS_STR, output=output)

    def set_cherry_pick_failure(self, output: dict[str, Any]) -> None:
        return self.set_check_run_status(check_run=CHERRY_PICKED_LABEL_PREFIX, conclusion=FAILURE_STR, output=output)

    def set_check_run_status(
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

        try:
            self.github_webhook.repository_by_github_app.create_check_run(**kwargs)
            if conclusion in (SUCCESS_STR, IN_PROGRESS_STR):
                self.logger.success(msg)
            return

        except Exception as ex:
            self.logger.debug(f"{self.log_prefix} Failed to set {check_run} check to {status or conclusion}, {ex}")
            kwargs["conclusion"] = FAILURE_STR
            self.github_webhook.repository_by_github_app.create_check_run(**kwargs)

    def get_check_run_text(self, err: str, out: str) -> str:
        total_len: int = len(err) + len(out)

        if total_len > 65534:  # GitHub limit is 65535 characters
            _output = f"```\n{err}\n\n{out}\n```"[:65534]
        else:
            _output = f"```\n{err}\n\n{out}\n```"

        _hased_str = "*****"

        if self.github_webhook.pypi and self.github_webhook.pypi.get("token"):
            _output = _output.replace(self.github_webhook.pypi["token"], _hased_str)

        if getattr(self, "container_repository_username", None):
            _output = _output.replace(self.github_webhook.container_repository_username, _hased_str)

        if getattr(self, "container_repository_password", None):
            _output = _output.replace(self.github_webhook.container_repository_password, _hased_str)

        if self.github_webhook.token:
            _output = _output.replace(self.github_webhook.token, _hased_str)

        return _output

    def is_check_run_in_progress(self, check_run: str) -> bool:
        if hasattr(self, "last_commit") and self.github_webhook.last_commit:
            for run in self.github_webhook.last_commit.get_check_runs():
                if run.name == check_run and run.status == IN_PROGRESS_STR:
                    return True
        return False

    def _required_check_failed(self, last_commit_check_runs: list[CheckRun], check_runs_in_progress: list[str]) -> str:
        failed_check_runs = []
        for check_run in last_commit_check_runs:
            self.logger.debug(f"{self.log_prefix} Check if {check_run.name} failed.")
            if (
                check_run.name == CAN_BE_MERGED_STR
                or check_run.conclusion == SUCCESS_STR
                or check_run.conclusion == QUEUED_STR
                or check_run.name not in self.all_required_status_checks
            ):
                continue

            failed_check_runs.append(check_run.name)

        if failed_check_runs:
            exclude_in_progress = [
                failed_check_run
                for failed_check_run in failed_check_runs
                if failed_check_run not in check_runs_in_progress
            ]
            return f"Some check runs failed: {', '.join(exclude_in_progress)}\n"

        return ""

    @functools.cached_property
    def all_required_status_checks(self) -> list[str]:
        if self.pull_request and not hasattr(self, "pull_request_branch"):
            self.pull_request_branch = self.pull_request.base.ref

        all_required_status_checks: list[str] = []
        branch_required_status_checks = self.get_branch_required_status_checks()
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

    def get_branch_required_status_checks(self) -> list[str]:
        if self.repository.private:
            self.logger.info(
                f"{self.log_prefix} Repository is private, skipping getting branch protection required status checks"
            )
            return []

        pull_request_branch = self.repository.get_branch(self.pull_request_branch)
        branch_protection = pull_request_branch.get_protection()
        return branch_protection.required_status_checks.contexts

    def required_check_in_progress(self, last_commit_check_runs: list[CheckRun]) -> tuple[str, list[str]]:
        last_commit_check_runs = list(self.github_webhook.last_commit.get_check_runs())
        self.logger.debug(f"{self.log_prefix} Check if any required check runs in progress.")
        check_runs_in_progress = [
            check_run.name
            for check_run in last_commit_check_runs
            if check_run.status == IN_PROGRESS_STR
            and check_run.name != CAN_BE_MERGED_STR
            and check_run.name in self.all_required_status_checks
        ]
        if check_runs_in_progress:
            self.logger.debug(
                f"{self.log_prefix} Some required check runs in progress {check_runs_in_progress}, "
                f"skipping check if {CAN_BE_MERGED_STR}."
            )
            return f"Some required check runs in progress {', '.join(check_runs_in_progress)}\n", check_runs_in_progress
        return "", []
