"""Tests for frontend performance optimizations in log viewer."""

import datetime
import logging
from pathlib import Path
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
    def static_files(self):
        """Get paths to static files for testing."""
        base_path = Path(__file__).parent.parent / "web" / "static"
        return {"css": base_path / "css" / "log_viewer.css", "js": base_path / "js" / "log_viewer.js"}

    def _read_static_file(self, file_path):
        """Read content from a static file."""
        try:
            return file_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            pytest.fail(f"Static file not found: {file_path}")
        except Exception as e:
            pytest.fail(f"Error reading static file {file_path}: {e}")

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

    def test_html_template_contains_optimized_rendering(self, controller, static_files):
        """Test that the JavaScript file includes optimized rendering functions."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])

        # Check that HTML template includes the JS file
        assert "/static/js/log_viewer.js" in html_content

        # Check for optimized rendering functions (non-virtual scrolling) in JS file
        assert "renderLogEntriesOptimized" in js_content
        assert "renderLogEntriesDirect" in js_content

        # Check for performance optimization features in JS file
        assert "createLogEntryElement" in js_content
        assert "DocumentFragment" in js_content

    def test_html_template_contains_progressive_loading(self, controller, static_files):
        """Test that the JavaScript and CSS files include progressive loading features."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])
        css_content = self._read_static_file(static_files["css"])

        # Check that HTML template includes the external files
        assert "/static/js/log_viewer.js" in html_content
        assert "/static/css/log_viewer.css" in html_content

        # Check for progressive loading functions in JS
        assert "loadEntriesProgressively" in js_content
        assert "showLoadingSkeleton" in js_content
        assert "hideLoadingSkeleton" in js_content

        # Check for skeleton loading styles in CSS
        assert "loading-skeleton" in css_content
        assert "skeleton-entry" in css_content
        assert "skeleton-line" in css_content

        # Check for error handling in JS
        assert "showErrorMessage" in js_content
        assert "retry-btn" in css_content  # CSS class in CSS file

    def test_html_template_contains_optimized_filtering(self, controller, static_files):
        """Test that the JavaScript file includes optimized filtering."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])

        # Check that HTML template includes the JS file
        assert "/static/js/log_viewer.js" in html_content

        # Check for filter caching in JS
        assert "lastFilterHash" in js_content
        assert "cachedFilteredEntries" in js_content
        assert "clearFilterCache" in js_content

        # Check for optimized filter function in JS
        assert "searchTerms" in js_content
        assert "every(term =>" in js_content

    def test_html_template_contains_performance_css(self, controller, static_files):
        """Test that the CSS file includes performance-optimized CSS."""
        html_content = controller._get_log_viewer_html()
        css_content = self._read_static_file(static_files["css"])

        # Check that HTML template includes the CSS file
        assert "/static/css/log_viewer.css" in html_content

        # Check for CSS performance optimizations in CSS file
        assert "contain: layout style paint" in css_content

        # Check for loading animations in CSS file
        assert "@keyframes pulse" in css_content
        assert "@keyframes shimmer" in css_content

        # Check for skeleton styles in CSS file
        assert ".skeleton-entry" in css_content
        assert ".loading-skeleton" in css_content

    def test_escaping_function_included(self, controller, static_files):
        """Test that HTML escaping function is included for security."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])

        # Check that HTML template includes the JS file
        assert "/static/js/log_viewer.js" in html_content

        # Check for HTML escaping function in JS
        assert "function escapeHtml(text)" in js_content
        assert "div.textContent = text" in js_content
        assert "div.innerHTML" in js_content

        # Check that escaping is used in log entry creation in JS
        assert "escapeHtml(entry.message)" in js_content
        assert "escapeHtml(entry.hook_id)" in js_content

    def test_rendering_functions_present(self, controller, static_files):
        """Test that rendering functions are present in the JavaScript file."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])

        # Check that HTML template includes the JS file
        assert "/static/js/log_viewer.js" in html_content

        # Check for the rendering functions in JS
        assert "renderLogEntriesDirect" in js_content
        assert "renderLogEntriesOptimized" in js_content

    def test_progressive_loading_threshold(self, controller, static_files):
        """Test that progressive loading activates for datasets > 200 entries."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])

        # Check that HTML template includes the JS file
        assert "/static/js/log_viewer.js" in html_content

        # Check for progressive loading threshold in JS
        assert "if (data.entries.length > 200)" in js_content
        assert "loadEntriesProgressively" in js_content

    def test_chunked_loading_configuration(self, controller, static_files):
        """Test that chunked loading is properly configured."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])

        # Check that HTML template includes the JS file
        assert "/static/js/log_viewer.js" in html_content

        # Check for chunk configuration in JS
        assert "chunkSize = 50" in js_content
        assert "setTimeout(resolve, 10)" in js_content

    def test_debounced_filtering_optimization(self, controller, static_files):
        """Test that debounced filtering is optimized."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])

        # Check that HTML template includes the JS file
        assert "/static/js/log_viewer.js" in html_content

        # Check for optimized debouncing in JS
        assert "setTimeout(() => {" in js_content
        assert "300)" in js_content  # Debounce delay
        assert "lastFilterHash = ''" in js_content

        # Check that immediate filtering still works in JS
        assert "renderLogEntries();" in js_content

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

    def test_filter_performance_with_search_terms(self, controller, static_files):
        """Test that search term optimization is implemented."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])

        # Check that HTML template includes the JS file
        assert "/static/js/log_viewer.js" in html_content

        # Check for search term preprocessing in JS
        assert "search.split(' ')" in js_content
        assert "filter(term => term.length > 0)" in js_content
        assert "searchTerms.every(term =>" in js_content

        # Check for case-insensitive search in JS
        assert "toLowerCase()" in js_content

    def test_error_handling_and_retry_mechanism(self, controller, static_files):
        """Test that error handling and retry mechanisms are in place."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])

        # Check that HTML template includes the JS file
        assert "/static/js/log_viewer.js" in html_content

        # Check for error handling in JS
        assert "catch (error)" in js_content
        assert "showErrorMessage" in js_content
        assert "hideLoadingSkeleton" in js_content

        # Check for retry functionality - onclick in HTML, message in JS
        assert 'onclick="loadHistoricalLogs()"' in html_content
        assert "Failed to load log entries" in js_content
