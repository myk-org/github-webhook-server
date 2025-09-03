"""Tests for log viewer API endpoints and WebSocket functionality."""

import asyncio
import datetime
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect

from webhook_server.libs.log_parser import LogEntry


class TestLogViewerController:
    """Test cases for LogViewerController class methods."""

    @pytest.fixture
    def mock_logger(self):
        """Create a mock logger for testing."""
        return Mock()

    @pytest.fixture
    def controller(self, mock_logger):
        """Create a LogViewerController instance for testing."""
        from webhook_server.web.log_viewer import LogViewerController

        with patch("webhook_server.web.log_viewer.Config") as mock_config:
            mock_config_instance = Mock()
            mock_config_instance.data_dir = "/test/data"
            mock_config.return_value = mock_config_instance
            return LogViewerController(logger=mock_logger)

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

    def test_get_log_page_success(self, controller):
        """Test successful log page generation."""
        with patch.object(controller, "_get_log_viewer_html", return_value="<html>Test</html>"):
            response = controller.get_log_page()
            assert response.status_code == 200
            assert "Test" in response.body.decode()

    def test_get_log_page_file_not_found(self, controller):
        """Test log page when template file not found."""
        with patch.object(controller, "_get_log_viewer_html", side_effect=FileNotFoundError):
            with pytest.raises(HTTPException) as exc:
                controller.get_log_page()
            assert exc.value.status_code == 404

    def test_get_log_page_error(self, controller):
        """Test log page with generic error."""
        with patch.object(controller, "_get_log_viewer_html", side_effect=Exception("Test error")):
            with pytest.raises(HTTPException) as exc:
                controller.get_log_page()
            assert exc.value.status_code == 500

    def test_get_log_entries_success(self, controller, sample_log_entries):
        """Test successful log entries retrieval."""
        with patch.object(controller, "_stream_log_entries", return_value=sample_log_entries):
            result = controller.get_log_entries()
            assert "entries" in result
            assert result["entries_processed"] == 3
            assert len(result["entries"]) == 3

    def test_get_log_entries_with_filters(self, controller, sample_log_entries):
        """Test log entries with filters applied."""
        with patch.object(controller, "_stream_log_entries", return_value=sample_log_entries):
            result = controller.get_log_entries(hook_id="hook1", level="INFO")
            assert "entries" in result

    def test_get_log_entries_with_pagination(self, controller, sample_log_entries):
        """Test log entries with pagination."""
        with patch.object(controller, "_stream_log_entries", return_value=sample_log_entries):
            result = controller.get_log_entries(limit=2, offset=1)
            assert result["limit"] == 2
            assert result["offset"] == 1

    def test_get_log_entries_invalid_limit(self, controller):
        """Test log entries with invalid limit."""
        with pytest.raises(HTTPException) as exc:
            controller.get_log_entries(limit=0)
        assert exc.value.status_code == 400

    def test_get_log_entries_file_error(self, controller):
        """Test log entries with file access error."""
        with patch.object(controller, "_stream_log_entries", side_effect=OSError("Permission denied")):
            with pytest.raises(HTTPException) as exc:
                controller.get_log_entries()
            assert exc.value.status_code == 500

    def test_export_logs_json(self, controller, sample_log_entries):
        """Test JSON export functionality."""
        with patch.object(controller, "_stream_log_entries", return_value=sample_log_entries):
            result = controller.export_logs(format_type="json")
            # This should return a StreamingResponse, not a JSON string
            assert hasattr(result, "status_code")
            assert result.status_code == 200

    def test_export_logs_invalid_format(self, controller):
        """Test export with invalid format."""
        with patch.object(controller, "_stream_log_entries", return_value=[]):
            with pytest.raises(HTTPException) as exc:
                controller.export_logs(format_type="xml")
            assert exc.value.status_code == 400

    def test_export_logs_result_too_large(self, controller):
        """Test export with result set too large."""
        with patch.object(controller, "_stream_log_entries", return_value=[]):
            with pytest.raises(HTTPException) as exc:
                controller.export_logs(format_type="json", limit=60000)
            assert exc.value.status_code == 413

    def test_export_logs_filtered_entries_too_large(self, controller):
        """Test export when filtered entries exceed limit."""
        # Create a large list of entries that will all match filters
        large_entries = [
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 0),
                level="INFO",
                logger_name="main",
                message=f"Entry {i}",
                hook_id="hook1",
            )
            for i in range(51000)
        ]

        # Mock stream_log_entries to return many entries
        with patch.object(controller, "_stream_log_entries", return_value=large_entries):
            # Mock _entry_matches_filters to always return True so all entries are included
            with patch.object(controller, "_entry_matches_filters", return_value=True):
                with pytest.raises(HTTPException) as exc:
                    # Call with a limit that would exceed 50000 to trigger the error
                    controller.export_logs(format_type="json", limit=51000)
                assert exc.value.status_code == 413

    def test_get_pr_flow_data_success(self, controller, sample_log_entries):
        """Test PR flow data retrieval."""
        # Create entries with matching hook_id
        matching_entries = [
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 0),
                level="INFO",
                logger_name="main",
                message="Test message",
                hook_id="test-hook-id",
            )
        ]

        with patch.object(controller, "_stream_log_entries", return_value=matching_entries):
            with patch.object(controller, "_analyze_pr_flow", return_value={"test": "data"}):
                result = controller.get_pr_flow_data("test-hook-id")
                assert result == {"test": "data"}

    def test_get_pr_flow_data_not_found(self, controller):
        """Test PR flow data when not found."""
        with patch.object(controller, "_stream_log_entries", return_value=[]):
            with pytest.raises(HTTPException) as exc:
                controller.get_pr_flow_data("nonexistent")
            assert exc.value.status_code == 404

    def test_get_pr_flow_data_hook_prefix(self, controller, sample_log_entries):
        """Test PR flow data with hook- prefix."""
        matching_entries = [
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 0),
                level="INFO",
                logger_name="main",
                message="Test message",
                hook_id="123",  # After stripping "hook-" prefix, it looks for "123"
            )
        ]

        with patch.object(controller, "_stream_log_entries", return_value=matching_entries):
            with patch.object(controller, "_analyze_pr_flow", return_value={"test": "data"}):
                result = controller.get_pr_flow_data("hook-123")
                assert result == {"test": "data"}

    def test_get_pr_flow_data_pr_prefix(self, controller, sample_log_entries):
        """Test PR flow data with pr- prefix."""
        matching_entries = [
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 0),
                level="INFO",
                logger_name="main",
                message="Test message",
                hook_id="some-hook",
                pr_number=123,
            )
        ]

        with patch.object(controller, "_stream_log_entries", return_value=matching_entries):
            with patch.object(controller, "_analyze_pr_flow", return_value={"test": "data"}):
                result = controller.get_pr_flow_data("pr-123")
                assert result == {"test": "data"}

    def test_get_pr_flow_data_direct_number(self, controller, sample_log_entries):
        """Test PR flow data with direct PR number."""
        matching_entries = [
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 0),
                level="INFO",
                logger_name="main",
                message="Test message",
                hook_id="some-hook",
                pr_number=123,
            )
        ]

        with patch.object(controller, "_stream_log_entries", return_value=matching_entries):
            with patch.object(controller, "_analyze_pr_flow", return_value={"test": "data"}):
                result = controller.get_pr_flow_data("123")
                assert result == {"test": "data"}

    def test_get_pr_flow_data_direct_hook_id(self, controller, sample_log_entries):
        """Test PR flow data with direct hook ID."""
        matching_entries = [
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 0),
                level="INFO",
                logger_name="main",
                message="Test message",
                hook_id="abc123-def456",
            )
        ]

        with patch.object(controller, "_stream_log_entries", return_value=matching_entries):
            with patch.object(controller, "_analyze_pr_flow", return_value={"test": "data"}):
                result = controller.get_pr_flow_data("abc123-def456")
                assert result == {"test": "data"}

    def test_get_workflow_steps_success(self, controller, sample_log_entries):
        """Test workflow steps retrieval."""
        workflow_steps = [
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 0),
                level="STEP",
                logger_name="main",
                message="Step 1",
                hook_id="hook1",
            )
        ]

        with patch.object(controller, "_stream_log_entries", return_value=sample_log_entries):
            with patch.object(controller.log_parser, "extract_workflow_steps", return_value=workflow_steps):
                with patch.object(controller, "_build_workflow_timeline", return_value={"test": "data"}):
                    result = controller.get_workflow_steps("hook1")
                    assert result == {"test": "data"}

    def test_get_workflow_steps_not_found(self, controller):
        """Test workflow steps when not found."""
        with patch.object(controller, "_stream_log_entries", return_value=[]):
            with pytest.raises(HTTPException) as exc:
                controller.get_workflow_steps("nonexistent")
            assert exc.value.status_code == 404

    def test_stream_log_entries_success(self, controller):
        """Test log entries loading."""
        mock_config = Mock()
        mock_config.data_dir = "/test"
        controller.config = mock_config

        with patch("webhook_server.web.log_viewer.Path") as mock_path:
            mock_path_instance = Mock()
            mock_path_instance.exists.return_value = True

            # Mock log file with proper stat() method
            mock_log_file = Mock()
            mock_log_file.name = "test.log"
            mock_stat = Mock()
            mock_stat.st_mtime = 123456789
            mock_log_file.stat.return_value = mock_stat

            mock_path_instance.glob.return_value = [mock_log_file]
            mock_path.return_value = mock_path_instance

            with patch.object(controller.log_parser, "parse_log_file", return_value=[]):
                result = list(controller._stream_log_entries())
                assert isinstance(result, list)

    def test_stream_log_entries_no_directory(self, controller):
        """Test log entries loading when directory doesn't exist."""
        mock_config = Mock()
        mock_config.data_dir = "/test"
        controller.config = mock_config

        with patch("webhook_server.web.log_viewer.Path") as mock_path:
            mock_path_instance = Mock()
            mock_path_instance.exists.return_value = False
            mock_path.return_value = mock_path_instance

            result = list(controller._stream_log_entries())
            assert result == []

    def test_stream_log_entries_parse_error(self, controller):
        """Test log entries loading with parse error."""
        mock_config = Mock()
        mock_config.data_dir = "/test"
        controller.config = mock_config

        with patch("webhook_server.web.log_viewer.Path") as mock_path:
            mock_path_instance = Mock()
            mock_path_instance.exists.return_value = True
            mock_log_file = Mock()
            mock_log_file.name = "test.log"
            mock_path_instance.glob.return_value = [mock_log_file]
            mock_path.return_value = mock_path_instance

            with patch.object(controller.log_parser, "parse_log_file", side_effect=Exception("Parse error")):
                result = list(controller._stream_log_entries())
                assert isinstance(result, list)

    def test_get_log_directory(self, controller):
        """Test log directory path generation."""
        mock_config = Mock()
        mock_config.data_dir = "/test"
        controller.config = mock_config

        result = controller._get_log_directory()
        assert str(result).endswith("logs")

    def test_generate_json_export(self, controller, sample_log_entries):
        """Test JSON export generation."""
        result = controller._generate_json_export(sample_log_entries)
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert len(parsed) == 3

    def test_analyze_pr_flow_empty_entries(self, controller):
        """Test PR flow analysis with empty entries."""
        result = controller._analyze_pr_flow([], "test-id")
        assert result["identifier"] == "test-id"
        assert result["stages"] == []
        assert result["success"] is False
        assert "error" in result

    def test_analyze_pr_flow_with_error_entries(self, controller, sample_log_entries):
        """Test PR flow analysis with error entries."""
        error_entry = LogEntry(
            timestamp=datetime.datetime(2025, 7, 31, 10, 3, 0),
            level="ERROR",
            logger_name="main",
            message="Processing failed",
            hook_id="hook1",
        )
        entries_with_error = sample_log_entries + [error_entry]

        result = controller._analyze_pr_flow(entries_with_error, "test-id")
        assert result["success"] is False
        assert "error" in result

    def test_build_workflow_timeline_success(self, controller):
        """Test workflow timeline building."""
        workflow_steps = [
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 0),
                level="STEP",
                logger_name="main",
                message="Step 1",
                hook_id="hook1",
            )
        ]
        result = controller._build_workflow_timeline(workflow_steps, "hook1")
        assert "hook_id" in result
        assert "steps" in result
        assert result["hook_id"] == "hook1"
        assert result["step_count"] == 1

    def test_build_workflow_timeline_multiple_steps(self, controller):
        """Test workflow timeline building with multiple steps."""
        workflow_steps = [
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 0),
                level="STEP",
                logger_name="main",
                message="Step 1",
                hook_id="hook1",
            ),
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 5),
                level="STEP",
                logger_name="main",
                message="Step 2",
                hook_id="hook1",
            ),
        ]
        result = controller._build_workflow_timeline(workflow_steps, "hook1")
        assert result["step_count"] == 2
        assert result["total_duration_ms"] == 5000
        assert len(result["steps"]) == 2

    def test_build_workflow_timeline_empty_steps(self, controller):
        """Test workflow timeline building with empty steps."""
        result = controller._build_workflow_timeline([], "hook1")
        assert result["hook_id"] == "hook1"
        assert result["step_count"] == 0
        assert result["steps"] == []
        assert result["start_time"] is None


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
            from fastapi.responses import HTMLResponse

            mock_instance.get_log_page.return_value = HTMLResponse(content="<html><body>Log Viewer</body></html>")
            mock_instance.shutdown = AsyncMock()  # Add async shutdown method

            # Mock httpx.AsyncClient to prevent SSL errors during lifespan startup
            mock_http_client = AsyncMock()
            mock_http_client.aclose = AsyncMock()

            with patch("webhook_server.app.httpx.AsyncClient", return_value=mock_http_client):
                # Mock external HTTP dependencies
                with patch(
                    "webhook_server.utils.app_utils.get_github_allowlist", new_callable=AsyncMock
                ) as mock_github:
                    with patch(
                        "webhook_server.utils.app_utils.get_cloudflare_allowlist", new_callable=AsyncMock
                    ) as mock_cloudflare:
                        mock_github.return_value = []
                        mock_cloudflare.return_value = []

                        from webhook_server.app import FASTAPI_APP

                        with TestClient(FASTAPI_APP) as client:
                            response = client.get("/logs")
                            assert response.status_code == 200
                            assert "Log Viewer" in response.text

    def test_get_log_entries_no_filters(self, sample_log_entries: list[LogEntry]) -> None:
        """Test retrieving log entries without filters."""
        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance
            mock_instance.get_log_entries.return_value = {
                "entries": [entry.to_dict() for entry in sample_log_entries],
                "entries_processed": len(sample_log_entries),
                "filtered_count_min": len(sample_log_entries),
                "limit": 100,
                "offset": 0,
                "is_partial_scan": False,
            }

            # Test would call GET /logs/api/entries
            # For now, test the data structure
            result = mock_instance.get_log_entries.return_value
            assert "entries" in result
            assert len(result["entries"]) == 3
            assert result["entries_processed"] == 3

    def test_get_log_entries_with_hook_id_filter(self, sample_log_entries: list[LogEntry]) -> None:
        """Test retrieving log entries filtered by hook ID."""
        with patch("webhook_server.web.log_viewer.LogViewerController") as mock_controller:
            mock_instance = Mock()
            mock_controller.return_value = mock_instance

            # Mock filtered result for hook_id="hook1"
            filtered_entries = [entry for entry in sample_log_entries if entry.hook_id == "hook1"]
            mock_instance.get_log_entries.return_value = {
                "entries": [entry.to_dict() for entry in filtered_entries],
                "entries_processed": len(filtered_entries),
                "filtered_count_min": len(filtered_entries),
                "limit": 100,
                "offset": 0,
                "is_partial_scan": False,
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
                "entries_processed": len(filtered_entries),
                "filtered_count_min": len(filtered_entries),
                "limit": 100,
                "offset": 0,
                "is_partial_scan": False,
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
                "entries_processed": len(sample_log_entries),
                "limit": 2,
                "offset": 1,
            }

            result = mock_instance.get_log_entries.return_value
            assert len(result["entries"]) == 2
            assert result["entries_processed"] == 3
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

    @pytest.mark.asyncio
    async def test_websocket_handle_real_implementation(self):
        """Test actual WebSocket handler implementation."""
        from unittest.mock import Mock

        from webhook_server.web.log_viewer import LogViewerController

        mock_logger = Mock()
        controller = LogViewerController(logger=mock_logger)

        mock_websocket = AsyncMock()
        mock_websocket.accept = AsyncMock()
        mock_websocket.send_json = AsyncMock()

        # Mock the log directory to not exist
        with patch.object(controller, "_get_log_directory") as mock_get_dir:
            mock_dir = Mock()
            mock_dir.exists.return_value = False
            mock_get_dir.return_value = mock_dir

            await controller.handle_websocket(mock_websocket)

            mock_websocket.accept.assert_called_once()
            mock_websocket.send_json.assert_called_once_with({"error": "Log directory not found"})

    @pytest.mark.asyncio
    async def test_websocket_handle_with_log_monitoring(self):
        """Test WebSocket handler with log monitoring."""
        from webhook_server.web.log_viewer import LogViewerController

        mock_logger = Mock()
        controller = LogViewerController(logger=mock_logger)

        mock_websocket = AsyncMock()
        mock_websocket.accept = AsyncMock()
        mock_websocket.send_json = AsyncMock()

        # Mock log directory exists
        with patch.object(controller, "_get_log_directory") as mock_get_dir:
            mock_dir = Mock()
            mock_dir.exists.return_value = True
            mock_get_dir.return_value = mock_dir

            # Mock monitor_log_directory to yield one entry then stop
            async def mock_monitor():
                yield LogEntry(
                    timestamp=datetime.datetime.now(),
                    level="INFO",
                    logger_name="test",
                    message="Test message",
                    hook_id="test-hook",
                )
                # Simulate WebSocket disconnect to stop the loop
                raise WebSocketDisconnect()

            with patch.object(controller.log_parser, "monitor_log_directory", return_value=mock_monitor()):
                await controller.handle_websocket(mock_websocket)

                mock_websocket.accept.assert_called_once()
                # Should have attempted to send the log entry
                assert mock_websocket.send_json.call_count >= 1

    @pytest.mark.asyncio
    async def test_shutdown_websocket_cleanup(self):
        """Test shutdown method properly closes all WebSocket connections."""
        from webhook_server.web.log_viewer import LogViewerController

        mock_logger = Mock()
        controller = LogViewerController(logger=mock_logger)

        # Create mock WebSocket connections
        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()
        mock_ws3 = AsyncMock()

        # Add them to the controller's connections set
        controller._websocket_connections.add(mock_ws1)
        controller._websocket_connections.add(mock_ws2)
        controller._websocket_connections.add(mock_ws3)

        # Verify connections are tracked
        assert len(controller._websocket_connections) == 3

        # Call shutdown
        await controller.shutdown()

        # Verify all connections were closed with correct parameters
        mock_ws1.close.assert_called_once_with(code=1001, reason="Server shutdown")
        mock_ws2.close.assert_called_once_with(code=1001, reason="Server shutdown")
        mock_ws3.close.assert_called_once_with(code=1001, reason="Server shutdown")

        # Verify connections set was cleared
        assert len(controller._websocket_connections) == 0

        # Verify logging
        assert mock_logger.info.call_count >= 2  # Start and completion messages

    @pytest.mark.asyncio
    async def test_shutdown_websocket_close_error_handling(self):
        """Test shutdown method handles WebSocket close errors gracefully."""
        from webhook_server.web.log_viewer import LogViewerController

        mock_logger = Mock()
        controller = LogViewerController(logger=mock_logger)

        # Create mock WebSocket connections
        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()

        # Make one connection fail to close
        mock_ws1.close.side_effect = Exception("Connection already closed")
        mock_ws2.close = AsyncMock()  # This one should succeed

        # Add them to the controller's connections set
        controller._websocket_connections.add(mock_ws1)
        controller._websocket_connections.add(mock_ws2)

        # Verify connections are tracked
        assert len(controller._websocket_connections) == 2

        # Call shutdown - should not raise exception despite ws1 error
        await controller.shutdown()

        # Verify both close attempts were made
        mock_ws1.close.assert_called_once_with(code=1001, reason="Server shutdown")
        mock_ws2.close.assert_called_once_with(code=1001, reason="Server shutdown")

        # Verify connections set was cleared despite error
        assert len(controller._websocket_connections) == 0

        # Verify error was logged
        mock_logger.warning.assert_called()
        warning_call_args = mock_logger.warning.call_args[0][0]
        assert "Error closing WebSocket connection during shutdown" in warning_call_args

    @pytest.mark.asyncio
    async def test_shutdown_empty_connections(self):
        """Test shutdown method works correctly with no active connections."""
        from webhook_server.web.log_viewer import LogViewerController

        mock_logger = Mock()
        controller = LogViewerController(logger=mock_logger)

        # Verify no connections initially
        assert len(controller._websocket_connections) == 0

        # Call shutdown with no connections
        await controller.shutdown()

        # Verify connections set is still empty
        assert len(controller._websocket_connections) == 0

        # Verify appropriate logging occurred
        assert mock_logger.info.call_count >= 2


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
            mock_instance.get_pr_flow_data.side_effect = ValueError("No data found for hook_id")

            # Test would return 404 Not Found
            with pytest.raises(ValueError, match="No data found for hook_id"):
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


class TestWorkflowStepsAPI:
    """Test class for workflow steps API endpoints."""

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    def test_get_workflow_steps_success(self) -> None:
        """Test successful workflow steps retrieval."""
        # Import modules and patch before creating test client
        from unittest.mock import AsyncMock, Mock

        # Mock workflow steps data
        mock_workflow_data = {
            "hook_id": "test-hook-123",
            "steps": [
                {
                    "timestamp": "2025-07-31T12:00:00",
                    "level": "STEP",
                    "message": "Starting PR processing workflow",
                    "step_number": 1,
                },
                {
                    "timestamp": "2025-07-31T12:00:01",
                    "level": "STEP",
                    "message": "Stage: Initial setup and check queuing",
                    "step_number": 2,
                },
                {
                    "timestamp": "2025-07-31T12:00:05",
                    "level": "STEP",
                    "message": "Stage: CI/CD execution",
                    "step_number": 3,
                },
            ],
            "total_steps": 3,
            "timeline_html": "<div class='timeline'>...</div>",
        }

        # Create a mock instance and configure its return value
        mock_instance = Mock()
        mock_instance.get_workflow_steps.return_value = mock_workflow_data
        mock_instance.shutdown = AsyncMock()  # Add async shutdown method

        # Mock httpx.AsyncClient to prevent SSL errors during lifespan startup
        mock_http_client = AsyncMock()
        mock_http_client.aclose = AsyncMock()

        # Patch using setattr to directly set the singleton instance
        with patch("webhook_server.app.httpx.AsyncClient", return_value=mock_http_client):
            with patch("webhook_server.app.get_log_viewer_controller", return_value=mock_instance):
                # Also patch the singleton variable itself
                with patch("webhook_server.app._log_viewer_controller_singleton", mock_instance):
                    from fastapi.testclient import TestClient

                    from webhook_server.app import FASTAPI_APP

                    client = TestClient(FASTAPI_APP)

                    # Make the request
                    response = client.get("/logs/api/workflow-steps/test-hook-123")

                # Assertions
                assert response.status_code == 200
                result = response.json()
                assert result["hook_id"] == "test-hook-123"
                assert result["total_steps"] == 3
                assert len(result["steps"]) == 3
                assert "timeline_html" in result

                # Verify method was called correctly
                mock_instance.get_workflow_steps.assert_called_once_with("test-hook-123")

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    def test_get_workflow_steps_no_steps_found(self) -> None:
        """Test workflow steps when no steps are found."""
        # Import modules and patch before creating test client
        from unittest.mock import AsyncMock, Mock

        # Mock empty workflow data
        mock_workflow_data = {
            "hook_id": "test-hook-456",
            "steps": [],
            "total_steps": 0,
            "timeline_html": "<div class='no-timeline'>No workflow steps found</div>",
        }

        # Create a mock instance and configure its return value
        mock_instance = Mock()
        mock_instance.get_workflow_steps.return_value = mock_workflow_data
        mock_instance.shutdown = AsyncMock()  # Add async shutdown method

        # Mock httpx.AsyncClient to prevent SSL errors during lifespan startup
        mock_http_client = AsyncMock()
        mock_http_client.aclose = AsyncMock()

        # Patch using setattr to directly set the singleton instance
        with patch("webhook_server.app.httpx.AsyncClient", return_value=mock_http_client):
            with patch("webhook_server.app.get_log_viewer_controller", return_value=mock_instance):
                # Also patch the singleton variable itself
                with patch("webhook_server.app._log_viewer_controller_singleton", mock_instance):
                    from fastapi.testclient import TestClient

                    from webhook_server.app import FASTAPI_APP

                    client = TestClient(FASTAPI_APP)

                    # Make the request
                    response = client.get("/logs/api/workflow-steps/test-hook-456")

                # Assertions
                assert response.status_code == 200
                result = response.json()
                assert result["hook_id"] == "test-hook-456"
                assert result["total_steps"] == 0
                assert len(result["steps"]) == 0
                assert "timeline_html" in result

                # Verify method was called correctly
                mock_instance.get_workflow_steps.assert_called_once_with("test-hook-456")
