import re

import requests
import yaml
from flask import Flask, request
from github import Github

app = Flask("github_webhook_server")
app.logger.info("Starting github-webhook-server app")


class GutHubApi:
    def __init__(self, hook_data):
        self.hook_data = hook_data
        self.repository_name = hook_data["repository"]["name"]
        self._repo_data_from_config()
        self.api = Github(login_or_token=self.token)
        self.repository = self.api.get_repo(self.repository_full_name)
        self.verified_label = "verified"

    def _repo_data_from_config(self):
        with open("/config.yaml") as fd:
            repos = yaml.safe_load(fd)

        for repo, data in repos["repositories"].items():
            if repo == self.repository_name:
                self.token = data["token"]
                self.repository_full_name = data["name"]

    @staticmethod
    def _get_labels_dict(labels):
        _labels = {}
        for label in labels:
            _labels[label.name.lower()] = label
        return _labels

    @staticmethod
    def _get_last_commit(pull_request):
        return list(pull_request.get_commits())[-1]

    @staticmethod
    def _remove_label(obj, label):
        app.logger.info(f"Removing label {label}")
        return obj.remove_from_labels(label)

    @staticmethod
    def _add_label(obj, label):
        app.logger.info(f"Adding label {label}")
        return obj.add_to_labels(label)

    @staticmethod
    def _generate_issue_title(pull_request):
        return f"{pull_request.title} - {pull_request.number}"

    @staticmethod
    def _generate_issue_body(pull_request):
        return f"[Auto generated]\nNumber: [#{pull_request.number}]"

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
        return yaml.safe_load(content)["reviewers"]

    def add_size_label(self, pull_request):
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

        label = f"size/{_label}"
        self._add_label(obj=pull_request, label=label)

    def label_by_user_comment(self, issue, body):
        user_requested_labels = re.findall(r"!(-)?(\w+)", body)
        if user_requested_labels:
            for user_requested_label in user_requested_labels:
                _label = user_requested_label[1]
                app.logger.info(f"Label requested by user: {_label}")
                if (
                    user_requested_label[0] == "-"
                    or self.hook_data["action"] == "deleted"
                ):
                    label = self.obj_labels(obj=issue).get(_label.lower())
                    if label:
                        self._remove_label(obj=issue, label=label.name)

                else:
                    label = self.repository_labels.get(_label.lower())
                    if label:
                        self._add_label(obj=issue, label=label.name)

    def reset_labels(self, pull_request):
        if self.obj_labels(obj=pull_request).get(self.verified_label.lower()):
            return self._remove_label(obj=pull_request, label=self.verified_label)

    def set_verify_check_pending(self, pull_request):
        last_commit = self._get_last_commit(pull_request)
        last_commit.create_status(
            state="pending",
            description="Waiting for verification (!verified)",
            context="Verified label",
        )

    def set_verify_check_success(self, pull_request):
        last_commit = self._get_last_commit(pull_request)
        last_commit.create_status(
            state="success",
            description="Waiting for verification (!verified)",
            context="Verified label",
        )

    def create_issue_for_new_pr(self, pull_request):
        app.logger.info(f"Creating issue for new PR: {pull_request.title}")
        self.repository.create_issue(
            title=self._generate_issue_title(pull_request),
            body=self._generate_issue_body(pull_request=pull_request),
            assignee=pull_request.user,
        )

    def close_issue_for_merged_or_closed_pr(self, pull_request, hook_action):
        for issue in self.repository.get_issues():
            if issue.body == self._generate_issue_body(pull_request=pull_request):
                app.logger.info(
                    f"Closing issue {issue.title} for PR: {pull_request.title}"
                )
                issue.create_comment(
                    f"Closing issue for PR: {pull_request.title}.\nPR was {hook_action}."
                )
                issue.edit(state="closed")
                break

    def process_comment_webhook_data(self):
        issue = self.repository.get_issue(self.hook_data["issue"]["number"])
        app.logger.info("Processing label by user comment")
        self.label_by_user_comment(issue=issue, body=self.hook_data["comment"]["body"])

    def process_pull_request_webhook_data(self):
        pull_request = self.repository.get_pull(self.hook_data["number"])
        hook_action = self.hook_data["action"]

        app.logger.info("Adding size label")
        self.add_size_label(pull_request=pull_request)

        if hook_action == "opened":
            app.logger.info("Adding PR owner as assignee")
            pull_request.add_to_assignees(
                self.hook_data["pull_request"]["user"]["login"]
            )
            for reviewer in self.reviewers:
                if reviewer != pull_request.user.login:
                    app.logger.info(f"Adding reviewer {reviewer}")
                    pull_request.create_review_request([reviewer])

            self.create_issue_for_new_pr(pull_request=pull_request)

        if hook_action == "closed" or hook_action == "merged":
            self.close_issue_for_merged_or_closed_pr(
                pull_request=pull_request, hook_action=hook_action
            )

        if hook_action == "synchronize":
            app.logger.info("Processing reset labels on new commits")
            self.reset_labels(pull_request=pull_request)
            app.logger.info("Processing set verified check pending")
            self.set_verify_check_pending(pull_request=pull_request)

        if (
            hook_action == "labeled"
            and self.hook_data["label"]["name"].lower() == self.verified_label
        ):
            app.logger.info("Set verified check to success")
            self.set_verify_check_success(pull_request=pull_request)

        if (
            hook_action == "unlabeled"
            and self.hook_data["label"]["name"].lower() == self.verified_label
        ):
            app.logger.info("Set verified check to pending")
            self.set_verify_check_pending(pull_request=pull_request)


@app.route("/github_webhook", methods=["POST"])
def process_webhook():
    app.logger.info("Processing webhook")
    gha = GutHubApi(hook_data=request.json)
    if request.headers.get("X-GitHub-Event") == "issue_comment":
        gha.process_comment_webhook_data()

    if request.headers.get("X-GitHub-Event") == "pull_request":
        gha.process_pull_request_webhook_data()

    return "Process done"
