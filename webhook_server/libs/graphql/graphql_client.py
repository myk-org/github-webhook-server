"""GraphQL client wrapper for GitHub API with authentication and error handling."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
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
        batch_concurrency_limit: int = 10,
    ) -> None:
        """
        Initialize GraphQL client.

        Args:
            token: GitHub personal access token or GitHub App token
            logger: Logger instance for operation logging
            retry_count: Number of retry attempts for failed requests (default: 3)
            timeout: Request timeout in seconds (default: 90, increased for large mutations)
            batch_concurrency_limit: Maximum concurrent batch operations.
                - Default: 10 (recommended to protect rate limits and connection pools)
                - Range: 1-100 (clamped at runtime)
                - 0: Unlimited concurrency (use with caution - may overload server/rate limits)

        Note:
            Setting batch_concurrency_limit to 0 enables unlimited concurrency which may
            overload rate limits or connection pools. Use only when necessary.
        """
        self.token = token
        self.logger = logger
        self.retry_count = retry_count
        self.timeout = timeout
        # Clamp batch_concurrency_limit to sane bounds (0 = unlimited, max 100)
        if batch_concurrency_limit > 0:
            self.batch_concurrency_limit = min(batch_concurrency_limit, 100)
            if batch_concurrency_limit != self.batch_concurrency_limit:
                logger.warning(
                    f"batch_concurrency_limit clamped from {batch_concurrency_limit} to {self.batch_concurrency_limit}"
                )
        else:
            self.batch_concurrency_limit = batch_concurrency_limit  # 0 = unlimited
        self._client: Client | None = None
        self._session: Any = None  # Store connected session explicitly (not internal detail)
        self._transport: AIOHTTPTransport | None = None
        self._client_lock = asyncio.Lock()  # Protect against concurrent client recreation
        # Semaphore for batch concurrency limiting (None means unlimited)
        self._batch_semaphore: asyncio.Semaphore | None = (
            asyncio.Semaphore(self.batch_concurrency_limit) if self.batch_concurrency_limit > 0 else None
        )

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
            # Store session reference explicitly (avoid accessing internal .session attribute)
            self._session = await self._client.connect_async()

            self.logger.debug("GraphQL client initialized with persistent connection pooling")

    async def close(self) -> None:
        """Close the GraphQL client and cleanup resources."""
        if self._client:
            try:
                await self._client.close_async()
            except Exception as ex:
                self.logger.debug(f"Ignoring error during client close: {ex}")
            self._client = None
            self._session = None  # Clear session reference
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

                # Use stored session reference (avoid accessing internal .session attribute)
                # The session was connected in _ensure_client and stays connected for connection pooling
                result = await self._session.execute(query, variable_values=variables)

                self.logger.debug("GraphQL query executed successfully")
                return dict(result) if result else {}

            except TransportQueryError as error:
                # Handle GraphQL-specific errors
                error_msg = error.errors[0] if error.errors else str(error)

                # Check for authentication errors
                if "401" in str(error_msg) or "Unauthorized" in str(error_msg) or "Bad credentials" in str(error_msg):
                    self.logger.exception(
                        f"AUTH FAILED: GraphQL authentication failed: {error_msg}",
                    )
                    raise GraphQLAuthenticationError(f"Authentication failed: {error_msg}") from error

                # Check for rate limit errors - wait until rate limit resets
                error_str = str(error_msg)
                if "rate limit" in error_str.lower() or "RATE_LIMITED" in error_str:
                    # Use GraphQL rateLimit query instead of REST /rate_limit for consistency
                    try:
                        # Use lightweight GraphQL query to get rate limit info
                        # Execute directly with session to bypass retry logic and avoid infinite loop
                        if self._session:
                            rate_limit_query = gql(
                                """
                                query {
                                    rateLimit {
                                        resetAt
                                    }
                                }
                                """
                            )
                            rate_result = await self._session.execute(rate_limit_query)
                            reset_at = rate_result["rateLimit"]["resetAt"]
                            reset_timestamp = datetime.fromisoformat(reset_at.replace("Z", "+00:00")).timestamp()
                            current_time = datetime.now(UTC).timestamp()
                            wait_seconds = int(reset_timestamp - current_time) + 5  # Add 5s buffer

                            if wait_seconds > 0:
                                self.logger.warning(
                                    f"RATE LIMIT: GraphQL rate limit exceeded. "
                                    f"Waiting {wait_seconds}s until reset at "
                                    f"{datetime.fromtimestamp(reset_timestamp, tz=UTC)}",
                                )
                                await asyncio.sleep(wait_seconds)
                                continue  # Retry after waiting
                    except Exception:
                        self.logger.exception(
                            "Failed to get rate limit info",
                        )

                    # If we can't get rate limit info, fail
                    self.logger.exception(
                        f"RATE LIMIT: GraphQL rate limit exceeded: {error_msg}",
                    )
                    raise GraphQLRateLimitError(f"Rate limit exceeded: {error_msg}") from error

                # Check if this is a NOT_FOUND error that will be handled by retry logic
                is_not_found = (
                    (isinstance(error_msg, dict) and error_msg.get("type") == "NOT_FOUND")
                    or "not_found" in error_str.lower()
                    or "could not resolve to a node" in error_str.lower()
                )

                if is_not_found:
                    # NOT_FOUND errors are handled by unified_api retry logic, just debug log
                    self.logger.debug(
                        f"GraphQL query error (NOT_FOUND - will be retried by caller): {error_msg}",
                    )
                else:
                    # For other query errors, log exception with traceback
                    self.logger.exception(
                        f"GraphQL query error: {error_msg}",
                    )

                raise GraphQLError(f"GraphQL query failed: {error_msg}") from error

            except TransportConnectionFailed as error:
                # Handle connection closed errors - recreate client and retry
                error_msg = str(error)
                if attempt < self.retry_count - 1:
                    self.logger.warning(
                        f"CONNECTION CLOSED: GraphQL connection closed "
                        f"(attempt {attempt + 1}/{self.retry_count}): {error_msg}. "
                        f"Recreating client and retrying...",
                    )
                    # Close and force recreate client on next iteration
                    if self._client:
                        try:
                            await self._client.close_async()
                        except Exception:
                            self.logger.debug("Ignoring error during client close after connection failure")
                    self._client = None
                    self._session = None  # Clear session reference
                    self._transport = None
                    await asyncio.sleep(1)  # Brief wait before retry
                    continue  # Retry with fresh client
                else:
                    # Final attempt failed — close client before raising
                    if self._client:
                        try:
                            await self._client.close_async()
                        except Exception:
                            self.logger.debug("Ignoring error during client close after final connection failure")
                    # Clear cached handles to avoid reusing half-closed client
                    self._client = None
                    self._session = None
                    self._transport = None
                    self.logger.exception(
                        f"CONNECTION CLOSED: GraphQL connection closed after {self.retry_count} attempts: {error_msg}",
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
                    )
                    await asyncio.sleep(wait_seconds)
                    continue  # Retry with exponential backoff
                else:
                    # Final attempt failed
                    self.logger.exception(
                        f"SERVER ERROR: GraphQL server error after {self.retry_count} attempts: {error_msg}",
                    )
                    raise GraphQLError(f"GraphQL server error: {error_msg}") from error

            except TimeoutError as error:
                # Explicit timeout handling - NEVER silent!
                self.logger.exception(
                    f"TIMEOUT: GraphQL query timeout after {self.timeout}s",
                )
                # Force close the client to stop any pending connections
                if self._client:
                    try:
                        await self._client.close_async()
                        self._client = None
                        self._session = None  # Clear session reference
                        self._transport = None
                    except Exception:
                        self.logger.exception(
                            "Error during timeout cleanup",
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
                self.logger.exception(
                    f"FATAL: GraphQL error [{error_type}]: {error_msg}",
                )
                raise GraphQLError(f"Unexpected error [{error_type}]: {error_msg}") from error

        # Should never reach here, but just in case
        raise GraphQLError("Failed to execute query after all retries")

    async def execute_batch(
        self,
        queries: list[tuple[str | DocumentNode, dict[str, Any] | None]],
    ) -> list[dict[str, Any]]:
        """
        Execute multiple GraphQL queries in parallel with optional concurrency limiting.

        Concurrency is controlled by batch_concurrency_limit set during initialization.
        - If batch_concurrency_limit > 0: Uses semaphore to limit concurrent operations
        - If batch_concurrency_limit = 0: Unlimited concurrency (all queries run in parallel)

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

        async def _execute_with_semaphore(
            query: str | DocumentNode, variables: dict[str, Any] | None
        ) -> dict[str, Any]:
            """Execute a single query with semaphore protection if configured."""
            if self._batch_semaphore:
                async with self._batch_semaphore:
                    return await self.execute(query, variables)
            return await self.execute(query, variables)

        tasks = [_execute_with_semaphore(query, variables) for query, variables in queries]
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
