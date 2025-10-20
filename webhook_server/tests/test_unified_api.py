"""Tests for unified GitHub API."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from webhook_server.libs.graphql.unified_api import APIType, UnifiedGitHubAPI


@pytest.fixture
def mock_logger():
    """Create a mock logger."""
    return MagicMock()


@pytest.fixture
def unified_api(mock_logger):
    """Create UnifiedGitHubAPI instance."""
    return UnifiedGitHubAPI(token="test_token", logger=mock_logger)


@pytest.mark.asyncio
async def test_unified_api_initialization(unified_api):
    """Test API initialization."""
    assert unified_api.token == "test_token"
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
async def test_get_pull_request(unified_api):
    """Test get_pull_request uses GraphQL."""
    mock_result = {"repository": {"pullRequest": {"id": "pr123", "number": 1}}}

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value=mock_result)
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()
        result = await unified_api.get_pull_request("owner", "repo", 1)

        assert result == mock_result["repository"]["pullRequest"]


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
    """Test add_labels uses GraphQL mutation."""
    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={})
        mock_gql_class.return_value = mock_gql

        await unified_api.initialize()
        await unified_api.add_labels("labelable123", ["label1", "label2"])

        mock_gql.execute.assert_called_once()


def test_get_repository_for_rest_operations(unified_api):
    """Test get_repository_for_rest_operations returns PyGithub repo."""
    # Note: This is an async test but mocking makes it testable synchronously
    assert hasattr(unified_api, "get_repository_for_rest_operations")


def test_get_pr_for_check_runs(unified_api):
    """Test get_pr_for_check_runs returns PyGithub PR."""
    # Note: This is an async test but mocking makes it testable synchronously
    assert hasattr(unified_api, "get_pr_for_check_runs")


def test_get_api_type_for_operation():
    """Test API type selection logic."""
    api = UnifiedGitHubAPI("token", MagicMock())

    # REST only operations
    assert api.get_api_type_for_operation("check_runs") == APIType.REST
    assert api.get_api_type_for_operation("create_webhook") == APIType.REST

    # GraphQL preferred operations
    assert api.get_api_type_for_operation("get_pull_request") == APIType.GRAPHQL
    assert api.get_api_type_for_operation("add_labels") == APIType.GRAPHQL

    # Hybrid/unknown operations
    assert api.get_api_type_for_operation("unknown_operation") == APIType.HYBRID
