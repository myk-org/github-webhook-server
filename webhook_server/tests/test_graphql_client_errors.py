"""Test GraphQL client error handling."""

import pytest
from unittest.mock import AsyncMock, patch
from gql.transport.exceptions import TransportQueryError, TransportServerError

from webhook_server.libs.graphql.graphql_client import GraphQLClient, GraphQLAuthenticationError, GraphQLRateLimitError


@pytest.fixture
def graphql_client():
    from unittest.mock import Mock

    return GraphQLClient(token="test_token", logger=Mock())


@pytest.mark.asyncio
async def test_authentication_error(graphql_client):
    """Test 401 authentication error."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=TransportQueryError("401: Unauthorized"))

    # Create a mock client that behaves like an async context manager
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_session
    mock_client.__aexit__.return_value = None

    # Replace the client
    graphql_client._client = mock_client

    with pytest.raises(GraphQLAuthenticationError):
        await graphql_client.execute("query { viewer { login } }")


@pytest.mark.asyncio
async def test_rate_limit_with_retry_success(graphql_client):
    """Test rate limit error that succeeds on retry."""
    mock_result = {"viewer": {"login": "testuser"}}
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(
        side_effect=[
            TransportQueryError("rate limit exceeded"),
            mock_result,
        ]
    )

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_session
    mock_client.__aexit__.return_value = None
    graphql_client._client = mock_client

    # Mock asyncio.sleep to avoid waiting
    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await graphql_client.execute("query { viewer { login } }")
        assert result == mock_result
        assert mock_session.execute.call_count == 2


@pytest.mark.asyncio
async def test_rate_limit_exhausted(graphql_client):
    """Test rate limit error that exhausts retries."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=TransportQueryError("RATE_LIMITED"))

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_session
    mock_client.__aexit__.return_value = None
    graphql_client._client = mock_client

    with pytest.raises(GraphQLRateLimitError):
        await graphql_client.execute("query { viewer { login } }")


@pytest.mark.asyncio
async def test_server_error_with_retry_success(graphql_client):
    """Test 500 server error that succeeds on retry."""
    mock_result = {"viewer": {"login": "testuser"}}
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(
        side_effect=[
            TransportServerError("500: Internal server error"),
            mock_result,
        ]
    )

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_session
    mock_client.__aexit__.return_value = None
    graphql_client._client = mock_client

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await graphql_client.execute("query { viewer { login } }")
        assert result == mock_result
        assert mock_session.execute.call_count == 2


@pytest.mark.asyncio
async def test_generic_query_error_with_retry(graphql_client):
    """Test generic query error with retry."""
    mock_result = {"viewer": {"login": "testuser"}}
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(
        side_effect=[
            TransportQueryError("Generic error"),
            mock_result,
        ]
    )

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_session
    mock_client.__aexit__.return_value = None
    graphql_client._client = mock_client

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await graphql_client.execute("query { viewer { login } }")
        assert result == mock_result
        assert mock_session.execute.call_count == 2
