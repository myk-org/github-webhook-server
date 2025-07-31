"""Log parsing and filtering functionality for webhook server logs."""

import asyncio
import datetime
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncGenerator


@dataclass
class LogEntry:
    """Represents a parsed log entry with structured data."""

    timestamp: datetime.datetime
    level: str
    logger_name: str
    message: str
    hook_id: str | None = None
    event_type: str | None = None
    repository: str | None = None
    pr_number: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert LogEntry to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level,
            "logger_name": self.logger_name,
            "message": self.message,
            "hook_id": self.hook_id,
            "event_type": self.event_type,
            "repository": self.repository,
            "pr_number": self.pr_number,
        }


class LogParser:
    """Parser for webhook server log files."""

    # Regex patterns for parsing
    LOG_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - (\w+) - (\w+) - (.+)$")
    HOOK_CONTEXT_PATTERN = re.compile(r"\[Event: ([^\]]+)\]\[Delivery: ([^\]]+)\]")
    PR_NUMBER_PATTERN = re.compile(r"(?:PR|pull request) #(\d+)")
    REPOSITORY_PATTERN = re.compile(r"(?:repository:|Repository) ([^\s,]+)")

    def parse_log_entry(self, log_line: str) -> LogEntry | None:
        """
        Parse a single log line into a LogEntry object.

        Args:
            log_line: Raw log line string

        Returns:
            LogEntry object if parsing successful, None otherwise
        """
        if not log_line.strip():
            return None

        match = self.LOG_PATTERN.match(log_line.strip())
        if not match:
            return None

        timestamp_str, logger_name, level, message = match.groups()

        # Parse timestamp
        try:
            timestamp = datetime.datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S,%f")
        except ValueError:
            return None

        # Extract GitHub webhook context
        hook_id = self._extract_hook_id(message)
        event_type = self._extract_event_type(message)

        # Clean message by removing GitHub context
        cleaned_message = self._clean_message(message)

        # Extract additional metadata
        pr_number = self._extract_pr_number(cleaned_message)
        repository = self._extract_repository(cleaned_message)

        return LogEntry(
            timestamp=timestamp,
            level=level,
            logger_name=logger_name,
            message=cleaned_message,
            hook_id=hook_id,
            event_type=event_type,
            repository=repository,
            pr_number=pr_number,
        )

    def _extract_hook_id(self, message: str) -> str | None:
        """Extract hook delivery ID from log message."""
        match = self.HOOK_CONTEXT_PATTERN.search(message)
        if match:
            return match.group(2)  # Delivery ID
        return None

    def _extract_event_type(self, message: str) -> str | None:
        """Extract GitHub event type from log message."""
        match = self.HOOK_CONTEXT_PATTERN.search(message)
        if match:
            return match.group(1)  # Event type
        return None

    def _extract_pr_number(self, message: str) -> int | None:
        """Extract PR number from log message."""
        match = self.PR_NUMBER_PATTERN.search(message)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        return None

    def _extract_repository(self, message: str) -> str | None:
        """Extract repository name from log message."""
        match = self.REPOSITORY_PATTERN.search(message)
        if match:
            return match.group(1)
        return None

    def _clean_message(self, message: str) -> str:
        """Remove GitHub webhook context from message to get clean message text."""
        # Remove the [Event: ...][Delivery: ...] part from the beginning
        cleaned = self.HOOK_CONTEXT_PATTERN.sub("", message).strip()
        return cleaned

    def parse_log_file(self, file_path: Path) -> list[LogEntry]:
        """
        Parse an entire log file and return list of LogEntry objects.

        Args:
            file_path: Path to the log file

        Returns:
            List of successfully parsed LogEntry objects
        """
        entries: list[LogEntry] = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    entry = self.parse_log_entry(line)
                    if entry:
                        entries.append(entry)
        except (OSError, UnicodeDecodeError):
            # Handle file reading errors gracefully
            pass

        return entries

    async def tail_log_file(self, file_path: Path, follow: bool = True) -> AsyncGenerator[LogEntry, None]:
        """
        Tail a log file and yield new LogEntry objects as they are added.

        Args:
            file_path: Path to the log file to monitor
            follow: Whether to continue monitoring for new entries

        Yields:
            LogEntry objects for new log lines
        """
        # Start from the end of the file
        if not file_path.exists():
            return

        with open(file_path, "r", encoding="utf-8") as f:
            # Move to end of file
            f.seek(0, 2)

            while True:
                line = f.readline()
                if line:
                    entry = self.parse_log_entry(line)
                    if entry:
                        yield entry
                elif follow:
                    # No new data, wait a bit before checking again
                    await asyncio.sleep(0.1)
                else:
                    # Not following, exit when no more data
                    break

    async def monitor_log_directory(self, log_dir: Path, pattern: str = "*.log") -> AsyncGenerator[LogEntry, None]:
        """
        Monitor a directory for log files and yield new entries from all files.

        Args:
            log_dir: Directory path containing log files
            pattern: Glob pattern for log files (default: "*.log")

        Yields:
            LogEntry objects from all monitored log files
        """
        if not log_dir.exists() or not log_dir.is_dir():
            return

        # Find all existing log files
        log_files = list(log_dir.glob(pattern))

        if not log_files:
            return

        # For simplicity, monitor the first log file found
        # In a full implementation, we would use a more sophisticated approach
        # to monitor multiple files concurrently
        async for entry in self.tail_log_file(log_files[0], follow=True):
            yield entry


class LogFilter:
    """Filter log entries based on various criteria."""

    def filter_entries(
        self,
        entries: list[LogEntry],
        hook_id: str | None = None,
        pr_number: int | None = None,
        repository: str | None = None,
        event_type: str | None = None,
        level: str | None = None,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
        search_text: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[LogEntry]:
        """
        Filter log entries based on provided criteria.

        Args:
            entries: List of LogEntry objects to filter
            hook_id: Filter by exact hook ID match
            pr_number: Filter by exact PR number match
            repository: Filter by exact repository match
            event_type: Filter by event type (supports partial matching)
            level: Filter by exact log level match
            start_time: Filter entries after this timestamp
            end_time: Filter entries before this timestamp
            search_text: Filter by text search in message (case-insensitive)
            limit: Maximum number of entries to return
            offset: Number of entries to skip (for pagination)

        Returns:
            Filtered list of LogEntry objects
        """
        filtered = entries[:]

        # Apply filters
        if hook_id is not None:
            filtered = [e for e in filtered if e.hook_id == hook_id]

        if pr_number is not None:
            filtered = [e for e in filtered if e.pr_number == pr_number]

        if repository is not None:
            filtered = [e for e in filtered if e.repository == repository]

        if event_type is not None:
            filtered = [e for e in filtered if e.event_type and event_type in e.event_type]

        if level is not None:
            filtered = [e for e in filtered if e.level == level]

        if start_time is not None:
            filtered = [e for e in filtered if e.timestamp >= start_time]

        if end_time is not None:
            filtered = [e for e in filtered if e.timestamp <= end_time]

        if search_text is not None:
            search_lower = search_text.lower()
            filtered = [e for e in filtered if search_lower in e.message.lower()]

        # Apply pagination
        if offset is not None:
            filtered = filtered[offset:]

        if limit is not None:
            filtered = filtered[:limit]

        return filtered

    def get_unique_values(self, entries: list[LogEntry], field: str) -> list[str]:
        """
        Get unique values for a specific field across all entries.

        Args:
            entries: List of LogEntry objects
            field: Field name to get unique values for

        Returns:
            List of unique non-None values for the specified field
        """
        values = set()
        for entry in entries:
            value = getattr(entry, field, None)
            if value is not None:
                values.add(str(value))
        return sorted(list(values))

    def get_entry_count_by_field(self, entries: list[LogEntry], field: str) -> dict[str, int]:
        """
        Get count of entries grouped by a specific field.

        Args:
            entries: List of LogEntry objects
            field: Field name to group by

        Returns:
            Dictionary mapping field values to entry counts
        """
        counts: dict[str, int] = {}
        for entry in entries:
            value = getattr(entry, field, None)
            if value is not None:
                key = str(value)
                counts[key] = counts.get(key, 0) + 1
        return counts
