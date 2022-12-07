import os
import re
import shlex
import shutil
import subprocess
from contextlib import contextmanager

import requests
import yaml
from constants import LABELS_DICT
from github import Github, GithubException
from github.GithubException import UnknownObjectException


@contextmanager
def change_directory(directory):
    old_cwd = os.getcwd()
    yield os.chdir(directory)
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
        self.welcome_msg = """
The following are automatically added:
 * Add reviewers from OWNER file (in the root of the repository) under reviewers section.
 * Set PR size label.
 * New issue is created for the PR.

Available user actions:
 * To mark PR as verified add `!verified` to a PR comment, to un-verify add `!-verified` to a PR comment.
        Verified label removed on each new commit push.
 * To cherry pick a merged PR add `!cherry-pick <target branch to cherry-pick to>` to a PR comment.
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

    @staticmethod
    def _get_labels_dict(labels):
        _labels = {}
        for label in labels:
            _labels[label.name.lower()] = label
        return _labels

    @staticmethod
    def _get_last_commit(pull_request):
        return list(pull_request.get_commits())[-1]

    def _remove_label(self, obj, label):
        self.app.logger.info(f"{self.repository_name}: Removing label {label}")
        return obj.remove_from_labels(label)

    def _add_label(self, obj, label):
        if len(label) > 49:
            self.app.logger.warning(f"{label} is to long, not adding.")
            return

        _color = [
            LABELS_DICT.get(_label.lower())
            for _label in LABELS_DICT
            if label.lower().startswith(_label)
        ]
        self.app.logger.info(
            f"Label {label} was {'found' if _color else 'not found'} in labels dict"
        )
        color = _color[0] if _color else LABELS_DICT["base"]
        self.app.logger.info(
            f"{self.repository_name}: Adding label {label} with color {color}"
        )

        try:
            _repo_label = self.repository.get_label(label)
            _repo_label.edit(name=_repo_label.name, color=color)
        except UnknownObjectException:
            self.repository.create_label(name=label, color=color)

        return obj.add_to_labels(label)

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
        self.app.logger.info(f"Run: {clone_cmd}")
        subprocess.check_output(shlex.split(clone_cmd))
        subprocess.check_output(
            shlex.split(
                f"git config --global user.name '{self.repository.owner.login}'"
            )
        )
        subprocess.check_output(
            shlex.split(
                f"git config --global user.email '{self.repository.owner.email}'"
            )
        )
        with change_directory(_clone_path):
            subprocess.check_output(shlex.split("git remote update"))
            subprocess.check_output(shlex.split("git fetch --all"))

        yield _clone_path
        shutil.rmtree(_clone_path, ignore_errors=True)

    def _checkout_tag(self, repo_path, tag):
        with change_directory(repo_path):
            self.app.logger.info(f"{self.repository_name}: Checking out tag: {tag}")
            subprocess.check_output(shlex.split(f"git checkout {tag}"))

    def _checkout_new_branch(self, repo_path, source_branch, new_branch_name):
        with change_directory(repo_path):
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
        repo_path,
        source_branch,
        new_branch_name,
        commit_hash,
        commit_msg,
        pull_request_url,
        user_login,
        issue,
    ):
        def _issue_from_err(_err, _commit_hash, _source_branch):
            _err_msg = _err.decode("utf-8")
            self.app.logger.error(
                f"{self.repository_name}: Cherry pick failed: {_err_msg}"
            )
            local_branch_name = _commit_hash[:39]
            issue.create_comment(
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

        self.app.logger.info(
            f"{self.repository_name}: Cherry picking {commit_hash} into {source_branch}, requested by {user_login}"
        )
        with change_directory(repo_path):
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
                return _issue_from_err(
                    _err=err, _commit_hash=commit_hash, _source_branch=source_branch
                )

        return True

    def upload_to_pypi(self, repo_path):
        with change_directory(repo_path):
            self.app.logger.info(f"{self.repository_name}: Start uploading to pypi")
            os.environ["TWINE_USERNAME"] = "__token__"
            os.environ["TWINE_PASSWORD"] = self.pypi_token
            build_folder = "dist"

            _out = subprocess.check_output(
                shlex.split(f"python -m build --sdist --outdir {build_folder}/")
            )
            dist_pkg = re.search(
                r"Successfully built (.*.tar.gz)", _out.decode("utf-8")
            ).group(1)
            dist_pkg_path = os.path.join(build_folder, dist_pkg)
            subprocess.check_output(shlex.split(f"twine check {dist_pkg_path}"))
            self.app.logger.info(
                f"{self.repository_name}: Uploading to pypi: {dist_pkg}"
            )
            subprocess.check_output(
                shlex.split(f"twine upload {dist_pkg_path} --skip-existing")
            )

    @property
    def repository_labels(self):
        return self._get_labels_dict(labels=self.repository.get_labels())

    def obj_labels(self, obj):
        return self._get_labels_dict(labels=obj.get_labels())

    @property
    def reviewers(self):
        owners_file_url = (
            f"https://raw.githubusercontent.com/{self.repository.owner.login}/"
            f"{self.repository.name}/main/OWNERS"
        )
        content = requests.get(owners_file_url).text
        return yaml.safe_load(content).get("reviewers", [])

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
            self._add_label(obj=pull_request, label=label)

        else:
            if label.lower() != current_size_label.lower():
                self._remove_label(obj=pull_request, label=current_size_label)
                self._add_label(obj=pull_request, label=label)

    def label_by_user_comment(self, issue, user_request):
        _label = user_request[1]

        # Skip sonar tests comments
        if "sonarsource.github.io" in _label:
            return

        self.app.logger.info(
            f"{self.repository_name}: Label requested by user: {_label}"
        )
        if user_request[0] == "-":
            label = self.obj_labels(obj=issue).get(_label.lower())
            if label:
                self._remove_label(obj=issue, label=label.name)

        else:
            self._add_label(obj=issue, label=_label)

    def reset_verify_label(self, pull_request):
        self.app.logger.info(
            f"{self.repository_name}: Processing reset verify label on new commit push"
        )
        pull_labels = self.obj_labels(obj=pull_request)
        # Remove Verified label
        if pull_labels.get(self.verified_label.lower()):
            self._remove_label(obj=pull_request, label=self.verified_label)

    def set_verify_check_pending(self, pull_request):
        self.app.logger.info(
            f"{self.repository_name}: Processing set verified check pending"
        )
        last_commit = self._get_last_commit(pull_request)
        last_commit.create_status(
            state="pending",
            description="Waiting for verification (!verified)",
            context="Verified label",
        )

    def set_verify_check_success(self, pull_request):
        self.app.logger.info(f"{self.repository_name}: Set verified check to success")
        last_commit = self._get_last_commit(pull_request)
        last_commit.create_status(
            state="success",
            description="Waiting for verification (!verified)",
            context="Verified label",
        )

    def create_issue_for_new_pr(self, pull_request):
        self.app.logger.info(
            f"{self.repository_name}: Creating issue for new PR: {pull_request.title}"
        )
        self.repository.create_issue(
            title=self._generate_issue_title(pull_request),
            body=self._generate_issue_body(pull_request=pull_request),
            assignee=pull_request.user.login,
        )

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
        issue = self.repository.get_issue(issue_number)
        body = self.hook_data["comment"]["body"]
        if body == self.welcome_msg:
            self.app.logger.info(
                f"{self.repository_name}: Welcome message found in issue {issue.title}. Not processing"
            )
            return

        user_requests = re.findall(r"!(-)?(.*)", body)
        user_login = self.hook_data["sender"]["login"]
        for user_request in user_requests:
            if "cherry-pick" in user_request[1]:
                self.app.logger.info(
                    f"{self.repository_name}: Cherry-pick requested by user: {user_request[1]}"
                )
                pull_request = self.repository.get_pull(issue_number)
                if not pull_request.is_merged():
                    error_msg = (
                        f"Cherry-pick requested for unmerged PR: "
                        f"{pull_request.title} is not supported"
                    )
                    self.app.logger.info(f"{self.repository_name}: {error_msg}")
                    self._get_last_commit(pull_request)
                    issue.create_comment(error_msg)
                    return

                base_source_branch_name = re.sub(
                    r"auto-cherry-pick: \[.*\] ",
                    "",
                    pull_request.head.ref.replace(" ", "-"),
                )
                new_branch_name = f"auto-cherry-pick-{base_source_branch_name}"
                source_branch = user_request[1].split()[1]
                if not self.is_branch_exists(branch=source_branch):
                    err_msg = f"cherry-pick failed: {source_branch} does not exists"
                    self.app.logger.error(err_msg)
                    issue.create_comment(err_msg)
                else:
                    with self._clone_repository(
                        path_suffix=base_source_branch_name
                    ) as repo_path:
                        self._checkout_new_branch(
                            repo_path=repo_path,
                            source_branch=source_branch,
                            new_branch_name=new_branch_name,
                        )
                        if self._cherry_pick(
                            repo_path=repo_path,
                            source_branch=source_branch,
                            new_branch_name=new_branch_name,
                            commit_hash=pull_request.merge_commit_sha,
                            commit_msg=pull_request.title,
                            pull_request_url=pull_request.html_url,
                            user_login=user_login,
                            issue=issue,
                        ):
                            issue.create_comment(
                                f"Cherry-picked PR {pull_request.title} into {source_branch}"
                            )
            else:
                self.app.logger.info(
                    f"{self.repository_name}: Processing label by user comment"
                )
                self.label_by_user_comment(issue=issue, user_request=user_request)

    def process_pull_request_webhook_data(self):
        pull_request = self.repository.get_pull(self.hook_data["number"])
        hook_action = self.hook_data["action"]
        self.app.logger.info(f"hook_action is: {hook_action}")

        if hook_action == "opened":
            pull_request_data = self.hook_data["pull_request"]
            self.add_size_label(pull_request=pull_request)
            self._add_label(
                obj=pull_request, label=f"branch-{pull_request_data['base']['ref']}"
            )
            self.app.logger.info(f"{self.repository_name}: Adding PR owner as assignee")
            if pull_request.title.startswith(
                "auto-cherry-pick:"
            ) and "auto-cherry-pick" in [_lb.name for _lb in pull_request.labels]:
                parent_committer = re.search(
                    r"requested-by (\w+)", pull_request.body
                ).group(1)
            else:
                parent_committer = pull_request_data["user"]["login"]

            pull_request.add_to_assignees(parent_committer)
            for reviewer in self.reviewers:
                if reviewer != pull_request.user.login:
                    self.app.logger.info(
                        f"{self.repository_name}: Adding reviewer {reviewer}"
                    )
                    pull_request.create_review_request([reviewer])

            self.create_issue_for_new_pr(pull_request=pull_request)
            self.app.logger.info(f"{self.repository_name}: Creating welcome comment")

            pull_request.create_issue_comment(self.welcome_msg)

        if hook_action == "closed" or hook_action == "merged":
            self.close_issue_for_merged_or_closed_pr(
                pull_request=pull_request, hook_action=hook_action
            )

        if hook_action == "synchronize":
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
                self._remove_label(obj=pull_request, label=_reviewed_label)

            if self.verified_job:
                self.reset_verify_label(pull_request=pull_request)
                self.set_verify_check_pending(pull_request=pull_request)

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
                with self._clone_repository(path_suffix=tag_name) as repo_path:
                    self._checkout_tag(repo_path=repo_path, tag=tag_name)
                    self.upload_to_pypi(repo_path=repo_path)

    def process_pull_request_review_webhook_data(self):
        if self.hook_data["action"] == "submitted":
            """
            commented
            approved
            changes_requested
            """
            reviewed_user = self.hook_data["review"]["user"]["login"]
            user_label = f"{self.reviewed_by_prefix}{reviewed_user}"
            reviewed_user_state = self.hook_data["review"]["state"]
            pr_owner = self.hook_data["pull_request"]["user"]["login"]
            if pr_owner == reviewed_user:
                return

            pull_request = self.repository.get_pull(
                self.hook_data["pull_request"]["number"]
            )
            reviewer_label = f"{reviewed_user_state.title()}{user_label}"
            pull_request_labels = self.obj_labels(obj=pull_request)
            for _label in pull_request_labels:
                if user_label in _label:
                    self._remove_label(obj=pull_request, label=_label)

            # if reviewer_label not in self.obj_labels(obj=pull_request):
            self._add_label(obj=pull_request, label=reviewer_label)
