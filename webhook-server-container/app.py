from flask import Flask, request
from github_api import GitHubApi
from gitlab_api import GitLabApi

app = Flask("webhook_server")
app.logger.info("Starting webhook-server app")


class GithubGitlabApiNotFoundError(Exception):
    pass


@app.route("/webhook_server", methods=["POST"])
def process_webhook():
    app.logger.info("Processing webhook")
    hook_data = request.json
    event_type_github = request.headers.get("X-GitHub-Event")
    event_type_gitlab = request.headers.get("X-GitLab-Event")
    if event_type_github:
        api = GitHubApi(app=app, hook_data=hook_data)

    elif event_type_gitlab:
        api = GitLabApi(app=app, hook_data=hook_data)

    else:
        raise GithubGitlabApiNotFoundError(hook_data)

    app.logger.info(
        f"{api.repository_full_name} Event type: {event_type_github or event_type_gitlab}"
    )
    if event_type_github:
        if event_type_github == "issue_comment":
            api.process_comment_webhook_data()

        if event_type_github == "pull_request":
            api.process_pull_request_webhook_data()

        if event_type_github == "push":
            api.process_push_webhook_data()

        if event_type_github == "pull_request_review":
            api.process_pull_request_review_webhook_data()

    elif event_type_gitlab:
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

    return "Process done"
