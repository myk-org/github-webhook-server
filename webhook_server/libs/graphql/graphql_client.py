"""GraphQL client wrapper for GitHub API with authentication and error handling."""

from __future__ import annotations

import asyncio
import logging
import random
import re
import traceback
from datetime import UTC, datetime
from typing import Any

import aiohttp
from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport
from gql.transport.exceptions import (
    TransportError,
    TransportQueryError,
    TransportServerError,
)
from graphql import DocumentNode

from webhook_server.libs.graphql.graphql_builders import QueryBuilder


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
        connection_timeout: int = 10,
        sock_read_timeout: int = 30,
    ) -> None:
        """
        Initialize GraphQL client.

        Args:
            token: GitHub personal access token or GitHub App token
            logger: Logger instance for operation logging
            retry_count: Number of retry attempts for failed requests (default: 3)
            timeout: Total request timeout in seconds (default: 90, increased for large mutations)
            batch_concurrency_limit: Maximum concurrent batch operations.
                - Default: 10 (recommended to protect rate limits and connection pools)
                - Range: 1-100 (clamped at runtime)
                - 0: Unlimited concurrency (use with caution - may overload server/rate limits)
            connection_timeout: DNS resolution + TCP handshake timeout in seconds (default: 10)
            sock_read_timeout: Socket read timeout in seconds (default: 30)

        Note:
            Setting batch_concurrency_limit to 0 enables unlimited concurrency which may
            overload rate limits or connection pools. Use only when necessary.

            Granular timeouts allow better error handling:
            - connection_timeout: Fast fail on DNS/connection issues (default: 10s)
            - sock_read_timeout: Timeout for reading response data (default: 30s)
            - timeout: Overall operation timeout (default: 90s)
        """
        self.token = token
        self.logger = logger
        self.retry_count = retry_count
        self.timeout = timeout
        self.connection_timeout = connection_timeout
        self.sock_read_timeout = sock_read_timeout
        # Clamp batch_concurrency_limit to sane bounds (0 = unlimited, max 100)
        # Negative values are clamped to 0 (unlimited)
        if batch_concurrency_limit > 0:
            self.batch_concurrency_limit = min(batch_concurrency_limit, 100)
            if batch_concurrency_limit != self.batch_concurrency_limit:
                logger.warning(
                    f"batch_concurrency_limit clamped from {batch_concurrency_limit} to {self.batch_concurrency_limit}"
                )
        else:
            # Clamp negative to 0 for explicit "unlimited" intent
            self.batch_concurrency_limit = max(0, batch_concurrency_limit)
        self._client: Client | None = None
        self._session: Any = None  # Store connected session explicitly (not internal detail)
        self._transport: AIOHTTPTransport | None = None
        self._client_lock = asyncio.Lock()
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
            if self._client is not None:
                return

            connector = aiohttp.TCPConnector(
                limit=100,  # Max total connections
                limit_per_host=10,  # Max connections per host
                ttl_dns_cache=300,  # DNS cache TTL in seconds
                keepalive_timeout=30,  # Keep connections alive for reuse
            )

            # Create granular timeout configuration for better error handling
            # - total: Overall operation timeout (default: 90s)
            # - connect: DNS resolution + TCP handshake timeout (default: 10s)
            # - sock_read: Socket read timeout (default: 30s)
            timeout_config = aiohttp.ClientTimeout(
                total=self.timeout,
                connect=self.connection_timeout,
                sock_read=self.sock_read_timeout,
            )

            self._transport = AIOHTTPTransport(
                url=self.GITHUB_GRAPHQL_URL,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github.v4+json",
                    "User-Agent": "github-webhook-server/graphql-client",
                },
                ssl=True,
                client_session_args={
                    "connector": connector,
                    "connector_owner": True,  # Session owns connector to ensure proper cleanup
                    "timeout": timeout_config,
                },
            )

            self._client = Client(
                transport=self._transport,
                fetch_schema_from_transport=False,  # Don't fetch schema on every request
            )

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
            self._session = None
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

                self.logger.debug(
                    f"Executing GraphQL query (total={self.timeout}s, "
                    f"connect={self.connection_timeout}s, sock_read={self.sock_read_timeout}s)"
                )

                # The session was connected in _ensure_client and stays connected for connection pooling
                result = await self._session.execute(query, variable_values=variables)

                self.logger.debug("GraphQL query executed successfully")
                return dict(result) if result else {}

            except TransportQueryError as error:
                error_msg = error.errors[0] if error.errors else str(error)

                if "401" in str(error_msg) or "Unauthorized" in str(error_msg) or "Bad credentials" in str(error_msg):
                    self.logger.exception(
                        f"AUTH FAILED: GraphQL authentication failed: {error_msg}",
                    )
                    raise GraphQLAuthenticationError(f"Authentication failed: {error_msg}") from error

                error_str = str(error_msg)
                if "rate limit" in error_str.lower() or "RATE_LIMITED" in error_str:
                    # Use GraphQL rateLimit query instead of REST /rate_limit for consistency
                    try:
                        # Execute directly with session to bypass retry logic and avoid infinite loop
                        if self._session:
                            rate_limit_query = gql(QueryBuilder.get_rate_limit())
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
                                continue
                    except Exception:
                        self.logger.exception(
                            "Failed to get rate limit info",
                        )

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
                    self.logger.exception(
                        f"GraphQL query error: {error_msg}",
                    )

                raise GraphQLError(f"GraphQL query failed: {error_msg}") from error

            except TransportServerError as error:
                # Handle server errors (5xx) and client errors like 403 with exponential backoff
                error_msg = str(error)
                # Try to get status code from error attribute or parse from error message
                status_code = getattr(error, "status", None) or getattr(error, "status_code", None)
                if status_code is None:
                    # Parse status code from error message (format: "403, message='Forbidden', url='...'")
                    match = re.search(r"(\d{3}),", error_msg)
                    if match:
                        status_code = int(match.group(1))

                # Special handling for 403 Forbidden - might be rate limit or transient GitHub API issue
                if status_code == 403:
                    # Check if this is actually a rate limit issue (GitHub sometimes returns 403 for rate limits)
                    graphql_rate_limit_info = None
                    try:
                        if self._session:
                            rate_limit_query = gql(QueryBuilder.get_rate_limit())
                            rate_result = await self._session.execute(rate_limit_query)
                            remaining = rate_result["rateLimit"]["remaining"]
                            reset_at = rate_result["rateLimit"]["resetAt"]
                            graphql_rate_limit_info = f"GraphQL rate limit: {remaining} remaining, resets at {reset_at}"

                            # If rate limit is exhausted, treat as rate limit error
                            if remaining == 0:
                                reset_timestamp = datetime.fromisoformat(reset_at.replace("Z", "+00:00")).timestamp()
                                current_time = datetime.now(UTC).timestamp()
                                wait_seconds = int(reset_timestamp - current_time) + 5  # Add 5s buffer

                                if wait_seconds > 0:
                                    self.logger.warning(
                                        f"RATE LIMIT (403): GraphQL rate limit exhausted (403 Forbidden). "
                                        f"Waiting {wait_seconds}s until reset at "
                                        f"{datetime.fromtimestamp(reset_timestamp, tz=UTC)}",
                                    )
                                    await asyncio.sleep(wait_seconds)
                                    continue
                    except Exception as rate_limit_error:
                        # Rate limit check failed - could be transient GitHub API issue
                        # Don't assume it's a permission issue if rate limit check also fails with 403
                        # This can happen during GitHub API outages or transient issues
                        if attempt == 0:
                            # Only log on first attempt to avoid spam
                            self.logger.debug(
                                f"Rate limit check failed for 403 error (attempt {attempt + 1}): {rate_limit_error}. "
                                "Treating original 403 as potentially transient."
                            )

                    # 403 might be transient (GitHub API issues) - retry with exponential backoff
                    rate_limit_context = f" ({graphql_rate_limit_info})" if graphql_rate_limit_info else ""
                    if attempt < self.retry_count - 1:
                        wait_seconds = (2**attempt) + random.uniform(0, 1)
                        self.logger.warning(
                            f"FORBIDDEN (403): GraphQL request forbidden "
                            f"(attempt {attempt + 1}/{self.retry_count}): {error_msg}{rate_limit_context}. "
                            f"This might be a transient GitHub API issue. Retrying in {wait_seconds:.1f}s...",
                        )
                        await asyncio.sleep(wait_seconds)
                        continue
                    else:
                        # After all retries failed, log error but don't assume it's permissions
                        # (token might be valid, could be persistent GitHub API issue)
                        self.logger.exception(
                            f"FORBIDDEN (403): GraphQL request forbidden after "
                            f"{self.retry_count} attempts: {error_msg}{rate_limit_context}. "
                            f"This might indicate a transient GitHub API problem or token permission issue.",
                        )
                        raise GraphQLError(
                            f"GraphQL request forbidden (403) after {self.retry_count} attempts: "
                            f"{error_msg}{rate_limit_context}. "
                            f"This might indicate a transient GitHub API problem or token permission issue."
                        ) from error

                # Handle other server errors (5xx) with exponential backoff
                if attempt < self.retry_count - 1:
                    # Add jitter to reduce retry stampedes under 5xx bursts
                    wait_seconds = (2**attempt) + random.uniform(0, 1)
                    self.logger.warning(
                        f"SERVER ERROR: GraphQL server error (attempt {attempt + 1}/{self.retry_count}): {error_msg}. "
                        f"Retrying in {wait_seconds:.1f}s...",
                    )
                    await asyncio.sleep(wait_seconds)
                    continue
                else:
                    self.logger.exception(
                        f"SERVER ERROR: GraphQL server error after {self.retry_count} attempts: {error_msg}",
                    )
                    raise GraphQLError(f"GraphQL server error: {error_msg}") from error

            except TransportError as error:
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
                    self._session = None
                    self._transport = None
                    await asyncio.sleep(1)
                    continue
                else:
                    # Final attempt failed ? close client before raising
                    if self._client:
                        try:
                            await self._client.close_async()
                        except Exception:
                            self.logger.debug("Ignoring error during client close after final connection failure")
                    self._client = None
                    self._session = None
                    self._transport = None
                    self.logger.exception(
                        f"CONNECTION CLOSED: GraphQL connection closed after {self.retry_count} attempts: {error_msg}",
                    )
                    raise GraphQLError(f"GraphQL connection closed: {error_msg}") from error

            except TimeoutError as error:
                # Explicit timeout handling - NEVER silent!
                # TimeoutError catches both builtin and asyncio.TimeoutError (aliases in Python 3.8+)

                # Determine which timeout was actually hit by inspecting the exception chain and traceback
                timeout_type = "total"
                timeout_value = self.timeout

                # Get full traceback as string for analysis
                tb_lines = traceback.format_exception(type(error), error, error.__traceback__)
                tb_str = "".join(tb_lines)

                # Check traceback for clues about which timeout occurred
                # DNS resolution/connection = connect timeout
                is_connect_timeout = (
                    "_resolve_host" in tb_str or "_create_connection" in tb_str or "_create_direct_connection" in tb_str
                )

                if is_connect_timeout:
                    timeout_type = "connect"
                    timeout_value = self.connection_timeout
                # Socket read operation = sock_read timeout
                elif "sock_read" in tb_str or ("read" in tb_str.lower() and "socket" in tb_str.lower()):
                    timeout_type = "sock_read"
                    timeout_value = self.sock_read_timeout
                # Check exception cause (for CancelledError with "deadline exceeded")
                elif error.__cause__:
                    cause_str = "".join(
                        traceback.format_exception(
                            type(error.__cause__), error.__cause__, error.__cause__.__traceback__
                        )
                    )
                    if (
                        "_resolve_host" in cause_str
                        or "_create_connection" in cause_str
                        or "_create_direct_connection" in cause_str
                    ):
                        timeout_type = "connect"
                        timeout_value = self.connection_timeout
                        is_connect_timeout = True
                    elif "sock_read" in cause_str:
                        timeout_type = "sock_read"
                        timeout_value = self.sock_read_timeout

                # Retry connect timeouts (DNS/connection issues) - these are often transient
                if is_connect_timeout and attempt < self.retry_count - 1:
                    # Add jitter to reduce retry stampedes for DNS issues
                    wait_seconds = (2**attempt) + random.uniform(0, 1)
                    self.logger.warning(
                        f"DNS/CONNECTION TIMEOUT: GraphQL connection timeout "
                        f"(attempt {attempt + 1}/{self.retry_count}): "
                        f"{timeout_type} timeout after {timeout_value}s. "
                        f"Retrying in {wait_seconds:.1f}s...",
                    )
                    # Force close the client to clear any stale connections
                    if self._client:
                        try:
                            await self._client.close_async()
                        except Exception:
                            self.logger.debug("Ignoring error during client close after connection timeout")
                        self._client = None
                        self._session = None
                        self._transport = None
                    await asyncio.sleep(wait_seconds)
                    continue

                # Non-retryable timeout or final attempt failed
                self.logger.exception(
                    f"TIMEOUT: GraphQL query {timeout_type} timeout after {timeout_value}s "
                    f"(total={self.timeout}s, connect={self.connection_timeout}s, sock_read={self.sock_read_timeout}s)",
                )
                # Force close the client to stop any pending connections
                if self._client:
                    try:
                        await self._client.close_async()
                        self._client = None
                        self._session = None
                        self._transport = None
                    except Exception:
                        self.logger.exception(
                            "Error during timeout cleanup",
                        )
                raise GraphQLError(
                    f"GraphQL query {timeout_type} timeout after {timeout_value}s "
                    f"(configured: total={self.timeout}s, "
                    f"connect={self.connection_timeout}s, sock_read={self.sock_read_timeout}s)"
                ) from error

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
