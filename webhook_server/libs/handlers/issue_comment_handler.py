from __future__ import annotations

import asyncio
import traceback
from asyncio import Task
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any

from github.PullRequest import PullRequest
from github.Repository import Repository

from webhook_server.libs.handlers.check_run_handler import CheckRunHandler
from webhook_server.libs.handlers.labels_handler import LabelsHandler
from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
from webhook_server.libs.handlers.pull_request_handler import PullRequestHandler
from webhook_server.libs.handlers.runner_handler import RunnerHandler
from webhook_server.utils.constants import (
    AUTOMERGE_LABEL_STR,
    BUILD_AND_PUSH_CONTAINER_STR,
    CHERRY_PICK_LABEL_PREFIX,
    COMMAND_ADD_ALLOWED_USER_STR,
    COMMAND_ASSIGN_REVIEWER_STR,
    COMMAND_ASSIGN_REVIEWERS_STR,
    COMMAND_CHECK_CAN_MERGE_STR,
    COMMAND_CHERRY_PICK_STR,
    COMMAND_REGENERATE_WELCOME_STR,
    COMMAND_REPROCESS_STR,
    COMMAND_RETEST_STR,
    HOLD_LABEL_STR,
    REACTIONS,
    USER_LABELS_DICT,
    VERIFIED_LABEL_STR,
    WIP_STR,
)

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook
    from webhook_server.utils.context import WebhookContext


class IssueCommentHandler:
    def __init__(self, github_webhook: GithubWebhook, owners_file_handler: OwnersFileHandler):
        self.github_webhook = github_webhook
        self.owners_file_handler = owners_file_handler

        self.hook_data = self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix: str = self.github_webhook.log_prefix
        self.repository: Repository = self.github_webhook.repository
        self.ctx: WebhookContext | None = github_webhook.ctx
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
        if self.ctx:
            self.ctx.start_step("issue_comment_handler")

        try:
            comment_action = self.hook_data["action"]

            if comment_action in ("edited", "deleted"):
                self.logger.debug(f"{self.log_prefix} Not processing comment. action is {comment_action}")
                if self.ctx:
                    self.ctx.complete_step("issue_comment_handler")
                return

            self.logger.info(f"{self.log_prefix} Processing issue {self.hook_data['issue']['number']}")

            body: str = self.hook_data["comment"]["body"]

            if self.github_webhook.issue_url_for_welcome_msg in body:
                self.logger.debug(
                    f"{self.log_prefix} Welcome message found in issue {pull_request.title}. Not processing"
                )
                if self.ctx:
                    self.ctx.complete_step("issue_comment_handler")
                return

            _user_commands: list[str] = [_cmd.strip("/") for _cmd in body.strip().splitlines() if _cmd.startswith("/")]

            user_login: str = self.hook_data["sender"]["login"]

            # Execute all commands in parallel
            if _user_commands:
                # Cache draft status once to avoid repeated API calls
                is_draft = await asyncio.to_thread(lambda: pull_request.draft)

                tasks: list[Coroutine[Any, Any, Any] | Task[Any]] = []
                for user_command in _user_commands:
                    task = asyncio.create_task(
                        self.user_commands(
                            pull_request=pull_request,
                            command=user_command,
                            reviewed_user=user_login,
                            issue_comment_id=self.hook_data["comment"]["id"],
                            is_draft=is_draft,
                        )
                    )
                    tasks.append(task)

                # Execute all commands concurrently
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Check if any command failed
                failed_commands: list[tuple[str, Exception]] = []
                for idx, result in enumerate(results):
                    user_command = _user_commands[idx]
                    if isinstance(result, Exception):
                        # Re-raise CancelledError immediately to allow cancellation to propagate
                        if isinstance(result, asyncio.CancelledError):
                            raise result
                        self.logger.error(f"{self.log_prefix} Command execution failed: /{user_command} - {result}")
                        failed_commands.append((user_command, result))

                # If any command failed, mark step as failed
                if failed_commands:
                    # Use first exception for context failure
                    first_failed_command, first_exception = failed_commands[0]
                    error_msg = f"Command /{first_failed_command} failed: {first_exception}"
                    if self.ctx:
                        # Format traceback from the actual exception object
                        tb_lines = traceback.format_exception(
                            type(first_exception), first_exception, first_exception.__traceback__
                        )
                        tb_str = "".join(tb_lines)
                        self.ctx.fail_step("issue_comment_handler", first_exception, tb_str)
                    raise RuntimeError(error_msg) from first_exception

            if self.ctx:
                self.ctx.complete_step("issue_comment_handler")

        except asyncio.CancelledError:
            # Always let cancellation propagate
            raise
        except Exception as ex:
            # If step not already failed, mark it as failed
            if self.ctx and not self.ctx.workflow_steps.get("issue_comment_handler", {}).get("status") == "failed":
                self.ctx.fail_step("issue_comment_handler", ex, traceback.format_exc())
            raise

    async def user_commands(
        self, pull_request: PullRequest, command: str, reviewed_user: str, issue_comment_id: int, *, is_draft: bool
    ) -> None:
        available_commands: list[str] = [
            COMMAND_RETEST_STR,
            COMMAND_REPROCESS_STR,
            COMMAND_CHERRY_PICK_STR,
            COMMAND_ASSIGN_REVIEWERS_STR,
            COMMAND_CHECK_CAN_MERGE_STR,
            BUILD_AND_PUSH_CONTAINER_STR,
            COMMAND_ASSIGN_REVIEWER_STR,
            COMMAND_ADD_ALLOWED_USER_STR,
            COMMAND_REGENERATE_WELCOME_STR,
        ]

        command_and_args: list[str] = command.split(" ", 1)
        _command = command_and_args[0]
        _args: str = command_and_args[1] if len(command_and_args) > 1 else ""

        # Check if command is allowed on draft PRs
        if is_draft:
            allow_commands_on_draft = self.github_webhook.config.get_value("allow-commands-on-draft-prs")
            if not isinstance(allow_commands_on_draft, list):
                self.logger.debug(
                    f"{self.log_prefix} Command {_command} blocked: "
                    "draft PR and allow-commands-on-draft-prs not configured"
                )
                return
            # Empty list means all commands allowed; non-empty list means only those commands
            if len(allow_commands_on_draft) > 0:
                # Sanitize: ensure all entries are strings for safe join and comparison
                allow_commands_on_draft = [str(cmd) for cmd in allow_commands_on_draft]
                if _command not in allow_commands_on_draft:
                    self.logger.debug(
                        f"{self.log_prefix} Command {_command} is not allowed on draft PRs. "
                        f"Allowed commands: {allow_commands_on_draft}"
                    )
                    await asyncio.to_thread(
                        pull_request.create_issue_comment,
                        f"Command `/{_command}` is not allowed on draft PRs.\n"
                        f"Allowed commands on draft PRs: {', '.join(allow_commands_on_draft)}",
                    )
                    return

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

        elif _command == COMMAND_REPROCESS_STR:
            if not await self.owners_file_handler.is_user_valid_to_run_commands(
                pull_request=pull_request, reviewed_user=reviewed_user
            ):
                return
            await self.pull_request_handler.process_command_reprocess(pull_request=pull_request)

        elif _command == COMMAND_REGENERATE_WELCOME_STR:
            if not await self.owners_file_handler.is_user_valid_to_run_commands(
                pull_request=pull_request, reviewed_user=reviewed_user
            ):
                return
            self.logger.info(f"{self.log_prefix} Regenerating welcome message")
            await self.pull_request_handler.regenerate_welcome_message(pull_request=pull_request)

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
                label_changed = await self.labels_handler._remove_label(pull_request=pull_request, label=WIP_STR)
                if label_changed:
                    pr_title = await asyncio.to_thread(lambda: pull_request.title)
                    # Case-insensitive check and removal of WIP prefix
                    pr_title_upper = pr_title.upper()
                    if pr_title_upper.startswith("WIP: "):
                        new_title = pr_title[5:]  # Remove "WIP: " (5 chars)
                        await asyncio.to_thread(pull_request.edit, title=new_title)
                    elif pr_title_upper.startswith("WIP:"):
                        new_title = pr_title[4:]  # Remove "WIP:" (4 chars)
                        await asyncio.to_thread(pull_request.edit, title=new_title)
            else:
                label_changed = await self.labels_handler._add_label(pull_request=pull_request, label=WIP_STR)
                if label_changed:
                    pr_title = await asyncio.to_thread(lambda: pull_request.title)
                    # Case-insensitive check: only prepend if prefix is not already there (idempotent)
                    if not pr_title.upper().startswith("WIP:"):
                        await asyncio.to_thread(pull_request.edit, title=f"{wip_for_title} {pr_title}")

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

        elif _command == VERIFIED_LABEL_STR:
            if remove:
                await self.labels_handler._remove_label(pull_request=pull_request, label=VERIFIED_LABEL_STR)
                await self.check_run_handler.set_check_queued(name=VERIFIED_LABEL_STR)
            else:
                await self.labels_handler._add_label(pull_request=pull_request, label=VERIFIED_LABEL_STR)
                await self.check_run_handler.set_check_success(name=VERIFIED_LABEL_STR)

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
        self.logger.debug(f"{self.log_prefix} Repo contributors are: {repo_contributors}")

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
        """Process cherry-pick command for pull requests.

        This method handles cherry-pick requests for both unmerged and merged PRs.
        Cherry-pick labels (cherry-pick/<branch-name>) are added in BOTH scenarios:

        **Unmerged PRs:**
        - Labels indicate which branches need cherry-picking after the PR is merged
        - Labels act as a TODO list for automatic cherry-picking
        - When the PR is merged, the PR handler detects these labels and triggers cherry-picks

        **Merged PRs:**
        - Cherry-picks are executed immediately for all target branches
        - Labels are added to track which branches have been cherry-picked to
        - Labels serve as a historical record of completed cherry-pick operations
        - Helps with auditing and tracking which releases include this change

        Args:
            pull_request: The pull request to cherry-pick
            command_args: Space-separated list of target branches (e.g., "v1.0 v2.0")
            reviewed_user: User who requested the cherry-pick

        Example:
            # Unmerged PR: /cherry-pick v1.0 v2.0
            # - Adds labels: cherry-pick/v1.0, cherry-pick/v2.0
            # - Posts comment explaining labels will trigger auto cherry-pick on merge

            # Merged PR: /cherry-pick v1.0 v2.0
            # - Executes cherry-pick to v1.0 and v2.0 immediately
            # - Adds labels: cherry-pick/v1.0, cherry-pick/v2.0 to track completion
        """
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
            f"{self.log_prefix} Found target branches {_exits_target_branches} "
            f"and not found {_non_exits_target_branches_msg}"
        )

        if _non_exits_target_branches_msg:
            self.logger.info(f"{self.log_prefix} {_non_exits_target_branches_msg}")
            await asyncio.to_thread(pull_request.create_issue_comment, _non_exits_target_branches_msg)

        cp_labels: list[str] = [
            f"{CHERRY_PICK_LABEL_PREFIX}{_target_branch}" for _target_branch in _exits_target_branches
        ]

        if _exits_target_branches:
            if not await asyncio.to_thread(pull_request.is_merged):
                info_msg: str = f"""
Cherry-pick requested for PR: `{pull_request.title}` by user `{reviewed_user}`
Adding label/s `{" ".join([_cp_label for _cp_label in cp_labels])}` for automatic cheery-pick once the PR is merged
"""

                self.logger.info(f"{self.log_prefix} {info_msg}")
                await asyncio.to_thread(pull_request.create_issue_comment, info_msg)
            else:
                for _exits_target_branch in _exits_target_branches:
                    await self.runner_handler.cherry_pick(
                        pull_request=pull_request,
                        target_branch=_exits_target_branch,
                        reviewed_user=reviewed_user,
                        assign_to_pr_owner=self.github_webhook.cherry_pick_assign_to_pr_author,
                    )

            for _cp_label in cp_labels:
                await self.labels_handler._add_label(pull_request=pull_request, label=_cp_label)

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
        self.logger.debug(f"{self.log_prefix} Supported retests are {_supported_retests}")
        self.logger.debug(f"{self.log_prefix} Not supported retests are {_not_supported_retests}")

        if _not_supported_retests:
            msg = f"No {' '.join(_not_supported_retests)} configured for this repository"
            error_msg = f"{self.log_prefix} {msg}."
            self.logger.debug(error_msg)
            await asyncio.to_thread(pull_request.create_issue_comment, msg)

        if _supported_retests:
            # Use runner_handler.run_retests() to avoid duplication
            await self.runner_handler.run_retests(
                supported_retests=_supported_retests,
                pull_request=pull_request,
            )

        if automerge:
            await self.labels_handler._add_label(pull_request=pull_request, label=AUTOMERGE_LABEL_STR)
