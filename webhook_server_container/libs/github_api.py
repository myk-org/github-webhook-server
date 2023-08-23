import contextlib
import datetime
import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import shortuuid
import yaml
from github import Github, GithubException
from github.GithubException import UnknownObjectException

from webhook_server_container.libs.sonar_qube import SonarQubeExt
from webhook_server_container.utils.constants import (
    ADD_STR,
    APP_ROOT_PATH,
    APPROVED_BY_LABEL_PREFIX,
    BRANCH_LABEL_PREFIX,
    BUILD_AND_PUSH_CONTAINER_STR,
    BUILD_CONTAINER_STR,
    CAN_BE_MERGED_STR,
    CHANGED_REQUESTED_BY_LABEL_PREFIX,
    CHERRY_PICK_LABEL_PREFIX,
    CHERRY_PICKED_LABEL_PREFIX,
    COMMENTED_BY_LABEL_PREFIX,
    DELETE_STR,
    DYNAMIC_LABELS_DICT,
    FAILURE_STR,
    FLASK_APP,
    HOLD_LABEL_STR,
    IN_PROGRESS_STR,
    LGTM_STR,
    NEEDS_REBASE_LABEL_STR,
    PRE_COMMIT_CI_BOT_USER,
    PYTHON_MODULE_INSTALL_STR,
    QUEUED_STR,
    REACTIONS,
    SIZE_LABEL_PREFIX,
    SONARQUBE_STR,
    STATIC_LABELS_DICT,
    SUCCESS_STR,
    TOX_STR,
    USER_LABELS_DICT,
    VERIFIED_LABEL_STR,
    WIP_STR,
)
from webhook_server_container.utils.dockerhub_rate_limit import DockerHub
from webhook_server_container.utils.helpers import (
    check_rate_limit,
    extract_key_from_dict,
    get_data_from_config,
    get_github_repo_api,
    ignore_exceptions,
    run_command,
)


class RepositoryNotFoundError(Exception):
    pass


class GitHubApi:
    def __init__(self, hook_data, repositories_app_api, missing_app_repositories):
        self.app = FLASK_APP
        self.hook_data = hook_data
        self.repository_name = hook_data["repository"]["name"]
        self.repositories_app_api = repositories_app_api
        self.missing_app_repositories = missing_app_repositories
        self.pull_request = None
        self.last_commit = None
        self.log_prefix_with_color = None
        self.container_repo_dir = "/tmp/repository"
        self.webhook_server_data_dir = os.environ.get(
            "WEBHOOK_SERVER_DATA_DIR", "/webhook_server"
        )

        # filled by self._repo_data_from_config()
        self.dockerhub_username = None
        self.dockerhub_password = None
        self.container_repository_username = None
        self.container_repository_password = None
        self.container_repository = None
        self.dockerfile = None
        self.container_tag = None
        self.container_build_args = None
        self.container_command_args = None
        self.token = None
        self.repository_full_name = None
        self.api_user = None
        self.github_app_id = None
        self.sonarqube_api = None
        self.sonarqube_project_key = None
        # End of filled by self._repo_data_from_config()

        self._repo_data_from_config()
        self._set_log_prefix_color()
        self.github_app_api = self.get_github_app_api()
        self.github_api = Github(login_or_token=self.token)
        check_rate_limit(github_api=self.github_api)
        self.api_user = self._api_username
        self.repository = get_github_repo_api(
            github_api=self.github_api, repository=self.repository_full_name
        )
        self.repository_by_github_app = get_github_repo_api(
            github_api=self.github_app_api, repository=self.repository_full_name
        )
        self.clone_repository_path = os.path.join("/", self.repository.name)
        self.dockerhub = DockerHub(
            username=self.dockerhub_username,
            password=self.dockerhub_password,
        )
        self.supported_user_labels_str = "".join(
            [f" * {label}\n" for label in USER_LABELS_DICT.keys()]
        )
        self.welcome_msg = f"""
Report bugs in [Issues](https://github.com/myakove/github-webhook-server/issues)

The following are automatically added:
 * Add reviewers from OWNER file (in the root of the repository) under reviewers section.
 * Set PR size label.
 * New issue is created for the PR. (Closed when PR is merged/closed)
 * Run [pre-commit](https://pre-commit.ci/) if `.pre-commit-config.yaml` exists in the repo.

Available user actions:
 * To mark PR as WIP comment `/wip` to the PR, To remove it from the PR comment `/wip cancel` to the PR.
 * To block merging of PR comment `/hold`, To un-block merging of PR comment `/hold cancel`.
 * To mark PR as verified comment `/verified` to the PR, to un-verify comment `/verified cancel` to the PR.
        verified label removed on each new commit push.
 * To cherry pick a merged PR comment `/cherry-pick <target branch to cherry-pick to>` in the PR.
    * Multiple target branches can be cherry-picked, separated by spaces. (`/cherry-pick branch1 branch2`)
    * Cherry-pick will be started when PR is merged
 * To re-run tox comment `/retest tox` in the PR.
 * To re-run build-container command `/retest build-container` in the PR.
 * To re-run python-module-install command `/retest python-module-install` in the PR.
 * To build and push container image command `/build-and-push-container` in the PR (tag will be the PR number).
 * To add a label by comment use `/<label name>`, to remove, use `/<label name> cancel`
<details>
<summary>Supported labels</summary>

{self.supported_user_labels_str}
</details>
    """

    def get_github_app_api(self):
        if self.repository_full_name in self.missing_app_repositories:
            raise RepositoryNotFoundError(
                f"Repository {self.repository_full_name} not found by manage-repositories-app, "
                f"make sure the app installed (https://github.com/apps/manage-repositories-app)"
            )
        return self.repositories_app_api[self.repository_full_name]

    def _set_log_prefix_color(self):
        repo_str = "\033[1;{color}m{name}\033[1;0m"
        color_file = "/tmp/color.json"
        try:
            with open(color_file) as fd:
                color_json = json.load(fd)
        except Exception:
            color_json = {}

        color = color_json.get(self.repository_name)
        if not color:
            color = random.choice(range(31, 39))
            color_json[self.repository_name] = color

        self.log_prefix_with_color = repo_str.format(
            color=color, name=self.repository_name
        )

        with open(color_file, "w") as fd:
            json.dump(color_json, fd)

    @property
    def log_prefix(self):
        return (
            f"{self.log_prefix_with_color}[PR {self.pull_request.number}]:"
            if self.pull_request
            else f"{self.log_prefix_with_color}:"
        )

    def hash_token(self, message):
        hashed_message = message.replace(self.token, "*****")
        return hashed_message

    def app_logger_info(self, message):
        hashed_message = self.hash_token(message=message)
        self.app.logger.info(hashed_message)

    def app_logger_error(self, message):
        hashed_message = self.hash_token(message=message)
        self.app.logger.error(hashed_message)

    def process_hook(self, data, event_log):
        self.app.logger.info(f"{self.log_prefix} {event_log}")
        ignore_data = ["status", "branch_protection_rule"]
        if data == "issue_comment":
            self.process_comment_webhook_data()

        elif data == "pull_request":
            self.process_pull_request_webhook_data()

        elif data == "push":
            self.process_push_webhook_data()

        elif data == "pull_request_review":
            self.process_pull_request_review_webhook_data()

        elif data not in ignore_data:
            if data == "check_run":
                _check_run = self.hook_data["check_run"]
                if _check_run["name"] == CAN_BE_MERGED_STR:
                    return

                if self.hook_data["action"] == "completed":
                    self.app.logger.info(
                        f"{self.log_prefix} Got event check_run completed, getting pull request"
                    )
                    for _pull_request in self.repository.get_pulls(state="open"):
                        _last_commit = list(_pull_request.get_commits())[-1]
                        for _commit_check_run in _last_commit.get_check_runs():
                            if _commit_check_run.id == int(_check_run["id"]):
                                self.pull_request = _pull_request
                                break

            self.pull_request = self.pull_request or self._get_pull_request()
            if self.pull_request:
                self.last_commit = self._get_last_commit()
                self.check_if_can_be_merged()

    @property
    def _api_username(self):
        return self.github_api.get_user().login

    def _repo_data_from_config(self):
        config_data = get_data_from_config()
        self.github_app_id = config_data["github-app-id"]
        self.token = config_data["github-token"]
        self.webhook_url = config_data.get("webhook_ip")
        sonarqube = config_data.get("sonarqube")
        if sonarqube:
            self.sonarqube_url = sonarqube["url"]
            self.sonarqube_api = SonarQubeExt(**sonarqube)

        repo_data = config_data["repositories"].get(self.repository_name)
        if not repo_data:
            raise RepositoryNotFoundError(
                f"Repository {self.repository_name} not found in config file"
            )

        self.repository_full_name = repo_data["name"]
        self.pypi = repo_data.get("pypi")
        self.verified_job = repo_data.get("verified_job", True)
        self.tox_enabled = repo_data.get("tox")
        self.slack_webhook_url = repo_data.get("slack_webhook_url")
        self.build_and_push_container = repo_data.get("container")
        self.dockerhub = repo_data.get("docker")
        if sonarqube:
            self.sonarqube_project_key = self.repository_full_name.replace("/", "_")

        if self.dockerhub:
            self.dockerhub_username = self.dockerhub["username"]
            self.dockerhub_password = self.dockerhub["password"]

        if self.build_and_push_container:
            self.container_repository_username = self.build_and_push_container[
                "username"
            ]
            self.container_repository_password = self.build_and_push_container[
                "password"
            ]
            self.container_repository = self.build_and_push_container["repository"]
            self.dockerfile = self.build_and_push_container.get(
                "dockerfile", "Dockerfile"
            )
            self.container_tag = self.build_and_push_container.get("tag", "latest")
            self.container_build_args = self.build_and_push_container.get("build-args")
            self.container_command_args = self.build_and_push_container.get("args")

    def _get_pull_request(self, number=None):
        if number:
            return self.repository.get_pull(number)

        for _number in extract_key_from_dict(key="number", _dict=self.hook_data):
            try:
                return self.repository.get_pull(_number)
            except GithubException:
                continue

        commit = self.hook_data.get("commit")
        if commit:
            commit_obj = self.repository.get_commit(commit["sha"])
            with contextlib.suppress(Exception):
                return commit_obj.get_pulls()[0]

        self.app.logger.info(
            f"{self.log_prefix} No issue or pull_request found in hook data"
        )

    def _get_last_commit(self):
        return list(self.pull_request.get_commits())[-1]

    def label_exists_in_pull_request(self, label):
        return any(lb for lb in self.pull_request_labels_names() if lb == label)

    def pull_request_labels_names(self):
        return [lb.name for lb in self.pull_request.labels]

    def skip_merged_pull_request(self):
        if self.pull_request.is_merged():
            self.app.logger.info(f"{self.log_prefix}: PR is merged, not processing")
            return True

    def _remove_label(self, label):
        if self.label_exists_in_pull_request(label=label):
            self.app.logger.info(f"{self.log_prefix} Removing label {label}")
            return self.pull_request.remove_from_labels(label)

        self.app.logger.warning(
            f"{self.log_prefix} Label {label} not found and cannot be removed"
        )

    def _add_label(self, label):
        label = label.strip()
        if len(label) > 49:
            self.app.logger.warning(f"{label} is to long, not adding.")
            return

        if self.label_exists_in_pull_request(label=label):
            self.app.logger.info(
                f"{self.log_prefix} Label {label} already assign to PR {self.pull_request.number}"
            )
            return

        if label in STATIC_LABELS_DICT:
            self.app.logger.info(
                f"{self.log_prefix} Adding pull request label {label} to {self.pull_request.number}"
            )
            return self.pull_request.add_to_labels(label)

        _color = [
            DYNAMIC_LABELS_DICT[_label]
            for _label in DYNAMIC_LABELS_DICT
            if _label in label
        ]
        self.app.logger.info(
            f"{self.log_prefix} Label {label} was "
            f"{'found' if _color else 'not found'} in labels dict"
        )
        color = _color[0] if _color else "D4C5F9"
        self.app.logger.info(
            f"{self.log_prefix} Adding label {label} with color {color}"
        )

        try:
            _repo_label = self.repository.get_label(label)
            _repo_label.edit(name=_repo_label.name, color=color)
            self.app.logger.info(
                f"{self.log_prefix} "
                f"Edit repository label {label} with color {color}"
            )
        except UnknownObjectException:
            self.app.logger.info(
                f"{self.log_prefix} Add repository label {label} with color {color}"
            )
            self.repository.create_label(name=label, color=color)

        self.app.logger.info(
            f"{self.log_prefix} Adding pull request label {label} to {self.pull_request.number}"
        )
        return self.pull_request.add_to_labels(label)

    def _generate_issue_title(self):
        return f"{self.pull_request.title} - {self.pull_request.number}"

    def _generate_issue_body(self):
        return f"[Auto generated]\nNumber: [#{self.pull_request.number}]"

    @ignore_exceptions()
    def is_branch_exists(self, branch):
        return self.repository.get_branch(branch)

    def upload_to_pypi(self, tag_name):
        token = self.pypi["token"]
        env = f"-e TWINE_USERNAME=__token__ -e TWINE_PASSWORD={token} "
        cmd = f"git checkout {tag_name}"
        self.app.logger.info(f"{self.log_prefix} Start uploading to pypi")
        cmd += (
            " && python3 -m build --sdist --outdir /tmp/dist"
            " && twine check /tmp/dist/$(echo *.tar.gz)"
            " && twine upload /tmp/dist/$(echo *.tar.gz) --skip-existing"
        )
        rc, out, err = self._run_in_container(command=cmd, env=env)
        if rc:
            self.app.logger.info(f"{self.log_prefix} Publish to pypi finished")
            if self.slack_webhook_url:
                message = f"""
```
{self.repository_name} Version {tag_name} published to PYPI.
```
"""
                self.send_slack_message(
                    message=message,
                    webhook_url=self.slack_webhook_url,
                )

        else:
            err = "Publish to pypi failed"
            self.app.logger.error(f"{self.log_prefix} {err}")
            self.repository.create_issue(
                title=err,
                body=f"""
stdout: `{out}`
stderr: `{err}`
""",
            )

    @property
    def owners_content(self):
        try:
            owners_content = self.repository.get_contents("OWNERS")
            return yaml.safe_load(owners_content.decoded_content)
        except UnknownObjectException:
            self.app.logger.error(f"{self.log_prefix} OWNERS file not found")
            return {}

    @property
    def reviewers(self):
        return self.owners_content.get("reviewers", [])

    @property
    def approvers(self):
        return self.owners_content.get("approvers", [])

    def assign_reviewers(self):
        for reviewer in self.reviewers:
            if reviewer != self.pull_request.user.login:
                self.app.logger.info(f"{self.log_prefix} Adding reviewer {reviewer}")
                try:
                    self.pull_request.create_review_request([reviewer])
                except GithubException as ex:
                    self.app.logger.error(
                        f"{self.log_prefix} Failed to add reviewer {reviewer}. {ex}"
                    )

    def add_size_label(self):
        size = self.pull_request.additions + self.pull_request.deletions
        if size < 20:
            _label = "XS"

        elif size < 50:
            _label = "S"

        elif size < 100:
            _label = "M"

        elif size < 300:
            _label = "L"

        elif size < 500:
            _label = "XL"

        else:
            _label = "XXL"

        size_label = f"{SIZE_LABEL_PREFIX}{_label}"

        if size_label in self.pull_request_labels_names():
            return

        exists_size_label = [
            label
            for label in self.pull_request_labels_names()
            if label.startswith(SIZE_LABEL_PREFIX)
        ]

        if exists_size_label:
            self._remove_label(label=exists_size_label[0])

        self._add_label(label=size_label)

    def label_by_user_comment(
        self, user_request, remove, reviewed_user, issue_comment_id
    ):
        if not any(
            user_request.startswith(label_name) for label_name in USER_LABELS_DICT
        ):
            self.app.logger.info(
                f"{self.log_prefix} "
                f"Label {user_request} is not a predefined one, "
                "will not be added / removed."
            )
            self.pull_request.create_issue_comment(
                body=f"""
Label {user_request} is not a predefined one, will not be added / removed.
Available labels:

{self.supported_user_labels_str}
""",
            )
            return

        self.app.logger.info(
            f"{self.log_prefix} {'Remove' if remove else 'Add'} "
            f"label requested by user {reviewed_user}: {user_request}"
        )
        self.create_comment_reaction(
            issue_comment_id=issue_comment_id,
            reaction=REACTIONS.ok,
        )

        if user_request == LGTM_STR:
            self.manage_reviewed_by_label(
                review_state=LGTM_STR,
                action=DELETE_STR if remove else ADD_STR,
                reviewed_user=reviewed_user,
            )

        else:
            label_func = self._remove_label if remove else self._add_label
            label_func(label=user_request)

    def reset_verify_label(self):
        self.app.logger.info(
            f"{self.log_prefix} Processing reset {VERIFIED_LABEL_STR} label on new commit push"
        )
        # Remove verified label
        self._remove_label(label=VERIFIED_LABEL_STR)

    def set_verify_check_queued(self):
        return self.set_check_run_status(
            check_run=VERIFIED_LABEL_STR, status=QUEUED_STR
        )

    def set_verify_check_success(self):
        return self.set_check_run_status(
            check_run=VERIFIED_LABEL_STR, conclusion=SUCCESS_STR
        )

    def set_run_tox_check_queued(self):
        if not self.tox_enabled:
            return False

        return self.set_check_run_status(check_run=TOX_STR, status=QUEUED_STR)

    def set_run_tox_check_in_progress(self):
        return self.set_check_run_status(check_run=TOX_STR, status=IN_PROGRESS_STR)

    def set_run_tox_check_failure(self, details_url):
        return self.set_check_run_status(
            check_run=TOX_STR, conclusion=FAILURE_STR, details_url=details_url
        )

    def set_run_tox_check_success(self, details_url):
        return self.set_check_run_status(
            check_run=TOX_STR, conclusion=SUCCESS_STR, details_url=details_url
        )

    def set_merge_check_queued(self):
        return self.set_check_run_status(check_run=CAN_BE_MERGED_STR, status=QUEUED_STR)

    def set_merge_check_in_progress(self):
        return self.set_check_run_status(
            check_run=CAN_BE_MERGED_STR, status=IN_PROGRESS_STR
        )

    def set_merge_check_success(self):
        return self.set_check_run_status(
            check_run=CAN_BE_MERGED_STR, conclusion=SUCCESS_STR
        )

    def set_container_build_queued(self):
        if not self.build_and_push_container:
            return

        return self.set_check_run_status(
            check_run=BUILD_CONTAINER_STR, status=QUEUED_STR
        )

    def set_container_build_in_progress(self):
        return self.set_check_run_status(
            check_run=BUILD_CONTAINER_STR, status=IN_PROGRESS_STR
        )

    def set_container_build_success(self, details_url):
        return self.set_check_run_status(
            check_run=BUILD_CONTAINER_STR,
            conclusion=SUCCESS_STR,
            details_url=details_url,
        )

    def set_container_build_failure(self, details_url):
        return self.set_check_run_status(
            check_run=BUILD_CONTAINER_STR,
            conclusion=FAILURE_STR,
            details_url=details_url,
        )

    def set_python_module_install_queued(self):
        if not self.pypi:
            return False

        return self.set_check_run_status(
            check_run=PYTHON_MODULE_INSTALL_STR, status=QUEUED_STR
        )

    def set_python_module_install_in_progress(self):
        return self.set_check_run_status(
            check_run=PYTHON_MODULE_INSTALL_STR, status=IN_PROGRESS_STR
        )

    def set_python_module_install_success(self, details_url):
        return self.set_check_run_status(
            check_run=PYTHON_MODULE_INSTALL_STR,
            conclusion=SUCCESS_STR,
            details_url=details_url,
        )

    def set_python_module_install_failure(self, details_url):
        return self.set_check_run_status(
            check_run=PYTHON_MODULE_INSTALL_STR,
            conclusion=FAILURE_STR,
            details_url=details_url,
        )

    def set_sonarqube_queued(self):
        if not self.sonarqube_project_key:
            return False

        return self.set_check_run_status(check_run=SONARQUBE_STR, status=QUEUED_STR)

    def set_sonarqube_in_progress(self):
        return self.set_check_run_status(
            check_run=SONARQUBE_STR, status=IN_PROGRESS_STR
        )

    def set_sonarqube_success(self, details_url):
        return self.set_check_run_status(
            check_run=SONARQUBE_STR, conclusion=SUCCESS_STR, details_url=details_url
        )

    def set_sonarqube_failure(self, details_url):
        return self.set_check_run_status(
            check_run=SONARQUBE_STR, conclusion=FAILURE_STR, details_url=details_url
        )

    def set_cherry_pick_in_progress(self):
        return self.set_check_run_status(
            check_run=CHERRY_PICKED_LABEL_PREFIX, status=IN_PROGRESS_STR
        )

    def set_cherry_pick_success(self, details_url):
        return self.set_check_run_status(
            check_run=CHERRY_PICKED_LABEL_PREFIX,
            conclusion=SUCCESS_STR,
            details_url=details_url,
        )

    def set_cherry_pick_failure(self, details_url):
        return self.set_check_run_status(
            check_run=CHERRY_PICKED_LABEL_PREFIX,
            conclusion=FAILURE_STR,
            details_url=details_url,
        )

    @ignore_exceptions(FLASK_APP.logger)
    def create_issue_for_new_pull_request(self, parent_committer):
        if parent_committer in (
            self.api_user,
            PRE_COMMIT_CI_BOT_USER,
        ):
            return
        self.app.logger.info(
            f"{self.log_prefix} Creating issue for new PR: {self.pull_request.title}"
        )
        self.repository.create_issue(
            title=self._generate_issue_title(),
            body=self._generate_issue_body(),
            assignee=self.pull_request.user.login,
        )

    def close_issue_for_merged_or_closed_pr(self, hook_action):
        for issue in self.repository.get_issues():
            if issue.body == self._generate_issue_body():
                self.app.logger.info(
                    f"{self.log_prefix} Closing issue {issue.title} for PR: "
                    f"{self.pull_request.title}"
                )
                issue.create_comment(
                    f"{self.log_prefix} Closing issue for PR: "
                    f"{self.pull_request.title}.\nPR was {hook_action}."
                )
                issue.edit(state="closed")
                break

    def process_comment_webhook_data(self):
        if self.hook_data["action"] in ("action", "deleted"):
            return

        issue_number = self.hook_data["issue"]["number"]
        self.app.logger.info(f"{self.log_prefix} Processing issue {issue_number}")

        self.pull_request = self._get_pull_request()
        if not self.pull_request:
            return

        self.last_commit = self._get_last_commit()

        body = self.hook_data["comment"]["body"]

        if body == self.welcome_msg:
            self.app.logger.info(
                f"{self.log_prefix} Welcome message found in issue "
                f"{self.pull_request.title}. Not processing"
            )
            return

        striped_body = body.strip()
        _user_commands = list(
            filter(
                lambda x: x,
                striped_body.split("/") if striped_body.startswith("/") else [],
            )
        )
        user_login = self.hook_data["sender"]["login"]
        for user_command in _user_commands:
            self.user_commands(
                command=user_command,
                reviewed_user=user_login,
                issue_comment_id=self.hook_data["comment"]["id"],
            )

    def process_pull_request_webhook_data(self):
        hook_action = self.hook_data["action"]
        self.app.logger.info(f"{self.log_prefix} hook_action is: {hook_action}")
        self.pull_request = self._get_pull_request()
        if not self.pull_request:
            return

        self.last_commit = self._get_last_commit()
        pull_request_data = self.hook_data["pull_request"]
        parent_committer = pull_request_data["user"]["login"]
        pull_request_branch = pull_request_data["base"]["ref"]

        if hook_action == "opened":
            self.app.logger.info(f"{self.log_prefix} Creating welcome comment")
            self.pull_request.create_issue_comment(self.welcome_msg)
            self.create_issue_for_new_pull_request(parent_committer=parent_committer)
            self.process_opened_or_synchronize_pull_request(
                parent_committer=parent_committer,
                pull_request_branch=pull_request_branch,
            )

        if hook_action == "synchronize":
            reviewed_by_labels = [
                label.name for label in self.pull_request.labels if "By-" in label.name
            ]
            for _reviewed_label in reviewed_by_labels:
                self._remove_label(label=_reviewed_label)

            self.process_opened_or_synchronize_pull_request(
                parent_committer=parent_committer,
                pull_request_branch=pull_request_branch,
            )

        if hook_action == "closed":
            self.close_issue_for_merged_or_closed_pr(hook_action=hook_action)

            if pull_request_data.get("merged"):
                self.app.logger.info(f"{self.log_prefix} PR is merged")
                self._build_container(push=True, set_check=False)

                for _label in self.pull_request.labels:
                    _label_name = _label.name
                    if _label_name.startswith(CHERRY_PICK_LABEL_PREFIX):
                        self.cherry_pick(
                            target_branch=_label_name.replace(
                                CHERRY_PICK_LABEL_PREFIX, ""
                            ),
                        )

                self.needs_rebase()

        if hook_action in ("labeled", "unlabeled"):
            labeled = self.hook_data["label"]["name"].lower()

            if (
                hook_action == "labeled"
                and labeled == CAN_BE_MERGED_STR
                and parent_committer
                in (
                    self.api_user,
                    PRE_COMMIT_CI_BOT_USER,
                )
            ):
                self.app.logger.info(
                    f"{self.log_prefix} "
                    f"will be merged automatically. owner: {self.api_user}"
                )
                self.pull_request.create_issue_comment(
                    f"Owner of the pull request is `{self.api_user}`\nPull request is merged automatically."
                )
                self.pull_request.merge(merge_method="squash")
                return

            self.app.logger.info(
                f"{self.log_prefix} PR {self.pull_request.number} {hook_action} with {labeled}"
            )
            if self.verified_job and labeled == VERIFIED_LABEL_STR:
                if hook_action == "labeled":
                    self.set_verify_check_success()

                if hook_action == "unlabeled":
                    self.set_verify_check_queued()

            if (
                CAN_BE_MERGED_STR not in self.pull_request_labels_names()
                or labeled != CAN_BE_MERGED_STR
            ):
                self.check_if_can_be_merged()

    def process_push_webhook_data(self):
        tag = re.search(r"refs/tags/?(.*)", self.hook_data["ref"])
        if tag and self.pypi:
            tag_name = tag.group(1)
            self.app.logger.info(
                f"{self.log_prefix} Processing push for tag: {tag_name}"
            )
            self.upload_to_pypi(tag_name=tag_name)

    def process_pull_request_review_webhook_data(self):
        self.pull_request = self._get_pull_request()
        if not self.pull_request:
            return

        self.last_commit = self._get_last_commit()

        if self.hook_data["action"] == "submitted":
            """
            commented
            approved
            changes_requested
            """
            self.manage_reviewed_by_label(
                review_state=self.hook_data["review"]["state"],
                action=ADD_STR,
                reviewed_user=self.hook_data["review"]["user"]["login"],
            )
        self.check_if_can_be_merged()

    def manage_reviewed_by_label(self, review_state, action, reviewed_user):
        self.app.logger.info(
            f"{self.log_prefix} "
            f"Processing label for review from {reviewed_user}. "
            f"review_state: {review_state}, action: {action}"
        )
        label_prefix = None
        label_to_remove = None

        pull_request_labels = self.pull_request_labels_names()

        if review_state in ("approved", LGTM_STR):
            base_dict = self.hook_data.get("issue", self.hook_data.get("pull_request"))
            pr_owner = base_dict["user"]["login"]
            if pr_owner == reviewed_user:
                self.app.logger.info(
                    f"{self.log_prefix} PR owner {pr_owner} set /lgtm, not adding label."
                )
                return

            label_prefix = APPROVED_BY_LABEL_PREFIX
            _remove_label = f"{CHANGED_REQUESTED_BY_LABEL_PREFIX}{reviewed_user}"
            if _remove_label in pull_request_labels:
                label_to_remove = _remove_label

        elif review_state == "changes_requested":
            label_prefix = CHANGED_REQUESTED_BY_LABEL_PREFIX
            _remove_label = f"{APPROVED_BY_LABEL_PREFIX}{reviewed_user}"
            if _remove_label in pull_request_labels:
                label_to_remove = _remove_label

        elif review_state == "commented":
            label_prefix = COMMENTED_BY_LABEL_PREFIX

        if label_prefix:
            reviewer_label = f"{label_prefix}{reviewed_user}"

            if action == ADD_STR:
                self._add_label(label=reviewer_label)
                if label_to_remove:
                    self._remove_label(label=label_to_remove)

            if action == DELETE_STR:
                self._remove_label(label=reviewer_label)
        else:
            self.app.logger.warning(
                f"{self.log_prefix} PR {self.pull_request.number} got unsupported review state: {review_state}"
            )

    def _run_tox(self):
        if not self.tox_enabled:
            return False

        if self.is_check_run_in_progress(check_run=TOX_STR):
            self.app.logger.info(
                f"{self.log_prefix} Check run is in progress, not running {TOX_STR}."
            )
            return False

        file_path, url_path = self._get_check_run_result_file_path(check_run=TOX_STR)
        cmd = f"{TOX_STR}"
        if self.tox_enabled != "all":
            tests = self.tox_enabled.replace(" ", "")
            cmd += f" -e {tests}"

        self.set_run_tox_check_in_progress()
        if self._run_in_container(command=cmd, file_path=file_path)[0]:
            return self.set_run_tox_check_success(details_url=url_path)
        else:
            return self.set_run_tox_check_failure(details_url=url_path)

    def user_commands(self, command, reviewed_user, issue_comment_id):
        remove = False
        available_commands = ["retest", "cherry-pick"]
        if "sonarsource.github.io" in command:
            self.app.logger.info(f"{self.log_prefix} command is in ignore list")
            return

        self.app.logger.info(
            f"{self.log_prefix} Processing label/user command {command} "
            f"by user {reviewed_user}"
        )
        command_and_args = command.split(" ", 1)
        _command = command_and_args[0]
        not_running_msg = f"Pull request already merged, not running {_command}"
        _args = command_and_args[1] if len(command_and_args) > 1 else ""
        if len(command_and_args) > 1 and _args == "cancel":
            self.app.logger.info(
                f"{self.log_prefix} User requested 'cancel' for command {_command}"
            )
            remove = True

        if _command in available_commands:
            if not _args:
                issue_msg = f"{_command} requires an argument"
                error_msg = f"{self.log_prefix} {issue_msg}"
                self.app.logger.info(error_msg)
                self.pull_request.create_issue_comment(issue_msg)
                return

            if _command == "cherry-pick":
                self.create_comment_reaction(
                    issue_comment_id=issue_comment_id,
                    reaction=REACTIONS.ok,
                )
                _target_branches = _args.split()
                _exits_target_branches = set()
                _non_exits_target_branches_msg = ""

                for _target_branch in _target_branches:
                    try:
                        self.repository.get_branch(_target_branch)
                    except Exception:
                        _non_exits_target_branches_msg += (
                            f"Target branch `{_target_branch}` does not exist\n"
                        )

                    _exits_target_branches.add(_target_branch)

                if _non_exits_target_branches_msg:
                    self.app.logger.info(
                        f"{self.log_prefix} {_non_exits_target_branches_msg}"
                    )
                    self.pull_request.create_issue_comment(
                        _non_exits_target_branches_msg
                    )

                if _exits_target_branches:
                    if not self.pull_request.is_merged():
                        cp_labels = [
                            f"{CHERRY_PICK_LABEL_PREFIX}{_target_branch}"
                            for _target_branch in _exits_target_branches
                        ]
                        info_msg = f"""
Cherry-pick requested for PR: `{self.pull_request.title}` by user `{reviewed_user}`
Adding label/s `{' '.join([_cp_label for _cp_label in cp_labels])}` for automatic cheery-pick once the PR is merged
"""
                        self.app.logger.info(f"{self.log_prefix} {info_msg}")
                        self.pull_request.create_issue_comment(info_msg)
                        for _cp_label in cp_labels:
                            self._add_label(label=_cp_label)
                    else:
                        for _exits_target_branch in _exits_target_branches:
                            self.cherry_pick(
                                target_branch=_exits_target_branch,
                                reviewed_user=reviewed_user,
                            )

            elif _command == "retest":
                if self.skip_merged_pull_request():
                    return self.pull_request.create_issue_comment(not_running_msg)

                _target_tests = _args.split()
                for _test in _target_tests:
                    if _test == TOX_STR:
                        if not self.tox_enabled:
                            msg = f"No {TOX_STR} configured for this repository"
                            error_msg = f"{self.log_prefix} {msg}."
                            self.app.logger.info(error_msg)
                            self.pull_request.create_issue_comment(msg)
                            return

                        self.create_comment_reaction(
                            issue_comment_id=issue_comment_id,
                            reaction=REACTIONS.ok,
                        )
                        self._run_tox()

                    elif _test == BUILD_CONTAINER_STR:
                        if self.build_and_push_container:
                            self.create_comment_reaction(
                                issue_comment_id=issue_comment_id,
                                reaction=REACTIONS.ok,
                            )
                            self._build_container()
                        else:
                            msg = f"No {BUILD_CONTAINER_STR} configured for this repository"
                            error_msg = f"{self.log_prefix} {msg}"
                            self.app.logger.info(error_msg)
                            self.pull_request.create_issue_comment(msg)

                    elif _test == PYTHON_MODULE_INSTALL_STR:
                        if not self.pypi:
                            error_msg = f"{self.log_prefix} No pypi configured"
                            self.app.logger.info(error_msg)
                            self.pull_request.create_issue_comment(error_msg)
                            return

                        self.create_comment_reaction(
                            issue_comment_id=issue_comment_id,
                            reaction=REACTIONS.ok,
                        )
                        self._install_python_module()

                    elif _test == SONARQUBE_STR:
                        if not self.sonarqube_project_key:
                            msg = f"No {SONARQUBE_STR} configured for this repository"
                            error_msg = f"{self.log_prefix} {msg}"
                            self.app.logger.info(error_msg)
                            self.pull_request.create_issue_comment(msg)
                            return

                        self.create_comment_reaction(
                            issue_comment_id=issue_comment_id,
                            reaction=REACTIONS.ok,
                        )
                        self._run_sonarqube()

        elif _command == BUILD_AND_PUSH_CONTAINER_STR:
            if self.build_and_push_container:
                self.create_comment_reaction(
                    issue_comment_id=issue_comment_id,
                    reaction=REACTIONS.ok,
                )
                self._build_container(push=True)
            else:
                msg = (
                    f"No {BUILD_AND_PUSH_CONTAINER_STR} configured for this repository"
                )
                error_msg = f"{self.log_prefix} {msg}"
                self.app.logger.info(error_msg)
                self.pull_request.create_issue_comment(msg)

        elif _command == WIP_STR:
            if self.skip_merged_pull_request():
                return self.pull_request.create_issue_comment(not_running_msg)

            self.create_comment_reaction(
                issue_comment_id=issue_comment_id,
                reaction=REACTIONS.ok,
            )
            wip_for_title = f"{WIP_STR.upper()}:"
            if remove:
                self._remove_label(label=WIP_STR)
                self.pull_request.edit(
                    title=self.pull_request.title.replace(wip_for_title, "")
                )
            else:
                self._add_label(label=WIP_STR)
                self.pull_request.edit(
                    title=f"{wip_for_title} {self.pull_request.title}"
                )

        else:
            if self.skip_merged_pull_request():
                return self.pull_request.create_issue_comment(not_running_msg)

            self.label_by_user_comment(
                user_request=_command,
                remove=remove,
                reviewed_user=reviewed_user,
                issue_comment_id=issue_comment_id,
            )

    def cherry_pick(self, target_branch, reviewed_user=None):
        requested_by = reviewed_user or "by target-branch label"
        self.app.logger.info(
            f"{self.log_prefix} Cherry-pick requested by user: {requested_by}"
        )

        new_branch_name = f"{CHERRY_PICKED_LABEL_PREFIX}-{self.pull_request.head.ref}-{shortuuid.uuid()[:5]}"
        if not self.is_branch_exists(branch=target_branch):
            err_msg = f"cherry-pick failed: {target_branch} does not exists"
            self.app.logger.error(err_msg)
            self.pull_request.create_issue_comment(err_msg)
        else:
            self.set_cherry_pick_in_progress()
            file_path, url_path = self._get_check_run_result_file_path(
                check_run=CHERRY_PICKED_LABEL_PREFIX
            )
            commit_hash = self.pull_request.merge_commit_sha
            commit_msg = self.pull_request.title
            pull_request_url = self.pull_request.html_url
            env = f"-e GITHUB_TOKEN={self.token}"
            cmd = (
                f" git checkout {target_branch}"
                f" && git pull origin {target_branch}"
                f" && git checkout -b {new_branch_name} origin/{target_branch}"
                f" && git cherry-pick {commit_hash}"
                f" && git push origin {new_branch_name}"
                f" && hub pull-request "
                f"-b {target_branch} "
                f"-h {new_branch_name} "
                f"-l {CHERRY_PICKED_LABEL_PREFIX} "
                f'-m "{CHERRY_PICKED_LABEL_PREFIX}: [{target_branch}] {commit_msg}" '
                f'-m "cherry-pick {pull_request_url} into {target_branch}" '
                f'-m "requested-by {requested_by}"'
            )
            rc, out, err = self._run_in_container(
                command=cmd, env=env, file_path=file_path
            )
            if rc:
                self.set_cherry_pick_success(details_url=url_path)
                self.pull_request.create_issue_comment(
                    f"Cherry-picked PR {self.pull_request.title} into {target_branch}"
                )
            else:
                self.set_cherry_pick_failure(details_url=url_path)
                self.app.logger.error(
                    f"{self.log_prefix} Cherry pick failed: {out} --- {err}"
                )
                local_branch_name = f"{self.pull_request.head.ref}-{target_branch}"
                self.pull_request.create_issue_comment(
                    f"**Manual cherry-pick is needed**\nCherry pick failed for "
                    f"{commit_hash} to {target_branch}:\n"
                    f"To cherry-pick run:\n"
                    "```\n"
                    f"git checkout {target_branch}\n"
                    f"git pull origin {target_branch}\n"
                    f"git checkout -b {local_branch_name}\n"
                    f"git cherry-pick {commit_hash}\n"
                    f"git push origin {local_branch_name}\n"
                    "```"
                )

    def needs_rebase(self):
        for pull_request in self.repository.get_pulls():
            self.app.logger.info(
                f"{self.log_prefix} "
                "Sleep for 30 seconds before checking if rebase needed"
            )
            time.sleep(30)
            merge_state = pull_request.mergeable_state
            self.app.logger.info(f"{self.log_prefix} Mergeable state is {merge_state}")
            if merge_state == "behind":
                self._add_label(label=NEEDS_REBASE_LABEL_STR)
            else:
                self._remove_label(label=NEEDS_REBASE_LABEL_STR)

    def check_if_can_be_merged(self):
        """
        Check if PR can be merged and set the job for it

        Check the following:
            Has verified label.
            Has approved from one of the approvers.
            All required run check passed.
            PR status is 'clean'.
            PR has no changed requests from approvers.
        """
        if self.skip_merged_pull_request():
            return False

        if self.is_check_run_in_progress(check_run=CAN_BE_MERGED_STR):
            self.app.logger.info(
                f"{self.log_prefix} Check run is in progress, not running {CAN_BE_MERGED_STR}."
            )
            return False

        self.app.logger.info(f"{self.log_prefix} Check if {CAN_BE_MERGED_STR}.")
        last_commit_check_runs = list(self.last_commit.get_check_runs())
        check_runs_in_progress = [
            check_run.name
            for check_run in last_commit_check_runs
            if check_run.status == IN_PROGRESS_STR
            and check_run.name != CAN_BE_MERGED_STR
        ]
        if check_runs_in_progress:
            self.app.logger.info(
                f"{self.log_prefix} Some check runs in progress {check_runs_in_progress}, "
                f"skipping check if {CAN_BE_MERGED_STR}."
            )
            return False

        try:
            self.set_merge_check_in_progress()
            _labels = self.pull_request_labels_names()

            if VERIFIED_LABEL_STR not in _labels or HOLD_LABEL_STR in _labels:
                self._remove_label(label=CAN_BE_MERGED_STR)
                self.set_merge_check_queued()
                return False

            if self.pull_request.mergeable_state == "behind":
                self._remove_label(label=CAN_BE_MERGED_STR)
                self.set_merge_check_queued()
                return False

            all_check_runs_passed = all(
                [
                    check_run.conclusion == SUCCESS_STR
                    for check_run in last_commit_check_runs
                    if check_run.name != CAN_BE_MERGED_STR
                ]
            )
            if not all_check_runs_passed:
                self._remove_label(label=CAN_BE_MERGED_STR)
                self.set_merge_check_queued()
                # TODO: Fix `run_retest_if_queued` and uncomment the call for it.
                # self.run_retest_if_queued(last_commit_check_runs=last_commit_check_runs)
                return False

            for _label in _labels:
                if CHANGED_REQUESTED_BY_LABEL_PREFIX.lower() in _label.lower():
                    change_request_user = _label.split("-")[-1]
                    if change_request_user in self.approvers:
                        self._remove_label(label=CAN_BE_MERGED_STR)
                        return self.set_merge_check_queued()

            for _label in _labels:
                if APPROVED_BY_LABEL_PREFIX.lower() in _label.lower():
                    approved_user = _label.split("-")[-1]
                    if approved_user in self.approvers:
                        self._add_label(label=CAN_BE_MERGED_STR)
                        return self.set_merge_check_success()

            return self.set_merge_check_queued()
        except Exception:
            return self.set_merge_check_queued()

    @staticmethod
    def _comment_with_details(title, body):
        return f"""
<details>
<summary>{title}</summary>
    {body}
</details>
        """

    def _container_repository_and_tag(self):
        tag = (
            self.container_tag
            if self.pull_request.is_merged()
            else self.pull_request.number
        )
        return f"{self.container_repository}:{tag}"

    def _build_container(self, set_check=True, push=False):
        if not self.build_and_push_container:
            return False

        if self.is_check_run_in_progress(check_run=BUILD_CONTAINER_STR):
            self.app.logger.info(
                f"{self.log_prefix} Check run is in progress, not running {BUILD_CONTAINER_STR}."
            )
            return False

        file_path, url_path = None, None

        if self.pull_request:
            file_path, url_path = self._get_check_run_result_file_path(
                check_run=BUILD_CONTAINER_STR
            )

        if set_check:
            self.set_container_build_in_progress()

        _container_repository_and_tag = self._container_repository_and_tag()
        build_cmd = (
            f"--network=host -f {self.container_repo_dir}/{self.dockerfile} "
            f"-t {_container_repository_and_tag}"
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
            build_cmd += f" && podman push --creds {repository_creds} {_container_repository_and_tag}"
        podman_build_cmd = f"podman build {build_cmd}"

        if self._run_in_container(command=podman_build_cmd, file_path=file_path)[0]:
            self.app.logger.info(
                f"{self.log_prefix} Done building {_container_repository_and_tag}"
            )
            if self.pull_request and set_check:
                return self.set_container_build_success(details_url=url_path)
            if push:
                push_msg = (
                    f"New container for {_container_repository_and_tag} published"
                )
                self.pull_request.create_issue_comment(push_msg)
                if self.slack_webhook_url:
                    message = f"""
```
{self.repository_full_name} {push_msg}.
```
"""
                    self.send_slack_message(
                        message=message,
                        webhook_url=self.slack_webhook_url,
                    )

                self.app.logger.info(
                    f"{self.log_prefix} Done push {_container_repository_and_tag}"
                )
        else:
            if self.pull_request and set_check:
                return self.set_container_build_failure(details_url=url_path)

    def _install_python_module(self):
        if not self.pypi:
            return False

        if self.is_check_run_in_progress(check_run=PYTHON_MODULE_INSTALL_STR):
            self.app.logger.info(
                f"{self.log_prefix} Check run is in progress, not running {PYTHON_MODULE_INSTALL_STR}."
            )
            return False

        self.app.logger.info(f"{self.log_prefix} Installing python module")
        file_path, url_path = self._get_check_run_result_file_path(
            check_run=PYTHON_MODULE_INSTALL_STR
        )
        f"{PYTHON_MODULE_INSTALL_STR}-{shortuuid.uuid()}"
        self.set_python_module_install_in_progress()
        if self._run_in_container(command="pip install .", file_path=file_path)[0]:
            return self.set_python_module_install_success(details_url=url_path)

        return self.set_python_module_install_failure(details_url=url_path)

    def send_slack_message(self, message, webhook_url):
        slack_data = {"text": message}
        self.app.logger.info(f"{self.log_prefix} Sending message to slack: {message}")
        response = requests.post(
            webhook_url,
            data=json.dumps(slack_data),
            headers={"Content-Type": "application/json"},
        )
        if response.status_code != 200:
            raise ValueError(
                f"Request to slack returned an error {response.status_code} with the following message: "
                f"{response.text}"
            )

    def _process_verified(self, parent_committer):
        if not self.verified_job:
            return

        if parent_committer in (self.api_user, PRE_COMMIT_CI_BOT_USER):
            self.app.logger.info(
                f"{self.log_prefix} Committer {parent_committer} == API user "
                f"{parent_committer}, Setting verified label"
            )
            self._add_label(label=VERIFIED_LABEL_STR)
            self.set_verify_check_success()
        else:
            self.reset_verify_label()
            self.set_verify_check_queued()

    def check_rate_limit(self):
        minimum_limit = 50
        rate_limit = self.github_api.get_rate_limit()
        rate_limit_reset = rate_limit.core.reset
        rate_limit_remaining = rate_limit.core.remaining
        rate_limit_limit = rate_limit.core.limit
        self.app.logger.info(
            f"{self.log_prefix}  API rate limit: Current {rate_limit_remaining} of {rate_limit_limit}. "
            f"Reset in {rate_limit_reset} (UTC time is {datetime.datetime.utcnow()})"
        )
        while (
            datetime.datetime.utcnow() < rate_limit_reset
            and rate_limit_remaining < minimum_limit
        ):
            self.app.logger.warning(
                f"{self.log_prefix} Rate limit is below {minimum_limit} waiting till {rate_limit_reset}"
            )
            time_for_limit_reset = (
                rate_limit_reset - datetime.datetime.utcnow()
            ).seconds
            self.app.logger.info(
                f"{self.log_prefix} Sleeping {time_for_limit_reset} seconds"
            )
            time.sleep(time_for_limit_reset + 1)
            rate_limit = self.github_api.get_rate_limit()
            rate_limit_reset = rate_limit.core.reset
            rate_limit_remaining = rate_limit.core.remaining

    def create_comment_reaction(self, issue_comment_id, reaction):
        _comment = self.pull_request.get_issue_comment(issue_comment_id)
        _comment.create_reaction(reaction)

    def _checkout_pull_request(self, clone_path, file_path=None):
        self.app.logger.info(f"{self.log_prefix} Current directory: {os.getcwd()}")
        pr_number = f"origin/pr/{self.pull_request.number}"
        checkout_cmd = f"git -C {clone_path} checkout {pr_number}"
        return run_command(
            command=checkout_cmd, log_prefix=self.log_prefix, file_path=file_path
        )[0]

    def process_opened_or_synchronize_pull_request(
        self, parent_committer, pull_request_branch
    ):
        self.set_merge_check_queued()
        self.set_run_tox_check_queued()
        self.set_python_module_install_queued()
        self.set_container_build_queued()
        self.set_sonarqube_queued()
        self._process_verified(parent_committer=parent_committer)
        self.add_size_label()
        self._add_label(label=f"{BRANCH_LABEL_PREFIX}{pull_request_branch}")
        self.app.logger.info(f"{self.log_prefix} Adding PR owner as assignee")
        self.pull_request.add_to_assignees(parent_committer)
        self.assign_reviewers()

        futures = []
        with ThreadPoolExecutor() as executor:
            futures.append(executor.submit(self._run_sonarqube))
            futures.append(executor.submit(self._run_tox))
            futures.append(executor.submit(self._install_python_module))
            futures.append(executor.submit(self._build_container))

        for _ in as_completed(futures):
            pass

    def run_retest_if_queued(self):
        last_commit_check_runs = list(self.last_commit.get_check_runs())
        for check_run in last_commit_check_runs:
            if check_run.status == QUEUED_STR:
                if check_run.name == TOX_STR:
                    self.app.logger.info(f"{self.log_prefix} retest {TOX_STR}.")
                    self._run_tox()
                if check_run.name == BUILD_CONTAINER_STR:
                    self.app.logger.info(
                        f"{self.log_prefix} retest {BUILD_CONTAINER_STR}."
                    )
                    self._build_container()

                if check_run.name == PYTHON_MODULE_INSTALL_STR:
                    self.app.logger.info(
                        f"{self.log_prefix} retest {PYTHON_MODULE_INSTALL_STR}."
                    )
                    self._install_python_module()

    def is_check_run_in_progress(self, check_run):
        for run in self.last_commit.get_check_runs():
            if run.name == check_run and run.status == IN_PROGRESS_STR:
                return True
        return False

    def _run_sonarqube(self):
        if not self.sonarqube_project_key:
            return False

        self.set_sonarqube_in_progress()
        target_url = f"{self.sonarqube_url}/dashboard?id={self.sonarqube_project_key}"
        cmd = self.sonarqube_api.get_sonar_scanner_command(
            project_key=self.sonarqube_project_key
        )
        if self._run_in_container(command=cmd)[0]:
            project_status = self.sonarqube_api.get_project_quality_status(
                project_key=self.sonarqube_project_key
            )
            project_status_res = project_status["projectStatus"]["status"]
            if project_status_res == "OK":
                return self.set_sonarqube_success(details_url=target_url)
            else:
                self.app.logger.info(
                    f"{self.log_prefix} Sonarqube scan failed, status: {project_status_res}"
                )
                return self.set_sonarqube_failure(details_url=target_url)
        return self.set_sonarqube_failure(details_url=target_url)

    def set_check_run_status(
        self, check_run, status=None, conclusion=None, details_url=None
    ):
        kwargs = {
            "name": check_run,
            "head_sha": self.last_commit.sha,
        }
        if status:
            kwargs["status"] = status

        if conclusion:
            kwargs["conclusion"] = conclusion

        if details_url:
            kwargs["details_url"] = details_url

        self.app.logger.info(
            f"{self.log_prefix} Set {check_run} check to {status or conclusion}"
        )
        return self.repository_by_github_app.create_check_run(**kwargs)

    def _get_check_run_result_file_path(self, check_run):
        base_path = os.path.join(self.webhook_server_data_dir, check_run)
        if not os.path.exists(base_path):
            os.makedirs(name=base_path, exist_ok=True)

        file_name = f"PR-{self.pull_request.number}-{self.last_commit.sha}"
        file_path = os.path.join(base_path, file_name)
        url_path = f"{self.webhook_url}{APP_ROOT_PATH}/{check_run}/{file_name}"
        return file_path, url_path

    def _run_in_container(self, command, env=None, file_path=None):
        podman_base_cmd = (
            f"podman run --privileged -v /var/lib/containers:/var/lib/containers --rm {env if env else ''} "
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
        if self.pull_request:
            clone_base_cmd += f" && git checkout origin/pr/{self.pull_request.number}"

        # final podman command
        podman_base_cmd += f" '{clone_base_cmd} && {command}'"
        return run_command(
            command=podman_base_cmd,
            log_prefix=self.log_prefix,
            file_path=file_path,
        )
