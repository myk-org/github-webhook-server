"""Tests for GithubWebhook.get_api_users.

Regression coverage for the rate-limit backoff issue: an exhausted token must be skipped
instead of being reported as a usable API user, and every login must come from a current
validation rather than a cached probe.
"""

from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import Mock, patch

import pytest
from github import GithubException

from webhook_server.libs.github_api import GithubWebhook
from webhook_server.utils import helpers as helpers_module


@pytest.fixture(autouse=True)
def _clear_token_probe_cache() -> Iterator[None]:
    """Token probes are cached process-wide, so keep tests independent."""
    helpers_module._token_probe_cache.clear()
    yield
    helpers_module._token_probe_cache.clear()


async def _inline_to_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a to_thread target inline so mocks never reach a real worker thread."""
    return fn(*args, **kwargs)


@pytest.mark.asyncio
async def test_get_api_users_returns_logins() -> None:
    """Healthy tokens yield their login, sourced from a fresh probe."""
    hook = Mock(spec=GithubWebhook)
    hook.logger = Mock()
    hook.log_prefix = ""
    hook.config = Mock()

    with (
        patch("asyncio.to_thread", new=_inline_to_thread),
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
        patch("asyncio.to_thread", new=_inline_to_thread),
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


@pytest.mark.asyncio
async def test_get_api_users_never_reuses_cached_login() -> None:
    """A cached login must not stand in for a current token validation.

    These logins feed the auto-verified and trusted-committer lists, so a token revoked
    since the last probe has to drop out rather than keep granting merge access.
    """
    hook = Mock(spec=GithubWebhook)
    hook.logger = Mock()
    hook.log_prefix = ""
    hook.config = Mock()

    api = Mock()
    # Stale cache entry: this token was valid when it was written.
    helpers_module._token_probe_cache["revoked-token"] = helpers_module.TokenProbe("revoked-user", 5000, 5000, 0.0)
    api.get_user.side_effect = GithubException(401, {"message": "Bad credentials"}, None)

    with (
        patch("asyncio.to_thread", new=_inline_to_thread),
        patch("webhook_server.libs.github_api.get_apis_and_tokes_from_config", return_value=[(api, "revoked-token")]),
    ):
        users = await GithubWebhook.get_api_users(hook)

    assert users == [None]
    # The stale entry is not handed back as a trusted identity
    assert users[0] != "revoked-user"
    api.get_user.assert_called_once()
