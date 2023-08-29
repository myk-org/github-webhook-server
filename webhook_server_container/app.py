import os

import urllib3
from flask import Response, request
from github import Auth, GithubIntegration

from webhook_server_container.libs.github_api import GitHubApi
from webhook_server_container.utils.constants import (
    APP_ROOT_PATH,
    BUILD_CONTAINER_STR,
    FLASK_APP,
    PYTHON_MODULE_INSTALL_STR,
    TOX_STR,
)
from webhook_server_container.utils.github_repository_settings import (
    set_all_in_progress_check_runs_to_queued,
    set_repositories_settings,
)
from webhook_server_container.utils.helpers import (
    check_rate_limit,
    get_app_data_dir,
    get_data_from_config,
)
from webhook_server_container.utils.sonar_qube import set_sonar_qube_projects
from webhook_server_container.utils.webhook import create_webhook


REPOSITORIES_APP_API = {}
MISSING_APP_REPOSITORIES = []

urllib3.disable_warnings()

PLAIN_TEXT_MIME_TYPE = "text/plain"
FILENAME_STRING = "<string:filename>"
APP_DATA_ROOT_PATH = get_app_data_dir()
TOX_ROUTE_PATH = f"{APP_DATA_ROOT_PATH}/{TOX_STR}"
BUILD_CONTAINER_ROUTE_PATH = f"{APP_DATA_ROOT_PATH}/{BUILD_CONTAINER_STR}"
PYTHON_MODULE_INSTALL_ROUTE_PATH = f"{APP_DATA_ROOT_PATH}/{PYTHON_MODULE_INSTALL_STR}"
TOX_DATA_PATH = os.path.join(APP_DATA_ROOT_PATH, TOX_STR)
BUILD_CONTAINER_DATA_PATH = os.path.join(APP_DATA_ROOT_PATH, BUILD_CONTAINER_STR)
PYTHON_MODULE_INSTALL_DATA_PATH = os.path.join(
    APP_DATA_ROOT_PATH, PYTHON_MODULE_INSTALL_STR
)


def get_repositories_github_app_api():
    FLASK_APP.logger.info("Getting repositories GitHub app API")
    with open(os.path.join(get_app_data_dir(), "webhook-server.private-key.pem")) as fd:
        private_key = fd.read()

    config_data = get_data_from_config()
    github_app_id = config_data["github-app-id"]
    auth = Auth.AppAuth(app_id=github_app_id, private_key=private_key)
    for installation in GithubIntegration(auth=auth).get_installations():
        for repo in installation.get_repos():
            REPOSITORIES_APP_API[
                repo.full_name
            ] = installation.get_github_for_installation()

    for data in config_data["repositories"].values():
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
    event_log = (
        f"Event type: {github_event} "
        f"event ID: {request.headers.get('X-GitHub-Delivery')}"
    )
    try:
        api.process_hook(data=github_event, event_log=event_log)
        return "process success"
    except Exception as ex:
        FLASK_APP.logger.error(f"Failed to process hook: {ex}")
        return process_failed_msg


@FLASK_APP.route(f"{APP_ROOT_PATH}/{TOX_STR}/{FILENAME_STRING}")
def return_tox(filename):
    FLASK_APP.logger.info(f"app.route: Processing {TOX_STR} file")
    with open(os.path.join(TOX_DATA_PATH, filename)) as fd:
        return Response(fd.read(), mimetype=PLAIN_TEXT_MIME_TYPE)


@FLASK_APP.route(f"{APP_ROOT_PATH}/{BUILD_CONTAINER_STR}/{FILENAME_STRING}")
def return_build_container(filename):
    FLASK_APP.logger.info(f"app.route: Processing {BUILD_CONTAINER_STR} file")
    with open(os.path.join(BUILD_CONTAINER_DATA_PATH, filename)) as fd:
        return Response(fd.read(), mimetype=PLAIN_TEXT_MIME_TYPE)


@FLASK_APP.route(f"{APP_ROOT_PATH}/{PYTHON_MODULE_INSTALL_STR}/{FILENAME_STRING}")
def return_python_module_install(filename):
    FLASK_APP.logger.info(f"app.route: Processing {PYTHON_MODULE_INSTALL_STR} file")
    with open(os.path.join(PYTHON_MODULE_INSTALL_DATA_PATH, filename)) as fd:
        return Response(fd.read(), mimetype=PLAIN_TEXT_MIME_TYPE)


def main():
    check_rate_limit()
    get_repositories_github_app_api()
    set_repositories_settings()
    set_sonar_qube_projects()
    set_all_in_progress_check_runs_to_queued(
        repositories_app_api=REPOSITORIES_APP_API,
        missing_app_repositories=MISSING_APP_REPOSITORIES,
    )

    for proc in create_webhook():
        proc.join()

    FLASK_APP.logger.info(f"Starting {FLASK_APP.name} app")
    FLASK_APP.run(
        port=int(os.environ.get("WEBHOOK_SERVER_PORT", 5000)),
        host="0.0.0.0",
        use_reloader=True if os.environ.get("WEBHOOK_SERVER_USE_RELOAD") else False,
    )


if __name__ == "__main__":
    main()
