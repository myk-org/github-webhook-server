"""Unified GitHub API interface supporting both GraphQL and REST operations.

This module provides an abstraction layer for GitHub API operations,
automatically selecting between GraphQL and REST based on operation type
and availability.

Strategy:
- GraphQL: Primary for queries and supported mutations
- REST: Fallback for check runs, webhooks, and some settings
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Any

from gql.transport.exceptions import TransportConnectionFailed, TransportQueryError, TransportServerError
from github import Github
from github.PullRequest import PullRequest as RestPullRequest
from github.Repository import Repository as RestRepository

from webhook_server.libs.graphql.graphql_builders import MutationBuilder, QueryBuilder
from webhook_server.libs.graphql.graphql_client import GraphQLClient, GraphQLError


class APIType(Enum):
    """API type for operations."""

    GRAPHQL = "graphql"
    REST = "rest"
    HYBRID = "hybrid"  # Uses both


class UnifiedGitHubAPI:
    """
    Unified interface for GitHub API operations.

    Automatically selects between GraphQL and REST based on:
    - Operation type (read/write)
    - API availability (some operations only in REST)
    - Performance considerations (GraphQL reduces API calls)

    Example:
        >>> api = UnifiedGitHubAPI(token="ghp_...", logger=logger)
        >>> await api.initialize()
        >>> pr = await api.get_pull_request("owner", "repo", 123)
        >>> await api.add_comment(pr['id'], "Hello!")
        >>> await api.close()
    """

    def __init__(self, token: str, logger: logging.Logger, batch_concurrency_limit: int = 10) -> None:
        """
        Initialize unified API client.

        Args:
            token: GitHub personal access token or GitHub App token
            logger: Logger instance
            batch_concurrency_limit: Maximum concurrent batch operations (default: 10, 0 for unlimited)
        """
        self.token = token
        self.logger = logger
        self.batch_concurrency_limit = batch_concurrency_limit

        # GraphQL client (async)
        self.graphql_client: GraphQLClient | None = None

        # REST client (sync) - kept for fallback operations
        self.rest_client: Github | None = None
        self._initialized = False
        self._init_lock = asyncio.Lock()  # Protect against concurrent initialization

    async def initialize(self) -> None:
        """Initialize both GraphQL and REST clients."""
        async with self._init_lock:
            if self._initialized:
                return

            # Initialize GraphQL client with batch concurrency limiting
            self.graphql_client = GraphQLClient(
                token=self.token, logger=self.logger, batch_concurrency_limit=self.batch_concurrency_limit
            )

            # Initialize REST client (PyGithub)
            self.rest_client = Github(self.token)

            self._initialized = True
            self.logger.info(
                f"Unified GitHub API initialized (GraphQL + REST, batch_concurrency_limit={self.batch_concurrency_limit})"
            )

    async def close(self) -> None:
        """Close and cleanup API clients."""
        if self.graphql_client:
            await self.graphql_client.close()

        if self.rest_client:
            # Guard against older PyGithub versions that may not have close()
            if hasattr(self.rest_client, "close"):
                self.rest_client.close()

        self._initialized = False
        self.logger.info("Unified GitHub API closed")

    async def __aenter__(self) -> UnifiedGitHubAPI:
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    # ===== Batch Operations =====

    async def execute_batch(
        self,
        queries: list[tuple[str, dict[str, Any] | None]],
    ) -> list[dict[str, Any]]:
        """
        Execute multiple GraphQL queries in parallel.

        This is a public wrapper around GraphQLClient.execute_batch that maintains
        API encapsulation. Tests should use this method instead of accessing
        the internal graphql_client directly.

        Args:
            queries: List of (query, variables) tuples

        Returns:
            List of query results in the same order as input

        Example:
            >>> api = UnifiedGitHubAPI(token="ghp_...", logger=logger)
            >>> await api.initialize()
            >>> queries = [
            ...     ("query { viewer { login } }", None),
            ...     ("query { rateLimit { remaining } }", None),
            ... ]
            >>> results = await api.execute_batch(queries)
            >>> await api.close()
        """
        if not self.graphql_client:
            await self.initialize()

        return await self.graphql_client.execute_batch(queries)  # type: ignore[union-attr]

    # ===== Query Operations (GraphQL Primary) =====

    async def get_rate_limit(self) -> dict[str, Any]:
        """
        Get current rate limit information.

        Uses: GraphQL
        Reason: More detailed rate limit info in GraphQL

        Returns:
            Rate limit information
        """
        if not self.graphql_client:
            await self.initialize()

        query = QueryBuilder.get_rate_limit()
        result = await self.graphql_client.execute(query)  # type: ignore[union-attr]
        return result["rateLimit"]

    async def get_viewer(self) -> dict[str, Any]:
        """
        Get authenticated user information.

        Uses: GraphQL
        Reason: Single optimized query

        Returns:
            User information
        """
        if not self.graphql_client:
            await self.initialize()

        query = QueryBuilder.get_viewer()
        result = await self.graphql_client.execute(query)  # type: ignore[union-attr]
        return result["viewer"]

    async def get_repository(self, owner: str, name: str) -> dict[str, Any]:
        """
        Get repository information.

        Uses: GraphQL
        Reason: More efficient, single query

        Args:
            owner: Repository owner
            name: Repository name

        Returns:
            Repository information
        """
        if not self.graphql_client:
            await self.initialize()

        query, variables = QueryBuilder.get_repository(owner, name)
        result = await self.graphql_client.execute(query, variables)  # type: ignore[union-attr]
        return result["repository"]

    async def get_pull_request(
        self,
        owner: str,
        name: str,
        number: int,
        include_commits: bool = False,
        include_labels: bool = False,
        include_reviews: bool = False,
    ) -> dict[str, Any]:
        """
        Get pull request with optional related data.

        Uses: GraphQL
        Reason: Can fetch PR + commits + labels + reviews in ONE query
                vs 4-5 REST calls

        Args:
            owner: Repository owner
            name: Repository name
            number: Pull request number
            include_commits: Include commit history
            include_labels: Include labels
            include_reviews: Include reviews

        Returns:
            Pull request data
        """
        if not self.graphql_client:
            await self.initialize()

        query, variables = QueryBuilder.get_pull_request(
            owner,
            name,
            number,
            include_commits=include_commits,
            include_labels=include_labels,
            include_reviews=include_reviews,
        )
        result = await self.graphql_client.execute(query, variables)  # type: ignore[union-attr]
        return result["repository"]["pullRequest"]

    async def get_pull_requests(
        self, owner: str, name: str, states: list[str] | None = None, first: int = 10, after: str | None = None
    ) -> dict[str, Any]:
        """
        Get pull requests with pagination.

        Uses: GraphQL
        Reason: More efficient pagination with cursors

        Args:
            owner: Repository owner
            name: Repository name
            states: PR states (OPEN, CLOSED, MERGED)
            first: Number of results
            after: Pagination cursor

        Returns:
            Pull requests data with pagination info
        """
        if not self.graphql_client:
            await self.initialize()

        query, variables = QueryBuilder.get_pull_requests(owner, name, states=states, first=first, after=after)
        result = await self.graphql_client.execute(query, variables)  # type: ignore[union-attr]
        return result["repository"]["pullRequests"]

    async def get_commit(self, owner: str, name: str, oid: str) -> dict[str, Any]:
        """
        Get commit information.

        Uses: GraphQL
        Reason: More efficient for commit metadata

        Args:
            owner: Repository owner
            name: Repository name
            oid: Commit SHA

        Returns:
            Commit information
        """
        if not self.graphql_client:
            await self.initialize()

        query, variables = QueryBuilder.get_commit(owner, name, oid)
        result = await self.graphql_client.execute(query, variables)  # type: ignore[union-attr]
        return result["repository"]["object"]

    async def get_file_contents(self, owner: str, name: str, path: str, ref: str = "main") -> str:
        """
        Get file contents from repository.

        Uses: GraphQL
        Reason: Efficient for single file retrieval

        Args:
            owner: Repository owner
            name: Repository name
            path: File path
            ref: Git ref (branch/tag)

        Returns:
            File contents as string
        """
        if not self.graphql_client:
            await self.initialize()

        expression = f"{ref}:{path}"
        query, variables = QueryBuilder.get_file_contents(owner, name, expression)
        result = await self.graphql_client.execute(query, variables)  # type: ignore[union-attr]
        blob = result["repository"]["object"]

        # Check if file exists
        if blob is None:
            raise FileNotFoundError(f"File not found: owner={owner}, repo={name}, path={path}, ref={ref}")

        # Handle binary files - text will be null for binary files
        if blob.get("isBinary") or blob.get("text") is None:
            # Fall back to REST API for binary files
            contents = await self.get_contents(owner, name, path, ref)
            # Handle non-UTF-8 content gracefully with error recovery
            # errors="replace" replaces invalid UTF-8 bytes with � (U+FFFD)
            return contents.decoded_content.decode("utf-8", errors="replace")

        return blob["text"]

    # ===== Mutation Operations (GraphQL Primary) =====

    async def add_comment(self, subject_id: str, body: str) -> dict[str, Any]:
        """
        Add comment to PR or issue.

        Uses: GraphQL
        Reason: Efficient mutation

        Args:
            subject_id: PR or issue node ID
            body: Comment text

        Returns:
            Created comment data
        """
        if not self.graphql_client:
            self.logger.debug("Initializing GraphQL client for add_comment")
            await self.initialize()

        self.logger.debug(f"Adding comment to subject_id={subject_id}, body length={len(body)}")
        mutation, variables = MutationBuilder.add_comment(subject_id, body)
        self.logger.debug("Calling graphql_client.execute for addComment mutation")

        try:
            result = await self.graphql_client.execute(mutation, variables)  # type: ignore[union-attr]
        except (GraphQLError, TransportQueryError, TransportConnectionFailed, TransportServerError):
            self.logger.exception(
                f"Failed to add comment to {subject_id}",
            )
            raise
        else:
            self.logger.debug("GraphQL execute returned, extracting comment node")
            try:
                comment_node = result["addComment"]["commentEdge"]["node"]
            except KeyError:
                self.logger.exception(
                    f"Failed to extract comment from GraphQL result for {subject_id}. Result: {result}",
                )
                raise
            else:
                self.logger.info(f"SUCCESS: Comment added to {subject_id}, comment_id={comment_node.get('id')}")
                return comment_node

    async def add_labels(self, labelable_id: str, label_ids: list[str]) -> None:
        """
        Add labels to PR or issue.

        Uses: GraphQL
        Reason: Efficient mutation

        Args:
            labelable_id: PR or issue node ID
            label_ids: List of label node IDs
        """
        if not self.graphql_client:
            await self.initialize()

        mutation, variables = MutationBuilder.add_labels(labelable_id, label_ids)
        await self.graphql_client.execute(mutation, variables)  # type: ignore[union-attr]

    async def remove_labels(self, labelable_id: str, label_ids: list[str]) -> None:
        """
        Remove labels from PR or issue.

        Uses: GraphQL
        Reason: Efficient mutation

        Args:
            labelable_id: PR or issue node ID
            label_ids: List of label node IDs
        """
        if not self.graphql_client:
            await self.initialize()

        mutation, variables = MutationBuilder.remove_labels(labelable_id, label_ids)
        await self.graphql_client.execute(mutation, variables)  # type: ignore[union-attr]

    async def add_assignees(self, assignable_id: str, assignee_ids: list[str]) -> None:
        """
        Add assignees to PR or issue.

        Uses: GraphQL
        Reason: Efficient mutation

        Args:
            assignable_id: PR or issue node ID
            assignee_ids: List of user node IDs
        """
        if not self.graphql_client:
            await self.initialize()

        mutation, variables = MutationBuilder.add_assignees(assignable_id, assignee_ids)
        await self.graphql_client.execute(mutation, variables)  # type: ignore[union-attr]

    async def create_issue(
        self,
        repository_id: str,
        title: str,
        body: str | None = None,
        assignee_ids: list[str] | None = None,
        label_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Create a new issue.

        Uses: GraphQL
        Reason: Can set labels/assignees in one call

        Args:
            repository_id: Repository node ID
            title: Issue title
            body: Issue body
            assignee_ids: List of assignee node IDs
            label_ids: List of label node IDs

        Returns:
            Created issue data
        """
        if not self.graphql_client:
            await self.initialize()

        mutation, variables = MutationBuilder.create_issue(repository_id, title, body, assignee_ids, label_ids)
        result = await self.graphql_client.execute(mutation, variables)  # type: ignore[union-attr]
        return result["createIssue"]["issue"]

    async def create_issue_on_repository(
        self,
        owner: str,
        name: str,
        title: str,
        body: str | None = None,
        assignee_ids: list[str] | None = None,
        label_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Create a new issue on a repository (convenience method).

        Uses: GraphQL
        Reason: More efficient than REST, fetches repository ID automatically

        Args:
            owner: Repository owner
            name: Repository name
            title: Issue title
            body: Issue body
            assignee_ids: Optional list of user node IDs to assign
            label_ids: Optional list of label node IDs to add

        Returns:
            Created issue data

        Example:
            >>> issue = await api.create_issue_on_repository(
            ...     "owner", "repo",
            ...     "Bug: Something broke",
            ...     "Details about the bug...",
            ...     assignee_ids=["MDQ6VXNlcjEyMzQ1"],
            ...     label_ids=["MDU6TGFiZWw5ODc2NTQzMjE="]
            ... )
        """
        # Get repository ID first
        repo_data = await self.get_repository(owner, name)
        repository_id = repo_data["id"]

        # Create the issue with optional assignees and labels
        return await self.create_issue(repository_id, title, body, assignee_ids, label_ids)

    async def request_reviews(self, pull_request_id: str, user_ids: list[str]) -> None:
        """
        Request reviews on a pull request.

        Uses: GraphQL
        Reason: Efficient mutation

        Args:
            pull_request_id: PR node ID
            user_ids: List of user node IDs to request reviews from
        """
        if not self.graphql_client:
            await self.initialize()

        mutation, variables = MutationBuilder.request_reviews(pull_request_id, user_ids)
        await self.graphql_client.execute(mutation, variables)  # type: ignore[union-attr]

    async def update_pull_request(
        self, pull_request_id: str, title: str | None = None, body: str | None = None
    ) -> dict[str, Any]:
        """
        Update pull request title or body.

        Uses: GraphQL
        Reason: Efficient mutation

        Args:
            pull_request_id: PR node ID
            title: New title (optional)
            body: New body (optional)

        Returns:
            Updated PR data
        """
        if not self.graphql_client:
            await self.initialize()

        mutation, variables = MutationBuilder.update_pull_request(pull_request_id, title, body)
        result = await self.graphql_client.execute(mutation, variables)  # type: ignore[union-attr]
        return result["updatePullRequest"]["pullRequest"]

    async def enable_pull_request_automerge(self, pull_request_id: str, merge_method: str = "SQUASH") -> None:
        """
        Enable auto-merge on a pull request.

        Uses: GraphQL
        Reason: Only available via GraphQL

        Args:
            pull_request_id: PR node ID
            merge_method: MERGE, SQUASH, or REBASE
        """
        if not self.graphql_client:
            await self.initialize()

        mutation, variables = MutationBuilder.enable_pull_request_automerge(pull_request_id, merge_method)
        await self.graphql_client.execute(mutation, variables)  # type: ignore[union-attr]

    async def get_user_id(self, login: str) -> str:
        """
        Get user node ID from login.

        Uses: GraphQL with REST fallback
        Reason: Need node ID for mutations

        Args:
            login: User login name

        Returns:
            User node ID
        """
        if not self.graphql_client:
            await self.initialize()

        try:
            query = """
                query($login: String!) {
                    user(login: $login) {
                        id
                    }
                }
            """
            variables = {"login": login}
            result = await self.graphql_client.execute(query, variables)  # type: ignore[union-attr]
            return result["user"]["id"]
        except (GraphQLError, TransportConnectionFailed, TransportQueryError, TransportServerError):
            # Fallback to REST API only for GraphQL/transport errors
            self.logger.debug(f"GraphQL failed for get_user_id, falling back to REST for user: {login}")
            return await self.get_user_id_rest(login)

    async def get_user_id_rest(self, login: str) -> str:
        """
        Get user node ID from login using REST API.

        Uses: REST (wrapped in asyncio.to_thread to avoid blocking)
        Reason: Fallback when GraphQL fails

        Args:
            login: User login name

        Returns:
            User node ID
        """
        if not self.rest_client:
            await self.initialize()

        user = await asyncio.to_thread(self.rest_client.get_user, login)  # type: ignore[union-attr]
        return user.node_id

    async def get_label_id(self, owner: str, name: str, label_name: str) -> str | None:
        """
        Get label node ID from label name.

        Uses: GraphQL
        Reason: Need node ID for mutations

        Args:
            owner: Repository owner
            name: Repository name
            label_name: Label name

        Returns:
            Label node ID or None if not found
        """
        if not self.graphql_client:
            await self.initialize()

        query = """
            query($owner: String!, $name: String!, $labelName: String!) {
                repository(owner: $owner, name: $name) {
                    label(name: $labelName) {
                        id
                    }
                }
            }
        """
        variables = {"owner": owner, "name": name, "labelName": label_name}
        result = await self.graphql_client.execute(query, variables)  # type: ignore[union-attr]
        label = result["repository"].get("label")
        return label["id"] if label else None

    async def create_label(self, repository_id: str, name: str, color: str) -> dict[str, Any]:
        """
        Create a new label in repository.

        Uses: GraphQL
        Reason: Efficient mutation

        Args:
            repository_id: Repository node ID
            name: Label name
            color: Label color (hex without #)

        Returns:
            Created label data
        """
        if not self.graphql_client:
            await self.initialize()

        mutation = """
            mutation($repositoryId: ID!, $name: String!, $color: String!) {
                createLabel(input: {repositoryId: $repositoryId, name: $name, color: $color}) {
                    label {
                        id
                        name
                        color
                    }
                }
            }
        """
        variables = {
            "repositoryId": repository_id,
            "name": name,
            "color": color,
        }
        result = await self.graphql_client.execute(mutation, variables)  # type: ignore[union-attr]
        return result["createLabel"]["label"]

    async def update_label(self, label_id: str, color: str) -> dict[str, Any]:
        """
        Update label color.

        Uses: GraphQL
        Reason: Efficient mutation

        Args:
            label_id: Label node ID
            color: New color (hex without #)

        Returns:
            Updated label data
        """
        if not self.graphql_client:
            await self.initialize()

        mutation = """
            mutation($labelId: ID!, $color: String!) {
                updateLabel(input: {id: $labelId, color: $color}) {
                    label {
                        id
                        name
                        color
                    }
                }
            }
        """
        variables = {
            "labelId": label_id,
            "color": color,
        }
        result = await self.graphql_client.execute(mutation, variables)  # type: ignore[union-attr]
        return result["updateLabel"]["label"]

    # ===== REST-Only Operations (GraphQL Not Supported) =====

    async def get_repository_for_rest_operations(self, owner: str, name: str) -> RestRepository:
        """
        Get REST repository object for operations NOT supported in GraphQL.

        Uses: REST (wrapped in asyncio.to_thread to avoid blocking)
        Use cases: Webhooks, check runs, some settings

        Args:
            owner: Repository owner
            name: Repository name

        Returns:
            PyGithub Repository object

        Note: Only use when operation is NOT available in GraphQL.
              For most operations, use the GraphQL methods instead.
        """
        # Lazy-initialize REST client for parity with GraphQL
        if not self.rest_client:
            await self.initialize()

        return await asyncio.to_thread(self.rest_client.get_repo, f"{owner}/{name}")  # type: ignore[union-attr]

    async def get_pr_for_check_runs(self, owner: str, name: str, number: int) -> RestPullRequest:
        """
        Get PR object specifically for check runs access.

        Uses: REST (wrapped in asyncio.to_thread to avoid blocking)
        Reason: Check Runs API is NOT available in GitHub GraphQL v4

        TODO: Cannot migrate to GraphQL - Check Runs API is not available in GraphQL.
              GitHub has not announced plans to add check runs to GraphQL v4.
              This function will likely remain REST-only indefinitely.

        Args:
            owner: Repository owner
            name: Repository name
            number: Pull request number

        Returns:
            PyGithub PullRequest object (for check runs only)

        Note: For PR data (title, labels, commits, etc.), use get_pull_request() instead!
              This method exists ONLY because check runs aren't in GraphQL.

        Example:
            >>> # CORRECT: Use GraphQL for PR data
            >>> pr_data = await api.get_pull_request("owner", "repo", 123)
            >>>
            >>> # CORRECT: Use REST ONLY for check runs
            >>> rest_pr = await api.get_pr_for_check_runs("owner", "repo", 123)
            >>> commits = await asyncio.to_thread(rest_pr.get_commits)
            >>> check_runs = await asyncio.to_thread(commits[0].get_check_runs)
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        return await asyncio.to_thread(repo.get_pull, number)

    async def get_pull_request_files(self, owner: str, name: str, number: int) -> list[Any]:
        """
        Get list of files changed in a pull request.

        Uses: REST (not yet in GraphQL)

        TODO: Migrate to GraphQL when available - PR files are not yet accessible in GraphQL v4.
              Monitor GitHub GraphQL schema updates for PullRequest.files field.
              Expected GraphQL query: pullRequest(number: X) { files(first: 100) { nodes { path, additions, deletions } } }

        Args:
            owner: Repository owner
            name: Repository name
            number: Pull request number

        Returns:
            List of file objects
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        pr = await asyncio.to_thread(repo.get_pull, number)
        return await asyncio.to_thread(lambda: list(pr.get_files()))

    async def get_open_pull_requests(self, owner: str, name: str) -> list[RestPullRequest]:
        """
        Get all open pull requests.

        Uses: REST (wrapped in asyncio.to_thread to avoid blocking)
        Reason: Simpler for iteration over all PRs; GraphQL pagination is more complex for this use case

        TODO: Consider migrating to GraphQL with pagination - GraphQL supports this via:
              repository(owner: X, name: Y) { pullRequests(states: OPEN, first: 100) { nodes { ... } pageInfo { ... } } }
              Trade-off: GraphQL requires cursor pagination (more complex) vs REST simple iteration.
              Migration only worthwhile if we need additional PR data beyond what REST provides.

        Args:
            owner: Repository owner
            name: Repository name

        Returns:
            List of PyGithub PullRequest objects

        Example:
            >>> prs = await api.get_open_pull_requests("owner", "repo")
            >>> for pr in prs:
            ...     print(pr.number, pr.title)
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        return await asyncio.to_thread(lambda: list(repo.get_pulls(state="open")))

    async def get_issue_comments(self, owner: str, name: str, number: int) -> list[Any]:
        """
        Get all comments on a pull request or issue.

        Uses: REST (not yet in GraphQL)

        TODO: Migrate to GraphQL when available - Issue/PR comments listing is not yet in GraphQL v4.
              GraphQL has individual comment queries but not efficient bulk listing.
              Monitor for: pullRequest(number: X) { comments(first: 100) { nodes { ... } } }

        Args:
            owner: Repository owner
            name: Repository name
            number: PR or issue number

        Returns:
            List of comment objects
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        pr = await asyncio.to_thread(repo.get_pull, number)
        return await asyncio.to_thread(lambda: list(pr.get_issue_comments()))

    async def add_assignees_by_login(self, owner: str, name: str, number: int, assignees: list[str]) -> None:
        """
        Add assignees to a pull request by login name.

        Uses: REST (helper method)

        TODO: Migrate to GraphQL addAssignees mutation - Already available in GraphQL:
              mutation { addAssigneesToAssignable(input: {assignableId: PR_ID, assigneeIds: [USER_IDS]}) { ... } }
              Requires: 1) get_pull_request to fetch PR node ID, 2) get_user_id for each assignee login
              Trade-off: 2-3 GraphQL calls vs 1 REST call. Only migrate if already fetching PR data.

        Args:
            owner: Repository owner
            name: Repository name
            number: PR number
            assignees: List of user logins
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        pr = await asyncio.to_thread(repo.get_pull, number)
        await asyncio.to_thread(pr.add_to_assignees, *assignees)

    async def get_issue_comment(self, owner: str, name: str, number: int, comment_id: int) -> Any:
        """
        Get a specific issue/PR comment.

        Uses: REST
        Scope: Currently fetches comment via PR endpoint (works for both PR comments and issue comments
               on PRs). For pure issue comments (non-PR), this method works as PyGithub's get_pull()
               returns an Issue object when the number refers to an issue.

        Args:
            owner: Repository owner
            name: Repository name
            number: PR or issue number
            comment_id: Comment ID to fetch

        Returns:
            Comment object from PyGithub

        TODO: Migrate to GraphQL when available - Individual comment queries not yet efficient in GraphQL v4.
              Monitor for: issueComment(id: COMMENT_NODE_ID) { ... } or similar query.
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        pr = await asyncio.to_thread(repo.get_pull, number)
        return await asyncio.to_thread(pr.get_issue_comment, comment_id)

    async def create_reaction(self, comment: Any, reaction: str) -> None:
        """
        Create a reaction on a comment.

        Uses: REST

        TODO: Cannot migrate to GraphQL - Reactions API is not available in GraphQL v4.
              GitHub has not announced plans to add reaction mutations to GraphQL.
              This function will likely remain REST-only indefinitely.
        """
        await asyncio.to_thread(comment.create_reaction, reaction)

    async def get_contributors(self, owner: str, name: str) -> list[Any]:
        """
        Get repository contributors.

        Uses: REST

        TODO: Consider migrating to GraphQL - Available via:
              repository(owner: X, name: Y) { mentionableUsers(first: 100) { ... } }
              However, REST is simpler for this use case and already efficient.
              Migration only worthwhile if we need contributor data in same query as other repo data.
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        return await asyncio.to_thread(lambda: list(repo.get_contributors()))

    async def get_collaborators(self, owner: str, name: str) -> list[Any]:
        """
        Get repository collaborators.

        Uses: REST

        TODO: Consider migrating to GraphQL - Available via:
              repository(owner: X, name: Y) { collaborators(first: 100) { edges { permission node { ... } } } }
              However, REST is simpler for this use case and already efficient.
              Migration only worthwhile if we need collaborator data in same query as other repo data.
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        return await asyncio.to_thread(lambda: list(repo.get_collaborators()))

    async def get_branch(self, owner: str, name: str, branch: str) -> Any:
        """
        Get branch information.

        Uses: REST

        TODO: Consider migrating to GraphQL - Available via:
              repository(owner: X, name: Y) { ref(qualifiedName: "refs/heads/BRANCH") { target { ... } } }
              However, REST is simpler for this use case and already efficient.
              Migration only worthwhile if we need branch data in same query as other repo data.
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        return await asyncio.to_thread(repo.get_branch, branch)

    async def get_branch_protection(self, owner: str, name: str, branch: str) -> Any:
        """
        Get branch protection rules.

        Uses: REST

        TODO: Partially available in GraphQL - Branch protection is only partially in GraphQL v4:
              repository(owner: X, name: Y) { branchProtectionRules(first: 100) { nodes { ... } } }
              However, many branch protection settings are only available via REST API.
              Monitor GitHub GraphQL schema for complete branch protection coverage.
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        branch_obj = await asyncio.to_thread(repo.get_branch, branch)
        return await asyncio.to_thread(branch_obj.get_protection)

    async def get_issues(self, owner: str, name: str) -> list[Any]:
        """
        Get repository issues.

        Uses: REST

        TODO: Consider migrating to GraphQL - Available via:
              repository(owner: X, name: Y) { issues(first: 100, states: [OPEN]) { nodes { ... } } }
              However, REST is simpler for iteration and already efficient.
              Migration only worthwhile if we need issue data in same query as other repo data.
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        return await asyncio.to_thread(lambda: list(repo.get_issues()))

    async def edit_issue(self, issue: Any, state: str) -> None:
        """
        Edit issue state.

        Uses: REST

        TODO: Migrate to GraphQL closeIssue/reopenIssue mutations - Already available:
              mutation { closeIssue(input: {issueId: ISSUE_NODE_ID}) { issue { state } } }
              mutation { reopenIssue(input: {issueId: ISSUE_NODE_ID}) { issue { state } } }
              Requires issue node ID (not REST object). Migrate when we have issue ID from GraphQL queries.
        """
        await asyncio.to_thread(issue.edit, state=state)

    async def edit_pull_request_rest(self, pull_request: RestPullRequest, **kwargs: Any) -> None:
        """
        Edit pull request via REST API (non-blocking).

        Uses: REST

        Note: This method wraps the sync edit() call with asyncio.to_thread to make it non-blocking.
              For GraphQL PR objects (PullRequestWrapper), use update_pull_request() instead.

        Args:
            pull_request: REST PullRequest object
            **kwargs: Arguments to pass to pull_request.edit() (e.g., title="New Title")
        """
        await asyncio.to_thread(pull_request.edit, **kwargs)

    async def get_contents(self, owner: str, name: str, path: str, ref: str) -> Any:
        """
        Get file contents from repository.

        Uses: REST

        TODO: Already have GraphQL alternative - get_file_contents() uses GraphQL for text files.
              This REST version is kept as fallback for binary files and as backward compatibility.
              Consider phasing out this method in favor of get_file_contents() where possible.
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        return await asyncio.to_thread(repo.get_contents, path, ref)

    async def get_git_tree(self, owner: str, name: str, ref: str, recursive: bool = True) -> Any:
        """
        Get git tree.

        Uses: REST

        TODO: Consider migrating to GraphQL - Available via:
              repository(owner: X, name: Y) { object(expression: "REF:") { ... tree { entries { ... } } } }
              However, REST is more straightforward for recursive tree operations.
              Migration only worthwhile if we need tree data in same query as other repo data.
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        return await asyncio.to_thread(repo.get_git_tree, ref, recursive=recursive)

    async def get_commit_check_runs(self, commit: Any, owner: str | None = None, name: str | None = None) -> list[Any]:
        """
        Get check runs for a commit.

        Works with both REST API Commit objects and CommitWrapper.
        If commit is CommitWrapper, fetches check runs via REST API using commit SHA.

        Uses: REST

        TODO: Cannot migrate to GraphQL - Check Runs API is not available in GraphQL v4.
              GitHub has not announced plans to add check runs queries to GraphQL.
              This function will likely remain REST-only indefinitely.

        Args:
            commit: REST Commit object or CommitWrapper
            owner: Repository owner (required if commit is CommitWrapper)
            name: Repository name (required if commit is CommitWrapper)
        """
        # Check if this is a REST commit object (has get_check_runs method)
        if hasattr(commit, "get_check_runs") and callable(commit.get_check_runs):
            return await asyncio.to_thread(lambda: list(commit.get_check_runs()))

        # CommitWrapper from GraphQL - fetch check runs via REST API
        if hasattr(commit, "sha") and owner and name:
            repo = await self.get_repository_for_rest_operations(owner, name)
            rest_commit = await asyncio.to_thread(repo.get_commit, commit.sha)
            return await asyncio.to_thread(lambda: list(rest_commit.get_check_runs()))

        # Fallback - return empty list with warning
        self.logger.warning(
            f"Unable to get check runs for commit (type={type(commit).__name__}, "
            f"owner={owner}, name={name}). Returning empty list."
        )
        return []

    async def create_check_run(self, repo_by_app: Any, **kwargs: Any) -> None:
        """
        Create a check run using GitHub App repository.

        Uses: REST

        TODO: Cannot migrate to GraphQL - Check Runs API is not available in GraphQL v4.
              GitHub has not announced plans to add check run mutations to GraphQL.
              This function will likely remain REST-only indefinitely.
        """
        await asyncio.to_thread(repo_by_app.create_check_run, **kwargs)

    async def merge_pull_request(self, owner: str, name: str, number: int, merge_method: str = "SQUASH") -> None:
        """
        Merge a pull request.

        Uses: REST

        TODO: Consider GraphQL enablePullRequestAutomerge mutation - Different from direct merge:
              GraphQL: mutation { enablePullRequestAutomerge(input: {pullRequestId: PR_ID, mergeMethod: SQUASH}) { ... } }
              This enables auto-merge (PR merges when checks pass), not immediate merge like REST.
              For immediate merge, REST is currently the only option.
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        pr = await asyncio.to_thread(repo.get_pull, number)
        await asyncio.to_thread(pr.merge, merge_method=merge_method)

    async def get_pulls_from_commit(self, commit: Any) -> list[Any]:
        """
        Get pull requests associated with a commit.

        Uses: REST

        TODO: Consider migrating to GraphQL - Available via:
              repository(owner: X, name: Y) { object(oid: "COMMIT_SHA") { ... associatedPullRequests { nodes { ... } } } }
              However, requires commit SHA and repo info. Only migrate if we already have this data from GraphQL.
        """
        return await asyncio.to_thread(lambda: list(commit.get_pulls()))

    async def get_pulls_from_commit_sha(self, owner: str, name: str, sha: str) -> list[Any]:
        """
        Get pull requests associated with a commit SHA.

        Uses: REST
        Reason: Efficient commit->PR lookup

        TODO: Consider migrating to GraphQL - Available via:
              repository(owner: X, name: Y) { object(oid: "COMMIT_SHA") { ... associatedPullRequests(first: 10) { nodes { ... } } } }
              However, REST is already efficient for this specific use case.
              Migration only worthwhile if we need PR data in same query as other repo data.

        Args:
            owner: Repository owner
            name: Repository name
            sha: Commit SHA

        Returns:
            List of pull requests associated with the commit
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        commit = await asyncio.to_thread(repo.get_commit, sha)
        return await asyncio.to_thread(lambda: list(commit.get_pulls()))

    async def get_pr_commits_rest(self, pull_request: Any) -> list[Any]:
        """
        Get commits from a pull request using REST API.

        Uses: REST
        Reason: Fallback when GraphQL fails to retrieve commits

        Args:
            pull_request: PyGithub PullRequest object

        Returns:
            List of commits in the pull request
        """
        return await asyncio.to_thread(lambda: list(pull_request.get_commits()))

    async def send_slack_message_async(self, send_slack_message_func: Any, message: str, webhook_url: str) -> None:
        """
        Send Slack message asynchronously.

        Uses: REST (Slack API via requests library)

        This method wraps the synchronous send_slack_message function from GithubWebhook
        to run in a separate thread, preventing blocking of the async event loop.

        Args:
            send_slack_message_func: The synchronous send_slack_message method to call
            message: Message text to send
            webhook_url: Slack webhook URL

        Note:
            This is the ONLY location where asyncio.to_thread should be used for Slack messages.
            All other code should call this method instead of using asyncio.to_thread directly.
        """
        await asyncio.to_thread(send_slack_message_func, message=message, webhook_url=webhook_url)

    # ===== Helper Methods =====

    def get_api_type_for_operation(self, operation: str) -> APIType:
        """
        Determine which API to use for an operation.

        Args:
            operation: Operation name

        Returns:
            API type to use
        """
        # Operations that MUST use REST
        rest_only = {
            "check_runs",
            "create_check_run",
            "update_check_run",
            "webhooks",
            "create_webhook",
            "repository_settings",
            "branch_protection",  # Partial - some in GraphQL
            "get_issues",  # REST-backed, see TODO in method for GraphQL migration consideration
        }

        # Operations better in GraphQL (fewer API calls)
        # Note: Only includes operations that have actual method implementations
        graphql_preferred = {
            "get_pull_request",
            "get_pull_requests",
            "get_commit",
            # Note: get_commits, get_labels removed - not currently implemented as unified_api methods
            "add_comment",
            "add_labels",
            "remove_labels",
            "get_file_contents",
            "create_issue",
            "get_rate_limit",
            "get_user_id",  # Aligned with actual method name
        }

        if operation in rest_only:
            return APIType.REST
        elif operation in graphql_preferred:
            return APIType.GRAPHQL
        else:
            return APIType.HYBRID


# API Selection Documentation
