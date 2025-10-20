"""Tests for GraphQL client wrapper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from webhook_server.libs.graphql.graphql_client import (
    GraphQLClient,
)


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
    return GraphQLClient(token="test_token", logger=mock_logger)


@pytest.mark.asyncio
async def test_graphql_client_initialization(graphql_client, mock_logger):
    """Test GraphQL client initialization."""
    assert graphql_client.token == "test_token"
    assert graphql_client.logger == mock_logger
    assert graphql_client.retry_count == 3
    assert graphql_client.timeout == 30
    assert graphql_client._client is None


@pytest.mark.asyncio
async def test_context_manager(graphql_client, mock_logger):
    """Test async context manager."""
    with (
        patch("webhook_server.libs.graphql.graphql_client.AIOHTTPTransport"),
        patch("webhook_server.libs.graphql.graphql_client.Client"),
    ):
        async with graphql_client as client:
            assert client is graphql_client
            assert graphql_client._client is not None


@pytest.mark.asyncio
async def test_execute_success(graphql_client, mock_logger):
    """Test successful query execution."""
    mock_result = {"viewer": {"login": "testuser"}}

    with (
        patch("webhook_server.libs.graphql.graphql_client.AIOHTTPTransport"),
        patch("webhook_server.libs.graphql.graphql_client.Client") as mock_client_class,
    ):
        # Create a mock session
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Create a mock client that returns the session
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_session)
        mock_client.__aexit__ = AsyncMock()

        mock_client_class.return_value = mock_client

        result = await graphql_client.execute("query { viewer { login } }")

        assert result == mock_result
        mock_logger.debug.assert_called()


@pytest.mark.asyncio
async def test_execute_batch(graphql_client, mock_logger):
    """Test batch query execution."""
    mock_result_1 = {"viewer": {"login": "testuser"}}
    mock_result_2 = {"rateLimit": {"remaining": 5000}}

    with (
        patch("webhook_server.libs.graphql.graphql_client.AIOHTTPTransport"),
        patch("webhook_server.libs.graphql.graphql_client.Client") as mock_client_class,
    ):
        # Create a mock session
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[mock_result_1, mock_result_2])

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_session)
        mock_client.__aexit__ = AsyncMock()

        mock_client_class.return_value = mock_client

        queries = [
            ("query { viewer { login } }", None),
            ("query { rateLimit { remaining } }", None),
        ]

        results = await graphql_client.execute_batch(queries)

        assert len(results) == 2
        assert results[0] == mock_result_1
        assert results[1] == mock_result_2


@pytest.mark.asyncio
async def test_get_rate_limit(graphql_client, mock_logger):
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
        mock_client.__aenter__ = AsyncMock(return_value=mock_session)
        mock_client.__aexit__ = AsyncMock()

        mock_client_class.return_value = mock_client

        result = await graphql_client.get_rate_limit()

        assert result == mock_result["rateLimit"]


@pytest.mark.asyncio
async def test_get_viewer_info(graphql_client, mock_logger):
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
        mock_client.__aenter__ = AsyncMock(return_value=mock_session)
        mock_client.__aexit__ = AsyncMock()

        mock_client_class.return_value = mock_client

        result = await graphql_client.get_viewer_info()

        assert result == mock_result["viewer"]


@pytest.mark.asyncio
async def test_close(graphql_client, mock_logger):
    """Test client cleanup."""
    with (
        patch("webhook_server.libs.graphql.graphql_client.AIOHTTPTransport"),
        patch("webhook_server.libs.graphql.graphql_client.Client") as mock_client_class,
    ):
        mock_client = AsyncMock()
        mock_client.close_async = AsyncMock()
        mock_client_class.return_value = mock_client

        await graphql_client._ensure_client()
        assert graphql_client._client is not None

        await graphql_client.close()
        assert graphql_client._client is None
        mock_client.close_async.assert_called_once()
