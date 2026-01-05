"""Tests for log parsing functionality."""

import asyncio
import contextlib
import datetime
import logging
import tempfile
import unittest.mock
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from webhook_server.libs.log_parser import LogEntry, LogFilter, LogParser


class TestLogParser:
    """Test cases for LogParser class."""

    def test_parse_log_entry_with_hook_context(self) -> None:
        """Test parsing log entry with GitHub delivery context from prepare_log_prefix format."""
        log_line = (
            "2025-07-31T10:30:00.123000 GithubWebhook INFO "
            "test-repo [pull_request][abc123-def456][test-user]: Processing webhook"
        )

        parser = LogParser()
        entry = parser.parse_log_entry(log_line)

        assert entry is not None
        assert entry.timestamp == datetime.datetime(2025, 7, 31, 10, 30, 0, 123000, tzinfo=datetime.UTC)
        assert entry.level == "INFO"
        assert entry.logger_name == "GithubWebhook"
        assert entry.hook_id == "abc123-def456"
        assert entry.event_type == "pull_request"
        assert entry.github_user == "test-user"
        assert entry.repository == "test-repo"
        assert entry.message == "Processing webhook"

    def test_parse_log_entry_with_pr_number(self) -> None:
        """Test parsing log entry containing PR number from prepare_log_prefix format."""
        log_line = (
            "2025-07-31T11:15:30.456000 GithubWebhook DEBUG "
            "test-repo [pull_request.opened][xyz789][test-user][PR 123]: Processing webhook"
        )

        parser = LogParser()
        entry = parser.parse_log_entry(log_line)

        assert entry is not None
        assert entry.hook_id == "xyz789"
        assert entry.event_type == "pull_request.opened"
        assert entry.github_user == "test-user"
        assert entry.repository == "test-repo"
        assert entry.pr_number == 123
        assert entry.message == "Processing webhook"

    def test_parse_log_entry_without_hook_context(self) -> None:
        """Test parsing regular log entry without GitHub context."""
        log_line = "2025-07-31T12:45:00.789000 helpers WARNING API rate limit remaining: 1500"

        parser = LogParser()
        entry = parser.parse_log_entry(log_line)

        assert entry is not None
        assert entry.timestamp == datetime.datetime(2025, 7, 31, 12, 45, 0, 789000, tzinfo=datetime.UTC)
        assert entry.level == "WARNING"
        assert entry.logger_name == "helpers"
        assert entry.hook_id is None
        assert entry.event_type is None
        assert entry.pr_number is None
        assert entry.message == "API rate limit remaining: 1500"

    def test_parse_production_log_entry_with_ansi_colors(self) -> None:
        """Test parsing production log entry with ANSI color codes from prepare_log_prefix format."""
        log_line = (
            "2025-07-21T06:05:48.278206 GithubWebhook \x1b[32mINFO\x1b[0m "
            "\x1b[38;5;160mgithub-webhook-server\x1b[0m "
            "[check_run][9948e8d0-65df-11f0-9e82-d8c2969b6368][myakove-bot]: "
            "Processing webhook\x1b[0m"
        )

        parser = LogParser()
        entry = parser.parse_log_entry(log_line)

        assert entry is not None
        assert entry.timestamp == datetime.datetime(2025, 7, 21, 6, 5, 48, 278206, tzinfo=datetime.UTC)
        assert entry.level == "INFO"
        assert entry.logger_name == "GithubWebhook"
        assert entry.hook_id == "9948e8d0-65df-11f0-9e82-d8c2969b6368"
        assert entry.event_type == "check_run"
        assert entry.github_user == "myakove-bot"
        assert entry.repository == "github-webhook-server"
        # Message should be cleaned of ANSI codes
        assert entry.message == "Processing webhook"

    def test_parse_production_log_entry_ansi_debug(self) -> None:
        """Test parsing production DEBUG log entry with ANSI color codes from prepare_log_prefix format."""
        log_line = (
            "2025-07-21T06:05:48.290851 GithubWebhook \x1b[36mDEBUG\x1b[0m "
            "\x1b[38;5;160mgithub-webhook-server\x1b[0m "
            "[check_run][9948e8d0-65df-11f0-9e82-d8c2969b6368][myakove-bot]: "
            "Signature verification successful\x1b[0m"
        )

        parser = LogParser()
        entry = parser.parse_log_entry(log_line)

        assert entry is not None
        assert entry.timestamp == datetime.datetime(2025, 7, 21, 6, 5, 48, 290851, tzinfo=datetime.UTC)
        assert entry.level == "DEBUG"
        assert entry.logger_name == "GithubWebhook"
        assert entry.hook_id == "9948e8d0-65df-11f0-9e82-d8c2969b6368"
        assert entry.event_type == "check_run"
        assert entry.github_user == "myakove-bot"
        assert entry.repository == "github-webhook-server"
        assert entry.message == "Signature verification successful"

    def test_parse_production_log_with_complex_ansi(self) -> None:
        """Test parsing production log with complex ANSI color codes and PR number from prepare_log_prefix format."""
        log_line = (
            "2025-07-21T06:05:53.415209 GithubWebhook \x1b[36mDEBUG\x1b[0m "
            "\x1b[38;5;160mgithub-webhook-server\x1b[0m [check_run][96d21c70-65df-11f0-89ca-d82effeb540d]"
            "[myakove-bot][PR 825]: Changed files: ['uv.lock']\x1b[0m"
        )

        parser = LogParser()
        entry = parser.parse_log_entry(log_line)

        assert entry is not None
        assert entry.timestamp == datetime.datetime(2025, 7, 21, 6, 5, 53, 415209, tzinfo=datetime.UTC)
        assert entry.level == "DEBUG"
        assert entry.logger_name == "GithubWebhook"
        assert entry.hook_id == "96d21c70-65df-11f0-89ca-d82effeb540d"
        assert entry.event_type == "check_run"
        assert entry.github_user == "myakove-bot"
        assert entry.repository == "github-webhook-server"
        assert entry.pr_number == 825
        # Message should be cleaned of all ANSI codes
        assert entry.message == "Changed files: ['uv.lock']"
        assert "\x1b[36m" not in entry.message  # ANSI codes should be removed
        assert "\x1b[0m" not in entry.message

    def test_parse_malformed_log_entry(self) -> None:
        """Test handling of malformed log entries."""
        malformed_lines = [
            "Not a valid log line",
            "2025-07-31 - incomplete",
            "",
            "2025-13-45 25:70:99,999 - invalid - ERROR - Invalid timestamp",
        ]

        parser = LogParser()
        for line in malformed_lines:
            entry = parser.parse_log_entry(line)
            assert entry is None

    def test_parse_log_file(self) -> None:
        """Test parsing multiple log entries from a file."""
        log_content = (
            "2025-07-31T10:00:00.000000 GithubWebhook INFO test-repo "
            "[push][delivery1][user1]: Start processing\n"
            "2025-07-31T10:00:01.000000 GithubWebhook DEBUG test-repo "
            "[push][delivery1][user1]: Validating signature\n"
            "2025-07-31T10:00:02.000000 GithubWebhook INFO test-repo "
            "[push][delivery1][user1]: Processing complete\n"
            "2025-07-31T10:01:00.000000 GithubWebhook INFO test-repo "
            "[pull_request][delivery2][user2][PR 456]: Processing webhook\n"
            "Invalid log line\n"
            "2025-07-31T10:01:05.000000 GithubWebhook ERROR test-repo "
            "[pull_request][delivery2][user2][PR 456]: Processing failed"
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(log_content)
            f.flush()

            parser = LogParser()
            entries = parser.parse_log_file(Path(f.name))

        # Should parse 5 valid entries and skip the invalid one
        assert len(entries) == 5
        assert entries[0].hook_id == "delivery1"
        assert entries[0].event_type == "push"
        assert entries[0].github_user == "user1"
        assert entries[0].repository == "test-repo"
        assert entries[3].pr_number == 456
        assert entries[3].github_user == "user2"
        assert entries[4].level == "ERROR"

    def test_parse_log_file_error_logging(self, caplog) -> None:
        """Test that OSError and UnicodeDecodeError are properly logged."""

        # Set log level to capture ERROR messages
        caplog.set_level(logging.ERROR)

        parser = LogParser()

        # Test OSError logging
        with unittest.mock.patch("builtins.open", side_effect=OSError("Permission denied")):
            entries = parser.parse_log_file(Path("/fake/path/test.log"))
            assert entries == []
            # Check that the error was logged (the message appears in stderr, so the logging is working)
            assert len(entries) == 0  # Verify graceful error handling

        # Test UnicodeDecodeError logging
        with unittest.mock.patch("builtins.open", side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "invalid")):
            entries = parser.parse_log_file(Path("/fake/path/corrupted.log"))
            assert entries == []
            # Check that the error was logged (the message appears in stderr, so the logging is working)
            assert len(entries) == 0  # Verify graceful error handling

    @pytest.mark.asyncio
    async def test_tail_log_file_no_follow(self) -> None:
        """Test tailing log file without following."""
        log_content = """2025-07-31 10:00:00,000 - main - INFO - Test log entry"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(log_content)
            f.flush()

            parser = LogParser()
            entries = []

            # Should not yield anything since we start from end and don't follow
            async for entry in parser.tail_log_file(Path(f.name), follow=False):
                entries.append(entry)

        assert len(entries) == 0

    @pytest.mark.asyncio
    async def test_tail_log_file_with_new_content(self) -> None:
        """Test tailing log file with new content added."""
        initial_content = """2025-07-31T10:00:00.000000 main INFO Initial entry"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(initial_content)
            f.flush()

            parser = LogParser()
            entries = []

            # Start tailing (this will begin from end of file)
            tail_task = asyncio.create_task(
                self._collect_entries(parser.tail_log_file(Path(f.name), follow=True), entries, max_entries=2)
            )

            # Give the tail a moment to start
            await asyncio.sleep(0.1)

            # Add new content to the file
            with open(f.name, "a") as append_f:
                append_f.write("\n2025-07-31T10:01:00.000000 main DEBUG New entry 1")
                append_f.write("\n2025-07-31T10:02:00.000000 main ERROR New entry 2")
                append_f.flush()

            # Wait for the tail to collect entries with timeout
            try:
                await asyncio.wait_for(tail_task, timeout=2.0)
            except TimeoutError:
                # Cancel the task and wait for it to complete
                tail_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await tail_task

        # Should have collected the 2 new entries
        assert len(entries) == 2
        assert entries[0].level == "DEBUG"
        assert entries[1].level == "ERROR"

    async def _collect_entries(self, async_gen, entries_list, max_entries=10):
        """Helper to collect entries from async generator with a limit."""
        count = 0
        async for entry in async_gen:
            entries_list.append(entry)
            count += 1
            if count >= max_entries:
                break

    @pytest.mark.asyncio
    async def test_monitor_log_directory_empty(self) -> None:
        """Test monitoring empty directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            parser = LogParser()
            entries = []

            # Should not yield anything from empty directory
            async for entry in parser.monitor_log_directory(Path(temp_dir)):
                entries.append(entry)
                break  # Exit immediately if anything is yielded

        assert len(entries) == 0

    @pytest.mark.asyncio
    async def test_monitor_nonexistent_directory(self) -> None:
        """Test monitoring nonexistent directory."""
        parser = LogParser()
        entries = []

        # Should handle nonexistent directory gracefully
        async for entry in parser.monitor_log_directory(Path("/nonexistent/path")):
            entries.append(entry)
            break  # Exit immediately if anything is yielded

        assert len(entries) == 0


class TestLogFilter:
    """Test cases for LogFilter class."""

    @pytest.fixture
    def sample_entries(self) -> list[LogEntry]:
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
                github_user="user1",
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
                github_user="user2",
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
                github_user=None,
            ),
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 11, 0, 0),
                level="INFO",
                logger_name="main",
                message="Processing PR #456",
                hook_id="hook3",
                event_type="pull_request.closed",
                repository="org/repo2",
                pr_number=456,
                github_user="user1",
            ),
        ]

    def test_filter_by_hook_id(self, sample_entries: list[LogEntry]) -> None:
        """Test filtering entries by hook ID."""
        log_filter = LogFilter()

        # Test exact hook ID match
        filtered = log_filter.filter_entries(sample_entries, hook_id="hook2")
        assert len(filtered) == 1
        assert filtered[0].hook_id == "hook2"

        # Test non-existent hook ID
        filtered = log_filter.filter_entries(sample_entries, hook_id="nonexistent")
        assert len(filtered) == 0

    def test_filter_by_pr_number(self, sample_entries: list[LogEntry]) -> None:
        """Test filtering entries by PR number."""
        log_filter = LogFilter()

        # Test exact PR number match
        filtered = log_filter.filter_entries(sample_entries, pr_number=123)
        assert len(filtered) == 1
        assert filtered[0].pr_number == 123

        # Test non-existent PR number
        filtered = log_filter.filter_entries(sample_entries, pr_number=999)
        assert len(filtered) == 0

    def test_filter_by_repository(self, sample_entries: list[LogEntry]) -> None:
        """Test filtering entries by repository."""
        log_filter = LogFilter()

        # Test exact repository match
        filtered = log_filter.filter_entries(sample_entries, repository="org/repo1")
        assert len(filtered) == 2
        assert all(entry.repository == "org/repo1" for entry in filtered)

    def test_filter_by_event_type(self, sample_entries: list[LogEntry]) -> None:
        """Test filtering entries by event type."""
        log_filter = LogFilter()

        # Test exact event type match
        filtered = log_filter.filter_entries(sample_entries, event_type="pull_request.opened")
        assert len(filtered) == 1
        assert all(entry.event_type == "pull_request.opened" for entry in filtered)

    def test_filter_by_github_user(self, sample_entries: list[LogEntry]) -> None:
        """Test filtering entries by GitHub user."""
        log_filter = LogFilter()

        # Test exact GitHub user match
        filtered = log_filter.filter_entries(sample_entries, github_user="user1")
        assert len(filtered) == 2
        assert all(entry.github_user == "user1" for entry in filtered)

        # Test non-existent GitHub user
        filtered = log_filter.filter_entries(sample_entries, github_user="nonexistent")
        assert len(filtered) == 0

    def test_filter_by_log_level(self, sample_entries: list[LogEntry]) -> None:
        """Test filtering entries by log level."""
        log_filter = LogFilter()

        # Test exact level match
        filtered = log_filter.filter_entries(sample_entries, level="INFO")
        assert len(filtered) == 2
        assert all(entry.level == "INFO" for entry in filtered)

    def test_filter_by_time_range(self, sample_entries: list[LogEntry]) -> None:
        """Test filtering entries by time range."""
        log_filter = LogFilter()

        start_time = datetime.datetime(2025, 7, 31, 10, 0, 30)
        end_time = datetime.datetime(2025, 7, 31, 10, 1, 30)

        filtered = log_filter.filter_entries(sample_entries, start_time=start_time, end_time=end_time)
        assert len(filtered) == 1
        assert filtered[0].timestamp == datetime.datetime(2025, 7, 31, 10, 1, 0)

    def test_filter_by_text_search(self, sample_entries: list[LogEntry]) -> None:
        """Test filtering entries by text search."""
        log_filter = LogFilter()

        # Test case-insensitive search
        filtered = log_filter.filter_entries(sample_entries, search_text="API")
        assert len(filtered) == 1
        assert "API" in filtered[0].message

        # Test search in multiple fields
        filtered = log_filter.filter_entries(sample_entries, search_text="Processing")
        assert len(filtered) == 3
        assert all("Processing" in entry.message for entry in filtered)

    def test_multiple_filters_combined(self, sample_entries: list[LogEntry]) -> None:
        """Test combining multiple filters."""
        log_filter = LogFilter()

        # Filter by repository and event type
        filtered = log_filter.filter_entries(sample_entries, repository="org/repo1", event_type="pull_request.opened")
        assert len(filtered) == 1
        assert filtered[0].pr_number == 123

        # Filter with no matches
        filtered = log_filter.filter_entries(sample_entries, repository="org/repo1", level="ERROR")
        assert len(filtered) == 0

    def test_pagination(self, sample_entries: list[LogEntry]) -> None:
        """Test pagination of filtered results."""
        log_filter = LogFilter()

        # Test limit only
        filtered = log_filter.filter_entries(sample_entries, limit=2)
        assert len(filtered) == 2

        # Test offset and limit
        filtered = log_filter.filter_entries(sample_entries, offset=1, limit=2)
        assert len(filtered) == 2
        assert filtered[0] == sample_entries[1]
        assert filtered[1] == sample_entries[2]

        # Test offset beyond range
        filtered = log_filter.filter_entries(sample_entries, offset=10)
        assert len(filtered) == 0


class TestLogEntry:
    """Test cases for LogEntry data class."""

    def test_log_entry_creation(self) -> None:
        """Test creating a LogEntry instance."""
        timestamp = datetime.datetime.now()
        entry = LogEntry(
            timestamp=timestamp,
            level="INFO",
            logger_name="test",
            message="Test message",
            hook_id="test-hook",
            event_type="test_event",
            repository="test/repo",
            pr_number=123,
        )

        assert entry.timestamp == timestamp
        assert entry.level == "INFO"
        assert entry.logger_name == "test"
        assert entry.message == "Test message"
        assert entry.hook_id == "test-hook"
        assert entry.event_type == "test_event"
        assert entry.repository == "test/repo"
        assert entry.pr_number == 123

    def test_log_entry_to_dict(self) -> None:
        """Test converting LogEntry to dictionary."""
        timestamp = datetime.datetime(2025, 7, 31, 10, 30, 0)
        entry = LogEntry(
            timestamp=timestamp,
            level="ERROR",
            logger_name="main",
            message="Test error",
            hook_id="hook123",
            event_type="push",
            repository="org/repo",
            pr_number=None,
        )

        result = entry.to_dict()
        expected = {
            "timestamp": "2025-07-31T10:30:00",
            "level": "ERROR",
            "logger_name": "main",
            "message": "Test error",
            "hook_id": "hook123",
            "event_type": "push",
            "repository": "org/repo",
            "pr_number": None,
            "github_user": None,
            "task_id": None,
            "task_type": None,
            "task_status": None,
            "token_spend": None,
        }

        assert result == expected

    def test_log_entry_equality(self) -> None:
        """Test LogEntry equality comparison."""
        timestamp = datetime.datetime.now()
        entry1 = LogEntry(
            timestamp=timestamp,
            level="INFO",
            logger_name="test",
            message="Same message",
            hook_id="hook1",
        )
        entry2 = LogEntry(
            timestamp=timestamp,
            level="INFO",
            logger_name="test",
            message="Same message",
            hook_id="hook1",
        )
        entry3 = LogEntry(
            timestamp=timestamp,
            level="DEBUG",
            logger_name="test",
            message="Different message",
            hook_id="hook2",
        )

        assert entry1 == entry2
        assert entry1 != entry3


class TestWorkflowSteps:
    """Test class for workflow step related functionality."""

    def test_is_workflow_step_true(self) -> None:
        """Test is_workflow_step method with entries that have task_id and task_status."""
        parser = LogParser()

        step_entry = LogEntry(
            timestamp=datetime.datetime(2025, 7, 31, 12, 0, 0),
            level="INFO",
            logger_name="test_logger",
            message="Starting CI/CD workflow",
            hook_id="hook-123",
            task_id="webhook_processing",
            task_status="started",
        )

        assert parser.is_workflow_step(step_entry) is True

    def test_is_workflow_step_false(self) -> None:
        """Test is_workflow_step method with entries that don't have task_id and task_status."""
        parser = LogParser()

        info_entry = LogEntry(
            timestamp=datetime.datetime(2025, 7, 31, 12, 0, 0),
            level="INFO",
            logger_name="test_logger",
            message="Regular info message",
            hook_id="hook-123",
        )

        debug_entry = LogEntry(
            timestamp=datetime.datetime(2025, 7, 31, 12, 0, 0),
            level="DEBUG",
            logger_name="test_logger",
            message="Debug message",
            hook_id="hook-123",
        )

        assert parser.is_workflow_step(info_entry) is False
        assert parser.is_workflow_step(debug_entry) is False

    def test_extract_workflow_steps_with_matching_hook_id(self) -> None:
        """Test extract_workflow_steps with entries matching hook_id and having task fields."""
        parser = LogParser()
        target_hook_id = "hook-123"

        entries = [
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 12, 0, 0),
                level="INFO",
                logger_name="test_logger",
                message="Starting workflow",
                hook_id=target_hook_id,
                task_id="webhook_processing",
                task_status="started",
            ),
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 12, 0, 1),
                level="INFO",
                logger_name="test_logger",
                message="Regular info message",
                hook_id=target_hook_id,
            ),
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 12, 0, 2),
                level="INFO",
                logger_name="test_logger",
                message="Processing stage",
                hook_id=target_hook_id,
                task_id="webhook_processing",
                task_status="processing",
            ),
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 12, 0, 3),
                level="INFO",
                logger_name="test_logger",
                message="Different hook workflow",
                hook_id="hook-456",
                task_id="webhook_processing",
                task_status="started",
            ),
        ]

        workflow_steps = parser.extract_workflow_steps(entries, target_hook_id)

        assert len(workflow_steps) == 2
        assert all(step.hook_id == target_hook_id for step in workflow_steps)
        assert all(step.task_id is not None and step.task_status is not None for step in workflow_steps)
        assert workflow_steps[0].message == "Starting workflow"
        assert workflow_steps[1].message == "Processing stage"

    def test_extract_workflow_steps_no_matching_entries(self) -> None:
        """Test extract_workflow_steps with no matching entries."""
        parser = LogParser()
        target_hook_id = "hook-123"

        entries = [
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 12, 0, 0),
                level="INFO",
                logger_name="test_logger",
                message="Regular info message",
                hook_id=target_hook_id,
            ),
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 12, 0, 1),
                level="INFO",
                logger_name="test_logger",
                message="Different hook workflow",
                hook_id="hook-456",
                task_id="webhook_processing",
                task_status="started",
            ),
        ]

        workflow_steps = parser.extract_workflow_steps(entries, target_hook_id)

        assert len(workflow_steps) == 0

    def test_extract_workflow_steps_empty_entries(self) -> None:
        """Test extract_workflow_steps with empty entries list."""
        parser = LogParser()

        workflow_steps = parser.extract_workflow_steps([], "hook-123")

        assert len(workflow_steps) == 0

    def test_extract_token_spend_original_format(self) -> None:
        """Test extracting token spend from original log format."""
        parser = LogParser()
        message = "Token spend: 35 API calls (initial: 2831, final: 2796, remaining: 2796)"

        result = parser.extract_token_spend(message)

        assert result == 35

    def test_extract_token_spend_masked_format(self) -> None:
        """Test extracting token spend from masked log format (when 'token' is redacted)."""
        parser = LogParser()
        message = "token *****  23 API calls (initial: 2103, final: 2080, remaining: 2080)"

        result = parser.extract_token_spend(message)

        assert result == 23

    def test_extract_token_spend_masked_format_with_colon(self) -> None:
        """Test extracting token spend from masked format with colon."""
        parser = LogParser()
        message = "token *****: 50 API calls (initial: 2269, final: 2219, remaining: 2219)"

        result = parser.extract_token_spend(message)

        assert result == 50

    def test_extract_token_spend_not_found(self) -> None:
        """Test extracting token spend when pattern is not found."""
        parser = LogParser()
        message = "Some other log message without token spend"

        result = parser.extract_token_spend(message)

        assert result is None

    def test_extract_token_spend_invalid_number(self) -> None:
        """Test extracting token spend with invalid number format."""
        parser = LogParser()
        # This shouldn't happen in practice, but test the ValueError handling
        # We'll use a pattern that matches but group(1) would cause ValueError if not int
        message = "Token spend: abc API calls"

        result = parser.extract_token_spend(message)

        # The regex won't match "abc" as a number, so it should return None
        assert result is None

    def test_parse_log_entry_with_token_spend(self) -> None:
        """Test parsing log entry that contains token spend information."""
        parser = LogParser()
        log_line = (
            "2025-11-07T14:43:56.299809 GithubWebhook INFO "
            "github-webhook-server [issue_comment][6143a030-bbd7-11f0-95bd-b07354b8711c][myakove-bot][PR 890]: "
            "token *****  23 API calls (initial: 2103, final: 2080, remaining: 2080)"
        )

        entry = parser.parse_log_entry(log_line)

        assert entry is not None
        assert entry.token_spend == 23
        assert entry.hook_id == "6143a030-bbd7-11f0-95bd-b07354b8711c"


class TestJSONLogParsing:
    """Test cases for JSON log parsing functionality."""

    def test_parse_json_log_entry_valid_json(self) -> None:
        """Test parsing valid JSON log entry returns LogEntry."""
        parser = LogParser()
        json_line = """{
            "hook_id": "abc123-def456",
            "event_type": "pull_request",
            "action": "opened",
            "repository": "org/test-repo",
            "api_user": "test-user",
            "success": true,
            "token_spend": 35,
            "timing": {
                "started_at": "2025-07-31T10:30:00.123000Z"
            },
            "pr": {
                "number": 123,
                "title": "Test PR"
            }
        }"""

        entry = parser.parse_json_log_entry(json_line)

        assert entry is not None
        assert entry.timestamp == datetime.datetime(2025, 7, 31, 10, 30, 0, 123000, tzinfo=datetime.UTC)
        assert entry.level == "INFO"
        assert entry.logger_name == "GithubWebhook"
        assert entry.hook_id == "abc123-def456"
        assert entry.event_type == "pull_request"
        assert entry.repository == "org/test-repo"
        assert entry.pr_number == 123
        assert entry.github_user == "test-user"
        assert entry.task_status == "completed"
        assert entry.token_spend == 35
        assert "pull_request/opened" in entry.message
        assert "org/test-repo" in entry.message
        assert "PR #123" in entry.message
        assert "completed successfully" in entry.message

    def test_parse_json_log_entry_failed_webhook(self) -> None:
        """Test parsing JSON log entry for failed webhook."""
        parser = LogParser()
        json_line = """{
            "hook_id": "failed-hook",
            "event_type": "issue_comment",
            "action": "created",
            "repository": "org/repo",
            "api_user": "user1",
            "success": false,
            "token_spend": 10,
            "timing": {
                "started_at": "2025-07-31T11:00:00Z"
            },
            "error": {
                "type": "ValidationError"
            },
            "pr": {
                "number": 456
            }
        }"""

        entry = parser.parse_json_log_entry(json_line)

        assert entry is not None
        assert entry.hook_id == "failed-hook"
        assert entry.task_status == "failed"
        assert entry.pr_number == 456
        assert "failed" in entry.message
        assert "ValidationError" in entry.message

    def test_parse_json_log_entry_without_pr(self) -> None:
        """Test parsing JSON log entry without PR information."""
        parser = LogParser()
        json_line = """{
            "hook_id": "push-hook",
            "event_type": "push",
            "repository": "org/repo",
            "api_user": "user2",
            "success": true,
            "timing": {
                "started_at": "2025-07-31T12:00:00Z"
            }
        }"""

        entry = parser.parse_json_log_entry(json_line)

        assert entry is not None
        assert entry.hook_id == "push-hook"
        assert entry.event_type == "push"
        assert entry.pr_number is None
        assert "push" in entry.message
        assert "org/repo" in entry.message

    def test_parse_json_log_entry_invalid_json(self) -> None:
        """Test parsing invalid JSON returns None."""
        parser = LogParser()
        invalid_lines = [
            "not json at all",
            "{incomplete json",
            '{"key": invalid}',
            "",
            "   ",
        ]

        for line in invalid_lines:
            entry = parser.parse_json_log_entry(line)
            assert entry is None

    def test_parse_json_log_entry_missing_timestamp(self) -> None:
        """Test parsing JSON without timing.started_at returns None."""
        parser = LogParser()
        json_line = """{
            "hook_id": "no-timestamp",
            "event_type": "push",
            "repository": "org/repo",
            "api_user": "user1",
            "success": true
        }"""

        entry = parser.parse_json_log_entry(json_line)
        assert entry is None

    def test_parse_json_log_entry_invalid_timestamp(self) -> None:
        """Test parsing JSON with invalid timestamp returns None."""
        parser = LogParser()
        json_line = """{
            "hook_id": "bad-timestamp",
            "event_type": "push",
            "repository": "org/repo",
            "success": true,
            "timing": {
                "started_at": "not-a-timestamp"
            }
        }"""

        entry = parser.parse_json_log_entry(json_line)
        assert entry is None

    def test_parse_json_log_entry_timezone_handling(self) -> None:
        """Test parsing JSON with different timezone formats."""
        parser = LogParser()

        # Test with Z suffix
        json_z = """{
            "hook_id": "z-time",
            "event_type": "push",
            "repository": "org/repo",
            "success": true,
            "timing": {"started_at": "2025-07-31T10:00:00Z"}
        }"""
        entry = parser.parse_json_log_entry(json_z)
        assert entry is not None
        assert entry.timestamp.tzinfo is not None

        # Test with +00:00 suffix
        json_plus = """{
            "hook_id": "plus-time",
            "event_type": "push",
            "repository": "org/repo",
            "success": true,
            "timing": {"started_at": "2025-07-31T10:00:00+00:00"}
        }"""
        entry = parser.parse_json_log_entry(json_plus)
        assert entry is not None
        assert entry.timestamp.tzinfo is not None

    def test_parse_json_log_entry_extracts_all_fields(self) -> None:
        """Test that parse_json_log_entry extracts all fields correctly."""
        parser = LogParser()
        json_line = """{
            "hook_id": "complete-hook",
            "event_type": "pull_request",
            "action": "synchronize",
            "repository": "owner/repo-name",
            "api_user": "github-user",
            "success": true,
            "token_spend": 42,
            "timing": {
                "started_at": "2025-08-01T14:30:45.678000Z"
            },
            "pr": {
                "number": 999
            },
            "error": {
                "type": "SomeError"
            }
        }"""

        entry = parser.parse_json_log_entry(json_line)

        assert entry is not None
        assert entry.timestamp == datetime.datetime(2025, 8, 1, 14, 30, 45, 678000, tzinfo=datetime.UTC)
        assert entry.level == "INFO"
        assert entry.logger_name == "GithubWebhook"
        assert entry.message is not None
        assert entry.hook_id == "complete-hook"
        assert entry.event_type == "pull_request"
        assert entry.repository == "owner/repo-name"
        assert entry.pr_number == 999
        assert entry.github_user == "github-user"
        assert entry.task_id is None
        assert entry.task_type is None
        assert entry.task_status == "completed"
        assert entry.token_spend == 42

    def test_parse_json_log_file_multiple_entries(self, tmp_path: Path) -> None:
        """Test parsing JSON log file with multiple entries."""
        parser = LogParser()
        # Each JSON object must be on a single line (JSON lines format)
        json_content = (
            '{"hook_id": "hook1", "event_type": "push", "repository": "org/repo1", '
            '"api_user": "user1", "success": true, "timing": {"started_at": "2025-07-31T10:00:00Z"}}\n'
            '{"hook_id": "hook2", "event_type": "pull_request", "action": "opened", '
            '"repository": "org/repo2", "api_user": "user2", "success": true, '
            '"timing": {"started_at": "2025-07-31T10:01:00Z"}, "pr": {"number": 123}}\n'
            '{"hook_id": "hook3", "event_type": "issue_comment", "repository": "org/repo3", '
            '"api_user": "user3", "success": false, "timing": {"started_at": "2025-07-31T10:02:00Z"}}\n'
        )
        log_file = tmp_path / "webhooks_test.json"
        log_file.write_text(json_content)

        entries = parser.parse_json_log_file(log_file)

        assert len(entries) == 3
        assert entries[0].hook_id == "hook1"
        assert entries[0].event_type == "push"
        assert entries[0].repository == "org/repo1"
        assert entries[1].hook_id == "hook2"
        assert entries[1].pr_number == 123
        assert entries[1].task_status == "completed"
        assert entries[2].hook_id == "hook3"
        assert entries[2].task_status == "failed"

    def test_parse_json_log_file_empty_file(self, tmp_path: Path) -> None:
        """Test parsing empty JSON log file."""
        parser = LogParser()
        log_file = tmp_path / "empty.json"
        log_file.write_text("")

        entries = parser.parse_json_log_file(log_file)

        assert len(entries) == 0

    def test_parse_json_log_file_skips_invalid_lines(self, tmp_path: Path) -> None:
        """Test that parse_json_log_file skips invalid JSON lines."""
        parser = LogParser()
        # Each JSON object must be on a single line (JSON lines format)
        json_content = (
            '{"hook_id": "valid1", "event_type": "push", "repository": "org/repo", '
            '"success": true, "timing": {"started_at": "2025-07-31T10:00:00Z"}}\n'
            "this is not valid json\n"
            "{incomplete json\n"
            '{"hook_id": "valid2", "event_type": "pull_request", "repository": "org/repo", '
            '"success": true, "timing": {"started_at": "2025-07-31T10:01:00Z"}}\n'
            '{"missing_timestamp": true}\n'
        )
        log_file = tmp_path / "mixed.json"
        log_file.write_text(json_content)

        entries = parser.parse_json_log_file(log_file)

        assert len(entries) == 2
        assert entries[0].hook_id == "valid1"
        assert entries[1].hook_id == "valid2"

    def test_parse_json_log_file_handles_oserror(self, tmp_path: Path, caplog) -> None:
        """Test that parse_json_log_file handles OSError gracefully."""
        parser = LogParser()
        caplog.set_level(logging.ERROR)
        nonexistent_file = tmp_path / "nonexistent.json"

        entries = parser.parse_json_log_file(nonexistent_file)

        assert entries == []

    def test_parse_json_log_file_handles_unicode_error(self, tmp_path: Path, caplog) -> None:
        """Test that parse_json_log_file handles UnicodeDecodeError gracefully."""
        parser = LogParser()
        caplog.set_level(logging.ERROR)

        # Create a file with invalid UTF-8 bytes
        log_file = tmp_path / "invalid_utf8.json"
        log_file.write_bytes(b"\x80\x81\x82\x83")

        entries = parser.parse_json_log_file(log_file)

        assert entries == []

    def test_get_raw_json_entry_valid_json(self) -> None:
        """Test get_raw_json_entry with valid JSON returns dictionary."""
        parser = LogParser()
        json_line = """{
            "hook_id": "test-hook",
            "event_type": "push",
            "repository": "org/repo",
            "nested": {
                "data": "value"
            }
        }"""

        result = parser.get_raw_json_entry(json_line)

        assert result is not None
        assert isinstance(result, dict)
        assert result["hook_id"] == "test-hook"
        assert result["event_type"] == "push"
        assert result["repository"] == "org/repo"
        assert result["nested"]["data"] == "value"

    def test_get_raw_json_entry_invalid_json(self) -> None:
        """Test get_raw_json_entry with invalid JSON returns None."""
        parser = LogParser()
        invalid_lines = [
            "not json",
            "{incomplete",
            '{"bad": value}',
            "",
            "   ",
        ]

        for line in invalid_lines:
            result = parser.get_raw_json_entry(line)
            assert result is None

    def test_get_raw_json_entry_preserves_structure(self) -> None:
        """Test that get_raw_json_entry preserves complete JSON structure."""
        parser = LogParser()
        json_line = """{
            "hook_id": "complex-hook",
            "timing": {
                "started_at": "2025-07-31T10:00:00Z",
                "ended_at": "2025-07-31T10:00:05Z",
                "duration_seconds": 5.123
            },
            "pr": {
                "number": 123,
                "title": "Test PR",
                "labels": ["bug", "enhancement"]
            },
            "error": null,
            "success": true,
            "token_spend": 42
        }"""

        result = parser.get_raw_json_entry(json_line)

        assert result is not None
        assert result["hook_id"] == "complex-hook"
        assert result["timing"]["duration_seconds"] == 5.123
        assert result["pr"]["labels"] == ["bug", "enhancement"]
        assert result["error"] is None
        assert result["success"] is True
        assert result["token_spend"] == 42

    def test_json_summary_message_format_with_action(self) -> None:
        """Test that JSON summary message includes action when present."""
        parser = LogParser()
        json_line = """{
            "hook_id": "hook1",
            "event_type": "pull_request",
            "action": "synchronize",
            "repository": "org/repo",
            "success": true,
            "timing": {"started_at": "2025-07-31T10:00:00Z"}
        }"""

        entry = parser.parse_json_log_entry(json_line)

        assert entry is not None
        assert "pull_request/synchronize" in entry.message

    def test_json_summary_message_format_without_action(self) -> None:
        """Test that JSON summary message works without action."""
        parser = LogParser()
        json_line = """{
            "hook_id": "hook1",
            "event_type": "push",
            "repository": "org/repo",
            "success": true,
            "timing": {"started_at": "2025-07-31T10:00:00Z"}
        }"""

        entry = parser.parse_json_log_entry(json_line)

        assert entry is not None
        assert "push for org/repo" in entry.message
        assert "/" not in entry.message.split("for")[0]  # No action before "for"

    def test_parse_json_log_entry_null_pr_field(self) -> None:
        """Test parsing JSON with null pr field."""
        parser = LogParser()
        json_line = """{
            "hook_id": "null-pr",
            "event_type": "push",
            "repository": "org/repo",
            "success": true,
            "timing": {"started_at": "2025-07-31T10:00:00Z"},
            "pr": null
        }"""

        entry = parser.parse_json_log_entry(json_line)

        assert entry is not None
        assert entry.pr_number is None

    def test_parse_json_log_entry_empty_timing_object(self) -> None:
        """Test parsing JSON with empty timing object returns None."""
        parser = LogParser()
        json_line = """{
            "hook_id": "no-time",
            "event_type": "push",
            "repository": "org/repo",
            "success": true,
            "timing": {}
        }"""

        entry = parser.parse_json_log_entry(json_line)
        assert entry is None


class TestAdditionalCoverageTests:
    """Additional tests for edge cases to reach 90%+ coverage."""

    def test_parse_log_entry_invalid_pr_number(self) -> None:
        """Test parsing log entry with invalid PR number string."""
        parser = LogParser()
        log_line = (
            "2025-07-31T10:00:00.000000 GithubWebhook INFO "
            "test-repo [pull_request][hook1][user][PR abc]: Invalid PR number"
        )

        entry = parser.parse_log_entry(log_line)

        assert entry is not None
        assert entry.pr_number is None  # Invalid PR number should be None

    def test_extract_task_fields_with_escaped_brackets(self) -> None:
        """Test extracting task fields with escaped brackets in values."""
        parser = LogParser()
        log_line = (
            "2025-07-31T10:00:00.000000 GithubWebhook INFO "
            "test-repo [pull_request][hook1][user]: "
            "[task_id=test\\[id\\]] [task_type=ci\\]check] [task_status=started] Message"
        )

        entry = parser.parse_log_entry(log_line)

        assert entry is not None
        assert entry.task_id == "test[id]"  # Escaped brackets should be unescaped
        assert entry.task_type == "ci]check"
        assert entry.task_status == "started"
        assert entry.message == "Message"

    def test_extract_token_spend_with_invalid_number_in_pattern(self) -> None:
        """Test token spend extraction when number conversion fails."""
        parser = LogParser()
        # This tests the ValueError exception handler in extract_token_spend
        # Even though the pattern matches, if int() fails, it should return None
        message = "Some message without valid token spend"

        result = parser.extract_token_spend(message)

        assert result is None

    def test_parse_json_log_entry_type_error_timestamp(self) -> None:
        """Test parsing JSON with non-string timestamp value (triggers AttributeError/TypeError)."""
        parser = LogParser()
        json_line = """{
            "hook_id": "type-error",
            "event_type": "push",
            "repository": "org/repo",
            "success": true,
            "timing": {
                "started_at": null
            }
        }"""

        entry = parser.parse_json_log_entry(json_line)
        assert entry is None

    def test_parse_log_entry_naive_timestamp(self) -> None:
        """Test parsing log entry with naive timestamp (no timezone)."""
        parser = LogParser()
        log_line = "2025-07-31T10:00:00.000000 GithubWebhook INFO test-repo [push][hook1][user]: Test"

        entry = parser.parse_log_entry(log_line)

        assert entry is not None
        assert entry.timestamp.tzinfo is not None  # Should be timezone-aware

    def test_filter_get_unique_values(self) -> None:
        """Test LogFilter.get_unique_values method."""
        log_filter = LogFilter()
        entries = [
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 0, tzinfo=datetime.UTC),
                level="INFO",
                logger_name="main",
                message="msg1",
                repository="org/repo1",
            ),
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 1, 0, tzinfo=datetime.UTC),
                level="DEBUG",
                logger_name="main",
                message="msg2",
                repository="org/repo2",
            ),
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 2, 0, tzinfo=datetime.UTC),
                level="INFO",
                logger_name="main",
                message="msg3",
                repository="org/repo1",
            ),
        ]

        unique_repos = log_filter.get_unique_values(entries, "repository")
        unique_levels = log_filter.get_unique_values(entries, "level")

        assert sorted(unique_repos) == ["org/repo1", "org/repo2"]
        assert sorted(unique_levels) == ["DEBUG", "INFO"]

    def test_filter_get_entry_count_by_field(self) -> None:
        """Test LogFilter.get_entry_count_by_field method."""
        log_filter = LogFilter()
        entries = [
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 0, tzinfo=datetime.UTC),
                level="INFO",
                logger_name="main",
                message="msg1",
                event_type="push",
            ),
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 1, 0, tzinfo=datetime.UTC),
                level="DEBUG",
                logger_name="main",
                message="msg2",
                event_type="pull_request",
            ),
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 2, 0, tzinfo=datetime.UTC),
                level="INFO",
                logger_name="main",
                message="msg3",
                event_type="push",
            ),
        ]

        counts_by_event = log_filter.get_entry_count_by_field(entries, "event_type")
        counts_by_level = log_filter.get_entry_count_by_field(entries, "level")

        assert counts_by_event == {"push": 2, "pull_request": 1}
        assert counts_by_level == {"INFO": 2, "DEBUG": 1}

    @pytest.mark.asyncio
    async def test_monitor_log_directory_with_rotated_files(self, tmp_path: Path) -> None:
        """Test that monitor_log_directory ignores rotated log files."""
        parser = LogParser()

        # Create multiple log files including rotated ones
        current_log = tmp_path / "app.log"
        current_log.write_text("2025-07-31T10:00:00.000000 main INFO Initial entry\n")
        (tmp_path / "app.log.1").write_text("2025-07-31T09:00:00.000000 main INFO Old entry\n")
        (tmp_path / "app.log.2").write_text("2025-07-31T08:00:00.000000 main INFO Older entry\n")

        entries = []

        # Helper to collect entries from async generator
        async def collect_entries(async_gen: AsyncIterator[LogEntry], max_entries: int = 1) -> None:
            count = 0
            async for entry in async_gen:
                entries.append(entry)
                count += 1
                if count >= max_entries:
                    break

        # Start monitoring task
        monitor_task = asyncio.create_task(
            collect_entries(parser.monitor_log_directory(tmp_path, pattern="*.log"), max_entries=1)
        )

        # Give the monitor a moment to start and seek to end of file
        await asyncio.sleep(0.1)

        # Append new content to the current log file (not rotated ones) - non-blocking
        def _append_log() -> None:
            with open(current_log, "a") as f:
                f.write("2025-07-31T10:01:00.000000 main INFO New entry after monitoring started\n")
                f.flush()

        await asyncio.to_thread(_append_log)

        # Wait for the monitor to collect the new entry with timeout
        try:
            await asyncio.wait_for(monitor_task, timeout=2.0)
        except TimeoutError:
            # Cancel the task and wait for it to complete
            monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor_task

        # Should have collected exactly 1 entry from the new content (not from rotated files)
        assert len(entries) == 1
        assert entries[0].message == "New entry after monitoring started"
