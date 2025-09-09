import asyncio
import os
import tempfile
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from simple_logger.logger import get_logger
from starlette.datastructures import Headers

from webhook_server.libs.exceptions import RepositoryNotFoundInConfigError
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

    @pytest.fixture
    def minimal_hook_data(self) -> dict[str, Any]:
        return {
            "repository": {"name": "test-repo", "full_name": "org/test-repo"},
            "number": 1,
        }

    @pytest.fixture
    def minimal_headers(self) -> dict[str, str]:
        return {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "abc"}

    @pytest.fixture
    def logger(self):
        return get_logger(name="test")

    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    def test_init_success(
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
        mock_get_api.return_value = (Mock(), "token", "apiuser")
        mock_get_repo_api.return_value = Mock(name="repo_api")
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"
        gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)
        assert gh.repository_name == "test-repo"
        assert gh.repository_full_name == "org/test-repo"
        assert hasattr(gh, "repository")
        assert hasattr(gh, "repository_by_github_app")
        assert gh.log_prefix

    @patch("webhook_server.libs.github_api.Config")
    def test_init_missing_repo(self, mock_config, minimal_hook_data, minimal_headers, logger):
        mock_config.return_value.repository = "repo"
        mock_config.return_value.repository_data = {}
        with pytest.raises(RepositoryNotFoundInConfigError):
            GithubWebhook(minimal_hook_data, minimal_headers, logger)

    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    def test_init_no_api_token(self, mock_color, mock_get_api, mock_config, minimal_hook_data, minimal_headers, logger):
        mock_config.return_value.repository = True
        mock_get_api.return_value = (None, None, None)
        mock_color.return_value = "test-repo"
        gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)
        assert not hasattr(gh, "repository")

    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    def test_init_no_github_app_api(
        self, mock_color, mock_get_repo_api, mock_get_api, mock_config, minimal_hook_data, minimal_headers, logger
    ):
        mock_config.return_value.repository = True
        mock_get_api.return_value = (Mock(), "token", "apiuser")
        mock_get_repo_api.return_value = Mock()
        mock_color.return_value = "test-repo"
        with patch("webhook_server.libs.github_api.get_repository_github_app_api", return_value=None):
            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)
            assert hasattr(gh, "repository")
            assert not hasattr(gh, "repository_by_github_app")

    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    def test_init_no_repository_objects(
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
        mock_get_api.return_value = (Mock(), "token", "apiuser")
        mock_get_repo_api.return_value = None
        mock_get_app_api.return_value = None
        mock_color.return_value = "test-repo"
        gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)
        assert not hasattr(gh, "repository_by_github_app")

    @patch("webhook_server.libs.github_api.PullRequest")
    @patch("webhook_server.libs.github_api.PushHandler")
    @patch("webhook_server.libs.github_api.IssueCommentHandler")
    @patch("webhook_server.libs.github_api.PullRequestHandler")
    @patch("webhook_server.libs.github_api.PullRequestReviewHandler")
    @patch("webhook_server.libs.github_api.CheckRunHandler")
    @patch("webhook_server.libs.github_api.OwnersFileHandler")
    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    def test_process_ping_event(
        self,
        mock_color,
        mock_get_app_api,
        mock_get_repo_api,
        mock_get_api,
        mock_config,
        mock_owners,
        mock_checkrun,
        mock_review,
        mock_pr_handler,
        mock_issue,
        mock_push,
        mock_pr,
        minimal_hook_data,
        minimal_headers,
        logger,
    ):
        mock_config.return_value.repository = True
        mock_get_api.return_value = (Mock(), "token", "apiuser")
        mock_get_repo_api.return_value = Mock()
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"
        headers = minimal_headers.copy()
        headers["X-GitHub-Event"] = "ping"
        gh = GithubWebhook(minimal_hook_data, headers, logger)
        result = asyncio.run(gh.process())
        assert result["message"] == "pong"

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
        """Test processing of unsupported event types."""
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

    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    @patch("webhook_server.libs.github_api.get_apis_and_tokes_from_config")
    def test_event_filtering_by_configuration(
        self,
        mock_get_apis,
        mock_color,
        mock_get_app_api,
        mock_get_repo_api,
        mock_get_api,
        mock_config,
        minimal_hook_data,
        minimal_headers,
        logger,
    ) -> None:
        """Test that events are filtered based on repository configuration."""
        # Mock GitHub API to prevent network calls
        mock_config.return_value.repository = True
        mock_config.return_value.repository_local_data.return_value = {}
        mock_get_api.return_value = (Mock(), "token", "apiuser")
        mock_get_repo_api.return_value = Mock()
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code

        webhook = GithubWebhook(hook_data=minimal_hook_data, headers=minimal_headers, logger=Mock())

        # The test config includes pull_request in events list, so should be processed
        assert webhook.repository_name == "test-repo"

    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    @patch("webhook_server.libs.github_api.get_apis_and_tokes_from_config")
    def test_webhook_data_extraction(
        self,
        mock_get_apis,
        mock_color,
        mock_get_app_api,
        mock_get_repo_api,
        mock_get_api,
        mock_config,
        minimal_hook_data,
        minimal_headers,
        logger,
    ) -> None:
        """Test that webhook data is properly extracted."""
        # Mock GitHub API to prevent network calls
        mock_config.return_value.repository = True
        mock_config.return_value.repository_local_data.return_value = {}
        mock_get_api.return_value = (Mock(), "token", "apiuser")
        mock_get_repo_api.return_value = Mock()
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code

        webhook = GithubWebhook(hook_data=minimal_hook_data, headers=minimal_headers, logger=Mock())

        # Verify data extraction
        assert webhook.repository_name == "test-repo"
        assert webhook.repository_full_name == "org/test-repo"
        assert webhook.github_event == "pull_request"
        assert webhook.x_github_delivery == "abc"

    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    @patch("webhook_server.libs.github_api.get_apis_and_tokes_from_config")
    def test_api_rate_limit_selection(
        self,
        mock_get_apis,
        mock_color,
        mock_get_app_api,
        mock_get_repo_api,
        mock_get_api,
        mock_config,
        minimal_hook_data,
        minimal_headers,
        logger,
    ) -> None:
        """Test that API with highest rate limit is selected."""
        # Mock GitHub API to prevent network calls
        mock_config.return_value.repository = True
        mock_config.return_value.repository_local_data.return_value = {}
        mock_get_api.return_value = (Mock(), "token", "apiuser")
        mock_get_repo_api.return_value = Mock()
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code

        webhook = GithubWebhook(hook_data=minimal_hook_data, headers=minimal_headers, logger=Mock())

        # Verify API selection
        assert webhook.api_user == "apiuser"
        assert webhook.token == "token"

    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    @patch("webhook_server.libs.github_api.get_apis_and_tokes_from_config")
    def test_repository_api_initialization(
        self,
        mock_get_apis,
        mock_color,
        mock_get_app_api,
        mock_get_repo_api,
        mock_get_api,
        mock_config,
        minimal_hook_data,
        minimal_headers,
        logger,
    ) -> None:
        """Test that repository API is properly initialized."""
        # Mock GitHub API to prevent network calls
        mock_config.return_value.repository = True
        mock_config.return_value.repository_local_data.return_value = {}
        mock_get_api.return_value = (Mock(), "token", "apiuser")
        mock_get_repo_api.return_value = Mock()
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code

        webhook = GithubWebhook(hook_data=minimal_hook_data, headers=minimal_headers, logger=Mock())

        # Should be called twice: once for main repo, once for github app repo
        assert mock_get_repo_api.call_count == 2
        assert webhook.repository_by_github_app == mock_get_repo_api.return_value

    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    @patch("webhook_server.libs.github_api.get_apis_and_tokes_from_config")
    def test_init_failed_repository_objects(
        self,
        mock_get_apis,
        mock_color,
        mock_get_app_api,
        mock_get_repo_api,
        mock_get_api,
        mock_config,
        minimal_hook_data,
        minimal_headers,
        logger,
    ) -> None:
        """Test initialization when both repository objects fail to be created."""
        mock_config.return_value.repository = True
        mock_config.return_value.repository_local_data.return_value = {}
        mock_get_api.return_value = (Mock(), "token", "apiuser")
        mock_get_repo_api.return_value = None
        mock_get_app_api.return_value = None
        mock_color.return_value = "test-repo"
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code
        gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)
        # Should have repository attribute but not repository_by_github_app
        assert gh.repository is None
        assert not hasattr(gh, "repository_by_github_app")

    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    @patch("webhook_server.libs.github_api.get_apis_and_tokes_from_config")
    def test_add_api_users_to_auto_verified_and_merged_users(
        self,
        mock_get_apis,
        mock_color,
        mock_get_app_api,
        mock_get_repo_api,
        mock_get_api,
        mock_config,
        minimal_hook_data,
        minimal_headers,
        logger,
    ) -> None:
        """Test the add_api_users_to_auto_verified_and_merged_users property."""
        mock_config.return_value.repository = True
        mock_config.return_value.repository_local_data.return_value = {}
        mock_get_api.return_value = (Mock(), "token", "apiuser")
        mock_get_repo_api.return_value = Mock()
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"

        def get_value_side_effect(value, *args, **kwargs):
            if value == "auto-verified-and-merged-users":
                return []
            if value == "container":
                return {}
            if value == "can-be-merged-required-labels":
                return []
            if value == "set-auto-merge-prs":
                return []
            if value == "minimum-lgtm":
                return 0
            if value == "create-issue-for-new-pr":
                return True
            if value == "pypi":
                return {}
            if value == "tox":
                return {}
            if value == "slack-webhook-url":
                return ""
            if value == "conventional-title":
                return ""
            if value == "tox-python-version":
                return ""
            if value == "verified-job":
                return True
            if value == "pre-commit":
                return False
            if value == "github-app-id":
                return ""
            return None

        mock_config.return_value.get_value.side_effect = get_value_side_effect
        # Use a valid rate limit (not 60)
        mock_api = Mock()
        mock_api.rate_limiting = [5000, 5000]  # Valid rate limit
        mock_user = Mock()
        mock_user.login = "test-user"
        mock_api.get_user.return_value = mock_user
        mock_get_apis.return_value = [(mock_api, "token")]
        gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)
        gh.add_api_users_to_auto_verified_and_merged_users
        assert "test-user" in gh.auto_verified_and_merged_users

    @patch("webhook_server.libs.github_api.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.Config")
    def test_prepare_log_prefix_with_color_file(
        self,
        mock_config,
        mock_get_api,
        mock_get_repo_api,
        mock_get_app_api,
        mock_get_apis,
        minimal_hook_data,
        minimal_headers,
        logger,
    ) -> None:
        """Test prepare_log_prefix with repository color functionality."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}
            mock_config.return_value.data_dir = temp_dir

            mock_get_api.return_value = (Mock(), "token", "apiuser")
            mock_get_repo_api.return_value = Mock()
            mock_get_app_api.return_value = Mock()

            # Create proper mock API objects
            mock_api1 = Mock()
            mock_api1.rate_limiting = [0, 5000]
            mock_api1.get_user.return_value.login = "user1"
            mock_api2 = Mock()
            mock_api2.rate_limiting = [0, 5000]
            mock_api2.get_user.return_value.login = "user2"
            mock_get_apis.return_value = [(mock_api1, "token1"), (mock_api2, "token2")]

            # Use a minimal_hook_data with repo name matching the test
            hook_data = {"repository": {"name": "test-repo", "full_name": "test-repo"}}
            webhook = GithubWebhook(hook_data, minimal_headers, logger)
            result = webhook.prepare_log_prefix()
            # Call again to ensure file is read after being created
            result2 = webhook.prepare_log_prefix()

            # Check that a color file was created
            color_file = os.path.join(temp_dir, "log-colors.json")
            assert os.path.exists(color_file)
            assert result is not None
            assert result2 is not None

    @pytest.mark.asyncio
    async def test_process_check_run_event(self, minimal_hook_data: dict, minimal_headers: dict, logger: Mock) -> None:
        """Test processing check run event."""
        check_run_data = {
            "repository": {"name": "test-repo", "full_name": "org/test-repo"},
            "check_run": {"name": "test-check", "head_sha": "abc123", "status": "completed", "conclusion": "success"},
        }
        headers = minimal_headers.copy()
        headers["X-GitHub-Event"] = "check_run"

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("webhook_server.libs.github_api.Config") as mock_config:
                mock_config.return_value.repository = True
                mock_config.return_value.repository_local_data.return_value = {}
                mock_config.return_value.data_dir = temp_dir

                with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                    mock_get_api.return_value = (Mock(), "token", "apiuser")

                    # Mock repository and get_pulls to return a PR with matching head.sha
                    mock_repo = Mock()
                    mock_repo.get_git_tree.return_value.tree = []
                    mock_pr = Mock()
                    mock_pr.head.sha = "abc123"
                    mock_pr.title = "Test PR"
                    mock_pr.number = 42
                    mock_pr.draft = False
                    mock_pr.user.login = "testuser"
                    mock_pr.base.ref = "main"
                    mock_pr.get_commits.return_value = [Mock()]
                    mock_pr.get_files.return_value = []
                    mock_repo.get_pulls.return_value = [mock_pr]
                    mock_repo.get_pull.return_value = mock_pr
                    with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                        mock_get_repo_api.return_value = mock_repo

                        with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                            mock_get_app_api.return_value = Mock()

                            with patch(
                                "webhook_server.libs.github_api.get_apis_and_tokes_from_config"
                            ) as mock_get_apis:
                                # Create proper mock API objects
                                mock_api1 = Mock()
                                mock_api1.rate_limiting = [0, 5000]
                                mock_api1.get_user.return_value.login = "user1"
                                mock_api2 = Mock()
                                mock_api2.rate_limiting = [0, 5000]
                                mock_api2.get_user.return_value.login = "user2"
                                mock_get_apis.return_value = [(mock_api1, "token1"), (mock_api2, "token2")]

                                with (
                                    patch("webhook_server.libs.github_api.CheckRunHandler") as mock_check_handler,
                                    patch("webhook_server.libs.github_api.PullRequestHandler") as mock_pr_handler,
                                ):
                                    mock_check_handler.return_value.process_pull_request_check_run_webhook_data = (
                                        AsyncMock(return_value=True)
                                    )
                                    mock_pr_handler.return_value.check_if_can_be_merged = AsyncMock(return_value=None)

                                    webhook = GithubWebhook(check_run_data, headers, logger)
                                    await webhook.process()

                                    mock_check_handler.return_value.process_pull_request_check_run_webhook_data.assert_awaited_once()
                                    mock_pr_handler.return_value.check_if_can_be_merged.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_pull_request_by_number(
        self, minimal_hook_data: dict, minimal_headers: dict, logger: Mock
    ) -> None:
        """Test getting pull request by number."""
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_repo = Mock()
                    mock_get_repo_api.return_value = mock_repo

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            mock_pr = Mock()
                            mock_repo.get_pull.return_value = mock_pr

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)
                            result = await gh.get_pull_request(number=123)
                            assert result == mock_pr
                            mock_repo.get_pull.assert_called_once_with(123)

    @pytest.mark.asyncio
    async def test_get_pull_request_github_exception(
        self, minimal_hook_data: dict, minimal_headers: dict, logger: Mock
    ) -> None:
        """Test getting pull request with GithubException."""
        from github import GithubException

        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_repo = Mock()
                    mock_get_repo_api.return_value = mock_repo

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            mock_repo.get_pull.side_effect = GithubException(404, "Not found")

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)
                            result = await gh.get_pull_request()
                            assert result is None

    @pytest.mark.asyncio
    async def test_get_pull_request_by_commit_with_pulls(
        self, minimal_hook_data: dict, minimal_headers: dict, logger: Mock
    ) -> None:
        """Test getting pull request by commit with pulls."""
        commit_data = {
            "repository": {"name": "test-repo", "full_name": "my-org/test-repo"},
            "commit": {"sha": "abc123"},
        }

        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_repo = Mock()
                    mock_get_repo_api.return_value = mock_repo

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            mock_commit = Mock()
                            mock_repo.get_commit.return_value = mock_commit

                            mock_pr = Mock()
                            mock_commit.get_pulls.return_value = [mock_pr]

                            gh = GithubWebhook(commit_data, minimal_headers, logger)
                            result = await gh.get_pull_request()
                            assert result == mock_pr

    def test_container_repository_and_tag_with_tag(
        self, minimal_hook_data: dict, minimal_headers: dict, logger: Mock
    ) -> None:
        """Test container_repository_and_tag with provided tag."""
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_get_repo_api.return_value = Mock()

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)
                            gh.container_repository = "test-repo"

                            result = gh.container_repository_and_tag(tag="v1.0.0")
                            assert result == "test-repo:v1.0.0"

    def test_container_repository_and_tag_with_pull_request(
        self, minimal_hook_data: dict, minimal_headers: dict, logger: Mock
    ) -> None:
        """Test container_repository_and_tag with pull request."""
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_get_repo_api.return_value = Mock()

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)
                            gh.container_repository = "test-repo"

                            mock_pr = Mock()
                            mock_pr.number = 123

                            result = gh.container_repository_and_tag(pull_request=mock_pr)
                            assert result == "test-repo:pr-123"

    def test_container_repository_and_tag_merged_pr(
        self, minimal_hook_data: dict, minimal_headers: dict, logger: Mock
    ) -> None:
        """Test container_repository_and_tag with merged pull request."""
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_get_repo_api.return_value = Mock()

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)
                            gh.container_repository = "test-repo"
                            gh.container_tag = "latest"

                            mock_pr = Mock()
                            mock_pr.base.ref = "develop"

                            result = gh.container_repository_and_tag(is_merged=True, pull_request=mock_pr)
                            assert result == "test-repo:develop"

    def test_container_repository_and_tag_no_pull_request(
        self, minimal_hook_data: dict, minimal_headers: dict, logger: Mock
    ) -> None:
        """Test container_repository_and_tag without pull request."""
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_get_repo_api.return_value = Mock()

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

                            result = gh.container_repository_and_tag()
                            assert result is None

    @patch("webhook_server.libs.github_api.requests.post")
    def test_send_slack_message_success(
        self, mock_post: Mock, minimal_hook_data: dict, minimal_headers: dict, logger: Mock
    ) -> None:
        """Test sending slack message successfully."""
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_get_repo_api.return_value = Mock()

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            mock_response = Mock()
                            mock_response.status_code = 200
                            mock_post.return_value = mock_response

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)
                            gh.send_slack_message("Test message", "https://hooks.slack.com/test")

                            mock_post.assert_called_once()
                            call_args = mock_post.call_args
                            assert call_args[0][0] == "https://hooks.slack.com/test"
                            assert "Test message" in call_args[1]["data"]

    @patch("webhook_server.libs.github_api.requests.post")
    def test_send_slack_message_failure(
        self, mock_post: Mock, minimal_hook_data: dict, minimal_headers: dict, logger: Mock
    ) -> None:
        """Test sending slack message with failure."""
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_get_repo_api.return_value = Mock()

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            mock_response = Mock()
                            mock_response.status_code = 400
                            mock_response.text = "Bad Request"
                            mock_post.return_value = mock_response

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

                            with pytest.raises(ValueError, match="Request to slack returned an error 400"):
                                gh.send_slack_message("Test message", "https://hooks.slack.com/test")

    def test_current_pull_request_supported_retest_property(
        self, minimal_hook_data: dict, minimal_headers: dict, logger: Mock
    ) -> None:
        """Test _current_pull_request_supported_retest property."""
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_get_repo_api.return_value = Mock()

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

                            # Test with all features enabled
                            gh.tox = {"main": "all"}
                            gh.build_and_push_container = {"repository": "test"}
                            gh.pypi = {"username": "test"}
                            gh.pre_commit = True
                            gh.conventional_title = "conventional"

                            result = gh._current_pull_request_supported_retest
                            assert "tox" in result
                            assert "build-container" in result
                            assert "python-module-install" in result
                            assert "pre-commit" in result
                            assert "conventional-title" in result

    @pytest.mark.asyncio
    async def test_get_last_commit(self, minimal_hook_data: dict, minimal_headers: dict, logger: Mock) -> None:
        """Test _get_last_commit method."""
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_get_repo_api.return_value = Mock()

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

                            mock_pr = Mock()
                            mock_commits = [Mock(), Mock(), Mock()]
                            mock_pr.get_commits.return_value = mock_commits

                            result = await gh._get_last_commit(mock_pr)
                            assert result == mock_commits[-1]
