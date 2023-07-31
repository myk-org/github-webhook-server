import os

import urllib3
from flask import Response, request

from webhook_server_container.libs.github_api import GitHubApi
from webhook_server_container.utils.constants import FLASK_APP
from webhook_server_container.utils.github_repository_settings import (
    set_repositories_settings,
)
from webhook_server_container.utils.webhook import create_webhook


urllib3.disable_warnings()

PLAIN_TEXT_MIME_TYPE = "text/plain"
APP_ROOT_PATH = "/webhook_server"
FILENAME_STRING = "<string:filename>"


@FLASK_APP.route(f"{APP_ROOT_PATH}/healthcheck")
def healthcheck():
    return "alive"


@FLASK_APP.route(APP_ROOT_PATH, methods=["POST"])
def process_webhook():
    try:
        hook_data = request.json
        github_event = request.headers.get("X-GitHub-Event")
        api = GitHubApi(hook_data=hook_data)

        FLASK_APP.logger.info(
            f"{api.repository_full_name} Event type: {github_event} "
            f"event ID: {request.headers.get('X-GitHub-Delivery')}"
        )
        api.process_hook(data=github_event)
        return "process success"
    except Exception as ex:
        FLASK_APP.logger.error(f"Error: {ex}")
        return "Process failed"


@FLASK_APP.route(f"{APP_ROOT_PATH}/tox/{FILENAME_STRING}")
def return_tox(filename):
    FLASK_APP.logger.info("app.route: Processing tox file")
    with open(f"{APP_ROOT_PATH}/tox/{filename}") as fd:
        return Response(fd.read(), mimetype=PLAIN_TEXT_MIME_TYPE)


@FLASK_APP.route(f"{APP_ROOT_PATH}/build-container/{FILENAME_STRING}")
def return_build_container(filename):
    FLASK_APP.logger.info("app.route: Processing build-container file")
    with open(f"{APP_ROOT_PATH}/build-container/{filename}") as fd:
        return Response(fd.read(), mimetype=PLAIN_TEXT_MIME_TYPE)


@FLASK_APP.route(f"{APP_ROOT_PATH}/python-module-install/{FILENAME_STRING}")
def return_python_module_install(filename):
    FLASK_APP.logger.info("app.route: Processing python-module-install file")
    with open(f"{APP_ROOT_PATH}/python-module-install/{filename}") as fd:
        return Response(fd.read(), mimetype=PLAIN_TEXT_MIME_TYPE)


def main():
    for proc in create_webhook():
        proc.join()

    set_repositories_settings()
    FLASK_APP.logger.info(f"Starting {FLASK_APP.name} app")
    FLASK_APP.run(
        port=int(os.environ.get("WEBHOOK_SERVER_PORT", 5000)),
        host="0.0.0.0",
        use_reloader=True if os.environ.get("WEBHOOK_SERVER_USE_RELOAD") else False,
    )


if __name__ == "__main__":
    main()
