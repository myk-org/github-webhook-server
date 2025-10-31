from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any

from github.GithubException import GithubException, UnknownObjectException
from github.Repository import Repository

from webhook_server.libs.graphql.graphql_client import (
    GraphQLAuthenticationError,
    GraphQLError,
    GraphQLRateLimitError,
)
from webhook_server.libs.graphql.webhook_data import PullRequestWrapper
from webhook_server.libs.handlers.check_run_handler import CheckRunHandler
from webhook_server.libs.handlers.labels_handler import LabelsHandler
from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
from webhook_server.libs.handlers.runner_handler import RunnerHandler
from webhook_server.utils.constants import (
    APPROVED_BY_LABEL_PREFIX,
    AUTOMERGE_LABEL_STR,
    BRANCH_LABEL_PREFIX,
    BUILD_CONTAINER_STR,
    CAN_BE_MERGED_STR,
    CHANGED_REQUESTED_BY_LABEL_PREFIX,
    CHERRY_PICK_LABEL_PREFIX,
    CHERRY_PICKED_LABEL_PREFIX,
    COMMENTED_BY_LABEL_PREFIX,
    CONVENTIONAL_TITLE_STR,
    FAILURE_STR,
    HAS_CONFLICTS_LABEL_STR,
    HOLD_LABEL_STR,
    LABELS_SEPARATOR,
    LGTM_BY_LABEL_PREFIX,
    NEEDS_REBASE_LABEL_STR,
    PRE_COMMIT_STR,
    PYTHON_MODULE_INSTALL_STR,
    TOX_STR,
    USER_LABELS_DICT,
    VERIFIED_LABEL_STR,
    WIP_STR,
)
from webhook_server.utils.container_utils import get_container_repository_and_tag
from webhook_server.utils.helpers import format_task_fields

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook


class PullRequestHandler:
    def __init__(
        self,
        github_webhook: GithubWebhook,
        owners_file_handler: OwnersFileHandler,
        hook_data: dict[str, Any] | None = None,
    ):
        self.github_webhook = github_webhook
        self.owners_file_handler = owners_file_handler

        # Support hook_data parameter for testing (backward compatibility)
        self.hook_data = hook_data if hook_data is not None else self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix: str = self.github_webhook.log_prefix
        self.repository: Repository = self.github_webhook.repository
        self.labels_handler = LabelsHandler(
            github_webhook=self.github_webhook, owners_file_handler=self.owners_file_handler
        )
        self.check_run_handler = CheckRunHandler(
            github_webhook=self.github_webhook, owners_file_handler=self.owners_file_handler
        )
        self.runner_handler = RunnerHandler(
            github_webhook=self.github_webhook, owners_file_handler=self.owners_file_handler
        )

    def _log_task_error(self, result: Exception, task_name: str = "") -> None:
        """Log error from async task result.

        Args:
            result: The exception result from the task
            task_name: Optional task name for better error context
        """
        task_label = f" '{task_name}'" if task_name else ""
        self.logger.error(
            f"{self.log_prefix} Async task{task_label} FAILED: {result}",
            exc_info=(type(result), result, result.__traceback__),
        )

    async def process_pull_request_webhook_data(self, pull_request: PullRequestWrapper) -> None:
        await self.owners_file_handler.initialize(pull_request)

        hook_action: str = self.hook_data["action"]
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('pr_handler', 'pr_management', 'started')} "
            f"Starting pull request processing: action={hook_action}",
        )
        self.logger.info(f"{self.log_prefix} hook_action is: {hook_action}")
        self.logger.debug(f"{self.log_prefix} pull_request: {pull_request.title} ({pull_request.number})")

        pull_request_data: dict[str, Any] = self.hook_data["pull_request"]

        if hook_action == "edited":
            await self.set_wip_label_based_on_title(pull_request=pull_request)
            if self.github_webhook.conventional_title and self.hook_data["changes"].get("title"):
                self.logger.info(f"{self.log_prefix} PR title changed, running conventional title check")
                await self.runner_handler.run_conventional_title_check(pull_request=pull_request)

        if hook_action in ("opened", "reopened", "ready_for_review"):
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('pr_handler', 'pr_management', 'processing')} "
                f"Processing PR {hook_action} event: initializing new pull request",
            )

            if hook_action in ("opened", "ready_for_review"):
                self.logger.info(f"{self.log_prefix} WELCOME: Triggering welcome message for action={hook_action}")
                welcome_msg = self._prepare_welcome_comment()
                await self.github_webhook.unified_api.add_pr_comment(pull_request, welcome_msg)
            else:
                self.logger.debug(f"{self.log_prefix} WELCOME: Skipping welcome message for action={hook_action}")

            tasks: list[Coroutine[Any, Any, Any]] = []
            task_names: list[str] = []

            tasks.append(self.create_issue_for_new_pull_request(pull_request=pull_request))
            task_names.append("create_issue")
            tasks.append(self.set_wip_label_based_on_title(pull_request=pull_request))
            task_names.append("set_wip_label")
            tasks.append(self.process_opened_or_synchronize_pull_request(pull_request=pull_request))
            task_names.append("process_pr")

            self.logger.info(f"{self.log_prefix} Executing {len(tasks)} parallel tasks: {task_names}")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for idx, result in enumerate(results):
                task_name = task_names[idx] if idx < len(task_names) else f"task_{idx}"
                if isinstance(result, Exception):
                    self._log_task_error(result, task_name)
                else:
                    self.logger.debug(f"{self.log_prefix} Async task '{task_name}' completed successfully")

            # Set auto merge only after all initialization of a new PR is done.
            await self.set_pull_request_automerge(pull_request=pull_request)

        if hook_action == "synchronize":
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('pr_handler', 'pr_management', 'processing')} "
                f"Processing PR synchronize event: handling new commits",
            )
            sync_tasks: list[Coroutine[Any, Any, Any]] = []

            sync_tasks.append(self.process_opened_or_synchronize_pull_request(pull_request=pull_request))
            sync_tasks.append(self.remove_labels_when_pull_request_sync(pull_request=pull_request))

            results = await asyncio.gather(*sync_tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    self._log_task_error(result)

        if hook_action == "closed":
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('pr_handler', 'pr_management', 'processing')} "
                f"Processing PR closed event: cleaning up resources",
            )
            await self.close_issue_for_merged_or_closed_pr(pull_request=pull_request, hook_action=hook_action)
            await self.delete_remote_tag_for_merged_or_closed_pr(pull_request=pull_request)
            if is_merged := pull_request_data.get("merged", False):
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('pr_handler', 'pr_management', 'processing')} "
                    f"PR was merged: processing post-merge tasks",
                )
                self.logger.info(f"{self.log_prefix} PR is merged")

                for _label in pull_request.get_labels():
                    _label_name = _label.name
                    if _label_name.startswith(CHERRY_PICK_LABEL_PREFIX):
                        await self.runner_handler.cherry_pick(
                            pull_request=pull_request, target_branch=_label_name.replace(CHERRY_PICK_LABEL_PREFIX, "")
                        )

                await self.runner_handler.run_build_container(
                    push=True,
                    set_check=False,
                    is_merged=is_merged,
                    pull_request=pull_request,
                )

                await self.label_all_opened_pull_requests_merge_state_after_merged()

        if hook_action in ("labeled", "unlabeled"):
            _check_for_merge: bool = False
            _user: str | None = None
            action_labeled = hook_action == "labeled"
            labeled = self.hook_data["label"]["name"]
            labeled_lower = labeled.lower()

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('pr_handler', 'pr_management', 'processing')} "
                f"Processing label {hook_action} event: {labeled}",
            )

            if labeled_lower == CAN_BE_MERGED_STR:
                return

            self.logger.info(f"{self.log_prefix} PR {pull_request.number} {hook_action} with {labeled}")
            label_names = [label.name for label in pull_request.get_labels()]
            self.logger.debug(f"PR labels are {label_names}")

            _split_label = labeled.split(LABELS_SEPARATOR, 1)

            if len(_split_label) == 2:
                _label_prefix, _user = _split_label

                if f"{_label_prefix}{LABELS_SEPARATOR}" in (
                    APPROVED_BY_LABEL_PREFIX,
                    LGTM_BY_LABEL_PREFIX,
                    CHANGED_REQUESTED_BY_LABEL_PREFIX,
                ):
                    if (
                        _user
                        in self.owners_file_handler.all_pull_request_reviewers
                        + self.owners_file_handler.all_pull_request_approvers
                        + self.owners_file_handler.root_approvers
                    ):
                        _check_for_merge = True
                        self.logger.debug(f"PR approved by label action, will check for merge. user: {_user}")

            if self.github_webhook.verified_job and labeled_lower == VERIFIED_LABEL_STR:
                _check_for_merge = True
                self.logger.debug(f"PR verified label action, will check for merge. label: {labeled_lower}")

                if action_labeled:
                    await self.check_run_handler.set_verify_check_success()
                else:
                    await self.check_run_handler.set_verify_check_queued()

            if labeled_lower in (WIP_STR, HOLD_LABEL_STR, AUTOMERGE_LABEL_STR):
                _check_for_merge = True
                self.logger.debug(f"PR has {labeled_lower} label, will check for merge.")

            if _check_for_merge:
                await self.check_if_can_be_merged(pull_request=pull_request)

    async def set_wip_label_based_on_title(self, pull_request: PullRequestWrapper) -> None:
        if pull_request.title.lower().startswith(f"{WIP_STR}:"):
            self.logger.debug(f"{self.log_prefix} Found {WIP_STR} in {pull_request.title}; adding {WIP_STR} label.")
            await self.labels_handler._add_label(pull_request=pull_request, label=WIP_STR)

        else:
            self.logger.debug(
                f"{self.log_prefix} {WIP_STR} not found in {pull_request.title}; removing {WIP_STR} label."
            )
            await self.labels_handler._remove_label(pull_request=pull_request, label=WIP_STR)

    def _prepare_welcome_comment(self) -> str:
        self.logger.info(f"{self.log_prefix} Prepare welcome comment")
        supported_user_labels_str: str = "".join([f" * {label}\n" for label in USER_LABELS_DICT.keys()])

        is_auto_verified = self.github_webhook.parent_committer in self.github_webhook.auto_verified_and_merged_users
        auto_verified_note = ""
        if is_auto_verified:
            auto_verified_note = (
                "\n\n> **Note**: You are an auto-verified user. Your PRs will be automatically verified "
                "and may be auto-merged when all requirements are met.\n"
            )

        # Check if issue creation is enabled
        issue_creation_note = ""
        if self.github_webhook.create_issue_for_new_pr:
            issue_creation_note = (
                "* **Issue Creation**: A tracking issue is created for this PR and will be closed "
                "when the PR is merged or closed\n"
            )
        else:
            issue_creation_note = "* **Issue Creation**: Disabled for this repository\n"

        return f"""
{self.github_webhook.issue_url_for_welcome_msg}

## Welcome! 🎉

This pull request will be automatically processed with the following features:
{auto_verified_note}

### 🔄 Automatic Actions
* **Reviewer Assignment**: Reviewers are automatically assigned based on the OWNERS file in the repository root
* **Size Labeling**: PR size labels (XS, S, M, L, XL, XXL) are automatically applied based on changes
{issue_creation_note}* **Pre-commit Checks**: [pre-commit](https://pre-commit.ci/) runs automatically if \
`.pre-commit-config.yaml` exists
* **Branch Labeling**: Branch-specific labels are applied to track the target branch
* **Auto-verification**: Auto-verified users have their PRs automatically marked as verified

### 📋 Available Commands

#### PR Status Management
* `/wip` - Mark PR as work in progress (adds WIP: prefix to title)
* `/wip cancel` - Remove work in progress status
* `/hold` - Block PR merging (approvers only)
* `/hold cancel` - Unblock PR merging
* `/verified` - Mark PR as verified
* `/verified cancel` - Remove verification status

#### Review & Approval
* `/lgtm` - Approve changes (looks good to me)
* `/approve` - Approve PR (approvers only)
* `/automerge` - Enable automatic merging when all requirements are met (maintainers and approvers only)
* `/assign-reviewers` - Assign reviewers based on OWNERS file
* `/assign-reviewer @username` - Assign specific reviewer
* `/check-can-merge` - Check if PR meets merge requirements

#### Testing & Validation
{self._prepare_retest_welcome_comment}

#### Container Operations
* `/build-and-push-container` - Build and push container image (tagged with PR number)
  * Supports additional build arguments: `/build-and-push-container --build-arg KEY=value`

#### Cherry-pick Operations
* `/cherry-pick <branch>` - Schedule cherry-pick to target branch when PR is merged
  * Multiple branches: `/cherry-pick branch1 branch2 branch3`

#### Label Management
* `/<label-name>` - Add a label to the PR
* `/<label-name> cancel` - Remove a label from the PR

### ✅ Merge Requirements

This PR will be automatically approved when the following conditions are met:

1. **Approval**: `/approve` from at least one approver
2. **LGTM Count**: Minimum {self.github_webhook.minimum_lgtm} `/lgtm` from reviewers
3. **Status Checks**: All required status checks must pass
4. **No Blockers**: No WIP, hold, or conflict labels
5. **Verified**: PR must be marked as verified (if verification is enabled)

### 📊 Review Process

<details>
<summary><strong>Approvers and Reviewers</strong></summary>

{self._prepare_owners_welcome_comment()}
</details>

<details>
<summary><strong>Available Labels</strong></summary>

{supported_user_labels_str}
</details>

### 💡 Tips

* **WIP Status**: Use `/wip` when your PR is not ready for review
* **Verification**: The verified label is automatically removed on each new commit
* **Cherry-picking**: Cherry-pick labels are processed when the PR is merged
* **Container Builds**: Container images are automatically tagged with the PR number
* **Permission Levels**: Some commands require approver permissions
* **Auto-verified Users**: Certain users have automatic verification and merge privileges

For more information, please refer to the project documentation or contact the maintainers.
    """

    def _prepare_owners_welcome_comment(self) -> str:
        body_approvers: str = "**Approvers:**\n"
        body_reviewers: str = "**Reviewers:**\n"

        for _approver in self.owners_file_handler.all_pull_request_approvers:
            body_approvers += f" * {_approver}\n"

        for _reviewer in self.owners_file_handler.all_pull_request_reviewers:
            body_reviewers += f" * {_reviewer}\n"

        return f"""
{body_approvers}

{body_reviewers}
"""

    @property
    def _prepare_retest_welcome_comment(self) -> str:
        retest_msg: str = ""

        if self.github_webhook.tox:
            retest_msg += f" * `/retest {TOX_STR}` - Run Python test suite with tox\n"

        if self.github_webhook.build_and_push_container:
            retest_msg += f" * `/retest {BUILD_CONTAINER_STR}` - Rebuild and test container image\n"

        if self.github_webhook.pypi:
            retest_msg += f" * `/retest {PYTHON_MODULE_INSTALL_STR}` - Test Python package installation\n"

        if self.github_webhook.pre_commit:
            retest_msg += f" * `/retest {PRE_COMMIT_STR}` - Run pre-commit hooks and checks\n"

        if self.github_webhook.conventional_title:
            retest_msg += f" * `/retest {CONVENTIONAL_TITLE_STR}` - Validate commit message format\n"

        if retest_msg:
            retest_msg += " * `/retest all` - Run all available tests\n"

        return " * No retest actions are configured for this repository" if not retest_msg else retest_msg

    async def label_all_opened_pull_requests_merge_state_after_merged(self) -> None:
        """
        Labels pull requests based on their mergeable state.

        If the mergeable state is 'behind', the 'needs rebase' label is added.
        If the mergeable state is 'dirty', the 'has conflicts' label is added.

        Performance: Uses batched GraphQL query to fetch all open PRs with labels/state
        in a single API call, eliminating N+1 query pattern.

        Note: Runs in background (non-blocking) with configurable delay via 'post-merge-relabel-delay'
        config setting (default: 30 seconds).
        """
        # Schedule background task to avoid blocking webhook processing
        # Store task reference to prevent garbage collection (RUF006)
        task = asyncio.create_task(self._label_all_opened_pull_requests_background())
        # Task runs independently - we don't await it but keep the reference
        # to prevent premature garbage collection
        _ = task

    async def _label_all_opened_pull_requests_background(self) -> None:
        """Background task for relabeling open PRs after merge.

        Runs with configurable delay to allow GitHub to update merge states.
        Error handling ensures failures don't crash the background task.
        """
        try:
            delay = self.github_webhook.config.get_value("post-merge-relabel-delay", return_on_none=30)
            self.logger.info(f"{self.log_prefix} Scheduled background relabeling of open PRs in {delay} seconds")
            await asyncio.sleep(delay)

            owner, repo_name = self.github_webhook.owner_and_repo
            # NEW: Single batched GraphQL query gets all open PRs with labels and merge state
            # Replaces: get_open_pull_requests() + get_pull_request_data() for each PR
            # Savings: If N PRs exist, saves N API calls (N+1 → 1)
            open_prs = await self.github_webhook.unified_api.get_open_pull_requests_with_details(owner, repo_name)
            for pull_request in open_prs:
                self.logger.info(f"{self.log_prefix} check label pull request after merge")
                # No additional API calls needed - labels and merge state already loaded in pull_request
                await self.label_pull_request_by_merge_state(pull_request=pull_request)

            self.logger.info(f"{self.log_prefix} Background relabeling of open PRs completed")
        except Exception:
            # Log error but don't crash - this is a background task
            self.logger.exception(f"{self.log_prefix} Background relabeling task failed")

    async def delete_remote_tag_for_merged_or_closed_pr(self, pull_request: PullRequestWrapper) -> None:
        self.logger.debug(f"{self.log_prefix} Checking if need to delete remote tag for {pull_request.number}")
        if not self.github_webhook.build_and_push_container:
            self.logger.info(f"{self.log_prefix} repository do not have container configured")
            return

        repository_full_tag = get_container_repository_and_tag(
            container_repository=self.github_webhook.container_repository,
            container_tag=self.github_webhook.container_tag,
            pull_request=pull_request,
            logger=self.logger,
            log_prefix=self.log_prefix,
        )
        if not repository_full_tag:
            return

        pr_tag = repository_full_tag.split(":")[-1]
        registry_info = self.github_webhook.container_repository.split("/")
        # If the repository reference does not contain an explicit registry host we
        # cannot (and should not) try to log in – just skip the deletion logic.
        if len(registry_info) < 3:
            self.logger.debug(
                f"{self.log_prefix} No registry host found in "
                f"{self.github_webhook.container_repository}; skipping tag deletion"
            )
            return

        registry_url = registry_info[0]
        reg_login_cmd = (
            f"regctl registry login {registry_url} "
            f"-u {self.github_webhook.container_repository_username} "
            f"-p {self.github_webhook.container_repository_password}"
        )

        rc, out, err = await self.runner_handler.run_podman_command(
            command=reg_login_cmd,
            redact_secrets=[
                self.github_webhook.container_repository_username,
                self.github_webhook.container_repository_password,
            ],
        )

        if rc:
            try:
                tag_ls_cmd = f"regctl tag ls {self.github_webhook.container_repository} --include {pr_tag}"
                rc, out, err = await self.runner_handler.run_podman_command(
                    command=tag_ls_cmd,
                    redact_secrets=[
                        self.github_webhook.container_repository_username,
                        self.github_webhook.container_repository_password,
                    ],
                )

                if rc and out:
                    tag_del_cmd = f"regctl tag delete {repository_full_tag}"

                    rc, _, _ = await self.runner_handler.run_podman_command(
                        command=tag_del_cmd,
                        redact_secrets=[
                            self.github_webhook.container_repository_username,
                            self.github_webhook.container_repository_password,
                        ],
                    )
                    if rc:
                        await self.github_webhook.unified_api.add_pr_comment(
                            pull_request=pull_request,
                            body=f"Successfully removed PR tag: {repository_full_tag}.",
                        )
                    else:
                        self.logger.error(
                            f"{self.log_prefix} Failed to delete tag: {repository_full_tag}. OUT:{out}. ERR:{err}"
                        )
                else:
                    self.logger.warning(
                        f"{self.log_prefix} {pr_tag} tag not found in registry "
                        f"{self.github_webhook.container_repository}. OUT:{out}. ERR:{err}"
                    )
            finally:
                await self.runner_handler.run_podman_command(command="regctl registry logout")

        else:
            await self.github_webhook.unified_api.add_pr_comment(
                pull_request=pull_request,
                body=f"Failed to delete tag: {repository_full_tag}. Please delete it manually.",
            )
            self.logger.error(f"{self.log_prefix} Failed to delete tag: {repository_full_tag}. OUT:{out}. ERR:{err}")

    async def close_issue_for_merged_or_closed_pr(self, pull_request: PullRequestWrapper, hook_action: str) -> None:
        owner, repo_name = self.github_webhook.owner_and_repo
        for issue in await self.github_webhook.unified_api.get_issues(
            owner, repo_name, repository_data=self.github_webhook.repository_data
        ):
            if issue["body"] == self._generate_issue_body(pull_request=pull_request):
                self.logger.info(f"{self.log_prefix} Closing issue {issue['title']} for PR: {pull_request.title}")
                await self.github_webhook.unified_api.add_comment(
                    issue["id"],
                    f"{self.log_prefix} Closing issue for PR: {pull_request.title}.\nPR was {hook_action}.",
                )
                await self.github_webhook.unified_api.edit_issue(issue, state="closed")

                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('pr_handler', 'issue_management', 'completed')} "
                    f"Issue closed for merged PR"
                )
                break

    async def process_opened_or_synchronize_pull_request(self, pull_request: PullRequestWrapper) -> None:
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('pr_handler', 'pr_management', 'started')} "
            f"Starting PR processing workflow",
        )

        # Stage 1: Initial setup and check queue tasks
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('pr_handler', 'pr_management', 'processing')} "
            f"Stage: Initial setup and check queuing",
        )
        setup_tasks: list[Coroutine[Any, Any, Any]] = []

        setup_tasks.append(self.owners_file_handler.assign_reviewers(pull_request=pull_request))
        setup_tasks.append(
            self.labels_handler._add_label(
                pull_request=pull_request,
                label=f"{BRANCH_LABEL_PREFIX}{pull_request.base.ref}",
            )
        )
        setup_tasks.append(self.label_pull_request_by_merge_state(pull_request=pull_request))
        setup_tasks.append(self.check_run_handler.set_merge_check_queued())
        setup_tasks.append(self.check_run_handler.set_run_tox_check_queued())
        setup_tasks.append(self.check_run_handler.set_run_pre_commit_check_queued())
        setup_tasks.append(self.check_run_handler.set_python_module_install_queued())
        setup_tasks.append(self.check_run_handler.set_container_build_queued())
        setup_tasks.append(self._process_verified_for_update_or_new_pull_request(pull_request=pull_request))
        setup_tasks.append(self.labels_handler.add_size_label(pull_request=pull_request))
        setup_tasks.append(self.add_pull_request_owner_as_assignee(pull_request=pull_request))

        if self.github_webhook.conventional_title:
            setup_tasks.append(self.check_run_handler.set_conventional_title_queued())

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('pr_handler', 'pr_management', 'processing')} Executing setup tasks"
        )
        setup_results = await asyncio.gather(*setup_tasks, return_exceptions=True)

        for result in setup_results:
            if isinstance(result, Exception):
                self._log_task_error(result, "setup")

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('pr_handler', 'pr_management', 'completed')} Setup tasks completed"
        )

        # Stage 2: CI/CD execution tasks
        self.logger.step(f"{self.log_prefix} Stage: CI/CD execution")  # type: ignore[attr-defined]
        ci_tasks: list[Coroutine[Any, Any, Any]] = []

        ci_tasks.append(self.runner_handler.run_tox(pull_request=pull_request))
        ci_tasks.append(self.runner_handler.run_pre_commit(pull_request=pull_request))
        ci_tasks.append(self.runner_handler.run_install_python_module(pull_request=pull_request))
        ci_tasks.append(self.runner_handler.run_build_container(pull_request=pull_request))

        if self.github_webhook.conventional_title:
            ci_tasks.append(self.runner_handler.run_conventional_title_check(pull_request=pull_request))

        self.logger.step(f"{self.log_prefix} Executing CI/CD tasks")  # type: ignore[attr-defined]
        ci_results = await asyncio.gather(*ci_tasks, return_exceptions=True)

        for result in ci_results:
            if isinstance(result, Exception):
                self._log_task_error(result, "CI/CD")

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('pr_handler', 'pr_management', 'completed')} "
            f"PR processing workflow completed",
        )

    async def create_issue_for_new_pull_request(self, pull_request: PullRequestWrapper) -> None:
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('pr_handler', 'issue_management', 'started')} "
            f"Creating issue for new PR"
        )
        if not self.github_webhook.create_issue_for_new_pr:
            self.logger.info(f"{self.log_prefix} Issue creation for new PRs is disabled for this repository")
            return

        if self.github_webhook.parent_committer in self.github_webhook.auto_verified_and_merged_users:
            self.logger.info(
                f"{self.log_prefix} Committer {self.github_webhook.parent_committer} is part of "
                f"{self.github_webhook.auto_verified_and_merged_users}, will not create issue."
            )
            return

        owner, repo_name = self.github_webhook.owner_and_repo
        issue_title = self._generate_issue_title(pull_request=pull_request)

        # Check if issue already exists
        # Note: GitHub GraphQL API does not support issue search by title.
        # Current approach reuses pre-fetched repository_data (zero additional API calls).
        # Alternative (REST search API) would add an API call, making it slower.
        # O(N) iteration is acceptable: typical repos have <100 open issues.
        self.logger.debug(
            f"{self.log_prefix} Checking if issue already exists for PR #{pull_request.number} "
            f"in repository {owner}/{repo_name}"
        )
        try:
            existing_issues = await self.github_webhook.unified_api.get_issues(
                owner, repo_name, repository_data=self.github_webhook.repository_data
            )

            for issue in existing_issues:
                if issue["title"] == issue_title:
                    issue_url = f"https://github.com/{owner}/{repo_name}/issues/{issue['number']}"
                    self.logger.info(
                        f"{self.log_prefix} Issue already exists for PR #{pull_request.number}: {issue_url}"
                    )
                    return
        except (GithubException, GraphQLError):
            self.logger.exception(
                f"{self.log_prefix} GitHub API error checking existing issues, proceeding with creation"
            )
        except Exception:
            self.logger.exception(
                f"{self.log_prefix} Unexpected error checking existing issues, proceeding with creation"
            )

        # Issue doesn't exist, create it
        self.logger.debug(
            f"{self.log_prefix} Creating issue for new PR: {pull_request.title} "
            f"(#{pull_request.number}) in {owner}/{repo_name}"
        )

        # Get repository ID and assignee ID for GraphQL mutation
        # Optimization: Use webhook data instead of API call
        repository_id = self.github_webhook.repository_id

        # Try to get assignee ID, but handle bots/apps gracefully
        # Bots (like renovate, dependabot) can't be assigned as they're not users
        try:
            # Use node_id from webhook - ALWAYS present in webhook/GraphQL data
            assignee_id = pull_request.user.node_id
            assignee_ids = [assignee_id]
        except (GraphQLError, UnknownObjectException):
            # Author is likely a bot/app (e.g., renovate, dependabot)
            self.logger.info(
                f"{self.log_prefix} Could not get user ID for '{pull_request.user.login}' "
                f"(likely a bot/app). Creating issue without assignee."
            )
            assignee_ids = []

        await self.github_webhook.unified_api.create_issue(
            repository_id=repository_id,
            title=issue_title,
            body=self._generate_issue_body(pull_request=pull_request),
            assignee_ids=assignee_ids,
        )

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('pr_handler', 'issue_management', 'completed')} "
            f"Issue creation completed"
        )

    def _generate_issue_title(self, pull_request: PullRequestWrapper) -> str:
        return f"[PR #{pull_request.number}] {pull_request.title}"

    def _generate_issue_body(self, pull_request: PullRequestWrapper) -> str:
        return f"[Auto generated]\nNumber: [#{pull_request.number}]"

    async def set_pull_request_automerge(self, pull_request: PullRequestWrapper) -> None:
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('pr_handler', 'automerge', 'started')} "
            f"Configuring auto-merge for PR"
        )
        set_auto_merge_base_branch = pull_request.base.ref in self.github_webhook.set_auto_merge_prs
        self.logger.debug(f"{self.log_prefix} set auto merge for base branch is {set_auto_merge_base_branch}")
        parent_committer_in_auto_merge_users = (
            self.github_webhook.parent_committer in self.github_webhook.auto_verified_and_merged_users
        )
        self.logger.debug(
            f"{self.log_prefix} parent committer {self.github_webhook.parent_committer} in auto merge users is "
            f"{parent_committer_in_auto_merge_users}"
        )

        auto_merge = set_auto_merge_base_branch or parent_committer_in_auto_merge_users

        self.logger.debug(f"{self.log_prefix} auto_merge: {auto_merge}, branch: {pull_request.base.ref}")

        if auto_merge:
            try:
                if not pull_request.webhook_data.get("auto_merge"):
                    self.logger.info(
                        f"{self.log_prefix} will be merged automatically. "
                        f"owner: {self.github_webhook.parent_committer} is part of auto merge enabled rules"
                    )

                    await self.github_webhook.unified_api.enable_pr_automerge(pull_request, "SQUASH")
                else:
                    self.logger.debug(f"{self.log_prefix} is already set to auto merge")

            except (GraphQLAuthenticationError, GraphQLRateLimitError):
                # Re-raise critical authentication and rate-limit errors
                raise
            except (GraphQLError, GithubException):
                # Catch API-layer exceptions; log with exception details
                self.logger.exception(f"{self.log_prefix} Exception while setting auto merge")

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('pr_handler', 'automerge', 'completed')} "
            f"Auto-merge configuration completed"
        )

    async def remove_labels_when_pull_request_sync(self, pull_request: PullRequestWrapper) -> None:
        tasks: list[Coroutine[Any, Any, Any]] = []
        for _label in pull_request.get_labels():
            _label_name = _label.name
            if (
                _label_name.startswith(APPROVED_BY_LABEL_PREFIX)
                or _label_name.startswith(COMMENTED_BY_LABEL_PREFIX)
                or _label_name.startswith(CHANGED_REQUESTED_BY_LABEL_PREFIX)
                or _label_name.startswith(LGTM_BY_LABEL_PREFIX)
            ):
                tasks.append(
                    self.labels_handler._remove_label(
                        pull_request=pull_request,
                        label=_label_name,
                    )
                )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                self._log_task_error(result)

    async def label_pull_request_by_merge_state(self, pull_request: PullRequestWrapper) -> None:
        merge_state = pull_request.mergeable_state
        self.logger.debug(f"{self.log_prefix} Mergeable state is {merge_state}")
        if merge_state == "unknown":
            return

        if merge_state == "behind":
            await self.labels_handler._add_label(pull_request=pull_request, label=NEEDS_REBASE_LABEL_STR)
        else:
            await self.labels_handler._remove_label(pull_request=pull_request, label=NEEDS_REBASE_LABEL_STR)

        if merge_state == "dirty":
            await self.labels_handler._add_label(pull_request=pull_request, label=HAS_CONFLICTS_LABEL_STR)
        else:
            await self.labels_handler._remove_label(pull_request=pull_request, label=HAS_CONFLICTS_LABEL_STR)

    async def _process_verified_for_update_or_new_pull_request(self, pull_request: PullRequestWrapper) -> None:
        if not self.github_webhook.verified_job:
            return

        # Log auto-verification context at the start
        self.logger.debug(
            f"{self.log_prefix} Auto-verification check: "
            f"parent_committer='{self.github_webhook.parent_committer}', "
            f"auto_verified_users={self.github_webhook.auto_verified_and_merged_users}, "
            f"verified_job={self.github_webhook.verified_job}"
        )

        # Check if this is a cherry-picked PR
        labels = pull_request.get_labels()
        is_cherry_picked = any(label.name.startswith(CHERRY_PICKED_LABEL_PREFIX) for label in labels)

        # Log cherry-pick check details
        self.logger.debug(
            f"{self.log_prefix} Cherry-pick check: "
            f"is_cherry_picked={is_cherry_picked}, "
            f"auto_verify_cherry_picked_prs={self.github_webhook.auto_verify_cherry_picked_prs}"
        )

        # If it's a cherry-picked PR and auto-verify is disabled for cherry-picks, skip auto-verification
        if is_cherry_picked and not self.github_webhook.auto_verify_cherry_picked_prs:
            self.logger.info(
                f"{self.log_prefix} Cherry-picked PR detected "
                f"(auto_verify_cherry_picked_prs={self.github_webhook.auto_verify_cherry_picked_prs}), "
                f"skipping auto-verification"
            )
            await self.check_run_handler.set_verify_check_queued()
            return

        if self.github_webhook.parent_committer in self.github_webhook.auto_verified_and_merged_users:
            # Log auto-verification match
            self.logger.info(
                f"{self.log_prefix} Committer '{self.github_webhook.parent_committer}' IS in "
                f"auto_verified_and_merged_users list {self.github_webhook.auto_verified_and_merged_users}, "
                f"adding verified label"
            )
            await self.labels_handler._add_label(pull_request=pull_request, label=VERIFIED_LABEL_STR)
            await self.check_run_handler.set_verify_check_success()
        else:
            # Log auto-verification miss
            self.logger.info(
                f"{self.log_prefix} Committer '{self.github_webhook.parent_committer}' NOT in "
                f"auto_verified_and_merged_users list {self.github_webhook.auto_verified_and_merged_users}, "
                f"removing verified label"
            )
            # Remove verified label
            await self.labels_handler._remove_label(pull_request=pull_request, label=VERIFIED_LABEL_STR)
            await self.check_run_handler.set_verify_check_queued()

    async def _assign_first_approver_as_fallback(
        self,
        owner: str,
        repo_name: str,
        pr_number: int,
        reason: str,
    ) -> None:
        """Assign first approver as assignee fallback.

        Args:
            owner: Repository owner
            repo_name: Repository name
            pr_number: Pull request number
            reason: Reason for fallback (for logging)
        """
        if self.owners_file_handler.root_approvers:
            self.logger.debug(f"{self.log_prefix} {reason}")
            await self.github_webhook.unified_api.add_assignees_by_login(
                owner, repo_name, pr_number, [self.owners_file_handler.root_approvers[0]]
            )

    async def add_pull_request_owner_as_assignee(self, pull_request: PullRequestWrapper) -> None:
        # Optimization: Use PR node ID directly instead of fetching PR data again
        # pull_request.id is already available from earlier fetch, saving one GraphQL query
        owner, repo_name = self.github_webhook.owner_and_repo
        author_login = pull_request.user.login

        # Check if author is a bot before attempting assignment
        # GitHub doesn't allow bots to be assigned to PRs
        # user.type is ALWAYS present (available in both GraphQL __typename and webhook type)
        if pull_request.user.type == "Bot":
            self.logger.info(
                f"{self.log_prefix} PR author '{author_login}' is a bot (type={pull_request.user.type}), "
                "skipping assignee assignment. Will use first approver instead."
            )
            # Skip assignment attempt and go straight to fallback
            await self._assign_first_approver_as_fallback(
                owner, repo_name, pull_request.number, "Assigning first approver as assignee"
            )
            return

        try:
            self.logger.info(f"{self.log_prefix} Adding PR owner '{author_login}' as assignee")
            # Use optimized method that accepts pr_id directly (saves one GraphQL query)
            await self.github_webhook.unified_api.add_assignees_by_login_with_pr_id(pull_request.id, [author_login])
        except UnknownObjectException:
            # 404 error - user not found (external contributor, deleted account, or bot)
            self.logger.debug(
                f"{self.log_prefix} Could not add '{author_login}' as assignee (404 Not Found). "
                f"Likely external contributor or bot account."
            )
            await self._assign_first_approver_as_fallback(
                owner, repo_name, pull_request.number, "Falling back to first approver as assignee"
            )
        except GithubException as ex:
            # Other GitHub API errors (rate limit, permissions, etc.)
            self.logger.exception(f"{self.log_prefix} GitHub API error while adding PR owner as assignee: {ex.status}")
            await self._assign_first_approver_as_fallback(
                owner, repo_name, pull_request.number, "Falling back to first approver as assignee"
            )
        except Exception:
            # Unexpected errors
            self.logger.exception(f"{self.log_prefix} Unexpected error while adding PR owner as assignee")
            await self._assign_first_approver_as_fallback(
                owner, repo_name, pull_request.number, "Falling back to first approver as assignee"
            )

    async def check_if_can_be_merged(self, pull_request: PullRequestWrapper) -> None:
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
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('pr_handler', 'pr_management', 'started')} "
            f"Starting merge eligibility check",
        )
        if self.skip_if_pull_request_already_merged(pull_request=pull_request):
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
            await self.check_run_handler.set_merge_check_in_progress()
            owner, repo_name = self.github_webhook.owner_and_repo
            if self.github_webhook.last_commit:
                last_commit_check_runs = await self.github_webhook.unified_api.get_commit_check_runs(
                    self.github_webhook.last_commit, owner, repo_name
                )
            else:
                self.logger.warning(f"{self.log_prefix} last_commit is None, using empty check runs list")
                last_commit_check_runs = []
            _labels = await self.labels_handler.pull_request_labels_names(pull_request=pull_request)
            self.logger.debug(f"{self.log_prefix} check if can be merged. PR labels are: {_labels}")

            is_pr_mergable = pull_request.mergeable
            self.logger.debug(f"{self.log_prefix} PR mergeable is {is_pr_mergable}")
            if not is_pr_mergable:
                failure_output += f"PR is not mergeable: {is_pr_mergable}\n"

            (
                required_check_in_progress_failure_output,
                check_runs_in_progress,
            ) = await self.check_run_handler.required_check_in_progress(
                pull_request=pull_request, last_commit_check_runs=last_commit_check_runs
            )
            if required_check_in_progress_failure_output:
                failure_output += required_check_in_progress_failure_output
            self.logger.debug(f"{self.log_prefix} required_check_in_progress_failure_output: {failure_output}")

            labels_failure_output = self.labels_handler.wip_or_hold_labels_exists(labels=_labels)
            if labels_failure_output:
                failure_output += labels_failure_output
            self.logger.debug(f"{self.log_prefix} wip_or_hold_labels_exists: {failure_output}")

            required_check_failed_failure_output = await self.check_run_handler.required_check_failed_or_no_status(
                pull_request=pull_request,
                last_commit_check_runs=last_commit_check_runs,
                check_runs_in_progress=check_runs_in_progress,
            )
            if required_check_failed_failure_output:
                failure_output += required_check_failed_failure_output
            self.logger.debug(f"{self.log_prefix} required_check_failed_or_no_status: {failure_output}")

            labels_failure_output = self._check_labels_for_can_be_merged(labels=_labels)
            if labels_failure_output:
                failure_output += labels_failure_output
            self.logger.debug(f"{self.log_prefix} _check_labels_for_can_be_merged: {failure_output}")

            pr_approvered_failure_output = await self._check_if_pr_approved(labels=_labels)
            if pr_approvered_failure_output:
                failure_output += pr_approvered_failure_output
            self.logger.debug(f"{self.log_prefix} _check_if_pr_approved: {failure_output}")

            if not failure_output:
                await self.labels_handler._add_label(pull_request=pull_request, label=CAN_BE_MERGED_STR)
                await self.check_run_handler.set_merge_check_success()

                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('pr_handler', 'ci_check', 'completed')} "
                    f"Merge eligibility check passed - PR can be merged"
                )
                self.logger.info(f"{self.log_prefix} Pull request can be merged")
                return

            self.logger.debug(f"{self.log_prefix} cannot be merged: {failure_output}")
            output["text"] = failure_output
            await self.labels_handler._remove_label(pull_request=pull_request, label=CAN_BE_MERGED_STR)
            await self.check_run_handler.set_merge_check_failure(output=output)

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('pr_handler', 'ci_check', 'failed')} "
                f"Merge eligibility check failed"
            )

        except Exception:
            self.logger.exception(f"{self.log_prefix} Failed to check if can be merged, set check run to {FAILURE_STR}")
            _err = "Failed to check if can be merged, check logs"
            output["text"] = _err
            await self.labels_handler._remove_label(pull_request=pull_request, label=CAN_BE_MERGED_STR)
            await self.check_run_handler.set_merge_check_failure(output=output)

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('pr_handler', 'ci_check', 'failed')} "
                f"Merge eligibility check encountered error"
            )

    async def _check_if_pr_approved(self, labels: list[str]) -> str:
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('pr_handler', 'approval', 'started')} PR approval check started"
        )
        self.logger.info(f"{self.log_prefix} Check if pull request is approved by pull request labels.")
        self.logger.debug(f"labels are {labels}")

        error: str = ""
        approved_by = []
        lgtm_count: int = 0

        all_reviewers = (
            self.owners_file_handler.all_pull_request_reviewers.copy()
            + self.owners_file_handler.root_approvers.copy()
            + self.owners_file_handler.root_reviewers.copy()
        )
        self.logger.debug(f"all_reviewers: {all_reviewers}")
        all_reviewers_without_pr_owner = {
            _reviewer for _reviewer in all_reviewers if _reviewer != self.github_webhook.parent_committer
        }
        self.logger.debug(f"all_reviewers_without_pr_owner: {all_reviewers_without_pr_owner}")

        if self.github_webhook.minimum_lgtm:
            for _label in labels:
                reviewer = _label.split(LABELS_SEPARATOR)[-1]
                if LGTM_BY_LABEL_PREFIX.lower() in _label.lower() and reviewer in all_reviewers_without_pr_owner:
                    lgtm_count += 1
        self.logger.debug(f"lgtm_count: {lgtm_count}")

        for _label in labels:
            if APPROVED_BY_LABEL_PREFIX.lower() in _label.lower():
                approved_by.append(_label.split(LABELS_SEPARATOR)[-1])
        self.logger.debug(f"approved_by: {approved_by}")

        missing_approvers = list(set(self.owners_file_handler.all_pull_request_approvers.copy()))
        self.logger.debug(f"missing_approvers: {missing_approvers}")
        owners_data_changed_files = await self.owners_file_handler.owners_data_for_changed_files()
        self.logger.debug(f"owners_data_changed_files: {owners_data_changed_files}")

        # If any of root approvers is in approved_by list, the pull request is approved
        for _approver in approved_by:
            if _approver in self.owners_file_handler.root_approvers:
                missing_approvers = []
                break

        if missing_approvers:
            for data in owners_data_changed_files.values():
                required_pr_approvers = data.get("approvers", [])

                for required_pr_approver in required_pr_approvers:
                    if required_pr_approver in approved_by:
                        for _approver in required_pr_approvers:
                            if _approver in missing_approvers:
                                missing_approvers.remove(_approver)

                        break

        missing_approvers = list(set(missing_approvers))
        self.logger.debug(f"missing_approvers after check: {missing_approvers}")

        if missing_approvers:
            error += f"Missing approved from approvers: {', '.join(missing_approvers)}\n"

        if lgtm_count < self.github_webhook.minimum_lgtm:
            if lgtm_count == len(all_reviewers_without_pr_owner):
                self.logger.debug(
                    f"{self.log_prefix} minimum_lgtm is {self.github_webhook.minimum_lgtm}, but number of "
                    f"reviewers is {len(all_reviewers_without_pr_owner)}. PR approved."
                )
            else:
                error += (
                    f"Missing lgtm from reviewers. Minimum {self.github_webhook.minimum_lgtm} required, "
                    f"({lgtm_count} given). Reviewers: {', '.join(all_reviewers_without_pr_owner)}.\n"
                )

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('pr_handler', 'approval', 'completed')} PR approval check completed"
        )
        return error

    def _check_labels_for_can_be_merged(self, labels: list[str]) -> str:
        self.logger.debug(f"{self.log_prefix} _check_labels_for_can_be_merged.")
        failure_output = ""

        for _label in labels:
            if CHANGED_REQUESTED_BY_LABEL_PREFIX.lower() in _label.lower():
                change_request_user = _label.split(LABELS_SEPARATOR)[-1]
                if change_request_user in self.owners_file_handler.all_pull_request_approvers:
                    failure_output += "PR has changed requests from approvers\n"
                    self.logger.debug(f"Found changed request by {change_request_user}")

        missing_required_labels = []
        for _req_label in self.github_webhook.can_be_merged_required_labels:
            if _req_label not in labels:
                missing_required_labels.append(_req_label)
                self.logger.debug(f"Missing required label {_req_label}")

        if missing_required_labels:
            failure_output += f"Missing required labels: {', '.join(missing_required_labels)}\n"

        return failure_output

    def skip_if_pull_request_already_merged(self, pull_request: PullRequestWrapper) -> bool:
        if pull_request and pull_request.merged:
            self.logger.info(f"{self.log_prefix}: PR is merged, not processing")
            return True

        return False
