"""Tests for webhook_server.libs.ai_review module."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx
import pytest

from webhook_server.libs.ai_review import DEFAULT_TRIGGERS, call_ai_reviewer


@pytest.fixture
def mock_github_webhook() -> Mock:
    """Create a mock GithubWebhook for AI review tests."""
    mock = Mock()
    mock.ai_review_config = {
        "server-url": "http://localhost:8001",
        "providers": [
            {"ai-provider": "claude", "ai-model": "sonnet"},
        ],
        "triggers": ["pr-opened", "pr-synchronized"],
    }
    mock.logger = MagicMock()
    mock.log_prefix = "[TEST]"
    mock.token = "ghp_test_token"  # pragma: allowlist secret
    return mock


@pytest.fixture
def mock_pull_request() -> Mock:
    """Create a mock PullRequest."""
    mock = Mock()
    mock.html_url = "https://github.com/test-org/test-repo/pull/42"
    mock.create_issue_comment = Mock()
    return mock


class TestCallAiReviewer:
    @pytest.mark.asyncio
    async def test_returns_early_when_config_is_none(self, mock_pull_request: Mock) -> None:
        mock_webhook = Mock()
        mock_webhook.ai_review_config = None
        await call_ai_reviewer(mock_webhook, mock_pull_request)
        mock_webhook.logger.debug.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_early_when_trigger_not_configured(
        self, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        mock_github_webhook.ai_review_config["triggers"] = ["pr-opened"]
        await call_ai_reviewer(mock_github_webhook, mock_pull_request, trigger="pr-synchronized")
        mock_github_webhook.logger.debug.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_trigger_always_runs(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """When trigger is None (command-triggered), always run regardless of configured triggers."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {"status": "healthy"}

        review_response = Mock()
        review_response.status_code = 200
        review_response.raise_for_status = Mock()
        review_response.json.return_value = {
            "review_posted": True,
            "comments": [{"path": "a.py", "line": 1, "body": "issue"}],
            "summary": "1 issue found",
        }

        with patch("webhook_server.libs.ai_review.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.post = AsyncMock(return_value=review_response)
            mock_client_cls.return_value = mock_client

            with patch("asyncio.to_thread", side_effect=lambda f, *a, **kw: f(*a, **kw) if callable(f) else f):
                await call_ai_reviewer(mock_github_webhook, mock_pull_request, trigger=None)

            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_check_failure_posts_comment(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        with patch("webhook_server.libs.ai_review.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_client_cls.return_value = mock_client

            with patch("asyncio.to_thread", side_effect=lambda f, *a, **kw: f(*a, **kw) if callable(f) else f):
                await call_ai_reviewer(mock_github_webhook, mock_pull_request, trigger="pr-opened")

            mock_github_webhook.logger.warning.assert_called_once()
            mock_pull_request.create_issue_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_successful_review(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        health_response = Mock()
        health_response.raise_for_status = Mock()

        review_response = Mock()
        review_response.raise_for_status = Mock()
        review_response.json.return_value = {
            "review_posted": True,
            "comments": [{"path": "a.py", "line": 1, "body": "bug"}],
            "summary": "1 issue",
        }

        with patch("webhook_server.libs.ai_review.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=health_response)
            mock_client.post = AsyncMock(return_value=review_response)
            mock_client_cls.return_value = mock_client

            with patch("asyncio.to_thread", side_effect=lambda f, *a, **kw: f(*a, **kw) if callable(f) else f):
                await call_ai_reviewer(mock_github_webhook, mock_pull_request, trigger="pr-opened")

            mock_github_webhook.logger.info.assert_called()

    @pytest.mark.asyncio
    async def test_review_http_error(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        health_response = Mock()
        health_response.raise_for_status = Mock()

        error_response = Mock()
        error_response.status_code = 502
        error_response.text = "Bad Gateway"
        review_error = httpx.HTTPStatusError("error", request=Mock(), response=error_response)

        with patch("webhook_server.libs.ai_review.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=health_response)
            mock_client.post = AsyncMock(side_effect=review_error)
            mock_client_cls.return_value = mock_client

            with patch("asyncio.to_thread", side_effect=lambda f, *a, **kw: f(*a, **kw) if callable(f) else f):
                await call_ai_reviewer(mock_github_webhook, mock_pull_request, trigger="pr-opened")

            mock_github_webhook.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_review_invalid_json(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        health_response = Mock()
        health_response.raise_for_status = Mock()

        review_response = Mock()
        review_response.raise_for_status = Mock()
        review_response.json.side_effect = ValueError("Invalid JSON")

        with patch("webhook_server.libs.ai_review.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=health_response)
            mock_client.post = AsyncMock(return_value=review_response)
            mock_client_cls.return_value = mock_client

            with patch("asyncio.to_thread", side_effect=lambda f, *a, **kw: f(*a, **kw) if callable(f) else f):
                await call_ai_reviewer(mock_github_webhook, mock_pull_request, trigger="pr-opened")

            mock_github_webhook.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_unexpected_exception(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        with patch("webhook_server.libs.ai_review.httpx.AsyncClient", side_effect=RuntimeError("boom")):
            await call_ai_reviewer(mock_github_webhook, mock_pull_request, trigger="pr-opened")
        mock_github_webhook.logger.exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancelled_error_reraised(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        with patch("webhook_server.libs.ai_review.httpx.AsyncClient", side_effect=asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await call_ai_reviewer(mock_github_webhook, mock_pull_request, trigger="pr-opened")

    @pytest.mark.asyncio
    async def test_provider_config_conversion(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """Verify YAML-format providers are converted to API format."""
        mock_github_webhook.ai_review_config["providers"] = [
            {"ai-provider": "claude", "ai-model": "sonnet"},
            {"ai-provider": "gemini", "ai-model": "pro"},
        ]

        health_response = Mock()
        health_response.raise_for_status = Mock()

        review_response = Mock()
        review_response.raise_for_status = Mock()
        review_response.json.return_value = {"review_posted": False, "comments": [], "summary": "ok"}

        with patch("webhook_server.libs.ai_review.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=health_response)
            mock_client.post = AsyncMock(return_value=review_response)
            mock_client_cls.return_value = mock_client

            with patch("asyncio.to_thread", side_effect=lambda f, *a, **kw: f(*a, **kw) if callable(f) else f):
                await call_ai_reviewer(mock_github_webhook, mock_pull_request, trigger="pr-opened")

            # Check the payload sent to the service
            call_args = mock_client.post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert payload["providers"] == [
                {"ai_provider": "claude", "ai_model": "sonnet"},
                {"ai_provider": "gemini", "ai_model": "pro"},
            ]


class TestDefaultTriggers:
    def test_default_triggers(self) -> None:
        assert DEFAULT_TRIGGERS == ["pr-opened", "pr-synchronized"]
