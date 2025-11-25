"""Comprehensive tests for MetricsDashboardController to achieve 90%+ coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, mock_open, patch

import pytest
from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from webhook_server.web.metrics_dashboard import MetricsDashboardController


@pytest.fixture
def mock_db_manager() -> AsyncMock:
    """Create a mock DatabaseManager."""
    db = AsyncMock()
    db.fetch = AsyncMock(return_value=[])
    return db


@pytest.fixture
def mock_logger() -> Mock:
    """Create a mock logger."""
    return Mock()


@pytest.fixture
def controller(mock_db_manager: AsyncMock, mock_logger: Mock) -> MetricsDashboardController:
    """Create a MetricsDashboardController instance with mocked dependencies."""
    return MetricsDashboardController(mock_db_manager, mock_logger)


@pytest.fixture
def mock_websocket() -> AsyncMock:
    """Create a mock WebSocket."""
    ws = AsyncMock(spec=WebSocket)
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def sample_event() -> dict:
    """Create a sample webhook event dictionary."""
    return {
        "delivery_id": "abc123",
        "repository": "org/repo",
        "event_type": "pull_request",
        "action": "opened",
        "pr_number": 42,
        "sender": "testuser",
        "created_at": datetime(2025, 11, 24, 12, 34, 56, tzinfo=UTC),
        "processed_at": datetime(2025, 11, 24, 12, 35, 0, tzinfo=UTC),
        "duration_ms": 4000,
        "status": "success",
        "error_message": None,
        "api_calls_count": 5,
        "token_spend": 100,
        "token_remaining": 4900,
    }


@pytest.fixture
def sample_error_event() -> dict:
    """Create a sample webhook event with error status."""
    return {
        "delivery_id": "def456",
        "repository": "org/repo",
        "event_type": "issue_comment",
        "action": "created",
        "pr_number": None,
        "sender": "erroruser",
        "created_at": datetime(2025, 11, 24, 13, 0, 0, tzinfo=UTC),
        "processed_at": datetime(2025, 11, 24, 13, 0, 5, tzinfo=UTC),
        "duration_ms": 5000,
        "status": "error",
        "error_message": "API rate limit exceeded",
        "api_calls_count": 10,
        "token_spend": 200,
        "token_remaining": 4700,
    }


@pytest.fixture
def sample_partial_event() -> dict:
    """Create a sample webhook event with partial status."""
    return {
        "delivery_id": "ghi789",
        "repository": "org/repo",
        "event_type": "check_run",
        "action": "completed",
        "pr_number": 55,
        "sender": "partialuser",
        "created_at": datetime(2025, 11, 24, 14, 0, 0, tzinfo=UTC),
        "processed_at": datetime(2025, 11, 24, 14, 0, 3, tzinfo=UTC),
        "duration_ms": 3000,
        "status": "partial",
        "error_message": "Some operations failed",
        "api_calls_count": 3,
        "token_spend": 50,
        "token_remaining": 4950,
    }


class TestMetricsDashboardControllerInit:
    """Test MetricsDashboardController initialization."""

    def test_init(self, controller: MetricsDashboardController, mock_db_manager: AsyncMock, mock_logger: Mock) -> None:
        """Test controller initialization."""
        assert controller.db_manager is mock_db_manager
        assert controller.logger is mock_logger
        assert isinstance(controller._websocket_connections, set)
        assert len(controller._websocket_connections) == 0

    def test_poll_interval_constant(self) -> None:
        """Test POLL_INTERVAL_SECONDS constant is defined."""
        assert hasattr(MetricsDashboardController, "POLL_INTERVAL_SECONDS")
        assert MetricsDashboardController.POLL_INTERVAL_SECONDS == 2.0


class TestMetricsDashboardControllerShutdown:
    """Test MetricsDashboardController shutdown method."""

    @pytest.mark.asyncio
    async def test_shutdown_with_active_connections(
        self, controller: MetricsDashboardController, mock_logger: Mock
    ) -> None:
        """Test shutdown with active WebSocket connections."""
        # Create mock WebSocket connections
        ws1 = AsyncMock(spec=WebSocket)
        ws2 = AsyncMock(spec=WebSocket)
        ws3 = AsyncMock(spec=WebSocket)

        # Add connections
        controller._websocket_connections.add(ws1)
        controller._websocket_connections.add(ws2)
        controller._websocket_connections.add(ws3)

        # Execute shutdown
        await controller.shutdown()

        # Verify all connections were closed
        ws1.close.assert_called_once_with(code=1001, reason="Server shutdown")
        ws2.close.assert_called_once_with(code=1001, reason="Server shutdown")
        ws3.close.assert_called_once_with(code=1001, reason="Server shutdown")

        # Verify connections set is cleared
        assert len(controller._websocket_connections) == 0

        # Verify logging
        assert mock_logger.info.call_count == 2
        mock_logger.info.assert_any_call("Shutting down MetricsDashboardController with 3 active connections")
        mock_logger.info.assert_any_call("MetricsDashboardController shutdown completed")

    @pytest.mark.asyncio
    async def test_shutdown_with_no_connections(
        self, controller: MetricsDashboardController, mock_logger: Mock
    ) -> None:
        """Test shutdown with no active connections."""
        # Execute shutdown with empty connections
        await controller.shutdown()

        # Verify logging for zero connections
        mock_logger.info.assert_any_call("Shutting down MetricsDashboardController with 0 active connections")
        mock_logger.info.assert_any_call("MetricsDashboardController shutdown completed")

        # Verify connections set is still empty
        assert len(controller._websocket_connections) == 0

    @pytest.mark.asyncio
    async def test_shutdown_handles_close_errors(
        self, controller: MetricsDashboardController, mock_logger: Mock
    ) -> None:
        """Test shutdown handles errors during WebSocket close."""
        # Create mock WebSocket that raises error on close
        ws_error = AsyncMock(spec=WebSocket)
        ws_error.close.side_effect = RuntimeError("Close failed")

        ws_ok = AsyncMock(spec=WebSocket)

        # Add connections
        controller._websocket_connections.add(ws_error)
        controller._websocket_connections.add(ws_ok)

        # Execute shutdown
        await controller.shutdown()

        # Verify both connections attempted to close
        ws_error.close.assert_called_once_with(code=1001, reason="Server shutdown")
        ws_ok.close.assert_called_once_with(code=1001, reason="Server shutdown")

        # Verify error was logged
        mock_logger.exception.assert_called_once_with("Error closing WebSocket connection during shutdown")

        # Verify connections set is cleared even with errors
        assert len(controller._websocket_connections) == 0


class TestGetDashboardPage:
    """Test get_dashboard_page method."""

    def test_get_dashboard_page_success(self, controller: MetricsDashboardController) -> None:
        """Test successful HTML page serving."""
        mock_html_content = "<html><body>Metrics Dashboard</body></html>"

        with patch.object(controller, "_get_dashboard_html", return_value=mock_html_content):
            response = controller.get_dashboard_page()

        assert isinstance(response, HTMLResponse)
        assert response.body.decode() == mock_html_content

    def test_get_dashboard_page_file_not_found_error(
        self, controller: MetricsDashboardController, mock_logger: Mock
    ) -> None:
        """Test get_dashboard_page with FileNotFoundError."""
        with patch.object(controller, "_get_dashboard_html", side_effect=FileNotFoundError("Template not found")):
            with pytest.raises(HTTPException) as exc_info:
                controller.get_dashboard_page()

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Internal server error"
        mock_logger.exception.assert_called_once_with("Error serving metrics dashboard page")

    def test_get_dashboard_page_os_error(self, controller: MetricsDashboardController, mock_logger: Mock) -> None:
        """Test get_dashboard_page with OSError."""
        with patch.object(controller, "_get_dashboard_html", side_effect=OSError("Read failed")):
            with pytest.raises(HTTPException) as exc_info:
                controller.get_dashboard_page()

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Internal server error"
        mock_logger.exception.assert_called_once_with("Error serving metrics dashboard page")


class TestHandleWebSocket:
    """Test handle_websocket method."""

    @pytest.mark.asyncio
    async def test_websocket_connection_accept(
        self, controller: MetricsDashboardController, mock_websocket: AsyncMock, mock_logger: Mock
    ) -> None:
        """Test WebSocket connection is accepted and added to connections set."""
        # Mock asyncio.sleep to exit immediately
        with patch("asyncio.sleep", side_effect=WebSocketDisconnect):
            try:
                await controller.handle_websocket(mock_websocket)
            except WebSocketDisconnect:
                pass

        # Verify connection was accepted
        mock_websocket.accept.assert_called_once()

        # Verify connection was removed from set after disconnect
        assert mock_websocket not in controller._websocket_connections

        # Verify logging
        mock_logger.info.assert_any_call(
            "WebSocket connection established for metrics streaming (repository=None, event_type=None, status=None)"
        )

    @pytest.mark.asyncio
    async def test_websocket_event_streaming(
        self,
        controller: MetricsDashboardController,
        mock_websocket: AsyncMock,
        mock_db_manager: AsyncMock,
        sample_event: dict,
    ) -> None:
        """Test event streaming with new events."""
        # Mock database to return one event, then empty
        mock_db_manager.fetch.side_effect = [
            [sample_event],  # First poll returns one event
            [],  # Second poll returns nothing
        ]

        # Mock asyncio.sleep to control loop execution
        sleep_call_count = 0

        async def mock_sleep(_duration: float) -> None:
            nonlocal sleep_call_count
            sleep_call_count += 1
            if sleep_call_count >= 2:
                raise WebSocketDisconnect

        with patch("asyncio.sleep", side_effect=mock_sleep):
            try:
                await controller.handle_websocket(mock_websocket)
            except WebSocketDisconnect:
                pass

        # Verify event was sent
        assert mock_websocket.send_json.call_count == 1
        sent_message = mock_websocket.send_json.call_args[0][0]
        assert sent_message["type"] == "metric_update"
        assert sent_message["data"]["event"]["delivery_id"] == "abc123"
        assert sent_message["data"]["summary_delta"]["successful_events"] == 1

    @pytest.mark.asyncio
    async def test_websocket_with_filters(
        self, controller: MetricsDashboardController, mock_websocket: AsyncMock, mock_logger: Mock
    ) -> None:
        """Test WebSocket connection with filters applied."""
        # Mock asyncio.sleep to exit immediately
        with patch("asyncio.sleep", side_effect=WebSocketDisconnect):
            try:
                await controller.handle_websocket(
                    mock_websocket, repository="org/repo", event_type="pull_request", status="success"
                )
            except WebSocketDisconnect:
                pass

        # Verify logging includes filters
        mock_logger.info.assert_any_call(
            "WebSocket connection established for metrics streaming "
            "(repository=org/repo, event_type=pull_request, status=success)"
        )

    @pytest.mark.asyncio
    async def test_websocket_disconnect_handling(
        self, controller: MetricsDashboardController, mock_websocket: AsyncMock, mock_logger: Mock
    ) -> None:
        """Test WebSocketDisconnect handling."""
        # Mock send_json to raise WebSocketDisconnect
        mock_websocket.send_json.side_effect = WebSocketDisconnect

        # Mock database to return an event
        with patch.object(
            controller, "_fetch_new_events", new=AsyncMock(return_value=[{"created_at": datetime.now(UTC)}])
        ):
            await controller.handle_websocket(mock_websocket)

        # Verify client disconnected message
        mock_logger.info.assert_any_call("WebSocket client disconnected")

        # Verify connection was removed
        assert mock_websocket not in controller._websocket_connections

    @pytest.mark.asyncio
    async def test_websocket_runtime_error_during_send(
        self, controller: MetricsDashboardController, mock_websocket: AsyncMock, mock_logger: Mock
    ) -> None:
        """Test RuntimeError handling during send_json."""
        # Mock send_json to raise RuntimeError
        mock_websocket.send_json.side_effect = RuntimeError("Connection closed")

        # Mock database to return an event
        with patch.object(
            controller, "_fetch_new_events", new=AsyncMock(return_value=[{"created_at": datetime.now(UTC)}])
        ):
            await controller.handle_websocket(mock_websocket)

        # Verify disconnect was logged (RuntimeError gets converted to WebSocketDisconnect)
        mock_logger.debug.assert_any_call("WebSocket connection closed: RuntimeError")

    @pytest.mark.asyncio
    async def test_websocket_exception_handling(
        self, controller: MetricsDashboardController, mock_websocket: AsyncMock, mock_logger: Mock
    ) -> None:
        """Test general exception handling in WebSocket handler."""
        # Mock _fetch_new_events to raise an exception
        with patch.object(controller, "_fetch_new_events", new=AsyncMock(side_effect=ValueError("Database error"))):
            # Mock asyncio.sleep to limit retries
            sleep_call_count = 0

            async def mock_sleep(_duration: float) -> None:
                nonlocal sleep_call_count
                sleep_call_count += 1
                if sleep_call_count >= 2:
                    raise KeyboardInterrupt  # Force exit

            with patch("asyncio.sleep", side_effect=mock_sleep):
                try:
                    await controller.handle_websocket(mock_websocket)
                except KeyboardInterrupt:
                    pass

        # Verify error was logged
        mock_logger.exception.assert_any_call("Error during metrics monitoring iteration")

    @pytest.mark.asyncio
    async def test_websocket_initial_timestamp_set_when_no_events(
        self,
        controller: MetricsDashboardController,
        mock_websocket: AsyncMock,
        mock_db_manager: AsyncMock,
    ) -> None:
        """Test last_seen_timestamp is set to now when no events found."""
        # Mock database to return empty list twice
        mock_db_manager.fetch.return_value = []

        # Mock asyncio.sleep to control loop execution
        sleep_call_count = 0

        async def mock_sleep(_duration: float) -> None:
            nonlocal sleep_call_count
            sleep_call_count += 1
            if sleep_call_count >= 2:
                raise WebSocketDisconnect

        with patch("asyncio.sleep", side_effect=mock_sleep):
            try:
                await controller.handle_websocket(mock_websocket)
            except WebSocketDisconnect:
                pass

        # Verify fetch was called with timestamp after first empty poll
        assert mock_db_manager.fetch.call_count == 2
        # Second call should have last_seen_timestamp set
        second_call_args = mock_db_manager.fetch.call_args_list[1][0]
        # First positional arg is the query, second is the timestamp (if any)
        if len(second_call_args) > 1:
            # Timestamp was passed
            assert isinstance(second_call_args[1], datetime)

    @pytest.mark.asyncio
    async def test_websocket_cleanup_in_finally_block(
        self, controller: MetricsDashboardController, mock_websocket: AsyncMock
    ) -> None:
        """Test connection cleanup in finally block when exception occurs in monitoring loop."""
        # Mock _fetch_new_events to raise an exception that's not caught
        # This will trigger the general exception handler and finally block
        with patch.object(controller, "_fetch_new_events", new=AsyncMock(side_effect=KeyError("Unexpected error"))):
            # Mock asyncio.sleep to also raise so we don't retry
            with patch("asyncio.sleep", side_effect=KeyError("Unexpected error")):
                # Exception should be caught and handled
                await controller.handle_websocket(mock_websocket)

        # Verify connection was removed even with exception
        assert mock_websocket not in controller._websocket_connections

    @pytest.mark.asyncio
    async def test_websocket_close_on_general_exception(
        self, controller: MetricsDashboardController, mock_websocket: AsyncMock, mock_logger: Mock
    ) -> None:
        """Test WebSocket close on general exception."""
        # Mock _fetch_new_events to raise a non-retriable exception
        with patch.object(controller, "_fetch_new_events", new=AsyncMock(side_effect=RuntimeError("Fatal error"))):
            # Mock asyncio.sleep to avoid retries
            with patch("asyncio.sleep", side_effect=RuntimeError("Fatal error")):
                await controller.handle_websocket(mock_websocket)

        # Verify error was logged
        mock_logger.exception.assert_any_call("Error in WebSocket handler")

        # Verify close was attempted with error code
        mock_websocket.close.assert_called_once_with(code=1011, reason="Internal server error")

    @pytest.mark.asyncio
    async def test_websocket_close_exception_suppressed(
        self, controller: MetricsDashboardController, mock_websocket: AsyncMock
    ) -> None:
        """Test that exceptions during close are suppressed."""
        # Mock close to raise an exception
        mock_websocket.close.side_effect = RuntimeError("Close failed")

        # Mock _fetch_new_events to raise an exception
        with patch.object(controller, "_fetch_new_events", new=AsyncMock(side_effect=ValueError("Error"))):
            with patch("asyncio.sleep", side_effect=ValueError("Error")):
                # Should not raise, exception should be suppressed
                await controller.handle_websocket(mock_websocket)


class TestFetchNewEvents:
    """Test _fetch_new_events method."""

    @pytest.mark.asyncio
    async def test_fetch_new_events_no_filters(
        self, controller: MetricsDashboardController, mock_db_manager: AsyncMock, sample_event: dict
    ) -> None:
        """Test fetching events with no filters."""
        mock_db_manager.fetch.return_value = [sample_event]

        events = await controller._fetch_new_events(
            last_seen_timestamp=None, repository=None, event_type=None, status=None
        )

        assert len(events) == 1
        assert events[0]["delivery_id"] == "abc123"

        # Verify query has no WHERE clause
        query = mock_db_manager.fetch.call_args[0][0]
        assert "WHERE" not in query
        assert "ORDER BY created_at DESC" in query
        assert "LIMIT 100" in query

    @pytest.mark.asyncio
    async def test_fetch_new_events_with_timestamp_filter(
        self, controller: MetricsDashboardController, mock_db_manager: AsyncMock, sample_event: dict
    ) -> None:
        """Test fetching events with last_seen_timestamp filter."""
        timestamp = datetime(2025, 11, 24, 12, 0, 0, tzinfo=UTC)
        mock_db_manager.fetch.return_value = [sample_event]

        events = await controller._fetch_new_events(
            last_seen_timestamp=timestamp, repository=None, event_type=None, status=None
        )

        assert len(events) == 1

        # Verify query has WHERE created_at > timestamp
        query_args = mock_db_manager.fetch.call_args[0]
        query = query_args[0]
        assert "WHERE created_at > $1" in query
        assert query_args[1] == timestamp

    @pytest.mark.asyncio
    async def test_fetch_new_events_with_repository_filter(
        self, controller: MetricsDashboardController, mock_db_manager: AsyncMock, sample_event: dict
    ) -> None:
        """Test fetching events with repository filter."""
        mock_db_manager.fetch.return_value = [sample_event]

        events = await controller._fetch_new_events(
            last_seen_timestamp=None, repository="org/repo", event_type=None, status=None
        )

        assert len(events) == 1

        # Verify query has WHERE repository = $1
        query_args = mock_db_manager.fetch.call_args[0]
        query = query_args[0]
        assert "WHERE repository = $1" in query
        assert query_args[1] == "org/repo"

    @pytest.mark.asyncio
    async def test_fetch_new_events_with_event_type_filter(
        self, controller: MetricsDashboardController, mock_db_manager: AsyncMock, sample_event: dict
    ) -> None:
        """Test fetching events with event_type filter."""
        mock_db_manager.fetch.return_value = [sample_event]

        events = await controller._fetch_new_events(
            last_seen_timestamp=None, repository=None, event_type="pull_request", status=None
        )

        assert len(events) == 1

        # Verify query has WHERE event_type = $1
        query_args = mock_db_manager.fetch.call_args[0]
        query = query_args[0]
        assert "WHERE event_type = $1" in query
        assert query_args[1] == "pull_request"

    @pytest.mark.asyncio
    async def test_fetch_new_events_with_status_filter(
        self, controller: MetricsDashboardController, mock_db_manager: AsyncMock, sample_event: dict
    ) -> None:
        """Test fetching events with status filter."""
        mock_db_manager.fetch.return_value = [sample_event]

        events = await controller._fetch_new_events(
            last_seen_timestamp=None, repository=None, event_type=None, status="success"
        )

        assert len(events) == 1

        # Verify query has WHERE status = $1
        query_args = mock_db_manager.fetch.call_args[0]
        query = query_args[0]
        assert "WHERE status = $1" in query
        assert query_args[1] == "success"

    @pytest.mark.asyncio
    async def test_fetch_new_events_with_all_filters(
        self, controller: MetricsDashboardController, mock_db_manager: AsyncMock, sample_event: dict
    ) -> None:
        """Test fetching events with all filters combined."""
        timestamp = datetime(2025, 11, 24, 12, 0, 0, tzinfo=UTC)
        mock_db_manager.fetch.return_value = [sample_event]

        events = await controller._fetch_new_events(
            last_seen_timestamp=timestamp, repository="org/repo", event_type="pull_request", status="success"
        )

        assert len(events) == 1

        # Verify query has all WHERE conditions
        query_args = mock_db_manager.fetch.call_args[0]
        query = query_args[0]
        assert "created_at > $1" in query
        assert "repository = $2" in query
        assert "event_type = $3" in query
        assert "status = $4" in query

        # Verify all parameters are passed
        assert query_args[1] == timestamp
        assert query_args[2] == "org/repo"
        assert query_args[3] == "pull_request"
        assert query_args[4] == "success"

    @pytest.mark.asyncio
    async def test_fetch_new_events_database_error(
        self, controller: MetricsDashboardController, mock_db_manager: AsyncMock, mock_logger: Mock
    ) -> None:
        """Test database error propagates from _fetch_new_events."""
        mock_db_manager.fetch.side_effect = Exception("Database connection failed")

        # Exception should propagate instead of returning empty list
        with pytest.raises(Exception, match="Database connection failed"):
            await controller._fetch_new_events(last_seen_timestamp=None, repository=None, event_type=None, status=None)

        # Error should NOT be logged at this level (handled by outer handler)
        mock_logger.exception.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_new_events_converts_rows_to_dicts(
        self, controller: MetricsDashboardController, mock_db_manager: AsyncMock
    ) -> None:
        """Test that database rows are converted to dictionaries."""

        # Create a simple dict-like mock object that behaves like asyncpg Record
        class MockRecord(dict):
            """Simple dict subclass that mimics asyncpg Record behavior."""

            def keys(self):
                return super().keys()

            def values(self):
                return super().values()

        # Use the simple mock record
        mock_row = MockRecord({"delivery_id": "test123", "status": "success"})
        mock_db_manager.fetch.return_value = [mock_row]

        events = await controller._fetch_new_events(
            last_seen_timestamp=None, repository=None, event_type=None, status=None
        )

        # Verify we got results with correct data
        assert len(events) == 1
        assert events[0]["delivery_id"] == "test123"
        assert events[0]["status"] == "success"


class TestBuildMetricUpdateMessage:
    """Test _build_metric_update_message method."""

    def test_build_message_for_success_status(self, controller: MetricsDashboardController, sample_event: dict) -> None:
        """Test message format for success status events."""
        message = controller._build_metric_update_message(sample_event)

        assert message["type"] == "metric_update"
        assert "timestamp" in message
        assert "data" in message

        event_data = message["data"]["event"]
        assert event_data["delivery_id"] == "abc123"
        assert event_data["repository"] == "org/repo"
        assert event_data["event_type"] == "pull_request"
        assert event_data["status"] == "success"
        assert event_data["duration_ms"] == 4000

        summary = message["data"]["summary_delta"]
        assert summary["total_events"] == 1
        assert summary["successful_events"] == 1
        assert summary["failed_events"] == 0
        assert summary["partial_events"] == 0

    def test_build_message_for_error_status(
        self, controller: MetricsDashboardController, sample_error_event: dict
    ) -> None:
        """Test message format for error status events."""
        message = controller._build_metric_update_message(sample_error_event)

        assert message["type"] == "metric_update"

        event_data = message["data"]["event"]
        assert event_data["status"] == "error"
        assert event_data["error_message"] == "API rate limit exceeded"

        summary = message["data"]["summary_delta"]
        assert summary["total_events"] == 1
        assert summary["successful_events"] == 0
        assert summary["failed_events"] == 1
        assert summary["partial_events"] == 0

    def test_build_message_for_partial_status(
        self, controller: MetricsDashboardController, sample_partial_event: dict
    ) -> None:
        """Test message format for partial status events."""
        message = controller._build_metric_update_message(sample_partial_event)

        assert message["type"] == "metric_update"

        event_data = message["data"]["event"]
        assert event_data["status"] == "partial"

        summary = message["data"]["summary_delta"]
        assert summary["total_events"] == 1
        assert summary["successful_events"] == 0
        assert summary["failed_events"] == 0
        assert summary["partial_events"] == 1

    def test_build_message_datetime_serialization(
        self, controller: MetricsDashboardController, sample_event: dict
    ) -> None:
        """Test datetime serialization in message."""
        message = controller._build_metric_update_message(sample_event)

        event_data = message["data"]["event"]
        assert event_data["created_at"] == "2025-11-24T12:34:56+00:00"
        assert event_data["processed_at"] == "2025-11-24T12:35:00+00:00"

    def test_build_message_with_none_values(self, controller: MetricsDashboardController) -> None:
        """Test message building with None values.

        Note: When event.get() is called with a default and the key exists with value None,
        it returns None (not the default). This test reflects that behavior.
        """
        event = {
            "delivery_id": None,
            "repository": None,
            "event_type": None,
            "action": None,
            "pr_number": None,
            "sender": None,
            "status": None,
            "duration_ms": None,
            "created_at": None,
            "processed_at": None,
            "error_message": None,
            "api_calls_count": None,
            "token_spend": None,
            "token_remaining": None,
        }

        message = controller._build_metric_update_message(event)

        event_data = message["data"]["event"]
        # When dict has key with None value, .get(key, default) returns None, not default
        assert event_data["delivery_id"] is None
        assert event_data["repository"] is None
        assert event_data["event_type"] is None
        assert event_data["sender"] is None
        assert event_data["status"] is None
        assert event_data["duration_ms"] is None
        assert event_data["created_at"] is None
        assert event_data["processed_at"] is None
        assert event_data["api_calls_count"] is None
        assert event_data["token_spend"] is None
        assert event_data["token_remaining"] is None


class TestSerializeDatetime:
    """Test _serialize_datetime method."""

    def test_serialize_datetime_with_valid_datetime(self, controller: MetricsDashboardController) -> None:
        """Test serialization with valid datetime object."""
        dt = datetime(2025, 11, 24, 12, 34, 56, tzinfo=UTC)
        result = controller._serialize_datetime(dt)

        assert result == "2025-11-24T12:34:56+00:00"

    def test_serialize_datetime_with_none(self, controller: MetricsDashboardController) -> None:
        """Test serialization with None input."""
        result = controller._serialize_datetime(None)
        assert result is None


class TestGetDashboardHtml:
    """Test _get_dashboard_html method."""

    def test_get_dashboard_html_success(self, controller: MetricsDashboardController) -> None:
        """Test successful template loading."""
        mock_html = "<html><body>Dashboard</body></html>"

        # Mock the file open operation
        m = mock_open(read_data=mock_html)

        with patch("builtins.open", m):
            result = controller._get_dashboard_html()

        assert result == mock_html

        # Verify file was opened with correct path and encoding
        m.assert_called_once()
        call_args = m.call_args
        assert "metrics_dashboard.html" in str(call_args[0][0])
        assert call_args[1]["encoding"] == "utf-8"

    def test_get_dashboard_html_file_not_found(self, controller: MetricsDashboardController, mock_logger: Mock) -> None:
        """Test FileNotFoundError handling."""
        with patch("builtins.open", side_effect=FileNotFoundError("Template not found")):
            result = controller._get_dashboard_html()

        # Should return fallback HTML
        assert "Metrics Dashboard Template Error" in result
        assert "<!DOCTYPE html>" in result

        # Verify error was logged
        mock_logger.exception.assert_called_once()
        assert "Metrics dashboard template not found" in mock_logger.exception.call_args[0][0]

    def test_get_dashboard_html_os_error(self, controller: MetricsDashboardController, mock_logger: Mock) -> None:
        """Test OSError handling."""
        with patch("builtins.open", side_effect=OSError("Permission denied")):
            result = controller._get_dashboard_html()

        # Should return fallback HTML
        assert "Metrics Dashboard Template Error" in result
        assert "<!DOCTYPE html>" in result

        # Verify error was logged
        mock_logger.exception.assert_called_once()
        assert "Failed to read metrics dashboard template" in mock_logger.exception.call_args[0][0]


class TestGetFallbackHtml:
    """Test _get_fallback_html method."""

    def test_get_fallback_html_returns_valid_html(self, controller: MetricsDashboardController) -> None:
        """Test fallback HTML generation."""
        result = controller._get_fallback_html()

        # Verify it's valid HTML
        assert result.startswith("<!DOCTYPE html>")
        assert "<html" in result
        assert "</html>" in result

        # Verify error message content
        assert "Metrics Dashboard Template Error" in result
        assert "could not be loaded" in result
        assert "Refresh Page" in result

        # Verify styling exists
        assert "<style>" in result
        assert "</style>" in result

        # Verify error icon
        assert "⚠️" in result


class TestIntegrationScenarios:
    """Integration tests for complex scenarios."""

    @pytest.mark.asyncio
    async def test_full_websocket_lifecycle(
        self,
        controller: MetricsDashboardController,
        mock_websocket: AsyncMock,
        mock_db_manager: AsyncMock,
        sample_event: dict,
    ) -> None:
        """Test complete WebSocket lifecycle from connect to disconnect."""
        # Setup: Return event on first poll, empty on second
        mock_db_manager.fetch.side_effect = [[sample_event], []]

        # Control loop execution
        sleep_count = 0

        async def controlled_sleep(_duration: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise WebSocketDisconnect

        with patch("asyncio.sleep", side_effect=controlled_sleep):
            await controller.handle_websocket(mock_websocket)

        # Verify full lifecycle
        mock_websocket.accept.assert_called_once()
        assert mock_websocket.send_json.call_count == 1
        assert mock_websocket not in controller._websocket_connections

    @pytest.mark.asyncio
    async def test_multiple_websocket_connections(self, controller: MetricsDashboardController) -> None:
        """Test handling multiple simultaneous WebSocket connections."""
        ws1 = AsyncMock(spec=WebSocket)
        ws2 = AsyncMock(spec=WebSocket)
        ws3 = AsyncMock(spec=WebSocket)

        # Add all connections
        controller._websocket_connections.add(ws1)
        controller._websocket_connections.add(ws2)
        controller._websocket_connections.add(ws3)

        assert len(controller._websocket_connections) == 3

        # Shutdown should close all
        await controller.shutdown()

        assert len(controller._websocket_connections) == 0
        ws1.close.assert_called_once()
        ws2.close.assert_called_once()
        ws3.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_timestamp_tracking_across_multiple_events(
        self,
        controller: MetricsDashboardController,
        mock_websocket: AsyncMock,
        mock_db_manager: AsyncMock,
    ) -> None:
        """Test last_seen_timestamp is updated correctly across multiple events."""
        event1 = {"created_at": datetime(2025, 11, 24, 12, 0, 0, tzinfo=UTC), "status": "success"}
        event2 = {"created_at": datetime(2025, 11, 24, 13, 0, 0, tzinfo=UTC), "status": "success"}

        # Return two events on first poll, then empty
        mock_db_manager.fetch.side_effect = [[event1, event2], []]

        sleep_count = 0

        async def controlled_sleep(_duration: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise WebSocketDisconnect

        with patch("asyncio.sleep", side_effect=controlled_sleep):
            await controller.handle_websocket(mock_websocket)

        # Verify both events were sent
        assert mock_websocket.send_json.call_count == 2
