from __future__ import annotations

import asyncio
from typing import Any

from github.PullRequest import PullRequest

from webhook_server.libs.check_run_handler import CheckRunHandler
from webhook_server.libs.labels_handler import LabelsHandler
from webhook_server.libs.owners_files_handler import OwnersFileHandler
from webhook_server.libs.runner_handler import RunnerHandler
from webhook_server.utils.constants import (
    APPROVED_BY_LABEL_PREFIX,
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


class PullRequestHandler:
    def __init__(self, github_webhook: Any, owners_file_handler: OwnersFileHandler):
        self.github_webhook = github_webhook
        self.owners_file_handler = owners_file_handler

        self.hook_data = self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix = self.github_webhook.log_prefix
        self.repository = self.github_webhook.repository
        self.labels_handler = LabelsHandler(
            github_webhook=self.github_webhook, owners_file_handler=self.owners_file_handler
        )
        self.check_run_handler = CheckRunHandler(github_webhook=self.github_webhook)
        self.runner_handler = RunnerHandler(
            github_webhook=self.github_webhook, owners_file_handler=self.owners_file_handler
        )

    async def process_pull_request_webhook_data(self, pull_request: PullRequest) -> None:
        hook_action: str = self.hook_data["action"]
        self.logger.info(f"{self.log_prefix} hook_action is: {hook_action}")

        pull_request_data: dict[str, Any] = self.hook_data["pull_request"]

        if hook_action == "edited":
            await self.set_wip_label_based_on_title(pull_request=pull_request)

        if hook_action in ("opened", "reopened"):
            tasks = []

            if hook_action == "opened":
                welcome_msg = self._prepare_welcome_comment()
                tasks.append(asyncio.to_thread(pull_request.create_issue_comment, body=welcome_msg))

            tasks.append(self.create_issue_for_new_pull_request(pull_request=pull_request))
            tasks.append(self.set_wip_label_based_on_title(pull_request=pull_request))
            tasks.append(self.process_opened_or_synchronize_pull_request(pull_request=pull_request))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    self.logger.error(f"{self.log_prefix} Async task failed: {results}")

            # Set auto merge only after all initialization of a new PR is done.
            await self.set_pull_request_automerge(pull_request=pull_request)

        if hook_action == "synchronize":
            tasks = []

            tasks.append(self.process_opened_or_synchronize_pull_request(pull_request=pull_request))
            tasks.append(self.remove_labels_when_pull_request_sync(pull_request=pull_request))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    self.logger.error(f"{self.log_prefix} Async task failed: {result}")

        if hook_action == "closed":
            await self.close_issue_for_merged_or_closed_pr(pull_request=pull_request, hook_action=hook_action)
            await self.delete_remote_tag_for_merged_or_closed_pr(pull_request=pull_request)
            if is_merged := pull_request_data.get("merged", False):
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

            if labeled_lower == CAN_BE_MERGED_STR:
                return

            self.logger.info(f"{self.log_prefix} PR {pull_request.number} {hook_action} with {labeled}")

            _split_label = labeled.split(LABELS_SEPARATOR, 1)

            if len(_split_label) == 2:
                _lable_prefix, _user = _split_label

                if f"{_lable_prefix}{LABELS_SEPARATOR}" in (
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

            if self.github_webhook.verified_job and labeled_lower == VERIFIED_LABEL_STR:
                _check_for_merge = True

                if action_labeled:
                    await self.check_run_handler.set_verify_check_success()
                else:
                    await self.check_run_handler.set_verify_check_queued()

            if labeled_lower in (WIP_STR, HOLD_LABEL_STR):
                _check_for_merge = True

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
        return f"""
{self.github_webhook.issue_url_for_welcome_msg}

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
 * Minimum number of required `/lgtm` (`{self.github_webhook.minimum_lgtm}`) is met.

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

    def _prepare_owners_welcome_comment(self) -> str:
        body_approvers: str = " * Approvers:\n"
        body_reviewers: str = " * Reviewers:\n"

        for _approver in self.owners_file_handler.all_pull_request_approvers:
            body_approvers += f"   * {_approver}\n"

        for _reviewer in self.owners_file_handler.all_pull_request_reviewers:
            body_reviewers += f"   * {_reviewer}\n"

        return f"""
{body_approvers}

{body_reviewers}
"""

    @property
    def _prepare_retest_welcome_comment(self) -> str:
        retest_msg: str = ""
        if self.github_webhook.tox:
            retest_msg += f" * `/retest {TOX_STR}`: Retest tox\n"

        if self.github_webhook.build_and_push_container:
            retest_msg += f" * `/retest {BUILD_CONTAINER_STR}`: Retest build-container\n"

        if self.github_webhook.pypi:
            retest_msg += f" * `/retest {PYTHON_MODULE_INSTALL_STR}`: Retest python-module-install\n"

        if self.github_webhook.pre_commit:
            retest_msg += f" * `/retest {PRE_COMMIT_STR}`: Retest pre-commit\n"

        if self.github_webhook.conventional_title:
            retest_msg += f" * `/retest {CONVENTIONAL_TITLE_STR}`: Retest conventional-title\n"

        if retest_msg:
            retest_msg += " * `/retest all`: Retest all\n"

        return " * This repository does not support retest actions" if not retest_msg else retest_msg

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
        tasks = []

        tasks.append(self.owners_file_handler.assign_reviewers(pull_request=pull_request))
        tasks.append(
            self.labels_handler._add_label(
                **{
                    "pull_request": pull_request,
                    "label": f"{BRANCH_LABEL_PREFIX}{pull_request.base.ref}",
                },
            )
        )
        tasks.append(self.label_pull_request_by_merge_state(pull_request=pull_request))
        tasks.append(self.check_run_handler.set_merge_check_queued())
        tasks.append(self.check_run_handler.set_run_tox_check_queued())
        tasks.append(self.check_run_handler.set_run_pre_commit_check_queued())
        tasks.append(self.check_run_handler.set_python_module_install_queued())
        tasks.append(self.check_run_handler.set_container_build_queued())
        tasks.append(self._process_verified_for_update_or_new_pull_request(pull_request=pull_request))
        tasks.append(self.labels_handler.add_size_label(pull_request=pull_request))
        tasks.append(self.add_pull_request_owner_as_assingee(pull_request=pull_request))

        tasks.append(self.runner_handler.run_tox(pull_request=pull_request))
        tasks.append(self.runner_handler.run_pre_commit(pull_request=pull_request))
        tasks.append(self.runner_handler.run_install_python_module(pull_request=pull_request))
        tasks.append(self.runner_handler.run_build_container(pull_request=pull_request))

        if self.github_webhook.conventional_title:
            tasks.append(self.check_run_handler.set_conventional_title_queued())
            tasks.append(self.runner_handler.run_conventional_title_check(pull_request=pull_request))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                self.logger.error(f"{self.log_prefix} Async task failed: {result}")

    async def create_issue_for_new_pull_request(self, pull_request: PullRequest) -> None:
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
        auto_merge = (
            pull_request.base.ref in self.github_webhook.set_auto_merge_prs
            or self.github_webhook.parent_committer in self.github_webhook.auto_verified_and_merged_users
        )

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
        tasks = []
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
                        **{
                            "pull_request": pull_request,
                            "label": _label_name,
                        },
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

            if self.github_webhook.root_approvers:
                self.logger.debug(f"{self.log_prefix} Falling back to first approver as assignee")
                pull_request.add_to_assignees(self.github_webhook.root_approvers[0])

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

            labels_failure_output = self.labels_handler.wip_or_hold_lables_exists(labels=_labels)
            if labels_failure_output:
                failure_output += labels_failure_output

            required_check_failed_failure_output = await self.check_run_handler.required_check_failed_or_no_status(
                pull_request=pull_request,
                last_commit_check_runs=last_commit_check_runs,
                check_runs_in_progress=check_runs_in_progress,
            )
            if required_check_failed_failure_output:
                failure_output += required_check_failed_failure_output

            labels_failure_output = self._check_lables_for_can_be_merged(labels=_labels)
            if labels_failure_output:
                failure_output += labels_failure_output

            pr_approvered_failure_output = await self._check_if_pr_approved(labels=_labels)
            if pr_approvered_failure_output:
                failure_output += pr_approvered_failure_output

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

        error: str = ""
        approved_by = []
        lgtm_count: int = 0

        all_reviewers = (
            self.owners_file_handler.all_pull_request_reviewers.copy()
            + self.owners_file_handler.root_approvers.copy()
            + self.owners_file_handler.root_reviewers.copy()
        )
        all_reviewers_without_pr_owner = {
            _reviewer for _reviewer in all_reviewers if _reviewer != self.github_webhook.parent_committer
        }

        if self.github_webhook.minimum_lgtm:
            for _label in labels:
                reviewer = _label.split(LABELS_SEPARATOR)[-1]
                if LGTM_BY_LABEL_PREFIX.lower() in _label.lower() and reviewer in all_reviewers_without_pr_owner:
                    lgtm_count += 1

        for _label in labels:
            if APPROVED_BY_LABEL_PREFIX.lower() in _label.lower():
                approved_by.append(_label.split(LABELS_SEPARATOR)[-1])

        missing_approvers = list(set(self.owners_file_handler.all_pull_request_approvers.copy()))
        owners_data_changed_files = await self.owners_file_handler.owners_data_for_changed_files()

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
                    f"Minimum {self.github_webhook.minimum_lgtm} required. Reviewers: {', '.join(all_reviewers_without_pr_owner)}.\n"
                )

        return error

    def _check_lables_for_can_be_merged(self, labels: list[str]) -> str:
        self.logger.debug(f"{self.log_prefix} _check_lables_for_can_be_merged.")
        failure_output = ""

        for _label in labels:
            if CHANGED_REQUESTED_BY_LABEL_PREFIX.lower() in _label.lower():
                change_request_user = _label.split(LABELS_SEPARATOR)[-1]
                if change_request_user in self.owners_file_handler.all_pull_request_approvers:
                    failure_output += "PR has changed requests from approvers\n"

        missing_required_labels = []
        for _req_label in self.github_webhook.can_be_merged_required_labels:
            if _req_label not in labels:
                missing_required_labels.append(_req_label)

        if missing_required_labels:
            failure_output += f"Missing required labels: {', '.join(missing_required_labels)}\n"

        return failure_output

    def skip_if_pull_request_already_merged(self, pull_request: PullRequest) -> bool:
        if pull_request and pull_request.is_merged():
            self.logger.info(f"{self.log_prefix}: PR is merged, not processing")
            return True

        return False
