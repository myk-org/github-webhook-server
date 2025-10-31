"""Tests for unified GitHub API."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from webhook_server.libs.graphql.graphql_client import GraphQLAuthenticationError, GraphQLError
from webhook_server.libs.graphql.unified_api import UnifiedGitHubAPI
from webhook_server.libs.graphql.webhook_data import CommitWrapper, PullRequestWrapper


@pytest.fixture
def mock_logger():
    """Create a mock logger."""
    return MagicMock()


@pytest.fixture
def mock_config():
    """Create a mock config."""
    config = MagicMock()

    # Mock config values with different defaults for different keys
    def get_value_side_effect(key, return_on_none=None):
        config_values = {
            "graphql.tree-max-depth": 9,
            "graphql.query-limits.labels": 100,
            "graphql.query-limits.reviews": 100,
            "graphql.query-limits.commits": 100,
            "graphql.query-limits.collaborators": 100,
            "graphql.query-limits.contributors": 100,
            "graphql.query-limits.issues": 100,
            "graphql.query-limits.pull-requests": 100,
        }
        return config_values.get(key, return_on_none)

    config.get_value = MagicMock(side_effect=get_value_side_effect)
    return config


@pytest.fixture
def unified_api(mock_logger, mock_config):
    """Create UnifiedGitHubAPI instance."""
    return UnifiedGitHubAPI(token="test_token", logger=mock_logger, config=mock_config)  # pragma: allowlist secret


@pytest.mark.asyncio
async def test_unified_api_initialization(unified_api):
    """Test API initialization."""
    assert unified_api.token == "test_token"  # pragma: allowlist secret
    assert not unified_api._initialized
    assert unified_api.graphql_client is None
    assert unified_api.rest_client is None


@pytest.mark.asyncio
async def test_unified_api_initialize(unified_api):
    """Test initialize method."""
    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient"),
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        await unified_api.initialize()

        assert unified_api._initialized
        assert unified_api.graphql_client is not None
        assert unified_api.rest_client is not None


@pytest.mark.asyncio
async def test_unified_api_context_manager(unified_api):
    """Test async context manager."""
    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql_instance = AsyncMock()
        mock_gql_instance.close = AsyncMock()
        mock_gql.return_value = mock_gql_instance

        async with unified_api as api:
            assert api is unified_api
            assert api._initialized

        # Should be closed after context
        assert not api._initialized


@pytest.mark.asyncio
async def test_get_rate_limit(unified_api):
    """Test get_rate_limit uses GraphQL."""
    mock_result = {"rateLimit": {"limit": 5000, "remaining": 4999}}

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value=mock_result)
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()
        result = await unified_api.get_rate_limit()

        assert result == mock_result["rateLimit"]
        mock_gql.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_viewer(unified_api):
    """Test get_viewer uses GraphQL."""
    mock_result = {"viewer": {"login": "testuser", "name": "Test User"}}

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value=mock_result)
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()
        result = await unified_api.get_viewer()

        assert result == mock_result["viewer"]


@pytest.mark.asyncio
async def test_get_repository(unified_api):
    """Test get_repository uses GraphQL."""
    mock_result = {"repository": {"id": "repo123", "name": "test-repo"}}

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value=mock_result)
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()
        result = await unified_api.get_repository("owner", "repo")

        assert result == mock_result["repository"]


@pytest.mark.asyncio
async def test_get_pull_request_data(unified_api):
    """Test get_pull_request_data uses GraphQL."""
    mock_result = {"repository": {"pullRequest": {"id": "pr123", "number": 1}}}

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value=mock_result)
        mock_gql.close = AsyncMock()
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()
        result = await unified_api.get_pull_request_data("owner", "repo", 1)

        assert result == mock_result["repository"]["pullRequest"]

    await unified_api.close()


@pytest.mark.asyncio
async def test_add_comment(unified_api):
    """Test add_comment uses GraphQL mutation."""
    mock_result = {"addComment": {"commentEdge": {"node": {"id": "comment123", "body": "Test"}}}}

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value=mock_result)
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()
        result = await unified_api.add_comment("subject123", "Test comment")

        assert result == mock_result["addComment"]["commentEdge"]["node"]


@pytest.mark.asyncio
async def test_add_labels(unified_api):
    """Test add_labels uses GraphQL mutation with correct variables."""
    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={})
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()

        # Test with GraphQL node IDs
        labelable_id = "gid://github.com/PullRequest/PR_kwDOABcD1M5abc123"
        label_ids = ["gid://github.com/Label/LA_kwDOABcD1M8def456", "gid://github.com/Label/LA_kwDOABcD1M8ghi789"]

        await unified_api.add_labels(labelable_id, label_ids)

        # Verify mock_gql.execute was called once
        assert mock_gql.execute.call_count == 1

        # Get the call arguments
        call_args = mock_gql.execute.call_args
        mutation = call_args[0][0]  # First positional argument
        variables = call_args[0][1]  # Second positional argument

        # Assert the mutation contains fragment and mutation definitions
        assert "fragment LabelFields" in mutation, "Mutation should contain LabelFields fragment definition"
        assert "mutation" in mutation, "Mutation should contain mutation keyword"

        # Assert the mutation contains the addLabelsToLabelable operation
        assert "addLabelsToLabelable" in mutation, "Mutation should contain addLabelsToLabelable operation"

        # Assert the mutation returns the full labelable object with updated labels
        assert "labelable {" in mutation, "Mutation should return labelable object"
        assert "labels(first: 100)" in mutation, "Mutation should return updated labels"
        assert "...LabelFields" in mutation, "Mutation should use LabelFields fragment for labels"

        # Assert variables contain the correct labelableId
        assert variables["labelableId"] == labelable_id, (
            f"Expected labelableId={labelable_id}, got {variables.get('labelableId')}"
        )

        # Assert variables contain the correct labelIds
        assert variables["labelIds"] == label_ids, f"Expected labelIds={label_ids}, got {variables.get('labelIds')}"


@pytest.mark.asyncio
async def test_get_repository_for_rest_operations(unified_api):
    """Test get_repository_for_rest_operations calls rest_client.get_repo with correct parameters."""
    mock_repo = MagicMock()

    # Track asyncio.to_thread call to verify wrapping
    async def mock_to_thread(func, *args):
        # Verify the function and arguments are correct
        assert func == unified_api.rest_client.get_repo
        assert args == ("owner/name",)
        return mock_repo

    with patch("asyncio.to_thread", side_effect=mock_to_thread) as mock_thread:
        result = await unified_api.get_repository_for_rest_operations("owner", "name")

        # Verify asyncio.to_thread was called exactly once
        mock_thread.assert_called_once_with(unified_api.rest_client.get_repo, "owner/name")
        # Verify correct return value
        assert result == mock_repo


@pytest.mark.asyncio
async def test_get_pr_for_check_runs(unified_api):
    """Test get_pr_for_check_runs calls repo.get_pull with correct PR number."""
    mock_repo = MagicMock()
    mock_pr = MagicMock()

    # Track both asyncio.to_thread calls
    call_count = [0]

    async def mock_to_thread(func, *args):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: get_repository_for_rest_operations
            assert func == unified_api.rest_client.get_repo
            assert args == ("owner/name",)
            return mock_repo
        elif call_count[0] == 2:
            # Second call: repo.get_pull
            assert func == mock_repo.get_pull
            assert args == (123,)
            return mock_pr
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread) as mock_thread:
        result = await unified_api.get_pr_for_check_runs("owner", "name", 123)

        # Verify both asyncio.to_thread calls were made
        assert mock_thread.call_count == 2
        # Verify correct return value
        assert result == mock_pr


# Removed test_get_api_type_for_operation - APIType and get_api_type_for_operation no longer exist in production code


@pytest.mark.asyncio
async def test_concurrent_initialize_creates_single_client():
    """
    Test that concurrent initialize() calls use lock and don't create multiple clients.

    Verifies that the initialization lock prevents race conditions that could
    create multiple GraphQL and REST client instances.

    Test: Call initialize() 10 times concurrently via asyncio.gather
    Verify: Only one GraphQL client and one REST client created
    """
    logger = MagicMock()
    mock_config = MagicMock()
    mock_config.get_value = MagicMock(return_value=9)
    api = UnifiedGitHubAPI("test_token", logger, mock_config)  # pragma: allowlist secret

    # Track how many times each client constructor is called
    graphql_client_count = {"count": 0}
    rest_client_count = {"count": 0}

    def mock_graphql_client(*_args, **_kwargs):
        graphql_client_count["count"] += 1
        mock = MagicMock()
        mock.close = AsyncMock()  # GraphQL client has async close
        return mock

    def mock_rest_client(*_args, **_kwargs):
        rest_client_count["count"] += 1
        mock = MagicMock()
        mock.close = MagicMock()  # REST client has sync close
        return mock

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient", side_effect=mock_graphql_client),
        patch("webhook_server.libs.graphql.unified_api.Github", side_effect=mock_rest_client),
    ):
        # Call initialize() 10 times concurrently
        await asyncio.gather(*[api.initialize() for _ in range(10)])

        # Verify only ONE GraphQL client was created
        assert graphql_client_count["count"] == 1, (
            f"Expected 1 GraphQL client, but {graphql_client_count['count']} were created. "
            "Lock should prevent multiple client creation."
        )

        # Verify only ONE REST client was created
        assert rest_client_count["count"] == 1, (
            f"Expected 1 REST client, but {rest_client_count['count']} were created. "
            "Lock should prevent multiple client creation."
        )

        # Verify API is initialized
        assert api._initialized
        assert api.graphql_client is not None
        assert api.rest_client is not None

    # Cleanup
    await api.close()


@pytest.mark.asyncio
async def test_concurrent_initialize_idempotency():
    """
    Test that multiple initialize() calls are idempotent.

    Verifies that calling initialize() multiple times (even after initialization)
    doesn't change the client instances.
    """
    logger = MagicMock()
    mock_config = MagicMock()
    mock_config.get_value = MagicMock(return_value=9)
    api = UnifiedGitHubAPI("test_token", logger, mock_config)  # pragma: allowlist secret

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github") as mock_rest_class,
    ):
        mock_gql = MagicMock()
        mock_gql.close = AsyncMock()  # GraphQL client has async close
        mock_rest = MagicMock()
        mock_rest.close = MagicMock()  # REST client has sync close

        mock_gql_class.return_value = mock_gql
        mock_rest_class.return_value = mock_rest

        # First initialize
        await api.initialize()
        first_gql_client = api.graphql_client
        first_rest_client = api.rest_client

        # Second initialize (should be idempotent)
        await api.initialize()
        assert api.graphql_client is first_gql_client, "GraphQL client should not change on re-initialization"
        assert api.rest_client is first_rest_client, "REST client should not change on re-initialization"

        # Third initialize concurrently (should still be idempotent)
        await asyncio.gather(*[api.initialize() for _ in range(5)])
        assert api.graphql_client is first_gql_client
        assert api.rest_client is first_rest_client

        # Verify constructors only called once
        assert mock_gql_class.call_count == 1
        assert mock_rest_class.call_count == 1

    # Cleanup
    await api.close()


@pytest.mark.asyncio
async def test_text_file_uses_graphql_no_fallback():
    """
    Test that text files use GraphQL without falling back to REST.

    Verifies that normal text files don't trigger REST fallback.
    """
    logger = MagicMock()
    mock_config = MagicMock()
    mock_config.get_value = MagicMock(return_value=9)
    api = UnifiedGitHubAPI("test_token", logger, mock_config)  # pragma: allowlist secret

    # Mock GraphQL response for text file
    text_blob_response = {
        "repository": {
            "object": {
                "isBinary": False,
                "text": "# Text file content\nHello world!",
            }
        }
    }

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github") as mock_rest_class,
        patch.object(api, "get_contents", new=AsyncMock()) as mock_get_contents,
    ):
        # Setup GraphQL mock
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value=text_blob_response)
        mock_gql.close = AsyncMock()
        mock_gql_class.return_value = mock_gql

        # Setup REST mock
        mock_rest = MagicMock()
        mock_rest.close = MagicMock()
        mock_rest_class.return_value = mock_rest

        await api.initialize()

        # Fetch text file
        result = await api.get_file_contents(owner="test-owner", name="test-repo", path="README.md", ref="main")

        # Verify GraphQL was used
        assert mock_gql.execute.call_count == 1

        # Verify NO fallback to REST API
        mock_get_contents.assert_not_called()

        # Verify text content returned directly from GraphQL
        assert result == "# Text file content\nHello world!"

    # Cleanup
    await api.close()


# ===== Tests for Moved PR Methods from github_api.py =====


class TestUnifiedAPIPRMethods:
    """Comprehensive tests for the 7 PR methods moved from GithubWebhook to UnifiedGitHubAPI."""

    @pytest.fixture
    def api(self, mock_logger, mock_config):
        """Create UnifiedGitHubAPI instance."""
        return UnifiedGitHubAPI(token="test_token", logger=mock_logger, config=mock_config)  # pragma: allowlist secret

    @pytest.fixture
    def mock_pr_wrapper(self):
        """Create mock PullRequestWrapper."""
        # Mock webhook PR data
        webhook_data = {
            "node_id": "PR_kwDOABcD1M5abc123",
            "number": 42,
            "title": "Test PR",
            "body": "Test description",
            "state": "open",
            "draft": False,
            "merged": False,
            "base": {"ref": "main", "sha": "abc123", "repo": {"owner": {"login": "test-owner"}, "name": "test-repo"}},
            "head": {
                "ref": "feature",
                "sha": "def456",
                "repo": {"owner": {"login": "test-owner"}, "name": "test-repo"},
            },
            "user": {"login": "testuser"},
        }
        return PullRequestWrapper("test-owner", "test-repo", webhook_data)

    # ===== 1. get_pull_request() Tests =====

    @pytest.mark.asyncio
    async def test_get_pull_request_with_pr_number(self, api, mock_logger):
        """Test get_pull_request with direct PR number (GraphQL fetch path)."""
        hook_data = {}  # No pull_request field ? forces GraphQL fetch
        pr_graphql_data = {
            "id": "PR_123",
            "number": 42,
            "title": "Test PR",
            "body": "Test description",
            "state": "OPEN",
            "isDraft": False,
            "merged": False,
            "author": {"login": "testuser"},
            "baseRef": {"name": "main", "target": {"oid": "abc123"}},
            "headRef": {"name": "feature", "target": {"oid": "def456"}},
        }

        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            mock_gql.execute = AsyncMock(return_value={"repository": {"pullRequest": pr_graphql_data}})
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            result = await api.get_pull_request(
                owner="test-owner",
                repo="test-repo",
                hook_data=hook_data,
                github_event="pull_request",
                logger=mock_logger,
                number=42,
            )

            assert isinstance(result, PullRequestWrapper)
            assert result.number == 42
            assert result.title == "Test PR"

        await api.close()

    @pytest.mark.asyncio
    async def test_get_pull_request_webhook_payload_optimization(self, api, mock_logger):
        """Test that get_pull_request reuses webhook payload when node_id is present (optimization)."""
        # Webhook payload with complete PR data including node_id
        hook_data = {
            "pull_request": {
                "number": 42,
                "node_id": "PR_kwDOABcD1M5abc123",  # GraphQL node ID from webhook
                "title": "Test PR from webhook",
                "body": "Description from webhook",
                "state": "open",
                "draft": False,
                "merged": False,
                "mergeable_state": "clean",
                "user": {"login": "testuser", "type": "User"},
            }
        }

        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            mock_gql.execute = AsyncMock()  # Should NOT be called due to optimization
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            result = await api.get_pull_request(
                owner="test-owner",
                repo="test-repo",
                hook_data=hook_data,
                github_event="pull_request",
                logger=mock_logger,
                number=42,
            )

            # Verify PR was constructed from webhook payload
            assert isinstance(result, PullRequestWrapper)
            assert result.number == 42
            assert result.title == "Test PR from webhook"
            assert result.id == "PR_kwDOABcD1M5abc123"

            # CRITICAL: Verify GraphQL was NOT called (optimization worked)
            mock_gql.execute.assert_not_called()

        await api.close()

    @pytest.mark.asyncio
    async def test_get_pull_request_webhook_payload_incomplete_fallback(self, api, mock_logger):
        """Test that get_pull_request falls back to GraphQL when webhook payload lacks node_id."""
        # Webhook payload WITHOUT node_id - should fall back to GraphQL
        hook_data = {
            "pull_request": {
                "number": 42,
                "title": "Test PR",
                "state": "open",
                "draft": False,
                "base": {
                    "ref": "main",
                    "sha": "abc123",
                    "repo": {"owner": {"login": "test-owner"}, "name": "test-repo"},
                },
                "head": {
                    "ref": "feature",
                    "sha": "def456",
                    "repo": {"owner": {"login": "test-owner"}, "name": "test-repo"},
                },
                "user": {"login": "testuser"},
            }
        }

        pr_graphql_data = {
            "id": "PR_123",
            "number": 42,
            "title": "Test PR from GraphQL",
            "state": "OPEN",
            "author": {"login": "testuser"},
            "baseRef": {"name": "main", "target": {"oid": "abc123"}},
            "headRef": {"name": "feature", "target": {"oid": "def456"}},
        }

        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            mock_gql.execute = AsyncMock(return_value={"repository": {"pullRequest": pr_graphql_data}})
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            result = await api.get_pull_request(
                owner="test-owner",
                repo="test-repo",
                hook_data=hook_data,
                github_event="pull_request",
                logger=mock_logger,
                number=42,
            )

            # Verify PR was created from GraphQL (fallback when webhook lacks node_id)
            assert isinstance(result, PullRequestWrapper)
            assert result.number == 42
            assert result.title == "Test PR from GraphQL"

            # CRITICAL: Verify GraphQL WAS called (fallback when webhook missing node_id)
            mock_gql.execute.assert_called_once()

        await api.close()

    @pytest.mark.asyncio
    async def test_get_pull_request_with_commit_sha(self, api, mock_logger):
        """Test get_pull_request with commit SHA lookup via GraphQL."""
        hook_data = {"commit": {"sha": "abc123def456"}}  # pragma: allowlist secret

        # Mock GraphQL PR data from associatedPullRequests
        mock_pr_data = {
            "id": "PR_kgDOTest123",
            "number": 42,
            "title": "Test PR from commit",
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": "feature",
            "author": {"login": "testuser"},
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-02T00:00:00Z",
            "mergedAt": None,
            "closedAt": None,
        }

        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github") as mock_github_class,
        ):
            mock_gql = AsyncMock()
            mock_gql.close = AsyncMock()
            # Mock GraphQL response for get_pulls_from_commit_sha
            mock_gql.execute = AsyncMock(
                return_value={"repository": {"object": {"associatedPullRequests": {"nodes": [mock_pr_data]}}}}
            )
            mock_gql_class.return_value = mock_gql

            mock_rest = MagicMock()
            mock_rest.close = MagicMock()
            mock_github_class.return_value = mock_rest

            await api.initialize()
            result = await api.get_pull_request(
                owner="test-owner",
                repo="test-repo",
                hook_data=hook_data,
                github_event="push",
                logger=mock_logger,
            )

            assert isinstance(result, PullRequestWrapper)
            assert result.number == 42
            assert result.title == "Test PR from commit"

        await api.close()

    @pytest.mark.asyncio
    async def test_get_pull_request_with_check_run(self, api, mock_logger):
        """Test get_pull_request with check_run event fallback to GraphQL iteration."""
        hook_data = {"check_run": {"name": "test-check", "head_sha": "abc123def456"}}  # pragma: allowlist secret

        # Mock GraphQL PR data
        mock_pr_data = {
            "id": "PR_kgDOTest123",
            "number": 42,
            "title": "Test PR from check run",
            "state": "OPEN",
            "headRef": {
                "name": "feature-branch",
                "target": {"oid": "abc123def456"},  # pragma: allowlist secret
            },
            "baseRefName": "main",
            "headRefName": "feature-branch",
            "labels": {"nodes": []},
        }

        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            mock_gql.close = AsyncMock()
            # Mock get_open_pull_requests_with_details GraphQL query
            mock_gql.execute = AsyncMock(return_value={"repository": {"pullRequests": {"nodes": [mock_pr_data]}}})
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            result = await api.get_pull_request(
                owner="test-owner",
                repo="test-repo",
                hook_data=hook_data,
                github_event="check_run",
                logger=mock_logger,
            )

            assert isinstance(result, PullRequestWrapper)
            assert result.number == 42

        await api.close()

    @pytest.mark.asyncio
    async def test_get_pull_request_with_check_run_using_pull_requests_array(self, api, mock_logger):
        """Test optimized check_run PR lookup using pull_requests array from webhook."""
        hook_data = {
            "check_run": {
                "name": "test-check",
                "head_sha": "abc123def456",  # pragma: allowlist secret
                "pull_requests": [
                    {
                        "number": 42,
                        "url": "https://api.github.com/repos/test-owner/test-repo/pulls/42",
                        "id": "PR_kgDOTest123",
                        "node_id": "PR_kgDOTest123",
                        "title": "Test PR from pull_requests array",
                        "state": "open",
                        "base": {
                            "ref": "main",
                            "sha": "base123",
                            "repo": {"owner": {"login": "test-owner"}, "name": "test-repo"},
                        },
                        "head": {
                            "ref": "feature-branch",
                            "sha": "abc123def456",  # pragma: allowlist secret
                            "repo": {"owner": {"login": "test-owner"}, "name": "test-repo"},
                        },
                        "user": {"login": "testuser"},
                    }
                ],
            }
        }

        # Mock GraphQL PR data
        mock_pr_data = {
            "id": "PR_kgDOTest123",
            "number": 42,
            "title": "Test PR from pull_requests array",
            "state": "OPEN",
            "url": "https://github.com/test-owner/test-repo/pull/42",
            "baseRefName": "main",
            "headRefName": "feature-branch",
            "headRefOid": "abc123def456",  # pragma: allowlist secret
            "commits": {"nodes": []},
            "labels": {"nodes": []},
        }

        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            mock_gql.close = AsyncMock()
            mock_gql.execute = AsyncMock(return_value={"repository": {"pullRequest": mock_pr_data}})
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            result = await api.get_pull_request(
                owner="test-owner",
                repo="test-repo",
                hook_data=hook_data,
                github_event="check_run",
                logger=mock_logger,
            )

            # Verify result is correct
            assert isinstance(result, PullRequestWrapper)
            assert result.number == 42

            # Verify optimization: fetch specific PR by number (not all open PRs)
            # After production fix: fetches complete PR data instead of using incomplete webhook reference
            mock_gql.execute.assert_called_once()

        await api.close()

    @pytest.mark.asyncio
    async def test_get_pull_request_with_check_run_empty_pull_requests_array(self, api, mock_logger):
        """Test check_run fallback when pull_requests array is empty."""
        hook_data = {
            "check_run": {
                "name": "test-check",
                "head_sha": "abc123def456",  # pragma: allowlist secret
                "pull_requests": [],  # Empty array
            }
        }

        # Mock GraphQL PR data for fallback iteration
        mock_pr_data = {
            "id": "PR_kgDOTest123",
            "number": 42,
            "title": "Test PR from fallback",
            "state": "OPEN",
            "headRef": {
                "name": "feature-branch",
                "target": {"oid": "abc123def456"},  # pragma: allowlist secret
            },
            "baseRefName": "main",
            "headRefName": "feature-branch",
            "labels": {"nodes": []},
        }

        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            mock_gql.close = AsyncMock()
            # Mock get_open_pull_requests_with_details GraphQL query
            mock_gql.execute = AsyncMock(return_value={"repository": {"pullRequests": {"nodes": [mock_pr_data]}}})
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            result = await api.get_pull_request(
                owner="test-owner",
                repo="test-repo",
                hook_data=hook_data,
                github_event="check_run",
                logger=mock_logger,
            )

            # Verify result is correct
            assert isinstance(result, PullRequestWrapper)
            assert result.number == 42

            # Verify warning log about fallback
            warning_calls = [call for call in mock_logger.warning.call_args_list]
            assert any("falling back to expensive iteration" in str(call) for call in warning_calls)

        await api.close()

    @pytest.mark.asyncio
    async def test_get_pull_request_with_check_run_no_head_sha_fallback(self, api, mock_logger):
        """Test check_run returns None when pull_requests is empty and no head_sha."""
        hook_data = {
            "check_run": {
                "name": "test-check",
                # No head_sha
                "pull_requests": [],  # Empty array
            }
        }

        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            result = await api.get_pull_request(
                owner="test-owner",
                repo="test-repo",
                hook_data=hook_data,
                github_event="check_run",
                logger=mock_logger,
            )

            # Should return None when no PR can be found
            assert result is None

        await api.close()

    @pytest.mark.asyncio
    async def test_get_pull_request_skips_issue_only_events(self, api, mock_logger):
        """Test get_pull_request returns None for issue-only events."""
        hook_data = {"issue": {"number": 99}}  # No pull_request field

        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            result = await api.get_pull_request(
                owner="test-owner",
                repo="test-repo",
                hook_data=hook_data,
                github_event="issue_comment",
                logger=mock_logger,
            )

            assert result is None

        await api.close()

    @pytest.mark.asyncio
    async def test_get_pull_request_invalid_commit_sha(self, api, mock_logger):
        """Test get_pull_request handles missing commit SHA gracefully."""
        hook_data = {"commit": {}}  # Missing 'sha' field

        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            result = await api.get_pull_request(
                owner="test-owner",
                repo="test-repo",
                hook_data=hook_data,
                github_event="push",
                logger=mock_logger,
            )

            assert result is None

        await api.close()

    # ===== 2. get_last_commit() Tests =====

    @pytest.mark.asyncio
    async def test_get_last_commit_from_graphql_wrapper(self, api):
        """Test get_last_commit extracts commit from PullRequestWrapper with commits."""
        # Create PR wrapper with commits in webhook format
        webhook_data = {
            "node_id": "PR_kwDOABcD1M5abc123",
            "number": 42,
            "title": "Test PR",
            "state": "open",
            "draft": False,
            "base": {"ref": "main", "sha": "abc123", "repo": {"owner": {"login": "test-owner"}, "name": "test-repo"}},
            "head": {
                "ref": "feature",
                "sha": "def456",
                "repo": {"owner": {"login": "test-owner"}, "name": "test-repo"},
            },
            "user": {"login": "testuser"},
            "commits": [{"sha": "abc123def456", "committer": {"login": "testuser"}}],  # pragma: allowlist secret
        }
        pr_wrapper = PullRequestWrapper("test-owner", "test-repo", webhook_data)

        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            commit_data = {"oid": "abc123def456", "message": "Test commit"}  # pragma: allowlist secret
            mock_gql = AsyncMock()
            mock_gql.execute = AsyncMock(
                return_value={
                    "repository": {"pullRequest": {"commits": {"nodes": [{"commit": commit_data}]}}},
                }
            )
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            result = await api.get_last_commit(owner="test-owner", repo="test-repo", pull_request=pr_wrapper)

            assert isinstance(result, CommitWrapper)
            assert result.sha == "abc123def456"  # pragma: allowlist secret

        await api.close()

    @pytest.mark.asyncio
    async def test_get_last_commit_graphql_error_propagates(self, api, mock_pr_wrapper, mock_logger):
        """Test get_last_commit raises GraphQL errors properly (no REST fallback)."""
        with patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class:
            mock_gql = AsyncMock()
            # GraphQL fails - should propagate error
            mock_gql.execute = AsyncMock(side_effect=GraphQLError("GraphQL failed"))
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()

            # Should raise GraphQL error (no REST fallback)
            with pytest.raises(GraphQLError, match="GraphQL failed"):
                await api.get_last_commit(owner="test-owner", repo="test-repo", pull_request=mock_pr_wrapper)

        await api.close()

    # ===== 3. add_pr_comment() Tests =====

    @pytest.mark.asyncio
    async def test_add_pr_comment_graphql_success(self, api, mock_pr_wrapper):
        """Test add_pr_comment via GraphQL mutation."""
        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            mock_gql.execute = AsyncMock(
                return_value={"addComment": {"commentEdge": {"node": {"id": "comment_123", "body": "Test"}}}}
            )
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            await api.add_pr_comment(pull_request=mock_pr_wrapper, body="Test comment")

            # Verify GraphQL mutation was called
            mock_gql.execute.assert_called_once()

        await api.close()

    # ===== 4. update_pr_title() Tests =====

    @pytest.mark.asyncio
    async def test_update_pr_title_success(self, api, mock_pr_wrapper):
        """Test update_pr_title via GraphQL mutation."""
        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            mock_gql.execute = AsyncMock(
                return_value={"updatePullRequest": {"pullRequest": {"id": "PR_123", "title": "New Title"}}}
            )
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            await api.update_pr_title(pull_request=mock_pr_wrapper, title="New Title")

            # Verify mutation was called
            mock_gql.execute.assert_called_once()

        await api.close()

    # ===== 5. enable_pr_automerge() Tests =====

    @pytest.mark.asyncio
    async def test_enable_pr_automerge_squash(self, api, mock_pr_wrapper):
        """Test enable_pr_automerge with SQUASH method."""
        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            mock_gql.execute = AsyncMock(return_value={"enablePullRequestAutomerge": {"pullRequest": {"id": "PR_123"}}})
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            await api.enable_pr_automerge(pull_request=mock_pr_wrapper, merge_method="SQUASH")

            # Verify mutation was called
            mock_gql.execute.assert_called_once()

        await api.close()

    @pytest.mark.asyncio
    async def test_enable_pr_automerge_merge(self, api, mock_pr_wrapper):
        """Test enable_pr_automerge with MERGE method."""
        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            mock_gql.execute = AsyncMock(return_value={"enablePullRequestAutomerge": {"pullRequest": {"id": "PR_123"}}})
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            await api.enable_pr_automerge(pull_request=mock_pr_wrapper, merge_method="MERGE")

            mock_gql.execute.assert_called_once()

        await api.close()

    @pytest.mark.asyncio
    async def test_enable_pr_automerge_error(self, api, mock_pr_wrapper):
        """Test enable_pr_automerge handles errors."""
        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            mock_gql.execute = AsyncMock(side_effect=GraphQLError("Automerge not allowed"))
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()

            with pytest.raises(GraphQLError):
                await api.enable_pr_automerge(pull_request=mock_pr_wrapper, merge_method="SQUASH")

        await api.close()

    # ===== 6. request_pr_reviews() Tests =====

    @pytest.mark.asyncio
    async def test_request_pr_reviews_single_reviewer(self, api, mock_pr_wrapper):
        """Test request_pr_reviews with single reviewer (batched resolution)."""
        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            # Mock execute_batch for batched user ID resolution
            mock_gql.execute_batch = AsyncMock(return_value=[{"user": {"id": "U_kgDOABcD1M"}}])
            # Mock execute for request_reviews mutation
            mock_gql.execute = AsyncMock(return_value={"requestReviews": {"pullRequest": {"id": "PR_123"}}})
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            await api.request_pr_reviews(pull_request=mock_pr_wrapper, reviewers=["reviewer1"])

            # Should call execute_batch once (batched user resolution) + execute once (request_reviews)
            assert mock_gql.execute_batch.call_count == 1
            assert mock_gql.execute.call_count == 1

        await api.close()

    @pytest.mark.asyncio
    async def test_request_pr_reviews_multiple_reviewers(self, api, mock_pr_wrapper):
        """Test request_pr_reviews with multiple reviewers (batched resolution)."""
        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            # Mock execute_batch for batched user ID resolution (returns list of results)
            mock_gql.execute_batch = AsyncMock(
                return_value=[
                    {"user": {"id": "U_kgDOABcD1M1"}},  # reviewer1
                    {"user": {"id": "U_kgDOABcD1M2"}},  # reviewer2
                ]
            )
            # Mock execute for request_reviews mutation
            mock_gql.execute = AsyncMock(return_value={"requestReviews": {"pullRequest": {"id": "PR_123"}}})
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            await api.request_pr_reviews(
                pull_request=mock_pr_wrapper,
                reviewers=["reviewer1", "reviewer2"],
            )

            # Should call execute_batch once (batched user resolution) + execute once (request_reviews)
            assert mock_gql.execute_batch.call_count == 1
            assert mock_gql.execute.call_count == 1

        await api.close()

    @pytest.mark.asyncio
    async def test_request_pr_reviews_with_graphql_node_id(self, api, mock_pr_wrapper):
        """Test request_pr_reviews with GraphQL node ID directly."""
        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            # Only request_reviews call, no get_user_id needed
            mock_gql.execute = AsyncMock(return_value={"requestReviews": {"pullRequest": {"id": "PR_123"}}})
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            await api.request_pr_reviews(
                pull_request=mock_pr_wrapper,
                reviewers=["U_kgDOABcD1M"],  # GraphQL node ID
            )

            # Should call GraphQL once: request_reviews only (skip get_user_id)
            assert mock_gql.execute.call_count == 1

        await api.close()

    @pytest.mark.asyncio
    async def test_request_pr_reviews_numeric_id_warning(self, api, mock_pr_wrapper, mock_logger):
        """Test request_pr_reviews raises TypeError for numeric reviewer IDs."""
        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()

            # Fail-fast validation: numeric IDs are invalid, expect TypeError
            with pytest.raises(TypeError, match="Reviewer must be str"):
                await api.request_pr_reviews(
                    pull_request=mock_pr_wrapper,
                    reviewers=[12345],  # Numeric ID - INVALID
                )

        await api.close()

    # ===== 7. add_pr_assignee() Tests =====

    @pytest.mark.asyncio
    async def test_add_pr_assignee_success(self, api, mock_pr_wrapper):
        """Test add_pr_assignee with valid assignee."""
        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            # Mock get_user_id and add_assignees
            mock_gql.execute = AsyncMock(
                side_effect=[
                    {"user": {"id": "U_kgDOABcD1M"}},  # get_user_id
                    {"addAssigneesToAssignable": {"assignable": {"id": "PR_123"}}},  # add_assignees
                ]
            )
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            await api.add_pr_assignee(pull_request=mock_pr_wrapper, assignee="assignee1")

            # Should call GraphQL twice: get_user_id + add_assignees
            assert mock_gql.execute.call_count == 2

        await api.close()

    @pytest.mark.asyncio
    async def test_add_pr_assignee_failure(self, api, mock_pr_wrapper, mock_logger):
        """Test add_pr_assignee handles errors gracefully."""
        with (
            patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
            patch("webhook_server.libs.graphql.unified_api.Github"),
        ):
            mock_gql = AsyncMock()
            mock_gql.execute = AsyncMock(side_effect=GraphQLError("User not found"))
            mock_gql.close = AsyncMock()
            mock_gql_class.return_value = mock_gql

            await api.initialize()
            # Should not raise, just log warning
            await api.add_pr_assignee(pull_request=mock_pr_wrapper, assignee="nonexistent_user")

            # Verify warning was logged
            assert any("Failed to add assignee" in str(call) for call in mock_logger.warning.call_args_list)

        await api.close()

    # ===== Static Helper Tests =====

    def test_is_graphql_node_id_valid_formats(self):
        """Test _is_graphql_node_id recognizes valid GraphQL node IDs."""
        # Valid GraphQL node IDs
        assert UnifiedGitHubAPI._is_graphql_node_id("U_kgDOABcD1M")  # User ID
        assert UnifiedGitHubAPI._is_graphql_node_id("PR_kwDOABcD1M5abc123")  # Pull Request ID
        assert UnifiedGitHubAPI._is_graphql_node_id("R_kgDOABcD1M")  # Repository ID
        assert UnifiedGitHubAPI._is_graphql_node_id("MDQ6VXNlcjEyMzQ1")  # Legacy User ID
        assert UnifiedGitHubAPI._is_graphql_node_id("MDExOlJlcG9zaXRvcnkxMjM0NQ==")  # Legacy Repository ID

    def test_is_graphql_node_id_invalid_formats(self):
        """Test _is_graphql_node_id rejects invalid formats."""
        # Invalid formats
        assert not UnifiedGitHubAPI._is_graphql_node_id("12345")  # Pure number
        assert not UnifiedGitHubAPI._is_graphql_node_id("short")  # Too short
        assert not UnifiedGitHubAPI._is_graphql_node_id("username123")  # No uppercase
        assert not UnifiedGitHubAPI._is_graphql_node_id("")  # Empty string

    def test_is_user_node_id_valid_formats(self):
        """Test _is_user_node_id recognizes valid User node IDs."""
        # Valid User node IDs
        assert UnifiedGitHubAPI._is_user_node_id("U_kgDOABcD1M")  # Modern User ID
        assert UnifiedGitHubAPI._is_user_node_id("MDQ6VXNlcjEyMzQ1")  # Legacy User ID

    def test_is_user_node_id_rejects_non_user_ids(self):
        """Test _is_user_node_id rejects non-User node IDs."""
        # Non-user GraphQL node IDs
        assert not UnifiedGitHubAPI._is_user_node_id("PR_kwDOABcD1M5abc123")  # Pull Request ID
        assert not UnifiedGitHubAPI._is_user_node_id("R_kgDOABcD1M")  # Repository ID
        assert not UnifiedGitHubAPI._is_user_node_id("I_kgDOABcD1M")  # Issue ID
        assert not UnifiedGitHubAPI._is_user_node_id("12345")  # Numeric ID
        assert not UnifiedGitHubAPI._is_user_node_id("username")  # Username string


@pytest.mark.asyncio
async def test_request_pr_reviews_with_graphql_errors(mock_logger, mock_config):
    """Test request_pr_reviews logs warning when batch and sequential GraphQL user lookup fails."""
    api = UnifiedGitHubAPI(token="test_token", logger=mock_logger, config=mock_config)  # pragma: allowlist secret

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_graphql_client_cls,
        patch("webhook_server.libs.graphql.unified_api.Github") as mock_github_cls,
    ):
        # Setup mocks
        mock_graphql = AsyncMock()
        mock_graphql_client_cls.return_value = mock_graphql
        mock_rest = MagicMock()
        mock_github_cls.return_value = mock_rest

        await api.initialize()

        # Mock execute_batch to fail (triggers fallback to sequential)
        mock_graphql.execute_batch = AsyncMock(side_effect=GraphQLError("Batch failed"))
        # Mock get_user_id to fail in sequential fallback
        api.get_user_id = AsyncMock(side_effect=GraphQLError("User not found"))

        pr_wrapper = MagicMock()
        pr_wrapper.id = "PR_test123"

        await api.request_pr_reviews(pr_wrapper, ["testuser"])

        # Verify warnings were logged about both batch failure and sequential failure
        assert mock_logger.warning.call_count >= 2
        log_messages = [str(call) for call in mock_logger.warning.call_args_list]
        assert any("Batch user ID resolution failed" in msg for msg in log_messages)
        assert any("Failed to get GraphQL node ID for reviewer 'testuser'" in msg for msg in log_messages)


@pytest.mark.asyncio
async def test_request_pr_reviews_with_auth_error_raises(mock_logger, mock_config):
    """Test request_pr_reviews re-raises authentication errors from batch operation."""
    api = UnifiedGitHubAPI(token="test_token", logger=mock_logger, config=mock_config)  # pragma: allowlist secret

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_graphql_client_cls,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_graphql = AsyncMock()
        mock_graphql_client_cls.return_value = mock_graphql

        await api.initialize()

        # Mock execute_batch to fail with auth error (critical error, should re-raise)
        mock_graphql.execute_batch = AsyncMock(side_effect=GraphQLAuthenticationError("Bad credentials"))

        pr_wrapper = MagicMock()
        pr_wrapper.id = "PR_test123"

        # Should re-raise auth error
        with pytest.raises(GraphQLAuthenticationError):
            await api.request_pr_reviews(pr_wrapper, ["testuser"])


@pytest.mark.asyncio
async def test_request_pr_reviews_with_invalid_node_id_in_dict(mock_logger, mock_config):
    """Test request_pr_reviews handles dict with invalid node ID."""
    api = UnifiedGitHubAPI(token="test_token", logger=mock_logger, config=mock_config)  # pragma: allowlist secret

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_graphql_client_cls,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_graphql = AsyncMock()
        mock_graphql_client_cls.return_value = mock_graphql

        await api.initialize()

        pr_wrapper = MagicMock()
        pr_wrapper.id = "PR_test123"

        # Pass dict with numeric ID - should raise TypeError (fail-fast enforcement)
        # Callers must normalize to list[str] before calling request_pr_reviews()
        with pytest.raises(TypeError, match="Reviewer must be str"):
            await api.request_pr_reviews(pr_wrapper, [{"id": "12345"}])


@pytest.mark.asyncio
async def test_request_pr_reviews_with_graphql_failure_skips_reviewer(mock_logger, mock_config):
    """Test request_pr_reviews raises TypeError for MagicMock reviewer objects."""
    api = UnifiedGitHubAPI(token="test_token", logger=mock_logger, config=mock_config)  # pragma: allowlist secret

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_graphql_client_cls,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_graphql = AsyncMock()
        mock_graphql_client_cls.return_value = mock_graphql

        await api.initialize()

        pr_wrapper = MagicMock()
        pr_wrapper.id = "PR_test123"

        # Fail-fast validation: MagicMock objects are invalid, expect TypeError
        reviewer = MagicMock()
        reviewer.login = "testuser"

        with pytest.raises(TypeError, match="Reviewer must be str"):
            await api.request_pr_reviews(pr_wrapper, [reviewer])


@pytest.mark.asyncio
async def test_request_pr_reviews_skips_on_graphql_failure(mock_logger, mock_config):
    """Test request_pr_reviews raises TypeError for MagicMock reviewer objects."""
    api = UnifiedGitHubAPI(token="test_token", logger=mock_logger, config=mock_config)  # pragma: allowlist secret

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_graphql_client_cls,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_graphql = AsyncMock()
        mock_graphql_client_cls.return_value = mock_graphql

        await api.initialize()

        pr_wrapper = MagicMock()
        pr_wrapper.id = "PR_test123"

        # Fail-fast validation: MagicMock objects are invalid, expect TypeError
        reviewer = MagicMock()
        reviewer.login = "testuser"
        reviewer.id = "12345"

        with pytest.raises(TypeError, match="Reviewer must be str"):
            await api.request_pr_reviews(pr_wrapper, [reviewer])


@pytest.mark.asyncio
async def test_request_pr_reviews_graphql_lookup_fails(mock_logger, mock_config):
    """Test request_pr_reviews when batch and sequential GraphQL user lookup fails."""
    api = UnifiedGitHubAPI(token="test_token", logger=mock_logger, config=mock_config)  # pragma: allowlist secret

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_graphql_client_cls,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_graphql = AsyncMock()
        mock_graphql_client_cls.return_value = mock_graphql

        await api.initialize()

        # Mock execute_batch to fail (triggers fallback to sequential)
        mock_graphql.execute_batch = AsyncMock(side_effect=GraphQLError("Batch failed"))
        # Mock GraphQL user lookup to fail in sequential fallback
        api.get_user_id = AsyncMock(side_effect=GraphQLError("GraphQL failed"))

        pr_wrapper = MagicMock()
        pr_wrapper.id = "PR_test123"

        # Should log warnings but not raise
        await api.request_pr_reviews(pr_wrapper, ["testuser"])

        # Verify warnings were logged
        assert mock_logger.warning.call_count >= 2
        log_messages = [str(call) for call in mock_logger.warning.call_args_list]
        assert any("Batch user ID resolution failed" in msg for msg in log_messages)
        assert any("Failed to get GraphQL node ID for reviewer 'testuser'" in msg for msg in log_messages)


@pytest.mark.asyncio
async def test_get_last_commit_no_commits_error(mock_logger, mock_config):
    """Test get_last_commit raises error when no commits found."""
    api = UnifiedGitHubAPI(token="test_token", logger=mock_logger, config=mock_config)  # pragma: allowlist secret

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_graphql_client_cls,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_graphql = AsyncMock()
        mock_graphql_client_cls.return_value = mock_graphql

        await api.initialize()

        # Create a PullRequestWrapper with no commits
        webhook_data = {
            "node_id": "PR_kwDOABcD1M5abc123",
            "number": 123,
            "title": "Test PR",
            "state": "open",
            "draft": False,
            "base": {"ref": "main", "sha": "abc123", "repo": {"owner": {"login": "owner"}, "name": "repo"}},
            "head": {"ref": "feature", "sha": "def456", "repo": {"owner": {"login": "owner"}, "name": "repo"}},
            "user": {"login": "testuser"},
            "commits": [],  # No commits
        }
        pr_wrapper = PullRequestWrapper("owner", "repo", webhook_data)

        # Mock get_pull_request_data to return empty commits
        api.get_pull_request_data = AsyncMock(return_value={"commits": {"nodes": []}})

        # Should raise ValueError
        with pytest.raises(ValueError, match="No commits found"):
            await api.get_last_commit("owner", "repo", pr_wrapper)


@pytest.mark.asyncio
async def test_ensure_initialized_auto_initializes(mock_logger, mock_config):
    """Test _ensure_initialized auto-initializes clients."""
    api = UnifiedGitHubAPI(token="test_token", logger=mock_logger, config=mock_config)  # pragma: allowlist secret

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient"),
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        # Initially not initialized
        assert not api._initialized
        assert api.graphql_client is None

        # Call ensure_initialized
        await api._ensure_initialized()

        # Should be initialized
        assert api._initialized
        assert api.graphql_client is not None


@pytest.mark.asyncio
async def test_get_open_pull_requests_with_details(unified_api):
    """Test get_open_pull_requests_with_details batches all data in one query."""
    # Mock GraphQL response with 3 open PRs with labels and merge state
    mock_result = {
        "repository": {
            "pullRequests": {
                "totalCount": 3,
                "nodes": [
                    {
                        "id": "PR_1",
                        "number": 1,
                        "title": "First PR",
                        "state": "OPEN",
                        "mergeStateStatus": "CLEAN",
                        "labels": {
                            "nodes": [
                                {"id": "L1", "name": "bug", "color": "d73a4a"},
                                {"id": "L2", "name": "priority-high", "color": "ff0000"},
                            ]
                        },
                    },
                    {
                        "id": "PR_2",
                        "number": 2,
                        "title": "Second PR",
                        "state": "OPEN",
                        "mergeStateStatus": "BEHIND",
                        "labels": {"nodes": [{"id": "L3", "name": "needs rebase", "color": "fbca04"}]},
                    },
                    {
                        "id": "PR_3",
                        "number": 3,
                        "title": "Third PR",
                        "state": "OPEN",
                        "mergeStateStatus": "DIRTY",
                        "labels": {"nodes": [{"id": "L4", "name": "has conflicts", "color": "e11d21"}]},
                    },
                ],
            }
        }
    }

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value=mock_result)
        mock_gql.close = AsyncMock()
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()
        prs = await unified_api.get_open_pull_requests_with_details("owner", "repo", max_prs=100)

        # Verify single API call was made
        mock_gql.execute.assert_called_once()

        # Verify all 3 PRs returned as PullRequestWrapper objects
        assert len(prs) == 3
        assert all(isinstance(pr, PullRequestWrapper) for pr in prs)

        # Verify PR data is accessible without additional API calls
        assert prs[0].number == 1
        assert prs[0].title == "First PR"
        assert prs[0].mergeable_state == "clean"

        # Verify labels are already loaded (no additional API calls)
        labels_pr1 = prs[0].get_labels()
        assert len(labels_pr1) == 2
        assert labels_pr1[0].name == "bug"
        assert labels_pr1[1].name == "priority-high"

        labels_pr2 = prs[1].get_labels()
        assert len(labels_pr2) == 1
        assert labels_pr2[0].name == "needs rebase"

        labels_pr3 = prs[2].get_labels()
        assert len(labels_pr3) == 1
        assert labels_pr3[0].name == "has conflicts"

        # Verify merge states
        assert prs[0].mergeable_state == "clean"
        assert prs[1].mergeable_state == "behind"
        assert prs[2].mergeable_state == "dirty"

        # Verify still only one GraphQL call (no N+1 pattern)
        assert mock_gql.execute.call_count == 1

    await unified_api.close()


@pytest.mark.asyncio
async def test_get_open_pull_requests_with_details_empty_result(unified_api):
    """Test get_open_pull_requests_with_details handles no open PRs."""
    mock_result = {"repository": {"pullRequests": {"totalCount": 0, "nodes": []}}}

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value=mock_result)
        mock_gql.close = AsyncMock()
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()
        prs = await unified_api.get_open_pull_requests_with_details("owner", "repo")

        assert prs == []
        mock_gql.execute.assert_called_once()

    await unified_api.close()


@pytest.mark.asyncio
async def test_get_pulls_from_commit_fallback_warning(mock_logger, mock_config):
    """Test get_pulls_from_commit_sha with correct parameters."""
    api = UnifiedGitHubAPI(token="test_token", logger=mock_logger, config=mock_config)  # pragma: allowlist secret

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql_class.return_value = mock_gql

        # Mock GraphQL response for associatedPullRequests
        mock_gql.execute = AsyncMock(
            return_value={
                "repository": {
                    "object": {
                        "associatedPullRequests": {
                            "nodes": [
                                {
                                    "id": "PR_1",
                                    "number": 1,
                                    "title": "Test PR",
                                    "state": "OPEN",
                                    "baseRefName": "main",
                                    "headRefName": "feature",
                                    "author": {"login": "testuser"},
                                    "createdAt": "2024-01-01T00:00:00Z",
                                    "updatedAt": "2024-01-02T00:00:00Z",
                                    "mergedAt": None,
                                    "closedAt": None,
                                }
                            ]
                        }
                    }
                }
            }
        )

        await api.initialize()

        # Call method with correct signature: owner, name, sha
        result = await api.get_pulls_from_commit_sha("owner", "repo", "abc123")

        # Verify result
        assert len(result) == 1
        assert result[0]["number"] == 1


@pytest.mark.asyncio
async def test_get_contributors_fallback_to_query(mock_logger, mock_config):
    """Test get_contributors falls back to individual query when repository_data is None."""
    api = UnifiedGitHubAPI(token="test_token", logger=mock_logger, config=mock_config)  # pragma: allowlist secret

    mock_contributors = [
        {"id": "U_1", "login": "user1", "name": "User One"},
        {"id": "U_2", "login": "user2", "name": "User Two"},
    ]

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"repository": {"mentionableUsers": {"nodes": mock_contributors}}})
        mock_gql_class.return_value = mock_gql

        # Initialize without graphql_client first (triggers auto-init path)
        api.graphql_client = None
        api._initialized = False

        result = await api.get_contributors("test-owner", "test-repo", repository_data=None)

        # Should have initialized
        assert api._initialized

        # Should return contributors
        assert result == mock_contributors

        # Should have called GraphQL
        mock_gql.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_collaborators_fallback_to_query(mock_logger, mock_config):
    """Test get_collaborators falls back to individual query when repository_data is None."""
    api = UnifiedGitHubAPI(token="test_token", logger=mock_logger, config=mock_config)  # pragma: allowlist secret

    mock_collaborators = [
        {"permission": "ADMIN", "node": {"id": "U_1", "login": "admin1"}},
        {"permission": "WRITE", "node": {"id": "U_2", "login": "collaborator1"}},
    ]

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"repository": {"collaborators": {"edges": mock_collaborators}}})
        mock_gql_class.return_value = mock_gql

        # Initialize without graphql_client first (triggers auto-init path)
        api.graphql_client = None
        api._initialized = False

        result = await api.get_collaborators("test-owner", "test-repo", repository_data=None)

        # Should have initialized
        assert api._initialized

        # Should return collaborators
        assert result == mock_collaborators

        # Should have called GraphQL
        mock_gql.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_user_id_graphql_error(mock_logger, mock_config):
    """Test get_user_id propagates GraphQL errors."""
    api = UnifiedGitHubAPI(token="test_token", logger=mock_logger, config=mock_config)  # pragma: allowlist secret

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(side_effect=GraphQLError("User not found"))
        mock_gql_class.return_value = mock_gql

        await api.initialize()

        # Should raise GraphQL error
        with pytest.raises(GraphQLError, match="User not found"):
            await api.get_user_id("nonexistent_user")
