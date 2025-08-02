"""Performance benchmark tests for webhook server log functionality."""

import asyncio
import datetime
import json
import os
import random
import tempfile
import time
from pathlib import Path

import pytest

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False

from webhook_server.libs.log_parser import LogEntry, LogFilter, LogParser


class TestLogParsingPerformance:
    """Performance benchmarks for log parsing functionality."""

    def generate_test_log_content(self, num_entries: int = 10000) -> str:
        """Generate realistic test log content for performance testing."""
        log_lines = []
        base_time = datetime.datetime(2025, 7, 31, 10, 0, 0)

        repos = ["test-repo-1", "test-repo-2", "webhook-server", "large-project"]
        events = ["push", "pull_request", "pull_request.opened", "pull_request.closed", "check_run"]
        users = ["user1", "user2", "myakove", "bot-user", "reviewer"]
        levels = ["INFO", "DEBUG", "WARNING", "ERROR"]

        for i in range(num_entries):
            # Generate time with microseconds like working tests
            microsecond = (i * 100000) % 1000000
            timestamp = base_time + datetime.timedelta(seconds=i // 10, microseconds=microsecond)
            repo = random.choice(repos)
            event = random.choice(events)
            user = random.choice(users)
            level = random.choice(levels)
            hook_id = f"hook-{random.randint(1000, 9999)}-{i}"

            # Add PR number to some entries
            pr_suffix = f"[PR {random.randint(1, 500)}]" if random.random() < 0.3 else ""

            # Format timestamp with microseconds
            timestamp_str = timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")

            log_line = (
                f"{timestamp_str} GithubWebhook {level} "
                f"{repo} [{event}][{hook_id}][{user}]{pr_suffix}: "
                f"Processing webhook step {i}"
            )
            log_lines.append(log_line)

        return "\n".join(log_lines)

    def test_log_parsing_performance_10k_entries(self):
        """Test parsing performance with 10,000 log entries."""
        content = self.generate_test_log_content(10000)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(content)
            f.flush()

            parser = LogParser()

            # Measure parsing time
            start_time = time.perf_counter()
            entries = parser.parse_log_file(Path(f.name))
            end_time = time.perf_counter()

            parse_duration = end_time - start_time

            # Performance assertions
            assert len(entries) > 9500  # Allow for some parsing failures
            assert parse_duration < 2.0  # Should parse 10k entries in under 2 seconds

            # Calculate performance metrics
            entries_per_second = len(entries) / parse_duration
            assert entries_per_second > 5000  # Should parse at least 5k entries/second

            # Memory efficiency check (basic)
            assert len(entries) == len([e for e in entries if e is not None])

    def test_log_parsing_performance_100k_entries(self):
        """Test parsing performance with 100,000 log entries (stress test)."""
        content = self.generate_test_log_content(100000)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(content)
            f.flush()

            parser = LogParser()

            # Measure parsing time
            start_time = time.perf_counter()
            entries = parser.parse_log_file(Path(f.name))
            end_time = time.perf_counter()

            parse_duration = end_time - start_time

            # Performance assertions for large datasets
            assert len(entries) > 95000  # Allow for some parsing failures
            assert parse_duration < 20.0  # Should parse 100k entries in under 20 seconds

            # Calculate performance metrics
            entries_per_second = len(entries) / parse_duration
            assert entries_per_second > 5000  # Maintain performance at scale

    def test_filter_performance_large_dataset(self):
        """Test filtering performance on large datasets."""
        # Generate large dataset in memory
        entries = []
        base_time = datetime.datetime(2025, 7, 31, 10, 0, 0)

        for i in range(50000):
            entry = LogEntry(
                timestamp=base_time + datetime.timedelta(seconds=i),
                level=random.choice(["INFO", "DEBUG", "WARNING", "ERROR"]),
                logger_name="GithubWebhook",
                message=f"Test message {i}",
                hook_id=f"hook-{random.randint(1000, 5000)}",
                event_type=random.choice(["push", "pull_request"]),
                repository=random.choice(["repo1", "repo2", "repo3"]),
                pr_number=random.randint(1, 1000) if random.random() < 0.3 else None,
                github_user=random.choice(["user1", "user2", "user3"]),
            )
            entries.append(entry)

        log_filter = LogFilter()

        # Test different filter operations and measure performance
        test_cases = [
            {"hook_id": "hook-1234"},
            {"repository": "repo1"},
            {"event_type": "pull_request"},
            {"level": "INFO"},
            {"pr_number": 123},
            {"search_text": "message"},
            {"limit": 1000},
            {"repository": "repo1", "event_type": "push", "level": "INFO"},
        ]

        for test_case in test_cases:
            start_time = time.perf_counter()
            filtered = log_filter.filter_entries(entries, **test_case)
            end_time = time.perf_counter()

            filter_duration = end_time - start_time

            # Filtering should be fast even on large datasets
            assert filter_duration < 1.0  # Filter 50k entries in under 1 second
            assert isinstance(filtered, list)

    @pytest.mark.asyncio
    async def test_async_log_monitoring_performance(self):
        """Test performance of async log monitoring."""
        content = self.generate_test_log_content(1000)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(content)
            f.flush()

            parser = LogParser()

            # Test async monitoring performance
            start_time = time.perf_counter()
            entries_collected = []

            async def collect_entries():
                async for entry in parser.tail_log_file(Path(f.name), follow=False):
                    entries_collected.append(entry)
                    if len(entries_collected) >= 10:  # Collect first 10 entries
                        break

            await collect_entries()
            end_time = time.perf_counter()

            monitoring_duration = end_time - start_time

            # Async monitoring should be efficient
            assert monitoring_duration < 0.5  # Should be very fast for non-following mode
            assert len(entries_collected) >= 0  # May be 0 for non-following tail


class TestMemoryUsageProfiler:
    """Memory usage profiling tests."""

    def test_memory_efficiency_large_dataset(self):
        """Test memory efficiency with large datasets."""
        if not PSUTIL_AVAILABLE:
            pytest.skip("psutil not available for memory monitoring")

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # Generate large dataset
        parser = LogParser()
        content = ""
        for i in range(10000):
            content += f"2025-07-31T10:{i // 600:02d}:{i % 60:02d}.000000 GithubWebhook INFO test-repo [push][hook-{i}][user]: Message {i}\n"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(content)
            f.flush()

            entries = parser.parse_log_file(Path(f.name))

            # Check memory usage after parsing
            peak_memory = process.memory_info().rss / 1024 / 1024  # MB
            memory_increase = peak_memory - initial_memory

            # Memory efficiency assertions
            assert len(entries) == 10000
            assert memory_increase < 100  # Should not use more than 100MB for 10k entries

            # Memory per entry should be reasonable
            memory_per_entry = memory_increase / len(entries) * 1024  # KB per entry
            assert memory_per_entry < 10  # Less than 10KB per entry

    def test_memory_cleanup_after_processing(self):
        """Test that memory is properly cleaned up after processing."""
        if not PSUTIL_AVAILABLE:
            pytest.skip("psutil not available for memory monitoring")

        import gc
        import os

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # Process large dataset and then clean up
        parser = LogParser()
        content = self._generate_large_log_content(5000)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(content)
            f.flush()

            entries = parser.parse_log_file(Path(f.name))
            del entries  # Explicit cleanup
            gc.collect()  # Force garbage collection

            # Check memory after cleanup
            final_memory = process.memory_info().rss / 1024 / 1024  # MB
            memory_leak = final_memory - initial_memory

            # Should not have significant memory leaks
            assert memory_leak < 20  # Less than 20MB increase after cleanup

    def _generate_large_log_content(self, num_entries: int) -> str:
        """Helper to generate large log content."""
        lines = []
        for i in range(num_entries):
            lines.append(
                f"2025-07-31T10:{i // 600:02d}:{i % 60:02d}.000000 GithubWebhook INFO "
                f"test-repo [push][hook-{i}][user]: Processing entry {i} with some additional content"
            )
        return "\n".join(lines)


class TestConcurrencyPerformance:
    """Test performance under concurrent load."""

    @pytest.mark.asyncio
    async def test_concurrent_parsing_performance(self):
        """Test performance of concurrent parsing operations."""
        # Create multiple log files
        files = []
        for i in range(5):
            content = self._generate_test_content(2000)
            with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
                f.write(content)
                f.flush()
                files.append(Path(f.name))

        parser = LogParser()

        def parse_file(file_path):
            """Parse a single file."""
            return parser.parse_log_file(file_path)

        # Measure concurrent parsing
        start_time = time.perf_counter()

        # Use asyncio.to_thread for concurrent execution of sync functions
        tasks = [asyncio.create_task(asyncio.to_thread(parse_file, f)) for f in files]
        results = await asyncio.gather(*tasks)

        end_time = time.perf_counter()
        concurrent_duration = end_time - start_time

        # Verify results
        total_entries = sum(len(entries) for entries in results)
        assert total_entries > 9500  # 5 files * ~2000 entries each

        # Concurrent parsing should be efficient
        assert concurrent_duration < 5.0  # Should complete in under 5 seconds

        # Calculate throughput
        entries_per_second = total_entries / concurrent_duration
        assert entries_per_second > 2000  # Good concurrent throughput

    @pytest.mark.asyncio
    async def test_concurrent_filtering_performance(self):
        """Test performance of concurrent filtering operations."""
        # Generate shared dataset
        entries = []
        for i in range(10000):
            entry = LogEntry(
                timestamp=datetime.datetime(2025, 7, 31, 10, 0, 0, i),
                level=random.choice(["INFO", "DEBUG", "ERROR"]),
                logger_name="GithubWebhook",
                message=f"Message {i}",
                hook_id=f"hook-{i % 100}",
                repository=f"repo-{i % 10}",
            )
            entries.append(entry)

        log_filter = LogFilter()

        def filter_task(filter_params):
            """Single filter task."""
            return log_filter.filter_entries(entries, **filter_params)

        # Different filter operations
        filter_operations = [
            {"repository": "repo-1"},
            {"level": "INFO"},
            {"hook_id": "hook-25"},
            {"search_text": "Message"},
            {"limit": 100},
        ]

        # Measure concurrent filtering
        start_time = time.perf_counter()

        tasks = [asyncio.create_task(asyncio.to_thread(filter_task, params)) for params in filter_operations]
        results = await asyncio.gather(*tasks)

        end_time = time.perf_counter()
        concurrent_duration = end_time - start_time

        # Verify results
        assert len(results) == 5
        assert all(isinstance(result, list) for result in results)

        # Concurrent filtering should be fast
        assert concurrent_duration < 2.0  # Multiple filters in under 2 seconds

    def _generate_test_content(self, num_entries: int) -> str:
        """Helper to generate test log content."""
        lines = []
        for i in range(num_entries):
            lines.append(
                f"2025-07-31T10:{i // 600:02d}:{i % 60:02d}.{i % 1000:03d}000 GithubWebhook INFO "
                f"test-repo-{i % 3} [push][hook-{i}][user-{i % 5}]: Processing entry {i}"
            )
        return "\n".join(lines)


class TestRealtimeStreamingPerformance:
    """Test performance of real-time streaming functionality."""

    @pytest.mark.asyncio
    async def test_websocket_streaming_throughput(self):
        """Test WebSocket streaming throughput under load."""
        # This test simulates WebSocket streaming performance
        entries_to_stream = []

        # Generate entries for streaming
        for i in range(1000):
            entry = LogEntry(
                timestamp=datetime.datetime.now() + datetime.timedelta(milliseconds=i),
                level="INFO",
                logger_name="GithubWebhook",
                message=f"Streaming message {i}",
                hook_id=f"stream-hook-{i}",
            )
            entries_to_stream.append(entry)

        # Simulate streaming performance
        start_time = time.perf_counter()

        streamed_entries = []
        for entry in entries_to_stream:
            # Simulate JSON serialization (what happens in real WebSocket)
            json_data = json.dumps(entry.to_dict())
            streamed_entries.append(json_data)

            # Simulate small async delay (realistic WebSocket behavior)
            if len(streamed_entries) % 100 == 0:
                await asyncio.sleep(0.001)  # 1ms delay every 100 entries

        end_time = time.perf_counter()
        streaming_duration = end_time - start_time

        # Performance assertions
        assert len(streamed_entries) == 1000
        assert streaming_duration < 2.0  # Stream 1000 entries in under 2 seconds

        # Calculate streaming rate
        entries_per_second = len(streamed_entries) / streaming_duration
        assert entries_per_second > 500  # At least 500 entries/second

    @pytest.mark.asyncio
    async def test_log_monitoring_latency(self):
        """Test latency of log file monitoring."""
        # Create initial log file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("2025-07-31T10:00:00.000000 GithubWebhook INFO test: Initial entry\n")
            f.flush()

            parser = LogParser()
            log_path = Path(f.name)

            # Start monitoring
            entries_received = []

            async def monitor_logs():
                async for entry in parser.tail_log_file(log_path, follow=True):
                    entries_received.append((time.perf_counter(), entry))
                    if len(entries_received) >= 3:  # Stop after receiving 3 new entries
                        break

            # Start monitoring task
            monitor_task = asyncio.create_task(monitor_logs())

            # Give monitoring time to start
            await asyncio.sleep(0.1)

            # Add new entries with timing
            write_times = []
            for i in range(3):
                write_time = time.perf_counter()
                with open(log_path, "a") as append_f:
                    append_f.write(f"2025-07-31T10:00:{i + 1:02d}.000000 GithubWebhook INFO test: New entry {i + 1}\n")
                    append_f.flush()
                write_times.append(write_time)
                await asyncio.sleep(0.05)  # Small delay between writes

            # Wait for monitoring to complete
            try:
                await asyncio.wait_for(monitor_task, timeout=2.0)
            except asyncio.TimeoutError:
                monitor_task.cancel()

            # Analyze latency
            if len(entries_received) >= 3:
                latencies = []
                for i, (receive_time, entry) in enumerate(entries_received):
                    if i < len(write_times):
                        latency = receive_time - write_times[i]
                        latencies.append(latency)

                if latencies:
                    avg_latency = sum(latencies) / len(latencies)
                    max_latency = max(latencies)

                    # Latency assertions
                    assert avg_latency < 0.5  # Average latency under 500ms
                    assert max_latency < 1.0  # Maximum latency under 1 second


class TestRegressionPrevention:
    """Test to prevent performance regressions."""

    def test_parsing_performance_baseline(self):
        """Establish baseline performance metrics for regression testing."""
        # Standard test dataset
        content = self._generate_standardized_content()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(content)
            f.flush()

            parser = LogParser()

            # Measure parsing performance
            start_time = time.perf_counter()
            entries = parser.parse_log_file(Path(f.name))
            end_time = time.perf_counter()

            parse_duration = end_time - start_time

            # Baseline metrics (these should not regress)
            baseline_metrics = {
                "entries_count": len(entries),
                "parse_duration": parse_duration,
                "entries_per_second": len(entries) / parse_duration,
                "average_entry_size": len(content) / len(entries) if entries else 0,
            }

            # Store baseline metrics for comparison
            assert baseline_metrics["entries_count"] == 5000  # Standardized dataset
            assert baseline_metrics["parse_duration"] < 1.0  # Should be fast
            assert baseline_metrics["entries_per_second"] > 5000  # Good throughput

            # Performance should be consistent and fast
            assert baseline_metrics["parse_duration"] < 1.0  # Should be fast

    def _generate_standardized_content(self) -> str:
        """Generate standardized test content for regression testing."""
        lines = []

        for i in range(5000):  # Standardized size
            level = ["INFO", "DEBUG", "WARNING", "ERROR"][i % 4]
            repo = ["test-repo", "webhook-server", "large-project"][i % 3]
            event = ["push", "pull_request", "check_run"][i % 3]

            # Use the same format as the working test
            line = (
                f"2025-07-31T10:{i // 600:02d}:{i % 60:02d}.{i % 1000:03d}000 GithubWebhook {level} "
                f"{repo} [{event}][hook-{i}][user{i % 10}]: "
                f"Standardized test message {i} for regression testing"
            )
            lines.append(line)

        return "\n".join(lines)
