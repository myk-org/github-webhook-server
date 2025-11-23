import asyncio
import ipaddress
import json
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import requests
import urllib3
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Import for MCP integration
from fastapi_mcp import FastApiMCP
from fastapi_mcp.transport.http import FastApiHttpSessionManager
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.datastructures import Headers

from webhook_server.libs.config import Config
from webhook_server.libs.exceptions import RepositoryNotFoundInConfigError
from webhook_server.libs.github_api import GithubWebhook
from webhook_server.libs.metrics_tracker import MetricsTracker
from webhook_server.utils.app_utils import (
    HTTP_TIMEOUT_SECONDS,
    gate_by_allowlist_ips,
    get_cloudflare_allowlist,
    get_github_allowlist,
    parse_datetime_string,
    verify_signature,
)
from webhook_server.utils.helpers import (
    get_logger_with_params,
    prepare_log_prefix,
)
from webhook_server.web.log_viewer import LogViewerController

# Constants
APP_URL_ROOT_PATH: str = "/webhook_server"
LOG_SERVER_ENABLED: bool = os.environ.get("ENABLE_LOG_SERVER") == "true"
MCP_SERVER_ENABLED: bool = os.environ.get("ENABLE_MCP_SERVER") == "true"
METRICS_SERVER_ENABLED: bool = os.environ.get("ENABLE_METRICS_SERVER") == "true"

# Global variables
ALLOWED_IPS: tuple[ipaddress._BaseNetwork, ...] = ()
LOGGER = get_logger_with_params()

_lifespan_http_client: httpx.AsyncClient | None = None
_background_tasks: set[asyncio.Task] = set()

# MCP Globals
http_transport: Any | None = None
mcp: Any | None = None

# Metrics Server Globals
db_manager: Any | None = None
redis_manager: Any | None = None
metrics_tracker: Any | None = None


class IgnoreMCPClosedResourceErrorFilter(logging.Filter):
    """Filter to suppress ClosedResourceError logs from MCP server."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Check for the specific error message from mcp.server.streamable_http
        if "Error in message router" in record.getMessage():
            if record.exc_info:
                exc_type, _, _ = record.exc_info
                # Check if it's a ClosedResourceError (from anyio)
                if exc_type and "ClosedResourceError" in exc_type.__name__:
                    return False
        return True


# Helper function to wrap the imported gate_by_allowlist_ips with ALLOWED_IPS
async def gate_by_allowlist_ips_dependency(request: Request) -> None:
    """Dependency wrapper for IP allowlist gating."""
    await gate_by_allowlist_ips(request, ALLOWED_IPS)


def require_log_server_enabled() -> None:
    """Dependency to ensure log server is enabled before accessing log viewer APIs."""
    if not LOG_SERVER_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Log server is disabled. Set ENABLE_LOG_SERVER=true to enable.",
        )


def require_metrics_server_enabled() -> None:
    """Dependency to ensure metrics server is enabled before accessing metrics APIs."""
    if not METRICS_SERVER_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metrics server is disabled. Set ENABLE_METRICS_SERVER=true to enable.",
        )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    global _lifespan_http_client, ALLOWED_IPS, http_transport, mcp, db_manager, redis_manager
    global metrics_tracker, _log_viewer_controller_singleton, _background_tasks
    _lifespan_http_client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS)

    # Apply filter to MCP logger to suppress client disconnect noise
    mcp_logger = logging.getLogger("mcp.server.streamable_http")
    if not any(isinstance(f, IgnoreMCPClosedResourceErrorFilter) for f in mcp_logger.filters):
        mcp_logger.addFilter(IgnoreMCPClosedResourceErrorFilter())

    try:
        LOGGER.info("Application starting up...")

        # Validate static files directory exists
        if not os.path.exists(static_files_path):
            raise FileNotFoundError(
                f"Static files directory not found: {static_files_path}. "
                f"This directory is required for serving web assets (CSS/JS). "
                f"Expected structure: webhook_server/web/static/ with css/ and js/ subdirectories."
            )

        if not os.path.isdir(static_files_path):
            raise NotADirectoryError(
                f"Static files path exists but is not a directory: {static_files_path}. "
                f"Expected a directory containing css/ and js/ subdirectories."
            )

        LOGGER.info(f"Static files directory validated: {static_files_path}")

        config = Config(logger=LOGGER)
        root_config = config.root_data

        # Configure MCP logging separation
        if MCP_SERVER_ENABLED:
            mcp_log_file = root_config.get("mcp-log-file", "mcp_server.log")

            # Use get_logger_with_params to reuse existing logging configuration logic
            # (rotation, sensitive data masking, formatting)
            # This returns a logger configured for the specific file
            mcp_file_logger = get_logger_with_params(log_file_name=mcp_log_file)

            # Add the configured handler to the actual MCP logger and stop propagation
            # This ensures MCP logs go ONLY to mcp_server.log and not webhook_server.log
            if mcp_file_logger.handlers:
                for handler in mcp_file_logger.handlers:
                    mcp_logger.addHandler(handler)

                mcp_logger.propagate = False
                LOGGER.info(f"MCP logging configured to: {mcp_log_file} via handlers from {mcp_file_logger.name}")

        # Configure Metrics Server logging separation
        if METRICS_SERVER_ENABLED:
            metrics_log_file = root_config.get("metrics-server-log-file", "metrics_server.log")

            # Use get_logger_with_params to reuse existing logging configuration logic
            # (rotation, sensitive data masking, formatting)
            # This returns a logger configured for the specific file
            metrics_file_logger = get_logger_with_params(log_file_name=metrics_log_file)

            # Create dedicated logger for metrics server and stop propagation
            # This ensures Metrics logs go ONLY to metrics_server.log and not webhook_server.log
            metrics_logger = logging.getLogger("webhook_server.metrics")
            if metrics_file_logger.handlers:
                for handler in metrics_file_logger.handlers:
                    metrics_logger.addHandler(handler)

                metrics_logger.propagate = False
                LOGGER.info(
                    f"Metrics Server logging configured to: {metrics_log_file} "
                    f"via handlers from {metrics_file_logger.name}"
                )

        verify_github_ips = root_config.get("verify-github-ips", False)
        verify_cloudflare_ips = root_config.get("verify-cloudflare-ips", False)
        disable_ssl_warnings = root_config.get("disable-ssl-warnings", False)

        # Conditionally disable urllib3 warnings based on config
        if disable_ssl_warnings:
            urllib3.disable_warnings()
            LOGGER.debug("SSL warnings disabled per configuration")

        LOGGER.debug(f"verify_github_ips: {verify_github_ips}, verify_cloudflare_ips: {verify_cloudflare_ips}")

        networks: set[ipaddress._BaseNetwork] = set()

        if verify_cloudflare_ips:
            try:
                cf_ips = await get_cloudflare_allowlist(_lifespan_http_client)
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
                gh_ips = await get_github_allowlist(_lifespan_http_client)
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
            # Fail-close: If IP verification is enabled but no networks loaded, reject all requests
            LOGGER.error("IP verification enabled but no valid IPs loaded - failing closed for security")
            raise RuntimeError(
                "IP verification enabled but no allowlist loaded. "
                "Cannot start server in insecure state. "
                "Check network connectivity to GitHub/Cloudflare API endpoints."
            )

        # Initialize MCP session manager if enabled and configured
        if MCP_SERVER_ENABLED and http_transport is not None and mcp is not None:
            if http_transport._session_manager is None:
                http_transport._session_manager = StreamableHTTPSessionManager(
                    app=mcp.server,
                    event_store=http_transport.event_store,
                    json_response=True,
                    stateless=True,  # Enable stateless mode - no session management required
                )

                async def run_manager() -> None:
                    if http_transport and http_transport._session_manager:
                        async with http_transport._session_manager.run():
                            await asyncio.Event().wait()

                http_transport._manager_task = asyncio.create_task(run_manager())
                http_transport._manager_started = True
                LOGGER.info("MCP session manager initialized in lifespan")

        # Initialize database managers if metrics server is enabled
        if METRICS_SERVER_ENABLED:
            from webhook_server.libs.database import DatabaseManager, RedisManager  # noqa: PLC0415

            metrics_logger = logging.getLogger("webhook_server.metrics")
            db_manager = DatabaseManager(config, metrics_logger)
            redis_manager = RedisManager(config, metrics_logger)

            await db_manager.connect()
            await redis_manager.connect()
            LOGGER.info("Metrics Server database managers initialized successfully")

            # Initialize metrics tracker
            metrics_tracker = MetricsTracker(db_manager, redis_manager, metrics_logger)
            LOGGER.info("Metrics tracker initialized successfully")

        yield

    except Exception as ex:
        LOGGER.error(f"Application failed during lifespan management: {ex}")
        raise

    finally:
        # Disconnect database managers if they exist
        if db_manager is not None:
            await db_manager.disconnect()
            LOGGER.debug("Database manager disconnected")
        if redis_manager is not None:
            await redis_manager.disconnect()
            LOGGER.debug("Redis manager disconnected")
        if db_manager is not None or redis_manager is not None:
            LOGGER.info("Metrics Server database managers shutdown complete")

        # Shutdown LogViewerController singleton and close WebSocket connections
        if _log_viewer_controller_singleton is not None:
            await _log_viewer_controller_singleton.shutdown()
            LOGGER.debug("LogViewerController singleton shutdown complete")

        if _lifespan_http_client:
            await _lifespan_http_client.aclose()
            LOGGER.debug("HTTP client closed")

        # Optionally wait for pending background tasks for graceful shutdown
        if _background_tasks:
            LOGGER.info(f"Waiting for {len(_background_tasks)} pending background task(s) to complete...")
            # Wait up to 30 seconds for tasks to complete
            done, pending = await asyncio.wait(_background_tasks, timeout=30.0, return_when=asyncio.ALL_COMPLETED)
            if pending:
                LOGGER.warning(f"{len(pending)} background task(s) did not complete within timeout, cancelling...")
                for task in pending:
                    task.cancel()
                # Wait briefly for cancellations to propagate
                await asyncio.wait(pending, timeout=5.0)
            LOGGER.debug(f"Background tasks cleanup complete: {len(done)} completed, {len(pending)} cancelled")

        LOGGER.info("Application shutdown complete.")


FASTAPI_APP: FastAPI = FastAPI(title="webhook-server", lifespan=lifespan)

# Mount static files
static_files_path = os.path.join(os.path.dirname(__file__), "web", "static")
FASTAPI_APP.mount("/static", StaticFiles(directory=static_files_path), name="static")


@FASTAPI_APP.get(f"{APP_URL_ROOT_PATH}/healthcheck", operation_id="healthcheck")
def healthcheck() -> dict[str, Any]:
    return {"status": requests.codes.ok, "message": "Alive"}


@FASTAPI_APP.post(
    APP_URL_ROOT_PATH,
    operation_id="process_webhook",
    dependencies=[Depends(gate_by_allowlist_ips_dependency)],
    tags=["mcp_exclude"],
)
async def process_webhook(request: Request) -> JSONResponse:
    """Process GitHub webhooks with immediate 200 OK response and background processing.

    **Critical Design Pattern:**
    This endpoint returns 200 OK immediately after validating that we have enough
    data to process the webhook. This design prevents GitHub webhook timeouts (10
    second limit) while allowing long-running operations to complete asynchronously.

    **Synchronous Validation (must pass to return 200):**
    1. Read request body
    2. Verify signature (if webhook-secret configured)
    3. Parse JSON payload
    4. Validate required fields: repository.name, repository.full_name, X-GitHub-Event

    **Background Processing (errors logged only):**
    - Config loading, repository validation, API initialization
    - All API calls
    - All handler processing
    - All errors (missing repos, API failures, etc.) are caught and logged

    **Why Background Processing:**
    - GitHub webhook timeout: 10 seconds
    - Typical processing time: 5-30 seconds (API calls, builds, notifications)
    - Without background processing: Frequent timeouts, webhook retries, duplicates
    - With background processing: Instant 200 OK, reliable webhook delivery

    **Implications:**
    - HTTP 200 OK means webhook payload was valid and queued for processing
    - HTTP 200 OK does NOT mean webhook was processed successfully
    - Check logs with delivery_id to verify actual processing results

    Args:
        request: FastAPI Request object containing webhook payload and headers

    Returns:
        JSONResponse: 200 OK response with delivery_id and event_type for tracking

    Raises:
        HTTPException 400: Missing required fields (X-GitHub-Event, repository.name,
            repository.full_name) or invalid JSON payload
        HTTPException 401: Signature verification failed (if webhook-secret configured)
        HTTPException 500: Configuration errors during signature verification setup

    Note:
        All processing errors (missing repos, API failures, etc.)
        happen in background and are logged only. They do NOT affect the HTTP response.
    """
    # Extract headers for validation and logging
    delivery_id = request.headers.get("X-GitHub-Delivery", "unknown-delivery")
    event_type = request.headers.get("X-GitHub-Event")
    log_context = prepare_log_prefix(event_type or "unknown-event", delivery_id)

    LOGGER.info(f"{log_context} Processing webhook")

    # Validate X-GitHub-Event header (required by GithubWebhook.__init__)
    if not event_type:
        LOGGER.error(f"{log_context} Missing X-GitHub-Event header")
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Event header")

    # Read request body
    try:
        payload_body = await request.body()
    except Exception as e:
        LOGGER.error(f"{log_context} Failed to read request body: {e}")
        raise HTTPException(status_code=400, detail="Failed to read request body") from e

    # Verify signature if configured
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
        raise HTTPException(status_code=500, detail="Configuration error") from e

    # Parse JSON payload
    try:
        hook_data: dict[Any, Any] = json.loads(payload_body)
    except json.JSONDecodeError:
        LOGGER.exception(f"{log_context} Invalid JSON payload")
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from None

    # Validate required fields for GithubWebhook.__init__()
    if "repository" not in hook_data:
        LOGGER.error(f"{log_context} Missing repository in payload")
        raise HTTPException(status_code=400, detail="Missing repository in payload")
    if "name" not in hook_data["repository"]:
        LOGGER.error(f"{log_context} Missing repository.name in payload")
        raise HTTPException(status_code=400, detail="Missing repository.name in payload")
    if "full_name" not in hook_data["repository"]:
        LOGGER.error(f"{log_context} Missing repository.full_name in payload")
        raise HTTPException(status_code=400, detail="Missing repository.full_name in payload")

    # Return 200 immediately - all validation passed, we can process this webhook
    LOGGER.info(f"{log_context} Webhook validation passed, queuing for background processing")

    async def process_with_error_handling(
        _hook_data: dict[Any, Any], _headers: Headers, _delivery_id: str, _event_type: str
    ) -> None:
        """Process webhook in background with granular error handling.

        This function runs in a background task after the webhook endpoint has already
        returned 200 OK to GitHub. Exceptions here do NOT affect the HTTP response,
        preventing webhook timeouts while still logging all errors for debugging.

        Args:
            _hook_data: Webhook payload data dictionary
            _headers: Starlette Headers object from the incoming request
            _delivery_id: GitHub delivery ID for logging
            _event_type: GitHub event type for logging
        """
        # Track processing start time for metrics
        start_time = datetime.now(UTC)

        # Create repository-specific logger in background
        repository_name = _hook_data.get("repository", {}).get("name", "unknown")
        _logger = get_logger_with_params(repository_name=repository_name)
        _log_context = prepare_log_prefix(
            event_type=_event_type, delivery_id=_delivery_id, repository_name=repository_name
        )
        _logger.info(f"{_log_context} Processing webhook")

        # Extract common webhook metadata for metrics tracking
        _repository = _hook_data.get("repository", {}).get("full_name", "unknown")
        _action = _hook_data.get("action")
        _sender = _hook_data.get("sender", {}).get("login")
        _pr_number = _hook_data.get("pull_request", {}).get("number")

        try:
            # Initialize GithubWebhook inside background task to avoid blocking webhook response
            _api: GithubWebhook = GithubWebhook(hook_data=_hook_data, headers=_headers, logger=_logger)
            try:
                await _api.process()

                # Track successful webhook event
                if METRICS_SERVER_ENABLED and metrics_tracker:
                    processing_time = (datetime.now(UTC) - start_time).total_seconds() * 1000
                    await metrics_tracker.track_webhook_event(
                        delivery_id=_delivery_id,
                        repository=_repository,
                        event_type=_event_type,
                        action=_action,
                        sender=_sender,
                        payload=_hook_data,
                        processing_time_ms=int(processing_time),
                        status="success",
                        pr_number=_pr_number,
                    )
            finally:
                await _api.cleanup()
        except RepositoryNotFoundInConfigError as ex:
            # Repository-specific error - not exceptional, log as error not exception
            _logger.error(f"{_log_context} Repository not found in configuration")

            # Track failed webhook event
            if METRICS_SERVER_ENABLED and metrics_tracker:
                processing_time = (datetime.now(UTC) - start_time).total_seconds() * 1000
                await metrics_tracker.track_webhook_event(
                    delivery_id=_delivery_id,
                    repository=_repository,
                    event_type=_event_type,
                    action=_action,
                    sender=_sender,
                    payload=_hook_data,
                    processing_time_ms=int(processing_time),
                    status="error",
                    error_message=str(ex),
                    pr_number=_pr_number,
                )
        except (httpx.ConnectError, httpx.RequestError, requests.exceptions.ConnectionError) as ex:
            # Network/connection errors - can be transient
            _logger.exception(f"{_log_context} API connection error - check network connectivity")

            # Track failed webhook event
            if METRICS_SERVER_ENABLED and metrics_tracker:
                processing_time = (datetime.now(UTC) - start_time).total_seconds() * 1000
                await metrics_tracker.track_webhook_event(
                    delivery_id=_delivery_id,
                    repository=_repository,
                    event_type=_event_type,
                    action=_action,
                    sender=_sender,
                    payload=_hook_data,
                    processing_time_ms=int(processing_time),
                    status="error",
                    error_message=str(ex),
                    pr_number=_pr_number,
                )
        except Exception as ex:
            # Catch-all for unexpected errors
            _logger.exception(f"{_log_context} Unexpected error in background webhook processing")

            # Track failed webhook event
            if METRICS_SERVER_ENABLED and metrics_tracker:
                processing_time = (datetime.now(UTC) - start_time).total_seconds() * 1000
                await metrics_tracker.track_webhook_event(
                    delivery_id=_delivery_id,
                    repository=_repository,
                    event_type=_event_type,
                    action=_action,
                    sender=_sender,
                    payload=_hook_data,
                    processing_time_ms=int(processing_time),
                    status="error",
                    error_message=str(ex),
                    pr_number=_pr_number,
                )

    # Start background task immediately using asyncio.create_task
    # This ensures the HTTP response is sent immediately without waiting
    # Store task reference for observability and graceful shutdown
    task = asyncio.create_task(
        process_with_error_handling(
            _hook_data=hook_data,
            _headers=request.headers,
            _delivery_id=delivery_id,
            _event_type=event_type,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    # Return 200 immediately with JSONResponse for fastest serialization
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": status.HTTP_200_OK,
            "message": "Webhook queued for processing",
            "delivery_id": delivery_id,
            "event_type": event_type,
        },
    )


# Module-level singleton instance
_log_viewer_controller_singleton: LogViewerController | None = None


def get_log_viewer_controller() -> LogViewerController:
    """Dependency to provide a singleton LogViewerController instance.

    Returns the same LogViewerController instance across all requests to ensure
    proper WebSocket connection tracking and shared state management.

    Returns:
        LogViewerController: The singleton instance
    """
    global _log_viewer_controller_singleton
    if _log_viewer_controller_singleton is None:
        # Use global LOGGER for config operations
        config = Config(logger=LOGGER)
        logs_server_log_file = config.get_value("logs-server-log-file", return_on_none="logs_server.log")

        # Create dedicated logger for log viewer
        log_viewer_logger = get_logger_with_params(log_file_name=logs_server_log_file)
        _log_viewer_controller_singleton = LogViewerController(logger=log_viewer_logger)
    return _log_viewer_controller_singleton


# Create dependency instance to avoid flake8 M511 warnings
controller_dependency = Depends(get_log_viewer_controller)


# Log Viewer Endpoints - Only register if ENABLE_LOG_SERVER=true
if LOG_SERVER_ENABLED:

    @FASTAPI_APP.get("/logs", operation_id="get_log_viewer_page", response_class=HTMLResponse)
    def get_log_viewer_page(controller: LogViewerController = controller_dependency) -> HTMLResponse:
        """Serve the main log viewer HTML page."""
        return controller.get_log_page()


async def _get_log_entries_core(
    controller: LogViewerController,
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
    """Core logic for retrieving historical log entries with filtering and pagination."""
    # Parse datetime strings using helper function
    start_datetime = parse_datetime_string(start_time, "start_time")
    end_datetime = parse_datetime_string(end_time, "end_time")

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


@FASTAPI_APP.get(
    "/logs/api/entries",
    operation_id="get_log_entries",
    dependencies=[Depends(require_log_server_enabled)],
)
async def get_log_entries(
    hook_id: str | None = None,
    pr_number: int | None = None,
    repository: str | None = None,
    event_type: str | None = None,
    github_user: str | None = None,
    level: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=10000, description="Maximum entries to return (1-10000)"),
    offset: int = Query(default=0, ge=0, description="Number of entries to skip for pagination"),
    controller: LogViewerController = controller_dependency,
) -> dict[str, Any]:
    """Retrieve and filter webhook processing logs with advanced pagination and search capabilities.

    This endpoint provides comprehensive access to webhook server logs for monitoring, debugging,
    and analysis. It supports multiple filtering dimensions and is optimized for memory-efficient
    streaming of large datasets.

    **Primary Use Cases:**
    - Debug webhook processing issues by filtering specific events or time ranges
    - Monitor PR processing workflows and identify bottlenecks
    - Audit user activity and GitHub interactions across repositories
    - Generate reports on webhook processing performance and errors
    - Investigate specific GitHub delivery failures or API rate limiting

    **Parameters:**
    - `hook_id` (str, optional): GitHub webhook delivery ID (X-GitHub-Delivery header).
      Example: "f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f"
    - `pr_number` (int, optional): Pull request number to filter PR-related events.
      Example: 42 (will match logs for PR #42)
    - `repository` (str, optional): Repository name in "owner/repo" format.
      Example: "myakove/github-webhook-server"
    - `event_type` (str, optional): GitHub webhook event type.
      Common values: "pull_request", "push", "issues", "issue_comment", "pull_request_review"
    - `github_user` (str, optional): GitHub username who triggered the webhook.
      Example: "myakove" (filters events by user activity)
    - `level` (str, optional): Log level filter.
      Values: "DEBUG", "INFO", "WARNING", "ERROR", "SUCCESS"
    - `start_time` (str, optional): Start of time range in ISO format.
      Example: "2024-01-15T10:00:00Z" or "2024-01-15T10:00:00.123456"
    - `end_time` (str, optional): End of time range in ISO format.
      Example: "2024-01-15T18:00:00Z"
    - `search` (str, optional): Text search across log messages (case-insensitive).
      Example: "rate limit" or "container build failed"
    - `limit` (int, default=100): Maximum entries to return (1-10000).
      Larger values may increase response time and memory usage.
    - `offset` (int, default=0): Number of entries to skip for pagination.
      Use with limit for paginated access to large result sets.

    **Return Structure:**
    ```json
    {
      "entries": [
        {
          "timestamp": "2024-01-15T14:30:25.123456",
          "level": "INFO",
          "message": "Processing webhook for repository: myakove/test-repo",
          "hook_id": "f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f",
          "repository": "myakove/test-repo",
          "event_type": "pull_request",
          "github_user": "contributor123",
          "pr_number": 42,
          "additional_data": {...}
        }
      ],
      "total_count": 1542,
      "has_more": true,
      "next_offset": 100
    }
    ```

    **Common Filtering Scenarios:**
    - Recent errors: `level=ERROR&start_time=2024-01-15T00:00:00Z`
    - Specific PR workflow: `pr_number=42&repository=owner/repo`
    - User activity audit: `github_user=username&start_time=2024-01-01T00:00:00Z`
    - Event type analysis: `event_type=pull_request&level=ERROR`
    - Webhook delivery debugging: `hook_id=specific-delivery-id`
    - Performance monitoring: `search=rate limit&level=WARNING`

    **Error Conditions:**
    - 400: Invalid datetime format in start_time/end_time parameters
    - 400: Invalid limit value (must be 1-10000)
    - 500: Log file access errors or disk I/O issues
    - 500: Memory exhaustion with very large result sets

    **AI Agent Usage Examples:**
    - "Get all ERROR level logs from the last 24 hours to identify system issues"
    - "Find all pull_request events for repository X to analyze PR processing workflow"
    - "Search for 'container build' failures in the last week to debug CI issues"
    - "Get logs for specific webhook delivery ID to debug why a webhook failed"
    - "Monitor specific user's activity across all repositories for security audit"

    **Performance Notes:**
    - Response times increase with larger date ranges and broader search terms
    - Memory usage is optimized through streaming; large limits may still impact performance
    - Use specific filters (hook_id, repository) for fastest queries
    - Avoid very broad searches without time constraints on production systems
    """
    return await _get_log_entries_core(
        controller=controller,
        hook_id=hook_id,
        pr_number=pr_number,
        repository=repository,
        event_type=event_type,
        github_user=github_user,
        level=level,
        start_time=start_time,
        end_time=end_time,
        search=search,
        limit=limit,
        offset=offset,
    )


async def _export_logs_core(
    controller: LogViewerController,
    format_type: str,
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
    """Core logic for exporting filtered logs as file."""
    # Parse datetime strings using helper function
    start_datetime = parse_datetime_string(start_time, "start_time")
    end_datetime = parse_datetime_string(end_time, "end_time")

    return controller.export_logs(
        format_type=format_type,
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


@FASTAPI_APP.get(
    "/logs/api/export",
    operation_id="export_logs",
    dependencies=[Depends(require_log_server_enabled)],
)
async def export_logs(
    format_type: str = Query(
        default="json",
        pattern="^json$",
        description="Export format (currently only 'json' supported)",
    ),
    hook_id: str | None = None,
    pr_number: int | None = None,
    repository: str | None = None,
    event_type: str | None = None,
    github_user: str | None = None,
    level: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    search: str | None = None,
    limit: int = Query(default=10000, ge=1, le=100000, description="Maximum entries to export (1-100000)"),
    controller: LogViewerController = controller_dependency,
) -> StreamingResponse:
    """Export filtered webhook logs to downloadable files for offline analysis and reporting.

    This endpoint generates downloadable files containing filtered log data, supporting various
    export formats for integration with external analysis tools, compliance reporting, and
    long-term log archival. Uses memory-efficient streaming to handle large datasets.

    **Primary Use Cases:**
    - Generate compliance reports for security audits and regulatory requirements
    - Export error logs for offline analysis and debugging sessions
    - Create data backups of critical webhook processing events
    - Feed log data into external monitoring and analytics platforms
    - Generate historical reports for performance analysis and trend identification
    - Archive logs for long-term storage and compliance requirements

    **Parameters:**
    - `format_type` (str, required): Export file format.
      Currently supported: "json" (additional formats may be added in future versions)
    - `hook_id` (str, optional): GitHub webhook delivery ID for specific event export.
      Example: "f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f"
    - `pr_number` (int, optional): Pull request number to export PR-related logs.
      Example: 42 (exports all logs related to PR #42)
    - `repository` (str, optional): Repository name filter in "owner/repo" format.
      Example: "myakove/github-webhook-server"
    - `event_type` (str, optional): GitHub webhook event type filter.
      Common values: "pull_request", "push", "issues", "issue_comment", "release"
    - `github_user` (str, optional): GitHub username filter for user activity exports.
      Example: "myakove" (exports all events triggered by this user)
    - `level` (str, optional): Log level filter for severity-based exports.
      Values: "DEBUG", "INFO", "WARNING", "ERROR", "SUCCESS"
    - `start_time` (str, optional): Export start time in ISO format.
      Example: "2024-01-01T00:00:00Z" (exports logs from this time forward)
    - `end_time` (str, optional): Export end time in ISO format.
      Example: "2024-01-31T23:59:59Z" (exports logs up to this time)
    - `search` (str, optional): Text search filter across log messages.
      Example: "container build" or "rate limit exceeded"
    - `limit` (int, default=10000): Maximum number of log entries to export.
      Higher limits increase export time and file size. Max recommended: 50000.

    **Return Structure:**
    Returns a StreamingResponse with appropriate headers for file download:
    - Content-Type: application/json (for JSON exports)
    - Content-Disposition: attachment; filename="logs_export_YYYY-MM-DD_HH-MM-SS.json"
    - Transfer-Encoding: chunked (for memory-efficient streaming)

    **JSON Export Format:**
    ```json
    {
      "export_metadata": {
        "generated_at": "2024-01-15T14:30:25.123456Z",
        "filters_applied": {
          "repository": "myakove/test-repo",
          "level": "ERROR",
          "start_time": "2024-01-01T00:00:00Z"
        },
        "total_entries": 156,
        "export_format": "json"
      },
      "log_entries": [
        {
          "timestamp": "2024-01-15T14:30:25.123456",
          "level": "ERROR",
          "message": "Container build failed for PR #42",
          "hook_id": "delivery-id-123",
          "repository": "myakove/test-repo",
          "event_type": "pull_request",
          "github_user": "contributor",
          "pr_number": 42,
          "additional_data": {...}
        }
      ]
    }
    ```

    **Common Export Scenarios:**
    - Monthly audit reports: `start_time=2024-01-01&end_time=2024-01-31&format_type=json`
    - Error analysis export: `level=ERROR&start_time=2024-01-15&format_type=json`
    - Repository-specific backup: `repository=owner/repo&limit=50000&format_type=json`
    - User activity report: `github_user=username&start_time=2024-01-01&format_type=json`
    - PR workflow analysis: `event_type=pull_request&search=container build&format_type=json`
    - Security incident investigation: `hook_id=specific-delivery&format_type=json`

    **Error Conditions:**
    - 400: Unsupported format_type (only "json" currently supported)
    - 400: Invalid datetime format in start_time/end_time parameters
    - 400: Limit exceeds maximum allowed value (typically 100000)
    - 500: File system errors during export generation
    - 500: Memory exhaustion with extremely large datasets
    - 503: Temporary service unavailability during heavy system load

    **AI Agent Usage Examples:**
    - "Export all ERROR logs from last month for analysis: format_type=json&level=ERROR&start_time=2024-01-01"
    - "Generate security audit report: format_type=json&github_user=suspicious_user&start_time=2024-01-01"
    - "Create backup of repository logs: format_type=json&repository=critical/repo&limit=50000"
    - "Export webhook delivery investigation data: format_type=json&hook_id=failed-delivery-id"
    - "Generate performance analysis dataset: format_type=json&search=rate limit&start_time=2024-01-01"

    **Performance and Limitations:**
    - Large exports (>10000 entries) may take several minutes to complete
    - Memory usage is optimized through streaming; file size limited by disk space
    - Concurrent exports may be throttled to prevent system overload
    - Export files are generated on-demand; no caching for repeated requests
    - Recommended to use specific filters to reduce export size and improve performance

    **File Naming Convention:**
    Exported files follow the pattern: `logs_export_YYYY-MM-DD_HH-MM-SS.{format}`
    Example: `logs_export_2024-01-15_14-30-25.json`
    """
    return await _export_logs_core(
        controller=controller,
        format_type=format_type,
        hook_id=hook_id,
        pr_number=pr_number,
        repository=repository,
        event_type=event_type,
        github_user=github_user,
        level=level,
        start_time=start_time,
        end_time=end_time,
        search=search,
        limit=limit,
    )


async def _get_pr_flow_data_core(
    controller: LogViewerController,
    hook_id: str,
) -> dict[str, Any]:
    """Core logic for getting PR flow visualization data for a specific hook ID."""
    return controller.get_pr_flow_data(hook_id)


@FASTAPI_APP.get(
    "/logs/api/pr-flow/{hook_id}",
    operation_id="get_pr_flow_data",
    dependencies=[Depends(require_log_server_enabled)],
)
async def get_pr_flow_data(hook_id: str, controller: LogViewerController = controller_dependency) -> dict[str, Any]:
    """Get PR workflow visualization data for process analysis and debugging.

    Provides detailed flow analysis of pull request processing workflows, tracking the complete
    lifecycle from webhook receipt through completion. Essential for debugging PR automation
    issues, identifying bottlenecks, and optimizing workflow performance.

    Args:
        hook_id: GitHub webhook delivery ID that initiated the PR workflow.
                Example: "f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f"

    Returns:
        dict: Comprehensive workflow data including:
            - hook_id: The webhook delivery ID
            - pr_metadata: PR details (number, repository, title, author, state, timestamps)
            - workflow_stages: Array of processing stages with timestamps, status, and details
            - performance_metrics: Processing time, completion status, health indicators
            - integration_status: GitHub API usage and external service call results

    Raises:
        400: Invalid hook_id format
        404: No PR workflow found for hook_id
        500: Log parsing errors or internal processing errors

    Note:
        For detailed documentation including complete JSON examples, workflow stages,
        analysis scenarios, and usage patterns, see:
        webhook_server/docs/pr-flow-api.md
    """
    return await _get_pr_flow_data_core(controller=controller, hook_id=hook_id)


async def _get_workflow_steps_core(
    controller: LogViewerController,
    hook_id: str,
) -> dict[str, Any]:
    """Core logic for getting workflow step timeline data for a specific hook ID."""
    return controller.get_workflow_steps(hook_id)


@FASTAPI_APP.get(
    "/logs/api/workflow-steps/{hook_id}",
    operation_id="get_workflow_steps",
    dependencies=[Depends(require_log_server_enabled)],
)
async def get_workflow_steps(hook_id: str, controller: LogViewerController = controller_dependency) -> dict[str, Any]:
    """Retrieve detailed timeline and execution data for individual workflow steps within a webhook processing flow.

    This endpoint provides granular, step-by-step analysis of webhook processing workflows, offering
    detailed timing, execution status, and diagnostic information for each operation. Essential for
    performance optimization, debugging specific step failures, and understanding workflow execution patterns.

    **Primary Use Cases:**
    - Debug specific workflow step failures with detailed error information
    - Analyze step-by-step performance timing for workflow optimization
    - Monitor individual operation success rates and failure patterns
    - Generate detailed audit trails for compliance and monitoring
    - Identify resource bottlenecks in specific workflow operations
    - Track GitHub API usage and rate limiting per workflow step
    - Investigate external service integration issues at the step level

    **Parameters:**
    - `hook_id` (str, required): GitHub webhook delivery ID for the workflow to analyze.
      Example: "f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f"
      This corresponds to the X-GitHub-Delivery header value from the original webhook.
      Must be a valid delivery ID that exists in the webhook processing logs.

    **Return Structure:**
    ```json
    {
      "hook_id": "f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f",
      "workflow_metadata": {
        "repository": "myakove/github-webhook-server",
        "event_type": "pull_request",
        "initiated_at": "2024-01-15T10:00:00.123456Z",
        "total_duration_ms": 45230,
        "total_steps": 12,
        "steps_completed": 10,
        "steps_failed": 1,
        "steps_skipped": 1
      },
      "execution_timeline": [
        {
          "step_id": "webhook_validation",
          "step_name": "Webhook Signature Validation",
          "sequence": 1,
          "started_at": "2024-01-15T10:00:00.123456Z",
          "completed_at": "2024-01-15T10:00:00.156789Z",
          "duration_ms": 33,
          "status": "completed",
          "operation_type": "security",
          "details": {
            "signature_valid": true,
            "payload_size_bytes": 2048,
            "validation_method": "sha256"
          }
        },
        {
          "step_id": "payload_parsing",
          "step_name": "JSON Payload Parsing",
          "sequence": 2,
          "started_at": "2024-01-15T10:00:00.156789Z",
          "completed_at": "2024-01-15T10:00:00.189012Z",
          "duration_ms": 32,
          "status": "completed",
          "operation_type": "data_processing",
          "details": {
            "payload_size_bytes": 2048,
            "fields_extracted": 15,
            "pr_number": 42,
            "repository": "myakove/github-webhook-server"
          }
        },
        {
          "step_id": "github_api_fetch_pr",
          "step_name": "Fetch PR Details from GitHub API",
          "sequence": 3,
          "started_at": "2024-01-15T10:00:00.189012Z",
          "completed_at": "2024-01-15T10:00:01.234567Z",
          "duration_ms": 1045,
          "status": "completed",
          "operation_type": "api_call",
          "details": {
            "api_endpoint": "GET /repos/myakove/github-webhook-server/pulls/42",
            "response_status": 200,
            "rate_limit_used": 1,
            "rate_limit_remaining": 4999,
            "response_size_bytes": 8192,
            "retry_attempts": 0
          }
        },
        {
          "step_id": "pr_size_analysis",
          "step_name": "Analyze PR Size and Complexity",
          "sequence": 4,
          "started_at": "2024-01-15T10:00:01.234567Z",
          "completed_at": "2024-01-15T10:00:01.789012Z",
          "duration_ms": 554,
          "status": "completed",
          "operation_type": "analysis",
          "details": {
            "files_changed": 15,
            "lines_added": 450,
            "lines_deleted": 120,
            "size_classification": "large",
            "complexity_score": 7.5,
            "analysis_rules_applied": ["line_count", "file_count", "complexity_heuristics"]
          }
        },
        {
          "step_id": "container_build_trigger",
          "step_name": "Trigger Container Build",
          "sequence": 8,
          "started_at": "2024-01-15T10:00:05.123456Z",
          "completed_at": "2024-01-15T10:00:05.234567Z",
          "duration_ms": 111,
          "status": "failed",
          "operation_type": "build_system",
          "error_details": {
            "error_code": "BUILD_TRIGGER_FAILED",
            "error_message": "Failed to trigger container build: Registry authentication failed",
            "retry_attempts": 3,
            "last_error": "401 Unauthorized: Invalid registry credentials",
            "recovery_action": "Check registry authentication configuration"
          },
          "details": {
            "build_system": "podman",
            "registry": "quay.io/myakove/test-repo",
            "dockerfile_path": "containerfiles/Dockerfile",
            "build_context": "."
          }
        }
      ],
      "performance_analysis": {
        "slowest_steps": [
          {
            "step_id": "github_api_fetch_pr",
            "duration_ms": 1045,
            "performance_category": "external_api"
          }
        ],
        "step_categories": {
          "security": {"total_duration_ms": 33, "step_count": 1},
          "data_processing": {"total_duration_ms": 586, "step_count": 2},
          "api_call": {"total_duration_ms": 2340, "step_count": 4},
          "analysis": {"total_duration_ms": 554, "step_count": 1},
          "build_system": {"total_duration_ms": 111, "step_count": 1}
        },
        "bottlenecks": [
          {
            "category": "api_call",
            "percentage_of_total": 51.8,
            "recommendation": "Consider API response caching for repeated requests"
          }
        ]
      },
      "error_summary": {
        "total_errors": 1,
        "error_categories": {
          "build_system": 1
        },
        "critical_errors": [],
        "recoverable_errors": [
          {
            "step_id": "container_build_trigger",
            "error_type": "authentication_failure",
            "recovery_suggestion": "Verify registry credentials in configuration"
          }
        ]
      }
    }
    ```

    **Step Operation Types:**
    - `security`: Authentication, signature validation, authorization checks
    - `data_processing`: Payload parsing, data transformation, validation
    - `api_call`: GitHub API requests, external service calls
    - `analysis`: PR analysis, file processing, complexity calculation
    - `build_system`: Container builds, compilation, asset generation
    - `notification`: Slack, email, webhook notifications
    - `storage`: Database operations, file I/O, caching
    - `integration`: JIRA, external system integration

    **Step Status Values:**
    - `pending`: Step is queued but not yet started
    - `in_progress`: Step is currently executing
    - `completed`: Step finished successfully
    - `failed`: Step encountered an error
    - `skipped`: Step was bypassed due to conditions
    - `timeout`: Step exceeded maximum execution time
    - `retrying`: Step is being retried after failure

    **Common Analysis Scenarios:**
    - Identify which specific step is causing workflow delays
    - Debug authentication or API failures in external service integrations
    - Analyze GitHub API usage patterns and rate limiting impact
    - Monitor container build failure rates and error patterns
    - Optimize workflow performance by identifying slow operations
    - Generate detailed audit logs for compliance requirements
    - Troubleshoot configuration issues in specific workflow operations

    **Error Conditions:**
    - 400: Invalid hook_id format or malformed request
    - 404: No workflow steps found for the specified hook_id
    - 404: Hook_id exists but workflow data is incomplete or corrupted
    - 500: Log parsing errors or step data aggregation failures
    - 500: Internal errors during performance analysis calculation

    **AI Agent Usage Examples:**
    - "Show detailed steps for hook abc123 to debug why container builds are failing"
    - "Analyze step timing for hook xyz789 to optimize PR processing performance"
    - "Get failure details for hook def456 to troubleshoot GitHub API issues"
    - "Review security steps for hook ghi789 to audit authentication processes"
    - "Generate performance report from hook jkl012 steps to identify bottlenecks"

    **Performance Analysis Features:**
    - Automatic identification of slowest steps and bottlenecks
    - Categorization of steps by operation type for pattern analysis
    - Performance recommendations based on step timing patterns
    - Error categorization and recovery suggestions
    - Resource usage tracking (API calls, rate limits, etc.)

    **Data Granularity:**
    - Microsecond-precision timing for all step operations
    - Detailed error information including retry attempts and recovery actions
    - Complete API request/response metadata including rate limiting
    - Resource usage metrics (memory, CPU, network) where available
    - Integration status for all external services (Slack, JIRA, registries)

    **Retention and Availability:**
    - Step data is retained for 30 days by default
    - Very recent workflows (<1 hour) may have incomplete step data
    - Historical analysis is available for completed workflows
    - Real-time step data for in-progress workflows
    """
    return await _get_workflow_steps_core(controller=controller, hook_id=hook_id)


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
    # Check if log server is enabled (manual check since WebSocket doesn't support dependencies same way)
    if not LOG_SERVER_ENABLED:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Log server is disabled")
        return

    controller = get_log_viewer_controller()
    await controller.handle_websocket(
        websocket=websocket,
        hook_id=hook_id,
        pr_number=pr_number,
        repository=repository,
        event_type=event_type,
        github_user=github_user,
        level=level,
    )


# Metrics API Endpoints - Only register if ENABLE_METRICS_SERVER=true
@FASTAPI_APP.get(
    "/api/metrics/webhooks",
    operation_id="get_webhook_events",
    dependencies=[Depends(require_metrics_server_enabled)],
)
async def get_webhook_events(
    repository: str | None = Query(default=None, description="Filter by repository (org/repo format)"),
    event_type: str | None = Query(
        default=None, description="Filter by event type (pull_request, issue_comment, etc.)"
    ),
    event_status: str | None = Query(default=None, description="Filter by status (success, error, partial)"),
    start_time: str | None = Query(
        default=None, description="Start time in ISO 8601 format (e.g., 2024-01-15T00:00:00Z)"
    ),
    end_time: str | None = Query(default=None, description="End time in ISO 8601 format (e.g., 2024-01-31T23:59:59Z)"),
    limit: int = Query(default=100, ge=1, le=1000, description="Maximum entries to return (1-1000)"),
    offset: int = Query(default=0, ge=0, description="Number of entries to skip for pagination"),
) -> dict[str, Any]:
    """Retrieve recent webhook events with filtering and pagination.

    This endpoint provides comprehensive access to webhook event history for monitoring,
    debugging, and analytics. It supports multiple filtering dimensions and is optimized
    for memory-efficient querying of large datasets.

    **Primary Use Cases:**
    - Monitor webhook processing status and identify failures
    - Analyze webhook traffic patterns by repository or event type
    - Debug specific webhook delivery issues
    - Generate reports on webhook processing performance
    - Track webhook event trends over time
    - Audit webhook activity for specific repositories

    **Parameters:**
    - `repository` (str, optional): Repository name in "owner/repo" format.
      Example: "myakove/github-webhook-server"
    - `event_type` (str, optional): GitHub webhook event type.
      Common values: "pull_request", "push", "issues", "issue_comment", "pull_request_review"
    - `status` (str, optional): Processing status filter.
      Values: "success", "error", "partial"
    - `start_time` (str, optional): Start of time range in ISO 8601 format.
      Example: "2024-01-15T10:00:00Z" or "2024-01-15T10:00:00.123456"
    - `end_time` (str, optional): End of time range in ISO 8601 format.
      Example: "2024-01-15T18:00:00Z"
    - `limit` (int, default=100): Maximum entries to return (1-1000).
    - `offset` (int, default=0): Number of entries to skip for pagination.

    **Return Structure:**
    ```json
    {
      "events": [
        {
          "delivery_id": "f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f",
          "repository": "myakove/test-repo",
          "event_type": "pull_request",
          "action": "opened",
          "pr_number": 42,
          "sender": "contributor123",
          "status": "success",
          "created_at": "2024-01-15T14:30:25.123456Z",
          "processed_at": "2024-01-15T14:30:30.456789Z",
          "duration_ms": 5333,
          "api_calls_count": 12,
          "token_spend": 12,
          "token_remaining": 4988,
          "error_message": null
        }
      ],
      "total_count": 1542,
      "has_more": true,
      "next_offset": 100
    }
    ```

    **Common Filtering Scenarios:**
    - Recent errors: `status=error&start_time=2024-01-15T00:00:00Z`
    - Repository-specific events: `repository=owner/repo&limit=50`
    - Event type analysis: `event_type=pull_request&start_time=2024-01-01T00:00:00Z`
    - Failed webhooks: `status=error&event_type=pull_request`

    **Error Conditions:**
    - 400: Invalid datetime format in start_time/end_time parameters
    - 404: Metrics server disabled (ENABLE_METRICS_SERVER=false)
    - 500: Database connection errors or query failures

    **Performance Notes:**
    - Response times increase with larger date ranges
    - Use specific filters (repository, event_type) for fastest queries
    - Pagination recommended for large result sets
    """
    # Validate database manager is available
    if db_manager is None:
        LOGGER.error("Database manager not initialized - metrics server may not be properly configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Metrics database not available",
        )

    # Parse datetime strings
    start_datetime = parse_datetime_string(start_time, "start_time")
    end_datetime = parse_datetime_string(end_time, "end_time")

    # Build query with filters
    query = """
        SELECT
            delivery_id,
            repository,
            event_type,
            action,
            pr_number,
            sender,
            status,
            created_at,
            processed_at,
            duration_ms,
            api_calls_count,
            token_spend,
            token_remaining,
            error_message
        FROM webhooks
        WHERE 1=1
    """
    params: list[Any] = []
    param_idx = 1

    if repository:
        query += f" AND repository = ${param_idx}"
        params.append(repository)
        param_idx += 1

    if event_type:
        query += f" AND event_type = ${param_idx}"
        params.append(event_type)
        param_idx += 1

    if event_status:
        query += f" AND status = ${param_idx}"
        params.append(event_status)
        param_idx += 1

    if start_datetime:
        query += f" AND created_at >= ${param_idx}"
        params.append(start_datetime)
        param_idx += 1

    if end_datetime:
        query += f" AND created_at <= ${param_idx}"
        params.append(end_datetime)
        param_idx += 1

    # Get total count for pagination
    count_query = f"SELECT COUNT(*) FROM ({query}) AS filtered"
    query += f" ORDER BY created_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
    params.extend([limit, offset])

    try:
        # Validate pool is initialized
        if db_manager.pool is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database pool not initialized",
            )

        async with db_manager.pool.acquire() as conn:
            # Get total count
            total_count = await conn.fetchval(count_query, *params[:-2])

            # Get paginated results
            rows = await conn.fetch(query, *params)

            events = [
                {
                    "delivery_id": row["delivery_id"],
                    "repository": row["repository"],
                    "event_type": row["event_type"],
                    "action": row["action"],
                    "pr_number": row["pr_number"],
                    "sender": row["sender"],
                    "status": row["status"],
                    "created_at": row["created_at"].isoformat(),
                    "processed_at": row["processed_at"].isoformat(),
                    "duration_ms": row["duration_ms"],
                    "api_calls_count": row["api_calls_count"],
                    "token_spend": row["token_spend"],
                    "token_remaining": row["token_remaining"],
                    "error_message": row["error_message"],
                }
                for row in rows
            ]

            has_more = (offset + limit) < total_count
            next_offset = offset + limit if has_more else None

            return {
                "events": events,
                "total_count": total_count,
                "has_more": has_more,
                "next_offset": next_offset,
            }
    except Exception as ex:
        LOGGER.exception("Failed to fetch webhook events from database")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch webhook events: {ex!s}",
        ) from ex


@FASTAPI_APP.get(
    "/api/metrics/webhooks/{delivery_id}",
    operation_id="get_webhook_event_by_id",
    dependencies=[Depends(require_metrics_server_enabled)],
)
async def get_webhook_event_by_id(delivery_id: str) -> dict[str, Any]:
    """Get specific webhook event details including full payload.

    Retrieve comprehensive details for a specific webhook event, including the complete
    GitHub webhook payload, processing metrics, and related metadata. Essential for
    debugging specific webhook deliveries and analyzing event processing.

    **Primary Use Cases:**
    - Debug specific webhook delivery failures
    - Inspect complete webhook payload for analysis
    - Verify webhook processing metrics and timing
    - Audit specific webhook events for compliance
    - Troubleshoot GitHub API integration issues

    **Parameters:**
    - `delivery_id` (str, required): GitHub webhook delivery ID (X-GitHub-Delivery header).
      Example: "f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f"

    **Return Structure:**
    ```json
    {
      "delivery_id": "f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f",
      "repository": "myakove/test-repo",
      "event_type": "pull_request",
      "action": "opened",
      "pr_number": 42,
      "sender": "contributor123",
      "status": "success",
      "created_at": "2024-01-15T14:30:25.123456Z",
      "processed_at": "2024-01-15T14:30:30.456789Z",
      "duration_ms": 5333,
      "api_calls_count": 12,
      "token_spend": 12,
      "token_remaining": 4988,
      "error_message": null,
      "payload": {
        "action": "opened",
        "number": 42,
        "pull_request": {...},
        "repository": {...},
        "sender": {...}
      }
    }
    ```

    **Error Conditions:**
    - 404: Webhook event not found for the specified delivery_id
    - 404: Metrics server disabled (ENABLE_METRICS_SERVER=false)
    - 500: Database connection errors or query failures

    **AI Agent Usage Examples:**
    - "Get webhook details for delivery abc123 to debug processing failure"
    - "Show full payload for webhook xyz789 to analyze event structure"
    - "Retrieve webhook event def456 to verify API call metrics"
    """
    # Validate database manager is available
    if db_manager is None:
        LOGGER.error("Database manager not initialized - metrics server may not be properly configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Metrics database not available",
        )

    query = """
        SELECT
            delivery_id,
            repository,
            event_type,
            action,
            pr_number,
            sender,
            payload,
            status,
            created_at,
            processed_at,
            duration_ms,
            api_calls_count,
            token_spend,
            token_remaining,
            error_message
        FROM webhooks
        WHERE delivery_id = $1
    """

    try:
        # Validate pool is initialized
        if db_manager.pool is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database pool not initialized",
            )

        async with db_manager.pool.acquire() as conn:
            row = await conn.fetchrow(query, delivery_id)

            if not row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Webhook event not found: {delivery_id}",
                )

            return {
                "delivery_id": row["delivery_id"],
                "repository": row["repository"],
                "event_type": row["event_type"],
                "action": row["action"],
                "pr_number": row["pr_number"],
                "sender": row["sender"],
                "status": row["status"],
                "created_at": row["created_at"].isoformat(),
                "processed_at": row["processed_at"].isoformat(),
                "duration_ms": row["duration_ms"],
                "api_calls_count": row["api_calls_count"],
                "token_spend": row["token_spend"],
                "token_remaining": row["token_remaining"],
                "error_message": row["error_message"],
                "payload": row["payload"],
            }
    except HTTPException:
        raise
    except Exception as ex:
        LOGGER.exception(f"Failed to fetch webhook event {delivery_id} from database")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch webhook event: {ex!s}",
        ) from ex


@FASTAPI_APP.get(
    "/api/metrics/repositories",
    operation_id="get_repository_statistics",
    dependencies=[Depends(require_metrics_server_enabled)],
)
async def get_repository_statistics(
    start_time: str | None = Query(
        default=None, description="Start time in ISO 8601 format (e.g., 2024-01-01T00:00:00Z)"
    ),
    end_time: str | None = Query(default=None, description="End time in ISO 8601 format (e.g., 2024-01-31T23:59:59Z)"),
) -> dict[str, Any]:
    """Get aggregated statistics per repository.

    Provides comprehensive repository-level metrics including event counts, processing
    performance, success rates, and API usage. Essential for identifying high-traffic
    repositories, performance bottlenecks, and operational trends.

    **Primary Use Cases:**
    - Identify repositories with highest webhook traffic
    - Analyze repository-specific processing performance
    - Monitor success rates and error patterns by repository
    - Track API usage and rate limiting by repository
    - Generate repository-level operational reports
    - Optimize webhook processing for high-volume repositories

    **Parameters:**
    - `start_time` (str, optional): Start of time range in ISO 8601 format.
      Example: "2024-01-01T00:00:00Z"
      Default: No time filter (all-time stats)
    - `end_time` (str, optional): End of time range in ISO 8601 format.
      Example: "2024-01-31T23:59:59Z"
      Default: No time filter (up to current time)

    **Return Structure:**
    ```json
    {
      "time_range": {
        "start_time": "2024-01-01T00:00:00Z",
        "end_time": "2024-01-31T23:59:59Z"
      },
      "repositories": [
        {
          "repository": "myakove/test-repo",
          "total_events": 1542,
          "successful_events": 1489,
          "failed_events": 53,
          "success_rate": 96.56,
          "avg_processing_time_ms": 5234,
          "median_processing_time_ms": 4123,
          "p95_processing_time_ms": 12456,
          "max_processing_time_ms": 45230,
          "total_api_calls": 18504,
          "avg_api_calls_per_event": 12.0,
          "total_token_spend": 18504,
          "event_type_breakdown": {
            "pull_request": 856,
            "issue_comment": 423,
            "check_run": 263
          }
        }
      ],
      "total_repositories": 5
    }
    ```

    **Metrics Explained:**
    - `total_events`: Total webhook events processed for this repository
    - `successful_events`: Events that completed successfully
    - `failed_events`: Events that failed or partially failed
    - `success_rate`: Percentage of successful events (0-100)
    - `avg_processing_time_ms`: Average processing duration in milliseconds
    - `median_processing_time_ms`: Median processing duration (50th percentile)
    - `p95_processing_time_ms`: 95th percentile processing time (performance SLA)
    - `max_processing_time_ms`: Maximum processing time (worst case)
    - `total_api_calls`: Total GitHub API calls made
    - `avg_api_calls_per_event`: Average API calls per webhook event
    - `total_token_spend`: Total rate limit tokens consumed
    - `event_type_breakdown`: Event count distribution by type

    **Common Analysis Scenarios:**
    - Monthly repository metrics: `start_time=2024-01-01&end_time=2024-01-31`
    - High-traffic repositories: Sort by `total_events` descending
    - Performance issues: Analyze `p95_processing_time_ms` and `max_processing_time_ms`
    - Error-prone repositories: Sort by `failed_events` descending or `success_rate` ascending
    - API usage optimization: Analyze `avg_api_calls_per_event` and `total_token_spend`

    **Error Conditions:**
    - 400: Invalid datetime format in start_time/end_time parameters
    - 404: Metrics server disabled (ENABLE_METRICS_SERVER=false)
    - 500: Database connection errors or query failures

    **AI Agent Usage Examples:**
    - "Show repository statistics for last month to identify high-traffic repos"
    - "Get repository performance metrics to find slow processing repositories"
    - "Analyze repository error rates to identify problematic configurations"
    - "Review API usage by repository to optimize rate limiting strategy"

    **Performance Notes:**
    - Statistics are computed in real-time from webhook events table
    - Queries with time filters are optimized using indexed created_at column
    - Large date ranges may increase query time
    - Results ordered by total events (highest traffic first)
    """
    # Validate database manager is available
    if db_manager is None:
        LOGGER.error("Database manager not initialized - metrics server may not be properly configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Metrics database not available",
        )

    # Parse datetime strings
    start_datetime = parse_datetime_string(start_time, "start_time")
    end_datetime = parse_datetime_string(end_time, "end_time")

    # Build query with time filters
    where_clause = "WHERE 1=1"
    params: list[Any] = []
    param_idx = 1

    if start_datetime:
        where_clause += f" AND created_at >= ${param_idx}"
        params.append(start_datetime)
        param_idx += 1

    if end_datetime:
        where_clause += f" AND created_at <= ${param_idx}"
        params.append(end_datetime)
        param_idx += 1

    query = f"""
        SELECT
            repository,
            COUNT(*) as total_events,
            COUNT(*) FILTER (WHERE status = 'success') as successful_events,
            COUNT(*) FILTER (WHERE status IN ('error', 'partial')) as failed_events,
            ROUND(
                (COUNT(*) FILTER (WHERE status = 'success')::numeric / COUNT(*)::numeric * 100)::numeric,
                2
            ) as success_rate,
            ROUND(AVG(duration_ms)) as avg_processing_time_ms,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_ms) as median_processing_time_ms,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) as p95_processing_time_ms,
            MAX(duration_ms) as max_processing_time_ms,
            SUM(api_calls_count) as total_api_calls,
            ROUND(AVG(api_calls_count), 2) as avg_api_calls_per_event,
            SUM(token_spend) as total_token_spend,
            jsonb_object_agg(event_type, event_count) as event_type_breakdown
        FROM (
            SELECT
                repository,
                event_type,
                status,
                duration_ms,
                api_calls_count,
                token_spend,
                COUNT(*) OVER (PARTITION BY repository, event_type) as event_count
            FROM webhooks
            {where_clause}
        ) as events_with_counts
        GROUP BY repository
        ORDER BY total_events DESC
    """

    try:
        # Validate pool is initialized
        if db_manager.pool is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database pool not initialized",
            )

        async with db_manager.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

            repositories = [
                {
                    "repository": row["repository"],
                    "total_events": row["total_events"],
                    "successful_events": row["successful_events"],
                    "failed_events": row["failed_events"],
                    "success_rate": float(row["success_rate"]) if row["success_rate"] is not None else 0.0,
                    "avg_processing_time_ms": int(row["avg_processing_time_ms"])
                    if row["avg_processing_time_ms"] is not None
                    else 0,
                    "median_processing_time_ms": int(row["median_processing_time_ms"])
                    if row["median_processing_time_ms"] is not None
                    else 0,
                    "p95_processing_time_ms": int(row["p95_processing_time_ms"])
                    if row["p95_processing_time_ms"] is not None
                    else 0,
                    "max_processing_time_ms": row["max_processing_time_ms"] or 0,
                    "total_api_calls": row["total_api_calls"] or 0,
                    "avg_api_calls_per_event": float(row["avg_api_calls_per_event"])
                    if row["avg_api_calls_per_event"] is not None
                    else 0.0,
                    "total_token_spend": row["total_token_spend"] or 0,
                    "event_type_breakdown": row["event_type_breakdown"] or {},
                }
                for row in rows
            ]

            return {
                "time_range": {
                    "start_time": start_datetime.isoformat() if start_datetime else None,
                    "end_time": end_datetime.isoformat() if end_datetime else None,
                },
                "repositories": repositories,
                "total_repositories": len(repositories),
            }
    except Exception as ex:
        LOGGER.exception("Failed to fetch repository statistics from database")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch repository statistics: {ex!s}",
        ) from ex


@FASTAPI_APP.get(
    "/api/metrics/summary",
    operation_id="get_metrics_summary",
    dependencies=[Depends(require_metrics_server_enabled)],
)
async def get_metrics_summary(
    start_time: str | None = Query(
        default=None, description="Start time in ISO 8601 format (e.g., 2024-01-01T00:00:00Z)"
    ),
    end_time: str | None = Query(default=None, description="End time in ISO 8601 format (e.g., 2024-01-31T23:59:59Z)"),
) -> dict[str, Any]:
    """Get overall metrics summary for webhook processing.

    Provides high-level overview of webhook processing metrics including total events,
    performance statistics, success rates, and top repositories. Essential for operational
    dashboards, executive reporting, and system health monitoring.

    **Primary Use Cases:**
    - Generate executive dashboards and summary reports
    - Monitor overall system health and performance
    - Track webhook processing trends over time
    - Identify system-wide performance issues
    - Analyze API usage patterns across all repositories
    - Quick health check for webhook processing system

    **Parameters:**
    - `start_time` (str, optional): Start of time range in ISO 8601 format.
      Example: "2024-01-01T00:00:00Z"
      Default: No time filter (all-time stats)
    - `end_time` (str, optional): End of time range in ISO 8601 format.
      Example: "2024-01-31T23:59:59Z"
      Default: No time filter (up to current time)

    **Return Structure:**
    ```json
    {
      "time_range": {
        "start_time": "2024-01-01T00:00:00Z",
        "end_time": "2024-01-31T23:59:59Z"
      },
      "summary": {
        "total_events": 8745,
        "successful_events": 8423,
        "failed_events": 322,
        "success_rate": 96.32,
        "avg_processing_time_ms": 5834,
        "median_processing_time_ms": 4521,
        "p95_processing_time_ms": 14234,
        "max_processing_time_ms": 52134,
        "total_api_calls": 104940,
        "avg_api_calls_per_event": 12.0,
        "total_token_spend": 104940
      },
      "top_repositories": [
        {
          "repository": "myakove/high-traffic-repo",
          "total_events": 3456,
          "success_rate": 98.5
        },
        {
          "repository": "myakove/medium-traffic-repo",
          "total_events": 2134,
          "success_rate": 95.2
        },
        {
          "repository": "myakove/low-traffic-repo",
          "total_events": 856,
          "success_rate": 97.8
        }
      ],
      "event_type_distribution": {
        "pull_request": 4523,
        "issue_comment": 2134,
        "check_run": 1234,
        "push": 854
      },
      "hourly_event_rate": 12.3,
      "daily_event_rate": 295.4
    }
    ```

    **Metrics Explained:**
    - `total_events`: Total webhook events processed in time range
    - `successful_events`: Events that completed successfully
    - `failed_events`: Events that failed or partially failed
    - `success_rate`: Overall success percentage (0-100)
    - `avg_processing_time_ms`: Average processing duration across all events
    - `median_processing_time_ms`: Median processing duration (50th percentile)
    - `p95_processing_time_ms`: 95th percentile processing time (SLA metric)
    - `max_processing_time_ms`: Maximum processing time (worst case scenario)
    - `total_api_calls`: Total GitHub API calls made across all events
    - `avg_api_calls_per_event`: Average API calls per webhook event
    - `total_token_spend`: Total rate limit tokens consumed
    - `top_repositories`: Top 10 repositories by event volume
    - `event_type_distribution`: Event count breakdown by type
    - `hourly_event_rate`: Average events per hour in time range
    - `daily_event_rate`: Average events per day in time range

    **Common Analysis Scenarios:**
    - Daily summary: `start_time=<today>&end_time=<now>`
    - Weekly trends: `start_time=<week_start>&end_time=<week_end>`
    - Monthly reporting: `start_time=2024-01-01&end_time=2024-01-31`
    - System health check: No time filters (all-time stats)

    **Error Conditions:**
    - 400: Invalid datetime format in start_time/end_time parameters
    - 404: Metrics server disabled (ENABLE_METRICS_SERVER=false)
    - 500: Database connection errors or query failures

    **AI Agent Usage Examples:**
    - "Show overall metrics summary for last month for executive report"
    - "Get webhook processing health metrics to check system status"
    - "Analyze event type distribution to understand webhook traffic patterns"
    - "Review top repositories by event volume to identify high-traffic sources"

    **Performance Notes:**
    - Summary computed in real-time from webhooks table
    - Optimized queries using indexed columns (created_at, repository, event_type)
    - Large date ranges may increase query time
    - Consider caching for frequently accessed time ranges
    """
    # Validate database manager is available
    if db_manager is None:
        LOGGER.error("Database manager not initialized - metrics server may not be properly configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Metrics database not available",
        )

    # Parse datetime strings
    start_datetime = parse_datetime_string(start_time, "start_time")
    end_datetime = parse_datetime_string(end_time, "end_time")

    # Build query with time filters
    where_clause = "WHERE 1=1"
    params: list[Any] = []
    param_idx = 1

    if start_datetime:
        where_clause += f" AND created_at >= ${param_idx}"
        params.append(start_datetime)
        param_idx += 1

    if end_datetime:
        where_clause += f" AND created_at <= ${param_idx}"
        params.append(end_datetime)
        param_idx += 1

    # Main summary query
    summary_query = f"""
        SELECT
            COUNT(*) as total_events,
            COUNT(*) FILTER (WHERE status = 'success') as successful_events,
            COUNT(*) FILTER (WHERE status IN ('error', 'partial')) as failed_events,
            ROUND(
                (COUNT(*) FILTER (WHERE status = 'success')::numeric / NULLIF(COUNT(*), 0)::numeric * 100)::numeric,
                2
            ) as success_rate,
            ROUND(AVG(duration_ms)) as avg_processing_time_ms,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_ms) as median_processing_time_ms,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) as p95_processing_time_ms,
            MAX(duration_ms) as max_processing_time_ms,
            SUM(api_calls_count) as total_api_calls,
            ROUND(AVG(api_calls_count), 2) as avg_api_calls_per_event,
            SUM(token_spend) as total_token_spend
        FROM webhooks
        {where_clause}
    """

    # Top repositories query
    top_repos_query = f"""
        SELECT
            repository,
            COUNT(*) as total_events,
            ROUND(
                (COUNT(*) FILTER (WHERE status = 'success')::numeric / COUNT(*)::numeric * 100)::numeric,
                2
            ) as success_rate
        FROM webhooks
        {where_clause}
        GROUP BY repository
        ORDER BY total_events DESC
        LIMIT 10
    """

    # Event type distribution query
    event_type_query = f"""
        SELECT
            event_type,
            COUNT(*) as event_count
        FROM webhooks
        {where_clause}
        GROUP BY event_type
        ORDER BY event_count DESC
    """

    # Time range for rate calculations
    time_range_query = f"""
        SELECT
            MIN(created_at) as first_event_time,
            MAX(created_at) as last_event_time
        FROM webhooks
        {where_clause}
    """

    try:
        # Validate pool is initialized
        if db_manager.pool is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database pool not initialized",
            )

        async with db_manager.pool.acquire() as conn:
            # Execute all queries in parallel
            summary_row = await conn.fetchrow(summary_query, *params)
            top_repos_rows = await conn.fetch(top_repos_query, *params)
            event_type_rows = await conn.fetch(event_type_query, *params)
            time_range_row = await conn.fetchrow(time_range_query, *params)

            # Process summary metrics
            total_events = summary_row["total_events"] or 0
            summary = {
                "total_events": total_events,
                "successful_events": summary_row["successful_events"] or 0,
                "failed_events": summary_row["failed_events"] or 0,
                "success_rate": float(summary_row["success_rate"]) if summary_row["success_rate"] is not None else 0.0,
                "avg_processing_time_ms": int(summary_row["avg_processing_time_ms"])
                if summary_row["avg_processing_time_ms"] is not None
                else 0,
                "median_processing_time_ms": int(summary_row["median_processing_time_ms"])
                if summary_row["median_processing_time_ms"] is not None
                else 0,
                "p95_processing_time_ms": int(summary_row["p95_processing_time_ms"])
                if summary_row["p95_processing_time_ms"] is not None
                else 0,
                "max_processing_time_ms": summary_row["max_processing_time_ms"] or 0,
                "total_api_calls": summary_row["total_api_calls"] or 0,
                "avg_api_calls_per_event": float(summary_row["avg_api_calls_per_event"])
                if summary_row["avg_api_calls_per_event"] is not None
                else 0.0,
                "total_token_spend": summary_row["total_token_spend"] or 0,
            }

            # Process top repositories
            top_repositories = [
                {
                    "repository": row["repository"],
                    "total_events": row["total_events"],
                    "success_rate": float(row["success_rate"]) if row["success_rate"] is not None else 0.0,
                }
                for row in top_repos_rows
            ]

            # Process event type distribution
            event_type_distribution = {row["event_type"]: row["event_count"] for row in event_type_rows}

            # Calculate event rates
            hourly_event_rate = 0.0
            daily_event_rate = 0.0
            if time_range_row and time_range_row["first_event_time"] and time_range_row["last_event_time"]:
                time_diff = time_range_row["last_event_time"] - time_range_row["first_event_time"]
                total_hours = max(time_diff.total_seconds() / 3600, 1)  # Avoid division by zero
                total_days = max(time_diff.total_seconds() / 86400, 1)  # Avoid division by zero
                hourly_event_rate = round(total_events / total_hours, 2)
                daily_event_rate = round(total_events / total_days, 2)

            return {
                "time_range": {
                    "start_time": start_datetime.isoformat() if start_datetime else None,
                    "end_time": end_datetime.isoformat() if end_datetime else None,
                },
                "summary": summary,
                "top_repositories": top_repositories,
                "event_type_distribution": event_type_distribution,
                "hourly_event_rate": hourly_event_rate,
                "daily_event_rate": daily_event_rate,
            }
    except Exception as ex:
        LOGGER.exception("Failed to fetch metrics summary from database")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch metrics summary: {ex!s}",
        ) from ex


# MCP Integration - Only register if ENABLE_MCP_SERVER=true
if MCP_SERVER_ENABLED:
    # Create MCP instance with the main app
    # NOTE: No authentication configured - MCP server runs without auth
    # ⚠️ SECURITY WARNING: Deploy only on trusted networks (VPN, internal)
    # Never expose to public internet - use reverse proxy with auth for external access
    mcp = FastApiMCP(FASTAPI_APP, exclude_tags=["mcp_exclude"])

    # Create stateless HTTP transport to avoid session management issues
    # Override with stateless session manager
    http_transport = FastApiHttpSessionManager(
        mcp_server=mcp.server,
        event_store=None,  # No event store needed for stateless mode
        json_response=True,
    )
    # Manually patch to use stateless mode
    http_transport._session_manager = None  # Force recreation with stateless=True

    # Register the HTTP endpoint manually
    @FASTAPI_APP.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False, operation_id="mcp_http")
    async def handle_mcp_streamable_http(request: Request) -> Response:
        # Session manager is initialized in lifespan
        if http_transport is None or http_transport._session_manager is None:
            LOGGER.error("MCP session manager not initialized")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="MCP server not initialized")

        return await http_transport.handle_fastapi_request(request)

    LOGGER.info("MCP integration initialized successfully (no authentication configured)")
    LOGGER.debug("MCP HTTP endpoint mounted at: /mcp")
