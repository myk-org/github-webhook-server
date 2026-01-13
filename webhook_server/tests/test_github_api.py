import asyncio
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from github.GithubException import GithubException
from simple_logger.logger import get_logger
from starlette.datastructures import Headers

from webhook_server.libs.exceptions import RepositoryNotFoundInConfigError
from webhook_server.libs.github_api import GithubWebhook
from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler


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
        """Push webhook payload for tag push (the only push type that triggers cloning)."""
        return {
            "repository": {"name": "test-repo", "full_name": "my-org/test-repo"},
            "ref": "refs/tags/v1.0.0",
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
    def minimal_headers(self) -> Headers:
        return Headers({"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "abc"})

    @pytest.fixture
    def logger(self):
        return get_logger(name="test")

    @pytest.fixture
    def to_thread_sync(self) -> Callable[..., Awaitable[object]]:
        """Async helper to make asyncio.to_thread awaitable while executing inline."""

        async def _to_thread_sync(fn: Callable[..., object], *args: object, **kwargs: object) -> object:
            return fn(*args, **kwargs)

        return _to_thread_sync

    @pytest.fixture
    def get_value_side_effect(self) -> Callable[..., object]:
        """Side effect function for Config.get_value mock in clone tests."""

        def _get_value_side_effect(value: str, *_args: object, **_kwargs: object) -> bool | dict[str, object] | None:
            if value == "mask-sensitive-data":
                return True
            if value == "container":
                return {}
            if value == "pypi":
                return {}
            if value == "tox":
                return {}
            if value == "verified-job":
                return True
            return None

        return _get_value_side_effect

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
        headers = Headers({"X-GitHub-Event": "ping", "X-GitHub-Delivery": "abc"})
        gh = GithubWebhook(minimal_hook_data, headers, logger)
        result = asyncio.run(gh.process())
        assert result is None

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.handlers.pull_request_handler.PullRequestHandler.process_pull_request_webhook_data")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch("webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users")
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
            patch.object(webhook, "_clone_repository", new=AsyncMock(return_value=None)),
            patch.object(
                OwnersFileHandler,
                "initialize",
                new=AsyncMock(return_value=None),
            ),
        ):
            await webhook.process()
            mock_process_pr.assert_called_once()

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.handlers.push_handler.PushHandler.process_push_webhook_data")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch("webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users")
    async def test_process_push_event(
        self,
        mock_auto_verified_prop: Mock,
        mock_repo_local_data: Mock,
        mock_get_apis: Mock,
        mock_process_push: Mock,
        mock_api_rate_limit: Mock,
        mock_repo_api: Mock,
        mock_get_repo: Mock,
        push_payload: dict[str, Any],
    ) -> None:
        """Test processing tag push event triggers cloning and PushHandler."""
        # Mock GitHub API to prevent network calls
        mock_api = Mock()
        mock_api.rate_limiting = [100, 5000]
        mock_user = Mock()
        mock_user.login = "test-user"
        mock_api.get_user.return_value = mock_user

        # Mock repository with proper clone_url
        mock_repository = Mock()
        mock_repository.clone_url = "https://github.com/test/repo.git"

        mock_api_rate_limit.return_value = (mock_api, "TOKEN", "USER")
        mock_repo_api.return_value = Mock()
        mock_get_repo.return_value = mock_repository
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code
        mock_repo_local_data.return_value = {}
        mock_process_push.return_value = None

        headers = Headers({"X-GitHub-Event": "push"})
        webhook = GithubWebhook(hook_data=push_payload, headers=headers, logger=Mock())

        with patch.object(webhook, "_clone_repository", new=AsyncMock(return_value=None)):
            await webhook.process()
        mock_process_push.assert_called_once()

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.handlers.issue_comment_handler.IssueCommentHandler.process_comment_webhook_data")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch("webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users")
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
            patch.object(webhook, "_clone_repository", new=AsyncMock(return_value=None)),
            patch.object(
                OwnersFileHandler,
                "initialize",
                new=AsyncMock(return_value=None),
            ),
        ):
            await webhook.process()
            mock_process_comment.assert_called_once()

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch("webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users")
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
        _ = gh.add_api_users_to_auto_verified_and_merged_users
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
    async def test_process_check_run_event(self) -> None:
        """Test processing check run event."""
        logger = Mock()
        check_run_data = {
            "action": "completed",
            "repository": {"name": "test-repo", "full_name": "org/test-repo"},
            "check_run": {"name": "test-check", "head_sha": "abc123", "status": "completed", "conclusion": "success"},
        }
        headers = Headers({"X-GitHub-Event": "check_run", "X-GitHub-Delivery": "abc"})

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
                                    with (
                                        patch.object(webhook, "_clone_repository", new=AsyncMock(return_value=None)),
                                        patch.object(
                                            OwnersFileHandler,
                                            "initialize",
                                            new=AsyncMock(return_value=None),
                                        ),
                                    ):
                                        await webhook.process()

                                    mock_check_handler.return_value.process_pull_request_check_run_webhook_data.assert_awaited_once()
                                    mock_pr_handler.return_value.check_if_can_be_merged.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_pull_request_by_number(
        self, minimal_hook_data: dict, minimal_headers: Headers, logger: Mock
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
        self, minimal_hook_data: dict, minimal_headers: Headers, logger: Mock
    ) -> None:
        """Test getting pull request with GithubException."""

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
        self, minimal_hook_data: dict, minimal_headers: Headers, logger: Mock
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
        self, minimal_hook_data: dict, minimal_headers: Headers, logger: Mock
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
        self, minimal_hook_data: dict, minimal_headers: Headers, logger: Mock
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
        self, minimal_hook_data: dict, minimal_headers: Headers, logger: Mock
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
        self, minimal_hook_data: dict, minimal_headers: Headers, logger: Mock
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

    def test_current_pull_request_supported_retest_property(
        self, minimal_hook_data: dict, minimal_headers: Headers, logger: Mock
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
    async def test_get_last_commit(self, minimal_hook_data: dict, minimal_headers: Headers, logger: Mock) -> None:
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

    @pytest.mark.asyncio
    async def test_clone_repository_success(
        self,
        minimal_hook_data: dict,
        minimal_headers: Headers,
        logger: Mock,
        get_value_side_effect: Callable[..., object],
        to_thread_sync: Callable[..., Awaitable[object]],
    ) -> None:
        """Test successful repository clone for PR."""
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}
            mock_config.return_value.get_value.side_effect = get_value_side_effect

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "test-token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_repo = Mock()
                    mock_repo.clone_url = "https://github.com/org/test-repo.git"
                    mock_owner = Mock()
                    mock_owner.login = "test-owner"
                    mock_repo.owner = mock_owner
                    mock_get_repo_api.return_value = mock_repo

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

                            # Mock pull request
                            mock_pr = Mock()
                            mock_base = Mock()
                            mock_base.ref = "main"
                            mock_pr.base = mock_base

                            # Mock run_command to succeed for all git operations
                            async def mock_run_command(*_args: object, **_kwargs: object) -> tuple[bool, str, str]:
                                return (True, "", "")

                            with (
                                patch("webhook_server.libs.github_api.run_command", side_effect=mock_run_command),
                                patch("asyncio.to_thread", side_effect=to_thread_sync),
                            ):
                                await gh._clone_repository(pull_request=mock_pr)

                                # Verify clone succeeded
                                assert gh._repo_cloned is True

    @pytest.mark.asyncio
    async def test_clone_repository_already_cloned(
        self, minimal_hook_data: dict, minimal_headers: Headers, logger: Mock
    ) -> None:
        """Test early return when repository already cloned."""
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "test-token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_get_repo_api.return_value = Mock()

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)
                            gh._repo_cloned = True  # Mark as already cloned

                            mock_pr = Mock()

                            with patch("webhook_server.libs.github_api.run_command") as mock_run_cmd:
                                await gh._clone_repository(pull_request=mock_pr)

                                # Verify run_command was never called
                                mock_run_cmd.assert_not_called()

    @pytest.mark.asyncio
    async def test_clone_repository_clone_failure(
        self,
        minimal_hook_data: dict,
        minimal_headers: Headers,
        logger: Mock,
        get_value_side_effect: Callable[..., object],
    ) -> None:
        """Test RuntimeError raised when git clone fails."""
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}
            mock_config.return_value.get_value.side_effect = get_value_side_effect

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "test-token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_repo = Mock()
                    mock_repo.clone_url = "https://github.com/org/test-repo.git"
                    mock_get_repo_api.return_value = mock_repo

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

                            mock_pr = Mock()

                            # Mock run_command to fail on clone
                            async def mock_run_command(command: str, **_kwargs: object) -> tuple[bool, str, str]:
                                if "git clone" in command:
                                    return (False, "", "Permission denied")
                                return (True, "", "")

                            with (
                                patch("webhook_server.libs.github_api.run_command", side_effect=mock_run_command),
                                pytest.raises(RuntimeError, match="Failed to clone repository"),
                            ):
                                await gh._clone_repository(pull_request=mock_pr)

    @pytest.mark.asyncio
    async def test_clone_repository_checkout_failure(
        self,
        minimal_hook_data: dict,
        minimal_headers: Headers,
        logger: Mock,
        get_value_side_effect: Callable[..., object],
        to_thread_sync: Callable[..., Awaitable[object]],
    ) -> None:
        """Test RuntimeError raised when git checkout fails."""
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}
            mock_config.return_value.get_value.side_effect = get_value_side_effect

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "test-token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_repo = Mock()
                    mock_repo.clone_url = "https://github.com/org/test-repo.git"
                    mock_owner = Mock()
                    mock_owner.login = "test-owner"
                    mock_repo.owner = mock_owner
                    mock_get_repo_api.return_value = mock_repo

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

                            # Mock pull request
                            mock_pr = Mock()
                            mock_base = Mock()
                            mock_base.ref = "main"
                            mock_pr.base = mock_base

                            # Mock run_command: succeed for clone/config, fail for checkout
                            async def mock_run_command(**kwargs: object) -> tuple[bool, str, str]:
                                command = kwargs.get("command", "")
                                if "checkout main" in command:
                                    return (False, "", "Branch not found")
                                return (True, "", "")

                            with (
                                patch("webhook_server.libs.github_api.run_command", side_effect=mock_run_command),
                                patch("asyncio.to_thread", side_effect=to_thread_sync),
                                pytest.raises(RuntimeError, match="Failed to checkout"),
                            ):
                                await gh._clone_repository(pull_request=mock_pr)

    @pytest.mark.asyncio
    async def test_clone_repository_git_config_warnings(
        self,
        minimal_hook_data: dict,
        minimal_headers: Headers,
        logger: Mock,
        get_value_side_effect: Callable[..., object],
        to_thread_sync: Callable[..., Awaitable[object]],
    ) -> None:
        """Test that git config failures log warnings but don't raise exceptions."""
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}
            mock_config.return_value.get_value.side_effect = get_value_side_effect

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "test-token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_repo = Mock()
                    mock_repo.clone_url = "https://github.com/org/test-repo.git"
                    mock_owner = Mock()
                    mock_owner.login = "test-owner"
                    mock_repo.owner = mock_owner
                    mock_get_repo_api.return_value = mock_repo

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

                            # Mock pull request
                            mock_pr = Mock()
                            mock_base = Mock()
                            mock_base.ref = "main"
                            mock_pr.base = mock_base
                            mock_pr.number = 123

                            # Mock run_command: succeed for clone/checkout, fail for config commands only
                            async def mock_run_command(**kwargs: object) -> tuple[bool, str, str]:
                                command = kwargs.get("command", "")
                                if "config user.name" in command or "config user.email" in command:
                                    return (False, "", "Config failed")
                                return (True, "", "")

                            mock_logger = Mock()

                            with (
                                patch("webhook_server.libs.github_api.run_command", side_effect=mock_run_command),
                                patch("asyncio.to_thread", side_effect=to_thread_sync),
                                patch.object(gh, "logger", mock_logger),
                            ):
                                await gh._clone_repository(pull_request=mock_pr)

                                # Verify clone succeeded despite config failures
                                assert gh._repo_cloned is True

                                # Verify warnings were logged for each config failure
                                warning_calls = [call for call in mock_logger.warning.call_args_list]
                                assert len(warning_calls) == 2  # user.name, user.email

    @pytest.mark.asyncio
    async def test_clone_repository_pr_ref_fetch_failure_raises(
        self,
        minimal_hook_data: dict,
        minimal_headers: Headers,
        logger: Mock,
        get_value_side_effect: Callable[..., object],
        to_thread_sync: Callable[..., Awaitable[object]],
    ) -> None:
        """Test that PR ref fetch failure raises RuntimeError (fatal error)."""
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}
            mock_config.return_value.get_value.side_effect = get_value_side_effect

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "test-token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_repo = Mock()
                    mock_repo.clone_url = "https://github.com/org/test-repo.git"
                    mock_owner = Mock()
                    mock_owner.login = "test-owner"
                    mock_repo.owner = mock_owner
                    mock_get_repo_api.return_value = mock_repo

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

                            # Mock pull request
                            mock_pr = Mock()
                            mock_base = Mock()
                            mock_base.ref = "main"
                            mock_pr.base = mock_base
                            mock_pr.number = 456

                            # Mock run_command: succeed for clone, fail for PR ref fetch
                            async def mock_run_command(**kwargs: object) -> tuple[bool, str, str]:
                                command = kwargs.get("command", "")
                                if "fetch origin +refs/pull/" in command:
                                    return (False, "", "Fetch failed: PR ref not found")
                                return (True, "", "")

                            mock_logger = Mock()

                            with (
                                patch("webhook_server.libs.github_api.run_command", side_effect=mock_run_command),
                                patch("asyncio.to_thread", side_effect=to_thread_sync),
                                patch.object(gh, "logger", mock_logger),
                            ):
                                with pytest.raises(RuntimeError) as exc_info:
                                    await gh._clone_repository(pull_request=mock_pr)

                                # Verify error message contains PR number and error
                                assert "456" in str(exc_info.value)
                                assert "Failed to fetch PR" in str(exc_info.value)

                                # Verify error was logged
                                error_calls = list(mock_logger.error.call_args_list)
                                assert len(error_calls) >= 1

    @pytest.mark.asyncio
    async def test_clone_repository_general_exception(
        self,
        minimal_hook_data: dict,
        minimal_headers: Headers,
        logger: Mock,
        get_value_side_effect: Callable[..., object],
    ) -> None:
        """Test exception handling during clone operation."""
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}
            mock_config.return_value.get_value.side_effect = get_value_side_effect

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "test-token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_repo = Mock()
                    mock_repo.clone_url = "https://github.com/org/test-repo.git"
                    mock_get_repo_api.return_value = mock_repo

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

                            mock_pr = Mock()

                            # Mock run_command to raise an exception
                            async def mock_run_command(*_args: object, **_kwargs: object) -> tuple[bool, str, str]:
                                raise ValueError("Unexpected error during git operation")

                            with (
                                patch("webhook_server.libs.github_api.run_command", side_effect=mock_run_command),
                                pytest.raises(RuntimeError, match="Repository clone failed"),
                            ):
                                await gh._clone_repository(pull_request=mock_pr)

    @pytest.mark.asyncio
    async def test_clone_repository_no_arguments(
        self, minimal_hook_data: dict, minimal_headers: Headers, logger: Mock
    ) -> None:
        """Test _clone_repository raises ValueError when no arguments provided."""
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "test-token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_get_repo_api.return_value = Mock()

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

                            # Test that calling _clone_repository with no arguments raises ValueError
                            with pytest.raises(ValueError, match="requires either pull_request or checkout_ref"):
                                await gh._clone_repository()

    @pytest.mark.asyncio
    async def test_clone_repository_empty_checkout_ref(
        self,
        minimal_hook_data: dict,
        minimal_headers: Headers,
        logger: Mock,
        tmp_path: Path,
    ) -> None:
        """Test _clone_repository raises ValueError when checkout_ref is empty string."""
        with (
            patch("webhook_server.libs.github_api.Config") as mock_config_cls,
            patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api,
            patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo,
            patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_github_app_api,
        ):
            # Setup mocks
            mock_config = Mock()
            mock_config.repository_data = {"enabled": True}
            mock_config.get_value.return_value = None
            mock_config.data_dir = str(tmp_path)
            mock_config_cls.return_value = mock_config

            mock_api = Mock()
            mock_api.get_rate_limit.return_value = Mock(rate=Mock(remaining=5000, limit=5000))
            mock_get_api.return_value = (mock_api, "test-token", "test-user")

            mock_repository = Mock()
            mock_repository.clone_url = "https://github.com/test/repo.git"
            mock_get_repo.return_value = mock_repository
            mock_get_github_app_api.return_value = mock_api

            # Create webhook
            webhook = GithubWebhook(
                hook_data=minimal_hook_data,
                headers=Headers(minimal_headers),
                logger=logger,
            )

            # Test that calling _clone_repository with empty string raises ValueError
            with pytest.raises(ValueError, match="requires either pull_request or checkout_ref"):
                await webhook._clone_repository(checkout_ref="")

    @pytest.mark.asyncio
    async def test_clone_repository_checkout_ref_fetch_path_for_tag(
        self,
        minimal_hook_data: dict,
        minimal_headers: Headers,
        logger: Mock,
        get_value_side_effect: Callable[..., object],
        to_thread_sync: Callable[..., Awaitable[object]],
    ) -> None:
        """Test _clone_repository with checkout_ref fetches and checks out the correct tag.

        Note: checkout_ref is now only used for tags, as branch pushes skip cloning.

        Verifies that when checkout_ref="refs/tags/v1.0.0" is provided:
        1. git fetch origin refs/tags/v1.0.0:refs/tags/v1.0.0 is called
        2. git checkout v1.0.0 is called
        """
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}
            mock_config.return_value.get_value.side_effect = get_value_side_effect

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "test-token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_repo = Mock()
                    mock_repo.clone_url = "https://github.com/org/test-repo.git"
                    mock_owner = Mock()
                    mock_owner.login = "test-owner"
                    mock_repo.owner = mock_owner
                    mock_get_repo_api.return_value = mock_repo

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

                            # Track commands executed
                            executed_commands: list[str] = []

                            async def mock_run_command(command: str, **_kwargs: object) -> tuple[bool, str, str]:
                                executed_commands.append(command)
                                return (True, "", "")

                            with (
                                patch(
                                    "webhook_server.libs.github_api.run_command",
                                    side_effect=mock_run_command,
                                ),
                                patch("asyncio.to_thread", side_effect=to_thread_sync),
                            ):
                                await gh._clone_repository(checkout_ref="refs/tags/v1.0.0")

                                # Verify clone succeeded
                                assert gh._repo_cloned is True

                                # Verify tag fetch command contains the expected refspec
                                # Using flexible matching to tolerate added git flags
                                tag_refspec = "refs/tags/v1.0.0:refs/tags/v1.0.0"
                                fetch_commands = [cmd for cmd in executed_commands if tag_refspec in cmd]
                                assert len(fetch_commands) == 1, (
                                    f"Expected exactly one fetch command containing refspec '{tag_refspec}', "
                                    f"got: {fetch_commands}"
                                )

                                # Verify checkout command contains the tag name
                                checkout_commands = [
                                    cmd for cmd in executed_commands if "checkout" in cmd and "v1.0.0" in cmd
                                ]
                                assert len(checkout_commands) == 1, (
                                    f"Expected exactly one checkout command for v1.0.0, got: {checkout_commands}"
                                )

    @pytest.mark.asyncio
    async def test_clone_repository_fetches_base_branch_for_pr(
        self,
        minimal_hook_data: dict,
        minimal_headers: Headers,
        logger: Mock,
        get_value_side_effect: Callable[..., object],
        to_thread_sync: Callable[..., Awaitable[object]],
    ) -> None:
        """Test _clone_repository fetches base branch before PR ref when pull_request is provided.

        Verifies that when _clone_repository is called with a pull_request:
        1. git fetch origin {base_ref} is called first (base branch fetch)
        2. git fetch origin +refs/pull/{pr_number}/head:refs/remotes/origin/pr/{pr_number} is called
        3. The base branch fetch happens BEFORE the PR ref fetch
        """
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}
            mock_config.return_value.get_value.side_effect = get_value_side_effect

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "test-token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api:
                    mock_repo = Mock()
                    mock_repo.clone_url = "https://github.com/org/test-repo.git"
                    mock_owner = Mock()
                    mock_owner.login = "test-owner"
                    mock_repo.owner = mock_owner
                    mock_get_repo_api.return_value = mock_repo

                    with patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api:
                        mock_get_app_api.return_value = Mock()

                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix") as mock_color:
                            mock_color.return_value = "test-repo"

                            gh = GithubWebhook(minimal_hook_data, minimal_headers, logger)

                            # Mock pull request with base.ref = "release-1.0" and number = 123
                            mock_pr = Mock()
                            mock_base = Mock()
                            mock_base.ref = "release-1.0"
                            mock_pr.base = mock_base
                            mock_pr.number = 123

                            # Track commands executed in order
                            executed_commands: list[str] = []

                            async def mock_run_command(command: str, **_kwargs: object) -> tuple[bool, str, str]:
                                executed_commands.append(command)
                                return (True, "", "")

                            with (
                                patch(
                                    "webhook_server.libs.github_api.run_command",
                                    side_effect=mock_run_command,
                                ),
                                patch("asyncio.to_thread", side_effect=to_thread_sync),
                            ):
                                await gh._clone_repository(pull_request=mock_pr)

                                # Verify clone succeeded
                                assert gh._repo_cloned is True

                                # Verify base branch fetch command contains the branch name
                                # Using flexible matching to tolerate added git flags
                                base_branch = "release-1.0"
                                base_fetch_commands = [
                                    cmd
                                    for cmd in executed_commands
                                    if "fetch" in cmd and base_branch in cmd and "refs/pull" not in cmd
                                ]
                                assert len(base_fetch_commands) == 1, (
                                    f"Expected exactly one fetch command for base branch '{base_branch}', "
                                    f"got: {base_fetch_commands}"
                                )

                                # Verify PR ref fetch command contains the expected refspec
                                pr_refspec = "+refs/pull/123/head:refs/remotes/origin/pr/123"
                                pr_fetch_commands = [cmd for cmd in executed_commands if pr_refspec in cmd]
                                assert len(pr_fetch_commands) == 1, (
                                    f"Expected exactly one PR ref fetch command containing '{pr_refspec}', "
                                    f"got: {pr_fetch_commands}"
                                )

                                # Verify order: base branch fetch should come BEFORE PR ref fetch
                                # Use index into executed_commands for ordering check
                                base_fetch_index = next(
                                    i
                                    for i, cmd in enumerate(executed_commands)
                                    if "fetch" in cmd and base_branch in cmd and "refs/pull" not in cmd
                                )
                                pr_fetch_index = next(i for i, cmd in enumerate(executed_commands) if pr_refspec in cmd)
                                assert base_fetch_index < pr_fetch_index, (
                                    f"Base branch fetch (index {base_fetch_index}) should come before "
                                    f"PR ref fetch (index {pr_fetch_index}). "
                                    f"Commands: {executed_commands}"
                                )

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch("webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users")
    async def test_process_push_event_deletion(
        self,
        mock_auto_verified_prop: Mock,
        mock_repo_local_data: Mock,
        mock_get_apis: Mock,
        mock_api_rate_limit: Mock,
        mock_repo_api: Mock,
        mock_get_repo: Mock,
    ) -> None:
        """Test that push event with deletion=True is skipped without cloning repository.

        Verifies:
        - Processing is skipped when hook_data["deleted"] == True
        - _clone_repository is NOT called
        - Appropriate log messages are generated
        - Returns None
        """
        # Prepare deletion push payload
        push_deletion_payload = {
            "repository": {"name": "test-repo", "full_name": "my-org/test-repo"},
            "ref": "refs/heads/feature-branch",
            "deleted": True,  # Key field indicating deletion
            "commits": [],
        }

        # Mock GitHub API to prevent network calls
        mock_api = Mock()
        mock_api.rate_limiting = [100, 5000]
        mock_user = Mock()
        mock_user.login = "test-user"
        mock_api.get_user.return_value = mock_user
        mock_api.get_rate_limit.return_value = Mock(rate=Mock(remaining=4990))

        # Mock repository with proper clone_url
        mock_repository = Mock()
        mock_repository.clone_url = "https://github.com/test/repo.git"

        mock_api_rate_limit.return_value = (mock_api, "TOKEN", "USER")
        mock_repo_api.return_value = Mock()
        mock_get_repo.return_value = mock_repository
        mock_get_apis.return_value = []  # Return empty list to skip the problematic property code
        mock_repo_local_data.return_value = {}

        headers = Headers({"X-GitHub-Event": "push", "X-GitHub-Delivery": "test-deletion-123"})

        # Create mock logger to verify log messages
        mock_logger = Mock()
        webhook = GithubWebhook(hook_data=push_deletion_payload, headers=headers, logger=mock_logger)

        # Mock _clone_repository to verify it's NOT called
        with patch.object(webhook, "_clone_repository", new=AsyncMock()) as mock_clone:
            result = await webhook.process()

            # Verify _clone_repository was NOT called (deletion should skip cloning)
            mock_clone.assert_not_called()

            # Verify return value is None (process() returns None now)
            assert result is None

            # Verify appropriate log messages
            # Check that deletion detection was logged
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("deletion detected" in call.lower() for call in info_calls), (
                f"Expected 'deletion detected' in info logs. Got: {info_calls}"
            )

            # Verify completion log with "deletion event (skipped)" message
            assert any("deletion event (skipped)" in call.lower() for call in info_calls), (
                f"Expected 'deletion event (skipped)' in info logs. Got: {info_calls}"
            )

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.handlers.push_handler.PushHandler.process_push_webhook_data")
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.libs.config.Config.repository_local_data")
    @patch("webhook_server.libs.github_api.GithubWebhook.add_api_users_to_auto_verified_and_merged_users")
    async def test_process_push_event_branch_push_skips_clone(
        self,
        mock_auto_verified_prop: Mock,
        mock_repo_local_data: Mock,
        mock_get_apis: Mock,
        mock_process_push: Mock,
        mock_api_rate_limit: Mock,
        mock_repo_api: Mock,
        mock_get_repo: Mock,
    ) -> None:
        """Test that branch push event skips cloning.

        Branch pushes don't require cloning because PushHandler only
        processes tags (PyPI upload, container build).

        Verifies:
        - _clone_repository is NOT called for branch pushes
        - PushHandler.process_push_webhook_data is NOT called
        - Returns None
        """
        # Branch push payload
        push_branch_payload = {
            "repository": {"name": "test-repo", "full_name": "my-org/test-repo"},
            "ref": "refs/heads/main",
            "deleted": False,
            "commits": [{"id": "abc123", "message": "Test commit", "author": {"name": "Test User"}}],
        }

        # Mock GitHub API to prevent network calls
        mock_api = Mock()
        mock_api.rate_limiting = [100, 5000]
        mock_user = Mock()
        mock_user.login = "test-user"
        mock_api.get_user.return_value = mock_user

        # Mock repository with proper clone_url
        mock_repository = Mock()
        mock_repository.clone_url = "https://github.com/test/repo.git"

        mock_api_rate_limit.return_value = (mock_api, "TOKEN", "USER")
        mock_repo_api.return_value = Mock()
        mock_get_repo.return_value = mock_repository
        mock_get_apis.return_value = []
        mock_repo_local_data.return_value = {}

        headers = Headers({"X-GitHub-Event": "push", "X-GitHub-Delivery": "test-branch-push-456"})
        mock_logger = Mock()
        webhook = GithubWebhook(hook_data=push_branch_payload, headers=headers, logger=mock_logger)

        # Mock _clone_repository to verify it is NOT called for branch pushes
        with patch.object(webhook, "_clone_repository", new=AsyncMock(return_value=None)) as mock_clone:
            result = await webhook.process()

            # Verify _clone_repository was NOT called (branch push skips cloning)
            mock_clone.assert_not_called()

            # Verify PushHandler.process_push_webhook_data was NOT called
            mock_process_push.assert_not_called()

            # Verify return value is None
            assert result is None

    @pytest.mark.asyncio
    async def test_cleanup(
        self,
        minimal_hook_data: dict,
        minimal_headers: Headers,
        to_thread_sync: Callable[..., Awaitable[object]],
    ) -> None:
        """Test cleanup method removes temporary directory."""
        mock_logger = Mock()
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api"):
                    with patch("webhook_server.libs.github_api.get_repository_github_app_api"):
                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix"):
                            gh = GithubWebhook(minimal_hook_data, minimal_headers, mock_logger)

                            # Set a fake clone dir
                            gh.clone_repo_dir = "/tmp/fake-clone-dir"

                            with patch("os.path.exists", return_value=True):
                                with patch("shutil.rmtree") as mock_rmtree:
                                    with patch("asyncio.to_thread", side_effect=to_thread_sync):
                                        await gh.cleanup()

                                        mock_rmtree.assert_called_once_with("/tmp/fake-clone-dir", ignore_errors=True)
                                        mock_logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_cleanup_exception(
        self,
        minimal_hook_data: dict,
        minimal_headers: Headers,
        to_thread_sync: Callable[..., Awaitable[object]],
    ) -> None:
        """Test cleanup method handles exceptions."""
        mock_logger = Mock()
        with patch("webhook_server.libs.github_api.Config") as mock_config:
            mock_config.return_value.repository = True
            mock_config.return_value.repository_local_data.return_value = {}

            with patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api:
                mock_get_api.return_value = (Mock(), "token", "apiuser")

                with patch("webhook_server.libs.github_api.get_github_repo_api"):
                    with patch("webhook_server.libs.github_api.get_repository_github_app_api"):
                        with patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix"):
                            gh = GithubWebhook(minimal_hook_data, minimal_headers, mock_logger)

                            gh.clone_repo_dir = "/tmp/fake-clone-dir"

                            with patch("os.path.exists", return_value=True):

                                def rmtree_fail(*args, **kwargs):
                                    raise PermissionError("Access denied")

                                with patch("shutil.rmtree", side_effect=rmtree_fail):
                                    with patch("asyncio.to_thread", side_effect=to_thread_sync):
                                        await gh.cleanup()

                                        mock_logger.warning.assert_called()

    @patch("webhook_server.libs.github_api.Config")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    def test_enabled_labels_with_non_string_entries_logs_warning(
        self,
        mock_color: Mock,
        mock_get_app_api: Mock,
        mock_get_repo_api: Mock,
        mock_get_api: Mock,
        mock_config: Mock,
        minimal_hook_data: dict,
        minimal_headers: Headers,
    ) -> None:
        """Test that non-string entries in enabled-labels are sanitized and logged."""
        mock_logger = Mock()

        # Configure mock to return enabled-labels with non-string items
        def get_value_side_effect(value: str, *_args: object, **kwargs: object) -> object:
            if value == "labels":
                # Return labels config with non-string entries in enabled-labels
                return {
                    "enabled-labels": [
                        "verified",  # Valid string - valid category
                        {"key1": "val1", "key2": "val2"},  # Dict - should be sanitized
                        ["nested", "list"],  # List - should be sanitized
                        12345,  # Integer - should be sanitized
                    ]
                }
            if value == "mask-sensitive-data":
                return True
            return kwargs.get("return_on_none")

        mock_config.return_value.repository = True
        mock_config.return_value.repository_local_data.return_value = {}
        mock_config.return_value.get_value.side_effect = get_value_side_effect
        mock_get_api.return_value = (Mock(), "token", "apiuser")
        mock_get_repo_api.return_value = Mock()
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"

        gh = GithubWebhook(minimal_hook_data, minimal_headers, mock_logger)

        # Verify warning was logged about non-string entries
        mock_logger.warning.assert_called()
        # Get all warning calls and check the first one (non-string entries warning)
        warning_calls = [call[0][0] for call in mock_logger.warning.call_args_list]
        non_string_warning = warning_calls[0]
        assert "Non-string entries in enabled-labels were ignored" in non_string_warning
        assert "dict(keys=" in non_string_warning
        assert "list(len=2)" in non_string_warning
        assert "int(" in non_string_warning

        # Verify only valid string entries are kept (and filtered to valid categories)
        assert gh.enabled_labels is not None
        assert "verified" in gh.enabled_labels
