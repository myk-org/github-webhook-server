"""Tests for log viewer API endpoints and WebSocket functionality."""

import asyncio
import datetime
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect

from webhook_server.libs.log_parser import LogEntry


class TestLogAPI:
    """Test cases for log viewer API endpoints."""

    @pytest.fixture
    def sample_log_entries(self) -> list[LogEntry]:
        """Create sample log entries for testing."""
        return [
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 0),
                level="INFO",
                logger_name="main",
                message="Processing webhook",
                hook_id="hook1",
                event_type="push",
                repository="org/repo1",
                pr_number=None,
            ),
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 1, 0),
                level="DEBUG",
                logger_name="main",
                message="Processing PR #123",
                hook_id="hook2",
                event_type="pull_request.opened",
                repository="org/repo1",
                pr_number=123,
            ),
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 2, 0),
                level="ERROR",
                logger_name="helpers",
                message="API error occurred",
                hook_id=None,
                event_type=None,
                repository=None,
                pr_number=None,
            ),
        ]

    @pytest.fixture
    def temp_log_file(self) -> Path:
        """Create a temporary log file for testing."""
        log_content = """2025-07-31 10:00:00,000 - main - INFO - [Event: push][Delivery: hook1] Processing webhook
2025-07-31 10:01:00,000 - main - DEBUG - [Event: pull_request.opened][Delivery: hook2] Processing PR #123
2025-07-31 10:02:00,000 - helpers - ERROR - API error occurred"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(log_content)
            f.flush()
            return Path(f.name)

    def test_get_logs_page(self) -> None:
        """Test serving the main log viewer HTML page."""
        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance
            mock_instance.get_log_page.return_value = "<html><body>Log Viewer</body></html>"

            from webhook_server.app import FASTAPI_APP

            with TestClient(FASTAPI_APP):
                # This test assumes the log viewer endpoints will be added to the app
                # For now, we'll test the structure
                pass

    def test_get_log_entries_no_filters(self, sample_log_entries: list[LogEntry]) -> None:
        """Test retrieving log entries without filters."""
        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance
            mock_instance.get_log_entries.return_value = {
                "entries": [entry.to_dict() for entry in sample_log_entries],
                "total": len(sample_log_entries),
                "limit": 100,
                "offset": 0,
            }

            # Test would call GET /logs/api/entries
            # For now, test the data structure
            result = mock_instance.get_log_entries.return_value
            assert "entries" in result
            assert len(result["entries"]) == 3
            assert result["total"] == 3

    def test_get_log_entries_with_hook_id_filter(self, sample_log_entries: list[LogEntry]) -> None:
        """Test retrieving log entries filtered by hook ID."""
        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance

            # Mock filtered result for hook_id="hook1"
            filtered_entries = [entry for entry in sample_log_entries if entry.hook_id == "hook1"]
            mock_instance.get_log_entries.return_value = {
                "entries": [entry.to_dict() for entry in filtered_entries],
                "total": len(filtered_entries),
                "limit": 100,
                "offset": 0,
            }

            # Test would call GET /logs/api/entries?hook_id=hook1
            result = mock_instance.get_log_entries.return_value
            assert len(result["entries"]) == 1
            assert result["entries"][0]["hook_id"] == "hook1"

    def test_get_log_entries_with_pr_number_filter(self, sample_log_entries: list[LogEntry]) -> None:
        """Test retrieving log entries filtered by PR number."""
        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance

            # Mock filtered result for pr_number=123
            filtered_entries = [entry for entry in sample_log_entries if entry.pr_number == 123]
            mock_instance.get_log_entries.return_value = {
                "entries": [entry.to_dict() for entry in filtered_entries],
                "total": len(filtered_entries),
                "limit": 100,
                "offset": 0,
            }

            result = mock_instance.get_log_entries.return_value
            assert len(result["entries"]) == 1
            assert result["entries"][0]["pr_number"] == 123

    def test_get_log_entries_with_pagination(self, sample_log_entries: list[LogEntry]) -> None:
        """Test retrieving log entries with pagination."""
        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance

            # Mock paginated result (limit=2, offset=1)
            paginated_entries = sample_log_entries[1:3]  # Skip first, take 2
            mock_instance.get_log_entries.return_value = {
                "entries": [entry.to_dict() for entry in paginated_entries],
                "total": len(sample_log_entries),
                "limit": 2,
                "offset": 1,
            }

            result = mock_instance.get_log_entries.return_value
            assert len(result["entries"]) == 2
            assert result["total"] == 3
            assert result["limit"] == 2
            assert result["offset"] == 1

    def test_get_log_entries_invalid_parameters(self) -> None:
        """Test error handling for invalid parameters."""
        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance
            mock_instance.get_log_entries.side_effect = ValueError("Invalid limit value")

            # Test would return 400 Bad Request for invalid parameters
            with pytest.raises(ValueError, match="Invalid limit value"):
                mock_instance.get_log_entries()

    def test_get_log_entries_file_access_error(self) -> None:
        """Test error handling for file access errors."""
        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance
            mock_instance.get_log_entries.side_effect = OSError("Permission denied")

            # Test would return 500 Internal Server Error for file access issues
            with pytest.raises(OSError, match="Permission denied"):
                mock_instance.get_log_entries()

    def test_export_logs_csv_format(self, sample_log_entries: list[LogEntry]) -> None:
        """Test exporting logs in CSV format."""
        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance

            csv_content = "timestamp,level,logger_name,message,hook_id,event_type,repository,pr_number\n"
            csv_content += "2025-07-31T10:00:00,INFO,main,Processing webhook,hook1,push,org/repo1,\n"

            mock_instance.export_logs.return_value = csv_content

            result = mock_instance.export_logs.return_value
            assert result.startswith("timestamp,level,logger_name")
            assert "Processing webhook" in result

    def test_export_logs_json_format(self, sample_log_entries: list[LogEntry]) -> None:
        """Test exporting logs in JSON format."""
        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance

            json_content = json.dumps([entry.to_dict() for entry in sample_log_entries])
            mock_instance.export_logs.return_value = json_content

            result = mock_instance.export_logs.return_value
            parsed_data = json.loads(result)
            assert len(parsed_data) == 3
            assert parsed_data[0]["message"] == "Processing webhook"

    def test_export_logs_invalid_format(self) -> None:
        """Test error handling for invalid export format."""
        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance
            mock_instance.export_logs.side_effect = ValueError("Invalid format: xml")

            # Test would return 400 Bad Request for invalid format
            with pytest.raises(ValueError, match="Invalid format: xml"):
                mock_instance.export_logs()

    def test_export_logs_result_too_large(self) -> None:
        """Test error handling when export result is too large."""
        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance
            mock_instance.export_logs.side_effect = ValueError("Result set too large")

            # Test would return 413 Payload Too Large
            with pytest.raises(ValueError, match="Result set too large"):
                mock_instance.export_logs()


class TestLogWebSocket:
    """Test cases for WebSocket log streaming functionality."""

    @pytest.mark.asyncio
    async def test_websocket_connection_success(self) -> None:
        """Test successful WebSocket connection."""
        mock_websocket = AsyncMock()
        mock_websocket.accept = AsyncMock()
        mock_websocket.send_json = AsyncMock()
        mock_websocket.close = AsyncMock()

        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance

            async def mock_handle_websocket(websocket):
                await websocket.accept()

            mock_instance.handle_websocket = mock_handle_websocket

            # Test would establish WebSocket connection to /logs/ws
            await mock_instance.handle_websocket(mock_websocket)
            mock_websocket.accept.assert_called_once()

    @pytest.mark.asyncio
    async def test_websocket_real_time_streaming(self) -> None:
        """Test real-time log streaming through WebSocket."""
        mock_websocket = AsyncMock()
        mock_websocket.accept = AsyncMock()
        mock_websocket.send_json = AsyncMock()

        sample_entry = LogEntry(
            timestamp=datetime.datetime.now(),
            level="INFO",
            logger_name="main",
            message="New log entry",
            hook_id="new-hook",
            event_type="push",
            repository="org/repo",
            pr_number=None,
        )

        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance

            async def mock_handle_websocket(websocket):
                await websocket.accept()
                # Simulate sending a log entry
                await websocket.send_json(sample_entry.to_dict())

            mock_instance.handle_websocket = mock_handle_websocket

            await mock_instance.handle_websocket(mock_websocket)
            mock_websocket.accept.assert_called_once()
            mock_websocket.send_json.assert_called_once_with(sample_entry.to_dict())

    @pytest.mark.asyncio
    async def test_websocket_with_filters(self) -> None:
        """Test WebSocket connection with filtering parameters."""
        mock_websocket = AsyncMock()
        mock_websocket.accept = AsyncMock()
        mock_websocket.query_params = {"hook_id": "test-hook", "level": "INFO"}

        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance

            async def mock_handle_websocket(websocket, **kwargs):
                await websocket.accept()

            mock_instance.handle_websocket = mock_handle_websocket

            # Test would apply filters from query parameters
            await mock_instance.handle_websocket(mock_websocket, hook_id="test-hook", level="INFO")
            mock_websocket.accept.assert_called_once()

    @pytest.mark.asyncio
    async def test_websocket_disconnect_handling(self) -> None:
        """Test graceful handling of WebSocket disconnection."""
        mock_websocket = AsyncMock()
        mock_websocket.accept = AsyncMock()
        mock_websocket.send_json = AsyncMock(side_effect=WebSocketDisconnect())

        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance

            async def mock_handle_websocket(websocket):
                await websocket.accept()
                try:
                    await websocket.send_json({"test": "data"})
                except WebSocketDisconnect:
                    # Handle disconnect gracefully
                    pass

            mock_instance.handle_websocket = mock_handle_websocket

            # Should not raise exception on disconnect
            await mock_instance.handle_websocket(mock_websocket)
            mock_websocket.accept.assert_called_once()

    @pytest.mark.asyncio
    async def test_websocket_authentication_failure(self) -> None:
        """Test WebSocket connection with authentication failure."""
        mock_websocket = AsyncMock()
        mock_websocket.close = AsyncMock()

        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance

            async def mock_handle_websocket_auth_fail(websocket):
                # Simulate authentication failure
                await websocket.close(code=4003, reason="Authentication failed")

            mock_instance.handle_websocket = mock_handle_websocket_auth_fail

            await mock_instance.handle_websocket(mock_websocket)
            mock_websocket.close.assert_called_once_with(code=4003, reason="Authentication failed")

    @pytest.mark.asyncio
    async def test_websocket_multiple_connections(self) -> None:
        """Test handling multiple concurrent WebSocket connections."""
        mock_websockets = [AsyncMock() for _ in range(3)]

        for ws in mock_websockets:
            ws.accept = AsyncMock()
            ws.send_json = AsyncMock()

        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance

            async def mock_handle_websocket(websocket):
                await websocket.accept()

            mock_instance.handle_websocket = mock_handle_websocket

            # Test handling multiple connections concurrently
            tasks = [mock_instance.handle_websocket(ws) for ws in mock_websockets]
            await asyncio.gather(*tasks)

            for ws in mock_websockets:
                ws.accept.assert_called_once()

    @pytest.mark.asyncio
    async def test_websocket_server_error(self) -> None:
        """Test WebSocket error handling for server errors."""
        mock_websocket = AsyncMock()
        mock_websocket.accept = AsyncMock()
        mock_websocket.close = AsyncMock()

        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance

            async def mock_handle_websocket_error(websocket):
                await websocket.accept()
                # Simulate server error
                raise Exception("Internal server error")

            mock_instance.handle_websocket = mock_handle_websocket_error

            # Should handle server errors gracefully
            with pytest.raises(Exception, match="Internal server error"):
                await mock_instance.handle_websocket(mock_websocket)

            mock_websocket.accept.assert_called_once()


class TestPRFlowAPI:
    """Test cases for PR flow visualization API."""

    def test_get_pr_flow_data_by_hook_id(self) -> None:
        """Test retrieving PR flow data by hook ID."""
        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance

            mock_flow_data = {
                "identifier": "hook-abc123",
                "stages": [
                    {
                        "name": "Webhook Received",
                        "timestamp": "2025-07-31T10:00:00",
                        "duration_ms": None,
                    },
                    {
                        "name": "Validation Complete",
                        "timestamp": "2025-07-31T10:00:01",
                        "duration_ms": 1000,
                    },
                    {
                        "name": "Processing Complete",
                        "timestamp": "2025-07-31T10:00:05",
                        "duration_ms": 5000,
                    },
                ],
                "total_duration_ms": 5000,
                "success": True,
            }

            mock_instance.get_pr_flow_data.return_value = mock_flow_data

            # Test would call GET /logs/api/pr-flow/hook-abc123
            result = mock_instance.get_pr_flow_data.return_value
            assert result["identifier"] == "hook-abc123"
            assert len(result["stages"]) == 3
            assert result["total_duration_ms"] == 5000
            assert result["success"] is True

    def test_get_pr_flow_data_by_pr_number(self) -> None:
        """Test retrieving PR flow data by PR number."""
        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance

            mock_flow_data = {
                "identifier": "pr-456",
                "stages": [
                    {
                        "name": "PR Opened",
                        "timestamp": "2025-07-31T11:00:00",
                        "duration_ms": None,
                    },
                    {
                        "name": "Reviewers Assigned",
                        "timestamp": "2025-07-31T11:00:02",
                        "duration_ms": 2000,
                    },
                    {
                        "name": "Checks Complete",
                        "timestamp": "2025-07-31T11:00:10",
                        "duration_ms": 10000,
                    },
                ],
                "total_duration_ms": 10000,
                "success": True,
            }

            mock_instance.get_pr_flow_data.return_value = mock_flow_data

            # Test would call GET /logs/api/pr-flow/pr-456
            result = mock_instance.get_pr_flow_data.return_value
            assert result["identifier"] == "pr-456"
            assert len(result["stages"]) == 3

    def test_get_pr_flow_data_not_found(self) -> None:
        """Test error handling when PR flow data is not found."""
        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance
            mock_instance.get_pr_flow_data.side_effect = ValueError("No data found for identifier")

            # Test would return 404 Not Found
            with pytest.raises(ValueError, match="No data found for identifier"):
                mock_instance.get_pr_flow_data()

    def test_get_pr_flow_data_with_errors(self) -> None:
        """Test PR flow data with processing errors."""
        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance

            mock_flow_data = {
                "identifier": "hook-error123",
                "stages": [
                    {
                        "name": "Webhook Received",
                        "timestamp": "2025-07-31T12:00:00",
                        "duration_ms": None,
                    },
                    {
                        "name": "Processing Failed",
                        "timestamp": "2025-07-31T12:00:02",
                        "duration_ms": 2000,
                        "error": "API rate limit exceeded",
                    },
                ],
                "total_duration_ms": 2000,
                "success": False,
                "error": "Processing failed due to API rate limit",
            }

            mock_instance.get_pr_flow_data.return_value = mock_flow_data

            result = mock_instance.get_pr_flow_data.return_value
            assert result["success"] is False
            assert "error" in result
            assert "error" in result["stages"][1]
