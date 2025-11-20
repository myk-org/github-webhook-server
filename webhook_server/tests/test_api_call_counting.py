import logging
from unittest.mock import MagicMock, patch

import pytest
from github import Github
from starlette.datastructures import Headers

from webhook_server.libs.github_api import CountingRequester, GithubWebhook


def test_counting_requester():
    mock_requester = MagicMock()
    mock_requester.requestJsonAndCheck.return_value = ("header", "data")

    wrapper = CountingRequester(mock_requester)

    # Initial state
    assert wrapper.count == 0

    # Make a call
    wrapper.requestJsonAndCheck("GET", "/foo")
    assert wrapper.count == 1
    mock_requester.requestJsonAndCheck.assert_called_once_with("GET", "/foo")

    # Access non-request attribute
    _ = wrapper.other_method
    # Count should not increase
    assert wrapper.count == 1

    # Make another call
    wrapper.requestMultipartAndCheck("POST", "/bar")
    assert wrapper.count == 2


@pytest.mark.asyncio
async def test_github_webhook_token_metrics_with_counter():
    # Mock dependencies
    mock_logger = MagicMock(spec=logging.Logger)
    mock_hook_data = {"repository": {"name": "test-repo", "full_name": "owner/test-repo"}}
    mock_headers = Headers({"X-GitHub-Delivery": "123", "X-GitHub-Event": "push"})

    with (
        patch("webhook_server.libs.github_api.Config") as MockConfig,
        patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api,
        patch("webhook_server.libs.github_api.get_github_repo_api"),
        patch("webhook_server.libs.github_api.get_repository_github_app_api"),
        patch("webhook_server.libs.github_api.prepare_log_prefix"),
        # Patch this method to avoid calls to get_apis_and_tokes_from_config which isn't mocked
        patch("webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users"),
    ):
        # Setup Config
        mock_config = MockConfig.return_value
        mock_config.repository_data = {"some": "data"}

        # Setup Github API mock
        mock_github = MagicMock(spec=Github)
        mock_requester = MagicMock()
        # PyGithub uses name mangling for private attributes
        mock_github._Github__requester = mock_requester

        # Mock rate limit
        mock_rate_limit = MagicMock()
        mock_rate_limit.rate.remaining = 5000
        mock_github.get_rate_limit.return_value = mock_rate_limit

        mock_get_api.return_value = (mock_github, "token123", "user")

        # Initialize webhook
        webhook = GithubWebhook(mock_hook_data, mock_headers, mock_logger)

        # Verify requester was wrapped
        assert isinstance(webhook.requester_wrapper, CountingRequester)
        assert webhook.github_api._Github__requester == webhook.requester_wrapper

        # Simulate API calls
        webhook.github_api._Github__requester.requestJsonAndCheck("GET", "/foo")
        webhook.github_api._Github__requester.requestJsonAndCheck("GET", "/bar")

        assert webhook.requester_wrapper.count == 2

        # Check metrics output
        # Mock final rate limit to be lower (simulating usage by others too)
        final_rate_limit = MagicMock()
        final_rate_limit.rate.remaining = 4000  # Dropped by 1000 globally

        with patch.object(webhook.github_api, "get_rate_limit", return_value=final_rate_limit):
            metrics = await webhook._get_token_metrics()

        # Expect metrics to show 2 calls (our local usage), not 1000 (global usage)
        assert "2 API calls" in metrics
        assert "initial: 5000" in metrics
        # When using wrapper, we don't show "final" anymore, but we show "remaining"
        # remaining = initial - spend = 5000 - 2 = 4998
        assert "remaining: 4998" in metrics
