import os

import urllib3
import uvicorn
from fastapi import FastAPI, Request
from fastapi.logger import logger
from fastapi.responses import PlainTextResponse
from github import Auth, GithubIntegration

from webhook_server_container.libs.github_api import GitHubApi
from webhook_server_container.utils.constants import (
    APP_ROOT_PATH,
    BUILD_CONTAINER_STR,
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


app = FastAPI(title="webhook-server")

REPOSITORIES_APP_API = {}
MISSING_APP_REPOSITORIES = []

urllib3.disable_warnings()

PLAIN_TEXT_MIME_TYPE = "text/plain"
APP_DATA_ROOT_PATH = get_app_data_dir()
TOX_ROUTE_PATH = f"{APP_DATA_ROOT_PATH}/{TOX_STR}"
BUILD_CONTAINER_ROUTE_PATH = f"{APP_DATA_ROOT_PATH}/{BUILD_CONTAINER_STR}"
PYTHON_MODULE_INSTALL_ROUTE_PATH = f"{APP_DATA_ROOT_PATH}/{PYTHON_MODULE_INSTALL_STR}"


def get_repositories_github_app_api():
    logger.info("Getting repositories GitHub app API")
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
            logger.error(
                f"Repository {full_name} not found by manage-repositories-app, "
                f"make sure the app installed (https://github.com/apps/manage-repositories-app)"
            )
            MISSING_APP_REPOSITORIES.append(full_name)


@app.get(f"{APP_ROOT_PATH}/healthcheck", response_class=PlainTextResponse)
def healthcheck():
    return "alive"


@app.post(APP_ROOT_PATH)
async def process_webhook(request: Request):
    try:
        hook_data = await request.json()
        github_event = request.headers.get("X-GitHub-Event")
        api = GitHubApi(
            hook_data=hook_data,
            repositories_app_api=REPOSITORIES_APP_API,
            missing_app_repositories=MISSING_APP_REPOSITORIES,
        )

        event_log = (
            f"Event type: {github_event} "
            f"event ID: {request.headers.get('X-GitHub-Delivery')}"
        )
        api.process_hook(data=github_event, event_log=event_log)
        return {"status": "success"}
    except Exception as ex:
        logger.error(f"Error: {ex}")
        return {"status": "failed"}


@app.get("/webhook_server/tox/{filename}", response_class=PlainTextResponse)
def return_tox(filename):
    logger.info(f"app.route: Processing {TOX_STR} file")
    with open(f"{TOX_ROUTE_PATH}/{filename}") as fd:
        return fd.read()


@app.get("/webhook_server/build-container/{filename}", response_class=PlainTextResponse)
def return_build_container(filename):
    logger.info(f"app.route: Processing {BUILD_CONTAINER_STR} file")
    with open(f"{BUILD_CONTAINER_ROUTE_PATH}/{filename}") as fd:
        return fd.read()


@app.get(
    "/webhook_server/python-module-install/{filename}",
    response_class=PlainTextResponse,
)
def return_python_module_install(filename):
    logger.info(f"app.route: Processing {PYTHON_MODULE_INSTALL_STR} file")
    with open(f"{PYTHON_MODULE_INSTALL_ROUTE_PATH}/{filename}") as fd:
        return fd.read()


@app.post(f"{APP_ROOT_PATH}/run/{TOX_STR}")
def run_tox(pull_request):
    pull_request["pull_request"]._run_tox()


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

    logger.info(f"Starting {app.title} app")
    uvicorn.run(
        app,
        port=int(os.environ.get("WEBHOOK_SERVER_PORT", 5000)),
        host="0.0.0.0",
        reload=True if os.environ.get("WEBHOOK_SERVER_USE_RELOAD") else False,
    )


if __name__ == "__main__":
    main()
