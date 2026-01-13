"""Tests for SafeRotatingFileHandler."""

from __future__ import annotations

import logging
import os
import tempfile
from unittest.mock import patch

import simple_logger.logger

from webhook_server.utils import helpers  # noqa: F401 - import triggers patching
from webhook_server.utils.safe_rotating_handler import SafeRotatingFileHandler


class TestSafeRotatingFileHandler:
    """Tests for SafeRotatingFileHandler crash resilience."""

    def test_basic_rollover_works(self) -> None:
        """Test that basic rollover works normally."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            handler = SafeRotatingFileHandler(
                filename=log_file,
                maxBytes=100,
                backupCount=3,
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            try:
                # Write enough data to trigger rollover
                for _ in range(20):
                    record = logging.LogRecord(
                        name="test",
                        level=logging.INFO,
                        pathname="",
                        lineno=0,
                        msg="X" * 50,
                        args=(),
                        exc_info=None,
                    )
                    handler.emit(record)
            finally:
                handler.close()

    def test_rollover_handles_missing_backup_files(self) -> None:
        """Test that rollover gracefully handles missing backup files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            handler = SafeRotatingFileHandler(
                filename=log_file,
                maxBytes=100,
                backupCount=5,
            )
            try:
                # Create the log file
                with open(log_file, "w") as f:
                    f.write("initial content")

                # Manually trigger rollover - this should not crash
                # even if backup files don't exist
                handler.doRollover()

                # Verify log file can still be used
                assert handler.stream is not None

            finally:
                handler.close()

    def test_rollover_handles_file_deleted_during_operation(self) -> None:
        """Test rollover handles files deleted between exists() and operation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            handler = SafeRotatingFileHandler(
                filename=log_file,
                maxBytes=100,
                backupCount=3,
            )
            try:
                # Create the log file
                with open(log_file, "w") as f:
                    f.write("initial content")

                # Mock os.exists to return True, but os.remove will raise FileNotFoundError
                original_exists = os.path.exists

                def mock_exists(path: str) -> bool:
                    if path.endswith(".1"):
                        return True  # Pretend .1 exists
                    return original_exists(path)

                original_remove = os.remove

                def mock_remove(path: str) -> None:
                    if path.endswith(".1"):
                        raise FileNotFoundError(f"No such file: {path}")
                    return original_remove(path)

                with patch("os.path.exists", side_effect=mock_exists):
                    with patch("os.remove", side_effect=mock_remove):
                        # This should not crash
                        handler.doRollover()

                # Verify handler is still functional
                assert handler.stream is not None

            finally:
                handler.close()

    def test_rollover_handles_rename_file_not_found(self) -> None:
        """Test rollover handles FileNotFoundError during rename."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            handler = SafeRotatingFileHandler(
                filename=log_file,
                maxBytes=100,
                backupCount=3,
            )
            try:
                # Create the log file
                with open(log_file, "w") as f:
                    f.write("initial content")

                original_exists = os.path.exists

                def mock_exists(path: str) -> bool:
                    if ".1" in path or ".2" in path:
                        return True  # Pretend backup files exist
                    return original_exists(path)

                original_rename = os.rename

                def mock_rename(src: str, dst: str) -> None:
                    if ".1" in src or ".2" in src:
                        raise FileNotFoundError(f"No such file: {src}")
                    return original_rename(src, dst)

                with patch("os.path.exists", side_effect=mock_exists):
                    with patch("os.rename", side_effect=mock_rename):
                        # This should not crash
                        handler.doRollover()

                # Verify handler is still functional
                assert handler.stream is not None

            finally:
                handler.close()

    def test_rollover_with_nonexistent_base_file(self) -> None:
        """Test rollover when base file is deleted before rotate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            handler = SafeRotatingFileHandler(
                filename=log_file,
                maxBytes=100,
                backupCount=3,
            )
            try:
                # Create and immediately delete the log file
                with open(log_file, "w") as f:
                    f.write("initial content")

                # Open handler's stream
                if handler.stream is None:
                    handler.stream = handler._open()

                # Delete the file before rollover
                os.remove(log_file)

                # This should not crash
                handler.doRollover()

                # Verify handler created a new file
                assert handler.stream is not None

            finally:
                handler.close()


class TestSafeRotatingHandlerPatch:
    """Test that simple_logger is patched correctly."""

    def test_simple_logger_uses_safe_handler(self) -> None:
        """Test that importing helpers patches simple_logger.

        The helpers module patches simple_logger.logger.RotatingFileHandler
        at import time. Since helpers is imported transitively by many modules
        in this project, the patch should already be in place.
        """
        # Verify the patch is in place (helpers is imported at module level above)
        assert simple_logger.logger.RotatingFileHandler is SafeRotatingFileHandler
