import hashlib
import hmac
import ipaddress
import os
import sys
from functools import lru_cache
from typing import Any

import requests
import urllib3
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    status,
)
from httpx import AsyncClient
from starlette.datastructures import Headers

from webhook_server.libs.exceptions import NoPullRequestError, RepositoryNotFoundError
from webhook_server.libs.github_api import ProcessGithubWehook
from webhook_server.utils.github_repository_and_webhook_settings import repository_and_webhook_settings
from webhook_server.utils.helpers import get_logger_with_params

VERIFY_GITHUB_IPS = os.getenv("GITHUB_IPS_ONLY", "").lower() in ["true", "1"]
VERIFY_CLOUDFLARE_IPS = os.getenv("CLOUDFLARE_IPS_ONLY", "").lower() in ["true", "1"]
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
FASTAPI_APP: FastAPI = FastAPI(title="webhook-server")
APP_URL_ROOT_PATH: str = "/webhook_server"
urllib3.disable_warnings()


def verify_signature(payload_body: bytes, secret_token: str, signature_header: Headers | None = None) -> None:
    """Verify that the payload was sent from GitHub by validating SHA256.

    Raise and return 403 if not authorized.

    Args:
        payload_body: original request body to verify (request.body())
        secret_token: GitHub app webhook token (WEBHOOK_SECRET)
        signature_header: header received from GitHub (x-hub-signature-256)
    """
    if not signature_header:
        raise HTTPException(status_code=403, detail="x-hub-signature-256 header is missing!")

    hash_object = hmac.new(secret_token.encode("utf-8"), msg=payload_body, digestmod=hashlib.sha256)
    expected_signature = "sha256=" + hash_object.hexdigest()

    if not hmac.compare_digest(expected_signature, signature_header):
        raise HTTPException(status_code=403, detail="Request signatures didn't match!")


@lru_cache(maxsize=1)
async def get_github_allowlist() -> list[str]:
    """Fetch and cache GitHub IP allowlist"""
    async with AsyncClient(timeout=10.0) as client:
        response = await client.get("https://api.github.com/meta")
        return response.json()["hooks"]


@lru_cache(maxsize=1)
async def get_cloudflare_allowlist() -> list[str]:
    """Fetch and cache Cloudflare IP allowlist"""
    async with AsyncClient(timeout=10.0) as client:
        response = await client.get("https://api.cloudflare.com/client/v4/ips")
        return response.json()["result"]["ipv4_cidrs"]


async def gate_by_allowlist_ips(request: Request) -> None:
    if VERIFY_GITHUB_IPS or VERIFY_CLOUDFLARE_IPS:
        allowlist = []

        try:
            src_ip = ipaddress.ip_address(request.client.host)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not hook sender ip address")

        if VERIFY_GITHUB_IPS:
            allowlist = await get_github_allowlist()

        elif VERIFY_CLOUDFLARE_IPS:
            allowlist = await get_cloudflare_allowlist()

        if not allowlist:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Failed to get allowlist ips")

        for valid_ip in allowlist:
            if src_ip in ipaddress.ip_network(valid_ip):
                return
        else:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a GitHub hooks ip address")


def on_starting(server: Any) -> None:
    logger = get_logger_with_params(name="startup")
    logger.info("Application starting up...")
    try:
        repository_and_webhook_settings(webhook_secret=WEBHOOK_SECRET)
        logger.info("Repository and webhook settings initialized successfully.")

    except Exception as ex:
        logger.exception(f"FATAL: Error during startup initialization: {ex}")
        raise


@FASTAPI_APP.get(f"{APP_URL_ROOT_PATH}/healthcheck")
def healthcheck() -> dict[str, Any]:
    return {"status": requests.codes.ok, "message": "Alive"}


@FASTAPI_APP.post(APP_URL_ROOT_PATH, dependencies=[Depends(gate_by_allowlist_ips)])
async def process_webhook(request: Request) -> dict[str, Any]:
    logger_name: str = "main"
    logger = get_logger_with_params(name=logger_name)

    payload_body = await request.body()

    if WEBHOOK_SECRET:
        signature_header = request.headers.get("x-hub-signature-256")
        verify_signature(payload_body=payload_body, secret_token=WEBHOOK_SECRET, signature_header=signature_header)

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
        raise HTTPException(status_code=404, detail=str(e))

    except ConnectionError as e:
        logger.error(f"{log_context} API connection error: {e}")
        raise HTTPException(status_code=503, detail=f"API Connection Error: {e}")

    except NoPullRequestError as e:
        logger.debug(f"{log_context} Processing skipped: {e}")
        return {"status": "OK", "message": f"Processing skipped: {e}"}

    except HTTPException:
        raise

    except Exception as e:
        logger.exception(f"{log_context} Unexpected error during processing: {e}")
        exc_type, exc_obj, exc_tb = sys.exc_info()
        line_no = exc_tb.tb_lineno if exc_tb else "unknown"
        file_name = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1] if exc_tb else "unknown"
        error_details = f"Error type: {exc_type.__name__ if exc_type else ''}, File: {file_name}, Line: {line_no}"
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {error_details}")
