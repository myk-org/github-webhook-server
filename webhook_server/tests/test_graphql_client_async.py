"""Additional async tests for GraphQL client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from webhook_server.libs.graphql.graphql_client import GraphQLClient


@pytest.fixture
def mock_logger():
    """Create a mock logger."""
    return MagicMock()


@pytest.mark.asyncio
async def test_graphql_client_auto_initialize(mock_logger):
    """Test client auto-initializes when calling methods."""
    client = GraphQLClient(token="test_token", logger=mock_logger)

    mock_result = {"rateLimit": {"limit": 5000}}

    with (
        patch("webhook_server.libs.graphql.graphql_client.AIOHTTPTransport"),
        patch("webhook_server.libs.graphql.graphql_client.Client") as mock_client_class,
    ):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_gql_client = AsyncMock()
        mock_gql_client.__aenter__ = AsyncMock(return_value=mock_session)
        mock_gql_client.__aexit__ = AsyncMock()

        mock_client_class.return_value = mock_gql_client

        # Client should auto-initialize
        result = await client.execute("query { rateLimit { limit } }")

        assert result == mock_result
        assert client._client is not None


@pytest.mark.asyncio
async def test_graphql_client_with_variables(mock_logger):
    """Test query execution with variables."""
    client = GraphQLClient(token="test_token", logger=mock_logger)

    mock_result = {"addComment": {"comment": {"id": "123"}}}
    variables = {"subjectId": "PR_123", "body": "Test"}

    with (
        patch("webhook_server.libs.graphql.graphql_client.AIOHTTPTransport"),
        patch("webhook_server.libs.graphql.graphql_client.Client") as mock_client_class,
    ):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_gql_client = AsyncMock()
        mock_gql_client.__aenter__ = AsyncMock(return_value=mock_session)
        mock_gql_client.__aexit__ = AsyncMock()

        mock_client_class.return_value = mock_gql_client

        result = await client.execute("mutation { addComment }", variables=variables)

        assert result == mock_result
        # Verify variables were passed
        mock_session.execute.assert_called()


@pytest.mark.asyncio
async def test_graphql_client_custom_timeout(mock_logger):
    """Test client with custom timeout and retry count."""
    client = GraphQLClient(token="test_token", logger=mock_logger, retry_count=5, timeout=60)

    assert client.retry_count == 5
    assert client.timeout == 60


@pytest.mark.asyncio
async def test_get_viewer_info_method(mock_logger):
    """Test get_viewer_info helper method."""
    client = GraphQLClient(token="test_token", logger=mock_logger)

    mock_result = {
        "viewer": {
            "login": "testuser",
            "name": "Test User",
            "id": "U_123",
            "avatarUrl": "https://example.com/avatar.png",
            "email": "test@example.com",
        }
    }

    with (
        patch("webhook_server.libs.graphql.graphql_client.AIOHTTPTransport"),
        patch("webhook_server.libs.graphql.graphql_client.Client") as mock_client_class,
    ):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_gql_client = AsyncMock()
        mock_gql_client.__aenter__ = AsyncMock(return_value=mock_session)
        mock_gql_client.__aexit__ = AsyncMock()

        mock_client_class.return_value = mock_gql_client

        result = await client.get_viewer_info()

        assert result["login"] == "testuser"
        assert result["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_execute_batch_empty_list(mock_logger):
    """Test execute_batch with empty query list."""
    client = GraphQLClient(token="test_token", logger=mock_logger)

    with (
        patch("webhook_server.libs.graphql.graphql_client.AIOHTTPTransport"),
        patch("webhook_server.libs.graphql.graphql_client.Client"),
    ):
        results = await client.execute_batch([])

        assert results == []


@pytest.mark.asyncio
async def test_close_when_not_initialized(mock_logger):
    """Test close when client was never initialized."""
    client = GraphQLClient(token="test_token", logger=mock_logger)

    # Should not raise error
    await client.close()

    assert client._client is None


@pytest.mark.asyncio
async def test_ensure_client_idempotent(mock_logger):
    """Test _ensure_client can be called multiple times."""
    client = GraphQLClient(token="test_token", logger=mock_logger)

    with (
        patch("webhook_server.libs.graphql.graphql_client.AIOHTTPTransport"),
        patch("webhook_server.libs.graphql.graphql_client.Client"),
    ):
        await client._ensure_client()
        first_client = client._client

        await client._ensure_client()
        second_client = client._client

        # Should be the same client instance
        assert first_client is second_client
