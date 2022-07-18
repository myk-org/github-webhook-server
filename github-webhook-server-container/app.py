import os
import re
import shlex
import shutil
import subprocess
from contextlib import contextmanager

import requests
import yaml
from flask import Flask, request
from github import Github

app = Flask("github_webhook_server")
app.logger.info("Starting github-webhook-server app")


class RepositoryNotFoundError(Exception):
    pass


@contextmanager
def change_directory(directory):
    old_cwd = os.getcwd()
    yield os.chdir(directory)
    os.chdir(old_cwd)


class GutHubApi:
    def __init__(self, hook_data):
        self.hook_data = hook_data
        self.repository_name = hook_data["repository"]["name"]
        self._repo_data_from_config()
        self.api = Github(login_or_token=self.token)
        self.repository = self.api.get_repo(self.repository_full_name)
        self.verified_label = "verified"
        self.size_label_prefix = "size/"
        self.clone_repository_path = os.path.join("/", self.repository.name)

    def _repo_data_from_config(self):
        with open("/config.yaml") as fd:
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
        app.logger.info(f"{self.repository_name}: Removing label {label}")
        return obj.remove_from_labels(label)

    def _add_label(self, obj, label):
        app.logger.info(f"{self.repository_name}: Adding label {label}")
        return obj.add_to_labels(label)

    @staticmethod
    def _generate_issue_title(pull_request):
        return f"{pull_request.title} - {pull_request.number}"

    @staticmethod
    def _generate_issue_body(pull_request):
        return f"[Auto generated]\nNumber: [#{pull_request.number}]"

    def _clone_repository(self):
        app.logger.info(f"Cloning repository: {self.repository_full_name}")
        subprocess.check_output(
            shlex.split(
                f"git clone {self.repository.clone_url.replace('https://', f'https://{self.token}@')} "
                f"{self.clone_repository_path}"
            )
        )
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
        with change_directory(self.clone_repository_path):
            subprocess.check_output(shlex.split("git remote update"))
            subprocess.check_output(shlex.split("git fetch --all"))

    def _checkout_tag(self, tag):
        with change_directory(self.clone_repository_path):
            app.logger.info(f"{self.repository_name}: Checking out tag: {tag}")
            subprocess.check_output(shlex.split(f"git checkout {tag}"))

    def _checkout_new_branch(self, source_branch, new_branch_name):
        with change_directory(self.clone_repository_path):
            app.logger.info(
                f"{self.repository_name}: Checking out new branch: {new_branch_name} from {source_branch}"
            )
            subprocess.check_output(
                shlex.split(f"git checkout -b {new_branch_name} origin/{source_branch}")
            )

    def _cherry_pick(
        self,
        source_branch,
        new_branch_name,
        commit_hash,
        commit_msg,
        pull_request_url,
        user_login,
    ):
        app.logger.info(f"{self.repository_name}: Cherry picking")
        with change_directory(self.clone_repository_path):
            subprocess.check_output(shlex.split(f"git cherry-pick {commit_hash}"))
            subprocess.check_output(
                shlex.split(f"git push -u origin {new_branch_name}")
            )

            subprocess.check_output(
                shlex.split(
                    f"hub pull-request -b {source_branch} -h {new_branch_name} "
                    f"-l auto-cherry-pick -m 'auto-cherry-pick: [{source_branch}] {commit_msg}' "
                    f"-m cherry-pick {pull_request_url} into {source_branch} -m requested-by {user_login}"
                )
            )

    def upload_to_pypi(self):
        with change_directory(self.clone_repository_path):
            app.logger.info(f"{self.repository_name}: Start uploading to pypi")
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
            app.logger.info(f"{self.repository_name}: Uploading to pypi: {dist_pkg}")
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
        app.logger.info(f"{self.repository_name}: Label requested by user: {_label}")
        if user_request[0] == "-" or self.hook_data["action"] == "deleted":
            label = self.obj_labels(obj=issue).get(_label.lower())
            if label:
                self._remove_label(obj=issue, label=label.name)

        else:
            label = self.repository_labels.get(_label.lower())
            if label:
                self._add_label(obj=issue, label=label.name)

    def reset_verify_label(self, pull_request):
        app.logger.info(
            f"{self.repository_name}: Processing reset verify label on new commit push"
        )
        pull_labels = self.obj_labels(obj=pull_request)
        # Remove Verified label
        if pull_labels.get(self.verified_label.lower()):
            self._remove_label(obj=pull_request, label=self.verified_label)

    def set_verify_check_pending(self, pull_request):
        app.logger.info(
            f"{self.repository_name}: Processing set verified check pending"
        )
        last_commit = self._get_last_commit(pull_request)
        last_commit.create_status(
            state="pending",
            description="Waiting for verification (!verified)",
            context="Verified label",
        )

    def set_verify_check_success(self, pull_request):
        app.logger.info(f"{self.repository_name}: Set verified check to success")
        last_commit = self._get_last_commit(pull_request)
        last_commit.create_status(
            state="success",
            description="Waiting for verification (!verified)",
            context="Verified label",
        )

    def create_issue_for_new_pr(self, pull_request):
        app.logger.info(
            f"{self.repository_name}: Creating issue for new PR: {pull_request.title}"
        )
        self.repository.create_issue(
            title=self._generate_issue_title(pull_request),
            body=self._generate_issue_body(pull_request=pull_request),
            assignee=pull_request.user,
        )

    def close_issue_for_merged_or_closed_pr(self, pull_request, hook_action):
        for issue in self.repository.get_issues():
            if issue.body == self._generate_issue_body(pull_request=pull_request):
                app.logger.info(
                    f"{self.repository_name}: Closing issue {issue.title} for PR: {pull_request.title}"
                )
                issue.create_comment(
                    f"{self.repository_name}: Closing issue for PR: {pull_request.title}.\nPR was {hook_action}."
                )
                issue.edit(state="closed")
                break

    def process_comment_webhook_data(self):
        issue_number = self.hook_data["issue"]["number"]
        issue = self.repository.get_issue(issue_number)
        user_requests = re.findall(r"!(-)?(.*)", self.hook_data["comment"]["body"])
        user_login = self.hook_data["sender"]["login"]
        for user_request in user_requests:
            if "cherry-pick" in user_request[1]:
                app.logger.info(
                    f"{self.repository_name}: Cherry-pick requested by user: {user_request[1]}"
                )
                pull_request = self.repository.get_pull(issue_number)
                if not pull_request.is_merged():
                    app.logger.info(
                        f"{self.repository_name}: Cherry-pick requested for unmerged PR: "
                        f"{pull_request.title} is not supported"
                    )
                    return

                new_branch_name = (
                    f"auto-cherry-pick-{pull_request.head.ref.replace(' ', '-')}"
                )
                source_branch = user_request[1].split()[1]
                self._clone_repository()
                self._checkout_new_branch(
                    source_branch=source_branch, new_branch_name=new_branch_name
                )
                self._cherry_pick(
                    source_branch=source_branch,
                    new_branch_name=new_branch_name,
                    commit_hash=pull_request.merge_commit_sha,
                    commit_msg=pull_request.title,
                    pull_request_url=pull_request.html_url,
                    user_login=user_login,
                )
                shutil.rmtree(self.clone_repository_path)
            else:
                app.logger.info(
                    f"{self.repository_name}: Processing label by user comment"
                )
                self.label_by_user_comment(issue=issue, user_request=user_request)

    def process_pull_request_webhook_data(self):
        pull_request = self.repository.get_pull(self.hook_data["number"])
        hook_action = self.hook_data["action"]
        app.logger.info(f"hook_action is: {hook_action}")

        if hook_action == "opened":
            self.add_size_label(pull_request=pull_request)
            app.logger.info(f"{self.repository_name}: Adding PR owner as assignee")
            pull_request.add_to_assignees(
                self.hook_data["pull_request"]["user"]["login"]
            )
            for reviewer in self.reviewers:
                if reviewer != pull_request.user.login:
                    app.logger.info(
                        f"{self.repository_name}: Adding reviewer {reviewer}"
                    )
                    pull_request.create_review_request([reviewer])

            self.create_issue_for_new_pr(pull_request=pull_request)
            app.logger.info(f"{self.repository_name}: Creating welcome comment")
            welcome_msg = """
The following are automatically added:
 * Add reviewers from OWNER file (in the root of the repository) under reviewers section.
 * Set PR size label.
 * New issue is created for the PR.

Available user actions:
 * To mark PR as verified add `!verified` to a PR comment, to un-verify add `!-verified` to a PR comment.
        Verified label removed on each new commit push.
 * To cherry pick a merged PR add `!cherry-pick <target branch to cherry-pick to>` to a PR comment.
            """
            commit = self._get_last_commit(pull_request)
            commit.create_comment(welcome_msg)

        if hook_action == "closed" or hook_action == "merged":
            self.close_issue_for_merged_or_closed_pr(
                pull_request=pull_request, hook_action=hook_action
            )

        if hook_action == "synchronize":
            current_size_label = [
                label
                for label in self.obj_labels(obj=pull_request)
                if label.startswith(self.size_label_prefix)
            ]
            self.add_size_label(
                pull_request=pull_request,
                current_size_label=current_size_label[0]
                if current_size_label
                else None,
            )

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
                app.logger.info(
                    f"{self.repository_name}: Processing push for tag: {tag_name}"
                )
                self._clone_repository()
                self._checkout_tag(tag=tag_name)
                self.upload_to_pypi()
                shutil.rmtree(self.clone_repository_path)


@app.route("/github_webhook", methods=["POST"])
def process_webhook():
    app.logger.info("Processing webhook")
    gha = GutHubApi(hook_data=request.json)
    event_type = request.headers.get("X-GitHub-Event")
    app.logger.info(f"{gha.repository_full_name} Event type: {event_type}")
    if event_type == "issue_comment":
        gha.process_comment_webhook_data()

    if event_type == "pull_request":
        gha.process_pull_request_webhook_data()

    if event_type == "push":
        gha.process_push_webhook_data()

    return "Process done"
