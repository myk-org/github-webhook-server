"""Tests for webhook_server.libs.ai_cli module."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from webhook_server.libs.ai_cli import AIResult, call_ai, get_ai_config


class TestGetAiConfig:
    """Test suite for get_ai_config function."""

    def test_get_ai_config_returns_tuple(self) -> None:
        result = get_ai_config({"ai-provider": "claude", "ai-model": "sonnet"})
        assert result == ("claude", "sonnet")

    def test_get_ai_config_returns_none_for_none(self) -> None:
        assert get_ai_config(None) is None

    def test_get_ai_config_returns_none_for_empty_dict(self) -> None:
        assert get_ai_config({}) is None

    def test_get_ai_config_partial_missing_model(self) -> None:
        assert get_ai_config({"ai-provider": "claude"}) is None

    def test_get_ai_config_partial_missing_provider(self) -> None:
        assert get_ai_config({"ai-model": "sonnet"}) is None


class TestCallAi:
    """Test suite for call_ai function."""

    @pytest.mark.asyncio
    async def test_call_ai_sidecar_unavailable(self) -> None:
        """Test call_ai returns error when sidecar is unavailable."""
        with patch(
            "webhook_server.libs.ai_cli.check_sidecar_available",
            new_callable=AsyncMock,
            return_value=(False, "connection refused"),
        ):
            result = await call_ai(
                prompt="test",
                ai_provider="claude",
                ai_model="sonnet",
                cwd="/tmp",
            )
            assert result.success is False
            assert "Pi-sidecar unavailable" in result.error
            assert "connection refused" in result.error

    @pytest.mark.asyncio
    async def test_call_ai_sidecar_available(self) -> None:
        """Test call_ai delegates to call_ai_once when sidecar is available."""
        expected = AIResult(success=True, text="hello", error="")
        with patch(
            "webhook_server.libs.ai_cli.check_sidecar_available",
            new_callable=AsyncMock,
            return_value=(True, "ok"),
        ):
            with patch(
                "webhook_server.libs.ai_cli.call_ai_once",
                new_callable=AsyncMock,
                return_value=expected,
            ) as mock_call:
                result = await call_ai(
                    prompt="test",
                    ai_provider="claude",
                    ai_model="sonnet",
                    cwd="/tmp",
                    timeout_minutes=5,
                    system_prompt="be helpful",
                )
                assert result.success is True
                assert result.text == "hello"
                mock_call.assert_awaited_once_with(
                    prompt="test",
                    ai_provider="claude",
                    ai_model="sonnet",
                    cwd="/tmp",
                    ai_call_timeout=5,
                    system_prompt="be helpful",
                    tools=None,
                    custom_tools=None,
                )
