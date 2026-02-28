"""Tests for webhook_server.libs.test_oracle module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from webhook_server.libs.test_oracle import call_test_oracle
from webhook_server.tests.conftest import TEST_GITHUB_TOKEN


class TestCallTestOracle:
    """Test suite for call_test_oracle function."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook with test-oracle config."""
        mock_webhook = Mock()
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.token = TEST_GITHUB_TOKEN
        mock_webhook.config = Mock()
        mock_webhook.config.get_value = Mock(
            return_value={
                "server-url": "http://localhost:8000",
                "ai-provider": "claude",
                "ai-model": "sonnet",
                "test-patterns": ["tests/**/*.py"],
                "triggers": ["approved"],
            }
        )
        return mock_webhook

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        """Create a mock PullRequest."""
        mock_pr = Mock()
        mock_pr.html_url = "https://github.com/test-org/test-repo/pull/42"
        return mock_pr

    @pytest.mark.asyncio
    async def test_not_configured_skips_silently(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """Test that call_test_oracle skips silently when not configured."""
        mock_github_webhook.config.get_value = Mock(return_value=None)

        with patch("webhook_server.libs.test_oracle.httpx.AsyncClient") as mock_client_cls:
            await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)
            mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_health_check_failure_posts_comment(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """Test that health check failure posts a PR comment and returns."""
        with patch("webhook_server.libs.test_oracle.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")

            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)

                mock_to_thread.assert_called_once()
                call_args = mock_to_thread.call_args
                assert call_args[0][0] == mock_pull_request.create_issue_comment
                assert "not responding" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_health_check_non_200_posts_comment(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """Test that health check returning non-200 posts a PR comment."""
        with patch("webhook_server.libs.test_oracle.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_response = Mock(status_code=503)
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Server Error", request=Mock(), response=Mock(status_code=503)
            )
            mock_client.get.return_value = mock_response

            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)

                mock_to_thread.assert_called_once()
                call_args = mock_to_thread.call_args
                assert call_args[0][0] == mock_pull_request.create_issue_comment
                assert "not responding" in call_args[0][1]
                assert "(status 503)" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_successful_analyze_call(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """Test successful health check + analyze call."""
        with patch("webhook_server.libs.test_oracle.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_client.get.return_value = Mock(status_code=200)

            mock_analyze_response = Mock()
            mock_analyze_response.status_code = 200
            mock_analyze_response.json.return_value = {
                "pr_url": "https://github.com/test-org/test-repo/pull/42",
                "summary": "2 test files recommended",
                "review_posted": True,
            }
            mock_client.post.return_value = mock_analyze_response

            await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)

            mock_client.get.assert_called_once_with("/health", timeout=5.0)

            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args[0][0] == "/analyze"
            payload = call_args[1]["json"]
            assert payload["pr_url"] == "https://github.com/test-org/test-repo/pull/42"
            assert payload["ai_provider"] == "claude"
            assert payload["ai_model"] == "sonnet"
            assert payload["github_token"] == TEST_GITHUB_TOKEN
            assert payload["test_patterns"] == ["tests/**/*.py"]

    @pytest.mark.asyncio
    async def test_analyze_error_logs_only(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """Test that analyze errors are logged but no PR comment is posted."""
        with patch("webhook_server.libs.test_oracle.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_health = Mock()
            mock_health.raise_for_status = Mock()
            mock_client.get.return_value = mock_health

            mock_analyze_response = Mock(status_code=500, text="Internal Server Error")
            mock_analyze_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Server Error", request=Mock(), response=Mock(status_code=500, text="Internal Server Error")
            )
            mock_client.post.return_value = mock_analyze_response

            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)

                mock_to_thread.assert_not_called()
                mock_github_webhook.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_analyze_network_error_logs_only(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """Test that network errors during analyze are logged but no PR comment is posted."""
        with patch("webhook_server.libs.test_oracle.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_client.get.return_value = Mock(status_code=200)
            mock_client.post.side_effect = httpx.ConnectError("Connection lost")

            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)

                mock_to_thread.assert_not_called()
                mock_github_webhook.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_no_test_patterns_in_config(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """Test that test_patterns is omitted from payload when not in config."""
        mock_github_webhook.config.get_value = Mock(
            return_value={
                "server-url": "http://localhost:8000",
                "ai-provider": "claude",
                "ai-model": "sonnet",
            }
        )

        with patch("webhook_server.libs.test_oracle.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_client.get.return_value = Mock(status_code=200)
            mock_client.post.return_value = Mock(
                status_code=200,
                json=Mock(return_value={"summary": "ok", "review_posted": True}),
            )

            await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)

            payload = mock_client.post.call_args[1]["json"]
            assert "test_patterns" not in payload

    @pytest.mark.asyncio
    async def test_trigger_check_approved(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """Test that trigger check works correctly for approved trigger."""
        with patch("webhook_server.libs.test_oracle.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.return_value = Mock(status_code=200)
            mock_client.post.return_value = Mock(
                status_code=200,
                json=Mock(return_value={"summary": "ok", "review_posted": True}),
            )

            await call_test_oracle(
                github_webhook=mock_github_webhook,
                pull_request=mock_pull_request,
                trigger="approved",
            )
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_trigger_not_in_config_skips(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """Test that call is skipped when trigger is not in config triggers list."""
        with patch("webhook_server.libs.test_oracle.httpx.AsyncClient") as mock_client_cls:
            await call_test_oracle(
                github_webhook=mock_github_webhook,
                pull_request=mock_pull_request,
                trigger="pr-opened",
            )
            mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_triggers_when_not_specified(
        self, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test that default trigger is 'approved' when triggers not in config."""
        mock_github_webhook.config.get_value = Mock(
            return_value={
                "server-url": "http://localhost:8000",
                "ai-provider": "claude",
                "ai-model": "sonnet",
            }
        )

        with patch("webhook_server.libs.test_oracle.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.return_value = Mock(status_code=200)
            mock_client.post.return_value = Mock(
                status_code=200,
                json=Mock(return_value={"summary": "ok", "review_posted": True}),
            )

            await call_test_oracle(
                github_webhook=mock_github_webhook,
                pull_request=mock_pull_request,
                trigger="approved",
            )
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_trigger_param_skips_trigger_check(
        self, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test that when trigger param is None (comment command), trigger check is skipped."""
        with patch("webhook_server.libs.test_oracle.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.return_value = Mock(status_code=200)
            mock_client.post.return_value = Mock(
                status_code=200,
                json=Mock(return_value={"summary": "ok", "review_posted": True}),
            )

            await call_test_oracle(
                github_webhook=mock_github_webhook,
                pull_request=mock_pull_request,
            )
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_outer_exception_caught_and_logged(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """Test that unexpected exceptions are caught by the outer try/except."""
        with patch("webhook_server.libs.test_oracle.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.side_effect = RuntimeError("Unexpected failure")

            await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)

            mock_github_webhook.logger.exception.assert_called_once()
            exc_msg = mock_github_webhook.logger.exception.call_args[0][0]
            assert "failed unexpectedly" in exc_msg

    @pytest.mark.asyncio
    async def test_cancelled_error_reraised(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """Test that asyncio.CancelledError is re-raised, not swallowed."""
        with patch("webhook_server.libs.test_oracle.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.side_effect = asyncio.CancelledError()

            with pytest.raises(asyncio.CancelledError):
                await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_health_comment_failure_logged(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """Test that failure to post health check PR comment is logged."""
        with patch("webhook_server.libs.test_oracle.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")

            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                mock_to_thread.side_effect = RuntimeError("GitHub API error")

                await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)

                mock_github_webhook.logger.exception.assert_called_once()
                exc_msg = mock_github_webhook.logger.exception.call_args[0][0]
                assert "Failed to post health check comment" in exc_msg

    @pytest.mark.asyncio
    async def test_analyze_invalid_json_logged(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """Test that invalid JSON response from analyze is logged."""
        with patch("webhook_server.libs.test_oracle.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_client.get.return_value = Mock(status_code=200)

            mock_analyze_response = Mock()
            mock_analyze_response.raise_for_status = Mock()
            mock_analyze_response.json.side_effect = ValueError("Invalid JSON")
            mock_client.post.return_value = mock_analyze_response

            await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)

            mock_github_webhook.logger.error.assert_called_once()
            error_msg = mock_github_webhook.logger.error.call_args[0][0]
            assert "invalid JSON" in error_msg
