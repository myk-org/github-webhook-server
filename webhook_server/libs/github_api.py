from __future__ import annotations

import contextlib
import json
import logging
import os
import random
import re
import shutil
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Generator
from uuid import uuid4

import requests
import shortuuid
import yaml
from fastapi.exceptions import HTTPException
from github import GithubException
from github.Branch import Branch
from github.CheckRun import CheckRun
from github.Commit import Commit
from github.GithubException import UnknownObjectException
from github.PullRequest import PullRequest
from starlette.datastructures import Headers
from stringcolor import cs
from timeout_sampler import TimeoutExpiredError, TimeoutSampler

from webhook_server.libs.config import Config
from webhook_server.libs.exceptions import NoPullRequestError, RepositoryNotFoundError
from webhook_server.utils.constants import (
    ADD_STR,
    APPROVE_STR,
    APPROVED_BY_LABEL_PREFIX,
    BRANCH_LABEL_PREFIX,
    BUILD_AND_PUSH_CONTAINER_STR,
    BUILD_CONTAINER_STR,
    CAN_BE_MERGED_STR,
    CHANGED_REQUESTED_BY_LABEL_PREFIX,
    CHERRY_PICK_LABEL_PREFIX,
    CHERRY_PICKED_LABEL_PREFIX,
    COMMAND_ASSIGN_REVIEWER_STR,
    COMMAND_ASSIGN_REVIEWERS_STR,
    COMMAND_CHECK_CAN_MERGE_STR,
    COMMAND_CHERRY_PICK_STR,
    COMMAND_RETEST_STR,
    COMMENTED_BY_LABEL_PREFIX,
    CONVENTIONAL_TITLE_STR,
    DELETE_STR,
    DYNAMIC_LABELS_DICT,
    FAILURE_STR,
    HAS_CONFLICTS_LABEL_STR,
    HOLD_LABEL_STR,
    IN_PROGRESS_STR,
    LABELS_SEPARATOR,
    LGTM_BY_LABEL_PREFIX,
    LGTM_STR,
    NEEDS_REBASE_LABEL_STR,
    OTHER_MAIN_BRANCH,
    PRE_COMMIT_STR,
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
)
from webhook_server.utils.github_repository_settings import (
    get_repository_github_app_api,
)
from webhook_server.utils.helpers import (
    extract_key_from_dict,
    get_api_with_highest_rate_limit,
    get_apis_and_tokes_from_config,
    get_github_repo_api,
    run_command,
)


class ProcessGithubWehook:
    def __init__(self, hook_data: dict[Any, Any], headers: Headers, logger: logging.Logger) -> None:
        self.logger = logger
        self.logger.name = "ProcessGithubWehook"
        self.hook_data = hook_data
        self.headers = headers
        self.repository_name: str = hook_data["repository"]["name"]
        self.repository_full_name: str = hook_data["repository"]["full_name"]
        self.parent_committer: str = ""
        self.issue_title: str = ""
        self.all_required_status_checks: list[str] = []
        self.x_github_delivery: str = self.headers.get("X-GitHub-Delivery", "")
        self.github_event: str = self.headers["X-GitHub-Event"]
        self.owners_content: dict[str, Any] = {}

        self.config = Config(repository=self.repository_name)

        if not self.config.repository:
            raise RepositoryNotFoundError(f"Repository {self.repository_name} not found in config file")

        # Get config without .github-webhook-server.yaml data
        self._repo_data_from_config(repository_config={})
        self.github_api, self.token, self.api_user = get_api_with_highest_rate_limit(
            config=self.config, repository_name=self.repository_name
        )

        if self.github_api and self.token:
            self.repository = get_github_repo_api(github_api=self.github_api, repository=self.repository_full_name)
            # Once we have a repository, we can get the config from .github-webhook-server.yaml
            local_repository_config = self.config.repository_local_data(
                github_api=self.github_api, repository_full_name=self.repository_full_name
            )
            # Call _repo_data_from_config() again to update self args from .github-webhook-server.yaml
            self._repo_data_from_config(repository_config=local_repository_config)

        else:
            self.logger.error(f"Failed to get GitHub API and token for repository {self.repository_name}.")
            return

        self.log_prefix = self.prepare_log_prefix()

        self.github_app_api = get_repository_github_app_api(
            config_=self.config, repository_name=self.repository_full_name
        )

        if not self.github_app_api:
            self.logger.error(
                (
                    f"{self.log_prefix} not found by manage-repositories-app, "
                    "make sure the app installed (https://github.com/apps/manage-repositories-app)"
                ),
            )
            return

        self.repository_by_github_app = get_github_repo_api(
            github_api=self.github_app_api, repository=self.repository_full_name
        )

        if not (self.repository or self.repository_by_github_app):
            self.logger.error(f"{self.log_prefix} Failed to get repository.")
            return

        self.clone_repo_dir: str = os.path.join("/tmp", f"{self.repository.name}")
        self.add_api_users_to_auto_verified_and_merged_users()

        self.current_pull_request_supported_retest = self._current_pull_request_supported_retest
        self.issue_url_for_welcome_msg: str = (
            "Report bugs in [Issues](https://github.com/myakove/github-webhook-server/issues)"
        )

    async def process(self) -> Any:
        if self.github_event == "ping":
            return {"status": requests.codes.ok, "message": "pong"}

        event_log: str = f"Event type: {self.github_event}. event ID: {self.x_github_delivery}"

        try:
            self.pull_request = self._get_pull_request()
            self.log_prefix = self.prepare_log_prefix(pull_request=self.pull_request)
            self.logger.debug(f"{self.log_prefix} {event_log}")
            self.last_commit = self._get_last_commit()
            self.parent_committer = self.pull_request.user.login
            self.last_committer = getattr(self.last_commit.committer, "login", self.parent_committer)
            self.changed_files = self.list_changed_files()
            self.pull_request_branch = self.pull_request.base.ref
            self.all_approvers_and_reviewers = self.get_all_approvers_and_reviewers()
            self.all_approvers = self.get_all_approvers()
            self.all_reviewers = self.get_all_reviewers()

            if self.github_event == "issue_comment":
                return self.process_comment_webhook_data()

            if self.github_event == "pull_request":
                return self.process_pull_request_webhook_data()

            if self.github_event == "pull_request_review":
                return self.process_pull_request_review_webhook_data()

            if self.github_event == "check_run":
                return self.process_pull_request_check_run_webhook_data()

        except NoPullRequestError:
            self.logger.debug(f"{self.log_prefix} {event_log}. [No pull request found in hook data]")

            if self.github_event == "push":
                return self.process_push_webhook_data()

            raise

        except Exception as e:
            self.logger.error(f"{self.log_prefix} {event_log}. Exception: {e}")
            raise HTTPException(status_code=404, detail=str(e))

    @property
    def _prepare_retest_welcome_comment(self) -> str:
        retest_msg: str = ""
        if self.tox:
            retest_msg += f" * `/retest {TOX_STR}`: Retest tox\n"

        if self.build_and_push_container:
            retest_msg += f" * `/retest {BUILD_CONTAINER_STR}`: Retest build-container\n"

        if self.pypi:
            retest_msg += f" * `/retest {PYTHON_MODULE_INSTALL_STR}`: Retest python-module-install\n"

        if self.pre_commit:
            retest_msg += f" * `/retest {PRE_COMMIT_STR}`: Retest pre-commit\n"

        if self.conventional_title:
            retest_msg += f" * `/retest {CONVENTIONAL_TITLE_STR}`: Retest conventional-title\n"

        if retest_msg:
            retest_msg += " * `/retest all`: Retest all\n"

        return " * This repository does not support retest actions" if not retest_msg else retest_msg

    def add_api_users_to_auto_verified_and_merged_users(self) -> None:
        apis_and_tokens = get_apis_and_tokes_from_config(config=self.config)
        self.auto_verified_and_merged_users.extend([_api[0].get_user().login for _api in apis_and_tokens])

    def _get_reposiroty_color_for_log_prefix(self) -> str:
        def _get_random_color(_colors: list[str], _json: dict[str, str]) -> str:
            color = random.choice(_colors)
            _json[self.repository_name] = color

            if _selected := cs(self.repository_name, color).render():
                return _selected

            return self.repository_name

        _all_colors: list[str] = []
        color_json: dict[str, str]
        _colors_to_exclude = ("blue", "white", "black", "grey")
        color_file: str = os.path.join(self.config.data_dir, "log-colors.json")

        for _color_name in cs.colors.values():
            _cname = _color_name["name"]
            if _cname.lower() in _colors_to_exclude:
                continue

            _all_colors.append(_cname)

        try:
            with open(color_file) as fd:
                color_json = json.load(fd)

        except Exception:
            color_json = {}

        if color := color_json.get(self.repository_name, ""):
            _cs_object = cs(self.repository_name, color)
            if cs.find_color(_cs_object):
                _str_color = _cs_object.render()

            else:
                _str_color = _get_random_color(_colors=_all_colors, _json=color_json)

        else:
            _str_color = _get_random_color(_colors=_all_colors, _json=color_json)

        with open(color_file, "w") as fd:
            json.dump(color_json, fd)

        if _str_color:
            _str_color = _str_color.replace("\x1b", "\033")
            return _str_color

        return self.repository_name

    def prepare_log_prefix(self, pull_request: PullRequest | None = None) -> str:
        _repository_color = self._get_reposiroty_color_for_log_prefix()

        return (
            f"{_repository_color}[{self.github_event}][{self.x_github_delivery}][{self.api_user}][PR {pull_request.number}]:"
            if pull_request
            else f"{_repository_color}[{self.github_event}][{self.x_github_delivery}][{self.api_user}]:"
        )

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

        return self.check_if_can_be_merged()

    def _repo_data_from_config(self, repository_config: dict[str, Any]) -> None:
        self.logger.debug(f"Read config for repository {self.repository_name}")

        self.github_app_id: str = self.config.get_value(value="github-app-id", extra_dict=repository_config)
        self.pypi: dict[str, str] = self.config.get_value(value="pypi", extra_dict=repository_config)
        self.verified_job: bool = self.config.get_value(
            value="verified-job", return_on_none=True, extra_dict=repository_config
        )
        self.tox: dict[str, str] = self.config.get_value(value="tox", extra_dict=repository_config)
        self.tox_python_version: str = self.config.get_value(value="tox-python-version", extra_dict=repository_config)
        self.slack_webhook_url: str = self.config.get_value(value="slack_webhook_url", extra_dict=repository_config)

        self.build_and_push_container: dict[str, Any] = self.config.get_value(
            value="container", return_on_none={}, extra_dict=repository_config
        )
        if self.build_and_push_container:
            self.container_repository_username: str = self.build_and_push_container["username"]
            self.container_repository_password: str = self.build_and_push_container["password"]
            self.container_repository: str = self.build_and_push_container["repository"]
            self.dockerfile: str = self.build_and_push_container.get("dockerfile", "Dockerfile")
            self.container_tag: str = self.build_and_push_container.get("tag", "latest")
            self.container_build_args: str = self.build_and_push_container.get("build-args", "")
            self.container_command_args: str = self.build_and_push_container.get("args", "")
            self.container_release: bool = self.build_and_push_container.get("release", False)

        self.pre_commit: bool = self.config.get_value(
            value="pre-commit", return_on_none=False, extra_dict=repository_config
        )

        self.auto_verified_and_merged_users: list[str] = self.config.get_value(
            value="auto-verified-and-merged-users", return_on_none=[], extra_dict=repository_config
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

    def _get_pull_request(self, number: int | None = None) -> PullRequest:
        if number:
            return self.repository.get_pull(number)

        for _number in extract_key_from_dict(key="number", _dict=self.hook_data):
            try:
                return self.repository.get_pull(_number)
            except GithubException:
                continue

        commit: dict[str, Any] = self.hook_data.get("commit", {})
        if commit:
            commit_obj = self.repository.get_commit(commit["sha"])
            with contextlib.suppress(Exception):
                return commit_obj.get_pulls()[0]

        if self.github_event == "check_run":
            for _pull_request in self.repository.get_pulls(state="open"):
                if _pull_request.head.sha == self.hook_data["check_run"]["head_sha"]:
                    self.logger.debug(
                        f"{self.log_prefix} Found pull request {_pull_request.title} [{_pull_request.number}] for check run {self.hook_data['check_run']['name']}"
                    )
                    return _pull_request

        raise NoPullRequestError(f"{self.log_prefix} No issue or pull_request found in hook data")

    def _get_last_commit(self) -> Commit:
        return list(self.pull_request.get_commits())[-1]

    def label_exists_in_pull_request(self, label: str) -> bool:
        return any(lb for lb in self.pull_request_labels_names() if lb == label)

    def pull_request_labels_names(self) -> list[str]:
        return [lb.name for lb in self.pull_request.labels] if self.pull_request else []

    def skip_if_pull_request_already_merged(self) -> bool:
        if self.pull_request and self.pull_request.is_merged():
            self.logger.info(f"{self.log_prefix}: PR is merged, not processing")
            return True

        return False

    def _remove_label(self, label: str) -> bool:
        try:
            if self.label_exists_in_pull_request(label=label):
                self.logger.info(f"{self.log_prefix} Removing label {label}")
                self.pull_request.remove_from_labels(label)
                return self.wait_for_label(label=label, exists=False)
        except Exception as exp:
            self.logger.debug(f"{self.log_prefix} Failed to remove {label} label. Exception: {exp}")
            return False

        self.logger.debug(f"{self.log_prefix} Label {label} not found and cannot be removed")
        return False

    def _add_label(self, label: str) -> None:
        label = label.strip()
        if len(label) > 49:
            self.logger.debug(f"{label} is to long, not adding.")
            return

        if self.label_exists_in_pull_request(label=label):
            self.logger.debug(f"{self.log_prefix} Label {label} already assign")
            return

        if label in STATIC_LABELS_DICT:
            self.logger.info(f"{self.log_prefix} Adding pull request label {label}")
            self.pull_request.add_to_labels(label)
            return

        _color = [DYNAMIC_LABELS_DICT[_label] for _label in DYNAMIC_LABELS_DICT if _label in label]
        self.logger.debug(f"{self.log_prefix} Label {label} was {'found' if _color else 'not found'} in labels dict")
        color = _color[0] if _color else "D4C5F9"
        _with_color_msg = f"repository label {label} with color {color}"

        try:
            _repo_label = self.repository.get_label(label)
            _repo_label.edit(name=_repo_label.name, color=color)
            self.logger.debug(f"{self.log_prefix} Edit {_with_color_msg}")

        except UnknownObjectException:
            self.logger.debug(f"{self.log_prefix} Add {_with_color_msg}")
            self.repository.create_label(name=label, color=color)

        self.logger.info(f"{self.log_prefix} Adding pull request label {label}")
        self.pull_request.add_to_labels(label)
        self.wait_for_label(label=label, exists=True)

    def wait_for_label(self, label: str, exists: bool) -> bool:
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
            self.logger.debug(f"{self.log_prefix} Label {label} {'not found' if exists else 'found'}")

        return False

    def _generate_issue_title(self) -> str:
        return f"{self.pull_request.title} - {self.pull_request.number}"

    def _generate_issue_body(self) -> str:
        return f"[Auto generated]\nNumber: [#{self.pull_request.number}]"

    def is_branch_exists(self, branch: str) -> Branch:
        return self.repository.get_branch(branch)

    def upload_to_pypi(self, tag_name: str) -> None:
        def _issue_on_error(_error: str) -> None:
            self.repository.create_issue(
                title=_error,
                assignee=self.root_approvers[0] if self.root_approvers else "",
                body=f"""
Publish to PYPI failed: `{_error}`
""",
            )

        clone_repo_dir = f"{self.clone_repo_dir}-{uuid4()}"
        uv_cmd_dir = f"--directory {clone_repo_dir}"
        self.logger.info(f"{self.log_prefix} Start uploading to pypi")
        _dist_dir: str = f"{clone_repo_dir}/pypi-dist"

        with self._prepare_cloned_repo_dir(checkout=tag_name, clone_repo_dir=clone_repo_dir) as _res:
            if not _res[0]:
                _error = self.get_check_run_text(out=_res[1], err=_res[2])
                return _issue_on_error(_error=_error)

            rc, out, err = run_command(
                command=f"uv {uv_cmd_dir} build --sdist --out-dir {_dist_dir}", log_prefix=self.log_prefix
            )
            if not rc:
                _error = self.get_check_run_text(out=out, err=err)
                return _issue_on_error(_error=_error)

            rc, tar_gz_file, err = run_command(command=f"ls {_dist_dir}", log_prefix=self.log_prefix)
            if not rc:
                _error = self.get_check_run_text(out=out, err=err)
                return _issue_on_error(_error=_error)

            tar_gz_file = tar_gz_file.strip()

            commands: list[str] = [
                f"uvx {uv_cmd_dir} twine check {_dist_dir}/{tar_gz_file}",
                f"uvx {uv_cmd_dir} twine upload --username __token__ --password {self.pypi['token']} {_dist_dir}/{tar_gz_file} --skip-existing",
            ]
            for cmd in commands:
                rc, out, err = run_command(command=cmd, log_prefix=self.log_prefix)
                if not rc:
                    _error = self.get_check_run_text(out=out, err=err)
                    return _issue_on_error(_error=_error)

            self.logger.info(f"{self.log_prefix} Publish to pypi finished")
            if self.slack_webhook_url:
                message: str = f"""
```
{self.repository_name} Version {tag_name} published to PYPI.
```
"""
                self.send_slack_message(message=message, webhook_url=self.slack_webhook_url)

    @property
    def root_reviewers(self) -> list[str]:
        _reviewers = self.all_approvers_and_reviewers.get(".", {}).get("reviewers", [])
        self.logger.debug(f"{self.log_prefix} ROOT Reviewers: {_reviewers}")
        return _reviewers

    @property
    def root_approvers(self) -> list[str]:
        _approvers = self.all_approvers_and_reviewers.get(".", {}).get("approvers", [])
        self.logger.debug(f"{self.log_prefix} ROOT Approvers: {_approvers}")
        return _approvers

    def list_changed_files(self) -> list[str]:
        return [_file.filename for _file in self.pull_request.get_files()]

    def assign_reviewers(self) -> None:
        self.logger.info(f"{self.log_prefix} Assign reviewers")

        _to_add: list[str] = list(set(self.all_reviewers))
        self.logger.debug(f"{self.log_prefix} Reviewers to add: {', '.join(_to_add)}")

        for reviewer in _to_add:
            if reviewer != self.pull_request.user.login:
                self.logger.debug(f"{self.log_prefix} Adding reviewer {reviewer}")
                try:
                    self.pull_request.create_review_request([reviewer])
                except GithubException as ex:
                    self.logger.debug(f"{self.log_prefix} Failed to add reviewer {reviewer}. {ex}")
                    self.pull_request.create_issue_comment(f"{reviewer} can not be added as reviewer. {ex}")

    def get_size(self) -> str:
        """Calculates size label based on additions and deletions."""

        size = self.pull_request.additions + self.pull_request.deletions

        # Define label thresholds in a more readable way
        threshold_sizes = [20, 50, 100, 300, 500]
        prefixes = ["XS", "S", "M", "L", "XL"]

        for i, size_threshold in enumerate(threshold_sizes):
            if size < size_threshold:
                _label = prefixes[i]
                return f"{SIZE_LABEL_PREFIX}{_label}"

        return f"{SIZE_LABEL_PREFIX}XXL"

    def add_size_label(self) -> None:
        """Add a size label to the pull request based on its additions and deletions."""
        size_label = self.get_size()
        if not size_label:
            self.logger.debug(f"{self.log_prefix} Size label not found")
            return

        if size_label in self.pull_request_labels_names():
            return

        exists_size_label = [label for label in self.pull_request_labels_names() if label.startswith(SIZE_LABEL_PREFIX)]

        if exists_size_label:
            self._remove_label(label=exists_size_label[0])

        self._add_label(label=size_label)

    def label_by_user_comment(
        self,
        user_requested_label: str,
        remove: bool,
        reviewed_user: str,
    ) -> None:
        self.logger.debug(
            f"{self.log_prefix} {DELETE_STR if remove else ADD_STR} "
            f"label requested by user {reviewed_user}: {user_requested_label}"
        )

        if user_requested_label in (LGTM_STR, APPROVE_STR):
            self.manage_reviewed_by_label(
                review_state=user_requested_label,
                action=DELETE_STR if remove else ADD_STR,
                reviewed_user=reviewed_user,
            )

        else:
            label_func = self._remove_label if remove else self._add_label
            label_func(label=user_requested_label)

    def set_verify_check_queued(self) -> None:
        return self.set_check_run_status(check_run=VERIFIED_LABEL_STR, status=QUEUED_STR)

    def set_verify_check_success(self) -> None:
        return self.set_check_run_status(check_run=VERIFIED_LABEL_STR, conclusion=SUCCESS_STR)

    def set_run_tox_check_queued(self) -> None:
        if not self.tox:
            return

        return self.set_check_run_status(check_run=TOX_STR, status=QUEUED_STR)

    def set_run_tox_check_in_progress(self) -> None:
        return self.set_check_run_status(check_run=TOX_STR, status=IN_PROGRESS_STR)

    def set_run_tox_check_failure(self, output: dict[str, Any]) -> None:
        return self.set_check_run_status(check_run=TOX_STR, conclusion=FAILURE_STR, output=output)

    def set_run_tox_check_success(self, output: dict[str, Any]) -> None:
        return self.set_check_run_status(check_run=TOX_STR, conclusion=SUCCESS_STR, output=output)

    def set_run_pre_commit_check_queued(self) -> None:
        if not self.pre_commit:
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
        if not self.build_and_push_container:
            return

        return self.set_check_run_status(check_run=BUILD_CONTAINER_STR, status=QUEUED_STR)

    def set_container_build_in_progress(self) -> None:
        return self.set_check_run_status(check_run=BUILD_CONTAINER_STR, status=IN_PROGRESS_STR)

    def set_container_build_success(self, output: dict[str, Any]) -> None:
        return self.set_check_run_status(check_run=BUILD_CONTAINER_STR, conclusion=SUCCESS_STR, output=output)

    def set_container_build_failure(self, output: dict[str, Any]) -> None:
        return self.set_check_run_status(check_run=BUILD_CONTAINER_STR, conclusion=FAILURE_STR, output=output)

    def set_python_module_install_queued(self) -> None:
        if not self.pypi:
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

    def create_issue_for_new_pull_request(self) -> None:
        if self.parent_committer in self.auto_verified_and_merged_users:
            self.logger.info(
                f"{self.log_prefix} Committer {self.parent_committer} is part of "
                f"{self.auto_verified_and_merged_users}, will not create issue."
            )
            return

        self.logger.info(f"{self.log_prefix} Creating issue for new PR: {self.pull_request.title}")
        self.repository.create_issue(
            title=self._generate_issue_title(),
            body=self._generate_issue_body(),
            assignee=self.pull_request.user.login,
        )

    def close_issue_for_merged_or_closed_pr(self, hook_action: str) -> None:
        for issue in self.repository.get_issues():
            if issue.body == self._generate_issue_body():
                self.logger.info(f"{self.log_prefix} Closing issue {issue.title} for PR: {self.pull_request.title}")
                issue.create_comment(
                    f"{self.log_prefix} Closing issue for PR: {self.pull_request.title}.\nPR was {hook_action}."
                )
                issue.edit(state="closed")
                break

    def delete_remote_tag_for_merged_or_closed_pr(self) -> None:
        if not self.build_and_push_container:
            self.logger.info(f"{self.log_prefix} repository do not have container configured")
            return

        repository_full_tag = self._container_repository_and_tag()
        if not repository_full_tag:
            return

        pr_tag = repository_full_tag.split(":")[-1]
        registry_info = self.container_repository.split("/")
        registry_url = "" if len(registry_info) < 3 else registry_info[0]

        reg_login_cmd = f"regctl registry login {registry_url} -u {self.container_repository_username} -p {self.container_repository_password}"
        rc, out, err = self.run_podman_command(command=reg_login_cmd)

        if rc:
            try:
                tag_ls_cmd = f"regctl tag ls {self.container_repository} --include {pr_tag}"
                rc, out, err = self.run_podman_command(command=tag_ls_cmd)

                if rc and out:
                    tag_del_cmd = f"regctl tag delete {repository_full_tag}"

                    if self.run_podman_command(command=tag_del_cmd)[0]:
                        self.pull_request.create_issue_comment(f"Successfully removed PR tag: {repository_full_tag}.")
                    else:
                        self.logger.error(
                            f"{self.log_prefix} Failed to delete tag: {repository_full_tag}. OUT:{out}. ERR:{err}"
                        )
                else:
                    self.logger.warning(
                        f"{self.log_prefix} {pr_tag} tag not found in registry {self.container_repository}. "
                        f"OUT:{out}. ERR:{err}"
                    )
            finally:
                self.run_podman_command(command="regctl registry logout")

        else:
            self.pull_request.create_issue_comment(
                f"Failed to delete tag: {repository_full_tag}. Please delete it manually."
            )
            self.logger.error(f"{self.log_prefix} Failed to delete tag: {repository_full_tag}. OUT:{out}. ERR:{err}")

    def process_comment_webhook_data(self) -> None:
        if comment_action := self.hook_data["action"] in ("edited", "deleted"):
            self.logger.debug(f"{self.log_prefix} Not processing comment. action is {comment_action}")
            return

        self.logger.info(f"{self.log_prefix} Processing issue {self.hook_data['issue']['number']}")

        body: str = self.hook_data["comment"]["body"]

        if self.issue_url_for_welcome_msg in body:
            self.logger.debug(
                f"{self.log_prefix} Welcome message found in issue {self.pull_request.title}. Not processing"
            )
            return

        _user_commands: list[str] = [_cmd.strip("/") for _cmd in body.strip().splitlines() if _cmd.startswith("/")]

        user_login: str = self.hook_data["sender"]["login"]
        for user_command in _user_commands:
            self.user_commands(
                command=user_command,
                reviewed_user=user_login,
                issue_comment_id=self.hook_data["comment"]["id"],
            )

    def process_pull_request_webhook_data(self) -> None:
        hook_action: str = self.hook_data["action"]
        self.logger.info(f"{self.log_prefix} hook_action is: {hook_action}")

        pull_request_data: dict[str, Any] = self.hook_data["pull_request"]
        self.parent_committer = pull_request_data["user"]["login"]
        self.pull_request_branch = pull_request_data["base"]["ref"]

        if hook_action == "edited":
            self.set_wip_label_based_on_title()

        if hook_action in ("opened", "reopened"):
            pull_request_opened_futures: list[Future] = []
            with ThreadPoolExecutor() as executor:
                if hook_action == "opened":
                    welcome_msg = self._prepare_welcome_comment()
                    pull_request_opened_futures.append(
                        executor.submit(self.pull_request.create_issue_comment, **{"body": welcome_msg})
                    )
                pull_request_opened_futures.append(executor.submit(self.create_issue_for_new_pull_request))
                pull_request_opened_futures.append(executor.submit(self.set_wip_label_based_on_title))
                pull_request_opened_futures.append(executor.submit(self.process_opened_or_synchronize_pull_request))

            # Set automerge only after all initialization of a new PR is done.
            self.set_pull_request_automerge()

            for result in as_completed(pull_request_opened_futures):
                if _exp := result.exception():
                    self.logger.error(f"{self.log_prefix} {_exp}")

        if hook_action == "synchronize":
            pull_request_synchronize_futures: list[Future] = []
            with ThreadPoolExecutor() as executor:
                pull_request_synchronize_futures.append(executor.submit(self.remove_labels_when_pull_request_sync))
                pull_request_synchronize_futures.append(
                    executor.submit(self.process_opened_or_synchronize_pull_request)
                )

            for result in as_completed(pull_request_synchronize_futures):
                if _exp := result.exception():
                    self.logger.error(f"{self.log_prefix} {_exp}")

        if hook_action == "closed":
            self.close_issue_for_merged_or_closed_pr(hook_action=hook_action)
            self.delete_remote_tag_for_merged_or_closed_pr()
            if is_merged := pull_request_data.get("merged", False):
                self.logger.info(f"{self.log_prefix} PR is merged")

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
                self.label_all_opened_pull_requests_merge_state_after_merged()
                self.pull_request = original_pull_request

        if hook_action in ("labeled", "unlabeled"):
            _check_for_merge: bool = False
            _user: str | None = None
            action_labeled = hook_action == "labeled"
            labeled = self.hook_data["label"]["name"]
            labeled_lower = labeled.lower()

            if labeled_lower == CAN_BE_MERGED_STR:
                return

            self.logger.info(f"{self.log_prefix} PR {self.pull_request.number} {hook_action} with {labeled}")

            _split_label = labeled.split(LABELS_SEPARATOR, 1)

            if len(_split_label) == 2:
                _lable_prefix, _user = _split_label

                if f"{_lable_prefix}{LABELS_SEPARATOR}" in (
                    APPROVED_BY_LABEL_PREFIX,
                    LGTM_BY_LABEL_PREFIX,
                    CHANGED_REQUESTED_BY_LABEL_PREFIX,
                ):
                    if _user in self.all_reviewers + self.all_approvers:
                        _check_for_merge = True

            if self.verified_job and labeled_lower == VERIFIED_LABEL_STR:
                _check_for_merge = True

                if action_labeled:
                    self.set_verify_check_success()
                else:
                    self.set_verify_check_queued()

            if _check_for_merge:
                self.check_if_can_be_merged()

    def process_push_webhook_data(self) -> None:
        tag = re.search(r"refs/tags/?(.*)", self.hook_data["ref"])
        if tag:
            tag_name = tag.group(1)
            self.logger.info(f"{self.log_prefix} Processing push for tag: {tag.group(1)}")
            if self.pypi:
                self.logger.info(f"{self.log_prefix} Processing upload to pypi for tag: {tag_name}")
                self.upload_to_pypi(tag_name=tag_name)

            if self.build_and_push_container and self.container_release:
                self.logger.info(f"{self.log_prefix} Processing build and push container for tag: {tag_name}")
                self._run_build_container(push=True, set_check=False, tag=tag_name)

    def process_pull_request_review_webhook_data(self) -> None:
        if self.hook_data["action"] == "submitted":
            """
            Available actions:
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

    def manage_reviewed_by_label(self, review_state: str, action: str, reviewed_user: str) -> None:
        self.logger.info(
            f"{self.log_prefix} "
            f"Processing label for review from {reviewed_user}. "
            f"review_state: {review_state}, action: {action}"
        )
        label_prefix: str = ""
        label_to_remove: str = ""

        if review_state == APPROVE_STR and reviewed_user in self.all_approvers:
            label_prefix = APPROVED_BY_LABEL_PREFIX
            label_to_remove = f"{CHANGED_REQUESTED_BY_LABEL_PREFIX}{reviewed_user}"

        elif review_state in ("approved", LGTM_STR):
            if base_dict := self.hook_data.get("issue", self.hook_data.get("pull_request")):
                pr_owner = base_dict["user"]["login"]
                if pr_owner == reviewed_user:
                    self.logger.info(f"{self.log_prefix} PR owner {pr_owner} set /lgtm, not adding label.")
                    return

            _remove_label = f"{CHANGED_REQUESTED_BY_LABEL_PREFIX}{reviewed_user}"
            label_prefix = LGTM_BY_LABEL_PREFIX
            label_to_remove = _remove_label

        elif review_state == "changes_requested":
            label_prefix = CHANGED_REQUESTED_BY_LABEL_PREFIX
            _remove_label = LGTM_BY_LABEL_PREFIX
            label_to_remove = _remove_label

        elif review_state == "commented":
            label_prefix = COMMENTED_BY_LABEL_PREFIX

        if label_prefix:
            reviewer_label = f"{label_prefix}{reviewed_user}"

            if action == ADD_STR:
                self._add_label(label=reviewer_label)
                self._remove_label(label=label_to_remove)

            if action == DELETE_STR:
                self._remove_label(label=reviewer_label)
        else:
            self.logger.warning(
                f"{self.log_prefix} PR {self.pull_request.number} got unsupported review state: {review_state}"
            )

    def _run_tox(self) -> None:
        if not self.tox:
            return

        if self.is_check_run_in_progress(check_run=TOX_STR):
            self.logger.debug(f"{self.log_prefix} Check run is in progress, re-running {TOX_STR}.")

        clone_repo_dir = f"{self.clone_repo_dir}-{uuid4()}"
        python_ver = f"--python={self.tox_python_version}" if self.tox_python_version else ""
        cmd = f"uvx {python_ver} {TOX_STR} --workdir {clone_repo_dir} --root {clone_repo_dir} -c {clone_repo_dir}"
        _tox_tests = self.tox.get(self.pull_request_branch, "")
        if _tox_tests and _tox_tests != "all":
            tests = _tox_tests.replace(" ", "")
            cmd += f" -e {tests}"

        self.set_run_tox_check_in_progress()
        with self._prepare_cloned_repo_dir(clone_repo_dir=clone_repo_dir) as _res:
            output: dict[str, Any] = {
                "title": "Tox",
                "summary": "",
                "text": None,
            }
            if not _res[0]:
                output["text"] = self.get_check_run_text(out=_res[1], err=_res[2])
                return self.set_run_tox_check_failure(output=output)

            rc, out, err = run_command(command=cmd, log_prefix=self.log_prefix)

            output["text"] = self.get_check_run_text(err=err, out=out)

            if rc:
                return self.set_run_tox_check_success(output=output)
            else:
                return self.set_run_tox_check_failure(output=output)

    def _run_pre_commit(self) -> None:
        if not self.pre_commit:
            return

        if self.is_check_run_in_progress(check_run=PRE_COMMIT_STR):
            self.logger.debug(f"{self.log_prefix} Check run is in progress, re-running {PRE_COMMIT_STR}.")

        clone_repo_dir = f"{self.clone_repo_dir}-{uuid4()}"
        cmd = f" uvx --directory {clone_repo_dir} {PRE_COMMIT_STR} run --all-files"
        self.set_run_pre_commit_check_in_progress()
        with self._prepare_cloned_repo_dir(clone_repo_dir=clone_repo_dir) as _res:
            output: dict[str, Any] = {
                "title": "Pre-Commit",
                "summary": "",
                "text": None,
            }
            if not _res[0]:
                output["text"] = self.get_check_run_text(out=_res[1], err=_res[2])
                return self.set_run_pre_commit_check_failure(output=output)

            rc, out, err = run_command(command=cmd, log_prefix=self.log_prefix)

            output["text"] = self.get_check_run_text(err=err, out=out)

            if rc:
                return self.set_run_pre_commit_check_success(output=output)
            else:
                return self.set_run_pre_commit_check_failure(output=output)

    def user_commands(self, command: str, reviewed_user: str, issue_comment_id: int) -> None:
        available_commands: list[str] = [
            COMMAND_RETEST_STR,
            COMMAND_CHERRY_PICK_STR,
            COMMAND_ASSIGN_REVIEWERS_STR,
            COMMAND_CHECK_CAN_MERGE_STR,
            BUILD_AND_PUSH_CONTAINER_STR,
            COMMAND_ASSIGN_REVIEWER_STR,
        ]

        command_and_args: list[str] = command.split(" ", 1)
        _command = command_and_args[0]
        _args: str = command_and_args[1] if len(command_and_args) > 1 else ""

        self.logger.debug(
            f"{self.log_prefix} User: {reviewed_user}, Command: {_command}, Command args: {_args if _args else 'None'}"
        )
        if _command not in available_commands + list(USER_LABELS_DICT.keys()):
            self.logger.debug(f"{self.log_prefix} Command {command} is not supported.")
            return

        self.logger.info(f"{self.log_prefix} Processing label/user command {command} by user {reviewed_user}")

        if remove := len(command_and_args) > 1 and _args == "cancel":
            self.logger.debug(f"{self.log_prefix} User requested 'cancel' for command {_command}")

        if _command in (COMMAND_RETEST_STR, COMMAND_ASSIGN_REVIEWER_STR) and not _args:
            missing_command_arg_comment_msg: str = f"{_command} requires an argument"
            error_msg: str = f"{self.log_prefix} {missing_command_arg_comment_msg}"
            self.logger.debug(error_msg)
            self.pull_request.create_issue_comment(missing_command_arg_comment_msg)
            return

        self.create_comment_reaction(issue_comment_id=issue_comment_id, reaction=REACTIONS.ok)

        if _command == COMMAND_ASSIGN_REVIEWER_STR:
            self._add_reviewer_by_user_comment(reviewer=_args)

        elif _command == COMMAND_ASSIGN_REVIEWERS_STR:
            self.assign_reviewers()

        elif _command == COMMAND_CHECK_CAN_MERGE_STR:
            self.check_if_can_be_merged()

        elif _command == COMMAND_CHERRY_PICK_STR:
            self.process_cherry_pick_command(
                issue_comment_id=issue_comment_id, command_args=_args, reviewed_user=reviewed_user
            )

        elif _command == COMMAND_RETEST_STR:
            self.process_retest_command(issue_comment_id=issue_comment_id, command_args=_args)

        elif _command == BUILD_AND_PUSH_CONTAINER_STR:
            if self.build_and_push_container:
                self._run_build_container(push=True, set_check=False, command_args=_args)
            else:
                msg = f"No {BUILD_AND_PUSH_CONTAINER_STR} configured for this repository"
                error_msg = f"{self.log_prefix} {msg}"
                self.logger.debug(error_msg)
                self.pull_request.create_issue_comment(msg)

        elif _command == WIP_STR:
            wip_for_title: str = f"{WIP_STR.upper()}:"
            if remove:
                self._remove_label(label=WIP_STR)
                self.pull_request.edit(title=self.pull_request.title.replace(wip_for_title, ""))
            else:
                self._add_label(label=WIP_STR)
                self.pull_request.edit(title=f"{wip_for_title} {self.pull_request.title}")

        elif _command == HOLD_LABEL_STR:
            if reviewed_user not in self.all_approvers:
                self.pull_request.create_issue_comment(
                    f"{reviewed_user} is not part of the approver, only approvers can mark pull request with hold"
                )
            else:
                if remove:
                    self._remove_label(label=HOLD_LABEL_STR)
                else:
                    self._add_label(label=HOLD_LABEL_STR)

                self.check_if_can_be_merged()

        elif _command == VERIFIED_LABEL_STR:
            if remove:
                self._remove_label(label=VERIFIED_LABEL_STR)
                self.set_verify_check_queued()
            else:
                self._add_label(label=VERIFIED_LABEL_STR)
                self.set_verify_check_success()

        else:
            self.label_by_user_comment(
                user_requested_label=_command,
                remove=remove,
                reviewed_user=reviewed_user,
            )

    def cherry_pick(self, target_branch: str, reviewed_user: str = "") -> None:
        requested_by = reviewed_user or "by target-branch label"
        self.logger.info(f"{self.log_prefix} Cherry-pick requested by user: {requested_by}")

        new_branch_name = f"{CHERRY_PICKED_LABEL_PREFIX}-{self.pull_request.head.ref}-{shortuuid.uuid()[:5]}"
        if not self.is_branch_exists(branch=target_branch):
            err_msg = f"cherry-pick failed: {target_branch} does not exists"
            self.logger.error(err_msg)
            self.pull_request.create_issue_comment(err_msg)

        else:
            self.set_cherry_pick_in_progress()
            commit_hash = self.pull_request.merge_commit_sha
            commit_msg_striped = self.pull_request.title.replace("'", "")
            pull_request_url = self.pull_request.html_url
            clone_repo_dir = f"{self.clone_repo_dir}-{uuid4()}"
            git_cmd = f"git --work-tree={clone_repo_dir} --git-dir={clone_repo_dir}/.git"
            hub_cmd = f"GITHUB_TOKEN={self.token} hub --work-tree={clone_repo_dir} --git-dir={clone_repo_dir}/.git"
            commands: list[str] = [
                f"{git_cmd} checkout {target_branch}",
                f"{git_cmd} pull origin {target_branch}",
                f"{git_cmd} checkout -b {new_branch_name} origin/{target_branch}",
                f"{git_cmd} cherry-pick {commit_hash}",
                f"{git_cmd} push origin {new_branch_name}",
                f"bash -c \"{hub_cmd} pull-request -b {target_branch} -h {new_branch_name} -l {CHERRY_PICKED_LABEL_PREFIX} -m '{CHERRY_PICKED_LABEL_PREFIX}: [{target_branch}] {commit_msg_striped}' -m 'cherry-pick {pull_request_url} into {target_branch}' -m 'requested-by {requested_by}'\"",
            ]

            rc, out, err = None, "", ""
            with self._prepare_cloned_repo_dir(clone_repo_dir=clone_repo_dir) as _res:
                output = {
                    "title": "Cherry-pick details",
                    "summary": "",
                    "text": None,
                }
                if not _res[0]:
                    output["text"] = self.get_check_run_text(out=_res[1], err=_res[2])
                    self.set_cherry_pick_failure(output=output)

                for cmd in commands:
                    rc, out, err = run_command(command=cmd, log_prefix=self.log_prefix)
                    if not rc:
                        output["text"] = self.get_check_run_text(err=err, out=out)
                        self.set_cherry_pick_failure(output=output)
                        self.logger.error(f"{self.log_prefix} Cherry pick failed: {out} --- {err}")
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
                        return

            output["text"] = self.get_check_run_text(err=err, out=out)

            self.set_cherry_pick_success(output=output)
            self.pull_request.create_issue_comment(f"Cherry-picked PR {self.pull_request.title} into {target_branch}")

    def label_all_opened_pull_requests_merge_state_after_merged(self) -> None:
        """
        Labels pull requests based on their mergeable state.

        If the mergeable state is 'behind', the 'needs rebase' label is added.
        If the mergeable state is 'dirty', the 'has conflicts' label is added.
        """
        time_sleep = 30
        self.logger.info(f"{self.log_prefix} Sleep for {time_sleep} seconds before getting all opened PRs")
        time.sleep(time_sleep)

        for pull_request in self.repository.get_pulls(state="open"):
            self.pull_request = pull_request
            self.logger.info(f"{self.log_prefix} check label pull request after merge")
            self.label_pull_request_by_merge_state()

    def label_pull_request_by_merge_state(self) -> None:
        merge_state = self.pull_request.mergeable_state
        self.logger.debug(f"{self.log_prefix} Mergeable state is {merge_state}")
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

    def check_if_can_be_merged(self) -> None:
        """
        Check if PR can be merged and set the job for it

        Check the following:
            None of the required status checks in progress.
            Has verified label.
            Has approved from one of the approvers.
            All required run check passed.
            PR status is not 'dirty'.
            PR has no changed requests from approvers.
        """
        if self.skip_if_pull_request_already_merged():
            self.logger.debug(f"{self.log_prefix} Pull request already merged")
            return

        output = {
            "title": "Check if can be merged",
            "summary": "",
            "text": None,
        }
        failure_output = ""

        try:
            self.logger.info(f"{self.log_prefix} Check if {CAN_BE_MERGED_STR}.")
            self.set_merge_check_queued()
            last_commit_check_runs = list(self.last_commit.get_check_runs())
            _labels = self.pull_request_labels_names()
            self.logger.debug(f"{self.log_prefix} check if can be merged. PR labels are: {_labels}")

            is_pr_mergable = self.pull_request.mergeable
            if not is_pr_mergable:
                failure_output += f"PR is not mergeable: {is_pr_mergable}\n"

            required_check_in_progress_failure_output, check_runs_in_progress = self._required_check_in_progress(
                last_commit_check_runs=last_commit_check_runs
            )
            if required_check_in_progress_failure_output:
                failure_output += required_check_in_progress_failure_output

            labels_failure_output = self._wip_or_hold_lables_exists(labels=_labels)
            if labels_failure_output:
                failure_output += labels_failure_output

            required_check_failed_failure_output = self._required_check_failed(
                last_commit_check_runs=last_commit_check_runs, check_runs_in_progress=check_runs_in_progress
            )
            if required_check_failed_failure_output:
                failure_output += required_check_failed_failure_output

            labels_failure_output = self._check_lables_for_can_be_merged(labels=_labels)
            if labels_failure_output:
                failure_output += labels_failure_output

            pr_approvered_failure_output = self._check_if_pr_approved(labels=_labels)
            if pr_approvered_failure_output:
                failure_output += pr_approvered_failure_output

            if not failure_output:
                self._add_label(label=CAN_BE_MERGED_STR)
                self.set_merge_check_success()

                self.logger.info(f"{self.log_prefix} Pull request can be merged")
                return

            self.logger.debug(f"{self.log_prefix} cannot be merged: {failure_output}")
            output["text"] = failure_output
            self._remove_label(label=CAN_BE_MERGED_STR)
            self.set_merge_check_failure(output=output)

        except Exception as ex:
            self.logger.error(
                f"{self.log_prefix} Failed to check if can be merged, set check run to {FAILURE_STR} {ex}"
            )
            _err = "Failed to check if can be merged, check logs"
            output["text"] = _err
            self._remove_label(label=CAN_BE_MERGED_STR)
            self.set_merge_check_failure(output=output)

    @staticmethod
    def _comment_with_details(title: str, body: str) -> str:
        return f"""
<details>
<summary>{title}</summary>
    {body}
</details>
        """

    def _container_repository_and_tag(self, is_merged: bool = False, tag: str = "") -> str:
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
            self.logger.debug(f"{self.log_prefix} container tag is: {tag}")
            return f"{self.container_repository}:{tag}"

        self.logger.error(f"{self.log_prefix} container tag not found")
        return f"{self.container_repository}:webhook-server-tag-not-found"

    def _run_build_container(
        self,
        set_check: bool = True,
        push: bool = False,
        is_merged: bool = False,
        tag: str = "",
        command_args: str = "",
    ) -> None:
        if not self.build_and_push_container:
            return

        if self.is_check_run_in_progress(check_run=BUILD_CONTAINER_STR):
            self.logger.info(f"{self.log_prefix} Check run is in progress, re-running {BUILD_CONTAINER_STR}.")

        clone_repo_dir = f"{self.clone_repo_dir}-{uuid4()}"
        pull_request = hasattr(self, "pull_request")

        if pull_request and set_check:
            if self.is_check_run_in_progress(check_run=BUILD_CONTAINER_STR) and not is_merged:
                self.logger.info(f"{self.log_prefix} Check run is in progress, re-running {BUILD_CONTAINER_STR}.")

            self.set_container_build_in_progress()

        _container_repository_and_tag = self._container_repository_and_tag(is_merged=is_merged, tag=tag)
        no_cache: str = " --no-cache" if is_merged else ""
        build_cmd: str = f"--network=host {no_cache} -f {clone_repo_dir}/{self.dockerfile} {clone_repo_dir} -t {_container_repository_and_tag}"

        if self.container_build_args:
            build_args: str = [f"--build-arg {b_arg}" for b_arg in self.container_build_args][0]
            build_cmd = f"{build_args} {build_cmd}"

        if self.container_command_args:
            build_cmd = f"{' '.join(self.container_command_args)} {build_cmd}"

        if command_args:
            build_cmd = f"{command_args} {build_cmd}"

        podman_build_cmd: str = f"podman build {build_cmd}"
        with self._prepare_cloned_repo_dir(
            is_merged=is_merged,
            tag_name=tag,
            clone_repo_dir=clone_repo_dir,
        ) as _res:
            output: dict[str, Any] = {
                "title": "Build container",
                "summary": "",
                "text": None,
            }
            if not _res[0]:
                output["text"] = self.get_check_run_text(out=_res[1], err=_res[2])
                if self.pull_request and set_check:
                    return self.set_container_build_failure(output=output)

            build_rc, build_out, build_err = self.run_podman_command(command=podman_build_cmd, pipe=True)
            output["text"] = self.get_check_run_text(err=build_err, out=build_out)

            if build_rc:
                self.logger.info(f"{self.log_prefix} Done building {_container_repository_and_tag}")
                if pull_request and set_check:
                    return self.set_container_build_success(output=output)
            else:
                self.logger.error(f"{self.log_prefix} Failed to build {_container_repository_and_tag}")
                if self.pull_request and set_check:
                    return self.set_container_build_failure(output=output)

            if push and build_rc:
                cmd = f"podman push --creds {self.container_repository_username}:{self.container_repository_password} {_container_repository_and_tag}"
                push_rc, _, _ = self.run_podman_command(command=cmd)
                if push_rc:
                    push_msg: str = f"New container for {_container_repository_and_tag} published"
                    if pull_request:
                        self.pull_request.create_issue_comment(push_msg)

                    if self.slack_webhook_url:
                        message = f"""
```
{self.repository_full_name} {push_msg}.
```
"""
                        self.send_slack_message(message=message, webhook_url=self.slack_webhook_url)

                    self.logger.info(f"{self.log_prefix} Done push {_container_repository_and_tag}")
                else:
                    err_msg: str = f"Failed to build and push {_container_repository_and_tag}"
                    if self.pull_request:
                        self.pull_request.create_issue_comment(err_msg)

                    if self.slack_webhook_url:
                        message = f"""
```
{self.repository_full_name} {err_msg}.
```
                        """
                        self.send_slack_message(message=message, webhook_url=self.slack_webhook_url)

    def _run_install_python_module(self) -> None:
        if not self.pypi:
            return

        if self.is_check_run_in_progress(check_run=PYTHON_MODULE_INSTALL_STR):
            self.logger.info(f"{self.log_prefix} Check run is in progress, re-running {PYTHON_MODULE_INSTALL_STR}.")

        clone_repo_dir = f"{self.clone_repo_dir}-{uuid4()}"
        self.logger.info(f"{self.log_prefix} Installing python module")
        self.set_python_module_install_in_progress()
        with self._prepare_cloned_repo_dir(
            clone_repo_dir=clone_repo_dir,
        ) as _res:
            output: dict[str, Any] = {
                "title": "Python module installation",
                "summary": "",
                "text": None,
            }
            if not _res[0]:
                output["text"] = self.get_check_run_text(out=_res[1], err=_res[2])
                return self.set_python_module_install_failure(output=output)

            rc, out, err = run_command(
                command=f"uvx pip wheel --no-cache-dir -w {clone_repo_dir}/dist {clone_repo_dir}",
                log_prefix=self.log_prefix,
            )

            output["text"] = self.get_check_run_text(err=err, out=out)

            if rc:
                return self.set_python_module_install_success(output=output)

            return self.set_python_module_install_failure(output=output)

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

    def _process_verified_for_update_or_new_pull_request(self) -> None:
        if not self.verified_job:
            return

        if self.parent_committer in self.auto_verified_and_merged_users:
            self.logger.info(
                f"{self.log_prefix} Committer {self.parent_committer} is part of {self.auto_verified_and_merged_users}"
                ", Setting verified label"
            )
            self._add_label(label=VERIFIED_LABEL_STR)
            self.set_verify_check_success()
        else:
            self.logger.info(f"{self.log_prefix} Processing reset {VERIFIED_LABEL_STR} label on new commit push")
            # Remove verified label
            self._remove_label(label=VERIFIED_LABEL_STR)
            self.set_verify_check_queued()

    def create_comment_reaction(self, issue_comment_id: int, reaction: str) -> None:
        _comment = self.pull_request.get_issue_comment(issue_comment_id)
        _comment.create_reaction(reaction)

    def process_opened_or_synchronize_pull_request(self) -> None:
        prepare_pull_futures: list[Future] = []

        with ThreadPoolExecutor() as executor:
            prepare_pull_futures.append(executor.submit(self.assign_reviewers))
            prepare_pull_futures.append(
                executor.submit(
                    self._add_label,
                    **{"label": f"{BRANCH_LABEL_PREFIX}{self.pull_request_branch}"},
                )
            )
            prepare_pull_futures.append(executor.submit(self.label_pull_request_by_merge_state))
            prepare_pull_futures.append(executor.submit(self.set_merge_check_queued))
            prepare_pull_futures.append(executor.submit(self.set_run_tox_check_queued))
            prepare_pull_futures.append(executor.submit(self.set_run_pre_commit_check_queued))
            prepare_pull_futures.append(executor.submit(self.set_python_module_install_queued))
            prepare_pull_futures.append(executor.submit(self.set_container_build_queued))
            prepare_pull_futures.append(executor.submit(self._process_verified_for_update_or_new_pull_request))
            prepare_pull_futures.append(executor.submit(self.add_size_label))
            prepare_pull_futures.append(executor.submit(self.add_pull_request_owner_as_assingee))

            prepare_pull_futures.append(executor.submit(self._run_tox))
            prepare_pull_futures.append(executor.submit(self._run_pre_commit))
            prepare_pull_futures.append(executor.submit(self._run_install_python_module))
            prepare_pull_futures.append(executor.submit(self._run_build_container))

            if self.conventional_title:
                prepare_pull_futures.append(executor.submit(self.set_conventional_title_queued))
                prepare_pull_futures.append(executor.submit(self._run_conventional_title_check))

        for result in as_completed(prepare_pull_futures):
            if _exp := result.exception():
                self.logger.error(f"{self.log_prefix} {_exp}")

    def is_check_run_in_progress(self, check_run: str) -> bool:
        for run in self.last_commit.get_check_runs():
            if run.name == check_run and run.status == IN_PROGRESS_STR:
                return True
        return False

    def set_check_run_status(
        self,
        check_run: str,
        status: str = "",
        conclusion: str = "",
        output: dict[str, str] | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {"name": check_run, "head_sha": self.last_commit.sha}

        if status:
            kwargs["status"] = status

        if conclusion:
            kwargs["conclusion"] = conclusion

        if output:
            kwargs["output"] = output

        msg: str = f"{self.log_prefix} check run {check_run} status: {status or conclusion}"

        try:
            self.repository_by_github_app.create_check_run(**kwargs)
            if conclusion in (SUCCESS_STR, IN_PROGRESS_STR):
                self.logger.success(msg)  # type: ignore
            return

        except Exception as ex:
            self.logger.debug(f"{self.log_prefix} Failed to set {check_run} check to {status or conclusion}, {ex}")
            kwargs["conclusion"] = FAILURE_STR
            self.repository_by_github_app.create_check_run(**kwargs)

    @contextlib.contextmanager
    def _prepare_cloned_repo_dir(
        self,
        clone_repo_dir: str,
        is_merged: bool = False,
        checkout: str = "",
        tag_name: str = "",
    ) -> Generator[tuple[bool, Any, Any], None, None]:
        git_cmd = f"git --work-tree={clone_repo_dir} --git-dir={clone_repo_dir}/.git"

        # Clone the repository
        rc, out, err = run_command(
            command=f"git clone {self.repository.clone_url.replace('https://', f'https://{self.token}@')} "
            f"{clone_repo_dir}",
            log_prefix=self.log_prefix,
        )
        try:
            rc, out, err = run_command(
                command=f"{git_cmd} config user.name '{self.repository.owner.login}'", log_prefix=self.log_prefix
            )
            if not rc:
                yield rc, out, err

            rc, out, err = run_command(
                f"{git_cmd} config user.email '{self.repository.owner.email}'", log_prefix=self.log_prefix
            )
            if not rc:
                yield rc, out, err

            rc, out, err = run_command(
                command=f"{git_cmd} config --local --add remote.origin.fetch +refs/pull/*/head:refs/remotes/origin/pr/*",
                log_prefix=self.log_prefix,
            )
            if not rc:
                yield rc, out, err

            rc, out, err = run_command(command=f"{git_cmd} remote update", log_prefix=self.log_prefix)
            if not rc:
                yield rc, out, err

            # Checkout to requested branch/tag
            if checkout:
                rc, out, err = run_command(f"{git_cmd} checkout {checkout}", log_prefix=self.log_prefix)
                if not rc:
                    yield rc, out, err

                if getattr(self, "pull_request", None):
                    rc, out, err = run_command(
                        f"{git_cmd} merge origin/{self.pull_request_branch} -m 'Merge {self.pull_request_branch}'",
                        log_prefix=self.log_prefix,
                    )
                    if not rc:
                        yield rc, out, err

            # Checkout the branch if pull request is merged or for release
            else:
                if is_merged:
                    rc, out, err = run_command(
                        command=f"{git_cmd} checkout {self.pull_request_branch}", log_prefix=self.log_prefix
                    )
                    if not rc:
                        yield rc, out, err

                elif tag_name:
                    rc, out, err = run_command(command=f"{git_cmd} checkout {tag_name}", log_prefix=self.log_prefix)
                    if not rc:
                        yield rc, out, err

                # Checkout the pull request
                else:
                    try:
                        pull_request = self._get_pull_request()
                        rc, out, err = run_command(
                            command=f"{git_cmd} checkout origin/pr/{pull_request.number}", log_prefix=self.log_prefix
                        )
                        if not rc:
                            yield rc, out, err

                        if getattr(self, "pull_request", None):
                            rc, out, err = run_command(
                                f"{git_cmd} merge origin/{self.pull_request_branch} -m 'Merge {self.pull_request_branch}'",
                                log_prefix=self.log_prefix,
                            )
                            if not rc:
                                yield rc, out, err
                    except NoPullRequestError:
                        self.logger.error(f"{self.log_prefix} [func:_run_in_container] No pull request found")
                        yield False, "", "[func:_run_in_container] No pull request found"

            yield rc, out, err

        finally:
            self.logger.debug(f"{self.log_prefix} Deleting {clone_repo_dir}")
            shutil.rmtree(clone_repo_dir)

    def get_check_run_text(self, err: str, out: str) -> str:
        total_len: int = len(err) + len(out)

        if total_len > 65534:  # GitHub limit is 65535 characters
            _output = f"```\n{err}\n\n{out}\n```"[:65534]
        else:
            _output = f"```\n{err}\n\n{out}\n```"

        _hased_str = "*****"

        if self.pypi and self.pypi.get("token"):
            _output = _output.replace(self.pypi["token"], _hased_str)

        if getattr(self, "container_repository_username", None):
            _output = _output.replace(self.container_repository_username, _hased_str)

        if getattr(self, "container_repository_password", None):
            _output = _output.replace(self.container_repository_password, _hased_str)

        if self.token:
            _output = _output.replace(self.token, _hased_str)

        return _output

    def get_branch_required_status_checks(self) -> list[str]:
        if self.repository.private:
            self.logger.info(
                f"{self.log_prefix} Repository is private, skipping getting branch protection required status checks"
            )
            return []

        pull_request_branch = self.repository.get_branch(self.pull_request_branch)
        branch_protection = pull_request_branch.get_protection()
        return branch_protection.required_status_checks.contexts

    def get_all_required_status_checks(self) -> list[str]:
        if not hasattr(self, "pull_request_branch"):
            self.pull_request_branch = self.pull_request.base.ref

        all_required_status_checks: list[str] = []
        branch_required_status_checks = self.get_branch_required_status_checks()
        if self.tox:
            all_required_status_checks.append(TOX_STR)

        if self.verified_job:
            all_required_status_checks.append(VERIFIED_LABEL_STR)

        if self.build_and_push_container:
            all_required_status_checks.append(BUILD_CONTAINER_STR)

        if self.pypi:
            all_required_status_checks.append(PYTHON_MODULE_INSTALL_STR)

        if self.conventional_title:
            all_required_status_checks.append(CONVENTIONAL_TITLE_STR)

        _all_required_status_checks = branch_required_status_checks + all_required_status_checks
        self.logger.debug(f"{self.log_prefix} All required status checks: {_all_required_status_checks}")
        return _all_required_status_checks

    def set_wip_label_based_on_title(self) -> None:
        if self.pull_request.title.lower().startswith(f"{WIP_STR}:"):
            self.logger.debug(
                f"{self.log_prefix} Found {WIP_STR} in {self.pull_request.title}; adding {WIP_STR} label."
            )
            self._add_label(label=WIP_STR)

        else:
            self.logger.debug(
                f"{self.log_prefix} {WIP_STR} not found in {self.pull_request.title}; removing {WIP_STR} label."
            )
            self._remove_label(label=WIP_STR)

    def process_cherry_pick_command(self, issue_comment_id: int, command_args: str, reviewed_user: str) -> None:
        _target_branches: list[str] = command_args.split()
        _exits_target_branches: set[str] = set()
        _non_exits_target_branches_msg: str = ""

        for _target_branch in _target_branches:
            try:
                self.repository.get_branch(_target_branch)
            except Exception:
                _non_exits_target_branches_msg += f"Target branch `{_target_branch}` does not exist\n"

            _exits_target_branches.add(_target_branch)

        if _non_exits_target_branches_msg:
            self.logger.info(f"{self.log_prefix} {_non_exits_target_branches_msg}")
            self.pull_request.create_issue_comment(_non_exits_target_branches_msg)

        if _exits_target_branches:
            if not self.pull_request.is_merged():
                cp_labels: list[str] = [
                    f"{CHERRY_PICK_LABEL_PREFIX}{_target_branch}" for _target_branch in _exits_target_branches
                ]
                info_msg: str = f"""
Cherry-pick requested for PR: `{self.pull_request.title}` by user `{reviewed_user}`
Adding label/s `{" ".join([_cp_label for _cp_label in cp_labels])}` for automatic cheery-pick once the PR is merged
"""
                self.logger.info(f"{self.log_prefix} {info_msg}")
                self.pull_request.create_issue_comment(info_msg)
                for _cp_label in cp_labels:
                    self._add_label(label=_cp_label)
            else:
                for _exits_target_branch in _exits_target_branches:
                    self.cherry_pick(
                        target_branch=_exits_target_branch,
                        reviewed_user=reviewed_user,
                    )

    def process_retest_command(self, issue_comment_id: int, command_args: str) -> None:
        _target_tests: list[str] = command_args.split()
        _not_supported_retests: list[str] = []
        _supported_retests: list[str] = []
        _retests_to_func_map: dict[str, Callable] = {
            TOX_STR: self._run_tox,
            PRE_COMMIT_STR: self._run_pre_commit,
            BUILD_CONTAINER_STR: self._run_build_container,
            PYTHON_MODULE_INSTALL_STR: self._run_install_python_module,
            CONVENTIONAL_TITLE_STR: self._run_conventional_title_check,
        }

        if not _target_tests:
            msg = "No test defined to retest"
            error_msg = f"{self.log_prefix} {msg}."
            self.logger.debug(error_msg)
            self.pull_request.create_issue_comment(msg)
            return

        if "all" in command_args:
            if len(_target_tests) > 1:
                msg = "Invalid command. `all` cannot be used with other tests"
                error_msg = f"{self.log_prefix} {msg}."
                self.logger.debug(error_msg)
                self.pull_request.create_issue_comment(msg)
                return

            else:
                _supported_retests = self.current_pull_request_supported_retest

        else:
            for _test in _target_tests:
                if _test in self.current_pull_request_supported_retest:
                    _supported_retests.append(_test)

                else:
                    _not_supported_retests.append(_test)

        if _not_supported_retests:
            msg = f"No {' '.join(_not_supported_retests)} configured for this repository"
            error_msg = f"{self.log_prefix} {msg}."
            self.logger.debug(error_msg)
            self.pull_request.create_issue_comment(msg)

        if _supported_retests:
            _retest_to_exec: list[Future] = []
            with ThreadPoolExecutor() as executor:
                for _test in _supported_retests:
                    _retest_to_exec.append(executor.submit(_retests_to_func_map[_test]))

            for result in as_completed(_retest_to_exec):
                if _exp := result.exception():
                    self.logger.error(f"{self.log_prefix} {_exp}")

    def remove_labels_when_pull_request_sync(self) -> None:
        futures = []
        with ThreadPoolExecutor() as executor:
            for _label in self.pull_request.labels:
                _label_name = _label.name
                if (
                    _label_name.startswith(APPROVED_BY_LABEL_PREFIX)
                    or _label_name.startswith(COMMENTED_BY_LABEL_PREFIX)
                    or _label_name.startswith(CHANGED_REQUESTED_BY_LABEL_PREFIX)
                    or _label_name.startswith(LGTM_BY_LABEL_PREFIX)
                ):
                    futures.append(
                        executor.submit(
                            self._remove_label,
                            **{
                                "label": _label_name,
                            },
                        )
                    )
        for _ in as_completed(futures):
            # wait for all tasks to complete
            pass

    def add_pull_request_owner_as_assingee(self) -> None:
        try:
            self.logger.info(f"{self.log_prefix} Adding PR owner as assignee")
            self.pull_request.add_to_assignees()
        except Exception as exp:
            self.logger.debug(f"{self.log_prefix} Exception while adding PR owner as assignee: {exp}")

            if self.root_approvers:
                self.logger.debug(f"{self.log_prefix} Falling back to first approver as assignee")
                self.pull_request.add_to_assignees(self.root_approvers[0])

    def set_pull_request_automerge(self) -> None:
        auto_merge = (
            self.pull_request_branch in self.set_auto_merge_prs
            or self.parent_committer in self.auto_verified_and_merged_users
        )

        self.logger.debug(f"{self.log_prefix} auto_merge: {auto_merge}, branch: {self.pull_request_branch}")

        if auto_merge:
            try:
                if not self.pull_request.raw_data.get("auto_merge"):
                    self.logger.info(
                        f"{self.log_prefix} will be merged automatically. owner: {self.parent_committer} "
                        f"is part of auto merge enabled rules"
                    )

                    self.pull_request.enable_automerge(merge_method="SQUASH")
                else:
                    self.logger.debug(f"{self.log_prefix} is already set to auto merge")

            except Exception as exp:
                self.logger.error(f"{self.log_prefix} Exception while setting auto merge: {exp}")

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

    def is_podman_bug(self, err: str) -> bool:
        _err = "Error: current system boot ID differs from cached boot ID; an unhandled reboot has occurred"
        return _err in err.strip()

    def fix_podman_bug(self) -> None:
        self.logger.debug(f"{self.log_prefix} Fixing podman bug")
        shutil.rmtree("/tmp/storage-run-1000/containers", ignore_errors=True)
        shutil.rmtree("/tmp/storage-run-1000/libpod/tmp", ignore_errors=True)

    def run_podman_command(self, command: str, pipe: bool = False) -> tuple[bool, str, str]:
        rc, out, err = run_command(command=command, log_prefix=self.log_prefix, pipe=pipe)

        if rc:
            return rc, out, err

        if self.is_podman_bug(err=err):
            self.fix_podman_bug()
            return run_command(command=command, log_prefix=self.log_prefix, pipe=pipe)

        return rc, out, err

    def get_all_approvers_and_reviewers(self) -> dict[str, dict[str, Any]]:
        # Dictionary mapping OWNERS file paths to their approvers and reviewers
        _owners: dict[str, dict[str, Any]] = {}

        max_owners_files = 1000  # Configurable limit
        owners_count = 0

        self.logger.debug(f"{self.log_prefix} Get git tree")
        tree = self.repository.get_git_tree(self.pull_request_branch, recursive=True)
        for element in tree.tree:
            if element.type == "blob" and element.path.endswith("OWNERS"):
                owners_count += 1
                if owners_count > max_owners_files:
                    self.logger.error(f"{self.log_prefix} Too many OWNERS files (>{max_owners_files})")
                    break

                content_path = element.path
                self.logger.debug(f"{self.log_prefix} Get OWNERS file from {content_path}")
                _path = self.repository.get_contents(content_path, self.pull_request_branch)
                if isinstance(_path, list):
                    _path = _path[0]

                try:
                    content = yaml.safe_load(_path.decoded_content)
                    if self._validate_owners_content(content, content_path):
                        parent_path = str(Path(content_path).parent)
                        if not parent_path:
                            parent_path = "."
                        _owners[parent_path] = content

                except yaml.YAMLError as exp:
                    self.logger.error(f"{self.log_prefix} Invalid OWNERS file {content_path}: {exp}")
                    continue

        return _owners

    def get_all_approvers(self) -> list[str]:
        _approvers: list[str] = []
        for list_of_approvers in self.owners_data_for_changed_files().values():
            for _approver in list_of_approvers.get("approvers", []):
                _approvers.append(_approver)

        _approvers.sort()
        return _approvers

    def get_all_reviewers(self) -> list[str]:
        _reviewers: list[str] = []
        for list_of_reviewers in self.owners_data_for_changed_files().values():
            for _approver in list_of_reviewers.get("reviewers", []):
                _reviewers.append(_approver)

        _reviewers.sort()
        return _reviewers

    def owners_data_for_changed_files(self) -> dict[str, dict[str, Any]]:
        data: dict[str, dict[str, Any]] = {}

        changed_folders = {Path(cf).parent for cf in self.changed_files}

        changed_folder_match: list[Path] = []

        require_root_approvers: bool | None = None

        for owners_dir, owners_data in self.all_approvers_and_reviewers.items():
            if owners_dir == ".":
                continue

            _owners_dir = Path(owners_dir)

            for changed_folder in changed_folders:
                if changed_folder == _owners_dir or _owners_dir in changed_folder.parents:
                    data[owners_dir] = owners_data
                    changed_folder_match.append(_owners_dir)
                    if require_root_approvers is None:
                        require_root_approvers = owners_data.get("root-approvers", True)

        if require_root_approvers or require_root_approvers is None:
            self.logger.debug(f"{self.log_prefix} require root_approvers")
            data["."] = self.all_approvers_and_reviewers.get(".", {})

        else:
            for _folder in changed_folders:
                for _changed_path in changed_folder_match:
                    if _folder == _changed_path or _changed_path in _folder.parents:
                        continue
                    else:
                        data["."] = self.all_approvers_and_reviewers.get(".", {})
                        break

        self.logger.debug(f"{self.log_prefix} Owners data for current pull request: {yaml.dump(data)}")
        return data

    def _validate_owners_content(self, content: Any, path: str) -> bool:
        """Validate OWNERS file content structure."""
        try:
            if not isinstance(content, dict):
                raise ValueError("OWNERS file must contain a dictionary")

            for key in ["approvers", "reviewers"]:
                if key in content:
                    if not isinstance(content[key], list):
                        raise ValueError(f"{key} must be a list")

                    if not all(isinstance(_elm, str) for _elm in content[key]):
                        raise ValueError(f"All {key} must be strings")

            return True

        except ValueError as e:
            self.logger.error(f"{self.log_prefix} Invalid OWNERS file {path}: {e}")
            return False

    def _required_check_in_progress(self, last_commit_check_runs: list[CheckRun]) -> tuple[str, list[str]]:
        self.all_required_status_checks = self.get_all_required_status_checks()
        last_commit_check_runs = list(self.last_commit.get_check_runs())
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

    def _wip_or_hold_lables_exists(self, labels: list[str]) -> str:
        failure_output = ""
        is_hold = HOLD_LABEL_STR in labels
        is_wip = WIP_STR in labels

        if is_hold or is_wip:
            if is_hold:
                failure_output += "Hold label exists.\n"

            if is_wip:
                failure_output += "WIP label exists.\n"

        return failure_output

    def _check_lables_for_can_be_merged(self, labels: list[str]) -> str:
        self.logger.debug(f"{self.log_prefix} _check_lables_for_can_be_merged.")
        failure_output = ""

        for _label in labels:
            if CHANGED_REQUESTED_BY_LABEL_PREFIX.lower() in _label.lower():
                change_request_user = _label.split(LABELS_SEPARATOR)[-1]
                if change_request_user in self.all_approvers:
                    failure_output += "PR has changed requests from approvers\n"

        missing_required_labels = []
        for _req_label in self.can_be_merged_required_labels:
            if _req_label not in labels:
                missing_required_labels.append(_req_label)

        if missing_required_labels:
            failure_output += f"Missing required labels: {', '.join(missing_required_labels)}\n"

        return failure_output

    def _check_if_pr_approved(self, labels: list[str]) -> str:
        self.logger.info(f"{self.log_prefix} Check if pull request is approved by pull request labels.")

        error: str = ""
        approved_by = []
        lgtm_count: int = 0

        all_reviewers = self.all_reviewers.copy()
        all_reviewers_without_pr_owner = {
            _reviewer for _reviewer in all_reviewers if _reviewer != self.parent_committer
        }

        if self.minimum_lgtm:
            for _label in labels:
                reviewer = _label.split(LABELS_SEPARATOR)[-1]
                if LGTM_BY_LABEL_PREFIX.lower() in _label.lower() and reviewer in all_reviewers_without_pr_owner:
                    lgtm_count += 1

        for _label in labels:
            if APPROVED_BY_LABEL_PREFIX.lower() in _label.lower():
                approved_by.append(_label.split(LABELS_SEPARATOR)[-1])

        missing_approvers = self.all_approvers.copy()

        for data in self.owners_data_for_changed_files().values():
            required_pr_approvers = data.get("approvers", [])
            for required_pr_approver in required_pr_approvers:
                if required_pr_approver in approved_by:
                    # Once we found approver in approved_by list, we remove all approvers from missing_approvers list for this owners file
                    {
                        missing_approvers.remove(_approver)  # type: ignore
                        for _approver in required_pr_approvers
                        if _approver in missing_approvers
                    }
                    break

        missing_approvers = list(set(missing_approvers))

        if missing_approvers:
            error += f"Missing approved from approvers: {', '.join(missing_approvers)}\n"

        if lgtm_count < self.minimum_lgtm:
            if lgtm_count == len(all_reviewers_without_pr_owner):
                self.logger.debug(
                    f"{self.log_prefix} minimum_lgtm is {self.minimum_lgtm}, but number of reviewers is {len(all_reviewers_without_pr_owner)}. PR approved."
                )
            else:
                error += (
                    "Missing lgtm from reviewers. "
                    f"Minimum {self.minimum_lgtm} required. Reviewers: {', '.join(all_reviewers_without_pr_owner)}.\n"
                )

        return error

    def _add_reviewer_by_user_comment(self, reviewer: str) -> None:
        reviewer = reviewer.strip("@")
        self.logger.info(f"{self.log_prefix} Adding reviewer {reviewer} by user comment")

        for contributer in self.repository.get_contributors():
            if contributer.login == reviewer:
                self.pull_request.create_review_request([reviewer])
                return

        _err = f"not adding reviewer {reviewer} by user comment, {reviewer} is not part of contributers"
        self.logger.debug(f"{self.log_prefix} {_err}")
        self.pull_request.create_issue_comment(_err)

    def _run_conventional_title_check(self) -> None:
        output: dict[str, str] = {
            "title": "Conventional Title",
            "summary": "",
            "text": "",
        }

        if self.is_check_run_in_progress(check_run=CONVENTIONAL_TITLE_STR):
            self.logger.info(f"{self.log_prefix} Check run is in progress, re-running {CONVENTIONAL_TITLE_STR}.")

        self.set_conventional_title_in_progress()
        allowed_names = self.conventional_title.split(",")
        title = self.pull_request.title
        if any([title.startswith(f"{_name}:") for _name in allowed_names]):
            self.set_conventional_title_success(output=output)
        else:
            output["summary"] = "Failed"
            output["text"] = f"Pull request title must starts with allowed title: {': ,'.join(allowed_names)}"

            self.set_conventional_title_failure(output=output)

    def _prepare_owners_welcome_comment(self) -> str:
        body_approvers: str = " * Approvers:\n"
        body_reviewers: str = " * Reviewers:\n"

        for _approver in self.all_approvers:
            body_approvers += f"   * {_approver}\n"

        for _reviewer in self.all_reviewers:
            body_reviewers += f"   * {_reviewer}\n"

        return f"""
{body_approvers}

{body_reviewers}
"""

    def _prepare_welcome_comment(self) -> str:
        self.logger.info(f"{self.log_prefix} Prepare welcome comment")
        supported_user_labels_str: str = "".join([f" * {label}\n" for label in USER_LABELS_DICT.keys()])
        return f"""
{self.issue_url_for_welcome_msg}

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
    * You can add extra args to the Podman build command
        * Example: `/build-and-push-container --build-arg OPENSHIFT_PYTHON_WRAPPER_COMMIT=<commit_hash>`
 * To add a label by comment use `/<label name>`, to remove, use `/<label name> cancel`
 * To assign reviewers based on OWNERS file use `/assign-reviewers`
 * To check if PR can be merged use `/check-can-merge`
 * to assign reviewer to PR use `/assign-reviewer @<reviewer>`

PR will be approved when the following conditions are met:
 * `/approve` from one of the approvers.
 * Minimum number of required `/lgtm` (`{self.minimum_lgtm}`) is met.

<details>
<summary>Approvers and Reviewers</summary>

{self._prepare_owners_welcome_comment()}
</details>

<details>
<summary>Supported /retest check runs</summary>

{self._prepare_retest_welcome_comment}
</details>

<details>
<summary>Supported labels</summary>

{supported_user_labels_str}
</details>
    """
