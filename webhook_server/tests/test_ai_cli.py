"""Tests for webhook_server.libs.ai_cli module."""

from __future__ import annotations

import asyncio
import subprocess
from unittest.mock import AsyncMock, Mock, patch

import pytest

from webhook_server.libs.ai_cli import PROVIDER_CONFIG, call_ai_cli, get_ai_config


class TestCallAiCli:
    """Test suite for call_ai_cli function."""

    @pytest.fixture
    def mock_logger(self) -> Mock:
        return Mock()

    @pytest.mark.asyncio
    async def test_successful_call(self, mock_logger: Mock) -> None:
        """Test successful AI CLI call."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "feat: add new feature"
        mock_result.stderr = ""

        with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_result):
            success, output = await call_ai_cli(
                prompt="suggest a title",
                ai_provider="claude",
                ai_model="sonnet",
                cwd="/tmp/repo",
                logger=mock_logger,
            )

        assert success is True
        assert output == "feat: add new feature"

    @pytest.mark.asyncio
    async def test_unknown_provider(self, mock_logger: Mock) -> None:
        """Test unknown AI provider returns error."""
        success, output = await call_ai_cli(
            prompt="test",
            ai_provider="unknown",
            ai_model="model",
            cwd="/tmp/repo",
            logger=mock_logger,
        )

        assert success is False
        assert "Unknown AI provider" in output

    @pytest.mark.asyncio
    async def test_cli_failure(self, mock_logger: Mock) -> None:
        """Test CLI returning non-zero exit code."""
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: invalid model"

        with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_result):
            success, output = await call_ai_cli(
                prompt="test",
                ai_provider="claude",
                ai_model="bad-model",
                cwd="/tmp/repo",
                logger=mock_logger,
            )

        assert success is False
        assert "invalid model" in output
        mock_logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_timeout(self, mock_logger: Mock) -> None:
        """Test CLI timeout."""
        with patch(
            "asyncio.to_thread",
            new_callable=AsyncMock,
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=300),
        ):
            success, output = await call_ai_cli(
                prompt="test",
                ai_provider="claude",
                ai_model="sonnet",
                cwd="/tmp/repo",
                logger=mock_logger,
                timeout_minutes=5,
            )

        assert success is False
        assert "timed out" in output
        mock_logger.exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_binary_not_found(self, mock_logger: Mock) -> None:
        """Test CLI binary not found."""
        with patch(
            "asyncio.to_thread",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError(),
        ):
            success, output = await call_ai_cli(
                prompt="test",
                ai_provider="claude",
                ai_model="sonnet",
                cwd="/tmp/repo",
                logger=mock_logger,
            )

        assert success is False
        assert "not found" in output
        mock_logger.exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_response(self, mock_logger: Mock) -> None:
        """Test CLI returning empty response."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_result):
            success, output = await call_ai_cli(
                prompt="test",
                ai_provider="gemini",
                ai_model="gemini-2.5-pro",
                cwd="/tmp/repo",
                logger=mock_logger,
            )

        assert success is False
        assert "empty response" in output

    @pytest.mark.asyncio
    async def test_cancelled_error_reraised(self, mock_logger: Mock) -> None:
        """Test that CancelledError is re-raised, not caught."""
        with patch("asyncio.to_thread", new_callable=AsyncMock, side_effect=asyncio.CancelledError()):
            with pytest.raises(asyncio.CancelledError):
                await call_ai_cli(
                    prompt="test", ai_provider="claude", ai_model="sonnet", cwd="/tmp/repo", logger=mock_logger
                )

    @pytest.mark.asyncio
    async def test_unexpected_exception(self, mock_logger: Mock) -> None:
        """Test that unexpected exceptions return failure tuple."""
        with patch("asyncio.to_thread", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            success, output = await call_ai_cli(
                prompt="test", ai_provider="claude", ai_model="sonnet", cwd="/tmp/repo", logger=mock_logger
            )
        assert success is False
        assert "Unexpected error" in output
        mock_logger.exception.assert_called_once()

    def test_provider_configs(self) -> None:
        """Test provider configurations are correct."""
        assert "claude" in PROVIDER_CONFIG
        assert "gemini" in PROVIDER_CONFIG
        assert "cursor" in PROVIDER_CONFIG

        assert PROVIDER_CONFIG["claude"].binary == "claude"
        assert PROVIDER_CONFIG["gemini"].binary == "gemini"
        assert PROVIDER_CONFIG["cursor"].binary == "agent"

    def test_claude_command(self) -> None:
        """Test Claude command construction."""
        config = PROVIDER_CONFIG["claude"]
        cmd = config.build_cmd(config.binary, "sonnet", "/tmp/repo")
        assert cmd == ["claude", "--model", "sonnet", "--dangerously-skip-permissions", "-p"]

    def test_gemini_command(self) -> None:
        """Test Gemini command construction."""
        config = PROVIDER_CONFIG["gemini"]
        cmd = config.build_cmd(config.binary, "gemini-2.5-pro", "/tmp/repo")
        assert cmd == ["gemini", "--model", "gemini-2.5-pro", "--yolo"]

    def test_cursor_command(self) -> None:
        """Test Cursor command construction with cwd."""
        config = PROVIDER_CONFIG["cursor"]
        cmd = config.build_cmd(config.binary, "cursor-model", "/tmp/repo")
        assert cmd == ["agent", "--force", "--model", "cursor-model", "--print", "--workspace", "/tmp/repo"]

    @pytest.mark.asyncio
    async def test_cwd_passed_to_subprocess(self, mock_logger: Mock) -> None:
        """Test that cwd is passed to subprocess.run for non-cursor providers."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "feat: suggestion"
        mock_result.stderr = ""

        with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_result) as mock_to_thread:
            await call_ai_cli(
                prompt="test",
                ai_provider="claude",
                ai_model="sonnet",
                cwd="/tmp/test-repo",
                logger=mock_logger,
            )

            # Verify subprocess.run was called with cwd
            call_kwargs = mock_to_thread.call_args[1]
            assert call_kwargs.get("cwd") == "/tmp/test-repo"

    @pytest.mark.asyncio
    async def test_cursor_cwd_not_in_subprocess(self, mock_logger: Mock) -> None:
        """Test that cursor provider does not pass cwd to subprocess (uses --workspace instead)."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "feat: suggestion"
        mock_result.stderr = ""

        with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_result) as mock_to_thread:
            await call_ai_cli(
                prompt="test",
                ai_provider="cursor",
                ai_model="cursor-model",
                cwd="/tmp/test-repo",
                logger=mock_logger,
            )

            # Verify subprocess.run was called without cwd (None) for cursor
            call_kwargs = mock_to_thread.call_args[1]
            assert call_kwargs.get("cwd") is None


class TestGetAiConfig:
    """Test suite for get_ai_config function."""

    def test_get_ai_config_returns_tuple(self) -> None:
        """Test that valid config returns (provider, model) tuple."""
        result = get_ai_config({"ai-provider": "claude", "ai-model": "sonnet"})
        assert result == ("claude", "sonnet")

    def test_get_ai_config_returns_none_for_none(self) -> None:
        """Test that None config returns None."""
        assert get_ai_config(None) is None

    def test_get_ai_config_returns_none_for_empty_dict(self) -> None:
        """Test that empty dict returns None."""
        assert get_ai_config({}) is None

    def test_get_ai_config_partial_missing_model(self) -> None:
        """Test partial config with missing ai-model returns None."""
        assert get_ai_config({"ai-provider": "claude"}) is None

    def test_get_ai_config_partial_missing_provider(self) -> None:
        """Test partial config with missing ai-provider returns None."""
        assert get_ai_config({"ai-model": "sonnet"}) is None
