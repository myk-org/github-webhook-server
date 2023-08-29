import re

import yaml
from github import UnknownObjectException

from webhook_server_container.libs.logs import Logs
from webhook_server_container.libs.pull_request import PullRequest
from webhook_server_container.utils.constants import (
    ADD_STR,
    CAN_BE_MERGED_STR,
    CHERRY_PICK_LABEL_PREFIX,
    PRE_COMMIT_CI_BOT_USER,
    VERIFIED_LABEL_STR,
)
from webhook_server_container.utils.helpers import (
    check_rate_limit,
    ignore_exceptions,
    send_slack_message,
)


class Repositories(PullRequest):
    def __init__(
        self, hook_data, github_event, repositories_app_api, missing_app_repositories
    ):
        super().__init__(
            hook_data=hook_data,
            github_event=github_event,
            repositories_app_api=repositories_app_api,
            missing_app_repositories=missing_app_repositories,
        )

        log = Logs(
            repository_name=self.repository_name,
            pull_request=self.pull_request,
            token=self.token,
        )
        self.logger = log.logger
        self.log_prefix = log.log_prefix

        self.logger.info(f"{self.log_prefix} Check rate limit")
        check_rate_limit()

    @property
    def owners_content(self):
        try:
            owners_content = self.repository.get_contents("OWNERS")
            return yaml.safe_load(owners_content.decoded_content)
        except UnknownObjectException:
            self.logger.error(f"{self.log_prefix} OWNERS file not found")
            return {}

    @property
    def reviewers(self):
        return self.owners_content.get("reviewers", [])

    @property
    def approvers(self):
        return self.owners_content.get("approvers", [])

    @ignore_exceptions()
    def is_branch_exists(self, branch):
        return self.repository.get_branch(branch)

    def process_comment_webhook_data(self):
        if self.hook_data["action"] in ("action", "deleted"):
            return

        issue_number = self.hook_data["issue"]["number"]
        self.logger.info(f"{self.log_prefix} Processing issue {issue_number}")

        if not self.pull_request:
            return

        body = self.hook_data["comment"]["body"]

        if body == self.welcome_msg:
            self.logger.info(
                f"{self.log_prefix} Welcome message found in issue "
                f"{self.pull_request.title}. Not processing"
            )
            return

        striped_body = body.strip()
        _user_commands = list(
            filter(
                lambda x: x,
                striped_body.split("/") if striped_body.startswith("/") else [],
            )
        )
        user_login = self.hook_data["sender"]["login"]
        for user_command in _user_commands:
            self.user_commands(
                command=user_command,
                reviewed_user=user_login,
                issue_comment_id=self.hook_data["comment"]["id"],
            )

    def process_pull_request_webhook_data(self):
        hook_action = self.hook_data["action"]
        self.logger.info(f"{self.log_prefix} hook_action is: {hook_action}")
        if not self.pull_request:
            return

        pull_request_data = self.hook_data["pull_request"]
        parent_committer = pull_request_data["user"]["login"]
        pull_request_branch = pull_request_data["base"]["ref"]

        if hook_action == "opened":
            self.logger.info(f"{self.log_prefix} Creating welcome comment")
            self.pull_request.create_issue_comment(self.welcome_msg)
            self.create_issue_for_new_pull_request(
                parent_committer=parent_committer, api_user=self.api_user
            )
            self.process_opened_or_synchronize_pull_request(
                parent_committer=parent_committer,
                pull_request_branch=pull_request_branch,
                api_user=self.api_user,
                reviewers=self.reviewers,
            )

        if hook_action == "synchronize":
            reviewed_by_labels = [
                label.name for label in self.pull_request.labels if "By-" in label.name
            ]
            for _reviewed_label in reviewed_by_labels:
                self.remove_label(label=_reviewed_label, pull_request=self.pull_request)

            self.process_opened_or_synchronize_pull_request(
                parent_committer=parent_committer,
                pull_request_branch=pull_request_branch,
                api_user=self.api_user,
                reviewers=self.reviewers,
            )

        if hook_action == "closed":
            self.close_issue_for_merged_or_closed_pr(hook_action=hook_action)

            if pull_request_data.get("merged"):
                self.logger.info(f"{self.log_prefix} PR is merged")
                self.build_container(
                    last_commit=self.last_commit,
                    pull_request=self.pull_request,
                    container_repository_and_tag=self.container_repository_and_tag,
                    push=True,
                    set_check=False,
                )

                for _label in self.pull_request.labels:
                    _label_name = _label.name
                    if _label_name.startswith(CHERRY_PICK_LABEL_PREFIX):
                        self.cherry_pick(
                            target_branch=_label_name.replace(
                                CHERRY_PICK_LABEL_PREFIX, ""
                            ),
                        )

                self.needs_rebase()

        if hook_action in ("labeled", "unlabeled"):
            labeled = self.hook_data["label"]["name"].lower()

            if (
                hook_action == "labeled"
                and labeled == CAN_BE_MERGED_STR
                and parent_committer
                in (
                    self.api_user,
                    PRE_COMMIT_CI_BOT_USER,
                )
            ):
                self.logger.info(
                    f"{self.log_prefix} "
                    f"will be merged automatically. owner: {self.api_user}"
                )
                self.pull_request.create_issue_comment(
                    f"Owner of the pull request is `{self.api_user}`\nPull request is merged automatically."
                )
                self.pull_request.merge(merge_method="squash")
                return

            self.logger.info(
                f"{self.log_prefix} PR {self.pull_request.number} {hook_action} with {labeled}"
            )
            if self.verified_job and labeled == VERIFIED_LABEL_STR:
                if hook_action == "labeled":
                    self.set_verify_check_success(last_commit=self.last_commit)

                if hook_action == "unlabeled":
                    self.set_verify_check_queued(last_commit=self.last_commit)

            if (
                CAN_BE_MERGED_STR
                not in self.pull_request_labels_names(pull_request=self.pull_request)
                or labeled != CAN_BE_MERGED_STR
            ):
                self.check_if_can_be_merged(
                    approvers=self.approvers, last_commit=self.last_commit
                )

    def process_unknown_webhook_data(self):
        if self.github_event == "check_run":
            _check_run = self.hook_data["check_run"]
            if _check_run["name"] == CAN_BE_MERGED_STR:
                self.logger.info(
                    f"{self.log_prefix} check_run event is for {CAN_BE_MERGED_STR}, not processing."
                )
                return

            if self.hook_data["action"] == "completed":
                self.process_check_run_complete(check_run=_check_run)

        if self.pull_request:
            self.check_if_can_be_merged(
                approvers=self.approvers, last_commit=self.last_commit
            )

    def process_check_run_complete(self, check_run):
        self.logger.info(
            f"{self.log_prefix} Got event check_run completed, getting pull request"
        )
        for _pull_request in self.repository.get_pulls(state="open"):
            _last_commit = list(_pull_request.get_commits())[-1]
            for _commit_check_run in _last_commit.get_check_runs():
                if _commit_check_run.id == int(check_run["id"]):
                    self.pull_request = _pull_request
                    break

    def upload_to_pypi(self, tag_name):
        token = self.pypi["token"]
        env = f"-e TWINE_USERNAME=__token__ -e TWINE_PASSWORD={token} "
        cmd = f"git checkout {tag_name}"
        self.logger.info(f"{self.log_prefix} Start uploading to pypi")
        cmd += (
            " && python3 -m build --sdist --outdir /tmp/dist"
            " && twine check /tmp/dist/$(echo *.tar.gz)"
            " && twine upload /tmp/dist/$(echo *.tar.gz) --skip-existing"
        )
        rc, out, err = self._run_in_container(
            command=cmd, pull_request=self.pull_request, env=env
        )
        if rc:
            self.logger.info(f"{self.log_prefix} Publish to pypi finished")
            if self.slack_webhook_url:
                message = f"""
```
{self.repository_name} Version {tag_name} published to PYPI.
```
"""
                send_slack_message(
                    message=message,
                    webhook_url=self.slack_webhook_url,
                    log_prefix=self.log_prefix,
                )

        else:
            err = "Publish to pypi failed"
            self.logger.error(f"{self.log_prefix} {err}")
            self.repository.create_issue(
                title=err,
                body=f"""
stdout: `{out}`
stderr: `{err}`
""",
            )

    def process_push_webhook_data(self):
        tag = re.search(r"refs/tags/?(.*)", self.hook_data["ref"])
        if tag and self.pypi:
            tag_name = tag.group(1)
            self.logger.info(f"{self.log_prefix} Processing push for tag: {tag_name}")
            self.upload_to_pypi(tag_name=tag_name)

    def process_pull_request_review_webhook_data(self):
        if not self.pull_request:
            return

        if self.hook_data["action"] == "submitted":
            """
            commented
            approved
            changes_requested
            """
            self.manage_reviewed_by_label(
                review_state=self.hook_data["review"]["state"],
                action=ADD_STR,
                reviewed_user=self.hook_data["review"]["user"]["login"],
            )
        self.check_if_can_be_merged(
            approvers=self.approvers, last_commit=self.last_commit
        )
