from __future__ import annotations

import asyncio
from asyncio import Task
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from github.GithubException import GithubException
from github.Repository import Repository

from webhook_server.libs.graphql.graphql_wrappers import PullRequestWrapper
from webhook_server.libs.handlers.check_run_handler import CheckRunHandler
from webhook_server.libs.handlers.labels_handler import LabelsHandler
from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
from webhook_server.libs.handlers.pull_request_handler import PullRequestHandler
from webhook_server.libs.handlers.runner_handler import RunnerHandler
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
from webhook_server.utils.helpers import format_task_fields

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook


class IssueCommentHandler:
    def __init__(self, github_webhook: GithubWebhook, owners_file_handler: OwnersFileHandler):
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

    @property
    def _owner_and_repo(self) -> tuple[str, str]:
        """Split repository full name into owner and repo name.

        Returns:
            Tuple of (owner, repo_name)
        """
        full_name = self.repository.full_name
        # Handle string split
        if isinstance(full_name, str) and "/" in full_name:
            owner, repo_name = full_name.split("/", 1)
            return owner, repo_name
        # Handle mock or invalid full_name - return default values and log warning
        self.logger.warning(f"Invalid repository full_name format: {full_name}, using defaults")
        return "owner", "repo"

    async def process_comment_webhook_data(self, pull_request: PullRequestWrapper) -> None:
        comment_action = self.hook_data["action"]
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('issue_comment', 'pr_management', 'started')} "
            f"Starting issue comment processing: action={comment_action}",
        )

        if comment_action in ("edited", "deleted"):
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('issue_comment', 'pr_management', 'processing')} "
                f"Skipping comment processing: action is {comment_action}",
            )
            self.logger.debug(f"{self.log_prefix} Not processing comment. action is {comment_action}")
            return

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('issue_comment', 'pr_management', 'processing')} "
            f"Processing issue comment for issue {self.hook_data['issue']['number']}",
        )
        self.logger.info(f"{self.log_prefix} Processing issue {self.hook_data['issue']['number']}")

        body: str = self.hook_data["comment"]["body"]

        if self.github_webhook.issue_url_for_welcome_msg in body:
            self.logger.debug(f"{self.log_prefix} Welcome message found in issue {pull_request.title}. Not processing")
            return

        _user_commands: list[str] = [_cmd.strip("/") for _cmd in body.strip().splitlines() if _cmd.startswith("/")]

        if _user_commands:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('issue_comment', 'pr_management', 'processing')} "
                f"Found {len(_user_commands)} user commands: {_user_commands}",
            )

        user_login: str = self.hook_data["sender"]["login"]
        for user_command in _user_commands:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('issue_comment', 'pr_management', 'processing')} "
                f"Executing user command: /{user_command} by {user_login}",
            )
            await self.user_commands(
                pull_request=pull_request,
                command=user_command,
                reviewed_user=user_login,
                issue_comment_id=self.hook_data["comment"]["id"],
            )

    async def user_commands(
        self, pull_request: PullRequestWrapper, command: str, reviewed_user: str, issue_comment_id: int
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
            owner, repo = self._owner_and_repo
            await self.github_webhook.unified_api.add_pr_comment(
                owner, repo, pull_request, missing_command_arg_comment_msg
            )
            return

        if _command == AUTOMERGE_LABEL_STR:
            if reviewed_user not in (
                await self.owners_file_handler.get_all_repository_maintainers()
                + self.owners_file_handler.all_repository_approvers
            ):
                msg = "Only maintainers or approvers can set pull request to auto-merge"
                self.logger.debug(f"{self.log_prefix} {msg}")
                owner, repo = self._owner_and_repo
                await self.github_webhook.unified_api.add_pr_comment(owner, repo, pull_request, msg)
                return

            await self.labels_handler._add_label(pull_request=pull_request, label=AUTOMERGE_LABEL_STR)

        await self.create_comment_reaction(
            pull_request=pull_request, issue_comment_id=issue_comment_id, reaction=REACTIONS.ok
        )
        self.logger.debug(f"{self.log_prefix} Added reaction to comment.")

        if _command == COMMAND_ASSIGN_REVIEWER_STR:
            await self._add_reviewer_by_user_comment(pull_request=pull_request, reviewer=_args)

        elif _command == COMMAND_ADD_ALLOWED_USER_STR:
            owner, repo = self._owner_and_repo
            await self.github_webhook.unified_api.add_pr_comment(
                owner, repo, pull_request, f"{_args} is now allowed to run commands"
            )

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
                owner, repo = self._owner_and_repo
                await self.github_webhook.unified_api.add_pr_comment(owner, repo, pull_request, msg)

        elif _command == WIP_STR:
            wip_for_title: str = f"{WIP_STR.upper()}:"
            if remove:
                await self.labels_handler._remove_label(pull_request=pull_request, label=WIP_STR)
                await self.github_webhook.unified_api.update_pr_title(
                    pull_request, pull_request.title.replace(wip_for_title, "")
                )
            else:
                await self.labels_handler._add_label(pull_request=pull_request, label=WIP_STR)
                await self.github_webhook.unified_api.update_pr_title(
                    pull_request, f"{wip_for_title} {pull_request.title}"
                )

        elif _command == HOLD_LABEL_STR:
            if reviewed_user not in self.owners_file_handler.all_pull_request_approvers:
                self.logger.debug(
                    f"{self.log_prefix} {reviewed_user} is not an approver, not adding {HOLD_LABEL_STR} label"
                )
                owner, repo = self._owner_and_repo
                await self.github_webhook.unified_api.create_issue_comment(
                    owner,
                    repo,
                    pull_request.number,
                    f"{reviewed_user} is not part of the approvers, only approvers can mark pull request with hold",
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

    async def create_comment_reaction(
        self, pull_request: PullRequestWrapper, issue_comment_id: int, reaction: str
    ) -> None:
        owner, repo_name = self._owner_and_repo
        try:
            _comment = await self.github_webhook.unified_api.get_issue_comment(
                owner, repo_name, pull_request.number, issue_comment_id
            )
            await self.github_webhook.unified_api.create_reaction(_comment, reaction)
        except GithubException as ex:
            # Handle deleted or inaccessible comments (404 or "not found" message)
            if (hasattr(ex, "status") and ex.status == 404) or "not found" in str(ex).lower():
                self.logger.info(
                    f"{self.log_prefix} Comment {issue_comment_id} not found "
                    f"(deleted or inaccessible), skipping reaction"
                )
                return
            # Re-raise other GitHub exceptions
            raise

    async def _add_reviewer_by_user_comment(self, pull_request: PullRequestWrapper, reviewer: str) -> None:
        reviewer = reviewer.strip("@")
        self.logger.info(f"{self.log_prefix} Adding reviewer {reviewer} by user comment")
        owner, repo_name = self._owner_and_repo
        repo_contributors = await self.github_webhook.unified_api.get_contributors(owner, repo_name)
        self.logger.debug(f"Repo contributors are: {repo_contributors}")

        for contributor in repo_contributors:
            # GitHub logins are case-insensitive, so match accordingly
            if contributor["login"].lower() == reviewer.lower():
                await self.github_webhook.unified_api.request_pr_reviews(pull_request, [reviewer])
                return

        _err = f"not adding reviewer {reviewer} by user comment, {reviewer} is not part of contributors"
        self.logger.debug(f"{self.log_prefix} {_err}")
        owner, repo = self._owner_and_repo
        await self.github_webhook.unified_api.add_pr_comment(owner, repo, pull_request, _err)

    async def process_cherry_pick_command(
        self, pull_request: PullRequestWrapper, command_args: str, reviewed_user: str
    ) -> None:
        _target_branches: list[str] = command_args.split()
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('issue_comment', 'pr_management', 'started')} "
            f"Processing cherry-pick command for branches: {_target_branches}"
        )
        _exits_target_branches: set[str] = set()
        _non_exits_target_branches_msg: str = ""
        self.logger.debug(f"{self.log_prefix} Processing cherry pick for branches {_target_branches}")

        for _target_branch in _target_branches:
            owner, repo_name = self._owner_and_repo
            branch_exists = await self.github_webhook.unified_api.get_branch(owner, repo_name, _target_branch)

            if branch_exists:
                _exits_target_branches.add(_target_branch)
            else:
                _non_exits_target_branches_msg += f"Target branch `{_target_branch}` does not exist\n"
        self.logger.debug(
            f"{self.log_prefix} Found target branches {_exits_target_branches} and not found "
            f"{_non_exits_target_branches_msg}"
        )

        if _non_exits_target_branches_msg:
            self.logger.info(f"{self.log_prefix} {_non_exits_target_branches_msg}")
            owner, repo = self._owner_and_repo
            await self.github_webhook.unified_api.add_pr_comment(
                owner, repo, pull_request, _non_exits_target_branches_msg
            )

        if _exits_target_branches:
            # Optimization: Use webhook data directly - merged status is immutable once set
            is_merged = pull_request.merged
            if not is_merged:
                cp_labels: list[str] = [
                    f"{CHERRY_PICK_LABEL_PREFIX}{_target_branch}" for _target_branch in _exits_target_branches
                ]
                info_msg: str = f"""
Cherry-pick requested for PR: `{pull_request.title}` by user `{reviewed_user}`
Adding label/s `{" ".join([_cp_label for _cp_label in cp_labels])}` for automatic cherry-pick once the PR is merged
"""
                self.logger.info(f"{self.log_prefix} {info_msg}")
                owner, repo = self._owner_and_repo
                await self.github_webhook.unified_api.add_pr_comment(owner, repo, pull_request, info_msg)
                for _cp_label in cp_labels:
                    await self.labels_handler._add_label(pull_request=pull_request, label=_cp_label)
            else:
                for _exits_target_branch in _exits_target_branches:
                    await self.runner_handler.cherry_pick(
                        pull_request=pull_request,
                        target_branch=_exits_target_branch,
                        reviewed_user=reviewed_user,
                    )

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('issue_comment', 'pr_management', 'completed')} "
            f"Cherry-pick command processing completed"
        )

    async def process_retest_command(
        self,
        pull_request: PullRequestWrapper,
        command_args: str,
        reviewed_user: str,
        automerge: bool = False,
    ) -> None:
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('issue_comment', 'pr_management', 'started')} "
            f"Processing retest command: {command_args}"
        )
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
            owner, repo = self._owner_and_repo
            await self.github_webhook.unified_api.add_pr_comment(owner, repo, pull_request, msg)
            return

        if "all" in command_args:
            if len(_target_tests) > 1:
                msg = "Invalid command. `all` cannot be used with other tests"
                error_msg = f"{self.log_prefix} {msg}."
                self.logger.debug(error_msg)
                owner, repo = self._owner_and_repo
                await self.github_webhook.unified_api.add_pr_comment(owner, repo, pull_request, msg)
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
            owner, repo = self._owner_and_repo
            await self.github_webhook.unified_api.add_pr_comment(owner, repo, pull_request, msg)

        if _supported_retests:
            tasks: list[Coroutine[Any, Any, Any] | Task[Any]] = []
            for _test in _supported_retests:
                self.logger.debug(f"{self.log_prefix} running retest {_test}")
                task = asyncio.create_task(_retests_to_func_map[_test](pull_request=pull_request))
                tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    exc_info = (type(result), result, result.__traceback__)
                    self.logger.error(
                        f"{self.log_prefix} Async task failed: {result}",
                        exc_info=exc_info,
                    )

        if automerge:
            await self.labels_handler._add_label(pull_request=pull_request, label=AUTOMERGE_LABEL_STR)

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('issue_comment', 'pr_management', 'completed')} "
            f"Retest command processing completed"
        )
