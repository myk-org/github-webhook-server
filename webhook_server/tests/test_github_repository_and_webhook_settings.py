"""Tests for webhook_server.utils.github_repository_and_webhook_settings module."""

from concurrent.futures import Future
from unittest.mock import AsyncMock, Mock, patch

import pytest

from webhook_server.utils.github_repository_and_webhook_settings import (
    get_repository_api,
    repository_and_webhook_settings,
)


class TestGetRepositoryApi:
    """Test suite for get_repository_api function."""

    @patch("webhook_server.utils.github_repository_and_webhook_settings.get_api_with_highest_rate_limit")
    @patch("webhook_server.utils.github_repository_and_webhook_settings.Config")
    def test_get_repository_api_success(self, mock_config_class: Mock, mock_get_api: Mock) -> None:
        """Test successful repository API retrieval."""
        # Mock config instance
        mock_config = Mock()
        mock_config_class.return_value = mock_config

        # Mock GitHub API and user
        mock_github_api = Mock()
        mock_get_api.return_value = (mock_github_api, None, "test-user")

        # Call function
        repository, github_api, api_user = get_repository_api("test-repo")

        # Verify results
        assert repository == "test-repo"
        assert github_api == mock_github_api
        assert api_user == "test-user"

        # Verify Config was created correctly
        mock_config_class.assert_called_once_with(
            repository="test-repo", logger=mock_config_class.call_args[1]["logger"]
        )

        # Verify get_api_with_highest_rate_limit was called
        mock_get_api.assert_called_once_with(config=mock_config, repository_name="test-repo")

    @patch("webhook_server.utils.github_repository_and_webhook_settings.get_api_with_highest_rate_limit")
    @patch("webhook_server.utils.github_repository_and_webhook_settings.Config")
    def test_get_repository_api_no_github_api(self, mock_config_class: Mock, mock_get_api: Mock) -> None:
        """Test repository API retrieval when GitHub API is None."""
        mock_config = Mock()
        mock_config_class.return_value = mock_config

        # Mock no GitHub API available
        mock_get_api.return_value = (None, None, "test-user")

        repository, github_api, api_user = get_repository_api("test-repo")

        assert repository == "test-repo"
        assert github_api is None
        assert api_user == "test-user"


class TestRepositoryAndWebhookSettings:
    """Test suite for repository_and_webhook_settings function."""

    @pytest.fixture
    def mock_config(self) -> Mock:
        """Mock Config object for testing."""
        config = Mock()
        config.root_data = {"repositories": {"repo1": {"name": "owner/repo1"}, "repo2": {"name": "owner/repo2"}}}
        return config

    @patch("webhook_server.utils.github_repository_and_webhook_settings.create_webhook")
    @patch("webhook_server.utils.github_repository_and_webhook_settings.set_all_in_progress_check_runs_to_queued")
    @patch("webhook_server.utils.github_repository_and_webhook_settings.set_repositories_settings")
    @patch("webhook_server.utils.github_repository_and_webhook_settings.ThreadPoolExecutor")
    @patch("webhook_server.utils.github_repository_and_webhook_settings.Config")
    @pytest.mark.asyncio
    async def test_repository_and_webhook_settings_success(
        self,
        mock_config_class: Mock,
        mock_thread_pool: Mock,
        mock_set_repos: AsyncMock,
        mock_set_check_runs: Mock,
        mock_create_webhook: Mock,
        mock_config: Mock,
    ) -> None:
        """Test successful execution of repository and webhook settings."""
        # Mock Config
        mock_config_class.return_value = mock_config

        # Mock ThreadPoolExecutor
        mock_executor = Mock()
        mock_thread_pool.return_value.__enter__.return_value = mock_executor

        # Mock futures for each repository
        future1 = Mock(spec=Future)
        future1.result.return_value = ("repo1", Mock(), "user1")
        future2 = Mock(spec=Future)
        future2.result.return_value = ("repo2", Mock(), "user2")

        mock_executor.submit.side_effect = [future1, future2]

        # Mock as_completed to return our futures
        with patch("webhook_server.utils.github_repository_and_webhook_settings.as_completed") as mock_as_completed:
            mock_as_completed.return_value = [future1, future2]

            # Call the function
            await repository_and_webhook_settings()

        # Verify Config was created
        mock_config_class.assert_called_once()

        # Verify ThreadPoolExecutor was used
        mock_thread_pool.assert_called_once()

        # Verify repository API calls were submitted
        assert mock_executor.submit.call_count == 2

        # Verify all final functions were called
        mock_set_repos.assert_called_once()
        mock_set_check_runs.assert_called_once()
        mock_create_webhook.assert_called_once()

    @patch("webhook_server.utils.github_repository_and_webhook_settings.create_webhook")
    @patch("webhook_server.utils.github_repository_and_webhook_settings.set_all_in_progress_check_runs_to_queued")
    @patch("webhook_server.utils.github_repository_and_webhook_settings.set_repositories_settings")
    @patch("webhook_server.utils.github_repository_and_webhook_settings.ThreadPoolExecutor")
    @patch("webhook_server.utils.github_repository_and_webhook_settings.Config")
    @pytest.mark.asyncio
    async def test_repository_and_webhook_settings_with_secret(
        self,
        mock_config_class: Mock,
        mock_thread_pool: Mock,
        mock_set_repos: AsyncMock,
        mock_set_check_runs: Mock,
        mock_create_webhook: Mock,
        mock_config: Mock,
    ) -> None:
        """Test repository and webhook settings with webhook secret."""
        mock_config_class.return_value = mock_config

        mock_executor = Mock()
        mock_thread_pool.return_value.__enter__.return_value = mock_executor

        future1 = Mock(spec=Future)
        future1.result.return_value = ("repo1", Mock(), "user1")
        mock_executor.submit.return_value = future1

        with patch("webhook_server.utils.github_repository_and_webhook_settings.as_completed") as mock_as_completed:
            mock_as_completed.return_value = [future1]

            await repository_and_webhook_settings(webhook_secret="test-secret")  # pragma: allowlist secret

        # Verify webhook was created with secret
        mock_create_webhook.assert_called_once()
        create_webhook_call = mock_create_webhook.call_args
        assert create_webhook_call[1]["secret"] == "test-secret"  # pragma: allowlist secret
