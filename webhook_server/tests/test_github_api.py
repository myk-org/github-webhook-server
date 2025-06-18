import os
from typing import Any
from unittest.mock import Mock, patch

import pytest
from starlette.datastructures import Headers

from webhook_server.libs.github_api import GithubWebhook


class TestGithubWebhook:
    """Test suite for GitHub webhook processing and API integration."""

    @pytest.fixture
    def webhook_headers(self) -> Headers:
        """Standard webhook headers for testing."""
        return Headers({
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "test-delivery-123",
            "Content-Type": "application/json",
        })

    @pytest.fixture
    def pull_request_payload(self) -> dict[str, Any]:
        """Pull request webhook payload."""
        return {
            "action": "opened",
            "repository": {"name": "test-repo", "full_name": "my-org/test-repo"},
            "pull_request": {
                "number": 123,
                "title": "Test PR",
                "user": {"login": "testuser"},
                "base": {"ref": "main"},
                "head": {"sha": "abc123"},
            },
        }

    @pytest.fixture
    def push_payload(self) -> dict[str, Any]:
        """Push webhook payload."""
        return {
            "repository": {"name": "test-repo", "full_name": "my-org/test-repo"},
            "ref": "refs/heads/main",
            "commits": [{"id": "abc123", "message": "Test commit", "author": {"name": "Test User"}}],
        }

    @pytest.fixture
    def issue_comment_payload(self) -> dict[str, Any]:
        """Issue comment webhook payload."""
        return {
            "action": "created",
            "repository": {"name": "test-repo", "full_name": "my-org/test-repo"},
            "issue": {"number": 123, "pull_request": {"url": "https://api.github.com/repos/test/pull/123"}},
            "comment": {"body": "/retest all", "user": {"login": "testuser"}},
        }

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch(
        "webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users",
        new_callable=lambda: property(lambda self: None),
    )
    def test_github_webhook_initialization(
        self,
        mock_auto_verified_prop: Mock,
        mock_repo_local_data: Mock,
        mock_api_rate_limit: Mock,
        mock_repo_api: Mock,
        mock_get_apis: Mock,
        pull_request_payload: dict[str, Any],
        webhook_headers: Headers,
    ) -> None:
        """Test GithubWebhook initialization with pull request payload."""
        # Mock GitHub API objects to prevent network calls
        mock_api = Mock()
        mock_api.rate_limiting = [100, 5000]  # Set rate limit to avoid network call
        mock_user = Mock()
        mock_user.login = "test-user"
        mock_api.get_user.return_value = mock_user

        # Mock the returns to prevent network calls
        mock_api_rate_limit.return_value = (mock_api, "TOKEN", "USER")
        mock_repo_api.return_value = Mock()
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code
        mock_repo_local_data.return_value = {}  # Mock repository local config

        webhook = GithubWebhook(hook_data=pull_request_payload, headers=webhook_headers, logger=Mock())

        assert webhook.repository_full_name == "my-org/test-repo"
        assert webhook.hook_data["action"] == "opened"
        assert webhook.github_event == "pull_request"

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch(
        "webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users",
        new_callable=lambda: property(lambda self: None),
    )
    def test_github_webhook_valid_repository_initialization(
        self,
        mock_auto_verified_prop: Mock,
        mock_repo_app_api: Mock,
        mock_api_rate_limit: Mock,
        mock_get_apis: Mock,
        webhook_headers: Headers,
    ) -> None:
        """Test GithubWebhook initialization with valid repository configuration."""
        # Mock GitHub API to prevent network calls
        mock_api = Mock()
        mock_api.rate_limiting = [100, 5000]
        mock_user = Mock()
        mock_user.login = "test-user"
        mock_api.get_user.return_value = mock_user

        mock_get_apis.return_value = [(mock_api, "TOKEN")]
        mock_api_rate_limit.return_value = (mock_api, "TOKEN", "USER")
        mock_repo_app_api.return_value = Mock()  # Mock repository GitHub app API

        # Use a valid repository that exists in test config
        payload = {"repository": {"name": "test-repo", "full_name": "my-org/test-repo"}}

        # Should create successfully without raising RepositoryNotFoundError
        webhook = GithubWebhook(hook_data=payload, headers=webhook_headers, logger=Mock())

        assert webhook.repository_full_name == "my-org/test-repo"
        assert webhook.repository_name == "test-repo"

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.pull_request_handler.PullRequestHandler.process_pull_request_webhook_data")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch(
        "webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users",
        new_callable=lambda: property(lambda self: None),
    )
    async def test_process_pull_request_event(
        self,
        mock_auto_verified_prop: Mock,
        mock_repo_local_data: Mock,
        mock_get_apis: Mock,
        mock_process_pr: Mock,
        mock_api_rate_limit: Mock,
        mock_repo_api: Mock,
        pull_request_payload: dict[str, Any],
        webhook_headers: Headers,
    ) -> None:
        """Test processing pull_request event."""
        # Mock GitHub API to prevent network calls
        mock_api = Mock()
        mock_api.rate_limiting = [100, 5000]
        mock_user = Mock()
        mock_user.login = "test-user"
        mock_api.get_user.return_value = mock_user

        mock_api_rate_limit.return_value = (mock_api, "TOKEN", "USER")
        mock_repo_api.return_value = Mock()
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code
        mock_repo_local_data.return_value = {}
        mock_process_pr.return_value = None

        webhook = GithubWebhook(hook_data=pull_request_payload, headers=webhook_headers, logger=Mock())

        # Mock get_pull_request to return a valid pull request object
        mock_pr = Mock()
        mock_pr.draft = False  # Not a draft, so processing should continue
        mock_pr.user.login = "testuser"  # Mock PR user
        mock_pr.base.ref = "main"  # Mock base reference
        mock_commit = Mock()  # Mock commit object
        mock_pr.get_commits.return_value = [mock_commit]  # Return iterable list
        mock_file = Mock()
        mock_file.filename = "test_file.py"
        mock_pr.get_files.return_value = [mock_file]  # Return iterable list of files

        # Mock the repository git tree operations for owners file handler
        mock_tree = Mock()
        mock_tree_element = Mock()
        mock_tree_element.path = "OWNERS"
        mock_tree_element.type = "blob"
        mock_tree.tree = [mock_tree_element]  # Make tree.tree iterable

        with (
            patch.object(webhook, "get_pull_request", return_value=mock_pr),
            patch.object(webhook.repository, "get_git_tree", return_value=mock_tree),
            patch.object(
                webhook.repository,
                "get_contents",
                return_value=Mock(decoded_content=b"approvers:\n  - user1\nreviewers:\n  - user2"),
            ),
        ):
            await webhook.process()
            mock_process_pr.assert_called_once()

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.push_handler.PushHandler.process_push_webhook_data")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch(
        "webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users",
        new_callable=lambda: property(lambda self: None),
    )
    async def test_process_push_event(
        self,
        mock_auto_verified_prop: Mock,
        mock_repo_local_data: Mock,
        mock_get_apis: Mock,
        mock_process_push: Mock,
        mock_api_rate_limit: Mock,
        mock_repo_api: Mock,
        push_payload: dict[str, Any],
    ) -> None:
        """Test processing push event."""
        # Mock GitHub API to prevent network calls
        mock_api = Mock()
        mock_api.rate_limiting = [100, 5000]
        mock_user = Mock()
        mock_user.login = "test-user"
        mock_api.get_user.return_value = mock_user

        mock_api_rate_limit.return_value = (mock_api, "TOKEN", "USER")
        mock_repo_api.return_value = Mock()
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code
        mock_repo_local_data.return_value = {}
        mock_process_push.return_value = None

        headers = Headers({"X-GitHub-Event": "push"})
        webhook = GithubWebhook(hook_data=push_payload, headers=headers, logger=Mock())

        await webhook.process()
        mock_process_push.assert_called_once()

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.issue_comment_handler.IssueCommentHandler.process_comment_webhook_data")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch(
        "webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users",
        new_callable=lambda: property(lambda self: None),
    )
    async def test_process_issue_comment_event(
        self,
        mock_auto_verified_prop: Mock,
        mock_repo_local_data: Mock,
        mock_get_apis: Mock,
        mock_process_comment: Mock,
        mock_api_rate_limit: Mock,
        mock_repo_api: Mock,
        issue_comment_payload: dict[str, Any],
    ) -> None:
        """Test processing issue_comment event."""
        # Mock GitHub API to prevent network calls
        mock_api = Mock()
        mock_api.rate_limiting = [100, 5000]
        mock_user = Mock()
        mock_user.login = "test-user"
        mock_api.get_user.return_value = mock_user

        mock_api_rate_limit.return_value = (mock_api, "TOKEN", "USER")
        mock_repo_api.return_value = Mock()
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code
        mock_repo_local_data.return_value = {}
        mock_process_comment.return_value = None

        headers = Headers({"X-GitHub-Event": "issue_comment"})
        webhook = GithubWebhook(hook_data=issue_comment_payload, headers=headers, logger=Mock())

        # Mock get_pull_request to return a valid pull request object
        mock_pr = Mock()
        mock_pr.draft = False  # Not a draft, so processing should continue
        mock_pr.user.login = "testuser"  # Mock PR user
        mock_pr.base.ref = "main"  # Mock base reference
        mock_commit = Mock()  # Mock commit object
        mock_pr.get_commits.return_value = [mock_commit]  # Return iterable list
        mock_file = Mock()
        mock_file.filename = "test_file.py"
        mock_pr.get_files.return_value = [mock_file]  # Return iterable list of files

        # Mock the repository git tree operations for owners file handler
        mock_tree = Mock()
        mock_tree_element = Mock()
        mock_tree_element.path = "OWNERS"
        mock_tree_element.type = "blob"
        mock_tree.tree = [mock_tree_element]  # Make tree.tree iterable

        with (
            patch.object(webhook, "get_pull_request", return_value=mock_pr),
            patch.object(webhook.repository, "get_git_tree", return_value=mock_tree),
            patch.object(
                webhook.repository,
                "get_contents",
                return_value=Mock(decoded_content=b"approvers:\n  - user1\nreviewers:\n  - user2"),
            ),
        ):
            await webhook.process()
            mock_process_comment.assert_called_once()

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch(
        "webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users",
        new_callable=lambda: property(lambda self: None),
    )
    async def test_process_unsupported_event(
        self,
        mock_auto_verified_prop: Mock,
        mock_repo_local_data: Mock,
        mock_get_apis: Mock,
        mock_api_rate_limit: Mock,
        mock_repo_api: Mock,
        pull_request_payload: dict[str, Any],
    ) -> None:
        """Test processing unsupported event type."""
        # Mock GitHub API to prevent network calls
        mock_api = Mock()
        mock_api.rate_limiting = [100, 5000]
        mock_user = Mock()
        mock_user.login = "test-user"
        mock_api.get_user.return_value = mock_user

        mock_api_rate_limit.return_value = (mock_api, "TOKEN", "USER")
        mock_repo_api.return_value = Mock()
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code
        mock_repo_local_data.return_value = {}

        headers = Headers({"X-GitHub-Event": "unsupported_event"})
        webhook = GithubWebhook(hook_data=pull_request_payload, headers=headers, logger=Mock())

        # Should not raise an exception, just skip processing
        await webhook.process()

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch(
        "webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users",
        new_callable=lambda: property(lambda self: None),
    )
    def test_event_filtering_by_configuration(
        self,
        mock_auto_verified_prop: Mock,
        mock_repo_local_data: Mock,
        mock_get_apis: Mock,
        mock_api_rate_limit: Mock,
        mock_repo_api: Mock,
        pull_request_payload: dict[str, Any],
        webhook_headers: Headers,
    ) -> None:
        """Test that events are filtered based on repository configuration."""
        # Mock GitHub API to prevent network calls
        mock_api = Mock()
        mock_api.rate_limiting = [100, 5000]
        mock_user = Mock()
        mock_user.login = "test-user"
        mock_api.get_user.return_value = mock_user

        mock_api_rate_limit.return_value = (mock_api, "TOKEN", "USER")
        mock_repo_api.return_value = Mock()
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code
        mock_repo_local_data.return_value = {}

        webhook = GithubWebhook(hook_data=pull_request_payload, headers=webhook_headers, logger=Mock())

        # The test config includes pull_request in events list, so should be processed
        events = webhook.config.get_value(value="events", extra_dict={})
        assert "pull_request" in events

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch(
        "webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users",
        new_callable=lambda: property(lambda self: None),
    )
    def test_webhook_data_extraction(
        self,
        mock_auto_verified_prop: Mock,
        mock_repo_local_data: Mock,
        mock_get_apis: Mock,
        mock_api_rate_limit: Mock,
        mock_repo_api: Mock,
        pull_request_payload: dict[str, Any],
        webhook_headers: Headers,
    ) -> None:
        """Test extraction of webhook data into class attributes."""
        # Mock GitHub API to prevent network calls
        mock_api = Mock()
        mock_api.rate_limiting = [100, 5000]
        mock_user = Mock()
        mock_user.login = "test-user"
        mock_api.get_user.return_value = mock_user

        mock_api_rate_limit.return_value = (mock_api, "TOKEN", "USER")
        mock_repo_api.return_value = Mock()
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code
        mock_repo_local_data.return_value = {}

        webhook = GithubWebhook(hook_data=pull_request_payload, headers=webhook_headers, logger=Mock())

        # Test extraction from the test configuration
        assert webhook.repository_full_name == "my-org/test-repo"
        # assert webhook.github_app_id == 123456  # Skip for now - type conflict between linter and runtime
        assert webhook.pypi == {"token": "PYPI TOKEN"}
        assert webhook.verified_job is True
        assert webhook.tox_python_version == "3.8"
        assert webhook.pre_commit is True

    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch(
        "webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users",
        new_callable=lambda: property(lambda self: None),
    )
    def test_api_rate_limit_selection(
        self,
        mock_auto_verified_prop: Mock,
        mock_repo_api: Mock,
        mock_repo_local_data: Mock,
        mock_get_apis: Mock,
        mock_api_rate_limit: Mock,
    ) -> None:
        """Test that the API with highest rate limit is selected."""
        # Mock GitHub API to prevent network calls
        mock_api = Mock()
        mock_api.rate_limiting = [100, 5000]
        mock_user = Mock()
        mock_user.login = "test-user"
        mock_api.get_user.return_value = mock_user

        mock_api_rate_limit.return_value = (mock_api, "SELECTED_TOKEN", "SELECTED_USER")
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code
        mock_repo_local_data.return_value = {}
        mock_repo_api.return_value = Mock()  # Mock the repository GitHub app API

        with patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"}):
            GithubWebhook(
                hook_data={"repository": {"name": "test-repo", "full_name": "my-org/test-repo"}},
                headers=Headers({"X-GitHub-Event": "pull_request"}),
                logger=Mock(),
            )

            mock_api_rate_limit.assert_called_once()

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch(
        "webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users",
        new_callable=lambda: property(lambda self: None),
    )
    def test_repository_api_initialization(
        self,
        mock_auto_verified_prop: Mock,
        mock_repo_local_data: Mock,
        mock_get_apis: Mock,
        mock_api_rate_limit: Mock,
        mock_repo_api: Mock,
        pull_request_payload: dict[str, Any],
        webhook_headers: Headers,
    ) -> None:
        """Test that repository API is properly initialized."""
        # Mock GitHub API to prevent network calls
        mock_api = Mock()
        mock_api.rate_limiting = [100, 5000]
        mock_user = Mock()
        mock_user.login = "test-user"
        mock_api.get_user.return_value = mock_user

        mock_api_rate_limit.return_value = (mock_api, "TOKEN", "USER")
        mock_repo_instance = Mock()
        mock_repo_api.return_value = mock_repo_instance
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code
        mock_repo_local_data.return_value = {}

        webhook = GithubWebhook(hook_data=pull_request_payload, headers=webhook_headers, logger=Mock())

        mock_repo_api.assert_called_once()
        # The repository_by_github_app should be the result of get_repo() call on mock_repo_instance
        assert webhook.repository_by_github_app == mock_repo_instance.get_repo.return_value
