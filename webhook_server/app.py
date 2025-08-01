import hashlib
import hmac
import ipaddress
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator
import datetime

import httpx
import requests
import urllib3
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    status,
)
from fastapi.responses import HTMLResponse, StreamingResponse

from webhook_server.libs.config import Config
from webhook_server.libs.exceptions import RepositoryNotFoundError
from webhook_server.libs.github_api import GithubWebhook
from webhook_server.web.log_viewer import LogViewerController
from webhook_server.utils.helpers import get_logger_with_params

# Constants
APP_URL_ROOT_PATH: str = "/webhook_server"
HTTP_TIMEOUT_SECONDS: float = 10.0
GITHUB_META_URL: str = "https://api.github.com/meta"
CLOUDFLARE_IPS_URL: str = "https://api.cloudflare.com/client/v4/ips"

# Global variables
ALLOWED_IPS: tuple[ipaddress._BaseNetwork, ...] = ()
LOGGER = get_logger_with_params(name="main")

_lifespan_http_client: httpx.AsyncClient | None = None


async def get_github_allowlist() -> list[str]:
    """Fetch and cache GitHub IP allowlist asynchronously."""
    try:
        assert _lifespan_http_client is not None
        response = await _lifespan_http_client.get(GITHUB_META_URL)
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
        response = await _lifespan_http_client.get(CLOUDFLARE_IPS_URL)
        response.raise_for_status()
        result = response.json().get("result", {})
        return result.get("ipv4_cidrs", []) + result.get("ipv6_cidrs", [])

    except httpx.RequestError as e:
        LOGGER.error(f"Error fetching Cloudflare allowlist: {e}")
        raise

    except Exception as e:
        LOGGER.error(f"Unexpected error fetching Cloudflare allowlist: {e}")
        raise


def verify_signature(payload_body: bytes, secret_token: str, signature_header: str | None = None) -> None:
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
        if not request.client or not request.client.host:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not determine client IP address")

        try:
            src_ip = ipaddress.ip_address(request.client.host)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not parse client IP address")

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
    _lifespan_http_client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS)

    try:
        LOGGER.info("Application starting up...")
        config = Config(logger=LOGGER)
        root_config = config.root_data
        verify_github_ips = root_config.get("verify-github-ips")
        verify_cloudflare_ips = root_config.get("verify-cloudflare-ips")
        disable_ssl_warnings = root_config.get("disable-ssl-warnings", False)

        # Conditionally disable urllib3 warnings based on config
        if disable_ssl_warnings:
            urllib3.disable_warnings()
            LOGGER.debug("SSL warnings disabled per configuration")

        LOGGER.debug(f"verify_github_ips: {verify_github_ips}, verify_cloudflare_ips: {verify_cloudflare_ips}")

        global ALLOWED_IPS
        networks: set[ipaddress._BaseNetwork] = set()

        if verify_cloudflare_ips:
            try:
                cf_ips = await get_cloudflare_allowlist()
                for cidr in cf_ips:
                    try:
                        networks.add(ipaddress.ip_network(cidr))
                    except ValueError:
                        LOGGER.warning(f"Skipping invalid CIDR from Cloudflare: {cidr}")
            except Exception as e:
                LOGGER.error(f"Failed to fetch Cloudflare IPs: {e}")
                if verify_github_ips is False:
                    raise  # If neither source works, fail

        if verify_github_ips:
            try:
                gh_ips = await get_github_allowlist()
                for cidr in gh_ips:
                    try:
                        networks.add(ipaddress.ip_network(cidr))
                    except ValueError:
                        LOGGER.warning(f"Skipping invalid CIDR from Github: {cidr}")
            except Exception as e:
                LOGGER.error(f"Failed to fetch GitHub IPs: {e}")
                if verify_cloudflare_ips is False:
                    raise  # If neither source works, fail

        if networks:
            ALLOWED_IPS = tuple(networks)
            LOGGER.info(f"IP allowlist initialized successfully with {len(ALLOWED_IPS)} networks.")
        elif verify_github_ips or verify_cloudflare_ips:
            LOGGER.warning("IP verification enabled but no valid IPs loaded - webhook will accept from any IP")

        yield

    except Exception as ex:
        LOGGER.error(f"Application failed during lifespan management: {ex}")
        raise

    finally:
        if _lifespan_http_client:
            await _lifespan_http_client.aclose()
            LOGGER.debug("HTTP client closed")

        LOGGER.info("Application shutdown complete.")


FASTAPI_APP: FastAPI = FastAPI(title="webhook-server", lifespan=lifespan)


@FASTAPI_APP.get(f"{APP_URL_ROOT_PATH}/healthcheck")
def healthcheck() -> dict[str, Any]:
    return {"status": requests.codes.ok, "message": "Alive"}


@FASTAPI_APP.post(APP_URL_ROOT_PATH, dependencies=[Depends(gate_by_allowlist_ips)])
async def process_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    # Extract headers early for logging
    delivery_id = request.headers.get("X-GitHub-Delivery", "unknown-delivery")
    event_type = request.headers.get("X-GitHub-Event", "unknown-event")
    log_context = f"[Event: {event_type}][Delivery: {delivery_id}]"

    LOGGER.info(f"{log_context} Processing webhook")

    try:
        payload_body = await request.body()
    except Exception as e:
        LOGGER.error(f"{log_context} Failed to read request body: {e}")
        raise HTTPException(status_code=400, detail="Failed to read request body")

    # Load config and verify signature
    try:
        config = Config(logger=LOGGER)
        root_config = config.root_data
        webhook_secret = root_config.get("webhook-secret")

        if webhook_secret:
            signature_header = request.headers.get("x-hub-signature-256")
            verify_signature(payload_body=payload_body, secret_token=webhook_secret, signature_header=signature_header)
            LOGGER.debug(f"{log_context} Signature verification successful")
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"{log_context} Configuration error: {e}")
        raise HTTPException(status_code=500, detail="Configuration error")

    # Parse JSON payload
    try:
        hook_data: dict[Any, Any] = json.loads(payload_body)
        if "repository" not in hook_data or "name" not in hook_data["repository"]:
            raise ValueError("Missing repository information in payload")
    except json.JSONDecodeError as e:
        LOGGER.error(f"{log_context} Invalid JSON payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    except ValueError as e:
        LOGGER.error(f"{log_context} Invalid payload structure: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    # Create repository-specific logger
    repository_name = hook_data["repository"]["name"]
    logger = get_logger_with_params(name="main", repository_name=repository_name)
    logger.info(f"{log_context} Processing webhook for repository: {repository_name}")

    async def process_with_error_handling(_api: GithubWebhook, _logger: logging.Logger) -> None:
        try:
            await _api.process()
            _logger.success(f"{log_context} Webhook processing completed successfully")  # type: ignore
        except Exception as e:
            _logger.exception(f"{log_context} Error in background task: {e}")

    try:
        api: GithubWebhook = GithubWebhook(hook_data=hook_data, headers=request.headers, logger=logger)
        background_tasks.add_task(process_with_error_handling, _api=api, _logger=logger)

        LOGGER.info(f"{log_context} Webhook queued for background processing")
        return {
            "status": status.HTTP_200_OK,
            "message": "Webhook queued for processing",
            "delivery_id": delivery_id,
            "event_type": event_type,
        }

    except RepositoryNotFoundError as e:
        logger.error(f"{log_context} Repository not found: {e}")
        raise HTTPException(status_code=404, detail=str(e))

    except ConnectionError as e:
        logger.error(f"{log_context} API connection error: {e}")
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


# Log Viewer Endpoints
@FASTAPI_APP.get("/logs", response_class=HTMLResponse)
def get_log_viewer_page() -> HTMLResponse:
    """Serve the main log viewer HTML page."""
    controller = LogViewerController(logger=LOGGER)
    return controller.get_log_page()


@FASTAPI_APP.get("/logs/api/entries")
def get_log_entries(
    hook_id: str | None = None,
    pr_number: int | None = None,
    repository: str | None = None,
    event_type: str | None = None,
    github_user: str | None = None,
    level: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Retrieve historical log entries with filtering and pagination."""
    controller = LogViewerController(logger=LOGGER)

    # Parse datetime strings if provided
    start_datetime = None
    end_datetime = None

    if start_time:
        try:
            start_datetime = datetime.datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_time format (use ISO 8601)")

    if end_time:
        try:
            end_datetime = datetime.datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end_time format (use ISO 8601)")

    return controller.get_log_entries(
        hook_id=hook_id,
        pr_number=pr_number,
        repository=repository,
        event_type=event_type,
        github_user=github_user,
        level=level,
        start_time=start_datetime,
        end_time=end_datetime,
        search=search,
        limit=limit,
        offset=offset,
    )


@FASTAPI_APP.get("/logs/api/export")
def export_logs(
    format: str,
    hook_id: str | None = None,
    pr_number: int | None = None,
    repository: str | None = None,
    event_type: str | None = None,
    github_user: str | None = None,
    level: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    search: str | None = None,
    limit: int = 10000,
) -> StreamingResponse:
    """Export filtered logs as JSON file."""
    controller = LogViewerController(logger=LOGGER)

    # Parse datetime strings if provided
    start_datetime = None
    end_datetime = None

    if start_time:
        try:
            start_datetime = datetime.datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_time format (use ISO 8601)")

    if end_time:
        try:
            end_datetime = datetime.datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end_time format (use ISO 8601)")

    return controller.export_logs(
        format_type=format,
        hook_id=hook_id,
        pr_number=pr_number,
        repository=repository,
        event_type=event_type,
        github_user=github_user,
        level=level,
        start_time=start_datetime,
        end_time=end_datetime,
        search=search,
        limit=limit,
    )


@FASTAPI_APP.get("/logs/api/pr-flow/{hook_id}")
def get_pr_flow_data(hook_id: str) -> dict[str, Any]:
    """Get PR flow visualization data for a specific hook ID or PR number."""
    controller = LogViewerController(logger=LOGGER)
    return controller.get_pr_flow_data(hook_id)


@FASTAPI_APP.get("/logs/api/workflow-steps/{hook_id}")
def get_workflow_steps(hook_id: str) -> dict[str, Any]:
    """Get workflow step timeline data for a specific hook ID."""
    controller = LogViewerController(logger=LOGGER)
    return controller.get_workflow_steps(hook_id)


@FASTAPI_APP.websocket("/logs/ws")
async def websocket_log_stream(
    websocket: WebSocket,
    hook_id: str | None = None,
    pr_number: int | None = None,
    repository: str | None = None,
    event_type: str | None = None,
    github_user: str | None = None,
    level: str | None = None,
) -> None:
    """Handle WebSocket connection for real-time log streaming."""
    controller = LogViewerController(logger=LOGGER)
    await controller.handle_websocket(
        websocket=websocket,
        hook_id=hook_id,
        pr_number=pr_number,
        repository=repository,
        event_type=event_type,
        github_user=github_user,
        level=level,
    )
