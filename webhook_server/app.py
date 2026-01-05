import asyncio
import ipaddress
import json
import logging
import os
import traceback
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
from webhook_server.utils.app_utils import (
    HTTP_TIMEOUT_SECONDS,
    gate_by_allowlist_ips,
    get_cloudflare_allowlist,
    get_github_allowlist,
    get_workflow_steps_core,
    log_webhook_summary,
    parse_datetime_string,
    verify_signature,
)
from webhook_server.utils.context import clear_context, create_context
from webhook_server.utils.helpers import (
    get_logger_with_params,
    prepare_log_prefix,
)
from webhook_server.utils.structured_logger import write_webhook_log
from webhook_server.web.log_viewer import LogViewerController

# Constants
APP_URL_ROOT_PATH: str = "/webhook_server"
LOG_SERVER_ENABLED: bool = os.environ.get("ENABLE_LOG_SERVER") == "true"
MCP_SERVER_ENABLED: bool = os.environ.get("ENABLE_MCP_SERVER") == "true"

# Global variables
ALLOWED_IPS: tuple[ipaddress._BaseNetwork, ...] = ()
LOGGER = get_logger_with_params()

_lifespan_http_client: httpx.AsyncClient | None = None
_background_tasks: set[asyncio.Task] = set()

# MCP Globals
http_transport: Any | None = None
mcp: Any | None = None


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


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    global _lifespan_http_client
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

        verify_github_ips = root_config.get("verify-github-ips", False)
        verify_cloudflare_ips = root_config.get("verify-cloudflare-ips", False)
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
        global http_transport, mcp
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

        yield

    except Exception as ex:
        LOGGER.error(f"Application failed during lifespan management: {ex}")
        raise

    finally:
        # Shutdown LogViewerController singleton and close WebSocket connections
        global _log_viewer_controller_singleton
        if _log_viewer_controller_singleton is not None:
            await _log_viewer_controller_singleton.shutdown()
            LOGGER.debug("LogViewerController singleton shutdown complete")

        if _lifespan_http_client:
            await _lifespan_http_client.aclose()
            LOGGER.debug("HTTP client closed")

        # Optionally wait for pending background tasks for graceful shutdown
        global _background_tasks
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
        # Create structured logging context at the VERY START
        repository_name = _hook_data.get("repository", {}).get("name", "unknown")
        repository_full_name = _hook_data.get("repository", {}).get("full_name", "unknown")
        ctx = create_context(
            hook_id=_delivery_id,
            event_type=_event_type,
            repository=repository_name,
            repository_full_name=repository_full_name,
            action=_hook_data.get("action"),
            sender=_hook_data.get("sender", {}).get("login"),
        )

        # Create repository-specific logger
        _logger = get_logger_with_params(repository_name=repository_name)
        _log_context = prepare_log_prefix(
            event_type=_event_type, delivery_id=_delivery_id, repository_name=repository_name
        )
        _logger.info(f"{_log_context} Processing webhook")

        try:
            # Initialize GithubWebhook inside background task to avoid blocking webhook response
            _api: GithubWebhook = GithubWebhook(hook_data=_hook_data, headers=_headers, logger=_logger)
            try:
                await _api.process()
            finally:
                await _api.cleanup()
        except RepositoryNotFoundInConfigError:
            # Repository-specific error - not exceptional, log as error not exception
            _logger.error(f"{_log_context} Repository not found in configuration")
            ctx.success = False
            ctx.error = {
                "type": "RepositoryNotFoundInConfigError",
                "message": "Repository not found in configuration",
                "traceback": "",
            }
        except (httpx.ConnectError, httpx.RequestError, requests.exceptions.ConnectionError) as ex:
            # Network/connection errors - can be transient
            _logger.exception(f"{_log_context} API connection error - check network connectivity")
            ctx.success = False
            ctx.error = {
                "type": type(ex).__name__,
                "message": str(ex),
                "traceback": traceback.format_exc(),
            }
        except asyncio.CancelledError:
            # Task cancellation - propagate without logging as error
            _logger.debug(f"{_log_context} Webhook processing cancelled")
            raise
        except Exception as ex:
            # Catch-all for unexpected errors
            _logger.exception(f"{_log_context} Unexpected error in background webhook processing")
            ctx.success = False
            ctx.error = {
                "type": type(ex).__name__,
                "message": str(ex),
                "traceback": traceback.format_exc(),
            }
        finally:
            # Set completion time and log summary from structured context
            if ctx:
                ctx.completed_at = datetime.now(UTC)
                log_webhook_summary(ctx, _logger, _log_context)

            # ALWAYS write the structured log, even on error
            try:
                write_webhook_log(ctx)
            except Exception:
                _logger.exception(f"{_log_context} Failed to write webhook log")
            finally:
                clear_context()

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
    return get_workflow_steps_core(controller=controller, hook_id=hook_id)


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
