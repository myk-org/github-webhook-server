import urllib3
from flask import Flask, request
from github_api import GitHubApi
from github_repository_settings import set_repositories_settings
from gitlab_api import GitLabApi
from webhook import create_webhook


urllib3.disable_warnings()

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


def main():
    procs = create_webhook(app=app) + set_repositories_settings(app=app)
    for proc in procs:
        proc.join()

    app.logger.info("Starting webhook-server app")
    app.run(port=5000, host="0.0.0.0", use_reloader=False)


if __name__ == "__main__":
    main()
