"""Tests for log parsing functionality."""

import asyncio
import datetime
import tempfile
from pathlib import Path

import pytest

from webhook_server.libs.log_parser import LogEntry, LogFilter, LogParser


class TestLogParser:
    """Test cases for LogParser class."""

    def test_parse_log_entry_with_hook_context(self) -> None:
        """Test parsing log entry with GitHub delivery context."""
        log_line = (
            "2025-07-31 10:30:00,123 - main - INFO - "
            "[Event: pull_request][Delivery: abc123-def456] "
            "Processing webhook for repository: test-repo"
        )

        parser = LogParser()
        entry = parser.parse_log_entry(log_line)

        assert entry is not None
        assert entry.timestamp == datetime.datetime(2025, 7, 31, 10, 30, 0, 123000)
        assert entry.level == "INFO"
        assert entry.logger_name == "main"
        assert entry.hook_id == "abc123-def456"
        assert entry.event_type == "pull_request"
        assert entry.message == "Processing webhook for repository: test-repo"
        assert entry.repository == "test-repo"

    def test_parse_log_entry_with_pr_number(self) -> None:
        """Test parsing log entry containing PR number."""
        log_line = (
            "2025-07-31 11:15:30,456 - main - DEBUG - "
            "[Event: pull_request.opened][Delivery: xyz789] "
            "Processing webhook for PR #123"
        )

        parser = LogParser()
        entry = parser.parse_log_entry(log_line)

        assert entry is not None
        assert entry.hook_id == "xyz789"
        assert entry.event_type == "pull_request.opened"
        assert entry.pr_number == 123
        assert "PR #123" in entry.message

    def test_parse_log_entry_without_hook_context(self) -> None:
        """Test parsing regular log entry without GitHub context."""
        log_line = "2025-07-31 12:45:00,789 - helpers - WARNING - API rate limit remaining: 1500"

        parser = LogParser()
        entry = parser.parse_log_entry(log_line)

        assert entry is not None
        assert entry.timestamp == datetime.datetime(2025, 7, 31, 12, 45, 0, 789000)
        assert entry.level == "WARNING"
        assert entry.logger_name == "helpers"
        assert entry.hook_id is None
        assert entry.event_type is None
        assert entry.pr_number is None
        assert entry.message == "API rate limit remaining: 1500"

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

    def test_extract_hook_id_from_context(self) -> None:
        """Test hook ID extraction from various context formats."""
        test_cases = [
            ("[Event: push][Delivery: abc123]", "abc123"),
            ("[Event: pull_request.opened][Delivery: def456-ghi789]", "def456-ghi789"),
            ("[Event: issue_comment][Delivery: 12345]", "12345"),
            ("No context here", None),
            ("[Event: push]", None),  # Missing delivery
            ("[Delivery: xyz]", None),  # Missing event
        ]

        parser = LogParser()
        for context, expected in test_cases:
            result = parser._extract_hook_id(context)
            assert result == expected

    def test_extract_pr_number_from_message(self) -> None:
        """Test PR number extraction from log messages."""
        test_cases = [
            ("Processing webhook for PR #123", 123),
            ("Updated labels for pull request #456", 456),
            ("PR #789 merged successfully", 789),
            ("No PR number in this message", None),
            ("PR without number", None),
            ("Issue #123 created", None),  # Should not match issues
        ]

        parser = LogParser()
        for message, expected in test_cases:
            result = parser._extract_pr_number(message)
            assert result == expected

    def test_extract_repository_name(self) -> None:
        """Test repository name extraction from log messages."""
        test_cases = [
            ("Processing webhook for repository: myorg/myrepo", "myorg/myrepo"),
            ("Repository test-repo updated", "test-repo"),
            ("Processing webhook for repository: single-name", "single-name"),
            ("No repository mentioned", None),
        ]

        parser = LogParser()
        for message, expected in test_cases:
            result = parser._extract_repository(message)
            assert result == expected

    def test_parse_log_file(self) -> None:
        """Test parsing multiple log entries from a file."""
        log_content = """2025-07-31 10:00:00,000 - main - INFO - [Event: push][Delivery: delivery1] Start processing
2025-07-31 10:00:01,000 - main - DEBUG - [Event: push][Delivery: delivery1] Validating signature
2025-07-31 10:00:02,000 - main - SUCCESS - [Event: push][Delivery: delivery1] Processing complete
2025-07-31 10:01:00,000 - main - INFO - [Event: pull_request][Delivery: delivery2] Processing webhook for PR #456
Invalid log line
2025-07-31 10:01:05,000 - main - ERROR - [Event: pull_request][Delivery: delivery2] Processing failed"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(log_content)
            f.flush()

            parser = LogParser()
            entries = parser.parse_log_file(Path(f.name))

        # Should parse 5 valid entries and skip the invalid one
        assert len(entries) == 5
        assert entries[0].hook_id == "delivery1"
        assert entries[0].event_type == "push"
        assert entries[3].pr_number == 456
        assert entries[4].level == "ERROR"

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
        initial_content = """2025-07-31 10:00:00,000 - main - INFO - Initial entry"""

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
                append_f.write("\n2025-07-31 10:01:00,000 - main - DEBUG - New entry 1")
                append_f.write("\n2025-07-31 10:02:00,000 - main - ERROR - New entry 2")
                append_f.flush()

            # Wait for the tail to collect entries
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
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 11, 0, 0),
                level="INFO",
                logger_name="main",
                message="Processing PR #456",
                hook_id="hook3",
                event_type="pull_request.closed",
                repository="org/repo2",
                pr_number=456,
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

        # Test partial event type match
        filtered = log_filter.filter_entries(sample_entries, event_type="pull_request")
        assert len(filtered) == 2
        assert all("pull_request" in str(entry.event_type) for entry in filtered)

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
        filtered = log_filter.filter_entries(sample_entries, repository="org/repo1", event_type="pull_request")
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
