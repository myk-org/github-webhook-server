"""GraphQL client wrapper for GitHub API with authentication and error handling."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp
from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport
from gql.transport.exceptions import (
    TransportConnectionFailed,
    TransportQueryError,
    TransportServerError,
)
from graphql import DocumentNode

from webhook_server.utils.constants import ERROR_IDS


class GraphQLError(Exception):
    """Base exception for GraphQL client errors."""

    pass


class GraphQLAuthenticationError(GraphQLError):
    """Raised when authentication fails."""

    pass


class GraphQLRateLimitError(GraphQLError):
    """Raised when rate limit is exceeded."""

    pass


class GraphQLClient:
    """
    Async GraphQL client wrapper for GitHub API.

    Provides:
    - Token-based authentication
    - Automatic retry logic with exponential backoff
    - Error handling for common GitHub API errors
    - Logging for all operations
    - Rate limit tracking

    Example:
        >>> client = GraphQLClient(token="ghp_...", logger=logger)
        >>> query = '''
        ...     query {
        ...         viewer {
        ...             login
        ...         }
        ...     }
        ... '''
        >>> result = await client.execute(query)
        >>> print(result['viewer']['login'])
    """

    GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

    def __init__(
        self,
        token: str,
        logger: logging.Logger,
        retry_count: int = 3,
        timeout: int = 90,
    ) -> None:
        """
        Initialize GraphQL client.

        Args:
            token: GitHub personal access token or GitHub App token
            logger: Logger instance for operation logging
            retry_count: Number of retry attempts for failed requests (default: 3)
            timeout: Request timeout in seconds (default: 90, increased for large mutations)
        """
        self.token = token
        self.logger = logger
        self.retry_count = retry_count
        self.timeout = timeout
        self._client: Client | None = None
        self._transport: AIOHTTPTransport | None = None
        self._client_lock = asyncio.Lock()  # Protect against concurrent client recreation

    async def __aenter__(self) -> GraphQLClient:
        """Async context manager entry."""
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def _ensure_client(self) -> None:
        """Ensure the GraphQL client is initialized and connected. Reuses existing client for connection pooling."""
        async with self._client_lock:
            # Only create and connect client once for connection pooling
            if self._client is not None:
                return

            # Create persistent transport with connection pooling via TCPConnector
            # Configure keepalive and connection limits for optimal performance
            connector = aiohttp.TCPConnector(
                limit=100,  # Max total connections
                limit_per_host=10,  # Max connections per host
                ttl_dns_cache=300,  # DNS cache TTL in seconds
                keepalive_timeout=30,  # Keep connections alive for reuse
            )

            self._transport = AIOHTTPTransport(
                url=self.GITHUB_GRAPHQL_URL,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github.v4+json",
                    "User-Agent": "github-webhook-server/graphql-client",
                },
                timeout=self.timeout,
                client_session_args={
                    "connector": connector,
                    "connector_owner": True,  # Session owns connector to ensure proper cleanup
                },
            )

            self._client = Client(
                transport=self._transport,
                fetch_schema_from_transport=False,  # Don't fetch schema on every request
            )

            # Connect the client session once for persistent connection pooling
            await self._client.connect_async()

            self.logger.debug("GraphQL client initialized with persistent connection pooling")

    async def close(self) -> None:
        """Close the GraphQL client and cleanup resources."""
        if self._client:
            try:
                await self._client.close_async()
            except Exception as ex:
                self.logger.debug(f"Ignoring error during client close: {ex}")
            self._client = None
            self._transport = None
            self.logger.debug("GraphQL client closed")

    async def execute(
        self,
        query: str | DocumentNode,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Execute a GraphQL query or mutation.

        Args:
            query: GraphQL query string or DocumentNode
            variables: Variables for the query (optional)

        Returns:
            Query result as a dictionary

        Raises:
            GraphQLAuthenticationError: If authentication fails
            GraphQLRateLimitError: If rate limit is exceeded
            GraphQLError: For other GraphQL errors
        """
        if isinstance(query, str):
            query = gql(query)

        result = None
        for attempt in range(self.retry_count):
            try:
                # Ensure client is available for this attempt (may need recreation after error)
                await self._ensure_client()

                self.logger.debug(f"Executing GraphQL query with {self.timeout}s timeout")

                # Use the already-connected session directly to avoid "Transport is already connected" error
                # The session was connected in _ensure_client and stays connected for connection pooling
                result = await self._client.session.execute(query, variable_values=variables)  # type: ignore[union-attr]

                self.logger.debug("GraphQL query executed successfully")
                return dict(result) if result else {}

            except TransportQueryError as error:
                # Handle GraphQL-specific errors
                error_msg = str(error)

                # Check for authentication errors
                if "401" in error_msg or "Unauthorized" in error_msg or "Bad credentials" in error_msg:
                    self.logger.error(
                        f"AUTH FAILED: GraphQL authentication failed: {error_msg}",
                        exc_info=True,
                        extra={"error_id": ERROR_IDS.GRAPHQL_AUTH_FAILED},
                    )
                    raise GraphQLAuthenticationError(f"Authentication failed: {error_msg}") from error

                # Check for rate limit errors - wait until rate limit resets
                if "rate limit" in error_msg.lower() or "RATE_LIMITED" in error_msg:
                    # Query GitHub API for current rate limit status
                    try:
                        async with aiohttp.ClientSession() as http_session:
                            async with http_session.get(
                                "https://api.github.com/rate_limit", headers={"Authorization": f"Bearer {self.token}"}
                            ) as resp:
                                rate_data = await resp.json()
                                reset_timestamp = rate_data["resources"]["graphql"]["reset"]
                                current_time = datetime.now(timezone.utc).timestamp()
                                wait_seconds = int(reset_timestamp - current_time) + 5  # Add 5s buffer

                                if wait_seconds > 0:
                                    self.logger.warning(
                                        f"RATE LIMIT: GraphQL rate limit exceeded. "
                                        f"Waiting {wait_seconds}s until reset at {datetime.fromtimestamp(reset_timestamp, tz=timezone.utc)}",
                                        extra={"error_id": ERROR_IDS.GRAPHQL_RATE_LIMIT},
                                    )
                                    await asyncio.sleep(wait_seconds)
                                    continue  # Retry after waiting
                    except Exception as ex:
                        self.logger.error(
                            f"Failed to get rate limit info: {ex}",
                            exc_info=True,
                            extra={"error_id": ERROR_IDS.GRAPHQL_RATE_LIMIT_INFO_FAILED},
                        )

                    # If we can't get rate limit info, fail
                    self.logger.error(
                        f"RATE LIMIT: GraphQL rate limit exceeded: {error_msg}",
                        exc_info=True,
                        extra={"error_id": ERROR_IDS.GRAPHQL_RATE_LIMIT},
                    )
                    raise GraphQLRateLimitError(f"Rate limit exceeded: {error_msg}") from error

                # For other query errors, fail immediately
                self.logger.error(
                    f"GraphQL query error: {error_msg}",
                    exc_info=True,
                    extra={"error_id": ERROR_IDS.GRAPHQL_QUERY_ERROR},
                )
                raise GraphQLError(f"GraphQL query failed: {error_msg}") from error

            except TransportConnectionFailed as error:
                # Handle connection closed errors - recreate client and retry
                error_msg = str(error)
                if attempt < self.retry_count - 1:
                    self.logger.warning(
                        f"CONNECTION CLOSED: GraphQL connection closed (attempt {attempt + 1}/{self.retry_count}): {error_msg}. "
                        f"Recreating client and retrying...",
                        exc_info=True,
                        extra={"error_id": ERROR_IDS.GRAPHQL_CONNECTION_CLOSED},
                    )
                    # Force recreate client on next iteration
                    self._client = None
                    self._transport = None
                    await asyncio.sleep(1)  # Brief wait before retry
                    continue  # Retry with fresh client
                else:
                    # Final attempt failed
                    self.logger.error(
                        f"CONNECTION CLOSED: GraphQL connection closed after {self.retry_count} attempts: {error_msg}",
                        exc_info=True,
                        extra={"error_id": ERROR_IDS.GRAPHQL_CONNECTION_CLOSED},
                    )
                    raise GraphQLError(f"GraphQL connection closed: {error_msg}") from error

            except TransportServerError as error:
                # Handle server errors (5xx) with exponential backoff
                error_msg = str(error)
                if attempt < self.retry_count - 1:
                    wait_seconds = 2**attempt
                    self.logger.warning(
                        f"SERVER ERROR: GraphQL server error (attempt {attempt + 1}/{self.retry_count}): {error_msg}. "
                        f"Retrying in {wait_seconds}s...",
                        exc_info=True,
                        extra={"error_id": ERROR_IDS.GRAPHQL_SERVER_ERROR},
                    )
                    await asyncio.sleep(wait_seconds)
                    continue  # Retry with exponential backoff
                else:
                    # Final attempt failed
                    self.logger.error(
                        f"SERVER ERROR: GraphQL server error after {self.retry_count} attempts: {error_msg}",
                        exc_info=True,
                        extra={"error_id": ERROR_IDS.GRAPHQL_SERVER_ERROR},
                    )
                    raise GraphQLError(f"GraphQL server error: {error_msg}") from error

            except asyncio.TimeoutError as error:
                # Explicit timeout handling - NEVER silent!
                self.logger.error(
                    f"TIMEOUT: GraphQL query timeout after {self.timeout}s",
                    exc_info=True,
                    extra={"error_id": ERROR_IDS.GRAPHQL_TIMEOUT},
                )
                # Force close the client to stop any pending connections
                if self._client:
                    try:
                        await self._client.close_async()
                        self._client = None
                        self._transport = None
                    except Exception:
                        self.logger.exception(
                            "Error during timeout cleanup",
                            extra={"error_id": ERROR_IDS.GRAPHQL_TIMEOUT_CLEANUP_FAILED},
                        )
                raise GraphQLError(f"GraphQL query timeout after {self.timeout}s") from error

            except asyncio.CancelledError:
                # Propagate cancellations without wrapping them
                self.logger.debug("GraphQL query cancelled")
                raise

            except Exception as error:
                # Handle unexpected errors - NEVER SILENT!
                error_msg = str(error)
                error_type = type(error).__name__

                # Log ALL exceptions with full context and re-raise immediately
                self.logger.error(
                    f"FATAL: GraphQL error [{error_type}]: {error_msg}",
                    exc_info=True,
                    extra={"error_id": ERROR_IDS.GRAPHQL_FATAL_ERROR},
                )
                raise GraphQLError(f"Unexpected error [{error_type}]: {error_msg}") from error

        # Should never reach here, but just in case
        raise GraphQLError("Failed to execute query after all retries")

    async def execute_batch(
        self,
        queries: list[tuple[str | DocumentNode, dict[str, Any] | None]],
    ) -> list[dict[str, Any]]:
        """
        Execute multiple GraphQL queries in parallel.

        Args:
            queries: List of (query, variables) tuples

        Returns:
            List of query results in the same order as input

        Example:
            >>> queries = [
            ...     ("query { viewer { login } }", None),
            ...     ("query { rateLimit { remaining } }", None),
            ... ]
            >>> results = await client.execute_batch(queries)
        """
        tasks = [self.execute(query, variables) for query, variables in queries]
        return await asyncio.gather(*tasks)

    async def get_rate_limit(self) -> dict[str, Any]:
        """
        Get current rate limit information.

        Returns:
            Dictionary with rate limit info: limit, remaining, resetAt
        """
        query = """
            query {
                rateLimit {
                    limit
                    remaining
                    resetAt
                    cost
                }
            }
        """

        result = await self.execute(query)
        return result["rateLimit"]

    async def get_viewer_info(self) -> dict[str, Any]:
        """
        Get information about the authenticated user.

        Returns:
            Dictionary with viewer info: login, name, id, etc.
        """
        query = """
            query {
                viewer {
                    login
                    name
                    id
                    avatarUrl
                    email
                }
            }
        """

        result = await self.execute(query)
        return result["viewer"]
