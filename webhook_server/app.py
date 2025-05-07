import hashlib
import hmac
import ipaddress
import os
import sys
from typing import Any

import requests
import urllib3
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    status,
)
from starlette.datastructures import Headers

from webhook_server.libs.config import Config
from webhook_server.libs.exceptions import NoPullRequestError, RepositoryNotFoundError
from webhook_server.libs.github_api import ProcessGithubWehook
from webhook_server.utils.github_repository_and_webhook_settings import repository_and_webhook_settings
from webhook_server.utils.helpers import get_logger_with_params

ALLOWED_IPS: tuple[ipaddress._BaseNetwork, ...] = ()
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


def get_github_allowlist() -> list[str]:
    """Fetch and cache GitHub IP allowlist"""
    response = requests.get("https://api.github.com/meta", timeout=5)
    response.raise_for_status()
    data = response.json()
    return data.get("hooks", [])


def get_cloudflare_allowlist() -> list[str]:
    """Fetch and cache Cloudflare IP allowlist"""
    response = requests.get("https://api.cloudflare.com/client/v4/ips", timeout=5)
    response.raise_for_status()
    result = response.json()["result"]
    return result.get("ipv4_cidrs", []) + result.get("ipv6_cidrs", [])


async def gate_by_allowlist_ips(request: Request) -> None:
    if ALLOWED_IPS:
        try:
            src_ip = ipaddress.ip_address(request.client.host)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not hook sender ip address")

        for valid_ip_range in ALLOWED_IPS:
            if src_ip in valid_ip_range:
                return
        else:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"{src_ip} IP is not a valid ip in allowlist IPs",
            )


def on_starting(server: Any) -> None:
    logger = get_logger_with_params(name="startup")
    logger.info("Application starting up...")
    try:
        config = Config()
        root_config = config.root_data
        webhook_secret = root_config.get("webhook-secret")
        verify_github_ips = root_config.get("verify-github-ips")
        verify_cloudflare_ips = root_config.get("verify-cloudflare-ips")

        repository_and_webhook_settings(webhook_secret=webhook_secret)
        logger.info("Repository and webhook settings initialized successfully.")

        global ALLOWED_IPS

        if verify_github_ips or verify_cloudflare_ips:
            networks: list[ipaddress._BaseNetwork] = []

            if verify_cloudflare_ips:
                networks += [ipaddress.ip_network(cidr) for cidr in get_cloudflare_allowlist()]

            if verify_github_ips:
                networks += [ipaddress.ip_network(cidr) for cidr in get_github_allowlist()]

            ALLOWED_IPS = tuple(networks)  # immutable & de-duplicated

            logger.info(f"IP allowlist initialized successfully. {ALLOWED_IPS}")

    except Exception as ex:
        logger.exception(f"FATAL: Error during startup initialization: {ex}")
        raise


@FASTAPI_APP.get(f"{APP_URL_ROOT_PATH}/healthcheck")
def healthcheck() -> dict[str, Any]:
    return {"status": requests.codes.ok, "message": "Alive"}


@FASTAPI_APP.post(APP_URL_ROOT_PATH, dependencies=[Depends(gate_by_allowlist_ips)])
async def process_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    logger_name: str = "main"
    logger = get_logger_with_params(name=logger_name)

    payload_body = await request.body()

    config = Config()
    root_config = config.root_data
    webhook_secret = root_config.get("webhook-secret")

    if webhook_secret:
        signature_header = request.headers.get("x-hub-signature-256")
        verify_signature(payload_body=payload_body, secret_token=webhook_secret, signature_header=signature_header)

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

        async def process_with_error_handling() -> None:
            try:
                await api.process()

            except NoPullRequestError:
                return

            except Exception as e:
                logger.exception(f"{log_context} Error in background task: {e}")

        background_tasks.add_task(process_with_error_handling)
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
        exc_type, _, exc_tb = sys.exc_info()
        line_no = exc_tb.tb_lineno if exc_tb else "unknown"
        file_name = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1] if exc_tb else "unknown"
        error_details = f"Error type: {exc_type.__name__ if exc_type else ''}, File: {file_name}, Line: {line_no}"
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {error_details}")
