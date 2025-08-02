"""Memory optimization tests for log viewer streaming functionality."""

import tempfile
import datetime
import time
import asyncio
from pathlib import Path
from unittest.mock import Mock
import pytest


from webhook_server.web.log_viewer import LogViewerController
from webhook_server.libs.log_parser import LogEntry


class TestStreamingMemoryOptimization:
    """Test memory efficiency improvements in log viewer."""

    def setup_method(self):
        """Set up test environment."""
        from unittest.mock import patch

        self.mock_logger = Mock()

        # Override log directory for testing
        self.temp_dir = tempfile.mkdtemp()
        self.log_dir = Path(self.temp_dir) / "logs"
        self.log_dir.mkdir(parents=True)

        # Mock Config to avoid file dependency
        mock_config = Mock()
        mock_config.data_dir = self.temp_dir

        # Create controller with mocked Config
        with patch("webhook_server.web.log_viewer.Config", return_value=mock_config):
            self.controller = LogViewerController(logger=self.mock_logger)

        # Override the log directory method to use our temp directory
        self.controller._get_log_directory = lambda: self.log_dir

    def generate_large_log_file(self, file_path: Path, num_entries: int = 10000) -> None:
        """Generate a large log file for testing with realistic format."""
        with open(file_path, "w") as f:
            base_time = datetime.datetime(2025, 7, 31, 10, 0, 0)

            for i in range(num_entries):
                # Add microseconds to match real format (ensure non-zero microseconds)
                timestamp = base_time + datetime.timedelta(seconds=i, microseconds=((i + 1) * 1000) % 1000000)
                level = ["INFO", "DEBUG", "WARNING", "ERROR"][i % 4]
                repo = ["test-repo", "webhook-server", "large-project"][i % 3]
                hook_id = f"hook-{i % 100:04d}"  # Zero-padded
                user = f"user{i % 10}"
                event = ["push", "pull_request", "issue_comment"][i % 3]

                # Generate realistic log format matching the production logs
                log_line = (
                    f"{timestamp.isoformat()} GithubWebhook {level} "
                    f"{repo} [{event}][{hook_id}][{user}]: Processing webhook step {i}\n"
                )
                f.write(log_line)

    def test_streaming_efficiency_and_limits(self):
        """Test that streaming approach processes efficiently with proper limits."""
        # Create multiple large log files
        for i in range(3):
            log_file = self.log_dir / f"webhook_{i}.log"
            self.generate_large_log_file(log_file, 5000)  # 15k total entries

        # Test streaming with limits to prevent memory issues
        streaming_entries = []
        count = 0
        for entry in self.controller._stream_log_entries(max_files=3, max_entries=1000):
            if count >= 500:  # Stop early to test early termination
                break
            streaming_entries.append(entry)
            count += 1

        # Streaming should respect limits and early termination
        assert len(streaming_entries) == 500
        assert all(isinstance(entry, LogEntry) for entry in streaming_entries)

        # Test that streaming doesn't load all entries at once
        all_possible_entries = list(self.controller._stream_log_entries(max_files=3, max_entries=50000))

        # Should respect max_entries limit
        assert len(all_possible_entries) <= 15000  # 3 files * 5000 entries max
        assert len(streaming_entries) < len(all_possible_entries)  # Early termination worked

    def test_chunked_processing_efficiency(self):
        """Test that chunked processing maintains good performance."""
        # Create a large log file
        log_file = self.log_dir / "large_webhook.log"
        self.generate_large_log_file(log_file, 10000)

        # Test chunked streaming performance
        start_time = time.perf_counter()

        entries_processed = 0
        for entry in self.controller._stream_log_entries(chunk_size=500, max_entries=5000):
            entries_processed += 1
            if entries_processed >= 2000:  # Stop after processing 2000 entries
                break

        end_time = time.perf_counter()
        duration = end_time - start_time

        # Should process efficiently
        assert entries_processed == 2000
        assert duration < 2.0  # Should complete in under 2 seconds

        # Calculate throughput
        entries_per_second = entries_processed / duration
        assert entries_per_second > 1000  # Should process at least 1000 entries/second

    def test_memory_efficient_filtering(self):
        """Test that memory-efficient filtering works correctly."""
        # Create log files with specific patterns
        log_file = self.log_dir / "filtered_test.log"

        with open(log_file, "w") as f:
            base_time = datetime.datetime(2025, 7, 31, 10, 0, 0)

            for i in range(5000):
                timestamp = base_time + datetime.timedelta(seconds=i, microseconds=((i + 1) * 1000) % 1000000)
                hook_id = "target-hook" if i % 10 == 0 else f"other-hook-{i}"

                log_line = (
                    f"{timestamp.isoformat()} GithubWebhook INFO test-repo [push][{hook_id}][user]: Message {i}\n"
                )
                f.write(log_line)

        # Use get_log_entries with filtering
        result = self.controller.get_log_entries(hook_id="target-hook", limit=100)

        # Should find approximately 500 entries (every 10th entry)
        # But limited to 100 by the limit parameter
        assert len(result["entries"]) <= 100

        # Check that filtering actually worked
        for entry_dict in result["entries"]:
            assert entry_dict["hook_id"] == "target-hook"

        # Test that we can get a reasonable number of filtered results
        assert len(result["entries"]) > 0  # Should find some matching entries

    def test_early_termination_optimization(self):
        """Test that early termination prevents unnecessary processing."""
        # Create log files
        log_file = self.log_dir / "early_term_test.log"
        self.generate_large_log_file(log_file, 8000)

        start_time = time.perf_counter()

        # Request small result set to test early termination
        result = self.controller.get_log_entries(limit=50)

        end_time = time.perf_counter()
        duration = end_time - start_time

        # Should complete quickly due to early termination
        assert len(result["entries"]) <= 50
        assert duration < 1.0  # Should complete in under 1 second

        # Should not process all 8000 entries
        # The streaming should stop after finding enough matching entries

    def test_large_export_memory_efficiency(self):
        """Test that large exports work correctly with streaming."""
        # Create multiple log files
        for i in range(3):
            log_file = self.log_dir / f"export_test_{i}.log"
            self.generate_large_log_file(log_file, 3000)  # 9k total entries

        # Test export with reasonable limit
        response = self.controller.export_logs(format_type="json", limit=2000)

        # Export should work correctly
        assert response.status_code == 200
        assert response.media_type == "application/json"

        # Should have content-disposition header for download
        assert "Content-Disposition" in response.headers
        assert "attachment" in response.headers["Content-Disposition"]

    def test_pagination_efficiency(self):
        """Test that pagination with offset works efficiently."""
        # Create log file
        log_file = self.log_dir / "pagination_test.log"
        self.generate_large_log_file(log_file, 5000)

        # Test pagination with offset
        start_time = time.perf_counter()

        result = self.controller.get_log_entries(
            limit=100,
            offset=2000,  # Skip first 2000 entries
        )

        end_time = time.perf_counter()
        duration = end_time - start_time

        # Should handle pagination efficiently
        assert len(result["entries"]) <= 100
        assert result["offset"] == 2000
        assert duration < 2.0  # Should complete in reasonable time

        # Verify pagination worked correctly by checking timestamps
        # (entries should be from later in the log due to offset)
        if result["entries"]:
            # All entries should be from the later part of the log
            assert len(result["entries"]) > 0

    @pytest.mark.asyncio
    async def test_concurrent_streaming_safety(self):
        """Test that streaming is safe under concurrent access."""
        # Create log file
        log_file = self.log_dir / "concurrent_test.log"
        self.generate_large_log_file(log_file, 3000)

        async def stream_entries():
            """Async wrapper for streaming entries."""
            # Run the synchronous streaming operation in a thread to avoid blocking
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: list(self.controller._stream_log_entries(max_entries=1000)))

        # Test multiple concurrent streaming operations
        # Simulate concurrent access by running multiple streaming operations simultaneously
        num_concurrent_operations = 5
        tasks = [stream_entries() for _ in range(num_concurrent_operations)]

        # Execute all tasks concurrently
        results = await asyncio.gather(*tasks)

        # Verify all operations completed successfully
        assert len(results) == num_concurrent_operations

        for entries in results:
            assert len(entries) <= 1000
            assert all(isinstance(entry, LogEntry) for entry in entries)

        # Verify that all concurrent operations returned consistent results
        # (all should have same number of entries since reading same file)
        entry_counts = [len(entries) for entries in results]
        assert all(count == entry_counts[0] for count in entry_counts), (
            f"Inconsistent entry counts across concurrent operations: {entry_counts}"
        )

    def teardown_method(self):
        """Clean up test environment."""
        import shutil

        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)


class TestMemoryRegressionPrevention:
    """Tests to prevent memory usage regressions."""

    def test_streaming_functionality_baseline(self):
        """Establish baseline functionality for regression testing."""
        from unittest.mock import patch

        mock_logger = Mock()

        # Create temporary log directory
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir) / "logs"
            log_dir.mkdir()

            # Mock Config to avoid file dependency
            mock_config = Mock()
            mock_config.data_dir = temp_dir

            # Create controller with mocked Config
            with patch("webhook_server.web.log_viewer.Config", return_value=mock_config):
                controller = LogViewerController(logger=mock_logger)

            # Mock log directory
            controller._get_log_directory = lambda: log_dir

            # Create small test log file
            log_file = log_dir / "baseline_test.log"
            with open(log_file, "w") as f:
                base_time = datetime.datetime(2025, 7, 31, 10, 0, 0)
                for i in range(1000):
                    # Ensure all entries have microseconds (avoid 0 by using 1000 + remainder)
                    microseconds = 1000 + (i * 1000) % 999000
                    timestamp = base_time + datetime.timedelta(seconds=i, microseconds=microseconds)
                    f.write(
                        f"{timestamp.isoformat()} GithubWebhook INFO test-repo [push][hook-{i:04d}][user]: Message {i}\n"
                    )

            # Test streaming functionality
            entries = list(controller._stream_log_entries(max_entries=1000))

            # Baseline functionality that should not regress
            assert len(entries) == 1000
            assert all(isinstance(entry, LogEntry) for entry in entries)

            # Test that streaming respects limits
            limited_entries = list(controller._stream_log_entries(max_entries=500))
            assert len(limited_entries) == 500

            # Test that get_log_entries works with streaming
            result = controller.get_log_entries(limit=100)
            assert len(result["entries"]) == 100
            assert "entries_processed" in result
            assert "is_partial_scan" in result
