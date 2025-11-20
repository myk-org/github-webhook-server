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


class TestGithubWebhookCoverage:
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
        assert gh.initial_wrapper_count == 0

    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    def test_init_reuses_wrapper(
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
        # Already wrapped
        existing_wrapper = CountingRequester(Mock())
        existing_wrapper.count = 5
        mock_github_api._Github__requester = existing_wrapper

        mock_get_api.return_value = (mock_github_api, "token", "apiuser")

        mock_get_repo_api.return_value = Mock()
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"

        gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

        # Verify wrapper was reused
        assert gh.requester_wrapper == existing_wrapper
        assert gh.initial_wrapper_count == 5

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
    async def test_get_token_metrics_with_reused_wrapper(
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

        # Reused wrapper started at 5
        existing_wrapper = CountingRequester(Mock())
        existing_wrapper.count = 5
        mock_github_api._Github__requester = existing_wrapper

        mock_get_api.return_value = (mock_github_api, "token", "apiuser")
        mock_get_repo_api.return_value = Mock()
        mock_get_app_api.return_value = Mock()

        gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

        # Initial wrapper count should be 5
        assert gh.initial_wrapper_count == 5

        # Simulate usage (+3 calls)
        gh.requester_wrapper.count = 8

        metrics = await gh._get_token_metrics()

        # Should show 3 calls (8 - 5)
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
        # No wrapper (simulate failure to wrap)
        del mock_github_api._Github__requester

        mock_get_api.return_value = (mock_github_api, "token", "apiuser")
        mock_get_repo_api.return_value = Mock()
        mock_get_app_api.return_value = Mock()

        gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

        # Manually unset wrapper to test fallback
        gh.requester_wrapper = None

        # Mock reset happened (final > initial)
        mock_github_api.get_rate_limit.return_value.rate.remaining = 5000

        metrics = await gh._get_token_metrics()

        assert "rate limit reset occurred" in metrics
        assert "initial: 100" in metrics
        assert "final: 5000" in metrics

    def test_del_safe_missing_attr(self, minimal_hook_data, minimal_headers, logger):
        # Create instance but don't init fully to avoid setting attributes
        gh = GithubWebhook.__new__(GithubWebhook)
        # Should not raise AttributeError
        gh.__del__()
