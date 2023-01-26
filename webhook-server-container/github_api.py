import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from contextlib import contextmanager

import yaml
from constants import ADD_STR, ALL_LABELS_DICT, DELETE_STR, USER_LABELS_DICT
from github import Github, GithubException
from github.GithubException import UnknownObjectException


@contextmanager
def change_directory(directory, logger):
    logger.info(f"Changing directory to {directory}")
    old_cwd = os.getcwd()
    yield os.chdir(directory)
    logger.info(f"Changing back to directory {old_cwd}")
    os.chdir(old_cwd)


class RepositoryNotFoundError(Exception):
    pass


class GitHubApi:
    def __init__(self, app, hook_data):
        self.app = app
        self.hook_data = hook_data
        self.repository_name = hook_data["repository"]["name"]
        self._repo_data_from_config()
        self.api = Github(login_or_token=self.token)
        self.repository = self.api.get_repo(self.repository_full_name)
        self.verified_label = "verified"
        self.size_label_prefix = "size/"
        self.clone_repository_path = os.path.join("/", self.repository.name)
        self.reviewed_by_prefix = "-by-"
        self.auto_cherry_pick_prefix = "auto-cherry-pick:"
        supported_user_labels_str = "".join(
            [f"* {label}\n  " for label in USER_LABELS_DICT.keys()]
        )
        self.welcome_msg = f"""
The following are automatically added:
 * Add reviewers from OWNER file (in the root of the repository) under reviewers section.
 * Set PR size label.
 * New issue is created for the PR.

Available user actions:
 * To mark PR as verified add `!verified` to a PR comment, to un-verify add `!-verified` to a PR comment.
        Verified label removed on each new commit push.
 * To cherry pick a merged PR add `!cherry-pick <target branch to cherry-pick to>` to a PR comment.
 * To add a label by comment use `!<label name>`, to remove, use `!-<label name>`
  Supported labels:
  {supported_user_labels_str}
            """

    def process_hook(self, data):
        if data == "issue_comment":
            self.process_comment_webhook_data()

        if data == "pull_request":
            self.process_pull_request_webhook_data()

        if data == "push":
            self.process_push_webhook_data()

        if data == "pull_request_review":
            self.process_pull_request_review_webhook_data()

    def _repo_data_from_config(self):
        with open("/config/config.yaml") as fd:
            repos = yaml.safe_load(fd)

        data = repos["repositories"].get(self.repository_name)
        if not data:
            raise RepositoryNotFoundError(
                f"Repository {self.repository_name} not found in config file"
            )

        self.token = data["token"]
        os.environ["GITHUB_TOKEN"] = self.token
        self.repository_full_name = data["name"]
        self.upload_to_pypi_enabled = data.get("upload_to_pypi")
        self.pypi_token = data.get("pypi_token")
        self.verified_job = data.get("verified_job", True)
        self.tox_enabled = data.get("tox")

    @staticmethod
    def _get_labels_dict(labels):
        _labels = {}
        for label in labels:
            _labels[label.name.lower()] = label
        return _labels

    @staticmethod
    def _get_last_commit(pull_request):
        return list(pull_request.get_commits())[-1]

    def _remove_label(self, pull_request, label):
        pull_request_labels = self.obj_labels(obj=pull_request)
        for _label in pull_request_labels:
            if label in _label:
                self.app.logger.info(f"{self.repository_name}: Removing label {label}")
                return pull_request.remove_from_labels(label)

    def _add_label(self, pull_request, label):
        if len(label) > 49:
            self.app.logger.warning(f"{label} is to long, not adding.")
            return

        _color = [
            ALL_LABELS_DICT.get(_label.lower())
            for _label in ALL_LABELS_DICT
            if label.lower().startswith(_label)
        ]
        self.app.logger.info(
            f"Label {label} was {'found' if _color else 'not found'} in labels dict"
        )
        color = _color[0] if _color else ALL_LABELS_DICT["base"]
        self.app.logger.info(
            f"{self.repository_name}: Adding label {label} with color {color}"
        )

        try:
            _repo_label = self.repository.get_label(label)
            _repo_label.edit(name=_repo_label.name, color=color)
            self.app.logger.info(
                f"{self.repository_name}: Edit repository label {label} with color {color}"
            )
        except UnknownObjectException:
            self.app.logger.info(
                f"{self.repository_name}: Add repository label {label} with color {color}"
            )
            self.repository.create_label(name=label, color=color)

        self.app.logger.info(
            f"{self.repository_name}: Adding pull request label {label} to {pull_request.number}"
        )
        return pull_request.add_to_labels(label)

    @staticmethod
    def _generate_issue_title(pull_request):
        return f"{pull_request.title} - {pull_request.number}"

    @staticmethod
    def _generate_issue_body(pull_request):
        return f"[Auto generated]\nNumber: [#{pull_request.number}]"

    @contextmanager
    def _clone_repository(self, path_suffix):
        _clone_path = f"{self.clone_repository_path}-{path_suffix}"
        self.app.logger.info(
            f"Cloning repository: {self.repository_full_name} into {_clone_path}"
        )
        clone_cmd = (
            f"git clone {self.repository.clone_url.replace('https://', f'https://{self.token}@')} "
            f"{_clone_path}"
        )
        git_user_name_cmd = (
            f"git config --global user.name '{self.repository.owner.login}'"
        )
        git_email_cmd = (
            f"git config --global user.email '{self.repository.owner.email}'"
        )
        fetch_pr_cmd = "git config --local --add remote.origin.fetch +refs/pull/*/head:refs/remotes/origin/pr/*"
        remote_update_cmd = "git remote update"
        fetch_all_cmd = "git fetch --all"

        for cmd in [
            clone_cmd,
            git_user_name_cmd,
            git_email_cmd,
        ]:
            self.app.logger.info(f"Run: {cmd}")
            subprocess.check_output(shlex.split(cmd))

        with change_directory(_clone_path, logger=self.app.logger):
            for cmd in [fetch_pr_cmd, remote_update_cmd, fetch_all_cmd]:
                self.app.logger.info(f"Run: {cmd}")
                subprocess.check_output(shlex.split(cmd))
            yield _clone_path

        shutil.rmtree(_clone_path, ignore_errors=True)

    def _checkout_tag(self, tag):
        self.app.logger.info(f"{self.repository_name}: Checking out tag: {tag}")
        subprocess.check_output(shlex.split(f"git checkout {tag}"))

    def _checkout_new_branch(self, source_branch, new_branch_name):
        self.app.logger.info(
            f"{self.repository_name}: Checking out new branch: {new_branch_name} from {source_branch}"
        )
        subprocess.check_output(
            shlex.split(f"git checkout -b {new_branch_name} origin/{source_branch}")
        )

    def is_branch_exists(self, branch):
        try:
            return self.repository.get_branch(branch)
        except GithubException:
            return False

    def _cherry_pick(
        self,
        source_branch,
        new_branch_name,
        pull_request,
        user_login,
    ):
        commit_hash = pull_request.merge_commit_sha
        commit_msg = pull_request.title
        pull_request_url = pull_request.html_url

        def _issue_from_err(_err, _commit_hash, _source_branch):
            _err_msg = _err.decode("utf-8")
            self.app.logger.error(
                f"{self.repository_name}: Cherry pick failed: {_err_msg}"
            )
            local_branch_name = _commit_hash[:39]
            pull_request.create_issue_comment(
                f"**Manual cherry-pick is needed**\nCherry pick failed for "
                f"{_commit_hash} to {_source_branch}:\n{_err_msg}\n"
                f"To cherry-pick run:\n"
                "```\n"
                f"git checkout {_source_branch}\n"
                f"git checkout -b {local_branch_name}\n"
                f"git cherry-pick {commit_hash}\n"
                f"git push origin {local_branch_name}\n"
                "```"
            )
            return False

        try:
            self.app.logger.info(
                f"{self.repository_name}: Cherry picking {commit_hash} into {source_branch}, requested by "
                f"{user_login}"
            )
            cherry_pick = subprocess.Popen(
                shlex.split(f"git cherry-pick {commit_hash}"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _, err = cherry_pick.communicate()
            if cherry_pick.returncode != 0:
                return _issue_from_err(
                    _err=err, _commit_hash=commit_hash, _source_branch=source_branch
                )

            git_push = subprocess.Popen(
                shlex.split(f"git push -u origin {new_branch_name}"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _, err = git_push.communicate()
            if git_push.returncode != 0:
                return _issue_from_err(
                    _err=err, _commit_hash=commit_hash, _source_branch=source_branch
                )

            pull_request_cmd = subprocess.Popen(
                shlex.split(
                    f"hub pull-request "
                    f"-b {source_branch} "
                    f"-h {new_branch_name} "
                    f"-l auto-cherry-pick "
                    f"-m '{self.auto_cherry_pick_prefix} [{source_branch}] {commit_msg}' "
                    f"-m 'cherry-pick {pull_request_url} into {source_branch}' "
                    f"-m 'requested-by {user_login}'"
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _, err = pull_request_cmd.communicate()
            if pull_request_cmd.returncode != 0:
                _issue_from_err(
                    _err=err, _commit_hash=commit_hash, _source_branch=source_branch
                )
                return False

            return True
        except Exception:
            _issue_from_err(
                _err=err, _commit_hash=commit_hash, _source_branch=source_branch
            )
            return False

    def upload_to_pypi(self):
        self.app.logger.info(f"{self.repository_name}: Start uploading to pypi")
        os.environ["TWINE_USERNAME"] = "__token__"
        os.environ["TWINE_PASSWORD"] = self.pypi_token
        build_folder = "dist"

        _out = subprocess.check_output(
            shlex.split(f"{sys.executable} -m build --sdist --outdir {build_folder}/")
        )
        dist_pkg = re.search(
            r"Successfully built (.*.tar.gz)", _out.decode("utf-8")
        ).group(1)
        dist_pkg_path = os.path.join(build_folder, dist_pkg)
        subprocess.check_output(shlex.split(f"twine check {dist_pkg_path}"))
        self.app.logger.info(f"{self.repository_name}: Uploading to pypi: {dist_pkg}")
        subprocess.check_output(
            shlex.split(f"twine upload {dist_pkg_path} --skip-existing")
        )
        self.app.logger.info(f"{self.repository_name}: Uploading to pypi finished")

    @property
    def repository_labels(self):
        return self._get_labels_dict(labels=self.repository.get_labels())

    def obj_labels(self, obj):
        return self._get_labels_dict(labels=obj.get_labels())

    @property
    def reviewers(self):
        owners_content = self.repository.get_contents("OWNERS")
        return yaml.safe_load(owners_content.decoded_content).get("reviewers", [])

    def assign_reviewers(self, pull_request):
        for reviewer in self.reviewers:
            if reviewer != pull_request.user.login:
                self.app.logger.info(
                    f"{self.repository_name}: Adding reviewer {reviewer}"
                )
                try:
                    pull_request.create_review_request([reviewer])
                except GithubException as ex:
                    self.app.logger.error(ex)

    def add_size_label(self, pull_request, current_size_label=None):
        size = pull_request.additions + pull_request.deletions
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

        label = f"{self.size_label_prefix}{_label}"
        if not current_size_label:
            self._add_label(pull_request=pull_request, label=label)

        else:
            if label.lower() != current_size_label.lower():
                self._remove_label(pull_request=pull_request, label=current_size_label)
                self._add_label(pull_request=pull_request, label=label)

    def label_by_user_comment(self, pull_request, user_request, reviewed_user):
        _label = user_request[1]

        # Skip sonar tests comments
        if "sonarsource.github.io" in _label:
            return

        if not any(
            _label.lower().startswith(label_name) for label_name in USER_LABELS_DICT
        ):
            self.app.logger.info(
                f"Label {_label} is not a predefined one, will not be added / removed."
            )
            return

        self.app.logger.info(
            f"{self.repository_name}: Label requested by user {reviewed_user}: {_label}"
        )
        if user_request[0] == "-":
            if _label.lower() == "lgtm":
                self.manage_reviewed_by_label(
                    review_state="approved",
                    action=DELETE_STR,
                    reviewed_user=reviewed_user,
                )
            else:
                label = self.obj_labels(obj=pull_request).get(_label.lower())
                if label:
                    self._remove_label(pull_request=pull_request, label=label.name)

        else:
            if _label.lower() == "lgtm":
                self.manage_reviewed_by_label(
                    review_state="approved", action=ADD_STR, reviewed_user=reviewed_user
                )
            else:
                self._add_label(pull_request=pull_request, label=_label)

    def reset_verify_label(self, pull_request):
        self.app.logger.info(
            f"{self.repository_name}: Processing reset verify label on new commit push"
        )
        # Remove Verified label
        self._remove_label(pull_request=pull_request, label=self.verified_label)

    def set_verify_check_pending(self, pull_request):
        self.app.logger.info(
            f"{self.repository_name}: Processing set verified check pending"
        )
        last_commit = self._get_last_commit(pull_request)
        last_commit.create_status(
            state="pending",
            description="Waiting for verification (!verified)",
            context="Verified",
        )

    def set_verify_check_success(self, pull_request):
        self.app.logger.info(f"{self.repository_name}: Set verified check to success")
        last_commit = self._get_last_commit(pull_request)
        last_commit.create_status(
            state="success",
            description="Verified",
            context="Verified",
        )

    def set_run_tox_check_pending(self, pull_request):
        if not self.tox_enabled:
            return

        self.app.logger.info(
            f"{self.repository_name}: Processing set tox check pending"
        )
        last_commit = self._get_last_commit(pull_request)
        last_commit.create_status(
            state="pending",
            description="Pending",
            context="tox",
        )

    def set_run_tox_check_failure(self, pull_request, tox_error):
        self.app.logger.info(
            f"{self.repository_name}: Processing set tox check failure"
        )
        error_comment = pull_request.create_issue_comment(tox_error)
        last_commit = self._get_last_commit(pull_request)
        last_commit.create_status(
            state="failure",
            description="Failed",
            target_url=error_comment.html_url,
            context="tox",
        )

    def set_run_tox_check_success(self, pull_request):
        self.app.logger.info(f"{self.repository_name}: Set tox check to success")
        last_commit = self._get_last_commit(pull_request)
        last_commit.create_status(
            state="success",
            description="Successful",
            context="tox",
        )

    def create_issue_for_new_pr(self, pull_request):
        try:
            self.app.logger.info(
                f"{self.repository_name}: Creating issue for new PR: {pull_request.title}"
            )
            self.repository.create_issue(
                title=self._generate_issue_title(pull_request),
                body=self._generate_issue_body(pull_request=pull_request),
                assignee=pull_request.user.login,
            )
        except Exception as ex:
            self.app.logger.error(f"Failed to create issue: {ex}")

    def close_issue_for_merged_or_closed_pr(self, pull_request, hook_action):
        for issue in self.repository.get_issues():
            if issue.body == self._generate_issue_body(pull_request=pull_request):
                self.app.logger.info(
                    f"{self.repository_name}: Closing issue {issue.title} for PR: {pull_request.title}"
                )
                issue.create_comment(
                    f"{self.repository_name}: Closing issue for PR: {pull_request.title}.\nPR was {hook_action}."
                )
                issue.edit(state="closed")
                break

    def process_comment_webhook_data(self):
        if self.hook_data["action"] == "action":
            return

        issue_number = self.hook_data["issue"]["number"]
        self.app.logger.info(f"Processing issue {issue_number}")

        try:
            pull_request = self.repository.get_pull(issue_number)
        except UnknownObjectException:
            self.app.logger.error(f"Pull request {issue_number} not found")
            return

        body = self.hook_data["comment"]["body"]
        if body == self.welcome_msg:
            self.app.logger.info(
                f"{self.repository_name}: Welcome message found in issue {pull_request.title}. Not processing"
            )
            return

        _user_requests = re.findall(r"!(-)?(.*)", body)
        if _user_requests:
            self.app.logger.info(f"User comment: {_user_requests}")

        _user_commands = re.findall(r"/(.*)", body)
        if _user_commands:
            self.app.logger.info(f"User commands: {_user_commands}")

        user_login = self.hook_data["sender"]["login"]

        for user_request in _user_requests:
            _user_request = user_request[1]
            if "cherry-pick" in _user_request:
                self.app.logger.info(
                    f"{self.repository_name}: Cherry-pick requested by user: {_user_request}"
                )
                if not pull_request.is_merged():
                    error_msg = (
                        f"Cherry-pick requested for unmerged PR: "
                        f"{pull_request.title} is not supported"
                    )
                    self.app.logger.info(f"{self.repository_name}: {error_msg}")
                    self._get_last_commit(pull_request)
                    pull_request.create_issue_comment(error_msg)
                    return

                self.cherry_pick(
                    pull_request=pull_request, target_branch=_user_request.split()[1]
                )
            else:
                self.app.logger.info(
                    f"{self.repository_name}: Processing label/user command by user comment"
                )
                self.label_by_user_comment(
                    pull_request=pull_request,
                    user_request=user_request,
                    reviewed_user=user_login,
                )

        for user_command in _user_commands:
            self.user_commands(command=user_command, pull_request=pull_request)

    @staticmethod
    def get_pr_owner(pull_request, pull_request_data):
        if pull_request.title.startswith(
            "auto-cherry-pick:"
        ) and "auto-cherry-pick" in [_lb.name for _lb in pull_request.labels]:
            parent_committer = re.search(
                r"requested-by (\w+)", pull_request.body
            ).group(1)
        else:
            parent_committer = pull_request_data["user"]["login"]

        return parent_committer

    def process_pull_request_webhook_data(self):
        pull_request = self.repository.get_pull(self.hook_data["number"])
        hook_action = self.hook_data["action"]
        self.app.logger.info(f"hook_action is: {hook_action}")

        if hook_action == "opened":
            pull_request_data = self.hook_data["pull_request"]
            pull_request.create_issue_comment(self.welcome_msg)
            self.set_run_tox_check_pending(pull_request=pull_request)

            self.add_size_label(pull_request=pull_request)
            self._add_label(
                pull_request=pull_request,
                label=f"branch-{pull_request_data['base']['ref']}",
            )
            self.app.logger.info(f"{self.repository_name}: Adding PR owner as assignee")
            parent_committer = self.get_pr_owner(
                pull_request=pull_request, pull_request_data=pull_request_data
            )
            pull_request.add_to_assignees(parent_committer)
            self.assign_reviewers(pull_request=pull_request)
            self.create_issue_for_new_pr(pull_request=pull_request)
            self.app.logger.info(f"{self.repository_name}: Creating welcome comment")
            self.run_tox(pull_request=pull_request)

        if hook_action == "closed":
            self.close_issue_for_merged_or_closed_pr(
                pull_request=pull_request, hook_action=hook_action
            )

            self.app.logger.error(self.hook_data)
            if self.hook_data["pull_request"].get("merged"):
                target_version_prefix = "target-version-"
                for _label in pull_request.labels:
                    _label_name = _label.name
                    if _label_name.startswith(target_version_prefix):
                        self.cherry_pick(
                            pull_request=pull_request,
                            target_branch=_label_name.replace(
                                target_version_prefix, ""
                            ),
                        )

        if hook_action == "synchronize":
            self.set_run_tox_check_pending(pull_request=pull_request)
            self.assign_reviewers(pull_request=pull_request)
            all_labels = self.obj_labels(obj=pull_request)
            current_size_label = [
                label
                for label in all_labels
                if label.startswith(self.size_label_prefix)
            ]
            self.add_size_label(
                pull_request=pull_request,
                current_size_label=current_size_label[0]
                if current_size_label
                else None,
            )
            reviewed_by_labels = [
                label
                for label in all_labels
                if self.reviewed_by_prefix.lower() in label.lower()
            ]
            for _reviewed_label in reviewed_by_labels:
                self._remove_label(pull_request=pull_request, label=_reviewed_label)

            if self.verified_job:
                self.reset_verify_label(pull_request=pull_request)
                self.set_verify_check_pending(pull_request=pull_request)

            self.run_tox(pull_request=pull_request)

        if hook_action in ("labeled", "unlabeled"):
            labeled = self.hook_data["label"]["name"].lower()
            if self.verified_job and labeled == self.verified_label:
                if hook_action == "labeled":
                    self.set_verify_check_success(pull_request=pull_request)

                if hook_action == "unlabeled":
                    self.set_verify_check_pending(pull_request=pull_request)

    def process_push_webhook_data(self):
        tag = re.search(r"refs/tags/?(.*)", self.hook_data["ref"])
        if tag:  # If push is to a tag (release created)
            if self.upload_to_pypi_enabled:
                tag_name = tag.group(1)
                self.app.logger.info(
                    f"{self.repository_name}: Processing push for tag: {tag_name}"
                )
                with self._clone_repository(path_suffix=tag_name):
                    self._checkout_tag(tag=tag_name)
                    self.upload_to_pypi()

    def process_pull_request_review_webhook_data(self):
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

    def manage_reviewed_by_label(self, review_state, action, reviewed_user):
        base_dict = self.hook_data.get("issue", self.hook_data.get("pull_request"))
        user_label = f"{self.reviewed_by_prefix}{reviewed_user}"
        pr_owner = base_dict["user"]["login"]
        if pr_owner == reviewed_user:
            return

        pull_request = self.repository.get_pull(base_dict["number"])
        reviewer_label = f"{review_state.title()}{user_label}"

        if action == ADD_STR:
            self._add_label(pull_request=pull_request, label=reviewer_label)
        if action == DELETE_STR:
            self._remove_label(pull_request=pull_request, label=reviewer_label)

    def run_tox(self, pull_request):
        if not self.tox_enabled:
            return

        with self._clone_repository(path_suffix=f"tox-{uuid.uuid4()}"):
            pr_number = f"origin/pr/{pull_request.number}"
            self.app.logger.info(f"checkout origin/pr/{pr_number}")
            try:
                subprocess.check_output(shlex.split(f"git checkout {pr_number}"))
            except subprocess.CalledProcessError as ex:
                self.app.logger.error(f"checkout for {pr_number} failed: {ex}")
                return

            try:
                cmd = "tox"
                if self.tox_enabled != "all":
                    tests = self.tox_enabled.replace(" ", "")
                    cmd += f" -e {tests}"

                self.app.logger.info(f"Run tox command: {cmd}")
                out = subprocess.check_output(shlex.split(cmd))
            except subprocess.CalledProcessError as ex:
                self.set_run_tox_check_failure(
                    pull_request=pull_request,
                    tox_error=ex.output.decode("utf-8"),
                )
            else:
                for_log = None
                out = out.decode("utf-8")
                last_passed = re.findall(r"=.* passed .* =.*", out)
                if last_passed:
                    last_passed = last_passed[-1]
                    # fmt: off
                    for_log = out[out.index(last_passed) + len(last_passed):]
                    # fmt: on
                self.app.logger.info(f"tox finished successfully\n{for_log or out}")
                self.set_run_tox_check_success(pull_request=pull_request)

    def user_commands(self, command, pull_request):
        self.app.logger.info(f"Process user command: {command}")
        if command == "tox":
            self.set_run_tox_check_pending(pull_request=pull_request)
            self.run_tox(pull_request=pull_request)

    def cherry_pick(self, pull_request, target_branch):
        base_source_branch_name = re.sub(
            r"auto-cherry-pick: \[.*\] ",
            "",
            pull_request.head.ref.replace(" ", "-"),
        )
        new_branch_name = f"auto-cherry-pick-{base_source_branch_name}"
        if not self.is_branch_exists(branch=target_branch):
            err_msg = f"cherry-pick failed: {target_branch} does not exists"
            self.app.logger.error(err_msg)
            pull_request.create_issue_comment(err_msg)
        else:
            with self._clone_repository(path_suffix=base_source_branch_name):
                self._checkout_new_branch(
                    source_branch=target_branch,
                    new_branch_name=new_branch_name,
                )
                if self._cherry_pick(
                    source_branch=target_branch,
                    new_branch_name=new_branch_name,
                    pull_request=pull_request,
                    user_login=pull_request.user.login,
                ):
                    pull_request.create_issue_comment(
                        f"Cherry-picked PR {pull_request.title} into {target_branch}"
                    )
