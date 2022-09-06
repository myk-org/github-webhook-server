from flask import Flask, request
from github_api import GitHubApi

app = Flask("webhook_server")
app.logger.info("Starting webhook-server app")


@app.route("/webhook_server", methods=["POST"])
def process_webhook():
    app.logger.info("Processing webhook")
    gha = GitHubApi(app=app, hook_data=request.json)
    event_type = request.headers.get("X-GitHub-Event")
    app.logger.info(f"{gha.repository_full_name} Event type: {event_type}")
    if event_type == "issue_comment":
        gha.process_comment_webhook_data()

    if event_type == "pull_request":
        gha.process_pull_request_webhook_data()

    if event_type == "push":
        gha.process_push_webhook_data()

    if event_type == "pull_request_review":
        gha.process_pull_request_review_webhook_data()

    return "Process done"
