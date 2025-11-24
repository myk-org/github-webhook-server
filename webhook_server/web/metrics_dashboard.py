"""Metrics dashboard controller for real-time webhook metrics streaming and visualization."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from webhook_server.libs.database import DatabaseManager


class MetricsDashboardController:
    """
    Controller for metrics dashboard functionality.

    Provides real-time streaming of webhook metrics from PostgreSQL database
    via WebSocket connections. Follows the WebSocket pattern from LogViewerController
    with periodic polling for database changes.

    Architecture:
    - WebSocket connection management with graceful shutdown
    - Periodic polling (1-2 seconds) to detect new webhook events
    - Filtering by repository, event_type, status
    - Real-time metrics updates to connected clients

    WebSocket Message Format:
        {
            "type": "metric_update",
            "timestamp": "2025-11-24T12:34:56.789Z",
            "data": {
                "event": {
                    "delivery_id": "...",
                    "repository": "org/repo",
                    "event_type": "pull_request",
                    "status": "success",
                    "duration_ms": 5234,
                    "created_at": "...",
                },
                "summary_delta": {
                    "total_events": 1,
                    "successful_events": 1,
                }
            }
        }

    Example:
        controller = MetricsDashboardController(db_manager, logger)
        await controller.handle_websocket(websocket, repository="org/repo")
    """

    # Polling interval for database changes (seconds)
    POLL_INTERVAL_SECONDS = 2.0

    def __init__(self, db_manager: DatabaseManager, logger: logging.Logger) -> None:
        """
        Initialize the metrics dashboard controller.

        Args:
            db_manager: DatabaseManager instance for query execution
            logger: Logger instance for this controller

        Architecture guarantees:
        - db_manager is ALWAYS provided (required parameter) - no defensive checks needed
        - logger is ALWAYS provided (required parameter) - no defensive checks needed
        - _websocket_connections starts empty - legitimate to check size
        """
        self.db_manager = db_manager
        self.logger = logger
        self._websocket_connections: set[WebSocket] = set()

    async def shutdown(self) -> None:
        """
        Close all active WebSocket connections during shutdown.

        This method should be called during application shutdown to properly
        close all WebSocket connections and prevent resource leaks.

        Follows the same pattern as LogViewerController.shutdown().
        """
        self.logger.info(
            f"Shutting down MetricsDashboardController with {len(self._websocket_connections)} active connections"
        )

        # Create a copy of the connections set to avoid modification during iteration
        connections_to_close = list(self._websocket_connections)

        for ws in connections_to_close:
            try:
                await ws.close(code=1001, reason="Server shutdown")
                self.logger.debug("Successfully closed WebSocket connection during shutdown")
            except Exception:
                # Log the error but continue closing other connections
                self.logger.exception("Error closing WebSocket connection during shutdown")

        # Clear the connections set
        self._websocket_connections.clear()
        self.logger.info("MetricsDashboardController shutdown completed")

    def get_dashboard_page(self) -> HTMLResponse:
        """
        Serve the metrics dashboard HTML page.

        Returns:
            HTML response with metrics dashboard interface

        Raises:
            HTTPException: 500 for template loading errors
        """
        try:
            html_content = self._get_dashboard_html()
            return HTMLResponse(content=html_content)
        except Exception as e:
            self.logger.exception("Error serving metrics dashboard page")
            raise HTTPException(status_code=500, detail="Internal server error") from e

    async def handle_websocket(
        self,
        websocket: WebSocket,
        repository: str | None = None,
        event_type: str | None = None,
        status: str | None = None,
    ) -> None:
        """
        Handle WebSocket connection for real-time metrics streaming.

        Accepts WebSocket connection, monitors database for new webhook events,
        and streams updates to the client. Uses periodic polling (every 2 seconds)
        to check for new events.

        Args:
            websocket: WebSocket connection
            repository: Filter by repository (e.g., "org/repo")
            event_type: Filter by event type (e.g., "pull_request", "issue_comment")
            status: Filter by status (e.g., "success", "error", "partial")

        Architecture:
        - Polling-based monitoring (LISTEN/NOTIFY can be added later)
        - Tracks last_seen_timestamp to detect new events
        - Applies filters server-side for efficiency
        - Sends both individual events and summary deltas
        """
        await websocket.accept()
        self._websocket_connections.add(websocket)

        try:
            self.logger.info(
                f"WebSocket connection established for metrics streaming "
                f"(repository={repository}, event_type={event_type}, status={status})"
            )

            # Track last seen timestamp to detect new events
            last_seen_timestamp: datetime | None = None

            # Start monitoring for new metrics
            while True:
                try:
                    # Query for new webhook events since last_seen_timestamp
                    new_events = await self._fetch_new_events(
                        last_seen_timestamp=last_seen_timestamp,
                        repository=repository,
                        event_type=event_type,
                        status=status,
                    )

                    # Send updates for each new event
                    for event in new_events:
                        try:
                            message = self._build_metric_update_message(event)
                            await websocket.send_json(message)

                            # Update last_seen_timestamp
                            event_timestamp = event.get("created_at")
                            if event_timestamp:
                                if last_seen_timestamp is None or event_timestamp > last_seen_timestamp:
                                    last_seen_timestamp = event_timestamp

                        except WebSocketDisconnect:
                            self.logger.debug("WebSocket disconnected while sending event")
                            break

                    # Ensure we don't repeatedly fetch historical events if no events are found
                    if last_seen_timestamp is None:
                        last_seen_timestamp = datetime.now(UTC)

                    # Wait before next poll
                    await asyncio.sleep(self.POLL_INTERVAL_SECONDS)

                except Exception:
                    self.logger.exception("Error during metrics monitoring iteration")
                    # Continue monitoring despite errors in individual iterations
                    await asyncio.sleep(self.POLL_INTERVAL_SECONDS)

        except WebSocketDisconnect:
            self.logger.info("WebSocket client disconnected")
        except Exception:
            self.logger.exception("Error in WebSocket handler")
            try:
                await websocket.close(code=1011, reason="Internal server error")
            except Exception:
                pass
        finally:
            self._websocket_connections.discard(websocket)

    async def _fetch_new_events(
        self,
        last_seen_timestamp: datetime | None,
        repository: str | None,
        event_type: str | None,
        status: str | None,
    ) -> list[dict[str, Any]]:
        """
        Fetch new webhook events from database since last_seen_timestamp.

        Builds dynamic query based on filters and timestamp to retrieve only
        new events efficiently.

        Args:
            last_seen_timestamp: Timestamp of last seen event (None = get latest)
            repository: Filter by repository
            event_type: Filter by event type
            status: Filter by status

        Returns:
            List of webhook event dictionaries with normalized fields

        Architecture:
        - Uses parameterized queries to prevent SQL injection
        - Applies filters server-side for efficiency
        - Returns newest events first (descending timestamp)
        - Limits to 100 events per poll to prevent overwhelming clients
        """
        # Build WHERE clause dynamically based on filters
        where_conditions = []
        query_params: list[Any] = []
        param_counter = 1

        if last_seen_timestamp is not None:
            where_conditions.append(f"created_at > ${param_counter}")
            query_params.append(last_seen_timestamp)
            param_counter += 1

        if repository is not None:
            where_conditions.append(f"repository = ${param_counter}")
            query_params.append(repository)
            param_counter += 1

        if event_type is not None:
            where_conditions.append(f"event_type = ${param_counter}")
            query_params.append(event_type)
            param_counter += 1

        if status is not None:
            where_conditions.append(f"status = ${param_counter}")
            query_params.append(status)
            param_counter += 1

        where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""

        # Query for new events (newest first, limit to 100 per poll)
        query = f"""
            SELECT
                delivery_id,
                repository,
                event_type,
                action,
                pr_number,
                sender,
                created_at,
                processed_at,
                duration_ms,
                status,
                error_message,
                api_calls_count,
                token_spend,
                token_remaining
            FROM webhooks
            {where_clause}
            ORDER BY created_at DESC
            LIMIT 100
        """

        try:
            rows = await self.db_manager.fetch(query, *query_params)

            # Convert rows to dictionaries and ensure datetime objects are serializable
            events = []
            for row in rows:
                event = dict(row)
                # Ensure datetimes are datetime objects (asyncpg returns them correctly)
                events.append(event)

            self.logger.debug(f"Fetched {len(events)} new events (filters: {where_clause})")
            return events

        except Exception:
            self.logger.exception("Error fetching new events from database")
            return []

    def _build_metric_update_message(self, event: dict[str, Any]) -> dict[str, Any]:
        """
        Build WebSocket message for metric update.

        Converts database row to WebSocket message format with:
        - Event details (delivery_id, repository, event_type, etc.)
        - Summary delta (incremental counts for aggregation)

        Args:
            event: Webhook event dictionary from database

        Returns:
            WebSocket message dictionary matching specification

        Format:
            {
                "type": "metric_update",
                "timestamp": "2025-11-24T12:34:56.789Z",
                "data": {
                    "event": {...},
                    "summary_delta": {...}
                }
            }
        """
        # Extract event data
        event_data = {
            "delivery_id": event.get("delivery_id", ""),
            "repository": event.get("repository", ""),
            "event_type": event.get("event_type", ""),
            "action": event.get("action"),
            "pr_number": event.get("pr_number"),
            "sender": event.get("sender", ""),
            "status": event.get("status", ""),
            "duration_ms": event.get("duration_ms", 0),
            "created_at": self._serialize_datetime(event.get("created_at")),
            "processed_at": self._serialize_datetime(event.get("processed_at")),
            "error_message": event.get("error_message"),
            "api_calls_count": event.get("api_calls_count", 0),
            "token_spend": event.get("token_spend", 0),
            "token_remaining": event.get("token_remaining", 0),
        }

        # Calculate summary delta (incremental counts)
        status = event.get("status", "")
        summary_delta = {
            "total_events": 1,
            "successful_events": 1 if status == "success" else 0,
            "failed_events": 1 if status == "error" else 0,
            "partial_events": 1 if status == "partial" else 0,
        }

        return {
            "type": "metric_update",
            "timestamp": datetime.now(UTC).isoformat(),
            "data": {
                "event": event_data,
                "summary_delta": summary_delta,
            },
        }

    def _serialize_datetime(self, dt: datetime | None) -> str | None:
        """
        Serialize datetime to ISO format string for JSON.

        Args:
            dt: datetime object to serialize

        Returns:
            ISO format string or None if dt is None
        """
        if dt is None:
            return None
        # Ensure timezone-aware datetime is serialized correctly
        return dt.isoformat()

    def _get_dashboard_html(self) -> str:
        """
        Load and return the metrics dashboard HTML template.

        Returns:
            HTML content for metrics dashboard interface

        Raises:
            FileNotFoundError: If template file cannot be found
            IOError: If template file cannot be read
        """
        template_path = Path(__file__).parent / "templates" / "metrics_dashboard.html"

        try:
            with open(template_path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            self.logger.exception(f"Metrics dashboard template not found at {template_path}")
            return self._get_fallback_html()
        except OSError:
            self.logger.exception("Failed to read metrics dashboard template")
            return self._get_fallback_html()

    def _get_fallback_html(self) -> str:
        """
        Provide a minimal fallback HTML when template loading fails.

        Returns:
            Basic HTML page with error message
        """
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GitHub Webhook Server - Metrics Dashboard (Error)</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }
        .error-container {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            text-align: center;
        }
        .error-icon {
            font-size: 48px;
            color: #dc3545;
            margin-bottom: 20px;
        }
        .retry-btn {
            background: #007bff;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 4px;
            cursor: pointer;
            margin-top: 20px;
        }
    </style>
</head>
<body>
    <div class="error-container">
        <div class="error-icon">⚠️</div>
        <h1>Metrics Dashboard Template Error</h1>
        <p>The metrics dashboard template could not be loaded. Please check the server logs for details.</p>
        <button class="retry-btn" onclick="window.location.reload()">Refresh Page</button>
    </div>
</body>
</html>"""
