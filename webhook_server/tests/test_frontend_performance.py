"""Tests for frontend performance optimizations in log viewer."""

import datetime
import logging
from unittest.mock import patch

import pytest

from webhook_server.libs.log_parser import LogEntry
from webhook_server.web.log_viewer import LogViewerController


class TestFrontendPerformanceOptimizations:
    """Test performance optimizations for large dataset handling."""

    @pytest.fixture
    def controller(self):
        """Create a LogViewerController instance for testing."""
        logger = logging.getLogger("test")
        return LogViewerController(logger=logger)

    @pytest.fixture
    def large_log_entries(self):
        """Create a large dataset of log entries for performance testing."""
        entries = []
        base_time = datetime.datetime(2025, 8, 1, 10, 0, 0)

        for i in range(2000):  # Large dataset
            entries.append(
                LogEntry(
                    timestamp=base_time + datetime.timedelta(seconds=i),
                    level="INFO" if i % 4 != 0 else "ERROR",
                    logger_name="GithubWebhook",
                    message=f"Processing webhook event {i}",
                    hook_id=f"test-hook-{i // 10}",  # Group by 10s
                    repository=f"test-repo-{i % 5}",  # 5 different repos
                    event_type="push" if i % 2 == 0 else "pull_request",
                    github_user="test-user",
                    pr_number=i if i % 3 == 0 else None,
                )
            )

        return entries

    def test_html_template_contains_virtual_scrolling_code(self, controller):
        """Test that the HTML template includes virtual scrolling optimizations."""
        html_content = controller._get_log_viewer_html()

        # Check for virtual scrolling constants
        assert "ITEM_HEIGHT = 60" in html_content
        assert "BUFFER_SIZE = 5" in html_content

        # Check for optimized rendering functions
        assert "renderLogEntriesOptimized" in html_content
        assert "renderLogEntriesDirect" in html_content
        assert "renderLogEntriesVirtual" in html_content

        # Check for performance optimization features
        assert "virtualScrollData" in html_content
        assert "createLogEntryElement" in html_content
        assert "DocumentFragment" in html_content

    def test_html_template_contains_progressive_loading(self, controller):
        """Test that the HTML template includes progressive loading features."""
        html_content = controller._get_log_viewer_html()

        # Check for progressive loading functions
        assert "loadEntriesProgressively" in html_content
        assert "showLoadingSkeleton" in html_content
        assert "hideLoadingSkeleton" in html_content

        # Check for skeleton loading styles
        assert "loading-skeleton" in html_content
        assert "skeleton-entry" in html_content
        assert "skeleton-line" in html_content

        # Check for error handling
        assert "showErrorMessage" in html_content
        assert "retry-btn" in html_content

    def test_html_template_contains_optimized_filtering(self, controller):
        """Test that the HTML template includes optimized filtering."""
        html_content = controller._get_log_viewer_html()

        # Check for filter caching
        assert "lastFilterHash" in html_content
        assert "cachedFilteredEntries" in html_content
        assert "clearFilterCache" in html_content

        # Check for optimized filter function
        assert "searchTerms" in html_content
        assert "every(term =>" in html_content

    def test_html_template_contains_performance_css(self, controller):
        """Test that the HTML template includes performance-optimized CSS."""
        html_content = controller._get_log_viewer_html()

        # Check for CSS performance optimizations
        assert "contain: layout style paint" in html_content
        assert "will-change: scroll-position" in html_content
        assert "will-change: transform" in html_content

        # Check for loading animations
        assert "@keyframes pulse" in html_content
        assert "@keyframes shimmer" in html_content

        # Check for skeleton styles
        assert ".skeleton-entry" in html_content
        assert ".loading-skeleton" in html_content

    def test_escaping_function_included(self, controller):
        """Test that HTML escaping function is included for security."""
        html_content = controller._get_log_viewer_html()

        # Check for HTML escaping function
        assert "function escapeHtml(text)" in html_content
        assert "div.textContent = text" in html_content
        assert "div.innerHTML" in html_content

        # Check that escaping is used in log entry creation
        assert "escapeHtml(entry.message)" in html_content
        assert "escapeHtml(entry.hook_id)" in html_content

    def test_virtual_scrolling_threshold(self, controller):
        """Test that virtual scrolling activates for datasets > 100 entries."""
        html_content = controller._get_log_viewer_html()

        # Check for the threshold logic
        assert "if (filteredEntries.length <= 100)" in html_content
        assert "renderLogEntriesDirect" in html_content
        assert "renderLogEntriesVirtual" in html_content

    def test_progressive_loading_threshold(self, controller):
        """Test that progressive loading activates for datasets > 200 entries."""
        html_content = controller._get_log_viewer_html()

        # Check for progressive loading threshold
        assert "if (data.entries.length > 200)" in html_content
        assert "loadEntriesProgressively" in html_content

    def test_chunked_loading_configuration(self, controller):
        """Test that chunked loading is properly configured."""
        html_content = controller._get_log_viewer_html()

        # Check for chunk configuration
        assert "chunkSize = 50" in html_content
        assert "setTimeout(resolve, 10)" in html_content
        assert "filters.append('limit', '1000')" in html_content

    def test_debounced_filtering_optimization(self, controller):
        """Test that debounced filtering is optimized."""
        html_content = controller._get_log_viewer_html()

        # Check for optimized debouncing
        assert "setTimeout(() => {" in html_content
        assert "300)" in html_content  # Debounce delay
        assert "lastFilterHash = ''" in html_content

        # Check that immediate filtering still works
        assert "renderLogEntries();" in html_content

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.iterdir")
    def test_controller_works_with_large_datasets(self, mock_iterdir, mock_exists, controller, large_log_entries):
        """Test that the controller can handle large datasets efficiently."""
        # Mock file system for log parsing
        mock_exists.return_value = True
        mock_iterdir.return_value = []

        # Mock the stream_log_entries method to return our large dataset
        with patch.object(controller, "_stream_log_entries", return_value=iter(large_log_entries)):
            # Test getting log entries with a large dataset
            result = controller.get_log_entries(limit=1000)

            # Should still work efficiently
            assert "entries" in result
            assert "entries_processed" in result
            assert "filtered_count_min" in result
            assert "limit" in result
            assert "offset" in result

            # Check that we got the expected number of entries
            assert len(result["entries"]) <= 1000

    def test_memory_efficient_export(self, controller, large_log_entries):
        """Test that export functionality works efficiently with large datasets."""
        # Mock the stream_log_entries method
        with patch.object(controller, "_stream_log_entries", return_value=iter(large_log_entries)):
            # Test JSON export with large dataset
            result = controller.export_logs(format_type="json")

            # Should return streaming response
            assert hasattr(result, "body_iterator")

    def test_filter_performance_with_search_terms(self, controller):
        """Test that search term optimization is implemented."""
        html_content = controller._get_log_viewer_html()

        # Check for search term preprocessing
        assert "search.split(' ')" in html_content
        assert "filter(term => term.length > 0)" in html_content
        assert "searchTerms.every(term =>" in html_content

        # Check for case-insensitive search
        assert "toLowerCase()" in html_content

    def test_error_handling_and_retry_mechanism(self, controller):
        """Test that error handling and retry mechanisms are in place."""
        html_content = controller._get_log_viewer_html()

        # Check for error handling
        assert "catch (error)" in html_content
        assert "showErrorMessage" in html_content
        assert "hideLoadingSkeleton" in html_content

        # Check for retry functionality
        assert 'onclick="loadHistoricalLogs()"' in html_content
        assert "Failed to load log entries" in html_content
