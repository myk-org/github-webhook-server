import hashlib
import hmac
import ipaddress
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import httpx
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
from webhook_server.libs.exceptions import RepositoryNotFoundError
from webhook_server.libs.github_api import GithubWebhook
from webhook_server.utils.helpers import get_logger_with_params

ALLOWED_IPS: tuple[ipaddress._BaseNetwork, ...] = ()
LOGGER = get_logger_with_params(name="main")


APP_URL_ROOT_PATH: str = "/webhook_server"
urllib3.disable_warnings()

_lifespan_http_client: httpx.AsyncClient | None = None


async def get_github_allowlist() -> list[str]:
    """Fetch and cache GitHub IP allowlist asynchronously."""
    try:
        assert _lifespan_http_client is not None
        response = await _lifespan_http_client.get("https://api.github.com/meta")
        response.raise_for_status()  # Check for HTTP errors
        data = response.json()
        return data.get("hooks", [])

    except httpx.RequestError as e:
        LOGGER.error(f"Error fetching GitHub allowlist: {e}")
        raise

    except Exception as e:
        LOGGER.error(f"Unexpected error fetching GitHub allowlist: {e}")
        raise


async def get_cloudflare_allowlist() -> list[str]:
    """Fetch and cache Cloudflare IP allowlist asynchronously."""
    try:
        assert _lifespan_http_client is not None
        response = await _lifespan_http_client.get("https://api.cloudflare.com/client/v4/ips")
        response.raise_for_status()
        result = response.json().get("result", {})
        return result.get("ipv4_cidrs", []) + result.get("ipv6_cidrs", [])

    except httpx.RequestError as e:
        LOGGER.error(f"Error fetching Cloudflare allowlist: {e}")
        raise

    except Exception as e:
        LOGGER.error(f"Unexpected error fetching Cloudflare allowlist: {e}")
        raise


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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _lifespan_http_client
    _lifespan_http_client = httpx.AsyncClient(timeout=10.0)

    try:
        LOGGER.info("Application starting up...")
        config = Config(logger=LOGGER)
        root_config = config.root_data
        verify_github_ips = root_config.get("verify-github-ips")
        verify_cloudflare_ips = root_config.get("verify-cloudflare-ips")
        LOGGER.debug(f"verify_github_ips: {verify_github_ips}, verify_cloudflare_ips: {verify_cloudflare_ips}")

        global ALLOWED_IPS
        networks: set[ipaddress._BaseNetwork] = set()

        if verify_cloudflare_ips:
            cf_ips = await get_cloudflare_allowlist()

            for cidr in cf_ips:
                try:
                    networks.add(ipaddress.ip_network(cidr))
                except ValueError:
                    LOGGER.warning(f"Skipping invalid CIDR from Cloudflare: {cidr}")

        if verify_github_ips:
            gh_ips = await get_github_allowlist()

            for cidr in gh_ips:
                try:
                    networks.add(ipaddress.ip_network(cidr))
                except ValueError:
                    LOGGER.warning(f"Skipping invalid CIDR from Github: {cidr}")

        if networks:
            ALLOWED_IPS = tuple(networks)
            LOGGER.info(f"IP allowlist initialized successfully with {len(ALLOWED_IPS)} networks.")

        yield

    except Exception as ex:
        LOGGER.error(f"Application failed during lifespan management: {ex}")
        raise

    finally:
        if _lifespan_http_client:
            await _lifespan_http_client.aclose()

        LOGGER.info("Application shutdown complete.")


FASTAPI_APP: FastAPI = FastAPI(title="webhook-server", lifespan=lifespan)


@FASTAPI_APP.get(f"{APP_URL_ROOT_PATH}/healthcheck")
def healthcheck() -> dict[str, Any]:
    return {"status": requests.codes.ok, "message": "Alive"}


@FASTAPI_APP.post(APP_URL_ROOT_PATH, dependencies=[Depends(gate_by_allowlist_ips)])
async def process_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    payload_body = await request.body()

    config = Config(logger=LOGGER)
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
        hook_data: dict[Any, Any] = json.loads(payload_body)

    except Exception as e:
        LOGGER.error(f"{log_context} Error parsing JSON body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    logger = get_logger_with_params(name="main", repository_name=hook_data["repository"]["name"])

    async def process_with_error_handling(_api: GithubWebhook, _logger: logging.Logger) -> None:
        try:
            await _api.process()

        except Exception as e:
            _logger.exception(f"{log_context} Error in background task: {e}")

    try:
        api: GithubWebhook = GithubWebhook(hook_data=hook_data, headers=request.headers, logger=logger)

        background_tasks.add_task(process_with_error_handling, _api=api, _logger=logger)
        return {"status": requests.codes.ok, "message": "ok", "delivery headers": delivery_headers}

    except RepositoryNotFoundError as e:
        logger.exception(f"{log_context} Configuration/Repository error: {e}")
        raise HTTPException(status_code=404, detail=str(e))

    except ConnectionError as e:
        logger.exception(f"{log_context} API connection error: {e}")
        raise HTTPException(status_code=503, detail=f"API Connection Error: {e}")

    except HTTPException:
        raise

    except Exception as e:
        logger.exception(f"{log_context} Unexpected error during processing: {e}")
        exc_type, _, exc_tb = sys.exc_info()
        line_no = exc_tb.tb_lineno if exc_tb else "unknown"
        file_name = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1] if exc_tb else "unknown"
        error_details = f"Error type: {exc_type.__name__ if exc_type else ''}, File: {file_name}, Line: {line_no}"
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {error_details}")
