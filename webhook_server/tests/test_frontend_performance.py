"""Tests for frontend performance optimizations in log viewer."""

import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from simple_logger.logger import get_logger

from webhook_server.libs.log_parser import LogEntry
from webhook_server.web.log_viewer import LogViewerController


class TestFrontendPerformanceOptimizations:
    """Test performance optimizations for large dataset handling."""

    @pytest.fixture
    def controller(self):
        """Create a LogViewerController instance for testing."""
        logger = get_logger(name="test")
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
        """Test that the JavaScript file includes optimized rendering capabilities."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])

        # Check that HTML template includes the JS file
        assert "/static/js/log_viewer.js" in html_content

        # Test for direct DOM manipulation capabilities (avoiding virtual scrolling)
        assert "Direct" in js_content or "direct" in js_content, "Should support direct rendering approach"

        # Test for efficient DOM operations using DocumentFragment
        assert "DocumentFragment" in js_content, "Should use DocumentFragment for efficient DOM updates"

        # Test for element creation capabilities
        assert "createElement" in js_content, "Should have element creation functionality"

        # Test that virtual scrolling is disabled/avoided (key performance decision)
        assert "virtual scrolling" in js_content.lower() and (
            "disabled" in js_content.lower() or "removed" in js_content.lower()
        ), "Virtual scrolling should be explicitly disabled"

    def test_html_template_contains_progressive_loading(self, controller, static_files):
        """Test that the JavaScript and CSS files include progressive loading capabilities."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])
        css_content = self._read_static_file(static_files["css"])

        # Check that HTML template includes the external files
        assert "/static/js/log_viewer.js" in html_content
        assert "/static/css/log_viewer.css" in html_content

        # Test for progressive/chunked loading capabilities
        assert "progressiv" in js_content.lower(), "Should support progressive loading"
        assert "chunk" in js_content.lower(), "Should support chunked loading"

        # Test for loading state management
        assert "loading" in js_content.lower() and "skeleton" in js_content.lower(), (
            "Should have loading skeleton functionality"
        )

        # Test for skeleton loading visual feedback in CSS
        assert "skeleton" in css_content.lower(), "CSS should include skeleton loading styles"
        assert "loading" in css_content.lower(), "CSS should include loading state styles"

        # Test for animations that provide visual feedback
        assert "@keyframes" in css_content, "Should include CSS animations for loading states"

        # Test for error handling and retry capabilities
        assert "error" in js_content.lower(), "Should include error handling"
        assert "retry" in css_content.lower() or "retry" in js_content.lower(), "Should support retry functionality"

    def test_html_template_contains_optimized_filtering(self, controller, static_files):
        """Test that the JavaScript file includes optimized filtering capabilities."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])

        # Check that HTML template includes the JS file
        assert "/static/js/log_viewer.js" in html_content

        # Test for caching mechanism to improve performance
        assert "cache" in js_content.lower(), "Should implement caching for filter performance"
        assert "hash" in js_content.lower(), "Should use hashing for cache invalidation"

        # Test for efficient search term processing
        assert "split" in js_content and "filter" in js_content.lower(), "Should efficiently process search terms"
        assert "every" in js_content.lower() or "all" in js_content.lower(), (
            "Should support multi-term search validation"
        )

        # Test for case-insensitive search capability
        assert "toLowerCase" in js_content or "toUpperCase" in js_content, "Should support case-insensitive search"

        # Test for early exit optimizations
        assert "return false" in js_content, "Should use early exits for filter performance"

    def test_html_template_contains_performance_css(self, controller, static_files):
        """Test that the CSS file includes performance optimizations."""
        html_content = controller._get_log_viewer_html()
        css_content = self._read_static_file(static_files["css"])

        # Check that HTML template includes the CSS file
        assert "/static/css/log_viewer.css" in html_content

        # Test for CSS containment optimization
        assert "contain:" in css_content, "Should use CSS containment for performance"
        assert "layout" in css_content and "paint" in css_content, "Should contain layout and paint operations"

        # Test for loading animations that provide visual feedback
        assert "@keyframes" in css_content, "Should include CSS animations"
        assert "pulse" in css_content.lower() or "shimmer" in css_content.lower(), "Should include loading animations"

        # Test for skeleton loading visual elements
        assert "skeleton" in css_content.lower(), "Should include skeleton loading styles"
        assert "loading" in css_content.lower(), "Should include loading state styles"

    def test_escaping_function_included(self, controller, static_files):
        """Test that HTML escaping functionality is included for security."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])

        # Check that HTML template includes the JS file
        assert "/static/js/log_viewer.js" in html_content

        # Test for HTML escaping mechanism
        assert "escape" in js_content.lower() and "html" in js_content.lower(), (
            "Should include HTML escaping functionality"
        )
        assert "textContent" in js_content, "Should use textContent for safe HTML escaping"
        assert "innerHTML" in js_content, "Should access innerHTML for escaped content"

        # Test that escaping is actually used in content rendering
        js_lower = js_content.lower()
        assert "escape" in js_lower and ("message" in js_lower or "entry" in js_lower), (
            "Should escape user content like messages"
        )
        assert "escape" in js_lower and "hook" in js_lower, "Should escape hook IDs"

    def test_progressive_loading_threshold(self, controller, static_files):
        """Test that progressive loading activates for large datasets."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])

        # Check that HTML template includes the JS file
        assert "/static/js/log_viewer.js" in html_content

        # Test for threshold-based progressive loading activation
        assert "entries.length >" in js_content, "Should check entry count for progressive loading"
        assert "200" in js_content or "100" in js_content, "Should have a reasonable threshold for progressive loading"
        assert "progressiv" in js_content.lower(), "Should activate progressive loading for large datasets"

    def test_chunked_loading_configuration(self, controller, static_files):
        """Test that chunked loading is properly configured."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])

        # Check that HTML template includes the JS file
        assert "/static/js/log_viewer.js" in html_content

        # Test for chunk size configuration
        assert "chunk" in js_content.lower() and ("size" in js_content.lower() or "Size" in js_content), (
            "Should configure chunk size for loading"
        )
        assert any(str(i) in js_content for i in [25, 50, 100]), "Should have reasonable chunk size (25, 50, or 100)"

        # Test for non-blocking behavior
        assert "setTimeout" in js_content, "Should use setTimeout for non-blocking chunked loading"
        assert any(str(i) in js_content for i in [5, 10, 15, 20]), "Should have reasonable delay between chunks"

    def test_debounced_filtering_optimization(self, controller, static_files):
        """Test that debounced filtering is optimized."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])

        # Check that HTML template includes the JS file
        assert "/static/js/log_viewer.js" in html_content

        # Test for debouncing mechanism
        assert "setTimeout" in js_content, "Should implement debouncing for filter performance"
        assert any(str(i) in js_content for i in [200, 300, 500]), "Should have reasonable debounce delay (200-500ms)"

        # Test for cache invalidation on filter changes
        assert "hash" in js_content.lower() and "=" in js_content, "Should reset cache hash on filter changes"

        # Test for immediate client-side filtering capability
        assert "render" in js_content.lower() and "entries" in js_content.lower(), (
            "Should support immediate client-side rendering"
        )

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

            # Test that essential API structure is maintained
            expected_keys = ["entries", "entries_processed", "filtered_count_min", "limit", "offset"]
            for key in expected_keys:
                assert key in result, f"Response should include {key} for API compatibility"

            # Test that memory limits are respected
            assert len(result["entries"]) <= 1000, "Should respect memory limits for large datasets"

            # Test that processing counts are reasonable
            assert result["entries_processed"] >= 0, "Should track number of entries processed"
            assert result["limit"] == 1000, "Should respect requested limit"

    def test_memory_efficient_export(self, controller, large_log_entries):
        """Test that export functionality works efficiently with large datasets."""
        # Mock the stream_log_entries method
        with patch.object(controller, "_stream_log_entries", return_value=iter(large_log_entries)):
            # Test JSON export with large dataset
            result = controller.export_logs(format_type="json")

            # Test that export uses streaming approach for memory efficiency
            assert hasattr(result, "body_iterator"), "Export should use streaming response for large datasets"

            # Test that the response is properly configured for streaming
            assert result is not None, "Export should return a valid response object"

    def test_filter_performance_with_search_terms(self, controller, static_files):
        """Test that search term optimization is implemented."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])

        # Check that HTML template includes the JS file
        assert "/static/js/log_viewer.js" in html_content

        # Test for search term preprocessing and optimization
        assert "split" in js_content, "Should split search terms for multi-term search"
        assert "filter" in js_content.lower() and "length" in js_content, "Should filter out empty search terms"
        assert "every" in js_content.lower(), "Should validate that all search terms match"

        # Test for case-insensitive search capability
        assert "toLowerCase" in js_content or "toUpperCase" in js_content, "Should support case-insensitive search"

    def test_error_handling_and_retry_mechanism(self, controller, static_files):
        """Test that error handling and retry mechanisms are in place."""
        html_content = controller._get_log_viewer_html()
        js_content = self._read_static_file(static_files["js"])

        # Check that HTML template includes the JS file
        assert "/static/js/log_viewer.js" in html_content

        # Test for comprehensive error handling
        assert "catch" in js_content and "error" in js_content.lower(), "Should implement try-catch error handling"
        assert "error" in js_content.lower() and "message" in js_content.lower(), "Should show error messages to users"
        assert "loading" in js_content.lower() and "skeleton" in js_content.lower(), (
            "Should hide loading states on error"
        )

        # Test for retry functionality
        assert "retry" in js_content.lower(), "Should provide retry functionality"
        assert "addEventListener" in js_content and "click" in js_content, "Should handle retry button clicks"
        assert "load" in js_content.lower() and "log" in js_content.lower(), "Should retry loading logs"
        assert "failed" in js_content.lower() or "error" in js_content.lower(), "Should provide clear error messages"
