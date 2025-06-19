"""Tests for webhook_server.utils.webhook module."""

from concurrent.futures import Future
from typing import Any
from unittest.mock import Mock, patch

import pytest

from webhook_server.utils.webhook import create_webhook, process_github_webhook


class TestProcessGithubWebhook:
    """Test suite for process_github_webhook function."""

    @pytest.fixture
    def sample_data(self) -> dict[str, Any]:
        """Sample repository data for testing."""
        return {"name": "owner/test-repo", "events": ["push", "pull_request"]}

    @pytest.fixture
    def apis_dict(self) -> dict[str, dict[str, Any]]:
        """Sample APIs dictionary for testing."""
        mock_api = Mock()
        return {"test-repo": {"api": mock_api, "user": "test-user"}}

    @pytest.fixture
    def mock_repo(self) -> Mock:
        """Mock GitHub repository object."""
        repo = Mock()
        repo.get_hooks.return_value = []
        repo.create_hook = Mock()
        return repo

    @patch("webhook_server.utils.webhook.get_github_repo_api")
    def test_process_github_webhook_success_no_existing_hooks(
        self,
        mock_get_repo_api: Mock,
        sample_data: dict[str, Any],
        apis_dict: dict[str, dict[str, Any]],
        mock_repo: Mock,
    ) -> None:
        """Test successful webhook creation when no existing hooks exist."""
        mock_get_repo_api.return_value = mock_repo

        success, message, log_func = process_github_webhook(
            repository_name="test-repo", data=sample_data, webhook_ip="http://example.com", apis_dict=apis_dict
        )

        assert success is True
        assert "Create webhook is done" in message
        assert "test-user" in message
        assert "owner/test-repo" in message

        # Verify webhook creation was called
        mock_repo.create_hook.assert_called_once_with(
            name="web",
            config={"url": "http://example.com/webhook_server", "content_type": "json"},
            events=["push", "pull_request"],
            active=True,
        )

    @patch("webhook_server.utils.webhook.get_github_repo_api")
    def test_process_github_webhook_success_with_secret(
        self,
        mock_get_repo_api: Mock,
        sample_data: dict[str, Any],
        apis_dict: dict[str, dict[str, Any]],
        mock_repo: Mock,
    ) -> None:
        """Test successful webhook creation with secret."""
        mock_get_repo_api.return_value = mock_repo

        success, message, log_func = process_github_webhook(
            repository_name="test-repo",
            data=sample_data,
            webhook_ip="http://example.com",
            apis_dict=apis_dict,
            secret="test-secret",  # pragma: allowlist secret
        )

        assert success is True

        # Verify webhook creation was called with secret
        mock_repo.create_hook.assert_called_once_with(
            name="web",
            config={
                "url": "http://example.com/webhook_server",
                "content_type": "json",
                "secret": "test-secret",  # pragma: allowlist secret
            },
            events=["push", "pull_request"],
            active=True,
        )

    @patch("webhook_server.utils.webhook.get_github_repo_api")
    def test_process_github_webhook_default_events(
        self, mock_get_repo_api: Mock, apis_dict: dict[str, dict[str, Any]], mock_repo: Mock
    ) -> None:
        """Test webhook creation with default events when none specified."""
        mock_get_repo_api.return_value = mock_repo
        data_without_events = {"name": "owner/test-repo"}

        success, message, log_func = process_github_webhook(
            repository_name="test-repo", data=data_without_events, webhook_ip="http://example.com", apis_dict=apis_dict
        )

        assert success is True

        # Verify default events are used
        mock_repo.create_hook.assert_called_once_with(
            name="web",
            config={"url": "http://example.com/webhook_server", "content_type": "json"},
            events=["*"],  # Default events
            active=True,
        )

    @patch("webhook_server.utils.webhook.get_github_repo_api")
    def test_process_github_webhook_existing_hook_same_config(
        self,
        mock_get_repo_api: Mock,
        sample_data: dict[str, Any],
        apis_dict: dict[str, dict[str, Any]],
        mock_repo: Mock,
    ) -> None:
        """Test when webhook already exists with same configuration."""
        # Mock existing hook with matching URL
        existing_hook = Mock()
        existing_hook.config = {"url": "http://example.com/webhook_server", "content_type": "json"}
        mock_repo.get_hooks.return_value = [existing_hook]
        mock_get_repo_api.return_value = mock_repo

        success, message, log_func = process_github_webhook(
            repository_name="test-repo", data=sample_data, webhook_ip="http://example.com", apis_dict=apis_dict
        )

        assert success is True
        assert "Hook already exists" in message
        assert "test-user" in message

        # Verify no new hook was created
        mock_repo.create_hook.assert_not_called()
        existing_hook.delete.assert_not_called()

    @patch("webhook_server.utils.webhook.get_github_repo_api")
    def test_process_github_webhook_secret_mismatch_deletes_old_hook(
        self,
        mock_get_repo_api: Mock,
        sample_data: dict[str, Any],
        apis_dict: dict[str, dict[str, Any]],
        mock_repo: Mock,
    ) -> None:
        """Test deletion of old webhook when secret configuration changes."""
        # Mock existing hook without secret, but we're adding a secret
        existing_hook = Mock()
        existing_hook.config = {
            "url": "http://example.com/webhook_server",
            "content_type": "json",
            # No secret in existing hook
        }
        mock_repo.get_hooks.return_value = [existing_hook]
        mock_get_repo_api.return_value = mock_repo

        success, message, log_func = process_github_webhook(
            repository_name="test-repo",
            data=sample_data,
            webhook_ip="http://example.com",
            apis_dict=apis_dict,
            secret="new-secret",  # pragma: allowlist secret
        )

        assert success is True

        # Verify old hook was deleted
        existing_hook.delete.assert_called_once()

        # Verify new hook was created with secret
        mock_repo.create_hook.assert_called_once_with(
            name="web",
            config={
                "url": "http://example.com/webhook_server",
                "content_type": "json",
                "secret": "new-secret",  # pragma: allowlist secret
            },
            events=["push", "pull_request"],
            active=True,
        )

    @patch("webhook_server.utils.webhook.get_github_repo_api")
    def test_process_github_webhook_secret_removal_deletes_old_hook(
        self,
        mock_get_repo_api: Mock,
        sample_data: dict[str, Any],
        apis_dict: dict[str, dict[str, Any]],
        mock_repo: Mock,
    ) -> None:
        """Test deletion of old webhook when secret is removed."""
        # Mock existing hook with secret, but we're removing it
        existing_hook = Mock()
        existing_hook.config = {
            "url": "http://example.com/webhook_server",
            "content_type": "json",
            "secret": "old-secret",  # pragma: allowlist secret
        }
        mock_repo.get_hooks.return_value = [existing_hook]
        mock_get_repo_api.return_value = mock_repo

        success, message, log_func = process_github_webhook(
            repository_name="test-repo",
            data=sample_data,
            webhook_ip="http://example.com",
            apis_dict=apis_dict,
            secret=None,  # Removing secret
        )

        assert success is True

        # Verify old hook was deleted
        existing_hook.delete.assert_called_once()

        # Verify new hook was created without secret
        mock_repo.create_hook.assert_called_once_with(
            name="web",
            config={"url": "http://example.com/webhook_server", "content_type": "json"},
            events=["push", "pull_request"],
            active=True,
        )

    def test_process_github_webhook_missing_api(self, sample_data: dict[str, Any]) -> None:
        """Test error handling when GitHub API is missing."""
        apis_dict_no_api = {
            "test-repo": {
                "user": "test-user"
                # Missing "api" key
            }
        }

        success, message, log_func = process_github_webhook(
            repository_name="test-repo", data=sample_data, webhook_ip="http://example.com", apis_dict=apis_dict_no_api
        )

        assert success is False
        assert "Failed to get github api" in message
        assert "owner/test-repo" in message

    @patch("webhook_server.utils.webhook.get_github_repo_api")
    def test_process_github_webhook_repository_not_found(
        self, mock_get_repo_api: Mock, sample_data: dict[str, Any], apis_dict: dict[str, dict[str, Any]]
    ) -> None:
        """Test error handling when repository cannot be found."""
        mock_get_repo_api.return_value = None

        success, message, log_func = process_github_webhook(
            repository_name="test-repo", data=sample_data, webhook_ip="http://example.com", apis_dict=apis_dict
        )

        assert success is False
        assert "Could not find repository" in message
        assert "owner/test-repo" in message
        assert "test-user" in message

    @patch("webhook_server.utils.webhook.get_github_repo_api")
    def test_process_github_webhook_hooks_listing_error(
        self,
        mock_get_repo_api: Mock,
        sample_data: dict[str, Any],
        apis_dict: dict[str, dict[str, Any]],
        mock_repo: Mock,
    ) -> None:
        """Test error handling when listing hooks fails."""
        mock_repo.get_hooks.side_effect = Exception("Permission denied")
        mock_get_repo_api.return_value = mock_repo

        success, message, log_func = process_github_webhook(
            repository_name="test-repo", data=sample_data, webhook_ip="http://example.com", apis_dict=apis_dict
        )

        assert success is False
        assert "Could not list webhook" in message
        assert "Permission denied" in message
        assert "check token permissions" in message
        assert "test-user" in message

    @patch("webhook_server.utils.webhook.get_github_repo_api")
    def test_process_github_webhook_multiple_existing_hooks(
        self,
        mock_get_repo_api: Mock,
        sample_data: dict[str, Any],
        apis_dict: dict[str, dict[str, Any]],
        mock_repo: Mock,
    ) -> None:
        """Test handling of multiple existing hooks, only matching ones are affected."""
        # Mock multiple existing hooks
        matching_hook = Mock()
        matching_hook.config = {"url": "http://example.com/webhook_server", "content_type": "json"}

        non_matching_hook = Mock()
        non_matching_hook.config = {"url": "http://different.com/webhook_server", "content_type": "json"}

        mock_repo.get_hooks.return_value = [matching_hook, non_matching_hook]
        mock_get_repo_api.return_value = mock_repo

        success, message, log_func = process_github_webhook(
            repository_name="test-repo", data=sample_data, webhook_ip="http://example.com", apis_dict=apis_dict
        )

        assert success is True
        assert "Hook already exists" in message

        # Verify no hooks were deleted or created
        matching_hook.delete.assert_not_called()
        non_matching_hook.delete.assert_not_called()
        mock_repo.create_hook.assert_not_called()


class TestCreateWebhook:
    """Test suite for create_webhook function."""

    @pytest.fixture
    def mock_config(self) -> Mock:
        """Mock Config object for testing."""
        config = Mock()
        config.root_data = {
            "webhook-ip": "http://example.com",
            "repositories": {
                "repo1": {"name": "owner/repo1", "events": ["push"]},
                "repo2": {"name": "owner/repo2", "events": ["pull_request"]},
            },
        }
        return config

    @pytest.fixture
    def apis_dict(self) -> dict[str, dict[str, Any]]:
        """Sample APIs dictionary for testing."""
        return {"repo1": {"api": Mock(), "user": "user1"}, "repo2": {"api": Mock(), "user": "user2"}}

    @patch("webhook_server.utils.webhook.get_future_results")
    @patch("webhook_server.utils.webhook.ThreadPoolExecutor")
    @patch("webhook_server.utils.webhook.process_github_webhook")
    def test_create_webhook_success(
        self,
        mock_process_webhook: Mock,
        mock_thread_pool: Mock,
        mock_get_results: Mock,
        mock_config: Mock,
        apis_dict: dict[str, dict[str, Any]],
    ) -> None:
        """Test successful webhook creation for multiple repositories."""
        # Mock ThreadPoolExecutor and futures
        mock_executor = Mock()
        mock_thread_pool.return_value.__enter__.return_value = mock_executor

        future1 = Mock(spec=Future)
        future2 = Mock(spec=Future)
        mock_executor.submit.side_effect = [future1, future2]

        create_webhook(config=mock_config, apis_dict=apis_dict)

        # Verify ThreadPoolExecutor was used
        mock_thread_pool.assert_called_once()

        # Verify submit was called for each repository
        assert mock_executor.submit.call_count == 2

        # Verify process_github_webhook was referenced (not called directly)
        calls = mock_executor.submit.call_args_list
        assert len(calls) == 2

        # Check first repository call
        first_call = calls[0]
        assert first_call[0][0] == mock_process_webhook
        first_kwargs = first_call[1]
        assert first_kwargs["repository_name"] == "repo1"
        assert first_kwargs["data"]["name"] == "owner/repo1"
        assert first_kwargs["webhook_ip"] == "http://example.com"
        assert first_kwargs["apis_dict"] == apis_dict
        assert first_kwargs["secret"] is None

        # Check second repository call
        second_call = calls[1]
        assert second_call[0][0] == mock_process_webhook
        second_kwargs = second_call[1]
        assert second_kwargs["repository_name"] == "repo2"
        assert second_kwargs["data"]["name"] == "owner/repo2"

        # Verify get_future_results was called with the futures
        mock_get_results.assert_called_once()
        futures_arg = mock_get_results.call_args[1]["futures"]
        assert future1 in futures_arg
        assert future2 in futures_arg

    @patch("webhook_server.utils.webhook.get_future_results")
    @patch("webhook_server.utils.webhook.ThreadPoolExecutor")
    @patch("webhook_server.utils.webhook.process_github_webhook")
    def test_create_webhook_with_secret(
        self,
        mock_process_webhook: Mock,
        mock_thread_pool: Mock,
        mock_get_results: Mock,
        mock_config: Mock,
        apis_dict: dict[str, dict[str, Any]],
    ) -> None:
        """Test webhook creation with secret."""
        mock_executor = Mock()
        mock_thread_pool.return_value.__enter__.return_value = mock_executor
        mock_executor.submit.return_value = Mock(spec=Future)

        create_webhook(config=mock_config, apis_dict=apis_dict, secret="test-secret")  # pragma: allowlist secret

        # Verify secret was passed to all repository calls
        calls = mock_executor.submit.call_args_list
        for call in calls:
            kwargs = call[1]
            assert kwargs["secret"] == "test-secret"  # pragma: allowlist secret

    @patch("webhook_server.utils.webhook.get_future_results")
    @patch("webhook_server.utils.webhook.ThreadPoolExecutor")
    @patch("webhook_server.utils.webhook.process_github_webhook")
    def test_create_webhook_empty_repositories(
        self,
        mock_process_webhook: Mock,
        mock_thread_pool: Mock,
        mock_get_results: Mock,
        apis_dict: dict[str, dict[str, Any]],
    ) -> None:
        """Test webhook creation with no repositories."""
        config = Mock()
        config.root_data = {"webhook-ip": "http://example.com", "repositories": {}}

        mock_executor = Mock()
        mock_thread_pool.return_value.__enter__.return_value = mock_executor

        create_webhook(config=config, apis_dict=apis_dict)

        # Verify no repository processing was attempted
        mock_executor.submit.assert_not_called()

        # Verify get_future_results was still called with empty list
        mock_get_results.assert_called_once()
        futures_arg = mock_get_results.call_args[1]["futures"]
        assert len(futures_arg) == 0

    @patch("webhook_server.utils.webhook.get_future_results")
    @patch("webhook_server.utils.webhook.ThreadPoolExecutor")
    @patch("webhook_server.utils.webhook.process_github_webhook")
    @patch("webhook_server.utils.webhook.LOGGER")
    def test_create_webhook_logging(
        self,
        mock_logger: Mock,
        mock_process_webhook: Mock,
        mock_thread_pool: Mock,
        mock_get_results: Mock,
        mock_config: Mock,
        apis_dict: dict[str, dict[str, Any]],
    ) -> None:
        """Test that proper logging occurs during webhook creation."""
        mock_executor = Mock()
        mock_thread_pool.return_value.__enter__.return_value = mock_executor
        mock_executor.submit.return_value = Mock(spec=Future)

        create_webhook(config=mock_config, apis_dict=apis_dict)

        # Verify initial logging message
        mock_logger.info.assert_called_with("Preparing webhook configuration")
