from typing import Any, Dict

from fastapi import Request
import requests
import urllib3

from fastapi import FastAPI

from webhook_server_container.libs.github_api import ProcessGithubWehook
from webhook_server_container.utils.helpers import get_logger_with_params

FASTAPI_APP: FastAPI = FastAPI(title="webhook-server")
APP_URL_ROOT_PATH: str = "/webhook_server"
urllib3.disable_warnings()


@FASTAPI_APP.get(f"{APP_URL_ROOT_PATH}/healthcheck")
def healthcheck() -> Dict[str, Any]:
    return {"status": requests.codes.ok, "message": "Alive"}


@FASTAPI_APP.post(APP_URL_ROOT_PATH)
async def process_webhook(request: Request) -> Dict[str, Any]:
    logger_name: str = "main"
    logger = get_logger_with_params(name=logger_name)
    delivery_headers = request.headers.get("X-GitHub-Delivery", "")
    process_failed_msg: Dict[str, Any] = {
        "status": requests.codes.server_error,
        "message": "Process failed",
        "log_prefix": delivery_headers,
    }
    try:
        hook_data: Dict[Any, Any] = await request.json()

    except Exception as ex:
        logger.error(f"Error get JSON from request: {ex}")
        return process_failed_msg

    logger = get_logger_with_params(name=logger_name, repository_name=hook_data["repository"]["name"])
    api: ProcessGithubWehook = ProcessGithubWehook(hook_data=hook_data, headers=request.headers, logger=logger)
    api.process()
    return {"status": requests.codes.ok, "message": "process success", "log_prefix": delivery_headers}
