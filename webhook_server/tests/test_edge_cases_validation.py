"""Edge case validation tests for webhook server log functionality."""

import asyncio
import concurrent.futures
import datetime
import os
import tempfile
import time
from collections.abc import Callable, Generator
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from fastapi.websockets import WebSocketDisconnect
from simple_logger.logger import get_logger

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False

from webhook_server.libs.log_parser import LogEntry, LogFilter, LogParser
from webhook_server.web.log_viewer import LogViewerController


@pytest.fixture
def temp_log_file() -> Generator[Callable[[str, str], Path], None, None]:
    """Fixture that provides a helper function to create temporary log files with content.

    Returns a function that takes log content and optional encoding,
    creates a temporary file, writes the content, and returns the file path.
    The file is automatically cleaned up after the test.
    """
    created_files = []

    def create_temp_log_file(content: str, encoding: str = "utf-8") -> Path:
        """Create a temporary log file with the given content.

        Args:
            content: The log content to write to the file
            encoding: File encoding (default: utf-8)

        Returns:
            Path to the created temporary file
        """
        temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding=encoding)
        temp_file.write(content)
        temp_file.flush()
        temp_file.close()

        file_path = Path(temp_file.name)
        created_files.append(file_path)
        return file_path

    yield create_temp_log_file

    # Cleanup: remove all created temporary files
    for file_path in created_files:
        try:
            if file_path.exists():
                file_path.unlink()
        except OSError:
            pass  # Ignore cleanup errors


def parse_log_content_helper(content: str, encoding: str = "utf-8") -> list[LogEntry]:
    """Helper function to parse log content using a temporary file.

    Args:
        content: The log content to parse
        encoding: File encoding (default: utf-8)

    Returns:
        List of parsed LogEntry objects
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding=encoding) as f:
        f.write(content)
        f.flush()

        parser = LogParser()
        entries = parser.parse_log_file(Path(f.name))

        # Clean up the temporary file
        try:
            Path(f.name).unlink()
        except OSError:
            pass  # Ignore cleanup errors

        return entries


class TestLogParsingEdgeCases:
    """Test edge cases in log parsing functionality."""

    def test_extremely_large_log_files(self, temp_log_file):
        """Test handling of large log files with optimized test data."""
        # Use a more reasonable test size (10K entries) to test large file handling
        # while keeping test execution time reasonable
        lines = []
        for i in range(10000):
            # Create proper timestamp with microseconds
            timestamp = datetime.datetime(2025, 7, 31, 10, 0, 0, i * 100)
            timestamp_str = timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")

            lines.append(f"{timestamp_str} GithubWebhook INFO repo-{i % 100} [push][hook-{i}][user]: Entry {i}")

        large_content = "\n".join(lines)
        log_file_path = temp_log_file(large_content)

        parser = LogParser()
        # Should handle large files without crashing
        entries = parser.parse_log_file(log_file_path)

        # Verify parsing worked
        assert len(entries) > 9500  # Allow for some parsing failures
        assert entries[0].timestamp < entries[-1].timestamp  # Chronological order

        # Test that the parser can handle the file efficiently
        # (This validates the large file handling logic without requiring massive data)

        # Memory should be manageable (skip if psutil not available)
        if PSUTIL_AVAILABLE:
            process = psutil.Process(os.getpid())
            memory_mb = process.memory_info().rss / 1024 / 1024
            assert memory_mb < 512  # Should not exceed 512MB memory usage for test environments
        else:
            pytest.skip("psutil not available for memory monitoring")

    def test_malformed_log_entries_handling(self):
        """Test handling of various malformed log entries."""
        malformed_content = """
        # Comment line

        Invalid line without timestamp
        2025-07-31 GithubWebhook INFO Missing microseconds
        2025-07-31T25:70:99.999999 GithubWebhook INFO Invalid timestamp
        2025-07-31T10:00:00.000000 GithubWebhook Invalid message with missing fields
        2025-07-31T10:00:00.000000 INFO Missing logger name
        2025-07-31T10:00:00.000000 GithubWebhook
        2025-07-31T10:00:00.000000 GithubWebhook INFO Valid entry after malformed ones
        Completely random text
        {"json": "object", "instead": "of log line"}
        2025-07-31T10:00:01.000000 GithubWebhook DEBUG Another valid entry
        Line with unicode characters: 🚀 💻 ✅
        Normal length line for testing standard parsing behavior
        2025-07-31T10:00:02.000000 GithubWebhook ERROR Final valid entry
        """

        entries = parse_log_content_helper(malformed_content)

        # Should parse entries that match the basic log format
        # The parser is tolerant and will parse entries that have valid timestamp/logger/level format
        # even if the content isn't in GitHub webhook format
        assert len(entries) == 5  # Valid timestamp format entries get parsed
        assert entries[-1].level == "ERROR"
        assert entries[-1].message == "Final valid entry"

        # Verify that malformed timestamps and completely invalid lines are skipped
        # The parser should skip lines without proper timestamp format

    def test_concurrent_file_access(self, temp_log_file):
        """Test concurrent access to the same log file."""
        content = """2025-07-31T10:00:00.000000 GithubWebhook INFO repo [push][hook-1][user]: Entry 1
2025-07-31T10:00:01.000000 GithubWebhook INFO repo [push][hook-2][user]: Entry 2
2025-07-31T10:00:02.000000 GithubWebhook INFO repo [push][hook-3][user]: Entry 3"""

        log_path = temp_log_file(content)
        parser = LogParser()

        # Simulate concurrent access
        def parse_file():
            return parser.parse_log_file(log_path)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(parse_file) for _ in range(10)]
            results = [future.result() for future in futures]

        # All concurrent reads should succeed
        assert len(results) == 10
        assert all(len(entries) == 3 for entries in results)
        assert all(entries[0].message == "Entry 1" for entries in results)

    def test_file_rotation_during_monitoring(self):
        """Test log monitoring behavior during file rotation."""
        # This test simulates log rotation scenarios
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "test.log"

            # Create initial log file
            with open(log_path, "w") as f:
                f.write("2025-07-31T10:00:00.000000 GithubWebhook INFO test: Initial entry\n")

            parser = LogParser()
            monitored_entries = []

            async def monitor_logs():
                try:
                    async for entry in parser.tail_log_file(log_path, follow=True):
                        monitored_entries.append(entry)
                        if len(monitored_entries) >= 3:
                            break
                except Exception as e:
                    # Handle file rotation gracefully
                    logger = get_logger(name="test")
                    logger.debug(f"Monitoring exception (expected): {e}")

            async def simulate_rotation():
                # Add entry to original file
                with open(log_path, "a") as f:
                    f.write("2025-07-31T10:00:01.000000 GithubWebhook INFO test: Before rotation\n")

                # Simulate log rotation (move file, create new one)
                rotated_path = Path(temp_dir) / "test.log.1"
                log_path.rename(rotated_path)

                # Create new log file
                with open(log_path, "w") as f:
                    f.write("2025-07-31T10:00:02.000000 GithubWebhook INFO test: After rotation\n")

                # Add more entries
                with open(log_path, "a") as f:
                    f.write("2025-07-31T10:00:03.000000 GithubWebhook INFO test: New file entry\n")

            # Run monitoring and rotation simulation
            async def run_test():
                monitor_task = asyncio.create_task(monitor_logs())
                rotation_task = asyncio.create_task(simulate_rotation())

                try:
                    await asyncio.wait_for(
                        asyncio.gather(monitor_task, rotation_task, return_exceptions=True),
                        timeout=1.0,  # Reduced from 5.0 to 1.0 second
                    )
                except TimeoutError:
                    # Catch TimeoutError from asyncio.wait_for timeout
                    # Note: In Python 3.11+, TimeoutError and asyncio.TimeoutError are aliased
                    monitor_task.cancel()
                    rotation_task.cancel()
                    # Await tasks after cancellation to avoid "Task was destroyed" warnings
                    await asyncio.gather(monitor_task, rotation_task, return_exceptions=True)

            asyncio.run(run_test())

            # Should handle rotation gracefully and capture at least some entries
            # The monitor should capture at least the "Before rotation" entry since it's added after monitoring starts
            # During rotation, some entries might be missed, but the monitor should capture at least 1 entry
            assert len(monitored_entries) >= 1, (
                f"Expected at least 1 monitored entry, got {len(monitored_entries)}. "
                f"Entries: {[e.message for e in monitored_entries]}"
            )

            # Verify that captured entries are valid LogEntry objects with expected content
            for entry in monitored_entries:
                assert hasattr(entry, "message"), "Monitored entry should have a message attribute"
                assert hasattr(entry, "timestamp"), "Monitored entry should have a timestamp attribute"
                assert "test:" in entry.message, f"Expected 'test:' in message, got: {entry.message}"

    def test_unicode_and_special_characters(self):
        """Test handling of unicode and special characters in log entries."""
        unicode_content = "\n".join([
            "2025-07-31T10:00:00.000000 GithubWebhook INFO test-repo [push][hook-1][user]: Unicode: 🚀 ✅ 💻",
            "2025-07-31T10:00:01.000000 GithubWebhook INFO test-repo [push][hook-2][user]: émojis: café naïve",
            "2025-07-31T10:00:02.000000 GithubWebhook INFO test-repo [push][hook-3][user]: Chinese: 你好世界",
            "2025-07-31T10:00:03.000000 GithubWebhook INFO test-repo [push][hook-4][user]: Arabic: مرحبا",
            "2025-07-31T10:00:04.000000 GithubWebhook INFO test-repo [push][hook-5][user]: Special: @#$%^&*()",
            "2025-07-31T10:00:05.000000 GithubWebhook INFO test-repo [push][hook-6][user]: Newlines: \\n\\t",
            "2025-07-31T10:00:06.000000 GithubWebhook INFO test-repo [push][hook-7][user]: Quotes: 'single'",
            "",
        ])

        entries = parse_log_content_helper(unicode_content, encoding="utf-8")

        # Should parse all unicode entries correctly
        assert len(entries) == 7
        assert "🚀" in entries[0].message
        assert "café" in entries[1].message
        assert "你好世界" in entries[2].message
        assert "مرحبا" in entries[3].message
        assert "@#$%^&*()" in entries[4].message

        # Test filtering with unicode
        log_filter = LogFilter()
        unicode_filtered = log_filter.filter_entries(entries, search_text="🚀")
        assert len(unicode_filtered) == 1
        assert "🚀" in unicode_filtered[0].message

    def test_empty_and_whitespace_only_files(self):
        """Test handling of empty or whitespace-only files."""
        test_cases = [
            "",  # Completely empty
            "   ",  # Only spaces
            "\n\n\n",  # Only newlines
            "\t\t\t",  # Only tabs
            "   \n  \t  \n  ",  # Mixed whitespace
        ]

        for _i, content in enumerate(test_cases):
            entries = parse_log_content_helper(content)

            # Should handle gracefully without errors
            assert entries == []  # No valid entries
            assert isinstance(entries, list)

    def test_very_long_individual_log_lines(self):
        """Test handling of extremely long individual log lines."""
        # Generate very long message
        long_message = "Very long message: " + "A" * 100000  # 100KB message

        long_line_content = (
            "2025-07-31T10:00:00.000000 GithubWebhook INFO test-repo [push][hook-1][user]: Normal message\n"
            f"2025-07-31T10:00:01.000000 GithubWebhook INFO test-repo [push][hook-2][user]: {long_message}\n"
            "2025-07-31T10:00:02.000000 GithubWebhook INFO test-repo [push][hook-3][user]: Another normal message"
        )

        entries = parse_log_content_helper(long_line_content)

        # Should handle very long lines
        assert len(entries) == 3
        assert "Normal message" in entries[0].message
        assert len(entries[1].message) > 100000  # Very long message
        assert "Another normal message" in entries[2].message


class TestFilteringEdgeCases:
    """Test edge cases in log filtering functionality."""

    def create_complex_test_dataset(self) -> list[LogEntry]:
        """Create a complex test dataset with edge cases."""
        entries = []

        # Various edge case entries
        edge_cases = [
            # Null/None values
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 0),
                level="INFO",
                logger_name="test",
                message="Entry with nulls",
                hook_id=None,
                event_type=None,
                repository=None,
                pr_number=None,
                github_user=None,
            ),
            # Empty strings
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 1),
                level="",
                logger_name="",
                message="",
                hook_id="",
                event_type="",
                repository="",
                pr_number=None,
                github_user="",
            ),
            # Very long strings
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 2),
                level="INFO",
                logger_name="test",
                message="Very long message: " + "X" * 10000,
                hook_id="hook-long-" + "Y" * 1000,
                event_type="very_long_event_type_" + "Z" * 500,
                repository="repo/" + "W" * 2000,
                pr_number=999999999,
                github_user="user_" + "U" * 100,
            ),
            # Special characters
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 3),
                level="DEBUG",
                logger_name="test",
                message="Special chars: @#$%^&*(){}[]|\\:\";'<>?,./",
                hook_id="hook-special-!@#$%",
                event_type="event.with.dots",
                repository="repo/with-dashes_and_underscores",
                pr_number=0,  # Edge case: PR number 0
                github_user="user@domain.com",
            ),
            # Unicode characters
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 4),
                level="ERROR",
                logger_name="test",
                message="Unicode: 🚀 ✅ 💻 你好 مرحبا",
                hook_id="hook-unicode-🚀",
                event_type="unicode_event_💻",
                repository="repo/unicode-🌟",
                pr_number=42,
                github_user="user-💻",
            ),
        ]

        entries.extend(edge_cases)
        return entries

    def test_filtering_with_null_values(self):
        """Test filtering behavior with null/None values."""
        entries = self.create_complex_test_dataset()
        log_filter = LogFilter()

        # Filter behavior with None values - the current implementation doesn't filter
        # when None is passed (it means "don't filter by this field")
        # So we test that passing None returns all entries
        none_hook_filtered = log_filter.filter_entries(entries, hook_id=None)
        assert len(none_hook_filtered) == len(entries)  # No filtering applied

        # Filter by non-None values (should exclude None entries)
        non_none_filtered = log_filter.filter_entries(entries, hook_id="hook-special-!@#$%")
        assert len(non_none_filtered) >= 1
        assert all(entry.hook_id == "hook-special-!@#$%" for entry in non_none_filtered)

    def test_filtering_with_empty_strings(self):
        """Test filtering behavior with empty strings."""
        entries = self.create_complex_test_dataset()
        log_filter = LogFilter()

        # Filter by empty string
        empty_level_filtered = log_filter.filter_entries(entries, level="")
        assert len(empty_level_filtered) >= 1
        assert all(entry.level == "" for entry in empty_level_filtered)

    def test_filtering_with_special_characters(self):
        """Test filtering with special characters and regex-sensitive content."""
        entries = self.create_complex_test_dataset()
        log_filter = LogFilter()

        # Test special characters in search
        special_char_searches = [
            "@#$%",
            "[]",
            "()",
            "\\",
            "'",
            '"',
            ".",
        ]

        for search_term in special_char_searches:
            try:
                filtered = log_filter.filter_entries(entries, search_text=search_term)
                assert isinstance(filtered, list)  # Should not crash
            except Exception as e:
                pytest.fail(f"Filtering failed with special character '{search_term}': {e}")

    def test_filtering_with_unicode(self):
        """Test filtering with unicode characters."""
        entries = self.create_complex_test_dataset()
        log_filter = LogFilter()

        # Test unicode searches
        unicode_searches = ["🚀", "你好", "مرحبا", "💻"]

        for search_term in unicode_searches:
            filtered = log_filter.filter_entries(entries, search_text=search_term)
            assert isinstance(filtered, list)
            if filtered:  # If any matches found
                assert any(search_term in entry.message for entry in filtered)

    def test_filtering_performance_with_large_strings(self):
        """Test filtering performance with very large string values."""
        entries = self.create_complex_test_dataset()
        log_filter = LogFilter()

        # Test search in very long content
        start_time = time.perf_counter()
        long_string_filtered = log_filter.filter_entries(entries, search_text="X" * 100)
        end_time = time.perf_counter()

        filter_duration = end_time - start_time

        # Should complete quickly even with large strings
        # Threshold set to 1.0s for local development validation
        # This test is automatically skipped in CI to prevent flakiness
        assert filter_duration < 1.0, f"Filtering took {filter_duration:.2f}s, expected < 1.0s"
        assert isinstance(long_string_filtered, list)

    def test_extreme_pagination_values(self):
        """Test filtering with extreme pagination values."""
        entries = self.create_complex_test_dataset()
        log_filter = LogFilter()

        # Test extreme pagination values
        test_cases = [
            {"limit": 0, "offset": 0},  # Zero limit
            {"limit": 1, "offset": 1000000},  # Very large offset
            {"limit": 1000000, "offset": 0},  # Very large limit
            {"limit": -1, "offset": -1},  # Negative values (should be handled gracefully)
        ]

        for params in test_cases:
            try:
                filtered = log_filter.filter_entries(entries, **params)
                assert isinstance(filtered, list)
                # For extreme values, just ensure no crash
                assert len(filtered) >= 0
            except Exception as e:
                # Some extreme values might raise exceptions - that's acceptable
                assert "invalid" in str(e).lower() or "negative" in str(e).lower()

    def test_multiple_filter_combinations(self):
        """Test complex combinations of multiple filters."""
        entries = self.create_complex_test_dataset()
        log_filter = LogFilter()

        # Complex filter combinations
        complex_filters = [
            {
                "level": "INFO",
                "search_text": "Special",
                "hook_id": "hook-special-!@#$%",
                "limit": 10,
            },
            {
                "repository": "repo/unicode-🌟",
                "event_type": "unicode_event_💻",
                "github_user": "user-💻",
                "pr_number": 42,
            },
            {
                "start_time": datetime.datetime(2025, 7, 31, 10, 0, 0),
                "end_time": datetime.datetime(2025, 7, 31, 10, 0, 5),
                "level": "ERROR",
                "search_text": "Unicode",
            },
        ]

        for filter_params in complex_filters:
            filtered = log_filter.filter_entries(entries, **filter_params)
            assert isinstance(filtered, list)
            # Verify all filter conditions are satisfied
            for entry in filtered:
                if "level" in filter_params and filter_params["level"]:
                    assert entry.level == filter_params["level"]
                if "repository" in filter_params and filter_params["repository"]:
                    assert entry.repository == filter_params["repository"]


class TestWebSocketEdgeCases:
    """Test edge cases in WebSocket functionality."""

    @pytest.mark.asyncio
    async def test_websocket_connection_limits(self):
        """Test WebSocket behavior under connection limits."""

        mock_logger = Mock()
        controller = LogViewerController(logger=mock_logger)

        # Mock multiple WebSocket connections (only what we actually use)
        mock_websockets = []
        for _ in range(10):  # Only create the 10 connections we actually test
            mock_ws = AsyncMock()
            mock_ws.accept = AsyncMock()
            mock_ws.send_json = AsyncMock()
            mock_websockets.append(mock_ws)

        # Mock log directory to exist
        with patch.object(controller, "_get_log_directory") as mock_get_dir:
            mock_dir = Mock()
            mock_dir.exists.return_value = True
            mock_get_dir.return_value = mock_dir

            # Mock monitor to yield entries continuously
            async def mock_monitor():
                i = 0
                while True:  # Run indefinitely
                    yield LogEntry(
                        timestamp=datetime.datetime.now(),
                        level="INFO",
                        logger_name="test",
                        message=f"Test {i}",
                        hook_id="test",
                    )
                    i += 1
                    await asyncio.sleep(0.1)  # Longer sleep

            with patch.object(controller.log_parser, "monitor_log_directory", return_value=mock_monitor()):
                # Test handling multiple connections simultaneously
                tasks = []
                for ws in mock_websockets:  # Use all 10 connections
                    task = asyncio.create_task(controller.handle_websocket(ws))
                    tasks.append(task)

                # Let them run briefly
                await asyncio.sleep(0.1)

                # Cancel all tasks
                for task in tasks:
                    task.cancel()

                # Wait for cancellation
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Should handle multiple connections without crashing
                assert len(results) == 10
                # Most should be cancelled, which is expected
                cancelled_count = sum(1 for r in results if isinstance(r, asyncio.CancelledError))
                assert cancelled_count > 0

    @pytest.mark.asyncio
    async def test_websocket_with_rapid_disconnections(self):
        """Test WebSocket handling with rapid connect/disconnect cycles."""

        mock_logger = Mock()
        controller = LogViewerController(logger=mock_logger)

        # Test rapid disconnection scenarios
        for _ in range(10):
            mock_ws = AsyncMock()
            mock_ws.accept = AsyncMock()

            # Simulate immediate disconnection
            mock_ws.send_json = AsyncMock(side_effect=WebSocketDisconnect())

            with patch.object(controller, "_get_log_directory") as mock_get_dir:
                mock_dir = Mock()
                mock_dir.exists.return_value = True
                mock_get_dir.return_value = mock_dir

                # Should handle disconnection gracefully
                await controller.handle_websocket(mock_ws)
                mock_ws.accept.assert_called_once()

    @pytest.mark.asyncio
    async def test_websocket_filters_none_entries(self):
        """Test that WebSocket filters out None entries gracefully."""

        mock_logger = Mock()
        controller = LogViewerController(logger=mock_logger)

        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_json = AsyncMock()

        with patch.object(controller, "_get_log_directory") as mock_get_dir:
            mock_dir = Mock()
            mock_dir.exists.return_value = True
            mock_get_dir.return_value = mock_dir

            async def mock_monitor_with_none():
                # Yield valid entry
                yield LogEntry(
                    timestamp=datetime.datetime.now(),
                    level="INFO",
                    logger_name="test",
                    message="Valid entry before None",
                    hook_id="test",
                )

                # Yield None entry - should be filtered
                yield None

                await asyncio.sleep(0.01)

            with patch.object(controller.log_parser, "monitor_log_directory", return_value=mock_monitor_with_none()):
                # Start WebSocket handling
                websocket_task = asyncio.create_task(controller.handle_websocket(mock_ws))

                # Let it run briefly
                await asyncio.sleep(0.1)

                # Cancel the task
                websocket_task.cancel()
                try:
                    await websocket_task
                except asyncio.CancelledError:
                    pass

                # Should have accepted connection
                mock_ws.accept.assert_called_once()

                # Verify only valid entries were sent (None entry causes exception and closes WebSocket)
                # The exception handler in handle_websocket catches errors when trying to call
                # .to_dict() on None, which closes the WebSocket with code 1011
                sent_messages = [
                    call[0][0] if call[0] else call.kwargs.get("data") for call in mock_ws.send_json.call_args_list
                ]

                # Verify all sent messages are valid dicts with required fields
                for msg in sent_messages:
                    assert isinstance(msg, dict), f"Expected dict, got {type(msg)}"
                    assert "timestamp" in msg
                    assert "level" in msg
                    assert "message" in msg
                    assert msg["timestamp"] is not None

                # WebSocket should be closed after encountering None entry
                mock_ws.close.assert_called_once()
                close_call_kwargs = mock_ws.close.call_args.kwargs
                assert close_call_kwargs.get("code") == 1011

    @pytest.mark.asyncio
    async def test_websocket_filters_invalid_types(self):
        """Test that WebSocket filters out invalid types (strings) gracefully."""

        mock_logger = Mock()
        controller = LogViewerController(logger=mock_logger)

        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_json = AsyncMock()

        with patch.object(controller, "_get_log_directory") as mock_get_dir:
            mock_dir = Mock()
            mock_dir.exists.return_value = True
            mock_get_dir.return_value = mock_dir

            async def mock_monitor_with_string():
                # Yield valid entry
                yield LogEntry(
                    timestamp=datetime.datetime.now(),
                    level="INFO",
                    logger_name="test",
                    message="Valid entry before string",
                    hook_id="test",
                )

                # Yield string entry - should be filtered
                yield "invalid_entry"

                await asyncio.sleep(0.01)

            with patch.object(controller.log_parser, "monitor_log_directory", return_value=mock_monitor_with_string()):
                # Start WebSocket handling
                websocket_task = asyncio.create_task(controller.handle_websocket(mock_ws))

                # Let it run briefly
                await asyncio.sleep(0.1)

                # Cancel the task
                websocket_task.cancel()
                try:
                    await websocket_task
                except asyncio.CancelledError:
                    pass

                # Should have accepted connection
                mock_ws.accept.assert_called_once()

                # Verify only valid LogEntry.to_dict() output was sent
                sent_messages = [
                    call[0][0] if call[0] else call.kwargs.get("data") for call in mock_ws.send_json.call_args_list
                ]

                for msg in sent_messages:
                    # Should be dict with valid structure
                    assert isinstance(msg, dict)
                    assert msg.get("timestamp") is not None
                    # Should not contain invalid string entries
                    assert msg.get("message") != "invalid_entry"

                # WebSocket should be closed after encountering invalid type
                mock_ws.close.assert_called_once()
                close_call_kwargs = mock_ws.close.call_args.kwargs
                assert close_call_kwargs.get("code") == 1011
                assert "Internal server error" in close_call_kwargs.get("reason", "")

    @pytest.mark.asyncio
    async def test_websocket_closes_on_processing_error(self):
        """Test that WebSocket closes gracefully on corrupted data processing errors."""

        mock_logger = Mock()
        controller = LogViewerController(logger=mock_logger)

        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_json = AsyncMock()

        with patch.object(controller, "_get_log_directory") as mock_get_dir:
            mock_dir = Mock()
            mock_dir.exists.return_value = True
            mock_get_dir.return_value = mock_dir

            async def mock_monitor_with_invalid_timestamp():
                # Yield valid entry first
                yield LogEntry(
                    timestamp=datetime.datetime.now(),
                    level="INFO",
                    logger_name="test",
                    message="Valid entry",
                    hook_id="test",
                )

                # Yield LogEntry with None timestamp - should cause processing error
                yield LogEntry(
                    timestamp=None,  # Invalid timestamp
                    level="INFO",
                    logger_name="test",
                    message="Invalid timestamp entry",
                    hook_id="test",
                )

                await asyncio.sleep(0.01)

            with patch.object(
                controller.log_parser, "monitor_log_directory", return_value=mock_monitor_with_invalid_timestamp()
            ):
                # Start WebSocket handling
                websocket_task = asyncio.create_task(controller.handle_websocket(mock_ws))

                # Let it run briefly
                await asyncio.sleep(0.1)

                # Cancel the task
                websocket_task.cancel()
                try:
                    await websocket_task
                except asyncio.CancelledError:
                    pass

                # Should have accepted connection
                mock_ws.accept.assert_called_once()

                # CRITICAL: Verify exactly 1 send_json call was made (only the valid entry)
                # The test yields: 1 valid entry + LogEntry with None timestamp
                # The invalid timestamp entry causes an exception when trying to call
                # .isoformat() on None timestamp, which is caught by the exception handler
                # in handle_websocket (lines 426-427 in log_viewer.py)
                # Only the valid entry should result in send_json call
                assert mock_ws.send_json.call_count == 1, (
                    f"Expected exactly 1 send_json call for the valid entry, got {mock_ws.send_json.call_count}"
                )

                # Verify only valid payloads were sent (dict/serializable)
                for call in mock_ws.send_json.call_args_list:
                    payload = call[0][0] if call[0] else call.kwargs.get("data")
                    # Assert payload is a dict (valid JSON-serializable)
                    assert isinstance(payload, dict), f"Expected dict payload, got {type(payload)}: {payload}"

                    # Verify the payload has required fields and valid timestamp
                    assert "timestamp" in payload, "Valid payload must contain 'timestamp' field"
                    assert "level" in payload, "Valid payload must contain 'level' field"
                    assert "message" in payload, "Valid payload must contain 'message' field"

                    # Verify timestamp is valid ISO format (not None)
                    assert payload["timestamp"] is not None, "Timestamp must not be None in sent payload"
                    # Verify it's parseable as ISO datetime
                    datetime.datetime.fromisoformat(payload["timestamp"])

                    # Verify it has the correct message
                    assert payload["message"] == "Valid entry"

                # Verify WebSocket was closed gracefully after encountering corrupted data
                # When corrupted entries (LogEntry with None timestamp) are processed,
                # they cause exceptions (AttributeError when calling .isoformat() on None)
                # which are caught by the exception handler in handle_websocket (lines 426-431 in log_viewer.py)
                # The handler closes the WebSocket with code 1011 (Internal server error)
                # This is correct behavior - the system handles errors gracefully by closing the connection
                mock_ws.close.assert_called_once()
                # Verify it was closed with appropriate error code
                close_call_kwargs = mock_ws.close.call_args.kwargs
                assert close_call_kwargs.get("code") == 1011, (
                    "WebSocket should be closed with code 1011 (Internal server error)"
                )
                assert "Internal server error" in close_call_kwargs.get("reason", ""), (
                    "Close reason should indicate internal server error"
                )


class TestAPIEndpointEdgeCases:
    """Test edge cases in API endpoint functionality."""

    def test_api_with_malformed_parameters(self):
        """Test API behavior with malformed parameters."""

        mock_logger = Mock()
        controller = LogViewerController(logger=mock_logger)

        # Test malformed parameters
        malformed_params = [
            {"limit": "not_a_number"},
            {"offset": -1},
            {"pr_number": "not_a_number"},
            {"start_time": "invalid_date"},
            {"end_time": "invalid_date"},
            {"hook_id": None},  # None value
            {"repository": ""},  # Empty string
        ]

        for params in malformed_params:
            try:
                # This would normally be called through FastAPI with parameter validation
                # Here we test the controller's parameter handling
                if "limit" in params and not isinstance(params["limit"], int):
                    with pytest.raises((ValueError, TypeError, HTTPException)):
                        controller.get_log_entries(**params)
                else:
                    # For other malformed params, should handle gracefully
                    result = controller.get_log_entries(**params)
                    assert isinstance(result, dict)
            except Exception as e:
                # Some malformed parameters should raise exceptions
                assert isinstance(e, (ValueError, TypeError, HTTPException))

    def test_api_with_extremely_large_responses(self):
        """Test API behavior with extremely large response datasets."""

        mock_logger = Mock()
        controller = LogViewerController(logger=mock_logger)

        # Mock very large dataset
        large_entries = []
        for i in range(1000):  # 1k entries (only 1k are streamed anyway)
            entry = LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 0, i),
                level="INFO",
                logger_name="test",
                message=f"Large dataset entry {i}",
                hook_id=f"hook-{i}",
            )
            large_entries.append(entry)

        with patch.object(controller, "_stream_log_entries", return_value=iter(large_entries[:1000])):
            # Test with default limit - the controller will process available entries and apply pagination
            result = controller.get_log_entries()
            assert "entries" in result
            assert "entries_processed" in result
            assert len(result["entries"]) <= 100  # Default limit applied

            # Test with large limit to get more entries
            result_large = controller.get_log_entries(limit=1000)
            assert len(result_large["entries"]) <= 1000  # Should not exceed available data

            # Test export with large dataset (should handle size limits)
            try:
                export_result = controller.export_logs(format_type="json")
                # Should either succeed or raise appropriate error for large datasets
                assert hasattr(export_result, "status_code") or isinstance(export_result, str)
            except HTTPException as e:
                # Should raise 413 for too large datasets
                assert e.status_code == 413

    def test_pr_flow_analysis_edge_cases(self):
        """Test PR flow analysis with edge case data."""

        mock_logger = Mock()
        controller = LogViewerController(logger=mock_logger)

        # Test with empty entries
        empty_result = controller._analyze_pr_flow([], "test-id")
        assert empty_result["success"] is False
        assert "error" in empty_result
        assert empty_result["stages"] == []

        # Test with entries without proper sequencing but with recognizable patterns
        unordered_entries = [
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 5),
                level="INFO",
                logger_name="test",
                message="Processing complete for PR",
                hook_id="test",
            ),
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 1),
                level="INFO",
                logger_name="test",
                message="Processing webhook for repository",
                hook_id="test",
            ),
        ]

        unordered_result = controller._analyze_pr_flow(unordered_entries, "test-id")
        assert "stages" in unordered_result
        # The method should find patterns and create stages even if entries are unordered
        assert len(unordered_result["stages"]) >= 1  # Should find at least one stage

        # Test with entries containing errors
        error_entries = [
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 1),
                level="INFO",
                logger_name="test",
                message="Starting process",
                hook_id="test",
            ),
            LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 2),
                level="ERROR",
                logger_name="test",
                message="Process failed",
                hook_id="test",
            ),
        ]

        error_result = controller._analyze_pr_flow(error_entries, "test-id")
        assert error_result["success"] is False
        assert "error" in error_result


class TestConcurrentUserScenarios:
    """Test scenarios with multiple concurrent users."""

    @pytest.mark.asyncio
    async def test_multiple_users_different_filters(self):
        """Test multiple users applying different filters simultaneously."""

        # Generate shared dataset
        entries = []
        for i in range(10000):
            entry = LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 0, i),
                level=["INFO", "DEBUG", "ERROR"][i % 3],
                logger_name="test",
                message=f"Message {i}",
                hook_id=f"hook-{i % 100}",
                repository=f"repo-{i % 10}",
                pr_number=i if i % 5 == 0 else None,
            )
            entries.append(entry)

        mock_logger = Mock()

        # Simulate multiple users with different controllers
        users = []
        for _ in range(5):
            controller = LogViewerController(logger=mock_logger)
            users.append(controller)

        # Different filter scenarios for each user
        user_filters = [
            {"repository": "repo-1", "level": "INFO"},
            {"hook_id": "hook-25", "pr_number": 25},
            {"search": "Message", "limit": 100},
            {"level": "ERROR", "offset": 50},
            {"repository": "repo-2", "search": "500"},
        ]

        def user_request(controller, filters):
            """Simulate a user making a request."""
            with patch.object(controller, "_stream_log_entries", return_value=iter(entries)):
                return controller.get_log_entries(**filters)

        # Execute concurrent requests
        tasks = []
        for controller, filters in zip(users, user_filters, strict=True):
            task = asyncio.create_task(asyncio.to_thread(user_request, controller, filters))
            tasks.append(task)

        results = await asyncio.gather(*tasks)

        # All requests should succeed
        assert len(results) == 5
        assert all("entries" in result for result in results)
        assert all("entries_processed" in result for result in results)

        # Results should be different based on filters
        entry_counts = [len(result["entries"]) for result in results]
        assert len(set(entry_counts)) > 1  # Should have different counts

    @pytest.mark.asyncio
    async def test_concurrent_websocket_connections_with_filters(self):
        """Test multiple WebSocket connections with different filter requirements."""

        mock_logger = Mock()

        # Create multiple controller instances for different users
        controllers = [LogViewerController(logger=mock_logger) for _ in range(3)]

        # Mock WebSocket connections for each user
        mock_websockets = []
        for _ in range(3):
            mock_ws = AsyncMock()
            mock_ws.accept = AsyncMock()
            mock_ws.send_json = AsyncMock()
            mock_websockets.append(mock_ws)

        # Mock different log monitoring scenarios
        for controller in controllers:
            with patch.object(controller, "_get_log_directory") as mock_get_dir:
                mock_dir = Mock()
                mock_dir.exists.return_value = True
                mock_get_dir.return_value = mock_dir

        async def mock_monitor(user_id):
            """Different monitoring behavior for each user."""
            for i in range(3):
                yield LogEntry(
                    timestamp=datetime.datetime.now(),
                    level="INFO",
                    logger_name="test",
                    message=f"User {user_id} message {i}",
                    hook_id=f"user-{user_id}-hook-{i}",
                )
                await asyncio.sleep(0.01)

        # Start WebSocket connections for all users
        tasks = []
        for i, (controller, ws) in enumerate(zip(controllers, mock_websockets, strict=True)):
            with patch.object(controller.log_parser, "monitor_log_directory", return_value=mock_monitor(i)):
                task = asyncio.create_task(controller.handle_websocket(ws))
                tasks.append(task)

        # Let them run briefly
        await asyncio.sleep(0.1)

        # Cancel all tasks
        for task in tasks:
            task.cancel()

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All connections should have been accepted
        for ws in mock_websockets:
            ws.accept.assert_called_once()

        # Should handle multiple concurrent connections without issues
        assert len(results) == 3
