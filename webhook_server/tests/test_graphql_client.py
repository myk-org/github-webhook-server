"""Tests for GraphQL client wrapper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from gql.transport.exceptions import TransportQueryError

from webhook_server.libs.graphql.graphql_client import (
    GraphQLClient,
    GraphQLError,
)

# Test token constant to silence S106 security warnings
TEST_GITHUB_TOKEN = "ghs_" + "test1234567890abcdefghijklmnopqrstuvwxyz"  # pragma: allowlist secret


@pytest.fixture
def mock_logger():
    """Create a mock logger."""
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


@pytest.fixture
def graphql_client(mock_logger):
    """Create a GraphQL client instance."""
    return GraphQLClient(token=TEST_GITHUB_TOKEN, logger=mock_logger)


@pytest.mark.asyncio
async def test_graphql_client_initialization(graphql_client, mock_logger):
    """Test GraphQL client initialization."""
    assert graphql_client.token == TEST_GITHUB_TOKEN
    assert graphql_client.logger == mock_logger
    assert graphql_client.retry_count == 3
    assert graphql_client.timeout == 90
    assert graphql_client._client is None
    assert graphql_client._client_lock is not None


@pytest.mark.asyncio
async def test_context_manager(graphql_client):
    """Test async context manager."""
    with (
        patch("webhook_server.libs.graphql.graphql_client.AIOHTTPTransport"),
        patch("webhook_server.libs.graphql.graphql_client.Client") as mock_client_class,
    ):
        mock_client = AsyncMock()
        mock_client.connect_async = AsyncMock()
        mock_client.close_async = AsyncMock()
        mock_client.session = AsyncMock()
        mock_client_class.return_value = mock_client

        async with graphql_client as client:
            assert client is graphql_client
            assert graphql_client._client is not None
            mock_client.connect_async.assert_called_once()

        # Verify cleanup after exiting context manager
        mock_client.close_async.assert_called_once()
        assert graphql_client._client is None


@pytest.mark.asyncio
async def test_execute_success(graphql_client):
    """Test successful query execution."""
    mock_result = {"viewer": {"login": "testuser"}}

    with (
        patch("webhook_server.libs.graphql.graphql_client.AIOHTTPTransport"),
        patch("webhook_server.libs.graphql.graphql_client.Client") as mock_client_class,
    ):
        # Create a mock session that returns the result
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_client = AsyncMock()
        mock_client.connect_async = AsyncMock()
        mock_client.close_async = AsyncMock()
        mock_client.session = mock_session

        mock_client_class.return_value = mock_client

        # Manually set _session to the mock session to bypass _ensure_client issues
        graphql_client._session = mock_session
        graphql_client._client = mock_client

        result = await graphql_client.execute("query { viewer { login } }")

        assert result == mock_result
        mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_execute_batch(graphql_client):
    """Test batch query execution."""
    mock_result_1 = {"viewer": {"login": "testuser"}}
    mock_result_2 = {"rateLimit": {"remaining": 5000}}

    with (
        patch("webhook_server.libs.graphql.graphql_client.AIOHTTPTransport"),
        patch("webhook_server.libs.graphql.graphql_client.Client") as mock_client_class,
    ):
        # Create a mock session that returns results
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[mock_result_1, mock_result_2])

        mock_client = AsyncMock()
        mock_client.connect_async = AsyncMock()
        mock_client.close_async = AsyncMock()
        mock_client.session = mock_session

        mock_client_class.return_value = mock_client

        # Manually set _session to the mock session
        graphql_client._session = mock_session
        graphql_client._client = mock_client

        queries = [
            ("query { viewer { login } }", None),
            ("query { rateLimit { remaining } }", None),
        ]

        results = await graphql_client.execute_batch(queries)

        assert len(results) == 2
        assert results[0] == mock_result_1
        assert results[1] == mock_result_2
        assert mock_session.execute.call_count == 2


@pytest.mark.asyncio
async def test_get_rate_limit(graphql_client):
    """Test get_rate_limit helper method."""
    mock_result = {
        "rateLimit": {
            "limit": 5000,
            "remaining": 4999,
            "resetAt": "2024-01-01T00:00:00Z",
            "cost": 1,
        }
    }

    with (
        patch("webhook_server.libs.graphql.graphql_client.AIOHTTPTransport"),
        patch("webhook_server.libs.graphql.graphql_client.Client") as mock_client_class,
    ):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_client = AsyncMock()
        mock_client.connect_async = AsyncMock()
        mock_client.close_async = AsyncMock()
        mock_client.session = mock_session

        mock_client_class.return_value = mock_client

        # Manually set _session to the mock session
        graphql_client._session = mock_session
        graphql_client._client = mock_client

        result = await graphql_client.get_rate_limit()

        assert result == mock_result["rateLimit"]
        mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_viewer_info(graphql_client):
    """Test get_viewer_info helper method."""
    mock_result = {
        "viewer": {
            "login": "testuser",
            "name": "Test User",
            "id": "12345",
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

        mock_client = AsyncMock()
        mock_client.connect_async = AsyncMock()
        mock_client.close_async = AsyncMock()
        mock_client.session = mock_session

        mock_client_class.return_value = mock_client

        # Manually set _session to the mock session
        graphql_client._session = mock_session
        graphql_client._client = mock_client

        result = await graphql_client.get_viewer_info()

        assert result == mock_result["viewer"]
        mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_close(graphql_client):
    """Test client cleanup."""
    with (
        patch("webhook_server.libs.graphql.graphql_client.AIOHTTPTransport"),
        patch("webhook_server.libs.graphql.graphql_client.Client") as mock_client_class,
    ):
        mock_client = AsyncMock()
        mock_client.connect_async = AsyncMock()
        mock_client.close_async = AsyncMock()
        mock_client.session = AsyncMock()
        mock_client_class.return_value = mock_client

        await graphql_client._ensure_client()
        assert graphql_client._client is not None
        assert graphql_client._transport is not None
        mock_client.connect_async.assert_called_once()

        await graphql_client.close()
        assert graphql_client._client is None
        assert graphql_client._transport is None
        assert graphql_client._session is None  # Confirm session cleared to prevent reuse
        mock_client.close_async.assert_called_once()


@pytest.mark.asyncio
async def test_batch_concurrency_limit_clamping(mock_logger):
    """Test batch_concurrency_limit is clamped to maximum of 100."""
    # Test with limit > 100 (should be clamped to 100)
    client = GraphQLClient(token=TEST_GITHUB_TOKEN, logger=mock_logger, batch_concurrency_limit=150)
    assert client.batch_concurrency_limit == 100
    mock_logger.warning.assert_called_once()
    assert "clamped" in mock_logger.warning.call_args[0][0]


@pytest.mark.asyncio
async def test_batch_concurrency_limit_zero_unlimited(mock_logger):
    """Test batch_concurrency_limit of 0 means unlimited (no semaphore)."""
    # Test with limit = 0 (unlimited)
    client = GraphQLClient(token=TEST_GITHUB_TOKEN, logger=mock_logger, batch_concurrency_limit=0)
    assert client.batch_concurrency_limit == 0
    assert client._batch_semaphore is None
    mock_logger.warning.assert_not_called()


@pytest.mark.asyncio
async def test_batch_concurrency_limit_negative_unlimited(mock_logger):
    """Test batch_concurrency_limit < 0 means unlimited (no semaphore)."""
    # Test with limit < 0 (unlimited)
    client = GraphQLClient(token=TEST_GITHUB_TOKEN, logger=mock_logger, batch_concurrency_limit=-1)
    assert client.batch_concurrency_limit == -1
    assert client._batch_semaphore is None
    mock_logger.warning.assert_not_called()


@pytest.mark.asyncio
async def test_not_found_error_debug_logging(graphql_client, mock_logger):
    """Test that NOT_FOUND errors are logged at DEBUG level without traceback."""
    # Test with NOT_FOUND error type in dict
    not_found_error_dict = TransportQueryError("Test error")
    not_found_error_dict.errors = [{"type": "NOT_FOUND", "message": "Could not resolve to a node"}]

    with (
        patch("webhook_server.libs.graphql.graphql_client.AIOHTTPTransport"),
        patch("webhook_server.libs.graphql.graphql_client.Client") as mock_client_class,
    ):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=not_found_error_dict)

        mock_client = AsyncMock()
        mock_client.connect_async = AsyncMock()
        mock_client.close_async = AsyncMock()
        mock_client.session = mock_session

        mock_client_class.return_value = mock_client

        graphql_client._session = mock_session
        graphql_client._client = mock_client

        # Execute should raise GraphQLError
        with pytest.raises(GraphQLError):
            await graphql_client.execute("query { test }")

        # Verify DEBUG logging was used (not exception logging)
        expected_msg = (
            "GraphQL query error (NOT_FOUND - will be retried by caller): "
            "{'type': 'NOT_FOUND', 'message': 'Could not resolve to a node'}"
        )
        mock_logger.debug.assert_any_call(expected_msg)
        # Verify exception() was NOT called for this error
        mock_logger.exception.assert_not_called()


@pytest.mark.asyncio
async def test_not_found_error_string_format_debug_logging(graphql_client, mock_logger):
    """Test that NOT_FOUND errors in string format are logged at DEBUG level."""
    # Test with NOT_FOUND error as string
    not_found_error_str = TransportQueryError("Could not resolve to a Node with the global id")
    not_found_error_str.errors = []

    with (
        patch("webhook_server.libs.graphql.graphql_client.AIOHTTPTransport"),
        patch("webhook_server.libs.graphql.graphql_client.Client") as mock_client_class,
    ):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=not_found_error_str)

        mock_client = AsyncMock()
        mock_client.connect_async = AsyncMock()
        mock_client.close_async = AsyncMock()
        mock_client.session = mock_session

        mock_client_class.return_value = mock_client

        graphql_client._session = mock_session
        graphql_client._client = mock_client

        with pytest.raises(GraphQLError):
            await graphql_client.execute("query { test }")

        # Verify DEBUG logging was used (contains NOT_FOUND keywords)
        assert any("NOT_FOUND" in str(call) for call in mock_logger.debug.call_args_list)
        # Verify exception() was NOT called for this error
        mock_logger.exception.assert_not_called()


@pytest.mark.asyncio
async def test_non_not_found_error_exception_logging(graphql_client, mock_logger):
    """Test that non-NOT_FOUND errors are logged with exception() for traceback."""
    # Test with a different error type
    other_error = TransportQueryError("Some other GraphQL error")
    other_error.errors = [{"type": "FORBIDDEN", "message": "Access denied"}]

    with (
        patch("webhook_server.libs.graphql.graphql_client.AIOHTTPTransport"),
        patch("webhook_server.libs.graphql.graphql_client.Client") as mock_client_class,
    ):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=other_error)

        mock_client = AsyncMock()
        mock_client.connect_async = AsyncMock()
        mock_client.close_async = AsyncMock()
        mock_client.session = mock_session

        mock_client_class.return_value = mock_client

        graphql_client._session = mock_session
        graphql_client._client = mock_client

        with pytest.raises(GraphQLError):
            await graphql_client.execute("query { test }")

        # Verify exception() was called (NOT debug())
        mock_logger.exception.assert_called_once()
        assert "GraphQL query error:" in mock_logger.exception.call_args[0][0]
