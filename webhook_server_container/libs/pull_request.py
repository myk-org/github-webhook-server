import contextlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import shortuuid
from github import GithubException

from webhook_server_container.libs.check_runs import CheckRuns
from webhook_server_container.libs.labels import Labels
from webhook_server_container.libs.logs import Logs
from webhook_server_container.utils.constants import (
    ADD_STR,
    APPROVED_BY_LABEL_PREFIX,
    BRANCH_LABEL_PREFIX,
    BUILD_AND_PUSH_CONTAINER_STR,
    BUILD_CONTAINER_STR,
    CAN_BE_MERGED_STR,
    CHANGED_REQUESTED_BY_LABEL_PREFIX,
    CHERRY_PICK_LABEL_PREFIX,
    CHERRY_PICKED_LABEL_PREFIX,
    COMMENTED_BY_LABEL_PREFIX,
    DELETE_STR,
    FLASK_APP,
    HOLD_LABEL_STR,
    IN_PROGRESS_STR,
    LGTM_STR,
    NEEDS_REBASE_LABEL_STR,
    PRE_COMMIT_CI_BOT_USER,
    PYTHON_MODULE_INSTALL_STR,
    REACTIONS,
    SONARQUBE_STR,
    SUCCESS_STR,
    TOX_STR,
    USER_LABELS_DICT,
    VERIFIED_LABEL_STR,
    WIP_STR,
)
from webhook_server_container.utils.helpers import (
    check_rate_limit,
    extract_key_from_dict,
    ignore_exceptions,
)


class PullRequest(CheckRuns, Labels):
    def __init__(self, hook_data, repositories_app_api, missing_app_repositories):
        super(CheckRuns, self).__init__(
            hook_data=hook_data,
            repositories_app_api=repositories_app_api,
            missing_app_repositories=missing_app_repositories,
        )
        super(Labels, self).__init__(
            hook_data=hook_data,
            repositories_app_api=repositories_app_api,
            missing_app_repositories=missing_app_repositories,
        )

        check_rate_limit(github_api=self.github_api)
        self.pull_request = self.get_pull_request()
        if not self.pull_request:
            return

        self.last_commit = self.get_last_commit()
        self.container_repository_and_tag = self._container_repository_and_tag()

        log = Logs(repository_name=self.repository_name)
        self.logger = log.logger
        self.log_prefix = log.log_prefix

        self.supported_user_labels_str = "".join(
            [f" * {label}\n" for label in USER_LABELS_DICT.keys()]
        )
        self.welcome_msg = f"""
Report bugs in [Issues](https://github.com/myakove/github-webhook-server/issues)

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
 * To re-run tox comment `/retest tox` in the PR.
 * To re-run build-container command `/retest build-container` in the PR.
 * To re-run python-module-install command `/retest python-module-install` in the PR.
 * To build and push container image command `/build-and-push-container` in the PR (tag will be the PR number).
 * To add a label by comment use `/<label name>`, to remove, use `/<label name> cancel`
<details>
<summary>Supported labels</summary>

{self.supported_user_labels_str}
</details>
    """

    def get_pull_request(self, number=None):
        if number:
            return self.repository.get_pull(number)

        for _number in extract_key_from_dict(key="number", _dict=self.hook_data):
            try:
                return self.repository.get_pull(_number)
            except GithubException:
                continue

        commit = self.hook_data.get("commit")
        if commit:
            commit_obj = self.repository.get_commit(commit["sha"])
            with contextlib.suppress(Exception):
                return commit_obj.get_pulls()[0]

        self.logger.info(
            f"{self.log_prefix} No issue or pull_request found in hook data"
        )

    def get_last_commit(self):
        return list(self.pull_request.get_commits())[-1]

    @ignore_exceptions(FLASK_APP.logger)
    def create_issue_for_new_pull_request(self, parent_committer, api_user):
        if parent_committer in (
            api_user,
            PRE_COMMIT_CI_BOT_USER,
        ):
            return
        self.logger.info(
            f"{self.log_prefix} Creating issue for new PR: {self.pull_request.title}"
        )
        self.repository.create_issue(
            title=self._generate_issue_title(),
            body=self._generate_issue_body(),
            assignee=self.pull_request.user.login,
        )

    def _generate_issue_title(self):
        return f"{self.pull_request.title} - {self.pull_request.number}"

    def _generate_issue_body(self):
        return f"[Auto generated]\nNumber: [#{self.pull_request.number}]"

    def create_comment_reaction(self, issue_comment_id, reaction):
        _comment = self.pull_request.get_issue_comment(issue_comment_id)
        _comment.create_reaction(reaction)

    def _container_repository_and_tag(self):
        tag = (
            self.container_tag
            if self.pull_request.is_merged()
            else self.pull_request.number
        )
        return f"{self.container_repository}:{tag}"

    @staticmethod
    def _comment_with_details(title, body):
        return f"""
<details>
<summary>{title}</summary>
    {body}
</details>
        """

    def process_opened_or_synchronize_pull_request(
        self, parent_committer, pull_request_branch, verified_job, api_user, reviewers
    ):
        self.set_merge_check_queued(last_commit=self.last_commit)
        self.set_run_tox_check_queued(
            tox_enabled=self.tox_enabled, last_commit=self.last_commit
        )
        self.set_python_module_install_queued(
            pypi=self.pypi, last_commit=self.last_commit
        )
        self.set_container_build_queued(
            build_and_push_container=self.build_and_push_container,
            last_commit=self.last_commit,
        )
        self.set_sonarqube_queued(
            sonarqube_project_key=self.sonarqube_project_key,
            last_commit=self.last_commit,
        )
        self._process_verified(
            parent_committer=parent_committer,
            verified_job=verified_job,
            api_user=api_user,
        )
        self.add_size_label()
        self.add_label(label=f"{BRANCH_LABEL_PREFIX}{pull_request_branch}")
        self.logger.info(f"{self.log_prefix} Adding PR owner as assignee")
        self.pull_request.add_to_assignees(parent_committer)
        self.assign_reviewers(reviewers=reviewers)

        futures = []
        with ThreadPoolExecutor() as executor:
            futures.append(
                executor.submit(
                    self.run_sonarqube,
                    self.sonarqube_project_key,
                    self.sonarqube_url,
                    self.sonarqube_api,
                    self.last_commit,
                )
            )
            futures.append(
                executor.submit(self.run_tox, self.tox_enabled, self.last_commit)
            )
            futures.append(
                executor.submit(self.install_python_module, self.pypi, self.last_commit)
            )
            futures.append(
                executor.submit(
                    self.build_container,
                    self.last_commit,
                    self.pull_request,
                    self.container_repository_and_tag,
                )
            )

        for _ in as_completed(futures):
            pass

    def skip_merged_pull_request(self):
        if self.pull_request.is_merged():
            self.logger.info(f"{self.log_prefix}: PR is merged, not processing")
            return True

    def assign_reviewers(self, reviewers):
        for reviewer in reviewers:
            if reviewer != self.pull_request.user.login:
                self.logger.info(f"{self.log_prefix} Adding reviewer {reviewer}")
                try:
                    self.pull_request.create_review_request([reviewer])
                except GithubException as ex:
                    self.logger.error(
                        f"{self.log_prefix} Failed to add reviewer {reviewer}. {ex}"
                    )

    def _process_verified(self, parent_committer, verified_job, api_user):
        if not verified_job:
            return

        if parent_committer in (api_user, PRE_COMMIT_CI_BOT_USER):
            self.logger.info(
                f"{self.log_prefix} Committer {parent_committer} == API user "
                f"{parent_committer}, Setting verified label"
            )
            self.add_label(label=VERIFIED_LABEL_STR, pull_request=self.pull_request)
            self.set_verify_check_success()
        else:
            self.reset_verify_label(pull_request=self.pull_request)
            self.set_verify_check_queued(last_commit=self.last_commit)

    def needs_rebase(self):
        for pull_request in self.repository.get_pulls():
            self.logger.info(
                f"{self.log_prefix} "
                "Sleep for 30 seconds before checking if rebase needed"
            )
            time.sleep(30)
            merge_state = pull_request.mergeable_state
            self.logger.info(f"{self.log_prefix} Mergeable state is {merge_state}")
            if merge_state == "behind":
                self.add_label(
                    label=NEEDS_REBASE_LABEL_STR, pull_request=self.pull_request
                )
            else:
                self.remove_label(
                    label=NEEDS_REBASE_LABEL_STR, pull_request=self.pull_request
                )

    def close_issue_for_merged_or_closed_pr(self, hook_action):
        for issue in self.repository.get_issues():
            if issue.body == self._generate_issue_body():
                self.logger.info(
                    f"{self.log_prefix} Closing issue {issue.title} for PR: "
                    f"{self.pull_request.title}"
                )
                issue.create_comment(
                    f"{self.log_prefix} Closing issue for PR: "
                    f"{self.pull_request.title}.\nPR was {hook_action}."
                )
                issue.edit(state="closed")
                break

    def cherry_pick(self, target_branch, github_token, reviewed_user=None):
        @ignore_exceptions()
        def is_branch_exists(self, branch):
            return self.repository.get_branch(branch)

        requested_by = reviewed_user or "by target-branch label"
        self.logger.info(
            f"{self.log_prefix} Cherry-pick requested by user: {requested_by}"
        )

        new_branch_name = f"{CHERRY_PICKED_LABEL_PREFIX}-{self.pull_request.head.ref}-{shortuuid.uuid()[:5]}"
        if not is_branch_exists(branch=target_branch):
            err_msg = f"cherry-pick failed: {target_branch} does not exists"
            self.logger.error(err_msg)
            self.pull_request.create_issue_comment(err_msg)
        else:
            self.set_cherry_pick_in_progress()
            file_path, url_path = self._get_check_run_result_file_path(
                check_run=CHERRY_PICKED_LABEL_PREFIX
            )
            commit_hash = self.pull_request.merge_commit_sha
            commit_msg = self.pull_request.title
            pull_request_url = self.pull_request.html_url
            env = f"-e GITHUB_TOKEN={github_token}"
            cmd = (
                f" git checkout {target_branch}"
                f" && git pull origin {target_branch}"
                f" && git checkout -b {new_branch_name} origin/{target_branch}"
                f" && git cherry-pick {commit_hash}"
                f" && git push origin {new_branch_name}"
                f" && hub pull-request "
                f"-b {target_branch} "
                f"-h {new_branch_name} "
                f"-l {CHERRY_PICKED_LABEL_PREFIX} "
                f'-m "{CHERRY_PICKED_LABEL_PREFIX}: [{target_branch}] {commit_msg}" '
                f'-m "cherry-pick {pull_request_url} into {target_branch}" '
                f'-m "requested-by {requested_by}"'
            )
            rc, out, err = self._run_in_container(
                command=cmd, env=env, file_path=file_path
            )
            if rc:
                self.set_cherry_pick_success(details_url=url_path)
                self.pull_request.create_issue_comment(
                    f"Cherry-picked PR {self.pull_request.title} into {target_branch}"
                )
            else:
                self.set_cherry_pick_failure(details_url=url_path)
                self.logger.error(
                    f"{self.log_prefix} Cherry pick failed: {out} --- {err}"
                )
                local_branch_name = f"{self.pull_request.head.ref}-{target_branch}"
                self.pull_request.create_issue_comment(
                    f"**Manual cherry-pick is needed**\nCherry pick failed for "
                    f"{commit_hash} to {target_branch}:\n"
                    f"To cherry-pick run:\n"
                    "```\n"
                    f"git checkout {target_branch}\n"
                    f"git pull origin {target_branch}\n"
                    f"git checkout -b {local_branch_name}\n"
                    f"git cherry-pick {commit_hash}\n"
                    f"git push origin {local_branch_name}\n"
                    "```"
                )

    def check_if_can_be_merged(self, approvers, last_commit):
        """
        Check if PR can be merged and set the job for it

        Check the following:
            Has verified label.
            Has approved from one of the approvers.
            All required run check passed.
            PR status is 'clean'.
            PR has no changed requests from approvers.
        """
        if self.skip_merged_pull_request():
            return False

        if self.is_check_run_in_progress(
            check_run=CAN_BE_MERGED_STR, last_commit=last_commit
        ):
            self.logger.info(
                f"{self.log_prefix} Check run is in progress, not running {CAN_BE_MERGED_STR}."
            )
            return False

        self.logger.info(f"{self.log_prefix} Check if {CAN_BE_MERGED_STR}.")
        last_commit_check_runs = list(self.last_commit.get_check_runs())
        check_runs_in_progress = [
            check_run.name
            for check_run in last_commit_check_runs
            if check_run.status == IN_PROGRESS_STR
            and check_run.name != CAN_BE_MERGED_STR
        ]
        if check_runs_in_progress:
            self.logger.info(
                f"{self.log_prefix} Some check runs in progress {check_runs_in_progress}, "
                f"skipping check if {CAN_BE_MERGED_STR}."
            )
            return False

        try:
            self.set_merge_check_in_progress()
            _labels = self.pull_request_labels_names(pull_request=self.pull_request)

            if VERIFIED_LABEL_STR not in _labels or HOLD_LABEL_STR in _labels:
                self.remove_label(
                    label=CAN_BE_MERGED_STR, pull_request=self.pull_request
                )
                self.set_merge_check_queued()
                return False

            if self.pull_request.mergeable_state == "behind":
                self.remove_label(
                    label=CAN_BE_MERGED_STR, pull_request=self.pull_request
                )
                self.set_merge_check_queued()
                return False

            all_check_runs_passed = all(
                [
                    check_run.conclusion == SUCCESS_STR
                    for check_run in last_commit_check_runs
                    if check_run.name != CAN_BE_MERGED_STR
                ]
            )
            if not all_check_runs_passed:
                self.remove_label(
                    label=CAN_BE_MERGED_STR, pull_request=self.pull_request
                )
                self.set_merge_check_queued()
                # TODO: Fix `run_retest_if_queued` and uncomment the call for it.
                # self.run_retest_if_queued(last_commit_check_runs=last_commit_check_runs)
                return False

            for _label in _labels:
                if CHANGED_REQUESTED_BY_LABEL_PREFIX.lower() in _label.lower():
                    change_request_user = _label.split("-")[-1]
                    if change_request_user in approvers:
                        self.remove_label(
                            label=CAN_BE_MERGED_STR, pull_request=self.pull_request
                        )
                        return self.set_merge_check_queued()

            for _label in _labels:
                if APPROVED_BY_LABEL_PREFIX.lower() in _label.lower():
                    approved_user = _label.split("-")[-1]
                    if approved_user in approvers:
                        self.add_label(
                            label=CAN_BE_MERGED_STR, pull_request=self.pull_request
                        )
                        return self.set_merge_check_success()

            return self.set_merge_check_queued()
        except Exception:
            return self.set_merge_check_queued()

    def manage_reviewed_by_label(self, review_state, action, reviewed_user):
        self.logger.info(
            f"{self.log_prefix} "
            f"Processing label for review from {reviewed_user}. "
            f"review_state: {review_state}, action: {action}"
        )
        label_prefix = None
        label_to_remove = None

        pull_request_labels = self.pull_request_labels_names(
            pull_request=self.pull_request
        )

        if review_state in ("approved", LGTM_STR):
            base_dict = self.hook_data.get("issue", self.hook_data.get("pull_request"))
            pr_owner = base_dict["user"]["login"]
            if pr_owner == reviewed_user:
                self.logger.info(
                    f"{self.log_prefix} PR owner {pr_owner} set /lgtm, not adding label."
                )
                return

            label_prefix = APPROVED_BY_LABEL_PREFIX
            _remove_label = f"{CHANGED_REQUESTED_BY_LABEL_PREFIX}{reviewed_user}"
            if _remove_label in pull_request_labels:
                label_to_remove = _remove_label

        elif review_state == "changes_requested":
            label_prefix = CHANGED_REQUESTED_BY_LABEL_PREFIX
            _remove_label = f"{APPROVED_BY_LABEL_PREFIX}{reviewed_user}"
            if _remove_label in pull_request_labels:
                label_to_remove = _remove_label

        elif review_state == "commented":
            label_prefix = COMMENTED_BY_LABEL_PREFIX

        if label_prefix:
            reviewer_label = f"{label_prefix}{reviewed_user}"

            if action == ADD_STR:
                self.add_label(label=reviewer_label, pull_request=self.pull_request)
                if label_to_remove:
                    self.remove_label(
                        label=label_to_remove, pull_request=self.pull_request
                    )

            if action == DELETE_STR:
                self.remove_label(label=reviewer_label, pull_request=self.pull_request)
        else:
            self.logger.warning(
                f"{self.log_prefix} PR {self.pull_request.number} got unsupported review state: {review_state}"
            )

    def label_by_user_comment(
        self, user_request, remove, reviewed_user, issue_comment_id
    ):
        if not any(
            user_request.startswith(label_name) for label_name in USER_LABELS_DICT
        ):
            self.logger.info(
                f"{self.log_prefix} "
                f"Label {user_request} is not a predefined one, "
                "will not be added / removed."
            )
            self.pull_request.create_issue_comment(
                body=f"""
Label {user_request} is not a predefined one, will not be added / removed.
Available labels:

{self.supported_user_labels_str}
""",
            )
            return

        self.logger.info(
            f"{self.log_prefix} {'Remove' if remove else 'Add'} "
            f"label requested by user {reviewed_user}: {user_request}"
        )
        self.create_comment_reaction(
            issue_comment_id=issue_comment_id,
            reaction=REACTIONS.ok,
        )

        if user_request == LGTM_STR:
            self.manage_reviewed_by_label(
                review_state=LGTM_STR,
                action=DELETE_STR if remove else ADD_STR,
                reviewed_user=reviewed_user,
            )

        else:
            label_func = self.remove_label if remove else self.add_label
            label_func(label=user_request)

    def user_commands(
        self,
        command,
        reviewed_user,
        issue_comment_id,
        github_token,
        tox_enabled,
        build_and_push_container,
        pypi,
        sonarqube_project_key,
    ):
        remove = False
        available_commands = ["retest", "cherry-pick"]
        if "sonarsource.github.io" in command:
            self.logger.info(f"{self.log_prefix} command is in ignore list")
            return

        self.logger.info(
            f"{self.log_prefix} Processing label/user command {command} "
            f"by user {reviewed_user}"
        )
        command_and_args = command.split(" ", 1)
        _command = command_and_args[0]
        not_running_msg = f"Pull request already merged, not running {_command}"
        _args = command_and_args[1] if len(command_and_args) > 1 else ""
        if len(command_and_args) > 1 and _args == "cancel":
            self.logger.info(
                f"{self.log_prefix} User requested 'cancel' for command {_command}"
            )
            remove = True

        if _command in available_commands:
            if not _args:
                issue_msg = f"{_command} requires an argument"
                error_msg = f"{self.log_prefix} {issue_msg}"
                self.logger.info(error_msg)
                self.pull_request.create_issue_comment(issue_msg)
                return

            if _command == "cherry-pick":
                self.create_comment_reaction(
                    issue_comment_id=issue_comment_id,
                    reaction=REACTIONS.ok,
                )
                _target_branches = _args.split()
                _exits_target_branches = set()
                _non_exits_target_branches_msg = ""

                for _target_branch in _target_branches:
                    try:
                        self.repository.get_branch(_target_branch)
                    except Exception:
                        _non_exits_target_branches_msg += (
                            f"Target branch `{_target_branch}` does not exist\n"
                        )

                    _exits_target_branches.add(_target_branch)

                if _non_exits_target_branches_msg:
                    self.logger.info(
                        f"{self.log_prefix} {_non_exits_target_branches_msg}"
                    )
                    self.pull_request.create_issue_comment(
                        _non_exits_target_branches_msg
                    )

                if _exits_target_branches:
                    if not self.pull_request.is_merged():
                        cp_labels = [
                            f"{CHERRY_PICK_LABEL_PREFIX}{_target_branch}"
                            for _target_branch in _exits_target_branches
                        ]
                        info_msg = f"""
Cherry-pick requested for PR: `{self.pull_request.title}` by user `{reviewed_user}`
Adding label/s `{' '.join([_cp_label for _cp_label in cp_labels])}` for automatic cheery-pick once the PR is merged
"""
                        self.logger.info(f"{self.log_prefix} {info_msg}")
                        self.pull_request.create_issue_comment(info_msg)
                        for _cp_label in cp_labels:
                            self.add_label(
                                label=_cp_label, pull_request=self.pull_request
                            )
                    else:
                        for _exits_target_branch in _exits_target_branches:
                            self.cherry_pick(
                                target_branch=_exits_target_branch,
                                github_token=github_token,
                                reviewed_user=reviewed_user,
                            )

            elif _command == "retest":
                if self.skip_merged_pull_request():
                    return self.pull_request.create_issue_comment(not_running_msg)

                _target_tests = _args.split()
                for _test in _target_tests:
                    if _test == TOX_STR:
                        if not tox_enabled:
                            msg = f"No {TOX_STR} configured for this repository"
                            error_msg = f"{self.log_prefix} {msg}."
                            self.logger.info(error_msg)
                            self.pull_request.create_issue_comment(msg)
                            return

                        self.create_comment_reaction(
                            issue_comment_id=issue_comment_id,
                            reaction=REACTIONS.ok,
                        )
                        self.run_tox()

                    elif _test == BUILD_CONTAINER_STR:
                        if build_and_push_container:
                            self.create_comment_reaction(
                                issue_comment_id=issue_comment_id,
                                reaction=REACTIONS.ok,
                            )
                            self.build_container(
                                last_commit=self.last_commit,
                                pull_request=self.pull_request,
                                container_repository_and_tag=self.container_repository_and_tag,
                            )
                        else:
                            msg = f"No {BUILD_CONTAINER_STR} configured for this repository"
                            error_msg = f"{self.log_prefix} {msg}"
                            self.logger.info(error_msg)
                            self.pull_request.create_issue_comment(msg)

                    elif _test == PYTHON_MODULE_INSTALL_STR:
                        if not pypi:
                            error_msg = f"{self.log_prefix} No pypi configured"
                            self.logger.info(error_msg)
                            self.pull_request.create_issue_comment(error_msg)
                            return

                        self.create_comment_reaction(
                            issue_comment_id=issue_comment_id,
                            reaction=REACTIONS.ok,
                        )
                        self.install_python_module()

                    elif _test == SONARQUBE_STR:
                        if not sonarqube_project_key:
                            msg = f"No {SONARQUBE_STR} configured for this repository"
                            error_msg = f"{self.log_prefix} {msg}"
                            self.logger.info(error_msg)
                            self.pull_request.create_issue_comment(msg)
                            return

                        self.create_comment_reaction(
                            issue_comment_id=issue_comment_id,
                            reaction=REACTIONS.ok,
                        )
                        self.run_sonarqube()

        elif _command == BUILD_AND_PUSH_CONTAINER_STR:
            if build_and_push_container:
                self.create_comment_reaction(
                    issue_comment_id=issue_comment_id,
                    reaction=REACTIONS.ok,
                )
                self.build_container(
                    last_commit=self.last_commit,
                    pull_request=self.pull_request,
                    container_repository_and_tag=self.container_repository_and_tag,
                    push=True,
                )
            else:
                msg = (
                    f"No {BUILD_AND_PUSH_CONTAINER_STR} configured for this repository"
                )
                error_msg = f"{self.log_prefix} {msg}"
                self.logger.info(error_msg)
                self.pull_request.create_issue_comment(msg)

        elif _command == WIP_STR:
            if self.skip_merged_pull_request():
                return self.pull_request.create_issue_comment(not_running_msg)

            self.create_comment_reaction(
                issue_comment_id=issue_comment_id,
                reaction=REACTIONS.ok,
            )
            wip_for_title = f"{WIP_STR.upper()}:"
            if remove:
                self.remove_label(label=WIP_STR, pull_request=self.pull_request)
                self.pull_request.edit(
                    title=self.pull_request.title.replace(wip_for_title, "")
                )
            else:
                self.add_label(label=WIP_STR, pull_request=self.pull_request)
                self.pull_request.edit(
                    title=f"{wip_for_title} {self.pull_request.title}"
                )

        else:
            if self.skip_merged_pull_request():
                return self.pull_request.create_issue_comment(not_running_msg)

            self.label_by_user_comment(
                user_request=_command,
                remove=remove,
                reviewed_user=reviewed_user,
                issue_comment_id=issue_comment_id,
            )
