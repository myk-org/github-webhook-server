from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any, Callable

from webhook_server.utils.constants import (
    BUILD_AND_PUSH_CONTAINER_STR,
    BUILD_CONTAINER_STR,
    CHERRY_PICK_LABEL_PREFIX,
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


class IssueCommentHandler:
    def __init__(self, github_webhook: Any):
        self.github_webhook = github_webhook
        self.hook_data = self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix = self.github_webhook.log_prefix
        self.repository = self.github_webhook.repository
        self.pull_request = self.github_webhook.pull_request

    def process_comment_webhook_data(self) -> None:
        if comment_action := self.hook_data["action"] in ("edited", "deleted"):
            self.logger.debug(f"{self.log_prefix} Not processing comment. action is {comment_action}")
            return

        self.logger.info(f"{self.log_prefix} Processing issue {self.hook_data['issue']['number']}")

        body: str = self.hook_data["comment"]["body"]

        if self.github_webhook.issue_url_for_welcome_msg in body:
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
            self.github_webhook.assign_reviewers()

        elif _command == COMMAND_CHECK_CAN_MERGE_STR:
            self.github_webhook.check_if_can_be_merged()

        elif _command == COMMAND_CHERRY_PICK_STR:
            self.process_cherry_pick_command(command_args=_args, reviewed_user=reviewed_user)

        elif _command == COMMAND_RETEST_STR:
            self.process_retest_command(command_args=_args, reviewed_user=reviewed_user)

        elif _command == BUILD_AND_PUSH_CONTAINER_STR:
            if self.github_webhook.build_and_push_container:
                self.github_webhook._run_build_container(
                    push=True, set_check=False, command_args=_args, reviewed_user=reviewed_user
                )
            else:
                msg = f"No {BUILD_AND_PUSH_CONTAINER_STR} configured for this repository"
                error_msg = f"{self.log_prefix} {msg}"
                self.logger.debug(error_msg)
                self.pull_request.create_issue_comment(msg)

        elif _command == WIP_STR:
            wip_for_title: str = f"{WIP_STR.upper()}:"
            if remove:
                self.github_webhook._remove_label(label=WIP_STR)
                self.pull_request.edit(title=self.pull_request.title.replace(wip_for_title, ""))
            else:
                self.github_webhook._add_label(label=WIP_STR)
                self.pull_request.edit(title=f"{wip_for_title} {self.pull_request.title}")

        elif _command == HOLD_LABEL_STR:
            if reviewed_user not in self.github_webhook.all_pull_request_approvers:
                self.pull_request.create_issue_comment(
                    f"{reviewed_user} is not part of the approver, only approvers can mark pull request with hold"
                )
            else:
                if remove:
                    self.github_webhook._remove_label(label=HOLD_LABEL_STR)
                else:
                    self.github_webhook._add_label(label=HOLD_LABEL_STR)

                self.github_webhook.check_if_can_be_merged()

        elif _command == VERIFIED_LABEL_STR:
            if remove:
                self.github_webhook._remove_label(label=VERIFIED_LABEL_STR)
                self.github_webhook.set_verify_check_queued()
            else:
                self.github_webhook._add_label(label=VERIFIED_LABEL_STR)
                self.github_webhook.set_verify_check_success()

        else:
            self.github_webhook.label_by_user_comment(
                user_requested_label=_command,
                remove=remove,
                reviewed_user=reviewed_user,
            )

    def create_comment_reaction(self, issue_comment_id: int, reaction: str) -> None:
        _comment = self.pull_request.get_issue_comment(issue_comment_id)
        _comment.create_reaction(reaction)

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

    def process_cherry_pick_command(self, command_args: str, reviewed_user: str) -> None:
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
                    self.github_webhook._add_label(label=_cp_label)
            else:
                for _exits_target_branch in _exits_target_branches:
                    self.github_webhook.cherry_pick(
                        target_branch=_exits_target_branch,
                        reviewed_user=reviewed_user,
                    )

    def process_retest_command(self, command_args: str, reviewed_user: str) -> None:
        if not self.github_webhook._is_user_valid_to_run_commands(reviewed_user=reviewed_user):
            return

        _target_tests: list[str] = command_args.split()
        _not_supported_retests: list[str] = []
        _supported_retests: list[str] = []
        _retests_to_func_map: dict[str, Callable] = {
            TOX_STR: self.github_webhook._run_tox,
            PRE_COMMIT_STR: self.github_webhook._run_pre_commit,
            BUILD_CONTAINER_STR: self.github_webhook._run_build_container,
            PYTHON_MODULE_INSTALL_STR: self.github_webhook._run_install_python_module,
            CONVENTIONAL_TITLE_STR: self.github_webhook._run_conventional_title_check,
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
                _supported_retests = self.github_webhook.current_pull_request_supported_retest

        else:
            for _test in _target_tests:
                if _test in self.github_webhook.current_pull_request_supported_retest:
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
