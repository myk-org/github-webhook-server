from __future__ import annotations
import contextlib
import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple

from fastapi import FastAPI
from jira import JIRA
import requests
import shortuuid
import yaml
from github import GithubException
from github.PullRequest import PullRequest
from github.GithubException import UnknownObjectException
from simple_logger.logger import get_logger
from timeout_sampler import TimeoutSampler, TimeoutExpiredError

from webhook_server_container.libs.config import Config
from webhook_server_container.libs.jira_api import JiraApi
from webhook_server_container.utils.constants import (
    ADD_STR,
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
    FASTAPI_APP,
    HAS_CONFLICTS_LABEL_STR,
    HOLD_LABEL_STR,
    IN_PROGRESS_STR,
    JIRA_STR,
    LGTM_STR,
    NEEDS_REBASE_LABEL_STR,
    PYTHON_MODULE_INSTALL_STR,
    QUEUED_STR,
    REACTIONS,
    SIZE_LABEL_PREFIX,
    STATIC_LABELS_DICT,
    SUCCESS_STR,
    TOX_STR,
    USER_LABELS_DICT,
    VERIFIED_LABEL_STR,
    WIP_STR,
    PRE_COMMIT_STR,
    OTHER_MAIN_BRANCH,
)
from pyhelper_utils.general import ignore_exceptions
from webhook_server_container.utils.dockerhub_rate_limit import DockerHub
from webhook_server_container.utils.github_repository_settings import (
    get_repository_github_app_api,
)
from webhook_server_container.utils.helpers import (
    get_api_with_highest_rate_limit,
    extract_key_from_dict,
    get_github_repo_api,
    get_value_from_dicts,
    run_command,
    get_apis_and_tokes_from_config,
)


LOGGER = get_logger(name="GitHubApi", filename=os.environ.get("WEBHOOK_SERVER_LOG_FILE"))


class RepositoryNotFoundError(Exception):
    pass


class GitHubApi:
    def __init__(self, hook_data):
        self.app: FastAPI = FASTAPI_APP
        self.hook_data: Dict[Any, Any] = hook_data
        self.repository_name: str = hook_data["repository"]["name"]
        self.log_prefix_with_color: str | None = None
        self.pull_request: PullRequest | None = None
        self.parent_committer: str | None = None
        self.log_uuid: str = shortuuid.uuid()[:5]
        self.container_repo_dir: str = "/tmp/repository"
        self.jira_conn: JIRA | None = None
        self.jira_track_pr: bool = False
        self.issue_title: str | None = None
        self.all_required_status_checks: List[str] = []

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
        self.repository_full_name = None
        self.github_app_id = None
        self.container_release = None
        self.can_be_merged_required_labels = []
        self.jira = None
        self.jira_tracking = False
        self.jira_enabled_repository = False
        # End of filled by self._repo_data_from_config()

        self.config = Config()
        self._repo_data_from_config()
        self._set_log_prefix_color()
        # self.log_repository_features()

        self.github_app_api = get_repository_github_app_api(config_=self.config, repository=self.repository_full_name)
        if not self.github_app_api:
            LOGGER.error(
                f"Repository {self.repository_full_name} not found by manage-repositories-app, "
                f"make sure the app installed (https://github.com/apps/manage-repositories-app)"
            )
            return

        self.github_api, self.token = get_api_with_highest_rate_limit(
            config=self.config, repository_name=self.repository_name
        )

        self.repository = get_github_repo_api(github_api=self.github_api, repository=self.repository_full_name)
        self.repository_by_github_app = get_github_repo_api(
            github_api=self.github_app_api, repository=self.repository_full_name
        )
        if not (self.repository or self.repository_by_github_app):
            LOGGER.error(f"{self.log_prefix} Failed to get repository.")
            return

        self.add_api_users_to_auto_verified_and_merged_users()
        self.clone_repository_path = os.path.join("/", self.repository.name)
        self.dockerhub = DockerHub(username=self.dockerhub_username, password=self.dockerhub_password)

        self.pull_request = self._get_pull_request()
        self.owners_content = self.get_owners_content()

        if self.pull_request:
            self.last_commit = self._get_last_commit()
            self.parent_committer = self.pull_request.user.login
            self.last_committer = self.last_commit.committer.login
            self.pull_request_branch = self.pull_request.base.ref
            self.all_required_status_checks = self.get_all_required_status_checks()

            if self.jira_enabled_repository:
                reviewers_and_approvers = self.reviewers + self.approvers
                if self.parent_committer in reviewers_and_approvers:
                    self.jira_assignee = self.jira_user_mapping.get(self.parent_committer)
                    if not self.jira_assignee:
                        LOGGER.info(
                            f"{self.log_prefix} Jira tracking is disabled for the current pull request. "
                            f"Committer {self.parent_committer} is not in configures in jira-user-mapping"
                        )
                    else:
                        self.jira_track_pr = True
                        self.issue_title = f"[AUTO:FROM:GITHUB] [{self.repository_name}] PR [{self.pull_request.number}]: {self.pull_request.title}"
                        LOGGER.info(f"{self.log_prefix} Jira tracking is enabled for the current pull request.")
                else:
                    LOGGER.info(
                        f"{self.log_prefix} Jira tracking is disabled for the current pull request. "
                        f"Committer {self.parent_committer} is not in {reviewers_and_approvers}"
                    )

        self.supported_user_labels_str = "".join([f" * {label}\n" for label in USER_LABELS_DICT.keys()])
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
 * To build and push container image command `/build-and-push-container` in the PR (tag will be the PR number).
 * To add a label by comment use `/<label name>`, to remove, use `/<label name> cancel`
 * To assign reviewers based on OWNERS file use `/assign-reviewers`
 * To check if PR can be merged use `/check-can-merge`

<details>
<summary>Supported /retest check runs</summary>

{self.prepare_retest_wellcome_msg}
</details>

<details>
<summary>Supported labels</summary>

{self.supported_user_labels_str}
</details>
    """

    @property
    def prepare_retest_wellcome_msg(self):
        retest_msg = ""
        if self.tox_enabled:
            retest_msg += f" * `/retest {TOX_STR}`: Retest tox\n"
        if self.build_and_push_container:
            retest_msg += f" * `/retest {BUILD_CONTAINER_STR}`: Retest build-container\n"
        if self.pypi:
            retest_msg += f" * `/retest {PYTHON_MODULE_INSTALL_STR}`: Retest python-module-install\n"

        return " * This repository does not support retest actions" if not retest_msg else retest_msg

    def add_api_users_to_auto_verified_and_merged_users(self):
        apis_and_tokens = get_apis_and_tokes_from_config(config=self.config, repository_name=self.repository_name)
        self.auto_verified_and_merged_users.extend([_api[0].get_user().login for _api in apis_and_tokens])

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

        self.log_prefix_with_color = repo_str.format(color=color, name=self.repository_name)

        with open(color_file, "w") as fd:
            json.dump(color_json, fd)

    @property
    def log_prefix(self):
        return (
            f"{self.log_prefix_with_color}({self.log_uuid})[PR {self.pull_request.number}]:"
            if self.pull_request
            else f"{self.log_prefix_with_color}:({self.log_uuid})"
        )

    def hash_token(self, message):
        hashed_message = message.replace(self.token, "*****")
        return hashed_message

    def app_logger_info(self, message):
        hashed_message = self.hash_token(message=message)
        LOGGER.info(hashed_message)

    def app_logger_error(self, message):
        hashed_message = self.hash_token(message=message)
        LOGGER.error(hashed_message)

    def process_hook(self, data, event_log):
        LOGGER.info(f"{self.log_prefix} {event_log}")
        if data == "ping":
            return

        if data == "issue_comment":
            self.process_comment_webhook_data()

        elif data == "pull_request":
            self.process_pull_request_webhook_data()

        elif data == "push":
            self.process_push_webhook_data()

        elif data == "pull_request_review":
            self.process_pull_request_review_webhook_data()

        elif data == "check_run":
            self.process_pull_request_check_run_webhook_data()

    def process_pull_request_check_run_webhook_data(self):
        _check_run = self.hook_data["check_run"]
        check_run_name = _check_run["name"]
        if check_run_name == CAN_BE_MERGED_STR:
            LOGGER.info(f"{self.log_prefix} check_run '{check_run_name}' skipped")
            return

        if (
            self.hook_data["action"] == "completed"
            and _check_run["conclusion"] == SUCCESS_STR
            and check_run_name in self.all_required_status_checks
        ):
            LOGGER.info(f"{self.log_prefix} check_run '{check_run_name}' completed and {SUCCESS_STR}")
            for _pull_request in self.repository.get_pulls(state="open"):
                _last_commit = list(_pull_request.get_commits())[-1]
                for _commit_check_run in _last_commit.get_check_runs():
                    if _commit_check_run.id == int(_check_run["id"]):
                        self.pull_request = _pull_request
                        self.last_commit = self._get_last_commit()
                        self.check_if_can_be_merged()
                        break

    def _repo_data_from_config(self):
        config_data = self.config.data  # Global repositories configuration
        repo_data = self.config.get_repository(
            repository_name=self.repository_name
        )  # Specific repository configuration

        if not repo_data:
            raise RepositoryNotFoundError(f"Repository {self.repository_name} not found in config file")

        self.github_app_id = get_value_from_dicts(
            primary_dict=repo_data, secondary_dict=config_data, key="github-app-id"
        )
        self.repository_full_name = repo_data["name"]
        self.pypi = get_value_from_dicts(primary_dict=repo_data, secondary_dict=config_data, key="pypi")
        self.verified_job = get_value_from_dicts(
            primary_dict=repo_data,
            secondary_dict=config_data,
            key="verified-job",
            return_on_none=True,
        )
        self.tox_enabled = get_value_from_dicts(primary_dict=repo_data, secondary_dict=config_data, key="tox")
        self.tox_python_version = get_value_from_dicts(
            primary_dict=repo_data,
            secondary_dict=config_data,
            key="tox-python-version",
            return_on_none="python",
        )
        self.slack_webhook_url = get_value_from_dicts(
            primary_dict=repo_data, secondary_dict=config_data, key="slack_webhook_url"
        )
        self.build_and_push_container = repo_data.get("container")
        self.dockerhub = get_value_from_dicts(primary_dict=repo_data, secondary_dict=config_data, key="docker")
        self.pre_commit = get_value_from_dicts(primary_dict=repo_data, secondary_dict=config_data, key="pre-commit")
        self.jira = get_value_from_dicts(primary_dict=repo_data, secondary_dict=config_data, key="jira")

        if self.jira:
            self.jira_server = self.jira.get("server")
            self.jira_project = self.jira.get("project")
            self.jira_token = self.jira.get("token")
            self.jira_epic = self.jira.get("epic")
            self.jira_user_mapping = self.jira.get("user-mapping", {})

            # Check if repository is enabled for jira
            self.jira_tracking = get_value_from_dicts(
                primary_dict=repo_data, secondary_dict=config_data, key="jira-tracking"
            )
            if self.jira_tracking:
                self.jira_enabled_repository = all([self.jira_server, self.jira_project, self.jira_token])
                if not self.jira_enabled_repository:
                    # if not (self.jira_enabled_repository := all([self.jira_server, self.jira_project, self.jira_token])):
                    LOGGER.error(
                        f"{self.log_prefix} Jira configuration is not valid. Server: {self.jira_server}, Project: {self.jira_project}, Token: {self.jira_token}"
                    )

        if self.dockerhub:
            self.dockerhub_username = self.dockerhub["username"]
            self.dockerhub_password = self.dockerhub["password"]

        if self.build_and_push_container:
            self.container_repository_username = self.build_and_push_container["username"]
            self.container_repository_password = self.build_and_push_container["password"]
            self.container_repository = self.build_and_push_container["repository"]
            self.dockerfile = self.build_and_push_container.get("dockerfile", "Dockerfile")
            self.container_tag = self.build_and_push_container.get("tag", "latest")
            self.container_build_args = self.build_and_push_container.get("build-args")
            self.container_command_args = self.build_and_push_container.get("args")
            self.container_release = self.build_and_push_container.get("release")

        self.auto_verified_and_merged_users = get_value_from_dicts(
            primary_dict=repo_data,
            secondary_dict=config_data,
            key="auto-verified-and-merged-users",
            return_on_none=[],
        )
        self.can_be_merged_required_labels = get_value_from_dicts(
            primary_dict=repo_data,
            secondary_dict=config_data,
            key="can-be-merged-required-labels",
            return_on_none=[],
        )

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

        LOGGER.info(f"{self.log_prefix} No issue or pull_request found in hook data")

    def _get_last_commit(self):
        return list(self.pull_request.get_commits())[-1]

    def label_exists_in_pull_request(self, label):
        return any(lb for lb in self.pull_request_labels_names() if lb == label)

    def pull_request_labels_names(self):
        return [lb.name for lb in self._get_pull_request(number=self.pull_request.number).labels]

    def skip_if_pull_request_already_merged(self):
        if self.pull_request.is_merged():
            LOGGER.info(f"{self.log_prefix}: PR is merged, not processing")
            return True

    @ignore_exceptions(logger=LOGGER)
    def _remove_label(self, label):
        if self.label_exists_in_pull_request(label=label):
            LOGGER.info(f"{self.log_prefix} Removing label {label}")
            self.pull_request.remove_from_labels(label)
            return self.wait_for_label(label=label, exists=False)

        LOGGER.warning(f"{self.log_prefix} Label {label} not found and cannot be removed")

    @ignore_exceptions(logger=LOGGER)
    def _add_label(self, label):
        label = label.strip()
        if len(label) > 49:
            LOGGER.warning(f"{label} is to long, not adding.")
            return

        if self.label_exists_in_pull_request(label=label):
            LOGGER.info(f"{self.log_prefix} Label {label} already assign to PR {self.pull_request.number}")
            return

        if label in STATIC_LABELS_DICT:
            LOGGER.info(f"{self.log_prefix} Adding pull request label {label} to {self.pull_request.number}")
            return self.pull_request.add_to_labels(label)

        _color = [DYNAMIC_LABELS_DICT[_label] for _label in DYNAMIC_LABELS_DICT if _label in label]
        LOGGER.info(f"{self.log_prefix} Label {label} was {'found' if _color else 'not found'} in labels dict")
        color = _color[0] if _color else "D4C5F9"
        LOGGER.info(f"{self.log_prefix} Adding label {label} with color {color}")

        try:
            _repo_label = self.repository.get_label(label)
            _repo_label.edit(name=_repo_label.name, color=color)
            LOGGER.info(f"{self.log_prefix} Edit repository label {label} with color {color}")
        except UnknownObjectException:
            LOGGER.info(f"{self.log_prefix} Add repository label {label} with color {color}")
            self.repository.create_label(name=label, color=color)

        LOGGER.info(f"{self.log_prefix} Adding pull request label {label} to {self.pull_request.number}")
        self.pull_request.add_to_labels(label)
        return self.wait_for_label(label=label, exists=True)

    def wait_for_label(self, label, exists):
        try:
            for sample in TimeoutSampler(
                wait_timeout=30,
                sleep=5,
                func=self.label_exists_in_pull_request,
                label=label,
            ):
                if sample == exists:
                    return True
        except TimeoutExpiredError:
            LOGGER.warning(f"{self.log_prefix} Label {label} {'not found' if exists else 'found'}")

    def _generate_issue_title(self):
        return f"{self.pull_request.title} - {self.pull_request.number}"

    def _generate_issue_body(self):
        return f"[Auto generated]\nNumber: [#{self.pull_request.number}]"

    @ignore_exceptions(logger=LOGGER)
    def is_branch_exists(self, branch):
        return self.repository.get_branch(branch)

    def upload_to_pypi(self, tag_name):
        out, err = "", ""
        token = self.pypi["token"]
        env = f"-e TWINE_USERNAME=__token__ -e TWINE_PASSWORD={token} "
        LOGGER.info(f"{self.log_prefix} Start uploading to pypi")
        _dist_dir = "/tmp/dist"
        cmd = (
            f" python3 -m build --sdist --outdir {_dist_dir} ."
            f" && twine check {_dist_dir}/$(echo *.tar.gz)"
            f" && twine upload {_dist_dir}/$(echo *.tar.gz) --skip-existing"
        )
        try:
            rc, out, err = self._run_in_container(command=cmd, env=env, checkout=tag_name)
            if rc:
                LOGGER.info(f"{self.log_prefix} Publish to pypi finished")
                if self.slack_webhook_url:
                    message = f"""
```
{self.repository_name} Version {tag_name} published to PYPI.
```
"""
                    self.send_slack_message(message=message, webhook_url=self.slack_webhook_url)

        except Exception as exp:
            err = f"Publish to pypi failed: {exp}"
            LOGGER.error(f"{self.log_prefix} {err}")
            self.repository.create_issue(
                title=err,
                assignee=self.approvers[0] if self.approvers else None,
                body=f"""
stdout: `{out}`
stderr: `{err}`
""",
            )

    def get_owners_content(self):
        try:
            owners_content = self.repository.get_contents("OWNERS")
            _content = yaml.safe_load(owners_content.decoded_content)
            LOGGER.info(f"{self.log_prefix} OWNERS file content: {_content}")
            return _content
        except UnknownObjectException:
            LOGGER.error(f"{self.log_prefix} OWNERS file not found")
            return {}

    @property
    def reviewers(self):
        bc_reviewers = self.owners_content.get("reviewers", [])
        if isinstance(bc_reviewers, dict):
            _reviewers = self.owners_content.get("reviewers", {}).get("any", [])
        else:
            _reviewers = bc_reviewers

        LOGGER.info(f"{self.log_prefix} Reviewers: {_reviewers}")
        return _reviewers

    @property
    def files_reviewers(self):
        _reviewers = self.owners_content.get("reviewers")
        if isinstance(_reviewers, dict):
            return _reviewers.get("files", {})
        return {}

    @property
    def folders_reviewers(self):
        _reviewers = self.owners_content.get("reviewers")
        if isinstance(_reviewers, dict):
            return _reviewers.get("folders", {})
        return {}

    @property
    def approvers(self):
        return self.owners_content.get("approvers", [])

    def list_changed_commit_files(self):
        return [fd["filename"] for fd in self.last_commit.raw_data["files"]]

    def assign_reviewers(self):
        LOGGER.info(f"{self.log_prefix} Assign reviewers")
        changed_files = self.list_changed_commit_files()
        reviewers_to_add = self.reviewers
        for _file, _reviewers in self.files_reviewers.items():
            if _file in changed_files:
                reviewers_to_add.extend(_reviewers)

        for _folder, _reviewers in self.folders_reviewers.items():
            if any(cf for cf in changed_files if _folder in str(Path(cf).parent)):
                reviewers_to_add.extend(_reviewers)

        _to_add = list(set(reviewers_to_add))
        LOGGER.info(f"{self.log_prefix} Reviewers to add: {_to_add}")
        for reviewer in _to_add:
            if reviewer != self.pull_request.user.login:
                LOGGER.info(f"{self.log_prefix} Adding reviewer {reviewer}")
                try:
                    self.pull_request.create_review_request([reviewer])
                except GithubException as ex:
                    LOGGER.error(f"{self.log_prefix} Failed to add reviewer {reviewer}. {ex}")

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

        exists_size_label = [label for label in self.pull_request_labels_names() if label.startswith(SIZE_LABEL_PREFIX)]

        if exists_size_label:
            self._remove_label(label=exists_size_label[0])

        self._add_label(label=size_label)

    def label_by_user_comment(self, user_request, remove, reviewed_user, issue_comment_id):
        if not any(user_request.startswith(label_name) for label_name in USER_LABELS_DICT):
            LOGGER.info(f"{self.log_prefix} Label {user_request} is not a predefined one, will not be added / removed.")

            return

        LOGGER.info(
            f"{self.log_prefix} {'Remove' if remove else 'Add'} "
            f"label requested by user {reviewed_user}: {user_request}"
        )
        self.create_comment_reaction(issue_comment_id=issue_comment_id, reaction=REACTIONS.ok)

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
        LOGGER.info(f"{self.log_prefix} Processing reset {VERIFIED_LABEL_STR} label on new commit push")
        # Remove verified label
        self._remove_label(label=VERIFIED_LABEL_STR)

    @ignore_exceptions(logger=LOGGER)
    def set_verify_check_queued(self):
        return self.set_check_run_status(check_run=VERIFIED_LABEL_STR, status=QUEUED_STR)

    @ignore_exceptions(logger=LOGGER)
    def set_verify_check_success(self):
        return self.set_check_run_status(check_run=VERIFIED_LABEL_STR, conclusion=SUCCESS_STR)

    @ignore_exceptions(logger=LOGGER)
    def set_run_tox_check_queued(self):
        if not self.tox_enabled:
            return False

        return self.set_check_run_status(check_run=TOX_STR, status=QUEUED_STR)

    @ignore_exceptions(logger=LOGGER)
    def set_run_tox_check_in_progress(self):
        return self.set_check_run_status(check_run=TOX_STR, status=IN_PROGRESS_STR)

    @ignore_exceptions(logger=LOGGER)
    def set_run_tox_check_failure(self, output):
        return self.set_check_run_status(check_run=TOX_STR, conclusion=FAILURE_STR, output=output)

    @ignore_exceptions(logger=LOGGER)
    def set_run_tox_check_success(self, output):
        return self.set_check_run_status(check_run=TOX_STR, conclusion=SUCCESS_STR, output=output)

    @ignore_exceptions(logger=LOGGER)
    def set_run_pre_commit_check_queued(self):
        if not self.pre_commit:
            return False

        return self.set_check_run_status(check_run=PRE_COMMIT_STR, status=QUEUED_STR)

    @ignore_exceptions(logger=LOGGER)
    def set_run_pre_commit_check_in_progress(self):
        return self.set_check_run_status(check_run=PRE_COMMIT_STR, status=IN_PROGRESS_STR)

    @ignore_exceptions(logger=LOGGER)
    def set_run_pre_commit_check_failure(self, output):
        return self.set_check_run_status(check_run=PRE_COMMIT_STR, conclusion=FAILURE_STR, output=output)

    @ignore_exceptions(logger=LOGGER)
    def set_run_pre_commit_check_success(self, output):
        return self.set_check_run_status(check_run=PRE_COMMIT_STR, conclusion=SUCCESS_STR, output=output)

    @ignore_exceptions(logger=LOGGER)
    def set_merge_check_queued(self, output=None):
        return self.set_check_run_status(check_run=CAN_BE_MERGED_STR, status=QUEUED_STR, output=output)

    @ignore_exceptions(logger=LOGGER)
    def set_merge_check_in_progress(self):
        return self.set_check_run_status(check_run=CAN_BE_MERGED_STR, status=IN_PROGRESS_STR)

    @ignore_exceptions(logger=LOGGER)
    def set_merge_check_success(self):
        return self.set_check_run_status(check_run=CAN_BE_MERGED_STR, conclusion=SUCCESS_STR)

    @ignore_exceptions(logger=LOGGER)
    def set_merge_check_failure(self, output):
        return self.set_check_run_status(check_run=CAN_BE_MERGED_STR, conclusion=FAILURE_STR, output=output)

    @ignore_exceptions(logger=LOGGER)
    def set_container_build_queued(self):
        if not self.build_and_push_container:
            return

        return self.set_check_run_status(check_run=BUILD_CONTAINER_STR, status=QUEUED_STR)

    @ignore_exceptions(logger=LOGGER)
    def set_container_build_in_progress(self):
        return self.set_check_run_status(check_run=BUILD_CONTAINER_STR, status=IN_PROGRESS_STR)

    @ignore_exceptions(logger=LOGGER)
    def set_container_build_success(self, output):
        return self.set_check_run_status(check_run=BUILD_CONTAINER_STR, conclusion=SUCCESS_STR, output=output)

    @ignore_exceptions(logger=LOGGER)
    def set_container_build_failure(self, output):
        return self.set_check_run_status(check_run=BUILD_CONTAINER_STR, conclusion=FAILURE_STR, output=output)

    @ignore_exceptions(logger=LOGGER)
    def set_python_module_install_queued(self):
        if not self.pypi:
            return False

        return self.set_check_run_status(check_run=PYTHON_MODULE_INSTALL_STR, status=QUEUED_STR)

    @ignore_exceptions(logger=LOGGER)
    def set_python_module_install_in_progress(self):
        return self.set_check_run_status(check_run=PYTHON_MODULE_INSTALL_STR, status=IN_PROGRESS_STR)

    @ignore_exceptions(logger=LOGGER)
    def set_python_module_install_success(self, output):
        return self.set_check_run_status(check_run=PYTHON_MODULE_INSTALL_STR, conclusion=SUCCESS_STR, output=output)

    @ignore_exceptions(logger=LOGGER)
    def set_python_module_install_failure(self, output):
        return self.set_check_run_status(check_run=PYTHON_MODULE_INSTALL_STR, conclusion=FAILURE_STR, output=output)

    @ignore_exceptions(logger=LOGGER)
    def set_cherry_pick_in_progress(self):
        return self.set_check_run_status(check_run=CHERRY_PICKED_LABEL_PREFIX, status=IN_PROGRESS_STR)

    @ignore_exceptions(logger=LOGGER)
    def set_cherry_pick_success(self, output):
        return self.set_check_run_status(check_run=CHERRY_PICKED_LABEL_PREFIX, conclusion=SUCCESS_STR, output=output)

    @ignore_exceptions(logger=LOGGER)
    def set_cherry_pick_failure(self, output):
        return self.set_check_run_status(check_run=CHERRY_PICKED_LABEL_PREFIX, conclusion=FAILURE_STR, output=output)

    @ignore_exceptions(logger=LOGGER)
    def create_issue_for_new_pull_request(self):
        if self.parent_committer in self.auto_verified_and_merged_users:
            LOGGER.info(
                f"{self.log_prefix} Committer {self.parent_committer} is part of "
                f"{self.auto_verified_and_merged_users}, will not create issue."
            )
            return

        LOGGER.info(f"{self.log_prefix} Creating issue for new PR: {self.pull_request.title}")
        self.repository.create_issue(
            title=self._generate_issue_title(),
            body=self._generate_issue_body(),
            assignee=self.pull_request.user.login,
        )

    @ignore_exceptions(logger=LOGGER)
    def close_issue_for_merged_or_closed_pr(self, hook_action):
        for issue in self.repository.get_issues():
            if issue.body == self._generate_issue_body():
                LOGGER.info(f"{self.log_prefix} Closing issue {issue.title} for PR: {self.pull_request.title}")
                issue.create_comment(
                    f"{self.log_prefix} Closing issue for PR: {self.pull_request.title}.\nPR was {hook_action}."
                )
                issue.edit(state="closed")
                break

    @ignore_exceptions(logger=LOGGER)
    def delete_remote_tag_for_merged_or_closed_pr(self):
        if not self.pull_request:
            LOGGER.warning(f"{self.log_prefix} [Delete remote container TAG] - No pull request found")
            return

        if not self.container_repository:
            LOGGER.info(f"{self.log_prefix} repository do not have container configured")
            return

        repository_full_tag = self._container_repository_and_tag()
        if not repository_full_tag:
            return

        pr_tag = repository_full_tag.split(":")[-1]
        base_regctl_command = (
            "podman run --rm --net host  -v regctl-conf:/home/appuser/.regctl/ ghcr.io/regclient/regctl:latest"
        )
        registry_info = self.container_repository.split("/")
        registry_url = "" if len(registry_info) < 3 else registry_info[0]

        rc, out, err = run_command(
            command=f"{base_regctl_command} registry login {registry_url} -u {self.container_repository_username} "
            f"-p {self.container_repository_password}",
            log_prefix=self.log_prefix,
        )
        if rc:
            rc, out, err = run_command(
                command=f"{base_regctl_command} tag ls {self.container_repository} --include {pr_tag}",
                log_prefix=self.log_prefix,
            )
            if rc and out:
                if run_command(
                    command=f"{base_regctl_command} tag delete {repository_full_tag}",
                    log_prefix=self.log_prefix,
                )[0]:
                    self.pull_request.create_issue_comment(f"Successfully removed PR tag: {repository_full_tag}.")
                else:
                    LOGGER.error(f"{self.log_prefix} Failed to delete tag: {repository_full_tag}. OUT:{out}. ERR:{err}")
            else:
                LOGGER.warning(
                    f"{self.log_prefix} {pr_tag} tag not found in registry {self.container_repository}. OUT:{out}. ERR:{err}"
                )
        else:
            self.pull_request.create_issue_comment(
                f"Failed to delete tag: {repository_full_tag}. Please delete it manually."
            )
            LOGGER.error(f"{self.log_prefix} Failed to delete tag: {repository_full_tag}. OUT:{out}. ERR:{err}")

    def process_comment_webhook_data(self):
        if self.hook_data["action"] in ("action", "deleted"):
            return

        issue_number = self.hook_data["issue"]["number"]
        LOGGER.info(f"{self.log_prefix} Processing issue {issue_number}")

        if not self.pull_request:
            return

        body = self.hook_data["comment"]["body"]

        if body == self.welcome_msg:
            LOGGER.info(f"{self.log_prefix} Welcome message found in issue {self.pull_request.title}. Not processing")
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
        LOGGER.info(f"{self.log_prefix} hook_action is: {hook_action}")
        if not self.pull_request:
            return

        pull_request_data = self.hook_data["pull_request"]
        self.parent_committer = pull_request_data["user"]["login"]
        self.pull_request_branch = pull_request_data["base"]["ref"]

        if hook_action == "opened":
            LOGGER.info(f"{self.log_prefix} Creating welcome comment")
            self.pull_request.create_issue_comment(self.welcome_msg)
            self.create_issue_for_new_pull_request()

            if self.jira_track_pr:
                self.get_jira_conn()
                if not self.jira_conn:
                    LOGGER.error(f"{self.log_prefix} Jira connection not found")
                    return

                LOGGER.info(f"{self.log_prefix} Creating Jira story")
                jira_story_key = self.jira_conn.create_story(
                    title=self.issue_title,
                    body=self.pull_request.html_url,
                    epic_key=self.jira_epic,
                    assignee=self.jira_assignee,
                )
                self._add_label(label=f"{JIRA_STR}:{jira_story_key}")

            self.process_opened_or_synchronize_pull_request()

        if hook_action == "synchronize":
            for _label in self.pull_request.labels:
                _label_name = _label.name
                if (
                    _label_name.startswith(APPROVED_BY_LABEL_PREFIX)
                    or _label_name.startswith(COMMENTED_BY_LABEL_PREFIX)
                    or _label_name.startswith(CHANGED_REQUESTED_BY_LABEL_PREFIX)
                ):
                    self._remove_label(label=_label_name)

            if self.jira_track_pr:
                if _story_key := self.get_story_key_with_jira_connection():
                    LOGGER.info(f"{self.log_prefix} Creating sub-task for Jira story {_story_key}")
                    self.jira_conn.create_closed_subtask(
                        title=f"{self.issue_title}: New commit from {self.last_committer}",
                        parent_key=_story_key,
                        assignee=self.jira_assignee,
                        body=f"PR: {self.pull_request.title}, new commit pushed by {self.last_committer}",
                    )

            self.process_opened_or_synchronize_pull_request()

        if hook_action == "closed":
            self.close_issue_for_merged_or_closed_pr(hook_action=hook_action)
            self.delete_remote_tag_for_merged_or_closed_pr()
            is_merged = pull_request_data.get("merged")

            if self.jira_track_pr:
                if _story_key := self.get_story_key_with_jira_connection():
                    LOGGER.info(f"{self.log_prefix} Closing Jira story")
                    self.jira_conn.close_issue(
                        key=_story_key,
                        comment=f"PR: {self.pull_request.title} is closed. Megred: {is_merged}",
                    )

            if is_merged:
                LOGGER.info(f"{self.log_prefix} PR is merged")

                for _label in self.pull_request.labels:
                    _label_name = _label.name
                    if _label_name.startswith(CHERRY_PICK_LABEL_PREFIX):
                        self.cherry_pick(target_branch=_label_name.replace(CHERRY_PICK_LABEL_PREFIX, ""))

                self._run_build_container(
                    push=True,
                    set_check=False,
                    is_merged=is_merged,
                )

                # label_by_pull_requests_merge_state_after_merged will override self.pull_request
                original_pull_request = self.pull_request
                self.label_by_pull_requests_merge_state_after_merged()
                self.pull_request = original_pull_request

        if hook_action in ("labeled", "unlabeled"):
            action_labeled = hook_action == "labeled"
            labeled = self.hook_data["label"]["name"].lower()
            if labeled == CAN_BE_MERGED_STR:
                return

            LOGGER.info(f"{self.log_prefix} PR {self.pull_request.number} {hook_action} with {labeled}")
            if self.verified_job and labeled == VERIFIED_LABEL_STR:
                if action_labeled:
                    self.set_verify_check_success()
                else:
                    self.set_verify_check_queued()

            return self.check_if_can_be_merged()

    def process_push_webhook_data(self):
        tag = re.search(r"refs/tags/?(.*)", self.hook_data["ref"])
        if tag:
            tag_name = tag.group(1)
            LOGGER.info(f"{self.log_prefix} Processing push for tag: {tag.group(1)}")
            if self.pypi:
                LOGGER.info(f"{self.log_prefix} Processing upload to pypi for tag: {tag_name}")
                self.upload_to_pypi(tag_name=tag_name)

            if self.container_release:
                LOGGER.info(f"{self.log_prefix} Processing build and push container for tag: {tag_name}")
                self._run_build_container(push=True, set_check=False, tag=tag_name)

    def process_pull_request_review_webhook_data(self):
        if not self.pull_request:
            return

        if self.hook_data["action"] == "submitted":
            """
            commented
            approved
            changes_requested
            """
            reviewed_user = self.hook_data["review"]["user"]["login"]

            review_state = self.hook_data["review"]["state"]
            self.manage_reviewed_by_label(
                review_state=review_state,
                action=ADD_STR,
                reviewed_user=reviewed_user,
            )

            if self.jira_track_pr:
                _story_label = [_label for _label in self.pull_request.labels if _label.name.startswith(JIRA_STR)]
                if _story_label:
                    if reviewed_user == self.parent_committer or reviewed_user == self.last_committer:
                        LOGGER.info(
                            f"{self.log_prefix} Skipping Jira review sub-task creation for review by {reviewed_user} which is parent or last committer"
                        )
                        return

                    _story_key = _story_label[0].name.split(":")[-1]
                    self.get_jira_conn()
                    if not self.jira_conn:
                        LOGGER.error(f"{self.log_prefix} Jira connection not found")
                        return

                    LOGGER.info(f"{self.log_prefix} Creating sub-task for Jira story {_story_key}")
                    self.jira_conn.create_closed_subtask(
                        title=f"{self.issue_title}: reviewed by: {reviewed_user} - {review_state}",
                        parent_key=_story_key,
                        assignee=self.jira_user_mapping.get(reviewed_user, self.parent_committer),
                        body=f"PR: {self.pull_request.title}, reviewed by: {reviewed_user}",
                    )

    def manage_reviewed_by_label(self, review_state, action, reviewed_user):
        LOGGER.info(
            f"{self.log_prefix} "
            f"Processing label for review from {reviewed_user}. "
            f"review_state: {review_state}, action: {action}"
        )
        label_prefix = None
        label_to_remove = None
        check_if_can_be_merged = False

        pull_request_labels = self.pull_request_labels_names()

        if review_state in ("approved", LGTM_STR):
            base_dict = self.hook_data.get("issue", self.hook_data.get("pull_request"))
            pr_owner = base_dict["user"]["login"]
            if pr_owner == reviewed_user:
                LOGGER.info(f"{self.log_prefix} PR owner {pr_owner} set /lgtm, not adding label.")
                return

            label_prefix = APPROVED_BY_LABEL_PREFIX
            _remove_label = f"{CHANGED_REQUESTED_BY_LABEL_PREFIX}{reviewed_user}"
            if _remove_label in pull_request_labels:
                label_to_remove = _remove_label
            check_if_can_be_merged = True

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

                if check_if_can_be_merged:
                    self.check_if_can_be_merged()

            if action == DELETE_STR:
                self._remove_label(label=reviewer_label)
        else:
            LOGGER.warning(
                f"{self.log_prefix} PR {self.pull_request.number} got unsupported review state: {review_state}"
            )

    def _run_tox(self):
        if not self.tox_enabled:
            return False

        if self.is_check_run_in_progress(check_run=TOX_STR):
            LOGGER.info(f"{self.log_prefix} Check run is in progress, not running {TOX_STR}.")
            return False

        cmd = f"{self.tox_python_version} -m {TOX_STR}"
        if self.tox_enabled != "all":
            tests = self.tox_enabled.replace(" ", "")
            cmd += f" -e {tests}"

        self.set_run_tox_check_in_progress()
        rc, out, err = self._run_in_container(command=cmd)

        output = {
            "title": "Tox",
            "summary": "",
            "text": self.get_check_run_text(err=err, out=out),
        }
        if rc:
            return self.set_run_tox_check_success(output=output)
        else:
            return self.set_run_tox_check_failure(output=output)

    def _run_pre_commit(self):
        if not self.pre_commit:
            return False

        if self.is_check_run_in_progress(check_run=PRE_COMMIT_STR):
            LOGGER.info(f"{self.log_prefix} Check run is in progress, not running {PRE_COMMIT_STR}.")
            return False

        cmd = f"{PRE_COMMIT_STR} run --all-files"
        self.set_run_pre_commit_check_in_progress()
        rc, out, err = self._run_in_container(command=cmd)

        output = {
            "title": "Pre-Commit",
            "summary": "",
            "text": self.get_check_run_text(err=err, out=out),
        }
        if rc:
            return self.set_run_pre_commit_check_success(output=output)
        else:
            return self.set_run_pre_commit_check_failure(output=output)

    def user_commands(self, command, reviewed_user, issue_comment_id):
        remove = False
        available_commands = [
            "retest",
            "cherry-pick",
            "assign-reviewers",
            "check-can-merge",
        ]
        if "sonarsource.github.io" in command:
            LOGGER.info(f"{self.log_prefix} command is in ignore list")
            return

        LOGGER.info(f"{self.log_prefix} Processing label/user command {command} by user {reviewed_user}")
        command_and_args = command.split(" ", 1)
        _command = command_and_args[0]
        not_running_msg = f"Pull request already merged, not running {_command}"
        _args = command_and_args[1] if len(command_and_args) > 1 else ""
        if len(command_and_args) > 1 and _args == "cancel":
            LOGGER.info(f"{self.log_prefix} User requested 'cancel' for command {_command}")
            remove = True

        if _command in available_commands:
            if not _args and _command not in ("assign-reviewers", "check-can-merge"):
                issue_msg = f"{_command} requires an argument"
                error_msg = f"{self.log_prefix} {issue_msg}"
                LOGGER.info(error_msg)
                self.pull_request.create_issue_comment(issue_msg)
                return

            if _command == "assign-reviewers":
                self.assign_reviewers()
                return

            if _command == "check-can-merge":
                self.check_if_can_be_merged()
                return

            if _command == "cherry-pick":
                self.create_comment_reaction(issue_comment_id=issue_comment_id, reaction=REACTIONS.ok)
                _target_branches = _args.split()
                _exits_target_branches = set()
                _non_exits_target_branches_msg = ""

                for _target_branch in _target_branches:
                    try:
                        self.repository.get_branch(_target_branch)
                    except Exception:
                        _non_exits_target_branches_msg += f"Target branch `{_target_branch}` does not exist\n"

                    _exits_target_branches.add(_target_branch)

                if _non_exits_target_branches_msg:
                    LOGGER.info(f"{self.log_prefix} {_non_exits_target_branches_msg}")
                    self.pull_request.create_issue_comment(_non_exits_target_branches_msg)

                if _exits_target_branches:
                    if not self.pull_request.is_merged():
                        cp_labels = [
                            f"{CHERRY_PICK_LABEL_PREFIX}{_target_branch}" for _target_branch in _exits_target_branches
                        ]
                        info_msg = f"""
Cherry-pick requested for PR: `{self.pull_request.title}` by user `{reviewed_user}`
Adding label/s `{" ".join([_cp_label for _cp_label in cp_labels])}` for automatic cheery-pick once the PR is merged
"""
                        LOGGER.info(f"{self.log_prefix} {info_msg}")
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
                if self.skip_if_pull_request_already_merged():
                    return self.pull_request.create_issue_comment(not_running_msg)

                _target_tests = _args.split()
                for _test in _target_tests:
                    if _test == TOX_STR:
                        if not self.tox_enabled:
                            msg = f"No {TOX_STR} configured for this repository"
                            error_msg = f"{self.log_prefix} {msg}."
                            LOGGER.info(error_msg)
                            self.pull_request.create_issue_comment(msg)
                            return

                        self.create_comment_reaction(issue_comment_id=issue_comment_id, reaction=REACTIONS.ok)
                        self._run_tox()

                    elif _test == PRE_COMMIT_STR:
                        self.create_comment_reaction(issue_comment_id=issue_comment_id, reaction=REACTIONS.ok)
                        self._run_pre_commit()

                    elif _test == BUILD_CONTAINER_STR:
                        if self.build_and_push_container:
                            self.create_comment_reaction(issue_comment_id=issue_comment_id, reaction=REACTIONS.ok)
                            self._run_build_container()
                        else:
                            msg = f"No {BUILD_CONTAINER_STR} configured for this repository"
                            error_msg = f"{self.log_prefix} {msg}"
                            LOGGER.info(error_msg)
                            self.pull_request.create_issue_comment(msg)

                    elif _test == PYTHON_MODULE_INSTALL_STR:
                        if not self.pypi:
                            error_msg = f"{self.log_prefix} No pypi configured"
                            LOGGER.info(error_msg)
                            self.pull_request.create_issue_comment(error_msg)
                            return

                        self.create_comment_reaction(issue_comment_id=issue_comment_id, reaction=REACTIONS.ok)
                        self._run_install_python_module()

        elif _command == BUILD_AND_PUSH_CONTAINER_STR:
            if self.build_and_push_container:
                self.create_comment_reaction(issue_comment_id=issue_comment_id, reaction=REACTIONS.ok)
                self._run_build_container(push=True, set_check=False)
            else:
                msg = f"No {BUILD_AND_PUSH_CONTAINER_STR} configured for this repository"
                error_msg = f"{self.log_prefix} {msg}"
                LOGGER.info(error_msg)
                self.pull_request.create_issue_comment(msg)

        elif _command == WIP_STR:
            if self.skip_if_pull_request_already_merged():
                return self.pull_request.create_issue_comment(not_running_msg)

            self.create_comment_reaction(issue_comment_id=issue_comment_id, reaction=REACTIONS.ok)
            wip_for_title = f"{WIP_STR.upper()}:"
            if remove:
                self._remove_label(label=WIP_STR)
                self.pull_request.edit(title=self.pull_request.title.replace(wip_for_title, ""))
            else:
                self._add_label(label=WIP_STR)
                self.pull_request.edit(title=f"{wip_for_title} {self.pull_request.title}")

        else:
            if self.skip_if_pull_request_already_merged():
                return self.pull_request.create_issue_comment(not_running_msg)

            self.label_by_user_comment(
                user_request=_command,
                remove=remove,
                reviewed_user=reviewed_user,
                issue_comment_id=issue_comment_id,
            )

    @ignore_exceptions(logger=LOGGER)
    def cherry_pick(self, target_branch, reviewed_user=None):
        requested_by = reviewed_user or "by target-branch label"
        LOGGER.info(f"{self.log_prefix} Cherry-pick requested by user: {requested_by}")

        new_branch_name = f"{CHERRY_PICKED_LABEL_PREFIX}-{self.pull_request.head.ref}-{shortuuid.uuid()[:5]}"
        if not self.is_branch_exists(branch=target_branch):
            err_msg = f"cherry-pick failed: {target_branch} does not exists"
            LOGGER.error(err_msg)
            self.pull_request.create_issue_comment(err_msg)
        else:
            self.set_cherry_pick_in_progress()
            commit_hash = self.pull_request.merge_commit_sha
            commit_msg_striped = self.pull_request.title.replace("'", "")
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
                f'-m "{CHERRY_PICKED_LABEL_PREFIX}: [{target_branch}] {commit_msg_striped}" '
                f'-m "cherry-pick {pull_request_url} into {target_branch}" '
                f'-m "requested-by {requested_by}"'
            )
            rc, out, err = self._run_in_container(command=cmd, env=env)

            output = {
                "title": "Cherry-pick details",
                "summary": "",
                "text": self.get_check_run_text(err=err, out=out),
            }
            if rc:
                self.set_cherry_pick_success(output=output)
                self.pull_request.create_issue_comment(
                    f"Cherry-picked PR {self.pull_request.title} into {target_branch}"
                )
            else:
                self.set_cherry_pick_failure(output=output)
                LOGGER.error(f"{self.log_prefix} Cherry pick failed: {out} --- {err}")
                local_branch_name = f"{self.pull_request.head.ref}-{target_branch}"
                self.pull_request.create_issue_comment(
                    f"**Manual cherry-pick is needed**\nCherry pick failed for "
                    f"{commit_hash} to {target_branch}:\n"
                    f"To cherry-pick run:\n"
                    "```\n"
                    f"git remote update\n"
                    f"git checkout {target_branch}\n"
                    f"git pull origin {target_branch}\n"
                    f"git checkout -b {local_branch_name}\n"
                    f"git cherry-pick {commit_hash}\n"
                    f"git push origin {local_branch_name}\n"
                    "```"
                )

    @ignore_exceptions(logger=LOGGER)
    def label_by_pull_requests_merge_state_after_merged(self):
        """
        Labels pull requests based on their mergeable state.

        If the mergeable state is 'behind', the 'needs rebase' label is added.
        If the mergeable state is 'dirty', the 'has conflicts' label is added.
        """
        time_sleep = 30
        LOGGER.info(f"{self.log_prefix} Sleep for {time_sleep} seconds before getting all opened PRs")
        time.sleep(time_sleep)

        for pull_request in self.repository.get_pulls(state="open"):
            self.pull_request = pull_request
            LOGGER.info(f"{self.log_prefix} check label pull request after merge")
            self.label_pull_request_by_merge_state(_sleep=time_sleep)

    def label_pull_request_by_merge_state(self, _sleep=0):
        if _sleep:
            LOGGER.info(f"{self.log_prefix} Sleep for {_sleep} seconds before checking merge state")
            time.sleep(_sleep)

        merge_state = self.pull_request.mergeable_state
        LOGGER.info(f"{self.log_prefix} Mergeable state is {merge_state}")
        if merge_state == "unknown":
            return

        if merge_state == "behind":
            self._add_label(label=NEEDS_REBASE_LABEL_STR)
        else:
            self._remove_label(label=NEEDS_REBASE_LABEL_STR)

        if merge_state == "dirty":
            self._add_label(label=HAS_CONFLICTS_LABEL_STR)
        else:
            self._remove_label(label=HAS_CONFLICTS_LABEL_STR)

    def check_if_can_be_merged(self):
        """
        Check if PR can be merged and set the job for it

        Check the following:
            Has verified label.
            Has approved from one of the approvers.
            All required run check passed.
            PR status is not 'dirty'.
            PR has no changed requests from approvers.
        """
        if self.skip_if_pull_request_already_merged():
            return False

        output = {
            "title": "Check if can be merged",
            "summary": "",
            "text": None,
        }
        failure_output = ""

        try:
            LOGGER.info(f"{self.log_prefix} Check if {CAN_BE_MERGED_STR}.")
            self.set_merge_check_queued()
            last_commit_check_runs = list(self.last_commit.get_check_runs())
            check_runs_in_progress = [
                check_run.name
                for check_run in last_commit_check_runs
                if check_run.status == IN_PROGRESS_STR
                and check_run.name != CAN_BE_MERGED_STR
                and check_run.name in self.all_required_status_checks
            ]
            if check_runs_in_progress:
                LOGGER.info(
                    f"{self.log_prefix} Some required check runs in progress {check_runs_in_progress}, "
                    f"skipping check if {CAN_BE_MERGED_STR}."
                )
                failure_output += f"Some required check runs in progress {check_runs_in_progress}\n"

            _labels = self.pull_request_labels_names()
            is_hold = HOLD_LABEL_STR in _labels
            is_wip = WIP_STR in _labels
            if is_hold or is_wip:
                self._remove_label(label=CAN_BE_MERGED_STR)
                if is_hold:
                    failure_output += "Hold label exists.\n"

                if is_wip:
                    failure_output += "WIP label exists.\n"

            if not self.pull_request.mergeable:
                self._remove_label(label=CAN_BE_MERGED_STR)
                failure_output += "PR is not mergeable: {self.pull_request.mergeable_state}\n"

            failed_check_runs = []
            for check_run in last_commit_check_runs:
                if (
                    check_run.name == CAN_BE_MERGED_STR
                    or check_run.conclusion == SUCCESS_STR
                    or check_run.conclusion == QUEUED_STR
                    or check_run.name not in self.all_required_status_checks
                ):
                    continue

                failed_check_runs.append(check_run.name)

            if failed_check_runs:
                self._remove_label(label=CAN_BE_MERGED_STR)
                failure_output += f"Some check runs failed: {failed_check_runs}\n"

            LOGGER.info(f"{self.log_prefix} check if can be merged. PR labels are: {_labels}")

            for _label in _labels:
                if CHANGED_REQUESTED_BY_LABEL_PREFIX.lower() in _label.lower():
                    change_request_user = _label.split("-")[-1]
                    if change_request_user in self.approvers:
                        self._remove_label(label=CAN_BE_MERGED_STR)
                        failure_output += "PR has changed requests from approvers\n"

            pr_approved = False
            for _label in _labels:
                if APPROVED_BY_LABEL_PREFIX.lower() in _label.lower():
                    approved_user = _label.split("-")[-1]
                    if approved_user in self.approvers:
                        pr_approved = True
                        break

            missing_required_labels = []
            for _req_label in self.can_be_merged_required_labels:
                if _req_label not in _labels:
                    missing_required_labels.append(_req_label)

            if missing_required_labels:
                self._remove_label(label=CAN_BE_MERGED_STR)
                failure_output += f"Missing required labels: {missing_required_labels}\n"

            if pr_approved and not failure_output:
                self._add_label(label=CAN_BE_MERGED_STR)
                self.set_merge_check_success()
                if self.parent_committer in self.auto_verified_and_merged_users:
                    LOGGER.info(
                        f"{self.log_prefix} will be merged automatically. owner: {self.parent_committer} "
                        f"is part of {self.auto_verified_and_merged_users}"
                    )
                    self.pull_request.create_issue_comment(
                        f"Owner of the pull request {self.parent_committer} "
                        f"is part of:\n`{self.auto_verified_and_merged_users}`\n"
                        "Pull request is merged automatically."
                    )
                    return self.pull_request.merge(merge_method="squash")

            if not pr_approved:
                failure_output += f"Missing lgtm/approved from approvers {self.approvers}\n"

            if failure_output:
                LOGGER.info(f"{self.log_prefix} cannot be merged: {failure_output}")
                output["text"] = failure_output
                self.set_merge_check_failure(output=output)

        except Exception as ex:
            LOGGER.error(f"{self.log_prefix} Failed to check if can be merged, set check run to {FAILURE_STR} {ex}")
            output["text"] = "Failed to check if can be merged, check logs"
            return self.set_merge_check_failure(output=output)

    @staticmethod
    def _comment_with_details(title, body):
        return f"""
<details>
<summary>{title}</summary>
    {body}
</details>
        """

    def _container_repository_and_tag(self, is_merged=None, tag=None):
        if not tag:
            if is_merged:
                tag = (
                    self.pull_request_branch
                    if self.pull_request_branch not in (OTHER_MAIN_BRANCH, "main")
                    else self.container_tag
                )
            else:
                if self.pull_request:
                    tag = f"pr-{self.pull_request.number}"

        if tag:
            LOGGER.info(f"{self.log_prefix} container tag is: {tag}")
            return f"{self.container_repository}:{tag}"

        LOGGER.error(f"{self.log_prefix} container tag not found")

    @ignore_exceptions(logger=LOGGER)
    def _run_build_container(
        self,
        set_check=True,
        push=False,
        is_merged=False,
        tag=None,
    ):
        if not self.build_and_push_container:
            return False

        if set_check:
            if self.is_check_run_in_progress(check_run=BUILD_CONTAINER_STR) and not is_merged:
                LOGGER.info(f"{self.log_prefix} Check run is in progress, not running {BUILD_CONTAINER_STR}.")
                return False

            self.set_container_build_in_progress()

        _container_repository_and_tag = self._container_repository_and_tag(is_merged=is_merged, tag=tag)
        no_cache = " --no-cache" if is_merged else ""
        build_cmd = f"--network=host {no_cache} -f {self.container_repo_dir}/{self.dockerfile} . -t {_container_repository_and_tag}"

        if self.container_build_args:
            build_args = [f"--build-arg {b_arg}" for b_arg in self.container_build_args][0]
            build_cmd = f"{build_args} {build_cmd}"

        if self.container_command_args:
            build_cmd = f"{' '.join(self.container_command_args)} {build_cmd}"

        if push:
            repository_creds = f"{self.container_repository_username}:{self.container_repository_password}"
            build_cmd += f" && podman push --creds {repository_creds} {_container_repository_and_tag}"
        podman_build_cmd = f"podman build {build_cmd}"

        rc, out, err = self._run_in_container(command=podman_build_cmd, is_merged=is_merged, tag_name=tag)
        output = {
            "title": "Build container",
            "summary": "",
            "text": self.get_check_run_text(err=err, out=out),
        }
        if rc:
            LOGGER.info(f"{self.log_prefix} Done building {_container_repository_and_tag}")
            if self.pull_request and set_check:
                return self.set_container_build_success(output=output)
            if push:
                push_msg = f"New container for {_container_repository_and_tag} published"
                if self.pull_request:
                    self.pull_request.create_issue_comment(push_msg)
                if self.slack_webhook_url:
                    message = f"""
```
{self.repository_full_name} {push_msg}.
```
"""
                    self.send_slack_message(message=message, webhook_url=self.slack_webhook_url)

                LOGGER.info(f"{self.log_prefix} Done push {_container_repository_and_tag}")
        else:
            if push:
                err_msg = f"Failed to create and push {_container_repository_and_tag}"
                if self.pull_request:
                    self.pull_request.create_issue_comment(err_msg)
                if self.slack_webhook_url:
                    message = f"""
```
{self.repository_full_name} {err_msg}.
```
                    """
                    self.send_slack_message(message=message, webhook_url=self.slack_webhook_url)
            if self.pull_request and set_check:
                return self.set_container_build_failure(output=output)

    def _run_install_python_module(self):
        if not self.pypi:
            return False

        if self.is_check_run_in_progress(check_run=PYTHON_MODULE_INSTALL_STR):
            LOGGER.info(f"{self.log_prefix} Check run is in progress, not running {PYTHON_MODULE_INSTALL_STR}.")
            return False

        LOGGER.info(f"{self.log_prefix} Installing python module")
        f"{PYTHON_MODULE_INSTALL_STR}-{shortuuid.uuid()}"
        self.set_python_module_install_in_progress()
        rc, out, err = self._run_in_container(command="pip install .")
        output = {
            "title": "Python module installation",
            "summary": "",
            "text": self.get_check_run_text(err=err, out=out),
        }
        if rc:
            return self.set_python_module_install_success(output=output)

        return self.set_python_module_install_failure(output=output)

    def send_slack_message(self, message, webhook_url):
        slack_data = {"text": message}
        LOGGER.info(f"{self.log_prefix} Sending message to slack: {message}")
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

    def _process_verified(self):
        if not self.verified_job:
            return

        if self.parent_committer in self.auto_verified_and_merged_users:
            LOGGER.info(
                f"{self.log_prefix} Committer {self.parent_committer} is part of {self.auto_verified_and_merged_users}"
                ", Setting verified label"
            )
            self._add_label(label=VERIFIED_LABEL_STR)
            self.set_verify_check_success()
        else:
            self.reset_verify_label()
            self.set_verify_check_queued()

    def create_comment_reaction(self, issue_comment_id, reaction):
        _comment = self.pull_request.get_issue_comment(issue_comment_id)
        _comment.create_reaction(reaction)

    def process_opened_or_synchronize_pull_request(self):
        self.set_merge_check_queued()
        self.set_run_tox_check_queued()
        self.set_run_pre_commit_check_queued()
        self.set_python_module_install_queued()
        self.set_container_build_queued()
        self._process_verified()
        self.add_size_label()
        self._add_label(label=f"{BRANCH_LABEL_PREFIX}{self.pull_request_branch}")
        LOGGER.info(f"{self.log_prefix} Adding PR owner as assignee")

        try:
            self.pull_request.add_to_assignees()
        except Exception:
            if self.approvers:
                self.pull_request.add_to_assignees(self.approvers[0])

        self.assign_reviewers()
        self.label_pull_request_by_merge_state()

        futures = []
        with ThreadPoolExecutor() as executor:
            futures.append(executor.submit(self._run_tox))
            futures.append(executor.submit(self._run_pre_commit))
            futures.append(executor.submit(self._run_install_python_module))
            futures.append(executor.submit(self._run_build_container))

        for result in as_completed(futures):
            if result.exception():
                LOGGER.error(f"{self.log_prefix} {result.exception()}")
            LOGGER.info(f"{self.log_prefix} {result.result()}")

    def is_check_run_in_progress(self, check_run):
        for run in self.last_commit.get_check_runs():
            if run.name == check_run and run.status == IN_PROGRESS_STR:
                return True
        return False

    def set_check_run_status(
        self,
        check_run: str,
        status: str = "",
        conclusion: str = "",
        output: str = "",
    ):
        kwargs: Dict[str, str] = {"name": check_run, "head_sha": self.last_commit.sha}

        if status:
            kwargs["status"] = status

        if conclusion:
            kwargs["conclusion"] = conclusion

        if output:
            kwargs["output"] = output

        msg: str = f"{self.log_prefix} Set {check_run} check to {status or conclusion}"
        LOGGER.info(msg)

        try:
            self.repository_by_github_app.create_check_run(**kwargs)
            if conclusion == SUCCESS_STR:
                LOGGER.success(msg)

            return

        except Exception as ex:
            LOGGER.error(f"{self.log_prefix} Failed to set {check_run} check to {status or conclusion}, {ex}")
            kwargs["conclusion"] = FAILURE_STR

            LOGGER.error(
                f"{self.log_prefix} Check run {check_run}, status: {FAILURE_STR}, output: {kwargs.get('output')}"
            )
            self.repository_by_github_app.create_check_run(**kwargs)

    def _run_in_container(
        self,
        command: str,
        env: str = "",
        is_merged: bool = False,
        checkout: str = "",
        tag_name: str = "",
    ) -> Tuple[int, str, str]:
        podman_base_cmd: str = (
            "podman run --network=host --privileged -v /tmp/containers:/var/lib/containers/:Z "
            f"--rm {env if env else ''} --entrypoint bash quay.io/myakove/github-webhook-server -c"
        )

        # Clone the repository
        clone_base_cmd: str = (
            f"git clone {self.repository.clone_url.replace('https://', f'https://{self.token}@')} "
            f"{self.container_repo_dir}"
        )
        clone_base_cmd += f" && cd {self.container_repo_dir}"
        clone_base_cmd += f" && git config user.name '{self.repository.owner.login}'"
        clone_base_cmd += f" && git config user.email '{self.repository.owner.email}'"
        clone_base_cmd += " && git config --local --add remote.origin.fetch +refs/pull/*/head:refs/remotes/origin/pr/*"
        clone_base_cmd += " && git remote update >/dev/null 2>&1"

        # Checkout to requested branch/tag
        if checkout:
            clone_base_cmd += f" && git checkout {checkout}"

        # Checkout the branch if pull request is merged or for release
        else:
            if is_merged:
                clone_base_cmd += f" && git checkout {self.pull_request_branch}"

            elif tag_name:
                clone_base_cmd += f" && git checkout {tag_name}"

            # Checkout the pull request
            else:
                if not self.pull_request:
                    LOGGER.error(f"{self.log_prefix} [func:_run_in_container] No pull request found")
                    return False, "", ""
                clone_base_cmd += f" && git checkout origin/pr/{self.pull_request.number}"

        # final podman command
        podman_base_cmd += f" '{clone_base_cmd} && {command}'"
        return run_command(command=podman_base_cmd, log_prefix=self.log_prefix)

    @staticmethod
    def get_check_run_text(err, out):
        total_len = len(err) + len(out)
        if total_len > 65534:  # GitHub limit is 65535 characters
            return f"```\n{err}\n\n{out}\n```"[:65534]
        else:
            return f"```\n{err}\n\n{out}\n```"

    @ignore_exceptions(logger=LOGGER)
    def get_jira_conn(self):
        self.jira_conn = JiraApi(
            server=self.jira_server,
            project=self.jira_project,
            token=self.jira_token,
        )

    def log_repository_features(self):
        repository_features = f"""
                        auto-verified-and-merged-users: {self.auto_verified_and_merged_users}
                        can-be-merged-required-labels: {self.can_be_merged_required_labels}
                        pypi: {self.pypi}
                        verified-job: {self.verified_job}
                        tox-enabled: {self.tox_enabled}
                        tox-python-version: {self.tox_python_version}
                        docker: {self.dockerhub}
                        pre-commit: {self.pre_commit}
                        slack-webhook-url: {self.slack_webhook_url}
                        container: {self.build_and_push_container}
                        jira-tracking: {self.jira_tracking}
                        jira-server: {self.jira_server}
                        jira-project: {self.jira_project}
                        jira-token: {self.jira_token}
                        jira-enabled-repository: {self.jira_enabled_repository}
                        jira-user-mapping: {self.jira_user_mapping}
"""
        LOGGER.info(f"{self.log_prefix} Repository features: {repository_features}")

    def get_story_key_with_jira_connection(self):
        _story_label = [_label for _label in self.pull_request.labels if _label.name.startswith(JIRA_STR)]
        if not _story_label:
            return None

        if _story_key := _story_label[0].name.split(":")[-1]:
            self.get_jira_conn()
            if not self.jira_conn:
                LOGGER.error(f"{self.log_prefix} Jira connection not found")
                return None
        return _story_key

    @ignore_exceptions(logger=LOGGER, return_on_error=[])
    def get_branch_required_status_checks(self):
        if self.repository.private:
            LOGGER.info(
                f"{self.log_prefix} Repository is private, skipping getting branch protection required status checks"
            )
            return []

        pull_request_branch = self.repository.get_branch(self.pull_request_branch)
        branch_protection = pull_request_branch.get_protection()
        return branch_protection.required_status_checks.contexts

    def get_all_required_status_checks(self):
        all_required_status_checks = []
        branch_required_status_checks = self.get_branch_required_status_checks()
        if self.tox_enabled:
            all_required_status_checks.append(TOX_STR)

        if self.verified_job:
            all_required_status_checks.append(VERIFIED_LABEL_STR)

        if self.build_and_push_container:
            all_required_status_checks.append(BUILD_CONTAINER_STR)

        if self.pypi:
            all_required_status_checks.append(PYTHON_MODULE_INSTALL_STR)

        return branch_required_status_checks + all_required_status_checks
