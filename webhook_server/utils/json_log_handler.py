"""JSON log handler that writes log records to the webhook JSONL log file.

Intercepts every log record and writes it as a JSON entry to the same
date-based JSONL file used by webhook summaries. Enriches entries with
webhook context (hook_id, repository, event_type, etc.) from the
ContextVar when available.

Architecture:
- Subclasses logging.Handler for standard library integration
- Reads WebhookContext from ContextVar for per-request enrichment
- Atomic append with fcntl file locking (same pattern as StructuredLogWriter)
- Never crashes the application — uses handleError() on failures

Entry format:
    {"type": "log_entry", "timestamp": "ISO8601", "level": "INFO",
     "logger_name": "...", "message": "...", "hook_id": "...", ...}
"""

import json
import logging
import os
import re
import traceback
from datetime import UTC, datetime
from pathlib import Path

# Platform-specific imports for file locking
try:
    import fcntl

    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False

from webhook_server.utils.context import get_context

# Pre-compiled regex for stripping ANSI escape codes
_ANSI_ESCAPE_RE: re.Pattern[str] = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


class JsonLogHandler(logging.Handler):
    """Logging handler that writes JSON entries to the webhook JSONL log file.

    Each log record is serialized as a compact JSON object and appended to
    the date-based log file (webhooks_YYYY-MM-DD.json). The handler enriches
    entries with webhook context data when available.

    Attributes:
        log_dir: Directory path for log files
    """

    def __init__(self, log_dir: str, level: int = logging.NOTSET) -> None:
        """Initialize the JSON log handler.

        Args:
            log_dir: Directory path where JSONL log files are written
            level: Minimum log level to handle (default: NOTSET, handles all)
        """
        super().__init__(level)
        self.log_dir: Path = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _get_log_file_path(self) -> Path:
        """Get log file path for the current UTC date.

        Returns:
            Path to the log file (e.g., {log_dir}/webhooks_2026-01-05.json)
        """
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        return self.log_dir / f"webhooks_{date_str}.json"

    def emit(self, record: logging.LogRecord) -> None:
        """Write a log record as JSON to the JSONL log file.

        Builds a JSON dict from the LogRecord and enriches it with webhook
        context data (hook_id, repository, event_type, etc.) when available.

        Args:
            record: The log record to write
        """
        try:
            entry = self._build_entry(record)
            log_line = json.dumps(entry, ensure_ascii=False)
            self._append_to_file(log_line)
        except Exception:
            self.handleError(record)

    def _build_entry(self, record: logging.LogRecord) -> dict[str, object]:
        """Build a JSON-serializable dict from a LogRecord.

        Strips ANSI codes from the message and enriches with webhook
        context when available.

        Args:
            record: The log record to convert

        Returns:
            Dict ready for JSON serialization
        """
        message = record.getMessage()
        message = _ANSI_ESCAPE_RE.sub("", message)

        exc_text: str | None = None
        if record.exc_info and record.exc_info[0] is not None:
            exc_text = "".join(traceback.format_exception(*record.exc_info))

        entry: dict[str, object] = {
            "type": "log_entry",
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger_name": record.name,
            "message": message,
        }

        if exc_text:
            entry["exc_info"] = exc_text

        # Enrich with webhook context when available
        ctx = get_context()
        if ctx is not None:
            entry["hook_id"] = ctx.hook_id
            entry["event_type"] = ctx.event_type
            entry["repository"] = ctx.repository
            entry["pr_number"] = ctx.pr_number
            entry["api_user"] = ctx.api_user

        return entry

    def _append_to_file(self, log_line: str) -> None:
        """Atomically append a JSON line to the log file.

        Uses fcntl file locking when available for safe concurrent access.

        Args:
            log_line: JSON string to append (without trailing newline)
        """
        log_file = self._get_log_file_path()

        with open(log_file, "a", encoding="utf-8") as fd:
            if HAS_FCNTL:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
            try:
                fd.write(f"{log_line}\n")
                fd.flush()
                os.fsync(fd.fileno())
            finally:
                if HAS_FCNTL:
                    fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
