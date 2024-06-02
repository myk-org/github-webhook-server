import os

import urllib3
from flask import request

from webhook_server_container.libs.github_api import GitHubApi
from webhook_server_container.utils.constants import (
    APP_ROOT_PATH,
    FLASK_APP,
)

REPOSITORIES_APP_API = {}
MISSING_APP_REPOSITORIES = []
urllib3.disable_warnings()


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
        api = GitHubApi(hook_data=hook_data)
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
    FLASK_APP.logger.info(f"Starting {FLASK_APP.name} app")
    FLASK_APP.run(
        port=5000,
        host="0.0.0.0",
        use_reloader=bool(os.getenv("WEBHOOK_SERVER_USE_RELOAD", False)),
        debug=bool(os.getenv("WEBHOOK_SERVER_USE_DEBUG", False)),
    )


if __name__ == "__main__":
    main()
