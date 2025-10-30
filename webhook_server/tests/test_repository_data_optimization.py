"""
Comprehensive tests for repository_data optimization feature.

Tests cover:
1. get_comprehensive_repository_data with configurable limits
2. repository_data fetch in GithubWebhook.process()
3. Pre-fetched data usage in unified_api methods
4. OwnersFileHandler uses pre-fetched data
5. PullRequestHandler passes repository_data
"""

from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from webhook_server.libs.github_api import GithubWebhook
from webhook_server.libs.graphql.graphql_client import GraphQLError
from webhook_server.libs.graphql.unified_api import UnifiedGitHubAPI
from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
from webhook_server.libs.handlers.pull_request_handler import PullRequestHandler

# Test token constant to avoid security warnings
TEST_GITHUB_TOKEN = "ghs_" + "test1234567890abcdefghijklmnopqrstuvwxyz"  # pragma: allowlist secret


@pytest.fixture
def mock_logger():
    """Create a mock logger for testing."""
    return Mock()


@pytest.fixture
def mock_config():
    """Create a mock config."""
    config = Mock()
    config.get_value = Mock(return_value=9)  # For tree-max-depth
    config.repository = None  # Allow repository attribute to be set dynamically
    return config


@pytest.fixture
def unified_api(mock_logger, mock_config):
    """Create UnifiedGitHubAPI instance."""
    return UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)


@pytest.fixture
def mock_comprehensive_data() -> dict[str, Any]:
    """Mock comprehensive repository data from GraphQL."""
    return {
        "id": "R_kgDOTestRepo",
        "name": "test-repo",
        "nameWithOwner": "owner/test-repo",
        "owner": {"id": "U_kgDOTestOwner", "login": "owner"},
        "collaborators": {
            "edges": [
                {
                    "permission": "ADMIN",
                    "node": {
                        "id": "U_kgDOCollab1",
                        "login": "collab1",
                        "name": "Collaborator 1",
                        "email": "collab1@example.com",
                        "avatarUrl": "https://avatars.example.com/collab1",
                    },
                },
                {
                    "permission": "WRITE",
                    "node": {
                        "id": "U_kgDOCollab2",
                        "login": "collab2",
                        "name": "Collaborator 2",
                        "email": "collab2@example.com",
                        "avatarUrl": "https://avatars.example.com/collab2",
                    },
                },
            ]
        },
        "mentionableUsers": {
            "nodes": [
                {
                    "id": "U_kgDOContrib1",
                    "login": "contrib1",
                    "name": "Contributor 1",
                    "email": "contrib1@example.com",
                    "avatarUrl": "https://avatars.example.com/contrib1",
                },
                {
                    "id": "U_kgDOContrib2",
                    "login": "contrib2",
                    "name": "Contributor 2",
                    "email": "contrib2@example.com",
                    "avatarUrl": "https://avatars.example.com/contrib2",
                },
            ]
        },
        "issues": {
            "nodes": [
                {
                    "id": "I_kgDOIssue1",
                    "number": 10,
                    "title": "Test Issue 1",
                    "body": "Issue body 1",
                    "state": "OPEN",
                    "createdAt": "2025-01-01T00:00:00Z",
                    "updatedAt": "2025-01-02T00:00:00Z",
                    "author": {"login": "user1"},
                    "labels": {"nodes": [{"id": "L_kgDOLabel1", "name": "bug", "color": "d73a4a"}]},
                },
                {
                    "id": "I_kgDOIssue2",
                    "number": 11,
                    "title": "Test Issue 2",
                    "body": "Issue body 2",
                    "state": "OPEN",
                    "createdAt": "2025-01-03T00:00:00Z",
                    "updatedAt": "2025-01-04T00:00:00Z",
                    "author": {"login": "user2"},
                    "labels": {"nodes": [{"id": "L_kgDOLabel2", "name": "enhancement", "color": "a2eeef"}]},
                },
            ]
        },
        "pullRequests": {
            "nodes": [
                {
                    "id": "PR_kgDOPR1",
                    "number": 20,
                    "title": "Test PR 1",
                    "state": "OPEN",
                    "baseRefName": "main",
                    "headRefName": "feature1",
                    "author": {"login": "dev1"},
                    "createdAt": "2025-01-05T00:00:00Z",
                    "updatedAt": "2025-01-06T00:00:00Z",
                },
                {
                    "id": "PR_kgDOPR2",
                    "number": 21,
                    "title": "Test PR 2",
                    "state": "OPEN",
                    "baseRefName": "main",
                    "headRefName": "feature2",
                    "author": {"login": "dev2"},
                    "createdAt": "2025-01-07T00:00:00Z",
                    "updatedAt": "2025-01-08T00:00:00Z",
                },
            ]
        },
    }


# ===== Test Category 1: get_comprehensive_repository_data with configurable limits =====


@pytest.mark.asyncio
async def test_comprehensive_data_default_limits(unified_api, mock_logger, mock_comprehensive_data, mock_config):
    """Test get_comprehensive_repository_data uses default limits (100) when config not specified."""
    # Reset mock_config for this test
    mock_config.get_value.reset_mock()
    mock_config.get_value.return_value = 100  # Default limit

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        # Mock GraphQL client
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"repository": mock_comprehensive_data})
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()

        # Execute
        result = await unified_api.get_comprehensive_repository_data("owner", "repo")

        # Verify config calls (4 config lookups for limits)
        # Since unified_api has self.config set, it reuses it instead of creating new Config
        assert mock_config.get_value.call_count == 4

        # Verify GraphQL query used default limits (100)
        call_args = mock_gql.execute.call_args
        query = call_args[0][0]  # First positional argument is the query string
        assert "collaborators(first: 100)" in query
        assert "mentionableUsers(first: 100)" in query
        assert "issues(first: 100" in query
        assert "pullRequests(first: 100" in query

        # Verify result structure
        assert result == mock_comprehensive_data
        assert len(result["collaborators"]["edges"]) == 2
        assert len(result["mentionableUsers"]["nodes"]) == 2


@pytest.mark.asyncio
async def test_comprehensive_data_custom_limits_from_config(unified_api, mock_comprehensive_data, mock_config):
    """Test get_comprehensive_repository_data respects custom config limits."""
    # Reset and configure mock_config with custom limits
    mock_config.get_value.reset_mock()
    mock_config.get_value.side_effect = [
        50,  # collaborators
        75,  # contributors
        30,  # issues
        60,  # pull-requests
    ]

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        # Mock GraphQL client
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"repository": mock_comprehensive_data})
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()

        # Execute
        result = await unified_api.get_comprehensive_repository_data("owner", "repo")

        # Verify GraphQL query used custom limits
        call_args = mock_gql.execute.call_args
        query = call_args[0][0]
        assert "collaborators(first: 50)" in query
        assert "mentionableUsers(first: 75)" in query
        assert "issues(first: 30" in query
        assert "pullRequests(first: 60" in query

        # Verify result
        assert result == mock_comprehensive_data


@pytest.mark.asyncio
async def test_comprehensive_data_per_repository_override(unified_api, mock_comprehensive_data, mock_config):
    """Test per-repository override from .github-webhook-server.yaml."""
    # Reset and configure mock_config with per-repo overrides
    mock_config.get_value.reset_mock()
    mock_config.get_value.side_effect = [
        200,  # collaborators (per-repo override)
        150,  # contributors (per-repo override)
        100,  # issues
        100,  # pull-requests
    ]

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        # Mock GraphQL client
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"repository": mock_comprehensive_data})
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()

        # Execute
        result = await unified_api.get_comprehensive_repository_data("owner", "repo")

        # Verify per-repo limits applied
        call_args = mock_gql.execute.call_args
        query = call_args[0][0]
        assert "collaborators(first: 200)" in query
        assert "mentionableUsers(first: 150)" in query

        # Verify result
        assert result == mock_comprehensive_data


@pytest.mark.asyncio
async def test_comprehensive_data_graphql_failure(unified_api):
    """Test get_comprehensive_repository_data fail-fast on GraphQL error."""
    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
        patch("webhook_server.libs.graphql.unified_api.Config") as mock_config_class,
    ):
        # Mock Config
        mock_config = Mock()
        mock_config.get_value.return_value = 100
        mock_config_class.return_value = mock_config

        # Mock GraphQL client to raise error
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(side_effect=GraphQLError("Test GraphQL error"))
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()

        # Execute and verify exception propagates
        with pytest.raises(GraphQLError, match="Test GraphQL error"):
            await unified_api.get_comprehensive_repository_data("owner", "repo")


# ===== Test Category 2: repository_data fetch in GithubWebhook.process() =====


@pytest.mark.asyncio
async def test_webhook_process_fetches_repository_data(mock_comprehensive_data):
    """Test repository_data fetch happens after PR data, before handler initialization."""
    with (
        patch("webhook_server.libs.github_api.Config") as mock_config,
        patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api,
        patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api,
        patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api,
        patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix"),
    ):
        # Setup mocks
        mock_config_inst = Mock()
        mock_config_inst.repository_data = {"name": "test-repo"}
        mock_config_inst.repository_local_data.return_value = {}
        mock_config_inst.get_value.return_value = None
        mock_config.return_value = mock_config_inst

        mock_api = Mock()
        mock_api.rate_limiting = (5000, 4999, 1234567890)
        mock_api.get_user = Mock(return_value=Mock(login="test-user"))
        mock_get_api.return_value = (mock_api, TEST_GITHUB_TOKEN, "test-user")

        mock_repo = Mock()
        mock_repo.full_name = "owner/test-repo"
        mock_repo.name = "test-repo"
        mock_get_repo_api.return_value = mock_repo
        mock_get_app_api.return_value = mock_api

        # Create webhook instance
        hook_data = {
            "action": "opened",
            "repository": {"name": "test-repo", "full_name": "owner/test-repo", "node_id": "R_test", "id": 12345},
            "pull_request": {
                "number": 123,
                "title": "Test PR",
                "draft": False,
                "merged": False,
                "user": {"login": "testuser", "type": "User"},
                "base": {"ref": "main"},
                "head": {"sha": "abc123", "user": {"login": "testuser"}},
                "id": "PR_test",
            },
        }
        headers = {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "test-123"}

        webhook = GithubWebhook(hook_data=hook_data, headers=headers, logger=Mock())

        # Mock unified_api methods
        webhook.unified_api.get_comprehensive_repository_data = AsyncMock(return_value=mock_comprehensive_data)
        webhook.unified_api.get_last_commit = AsyncMock(return_value=Mock(sha="abc123", committer=Mock(login="user")))

        # Mock handlers to avoid actual processing
        with (
            patch("webhook_server.libs.github_api.OwnersFileHandler") as mock_owners,
            patch("webhook_server.libs.github_api.PullRequestHandler") as mock_pr_handler,
        ):
            mock_owners_inst = AsyncMock()
            mock_owners_inst.initialize = AsyncMock(return_value=mock_owners_inst)
            mock_owners.return_value = mock_owners_inst

            mock_pr_handler_inst = AsyncMock()
            mock_pr_handler_inst.process_pull_request_webhook_data = AsyncMock()
            mock_pr_handler.return_value = mock_pr_handler_inst

            # Execute
            await webhook.process()

            # Verify get_comprehensive_repository_data was called
            webhook.unified_api.get_comprehensive_repository_data.assert_called_once_with("owner", "test-repo")

            # Verify repository_data was stored
            assert webhook.repository_data == mock_comprehensive_data


@pytest.mark.asyncio
async def test_webhook_process_fail_fast_on_repository_data_error():
    """Test webhook processing aborts (fail-fast) when repository_data fetch fails."""
    with (
        patch("webhook_server.libs.github_api.Config") as mock_config,
        patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api,
        patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api,
        patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api,
        patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix"),
    ):
        # Setup mocks
        mock_config_inst = Mock()
        mock_config_inst.repository_data = {"name": "test-repo"}
        mock_config_inst.repository_local_data.return_value = {}
        mock_config_inst.get_value.return_value = None
        mock_config.return_value = mock_config_inst

        mock_api = Mock()
        mock_api.rate_limiting = (5000, 4999, 1234567890)
        mock_api.get_user = Mock(return_value=Mock(login="test-user"))
        mock_get_api.return_value = (mock_api, TEST_GITHUB_TOKEN, "test-user")

        mock_repo = Mock()
        mock_repo.full_name = "owner/test-repo"
        mock_repo.name = "test-repo"
        mock_get_repo_api.return_value = mock_repo
        mock_get_app_api.return_value = mock_api

        # Create webhook instance
        hook_data = {
            "action": "opened",
            "repository": {"name": "test-repo", "full_name": "owner/test-repo", "node_id": "R_test", "id": 12345},
            "pull_request": {
                "number": 123,
                "title": "Test PR",
                "draft": False,
                "merged": False,
                "user": {"login": "testuser", "type": "User"},
                "base": {"ref": "main"},
                "head": {"sha": "abc123", "user": {"login": "testuser"}},
                "id": "PR_test",
            },
        }
        headers = {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "test-123"}

        webhook = GithubWebhook(hook_data=hook_data, headers=headers, logger=Mock())

        # Mock unified_api to raise exception
        webhook.unified_api.get_comprehensive_repository_data = AsyncMock(
            side_effect=GraphQLError("API rate limit exceeded")
        )
        webhook.unified_api.get_last_commit = AsyncMock(return_value=Mock(sha="abc123", committer=Mock(login="user")))

        # Execute and verify exception propagates (fail-fast)
        with pytest.raises(GraphQLError, match="API rate limit exceeded"):
            await webhook.process()


@pytest.mark.asyncio
async def test_webhook_process_push_event_skips_repository_data():
    """Test PushHandler exits before repository_data fetch (optimization)."""
    with (
        patch("webhook_server.libs.github_api.Config") as mock_config,
        patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit") as mock_get_api,
        patch("webhook_server.libs.github_api.get_github_repo_api") as mock_get_repo_api,
        patch("webhook_server.libs.github_api.get_repository_github_app_api") as mock_get_app_api,
        patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix"),
        patch("webhook_server.libs.github_api.PushHandler") as mock_push_handler,
    ):
        # Setup mocks
        mock_config_inst = Mock()
        mock_config_inst.repository_data = {"name": "test-repo"}
        mock_config_inst.repository_local_data.return_value = {}
        mock_config_inst.get_value.return_value = None
        mock_config.return_value = mock_config_inst

        mock_api = Mock()
        mock_api.rate_limiting = (5000, 4999, 1234567890)
        mock_api.get_user = Mock(return_value=Mock(login="test-user"))
        mock_get_api.return_value = (mock_api, TEST_GITHUB_TOKEN, "test-user")

        mock_repo = Mock()
        mock_repo.full_name = "owner/test-repo"
        mock_repo.name = "test-repo"
        mock_get_repo_api.return_value = mock_repo
        mock_get_app_api.return_value = mock_api

        # Create webhook instance with push event
        hook_data = {
            "repository": {"name": "test-repo", "full_name": "owner/test-repo"},
            "ref": "refs/heads/main",
        }
        headers = {"X-GitHub-Event": "push", "X-GitHub-Delivery": "test-123"}

        webhook = GithubWebhook(hook_data=hook_data, headers=headers, logger=Mock())

        # Mock PushHandler
        mock_push_inst = Mock()
        mock_push_inst.process_push_webhook_data = AsyncMock()
        mock_push_handler.return_value = mock_push_inst

        # Mock unified_api
        webhook.unified_api.get_comprehensive_repository_data = AsyncMock()

        # Execute
        await webhook.process()

        # Verify get_comprehensive_repository_data was NOT called for push event
        webhook.unified_api.get_comprehensive_repository_data.assert_not_called()


# ===== Test Category 3: Pre-fetched data usage in unified_api methods =====


@pytest.mark.asyncio
async def test_get_contributors_uses_prefetched_data(unified_api, mock_comprehensive_data):
    """Test get_contributors() uses repository_data when provided (no API call)."""
    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock()  # Should not be called
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()

        # Execute with pre-fetched repository_data
        result = await unified_api.get_contributors("owner", "repo", repository_data=mock_comprehensive_data)

        # Verify no GraphQL query was made
        mock_gql.execute.assert_not_called()

        # Verify result came from pre-fetched data
        assert result == mock_comprehensive_data["mentionableUsers"]["nodes"]
        assert len(result) == 2
        assert result[0]["login"] == "contrib1"


@pytest.mark.asyncio
async def test_get_contributors_fallback_to_api(unified_api):
    """Test get_contributors() queries GraphQL when repository_data not provided."""
    contributors_data = {
        "repository": {
            "mentionableUsers": {
                "nodes": [
                    {"id": "U_1", "login": "user1", "name": "User 1", "email": "user1@example.com", "avatarUrl": "url1"}
                ]
            }
        }
    }

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value=contributors_data)
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()

        # Execute WITHOUT repository_data (fallback to API)
        result = await unified_api.get_contributors("owner", "repo", repository_data=None)

        # Verify GraphQL query WAS made
        mock_gql.execute.assert_called_once()

        # Verify result
        assert result == contributors_data["repository"]["mentionableUsers"]["nodes"]


@pytest.mark.asyncio
async def test_get_collaborators_uses_prefetched_data(unified_api, mock_comprehensive_data):
    """Test get_collaborators() uses repository_data when provided (no API call)."""
    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock()  # Should not be called
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()

        # Execute with pre-fetched repository_data
        result = await unified_api.get_collaborators("owner", "repo", repository_data=mock_comprehensive_data)

        # Verify no GraphQL query was made
        mock_gql.execute.assert_not_called()

        # Verify result came from pre-fetched data
        assert result == mock_comprehensive_data["collaborators"]["edges"]
        assert len(result) == 2
        assert result[0]["node"]["login"] == "collab1"


@pytest.mark.asyncio
async def test_get_issues_uses_prefetched_data_for_open_state(unified_api, mock_comprehensive_data):
    """Test get_issues() uses repository_data for OPEN states (no API call)."""
    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock()  # Should not be called
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()

        # Execute with OPEN state (default) and pre-fetched data
        result = await unified_api.get_issues("owner", "repo", states=["OPEN"], repository_data=mock_comprehensive_data)

        # Verify no GraphQL query was made
        mock_gql.execute.assert_not_called()

        # Verify result came from pre-fetched data
        assert result == mock_comprehensive_data["issues"]["nodes"]
        assert len(result) == 2
        assert result[0]["number"] == 10


@pytest.mark.asyncio
async def test_get_issues_queries_graphql_for_non_open_states(unified_api):
    """Test get_issues() queries GraphQL for non-OPEN states (CLOSED, etc.)."""
    closed_issues_data = {
        "repository": {
            "issues": {
                "nodes": [
                    {
                        "id": "I_closed1",
                        "number": 99,
                        "title": "Closed Issue",
                        "body": "Body",
                        "state": "CLOSED",
                        "createdAt": "2025-01-01T00:00:00Z",
                        "updatedAt": "2025-01-02T00:00:00Z",
                        "author": {"login": "user1"},
                        "labels": {"nodes": []},
                    }
                ]
            }
        }
    }

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value=closed_issues_data)
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()

        # Execute with CLOSED state (NOT OPEN)
        result = await unified_api.get_issues("owner", "repo", states=["CLOSED"], repository_data=None)

        # Verify GraphQL query WAS made (CLOSED issues not in pre-fetched data)
        mock_gql.execute.assert_called_once()

        # Verify result
        assert result == closed_issues_data["repository"]["issues"]["nodes"]
        assert result[0]["state"] == "CLOSED"


# ===== Test Category 4: OwnersFileHandler uses pre-fetched data =====


@pytest.mark.asyncio
async def test_owners_file_handler_uses_prefetched_data():
    """Test OwnersFileHandler.initialize() uses pre-fetched data (no API calls)."""
    # Mock webhook with repository_data
    mock_webhook = Mock()
    mock_webhook.logger = Mock()
    mock_webhook.log_prefix = "[TEST]"
    mock_webhook.repository = Mock()
    mock_webhook.repository.full_name = "owner/test-repo"
    mock_webhook.config = Mock()
    mock_webhook.config.get_value.return_value = 1000

    # Mock repository_data (pre-fetched)
    mock_webhook.repository_data = {
        "collaborators": {
            "edges": [
                {
                    "permission": "ADMIN",
                    "node": {"login": "admin1", "name": "Admin User", "email": "admin@example.com"},
                },
                {
                    "permission": "WRITE",
                    "node": {"login": "writer1", "name": "Writer User", "email": "writer@example.com"},
                },
            ]
        },
        "mentionableUsers": {
            "nodes": [
                {"login": "contrib1", "name": "Contributor 1", "email": "contrib1@example.com"},
                {"login": "contrib2", "name": "Contributor 2", "email": "contrib2@example.com"},
            ]
        },
    }

    # Mock unified_api (should not be called for collaborators/contributors)
    mock_webhook.unified_api = Mock()
    mock_webhook.unified_api.get_collaborators = AsyncMock()
    mock_webhook.unified_api.get_contributors = AsyncMock()
    mock_webhook.unified_api.get_pull_request_files = AsyncMock(return_value=[])

    # Mock pull request
    mock_pr = Mock()
    mock_pr.number = 123
    mock_pr.base.ref = "main"

    # Mock OWNERS file data access
    with (
        patch.object(OwnersFileHandler, "get_all_repository_approvers_and_reviewers", return_value={}),
        patch.object(OwnersFileHandler, "get_all_repository_approvers", return_value=[]),
        patch.object(OwnersFileHandler, "get_all_repository_reviewers", return_value=[]),
        patch.object(OwnersFileHandler, "get_all_pull_request_approvers", return_value=[]),
        patch.object(OwnersFileHandler, "get_all_pull_request_reviewers", return_value=[]),
    ):
        handler = OwnersFileHandler(mock_webhook)

        # Execute
        await handler.initialize(mock_pr)

        # Verify no API calls were made
        mock_webhook.unified_api.get_collaborators.assert_not_called()
        mock_webhook.unified_api.get_contributors.assert_not_called()

        # Verify SimpleNamespace conversion happened
        assert len(handler._repository_collaborators) == 2
        assert handler._repository_collaborators[0].login == "admin1"
        assert handler._repository_collaborators[0].permissions.admin is True  # ADMIN → admin=True

        assert len(handler._repository_contributors) == 2
        assert handler._repository_contributors[0].login == "contrib1"


@pytest.mark.asyncio
async def test_owners_file_handler_collaborator_permission_mapping():
    """Test collaborators permission mapping (ADMIN → admin=True)."""
    mock_webhook = Mock()
    mock_webhook.logger = Mock()
    mock_webhook.log_prefix = "[TEST]"
    mock_webhook.repository = Mock()
    mock_webhook.repository.full_name = "owner/test-repo"
    mock_webhook.config = Mock()
    mock_webhook.config.get_value.return_value = 1000

    # Mock repository_data with various permission levels
    mock_webhook.repository_data = {
        "collaborators": {
            "edges": [
                {"permission": "ADMIN", "node": {"login": "admin_user"}},
                {"permission": "MAINTAIN", "node": {"login": "maintain_user"}},
                {"permission": "WRITE", "node": {"login": "write_user"}},
            ]
        },
        "mentionableUsers": {"nodes": []},
    }

    mock_webhook.unified_api = Mock()
    mock_webhook.unified_api.get_pull_request_files = AsyncMock(return_value=[])

    mock_pr = Mock()
    mock_pr.number = 123
    mock_pr.base.ref = "main"

    with (
        patch.object(OwnersFileHandler, "get_all_repository_approvers_and_reviewers", return_value={}),
        patch.object(OwnersFileHandler, "get_all_repository_approvers", return_value=[]),
        patch.object(OwnersFileHandler, "get_all_repository_reviewers", return_value=[]),
        patch.object(OwnersFileHandler, "get_all_pull_request_approvers", return_value=[]),
        patch.object(OwnersFileHandler, "get_all_pull_request_reviewers", return_value=[]),
    ):
        handler = OwnersFileHandler(mock_webhook)
        await handler.initialize(mock_pr)

        # Verify permission mapping
        collabs = handler._repository_collaborators
        assert collabs[0].permissions.admin is True  # ADMIN
        assert collabs[0].permissions.maintain is False

        assert collabs[1].permissions.admin is False
        assert collabs[1].permissions.maintain is True  # MAINTAIN

        assert collabs[2].permissions.admin is False
        assert collabs[2].permissions.maintain is False  # WRITE


# ===== Test Category 5: PullRequestHandler passes repository_data =====


@pytest.mark.asyncio
async def test_pull_request_handler_passes_repository_data():
    """Test PullRequestHandler.get_issues() calls include repository_data parameter."""
    # Mock webhook with repository_data
    mock_webhook = Mock()
    mock_webhook.logger = Mock()
    mock_webhook.log_prefix = "[TEST]"
    mock_webhook.repository = Mock()
    mock_webhook.repository.full_name = "owner/test-repo"
    mock_webhook.repository_full_name = "owner/test-repo"  # Direct attribute for _owner_and_repo property
    mock_webhook.config = Mock()
    mock_webhook.repository_data = {
        "issues": {
            "nodes": [{"id": "I_1", "number": 10, "title": "Test Issue", "body": "[Auto generated]\nNumber: [#123]"}]
        }
    }

    # Mock unified_api - return dict format for GraphQL compatibility
    mock_issue = {
        "id": "I_1",
        "number": 10,
        "title": "Test Issue",
        "body": "[Auto generated]\nNumber: [#123]",
        "node_id": "I_1",
    }
    mock_webhook.unified_api = Mock()
    mock_webhook.unified_api.get_issues = AsyncMock(return_value=[mock_issue])
    mock_webhook.unified_api.add_comment = AsyncMock()
    mock_webhook.unified_api.edit_issue = AsyncMock()

    # Mock owners handler
    mock_owners = Mock()

    # Create handler
    handler = PullRequestHandler(mock_webhook, mock_owners)

    # Mock pull request
    mock_pr = Mock()
    mock_pr.number = 123
    mock_pr.title = "Test PR"

    # Execute method that calls get_issues
    await handler.close_issue_for_merged_or_closed_pr(mock_pr, "closed")

    # Verify get_issues was called with repository_data parameter
    mock_webhook.unified_api.get_issues.assert_called_once()
    call_args = mock_webhook.unified_api.get_issues.call_args
    # Verify repository_data was passed
    assert "repository_data" in call_args.kwargs
    assert call_args.kwargs["repository_data"] == mock_webhook.repository_data


# ===== Test Category 6: Logging and data counts =====


@pytest.mark.asyncio
async def test_comprehensive_data_logging(unified_api, mock_logger, mock_comprehensive_data):
    """Test logging includes correct data counts from repository_data fetch."""
    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
        patch("webhook_server.libs.graphql.unified_api.Config") as mock_config_class,
    ):
        # Mock Config
        mock_config = Mock()
        mock_config.get_value.return_value = 100
        mock_config_class.return_value = mock_config

        # Mock GraphQL client
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"repository": mock_comprehensive_data})
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()

        # Execute
        await unified_api.get_comprehensive_repository_data("owner", "repo")

        # Verify logging includes data counts
        log_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("2 collaborators" in call for call in log_calls)
        assert any("2 contributors" in call for call in log_calls)
        assert any("2 open issues" in call for call in log_calls)
        assert any("2 open PRs" in call for call in log_calls)


# ===== Test Category 7: Edge cases and error handling =====


@pytest.mark.asyncio
async def test_comprehensive_data_empty_results(unified_api, mock_logger):
    """Test handling of repositories with no collaborators/contributors/issues/PRs."""
    empty_data = {
        "id": "R_test",
        "name": "test-repo",
        "nameWithOwner": "owner/test-repo",
        "owner": {"id": "U_owner", "login": "owner"},
        "collaborators": {"edges": []},
        "mentionableUsers": {"nodes": []},
        "issues": {"nodes": []},
        "pullRequests": {"nodes": []},
    }

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
        patch("webhook_server.libs.graphql.unified_api.Config") as mock_config_class,
    ):
        # Mock Config
        mock_config = Mock()
        mock_config.get_value.return_value = 100
        mock_config_class.return_value = mock_config

        # Mock GraphQL client
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"repository": empty_data})
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()

        # Execute
        result = await unified_api.get_comprehensive_repository_data("owner", "repo")

        # Verify result structure is correct even with empty data
        assert len(result["collaborators"]["edges"]) == 0
        assert len(result["mentionableUsers"]["nodes"]) == 0
        assert len(result["issues"]["nodes"]) == 0
        assert len(result["pullRequests"]["nodes"]) == 0

        # Verify logging reflects empty results
        log_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("0 collaborators" in call for call in log_calls)


@pytest.mark.asyncio
async def test_get_issues_default_state_behavior(unified_api, mock_comprehensive_data):
    """Test get_issues() defaults to OPEN when states not specified."""
    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock()  # Should not be called
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()

        # Execute without states parameter (should default to OPEN)
        result = await unified_api.get_issues("owner", "repo", repository_data=mock_comprehensive_data)

        # Verify no GraphQL query was made (used pre-fetched OPEN issues)
        mock_gql.execute.assert_not_called()

        # Verify result
        assert result == mock_comprehensive_data["issues"]["nodes"]


@pytest.mark.asyncio
async def test_comprehensive_data_api_reduction():
    """Test that comprehensive data fetch reduces API calls from 10+ to 1."""
    # This is a documentation test showing the optimization benefit
    # Before: get_collaborators() + get_contributors() + get_issues() + get_pull_requests() = 4+ API calls
    # After: get_comprehensive_repository_data() = 1 API call
    # Additional savings from N+1 queries in handlers

    # Verify the optimization by counting mock calls
    mock_gql = AsyncMock()
    mock_gql.execute = AsyncMock(
        return_value={
            "repository": {
                "id": "R_test",
                "name": "test-repo",
                "nameWithOwner": "owner/test-repo",
                "owner": {"id": "U_owner", "login": "owner"},
                "collaborators": {"edges": []},
                "mentionableUsers": {"nodes": []},
                "issues": {"nodes": []},
                "pullRequests": {"nodes": []},
            }
        }
    )

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient", return_value=mock_gql),
        patch("webhook_server.libs.graphql.unified_api.Github"),
        patch("webhook_server.libs.graphql.unified_api.Config") as mock_config_class,
    ):
        mock_config_class.return_value.get_value.return_value = 100

        # Create mock config with get_value
        test_config = Mock()
        test_config.get_value = Mock(return_value=100)

        api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=Mock(), config=test_config)
        await api.initialize()

        # Get comprehensive data (1 API call)
        repo_data = await api.get_comprehensive_repository_data("owner", "repo")

        # Use pre-fetched data (0 additional API calls)
        await api.get_collaborators("owner", "repo", repository_data=repo_data)
        await api.get_contributors("owner", "repo", repository_data=repo_data)
        await api.get_issues("owner", "repo", repository_data=repo_data)

        # Verify only 1 GraphQL call was made (for comprehensive data)
        assert mock_gql.execute.call_count == 1
        # Before optimization: would be 4+ calls (1 for each method)
