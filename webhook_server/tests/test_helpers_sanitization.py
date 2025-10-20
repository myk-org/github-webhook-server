"""Tests for log sanitization in helpers module."""

from __future__ import annotations

from webhook_server.utils.helpers import _sanitize_log_value, format_task_fields


class TestSanitizeLogValue:
    """Test the _sanitize_log_value helper function."""

    def test_sanitize_newlines(self) -> None:
        """Test that newlines are removed."""
        value = "test\nvalue\nwith\nnewlines"
        result = _sanitize_log_value(value)
        assert "\n" not in result
        assert result == "test value with newlines"

    def test_sanitize_carriage_returns(self) -> None:
        """Test that carriage returns are removed."""
        value = "test\rvalue\r\nwith\rreturns"
        result = _sanitize_log_value(value)
        assert "\r" not in result
        assert "\n" not in result
        assert result == "test value  with returns"

    def test_sanitize_tabs(self) -> None:
        """Test handling of tabs (currently preserved by implementation)."""
        value = "test\tvalue\twith\ttabs"
        result = _sanitize_log_value(value)
        # Note: Current implementation doesn't remove tabs, only newlines and carriage returns
        # This test documents current behavior and can be updated if tabs should be sanitized
        assert result == "test\tvalue\twith\ttabs"

    def test_sanitize_control_characters(self) -> None:
        """Test handling of control characters (currently preserved by implementation)."""
        value = "test\x00value\x01with\x02control"
        result = _sanitize_log_value(value)
        # Note: Current implementation doesn't remove control characters
        # This test documents current behavior and can be updated if control chars should be sanitized
        assert "test" in result and "value" in result
        # Verify the function doesn't break with control characters
        assert isinstance(result, str)

    def test_escape_brackets(self) -> None:
        """Test that brackets are escaped."""
        value = "value[with]brackets"
        result = _sanitize_log_value(value)
        assert result == "value\\[with\\]brackets"

    def test_combined_injection_attempt(self) -> None:
        """Test sanitization of complex injection attempt."""
        # Simulates log injection: task_id=normal] [task_id=injected
        value = "normal] [task_id=injected"
        result = _sanitize_log_value(value)
        # Should escape brackets to prevent breaking out of structured field
        assert result == "normal\\] \\[task_id=injected"

    def test_newline_injection_attempt(self) -> None:
        """Test sanitization of newline injection attempt."""
        # Simulates log injection with newline to insert fake log entry
        value = "normal\n[ERROR] Fake log entry"
        result = _sanitize_log_value(value)
        assert "\n" not in result
        # Brackets should also be escaped to prevent fake structured log entries
        assert result == "normal \\[ERROR\\] Fake log entry"

    def test_empty_string(self) -> None:
        """Test sanitization of empty string."""
        result = _sanitize_log_value("")
        assert result == ""

    def test_clean_value_unchanged_content(self) -> None:
        """Test that clean values have same content (just escaped brackets)."""
        value = "clean_task_id_123"
        result = _sanitize_log_value(value)
        assert result == value  # No brackets, newlines, or returns to sanitize


class TestFormatTaskFields:
    """Test the format_task_fields function with sanitization."""

    def test_format_task_fields_normal(self) -> None:
        """Test normal task field formatting."""
        result = format_task_fields(
            task_id="check_tox",
            task_type="ci_check",
            task_status="started",
        )
        assert result == "[task_id=check_tox] [task_type=ci_check] [task_status=started]"

    def test_format_task_fields_with_injection(self) -> None:
        """Test task field formatting with injection attempt."""
        # Try to inject additional fields via bracket manipulation
        result = format_task_fields(
            task_id="normal] [task_id=injected",
            task_type="ci_check",
            task_status="started",
        )
        # Brackets should be escaped to prevent injection
        assert result == "[task_id=normal\\] \\[task_id=injected] [task_type=ci_check] [task_status=started]"

    def test_format_task_fields_with_newlines(self) -> None:
        """Test task field formatting with newline injection attempt."""
        result = format_task_fields(
            task_id="check_tox\nFAKE_LOG_ENTRY",
            task_type="ci_check",
            task_status="started",
        )
        # Newlines should be replaced with spaces
        assert "\n" not in result
        assert result == "[task_id=check_tox FAKE_LOG_ENTRY] [task_type=ci_check] [task_status=started]"

    def test_format_task_fields_with_tabs_and_control_chars(self) -> None:
        """Test task field formatting with tabs and control characters."""
        result = format_task_fields(
            task_id="check\ttox\x00test",
            task_type="ci_check",
            task_status="started",
        )
        # Note: Current implementation preserves tabs and control chars (only sanitizes \n, \r, and brackets)
        # This test documents current behavior
        assert isinstance(result, str)
        assert "task_id=" in result
        assert "task_type=ci_check" in result
        assert "task_status=started" in result

    def test_format_task_fields_partial(self) -> None:
        """Test formatting with only some fields provided."""
        result = format_task_fields(task_id="check_tox")
        assert result == "[task_id=check_tox]"

        result = format_task_fields(task_type="ci_check", task_status="started")
        assert result == "[task_type=ci_check] [task_status=started]"

    def test_format_task_fields_empty(self) -> None:
        """Test formatting with no fields provided."""
        result = format_task_fields()
        assert result == ""

    def test_format_task_fields_all_injections(self) -> None:
        """Test formatting with injection attempts in all fields."""
        result = format_task_fields(
            task_id="id]\n[fake=field",
            task_type="type]\r\n[fake=log",
            task_status="status[bracket]test",
        )
        # All dangerous characters should be sanitized
        assert "\n" not in result
        assert "\r" not in result
        # Brackets should be escaped
        assert "\\[" in result
        assert "\\]" in result
