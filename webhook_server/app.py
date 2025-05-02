import os
import sys
from typing import Any

import requests
import urllib3
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException

from webhook_server.libs.exceptions import NoPullRequestError, RepositoryNotFoundError
from webhook_server.libs.github_api import ProcessGithubWehook
from webhook_server.utils.github_repository_and_webhook_settings import repository_and_webhook_settings
from webhook_server.utils.helpers import get_logger_with_params

FASTAPI_APP: FastAPI = FastAPI(title="webhook-server")
APP_URL_ROOT_PATH: str = "/webhook_server"
urllib3.disable_warnings()


def on_starting(server: Any) -> None:
    logger = get_logger_with_params(name="startup")
    logger.info("Application starting up...")
    try:
        repository_and_webhook_settings()
        logger.info("Repository and webhook settings initialized successfully.")
    except Exception as ex:
        logger.exception(f"FATAL: Error during startup initialization: {ex}")


@FASTAPI_APP.get(f"{APP_URL_ROOT_PATH}/healthcheck")
def healthcheck() -> dict[str, Any]:
    return {"status": requests.codes.ok, "message": "Alive"}


@FASTAPI_APP.post(APP_URL_ROOT_PATH)
async def process_webhook(request: Request) -> dict[str, Any]:
    logger_name: str = "main"
    logger = get_logger_with_params(name=logger_name)
    delivery_id = request.headers.get("X-GitHub-Delivery", "unknown-delivery")
    event_type = request.headers.get("X-GitHub-Event", "unknown-event")
    delivery_headers = request.headers.get("X-GitHub-Delivery", "")
    log_context = f"[Event: {event_type}][Delivery: {delivery_id}]"

    try:
        hook_data: dict[Any, Any] = await request.json()
    except Exception as e:
        logger.error(f"{log_context} Error parsing JSON body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    logger = get_logger_with_params(name=logger_name, repository_name=hook_data["repository"]["name"])

    try:
        api: ProcessGithubWehook = ProcessGithubWehook(hook_data=hook_data, headers=request.headers, logger=logger)
        api.process()
        return {"status": requests.codes.ok, "message": "process success", "log_prefix": delivery_headers}

    except RepositoryNotFoundError as e:
        logger.error(f"{log_context} Configuration/Repository error: {e}")
        raise HTTPException(status_code=404, detail=str(e))  # Not Found might be appropriate

    except ConnectionError as e:
        logger.error(f"{log_context} API connection error: {e}")
        raise HTTPException(status_code=503, detail=f"API Connection Error: {e}")  # Service Unavailable

    except NoPullRequestError as e:
        logger.debug(f"{log_context} Processing skipped: {e}")
        return {"status": "OK", "message": f"Processing skipped: {e}"}  # Still a successful request handling

    except HTTPException:
        raise

    except Exception as e:
        logger.exception(f"{log_context} Unexpected error during processing: {e}")
        exc_type, exc_obj, exc_tb = sys.exc_info()
        line_no = exc_tb.tb_lineno if exc_tb else "unknown"
        file_name = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1] if exc_tb else "unknown"
        error_details = f"Error type: {exc_type.__name__ if exc_type else ''}, File: {file_name}, Line: {line_no}"
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {error_details}")
