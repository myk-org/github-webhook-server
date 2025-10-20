"""GraphQL client wrapper for GitHub API with authentication and error handling."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport
from gql.transport.exceptions import (
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

    async def __aenter__(self) -> GraphQLClient:
        """Async context manager entry."""
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def _ensure_client(self) -> None:
        """Ensure the GraphQL client is initialized with fresh transport for each query."""
        # ALWAYS recreate transport and client for each query to avoid connection reuse
        # Close existing client first if it exists
        if self._client:
            try:
                await self._client.close_async()
            except Exception:
                pass  # Ignore cleanup errors
            
        # Create fresh transport with new connection for this query
        self._transport = AIOHTTPTransport(
            url=self.GITHUB_GRAPHQL_URL,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github.v4+json",
            },
            timeout=self.timeout,
        )

        self._client = Client(
            transport=self._transport,
            fetch_schema_from_transport=False,  # Don't fetch schema on every request
        )

        self.logger.debug("GraphQL client recreated with fresh transport")

    async def close(self) -> None:
        """Close the GraphQL client and cleanup resources."""
        if self._client:
            try:
                await self._client.close_async()
            except Exception:
                pass  # Ignore cleanup errors
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
        await self._ensure_client()

        if isinstance(query, str):
            query = gql(query)

        result = None
        for attempt in range(self.retry_count):
            try:
                self.logger.debug(
                    f"Executing GraphQL query (attempt {attempt + 1}/{self.retry_count})",
                    extra={"variables": variables},
                )

                # Use session context manager for each query to ensure clean connection state
                async with self._client as session:  # type: ignore[union-attr]
                    result = await session.execute(query, variable_values=variables)

                self.logger.debug("GraphQL query executed successfully")
                return result

            except TransportQueryError as error:
                # Handle GraphQL-specific errors
                error_msg = str(error)

                # Check for authentication errors
                if "401" in error_msg or "Unauthorized" in error_msg or "Bad credentials" in error_msg:
                    self.logger.error(f"GraphQL authentication failed: {error_msg}")
                    raise GraphQLAuthenticationError(f"Authentication failed: {error_msg}") from error

                # Check for rate limit errors
                if "rate limit" in error_msg.lower() or "RATE_LIMITED" in error_msg:
                    self.logger.warning(f"GraphQL rate limit exceeded: {error_msg}")

                    # If not the last attempt, wait before retrying
                    if attempt < self.retry_count - 1:
                        wait_time = 2**attempt  # Exponential backoff: 1s, 2s, 4s
                        self.logger.info(f"Waiting {wait_time}s before retry...")
                        await asyncio.sleep(wait_time)
                        continue

                    raise GraphQLRateLimitError(f"Rate limit exceeded: {error_msg}") from error

                # For other query errors, retry with exponential backoff
                self.logger.warning(f"GraphQL query error (attempt {attempt + 1}): {error_msg}")

                if attempt < self.retry_count - 1:
                    wait_time = 2**attempt
                    await asyncio.sleep(wait_time)
                    continue

                raise GraphQLError(f"GraphQL query failed: {error_msg}") from error

            except TransportServerError as error:
                # Handle server errors (5xx)
                error_msg = str(error)
                self.logger.warning(f"GraphQL server error (attempt {attempt + 1}): {error_msg}")

                if attempt < self.retry_count - 1:
                    wait_time = 2**attempt
                    self.logger.info(f"Server error, waiting {wait_time}s before retry...")
                    await asyncio.sleep(wait_time)
                    continue

                raise GraphQLError(f"GraphQL server error: {error_msg}") from error

            except Exception as error:
                # Handle unexpected errors
                error_msg = str(error)
                self.logger.error(f"Unexpected GraphQL error: {error_msg}")

                if attempt < self.retry_count - 1:
                    wait_time = 2**attempt
                    await asyncio.sleep(wait_time)
                    continue

                raise GraphQLError(f"Unexpected error: {error_msg}") from error

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
