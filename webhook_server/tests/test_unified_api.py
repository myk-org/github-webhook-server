"""Tests for unified GitHub API."""

import asyncio
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
    return UnifiedGitHubAPI(token="test_token", logger=mock_logger)  # pragma: allowlist secret


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

        # Assert the mutation starts with "mutation"
        assert mutation.strip().startswith("mutation"), "Mutation should start with 'mutation' keyword"

        # Assert the mutation contains the addLabelsToLabelable operation
        assert "addLabelsToLabelable" in mutation, "Mutation should contain addLabelsToLabelable operation"

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


def test_get_api_type_for_operation():
    """Test API type selection logic."""
    api = UnifiedGitHubAPI("token", MagicMock())  # pragma: allowlist secret

    # REST only operations
    assert api.get_api_type_for_operation("check_runs") == APIType.REST
    assert api.get_api_type_for_operation("create_webhook") == APIType.REST
    assert api.get_api_type_for_operation("get_issues") == APIType.REST

    # GraphQL preferred operations
    assert api.get_api_type_for_operation("get_pull_request") == APIType.GRAPHQL
    assert api.get_api_type_for_operation("add_labels") == APIType.GRAPHQL

    # Hybrid/unknown operations
    assert api.get_api_type_for_operation("unknown_operation") == APIType.HYBRID


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
    api = UnifiedGitHubAPI("test_token", logger)  # pragma: allowlist secret

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
    api = UnifiedGitHubAPI("test_token", logger)  # pragma: allowlist secret

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
async def test_binary_file_fallback_to_rest():
    """
    Test that binary files fall back to REST API correctly.

    Verifies that when GraphQL returns isBinary=True, the API falls back
    to REST API's get_contents() method for proper binary file handling.

    Test: GraphQL returns isBinary=True, verify fallback to REST API
    Verify: get_contents called, decoded_content used correctly
    """
    logger = MagicMock()
    api = UnifiedGitHubAPI("test_token", logger)  # pragma: allowlist secret

    # Mock GraphQL response for binary file
    binary_blob_response = {"repository": {"object": {"isBinary": True, "text": None}}}

    # Mock REST API response with binary content
    mock_contents = MagicMock()
    mock_contents.decoded_content = b"Binary file content here"

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github") as mock_rest_class,
        patch.object(api, "get_contents", new=AsyncMock(return_value=mock_contents)) as mock_get_contents,
    ):
        # Setup GraphQL mock
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value=binary_blob_response)
        mock_gql.close = AsyncMock()
        mock_gql_class.return_value = mock_gql

        # Setup REST mock
        mock_rest = MagicMock()
        mock_rest.close = MagicMock()
        mock_rest_class.return_value = mock_rest

        await api.initialize()

        # Fetch binary file
        result = await api.get_file_contents(owner="test-owner", name="test-repo", path="image.png", ref="main")

        # Verify GraphQL was called first
        assert mock_gql.execute.call_count == 1

        # Verify fallback to REST API
        mock_get_contents.assert_called_once_with("test-owner", "test-repo", "image.png", "main")

        # Verify binary content was decoded correctly
        assert result == "Binary file content here"

    # Cleanup
    await api.close()


@pytest.mark.asyncio
async def test_text_file_uses_graphql_no_fallback():
    """
    Test that text files use GraphQL without falling back to REST.

    Verifies that normal text files don't trigger REST fallback.
    """
    logger = MagicMock()
    api = UnifiedGitHubAPI("test_token", logger)  # pragma: allowlist secret

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


@pytest.mark.asyncio
async def test_null_text_triggers_rest_fallback():
    """
    Test that null text (even without isBinary flag) triggers REST fallback.

    Verifies edge case where text is None but isBinary might be False/missing.
    """
    logger = MagicMock()
    api = UnifiedGitHubAPI("test_token", logger)  # pragma: allowlist secret

    # Mock GraphQL response with null text but no isBinary flag
    null_text_response = {"repository": {"object": {"text": None}}}

    # Mock REST API response
    mock_contents = MagicMock()
    mock_contents.decoded_content = b"Content from REST API"

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github") as mock_rest_class,
        patch.object(api, "get_contents", new=AsyncMock(return_value=mock_contents)) as mock_get_contents,
    ):
        # Setup GraphQL mock
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value=null_text_response)
        mock_gql.close = AsyncMock()
        mock_gql_class.return_value = mock_gql

        # Setup REST mock
        mock_rest = MagicMock()
        mock_rest.close = MagicMock()
        mock_rest_class.return_value = mock_rest

        await api.initialize()

        # Fetch file with null text
        result = await api.get_file_contents(owner="test-owner", name="test-repo", path="data.bin", ref="main")

        # Verify fallback to REST API triggered
        mock_get_contents.assert_called_once_with("test-owner", "test-repo", "data.bin", "main")

        # Verify result from REST API
        assert result == "Content from REST API"

    # Cleanup
    await api.close()
