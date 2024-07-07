import os
from typing import Any, Dict

from fastapi import Request
import requests
import urllib3
from simple_logger.logger import get_logger


from webhook_server_container.libs.github_api import ProcessGithubWehook
from webhook_server_container.utils.constants import FASTAPI_APP

APP_ROOT_PATH: str = "/webhook_server"
urllib3.disable_warnings()

LOGGER = get_logger(name="app", filename=os.environ.get("WEBHOOK_SERVER_LOG_FILE"))


@FASTAPI_APP.get(f"{APP_ROOT_PATH}/healthcheck")
def healthcheck() -> Dict[str, Any]:
    return {"status": requests.status_codes.codes.ok, "message": "Alive"}


@FASTAPI_APP.post(APP_ROOT_PATH)
async def process_webhook(request: Request) -> Dict[str, Any]:
    process_failed_msg = {"status": requests.status_codes.codes.server_error, "Message": "Process failed"}
    try:
        hook_data = await request.json()
    except Exception as ex:
        LOGGER.error(f"Error get JSON from request: {ex}")
        return process_failed_msg

    try:
        ProcessGithubWehook(hook_data=hook_data, headers=request.headers)
        return {"status": requests.status_codes.codes.ok, "Message": "process success"}

    except Exception as ex:
        LOGGER.error(f"Failed to process hook: {ex}")
        return process_failed_msg
