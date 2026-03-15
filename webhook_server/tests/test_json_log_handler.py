"""Tests for webhook_server/utils/json_log_handler.py.

Tests JsonLogHandler which writes log records as JSONL to date-based webhook log files,
enriched with WebhookContext data when available.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from webhook_server.utils.context import clear_context, create_context
from webhook_server.utils.json_log_handler import JsonLogHandler


@pytest.fixture(autouse=True)
def cleanup_context() -> None:
    """Clean up context after each test."""
    yield  # type: ignore[misc]
    clear_context()


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    """Return a temporary log directory path."""
    return tmp_path / "logs"


@pytest.fixture
def handler(log_dir: Path) -> JsonLogHandler:
    """Create a JsonLogHandler pointing at a temporary directory."""
    return JsonLogHandler(log_dir=str(log_dir))


@pytest.fixture
def logger_with_handler(handler: JsonLogHandler) -> logging.Logger:
    """Create a stdlib logger wired to the JsonLogHandler."""
    logger = logging.getLogger("test_json_log_handler")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.addHandler(handler)
    return logger


def _read_log_lines(log_dir: Path) -> list[dict]:
    """Read all JSONL lines from the current-date log file in *log_dir*."""
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    log_file = log_dir / f"webhooks_{date_str}.json"
    assert log_file.exists(), f"Expected log file {log_file} to exist"
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


class TestJsonLogHandlerInit:
    """Tests for handler initialisation."""

    def test_creates_log_dir_if_missing(self, tmp_path: Path) -> None:
        """Handler __init__ creates the log directory when it does not exist."""
        new_dir = tmp_path / "nonexistent" / "logs"
        assert not new_dir.exists()
        JsonLogHandler(log_dir=str(new_dir))
        assert new_dir.is_dir()

    def test_existing_log_dir_is_fine(self, tmp_path: Path) -> None:
        """Handler __init__ succeeds when the log directory already exists."""
        existing = tmp_path / "logs"
        existing.mkdir()
        handler = JsonLogHandler(log_dir=str(existing))
        assert handler.log_dir == existing


class TestEmitBasic:
    """Tests for basic emit behaviour and entry format."""

    def test_emit_writes_valid_jsonl(self, logger_with_handler: logging.Logger, log_dir: Path) -> None:
        """A single emit produces a valid JSONL line in the correct file."""
        logger_with_handler.info("hello world")
        entries = _read_log_lines(log_dir)
        assert len(entries) == 1

    def test_entry_format_fields(self, logger_with_handler: logging.Logger, log_dir: Path) -> None:
        """Each entry contains the mandatory fields."""
        logger_with_handler.warning("something happened")
        entry = _read_log_lines(log_dir)[0]

        assert entry["type"] == "log_entry"
        assert "timestamp" in entry
        # Verify timestamp is valid ISO format
        datetime.fromisoformat(entry["timestamp"])
        assert entry["level"] == "WARNING"
        assert entry["logger_name"] == "test_json_log_handler"
        assert entry["message"] == "something happened"

    def test_date_based_file_naming(self, logger_with_handler: logging.Logger, log_dir: Path) -> None:
        """Log file follows webhooks_YYYY-MM-DD.json naming convention."""
        logger_with_handler.info("test")
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        expected = log_dir / f"webhooks_{date_str}.json"
        assert expected.exists()

    def test_append_behaviour(self, logger_with_handler: logging.Logger, log_dir: Path) -> None:
        """Multiple emits append to the same file."""
        logger_with_handler.info("first")
        logger_with_handler.info("second")
        logger_with_handler.info("third")

        entries = _read_log_lines(log_dir)
        assert len(entries) == 3
        assert entries[0]["message"] == "first"
        assert entries[1]["message"] == "second"
        assert entries[2]["message"] == "third"


class TestLogLevels:
    """Tests for different log levels."""

    @pytest.mark.parametrize(
        ("method", "expected_level"),
        [
            ("debug", "DEBUG"),
            ("info", "INFO"),
            ("warning", "WARNING"),
            ("error", "ERROR"),
        ],
    )
    def test_level_is_recorded(
        self,
        logger_with_handler: logging.Logger,
        log_dir: Path,
        method: str,
        expected_level: str,
    ) -> None:
        """Log level is correctly captured in the entry."""
        getattr(logger_with_handler, method)("test message")
        entry = _read_log_lines(log_dir)[0]
        assert entry["level"] == expected_level


class TestAnsiStripping:
    """Tests for ANSI escape code removal."""

    def test_ansi_codes_stripped_from_message(self, logger_with_handler: logging.Logger, log_dir: Path) -> None:
        """ANSI colour codes are removed from the message before writing."""
        logger_with_handler.info("\x1b[31mRed text\x1b[0m and \x1b[1;32mbold green\x1b[0m")
        entry = _read_log_lines(log_dir)[0]
        assert "\x1b" not in entry["message"]
        assert "Red text" in entry["message"]
        assert "bold green" in entry["message"]

    def test_message_without_ansi_unchanged(self, logger_with_handler: logging.Logger, log_dir: Path) -> None:
        """Plain messages pass through without modification."""
        logger_with_handler.info("plain message")
        entry = _read_log_lines(log_dir)[0]
        assert entry["message"] == "plain message"


class TestContextEnrichment:
    """Tests for WebhookContext enrichment."""

    def test_context_fields_added_when_context_set(self, logger_with_handler: logging.Logger, log_dir: Path) -> None:
        """When WebhookContext is active, its fields appear in the entry."""
        ctx = create_context(
            hook_id="delivery-abc",
            event_type="pull_request",
            repository="org/repo",
            repository_full_name="org/repo",
            action="opened",
            sender="user1",
            api_user="bot-user",
        )
        ctx.pr_number = 42

        logger_with_handler.info("processing webhook")
        entry = _read_log_lines(log_dir)[0]

        assert entry["hook_id"] == "delivery-abc"
        assert entry["event_type"] == "pull_request"
        assert entry["repository"] == "org/repo"
        assert entry["pr_number"] == 42
        assert entry["api_user"] == "bot-user"

    def test_no_context_fields_when_context_absent(self, logger_with_handler: logging.Logger, log_dir: Path) -> None:
        """Without WebhookContext, context-specific fields are absent."""
        logger_with_handler.info("no context here")
        entry = _read_log_lines(log_dir)[0]

        assert "hook_id" not in entry
        assert "event_type" not in entry
        assert "repository" not in entry
        assert "pr_number" not in entry
        assert "api_user" not in entry

    def test_basic_fields_present_without_context(self, logger_with_handler: logging.Logger, log_dir: Path) -> None:
        """Basic fields (type, timestamp, level, logger_name, message) always present."""
        logger_with_handler.info("basic info")
        entry = _read_log_lines(log_dir)[0]

        assert entry["type"] == "log_entry"
        assert "timestamp" in entry
        assert entry["level"] == "INFO"
        assert entry["logger_name"] == "test_json_log_handler"
        assert entry["message"] == "basic info"


class TestExceptionTraceback:
    """Tests for exception traceback capture."""

    def test_emit_with_exception_includes_traceback(self, logger_with_handler: logging.Logger, log_dir: Path) -> None:
        """Test that exception info is included in JSON entry."""
        try:
            raise ValueError("test error")
        except ValueError:
            logger_with_handler.exception("Something failed")

        entries = _read_log_lines(log_dir)
        assert len(entries) == 1
        entry = entries[0]

        assert entry["level"] == "ERROR"
        assert "Something failed" in entry["message"]
        assert "exc_info" in entry
        assert "ValueError" in entry["exc_info"]
        assert "test error" in entry["exc_info"]
        assert "Traceback" in entry["exc_info"]

    def test_emit_without_exception_has_no_exc_info(self, logger_with_handler: logging.Logger, log_dir: Path) -> None:
        """Normal log entries do not contain exc_info field."""
        logger_with_handler.info("no error here")
        entry = _read_log_lines(log_dir)[0]
        assert "exc_info" not in entry


class TestErrorHandling:
    """Tests for error resilience."""

    def test_emit_does_not_crash_on_write_failure(self, handler: JsonLogHandler) -> None:
        """Handler silently handles write failures via handleError."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test message",
            args=(),
            exc_info=None,
        )

        with patch.object(handler, "_append_to_file", side_effect=OSError("Disk full")):
            # Must not raise
            handler.emit(record)

    def test_emit_does_not_crash_on_open_failure(
        self,
        handler: JsonLogHandler,
    ) -> None:
        """Handler silently handles open() failures."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test message",
            args=(),
            exc_info=None,
        )

        with patch("builtins.open", side_effect=OSError("Permission denied")):
            handler.emit(record)


class TestFileLocking:
    """Tests for fcntl file locking behaviour."""

    @patch("webhook_server.utils.json_log_handler.HAS_FCNTL", new=True)
    @patch("fcntl.flock")
    def test_flock_called_when_available(
        self, mock_flock: Mock, logger_with_handler: logging.Logger, log_dir: Path
    ) -> None:
        """When HAS_FCNTL is True, fcntl.flock is called for lock/unlock."""
        logger_with_handler.info("locked write")
        assert mock_flock.call_count >= 2  # At least LOCK_EX + LOCK_UN

    @patch("webhook_server.utils.json_log_handler.HAS_FCNTL", new=False)
    def test_works_without_fcntl(self, logger_with_handler: logging.Logger, log_dir: Path) -> None:
        """When HAS_FCNTL is False, writing still works without locking."""
        logger_with_handler.info("no lock write")
        entries = _read_log_lines(log_dir)
        assert len(entries) == 1
        assert entries[0]["message"] == "no lock write"


class TestUnicodeContent:
    """Tests for Unicode content handling."""

    def test_unicode_message_written_correctly(self, logger_with_handler: logging.Logger, log_dir: Path) -> None:
        """Messages with Unicode characters are preserved."""
        logger_with_handler.info("测试 emoji 🚀 and accents: éàü")
        entry = _read_log_lines(log_dir)[0]
        assert "测试" in entry["message"]
        assert "🚀" in entry["message"]
        assert "éàü" in entry["message"]

    def test_unicode_in_context_fields(self, logger_with_handler: logging.Logger, log_dir: Path) -> None:
        """Unicode in WebhookContext fields is preserved."""
        create_context(
            hook_id="hook-unicode",
            event_type="pull_request",
            repository="org/日本語リポ",
            repository_full_name="org/日本語リポ",
            api_user="用户",
        )
        logger_with_handler.info("ctx test")
        entry = _read_log_lines(log_dir)[0]
        assert entry["repository"] == "org/日本語リポ"
        assert entry["api_user"] == "用户"
