"""Tests for GithubWebhook.get_api_users.

Regression coverage for the rate-limit backoff issue: an exhausted token must be skipped
instead of being reported as a usable API user, and the per-token probe must go through the
cached helper rather than issuing a fresh GET /user on every webhook.
"""

from unittest.mock import Mock, patch

import pytest
from github import GithubException

from webhook_server.libs.github_api import GithubWebhook
from webhook_server.utils import helpers as helpers_module


@pytest.fixture(autouse=True)
def _clear_token_probe_cache():
    """Token probes are cached process-wide, so keep tests independent."""
    helpers_module._token_probe_cache.clear()
    yield
    helpers_module._token_probe_cache.clear()


@pytest.mark.asyncio
async def test_get_api_users_returns_logins() -> None:
    """Healthy tokens yield their login, sourced from the cached probe."""
    hook = Mock(spec=GithubWebhook)
    hook.logger = Mock()
    hook.log_prefix = ""
    hook.config = Mock()

    with (
        patch("webhook_server.libs.github_api.get_apis_and_tokes_from_config") as mock_get_apis,
        patch(
            "webhook_server.libs.github_api.probe_token",
            side_effect=[
                helpers_module.TokenProbe("user1", 100, 5000, 0.0),
                helpers_module.TokenProbe("user2", 50, 5000, 0.0),
            ],
        ) as mock_probe,
    ):
        mock_get_apis.return_value = [(Mock(), "t1"), (Mock(), "t2")]

        users = await GithubWebhook.get_api_users(hook)

    assert users == ["user1", "user2"]
    assert mock_probe.call_count == 2


@pytest.mark.asyncio
async def test_get_api_users_skips_exhausted_token() -> None:
    """A rate-limited token yields None rather than stalling or being reported as usable."""
    hook = Mock(spec=GithubWebhook)
    hook.logger = Mock()
    hook.log_prefix = ""
    hook.config = Mock()

    with (
        patch("webhook_server.libs.github_api.get_apis_and_tokes_from_config") as mock_get_apis,
        patch("webhook_server.libs.github_api.probe_token") as mock_probe,
    ):
        mock_get_apis.return_value = [(Mock(), "exhausted"), (Mock(), "healthy")]
        mock_probe.side_effect = [
            GithubException(403, {"message": "API rate limit exceeded"}, None),
            helpers_module.TokenProbe("healthy-user", 4000, 5000, 0.0),
        ]

        users = await GithubWebhook.get_api_users(hook)

    assert users == [None, "healthy-user"]
    hook.logger.exception.assert_called_once()
    # token is masked to its last 4 chars, never logged in full
    logged = hook.logger.exception.call_args.args[0]
    assert "...sted" in logged
    assert "API rate limit exceeded" in logged
