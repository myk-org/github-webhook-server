import os
import re

from github import Github
from flask import Flask, request

app = Flask(__name__)


class GutHubApi:
    def __init__(self, hook_data):
        self.token = os.getenv("GITHUB_TOKEN")
        self.api = Github(login_or_token=self.token)
        self.hook_data = hook_data
        self.repository = self.api.get_repo(self.hook_data["repository"]["full_name"])
        self.repository_labels = self._repository_labels
        self.verified_label = "verified"

    def _get_issue(self):
        _issue = self.hook_data.get("number")
        if not _issue:
            _issue = self.hook_data.get("issue", {}).get("number")
        if not _issue:
            print("No issue found")
            return None

        return self.repository.get_issue(_issue)

    @property
    def _repository_labels(self):
        labels = {}
        for label in self.repository.get_labels():
            labels[label.name.lower()] = label
        return labels

    @staticmethod
    def remove_label(pr, label):
        match_label = [_label for _label in pr.labels if _label.name.lower() == label]
        if match_label:
            match_label = match_label[0]
            print(f"Removing label {match_label.name} from issue {pr.number}")
            pr.remove_from_labels(match_label)

    def label_pr_by_user_comment(self, issue, body):
        user_requested_label = re.findall(r"/\w+", body)
        if user_requested_label:
            user_requested_label = user_requested_label[0]
            if user_requested_label.startswith("/un"):
                user_requested_label = user_requested_label.replace("/un", "")
                self.remove_label(pr=issue, label=user_requested_label)
                return

            else:
                user_requested_label = user_requested_label.replace("/", "")
                if user_requested_label in self.repository_labels:
                    label_to_add = self.repository_labels[user_requested_label]
                    print(f"Labeling issue {issue.number} with {label_to_add.name}")
                    issue.add_to_labels(label_to_add)
                    return

            available_labels = "\n".join(self.repository_labels.keys())
            print(f"Label {user_requested_label} not found in repository")
            print(f"Available labels:\n {available_labels}")

    def reset_labels(self, pull_request):
        self.remove_label(pr=pull_request, label=self.verified_label)

    def process_comment_webhook_data(self):
        issue = self.repository.get_issue(self.hook_data["issue"]["number"])
        print("Processing label by user comment")
        self.label_pr_by_user_comment(
            issue=issue, body=self.hook_data["comment"]["body"]
        )

    def process_pull_request_webhook_data(self):
        pull_request = self.repository.get_issue(self.hook_data["number"])
        if self.hook_data["action"] == "opened":
            print("Processing add maintainer as reviewers")
            pull_request.add_to_assignees(
                self.hook_data["pull_request"]["user"]["login"]
            )

        if self.hook_data["action"] == "synchronize":
            print("Processing reset labels")
            self.reset_labels(pull_request=pull_request)


@app.route("/github_webhook", methods=["POST"])
def process_webhook():
    print("Processing webhook")
    gha = GutHubApi(hook_data=request.json)
    if request.headers.get("X-GitHub-Event") == "issue_comment":
        gha.process_comment_webhook_data()

    if request.headers.get("X-GitHub-Event") == "pull_request":
        gha.process_pull_request_webhook_data()

    return "Done"

