import os

import urllib3
from flask import Flask, request
from flask_script import Manager, Server
from github_api import GitHubApi
from gitlab_api import GitLabApi
from webhook import create_webhook


urllib3.disable_warnings()

os.environ["FLASK_DEBUG"] = "1"
app = Flask("webhook_server")


class GithubGitlabApiNotFoundError(Exception):
    pass


def get_api(github_event, gitlab_event, hook_data):
    if github_event:
        return GitHubApi(app=app, hook_data=hook_data)

    elif gitlab_event:
        return GitLabApi(app=app, hook_data=hook_data)

    else:
        raise GithubGitlabApiNotFoundError(hook_data)


@app.route("/webhook_server", methods=["POST"])
def process_webhook():
    hook_data = request.json
    github_event = request.headers.get("X-GitHub-Event")
    gitlab_event = request.headers.get("X-GitLab-Event")
    api = get_api(
        github_event=github_event, gitlab_event=gitlab_event, hook_data=hook_data
    )

    app.logger.info(
        f"{api.repository_full_name} Event type: {github_event or gitlab_event} "
        f"event ID: {request.headers.get('X-GitHub-Delivery')}"
    )
    api.process_hook(data=hook_data if gitlab_event else github_event)
    return "Process done"


class CustomServer(Server):
    def __call__(self, app, *args, **kwargs):
        create_webhook(app=app)
        return Server.__call__(self, app, *args, **kwargs)


manager = Manager(app)
manager.add_command("runserver", CustomServer())


if __name__ == "__main__":
    app.logger.info("Starting webhook-server app")
    manager.run(default_command="runserver")
