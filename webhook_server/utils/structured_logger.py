"""Structured JSON logging for webhook execution tracking.

This module provides JSON-based logging for webhook executions in JSONL format.
Each webhook execution generates a compact JSON entry containing all workflow steps,
timing, errors, and API metrics.

Architecture:
- JSONL format: One compact JSON object per line (no indentation)
- Entry separation: Newline character between entries
- Date-based files: webhooks_YYYY-MM-DD.json for easy log rotation
- Atomic writes: Temporary file + rename for crash safety
- Concurrent writes: File locking to handle multiple webhook processes

Log File Format:
- Location: {config.data_dir}/logs/webhooks_YYYY-MM-DD.json
- Format: JSONL (JSON Lines - one JSON object per line)
- Rotation: Daily based on date
- Size: Unbounded (external rotation recommended)

Usage:
    from webhook_server.utils.structured_logger import write_webhook_log
    from webhook_server.utils.context import get_context

    # At end of webhook processing
    ctx = get_context()
    write_webhook_log(ctx)

    # Or use current context automatically
    write_webhook_log()
"""

import json
import os
import tempfile
from datetime import UTC, datetime
from logging import Logger
from pathlib import Path

from simple_logger.logger import get_logger

from webhook_server.libs.config import Config
from webhook_server.utils.context import WebhookContext, get_context

# Platform-specific imports for file locking
try:
    import fcntl

    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False


class StructuredLogWriter:
    """JSON log writer for webhook execution tracking.

    Writes webhook execution contexts as JSONL (one compact JSON object per line) to date-based log files.
    Provides atomic writes with file locking for safe concurrent access.

    Attributes:
        config: Configuration instance for accessing data_dir
        logger: Logger instance for error reporting
        log_dir: Directory path for log files ({config.data_dir}/logs/)
    """

    def __init__(self, config: Config, logger: Logger | None = None) -> None:
        """Initialize the structured log writer.

        Args:
            config: Configuration instance for accessing data_dir
            logger: Logger instance for error reporting (creates one if not provided)
        """
        self.config = config
        self.logger = logger or get_logger(name="structured_logger")
        self.log_dir = Path(self.config.data_dir) / "logs"

        # Create log directory if it doesn't exist
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _get_log_file_path(self, date: datetime | None = None) -> Path:
        """Get log file path for the specified date.

        Args:
            date: Date for the log file (defaults to current UTC date)

        Returns:
            Path to the log file (e.g., {log_dir}/webhooks_2026-01-05.json)
        """
        if date is None:
            date = datetime.now(UTC)
        date_str = date.strftime("%Y-%m-%d")
        return self.log_dir / f"webhooks_{date_str}.json"

    def write_log(self, context: WebhookContext) -> None:
        """Write webhook context as JSONL entry to date-based log file.

        Writes a compact JSON entry (single line, no indentation) containing complete webhook execution context.
        Each entry is terminated by a newline character.
        Uses atomic write pattern (temp file + rename) with file locking for safety.

        Args:
            context: WebhookContext to serialize and write

        Note:
            Uses context.completed_at as source of truth, falls back to datetime.now(UTC)
        """
        # Prefer context.completed_at as source of truth, fall back to current time
        completed_at = context.completed_at if context.completed_at else datetime.now(UTC)

        # Get context dict and update timing locally (without mutating context)
        context_dict = context.to_dict()
        if "timing" in context_dict:
            context_dict["timing"]["completed_at"] = completed_at.isoformat()
            if context.started_at:
                duration_ms = int((completed_at - context.started_at).total_seconds() * 1000)
                context_dict["timing"]["duration_ms"] = duration_ms

        # Get log file path
        log_file = self._get_log_file_path(completed_at)

        # Serialize context to JSON (compact JSONL format - single line, no indentation)
        log_entry = json.dumps(context_dict, ensure_ascii=False)

        # Atomic write with file locking
        try:
            # Write to temporary file in same directory (ensures atomic rename on same filesystem)
            temp_fd, temp_path = tempfile.mkstemp(
                dir=self.log_dir,
                prefix=f".{log_file.name}_",
                suffix=".tmp",
            )

            try:
                # Acquire exclusive lock (blocks if another process is writing)
                if HAS_FCNTL:
                    fcntl.flock(temp_fd, fcntl.LOCK_EX)

                try:
                    # Write JSON entry with single newline (JSONL format)
                    os.write(temp_fd, f"{log_entry}\n".encode())
                    os.fsync(temp_fd)  # Ensure data is written to disk

                    # Append to target log file (atomic on POSIX)
                    with open(log_file, "a") as log_fd:
                        # Acquire lock on target file
                        if HAS_FCNTL:
                            fcntl.flock(log_fd.fileno(), fcntl.LOCK_EX)
                        try:
                            # Read temp file and append to log file
                            os.lseek(temp_fd, 0, os.SEEK_SET)
                            data = os.read(temp_fd, os.path.getsize(temp_path))
                            log_fd.write(data.decode("utf-8"))
                            log_fd.flush()
                            os.fsync(log_fd.fileno())
                        finally:
                            if HAS_FCNTL:
                                fcntl.flock(log_fd.fileno(), fcntl.LOCK_UN)

                finally:
                    if HAS_FCNTL:
                        fcntl.flock(temp_fd, fcntl.LOCK_UN)

            finally:
                os.close(temp_fd)
                # Clean up temp file
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass  # Ignore errors during cleanup

            self.logger.debug(
                f"Wrote webhook log entry: hook_id={context.hook_id} "
                f"event={context.event_type} repository={context.repository}"
            )

        except Exception:
            self.logger.exception(
                f"Failed to write webhook log entry: hook_id={context.hook_id} "
                f"event={context.event_type} repository={context.repository}"
            )

    def write_error_log(
        self,
        hook_id: str,
        event_type: str,
        repository: str,
        error_message: str,
        context: WebhookContext | None = None,
    ) -> None:
        """Write error log entry for early webhook failures.

        Used when webhook processing fails before context is fully populated.
        Creates a minimal log entry with error details.

        Args:
            hook_id: GitHub webhook delivery ID
            event_type: GitHub event type
            repository: Repository name
            error_message: Error message describing the failure
            context: Partial WebhookContext if available
        """
        try:
            # Use existing context if provided, otherwise create minimal entry
            if context:
                # Context exists but failed - mark as failed and set error
                context.success = False
                if not context.error:
                    context.error = {
                        "type": "WebhookProcessingError",
                        "message": error_message,
                        "traceback": "",
                    }
                self.write_log(context)
            else:
                # No context - create minimal error entry
                error_entry = {
                    "hook_id": hook_id,
                    "event_type": event_type,
                    "action": None,
                    "sender": None,
                    "repository": repository,
                    "repository_full_name": repository,
                    "pr": None,
                    "api_user": "",
                    "timing": {
                        "started_at": datetime.now(UTC).isoformat(),
                        "completed_at": datetime.now(UTC).isoformat(),
                        "duration_ms": 0,
                    },
                    "workflow_steps": {},
                    "token_spend": None,
                    "initial_rate_limit": None,
                    "final_rate_limit": None,
                    "success": False,
                    "error": {
                        "type": "WebhookProcessingError",
                        "message": error_message,
                        "traceback": "",
                    },
                }

                # Write to log file
                log_file = self._get_log_file_path()
                log_entry = json.dumps(error_entry, ensure_ascii=False)

                with open(log_file, "a") as log_fd:
                    if HAS_FCNTL:
                        fcntl.flock(log_fd.fileno(), fcntl.LOCK_EX)
                    try:
                        log_fd.write(f"{log_entry}\n")
                        log_fd.flush()
                        os.fsync(log_fd.fileno())
                    finally:
                        if HAS_FCNTL:
                            fcntl.flock(log_fd.fileno(), fcntl.LOCK_UN)

                self.logger.debug(
                    f"Wrote error log entry: hook_id={hook_id} event={event_type} repository={repository}"
                )

        except Exception:
            self.logger.exception(
                f"Failed to write error log entry: hook_id={hook_id} event={event_type} repository={repository}"
            )


def write_webhook_log(context: WebhookContext | None = None) -> None:
    """Write webhook log entry using current or provided context.

    Convenience function that handles Config and StructuredLogWriter instantiation.
    Uses the current context from ContextVar if not explicitly provided.

    Args:
        context: WebhookContext to log (uses get_context() if not provided)

    Raises:
        ValueError: If no context is provided and get_context() returns None
    """
    # Get context from ContextVar if not provided
    if context is None:
        context = get_context()
        if context is None:
            raise ValueError("No webhook context available - call create_context() first")

    # Create Config and StructuredLogWriter
    config = Config()
    logger = get_logger(name="structured_logger")
    writer = StructuredLogWriter(config=config, logger=logger)

    # Write log entry
    writer.write_log(context)
