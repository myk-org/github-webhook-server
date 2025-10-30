"""Test GraphQL client error handling."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from gql.transport.exceptions import TransportError, TransportQueryError, TransportServerError

from webhook_server.libs.graphql.graphql_client import (
    GraphQLAuthenticationError,
    GraphQLClient,
    GraphQLError,
    GraphQLRateLimitError,
)

# Test token constant
TEST_GITHUB_TOKEN = "test_token_12345"  # noqa: S105


@pytest.fixture
def graphql_client():
    return GraphQLClient(token=TEST_GITHUB_TOKEN, logger=Mock())


@pytest.mark.asyncio
async def test_authentication_error(graphql_client):
    """Test 401 authentication error."""
    # Create a mock session that raises auth error
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=TransportQueryError("401: Unauthorized"))

    # Create a mock client with session
    mock_client = AsyncMock()
    mock_client.connect_async = AsyncMock()
    mock_client.close_async = AsyncMock()
    mock_client.session = mock_session

    # Replace the client and bypass _ensure_client
    graphql_client._client = mock_client
    graphql_client._session = mock_session
    graphql_client._ensure_client = AsyncMock()  # Don't recreate client

    with pytest.raises(GraphQLAuthenticationError):
        await graphql_client.execute("query { viewer { login } }")


@pytest.mark.asyncio
async def test_rate_limit_error_raises(graphql_client, monkeypatch):
    """Test rate limit error is raised when retry fails."""

    # Mock session that fails for both main query and rate limit query
    def execute_side_effect(query, *_args, **_kwargs):
        query_str = str(query)
        if "rateLimit" in query_str and "resetAt" in query_str:
            # Fail the rate limit query too
            raise Exception("Failed to get rate limit info")
        # Main query fails with rate limit
        raise TransportQueryError("RATE_LIMITED")

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)

    mock_client = AsyncMock()
    mock_client.connect_async = AsyncMock()
    mock_client.close_async = AsyncMock()
    mock_client.session = mock_session

    graphql_client._client = mock_client
    graphql_client._session = mock_session
    graphql_client._ensure_client = AsyncMock()

    # Patch asyncio.sleep to avoid real delays
    mock_sleep = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", mock_sleep)

    # This should raise GraphQLRateLimitError (after trying to get rate limit info and failing)
    with pytest.raises(GraphQLRateLimitError):
        await graphql_client.execute("query { viewer { login } }")

    # Verify no sleep was called since we couldn't get rate limit info
    assert mock_sleep.call_count == 0


@pytest.mark.asyncio
async def test_rate_limit_exhausted(graphql_client, monkeypatch):
    """Test rate limit error that exhausts retries."""

    # Mock session that fails for both main query and rate limit query
    def execute_side_effect(query, *_args, **_kwargs):
        query_str = str(query)
        if "rateLimit" in query_str and "resetAt" in query_str:
            # Fail the rate limit query too
            raise Exception("Network error")
        # Main query fails with rate limit
        raise TransportQueryError("RATE_LIMITED")

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)

    mock_client = AsyncMock()
    mock_client.connect_async = AsyncMock()
    mock_client.close_async = AsyncMock()
    mock_client.session = mock_session

    graphql_client._client = mock_client
    graphql_client._session = mock_session
    graphql_client._ensure_client = AsyncMock()  # Don't recreate client
    graphql_client.retry_count = 1  # Reduce retries to exhaust quickly

    # Patch asyncio.sleep to avoid real delays
    mock_sleep = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", mock_sleep)

    with pytest.raises(GraphQLRateLimitError):
        await graphql_client.execute("query { viewer { login } }")

    # Verify no sleep was called (failed to get rate limit info, so raised immediately)
    assert mock_sleep.call_count == 0


@pytest.mark.asyncio
async def test_server_error_with_retry(graphql_client, monkeypatch):
    """Test 500 server error retries with exponential backoff before failing."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=TransportServerError("500: Internal server error"))

    mock_client = AsyncMock()
    mock_client.connect_async = AsyncMock()
    mock_client.close_async = AsyncMock()
    mock_client.session = mock_session

    # _ensure_client mock that maintains session reference
    async def ensure_client_mock():
        if graphql_client._session is None:
            graphql_client._session = mock_session
        if graphql_client._client is None:
            graphql_client._client = mock_client

    graphql_client._client = mock_client
    graphql_client._session = mock_session
    graphql_client._ensure_client = AsyncMock(side_effect=ensure_client_mock)

    # Patch asyncio.sleep to avoid real delays
    mock_sleep = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", mock_sleep)

    # Server errors are now caught by TransportServerError handler (before TransportError)
    # This uses exponential backoff retry behavior
    with pytest.raises(GraphQLError, match="GraphQL server error: 500: Internal server error"):
        await graphql_client.execute("query { viewer { login } }")

    # Verify retries happened (default retry_count=3 means 3 attempts)
    assert mock_session.execute.call_count == 3
    # TransportServerError handler uses exponential backoff (2^0=1s, 2^1=2s)
    assert mock_sleep.call_count == 2  # 2 sleeps between 3 attempts
    # Verify exponential backoff pattern with jitter: base + random(0,1)
    # First retry: 2^0 + jitter = 1s-2s range
    assert 1.0 <= mock_sleep.call_args_list[0][0][0] <= 2.0
    # Second retry: 2^1 + jitter = 2s-3s range
    assert 2.0 <= mock_sleep.call_args_list[1][0][0] <= 3.0


@pytest.mark.asyncio
async def test_generic_query_error_no_retry(graphql_client):
    """Test generic query error fails immediately without retry."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=TransportQueryError("Generic error"))

    mock_client = AsyncMock()
    mock_client.connect_async = AsyncMock()
    mock_client.close_async = AsyncMock()
    mock_client.session = mock_session

    graphql_client._client = mock_client
    graphql_client._session = mock_session
    graphql_client._ensure_client = AsyncMock()  # Don't recreate client

    # Generic query errors don't retry - they fail immediately
    with pytest.raises(GraphQLError, match="GraphQL query failed"):
        await graphql_client.execute("query { viewer { login } }")


@pytest.mark.asyncio
async def test_connection_failed_retry_success(graphql_client, monkeypatch):
    """Test connection failure retries with fresh client and succeeds."""
    # Track calls to verify retry behavior
    call_count = {"count": 0}

    def execute_side_effect(*_args, **_kwargs):
        call_count["count"] += 1
        if call_count["count"] == 1:
            # First attempt fails with connection error
            raise TransportError("Connection lost")
        # Second attempt succeeds
        return {"viewer": {"login": "test-user"}}

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)

    mock_client = AsyncMock()
    mock_client.connect_async = AsyncMock()
    mock_client.close_async = AsyncMock()
    mock_client.session = mock_session

    # Mock _ensure_client to return our mock client
    async def ensure_client_side_effect():
        if graphql_client._client is None:
            graphql_client._client = mock_client
            graphql_client._session = mock_session

    graphql_client._ensure_client = AsyncMock(side_effect=ensure_client_side_effect)
    graphql_client._client = mock_client
    graphql_client._session = mock_session

    # Patch asyncio.sleep to avoid real delays
    mock_sleep = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", mock_sleep)

    # Execute query - should fail once, then succeed on retry
    result = await graphql_client.execute("query { viewer { login } }")

    # Verify retry happened
    assert call_count["count"] == 2
    assert result == {"viewer": {"login": "test-user"}}

    # Verify sleep was called once between retries
    mock_sleep.assert_called_once_with(1)

    # Verify client was recreated (ensured twice - initial + after failure)
    assert graphql_client._ensure_client.call_count == 2


@pytest.mark.asyncio
async def test_connection_failed_exhausts_retries(graphql_client, monkeypatch):
    """Test connection failure that exhausts all retry attempts."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=TransportError("Connection lost"))

    mock_client = AsyncMock()
    mock_client.connect_async = AsyncMock()
    mock_client.close_async = AsyncMock()
    mock_client.session = mock_session

    # Mock _ensure_client to return our mock client
    async def ensure_client_side_effect():
        if graphql_client._client is None:
            graphql_client._client = mock_client
            graphql_client._session = mock_session

    graphql_client._ensure_client = AsyncMock(side_effect=ensure_client_side_effect)
    graphql_client._client = mock_client
    graphql_client._session = mock_session

    # Patch asyncio.sleep to avoid real delays
    mock_sleep = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", mock_sleep)

    # Should exhaust retries and raise GraphQLError
    with pytest.raises(GraphQLError, match="GraphQL connection closed"):
        await graphql_client.execute("query { viewer { login } }")

    # Verify retries happened (default retry_count=3 means 3 attempts)
    assert mock_session.execute.call_count == 3

    # Verify sleep was called between retry attempts (2 sleeps for 3 attempts)
    assert mock_sleep.call_count == 2
    mock_sleep.assert_any_call(1)


@pytest.mark.asyncio
async def test_rate_limit_wait_and_retry_success(graphql_client, monkeypatch):
    """Test rate limit error triggers wait based on reset time and succeeds on retry."""
    # Track calls to verify retry behavior
    call_count = {"main_query_count": 0, "rate_limit_query_count": 0}

    def execute_side_effect(query, *_args, **_kwargs):
        # Check if this is the rate limit query by accessing the query source
        # DocumentNode has loc.source.body that contains the actual query string
        query_source = query.loc.source.body if hasattr(query, "loc") and hasattr(query.loc, "source") else str(query)
        if "rateLimit" in query_source and "resetAt" in query_source:
            # Return GraphQL rate limit response
            call_count["rate_limit_query_count"] += 1
            reset_time = datetime(2024, 1, 1, 12, 1, 0, tzinfo=UTC)  # 60 seconds from now
            return {"rateLimit": {"resetAt": reset_time.isoformat()}}

        call_count["main_query_count"] += 1
        if call_count["main_query_count"] == 1:
            # First attempt fails with rate limit error
            raise TransportQueryError("RATE_LIMITED: API rate limit exceeded")
        # Second attempt succeeds
        return {"viewer": {"login": "test-user"}}

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)

    mock_client = AsyncMock()
    mock_client.connect_async = AsyncMock()
    mock_client.close_async = AsyncMock()
    mock_client.session = mock_session

    graphql_client._client = mock_client
    graphql_client._session = mock_session
    graphql_client._ensure_client = AsyncMock()

    # Freeze time to fixed value for deterministic testing
    fixed_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    reset_datetime = datetime(2024, 1, 1, 12, 1, 0, tzinfo=UTC)  # Reset in 60 seconds

    # Create MockDatetime class that implements both now() and fromtimestamp()
    class MockDatetime:
        @staticmethod
        def now(tz=None):  # noqa: ARG004
            return fixed_time

        @staticmethod
        def fromtimestamp(timestamp, tz=None):  # noqa: ARG004
            return reset_datetime

        @staticmethod
        def fromisoformat(date_string):  # noqa: ARG004
            # Return a proper datetime object with timestamp() method
            return reset_datetime

    # Mock datetime module to return MockDatetime
    monkeypatch.setattr(
        "webhook_server.libs.graphql.graphql_client.datetime",
        MockDatetime,
    )

    # Patch asyncio.sleep to avoid real delays
    mock_sleep = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", mock_sleep)

    # Execute query - should fail with rate limit, wait, then succeed on retry
    result = await graphql_client.execute("query { viewer { login } }")

    # Verify retry happened (main query called twice: initial fail + retry success)
    assert call_count["main_query_count"] == 2
    # Rate limit query called once to get reset time
    assert call_count["rate_limit_query_count"] == 1
    assert result == {"viewer": {"login": "test-user"}}

    # Verify sleep was called with correct wait time (60s + 5s buffer = 65s exactly)
    assert mock_sleep.call_count == 1
    # With frozen time, wait time should be exactly 65 seconds (60s until reset + 5s buffer)
    actual_wait = mock_sleep.call_args[0][0]
    assert actual_wait == 65, f"Expected wait time exactly 65s, got {actual_wait}s"


@pytest.mark.asyncio
async def test_rate_limit_no_reset_info_fails(graphql_client, monkeypatch):
    """Test rate limit error without reset info raises GraphQLRateLimitError."""

    # Mock session that fails for both main query and rate limit query
    def execute_side_effect(query, *_args, **_kwargs):
        query_str = str(query)
        if "rateLimit" in query_str and "resetAt" in query_str:
            # Fail the rate limit query
            raise Exception("Network error")
        # Main query fails with rate limit
        raise TransportQueryError("RATE_LIMITED: API rate limit exceeded")

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)

    mock_client = AsyncMock()
    mock_client.connect_async = AsyncMock()
    mock_client.close_async = AsyncMock()
    mock_client.session = mock_session

    graphql_client._client = mock_client
    graphql_client._session = mock_session
    graphql_client._ensure_client = AsyncMock()

    # Patch asyncio.sleep to avoid real delays
    mock_sleep = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", mock_sleep)

    # Should raise GraphQLRateLimitError when we can't get reset info
    with pytest.raises(GraphQLRateLimitError, match="Rate limit exceeded"):
        await graphql_client.execute("query { viewer { login } }")

    # Verify no sleep was called since we couldn't get rate limit info
    assert mock_sleep.call_count == 0


@pytest.mark.asyncio
async def test_timeout_error_cleanup(graphql_client):
    """Test timeout error triggers proper cleanup of client resources."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=TimeoutError("Query timeout"))

    mock_client = AsyncMock()
    mock_client.connect_async = AsyncMock()
    mock_client.close_async = AsyncMock()
    mock_client.session = mock_session

    graphql_client._client = mock_client
    graphql_client._session = mock_session
    graphql_client._transport = Mock()  # Add a transport object
    graphql_client._ensure_client = AsyncMock()

    # Should raise GraphQLError wrapping TimeoutError
    with pytest.raises(GraphQLError, match="GraphQL query timeout"):
        await graphql_client.execute("query { viewer { login } }")

    # Verify cleanup happened
    mock_client.close_async.assert_called_once()
    assert graphql_client._client is None
    assert graphql_client._transport is None


@pytest.mark.asyncio
async def test_cancelled_error_propagation(graphql_client):
    """Test CancelledError is re-raised without wrapping in GraphQLError."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=asyncio.CancelledError())

    mock_client = AsyncMock()
    mock_client.connect_async = AsyncMock()
    mock_client.close_async = AsyncMock()
    mock_client.session = mock_session

    graphql_client._client = mock_client
    graphql_client._session = mock_session
    graphql_client._ensure_client = AsyncMock()

    # Should propagate CancelledError as-is, not wrapped in GraphQLError
    with pytest.raises(asyncio.CancelledError):
        await graphql_client.execute("query { viewer { login } }")

    # Verify debug log was called (can check logger mock if needed)
