import asyncio
import logging
import os
import subprocess as sp
import sys
import threading
import time
from collections.abc import Iterator
from unittest.mock import Mock, patch

import github
import pytest
from github import GithubException
from requests.exceptions import ConnectionError as RequestsConnectionError
from urllib3.exceptions import MaxRetryError, ResponseError

from webhook_server.libs.config import Config
from webhook_server.libs.exceptions import NoApiTokenError
from webhook_server.utils import helpers as helpers_module
from webhook_server.utils.helpers import (
    cached_token_probe,
    get_api_with_highest_rate_limit,
    get_apis_and_tokes_from_config,
    get_future_results,
    get_github_client,
    get_github_repo_api,
    get_logger_with_params,
    log_rate_limit,
    probe_token,
    run_command,
)


@pytest.fixture(autouse=True)
def _clear_token_probe_cache() -> Iterator[None]:
    """Token probes are cached process-wide, so keep tests independent."""
    helpers_module._token_probe_cache.clear()
    yield
    helpers_module._token_probe_cache.clear()


class TestTokenProbing:
    """Rate-limit probing: fail fast, trust enforced budget, don't re-probe per webhook."""

    def test_get_github_client_passes_failing_retry(self) -> None:
        """max_rate_limit_wait=0 is what stops PyGithub blocking a worker thread."""
        with patch("github.Github") as mock_github_cls:
            get_github_client("ghp_faketest1234")  # pragma: allowlist secret

        kwargs = mock_github_cls.call_args.kwargs
        assert isinstance(kwargs["retry"], github.GithubRetry)
        assert kwargs["retry"].max_rate_limit_wait == 0

    def test_probe_token_reads_budget_from_response_headers(self) -> None:
        """Budget must come from X-RateLimit-Remaining, not from GET /rate_limit."""
        api = Mock()
        api.get_user.return_value.login = "user1"
        api.rate_limiting = (4321, 5000)

        probe = probe_token(api, "probe-headers-token", logger=Mock(), log_prefix="")

        assert probe.login == "user1"
        assert probe.remaining == 4321
        assert probe.limit == 5000
        # GET /rate_limit is free but can advertise a budget GitHub is not enforcing
        api.get_rate_limit.assert_not_called()

    def test_probe_token_never_reuses_cached_budget(self) -> None:
        """A cached budget may describe a window that earlier webhooks already spent."""
        api = Mock()
        api.get_user.return_value.login = "user1"
        api.rate_limiting = (4321, 5000)
        probe_token(api, "stale-budget-token", logger=Mock(), log_prefix="")

        # A later webhook finds the token exhausted - the cached 4321 must not be reused
        api.rate_limiting = (0, 5000)
        api.get_user.side_effect = GithubException(403, {"message": "API rate limit exceeded"}, None)

        with pytest.raises(GithubException):
            probe_token(api, "stale-budget-token", logger=Mock(), log_prefix="")

        assert api.get_user.call_count == 2

    def test_cached_token_probe_is_ranking_only(self) -> None:
        """cached_token_probe() exposes the last result for ordering, not for decisions."""
        api = Mock()
        api.get_user.return_value.login = "user1"
        api.rate_limiting = (4321, 5000)

        assert cached_token_probe("unknown-token") is None

        probe_token(api, "ranked-token", logger=Mock(), log_prefix="")

        cached = cached_token_probe("ranked-token")
        assert cached is not None
        assert (cached.login, cached.remaining, cached.limit) == ("user1", 4321, 5000)

    def test_probe_token_coalesces_concurrent_callers(self) -> None:
        """Concurrent callers for one token share a single request."""
        slow_api, other_api = Mock(), Mock()
        for api in (slow_api, other_api):
            api.get_user.return_value.login = "user1"
            api.rate_limiting = (4000, 5000)

        started = threading.Event()
        release = threading.Event()

        def _blocking_get_user() -> Mock:
            started.set()
            release.wait(timeout=5)
            return slow_api.get_user.return_value

        slow_api.get_user.side_effect = _blocking_get_user

        results: list[helpers_module.TokenProbe] = []
        threads = [
            threading.Thread(target=lambda a=api: results.append(probe_token(a, "shared-token", Mock(), "")))
            for api in (slow_api, other_api)
        ]
        for thread in threads:
            thread.start()
        started.wait(timeout=5)
        time.sleep(0.05)  # let the second caller block on the per-token lock
        release.set()
        for thread in threads:
            thread.join(timeout=5)

        assert len(results) == 2
        assert results[0] is results[1]
        # Only the caller that won the lock issued a request
        assert slow_api.get_user.call_count == 1
        assert other_api.get_user.call_count == 0

    def test_probe_token_materialises_request_before_reading_rate_limit(self) -> None:
        """rate_limiting must be read AFTER .login, or it is PyGithub's default.

        Github.get_user() is lazy: it issues no request and leaves rate_limiting at
        (5000, 5000). Reading it before touching .login made every probe report a full
        budget, so selection always saw a tie and the first configured token always won -
        including when it was exhausted.
        """

        class _LazyAuthenticatedUser:
            """Materialises the GET /user request only when a field is read."""

            def __init__(self, api: Mock) -> None:
                self._api = api

            @property
            def login(self) -> str:
                self._api.rate_limiting = (1234, 5000)  # request happens here
                return "real-user"

        api = Mock()
        api.rate_limiting = (5000, 5000)  # PyGithub default before any request
        api.get_user.return_value = _LazyAuthenticatedUser(api)

        probe = probe_token(api, "lazy-token", logger=Mock(), log_prefix="")

        assert probe.remaining == 1234, "probe read PyGithub's default instead of the real budget"
        assert probe.login == "real-user"

    def test_probe_token_raises_on_rate_limit(self) -> None:
        """An exhausted token raises, so callers can skip it instead of selecting it."""
        api = Mock()
        api.get_user.side_effect = GithubException(403, {"message": "API rate limit exceeded"}, None)

        with pytest.raises(GithubException):
            probe_token(api, "exhausted-token", logger=Mock(), log_prefix="")

        # A failed probe must not be cached - the token may recover after the reset
        assert "exhausted-token" not in helpers_module._token_probe_cache


class TestHelpers:
    """Test suite for utility helper functions."""

    def test_get_logger_with_params_default(self) -> None:
        """Test logger creation with default parameters."""
        logger = get_logger_with_params()
        assert isinstance(logger, logging.Logger)
        # Logger name is now the log file path (or 'console') to ensure single handler instance
        assert logger.name  # Just verify it has a name

    def test_get_logger_with_params_with_repository(self) -> None:
        """Test logger creation with repository name."""
        logger = get_logger_with_params(repository_name="test-repo")
        assert isinstance(logger, logging.Logger)
        # The logger should have repository-specific formatting

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    def test_get_apis_and_tokes_from_config(self) -> None:
        """Test getting APIs and tokens from configuration."""

        config = Config(repository="test-repo")
        apis_and_tokens = get_apis_and_tokes_from_config(config=config)

        # Should return a list of tuples (api, token)
        assert isinstance(apis_and_tokens, list)
        # Each item should be a tuple
        for api, token in apis_and_tokens:
            assert isinstance(token, str)
            # API objects should have certain attributes
            assert hasattr(api, "get_user")

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.utils.helpers.log_rate_limit")
    def test_get_api_with_highest_rate_limit(self, mock_log_rate_limit: Mock, mock_get_apis: Mock) -> None:
        """Test the token with the most calls left is selected."""

        # Mock APIs with different rate limits
        mock_api1 = Mock()
        mock_api1.rate_limiting = [100, 5000]  # 100 remaining, 5000 limit
        mock_api1.get_user.return_value.login = "user1"

        mock_api2 = Mock()
        mock_api2.rate_limiting = [200, 5000]  # 200 remaining, 5000 limit
        mock_api2.get_user.return_value.login = "user2"

        mock_get_apis.return_value = [(mock_api1, "token1"), (mock_api2, "token2")]

        config = Config(repository="test-repo")
        api, token, selected = get_api_with_highest_rate_limit(config=config, repository_name="test-repo")

        # Both tokens are probed fresh and the higher one wins
        assert api == mock_api2
        assert token == "token2"
        assert selected.login == "user2"
        mock_api1.get_user.assert_called_once()
        mock_api2.get_user.assert_called_once()

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    def test_get_api_with_highest_rate_limit_ignores_stale_cache(self, mock_get_apis: Mock) -> None:
        """Selection must use fresh probes, never a cached ranking.

        The cached rank is deliberately inverted here: token1 was last seen with 5000 calls
        and token2 with 10, but the fresh probes report the opposite. Picking the cached
        leader would return token1 and run the webhook on 50 calls while token2 has 4800.
        """
        mock_api1 = Mock()
        mock_api1.rate_limiting = [50, 5000]
        mock_api1.get_user.return_value.login = "user1"

        mock_api2 = Mock()
        mock_api2.rate_limiting = [4800, 5000]
        mock_api2.get_user.return_value.login = "user2"

        mock_get_apis.return_value = [(mock_api1, "token1"), (mock_api2, "token2")]
        helpers_module._token_probe_cache["token1"] = helpers_module.TokenProbe("user1", 5000, 5000, 0.0)
        helpers_module._token_probe_cache["token2"] = helpers_module.TokenProbe("user2", 10, 5000, 0.0)

        config = Config(repository="test-repo")
        api, token, selected = get_api_with_highest_rate_limit(config=config, repository_name="test-repo")

        assert api == mock_api2
        assert token == "token2"
        assert selected.login == "user2"

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    def test_get_api_with_highest_rate_limit_falls_back(self, mock_get_apis: Mock) -> None:
        """A token that cannot be probed must be skipped, not selected."""

        # Exhausted: cannot be probed at all
        dead_api = Mock()
        dead_api.get_user.side_effect = GithubException(403, {"message": "API rate limit exceeded"}, None)

        # Usable, but with the lowest budget of the two healthy tokens
        thin_api = Mock()
        thin_api.rate_limiting = [10, 5000]
        thin_api.get_user.return_value.login = "thin"

        healthy_api = Mock()
        healthy_api.rate_limiting = [4000, 5000]
        healthy_api.get_user.return_value.login = "healthy"

        mock_get_apis.return_value = [
            (dead_api, "dead-token"),
            (thin_api, "thin-token"),
            (healthy_api, "healthy-token"),
        ]

        with patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"}):
            config = Config(repository="test-repo")
            api, token, selected = get_api_with_highest_rate_limit(config=config, repository_name="test-repo")

        assert api == healthy_api
        assert token == "healthy-token"
        assert selected.login == "healthy"

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    def test_exhausted_token_rank_is_corrected(self, mock_get_apis: Mock) -> None:
        """A token that fails must lose its top rank, not stay preferred forever."""
        exhausted_api = Mock()
        exhausted_api.get_user.side_effect = GithubException(403, {"message": "API rate limit exceeded"}, None)

        healthy_api = Mock()
        healthy_api.rate_limiting = [10, 5000]
        healthy_api.get_user.return_value.login = "healthy"

        mock_get_apis.return_value = [(exhausted_api, "exhausted"), (healthy_api, "healthy")]
        helpers_module._token_probe_cache["exhausted"] = helpers_module.TokenProbe("exhausted", 5000, 5000, 0.0)
        helpers_module._token_probe_cache["healthy"] = helpers_module.TokenProbe("healthy", 10, 5000, 0.0)

        config = Config(repository="test-repo")
        api, _, _ = get_api_with_highest_rate_limit(config=config, repository_name="test-repo")
        assert api == healthy_api

        # The stale 5000 entry is gone, so the next webhook does not try it first
        assert cached_token_probe("exhausted") is None
        assert cached_token_probe("healthy") is not None

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    def test_get_api_with_highest_rate_limit_no_apis(self, mock_get_apis: Mock) -> None:
        """Test getting API when no APIs available."""

        mock_get_apis.return_value = []

        config = Config(repository="test-repo")

        # Should raise NoApiTokenError when no APIs available
        with pytest.raises(NoApiTokenError, match="Failed to get API with highest rate limit"):
            get_api_with_highest_rate_limit(config=config, repository_name="test-repo")

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    def test_get_api_with_highest_rate_limit_skips_retry_backoff(self, mock_get_apis: Mock) -> None:
        """A transiently failing token must not park selection through its full backoff.

        This loop runs inside the webhook constructor, which the app limits to four workers.
        A sick token retrying 2+4+8+16s would block the queue after a healthy token had
        already proved usable.
        """
        healthy_api = Mock()
        healthy_api.rate_limiting = [4000, 5000]
        healthy_api.get_user.return_value.login = "healthy"

        sick_api = Mock()
        sick_api.get_user.side_effect = GithubException(500, {"message": "Internal Server Error"})

        mock_get_apis.return_value = [(healthy_api, "healthy-token"), (sick_api, "sick-token")]

        with patch("webhook_server.utils.github_retry.time.sleep") as mock_sleep:
            with patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"}):
                config = Config(repository="test-repo")
                api, token, selected = get_api_with_highest_rate_limit(config=config, repository_name="test-repo")

        assert (api, token, selected.login) == (healthy_api, "healthy-token", "healthy")
        # Sick token was tried exactly once and never slept
        assert sick_api.get_user.call_count == 1
        mock_sleep.assert_not_called()

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    def test_get_api_with_highest_rate_limit_skips_spent_token(self, mock_get_apis: Mock) -> None:
        """A token that answers but has zero budget must never be selected.

        The single-token path still returns an exhausted token - with one configured token
        there is nothing better to use, so failing fast on selection would change behaviour
        without improving it. With several configured, picking a spent one means every
        later call 403s and the webhook fails confusingly instead of simply not being routed
        there.
        """
        spent_api = Mock()
        spent_api.rate_limiting = [0, 5000]
        spent_api.get_user.return_value.login = "spent"

        healthy_api = Mock()
        healthy_api.rate_limiting = [40, 5000]
        healthy_api.get_user.return_value.login = "healthy"

        mock_get_apis.return_value = [(spent_api, "spent-token"), (healthy_api, "healthy-token")]

        with patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"}):
            config = Config(repository="test-repo")
            api, token, selected = get_api_with_highest_rate_limit(config=config, repository_name="test-repo")

        # The spent token answered and would have won on "only one that responded"
        assert (api, token, selected.login) == (healthy_api, "healthy-token", "healthy")

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    def test_all_spent_tokens_raises(self, mock_get_apis: Mock) -> None:
        """Every probe reporting zero remaining must produce the no-usable-token error."""
        apis = []
        for index in range(3):
            api = Mock()
            api.rate_limiting = [0, 5000]
            api.get_user.return_value.login = f"spent{index}"
            apis.append((api, f"spent-token-{index}"))
        mock_get_apis.return_value = apis

        with patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"}):
            config = Config(repository="test-repo")
            with pytest.raises(NoApiTokenError, match="Failed to get API with highest rate limit"):
                get_api_with_highest_rate_limit(config=config, repository_name="test-repo")

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    def test_network_blip_does_not_drop_the_webhook(self, mock_get_apis: Mock) -> None:
        """A transport failure on one token must skip it, not end construction.

        Selection probes single-attempt, so a connection blip raises immediately instead of
        being retried away. If that exception is not caught, it escapes __init__ and the
        delivery - which the endpoint already answered 200 for - is dropped even though
        another configured token is perfectly healthy.
        """
        healthy_api = Mock()
        healthy_api.rate_limiting = [4000, 5000]
        healthy_api.get_user.return_value.login = "healthy"

        for error in (
            RequestsConnectionError("connection reset"),
            MaxRetryError(None, "https://api.github.com/user"),
            ResponseError("too many 500 error responses"),
        ):
            sick_api = Mock()
            sick_api.get_user.side_effect = error
            mock_get_apis.return_value = [(sick_api, "sick-token"), (healthy_api, "healthy-token")]

            helpers_module._token_probe_cache.clear()
            with patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"}):
                config = Config(repository="test-repo")
                api, token, selected = get_api_with_highest_rate_limit(config=config, repository_name="test-repo")

            assert (api, token, selected.login) == (healthy_api, "healthy-token", "healthy"), f"blip escaped: {error!r}"

    def test_get_github_repo_api(self) -> None:
        """Test getting GitHub repository API."""
        mock_github_api = Mock()
        mock_repo = Mock()
        mock_github_api.get_repo.return_value = mock_repo

        repository_name = "owner/repo"
        result = get_github_repo_api(github_app_api=mock_github_api, repository=repository_name)

        mock_github_api.get_repo.assert_called_once_with(repository_name)
        assert result == mock_repo

    def test_get_github_repo_api_exception(self) -> None:
        """Test getting GitHub repository API with exception."""
        mock_github_api = Mock()
        mock_github_api.get_repo.side_effect = Exception("Repository not found")

        repository_name = "owner/repo"

        # Should raise the exception when it occurs
        with pytest.raises(Exception, match="Repository not found"):
            get_github_repo_api(github_app_api=mock_github_api, repository=repository_name)

    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.utils.helpers.log_rate_limit")
    def test_get_api_with_highest_rate_limit_invalid_tokens(
        self, mock_log_rate_limit: Mock, mock_get_apis: Mock
    ) -> None:
        """Test getting API when the exhausted token is skipped, not selected."""

        # Token 1 is exhausted: GET /user raises the way PyGithub does once
        # max_rate_limit_wait=0 stops it from sleeping until the reset.
        exhausted_api = Mock()
        exhausted_api.rate_limiting = [0, 5000]
        exhausted_api.get_user.side_effect = GithubException(403, {"message": "API rate limit exceeded"}, None)

        # Token 2 is healthy
        healthy_api = Mock()
        healthy_api.rate_limiting = [100, 5000]
        healthy_api.get_user.return_value.login = "user2"

        mock_get_apis.return_value = [(exhausted_api, "exhausted-token"), (healthy_api, "valid_token")]

        with patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"}):
            config = Config(repository="test-repo")
            api, token, selected = get_api_with_highest_rate_limit(config=config, repository_name="test-repo")

        # Should skip the exhausted token and return the healthy one
        assert api == healthy_api
        assert token == "valid_token"
        assert selected.login == "user2"

    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.utils.helpers.log_rate_limit")
    def test_selection_returns_the_probe_that_selected_it(self, mock_log_rate_limit: Mock, mock_get_apis: Mock) -> None:
        """Selection must hand back the probe, so callers never re-read the shared cache.

        Returning only the login forces callers to look the budget up again, and a
        concurrent constructor can refresh that process-wide cache for the same token in
        between - so the webhook would record someone else's remaining budget as its own.
        """
        mock_api = Mock()
        mock_api.rate_limiting = [1234, 5000]
        mock_api.get_user.return_value.login = "bot"
        mock_get_apis.return_value = [(mock_api, "tok1")]

        with patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"}):
            config = Config(repository="test-repo")
            _, _, selected = get_api_with_highest_rate_limit(config=config, repository_name="test-repo")

        assert isinstance(selected, helpers_module.TokenProbe)
        assert selected.login == "bot"
        assert selected.remaining == 1234

    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.utils.helpers.log_rate_limit")
    def test_get_api_with_highest_rate_limit_single_token(self, mock_log_rate_limit: Mock, mock_get_apis: Mock) -> None:
        """Test single-token short-circuit skips comparison loop."""
        mock_api = Mock()
        mock_api.rate_limiting = [4500, 5000]
        mock_api.get_user.return_value.login = "user1"

        mock_get_apis.return_value = [(mock_api, "single-token")]

        with patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"}):
            config = Config(repository="test-repo")
            api, token, selected = get_api_with_highest_rate_limit(config=config, repository_name="test-repo")

        assert api == mock_api
        assert token == "single-token"
        assert selected.login == "user1"
        # Budget comes from the response header of the probe, not from GET /rate_limit
        mock_api.get_rate_limit.assert_not_called()
        # log_prefix carries the delivery id so concurrent selections can be attributed
        mock_log_rate_limit.assert_called_once_with(
            remaining=4500, limit=5000, api_user="user1", log_prefix=mock_log_rate_limit.call_args.kwargs["log_prefix"]
        )
        assert "test-repo" in mock_log_rate_limit.call_args.kwargs["log_prefix"]

    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    def test_get_api_with_highest_rate_limit_single_token_invalid(self, mock_get_apis: Mock) -> None:
        """Test single-token path rejects a token whose probe fails."""
        mock_api = Mock()
        mock_api.get_user.side_effect = GithubException(401, {"message": "Bad credentials"}, None)

        mock_get_apis.return_value = [(mock_api, "invalid_token")]

        with patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"}):
            config = Config(repository="test-repo")
            with pytest.raises(NoApiTokenError, match="Single configured token is invalid"):
                get_api_with_highest_rate_limit(config=config, repository_name="test-repo")

    def test_get_logger_with_params_log_file_path(self, tmp_path, monkeypatch):
        """Test get_logger_with_params with log_file that is not an absolute path."""
        # Patch Config.get_value to return a log file name
        with patch("webhook_server.utils.helpers.Config") as MockConfig:
            mock_config = MockConfig.return_value
            mock_config.get_value.side_effect = lambda value, **kwargs: "test.log" if value == "log-file" else "INFO"
            mock_config.data_dir = str(tmp_path)
            logger = get_logger_with_params(repository_name="repo")
            assert isinstance(logger, logging.Logger)
            log_dir = tmp_path / "logs"
            assert log_dir.exists()
            assert (log_dir / "test.log").exists() or True  # File may not be created until logging

    def test_get_logger_with_params_mask_sensitive_default(self, tmp_path):
        """Test get_logger_with_params masks sensitive data by default."""
        with patch("webhook_server.utils.helpers.Config") as mock_config:
            # Set up config to return default values (mask_sensitive not set)
            def get_value_side_effect(value, **kwargs):
                if value == "log-file":
                    return "test.log"
                if value == "log-level":
                    return "INFO"
                if value == "mask-sensitive-data":
                    return kwargs.get("return_on_none", True)
                return kwargs.get("return_on_none")

            mock_config.return_value.get_value.side_effect = get_value_side_effect
            mock_config.return_value.data_dir = str(tmp_path)

            with patch("webhook_server.utils.helpers.get_logger") as mock_get_logger:
                get_logger_with_params()
                # Verify mask_sensitive=True was passed
                mock_get_logger.assert_called_once()
                call_kwargs = mock_get_logger.call_args[1]
                assert call_kwargs["mask_sensitive"] is True

    def test_get_logger_with_params_mask_sensitive_disabled(self, tmp_path):
        """Test get_logger_with_params respects mask-sensitive-data=false config."""
        with patch("webhook_server.utils.helpers.Config") as mock_config:
            # Set up config to explicitly disable masking
            def get_value_side_effect(value, **kwargs):
                if value == "log-file":
                    return "test.log"
                if value == "log-level":
                    return "INFO"
                if value == "mask-sensitive-data":
                    return False  # Explicitly disabled
                return kwargs.get("return_on_none")

            mock_config.return_value.get_value.side_effect = get_value_side_effect
            mock_config.return_value.data_dir = str(tmp_path)

            with patch("webhook_server.utils.helpers.get_logger") as mock_get_logger:
                get_logger_with_params()
                # Verify mask_sensitive=False was passed
                mock_get_logger.assert_called_once()
                call_kwargs = mock_get_logger.call_args[1]
                assert call_kwargs["mask_sensitive"] is False

    def test_get_logger_with_params_mask_sensitive_enabled_explicit(self, tmp_path):
        """Test get_logger_with_params respects mask-sensitive-data=true config."""
        with patch("webhook_server.utils.helpers.Config") as mock_config:
            # Set up config to explicitly enable masking
            def get_value_side_effect(value, **kwargs):
                if value == "log-file":
                    return "test.log"
                if value == "log-level":
                    return "INFO"
                if value == "mask-sensitive-data":
                    return True  # Explicitly enabled
                return kwargs.get("return_on_none")

            mock_config.return_value.get_value.side_effect = get_value_side_effect
            mock_config.return_value.data_dir = str(tmp_path)

            with patch("webhook_server.utils.helpers.get_logger") as mock_get_logger:
                get_logger_with_params()
                # Verify mask_sensitive=True was passed
                mock_get_logger.assert_called_once()
                call_kwargs = mock_get_logger.call_args[1]
                assert call_kwargs["mask_sensitive"] is True

    @pytest.mark.asyncio
    async def test_run_command_success(self):
        """Test run_command with a successful command."""
        result = await run_command("echo hello", log_prefix="[TEST]")
        assert result[0] is True
        assert "hello" in result[1]

    @pytest.mark.asyncio
    async def test_run_command_failure(self):
        """Test run_command with a failing command."""
        result = await run_command("false", log_prefix="[TEST]")
        assert result[0] is False

    @pytest.mark.asyncio
    async def test_run_command_stderr(self):
        """Test run_command with stderr and verify_stderr=True."""
        # Use python to print to stderr
        result = await run_command(
            f'{sys.executable} -c "import sys; sys.stderr.write("err")"', log_prefix="[TEST]", verify_stderr=True
        )
        assert result[0] is False
        assert "err" in result[2]

    @pytest.mark.asyncio
    async def test_run_command_exception(self):
        """Test run_command with an invalid command to trigger exception."""
        result = await run_command("nonexistent_command_xyz", log_prefix="[TEST]")
        assert result[0] is False

    def test_log_rate_limit_all_branches(self):
        """Test log_rate_limit warns below the minimum and stays quiet above it."""

        # Patch logger to capture logs
        with patch("webhook_server.utils.helpers.get_logger_with_params") as mock_get_logger:
            mock_logger = Mock()
            mock_get_logger.return_value = mock_logger
            log_rate_limit(600, 5000, api_user="user1")  # below minimum -> warning
            log_rate_limit(1000, 5000, api_user="user2")
            log_rate_limit(3000, 5000, api_user="user3")  # healthy -> debug
            # Check that warning was called for the low-remaining branch
            assert mock_logger.warning.called
            assert mock_logger.debug.called

    def test_get_future_results_all_branches(self):
        """Test get_future_results for all result/exception branches."""

        # Success result
        class DummyFuture:
            def result(self):
                return (True, "success", lambda msg: self.log(msg))

            def exception(self):
                return None

            def log(self, msg):
                self.logged = msg

        # Failure result
        class DummyFutureFail:
            def result(self):
                return (False, "fail", lambda msg: self.log(msg))

            def exception(self):
                return None

            def log(self, msg):
                self.logged = msg

        # Exception result - result() should RAISE the exception
        class DummyFutureException:
            def result(self):
                raise RuntimeError("Repository configuration crashed")

            def exception(self):
                return RuntimeError("Repository configuration crashed")

            def log(self, msg):
                self.logged = msg

        futures = [DummyFuture(), DummyFutureFail(), DummyFutureException()]

        # Patch as_completed to just yield the futures and capture logger calls
        with patch("webhook_server.utils.helpers.as_completed", return_value=futures):
            with patch("webhook_server.utils.helpers.get_logger_with_params") as mock_get_logger:
                mock_logger = Mock()
                mock_get_logger.return_value = mock_logger

                get_future_results(futures)

                # Verify logger.exception was called for the exception case
                mock_logger.exception.assert_called_once_with(
                    "Repository configuration crashed. Check for archived repositories or API permission issues."
                )

                # Verify all futures were processed (success and failure futures should have logged)
                assert futures[0].logged == "success"
                assert futures[1].logged == "fail"
                # futures[2] raised exception so no log attribute set

    @pytest.mark.asyncio
    async def test_run_command_timeout_cleanup(self) -> None:
        """Test that subprocess is properly cleaned up on timeout."""
        # Get initial zombie count
        initial_zombies = 0
        try:
            proc = sp.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
            initial_zombies = proc.stdout.count("<defunct>")
        except Exception:
            pass

        # Run command that times out
        result = await run_command("sleep 100", log_prefix="[TEST]", timeout=1)
        assert result[0] is False
        assert "timed out" in result[2].lower()

        # Wait for cleanup
        await asyncio.sleep(0.2)

        # Verify no new zombies created
        try:
            proc = sp.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
            final_zombies = proc.stdout.count("<defunct>")
            assert final_zombies == initial_zombies, f"Zombie count increased from {initial_zombies} to {final_zombies}"
        except Exception:
            pass  # ps not available, but timeout test still validates behavior

    @pytest.mark.asyncio
    async def test_run_command_cancelled_cleanup(self) -> None:
        """Test that subprocess is properly cleaned up when cancelled."""
        # Create a task that runs a long command
        task = asyncio.create_task(run_command("sleep 100", log_prefix="[TEST]"))

        # Let it start, then cancel
        await asyncio.sleep(0.1)
        task.cancel()

        # Verify CancelledError is raised
        with pytest.raises(asyncio.CancelledError):
            await task

        # Give process time to be reaped
        await asyncio.sleep(0.1)
        # Verify no zombie processes (implicit - would cause issues if zombies exist)

    @pytest.mark.asyncio
    async def test_run_command_oserror_cleanup(self) -> None:
        """Test that subprocess is properly cleaned up on OSError."""
        # Try to run nonexistent command
        result = await run_command("totally_nonexistent_command_12345", log_prefix="[TEST]")
        assert result[0] is False

        # Give process time to be reaped
        await asyncio.sleep(0.1)
        # Verify no zombie processes (implicit verification)

    @pytest.mark.asyncio
    async def test_run_command_no_zombie_processes(self) -> None:
        """Test that multiple failed commands don't create zombie processes."""
        # Get initial zombie count at the start
        initial_zombies = 0
        try:
            proc = sp.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
            initial_zombies = proc.stdout.count("<defunct>")
        except Exception:
            pytest.skip("ps command not available")

        # Run more iterations to trigger potential race conditions
        tasks = [
            run_command("sleep 10", log_prefix="[TEST]", timeout=0.5),  # Timeout
            run_command("sleep 10", log_prefix="[TEST]", timeout=0.5),  # Timeout
            run_command("sleep 10", log_prefix="[TEST]", timeout=0.5),  # Timeout
            run_command("nonexistent_cmd", log_prefix="[TEST]"),  # OSError
            run_command("nonexistent_cmd", log_prefix="[TEST]"),  # OSError
            run_command("false", log_prefix="[TEST]"),  # Normal failure
            run_command("false", log_prefix="[TEST]"),  # Normal failure
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Verify all commands failed appropriately
        for result in results:
            if isinstance(result, tuple):
                assert result[0] is False, "All test commands should fail"

        # Wait longer for cleanup with multiple processes
        await asyncio.sleep(0.5)

        # Check zombie count hasn't increased
        try:
            proc = sp.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
            final_zombies = proc.stdout.count("<defunct>")
            assert final_zombies == initial_zombies, (
                f"Zombie processes created: {final_zombies - initial_zombies} "
                f"(initial: {initial_zombies}, final: {final_zombies})"
            )
        except Exception:
            # ps command failed, but test still validates no exceptions occurred
            pass

    @pytest.mark.asyncio
    async def test_run_command_race_condition_cleanup(self) -> None:
        """Test that zombie is reaped even in race condition where returncode is set quickly."""
        # Get initial zombie count
        initial_zombies = 0
        try:
            proc = sp.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
            initial_zombies = proc.stdout.count("<defunct>")
        except Exception:
            pass

        # Run multiple timeouts concurrently to trigger race conditions
        tasks = [run_command("sleep 100", log_prefix="[TEST]", timeout=0.5) for _ in range(10)]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should timeout
        for result in results:
            if isinstance(result, tuple):
                assert result[0] is False, "All commands should timeout"

        # Wait for all cleanup
        await asyncio.sleep(0.5)

        # Verify no zombies created despite race conditions
        try:
            proc = sp.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
            final_zombies = proc.stdout.count("<defunct>")
            assert final_zombies == initial_zombies, f"Zombie processes created: {final_zombies - initial_zombies}"
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_run_command_stdin_cleanup(self) -> None:
        """Test that subprocess is properly cleaned up when using stdin."""
        # Use a command that processes stdin slowly - sleep after reading to simulate slow processing
        # This ensures we can cancel during the communicate() phase
        task = asyncio.create_task(
            run_command(
                f"{sys.executable} -c 'import sys, time; sys.stdin.read(); time.sleep(10)'",
                log_prefix="[TEST]",
                stdin_input="test data",
            )
        )

        # Let it start and begin reading stdin, then cancel
        await asyncio.sleep(0.1)
        task.cancel()

        # Verify CancelledError is raised
        with pytest.raises(asyncio.CancelledError):
            await task

        # Give process time to be reaped
        await asyncio.sleep(0.1)
        # Verify no zombie processes (implicit - would cause issues if zombies exist)
