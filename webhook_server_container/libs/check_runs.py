import os

import shortuuid

from webhook_server_container.libs.labels import Labels
from webhook_server_container.libs.logs import Logs
from webhook_server_container.utils.constants import (
    APP_ROOT_PATH,
    BUILD_CONTAINER_STR,
    CAN_BE_MERGED_STR,
    CHERRY_PICKED_LABEL_PREFIX,
    FAILURE_STR,
    IN_PROGRESS_STR,
    PYTHON_MODULE_INSTALL_STR,
    QUEUED_STR,
    SONARQUBE_STR,
    SUCCESS_STR,
    TOX_STR,
    VERIFIED_LABEL_STR,
)
from webhook_server_container.utils.helpers import (
    check_rate_limit,
    run_command,
    send_slack_message,
)


class CheckRuns(Labels):
    def __init__(
        self, hook_data, github_event, repositories_app_api, missing_app_repositories
    ):
        super().__init__(
            hook_data=hook_data,
            github_event=github_event,
            repositories_app_api=repositories_app_api,
            missing_app_repositories=missing_app_repositories,
        )

        log = Logs(repository_name=self.repository_name, token=self.token)
        self.logger = log.logger
        self.log_prefix = log.log_prefix

        self.logger.info(f"{self.log_prefix} Check rate limit")
        check_rate_limit()

    def reset_verify_label(self, pull_request):
        self.logger.info(
            f"{self.log_prefix} Processing reset {VERIFIED_LABEL_STR} label on new commit push"
        )
        # Remove verified label
        self.remove_label(label=VERIFIED_LABEL_STR, pull_request=pull_request)

    def set_verify_check_queued(self, last_commit):
        return self.set_check_run_status(
            check_run=VERIFIED_LABEL_STR, status=QUEUED_STR, last_commit=last_commit
        )

    def set_verify_check_success(self, last_commit):
        return self.set_check_run_status(
            check_run=VERIFIED_LABEL_STR,
            conclusion=SUCCESS_STR,
            last_commit=last_commit,
        )

    def set_run_tox_check_queued(self, last_commit):
        if not self.tox_enabled:
            return False

        return self.set_check_run_status(
            check_run=TOX_STR, status=QUEUED_STR, last_commit=last_commit
        )

    def set_run_tox_check_in_progress(self, last_commit):
        return self.set_check_run_status(
            check_run=TOX_STR, status=IN_PROGRESS_STR, last_commit=last_commit
        )

    def set_run_tox_check_failure(self, details_url, last_commit):
        return self.set_check_run_status(
            check_run=TOX_STR,
            conclusion=FAILURE_STR,
            details_url=details_url,
            last_commit=last_commit,
        )

    def set_run_tox_check_success(self, details_url, last_commit):
        return self.set_check_run_status(
            check_run=TOX_STR,
            conclusion=SUCCESS_STR,
            details_url=details_url,
            last_commit=last_commit,
        )

    def set_merge_check_queued(self, last_commit):
        return self.set_check_run_status(
            check_run=CAN_BE_MERGED_STR, status=QUEUED_STR, last_commit=last_commit
        )

    def set_merge_check_in_progress(self, last_commit):
        return self.set_check_run_status(
            check_run=CAN_BE_MERGED_STR, status=IN_PROGRESS_STR, last_commit=last_commit
        )

    def set_merge_check_success(self, last_commit):
        return self.set_check_run_status(
            check_run=CAN_BE_MERGED_STR, conclusion=SUCCESS_STR, last_commit=last_commit
        )

    def set_container_build_queued(self, last_commit):
        if not self.build_and_push_container:
            return

        return self.set_check_run_status(
            check_run=BUILD_CONTAINER_STR, status=QUEUED_STR, last_commit=last_commit
        )

    def set_container_build_in_progress(self, last_commit):
        return self.set_check_run_status(
            check_run=BUILD_CONTAINER_STR,
            status=IN_PROGRESS_STR,
            last_commit=last_commit,
        )

    def set_container_build_success(self, details_url, last_commit):
        return self.set_check_run_status(
            check_run=BUILD_CONTAINER_STR,
            conclusion=SUCCESS_STR,
            details_url=details_url,
            last_commit=last_commit,
        )

    def set_container_build_failure(self, details_url, last_commit):
        return self.set_check_run_status(
            check_run=BUILD_CONTAINER_STR,
            conclusion=FAILURE_STR,
            details_url=details_url,
            last_commit=last_commit,
        )

    def set_python_module_install_queued(self, last_commit):
        if not self.pypi:
            return False

        return self.set_check_run_status(
            check_run=PYTHON_MODULE_INSTALL_STR,
            status=QUEUED_STR,
            last_commit=last_commit,
        )

    def set_python_module_install_in_progress(self, last_commit):
        return self.set_check_run_status(
            check_run=PYTHON_MODULE_INSTALL_STR,
            status=IN_PROGRESS_STR,
            last_commit=last_commit,
        )

    def set_python_module_install_success(self, details_url, last_commit):
        return self.set_check_run_status(
            check_run=PYTHON_MODULE_INSTALL_STR,
            conclusion=SUCCESS_STR,
            details_url=details_url,
            last_commit=last_commit,
        )

    def set_python_module_install_failure(self, details_url, last_commit):
        return self.set_check_run_status(
            check_run=PYTHON_MODULE_INSTALL_STR,
            conclusion=FAILURE_STR,
            details_url=details_url,
            last_commit=last_commit,
        )

    def set_sonarqube_queued(self, last_commit):
        if not self.sonarqube_project_key:
            return False

        return self.set_check_run_status(
            check_run=SONARQUBE_STR, status=QUEUED_STR, last_commit=last_commit
        )

    def set_sonarqube_in_progress(self, last_commit):
        return self.set_check_run_status(
            check_run=SONARQUBE_STR, status=IN_PROGRESS_STR, last_commit=last_commit
        )

    def set_sonarqube_success(self, details_url, last_commit):
        return self.set_check_run_status(
            check_run=SONARQUBE_STR,
            conclusion=SUCCESS_STR,
            details_url=details_url,
            last_commit=last_commit,
        )

    def set_sonarqube_failure(self, details_url, last_commit):
        return self.set_check_run_status(
            check_run=SONARQUBE_STR,
            conclusion=FAILURE_STR,
            details_url=details_url,
            last_commit=last_commit,
        )

    def set_cherry_pick_in_progress(self, last_commit):
        return self.set_check_run_status(
            check_run=CHERRY_PICKED_LABEL_PREFIX,
            status=IN_PROGRESS_STR,
            last_commit=last_commit,
        )

    def set_cherry_pick_success(self, details_url, last_commit):
        return self.set_check_run_status(
            check_run=CHERRY_PICKED_LABEL_PREFIX,
            conclusion=SUCCESS_STR,
            details_url=details_url,
            last_commit=last_commit,
        )

    def set_cherry_pick_failure(self, details_url, last_commit):
        return self.set_check_run_status(
            check_run=CHERRY_PICKED_LABEL_PREFIX,
            conclusion=FAILURE_STR,
            details_url=details_url,
            last_commit=last_commit,
        )

    @staticmethod
    def is_check_run_in_progress(check_run, last_commit):
        for run in last_commit.get_check_runs():
            if run.name == check_run and run.status == IN_PROGRESS_STR:
                return True
        return False

    def build_container(
        self,
        pull_request,
        last_commit,
        container_repository_and_tag,
        set_check=True,
        push=False,
    ):
        if not self.build_and_push_container:
            return False

        if self.is_check_run_in_progress(
            check_run=BUILD_CONTAINER_STR, last_commit=last_commit
        ):
            self.logger.info(
                f"{self.log_prefix} Check run is in progress, not running {BUILD_CONTAINER_STR}."
            )
            return False

        file_path, url_path = None, None

        if pull_request:
            file_path, url_path = self._get_check_run_result_file_path(
                check_run=BUILD_CONTAINER_STR,
                pull_request=pull_request,
                last_commit=last_commit,
            )

        if set_check:
            self.set_container_build_in_progress(last_commit=last_commit)

        build_cmd = (
            f"--network=host -f {self.container_repo_dir}/{self.dockerfile} "
            f"-t {container_repository_and_tag}"
        )
        if self.container_build_args:
            build_args = [
                f"--build-arg {b_arg}" for b_arg in self.container_build_args
            ][0]
            build_cmd = f"{build_args} {build_cmd}"

        if self.container_command_args:
            build_cmd = f"{' '.join(self.container_command_args)} {build_cmd}"

        if push:
            repository_creds = f"{self.container_repository_username}:{self.container_repository_password}"
            build_cmd += f" && podman push --creds {repository_creds} {container_repository_and_tag}"
        podman_build_cmd = f"podman build {build_cmd}"

        if self._run_in_container(
            command=podman_build_cmd, pull_request=pull_request, file_path=file_path
        )[0]:
            self.logger.info(
                f"{self.log_prefix} Done building {container_repository_and_tag}"
            )
            if pull_request and set_check:
                return self.set_container_build_success(
                    details_url=url_path, last_commit=last_commit
                )
            if push:
                push_msg = f"New container for {container_repository_and_tag} published"
                pull_request.create_issue_comment(push_msg)
                if self.slack_webhook_url:
                    message = f"""
```
{self.repository_name} {push_msg}.
```
"""
                    send_slack_message(
                        message=message,
                        webhook_url=self.slack_webhook_url,
                        log_prefix=self.log_prefix,
                    )

                self.logger.info(
                    f"{self.log_prefix} Done push {container_repository_and_tag}"
                )
        else:
            if pull_request and set_check:
                return self.set_container_build_failure(
                    details_url=url_path, last_commit=last_commit
                )

    def install_python_module(self, pull_request, last_commit):
        if not self.pypi:
            return False

        if self.is_check_run_in_progress(
            check_run=PYTHON_MODULE_INSTALL_STR, last_commit=last_commit
        ):
            self.logger.info(
                f"{self.log_prefix} Check run is in progress, not running {PYTHON_MODULE_INSTALL_STR}."
            )
            return False

        self.logger.info(f"{self.log_prefix} Installing python module")
        file_path, url_path = self._get_check_run_result_file_path(
            check_run=PYTHON_MODULE_INSTALL_STR,
            pull_request=pull_request,
            last_commit=last_commit,
        )
        f"{PYTHON_MODULE_INSTALL_STR}-{shortuuid.uuid()}"
        self.set_python_module_install_in_progress(last_commit=last_commit)
        if self._run_in_container(
            command="pip install .", pull_request=pull_request, file_path=file_path
        )[0]:
            return self.set_python_module_install_success(
                details_url=url_path, last_commit=last_commit
            )

        return self.set_python_module_install_failure(
            details_url=url_path, last_commit=last_commit
        )

    def run_tox(self, pull_request, last_commit):
        if not self.tox_enabled:
            return False

        if self.is_check_run_in_progress(check_run=TOX_STR, last_commit=last_commit):
            self.logger.info(
                f"{self.log_prefix} Check run is in progress, not running {TOX_STR}."
            )
            return False

        file_path, url_path = self._get_check_run_result_file_path(
            check_run=TOX_STR, pull_request=pull_request, last_commit=last_commit
        )
        cmd = f"{TOX_STR}"
        if self.tox_enabled != "all":
            tests = self.tox_enabled.replace(" ", "")
            cmd += f" -e {tests}"

        self.set_run_tox_check_in_progress(last_commit=last_commit)
        if self._run_in_container(
            command=cmd, pull_request=pull_request, file_path=file_path
        )[0]:
            return self.set_run_tox_check_success(
                details_url=url_path, last_commit=last_commit
            )
        else:
            return self.set_run_tox_check_failure(
                details_url=url_path, last_commit=last_commit
            )

    def run_sonarqube(self, pull_request, last_commit):
        if not self.sonarqube_project_key:
            return False

        self.set_sonarqube_in_progress(last_commit=last_commit)
        target_url = f"{self.sonarqube_url}/dashboard?id={self.sonarqube_project_key}"
        cmd = self.sonarqube_api.get_sonar_scanner_command(
            project_key=self.sonarqube_project_key
        )
        if self._run_in_container(command=cmd, pull_request=pull_request)[0]:
            project_status = self.sonarqube_api.get_project_quality_status(
                project_key=self.sonarqube_project_key
            )
            project_status_res = project_status["projectStatus"]["status"]
            if project_status_res == "OK":
                return self.set_sonarqube_success(
                    details_url=target_url, last_commit=last_commit
                )
            else:
                self.logger.info(
                    f"{self.log_prefix} Sonarqube scan failed, status: {project_status_res}"
                )
                return self.set_sonarqube_failure(
                    details_url=target_url, last_commit=last_commit
                )
        return self.set_sonarqube_failure(
            details_url=target_url, last_commit=last_commit
        )

    def set_check_run_status(
        self, check_run, last_commit, status=None, conclusion=None, details_url=None
    ):
        kwargs = {
            "name": check_run,
            "head_sha": last_commit.sha,
        }
        if status:
            kwargs["status"] = status

        if conclusion:
            kwargs["conclusion"] = conclusion

        if details_url:
            kwargs["details_url"] = details_url

        self.logger.info(
            f"{self.log_prefix} Set {check_run} check to {status or conclusion}"
        )
        return self.repository_by_github_app.create_check_run(**kwargs)

    def _get_check_run_result_file_path(self, check_run, pull_request, last_commit):
        base_path = os.path.join(self.webhook_server_data_dir, check_run)
        if not os.path.exists(base_path):
            os.makedirs(name=base_path, exist_ok=True)

        file_name = f"PR-{pull_request.number}-{last_commit.sha}"
        file_path = os.path.join(base_path, file_name)
        url_path = f"{self.webhook_url}{APP_ROOT_PATH}/{check_run}/{file_name}"
        return file_path, url_path

    def _run_in_container(self, command, pull_request, env=None, file_path=None):
        podman_base_cmd = (
            f"podman run --privileged -v /tmp/containers:/var/lib/containers/:Z --rm {env if env else ''} "
            f"--entrypoint bash quay.io/myakove/github-webhook-server -c"
        )

        # Clone the repository
        clone_base_cmd = (
            f"git clone {self.repository.clone_url.replace('https://', f'https://{self.token}@')} "
            f"{self.container_repo_dir}"
        )
        clone_base_cmd += f" && cd {self.container_repo_dir}"
        clone_base_cmd += f" && git config user.name '{self.repository.owner.login}'"
        clone_base_cmd += f" && git config user.email '{self.repository.owner.email}'"
        clone_base_cmd += " && git config --local --add remote.origin.fetch +refs/pull/*/head:refs/remotes/origin/pr/*"
        clone_base_cmd += " && git remote update"

        # Checkout the pull request
        if pull_request:
            clone_base_cmd += f" && git checkout origin/pr/{pull_request.number}"

        # final podman command
        podman_base_cmd += f" '{clone_base_cmd} && {command}'"
        return run_command(
            command=podman_base_cmd,
            log_prefix=self.log_prefix,
            file_path=file_path,
        )
