from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Coroutine

from github.PullRequest import PullRequest
from github.Repository import Repository

from webhook_server.libs.check_run_handler import CheckRunHandler
from webhook_server.libs.labels_handler import LabelsHandler
from webhook_server.libs.owners_files_handler import OwnersFileHandler
from webhook_server.libs.runner_handler import RunnerHandler
from webhook_server.utils.constants import (
    APPROVED_BY_LABEL_PREFIX,
    AUTOMERGE_LABEL_STR,
    BRANCH_LABEL_PREFIX,
    BUILD_CONTAINER_STR,
    CAN_BE_MERGED_STR,
    CHANGED_REQUESTED_BY_LABEL_PREFIX,
    CHERRY_PICK_LABEL_PREFIX,
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

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook


class PullRequestHandler:
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
        self.check_run_handler = CheckRunHandler(
            github_webhook=self.github_webhook, owners_file_handler=self.owners_file_handler
        )
        self.runner_handler = RunnerHandler(
            github_webhook=self.github_webhook, owners_file_handler=self.owners_file_handler
        )

    async def process_pull_request_webhook_data(self, pull_request: PullRequest) -> None:
        hook_action: str = self.hook_data["action"]
        self.logger.step(f"{self.log_prefix} Starting pull request processing: action={hook_action}")  # type: ignore
        self.logger.info(f"{self.log_prefix} hook_action is: {hook_action}")
        self.logger.debug(f"{self.log_prefix} pull_request: {pull_request.title} ({pull_request.number})")

        pull_request_data: dict[str, Any] = self.hook_data["pull_request"]

        if hook_action == "edited":
            await self.set_wip_label_based_on_title(pull_request=pull_request)
            if self.github_webhook.conventional_title and self.hook_data["changes"].get("title"):
                self.logger.info(f"{self.log_prefix} PR title changed, running conventional title check")
                await self.runner_handler.run_conventional_title_check(pull_request=pull_request)

        if hook_action in ("opened", "reopened", "ready_for_review"):
            self.logger.step(f"{self.log_prefix} Processing PR {hook_action} event: initializing new pull request")  # type: ignore
            tasks: list[Coroutine[Any, Any, Any]] = []

            if hook_action in ("opened", "ready_for_review"):
                welcome_msg = self._prepare_welcome_comment()
                tasks.append(asyncio.to_thread(pull_request.create_issue_comment, body=welcome_msg))

            tasks.append(self.create_issue_for_new_pull_request(pull_request=pull_request))
            tasks.append(self.set_wip_label_based_on_title(pull_request=pull_request))
            tasks.append(self.process_opened_or_synchronize_pull_request(pull_request=pull_request))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    self.logger.error(f"{self.log_prefix} Async task failed: {result}")

            # Set auto merge only after all initialization of a new PR is done.
            await self.set_pull_request_automerge(pull_request=pull_request)

        if hook_action == "synchronize":
            self.logger.step(f"{self.log_prefix} Processing PR synchronize event: handling new commits")  # type: ignore
            sync_tasks: list[Coroutine[Any, Any, Any]] = []

            sync_tasks.append(self.process_opened_or_synchronize_pull_request(pull_request=pull_request))
            sync_tasks.append(self.remove_labels_when_pull_request_sync(pull_request=pull_request))

            results = await asyncio.gather(*sync_tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    self.logger.error(f"{self.log_prefix} Async task failed: {result}")

        if hook_action == "closed":
            self.logger.step(f"{self.log_prefix} Processing PR closed event: cleaning up resources")  # type: ignore
            await self.close_issue_for_merged_or_closed_pr(pull_request=pull_request, hook_action=hook_action)
            await self.delete_remote_tag_for_merged_or_closed_pr(pull_request=pull_request)
            if is_merged := pull_request_data.get("merged", False):
                self.logger.step(f"{self.log_prefix} PR was merged: processing post-merge tasks")  # type: ignore
                self.logger.info(f"{self.log_prefix} PR is merged")

                for _label in pull_request.labels:
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

            self.logger.step(f"{self.log_prefix} Processing label {hook_action} event: {labeled}")  # type: ignore

            if labeled_lower == CAN_BE_MERGED_STR:
                return

            self.logger.info(f"{self.log_prefix} PR {pull_request.number} {hook_action} with {labeled}")
            self.logger.debug(f"PR labels are {pull_request.labels}")

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

    async def set_wip_label_based_on_title(self, pull_request: PullRequest) -> None:
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

        # Check if current user is auto-verified
        is_auto_verified = self.github_webhook.parent_committer in self.github_webhook.auto_verified_and_merged_users
        auto_verified_note = ""
        if is_auto_verified:
            auto_verified_note = """

> **Note**: You are an auto-verified user. Your PRs will be automatically verified and may be auto-merged when all requirements are met.
"""

        # Check if issue creation is enabled
        issue_creation_note = ""
        if self.github_webhook.create_issue_for_new_pr:
            issue_creation_note = "* **Issue Creation**: A tracking issue is created for this PR and will be closed when the PR is merged or closed\n"
        else:
            issue_creation_note = "* **Issue Creation**: Disabled for this repository\n"

        return f"""
{self.github_webhook.issue_url_for_welcome_msg}

## Welcome! 🎉

This pull request will be automatically processed with the following features:{auto_verified_note}

### 🔄 Automatic Actions
* **Reviewer Assignment**: Reviewers are automatically assigned based on the OWNERS file in the repository root
* **Size Labeling**: PR size labels (XS, S, M, L, XL, XXL) are automatically applied based on changes
{issue_creation_note}* **Pre-commit Checks**: [pre-commit](https://pre-commit.ci/) runs automatically if `.pre-commit-config.yaml` exists
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
        """
        time_sleep = 30
        self.logger.info(f"{self.log_prefix} Sleep for {time_sleep} seconds before getting all opened PRs")
        await asyncio.sleep(time_sleep)

        for pull_request in self.repository.get_pulls(state="open"):
            self.logger.info(f"{self.log_prefix} check label pull request after merge")
            await self.label_pull_request_by_merge_state(pull_request=pull_request)

    async def delete_remote_tag_for_merged_or_closed_pr(self, pull_request: PullRequest) -> None:
        self.logger.debug(f"{self.log_prefix} Checking if need to delete remote tag for {pull_request.number}")
        if not self.github_webhook.build_and_push_container:
            self.logger.info(f"{self.log_prefix} repository do not have container configured")
            return

        repository_full_tag = self.github_webhook.container_repository_and_tag(pull_request=pull_request)
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

        rc, out, err = await self.runner_handler.run_podman_command(command=reg_login_cmd)

        if rc:
            try:
                tag_ls_cmd = f"regctl tag ls {self.github_webhook.container_repository} --include {pr_tag}"
                rc, out, err = await self.runner_handler.run_podman_command(command=tag_ls_cmd)

                if rc and out:
                    tag_del_cmd = f"regctl tag delete {repository_full_tag}"

                    rc, _, _ = await self.runner_handler.run_podman_command(command=tag_del_cmd)
                    if rc:
                        await asyncio.to_thread(
                            pull_request.create_issue_comment, f"Successfully removed PR tag: {repository_full_tag}."
                        )
                    else:
                        self.logger.error(
                            f"{self.log_prefix} Failed to delete tag: {repository_full_tag}. OUT:{out}. ERR:{err}"
                        )
                else:
                    self.logger.warning(
                        f"{self.log_prefix} {pr_tag} tag not found in registry {self.github_webhook.container_repository}. "
                        f"OUT:{out}. ERR:{err}"
                    )
            finally:
                await self.runner_handler.run_podman_command(command="regctl registry logout")

        else:
            await asyncio.to_thread(
                pull_request.create_issue_comment,
                f"Failed to delete tag: {repository_full_tag}. Please delete it manually.",
            )
            self.logger.error(f"{self.log_prefix} Failed to delete tag: {repository_full_tag}. OUT:{out}. ERR:{err}")

    async def close_issue_for_merged_or_closed_pr(self, pull_request: PullRequest, hook_action: str) -> None:
        for issue in await asyncio.to_thread(self.repository.get_issues):
            if issue.body == self._generate_issue_body(pull_request=pull_request):
                self.logger.info(f"{self.log_prefix} Closing issue {issue.title} for PR: {pull_request.title}")
                await asyncio.to_thread(
                    issue.create_comment,
                    f"{self.log_prefix} Closing issue for PR: {pull_request.title}.\nPR was {hook_action}.",
                )
                await asyncio.to_thread(issue.edit, state="closed")

                break

    async def process_opened_or_synchronize_pull_request(self, pull_request: PullRequest) -> None:
        self.logger.step(f"{self.log_prefix} Starting PR processing workflow")  # type: ignore

        # Stage 1: Initial setup and check queue tasks
        self.logger.step(f"{self.log_prefix} Stage: Initial setup and check queuing")  # type: ignore
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
        setup_tasks.append(self.add_pull_request_owner_as_assingee(pull_request=pull_request))

        if self.github_webhook.conventional_title:
            setup_tasks.append(self.check_run_handler.set_conventional_title_queued())

        self.logger.step(f"{self.log_prefix} Executing setup tasks")  # type: ignore
        setup_results = await asyncio.gather(*setup_tasks, return_exceptions=True)

        for result in setup_results:
            if isinstance(result, Exception):
                self.logger.error(f"{self.log_prefix} Setup task failed: {result}")

        self.logger.step(f"{self.log_prefix} Setup tasks completed")  # type: ignore

        # Stage 2: CI/CD execution tasks
        self.logger.step(f"{self.log_prefix} Stage: CI/CD execution")  # type: ignore
        ci_tasks: list[Coroutine[Any, Any, Any]] = []

        ci_tasks.append(self.runner_handler.run_tox(pull_request=pull_request))
        ci_tasks.append(self.runner_handler.run_pre_commit(pull_request=pull_request))
        ci_tasks.append(self.runner_handler.run_install_python_module(pull_request=pull_request))
        ci_tasks.append(self.runner_handler.run_build_container(pull_request=pull_request))

        if self.github_webhook.conventional_title:
            ci_tasks.append(self.runner_handler.run_conventional_title_check(pull_request=pull_request))

        self.logger.step(f"{self.log_prefix} Executing CI/CD tasks")  # type: ignore
        ci_results = await asyncio.gather(*ci_tasks, return_exceptions=True)

        for result in ci_results:
            if isinstance(result, Exception):
                self.logger.error(f"{self.log_prefix} CI/CD task failed: {result}")

        self.logger.step(f"{self.log_prefix} PR processing workflow completed")  # type: ignore

    async def create_issue_for_new_pull_request(self, pull_request: PullRequest) -> None:
        if not self.github_webhook.create_issue_for_new_pr:
            self.logger.info(f"{self.log_prefix} Issue creation for new PRs is disabled for this repository")
            return

        if self.github_webhook.parent_committer in self.github_webhook.auto_verified_and_merged_users:
            self.logger.info(
                f"{self.log_prefix} Committer {self.github_webhook.parent_committer} is part of "
                f"{self.github_webhook.auto_verified_and_merged_users}, will not create issue."
            )
            return

        self.logger.info(f"{self.log_prefix} Creating issue for new PR: {pull_request.title}")
        await asyncio.to_thread(
            self.repository.create_issue,
            title=self._generate_issue_title(pull_request=pull_request),
            body=self._generate_issue_body(pull_request=pull_request),
            assignee=pull_request.user.login,
        )

    def _generate_issue_title(self, pull_request: PullRequest) -> str:
        return f"{pull_request.title} - {pull_request.number}"

    def _generate_issue_body(self, pull_request: PullRequest) -> str:
        return f"[Auto generated]\nNumber: [#{pull_request.number}]"

    async def set_pull_request_automerge(self, pull_request: PullRequest) -> None:
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
                if not pull_request.raw_data.get("auto_merge"):
                    self.logger.info(
                        f"{self.log_prefix} will be merged automatically. owner: {self.github_webhook.parent_committer} "
                        f"is part of auto merge enabled rules"
                    )

                    await asyncio.to_thread(pull_request.enable_automerge, merge_method="SQUASH")
                else:
                    self.logger.debug(f"{self.log_prefix} is already set to auto merge")

            except Exception as exp:
                self.logger.error(f"{self.log_prefix} Exception while setting auto merge: {exp}")

    async def remove_labels_when_pull_request_sync(self, pull_request: PullRequest) -> None:
        tasks: list[Coroutine[Any, Any, Any]] = []
        for _label in pull_request.labels:
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
                self.logger.error(f"{self.log_prefix} Async task failed: {result}")

    async def label_pull_request_by_merge_state(self, pull_request: PullRequest) -> None:
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

    async def _process_verified_for_update_or_new_pull_request(self, pull_request: PullRequest) -> None:
        if not self.github_webhook.verified_job:
            return

        if self.github_webhook.parent_committer in self.github_webhook.auto_verified_and_merged_users:
            self.logger.info(
                f"{self.log_prefix} Committer {self.github_webhook.parent_committer} is part of {self.github_webhook.auto_verified_and_merged_users}"
                ", Setting verified label"
            )
            await self.labels_handler._add_label(pull_request=pull_request, label=VERIFIED_LABEL_STR)
            await self.check_run_handler.set_verify_check_success()
        else:
            self.logger.info(f"{self.log_prefix} Processing reset {VERIFIED_LABEL_STR} label on new commit push")
            # Remove verified label
            await self.labels_handler._remove_label(pull_request=pull_request, label=VERIFIED_LABEL_STR)
            await self.check_run_handler.set_verify_check_queued()

    async def add_pull_request_owner_as_assingee(self, pull_request: PullRequest) -> None:
        try:
            self.logger.info(f"{self.log_prefix} Adding PR owner as assignee")
            pull_request.add_to_assignees(pull_request.user.login)
        except Exception as exp:
            self.logger.debug(f"{self.log_prefix} Exception while adding PR owner as assignee: {exp}")

            if self.owners_file_handler.root_approvers:
                self.logger.debug(f"{self.log_prefix} Falling back to first approver as assignee")
                pull_request.add_to_assignees(self.owners_file_handler.root_approvers[0])

    async def check_if_can_be_merged(self, pull_request: PullRequest) -> None:
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
        self.logger.step(f"{self.log_prefix} Starting merge eligibility check")  # type: ignore
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
            _last_commit_check_runs = await asyncio.to_thread(self.github_webhook.last_commit.get_check_runs)
            last_commit_check_runs = list(_last_commit_check_runs)
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

            labels_failure_output = self.labels_handler.wip_or_hold_lables_exists(labels=_labels)
            if labels_failure_output:
                failure_output += labels_failure_output
            self.logger.debug(f"{self.log_prefix} wip_or_hold_lables_exists: {failure_output}")

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

                self.logger.info(f"{self.log_prefix} Pull request can be merged")
                return

            self.logger.debug(f"{self.log_prefix} cannot be merged: {failure_output}")
            output["text"] = failure_output
            await self.labels_handler._remove_label(pull_request=pull_request, label=CAN_BE_MERGED_STR)
            await self.check_run_handler.set_merge_check_failure(output=output)

        except Exception as ex:
            self.logger.error(
                f"{self.log_prefix} Failed to check if can be merged, set check run to {FAILURE_STR} {ex}"
            )
            _err = "Failed to check if can be merged, check logs"
            output["text"] = _err
            await self.labels_handler._remove_label(pull_request=pull_request, label=CAN_BE_MERGED_STR)
            await self.check_run_handler.set_merge_check_failure(output=output)

    async def _check_if_pr_approved(self, labels: list[str]) -> str:
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

        all_reviewers_without_pr_owner_and_lgtmed = all_reviewers_without_pr_owner.copy()

        if self.github_webhook.minimum_lgtm:
            for _label in labels:
                reviewer = _label.split(LABELS_SEPARATOR)[-1]
                if LGTM_BY_LABEL_PREFIX.lower() in _label.lower() and reviewer in all_reviewers_without_pr_owner:
                    lgtm_count += 1
                    all_reviewers_without_pr_owner_and_lgtmed.remove(reviewer)
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
                        # Once we found approver in approved_by list, we remove all approvers from missing_approvers list for this owners file
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
                    f"{self.log_prefix} minimum_lgtm is {self.github_webhook.minimum_lgtm}, but number of reviewers is {len(all_reviewers_without_pr_owner)}. PR approved."
                )
            else:
                error += (
                    "Missing lgtm from reviewers. "
                    f"Minimum {self.github_webhook.minimum_lgtm} required, ({lgtm_count} given). Reviewers: {', '.join(all_reviewers_without_pr_owner)}.\n"
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

    def skip_if_pull_request_already_merged(self, pull_request: PullRequest) -> bool:
        if pull_request and pull_request.is_merged():
            self.logger.info(f"{self.log_prefix}: PR is merged, not processing")
            return True

        return False
