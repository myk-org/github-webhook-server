import urllib3
from flask import Flask, request
from github_api import GitHubApi
from gitlab_api import GitLabApi

urllib3.disable_warnings()

app = Flask("webhook_server")
app.logger.info("Starting webhook-server app")


class GithubGitlabApiNotFoundError(Exception):
    pass


def get_api(github_event, gitlab_event, hook_data):
    if github_event:
        return GitHubApi(app=app, hook_data=hook_data)

    elif gitlab_event:
        return GitLabApi(app=app, hook_data=hook_data)

    else:
        raise GithubGitlabApiNotFoundError(hook_data)


def precess_github_hook(api, event_type):
    if event_type == "issue_comment":
        api.process_comment_webhook_data()

    if event_type == "pull_request":
        api.process_pull_request_webhook_data()

    if event_type == "push":
        api.process_push_webhook_data()

    if event_type == "pull_request_review":
        api.process_pull_request_review_webhook_data()


def process_gitlab_hook(api, hook_data):
    event_type = hook_data["event_type"]
    if event_type == "merge_request":
        action = hook_data["object_attributes"]["action"]
        if action == "open":
            api.process_new_merge_request_webhook_data()
        if action == "update":
            api.process_updated_merge_request_webhook_data()
        if action == "approved":
            api.process_approved_merge_request_webhook_data()
        if action == "unapproved":
            api.process_unapproved_merge_request_webhook_data()

    if event_type == "note":
        api.process_comment_webhook_data()


@app.route("/webhook_server", methods=["POST"])
def process_webhook():
    hook_data = request.json
    github_event = request.headers.get("X-GitHub-Event")
    gitlab_event = request.headers.get("X-GitLab-Event")
    api = get_api(
        github_event=github_event, gitlab_event=gitlab_event, hook_data=hook_data
    )

    app.logger.info(
        f"{api.repository_full_name} Event type: {github_event or gitlab_event}"
    )
    if isinstance(api, GitHubApi):
        precess_github_hook(api=api, event_type=github_event)

    elif isinstance(api, GitLabApi):
        process_gitlab_hook(api=api, hook_data=hook_data)

    return "Process done"
