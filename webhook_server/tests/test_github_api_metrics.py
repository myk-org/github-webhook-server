import threading
from unittest.mock import Mock, patch

import pytest
from starlette.datastructures import Headers

from webhook_server.libs.github_api import CountingRequester, GithubWebhook


class TestCountingRequester:
    def test_counting_requester_init(self):
        requester = Mock()
        wrapper = CountingRequester(requester)
        assert wrapper._requester == requester
        assert wrapper.count == 0
        assert wrapper._shared_count == [0]
        assert isinstance(wrapper._thread_lock, type(threading.Lock()))

    def test_counting_requester_increments(self):
        requester = Mock()
        # Mock a request method on the requester
        requester.requestJsonAndCheck = Mock(return_value="result")

        wrapper = CountingRequester(requester)

        # Call the method through the wrapper
        result = wrapper.requestJsonAndCheck("arg", key="value")

        assert result == "result"
        assert wrapper.count == 1
        requester.requestJsonAndCheck.assert_called_once_with("arg", key="value")

    def test_counting_requester_ignores_non_request_methods(self):
        requester = Mock()
        requester.other_method = Mock(return_value="result")

        wrapper = CountingRequester(requester)

        result = wrapper.other_method()

        assert result == "result"
        assert wrapper.count == 0

    def test_counting_requester_thread_safety(self):
        requester = Mock()
        requester.requestJson = Mock()
        wrapper = CountingRequester(requester)

        def make_requests():
            for _ in range(100):
                wrapper.requestJson()

        threads = []
        for _ in range(10):
            t = threading.Thread(target=make_requests)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert wrapper.count == 1000

    def test_counting_requester_with_lazy_shares_count(self):
        requester = Mock()
        requester.requestJsonAndCheck = Mock(return_value="result")

        # withLazy returns a new mock requester
        lazy_requester = Mock()
        lazy_requester.requestJsonAndCheck = Mock(return_value="lazy_result")
        requester.withLazy = Mock(return_value=lazy_requester)

        wrapper = CountingRequester(requester)

        # Call on original wrapper
        wrapper.requestJsonAndCheck("arg1")
        assert wrapper.count == 1

        # Create lazy version (simulates what PyGithub does in get_repo)
        lazy_wrapper = wrapper.withLazy(True)

        # Call on lazy wrapper
        lazy_wrapper.requestJsonAndCheck("arg2")

        # Both should share the count
        assert wrapper.count == 2
        assert lazy_wrapper.count == 2


class TestGithubWebhookMetrics:
    @pytest.fixture
    def minimal_hook_data(self):
        return {
            "repository": {"name": "test-repo", "full_name": "org/test-repo"},
            "number": 1,
        }

    @pytest.fixture
    def minimal_headers(self):
        return Headers({"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "abc"})

    @pytest.fixture
    def logger(self):
        return Mock()

    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    def test_init_wraps_requester_new(
        self,
        mock_color,
        mock_get_app_api,
        mock_get_repo_api,
        mock_get_api,
        mock_config,
        minimal_hook_data,
        minimal_headers,
        logger,
    ):
        mock_config.return_value.repository = True
        mock_config.return_value.repository_local_data.return_value = {}

        mock_github_api = Mock()
        mock_requester = Mock()
        mock_github_api._Github__requester = mock_requester
        mock_get_api.return_value = (mock_github_api, "token", "apiuser")

        mock_get_repo_api.return_value = Mock()
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"

        gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

        # Verify wrapper was created and set
        assert isinstance(gh.requester_wrapper, CountingRequester)
        assert mock_github_api._Github__requester == gh.requester_wrapper

    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    def test_init_unwraps_existing_wrapper(
        self,
        mock_color,
        mock_get_app_api,
        mock_get_repo_api,
        mock_get_api,
        mock_config,
        minimal_hook_data,
        minimal_headers,
        logger,
    ):
        mock_config.return_value.repository = True
        mock_config.return_value.repository_local_data.return_value = {}

        mock_github_api = Mock()
        # Already wrapped by a previous webhook
        inner_requester = Mock()
        existing_wrapper = CountingRequester(inner_requester)
        existing_wrapper.count = 5
        mock_github_api._Github__requester = existing_wrapper

        mock_get_api.return_value = (mock_github_api, "token", "apiuser")

        mock_get_repo_api.return_value = Mock()
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"

        gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

        # Verify a NEW wrapper was created (not the existing one)
        assert isinstance(gh.requester_wrapper, CountingRequester)
        assert gh.requester_wrapper is not existing_wrapper
        # New wrapper starts at count 0
        assert gh.requester_wrapper.count == 0
        # The new wrapper wraps the inner requester, not the old wrapper
        assert gh.requester_wrapper._requester is inner_requester

    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    def test_concurrent_webhooks_have_independent_counts(
        self,
        mock_color,
        mock_get_app_api,
        mock_get_repo_api,
        mock_get_api,
        mock_config,
        minimal_hook_data,
        minimal_headers,
        logger,
    ):
        """Regression test for #970: two concurrent webhooks must not share counts."""
        mock_config.return_value.repository = True
        mock_config.return_value.repository_local_data.return_value = {}

        # Both webhooks use the same Github instance (same token)
        mock_github_api = Mock()
        mock_requester = Mock()
        mock_requester.requestJsonAndCheck = Mock(return_value="result")
        # withLazy returns a new mock that also has request methods
        lazy_requester = Mock()
        lazy_requester.requestJsonAndCheck = Mock(return_value="lazy_result")
        mock_requester.withLazy = Mock(return_value=lazy_requester)
        mock_github_api._Github__requester = mock_requester
        mock_github_api.get_rate_limit.return_value.rate.remaining = 5000

        mock_get_api.return_value = (mock_github_api, "token", "apiuser")
        mock_get_repo_api.return_value = Mock()
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"

        gh1 = GithubWebhook(minimal_hook_data, minimal_headers, logger)
        gh2 = GithubWebhook(minimal_hook_data, minimal_headers, logger)

        # Each webhook has its own wrapper
        assert gh1.requester_wrapper is not gh2.requester_wrapper

        # Simulate API calls through each webhook's wrapper
        gh1.requester_wrapper.requestJsonAndCheck("a")
        gh1.requester_wrapper.requestJsonAndCheck("b")
        gh2.requester_wrapper.requestJsonAndCheck("c")

        # Counts must be independent
        assert gh1.requester_wrapper.count == 2
        assert gh2.requester_wrapper.count == 1

    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    @pytest.mark.asyncio
    async def test_get_token_metrics_with_wrapper(
        self,
        mock_color,
        mock_get_app_api,
        mock_get_repo_api,
        mock_get_api,
        mock_config,
        minimal_hook_data,
        minimal_headers,
        logger,
    ):
        mock_config.return_value.repository = True
        mock_config.return_value.repository_local_data.return_value = {}

        mock_github_api = Mock()
        mock_github_api.get_rate_limit.return_value.rate.remaining = 5000
        mock_requester = Mock()
        mock_github_api._Github__requester = mock_requester

        mock_get_api.return_value = (mock_github_api, "token", "apiuser")
        mock_get_repo_api.return_value = Mock()
        mock_get_app_api.return_value = Mock()

        gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

        # Simulate usage
        gh.requester_wrapper.count = 10

        metrics = await gh._get_token_metrics()

        assert "10 API calls" in metrics
        assert "initial: 5000" in metrics
        assert "remaining: 4990" in metrics

    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    @pytest.mark.asyncio
    async def test_get_token_metrics_per_webhook_count(
        self,
        mock_color,
        mock_get_app_api,
        mock_get_repo_api,
        mock_get_api,
        mock_config,
        minimal_hook_data,
        minimal_headers,
        logger,
    ):
        mock_config.return_value.repository = True
        mock_config.return_value.repository_local_data.return_value = {}

        mock_github_api = Mock()
        mock_github_api.get_rate_limit.return_value.rate.remaining = 4995

        mock_requester = Mock()
        mock_github_api._Github__requester = mock_requester

        mock_get_api.return_value = (mock_github_api, "token", "apiuser")
        mock_get_repo_api.return_value = Mock()
        mock_get_app_api.return_value = Mock()

        gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

        # New wrapper always starts at 0
        assert gh.requester_wrapper.count == 0

        # Simulate 3 API calls
        gh.requester_wrapper.count = 3

        metrics = await gh._get_token_metrics()

        # Should show 3 calls (count is used directly)
        assert "3 API calls" in metrics
        assert "initial: 4995" in metrics
        assert "remaining: 4992" in metrics

    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    @pytest.mark.asyncio
    async def test_get_token_metrics_fallback_reset(
        self,
        mock_color,
        mock_get_app_api,
        mock_get_repo_api,
        mock_get_api,
        mock_config,
        minimal_hook_data,
        minimal_headers,
        logger,
    ):
        mock_config.return_value.repository = True
        mock_config.return_value.repository_local_data.return_value = {}

        mock_github_api = Mock()
        # Initial rate limit
        mock_github_api.get_rate_limit.return_value.rate.remaining = 100
        mock_requester = Mock()
        mock_github_api._Github__requester = mock_requester

        mock_get_api.return_value = (mock_github_api, "token", "apiuser")
        mock_get_repo_api.return_value = Mock()
        mock_get_app_api.return_value = Mock()

        gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

        # Manually unset wrapper to test the fallback path in _get_token_metrics
        gh.requester_wrapper = None

        # Mock reset happened (final > initial)
        mock_github_api.get_rate_limit.return_value.rate.remaining = 5000

        metrics = await gh._get_token_metrics()

        assert "rate limit reset occurred" in metrics
        assert "initial: 100" in metrics
        assert "final: 5000" in metrics

    def test_del_cleanup_without_clone_dir(self):
        """Test that __del__ handles missing clone_repo_dir gracefully.

        __del__ uses getattr with a default because it can be called during failed
        initialization when clone_repo_dir was never set.
        """
        gh = GithubWebhook.__new__(GithubWebhook)
        # Call destructor directly to make failures deterministic —
        # del gh would silently swallow any exceptions in __del__
        GithubWebhook.__del__(gh)
