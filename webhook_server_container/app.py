import os

import urllib3
from flask import request
from github import Auth, GithubIntegration

from webhook_server_container.libs.config import Config
from webhook_server_container.libs.github_api import GitHubApi
from webhook_server_container.utils.constants import (
    APP_ROOT_PATH,
    FLASK_APP,
)
from webhook_server_container.utils.github_repository_settings import (
    set_all_in_progress_check_runs_to_queued,
    set_repositories_settings,
)
from webhook_server_container.utils.helpers import (
    get_api_with_highest_rate_limit,
    ignore_exceptions,
)
from webhook_server_container.utils.webhook import create_webhook

REPOSITORIES_APP_API = {}
MISSING_APP_REPOSITORIES = []

urllib3.disable_warnings()


@ignore_exceptions(logger=FLASK_APP.logger)
def get_repositories_github_app_api(config):
    FLASK_APP.logger.info("Getting repositories GitHub app API")
    with open(os.path.join(config.data_dir, "webhook-server.private-key.pem")) as fd:
        private_key = fd.read()

    github_app_id = config.data["github-app-id"]
    auth = Auth.AppAuth(app_id=github_app_id, private_key=private_key)
    for installation in GithubIntegration(auth=auth).get_installations():
        for repo in installation.get_repos():
            FLASK_APP.logger.info(f"Getting repository {repo.full_name} GitHub app API")
            REPOSITORIES_APP_API[repo.full_name] = installation.get_github_for_installation()

    for data in config.data["repositories"].values():
        full_name = data["name"]
        if not REPOSITORIES_APP_API.get(full_name):
            FLASK_APP.logger.error(
                f"Repository {full_name} not found by manage-repositories-app, "
                f"make sure the app installed (https://github.com/apps/manage-repositories-app)"
            )
            MISSING_APP_REPOSITORIES.append(full_name)


@FLASK_APP.route(f"{APP_ROOT_PATH}/healthcheck")
def healthcheck():
    return "alive"


@FLASK_APP.route(APP_ROOT_PATH, methods=["POST"])
def process_webhook():
    process_failed_msg = "Process failed"
    try:
        hook_data = request.json
    except Exception as ex:
        FLASK_APP.logger.error(f"Error get JSON from request: {ex}")
        return process_failed_msg

    try:
        api = GitHubApi(
            hook_data=hook_data,
            repositories_app_api=REPOSITORIES_APP_API,
            missing_app_repositories=MISSING_APP_REPOSITORIES,
        )
    except Exception as ex:
        FLASK_APP.logger.error(f"Failed to initialized GitHubApi instance: {ex}")
        return process_failed_msg

    github_event = request.headers.get("X-GitHub-Event")
    event_log = f"Event type: {github_event}. event ID: {request.headers.get('X-GitHub-Delivery')}"
    try:
        api.process_hook(data=github_event, event_log=event_log)
        return "process success"

    except Exception as ex:
        FLASK_APP.logger.error(f"Failed to process hook: {ex}")
        return process_failed_msg


def main():
    config = Config()
    api, _ = get_api_with_highest_rate_limit(config=config)
    get_repositories_github_app_api(config=config)
    set_repositories_settings(config=config, github_api=api)
    set_all_in_progress_check_runs_to_queued(
        config=config,
        repositories_app_api=REPOSITORIES_APP_API,
        missing_app_repositories=MISSING_APP_REPOSITORIES,
        github_api=api,
    )
    create_webhook(config=config, github_api=api)
    FLASK_APP.logger.info(f"Starting {FLASK_APP.name} app")
    FLASK_APP.run(
        port=int(os.environ.get("WEBHOOK_SERVER_PORT", 5000)),
        host="0.0.0.0",
        use_reloader=True if os.environ.get("WEBHOOK_SERVER_USE_RELOAD") else False,
    )


if __name__ == "__main__":
    main()
