from __future__ import annotations

import contextlib
import json
import logging
import os
import random
from pathlib import Path
from typing import Any

import requests
import yaml
from fastapi.exceptions import HTTPException
from github import GithubException
from github.Branch import Branch
from github.Commit import Commit
from github.PullRequest import PullRequest
from starlette.datastructures import Headers
from stringcolor import cs

from webhook_server.libs.check_run_handler import CheckRunHandler
from webhook_server.libs.config import Config
from webhook_server.libs.exceptions import NoPullRequestError, RepositoryNotFoundError
from webhook_server.libs.issue_comment_handler import IssueCommentHandler
from webhook_server.libs.pull_request_handler import PullRequestHandler
from webhook_server.libs.pull_request_review_handler import PullRequestReviewHandler
from webhook_server.libs.push_handler import PushHandler
from webhook_server.utils.constants import (
    BUILD_CONTAINER_STR,
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
    extract_key_from_dict,
    get_api_with_highest_rate_limit,
    get_apis_and_tokes_from_config,
    get_github_repo_api,
)


class GithubWebhook:
    def __init__(self, hook_data: dict[Any, Any], headers: Headers, logger: logging.Logger) -> None:
        self.logger = logger
        self.logger.name = "GithubWebhook"
        self.hook_data = hook_data
        self.headers = headers
        self.repository_name: str = hook_data["repository"]["name"]
        self.repository_full_name: str = hook_data["repository"]["full_name"]
        self.parent_committer: str = ""
        self.issue_title: str = ""
        self.x_github_delivery: str = self.headers.get("X-GitHub-Delivery", "")
        self.github_event: str = self.headers["X-GitHub-Event"]
        self.owners_content: dict[str, Any] = {}

        self.config = Config(repository=self.repository_name, logger=self.logger)

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
        self.add_api_users_to_auto_verified_and_merged_users

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
            self.all_repository_approvers_and_reviewers = self.get_all_repository_approvers_and_reviewers()
            self.all_repository_approvers = self.get_all_repository_approvers()
            self.all_repository_reviewers = self.get_all_repository_reviewers()
            self.all_pull_request_approvers = self.get_all_pull_request_approvers()
            self.all_pull_request_reviewers = self.get_all_pull_request_reviewers()

            if self.github_event == "issue_comment":
                return IssueCommentHandler(github_webhook=self).process_comment_webhook_data()

            if self.github_event == "pull_request":
                return PullRequestHandler(github_webhook=self).process_pull_request_webhook_data()

            if self.github_event == "pull_request_review":
                return PullRequestReviewHandler(github_webhook=self).process_pull_request_review_webhook_data()

            if self.github_event == "check_run":
                if CheckRunHandler(github_webhook=self).process_pull_request_check_run_webhook_data():
                    PullRequestHandler(github_webhook=self).check_if_can_be_merged()

        except NoPullRequestError:
            self.logger.debug(f"{self.log_prefix} {event_log}. [No pull request found in hook data]")

            if self.github_event == "push":
                return PushHandler(github_webhook=self).process_push_webhook_data()

            raise

        except Exception as e:
            self.logger.error(f"{self.log_prefix} {event_log}. Exception: {e}")
            raise HTTPException(status_code=404, detail=str(e))

    @property
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

    def is_branch_exists(self, branch: str) -> Branch:
        return self.repository.get_branch(branch)

    @property
    def root_reviewers(self) -> list[str]:
        _reviewers = self.all_repository_approvers_and_reviewers.get(".", {}).get("reviewers", [])
        self.logger.debug(f"{self.log_prefix} ROOT Reviewers: {_reviewers}")
        return _reviewers

    @property
    def root_approvers(self) -> list[str]:
        _approvers = self.all_repository_approvers_and_reviewers.get(".", {}).get("approvers", [])
        self.logger.debug(f"{self.log_prefix} ROOT Approvers: {_approvers}")
        return _approvers

    def list_changed_files(self) -> list[str]:
        return [_file.filename for _file in self.pull_request.get_files()]

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

    def get_all_repository_approvers_and_reviewers(self) -> dict[str, dict[str, Any]]:
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

    def get_all_repository_approvers(self) -> list[str]:
        _approvers: list[str] = []

        for value in self.all_repository_approvers_and_reviewers.values():
            for key, val in value.items():
                if key == "approvers":
                    _approvers.extend(val)

        return _approvers

    def get_all_repository_reviewers(self) -> list[str]:
        _reviewers: list[str] = []

        for value in self.all_repository_approvers_and_reviewers.values():
            for key, val in value.items():
                if key == "reviewers":
                    _reviewers.extend(val)

        return _reviewers

    def get_all_pull_request_approvers(self) -> list[str]:
        _approvers: list[str] = []
        for list_of_approvers in self.owners_data_for_changed_files().values():
            for _approver in list_of_approvers.get("approvers", []):
                _approvers.append(_approver)

        _approvers.sort()
        return _approvers

    def get_all_pull_request_reviewers(self) -> list[str]:
        _reviewers: list[str] = []
        for list_of_reviewers in self.owners_data_for_changed_files().values():
            for _reviewer in list_of_reviewers.get("reviewers", []):
                _reviewers.append(_reviewer)

        _reviewers.sort()
        return _reviewers

    def owners_data_for_changed_files(self) -> dict[str, dict[str, Any]]:
        data: dict[str, dict[str, Any]] = {}

        changed_folders = {Path(cf).parent for cf in self.changed_files}

        changed_folder_match: list[Path] = []

        require_root_approvers: bool | None = None

        for owners_dir, owners_data in self.all_repository_approvers_and_reviewers.items():
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
            data["."] = self.all_repository_approvers_and_reviewers.get(".", {})

        else:
            for _folder in changed_folders:
                for _changed_path in changed_folder_match:
                    if _folder == _changed_path or _changed_path in _folder.parents:
                        continue
                    else:
                        data["."] = self.all_repository_approvers_and_reviewers.get(".", {})
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

    def assign_reviewers(self) -> None:
        self.logger.info(f"{self.log_prefix} Assign reviewers")

        _to_add: list[str] = list(set(self.all_pull_request_reviewers))
        self.logger.debug(f"{self.log_prefix} Reviewers to add: {', '.join(_to_add)}")

        for reviewer in _to_add:
            if reviewer != self.pull_request.user.login:
                self.logger.debug(f"{self.log_prefix} Adding reviewer {reviewer}")
                try:
                    self.pull_request.create_review_request([reviewer])
                except GithubException as ex:
                    self.logger.debug(f"{self.log_prefix} Failed to add reviewer {reviewer}. {ex}")
                    self.pull_request.create_issue_comment(f"{reviewer} can not be added as reviewer. {ex}")
