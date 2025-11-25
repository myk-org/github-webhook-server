import asyncio
import base64
import ipaddress
import json
import logging
import math
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
)
from fastapi import (
    status as http_status,
)
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Import for MCP integration
from fastapi_mcp import FastApiMCP
from fastapi_mcp.transport.http import FastApiHttpSessionManager
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.datastructures import Headers

from webhook_server.libs.config import Config
from webhook_server.libs.database import DatabaseManager
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
from webhook_server.web.metrics_dashboard import MetricsDashboardController

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
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Log server is disabled. Set ENABLE_LOG_SERVER=true to enable.",
        )


def require_metrics_server_enabled() -> None:
    """Dependency to ensure metrics server is enabled before accessing metrics APIs."""
    if not METRICS_SERVER_ENABLED:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Metrics server is disabled. Set ENABLE_METRICS_SERVER=true to enable.",
        )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    global _lifespan_http_client, ALLOWED_IPS, http_transport, mcp, db_manager
    global metrics_tracker, _log_viewer_controller_singleton, _metrics_dashboard_controller_singleton, _background_tasks
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
            if metrics_file_logger.handlers and not metrics_logger.handlers:
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
            metrics_logger = logging.getLogger("webhook_server.metrics")
            db_manager = DatabaseManager(config, metrics_logger)

            await db_manager.connect()
            LOGGER.info("Metrics Server database manager initialized successfully")

            # Initialize metrics tracker
            metrics_tracker = MetricsTracker(db_manager, metrics_logger)
            LOGGER.info("Metrics tracker initialized successfully")

        yield

    except Exception:
        LOGGER.exception("Application failed during lifespan management")
        raise

    finally:
        # Shutdown LogViewerController singleton and close WebSocket connections
        if _log_viewer_controller_singleton is not None:
            await _log_viewer_controller_singleton.shutdown()
            LOGGER.debug("LogViewerController singleton shutdown complete")

        # Shutdown MetricsDashboardController singleton and close WebSocket connections
        if _metrics_dashboard_controller_singleton is not None:
            await _metrics_dashboard_controller_singleton.shutdown()
            LOGGER.debug("MetricsDashboardController singleton shutdown complete")

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

        # Disconnect database managers if they exist (after background tasks complete)
        if db_manager is not None:
            await db_manager.disconnect()
            LOGGER.debug("Database manager disconnected")
            LOGGER.info("Metrics Server database manager shutdown complete")

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

        # Extract PR number from multiple sources depending on event type
        _pr_number = _hook_data.get("pull_request", {}).get("number")  # pull_request events

        # For issue_comment events on PRs: issue has pull_request key
        if not _pr_number and "issue" in _hook_data:
            issue = _hook_data["issue"]
            # If issue has pull_request key, it's actually a PR comment
            if "pull_request" in issue:
                _pr_number = issue.get("number")

        # For check_run events: extract from pull_requests array
        if not _pr_number and "check_run" in _hook_data:
            check_run = _hook_data["check_run"]
            pull_requests = check_run.get("pull_requests", [])
            if pull_requests and len(pull_requests) > 0:
                _pr_number = pull_requests[0].get("number")

        async def track_metrics_safe(
            status: str,
            error_message: str | None = None,
            api_calls_count: int = 0,
            token_spend: int = 0,
            token_remaining: int = 0,
            metrics_available: bool = True,
        ) -> None:
            """Track webhook metrics in best-effort manner - never fail webhook processing.

            Args:
                status: Processing status (success, error, partial)
                error_message: Optional error message for failures
                api_calls_count: Number of GitHub API calls made
                token_spend: Rate limit tokens consumed
                token_remaining: Remaining rate limit tokens
                metrics_available: Whether API metrics are available (False = no tracking)
            """
            if not (METRICS_SERVER_ENABLED and metrics_tracker):
                return

            try:
                processing_time = (datetime.now(UTC) - start_time).total_seconds() * 1000
                await metrics_tracker.track_webhook_event(
                    delivery_id=_delivery_id,
                    repository=_repository,
                    event_type=_event_type,
                    action=_action,
                    sender=_sender,
                    payload=_hook_data,
                    processing_time_ms=int(processing_time),
                    status=status,
                    pr_number=_pr_number,
                    error_message=error_message,
                    api_calls_count=api_calls_count,
                    token_spend=token_spend,
                    token_remaining=token_remaining,
                    metrics_available=metrics_available,
                )
            except Exception:
                # Metrics tracking failures should never affect webhook processing
                # Log the failure but don't re-raise
                _logger.exception(f"{_log_context} Metrics tracking failed (non-critical)")

        try:
            # Initialize GithubWebhook inside background task to avoid blocking webhook response
            _api: GithubWebhook = GithubWebhook(hook_data=_hook_data, headers=_headers, logger=_logger)
            try:
                await _api.process()

                # Extract API usage metrics for database tracking (defensive - use .get() for safety)
                api_metrics = _api.get_api_metrics()

                # Track successful webhook event with API metrics (best-effort)
                # Use .get() with defaults since metrics tracking is best-effort and shouldn't break on partial dict
                await track_metrics_safe(
                    status="success",
                    api_calls_count=int(api_metrics.get("api_calls_count", 0)),
                    token_spend=int(api_metrics.get("token_spend", 0)),
                    token_remaining=int(api_metrics.get("token_remaining", 0)),
                    metrics_available=bool(api_metrics.get("metrics_available", False)),
                )
            finally:
                await _api.cleanup()
        except RepositoryNotFoundInConfigError as ex:
            # Repository-specific error - not exceptional, log as error not exception
            _logger.error(f"{_log_context} Repository not found in configuration")

            # Track failed webhook event (best-effort)
            # Note: No API metrics available - error happened before GithubWebhook processing
            await track_metrics_safe(status="error", error_message=str(ex), metrics_available=False)
        except (httpx.ConnectError, httpx.RequestError, requests.exceptions.ConnectionError) as ex:
            # Network/connection errors - can be transient
            _logger.exception(f"{_log_context} API connection error - check network connectivity")

            # Track failed webhook event (best-effort)
            # Note: No API metrics available - error happened during GithubWebhook processing
            await track_metrics_safe(status="error", error_message=str(ex), metrics_available=False)
        except Exception as ex:
            # Catch-all for unexpected errors
            _logger.exception(f"{_log_context} Unexpected error in background webhook processing")

            # Track failed webhook event (best-effort)
            # Note: No API metrics available - error happened during GithubWebhook processing
            await track_metrics_safe(status="error", error_message=str(ex), metrics_available=False)

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
        status_code=http_status.HTTP_200_OK,
        content={
            "status": http_status.HTTP_200_OK,
            "message": "Webhook queued for processing",
            "delivery_id": delivery_id,
            "event_type": event_type,
        },
    )


# Module-level singleton instances
_log_viewer_controller_singleton: LogViewerController | None = None
_metrics_dashboard_controller_singleton: MetricsDashboardController | None = None


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


def get_metrics_dashboard_controller() -> MetricsDashboardController:
    """Dependency to provide a singleton MetricsDashboardController instance.

    Returns the same MetricsDashboardController instance across all requests to ensure
    proper WebSocket connection tracking and shared state management.

    Returns:
        MetricsDashboardController: The singleton instance
    """
    global _metrics_dashboard_controller_singleton
    if _metrics_dashboard_controller_singleton is None:
        # Metrics dashboard requires database manager and logger
        if db_manager is None:
            raise RuntimeError("Metrics database not available - metrics server not enabled")

        metrics_logger = logging.getLogger("webhook_server.metrics")
        _metrics_dashboard_controller_singleton = MetricsDashboardController(db_manager, metrics_logger)
    return _metrics_dashboard_controller_singleton


# Create dependency instance to avoid flake8 M511 warnings
metrics_dashboard_dependency = Depends(get_metrics_dashboard_controller)


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
        await websocket.close(code=http_status.WS_1008_POLICY_VIOLATION, reason="Log server is disabled")
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


# Metrics Dashboard Endpoints - Only register if ENABLE_METRICS_SERVER=true
if METRICS_SERVER_ENABLED:

    @FASTAPI_APP.get("/metrics", operation_id="get_metrics_dashboard_page", response_class=HTMLResponse)
    def get_metrics_dashboard_page(
        controller: MetricsDashboardController = metrics_dashboard_dependency,
    ) -> HTMLResponse:
        """Serve the metrics dashboard HTML page."""
        return controller.get_dashboard_page()

    @FASTAPI_APP.websocket("/metrics/ws")
    async def websocket_metrics_stream(
        websocket: WebSocket,
        repository: str | None = None,
        event_type: str | None = None,
        status: str | None = None,
    ) -> None:
        """Handle WebSocket connection for real-time metrics streaming."""
        # Check if metrics server is enabled (manual check since WebSocket doesn't support dependencies same way)
        if not METRICS_SERVER_ENABLED:
            await websocket.close(code=http_status.WS_1008_POLICY_VIOLATION, reason="Metrics server is disabled")
            return

        controller = get_metrics_dashboard_controller()
        await controller.handle_websocket(
            websocket=websocket,
            repository=repository,
            event_type=event_type,
            status=status,
        )


@FASTAPI_APP.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Serve favicon.ico to prevent 404 errors.

    Returns a minimal 1x1 transparent PNG as favicon to eliminate browser 404 errors
    without requiring an actual favicon file. This is a lightweight solution that
    satisfies browser favicon requests with minimal overhead.
    """
    # 1x1 transparent PNG (base64 encoded)
    transparent_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )

    return Response(content=transparent_png, media_type="image/x-icon")


# Metrics API Endpoints - Only functional if ENABLE_METRICS_SERVER=true (guarded by dependency)
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
    status: str | None = Query(default=None, description="Filter by status (success, error, partial)"),
    start_time: str | None = Query(
        default=None, description="Start time in ISO 8601 format (e.g., 2024-01-15T00:00:00Z)"
    ),
    end_time: str | None = Query(default=None, description="End time in ISO 8601 format (e.g., 2024-01-31T23:59:59Z)"),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=100, ge=1, le=1000, description="Items per page (1-1000)"),
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
    - `page` (int, default=1): Page number (1-indexed).
    - `page_size` (int, default=100): Items per page (1-1000).

    **Pagination:**
    - Response includes pagination metadata with total count, page info, and navigation flags
    - Use `page` and `page_size` to navigate through results
    - `has_next` and `has_prev` indicate if more pages are available

    **Return Structure:**
    ```json
    {
      "data": [
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
      "pagination": {
        "total": 1542,
        "page": 1,
        "page_size": 100,
        "total_pages": 16,
        "has_next": true,
        "has_prev": false
      }
    }
    ```

    **Common Filtering Scenarios:**
    - Recent errors: `status=error&start_time=2024-01-15T00:00:00Z`
    - Repository-specific events: `repository=owner/repo&page=1&page_size=50`
    - Event type analysis: `event_type=pull_request&start_time=2024-01-01T00:00:00Z&page=1&page_size=100`
    - Failed webhooks: `status=error&event_type=pull_request&page=1&page_size=100`

    **Note:** `page` is 1-indexed, and `page_size` is capped at 1000.

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
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
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

    if status:
        query += f" AND status = ${param_idx}"
        params.append(status)
        param_idx += 1

    if start_datetime:
        query += f" AND created_at >= ${param_idx}"
        params.append(start_datetime)
        param_idx += 1

    if end_datetime:
        query += f" AND created_at <= ${param_idx}"
        params.append(end_datetime)
        param_idx += 1

    # Calculate offset for pagination
    offset = (page - 1) * page_size

    # Get total count for pagination
    # Safe: query is built with parameterized WHERE clauses, no user input in SQL string
    count_query = f"SELECT COUNT(*) FROM ({query}) AS filtered"  # noqa: S608
    query += f" ORDER BY created_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
    params.extend([page_size, offset])

    try:
        # Get total count using DatabaseManager helper
        total_count = await db_manager.fetchval(count_query, *params[:-2])

        # Get paginated results using DatabaseManager helper
        rows = await db_manager.fetch(query, *params)

        events = [
            {
                "delivery_id": row["delivery_id"],
                "repository": row["repository"],
                "event_type": row["event_type"],
                "action": row["action"],
                "pr_number": row["pr_number"],
                "sender": row["sender"],
                "status": row["status"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "processed_at": row["processed_at"].isoformat() if row["processed_at"] else None,
                "duration_ms": row["duration_ms"],
                "api_calls_count": row["api_calls_count"],
                "token_spend": row["token_spend"],
                "token_remaining": row["token_remaining"],
                "error_message": row["error_message"],
            }
            for row in rows
        ]

        total_pages = math.ceil(total_count / page_size) if total_count > 0 else 0
        has_next = page < total_pages
        has_prev = page > 1

        return {
            "data": events,
            "pagination": {
                "total": total_count,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "has_next": has_next,
                "has_prev": has_prev,
            },
        }
    except HTTPException:
        raise
    except Exception as ex:
        LOGGER.exception("Failed to fetch webhook events from database")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch webhook events",
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
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
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
        # Fetch single row using DatabaseManager helper
        row = await db_manager.fetchrow(query, delivery_id)

        if not row:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
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
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "processed_at": row["processed_at"].isoformat() if row["processed_at"] else None,
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
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch webhook event",
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
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=10, ge=1, le=100, description="Items per page (1-100)"),
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
    - `page` (int, default=1): Page number (1-indexed)
    - `page_size` (int, default=10): Items per page (1-100)

    **Pagination:**
    - Response includes pagination metadata
    - `total`: Total number of repositories
    - `total_pages`: Total number of pages
    - `has_next`: Whether there's a next page
    - `has_prev`: Whether there's a previous page

    **Return Structure:**
    ```json
    {
      "time_range": {
        "start_time": "2024-01-01T00:00:00Z",
        "end_time": "2024-01-31T23:59:59Z"
      },
      "data": [
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
      "pagination": {
        "total": 150,
        "page": 1,
        "page_size": 10,
        "total_pages": 15,
        "has_next": true,
        "has_prev": false
      }
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
    - Monthly repository metrics: `start_time=2024-01-01&end_time=2024-01-31&page=1&page_size=10`
    - High-traffic repositories: Sort by `total_events` descending
    - Performance issues: Analyze `p95_processing_time_ms` and `max_processing_time_ms`
    - Error-prone repositories: Sort by `failed_events` descending or `success_rate` ascending
    - API usage optimization: Analyze `avg_api_calls_per_event` and `total_token_spend`

    **Note:** `page` is 1-indexed, and `page_size` is capped at 100 for this endpoint.

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
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
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

    # Calculate offset for pagination
    offset = (page - 1) * page_size

    # Count total repositories for pagination
    count_query = f"""
        SELECT COUNT(DISTINCT repository) as total
        FROM webhooks
        {where_clause}
    """  # noqa: S608

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
        LIMIT ${param_idx} OFFSET ${param_idx + 1}
    """  # noqa: S608
    params.extend([page_size, offset])

    try:
        # Get total count for pagination (params without LIMIT/OFFSET)
        total_count = await db_manager.fetchval(count_query, *params[:-2])

        # Fetch repository statistics using DatabaseManager helper
        rows = await db_manager.fetch(query, *params)

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

        total_pages = math.ceil(total_count / page_size) if total_count > 0 else 0
        has_next = page < total_pages
        has_prev = page > 1

        return {
            "time_range": {
                "start_time": start_datetime.isoformat() if start_datetime else None,
                "end_time": end_datetime.isoformat() if end_datetime else None,
            },
            "data": repositories,
            "pagination": {
                "total": total_count,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "has_next": has_next,
                "has_prev": has_prev,
            },
        }
    except HTTPException:
        raise
    except Exception as ex:
        LOGGER.exception("Failed to fetch repository statistics from database")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch repository statistics",
        ) from ex


@FASTAPI_APP.get(
    "/api/metrics/contributors",
    operation_id="get_metrics_contributors",
    dependencies=[Depends(require_metrics_server_enabled)],
)
async def get_metrics_contributors(
    start_time: str | None = Query(
        default=None, description="Start time in ISO 8601 format (e.g., 2024-01-01T00:00:00Z)"
    ),
    end_time: str | None = Query(default=None, description="End time in ISO 8601 format (e.g., 2024-01-31T23:59:59Z)"),
    user: str | None = Query(default=None, description="Filter by username"),
    repository: str | None = Query(default=None, description="Filter by repository (org/repo format)"),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=10, ge=1, le=100, description="Items per page (1-100)"),
) -> dict[str, Any]:
    """Get PR contributors statistics (creators, reviewers, approvers, LGTM).

    Analyzes webhook payloads to extract contributor activity including PR creation,
    code review, approval, and LGTM metrics. Essential for understanding team contributions
    and identifying active contributors.

    **Primary Use Cases:**
    - Track who is creating PRs and how many
    - Monitor code review participation
    - Identify approval patterns and bottlenecks
    - Track LGTM activity separate from approvals
    - Measure team collaboration and engagement
    - Generate contributor leaderboards

    **Parameters:**
    - `start_time` (str, optional): Start of time range in ISO 8601 format
    - `end_time` (str, optional): End of time range in ISO 8601 format
    - `user` (str, optional): Filter by username
    - `repository` (str, optional): Filter by repository (org/repo format)
    - `page` (int, default=1): Page number (1-indexed)
    - `page_size` (int, default=10): Items per page (1-100)

    **Pagination:**
    - Each category (pr_creators, pr_reviewers, pr_approvers, pr_lgtm) includes pagination metadata
    - `total`: Total number of contributors in this category
    - `total_pages`: Total number of pages
    - `has_next`: Whether there's a next page
    - `has_prev`: Whether there's a previous page

    **Return Structure:**
    ```json
    {
      "time_range": {
        "start_time": "2024-01-01T00:00:00Z",
        "end_time": "2024-01-31T23:59:59Z"
      },
      "pr_creators": {
        "data": [
          {
            "user": "john-doe",
            "total_prs": 45,
            "merged_prs": 42,
            "closed_prs": 3,
            "avg_commits_per_pr": 3.0
          }
        ],
        "pagination": {
          "total": 150,
          "page": 1,
          "page_size": 10,
          "total_pages": 15,
          "has_next": true,
          "has_prev": false
        }
      },
      "pr_reviewers": {
        "data": [
          {
            "user": "jane-smith",
            "total_reviews": 78,
            "prs_reviewed": 65,
            "avg_reviews_per_pr": 1.2
          }
        ],
        "pagination": {
          "total": 120,
          "page": 1,
          "page_size": 10,
          "total_pages": 12,
          "has_next": true,
          "has_prev": false
        }
      },
      "pr_approvers": {
        "data": [
          {
            "user": "bob-wilson",
            "total_approvals": 56,
            "prs_approved": 54
          }
        ],
        "pagination": {
          "total": 95,
          "page": 1,
          "page_size": 10,
          "total_pages": 10,
          "has_next": true,
          "has_prev": false
        }
      },
      "pr_lgtm": {
        "data": [
          {
            "user": "alice-jones",
            "total_lgtm": 42,
            "prs_lgtm": 40
          }
        ],
        "pagination": {
          "total": 78,
          "page": 1,
          "page_size": 10,
          "total_pages": 8,
          "has_next": true,
          "has_prev": false
        }
      }
    }
    ```

    **Notes:**
    - PR Approvers: Tracks /approve commands (approved-<username> labels)
    - PR LGTM: Tracks /lgtm commands (lgtm-<username> labels)
    - LGTM is separate from approvals in this workflow

    **Errors:**
    - 500: Database connection error or metrics server disabled
    """
    if db_manager is None:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Metrics database not available",
        )

    start_datetime = parse_datetime_string(start_time, "start_time")
    end_datetime = parse_datetime_string(end_time, "end_time")

    # Build filter clause with time, user, and repository filters
    time_filter = ""
    params: list[Any] = []
    param_count = 0

    if start_datetime:
        param_count += 1
        time_filter += f" AND created_at >= ${param_count}"
        params.append(start_datetime)

    if end_datetime:
        param_count += 1
        time_filter += f" AND created_at <= ${param_count}"
        params.append(end_datetime)

    # Add repository filter if provided
    repository_filter = ""
    if repository:
        param_count += 1
        repository_filter = f" AND repository = ${param_count}"
        params.append(repository)

    # Build category-specific user filters to align with per-category "user" semantics
    # PR Creators: user = COALESCE(CASE event_type WHEN 'pull_request'/'pull_request_review'/'issue_comment'..., sender)
    # PR Reviewers: user = sender
    # PR Approvers: user = SUBSTRING(payload->'label'->>'name' FROM 10)
    # PR LGTM: user = SUBSTRING(payload->'label'->>'name' FROM 6)
    user_filter_reviewers = ""
    user_filter_approvers = ""
    user_filter_lgtm = ""

    if user:
        param_count += 1
        user_param_idx = param_count
        params.append(user)

        # PR Reviewers: filter on sender (correct as-is)
        user_filter_reviewers = f" AND sender = ${user_param_idx}"
        # PR Approvers: filter on extracted username from 'approved-<username>' label
        user_filter_approvers = f" AND SUBSTRING(payload->'label'->>'name' FROM 10) = ${user_param_idx}"
        # PR LGTM: filter on extracted username from 'lgtm-<username>' label
        user_filter_lgtm = f" AND SUBSTRING(payload->'label'->>'name' FROM 6) = ${user_param_idx}"

    # Calculate offset for pagination
    offset = (page - 1) * page_size

    # Add page_size and offset to params
    param_count += 1
    page_size_param = param_count
    param_count += 1
    offset_param = param_count
    params.extend([page_size, offset])

    # Count query for PR Creators
    pr_creators_count_query = f"""
        WITH pr_creators AS (
            SELECT DISTINCT ON (pr_number)
                pr_number,
                CASE event_type
                    WHEN 'pull_request' THEN payload->'pull_request'->'user'->>'login'
                    WHEN 'pull_request_review' THEN payload->'pull_request'->'user'->>'login'
                    WHEN 'pull_request_review_comment'
                        THEN payload->'pull_request'->'user'->>'login'
                    WHEN 'issue_comment' THEN COALESCE(
                        payload->'pull_request'->'user'->>'login',
                        payload->'issue'->'user'->>'login'
                    )
                END as pr_creator
            FROM webhooks
            WHERE pr_number IS NOT NULL
              AND event_type IN (
                  'pull_request',
                  'pull_request_review',
                  'pull_request_review_comment',
                  'issue_comment'
              )
              {time_filter}
              {repository_filter}
            ORDER BY pr_number, created_at ASC
        )
        SELECT COUNT(DISTINCT pr_creator) as total
        FROM pr_creators
        WHERE pr_creator IS NOT NULL{f" AND pr_creator = ${user_param_idx}" if user else ""}
    """  # noqa: S608

    # Query PR Creators (from any event with pr_number)
    pr_creators_query = f"""
        WITH pr_creators AS (
            SELECT DISTINCT ON (pr_number)
                pr_number,
                CASE event_type
                    WHEN 'pull_request' THEN payload->'pull_request'->'user'->>'login'
                    WHEN 'pull_request_review' THEN payload->'pull_request'->'user'->>'login'
                    WHEN 'pull_request_review_comment'
                        THEN payload->'pull_request'->'user'->>'login'
                    WHEN 'issue_comment' THEN COALESCE(
                        payload->'pull_request'->'user'->>'login',
                        payload->'issue'->'user'->>'login'
                    )
                END as pr_creator
            FROM webhooks
            WHERE pr_number IS NOT NULL
              AND event_type IN (
                  'pull_request',
                  'pull_request_review',
                  'pull_request_review_comment',
                  'issue_comment'
              )
              {time_filter}
              {repository_filter}
            ORDER BY pr_number, created_at ASC
        ),
        user_prs AS (
            SELECT
                pc.pr_creator,
                w.pr_number,
                COALESCE((w.payload->'pull_request'->>'commits')::int, 0) as commits,
                (w.payload->'pull_request'->>'merged' = 'true') as is_merged,
                (
                    w.payload->'pull_request'->>'state' = 'closed'
                    AND w.payload->'pull_request'->>'merged' = 'false'
                ) as is_closed
            FROM webhooks w
            INNER JOIN pr_creators pc ON w.pr_number = pc.pr_number
            WHERE w.pr_number IS NOT NULL
              {time_filter}
              {repository_filter}
        )
        SELECT
            pr_creator as user,
            COUNT(DISTINCT pr_number) as total_prs,
            COUNT(DISTINCT pr_number) FILTER (WHERE is_merged) as merged_prs,
            COUNT(DISTINCT pr_number) FILTER (WHERE is_closed) as closed_prs,
            ROUND(AVG(max_commits), 1) as avg_commits
        FROM (
            SELECT
                pr_creator,
                pr_number,
                MAX(commits) as max_commits,
                BOOL_OR(is_merged) as is_merged,
                BOOL_OR(is_closed) as is_closed
            FROM user_prs
            WHERE pr_creator IS NOT NULL
            GROUP BY pr_creator, pr_number
        ) pr_stats
        WHERE 1=1{f" AND pr_creator = ${user_param_idx}" if user else ""}
        GROUP BY pr_creator
        ORDER BY total_prs DESC
        LIMIT ${page_size_param} OFFSET ${offset_param}
    """  # noqa: S608

    # Count query for PR Reviewers
    pr_reviewers_count_query = f"""
        SELECT COUNT(DISTINCT sender) as total
        FROM webhooks
        WHERE event_type = 'pull_request_review'
          AND action = 'submitted'
          AND sender != payload->'pull_request'->'user'->>'login'
          {time_filter}
          {user_filter_reviewers}
          {repository_filter}
    """  # noqa: S608

    # Query PR Reviewers (from pull_request_review events)
    pr_reviewers_query = f"""
        SELECT
            sender as user,
            COUNT(*) as total_reviews,
            COUNT(DISTINCT pr_number) as prs_reviewed
        FROM webhooks
        WHERE event_type = 'pull_request_review'
          AND action = 'submitted'
          AND sender != payload->'pull_request'->'user'->>'login'
          {time_filter}
          {user_filter_reviewers}
          {repository_filter}
        GROUP BY sender
        ORDER BY total_reviews DESC
        LIMIT ${page_size_param} OFFSET ${offset_param}
    """  # noqa: S608

    # Count query for PR Approvers
    pr_approvers_count_query = f"""
        SELECT COUNT(DISTINCT SUBSTRING(payload->'label'->>'name' FROM 10)) as total
        FROM webhooks
        WHERE event_type = 'pull_request'
          AND action = 'labeled'
          AND payload->'label'->>'name' LIKE 'approved-%'
          {time_filter}
          {user_filter_approvers}
          {repository_filter}
    """  # noqa: S608

    # Query PR Approvers (from pull_request labeled events with 'approved-' prefix only)
    # Custom approval workflow: /approve comment triggers 'approved-<username>' label
    # Note: LGTM is separate from approval - tracked separately
    pr_approvers_query = f"""
        SELECT
            SUBSTRING(payload->'label'->>'name' FROM 10) as user,
            COUNT(*) as total_approvals,
            COUNT(DISTINCT pr_number) as prs_approved
        FROM webhooks
        WHERE event_type = 'pull_request'
          AND action = 'labeled'
          AND payload->'label'->>'name' LIKE 'approved-%'
          {time_filter}
          {user_filter_approvers}
          {repository_filter}
        GROUP BY SUBSTRING(payload->'label'->>'name' FROM 10)
        ORDER BY total_approvals DESC
        LIMIT ${page_size_param} OFFSET ${offset_param}
    """  # noqa: S608

    # Count query for LGTM
    pr_lgtm_count_query = f"""
        SELECT COUNT(DISTINCT SUBSTRING(payload->'label'->>'name' FROM 6)) as total
        FROM webhooks
        WHERE event_type = 'pull_request'
          AND action = 'labeled'
          AND payload->'label'->>'name' LIKE 'lgtm-%'
          {time_filter}
          {user_filter_lgtm}
          {repository_filter}
    """  # noqa: S608

    # Query LGTM (from pull_request labeled events with 'lgtm-' prefix)
    # Custom LGTM workflow: /lgtm comment triggers 'lgtm-<username>' label
    pr_lgtm_query = f"""
        SELECT
            SUBSTRING(payload->'label'->>'name' FROM 6) as user,
            COUNT(*) as total_lgtm,
            COUNT(DISTINCT pr_number) as prs_lgtm
        FROM webhooks
        WHERE event_type = 'pull_request'
          AND action = 'labeled'
          AND payload->'label'->>'name' LIKE 'lgtm-%'
          {time_filter}
          {user_filter_lgtm}
          {repository_filter}
        GROUP BY SUBSTRING(payload->'label'->>'name' FROM 6)
        ORDER BY total_lgtm DESC
        LIMIT ${page_size_param} OFFSET ${offset_param}
    """  # noqa: S608

    try:
        # Execute all count queries in parallel (params without LIMIT/OFFSET)
        params_without_pagination = params[:-2]
        (
            pr_creators_total,
            pr_reviewers_total,
            pr_approvers_total,
            pr_lgtm_total,
        ) = await asyncio.gather(
            db_manager.fetchval(pr_creators_count_query, *params_without_pagination),
            db_manager.fetchval(pr_reviewers_count_query, *params_without_pagination),
            db_manager.fetchval(pr_approvers_count_query, *params_without_pagination),
            db_manager.fetchval(pr_lgtm_count_query, *params_without_pagination),
        )

        # Execute all data queries in parallel for better performance
        pr_creators_rows, pr_reviewers_rows, pr_approvers_rows, pr_lgtm_rows = await asyncio.gather(
            db_manager.fetch(pr_creators_query, *params),
            db_manager.fetch(pr_reviewers_query, *params),
            db_manager.fetch(pr_approvers_query, *params),
            db_manager.fetch(pr_lgtm_query, *params),
        )

        # Format PR creators
        pr_creators = [
            {
                "user": row["user"],
                "total_prs": row["total_prs"],
                "merged_prs": row["merged_prs"] or 0,
                "closed_prs": row["closed_prs"] or 0,
                "avg_commits_per_pr": round(row["avg_commits"] or 0, 1),
            }
            for row in pr_creators_rows
        ]

        # Format PR reviewers
        pr_reviewers = [
            {
                "user": row["user"],
                "total_reviews": row["total_reviews"],
                "prs_reviewed": row["prs_reviewed"],
                "avg_reviews_per_pr": round(row["total_reviews"] / max(row["prs_reviewed"], 1), 2),
            }
            for row in pr_reviewers_rows
        ]

        # Format PR approvers
        pr_approvers = [
            {
                "user": row["user"],
                "total_approvals": row["total_approvals"],
                "prs_approved": row["prs_approved"],
            }
            for row in pr_approvers_rows
        ]

        # Format LGTM
        pr_lgtm = [
            {
                "user": row["user"],
                "total_lgtm": row["total_lgtm"],
                "prs_lgtm": row["prs_lgtm"],
            }
            for row in pr_lgtm_rows
        ]

        # Calculate pagination metadata for each category
        total_pages_creators = math.ceil(pr_creators_total / page_size) if pr_creators_total > 0 else 0
        total_pages_reviewers = math.ceil(pr_reviewers_total / page_size) if pr_reviewers_total > 0 else 0
        total_pages_approvers = math.ceil(pr_approvers_total / page_size) if pr_approvers_total > 0 else 0
        total_pages_lgtm = math.ceil(pr_lgtm_total / page_size) if pr_lgtm_total > 0 else 0

        return {
            "time_range": {
                "start_time": start_datetime.isoformat() if start_datetime else None,
                "end_time": end_datetime.isoformat() if end_datetime else None,
            },
            "pr_creators": {
                "data": pr_creators,
                "pagination": {
                    "total": pr_creators_total,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages_creators,
                    "has_next": page < total_pages_creators,
                    "has_prev": page > 1,
                },
            },
            "pr_reviewers": {
                "data": pr_reviewers,
                "pagination": {
                    "total": pr_reviewers_total,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages_reviewers,
                    "has_next": page < total_pages_reviewers,
                    "has_prev": page > 1,
                },
            },
            "pr_approvers": {
                "data": pr_approvers,
                "pagination": {
                    "total": pr_approvers_total,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages_approvers,
                    "has_next": page < total_pages_approvers,
                    "has_prev": page > 1,
                },
            },
            "pr_lgtm": {
                "data": pr_lgtm,
                "pagination": {
                    "total": pr_lgtm_total,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages_lgtm,
                    "has_next": page < total_pages_lgtm,
                    "has_prev": page > 1,
                },
            },
        }
    except HTTPException:
        raise
    except Exception:
        LOGGER.exception("Failed to fetch contributor metrics from database")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch contributor metrics",
        ) from None


@FASTAPI_APP.get(
    "/api/metrics/user-prs",
    operation_id="get_user_pull_requests",
    dependencies=[Depends(require_metrics_server_enabled)],
)
async def get_user_pull_requests(
    user: str | None = Query(None, description="GitHub username (optional - shows all PRs if not specified)"),
    repository: str | None = Query(None, description="Filter by repository (org/repo)"),
    start_time: str | None = Query(
        default=None, description="Start time in ISO 8601 format (e.g., 2024-01-01T00:00:00Z)"
    ),
    end_time: str | None = Query(default=None, description="End time in ISO 8601 format (e.g., 2024-01-31T23:59:59Z)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page"),
) -> dict[str, Any]:
    """Get pull requests with optional user filtering and commit details.

    Retrieves pull requests with pagination. Can show all PRs or filter by user.
    Includes detailed commit information for each PR. Supports filtering by repository
    and time range.

    **Primary Use Cases:**
    - View all PRs across repositories with pagination
    - Filter PRs by specific user to track contributions
    - Analyze commit patterns per PR
    - Monitor PR lifecycle (created, merged, closed)
    - Filter PR activity by repository or time period

    **Parameters:**
    - `user` (str, optional): GitHub username to filter by (shows all PRs if not specified)
    - `repository` (str, optional): Filter by specific repository (format: org/repo)
    - `start_time` (str, optional): Start of time range in ISO 8601 format
    - `end_time` (str, optional): End of time range in ISO 8601 format
    - `page` (int, optional): Page number for pagination (default: 1)
    - `page_size` (int, optional): Items per page, 1-100 (default: 10)

    **Return Structure:**
    ```json
    {
      "data": [
        {
          "pr_number": 123,
          "title": "Add feature X",
          "repository": "org/repo1",
          "state": "closed",
          "merged": true,
          "url": "https://github.com/org/repo1/pull/123",
          "created_at": "2024-11-20T10:00:00Z",
          "updated_at": "2024-11-21T15:30:00Z",
          "commits_count": 5,
          "head_sha": "abc123def456"  # pragma: allowlist secret
        }
      ],
      "pagination": {
        "total": 45,
        "page": 1,
        "page_size": 10,
        "total_pages": 5,
        "has_next": true,
        "has_prev": false
      }
    }
    ```

    **Errors:**
    - 500: Database connection error or metrics server disabled
    """
    if db_manager is None:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Metrics database not available",
        )

    # Parse datetime strings
    start_datetime = parse_datetime_string(start_time, "start_time")
    end_datetime = parse_datetime_string(end_time, "end_time")

    # Build filter clauses
    filters = []
    params: list[Any] = []
    param_count = 0

    # Add user filter if provided
    if user and user.strip():
        param_count += 1
        filters.append(f"(payload->'pull_request'->'user'->>'login' = ${param_count} OR sender = ${param_count})")
        params.append(user.strip())

    if start_datetime:
        param_count += 1
        filters.append(f"created_at >= ${param_count}")
        params.append(start_datetime)

    if end_datetime:
        param_count += 1
        filters.append(f"created_at <= ${param_count}")
        params.append(end_datetime)

    if repository:
        param_count += 1
        filters.append(f"repository = ${param_count}")
        params.append(repository)

    where_clause = " AND ".join(filters) if filters else "1=1"

    # Count total matching PRs
    count_query = f"""
        SELECT COUNT(DISTINCT (payload->'pull_request'->>'number')::int) as total
        FROM webhooks
        WHERE event_type = 'pull_request'
          AND {where_clause}
    """  # noqa: S608

    # Calculate pagination
    offset = (page - 1) * page_size
    param_count += 1
    limit_param_idx = param_count
    param_count += 1
    offset_param_idx = param_count

    # Query for PR data with pagination
    data_query = f"""
        SELECT DISTINCT ON (pr_number)
            (payload->'pull_request'->>'number')::int as pr_number,
            payload->'pull_request'->>'title' as title,
            repository,
            payload->'pull_request'->>'state' as state,
            (payload->'pull_request'->>'merged')::boolean as merged,
            payload->'pull_request'->>'html_url' as url,
            payload->'pull_request'->>'created_at' as created_at,
            payload->'pull_request'->>'updated_at' as updated_at,
            (payload->'pull_request'->>'commits')::int as commits_count,
            payload->'pull_request'->'head'->>'sha' as head_sha
        FROM webhooks
        WHERE event_type = 'pull_request'
          AND {where_clause}
        ORDER BY pr_number DESC, created_at DESC
        LIMIT ${limit_param_idx} OFFSET ${offset_param_idx}
    """  # noqa: S608

    try:
        # Execute count and data queries in parallel
        count_result, pr_rows = await asyncio.gather(
            db_manager.fetchrow(count_query, *params),
            db_manager.fetch(data_query, *params, page_size, offset),
        )

        total = count_result["total"] if count_result else 0
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0

        # Format PR data
        prs = [
            {
                "pr_number": row["pr_number"],
                "title": row["title"],
                "repository": row["repository"],
                "state": row["state"],
                "merged": row["merged"] or False,
                "url": row["url"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "commits_count": row["commits_count"] or 0,
                "head_sha": row["head_sha"],
            }
            for row in pr_rows
        ]

        return {
            "data": prs,
            "pagination": {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
            },
        }
    except HTTPException:
        raise
    except Exception:
        LOGGER.exception("Failed to fetch user pull requests from database")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch user pull requests",
        ) from None


@FASTAPI_APP.get(
    "/api/metrics/trends",
    operation_id="get_metrics_trends",
    dependencies=[Depends(require_metrics_server_enabled)],
)
async def get_metrics_trends(
    start_time: str | None = Query(
        default=None, description="Start time in ISO 8601 format (e.g., 2024-01-01T00:00:00Z)"
    ),
    end_time: str | None = Query(default=None, description="End time in ISO 8601 format (e.g., 2024-01-31T23:59:59Z)"),
    bucket: str = Query(default="hour", pattern="^(hour|day)$", description="Time bucket ('hour', 'day')"),
) -> dict[str, Any]:
    """Get aggregated event trends over time.

    Returns aggregated event counts (total, success, error) grouped by time bucket.
    Essential for visualizing event volume and success rates over time on charts.

    **Parameters:**
    - `start_time`: Start of time range in ISO format.
    - `end_time`: End of time range in ISO format.
    - `bucket`: Time aggregation bucket ('hour' or 'day').

    **Return Structure:**
    ```json
    {
      "time_range": {
        "start_time": "...",
        "end_time": "..."
      },
      "trends": [
        {
          "bucket": "2024-01-15T14:00:00Z",
          "total_events": 120,
          "successful_events": 115,
          "failed_events": 5
        },
        ...
      ]
    }
    ```
    """
    if db_manager is None:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Metrics database not available",
        )

    start_datetime = parse_datetime_string(start_time, "start_time")
    end_datetime = parse_datetime_string(end_time, "end_time")

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

    # Add bucket parameter
    params.append(bucket)
    bucket_param_idx = param_idx

    query = f"""
        SELECT
            date_trunc(${bucket_param_idx}, created_at) as bucket,
            COUNT(*) as total_events,
            COUNT(*) FILTER (WHERE status = 'success') as successful_events,
            COUNT(*) FILTER (WHERE status IN ('error', 'partial')) as failed_events
        FROM webhooks
        {where_clause}
        GROUP BY bucket
        ORDER BY bucket
    """  # noqa: S608

    try:
        rows = await db_manager.fetch(query, *params)

        trends = [
            {
                "bucket": row["bucket"].isoformat() if row["bucket"] else None,
                "total_events": row["total_events"],
                "successful_events": row["successful_events"],
                "failed_events": row["failed_events"],
            }
            for row in rows
        ]

        return {
            "time_range": {
                "start_time": start_datetime.isoformat() if start_datetime else None,
                "end_time": end_datetime.isoformat() if end_datetime else None,
            },
            "trends": trends,
        }
    except Exception as ex:
        LOGGER.exception("Failed to fetch metrics trends from database")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch metrics trends",
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
        "total_token_spend": 104940,
        "total_events_trend": 15.3,
        "success_rate_trend": 2.1,
        "failed_events_trend": -8.5,
        "avg_duration_trend": -12.4
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
    - `total_events_trend`: Percentage change in total events vs previous period (e.g., 15.3 = 15.3% increase)
    - `success_rate_trend`: Percentage change in success rate vs previous period
    - `failed_events_trend`: Percentage change in failed events vs previous period (negative = improvement)
    - `avg_duration_trend`: Percentage change in avg processing time vs previous period (negative = faster)
    - `top_repositories`: Top 10 repositories by event volume
    - `event_type_distribution`: Event count breakdown by type
    - `hourly_event_rate`: Average events per hour in time range
    - `daily_event_rate`: Average events per day in time range

    **Trend Calculation:**
    - Trends compare current period to previous period of equal duration
    - Example: If querying last 24 hours, trends compare to 24 hours before that
    - Trend = ((current - previous) / previous) * 100
    - Returns 0.0 if no previous data or both periods have no events
    - Returns 100.0 if previous period had 0 but current period has data
    - Negative trends for duration metrics indicate performance improvement

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

    # Helper function to calculate percentage change trends
    def calculate_trend(current: float, previous: float) -> float:
        """Calculate percentage change from previous to current.

        Args:
            current: Current period value
            previous: Previous period value

        Returns:
            Percentage change rounded to 1 decimal place
            - Returns 0.0 if both values are 0
            - Returns 100.0 if previous is 0 but current is not
        """
        if previous == 0:
            return 0.0 if current == 0 else 100.0
        return round(((current - previous) / previous) * 100, 1)

    # Validate database manager is available
    if db_manager is None:
        LOGGER.error("Database manager not initialized - metrics server may not be properly configured")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Metrics database not available",
        )

    # Parse datetime strings
    start_datetime = parse_datetime_string(start_time, "start_time")
    end_datetime = parse_datetime_string(end_time, "end_time")

    # Calculate previous period for trend comparison
    prev_start_datetime = None
    prev_end_datetime = None
    if start_datetime and end_datetime:
        # Previous period has same duration as current period
        period_duration = end_datetime - start_datetime
        prev_start_datetime = start_datetime - period_duration
        prev_end_datetime = end_datetime - period_duration

    # Build query with time filters for current period
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

    # Build query with time filters for previous period
    prev_where_clause = "WHERE 1=1"
    prev_params: list[Any] = []
    prev_param_idx = 1

    if prev_start_datetime:
        prev_where_clause += f" AND created_at >= ${prev_param_idx}"
        prev_params.append(prev_start_datetime)
        prev_param_idx += 1

    if prev_end_datetime:
        prev_where_clause += f" AND created_at <= ${prev_param_idx}"
        prev_params.append(prev_end_datetime)
        prev_param_idx += 1

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
    """  # noqa: S608

    # Top repositories query
    top_repos_query = f"""
        WITH total AS (
            SELECT COUNT(*) as total_count
            FROM webhooks
            {where_clause}
        )
        SELECT
            repository,
            COUNT(*) as total_events,
            ROUND(
                (COUNT(*) FILTER (WHERE status = 'success')::numeric / COUNT(*)::numeric * 100)::numeric,
                2
            ) as success_rate,
            ROUND(
                (COUNT(*)::numeric / (SELECT total_count FROM total) * 100)::numeric,
                2
            ) as percentage
        FROM webhooks
        {where_clause}
        GROUP BY repository
        ORDER BY total_events DESC
        LIMIT 10
    """  # noqa: S608

    # Event type distribution query
    event_type_query = f"""
        SELECT
            event_type,
            COUNT(*) as event_count
        FROM webhooks
        {where_clause}
        GROUP BY event_type
        ORDER BY event_count DESC
    """  # noqa: S608

    # Time range for rate calculations
    time_range_query = f"""
        SELECT
            MIN(created_at) as first_event_time,
            MAX(created_at) as last_event_time
        FROM webhooks
        {where_clause}
    """  # noqa: S608

    # Previous period summary query for trend calculation
    prev_summary_query = f"""
        SELECT
            COUNT(*) as total_events,
            COUNT(*) FILTER (WHERE status = 'success') as successful_events,
            COUNT(*) FILTER (WHERE status IN ('error', 'partial')) as failed_events,
            ROUND(
                (COUNT(*) FILTER (WHERE status = 'success')::numeric / NULLIF(COUNT(*), 0)::numeric * 100)::numeric,
                2
            ) as success_rate,
            ROUND(AVG(duration_ms)) as avg_processing_time_ms
        FROM webhooks
        {prev_where_clause}
    """  # noqa: S608

    try:
        # Execute queries using DatabaseManager helpers
        summary_row = await db_manager.fetchrow(summary_query, *params)
        top_repos_rows = await db_manager.fetch(top_repos_query, *params)
        event_type_rows = await db_manager.fetch(event_type_query, *params)
        time_range_row = await db_manager.fetchrow(time_range_query, *params)

        # Execute previous period query if time range is specified
        prev_summary_row = None
        if prev_start_datetime and prev_end_datetime:
            prev_summary_row = await db_manager.fetchrow(prev_summary_query, *prev_params)

        # Process summary metrics
        total_events = summary_row["total_events"] or 0
        current_success_rate = float(summary_row["success_rate"]) if summary_row["success_rate"] is not None else 0.0
        current_failed_events = summary_row["failed_events"] or 0
        current_avg_duration = (
            int(summary_row["avg_processing_time_ms"]) if summary_row["avg_processing_time_ms"] is not None else 0
        )

        summary = {
            "total_events": total_events,
            "successful_events": summary_row["successful_events"] or 0,
            "failed_events": current_failed_events,
            "success_rate": current_success_rate,
            "avg_processing_time_ms": current_avg_duration,
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

        # Calculate and add trend fields if previous period data is available
        if prev_summary_row:
            prev_total_events = prev_summary_row["total_events"] or 0
            prev_success_rate = (
                float(prev_summary_row["success_rate"]) if prev_summary_row["success_rate"] is not None else 0.0
            )
            prev_failed_events = prev_summary_row["failed_events"] or 0
            prev_avg_duration = (
                int(prev_summary_row["avg_processing_time_ms"])
                if prev_summary_row["avg_processing_time_ms"] is not None
                else 0
            )

            summary["total_events_trend"] = calculate_trend(float(total_events), float(prev_total_events))
            summary["success_rate_trend"] = calculate_trend(current_success_rate, prev_success_rate)
            summary["failed_events_trend"] = calculate_trend(float(current_failed_events), float(prev_failed_events))
            summary["avg_duration_trend"] = calculate_trend(float(current_avg_duration), float(prev_avg_duration))
        else:
            # No previous period data - set trends to 0.0
            summary["total_events_trend"] = 0.0
            summary["success_rate_trend"] = 0.0
            summary["failed_events_trend"] = 0.0
            summary["avg_duration_trend"] = 0.0

        # Process top repositories
        top_repositories = [
            {
                "repository": row["repository"],
                "total_events": row["total_events"],
                "percentage": float(row["percentage"]) if row["percentage"] is not None else 0.0,
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
    except HTTPException:
        raise
    except Exception as ex:
        LOGGER.exception("Failed to fetch metrics summary from database")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch metrics summary",
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
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="MCP server not initialized"
            )

        return await http_transport.handle_fastapi_request(request)

    LOGGER.info("MCP integration initialized successfully (no authentication configured)")
    LOGGER.debug("MCP HTTP endpoint mounted at: /mcp")
