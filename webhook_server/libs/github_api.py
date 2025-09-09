from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from typing import Any

import requests
from github import GithubException
from github.Commit import Commit
from github.PullRequest import PullRequest
from github.Repository import Repository
from starlette.datastructures import Headers

from webhook_server.libs.check_run_handler import CheckRunHandler
from webhook_server.libs.config import Config
from webhook_server.libs.exceptions import RepositoryNotFoundInConfigError
from webhook_server.libs.issue_comment_handler import IssueCommentHandler
from webhook_server.libs.owners_files_handler import OwnersFileHandler
from webhook_server.libs.pull_request_handler import PullRequestHandler
from webhook_server.libs.pull_request_review_handler import PullRequestReviewHandler
from webhook_server.libs.push_handler import PushHandler
from webhook_server.utils.constants import (
    BUILD_CONTAINER_STR,
    CAN_BE_MERGED_STR,
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
    prepare_log_prefix,
)


class GithubWebhook:
    def __init__(self, hook_data: dict[Any, Any], headers: Headers, logger: logging.Logger) -> None:
        logger.name = "GithubWebhook"
        self.logger = logger
        self.hook_data = hook_data
        self.repository_name: str = hook_data["repository"]["name"]
        self.repository_full_name: str = hook_data["repository"]["full_name"]
        self.parent_committer: str = ""
        self.x_github_delivery: str = headers.get("X-GitHub-Delivery", "")
        self.github_event: str = headers["X-GitHub-Event"]
        self.config = Config(repository=self.repository_name, logger=self.logger)

        # Type annotations for conditionally assigned attributes
        self.repository: Repository
        self.repository_by_github_app: Repository
        self.token: str
        self.api_user: str
        self.current_pull_request_supported_retest: list[str] = []

        if not self.config.repository_data:
            raise RepositoryNotFoundInConfigError(f"Repository {self.repository_name} not found in config file")

        # Get config without .github-webhook-server.yaml data
        self._repo_data_from_config(repository_config={})
        github_api, self.token, self.api_user = get_api_with_highest_rate_limit(
            config=self.config, repository_name=self.repository_name
        )

        if github_api and self.token:
            self.repository = get_github_repo_api(github_app_api=github_api, repository=self.repository_full_name)
            # Once we have a repository, we can get the config from .github-webhook-server.yaml
            local_repository_config = self.config.repository_local_data(
                github_api=github_api, repository_full_name=self.repository_full_name
            )
            # Call _repo_data_from_config() again to update self args from .github-webhook-server.yaml
            self._repo_data_from_config(repository_config=local_repository_config)

        else:
            self.logger.error(f"Failed to get GitHub API and token for repository {self.repository_name}.")
            return

        self.log_prefix: str = self.prepare_log_prefix()

        github_app_api = get_repository_github_app_api(config_=self.config, repository_name=self.repository_full_name)

        if not github_app_api:
            self.logger.error(
                (
                    f"{self.log_prefix} not found by manage-repositories-app, "
                    "make sure the app installed (https://github.com/apps/manage-repositories-app)"
                ),
            )
            return

        self.repository_by_github_app = get_github_repo_api(
            github_app_api=github_app_api, repository=self.repository_full_name
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
        event_log: str = f"Event type: {self.github_event}. event ID: {self.x_github_delivery}"
        self.logger.step(f"{self.log_prefix} Starting webhook processing: {event_log}")  # type: ignore

        if self.github_event == "ping":
            self.logger.step(f"{self.log_prefix} Processing ping event")  # type: ignore
            self.logger.debug(f"{self.log_prefix} {event_log}")
            return {"status": requests.codes.ok, "message": "pong"}

        if self.github_event == "push":
            self.logger.step(f"{self.log_prefix} Processing push event")  # type: ignore
            self.logger.debug(f"{self.log_prefix} {event_log}")
            return await PushHandler(github_webhook=self).process_push_webhook_data()

        if pull_request := await self.get_pull_request():
            self.log_prefix = self.prepare_log_prefix(pull_request=pull_request)
            self.logger.step(f"{self.log_prefix} Processing pull request event: {event_log}")  # type: ignore
            self.logger.debug(f"{self.log_prefix} {event_log}")

            if pull_request.draft:
                self.logger.step(f"{self.log_prefix} Pull request is draft, skipping processing")  # type: ignore
                self.logger.debug(f"{self.log_prefix} Pull request is draft, doing nothing")
                return None

            self.logger.step(f"{self.log_prefix} Initializing pull request data")  # type: ignore
            self.last_commit = await self._get_last_commit(pull_request=pull_request)
            self.parent_committer = pull_request.user.login
            self.last_committer = getattr(self.last_commit.committer, "login", self.parent_committer)

            if self.github_event == "issue_comment":
                self.logger.step(f"{self.log_prefix} Initializing OWNERS file handler for issue comment")  # type: ignore
                owners_file_handler = OwnersFileHandler(github_webhook=self)
                owners_file_handler = await owners_file_handler.initialize(pull_request=pull_request)

                self.logger.step(f"{self.log_prefix} Processing issue comment with IssueCommentHandler")  # type: ignore
                return await IssueCommentHandler(
                    github_webhook=self, owners_file_handler=owners_file_handler
                ).process_comment_webhook_data(pull_request=pull_request)

            elif self.github_event == "pull_request":
                self.logger.step(f"{self.log_prefix} Initializing OWNERS file handler for pull request")  # type: ignore
                owners_file_handler = OwnersFileHandler(github_webhook=self)
                owners_file_handler = await owners_file_handler.initialize(pull_request=pull_request)

                self.logger.step(f"{self.log_prefix} Processing pull request with PullRequestHandler")  # type: ignore
                return await PullRequestHandler(
                    github_webhook=self, owners_file_handler=owners_file_handler
                ).process_pull_request_webhook_data(pull_request=pull_request)

            elif self.github_event == "pull_request_review":
                self.logger.step(f"{self.log_prefix} Initializing OWNERS file handler for pull request review")  # type: ignore
                owners_file_handler = OwnersFileHandler(github_webhook=self)
                owners_file_handler = await owners_file_handler.initialize(pull_request=pull_request)

                self.logger.step(f"{self.log_prefix} Processing pull request review with PullRequestReviewHandler")  # type: ignore
                return await PullRequestReviewHandler(
                    github_webhook=self, owners_file_handler=owners_file_handler
                ).process_pull_request_review_webhook_data(
                    pull_request=pull_request,
                )

            elif self.github_event == "check_run":
                self.logger.step(f"{self.log_prefix} Initializing OWNERS file handler for check run")  # type: ignore
                owners_file_handler = OwnersFileHandler(github_webhook=self)
                owners_file_handler = await owners_file_handler.initialize(pull_request=pull_request)
                self.logger.step(f"{self.log_prefix} Processing check run with CheckRunHandler")  # type: ignore
                if await CheckRunHandler(
                    github_webhook=self, owners_file_handler=owners_file_handler
                ).process_pull_request_check_run_webhook_data(pull_request=pull_request):
                    if self.hook_data["check_run"]["name"] != CAN_BE_MERGED_STR:
                        self.logger.step(f"{self.log_prefix} Checking if pull request can be merged after check run")  # type: ignore
                        return await PullRequestHandler(
                            github_webhook=self, owners_file_handler=owners_file_handler
                        ).check_if_can_be_merged(pull_request=pull_request)

    @property
    def add_api_users_to_auto_verified_and_merged_users(self) -> None:
        apis_and_tokens = get_apis_and_tokes_from_config(config=self.config)
        for _api, _ in apis_and_tokens:
            if _api.rate_limiting[-1] == 60:
                self.logger.warning(
                    f"{self.log_prefix} API has rate limit set to 60 which indicates an invalid token, skipping"
                )
                continue

            self.auto_verified_and_merged_users.append(_api.get_user().login)

    def prepare_log_prefix(self, pull_request: PullRequest | None = None) -> str:
        return prepare_log_prefix(
            event_type=self.github_event,
            delivery_id=self.x_github_delivery,
            repository_name=self.repository_name,
            api_user=self.api_user,
            pr_number=pull_request.number if pull_request else None,
            data_dir=self.config.data_dir,
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
        self.slack_webhook_url: str = self.config.get_value(value="slack-webhook-url", extra_dict=repository_config)

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
        # Load global create_issue_for_new_pr setting as fallback
        global_create_issue_for_new_pr: bool = self.config.get_value(
            value="create-issue-for-new-pr", return_on_none=True
        )
        # Repository-specific setting overrides global setting
        self.create_issue_for_new_pr: bool = self.config.get_value(
            value="create-issue-for-new-pr", return_on_none=global_create_issue_for_new_pr, extra_dict=repository_config
        )

    async def get_pull_request(self, number: int | None = None) -> PullRequest | None:
        if number:
            return await asyncio.to_thread(self.repository.get_pull, number)

        for _number in extract_key_from_dict(key="number", _dict=self.hook_data):
            try:
                return await asyncio.to_thread(self.repository.get_pull, _number)
            except GithubException:
                continue

        commit: dict[str, Any] = self.hook_data.get("commit", {})
        if commit:
            commit_obj = await asyncio.to_thread(self.repository.get_commit, commit["sha"])
            with contextlib.suppress(Exception):
                _pulls = await asyncio.to_thread(commit_obj.get_pulls)
                return _pulls[0]

        if self.github_event == "check_run":
            for _pull_request in await asyncio.to_thread(self.repository.get_pulls, state="open"):
                if _pull_request.head.sha == self.hook_data["check_run"]["head_sha"]:
                    self.logger.debug(
                        f"{self.log_prefix} Found pull request {_pull_request.title} [{_pull_request.number}] for check run {self.hook_data['check_run']['name']}"
                    )
                    return _pull_request

        return None

    async def _get_last_commit(self, pull_request: PullRequest) -> Commit:
        _commits = await asyncio.to_thread(pull_request.get_commits)
        return list(_commits)[-1]

    @staticmethod
    def _comment_with_details(title: str, body: str) -> str:
        return f"""
<details>
<summary>{title}</summary>
    {body}
</details>
        """

    def container_repository_and_tag(
        self, is_merged: bool = False, tag: str = "", pull_request: PullRequest | None = None
    ) -> str | None:
        if not tag:
            if not pull_request:
                return None

            if is_merged:
                pull_request_branch = pull_request.base.ref
                tag = (
                    pull_request_branch
                    if pull_request_branch not in (OTHER_MAIN_BRANCH, "main")
                    else self.container_tag
                )
            else:
                tag = f"pr-{pull_request.number}"

        if tag:
            self.logger.debug(f"{self.log_prefix} container tag is: {tag}")
            return f"{self.container_repository}:{tag}"

        self.logger.error(f"{self.log_prefix} container tag not found")
        return None

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
