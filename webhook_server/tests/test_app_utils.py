"""Tests for webhook_server.utils.app_utils module."""

import datetime
import hashlib
import hmac
from datetime import UTC, timedelta
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from webhook_server.utils.app_utils import format_duration, log_webhook_summary, parse_datetime_string, verify_signature
from webhook_server.utils.context import WebhookContext


class TestVerifySignature:
    """Test suite for verify_signature function."""

    def test_verify_signature_missing_header(self) -> None:
        """Test verify_signature raises HTTPException when signature_header is None."""
        payload_body = b"test payload"
        secret_token = "test_secret"  # pragma: allowlist secret

        with pytest.raises(HTTPException) as exc_info:
            verify_signature(payload_body, secret_token, signature_header=None)

        assert exc_info.value.status_code == 403
        assert "x-hub-signature-256 header is missing" in exc_info.value.detail

    def test_verify_signature_valid(self) -> None:
        """Test verify_signature with valid signature."""
        payload_body = b"test payload"
        secret_token = "test_secret"  # pragma: allowlist secret

        hash_object = hmac.new(secret_token.encode("utf-8"), msg=payload_body, digestmod=hashlib.sha256)
        expected_signature = "sha256=" + hash_object.hexdigest()

        # Should not raise exception
        verify_signature(payload_body, secret_token, signature_header=expected_signature)

    def test_verify_signature_invalid(self) -> None:
        """Test verify_signature with invalid signature."""
        payload_body = b"test payload"
        secret_token = "test_secret"  # pragma: allowlist secret
        invalid_signature = "sha256=invalid_signature"

        with pytest.raises(HTTPException) as exc_info:
            verify_signature(payload_body, secret_token, signature_header=invalid_signature)

        assert exc_info.value.status_code == 403
        assert "Request signatures didn't match" in exc_info.value.detail


class TestParseDatetimeString:
    """Test suite for parse_datetime_string function."""

    def test_parse_datetime_string_none(self) -> None:
        """Test parse_datetime_string with None input."""
        result = parse_datetime_string(None, "test_field")
        assert result is None

    def test_parse_datetime_string_valid_iso(self) -> None:
        """Test parse_datetime_string with valid ISO format."""
        datetime_str = "2024-01-01T12:00:00Z"
        result = parse_datetime_string(datetime_str, "test_field")
        assert isinstance(result, datetime.datetime)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 1

    def test_parse_datetime_string_valid_with_timezone(self) -> None:
        """Test parse_datetime_string with valid ISO format with timezone."""
        datetime_str = "2024-01-01T12:00:00+00:00"
        result = parse_datetime_string(datetime_str, "test_field")
        assert isinstance(result, datetime.datetime)

    def test_parse_datetime_string_invalid_format(self) -> None:
        """Test parse_datetime_string with invalid format raises HTTPException."""
        datetime_str = "invalid-datetime-format"
        field_name = "test_field"

        with pytest.raises(HTTPException) as exc_info:
            parse_datetime_string(datetime_str, field_name)

        assert exc_info.value.status_code == 400
        assert f"Invalid {field_name} format" in exc_info.value.detail
        assert datetime_str in exc_info.value.detail
        assert "Expected ISO 8601 format" in exc_info.value.detail

    def test_parse_datetime_string_empty_string(self) -> None:
        """Test parse_datetime_string with empty string returns None."""
        datetime_str = ""
        result = parse_datetime_string(datetime_str, "test_field")
        # Empty string is falsy, so it returns None (same as None input)
        assert result is None


class TestFormatDuration:
    """Test suite for format_duration function."""

    def test_format_duration_milliseconds_only(self) -> None:
        """Test format_duration with less than 1 second."""
        assert format_duration(500) == "500ms"
        assert format_duration(0) == "0ms"
        assert format_duration(999) == "999ms"

    def test_format_duration_seconds_only(self) -> None:
        """Test format_duration with exact seconds (no remaining ms)."""
        assert format_duration(1000) == "1s"
        assert format_duration(5000) == "5s"
        assert format_duration(59000) == "59s"

    def test_format_duration_seconds_with_milliseconds(self) -> None:
        """Test format_duration with seconds and remaining milliseconds."""
        assert format_duration(1500) == "1s500ms"
        assert format_duration(5123) == "5s123ms"

    def test_format_duration_minutes_only(self) -> None:
        """Test format_duration with exact minutes (no remaining seconds)."""
        assert format_duration(60000) == "1m"
        assert format_duration(120000) == "2m"
        assert format_duration(3540000) == "59m"

    def test_format_duration_minutes_with_seconds(self) -> None:
        """Test format_duration with minutes and remaining seconds."""
        assert format_duration(65000) == "1m5s"
        assert format_duration(125000) == "2m5s"

    def test_format_duration_hours_only(self) -> None:
        """Test format_duration with exact hours (no remaining minutes)."""
        assert format_duration(3600000) == "1h"
        assert format_duration(7200000) == "2h"

    def test_format_duration_hours_with_minutes(self) -> None:
        """Test format_duration with hours and remaining minutes."""
        assert format_duration(3660000) == "1h1m"
        assert format_duration(5700000) == "1h35m"


class TestLogWebhookSummary:
    """Test suite for log_webhook_summary function."""

    def test_log_webhook_summary_with_complete_steps(self) -> None:
        """Test log_webhook_summary with fully completed steps."""
        # Create context with completed_at set
        start_time = datetime.datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = start_time
            ctx = WebhookContext(
                hook_id="test-hook-1",
                event_type="pull_request",
                repository="owner/repo",
                repository_full_name="owner/repo",
                pr_number=42,
            )

        # Start and complete a step
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = start_time
            ctx.start_step("webhook_routing")

        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = start_time + timedelta(seconds=2)
            ctx.complete_step("webhook_routing")

        # Set completed_at
        ctx.completed_at = start_time + timedelta(seconds=5)

        # Mock logger
        mock_logger = Mock()

        # Call the function - should not raise
        log_webhook_summary(ctx, mock_logger, "[TEST]")

        # Verify logger was called with info level
        mock_logger.info.assert_called_once()
        log_message = mock_logger.info.call_args[0][0]

        # Verify log message contains expected information
        assert "[SUCCESS]" in log_message
        assert "PR#42" in log_message
        assert "webhook_routing:completed(2s)" in log_message

    def test_log_webhook_summary_with_incomplete_step(self) -> None:
        """Test log_webhook_summary handles incomplete steps (started but not completed).

        This tests the bug fix for:
        ValueError: Workflow step 'webhook_routing' missing or None 'duration_ms' field
        """
        # Create context
        start_time = datetime.datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = start_time
            ctx = WebhookContext(
                hook_id="test-hook-2",
                event_type="pull_request",
                repository="owner/repo",
                repository_full_name="owner/repo",
            )

        # Start a step but DON'T complete it (simulating exception before complete_step)
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = start_time
            ctx.start_step("webhook_routing")

        # Set completed_at (happens in finally block even on exception)
        ctx.completed_at = start_time + timedelta(seconds=3)
        ctx.success = False

        # Mock logger
        mock_logger = Mock()

        # Call the function - should NOT raise ValueError anymore
        log_webhook_summary(ctx, mock_logger, "[TEST]")

        # Verify logger was called
        mock_logger.info.assert_called_once()
        log_message = mock_logger.info.call_args[0][0]

        # Verify incomplete step is shown as "(incomplete)"
        assert "[FAILED]" in log_message
        assert "webhook_routing:started(incomplete)" in log_message

    def test_log_webhook_summary_with_missing_status(self) -> None:
        """Test log_webhook_summary handles steps with missing status field."""
        # Create context
        start_time = datetime.datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = start_time
            ctx = WebhookContext(
                hook_id="test-hook-3",
                event_type="push",
                repository="owner/repo",
                repository_full_name="owner/repo",
            )

        # Manually add a malformed step (missing status)
        ctx.workflow_steps["bad_step"] = {"timestamp": start_time.isoformat()}

        # Set completed_at
        ctx.completed_at = start_time + timedelta(seconds=1)

        # Mock logger
        mock_logger = Mock()

        # Call the function - should handle gracefully
        log_webhook_summary(ctx, mock_logger, "[TEST]")

        # Verify logger was called
        mock_logger.info.assert_called_once()
        log_message = mock_logger.info.call_args[0][0]

        # Verify missing status defaults to "unknown" and shows as incomplete
        assert "bad_step:unknown(incomplete)" in log_message

    def test_log_webhook_summary_raises_when_not_completed(self) -> None:
        """Test log_webhook_summary raises ValueError when completed_at is None."""
        # Create context without setting completed_at
        start_time = datetime.datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = start_time
            ctx = WebhookContext(
                hook_id="test-hook-4",
                event_type="push",
                repository="owner/repo",
                repository_full_name="owner/repo",
            )

        # Mock logger
        mock_logger = Mock()

        # Call should raise ValueError because completed_at is None
        with pytest.raises(ValueError, match="Context completed_at is None"):
            log_webhook_summary(ctx, mock_logger, "[TEST]")
