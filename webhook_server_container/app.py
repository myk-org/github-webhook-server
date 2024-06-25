import requests
import urllib3
from flask import request
from simple_logger.logger import get_logger

from webhook_server_container.libs.github_api import GitHubApi
from webhook_server_container.utils.constants import (
    APP_ROOT_PATH,
    FastAPI_APP,
)

REPOSITORIES_APP_API = {}
MISSING_APP_REPOSITORIES = []
urllib3.disable_warnings()

LOGGER = get_logger(name="app")


@FastAPI_APP.get(f"{APP_ROOT_PATH}/healthcheck")
def healthcheck():
    return {"status": requests.status_codes.codes.ok, "message": "Alive"}


@FastAPI_APP.post(APP_ROOT_PATH)
async def process_webhook():
    process_failed_msg = {"status": requests.status_codes.codes.server_error, "Message": "Process failed"}
    try:
        hook_data = request.json
    except Exception as ex:
        LOGGER.error(f"Error get JSON from request: {ex}")
        return process_failed_msg

    try:
        api = GitHubApi(hook_data=hook_data)
    except Exception as ex:
        LOGGER.error(f"Failed to initialized GitHubApi instance: {ex}")
        return process_failed_msg

    github_event = request.headers.get("X-GitHub-Event")
    event_log = f"Event type: {github_event}. event ID: {request.headers.get('X-GitHub-Delivery')}"
    try:
        api.process_hook(data=github_event, event_log=event_log)
        return {"status": requests.status_codes.codes.ok, "Message": "process success"}

    except Exception as ex:
        LOGGER.error(f"Failed to process hook: {ex}")
        return process_failed_msg
