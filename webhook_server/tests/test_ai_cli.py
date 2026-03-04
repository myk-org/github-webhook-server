"""Tests for webhook_server.libs.ai_cli module."""

from __future__ import annotations

from webhook_server.libs.ai_cli import get_ai_config


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
