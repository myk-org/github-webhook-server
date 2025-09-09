from __future__ import annotations

import asyncio
from asyncio import Task
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Union

from github.PullRequest import PullRequest
from github.Repository import Repository

from webhook_server.libs.check_run_handler import CheckRunHandler
from webhook_server.libs.labels_handler import LabelsHandler
from webhook_server.libs.owners_files_handler import OwnersFileHandler
from webhook_server.libs.pull_request_handler import PullRequestHandler
from webhook_server.libs.runner_handler import RunnerHandler
from webhook_server.utils.constants import (
    AUTOMERGE_LABEL_STR,
    BUILD_AND_PUSH_CONTAINER_STR,
    BUILD_CONTAINER_STR,
    CHERRY_PICK_LABEL_PREFIX,
    COMMAND_ADD_ALLOWED_USER_STR,
    COMMAND_ASSIGN_REVIEWER_STR,
    COMMAND_ASSIGN_REVIEWERS_STR,
    COMMAND_CHECK_CAN_MERGE_STR,
    COMMAND_CHERRY_PICK_STR,
    COMMAND_RETEST_STR,
    CONVENTIONAL_TITLE_STR,
    HOLD_LABEL_STR,
    PRE_COMMIT_STR,
    PYTHON_MODULE_INSTALL_STR,
    REACTIONS,
    TOX_STR,
    USER_LABELS_DICT,
    VERIFIED_LABEL_STR,
    WIP_STR,
)

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook


class IssueCommentHandler:
    def __init__(self, github_webhook: "GithubWebhook", owners_file_handler: OwnersFileHandler):
        self.github_webhook = github_webhook
        self.owners_file_handler = owners_file_handler

        self.hook_data = self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix: str = self.github_webhook.log_prefix
        self.repository: Repository = self.github_webhook.repository
        self.labels_handler = LabelsHandler(
            github_webhook=self.github_webhook, owners_file_handler=self.owners_file_handler
        )
        self.check_run_handler = CheckRunHandler(github_webhook=self.github_webhook)
        self.pull_request_handler = PullRequestHandler(
            github_webhook=self.github_webhook, owners_file_handler=self.owners_file_handler
        )
        self.runner_handler = RunnerHandler(
            github_webhook=self.github_webhook, owners_file_handler=self.owners_file_handler
        )

    async def process_comment_webhook_data(self, pull_request: PullRequest) -> None:
        comment_action = self.hook_data["action"]
        self.logger.step(f"{self.log_prefix} Starting issue comment processing: action={comment_action}")  # type: ignore

        if comment_action in ("edited", "deleted"):
            self.logger.step(f"{self.log_prefix} Skipping comment processing: action is {comment_action}")  # type: ignore
            self.logger.debug(f"{self.log_prefix} Not processing comment. action is {comment_action}")
            return

        self.logger.step(f"{self.log_prefix} Processing issue comment for issue {self.hook_data['issue']['number']}")  # type: ignore
        self.logger.info(f"{self.log_prefix} Processing issue {self.hook_data['issue']['number']}")

        body: str = self.hook_data["comment"]["body"]

        if self.github_webhook.issue_url_for_welcome_msg in body:
            self.logger.debug(f"{self.log_prefix} Welcome message found in issue {pull_request.title}. Not processing")
            return

        _user_commands: list[str] = [_cmd.strip("/") for _cmd in body.strip().splitlines() if _cmd.startswith("/")]

        if _user_commands:
            self.logger.step(f"{self.log_prefix} Found {len(_user_commands)} user commands: {_user_commands}")  # type: ignore

        user_login: str = self.hook_data["sender"]["login"]
        for user_command in _user_commands:
            self.logger.step(f"{self.log_prefix} Executing user command: /{user_command} by {user_login}")  # type: ignore
            await self.user_commands(
                pull_request=pull_request,
                command=user_command,
                reviewed_user=user_login,
                issue_comment_id=self.hook_data["comment"]["id"],
            )

    async def user_commands(
        self, pull_request: PullRequest, command: str, reviewed_user: str, issue_comment_id: int
    ) -> None:
        available_commands: list[str] = [
            COMMAND_RETEST_STR,
            COMMAND_CHERRY_PICK_STR,
            COMMAND_ASSIGN_REVIEWERS_STR,
            COMMAND_CHECK_CAN_MERGE_STR,
            BUILD_AND_PUSH_CONTAINER_STR,
            COMMAND_ASSIGN_REVIEWER_STR,
            COMMAND_ADD_ALLOWED_USER_STR,
        ]

        command_and_args: list[str] = command.split(" ", 1)
        _command = command_and_args[0]
        _args: str = command_and_args[1] if len(command_and_args) > 1 else ""

        self.logger.debug(
            f"{self.log_prefix} User: {reviewed_user}, Command: {_command}, Command args: {_args or 'None'}"
        )
        if _command not in available_commands + list(USER_LABELS_DICT.keys()):
            self.logger.debug(f"{self.log_prefix} Command {command} is not supported.")
            return

        self.logger.info(f"{self.log_prefix} Processing label/user command {command} by user {reviewed_user}")
        self.logger.debug(f"{self.log_prefix} Command {command} is supported.")

        if remove := len(command_and_args) > 1 and _args == "cancel":
            self.logger.debug(f"{self.log_prefix} User requested 'cancel' for command {_command}")

        if (
            _command
            in (
                COMMAND_RETEST_STR,
                COMMAND_ASSIGN_REVIEWER_STR,
                COMMAND_ADD_ALLOWED_USER_STR,
            )
            and not _args
        ):
            missing_command_arg_comment_msg: str = f"{_command} requires an argument"
            error_msg: str = f"{self.log_prefix} {missing_command_arg_comment_msg}"
            self.logger.debug(error_msg)
            await asyncio.to_thread(pull_request.create_issue_comment, body=missing_command_arg_comment_msg)
            return

        if _command == AUTOMERGE_LABEL_STR:
            if reviewed_user not in (
                await self.owners_file_handler.get_all_repository_maintainers()
                + self.owners_file_handler.all_repository_approvers
            ):
                msg = "Only maintainers or approvers can set pull request to auto-merge"
                self.logger.debug(f"{self.log_prefix} {msg}")
                await asyncio.to_thread(pull_request.create_issue_comment, body=msg)
                return

            await self.labels_handler._add_label(pull_request=pull_request, label=AUTOMERGE_LABEL_STR)

        await self.create_comment_reaction(
            pull_request=pull_request, issue_comment_id=issue_comment_id, reaction=REACTIONS.ok
        )
        self.logger.debug(f"{self.log_prefix} Added reaction to comment.")

        if _command == COMMAND_ASSIGN_REVIEWER_STR:
            await self._add_reviewer_by_user_comment(pull_request=pull_request, reviewer=_args)

        elif _command == COMMAND_ADD_ALLOWED_USER_STR:
            await asyncio.to_thread(pull_request.create_issue_comment, body=f"{_args} is now allowed to run commands")

        elif _command == COMMAND_ASSIGN_REVIEWERS_STR:
            await self.owners_file_handler.assign_reviewers(pull_request=pull_request)

        elif _command == COMMAND_CHECK_CAN_MERGE_STR:
            await self.pull_request_handler.check_if_can_be_merged(pull_request=pull_request)

        elif _command == COMMAND_CHERRY_PICK_STR:
            await self.process_cherry_pick_command(
                pull_request=pull_request, command_args=_args, reviewed_user=reviewed_user
            )

        elif _command == COMMAND_RETEST_STR:
            await self.process_retest_command(
                pull_request=pull_request, command_args=_args, reviewed_user=reviewed_user
            )

        elif _command == BUILD_AND_PUSH_CONTAINER_STR:
            if self.github_webhook.build_and_push_container:
                await self.runner_handler.run_build_container(
                    push=True,
                    set_check=False,
                    command_args=_args,
                    reviewed_user=reviewed_user,
                    pull_request=pull_request,
                )
            else:
                msg = f"No {BUILD_AND_PUSH_CONTAINER_STR} configured for this repository"
                error_msg = f"{self.log_prefix} {msg}"
                self.logger.debug(error_msg)
                await asyncio.to_thread(pull_request.create_issue_comment, msg)

        elif _command == WIP_STR:
            wip_for_title: str = f"{WIP_STR.upper()}:"
            if remove:
                await self.labels_handler._remove_label(pull_request=pull_request, label=WIP_STR)
                await asyncio.to_thread(pull_request.edit, title=pull_request.title.replace(wip_for_title, ""))
            else:
                await self.labels_handler._add_label(pull_request=pull_request, label=WIP_STR)
                await asyncio.to_thread(pull_request.edit, title=f"{wip_for_title} {pull_request.title}")

        elif _command == HOLD_LABEL_STR:
            if reviewed_user not in self.owners_file_handler.all_pull_request_approvers:
                self.logger.debug(
                    f"{self.log_prefix} {reviewed_user} is not an approver, not adding {HOLD_LABEL_STR} label"
                )
                await asyncio.to_thread(
                    pull_request.create_issue_comment,
                    f"{reviewed_user} is not part of the approver, only approvers can mark pull request with hold",
                )
            else:
                if remove:
                    await self.labels_handler._remove_label(pull_request=pull_request, label=HOLD_LABEL_STR)
                else:
                    await self.labels_handler._add_label(pull_request=pull_request, label=HOLD_LABEL_STR)

                await self.pull_request_handler.check_if_can_be_merged(pull_request=pull_request)

        elif _command == VERIFIED_LABEL_STR:
            if remove:
                await self.labels_handler._remove_label(pull_request=pull_request, label=VERIFIED_LABEL_STR)
                await self.check_run_handler.set_verify_check_queued()
            else:
                await self.labels_handler._add_label(pull_request=pull_request, label=VERIFIED_LABEL_STR)
                await self.check_run_handler.set_verify_check_success()

        elif _command != AUTOMERGE_LABEL_STR:
            await self.labels_handler.label_by_user_comment(
                pull_request=pull_request,
                user_requested_label=_command,
                remove=remove,
                reviewed_user=reviewed_user,
            )

    async def create_comment_reaction(self, pull_request: PullRequest, issue_comment_id: int, reaction: str) -> None:
        _comment = await asyncio.to_thread(pull_request.get_issue_comment, issue_comment_id)
        await asyncio.to_thread(_comment.create_reaction, reaction)

    async def _add_reviewer_by_user_comment(self, pull_request: PullRequest, reviewer: str) -> None:
        reviewer = reviewer.strip("@")
        self.logger.info(f"{self.log_prefix} Adding reviewer {reviewer} by user comment")
        repo_contributors = list(await asyncio.to_thread(self.repository.get_contributors))
        self.logger.debug(f"Repo contributors are: {repo_contributors}")

        for contributer in repo_contributors:
            if contributer.login == reviewer:
                await asyncio.to_thread(pull_request.create_review_request, [reviewer])
                return

        _err = f"not adding reviewer {reviewer} by user comment, {reviewer} is not part of contributers"
        self.logger.debug(f"{self.log_prefix} {_err}")
        await asyncio.to_thread(pull_request.create_issue_comment, _err)

    async def process_cherry_pick_command(
        self, pull_request: PullRequest, command_args: str, reviewed_user: str
    ) -> None:
        _target_branches: list[str] = command_args.split()
        _exits_target_branches: set[str] = set()
        _non_exits_target_branches_msg: str = ""
        self.logger.debug(f"{self.log_prefix} Processing cherry pick for branches {_target_branches}")

        for _target_branch in _target_branches:
            try:
                await asyncio.to_thread(self.repository.get_branch, _target_branch)
                _exits_target_branches.add(_target_branch)
            except Exception:
                _non_exits_target_branches_msg += f"Target branch `{_target_branch}` does not exist\n"
        self.logger.debug(
            f"{self.log_prefix} Found target branches {_exits_target_branches} and not found {_non_exits_target_branches_msg}"
        )

        if _non_exits_target_branches_msg:
            self.logger.info(f"{self.log_prefix} {_non_exits_target_branches_msg}")
            await asyncio.to_thread(pull_request.create_issue_comment, _non_exits_target_branches_msg)

        if _exits_target_branches:
            if not await asyncio.to_thread(pull_request.is_merged):
                cp_labels: list[str] = [
                    f"{CHERRY_PICK_LABEL_PREFIX}{_target_branch}" for _target_branch in _exits_target_branches
                ]
                info_msg: str = f"""
Cherry-pick requested for PR: `{pull_request.title}` by user `{reviewed_user}`
Adding label/s `{" ".join([_cp_label for _cp_label in cp_labels])}` for automatic cheery-pick once the PR is merged
"""
                self.logger.info(f"{self.log_prefix} {info_msg}")
                await asyncio.to_thread(pull_request.create_issue_comment, info_msg)
                for _cp_label in cp_labels:
                    await self.labels_handler._add_label(pull_request=pull_request, label=_cp_label)
            else:
                for _exits_target_branch in _exits_target_branches:
                    await self.runner_handler.cherry_pick(
                        pull_request=pull_request,
                        target_branch=_exits_target_branch,
                        reviewed_user=reviewed_user,
                    )

    async def process_retest_command(
        self, pull_request: PullRequest, command_args: str, reviewed_user: str, automerge: bool = False
    ) -> None:
        if not await self.owners_file_handler.is_user_valid_to_run_commands(
            pull_request=pull_request, reviewed_user=reviewed_user
        ):
            return

        _target_tests: list[str] = command_args.split()
        self.logger.debug(f"{self.log_prefix} Target tests for re-test: {_target_tests}")
        _not_supported_retests: list[str] = []
        _supported_retests: list[str] = []
        _retests_to_func_map: dict[str, Callable] = {
            TOX_STR: self.runner_handler.run_tox,
            PRE_COMMIT_STR: self.runner_handler.run_pre_commit,
            BUILD_CONTAINER_STR: self.runner_handler.run_build_container,
            PYTHON_MODULE_INSTALL_STR: self.runner_handler.run_install_python_module,
            CONVENTIONAL_TITLE_STR: self.runner_handler.run_conventional_title_check,
        }
        self.logger.debug(f"Retest map is {_retests_to_func_map}")

        if not _target_tests:
            msg = "No test defined to retest"
            error_msg = f"{self.log_prefix} {msg}."
            self.logger.debug(error_msg)
            await asyncio.to_thread(pull_request.create_issue_comment, msg)
            return

        if "all" in command_args:
            if len(_target_tests) > 1:
                msg = "Invalid command. `all` cannot be used with other tests"
                error_msg = f"{self.log_prefix} {msg}."
                self.logger.debug(error_msg)
                await asyncio.to_thread(pull_request.create_issue_comment, msg)
                return

            else:
                _supported_retests = self.github_webhook.current_pull_request_supported_retest
                self.logger.debug(f"{self.log_prefix} running all supported retests: {_supported_retests}")

        else:
            for _test in _target_tests:
                if _test in self.github_webhook.current_pull_request_supported_retest:
                    _supported_retests.append(_test)

                else:
                    _not_supported_retests.append(_test)
        self.logger.debug(f"Supported retests are {_supported_retests}")
        self.logger.debug(f"Not supported retests are {_not_supported_retests}")

        if _not_supported_retests:
            msg = f"No {' '.join(_not_supported_retests)} configured for this repository"
            error_msg = f"{self.log_prefix} {msg}."
            self.logger.debug(error_msg)
            await asyncio.to_thread(pull_request.create_issue_comment, msg)

        if _supported_retests:
            tasks: list[Union[Coroutine[Any, Any, Any], Task[Any]]] = []
            for _test in _supported_retests:
                self.logger.debug(f"{self.log_prefix} running retest {_test}")
                task = asyncio.create_task(_retests_to_func_map[_test](pull_request=pull_request))
                tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    self.logger.error(f"{self.log_prefix} Async task failed: {result}")

        if automerge:
            await self.labels_handler._add_label(pull_request=pull_request, label=AUTOMERGE_LABEL_STR)
