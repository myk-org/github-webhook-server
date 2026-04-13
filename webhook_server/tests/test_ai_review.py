"""Tests for webhook_server.libs.ai_review module."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx
import pytest

from webhook_server.libs.ai_review import DEFAULT_TRIGGERS, call_ai_reviewer
from webhook_server.utils.constants import AI_REVIEW_STR


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
    return mock


@pytest.fixture
def mock_check_run_handler() -> AsyncMock:
    """Create a mock CheckRunHandler."""
    mock = AsyncMock()
    mock.set_check_in_progress = AsyncMock()
    mock.set_check_success = AsyncMock()
    mock.set_check_failure = AsyncMock()
    return mock


class TestCallAiReviewer:
    @pytest.mark.asyncio
    async def test_returns_early_when_config_is_none(
        self, mock_pull_request: Mock, mock_check_run_handler: AsyncMock
    ) -> None:
        mock_webhook = Mock()
        mock_webhook.ai_review_config = None
        await call_ai_reviewer(mock_webhook, mock_pull_request, mock_check_run_handler)
        mock_check_run_handler.set_check_in_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_early_when_trigger_not_configured(
        self, mock_github_webhook: Mock, mock_pull_request: Mock, mock_check_run_handler: AsyncMock
    ) -> None:
        mock_github_webhook.ai_review_config["triggers"] = ["pr-opened"]
        await call_ai_reviewer(
            mock_github_webhook, mock_pull_request, mock_check_run_handler, trigger="pr-synchronized"
        )
        mock_github_webhook.logger.debug.assert_called_once()
        mock_check_run_handler.set_check_in_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_command_trigger_always_runs(
        self, mock_github_webhook: Mock, mock_pull_request: Mock, mock_check_run_handler: AsyncMock
    ) -> None:
        """When trigger is None (command-triggered), always run regardless of configured triggers."""
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {"status": "healthy"}

        review_response = Mock()
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
                await call_ai_reviewer(mock_github_webhook, mock_pull_request, mock_check_run_handler, trigger=None)

            mock_client.post.assert_called_once()
        mock_check_run_handler.set_check_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_check_failure_sets_check_failure(
        self, mock_github_webhook: Mock, mock_pull_request: Mock, mock_check_run_handler: AsyncMock
    ) -> None:
        with patch("webhook_server.libs.ai_review.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_client_cls.return_value = mock_client

            await call_ai_reviewer(mock_github_webhook, mock_pull_request, mock_check_run_handler, trigger="pr-opened")

        mock_check_run_handler.set_check_in_progress.assert_called_once()
        mock_check_run_handler.set_check_failure.assert_called_once()
        call_args = mock_check_run_handler.set_check_failure.call_args
        assert call_args.kwargs["name"] == AI_REVIEW_STR
        assert "not responding" in call_args.kwargs["output"]["summary"]

    @pytest.mark.asyncio
    async def test_successful_review(
        self, mock_github_webhook: Mock, mock_pull_request: Mock, mock_check_run_handler: AsyncMock
    ) -> None:
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
                await call_ai_reviewer(
                    mock_github_webhook, mock_pull_request, mock_check_run_handler, trigger="pr-opened"
                )

        mock_check_run_handler.set_check_success.assert_called_once()
        call_args = mock_check_run_handler.set_check_success.call_args
        assert "1 comment(s) posted" in call_args.kwargs["output"]["summary"]

    @pytest.mark.asyncio
    async def test_successful_review_no_issues(
        self, mock_github_webhook: Mock, mock_pull_request: Mock, mock_check_run_handler: AsyncMock
    ) -> None:
        health_response = Mock()
        health_response.raise_for_status = Mock()

        review_response = Mock()
        review_response.raise_for_status = Mock()
        review_response.json.return_value = {
            "review_posted": False,
            "comments": [],
            "summary": "No issues found",
        }

        with patch("webhook_server.libs.ai_review.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=health_response)
            mock_client.post = AsyncMock(return_value=review_response)
            mock_client_cls.return_value = mock_client

            with patch("asyncio.to_thread", side_effect=lambda f, *a, **kw: f(*a, **kw) if callable(f) else f):
                await call_ai_reviewer(
                    mock_github_webhook, mock_pull_request, mock_check_run_handler, trigger="pr-opened"
                )

        mock_check_run_handler.set_check_success.assert_called_once()
        call_args = mock_check_run_handler.set_check_success.call_args
        assert "No issues found" in call_args.kwargs["output"]["summary"]

    @pytest.mark.asyncio
    async def test_review_http_error_sets_check_failure(
        self, mock_github_webhook: Mock, mock_pull_request: Mock, mock_check_run_handler: AsyncMock
    ) -> None:
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
                await call_ai_reviewer(
                    mock_github_webhook, mock_pull_request, mock_check_run_handler, trigger="pr-opened"
                )

        mock_check_run_handler.set_check_failure.assert_called_once()
        call_args = mock_check_run_handler.set_check_failure.call_args
        assert "failed" in call_args.kwargs["output"]["summary"].lower()

    @pytest.mark.asyncio
    async def test_review_invalid_json_sets_check_failure(
        self, mock_github_webhook: Mock, mock_pull_request: Mock, mock_check_run_handler: AsyncMock
    ) -> None:
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
                await call_ai_reviewer(
                    mock_github_webhook, mock_pull_request, mock_check_run_handler, trigger="pr-opened"
                )

        mock_check_run_handler.set_check_failure.assert_called_once()
        call_args = mock_check_run_handler.set_check_failure.call_args
        assert "Invalid JSON" in call_args.kwargs["output"]["summary"]

    @pytest.mark.asyncio
    async def test_unexpected_exception_sets_check_failure(
        self, mock_github_webhook: Mock, mock_pull_request: Mock, mock_check_run_handler: AsyncMock
    ) -> None:
        with patch("webhook_server.libs.ai_review.httpx.AsyncClient", side_effect=RuntimeError("boom")):
            await call_ai_reviewer(mock_github_webhook, mock_pull_request, mock_check_run_handler, trigger="pr-opened")
        mock_github_webhook.logger.exception.assert_called_once()
        mock_check_run_handler.set_check_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancelled_error_reraised(
        self, mock_github_webhook: Mock, mock_pull_request: Mock, mock_check_run_handler: AsyncMock
    ) -> None:
        with patch("webhook_server.libs.ai_review.httpx.AsyncClient", side_effect=asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await call_ai_reviewer(
                    mock_github_webhook, mock_pull_request, mock_check_run_handler, trigger="pr-opened"
                )

    @pytest.mark.asyncio
    async def test_provider_config_conversion(
        self, mock_github_webhook: Mock, mock_pull_request: Mock, mock_check_run_handler: AsyncMock
    ) -> None:
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
                await call_ai_reviewer(
                    mock_github_webhook, mock_pull_request, mock_check_run_handler, trigger="pr-opened"
                )

            call_args = mock_client.post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert payload["providers"] == [
                {"ai_provider": "claude", "ai_model": "sonnet"},
                {"ai_provider": "gemini", "ai_model": "pro"},
            ]


class TestDefaultTriggers:
    def test_default_triggers(self) -> None:
        assert DEFAULT_TRIGGERS == ["pr-opened", "pr-synchronized"]
