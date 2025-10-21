"""Test GraphQL client error handling."""

import pytest
from unittest.mock import AsyncMock, Mock
from gql.transport.exceptions import TransportQueryError, TransportServerError

from webhook_server.libs.graphql.graphql_client import GraphQLClient, GraphQLAuthenticationError, GraphQLRateLimitError

# Test token constant to silence S106 security warnings
TEST_GITHUB_TOKEN = "ghs_" + "test1234567890abcdefghijklmnopqrstuvwxyz"  # pragma: allowlist secret


@pytest.fixture
def graphql_client():
    return GraphQLClient(token=TEST_GITHUB_TOKEN, logger=Mock())


@pytest.mark.asyncio
async def test_authentication_error(graphql_client):
    """Test 401 authentication error."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=TransportQueryError("401: Unauthorized"))

    # Create a mock client that behaves like an async context manager
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_session
    mock_client.__aexit__.return_value = None
    mock_client.close_async = AsyncMock()

    # Replace the client and bypass _ensure_client
    graphql_client._client = mock_client
    graphql_client._ensure_client = AsyncMock()  # Don't recreate client

    with pytest.raises(GraphQLAuthenticationError):
        await graphql_client.execute("query { viewer { login } }")


@pytest.mark.asyncio
async def test_rate_limit_error_raises(graphql_client):
    """Test rate limit error is raised when retry fails."""
    # Just test that rate limit errors are properly detected and raised
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=TransportQueryError("RATE_LIMITED"))

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_session
    mock_client.__aexit__.return_value = None
    mock_client.close_async = AsyncMock()
    graphql_client._client = mock_client
    graphql_client._ensure_client = AsyncMock()

    # This should raise GraphQLRateLimitError (after trying to get rate limit info and failing)
    with pytest.raises(GraphQLRateLimitError):
        await graphql_client.execute("query { viewer { login } }")


@pytest.mark.asyncio
async def test_rate_limit_exhausted(graphql_client):
    """Test rate limit error that exhausts retries."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=TransportQueryError("RATE_LIMITED"))

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_session
    mock_client.__aexit__.return_value = None
    mock_client.close_async = AsyncMock()
    graphql_client._client = mock_client
    graphql_client._ensure_client = AsyncMock()  # Don't recreate client

    with pytest.raises(GraphQLRateLimitError):
        await graphql_client.execute("query { viewer { login } }")


@pytest.mark.asyncio
async def test_server_error_with_retry(graphql_client, monkeypatch):
    """Test 500 server error retries with exponential backoff before failing."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=TransportServerError("500: Internal server error"))

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_session
    mock_client.__aexit__.return_value = None
    mock_client.close_async = AsyncMock()
    graphql_client._client = mock_client
    graphql_client._ensure_client = AsyncMock()  # Don't recreate client

    # Patch asyncio.sleep to avoid real delays
    mock_sleep = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", mock_sleep)

    from webhook_server.libs.graphql.graphql_client import GraphQLError

    # Server errors retry with backoff, then fail after retry_count attempts
    with pytest.raises(GraphQLError, match="GraphQL server error"):
        await graphql_client.execute("query { viewer { login } }")

    # Verify retries happened (default retry_count=3 means 3 attempts, 2 sleeps between them)
    assert mock_session.execute.call_count == 3
    assert mock_sleep.call_count == 2  # 2 sleeps between 3 attempts
    # Verify exponential backoff: 2^0=1s, 2^1=2s
    mock_sleep.assert_any_call(1)
    mock_sleep.assert_any_call(2)


@pytest.mark.asyncio
async def test_generic_query_error_no_retry(graphql_client):
    """Test generic query error fails immediately without retry."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=TransportQueryError("Generic error"))

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_session
    mock_client.__aexit__.return_value = None
    mock_client.close_async = AsyncMock()
    graphql_client._client = mock_client
    graphql_client._ensure_client = AsyncMock()  # Don't recreate client

    from webhook_server.libs.graphql.graphql_client import GraphQLError

    # Generic query errors don't retry - they fail immediately
    with pytest.raises(GraphQLError, match="GraphQL query failed"):
        await graphql_client.execute("query { viewer { login } }")
