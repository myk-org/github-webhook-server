"""Tests for log sanitization in helpers module."""

from __future__ import annotations

from webhook_server.utils.helpers import (
    _redact_secrets,
    _sanitize_log_value,
    _truncate_output,
    format_task_fields,
    strip_ansi_codes,
)


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


class TestRedactSecrets:
    """Test the _redact_secrets function."""

    def test_redact_single_secret(self) -> None:
        """Test redacting a single secret."""
        text = "Password is secret123"
        secrets = ["secret123"]
        result = _redact_secrets(text, secrets)
        assert "secret123" not in result
        assert "***REDACTED***" in result

    def test_redact_multiple_secrets(self) -> None:
        """Test redacting multiple secrets."""
        text = "Token: abc123 Password: xyz789"
        secrets = ["abc123", "xyz789"]
        result = _redact_secrets(text, secrets)
        assert "abc123" not in result
        assert "xyz789" not in result
        assert result.count("***REDACTED***") == 2

    def test_redact_empty_secrets_list(self) -> None:
        """Test with empty secrets list."""
        text = "No secrets here"
        result = _redact_secrets(text, None)
        assert result == text

    def test_redact_no_secrets_in_text(self) -> None:
        """Test when no secrets are found in text."""
        text = "No secrets here"
        secrets = ["secret123"]
        result = _redact_secrets(text, secrets)
        assert result == text

    def test_redact_secret_with_special_regex_chars(self) -> None:
        """Test redacting secrets containing regex special characters."""
        text = "Password: test[123].*"
        secrets = ["test[123].*"]
        result = _redact_secrets(text, secrets)
        assert "test[123].*" not in result
        assert "***REDACTED***" in result

    def test_redact_case_sensitive(self) -> None:
        """Test case-sensitive redaction (default)."""
        text = "Token: ABC123"
        secrets = ["abc123"]
        result = _redact_secrets(text, secrets, case_insensitive=False)
        # Should not match due to case difference
        assert "ABC123" in result

    def test_redact_case_insensitive(self) -> None:
        """Test case-insensitive redaction."""
        text = "Token: ABC123"
        secrets = ["abc123"]
        result = _redact_secrets(text, secrets, case_insensitive=True)
        assert "ABC123" not in result
        assert "***REDACTED***" in result

    def test_redact_substring_prevention(self) -> None:
        """Test that longer secrets are matched first to prevent substring leaks."""
        text = "Secret: abcdef"
        secrets = ["abc", "abcdef"]
        result = _redact_secrets(text, secrets)
        # Should match "abcdef" first, not "abc"
        assert result.count("***REDACTED***") == 1
        assert "abc" not in result

    def test_redact_empty_strings_filtered(self) -> None:
        """Test that empty strings in secrets list are filtered out."""
        text = "No secrets"
        secrets = ["", "  ", "secret"]
        result = _redact_secrets(text, secrets)
        # Empty strings should be filtered, but "secret" should still be redacted
        assert result == "No ***REDACTED***s"
        # Verify empty strings don't cause issues (they're filtered in the function)
        result2 = _redact_secrets(text, ["", "  "])
        assert result2 == text  # No non-empty secrets, so no redaction


class TestTruncateOutput:
    """Test the _truncate_output function."""

    def test_truncate_long_text(self) -> None:
        """Test truncating text longer than max_length."""
        text = "A" * 1000
        result = _truncate_output(text, max_length=500)
        assert len(result) < len(text)
        assert "... [truncated" in result
        assert "500 chars]" in result

    def test_truncate_short_text(self) -> None:
        """Test that short text is not truncated."""
        text = "Short text"
        result = _truncate_output(text, max_length=500)
        assert result == text

    def test_truncate_exact_length(self) -> None:
        """Test text exactly at max_length."""
        text = "A" * 500
        result = _truncate_output(text, max_length=500)
        assert result == text

    def test_truncate_custom_max_length(self) -> None:
        """Test with custom max_length."""
        text = "A" * 200
        result = _truncate_output(text, max_length=100)
        assert len(result) < len(text)
        assert "... [truncated 100 chars]" in result

    def test_truncate_empty_string(self) -> None:
        """Test truncating empty string."""
        result = _truncate_output("", max_length=500)
        assert result == ""


class TestStripAnsiCodes:
    """Test the strip_ansi_codes function."""

    def test_strip_color_codes(self) -> None:
        """Test stripping ANSI color codes."""
        text = "\x1b[31mRed text\x1b[0m"
        result = strip_ansi_codes(text)
        assert result == "Red text"
        assert "\x1b" not in result

    def test_strip_bold_codes(self) -> None:
        """Test stripping ANSI bold codes."""
        text = "\x1b[1mBold text\x1b[0m"
        result = strip_ansi_codes(text)
        assert result == "Bold text"

    def test_strip_multiple_codes(self) -> None:
        """Test stripping multiple ANSI codes."""
        text = "\x1b[1m\x1b[32mBold green\x1b[0m"
        result = strip_ansi_codes(text)
        assert result == "Bold green"

    def test_strip_no_ansi_codes(self) -> None:
        """Test text with no ANSI codes."""
        text = "Plain text"
        result = strip_ansi_codes(text)
        assert result == text

    def test_strip_cursor_movement_codes(self) -> None:
        """Test stripping cursor movement codes."""
        text = "\x1b[2J\x1b[HClear screen"
        result = strip_ansi_codes(text)
        assert result == "Clear screen"

    def test_strip_mixed_content(self) -> None:
        """Test stripping ANSI codes from mixed content."""
        text = "Start \x1b[31mred\x1b[0m middle \x1b[32mgreen\x1b[0m end"
        result = strip_ansi_codes(text)
        assert result == "Start red middle green end"

    def test_strip_empty_string(self) -> None:
        """Test stripping ANSI codes from empty string."""
        result = strip_ansi_codes("")
        assert result == ""

    def test_strip_escape_sequences_only(self) -> None:
        """Test that only ANSI escape sequences are removed."""
        text = "Text with [brackets] and (parentheses)"
        result = strip_ansi_codes(text)
        assert result == text  # Should remain unchanged
