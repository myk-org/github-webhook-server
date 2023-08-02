import asyncio
import contextlib
import datetime
import json
import os
import re
import shutil
import sys
import time
from contextlib import contextmanager

import requests
import shortuuid
import yaml
from github import Github, GithubException
from github.GithubException import UnknownObjectException
from sonarqube import SonarQubeClient

from webhook_server_container.utils.constants import (
    ADD_STR,
    APPROVED_BY_LABEL_PREFIX,
    BRANCH_LABEL_PREFIX,
    BUILD_CONTAINER_STR,
    CAN_BE_MERGED_STR,
    CHANGED_REQUESTED_BY_LABEL_PREFIX,
    CHERRY_PICK_LABEL_PREFIX,
    CHERRY_PICKED_LABEL_PREFIX,
    COMMENTED_BY_LABEL_PREFIX,
    DELETE_STR,
    DYNAMIC_LABELS_DICT,
    FAILURE_STR,
    FLASK_APP,
    HOLD_LABEL_STR,
    IN_PROGRESS_STR,
    LGTM_STR,
    NEEDS_REBASE_LABEL_STR,
    PYTHON_MODULE_INSTALL_STR,
    QUEUED_STR,
    REACTIONS,
    SIZE_LABEL_PREFIX,
    SONARQUBE_STR,
    STATIC_LABELS_DICT,
    SUCCESS_STR,
    TOX_STR,
    USER_LABELS_DICT,
    VERIFIED_LABEL_STR,
    WIP_STR,
)
from webhook_server_container.utils.dockerhub_rate_limit import DockerHub
from webhook_server_container.utils.helpers import (
    extract_key_from_dict,
    get_data_from_config,
    get_github_repo_api,
    ignore_exceptions,
    run_command,
)


class RepositoryNotFoundError(Exception):
    pass


@contextmanager
def change_directory(directory, logger):
    logger.info(f"Changing directory to {directory}")
    old_cwd = os.getcwd()
    yield os.chdir(directory)
    logger.info(f"Changing back to directory {old_cwd}")
    os.chdir(old_cwd)


class GitHubApi:
    def __init__(self, hook_data, repositories_app_api, missing_app_repositories):
        self.app = FLASK_APP
        self.hook_data = hook_data
        self.repository_name = hook_data["repository"]["name"]
        self.repositories_app_api = repositories_app_api
        self.missing_app_repositories = missing_app_repositories
        self.pull_request = None
        self.last_commit = None

        # filled by self._repo_data_from_config()
        self.dockerhub_username = None
        self.dockerhub_password = None
        self.container_repository_username = None
        self.container_repository_password = None
        self.container_repository = None
        self.dockerfile = None
        self.container_tag = None
        self.container_build_args = None
        self.container_command_args = None
        self.token = None
        self.repository_full_name = None
        self.api_user = None
        self.github_app_id = None
        self.sonarqube = None
        self.sonarqube_api = None
        self.sonarqube_project_key = None
        # End of filled by self._repo_data_from_config()

        self._repo_data_from_config()
        self.github_app_api = self.get_github_app_api()
        self.github_api = Github(login_or_token=self.token)
        self.api_user = self._api_username
        self.repository = get_github_repo_api(
            gapi=self.github_api, repository=self.repository_full_name
        )
        self.repository_by_github_app = get_github_repo_api(
            gapi=self.github_app_api, repository=self.repository_full_name
        )
        self.clone_repository_path = os.path.join("/", self.repository.name)
        self.check_rate_limit()
        self.dockerhub = DockerHub(
            username=self.dockerhub_username,
            password=self.dockerhub_password,
        )
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

    def get_github_app_api(self):
        if self.repository_full_name in self.missing_app_repositories:
            raise RepositoryNotFoundError(
                f"Repository {self.repository_full_name} not found by manage-repositories-app, "
                f"make sure the app installed (https://github.com/apps/manage-repositories-app)"
            )
        return self.repositories_app_api[self.repository_full_name]

    @property
    def log_prefix(self):
        return (
            f"{self.repository_name}[PR {self.pull_request.number}]:"
            if self.pull_request
            else f"{self.repository_name}:"
        )

    def hash_token(self, message):
        hashed_message = message.replace(self.token, "*****")
        return hashed_message

    def app_logger_info(self, message):
        hashed_message = self.hash_token(message=message)
        self.app.logger.info(hashed_message)

    def app_logger_error(self, message):
        hashed_message = self.hash_token(message=message)
        self.app.logger.error(hashed_message)

    def process_hook(self, data):
        ignore_data = ["status", "branch_protection_rule"]
        if data == "issue_comment":
            self.process_comment_webhook_data()

        elif data == "pull_request":
            self.process_pull_request_webhook_data()

        elif data == "push":
            self.process_push_webhook_data()

        elif data == "pull_request_review":
            self.process_pull_request_review_webhook_data()

        elif data not in ignore_data:
            if data == "check_run":
                if self.hook_data["check_run"]["name"] == CAN_BE_MERGED_STR:
                    return

            self.pull_request = self._get_pull_request()
            if self.pull_request:
                self.last_commit = self._get_last_commit()
                self.check_if_can_be_merged()

    @property
    def _api_username(self):
        return self.github_api.get_user().login

    def _repo_data_from_config(self):
        config_data = get_data_from_config()
        self.github_app_id = config_data["github-app-id"]
        self.token = config_data["github-token"]
        self.sonarqube = config_data.get("sonarqube")
        if self.sonarqube:
            self.sonarqube_url = self.sonarqube["url"]
            self.sonarqube_token = self.sonarqube["token"]
            self.sonarqube_api = SonarQubeClient(
                sonarqube_url=self.sonarqube_url, token=self.sonarqube_token
            )

        repo_data = config_data["repositories"].get(self.repository_name)
        if not repo_data:
            raise RepositoryNotFoundError(
                f"Repository {self.repository_name} not found in config file"
            )

        self.repository_full_name = repo_data["name"]
        self.pypi = repo_data.get("pypi")
        self.verified_job = repo_data.get("verified_job", True)
        self.tox_enabled = repo_data.get("tox")
        self.webhook_url = repo_data.get("webhook_ip")
        self.slack_webhook_url = repo_data.get("slack_webhook_url")
        self.build_and_push_container = repo_data.get("container")
        self.dockerhub = repo_data.get("docker")
        if self.sonarqube:
            self.sonarqube_project_key = repo_data.get("sonarqube-project-key")

        if self.dockerhub:
            self.dockerhub_username = self.dockerhub["username"]
            self.dockerhub_password = self.dockerhub["password"]

        if self.build_and_push_container:
            self.container_repository_username = self.build_and_push_container[
                "username"
            ]
            self.container_repository_password = self.build_and_push_container[
                "password"
            ]
            self.container_repository = self.build_and_push_container["repository"]
            self.dockerfile = self.build_and_push_container.get(
                "dockerfile", "Dockerfile"
            )
            self.container_tag = self.build_and_push_container.get("tag", "latest")
            self.container_build_args = self.build_and_push_container.get("build-args")
            self.container_command_args = self.build_and_push_container.get("args")

    def _get_pull_request(self, number=None):
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

        self.app.logger.info(
            f"{self.log_prefix} No issue or pull_request found in hook data"
        )

    def _get_last_commit(self):
        return list(self.pull_request.get_commits())[-1]

    def label_exists_in_pull_request(self, label):
        return any(lb for lb in self.pull_request_labels_names() if lb == label)

    def pull_request_labels_names(self):
        return [lb.name for lb in self.pull_request.labels]

    def skip_merged_pull_request(self):
        if self.pull_request.is_merged():
            self.app.logger.info(f"{self.log_prefix}: PR is merged, not processing")
            return True

    def _remove_label(self, label):
        if self.label_exists_in_pull_request(label=label):
            self.app.logger.info(f"{self.log_prefix} Removing label {label}")
            return self.pull_request.remove_from_labels(label)

        self.app.logger.warning(
            f"{self.log_prefix} Label {label} not found and cannot be removed"
        )

    def _add_label(self, label):
        label = label.strip()
        if len(label) > 49:
            self.app.logger.warning(f"{label} is to long, not adding.")
            return

        if self.label_exists_in_pull_request(label=label):
            self.app.logger.info(
                f"{self.log_prefix} Label {label} already assign to PR {self.pull_request.number}"
            )
            return

        if label in STATIC_LABELS_DICT:
            self.app.logger.info(
                f"{self.log_prefix} Adding pull request label {label} to {self.pull_request.number}"
            )
            return self.pull_request.add_to_labels(label)

        _color = [
            DYNAMIC_LABELS_DICT[_label]
            for _label in DYNAMIC_LABELS_DICT
            if _label in label
        ]
        self.app.logger.info(
            f"{self.log_prefix} Label {label} was "
            f"{'found' if _color else 'not found'} in labels dict"
        )
        color = _color[0] if _color else "D4C5F9"
        self.app.logger.info(
            f"{self.log_prefix} Adding label {label} with color {color}"
        )

        try:
            _repo_label = self.repository.get_label(label)
            _repo_label.edit(name=_repo_label.name, color=color)
            self.app.logger.info(
                f"{self.log_prefix} "
                f"Edit repository label {label} with color {color}"
            )
        except UnknownObjectException:
            self.app.logger.info(
                f"{self.log_prefix} Add repository label {label} with color {color}"
            )
            self.repository.create_label(name=label, color=color)

        self.app.logger.info(
            f"{self.log_prefix} Adding pull request label {label} to {self.pull_request.number}"
        )
        return self.pull_request.add_to_labels(label)

    def _generate_issue_title(self):
        return f"{self.pull_request.title} - {self.pull_request.number}"

    def _generate_issue_body(self):
        return f"[Auto generated]\nNumber: [#{self.pull_request.number}]"

    @contextmanager
    def _clone_repository(self, path_suffix):
        _clone_path = f"/tmp{self.clone_repository_path}-{path_suffix}"
        clone_cmd = (
            f"git clone {self.repository.clone_url.replace('https://', f'https://{self.token}@')} "
            f"{_clone_path}"
        )
        git_user_name_cmd = f"git config user.name '{self.repository.owner.login}'"
        git_email_cmd = f"git config user.email '{self.repository.owner.email}'"
        remote_update_cmd = "git remote update"
        fetch_pr_cmd = "git config --local --add remote.origin.fetch +refs/pull/*/head:refs/remotes/origin/pr/*"

        if run_command(command=clone_cmd, log_prefix=self.log_prefix)[0]:
            with change_directory(_clone_path, logger=self.app.logger):
                for cmd in [
                    git_user_name_cmd,
                    git_email_cmd,
                    fetch_pr_cmd,
                    remote_update_cmd,
                ]:
                    run_command(command=cmd, log_prefix=self.log_prefix)
                yield _clone_path

            self.app.logger.info(
                f"{self.log_prefix} Removing cloned repository: {_clone_path}"
            )
            shutil.rmtree(_clone_path, ignore_errors=True)
        else:
            yield

    def _checkout_tag(self, tag):
        return run_command(command=f"git checkout {tag}", log_prefix=self.log_prefix)[0]

    def _checkout_new_branch(self, source_branch, new_branch_name):
        for cmd in (
            f"git checkout {source_branch}",
            f"git pull origin {source_branch}",
            f"git checkout -b {new_branch_name} origin/{source_branch}",
        ):
            run_command(command=cmd, log_prefix=self.log_prefix)

    @ignore_exceptions()
    def is_branch_exists(self, branch):
        return self.repository.get_branch(branch)

    def _cherry_pick(self, source_branch, new_branch_name):
        def _issue_from_err(_out, _err, _commit_hash, _source_branch, _step):
            self.app.logger.error(
                f"{self.log_prefix} [{_step}] Cherry pick failed: {_out} --- {_err}"
            )
            local_branch_name = f"{self.pull_request.head.ref}-{source_branch}"
            self.pull_request.create_issue_comment(
                f"**Manual cherry-pick is needed**\nCherry pick failed for "
                f"{_commit_hash} to {_source_branch}:\n"
                f"To cherry-pick run:\n"
                "```\n"
                f"git checkout {_source_branch}\n"
                f"git pull origin {_source_branch}\n"
                f"git checkout -b {local_branch_name}\n"
                f"git cherry-pick {_commit_hash}\n"
                f"git push origin {local_branch_name}\n"
                "```"
            )
            return False

        commit_hash = self.pull_request.merge_commit_sha
        commit_msg = self.pull_request.title
        pull_request_url = self.pull_request.html_url
        user_login = self.pull_request.user.login

        self.app.logger.info(
            f"{self.log_prefix} Cherry picking [PR {self.pull_request.number}]{commit_hash} "
            f"into {source_branch}, requested by {user_login}"
        )
        cherry_pick, out, err = run_command(
            command=f"git cherry-pick {commit_hash}",
            log_prefix=self.log_prefix,
        )
        if not cherry_pick:
            return _issue_from_err(
                _out=out,
                _err=err,
                _commit_hash=commit_hash,
                _source_branch=source_branch,
                _step="git cherry-pick",
            )

        git_push, out, err = run_command(
            command=f"git push origin {new_branch_name}",
            log_prefix=self.log_prefix,
        )
        if not git_push:
            return _issue_from_err(
                _out=out,
                _err=err,
                _commit_hash=commit_hash,
                _source_branch=source_branch,
                _step="git push",
            )

        with self.set_os_env_github_token():
            pull_request_cmd, out, err = run_command(
                command=f"hub pull-request "
                f"-b {source_branch} "
                f"-h {new_branch_name} "
                f"-l {CHERRY_PICKED_LABEL_PREFIX} "
                f"-m '{CHERRY_PICKED_LABEL_PREFIX}: [{source_branch}] {commit_msg}' "
                f"-m 'cherry-pick {pull_request_url} into {source_branch}' "
                f"-m 'requested-by {user_login}'",
                log_prefix=self.log_prefix,
            )
        if not pull_request_cmd:
            return _issue_from_err(
                _out=out,
                _err=err,
                _commit_hash=commit_hash,
                _source_branch=source_branch,
                _step="create pull request",
            )
        return True

    def upload_to_pypi(self, tag_name):
        tool = self.pypi["tool"]
        token = self.pypi["token"]
        self.app.logger.info(f"{self.log_prefix} Start uploading to pypi")
        rc, out, err = None, None, None
        if tool == "twine":
            os.environ["TWINE_USERNAME"] = "__token__"
            os.environ["TWINE_PASSWORD"] = token
            build_folder = "dist"

            rc, out, err = run_command(
                command=f"{sys.executable} -m build --sdist --outdir {build_folder}/",
                log_prefix=self.log_prefix,
            )
            if rc:
                dist_pkg = re.search(r"Successfully built (.*.tar.gz)", out).group(1)
                dist_pkg_path = os.path.join(build_folder, dist_pkg)
                rc, out, err = run_command(
                    command=f"twine check {dist_pkg_path}",
                    log_prefix=self.log_prefix,
                )
                if rc:
                    rc, out, err = run_command(
                        command=f"twine upload {dist_pkg_path} --skip-existing",
                        log_prefix=self.log_prefix,
                    )

        elif tool == "poetry":
            rc, out, err = run_command(
                command=f"poetry config --local pypi-token.pypi {token}",
                log_prefix=self.log_prefix,
            )
            if rc:
                rc, out, err = run_command(
                    command="poetry publish --build", log_prefix=self.log_prefix
                )
        if rc:
            self.app.logger.info(
                f"{self.log_prefix} Publish to pypi finished [using {tool}]"
            )
            message = f"""
```
{self.log_prefix} Version {tag_name} published to PYPI.
```
"""
            self.send_slack_message(
                message=message,
                webhook_url=self.slack_webhook_url,
            )

        else:
            err = f"Publish to pypi failed [using {tool}]"
            self.app.logger.error(f"{self.log_prefix} {err}")
            self.repository.create_issue(
                title=err,
                body=f"""
stdout: `{out}`
stderr: `{err}`
""",
            )

    @property
    def owners_content(self):
        try:
            owners_content = self.repository.get_contents("OWNERS")
            return yaml.safe_load(owners_content.decoded_content)
        except UnknownObjectException:
            self.app.logger.error(f"{self.log_prefix} OWNERS file not found")
            return {}

    @property
    def reviewers(self):
        return self.owners_content.get("reviewers", [])

    @property
    def approvers(self):
        return self.owners_content.get("approvers", [])

    def assign_reviewers(self):
        for reviewer in self.reviewers:
            if reviewer != self.pull_request.user.login:
                self.app.logger.info(f"{self.log_prefix} Adding reviewer {reviewer}")
                try:
                    self.pull_request.create_review_request([reviewer])
                except GithubException as ex:
                    self.app.logger.error(
                        f"{self.log_prefix} Failed to add reviewer {reviewer}. {ex}"
                    )

    def add_size_label(self):
        size = self.pull_request.additions + self.pull_request.deletions
        if size < 20:
            _label = "XS"

        elif size < 50:
            _label = "S"

        elif size < 100:
            _label = "M"

        elif size < 300:
            _label = "L"

        elif size < 500:
            _label = "XL"

        else:
            _label = "XXL"

        size_label = f"{SIZE_LABEL_PREFIX}{_label}"

        if size_label in self.pull_request_labels_names():
            return

        exists_size_label = [
            label
            for label in self.pull_request_labels_names()
            if label.startswith(SIZE_LABEL_PREFIX)
        ]

        if exists_size_label:
            self._remove_label(label=exists_size_label[0])

        self._add_label(label=size_label)

    def label_by_user_comment(
        self, user_request, remove, reviewed_user, issue_comment_id
    ):
        if not any(
            user_request.startswith(label_name) for label_name in USER_LABELS_DICT
        ):
            self.app.logger.info(
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

        self.app.logger.info(
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
            label_func = self._remove_label if remove else self._add_label
            label_func(label=user_request)

    def reset_verify_label(self):
        self.app.logger.info(
            f"{self.log_prefix} Processing reset {VERIFIED_LABEL_STR} label on new commit push"
        )
        # Remove verified label
        self._remove_label(label=VERIFIED_LABEL_STR)

    def set_verify_check_queued(self):
        self.app.logger.info(
            f"{self.log_prefix} Processing set {VERIFIED_LABEL_STR} check {QUEUED_STR}"
        )

        self.repository_by_github_app.create_check_run(
            name=VERIFIED_LABEL_STR, head_sha=self.last_commit.sha, status=QUEUED_STR
        )

    def set_verify_check_success(self):
        self.app.logger.info(
            f"{self.log_prefix} Set {VERIFIED_LABEL_STR} check to {SUCCESS_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=VERIFIED_LABEL_STR,
            head_sha=self.last_commit.sha,
            conclusion=SUCCESS_STR,
        )

    def set_run_tox_check_queued(self):
        if not self.tox_enabled:
            return

        self.app.logger.info(
            f"{self.log_prefix} Processing set {TOX_STR} check {QUEUED_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=TOX_STR, head_sha=self.last_commit.sha, status=QUEUED_STR
        )

    def set_run_tox_check_in_progress(self):
        self.app.logger.info(
            f"{self.log_prefix} Processing set {TOX_STR} check {IN_PROGRESS_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=TOX_STR, head_sha=self.last_commit.sha, status=IN_PROGRESS_STR
        )

    def set_run_tox_check_failure(self, target_url):
        self.app.logger.info(
            f"{self.log_prefix} Processing set {TOX_STR} check {FAILURE_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=TOX_STR,
            head_sha=self.last_commit.sha,
            conclusion=FAILURE_STR,
            details_url=target_url,
        )

    def set_run_tox_check_success(self, target_url):
        self.app.logger.info(f"{self.log_prefix} Set {TOX_STR} check to {SUCCESS_STR}")
        self.repository_by_github_app.create_check_run(
            name=TOX_STR,
            head_sha=self.last_commit.sha,
            conclusion=SUCCESS_STR,
            details_url=target_url,
        )

    def set_merge_check_queued(self):
        self.app.logger.info(
            f"{self.log_prefix} Set {CAN_BE_MERGED_STR} check to {QUEUED_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=CAN_BE_MERGED_STR, head_sha=self.last_commit.sha, status=QUEUED_STR
        )

    def set_merge_check_in_progress(self):
        self.app.logger.info(
            f"{self.log_prefix} Set {CAN_BE_MERGED_STR} check to {IN_PROGRESS_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=CAN_BE_MERGED_STR,
            head_sha=self.last_commit.sha,
            status=IN_PROGRESS_STR,
        )

    def set_merge_check_success(self):
        self.app.logger.info(
            f"{self.log_prefix} Set {CAN_BE_MERGED_STR} check to {SUCCESS_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=CAN_BE_MERGED_STR,
            head_sha=self.last_commit.sha,
            conclusion=SUCCESS_STR,
        )

    def set_container_build_queued(self):
        if not self.build_and_push_container:
            return

        self.app.logger.info(
            f"{self.log_prefix} Set {BUILD_CONTAINER_STR} check to {QUEUED_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=BUILD_CONTAINER_STR, head_sha=self.last_commit.sha, status=QUEUED_STR
        )

    def set_container_build_in_progress(self):
        self.app.logger.info(
            f"{self.log_prefix} Processing set {BUILD_CONTAINER_STR} check {IN_PROGRESS_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=BUILD_CONTAINER_STR,
            head_sha=self.last_commit.sha,
            status=IN_PROGRESS_STR,
        )

    def set_container_build_success(self, target_url):
        self.app.logger.info(
            f"{self.log_prefix} Set {BUILD_CONTAINER_STR} check to {SUCCESS_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=BUILD_CONTAINER_STR,
            head_sha=self.last_commit.sha,
            conclusion=SUCCESS_STR,
            details_url=target_url,
        )

    def set_container_build_failure(self, target_url):
        self.app.logger.info(
            f"{self.log_prefix} Set {BUILD_CONTAINER_STR} check to {FAILURE_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=BUILD_CONTAINER_STR,
            head_sha=self.last_commit.sha,
            conclusion=FAILURE_STR,
            details_url=target_url,
        )

    def set_python_module_install_queued(self):
        if not self.pypi:
            return

        self.app.logger.info(
            f"{self.log_prefix} Set {PYTHON_MODULE_INSTALL_STR} check to {QUEUED_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=PYTHON_MODULE_INSTALL_STR,
            head_sha=self.last_commit.sha,
            status=QUEUED_STR,
        )

    def set_python_module_install_in_progress(self):
        self.app.logger.info(
            f"{self.log_prefix} Processing set {PYTHON_MODULE_INSTALL_STR} check {IN_PROGRESS_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=PYTHON_MODULE_INSTALL_STR,
            head_sha=self.last_commit.sha,
            status=IN_PROGRESS_STR,
        )

    def set_python_module_install_success(self, target_url):
        self.app.logger.info(
            f"{self.log_prefix} Set {PYTHON_MODULE_INSTALL_STR} check to {SUCCESS_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=PYTHON_MODULE_INSTALL_STR,
            head_sha=self.last_commit.sha,
            conclusion=SUCCESS_STR,
            details_url=target_url,
        )

    def set_python_module_install_failure(self, target_url):
        self.app.logger.info(
            f"{self.log_prefix} Set {PYTHON_MODULE_INSTALL_STR} check to {FAILURE_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=PYTHON_MODULE_INSTALL_STR,
            head_sha=self.last_commit.sha,
            conclusion=FAILURE_STR,
            details_url=target_url,
        )

    def set_sonarqube_queued(self):
        if not self.sonarqube_project_key:
            return

        self.app.logger.info(
            f"{self.log_prefix} Set {SONARQUBE_STR} check to {QUEUED_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=SONARQUBE_STR,
            head_sha=self.last_commit.sha,
            status=QUEUED_STR,
        )

    def set_sonarqube_in_progress(self):
        self.app.logger.info(
            f"{self.log_prefix} Processing set {SONARQUBE_STR} check {IN_PROGRESS_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=SONARQUBE_STR,
            head_sha=self.last_commit.sha,
            status=IN_PROGRESS_STR,
        )

    def set_sonarqube_success(self, target_url):
        self.app.logger.info(
            f"{self.log_prefix} Set {SONARQUBE_STR} check to {SUCCESS_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=SONARQUBE_STR,
            head_sha=self.last_commit.sha,
            conclusion=SUCCESS_STR,
            details_url=target_url,
        )

    def set_sonarqube_failure(self, target_url):
        self.app.logger.info(
            f"{self.log_prefix} Set {SONARQUBE_STR} check to {FAILURE_STR}"
        )
        self.repository_by_github_app.create_check_run(
            name=SONARQUBE_STR,
            head_sha=self.last_commit.sha,
            conclusion=FAILURE_STR,
            details_url=target_url,
        )

    @ignore_exceptions(FLASK_APP.logger)
    def create_issue_for_new_pull_request(self):
        self.app.logger.info(
            f"{self.log_prefix} Creating issue for new PR: {self.pull_request.title}"
        )
        self.repository.create_issue(
            title=self._generate_issue_title(),
            body=self._generate_issue_body(),
            assignee=self.pull_request.user.login,
        )

    def close_issue_for_merged_or_closed_pr(self, hook_action):
        for issue in self.repository.get_issues():
            if issue.body == self._generate_issue_body():
                self.app.logger.info(
                    f"{self.log_prefix} Closing issue {issue.title} for PR: "
                    f"{self.pull_request.title}"
                )
                issue.create_comment(
                    f"{self.log_prefix} Closing issue for PR: "
                    f"{self.pull_request.title}.\nPR was {hook_action}."
                )
                issue.edit(state="closed")
                break

    def process_comment_webhook_data(self):
        if self.hook_data["action"] in ("action", "deleted"):
            return

        issue_number = self.hook_data["issue"]["number"]
        self.app.logger.info(f"{self.log_prefix} Processing issue {issue_number}")

        self.pull_request = self._get_pull_request()
        if not self.pull_request:
            return

        self.last_commit = self._get_last_commit()

        body = self.hook_data["comment"]["body"]

        if body == self.welcome_msg:
            self.app.logger.info(
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
        self.app.logger.info(f"{self.log_prefix} hook_action is: {hook_action}")
        self.pull_request = self._get_pull_request()
        if not self.pull_request:
            return

        self.last_commit = self._get_last_commit()
        pull_request_data = self.hook_data["pull_request"]
        parent_committer = pull_request_data["user"]["login"]
        pull_request_branch = pull_request_data["base"]["ref"]

        if hook_action == "opened":
            self.app.logger.info(f"{self.log_prefix} Creating welcome comment")
            self.pull_request.create_issue_comment(self.welcome_msg)
            self.create_issue_for_new_pull_request()
            self.process_opened_or_synchronize_pull_request(
                parent_committer=parent_committer,
                pull_request_branch=pull_request_branch,
            )

        if hook_action == "synchronize":
            reviewed_by_labels = [
                label.name for label in self.pull_request.labels if "By-" in label.name
            ]
            for _reviewed_label in reviewed_by_labels:
                self._remove_label(label=_reviewed_label)

            self.process_opened_or_synchronize_pull_request(
                parent_committer=parent_committer,
                pull_request_branch=pull_request_branch,
            )

        if hook_action == "closed":
            self.close_issue_for_merged_or_closed_pr(hook_action=hook_action)

            if pull_request_data.get("merged"):
                self.app.logger.info(f"{self.log_prefix} PR is merged")
                self._build_and_push_container()

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
                    "pre-commit-ci[bot]",
                )
            ):
                self.app.logger.info(
                    f"{self.log_prefix} "
                    f"will be merged automatically. owner: {self.api_user}"
                )
                self.pull_request.create_issue_comment(
                    f"Owner of the pull request is `{self.api_user}`\nPull request is merged automatically."
                )
                self.pull_request.merge(merge_method="squash")
                return

            self.app.logger.info(
                f"{self.log_prefix} PR {self.pull_request.number} {hook_action} with {labeled}"
            )
            if self.verified_job and labeled == VERIFIED_LABEL_STR:
                if hook_action == "labeled":
                    self.set_verify_check_success()

                if hook_action == "unlabeled":
                    self.set_verify_check_queued()

            if (
                CAN_BE_MERGED_STR not in self.pull_request_labels_names()
                or labeled != CAN_BE_MERGED_STR
            ):
                self.check_if_can_be_merged()

    def process_push_webhook_data(self):
        tag = re.search(r"refs/tags/?(.*)", self.hook_data["ref"])
        if tag and self.pypi:
            tag_name = tag.group(1)
            self.app.logger.info(
                f"{self.log_prefix} Processing push for tag: {tag_name}"
            )
            with self._clone_repository(path_suffix=f"{tag_name}-{shortuuid.uuid()}"):
                if self._checkout_tag(tag=tag_name):
                    self.upload_to_pypi(tag_name=tag_name)

    def process_pull_request_review_webhook_data(self):
        self.pull_request = self._get_pull_request()
        if not self.pull_request:
            return

        self.last_commit = self._get_last_commit()

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
        self.check_if_can_be_merged()

    def manage_reviewed_by_label(self, review_state, action, reviewed_user):
        self.app.logger.info(
            f"{self.log_prefix} "
            f"Processing label for review from {reviewed_user}. "
            f"review_state: {review_state}, action: {action}"
        )
        label_prefix = None
        label_to_remove = None

        pull_request_labels = self.pull_request_labels_names()

        if review_state in ("approved", LGTM_STR):
            base_dict = self.hook_data.get("issue", self.hook_data.get("pull_request"))
            pr_owner = base_dict["user"]["login"]
            if pr_owner == reviewed_user:
                self.app.logger.info(
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
                self._add_label(label=reviewer_label)
                if label_to_remove:
                    self._remove_label(label=label_to_remove)

            if action == DELETE_STR:
                self._remove_label(label=reviewer_label)
        else:
            self.app.logger.warning(
                f"{self.log_prefix} PR {self.pull_request.number} got unsupported review state: {review_state}"
            )

    def _run_tox(self):
        if not self.tox_enabled:
            return False

        if self.is_check_run_in_progress(check_run=TOX_STR):
            self.app.logger.info(
                f"{self.log_prefix} Check run is in progress, not running {TOX_STR}."
            )
            return False

        self.set_run_tox_check_in_progress()
        base_path = f"/webhook_server/tox/{self.pull_request.number}"
        base_url = f"{self.webhook_url}{base_path}"
        with self._clone_repository(path_suffix=f"tox-{shortuuid.uuid()}"):
            if not self._checkout_pull_request():
                return False

            cmd = "tox"
            if self.tox_enabled != "all":
                tests = self.tox_enabled.replace(" ", "")
                cmd += f" -e {tests}"

            rc, out, err = run_command(command=cmd, log_prefix=self.log_prefix)
            if not rc:
                with open(base_path, "w") as fd:
                    fd.write(f"stdout: {out}, stderr: {err}")

                return self.set_run_tox_check_failure(target_url=base_url)

            with open(base_path, "w") as fd:
                fd.write(out)

            return self.set_run_tox_check_success(target_url=base_url)

    def user_commands(self, command, reviewed_user, issue_comment_id):
        remove = False
        available_commands = ["retest", "cherry-pick"]
        if "sonarsource.github.io" in command:
            self.app.logger.info(f"{self.log_prefix} command is in ignore list")
            return

        self.app.logger.info(
            f"{self.log_prefix} Processing label/user command {command} "
            f"by user {reviewed_user}"
        )
        command_and_args = command.split(" ", 1)
        _command = command_and_args[0]
        _args = command_and_args[1] if len(command_and_args) > 1 else ""
        if len(command_and_args) > 1 and _args == "cancel":
            self.app.logger.info(
                f"{self.log_prefix} User requested 'cancel' for command {_command}"
            )
            remove = True

        if _command in available_commands:
            if not _args:
                error_msg = f"{self.log_prefix} retest/cherry-pick requires an argument"
                self.app.logger.info(error_msg)
                self.pull_request.create_issue_comment(error_msg)
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
                    self.app.logger.info(
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
                        self.app.logger.info(f"{self.log_prefix} {info_msg}")
                        self.pull_request.create_issue_comment(info_msg)
                        for _cp_label in cp_labels:
                            self._add_label(label=_cp_label)
                    else:
                        for _exits_target_branch in _exits_target_branches:
                            self.cherry_pick(
                                target_branch=_exits_target_branch,
                                reviewed_user=reviewed_user,
                            )

            elif _command == "retest":
                if self.skip_merged_pull_request():
                    self.pull_request.create_issue_comment(
                        "Pull request already merged, not running /retest"
                    )
                    return

                if _args == "tox":
                    if not self.tox_enabled:
                        error_msg = f"{self.log_prefix} Tox is not enabled."
                        self.app.logger.info(error_msg)
                        self.pull_request.create_issue_comment(error_msg)
                        return

                    self.create_comment_reaction(
                        issue_comment_id=issue_comment_id,
                        reaction=REACTIONS.ok,
                    )
                    self.set_run_tox_check_in_progress()
                    self._run_tox()

                elif _args == BUILD_CONTAINER_STR:
                    if self.build_and_push_container:
                        self.create_comment_reaction(
                            issue_comment_id=issue_comment_id,
                            reaction=REACTIONS.ok,
                        )
                        self._build_container()
                    else:
                        error_msg = f"{self.log_prefix} No build-container configured"
                        self.app.logger.info(error_msg)
                        self.pull_request.create_issue_comment(error_msg)

                elif _args == PYTHON_MODULE_INSTALL_STR:
                    if not self.pypi:
                        error_msg = f"{self.log_prefix} No pypi configured"
                        self.app.logger.info(error_msg)
                        self.pull_request.create_issue_comment(error_msg)
                        return

                    self.create_comment_reaction(
                        issue_comment_id=issue_comment_id,
                        reaction=REACTIONS.ok,
                    )
                    self._install_python_module()

        elif _command == "build-and-push-container":
            if self.build_and_push_container:
                self.create_comment_reaction(
                    issue_comment_id=issue_comment_id,
                    reaction=REACTIONS.ok,
                )
                self._build_and_push_container()
            else:
                error_msg = f"{self.log_prefix} No build-and-push-container configured"
                self.app.logger.info(error_msg)
                self.pull_request.create_issue_comment(error_msg)

        elif _command == WIP_STR:
            if self.skip_merged_pull_request():
                self.pull_request.create_issue_comment(
                    "Pull request already merged, not processing /wip"
                )
                return

            self.create_comment_reaction(
                issue_comment_id=issue_comment_id,
                reaction=REACTIONS.ok,
            )
            wip_for_title = f"{WIP_STR.upper()}:"
            if remove:
                self._remove_label(label=WIP_STR)
                self.pull_request.edit(
                    title=self.pull_request.title.replace(wip_for_title, "")
                )
            else:
                self._add_label(label=WIP_STR)
                self.pull_request.edit(
                    title=f"{wip_for_title} {self.pull_request.title}"
                )

        else:
            if self.skip_merged_pull_request():
                self.pull_request.create_issue_comment(
                    f"Pull request already merged, not processing /{_command}"
                )
                return

            self.label_by_user_comment(
                user_request=_command,
                remove=remove,
                reviewed_user=reviewed_user,
                issue_comment_id=issue_comment_id,
            )

    def cherry_pick(self, target_branch, reviewed_user=None):
        self.app.logger.info(
            f"{self.log_prefix} Cherry-pick requested by user: "
            f"{reviewed_user or 'by target-branch label'}"
        )

        new_branch_name = f"{CHERRY_PICKED_LABEL_PREFIX}-{self.pull_request.head.ref}-{shortuuid.uuid()[:5]}"
        if not self.is_branch_exists(branch=target_branch):
            err_msg = f"cherry-pick failed: {target_branch} does not exists"
            self.app.logger.error(err_msg)
            self.pull_request.create_issue_comment(err_msg)
        else:
            with self._clone_repository(path_suffix=shortuuid.uuid()):
                self._checkout_new_branch(
                    source_branch=target_branch,
                    new_branch_name=new_branch_name,
                )
                if self._cherry_pick(
                    source_branch=target_branch,
                    new_branch_name=new_branch_name,
                ):
                    self.pull_request.create_issue_comment(
                        f"Cherry-picked PR {self.pull_request.title} into {target_branch}"
                    )

    def needs_rebase(self):
        for pull_request in self.repository.get_pulls():
            self.app.logger.info(
                f"{self.log_prefix} "
                "Sleep for 30 seconds before checking if rebase needed"
            )
            time.sleep(30)
            merge_state = pull_request.mergeable_state
            self.app.logger.info(f"{self.log_prefix} Mergeable state is {merge_state}")
            if merge_state == "behind":
                self._add_label(label=NEEDS_REBASE_LABEL_STR)
            else:
                self._remove_label(label=NEEDS_REBASE_LABEL_STR)

    def check_if_can_be_merged(self):
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
            return

        self.app.logger.info(f"{self.log_prefix} Check if can be merged.")
        last_commit_check_runs = list(self.last_commit.get_check_runs())
        check_runs_in_progress = [
            check_run.name
            for check_run in last_commit_check_runs
            if check_run.status == IN_PROGRESS_STR
        ]
        if any(check_runs_in_progress):
            self.app.logger.info(
                f"{self.log_prefix} Some check runs in progress {check_runs_in_progress}, "
                f"skipping check if can be merged."
            )
            return

        self.set_merge_check_in_progress()
        _labels = self.pull_request_labels_names()

        if VERIFIED_LABEL_STR not in _labels or HOLD_LABEL_STR in _labels:
            self._remove_label(label=CAN_BE_MERGED_STR)
            self.set_merge_check_queued()
            return

        if self.pull_request.mergeable_state == "behind":
            self._remove_label(label=CAN_BE_MERGED_STR)
            self.set_merge_check_queued()
            return

        all_check_runs_passed = all(
            [
                check_run.conclusion == SUCCESS_STR
                for check_run in last_commit_check_runs
                if check_run.name != CAN_BE_MERGED_STR
            ]
        )
        if not all_check_runs_passed:
            self._remove_label(label=CAN_BE_MERGED_STR)
            self.set_merge_check_queued()
            # TODO: Fix `run_retest_if_queued` and uncomment the call for it.
            # self.run_retest_if_queued(last_commit_check_runs=last_commit_check_runs)
            return

        for _label in _labels:
            if CHANGED_REQUESTED_BY_LABEL_PREFIX.lower() in _label.lower():
                change_request_user = _label.split("-")[-1]
                if change_request_user in self.approvers:
                    self._remove_label(label=CAN_BE_MERGED_STR)
                    self.set_merge_check_queued()
                    return

        for _label in _labels:
            if APPROVED_BY_LABEL_PREFIX.lower() in _label.lower():
                approved_user = _label.split("-")[-1]
                if approved_user in self.approvers:
                    self._add_label(label=CAN_BE_MERGED_STR)
                    self.set_merge_check_success()
                    return

    @staticmethod
    def _comment_with_details(title, body):
        return f"""
<details>
<summary>{title}</summary>
    {body}
</details>
        """

    def _container_repository_and_tag(self):
        tag = (
            self.container_tag
            if self.pull_request.is_merged()
            else self.pull_request.number
        )
        return f"{self.container_repository}:{tag}"

    def _build_container(self, set_check=True):
        if not self.build_and_push_container:
            return False

        if self.is_check_run_in_progress(check_run=BUILD_CONTAINER_STR):
            self.app.logger.info(
                f"{self.log_prefix} Check run is in progress, not running {BUILD_CONTAINER_STR}."
            )
            return False

        self.set_container_build_in_progress()
        base_path = None
        base_url = None

        if self.pull_request:
            base_path = f"/webhook_server/build-container/{self.pull_request.number}"
            base_url = f"{self.webhook_url}{base_path}"

        with self._clone_repository(path_suffix=f"build-container-{shortuuid.uuid()}"):
            self.app.logger.info(
                f"{self.log_prefix} Current directory is {os.getcwd()}"
            )
            if self.pull_request and not self._checkout_pull_request():
                return False

            _container_repository_and_tag = self._container_repository_and_tag()
            build_cmd = (
                f"--network=host -f {self.dockerfile} "
                f"-t {_container_repository_and_tag}"
            )
            if self.container_build_args:
                build_args = [
                    f"--build-arg {b_arg}" for b_arg in self.container_build_args
                ][0]
                build_cmd = f"{build_args} {build_cmd}"

            if self.container_command_args:
                build_cmd = f"{' '.join(self.container_command_args)} {build_cmd}"

            podman_build_cmd = f"podman build {build_cmd}"
            self.app.logger.info(
                f"{self.log_prefix} Build container image for {_container_repository_and_tag}"
            )
            rc, out, err = run_command(
                command=podman_build_cmd, log_prefix=self.log_prefix
            )
            if not rc and self.pull_request and set_check:
                with open(base_path, "w") as fd:
                    fd.write(f"stdout: {out}, stderr: {err}")

                return self.set_container_build_failure(target_url=base_url)

            self.app.logger.info(
                f"{self.log_prefix} Done building {_container_repository_and_tag}"
            )
            if self.pull_request and set_check:
                with open(base_path, "w") as fd:
                    fd.write(out)

                return self.set_container_build_success(target_url=base_url)

    def _build_and_push_container(self):
        if not self.build_and_push_container:
            return

        repository_creds = (
            f"{self.container_repository_username}:{self.container_repository_password}"
        )

        self._build_container(set_check=False)
        _container_repository_and_tag = self._container_repository_and_tag()
        push_cmd = (
            f"podman push --creds {repository_creds} {_container_repository_and_tag}"
        )
        self.app.logger.info(
            f"{self.log_prefix} Push container image to {_container_repository_and_tag}"
        )

        if not run_command(command=push_cmd, log_prefix=self.log_prefix)[0]:
            return

        if self.pull_request:
            self.pull_request.create_issue_comment(
                f"Container {_container_repository_and_tag} pushed"
            )

        if self.slack_webhook_url:
            message = f"""
```
{self.log_prefix} New container for {_container_repository_and_tag} published.
```
"""
            self.send_slack_message(
                message=message,
                webhook_url=self.slack_webhook_url,
            )

        self.app.logger.info(
            f"{self.log_prefix} Done push {_container_repository_and_tag}"
        )

    def _install_python_module(self):
        if not self.pypi:
            return False

        if self.is_check_run_in_progress(check_run=PYTHON_MODULE_INSTALL_STR):
            self.app.logger.info(
                f"{self.log_prefix} Check run is in progress, not running {PYTHON_MODULE_INSTALL_STR}."
            )
            return False

        self.set_python_module_install_in_progress()

        self.app.logger.info(f"{self.log_prefix} Installing python module")
        base_path = f"/webhook_server/python-module-install/{self.pull_request.number}"
        base_url = f"{self.webhook_url}{base_path}"

        with self._clone_repository(
            path_suffix=f"python-module-install-{shortuuid.uuid()}"
        ):
            self.app.logger.info(f"{self.log_prefix} Current directory: {os.getcwd()}")
            if not self._checkout_pull_request():
                return False

            build_cmd = "pipx install . --include-deps --force"
            rc, out, err = run_command(command=build_cmd, log_prefix=self.log_prefix)
            if not rc:
                with open(base_path, "w") as fd:
                    fd.write(f"stdout: {out}, stderr: {err}")

                return self.set_python_module_install_failure(target_url=base_url)

            with open(base_path, "w") as fd:
                fd.write(out)

            return self.set_python_module_install_success(target_url=base_url)

    def send_slack_message(self, message, webhook_url):
        slack_data = {"text": message}
        self.app.logger.info(f"{self.log_prefix} Sending message to slack: {message}")
        response = requests.post(
            webhook_url,
            data=json.dumps(slack_data),
            headers={"Content-Type": "application/json"},
        )
        if response.status_code != 200:
            raise ValueError(
                f"Request to slack returned an error {response.status_code} with the following message: "
                f"{response.text}"
            )

    def _process_verified(self, parent_committer):
        if not self.verified_job:
            return

        if parent_committer in (self.api_user, "pre-commit-ci[bot]"):
            self.app.logger.info(
                f"{self.log_prefix} Committer {parent_committer} == API user "
                f"{parent_committer}, Setting verified label"
            )
            self._add_label(label=VERIFIED_LABEL_STR)
            self.set_verify_check_success()
        else:
            self.reset_verify_label()
            self.set_verify_check_queued()

    def check_rate_limit(self):
        minimum_limit = 50
        rate_limit = self.github_api.get_rate_limit()
        rate_limit_reset = rate_limit.core.reset
        rate_limit_remaining = rate_limit.core.remaining
        rate_limit_limit = rate_limit.core.limit
        self.app.logger.info(
            f"{self.log_prefix}  API rate limit: Current {rate_limit_remaining} of {rate_limit_limit}. "
            f"Reset in {rate_limit_reset} (UTC time is {datetime.datetime.utcnow()})"
        )
        while (
            datetime.datetime.utcnow() < rate_limit_reset
            and rate_limit_remaining < minimum_limit
        ):
            self.app.logger.warning(
                f"{self.log_prefix} Rate limit is below {minimum_limit} waiting till {rate_limit_reset}"
            )
            time_for_limit_reset = (
                rate_limit_reset - datetime.datetime.utcnow()
            ).seconds
            self.app.logger.info(
                f"{self.log_prefix} Sleeping {time_for_limit_reset} seconds"
            )
            time.sleep(time_for_limit_reset + 1)
            rate_limit = self.github_api.get_rate_limit()
            rate_limit_reset = rate_limit.core.reset
            rate_limit_remaining = rate_limit.core.remaining

    def create_comment_reaction(self, issue_comment_id, reaction):
        _comment = self.pull_request.get_issue_comment(issue_comment_id)
        _comment.create_reaction(reaction)

    @contextmanager
    def set_os_env_github_token(self):
        """
        Set os environment GitHub token for `hub` cli.

        Since the code run in parallel we need to wait if we already have
         a token configured (every repository can have different token)
        """
        github_token_env = "GITHUB_TOKEN"
        os_env_github_token = os.environ.get(github_token_env)
        if os_env_github_token and os_env_github_token != self.token:
            while True:
                if not os.environ.get(github_token_env):
                    break
                time.sleep(1)

        os.environ[github_token_env] = self.token
        yield
        os.environ.pop(github_token_env)

    def _checkout_pull_request(self):
        self.app.logger.info(f"{self.log_prefix} Current directory: {os.getcwd()}")
        pr_number = f"origin/pr/{self.pull_request.number}"
        checkout_cmd = f"git checkout {pr_number}"
        return run_command(command=checkout_cmd, log_prefix=self.log_prefix)[0]

    def process_opened_or_synchronize_pull_request(
        self, parent_committer, pull_request_branch
    ):
        self.set_merge_check_queued()
        self.set_run_tox_check_queued()
        self.set_python_module_install_queued()
        self.set_container_build_queued()
        self.set_sonarqube_queued()
        self._process_verified(parent_committer=parent_committer)
        self.add_size_label()
        self._add_label(label=f"{BRANCH_LABEL_PREFIX}{pull_request_branch}")
        self.app.logger.info(f"{self.log_prefix} Adding PR owner as assignee")
        self.pull_request.add_to_assignees(parent_committer)
        self.assign_reviewers()

        asyncio.run(self._run_check_runs_async())

        #
        # self._run_sonarqube()
        # self._run_tox()
        # self._install_python_module()
        # self._build_container()

    def run_retest_if_queued(self):
        last_commit_check_runs = list(self.last_commit.get_check_runs())
        for check_run in last_commit_check_runs:
            if check_run.status == QUEUED_STR:
                if check_run.name == TOX_STR:
                    self.app.logger.info(f"{self.log_prefix} retest {TOX_STR}.")
                    self._run_tox()
                if check_run.name == BUILD_CONTAINER_STR:
                    self.app.logger.info(
                        f"{self.log_prefix} retest {BUILD_CONTAINER_STR}."
                    )
                    self._build_container()

                if check_run.name == PYTHON_MODULE_INSTALL_STR:
                    self.app.logger.info(
                        f"{self.log_prefix} retest {PYTHON_MODULE_INSTALL_STR}."
                    )
                    self._install_python_module()

    def is_check_run_in_progress(self, check_run):
        last_commit_check_runs = list(self.last_commit.get_check_runs())
        for run in last_commit_check_runs:
            if run.name == check_run and run.status == IN_PROGRESS_STR:
                return True
        return False

    def _run_sonarqube(self):
        if not self.sonarqube_project_key:
            return False

        with self._clone_repository(path_suffix=f"sonarqube-{shortuuid.uuid()}"):
            self.app.logger.info(f"{self.log_prefix} Current directory: {os.getcwd()}")
            if not self._checkout_pull_request():
                return False

            self.set_sonarqube_in_progress()
            target_url = (
                f"{self.sonarqube_url}/dashboard?id={self.sonarqube_project_key}"
            )
            cmd = (
                f"/sonar-scanner-cli/bin/sonar-scanner -Dsonar.projectKey={self.sonarqube_project_key} "
                f"-Dsonar.sources=. -Dsonar.host.url={self.sonarqube_url} "
                f"-Dsonar.token={self.sonarqube_token}"
            )
            if run_command(command=cmd, log_prefix=self.log_prefix)[0]:
                project_status = self.sonarqube_api.qualitygates.request(
                    path="api/qualitygates/project_status",
                    params={"projectKey": self.sonarqube_project_key},
                ).json()
                if project_status["projectStatus"]["status"]:
                    return self.set_sonarqube_success(target_url=target_url)
                else:
                    return self.set_sonarqube_failure(target_url=target_url)

            else:
                return self.set_sonarqube_failure(target_url=target_url)

    async def _run_check_runs_async(self):
        async def _run_check_run(check_run):
            return check_run()

        task1 = asyncio.create_task(_run_check_run(check_run=self._run_sonarqube))
        task2 = asyncio.create_task(_run_check_run(check_run=self._run_tox))
        task3 = asyncio.create_task(
            _run_check_run(check_run=self._install_python_module)
        )
        task4 = asyncio.create_task(_run_check_run(check_run=self._build_container))
        await task1
        await task2
        await task3
        await task4
