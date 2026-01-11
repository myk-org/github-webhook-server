"""Tests for webhook_server.utils.app_utils module."""

import datetime
import hashlib
import hmac

import pytest
from fastapi import HTTPException

from webhook_server.utils.app_utils import format_duration, parse_datetime_string, verify_signature


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
