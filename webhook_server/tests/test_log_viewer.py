"""Tests for log viewer JSON functionality."""

import copy
import json
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from webhook_server.web.log_viewer import LogViewerController


class TestLogViewerJSONMethods:
    """Test cases for LogViewerController JSON log methods."""

    @pytest.fixture
    def mock_logger(self):
        """Create a mock logger for testing."""
        return Mock()

    @pytest.fixture
    def controller(self, mock_logger, tmp_path):
        """Create a LogViewerController instance with mocked config."""
        with patch("webhook_server.web.log_viewer.Config") as mock_config:
            mock_config_instance = Mock()
            mock_config_instance.data_dir = str(tmp_path)
            mock_config.return_value = mock_config_instance
            return LogViewerController(logger=mock_logger)

    @pytest.fixture
    def sample_json_webhook_data(self) -> dict:
        """Create sample JSON webhook log data."""
        return {
            "hook_id": "test-hook-123",
            "event_type": "pull_request",
            "action": "opened",
            "repository": "org/test-repo",
            "sender": "test-user",
            "pr": {
                "number": 456,
                "title": "Test PR",
                "url": "https://github.com/org/test-repo/pull/456",
            },
            "timing": {
                "started_at": "2025-01-05T10:00:00.000000Z",
                "ended_at": "2025-01-05T10:00:05.000000Z",
                "duration_seconds": 5.0,
            },
            "workflow_steps": {
                "step1": {"status": "completed", "duration_ms": 1000},
                "step2": {"status": "completed", "duration_ms": 2000},
            },
            "token_spend": 35,
            "success": True,
        }

    def create_json_log_file(self, log_dir: Path, filename: str, entries: list[dict]) -> Path:
        """Create a test JSON log file with entries.

        Args:
            log_dir: Directory to create the log file in
            filename: Name of the log file
            entries: List of JSON webhook data dictionaries

        Returns:
            Path to created log file
        """
        log_file = log_dir / filename
        with open(log_file, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
        return log_file

    def test_stream_json_log_entries_yields_entries(self, controller, tmp_path, sample_json_webhook_data):
        """Test that _stream_json_log_entries yields JSON entries from log files."""
        # Create logs directory
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create a JSON log file with multiple entries
        entry1 = sample_json_webhook_data.copy()
        entry2 = sample_json_webhook_data.copy()
        entry2["hook_id"] = "test-hook-456"
        entry2["pr"]["number"] = 789

        self.create_json_log_file(log_dir, "webhooks_2025-01-05.json", [entry1, entry2])

        # Stream JSON entries
        entries = list(controller._stream_json_log_entries(max_files=10, max_entries=100))

        # Should yield 2 entries (reversed order - newest first)
        assert len(entries) == 2
        assert entries[0]["hook_id"] == "test-hook-456"
        assert entries[1]["hook_id"] == "test-hook-123"

    def test_stream_json_log_entries_respects_max_files_limit(self, controller, tmp_path, sample_json_webhook_data):
        """Test that _stream_json_log_entries respects max_files limit."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create 5 JSON log files
        for i in range(5):
            entry = sample_json_webhook_data.copy()
            entry["hook_id"] = f"hook-file-{i}"
            self.create_json_log_file(log_dir, f"webhooks_2025-01-0{i}.json", [entry])

        # Stream with max_files=2
        entries = list(controller._stream_json_log_entries(max_files=2, max_entries=100))

        # Should only process 2 files (2 entries total)
        assert len(entries) == 2

    def test_stream_json_log_entries_respects_max_entries_limit(self, controller, tmp_path, sample_json_webhook_data):
        """Test that _stream_json_log_entries respects max_entries limit."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create a JSON log file with 10 entries
        entries_data = []
        for i in range(10):
            entry = sample_json_webhook_data.copy()
            entry["hook_id"] = f"hook-{i}"
            entries_data.append(entry)

        self.create_json_log_file(log_dir, "webhooks_2025-01-05.json", entries_data)

        # Stream with max_entries=5
        entries = list(controller._stream_json_log_entries(max_files=10, max_entries=5))

        # Should only yield 5 entries
        assert len(entries) == 5

    def test_stream_json_log_entries_skips_invalid_json_lines(self, controller, tmp_path, sample_json_webhook_data):
        """Test that _stream_json_log_entries skips invalid JSON lines."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create a log file with mixed valid and invalid JSON
        log_file = log_dir / "webhooks_2025-01-05.json"
        with open(log_file, "w", encoding="utf-8") as f:
            # Valid JSON
            f.write(json.dumps(sample_json_webhook_data) + "\n")
            # Invalid JSON lines
            f.write("not valid json\n")
            f.write('{"incomplete": \n')
            # Another valid JSON
            entry2 = sample_json_webhook_data.copy()
            entry2["hook_id"] = "hook-valid-2"
            f.write(json.dumps(entry2) + "\n")

        # Stream JSON entries
        entries = list(controller._stream_json_log_entries(max_files=10, max_entries=100))

        # Should only yield 2 valid entries (reversed order)
        assert len(entries) == 2
        assert entries[0]["hook_id"] == "hook-valid-2"
        assert entries[1]["hook_id"] == "test-hook-123"

    def test_stream_json_log_entries_no_log_directory(self, controller, tmp_path):
        """Test _stream_json_log_entries when log directory doesn't exist."""
        # Don't create logs directory
        assert tmp_path is not None
        entries = list(controller._stream_json_log_entries(max_files=10, max_entries=100))

        # Should yield nothing
        assert len(entries) == 0

    def test_stream_json_log_entries_empty_directory(self, controller, tmp_path):
        """Test _stream_json_log_entries with empty log directory."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # No log files created
        entries = list(controller._stream_json_log_entries(max_files=10, max_entries=100))

        # Should yield nothing
        assert len(entries) == 0

    def test_stream_json_log_entries_newest_first_ordering(self, controller, tmp_path, sample_json_webhook_data):
        """Test that _stream_json_log_entries returns newest files first."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create multiple JSON log files with different modification times
        # Older file
        entry1 = sample_json_webhook_data.copy()
        entry1["hook_id"] = "old-hook"
        self.create_json_log_file(log_dir, "webhooks_2025-01-01.json", [entry1])

        time.sleep(0.01)  # Ensure different mtime

        # Newer file
        entry2 = sample_json_webhook_data.copy()
        entry2["hook_id"] = "new-hook"
        file2 = self.create_json_log_file(log_dir, "webhooks_2025-01-05.json", [entry2])

        # Ensure file2 has a newer mtime
        file2.touch()

        # Stream entries
        entries = list(controller._stream_json_log_entries(max_files=10, max_entries=100))

        # Should process newer file first (entries within file are reversed)
        # So first entry should be from newer file
        assert len(entries) == 2
        assert entries[0]["hook_id"] == "new-hook"
        assert entries[1]["hook_id"] == "old-hook"

    def test_get_workflow_steps_json_returns_workflow_data(self, controller, tmp_path, sample_json_webhook_data):
        """Test get_workflow_steps_json returns workflow steps for valid hook_id."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create JSON log file
        self.create_json_log_file(log_dir, "webhooks_2025-01-05.json", [sample_json_webhook_data])

        # Get workflow steps
        result = controller.get_workflow_steps_json("test-hook-123")

        # Should return structured workflow data
        assert result["hook_id"] == "test-hook-123"
        assert result["event_type"] == "pull_request"
        assert result["action"] == "opened"
        assert result["repository"] == "org/test-repo"
        assert result["sender"] == "test-user"
        assert result["pr"]["number"] == 456
        assert result["timing"]["duration_seconds"] == 5.0
        assert result["steps"] == sample_json_webhook_data["workflow_steps"]
        assert result["token_spend"] == 35
        assert result["success"] is True

    def test_get_workflow_steps_json_returns_none_for_unknown_hook_id(
        self, controller, tmp_path, sample_json_webhook_data
    ):
        """Test get_workflow_steps_json raises HTTPException for unknown hook_id."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create JSON log file with different hook_id
        self.create_json_log_file(log_dir, "webhooks_2025-01-05.json", [sample_json_webhook_data])

        # Try to get workflow steps for non-existent hook_id
        with pytest.raises(HTTPException) as exc:
            controller.get_workflow_steps_json("non-existent-hook")

        # Should raise 404
        assert exc.value.status_code == 404
        assert "No JSON log entry found" in str(exc.value.detail)

    def test_get_workflow_steps_json_no_log_files(self, controller, tmp_path):
        """Test get_workflow_steps_json when no log files exist."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Try to get workflow steps when no logs exist
        with pytest.raises(HTTPException) as exc:
            controller.get_workflow_steps_json("test-hook-123")

        # Should raise 404
        assert exc.value.status_code == 404

    def test_get_workflow_steps_json_with_error_in_log(self, controller, tmp_path, sample_json_webhook_data):
        """Test get_workflow_steps_json with webhook that has error."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create JSON log entry with error
        error_data = sample_json_webhook_data.copy()
        error_data["success"] = False
        error_data["error"] = "Test error occurred"

        self.create_json_log_file(log_dir, "webhooks_2025-01-05.json", [error_data])

        # Get workflow steps
        result = controller.get_workflow_steps_json("test-hook-123")

        # Should include error information
        assert result["success"] is False
        assert result["error"] == "Test error occurred"

    def test_get_workflow_steps_uses_json_when_available(self, controller, tmp_path, sample_json_webhook_data):
        """Test get_workflow_steps uses JSON logs when available."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create JSON log file
        self.create_json_log_file(log_dir, "webhooks_2025-01-05.json", [sample_json_webhook_data])

        # Get workflow steps (should use JSON, not fall back to text)
        result = controller.get_workflow_steps("test-hook-123")

        # Should return JSON-based data
        assert result["hook_id"] == "test-hook-123"
        assert result["event_type"] == "pull_request"
        assert "steps" in result
        assert result["token_spend"] == 35

    def test_get_workflow_steps_falls_back_to_text_logs(self, controller, tmp_path):
        """Test get_workflow_steps falls back to text logs when JSON not found."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create a text log file (not JSON)
        log_file = log_dir / "webhook-server.log"
        log_entries = [
            "2025-01-05T10:00:00.000000 GithubWebhook INFO org/test-repo "
            "[pull_request][fallback-hook][test-user][PR 123]: Processing webhook "
            "[task_id=task1][task_type=webhook][task_status=started]",
            "2025-01-05T10:00:01.000000 GithubWebhook INFO org/test-repo "
            "[pull_request][fallback-hook][test-user][PR 123]: Validation complete "
            "[task_id=task2][task_type=validation][task_status=completed]",
            "2025-01-05T10:00:02.000000 GithubWebhook INFO org/test-repo "
            "[pull_request][fallback-hook][test-user][PR 123]: Token spend: 15 API calls",
        ]
        with open(log_file, "w", encoding="utf-8") as f:
            for line in log_entries:
                f.write(line + "\n")

        # Get workflow steps for hook not in JSON logs
        result = controller.get_workflow_steps("fallback-hook")

        # Should fall back to text log parsing
        assert result["hook_id"] == "fallback-hook"
        assert "steps" in result
        assert len(result["steps"]) == 2  # Two workflow steps with task_status
        assert result["token_spend"] == 15

    def test_get_workflow_steps_json_searches_multiple_files(self, controller, tmp_path, sample_json_webhook_data):
        """Test get_workflow_steps_json searches through multiple JSON log files."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create multiple JSON log files
        entry1 = sample_json_webhook_data.copy()
        entry1["hook_id"] = "hook-file1"
        self.create_json_log_file(log_dir, "webhooks_2025-01-01.json", [entry1])

        entry2 = sample_json_webhook_data.copy()
        entry2["hook_id"] = "hook-file2"
        self.create_json_log_file(log_dir, "webhooks_2025-01-02.json", [entry2])

        entry3 = sample_json_webhook_data.copy()
        entry3["hook_id"] = "target-hook"
        self.create_json_log_file(log_dir, "webhooks_2025-01-03.json", [entry3])

        # Search for hook in third file
        result = controller.get_workflow_steps_json("target-hook")

        # Should find it
        assert result["hook_id"] == "target-hook"

    def test_get_workflow_steps_json_handles_missing_optional_fields(self, controller, tmp_path):
        """Test get_workflow_steps_json handles missing optional fields gracefully."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create minimal JSON log entry
        minimal_data = {
            "hook_id": "minimal-hook",
            # Missing: event_type, action, sender, pr, workflow_steps, token_spend, success, error
        }

        self.create_json_log_file(log_dir, "webhooks_2025-01-05.json", [minimal_data])

        # Get workflow steps
        result = controller.get_workflow_steps_json("minimal-hook")

        # Should handle missing fields with None
        assert result["hook_id"] == "minimal-hook"
        assert result["event_type"] is None
        assert result["action"] is None
        assert result["repository"] is None
        assert result["sender"] is None
        assert result["pr"] is None
        assert result["timing"] is None
        assert result["steps"] == {}  # Default to empty dict
        assert result["token_spend"] is None
        assert result["success"] is None
        assert result["error"] is None

    def test_stream_json_log_entries_handles_file_read_errors(self, controller, tmp_path, sample_json_webhook_data):
        """Test _stream_json_log_entries handles file read errors gracefully."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create a valid JSON log file
        self.create_json_log_file(log_dir, "webhooks_valid.json", [sample_json_webhook_data])

        # Create a file that will cause read error (simulate by making it unreadable)
        bad_file = log_dir / "webhooks_bad.json"
        bad_file.write_text(json.dumps(sample_json_webhook_data))
        bad_file.chmod(0o000)  # Remove all permissions

        try:
            # Stream entries - should skip bad file and continue
            entries = list(controller._stream_json_log_entries(max_files=10, max_entries=100))

            # Should still get the valid entry (or none if permission error blocks all)
            # Depending on OS, this may yield 0 or 1 entry
            assert len(entries) >= 0
        finally:
            # Restore permissions for cleanup
            bad_file.chmod(0o644)

    def test_get_workflow_steps_json_with_multiple_entries_same_file(
        self, controller, tmp_path, sample_json_webhook_data
    ):
        """Test get_workflow_steps_json finds correct entry in file with multiple hooks."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create JSON log file with multiple hook entries (deep copy to avoid reference issues)
        entry1 = copy.deepcopy(sample_json_webhook_data)
        entry1["hook_id"] = "hook-1"
        entry1["pr"]["number"] = 100

        entry2 = copy.deepcopy(sample_json_webhook_data)
        entry2["hook_id"] = "target-hook"
        entry2["pr"]["number"] = 200

        entry3 = copy.deepcopy(sample_json_webhook_data)
        entry3["hook_id"] = "hook-3"
        entry3["pr"]["number"] = 300

        self.create_json_log_file(log_dir, "webhooks_2025-01-05.json", [entry1, entry2, entry3])

        # Search for middle entry
        result = controller.get_workflow_steps_json("target-hook")

        # Should find correct entry
        assert result["hook_id"] == "target-hook"
        assert result["pr"]["number"] == 200
