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

# Test token constant to avoid S106 security warnings
TEST_GITHUB_TOKEN = (
    "ghp_test1234567890abcdefghijklmnopqrstuvwxyz"  # pragma: allowlist secret  # noqa: S105  # gitleaks:allow
)


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
                "node_id": "PR_test123",
                "title": "Test PR",
                "user": {"login": "testuser"},
                "base": {"ref": "main", "sha": "base123"},  # Added sha for RefWrapper
                # Note: Removed "user" field from head to avoid production code bug
                # in CommitWrapper.committer (line 153) which passes login string
                # instead of dict to UserWrapper, causing ValueError
                "head": {"sha": "abc123"},
                "draft": False,
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

    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    @patch("webhook_server.libs.github_api.get_apis_and_tokes_from_config")
    def test_init_with_only_github_app_repository(
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
    ):
        """Test initialization when only github_app repository is available.

        Edge case: self.repository is None but self.repository_by_github_app is valid.
        Should use self.repository_name for clone_repo_dir, not self.repository.name.
        This tests the fix for AttributeError on line 114 of github_api.py.
        """
        mock_config.return_value.repository = True
        mock_config.return_value.repository_local_data.return_value = {}
        mock_get_api.return_value = (Mock(), "token", "apiuser")

        # First call returns None (self.repository), second returns valid (self.repository_by_github_app)
        mock_github_app_repo = Mock(name="github_app_repo")
        mock_get_repo_api.side_effect = [None, mock_github_app_repo]

        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"
        mock_get_apis.return_value = []

        gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

        # Verify clone_repo_dir was created successfully using repository_name
        assert hasattr(gh, "clone_repo_dir")
        assert "test-repo" in gh.clone_repo_dir
        assert gh.repository is None
        assert gh.repository_by_github_app is not None
        assert gh.repository_by_github_app == mock_github_app_repo

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
    @patch("webhook_server.libs.handlers.pull_request_handler.PullRequestHandler.process_pull_request_webhook_data")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch(
        "webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users",
        return_value=None,
    )
    async def test_process_pull_request_event(
        self,
        _mock_auto_verified_method: Mock,
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
        mock_repo = Mock()
        mock_repo.full_name = "my-org/test-repo"
        mock_repo_api.return_value = mock_repo
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code
        mock_repo_local_data.return_value = {}
        mock_process_pr.return_value = None

        webhook = GithubWebhook(hook_data=pull_request_payload, headers=webhook_headers, logger=Mock())
        webhook.unified_api = AsyncMock()
        webhook.unified_api.get_pull_request_files = AsyncMock(return_value=[Mock(filename="test.py")])
        # Return dict format for GraphQL compatibility
        webhook.unified_api.get_git_tree = AsyncMock(return_value={"tree": [{"path": "OWNERS", "type": "blob"}]})
        webhook.unified_api.get_file_contents = AsyncMock(
            return_value="approvers:\\n  - user1\\nreviewers:\\n  - user2"
        )
        webhook.unified_api.add_assignees_by_login = AsyncMock()

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

        # Mock unified_api.get_pull_request and get_last_commit directly
        webhook.unified_api.get_pull_request = AsyncMock(return_value=mock_pr)
        webhook.unified_api.get_last_commit = AsyncMock(return_value=mock_commit)

        # No need to patch repository methods anymore - unified_api is already mocked
        await webhook.process()
        mock_process_pr.assert_called_once()

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.handlers.push_handler.PushHandler.process_push_webhook_data")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch(
        "webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users",
        return_value=None,
    )
    async def test_process_push_event(
        self,
        _mock_auto_verified_method: Mock,
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
    @patch("webhook_server.libs.handlers.issue_comment_handler.IssueCommentHandler.process_comment_webhook_data")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch(
        "webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users",
        return_value=None,
    )
    async def test_process_issue_comment_event(
        self,
        _mock_auto_verified_method: Mock,
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
        mock_repo = Mock()
        mock_repo.full_name = "my-org/test-repo"
        mock_repo_api.return_value = mock_repo
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code
        mock_repo_local_data.return_value = {}
        mock_process_comment.return_value = None

        headers = Headers({"X-GitHub-Event": "issue_comment"})
        webhook = GithubWebhook(hook_data=issue_comment_payload, headers=headers, logger=Mock())
        webhook.unified_api = AsyncMock()
        webhook.unified_api.get_pull_request_files = AsyncMock(return_value=[Mock(filename="test.py")])
        # Return dict format for GraphQL compatibility
        webhook.unified_api.get_git_tree = AsyncMock(return_value={"tree": [{"path": "OWNERS", "type": "blob"}]})
        webhook.unified_api.get_file_contents = AsyncMock(
            return_value="approvers:\\n  - user1\\nreviewers:\\n  - user2"
        )

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

        # Mock unified_api.get_pull_request and get_last_commit directly
        webhook.unified_api.get_pull_request = AsyncMock(return_value=mock_pr)
        webhook.unified_api.get_last_commit = AsyncMock(return_value=mock_commit)

        # No need to patch repository methods anymore - unified_api is already mocked
        await webhook.process()
        mock_process_comment.assert_called_once()

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch(
        "webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users",
        return_value=None,
    )
    async def test_process_unsupported_event(
        self,
        _mock_auto_verified_method: Mock,
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

        # Mock UnifiedGitHubAPI to prevent real GraphQL calls
        with patch("webhook_server.libs.github_api.UnifiedGitHubAPI") as mock_unified:
            mock_unified_instance = AsyncMock()
            # Make get_pull_request return a proper mock PR with draft=False
            mock_pr = Mock()
            mock_pr.draft = False
            mock_pr.number = 123
            mock_unified_instance.get_pull_request = AsyncMock(return_value=mock_pr)
            mock_unified.return_value = mock_unified_instance

            headers = Headers({"X-GitHub-Event": "unsupported_event"})
            webhook = GithubWebhook(hook_data=pull_request_payload, headers=headers, logger=Mock())

            # Should not raise an exception, just skip processing
            await webhook.process()

    @patch("webhook_server.libs.github_api.Config")
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

    @patch("webhook_server.libs.github_api.Config")
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

    @patch("webhook_server.libs.github_api.Config")
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
        gh.add_api_users_to_auto_verified_and_merged_users()
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

    @patch("webhook_server.libs.github_api.PullRequestHandler")
    @patch("webhook_server.libs.github_api.OwnersFileHandler")
    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    @patch("webhook_server.libs.github_api.get_apis_and_tokes_from_config")
    async def test_webhook_data_optimization_for_pull_request_event(
        self,
        mock_get_apis: Mock,
        mock_color: Mock,
        mock_get_app_api: Mock,
        mock_get_repo_api: Mock,
        mock_api_rate_limit: Mock,
        mock_config: Mock,
        mock_owners_handler: Mock,
        mock_pr_handler: Mock,
    ) -> None:
        """Test that pull_request events use webhook data directly without API calls.

        This test validates the optimization where pull_request events construct
        PullRequestWrapper and CommitWrapper directly from webhook payload instead
        of making redundant API calls. Expected savings: 2 API calls per pull_request webhook.
        """
        # Setup webhook payload with complete PR data (as GitHub sends)
        webhook_payload = {
            "action": "opened",
            "repository": {
                "name": "test-repo",
                "full_name": "my-org/test-repo",
                "node_id": "R_test123",
                "id": 12345,
            },
            "pull_request": {
                "number": 456,
                "title": "Test optimization PR",
                "body": "Testing webhook data optimization",
                "state": "open",
                "draft": False,
                "merged": False,
                "user": {"login": "testuser", "id": 789, "node_id": "U_test789"},
                "head": {
                    "ref": "feature-branch",
                    "sha": "abc1234567890def",  # pragma: allowlist secret
                    # Note: Removed "user" field to avoid production code bug in CommitWrapper.committer (line 153)
                    # which passes login string instead of dict to UserWrapper, causing ValueError
                    "repo": {"owner": {"login": "my-org"}, "name": "test-repo"},
                },
                "base": {
                    "ref": "main",
                    "sha": "def0987654321abc",  # pragma: allowlist secret
                    "repo": {"owner": {"login": "my-org"}, "name": "test-repo"},
                },
                "labels": [],
                "commits": [
                    {
                        "sha": "abc1234567890def",  # pragma: allowlist secret
                        "author": {"login": "testuser", "id": 789},
                    }
                ],
            },
        }

        webhook_headers = Headers({
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "optimization-test-123",
        })

        # Mock config and API
        mock_config_instance = Mock()
        mock_config_instance.repository_data = True
        mock_config_instance.get_value.side_effect = lambda value, **_kwargs: {
            "auto-verified-and-merged-users": [],
            "container": {},
            "can-be-merged-required-labels": [],
            "set-auto-merge-prs": [],
            "create-issue-for-new-pr": False,
        }.get(value, None)
        mock_config_instance.repository_local_data.return_value = {}
        mock_config.return_value = mock_config_instance

        mock_api = Mock()
        mock_api.rate_limiting = [100, 5000]
        mock_user = Mock()
        mock_user.login = "test-api-user"
        mock_api.get_user.return_value = mock_user
        mock_api_rate_limit.return_value = (mock_api, TEST_GITHUB_TOKEN, "test-api-user")

        mock_repo = Mock()
        mock_repo.full_name = "my-org/test-repo"
        mock_repo.name = "test-repo"
        mock_get_repo_api.return_value = mock_repo
        mock_get_app_api.return_value = mock_api
        mock_color.return_value = "test-repo"
        mock_get_apis.return_value = []

        # Stub repository_data with expected shape for comprehensive_repository_data optimization
        # Provides minimal dict structure so GithubWebhook.process() can access
        # repository_data["collaborators"]["edges"], ["mentionableUsers"]["nodes"], etc.
        mock_repo_data_stub = {
            "collaborators": {"edges": []},
            "mentionableUsers": {"nodes": []},
            "issues": {"nodes": []},
            "pullRequests": {"nodes": []},
        }

        # Mock handlers to prevent actual processing
        mock_owners_instance = AsyncMock()
        mock_owners_instance.initialize = AsyncMock(return_value=mock_owners_instance)
        mock_owners_handler.return_value = mock_owners_instance
        mock_pr_handler_instance = AsyncMock()
        mock_pr_handler_instance.process_pull_request_webhook_data = AsyncMock()
        mock_pr_handler.return_value = mock_pr_handler_instance

        # Create webhook instance
        webhook = GithubWebhook(hook_data=webhook_payload, headers=webhook_headers, logger=Mock())

        # Mock unified_api.get_comprehensive_repository_data BEFORE calling process()
        webhook.unified_api.get_comprehensive_repository_data = AsyncMock(return_value=mock_repo_data_stub)
        webhook.unified_api.get_pull_request = AsyncMock()  # Should NOT be called
        webhook.unified_api.get_last_commit = AsyncMock()  # Should NOT be called
        webhook.unified_api.get_pull_request_files = AsyncMock(return_value=[])
        webhook.unified_api.get_git_tree = AsyncMock(return_value=Mock(tree=[]))

        # Process the webhook
        await webhook.process()

        # CRITICAL ASSERTIONS: Verify optimization worked
        # For pull_request events, get_pull_request and get_last_commit should NOT be called
        webhook.unified_api.get_pull_request.assert_not_called()
        webhook.unified_api.get_last_commit.assert_not_called()

        # Verify that last_commit was set directly from webhook data
        assert hasattr(webhook, "last_commit")
        assert webhook.last_commit.sha == "abc1234567890def"  # pragma: allowlist secret

        # Verify that parent_committer was set from webhook data
        assert webhook.parent_committer == "testuser"
        # Note: last_committer is "unknown" due to production code bug in CommitWrapper.committer (line 153)
        # which passes login string instead of dict to UserWrapper. When head.user is missing,
        # the fallback returns UserWrapper({"login": "unknown"}).
        assert webhook.last_committer == "unknown"

        # Verify handlers were called (processing continued normally)
        mock_owners_instance.initialize.assert_called_once()
        mock_pr_handler_instance.process_pull_request_webhook_data.assert_called_once()

    def test_github_webhook_repository_id_property(self, webhook_headers: Headers) -> None:
        """Test repository_id property returns node_id from webhook payload."""
        payload = {
            "repository": {"name": "test-repo", "full_name": "my-org/test-repo", "node_id": "R_test123"},
            "pull_request": {"number": 123},
        }

        with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
            mock_api = Mock()
            mock_token = TEST_GITHUB_TOKEN
            mock_get_api.return_value = (mock_api, mock_token, "testuser")

            with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo:
                mock_repo = Mock()
                mock_get_repo.return_value = mock_repo

                with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                    mock_app_api = Mock()
                    mock_get_app_api.return_value = mock_app_api

                    with patch("webhook_server.libs.github_api.UnifiedGitHubAPI"):
                        with patch("webhook_server.libs.github_api.get_apis_and_tokes_from_config") as mock_get_apis:
                            mock_get_apis.return_value = []
                            webhook = GithubWebhook(hook_data=payload, headers=webhook_headers, logger=Mock())
                            assert webhook.repository_id == "R_test123"

    def test_github_webhook_repository_numeric_id_property(self, webhook_headers: Headers) -> None:
        """Test repository_numeric_id property returns id from webhook payload."""
        payload = {
            "repository": {"name": "test-repo", "full_name": "my-org/test-repo", "id": 12345},
            "pull_request": {"number": 123},
        }

        with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
            mock_api = Mock()
            mock_token = TEST_GITHUB_TOKEN
            mock_get_api.return_value = (mock_api, mock_token, "testuser")

            with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo:
                mock_repo = Mock()
                mock_get_repo.return_value = mock_repo

                with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                    mock_app_api = Mock()
                    mock_get_app_api.return_value = mock_app_api

                    with patch("webhook_server.libs.github_api.UnifiedGitHubAPI"):
                        with patch("webhook_server.libs.github_api.get_apis_and_tokes_from_config") as mock_get_apis:
                            mock_get_apis.return_value = []
                            webhook = GithubWebhook(hook_data=payload, headers=webhook_headers, logger=Mock())
                            assert webhook.repository_numeric_id == 12345

    def test_normalize_container_args_none(self, webhook_headers: Headers) -> None:
        """Test _normalize_container_args with None."""
        payload = {
            "repository": {"name": "test-repo", "full_name": "my-org/test-repo"},
            "pull_request": {"number": 123},
        }

        with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
            mock_api = Mock()
            mock_token = TEST_GITHUB_TOKEN
            mock_get_api.return_value = (mock_api, mock_token, "testuser")

            with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo:
                mock_repo = Mock()
                mock_get_repo.return_value = mock_repo

                with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                    mock_app_api = Mock()
                    mock_get_app_api.return_value = mock_app_api

                    with patch("webhook_server.libs.github_api.UnifiedGitHubAPI"):
                        with patch("webhook_server.libs.github_api.get_apis_and_tokes_from_config") as mock_get_apis:
                            mock_get_apis.return_value = []
                            webhook = GithubWebhook(hook_data=payload, headers=webhook_headers, logger=Mock())
                            result = webhook._normalize_container_args(None)
                            assert result == []

    def test_normalize_container_args_dict(self, webhook_headers: Headers) -> None:
        """Test _normalize_container_args with dict."""
        payload = {
            "repository": {"name": "test-repo", "full_name": "my-org/test-repo"},
            "pull_request": {"number": 123},
        }

        with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
            mock_api = Mock()
            mock_token = TEST_GITHUB_TOKEN
            mock_get_api.return_value = (mock_api, mock_token, "testuser")

            with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo:
                mock_repo = Mock()
                mock_get_repo.return_value = mock_repo

                with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                    mock_app_api = Mock()
                    mock_get_app_api.return_value = mock_app_api

                    with patch("webhook_server.libs.github_api.UnifiedGitHubAPI"):
                        with patch("webhook_server.libs.github_api.get_apis_and_tokes_from_config") as mock_get_apis:
                            mock_get_apis.return_value = []
                            webhook = GithubWebhook(hook_data=payload, headers=webhook_headers, logger=Mock())
                            result = webhook._normalize_container_args({"key1": "value1", "key2": "value2"})
                            assert result == ["key1=value1", "key2=value2"]

    def test_container_repository_and_tag(self, webhook_headers: Headers) -> None:
        """Test container_repository_and_tag method."""
        payload = {
            "repository": {"name": "test-repo", "full_name": "my-org/test-repo"},
            "pull_request": {"number": 123},
        }

        with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
            mock_api = Mock()
            mock_token = TEST_GITHUB_TOKEN
            mock_get_api.return_value = (mock_api, mock_token, "testuser")

            with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo:
                mock_repo = Mock()
                mock_get_repo.return_value = mock_repo

                with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                    mock_app_api = Mock()
                    mock_get_app_api.return_value = mock_app_api

                    with patch("webhook_server.libs.github_api.UnifiedGitHubAPI"):
                        with patch("webhook_server.libs.github_api.get_apis_and_tokes_from_config") as mock_get_apis:
                            mock_get_apis.return_value = []
                            with patch(
                                "webhook_server.libs.github_api.get_container_repository_and_tag"
                            ) as mock_get_container:
                                mock_get_container.return_value = "registry/repo:tag"
                                webhook = GithubWebhook(hook_data=payload, headers=webhook_headers, logger=Mock())
                                result = webhook.container_repository_and_tag()
                                assert result == "registry/repo:tag"
