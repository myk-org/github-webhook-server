"""Comprehensive tests for structured logger functionality."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from webhook_server.libs.config import Config
from webhook_server.utils.context import WebhookContext
from webhook_server.utils.structured_logger import StructuredLogWriter, write_webhook_log


class TestStructuredLogWriter:
    """Test suite for StructuredLogWriter class."""

    @pytest.fixture
    def mock_config(self, tmp_path: Path) -> Mock:
        """Create a mock Config with temporary data directory."""
        config = Mock(spec=Config)
        config.data_dir = str(tmp_path)
        return config

    @pytest.fixture
    def mock_logger(self) -> Mock:
        """Create a mock logger."""
        return Mock()

    @pytest.fixture
    def log_writer(self, mock_config: Mock, mock_logger: Mock) -> StructuredLogWriter:
        """Create StructuredLogWriter instance with mocks."""
        return StructuredLogWriter(config=mock_config, logger=mock_logger)

    @pytest.fixture
    def sample_context(self) -> WebhookContext:
        """Create a sample WebhookContext for testing."""
        return WebhookContext(
            hook_id="test-hook-123",
            event_type="pull_request",
            repository="org/repo",
            repository_full_name="org/repo",
            action="opened",
            sender="test-user",
            pr_number=42,
            pr_title="Test PR",
            pr_author="pr-author",
            api_user="api-bot",
        )

    def test_init_creates_log_directory(self, tmp_path: Path, mock_config: Mock, mock_logger: Mock) -> None:
        """Test that __init__ creates the log directory."""
        # Arrange
        log_dir = tmp_path / "logs"
        assert not log_dir.exists()

        # Act
        writer = StructuredLogWriter(config=mock_config, logger=mock_logger)

        # Assert
        assert log_dir.exists()
        assert log_dir.is_dir()
        assert writer.config == mock_config
        assert writer.logger == mock_logger
        assert writer.log_dir == log_dir

    def test_init_with_existing_log_directory(self, tmp_path: Path, mock_config: Mock, mock_logger: Mock) -> None:
        """Test that __init__ works when log directory already exists."""
        # Arrange
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        assert log_dir.exists()

        # Act
        writer = StructuredLogWriter(config=mock_config, logger=mock_logger)

        # Assert
        assert writer.log_dir == log_dir
        assert log_dir.exists()

    def test_init_without_logger_creates_default(self, mock_config: Mock) -> None:
        """Test that __init__ creates a default logger if not provided."""
        # Act
        writer = StructuredLogWriter(config=mock_config)

        # Assert
        assert writer.logger is not None

    def test_get_log_file_path_default_date(self, log_writer: StructuredLogWriter, tmp_path: Path) -> None:
        """Test _get_log_file_path returns correct path with default (current) date."""
        # Arrange
        expected_date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        expected_path = tmp_path / "logs" / f"webhooks_{expected_date_str}.json"

        # Act
        result = log_writer._get_log_file_path()

        # Assert
        assert result == expected_path

    def test_get_log_file_path_with_specific_date(self, log_writer: StructuredLogWriter, tmp_path: Path) -> None:
        """Test _get_log_file_path returns correct path with specific date."""
        # Arrange
        test_date = datetime(2026, 1, 5, 12, 30, 45, tzinfo=UTC)
        expected_path = tmp_path / "logs" / "webhooks_2026-01-05.json"

        # Act
        result = log_writer._get_log_file_path(date=test_date)

        # Assert
        assert result == expected_path

    def test_write_log_writes_valid_json(
        self, log_writer: StructuredLogWriter, sample_context: WebhookContext, tmp_path: Path
    ) -> None:
        """Test write_log writes valid JSON to correct file."""
        # Act
        log_writer.write_log(sample_context)

        # Assert
        log_file = tmp_path / "logs" / f"webhooks_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
        assert log_file.exists()

        # Read and validate JSON (pretty-printed format with blank line separator)
        with open(log_file) as f:
            content = f.read().strip()
            # Pretty-printed JSON is multi-line, parse the entire content as one JSON object
            log_entry = json.loads(content)
            assert log_entry["hook_id"] == "test-hook-123"
            assert log_entry["event_type"] == "pull_request"
            assert log_entry["repository"] == "org/repo"
            assert log_entry["action"] == "opened"
            assert log_entry["sender"] == "test-user"

    def test_write_log_sets_completed_at(
        self, log_writer: StructuredLogWriter, sample_context: WebhookContext, tmp_path: Path
    ) -> None:
        """Test write_log sets completed_at timestamp in timing."""
        # Arrange - context without completed_at
        assert sample_context.completed_at is None

        # Act
        log_writer.write_log(sample_context)

        # Assert
        log_file = tmp_path / "logs" / f"webhooks_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
        with open(log_file) as f:
            log_entry = json.loads(f.read().strip())

        assert "timing" in log_entry
        assert log_entry["timing"]["completed_at"] is not None
        # Verify it's a valid ISO format timestamp
        completed_at = datetime.fromisoformat(log_entry["timing"]["completed_at"])
        assert completed_at.tzinfo is not None

    def test_write_log_calculates_duration(
        self, log_writer: StructuredLogWriter, sample_context: WebhookContext, tmp_path: Path
    ) -> None:
        """Test write_log calculates duration_ms when started_at is available."""
        # Arrange - set started_at to 5 seconds ago
        sample_context.started_at = datetime.now(UTC) - timedelta(seconds=5)

        # Act
        log_writer.write_log(sample_context)

        # Assert
        log_file = tmp_path / "logs" / f"webhooks_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
        with open(log_file) as f:
            log_entry = json.loads(f.read().strip())

        assert "timing" in log_entry
        assert log_entry["timing"]["duration_ms"] is not None
        # Duration should be approximately 5000ms (allowing some tolerance)
        assert 4900 <= log_entry["timing"]["duration_ms"] <= 6000

    def test_write_log_does_not_mutate_context(
        self, log_writer: StructuredLogWriter, sample_context: WebhookContext
    ) -> None:
        """Test write_log does not mutate the original context."""
        # Arrange - capture original state
        original_completed_at = sample_context.completed_at

        # Act
        log_writer.write_log(sample_context)

        # Assert - context unchanged
        assert sample_context.completed_at == original_completed_at

    def test_write_log_multiple_entries_append(
        self, log_writer: StructuredLogWriter, sample_context: WebhookContext, tmp_path: Path
    ) -> None:
        """Test multiple writes append to same file (pretty-printed format)."""
        # Arrange
        context2 = WebhookContext(
            hook_id="test-hook-456",
            event_type="issue_comment",
            repository="org/repo2",
            repository_full_name="org/repo2",
        )

        # Act
        log_writer.write_log(sample_context)
        log_writer.write_log(context2)

        # Assert
        log_file = tmp_path / "logs" / f"webhooks_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
        with open(log_file) as f:
            content = f.read().strip()

        # Split by double newline to separate pretty-printed JSON entries
        json_blocks = content.split("\n\n")
        assert len(json_blocks) == 2

        entry1 = json.loads(json_blocks[0])
        entry2 = json.loads(json_blocks[1])

        assert entry1["hook_id"] == "test-hook-123"
        assert entry2["hook_id"] == "test-hook-456"

    def test_write_log_with_workflow_steps(
        self, log_writer: StructuredLogWriter, sample_context: WebhookContext, tmp_path: Path
    ) -> None:
        """Test write_log includes workflow steps from context."""
        # Arrange
        sample_context.start_step("clone_repository", branch="main")
        sample_context.complete_step("clone_repository", commit_sha="abc123")

        # Act
        log_writer.write_log(sample_context)

        # Assert
        log_file = tmp_path / "logs" / f"webhooks_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
        with open(log_file) as f:
            log_entry = json.loads(f.read().strip())

        assert "workflow_steps" in log_entry
        assert "clone_repository" in log_entry["workflow_steps"]
        assert log_entry["workflow_steps"]["clone_repository"]["status"] == "completed"
        assert log_entry["workflow_steps"]["clone_repository"]["commit_sha"] == "abc123"

    def test_write_log_with_error(
        self, log_writer: StructuredLogWriter, sample_context: WebhookContext, tmp_path: Path
    ) -> None:
        """Test write_log includes error details from context."""
        # Arrange
        sample_context.success = False
        sample_context.error = {
            "type": "ValueError",
            "message": "Something went wrong",
            "traceback": "Traceback...",
        }

        # Act
        log_writer.write_log(sample_context)

        # Assert
        log_file = tmp_path / "logs" / f"webhooks_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
        with open(log_file) as f:
            log_entry = json.loads(f.read().strip())

        assert log_entry["success"] is False
        assert log_entry["error"] is not None
        assert log_entry["error"]["type"] == "ValueError"
        assert log_entry["error"]["message"] == "Something went wrong"

    def test_write_log_with_pr_details(
        self, log_writer: StructuredLogWriter, sample_context: WebhookContext, tmp_path: Path
    ) -> None:
        """Test write_log includes PR details when available."""
        # Act
        log_writer.write_log(sample_context)

        # Assert
        log_file = tmp_path / "logs" / f"webhooks_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
        with open(log_file) as f:
            log_entry = json.loads(f.read().strip())

        assert log_entry["pr"] is not None
        assert log_entry["pr"]["number"] == 42
        assert log_entry["pr"]["title"] == "Test PR"
        assert log_entry["pr"]["author"] == "pr-author"

    def test_write_log_without_pr_details(self, log_writer: StructuredLogWriter, tmp_path: Path) -> None:
        """Test write_log handles context without PR details."""
        # Arrange
        context = WebhookContext(
            hook_id="test-hook-789",
            event_type="push",
            repository="org/repo",
            repository_full_name="org/repo",
        )

        # Act
        log_writer.write_log(context)

        # Assert
        log_file = tmp_path / "logs" / f"webhooks_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
        with open(log_file) as f:
            log_entry = json.loads(f.read().strip())

        assert log_entry["pr"] is None

    @patch("webhook_server.utils.structured_logger.HAS_FCNTL", False)
    def test_write_log_without_fcntl(
        self, log_writer: StructuredLogWriter, sample_context: WebhookContext, tmp_path: Path
    ) -> None:
        """Test write_log works on platforms without fcntl (Windows)."""
        # Act
        log_writer.write_log(sample_context)

        # Assert
        log_file = tmp_path / "logs" / f"webhooks_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
        assert log_file.exists()

        with open(log_file) as f:
            log_entry = json.loads(f.read().strip())

        assert log_entry["hook_id"] == "test-hook-123"

    @patch("webhook_server.utils.structured_logger.HAS_FCNTL", True)
    @patch("fcntl.flock")
    def test_write_log_uses_file_locking(
        self, mock_flock: Mock, log_writer: StructuredLogWriter, sample_context: WebhookContext
    ) -> None:
        """Test write_log uses file locking when fcntl is available."""
        # Act
        log_writer.write_log(sample_context)

        # Assert - flock called for both temp file and log file
        assert mock_flock.call_count >= 2  # At least lock and unlock

    def test_write_log_handles_exception_gracefully(
        self, log_writer: StructuredLogWriter, sample_context: WebhookContext, mock_logger: Mock
    ) -> None:
        """Test write_log handles exceptions and logs them."""
        # Arrange - make tempfile.mkstemp fail
        with patch("tempfile.mkstemp", side_effect=OSError("Disk full")):
            # Act
            log_writer.write_log(sample_context)

            # Assert
            mock_logger.exception.assert_called_once()
            assert "Failed to write webhook log entry" in str(mock_logger.exception.call_args)

    def test_write_log_logs_success(
        self, log_writer: StructuredLogWriter, sample_context: WebhookContext, mock_logger: Mock
    ) -> None:
        """Test write_log logs debug message on success."""
        # Act
        log_writer.write_log(sample_context)

        # Assert
        mock_logger.debug.assert_called_once()
        debug_msg = str(mock_logger.debug.call_args)
        assert "test-hook-123" in debug_msg
        assert "pull_request" in debug_msg

    def test_write_error_log_with_partial_context(
        self, log_writer: StructuredLogWriter, sample_context: WebhookContext, tmp_path: Path
    ) -> None:
        """Test write_error_log with partial context."""
        # Arrange
        sample_context.success = True  # Initially success
        sample_context.error = None

        # Act
        log_writer.write_error_log(
            hook_id="test-hook-123",
            event_type="pull_request",
            repository="org/repo",
            error_message="Early failure",
            context=sample_context,
        )

        # Assert
        log_file = tmp_path / "logs" / f"webhooks_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
        with open(log_file) as f:
            log_entry = json.loads(f.read().strip())

        assert log_entry["success"] is False
        assert log_entry["error"] is not None
        assert log_entry["error"]["message"] == "Early failure"

    def test_write_error_log_without_context(self, log_writer: StructuredLogWriter, tmp_path: Path) -> None:
        """Test write_error_log creates minimal entry when no context available."""
        # Act
        log_writer.write_error_log(
            hook_id="test-hook-error",
            event_type="push",
            repository="org/repo",
            error_message="Critical error",
            context=None,
        )

        # Assert
        log_file = tmp_path / "logs" / f"webhooks_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
        with open(log_file) as f:
            log_entry = json.loads(f.read().strip())

        assert log_entry["hook_id"] == "test-hook-error"
        assert log_entry["event_type"] == "push"
        assert log_entry["repository"] == "org/repo"
        assert log_entry["success"] is False
        assert log_entry["error"]["message"] == "Critical error"
        assert log_entry["pr"] is None
        assert log_entry["action"] is None

    def test_write_error_log_preserves_existing_error(
        self, log_writer: StructuredLogWriter, sample_context: WebhookContext, tmp_path: Path
    ) -> None:
        """Test write_error_log preserves existing error in context."""
        # Arrange
        sample_context.error = {
            "type": "ExistingError",
            "message": "Original error",
            "traceback": "Original traceback",
        }

        # Act
        log_writer.write_error_log(
            hook_id="test-hook-123",
            event_type="pull_request",
            repository="org/repo",
            error_message="New error",
            context=sample_context,
        )

        # Assert
        log_file = tmp_path / "logs" / f"webhooks_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
        with open(log_file) as f:
            log_entry = json.loads(f.read().strip())

        # Original error preserved
        assert log_entry["error"]["type"] == "ExistingError"
        assert log_entry["error"]["message"] == "Original error"

    @patch("webhook_server.utils.structured_logger.HAS_FCNTL", True)
    @patch("fcntl.flock")
    def test_write_error_log_uses_file_locking(self, mock_flock: Mock, log_writer: StructuredLogWriter) -> None:
        """Test write_error_log uses file locking when fcntl is available."""
        # Act
        log_writer.write_error_log(
            hook_id="test-hook",
            event_type="push",
            repository="org/repo",
            error_message="Error message",
            context=None,
        )

        # Assert
        assert mock_flock.call_count >= 1

    def test_write_error_log_handles_exception(self, log_writer: StructuredLogWriter, mock_logger: Mock) -> None:
        """Test write_error_log handles exceptions gracefully."""
        # Arrange - make open fail
        with patch("builtins.open", side_effect=OSError("Disk full")):
            # Act
            log_writer.write_error_log(
                hook_id="test-hook",
                event_type="push",
                repository="org/repo",
                error_message="Error message",
                context=None,
            )

            # Assert
            mock_logger.exception.assert_called_once()


class TestWriteWebhookLogFunction:
    """Test suite for write_webhook_log module-level function."""

    @pytest.fixture
    def sample_context(self) -> WebhookContext:
        """Create a sample WebhookContext for testing."""
        return WebhookContext(
            hook_id="test-hook-func",
            event_type="pull_request",
            repository="org/repo",
            repository_full_name="org/repo",
        )

    def test_write_webhook_log_with_provided_context(self, sample_context: WebhookContext, tmp_path: Path) -> None:
        """Test write_webhook_log uses provided context."""
        # Arrange
        with patch("webhook_server.utils.structured_logger.Config") as mock_config_class:
            mock_config = Mock()
            mock_config.data_dir = str(tmp_path)
            mock_config_class.return_value = mock_config

            # Act
            write_webhook_log(context=sample_context)

            # Assert
            log_file = tmp_path / "logs" / f"webhooks_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
            assert log_file.exists()

            with open(log_file) as f:
                log_entry = json.loads(f.read().strip())

            assert log_entry["hook_id"] == "test-hook-func"

    def test_write_webhook_log_uses_context_var(self, sample_context: WebhookContext, tmp_path: Path) -> None:
        """Test write_webhook_log gets context from ContextVar when not provided."""
        # Arrange
        with patch("webhook_server.utils.structured_logger.Config") as mock_config_class:
            mock_config = Mock()
            mock_config.data_dir = str(tmp_path)
            mock_config_class.return_value = mock_config

            with patch("webhook_server.utils.structured_logger.get_context", return_value=sample_context):
                # Act
                write_webhook_log()

                # Assert
                log_file = tmp_path / "logs" / f"webhooks_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
                assert log_file.exists()

                with open(log_file) as f:
                    log_entry = json.loads(f.read().strip())

                assert log_entry["hook_id"] == "test-hook-func"

    def test_write_webhook_log_raises_when_no_context(self) -> None:
        """Test write_webhook_log raises ValueError when no context available."""
        # Arrange
        with patch("webhook_server.utils.structured_logger.get_context", return_value=None):
            # Act & Assert
            with pytest.raises(ValueError, match="No webhook context available"):
                write_webhook_log()

    def test_write_webhook_log_creates_config_and_logger(self, sample_context: WebhookContext) -> None:
        """Test write_webhook_log creates Config and logger instances."""
        # Arrange
        with (
            patch("webhook_server.utils.structured_logger.Config") as mock_config_class,
            patch("webhook_server.utils.structured_logger.get_logger") as mock_get_logger,
            patch("webhook_server.utils.structured_logger.StructuredLogWriter") as mock_writer_class,
        ):
            mock_config = Mock()
            mock_logger = Mock()
            mock_writer = Mock()

            mock_config_class.return_value = mock_config
            mock_get_logger.return_value = mock_logger
            mock_writer_class.return_value = mock_writer

            # Act
            write_webhook_log(context=sample_context)

            # Assert
            mock_config_class.assert_called_once_with()
            mock_get_logger.assert_called_once_with(name="structured_logger")
            mock_writer_class.assert_called_once_with(config=mock_config, logger=mock_logger)
            mock_writer.write_log.assert_called_once_with(sample_context)


class TestEdgeCases:
    """Test edge cases and error conditions."""

    @pytest.fixture
    def mock_config(self, tmp_path: Path) -> Mock:
        """Create a mock Config with temporary data directory."""
        config = Mock(spec=Config)
        config.data_dir = str(tmp_path)
        return config

    def test_write_log_with_unicode_content(self, mock_config: Mock, tmp_path: Path) -> None:
        """Test write_log handles Unicode content correctly."""
        # Arrange
        writer = StructuredLogWriter(config=mock_config)
        context = WebhookContext(
            hook_id="test-unicode",
            event_type="pull_request",
            repository="org/repo",
            repository_full_name="org/repo",
            pr_number=1,
            pr_title="测试 🚀 émojis",
            sender="用户",
        )

        # Act
        writer.write_log(context)

        # Assert
        log_file = tmp_path / "logs" / f"webhooks_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
        with open(log_file, encoding="utf-8") as f:
            log_entry = json.loads(f.read().strip())

        assert log_entry["pr"]["title"] == "测试 🚀 émojis"
        assert log_entry["sender"] == "用户"

    def test_write_log_temp_file_cleanup_on_error(self, mock_config: Mock, tmp_path: Path) -> None:
        """Test temporary file is cleaned up even when an error occurs."""
        # Arrange
        writer = StructuredLogWriter(config=mock_config)
        context = WebhookContext(
            hook_id="test-cleanup",
            event_type="pull_request",
            repository="org/repo",
            repository_full_name="org/repo",
        )

        # Count temp files before
        temp_files_before = list((tmp_path / "logs").glob(".webhooks_*.tmp"))

        # Act - cause an error during write
        with patch("os.fsync", side_effect=OSError("Sync failed")):
            writer.write_log(context)

        # Assert - no temp files left behind
        temp_files_after = list((tmp_path / "logs").glob(".webhooks_*.tmp"))
        assert len(temp_files_after) == len(temp_files_before)

    def test_write_log_handles_missing_timing_in_context_dict(self, mock_config: Mock, tmp_path: Path) -> None:
        """Test write_log handles context without timing key."""
        # Arrange
        writer = StructuredLogWriter(config=mock_config)
        context = Mock(spec=WebhookContext)
        context.hook_id = "test"
        context.event_type = "push"
        context.repository = "org/repo"
        context.started_at = None
        context.to_dict = Mock(return_value={"hook_id": "test", "event_type": "push"})

        # Act
        writer.write_log(context)

        # Assert - should not raise exception
        log_file = tmp_path / "logs" / f"webhooks_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
        assert log_file.exists()

    def test_different_dates_create_different_files(self, mock_config: Mock, tmp_path: Path) -> None:
        """Test that logs for different dates go to different files."""
        # Arrange
        writer = StructuredLogWriter(config=mock_config)
        date1 = datetime(2026, 1, 5, tzinfo=UTC)
        date2 = datetime(2026, 1, 6, tzinfo=UTC)

        # Act
        path1 = writer._get_log_file_path(date=date1)
        path2 = writer._get_log_file_path(date=date2)

        # Assert
        assert path1 != path2
        assert path1.name == "webhooks_2026-01-05.json"
        assert path2.name == "webhooks_2026-01-06.json"

    def test_write_log_handles_temp_file_deletion_error(self, mock_config: Mock, tmp_path: Path) -> None:
        """Test that write_log handles temp file deletion errors gracefully."""
        # Arrange
        writer = StructuredLogWriter(config=mock_config)
        context = WebhookContext(
            hook_id="test-cleanup-error",
            event_type="pull_request",
            repository="org/repo",
            repository_full_name="org/repo",
        )

        # Mock os.unlink to raise OSError
        with patch("os.unlink", side_effect=OSError("Permission denied")):
            # Act - should not raise exception
            writer.write_log(context)

        # Assert - log file still created successfully
        log_file = tmp_path / "logs" / f"webhooks_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
        assert log_file.exists()

        with open(log_file) as f:
            log_entry = json.loads(f.read().strip())

        assert log_entry["hook_id"] == "test-cleanup-error"
