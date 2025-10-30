"""Unified GitHub API interface supporting both GraphQL and REST operations.

This module provides an abstraction layer for GitHub API operations.

Strategy:
- GraphQL: For queries and mutations (comments, labels, reviews, PRs, etc.)
- REST: For operations not supported by GraphQL (check runs, some repository settings)

Note: Operations use either GraphQL OR REST, not both. No automatic fallback between them.
"""

from __future__ import annotations

import asyncio
import logging
import re
from enum import Enum
from typing import Any

from github import Auth, Github, GithubException
from github.Commit import Commit
from github.PullRequest import PullRequest as RestPullRequest
from github.Repository import Repository as RestRepository
from gql.transport.exceptions import TransportConnectionFailed, TransportQueryError, TransportServerError

from webhook_server.libs.config import Config
from webhook_server.libs.graphql.graphql_builders import MutationBuilder, QueryBuilder
from webhook_server.libs.graphql.graphql_client import (
    GraphQLAuthenticationError,
    GraphQLClient,
    GraphQLError,
    GraphQLRateLimitError,
)
from webhook_server.libs.graphql.graphql_wrappers import CommitWrapper, PullRequestWrapper
from webhook_server.utils.helpers import extract_key_from_dict


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

    def __init__(self, token: str, logger: logging.Logger, config: Config, batch_concurrency_limit: int = 10) -> None:
        """
        Initialize unified API client.

        Args:
            token: GitHub personal access token or GitHub App token
            logger: Logger instance
            config: Configuration object for reading settings
            batch_concurrency_limit: Maximum concurrent batch operations (default: 10, 0 for unlimited)
        """
        self.token = token
        self.logger = logger
        self.config = config
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
            self.rest_client = Github(auth=Auth.Token(self.token))

            self._initialized = True
            self.logger.info(
                f"Unified GitHub API initialized (GraphQL + REST, "
                f"batch_concurrency_limit={self.batch_concurrency_limit})"
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

    async def _ensure_initialized(self) -> None:
        """Ensure API clients are initialized before use.

        Helper method to reduce duplication of initialization checks.
        """
        if not self.graphql_client or not self._initialized:
            await self.initialize()

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

    async def get_comprehensive_repository_data(self, owner: str, name: str) -> dict[str, Any]:
        """
        Fetch comprehensive repository data in ONE GraphQL query.

        This demonstrates the true power of GraphQL - fetching all related data
        in a single request instead of multiple separate calls.

        **Performance Impact:**
        - Reduces 10+ API calls to 1 GraphQL query
        - No caching - each webhook gets fresh data

        **Configurable Query Limits:**
        Uses limits from config.yaml (defaults shown):
        - graphql.query-limits.collaborators (default: 100)
        - graphql.query-limits.contributors (default: 100)
        - graphql.query-limits.issues (default: 100)
        - graphql.query-limits.pull-requests (default: 100)

        Args:
            owner: Repository owner
            name: Repository name

        Returns:
            Comprehensive repository data including:
            - Repository metadata (id, name, owner)
            - Collaborators (with permissions)
            - Contributors (mentionableUsers)
            - Issues (open, with labels)
            - Pull requests (open)

        Example:
            >>> api = UnifiedGitHubAPI(token="ghp_...", logger=logger)
            >>> await api.initialize()
            >>> data = await api.get_comprehensive_repository_data("owner", "repo")
            >>> collaborators = data["collaborators"]["edges"]
            >>> contributors = data["mentionableUsers"]["nodes"]
            >>> issues = data["issues"]["nodes"]
        """
        if not self.graphql_client:
            await self.initialize()

        # Read configurable query limits from config
        config = Config(repository=f"{owner}/{name}")
        query_limits = {
            "collaborators": config.get_value("graphql.query-limits.collaborators", return_on_none=100),
            "contributors": config.get_value("graphql.query-limits.contributors", return_on_none=100),
            "issues": config.get_value("graphql.query-limits.issues", return_on_none=100),
            "pull_requests": config.get_value("graphql.query-limits.pull-requests", return_on_none=100),
        }

        # Build comprehensive GraphQL query with configurable limits
        query = f"""
            query($owner: String!, $name: String!) {{
                repository(owner: $owner, name: $name) {{
                    id
                    name
                    nameWithOwner
                    owner {{
                        id
                        login
                    }}
                    collaborators(first: {query_limits["collaborators"]}) {{
                        edges {{
                            permission
                            node {{
                                id
                                login
                                name
                                email
                                avatarUrl
                            }}
                        }}
                    }}
                    mentionableUsers(first: {query_limits["contributors"]}) {{
                        nodes {{
                            id
                            login
                            name
                            email
                            avatarUrl
                        }}
                    }}
                    issues(first: {query_limits["issues"]}, states: OPEN) {{
                        nodes {{
                            id
                            number
                            title
                            body
                            state
                            createdAt
                            updatedAt
                            author {{
                                login
                            }}
                            labels(first: 10) {{
                                nodes {{
                                    id
                                    name
                                    color
                                }}
                            }}
                        }}
                    }}
                    pullRequests(first: {query_limits["pull_requests"]}, states: OPEN) {{
                        nodes {{
                            id
                            number
                            title
                            state
                            baseRefName
                            headRefName
                            author {{
                                login
                            }}
                            createdAt
                            updatedAt
                        }}
                    }}
                }}
            }}
        """
        variables = {"owner": owner, "name": name}

        self.logger.info(f"Fetching comprehensive repository data for {owner}/{name} (1 GraphQL query)")
        result = await self.graphql_client.execute(query, variables)  # type: ignore[union-attr]
        repo_data = result["repository"]

        self.logger.info(
            f"Fetched comprehensive data for {owner}/{name}: "
            f"{len(repo_data['collaborators']['edges'])} collaborators, "
            f"{len(repo_data['mentionableUsers']['nodes'])} contributors, "
            f"{len(repo_data['issues']['nodes'])} open issues, "
            f"{len(repo_data['pullRequests']['nodes'])} open PRs"
        )

        return repo_data

    async def get_pull_request_data(
        self,
        owner: str,
        name: str,
        number: int,
        include_commits: bool = False,
        include_labels: bool = False,
        include_reviews: bool = False,
    ) -> dict[str, Any]:
        """
        Get pull request data (raw GraphQL dict) with optional related data.

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
            Pull request data (dict, not wrapped)
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

    async def get_pull_request(
        self,
        owner: str,
        repo: str,
        hook_data: dict[str, Any],
        github_event: str,
        logger: logging.Logger,
        x_github_delivery: str = "",
        number: int | None = None,
    ) -> PullRequestWrapper | None:
        """
        Get pull request using GraphQL or REST API with smart lookup.

        Handles multiple lookup scenarios:
        - By PR number (direct GraphQL query)
        - By commit SHA (for commit-based webhooks)
        - By check_run head_sha (for check run events)
        - Skip issue-only events (no pull_request field)

        Args:
            owner: Repository owner
            repo: Repository name
            hook_data: GitHub webhook payload
            github_event: Event type (pull_request, issue_comment, check_run, etc.)
            logger: Logger instance
            x_github_delivery: GitHub delivery ID for logging
            number: Optional PR number override

        Returns:
            PullRequestWrapper with both GraphQL data and REST object, or None
        """
        log_prefix = f"[{github_event}][{x_github_delivery}]"

        # Skip PR lookup for issue-only events (comments on issues, not PRs)
        # For issue_comment events on PRs, GitHub includes issue.pull_request field
        if "issue" in hook_data and not hook_data["issue"].get("pull_request"):
            logger.debug(
                f"{log_prefix} Event is for an issue (#{hook_data['issue'].get('number')}), "
                "not a pull request. Skipping PR lookup."
            )
            return None

        # CRITICAL OPTIMIZATION: Handle check_run events FIRST before generic PR lookup
        # This prevents extract_key_from_dict from finding PR numbers in pull_requests array
        if github_event == "check_run":
            # CRITICAL OPTIMIZATION: Use pull_requests array from webhook payload
            # This reduces API calls from 10-100+ (iterating all open PRs) to 0-1
            check_run = hook_data.get("check_run", {})
            pr_refs = check_run.get("pull_requests", [])

            if pr_refs:
                # GitHub webhook includes pull_requests array with associated PRs
                # Use first PR (check_run is typically associated with one PR)
                pr_number = pr_refs[0].get("number")
                if pr_number:
                    logger.debug(
                        f"{log_prefix} Using pull_requests array from check_run webhook "
                        f"(PR #{pr_number} for check run {check_run.get('name')})"
                    )
                    # Fetch PR data via GraphQL for consistency
                    try:
                        pr_data = await self.get_pull_request_data(
                            owner, repo, pr_number, include_commits=True, include_labels=True
                        )
                        return PullRequestWrapper(pr_data, owner, repo)
                    except (GraphQLError, GithubException) as ex:
                        logger.warning(
                            f"{log_prefix} Failed to fetch PR #{pr_number} from pull_requests array: {ex}, "
                            "falling back to head_sha iteration"
                        )

            # Fallback: If no PR refs in webhook or GraphQL failed, use head_sha iteration
            # This should be rare - indicates webhook payload missing pull_requests or API error
            head_sha = check_run.get("head_sha")
            if head_sha:
                logger.warning(
                    f"{log_prefix} check_run webhook missing pull_requests array or PR fetch failed, "
                    "falling back to expensive iteration through all open PRs"
                )
                for _pull_request in await self.get_open_pull_requests_with_details(owner, repo):
                    if _pull_request.head.sha == head_sha:
                        logger.debug(
                            f"{log_prefix} Found pull request {_pull_request.title} [{_pull_request.number}] "
                            f"for check run {check_run.get('name')} via fallback iteration"
                        )
                        # Already a PullRequestWrapper from GraphQL
                        return _pull_request

        # Try to get PR number from various sources (for non-check_run events)
        pr_number = number
        if not pr_number:
            for _number in extract_key_from_dict(key="number", _dict=hook_data):
                pr_number = _number
                break

        # If we have a PR number, use GraphQL
        if pr_number:
            # Fetch PR with commits and labels (commonly needed data)
            pr_data = await self.get_pull_request_data(
                owner, repo, pr_number, include_commits=True, include_labels=True
            )
            # Pass webhook payload to PullRequestWrapper for accurate user.login (includes [bot] suffix)
            # This fixes auto-verification for bot accounts like pre-commit-ci[bot]
            return PullRequestWrapper(pr_data, owner, repo, webhook_data=hook_data.get("pull_request"))

        # For commit-based lookups, use GraphQL associatedPullRequests
        commit: dict[str, Any] = hook_data.get("commit", {})
        if commit:
            commit_sha = commit.get("sha")
            if not commit_sha:
                logger.warning(f"{log_prefix} Commit object present but missing 'sha' field. Commit data: {commit}")
                return None
            try:
                # Get PRs associated with this commit SHA via GraphQL
                _pulls = await self.get_pulls_from_commit_sha(owner, repo, commit_sha)
                if _pulls:
                    # _pulls is now a list of GraphQL PR dicts from associatedPullRequests
                    # Wrap first PR in PullRequestWrapper (GraphQL dict format)
                    pr_data = _pulls[0]
                    return PullRequestWrapper(pr_data, owner, repo)
                logger.warning(f"{log_prefix} No PRs found for commit {commit_sha}")
            except (GraphQLError, GithubException, IndexError) as ex:
                logger.warning(f"{log_prefix} Failed to get PR from commit {commit_sha}: {ex}")
            # Don't suppress authentication or connection errors

        return None

    async def get_last_commit(
        self,
        owner: str,
        repo: str,
        pull_request: PullRequestWrapper | int,
        pr_number: int | None = None,
    ) -> Commit | CommitWrapper:
        """Get last commit from pull request.

        Uses: GraphQL
        Reason: Efficient single query for commit data

        Supports two calling patterns:
        1. get_last_commit(owner, repo, pull_request, pr_number) - full signature
        2. get_last_commit(owner, repo, pr_number) - test compatibility signature

        Raises:
            ValueError: If no commits found in PR
            GraphQLError: If GraphQL query fails
        """
        # Handle both calling patterns
        if isinstance(pull_request, int):
            # Pattern 2: get_last_commit(owner, repo, pr_number)
            actual_pr_number = pull_request
            actual_pull_request = None
        else:
            # Pattern 1: get_last_commit(owner, repo, pull_request, pr_number)
            actual_pull_request = pull_request
            actual_pr_number = pr_number if pr_number is not None else pull_request.number

        # Check if we have commits already loaded in wrapper (optimization)
        if actual_pull_request is not None and actual_pull_request.get_commits():
            commits = actual_pull_request.get_commits()
            if commits:
                return commits[-1]

        # Fetch PR with commits via GraphQL
        pr_data = await self.get_pull_request_data(owner, repo, actual_pr_number, include_commits=True)

        # Extract commits from GraphQL response
        commits_nodes = pr_data.get("commits", {}).get("nodes", [])
        if not commits_nodes:
            raise ValueError(f"No commits found in PR {actual_pr_number}")  # noqa: TRY003

        # Return last commit (wrapped)
        last_commit_data = commits_nodes[-1].get("commit", {})
        return CommitWrapper(last_commit_data)

    async def add_pr_comment(
        self,
        owner: str | PullRequestWrapper,
        repo: str | None = None,
        pull_request: PullRequestWrapper | str | None = None,
        body: str | None = None,
    ) -> None:
        """Add comment to PR via GraphQL.

        Uses: GraphQL
        Reason: addComment mutation is fully supported

        Supports two calling patterns:
        1. add_pr_comment(owner, repo, pull_request, body) - full signature
        2. add_pr_comment(pull_request, body=...) - test compatibility signature with keyword body
        """
        # Handle both calling patterns
        # First check if owner is PullRequestWrapper (pattern 2)
        if isinstance(owner, PullRequestWrapper) or (hasattr(owner, "id") and hasattr(owner, "number")):
            # Pattern 2: add_pr_comment(pull_request, body=...)
            actual_pull_request: PullRequestWrapper = owner  # type: ignore[assignment]
            # Check if body was passed as keyword argument
            if body is not None:
                actual_body: str = body
            else:
                # Body passed as positional argument in repo position
                actual_body = repo  # type: ignore[assignment]
        else:
            # Pattern 1: add_pr_comment(owner, repo, pull_request, body)
            actual_pull_request = pull_request  # type: ignore[assignment]
            actual_body = body  # type: ignore[assignment]

        # Use GraphQL mutation with PR node ID
        pr_id = actual_pull_request.id
        if actual_body:
            self.logger.debug(f"Adding PR comment with pr_id={pr_id}, body length={len(actual_body)}")
            await self.add_comment(pr_id, actual_body)
            self.logger.info("Successfully added PR comment")

    async def update_pr_title(self, pull_request: PullRequestWrapper, title: str) -> None:
        """Update PR title via unified_api."""
        # Use GraphQL mutation
        pr_id = pull_request.id
        await self.update_pull_request(pr_id, title=title)

    async def enable_pr_automerge(self, pull_request: PullRequestWrapper, merge_method: str = "SQUASH") -> None:
        """Enable automerge on PR via unified_api.

        Args:
            pull_request: PR object (PullRequestWrapper)
            merge_method: Merge method (SQUASH, MERGE, REBASE)
        """
        try:
            # Use GraphQL mutation
            pr_id = pull_request.id
            await self.enable_pull_request_automerge(pr_id, merge_method)
            self.logger.info(f"Enabled automerge via GraphQL for PR #{pull_request.number}")
        except (GraphQLAuthenticationError, GraphQLRateLimitError):
            # Re-raise auth/rate-limit errors - these are critical
            raise
        except (GraphQLError, GithubException):
            # Log and re-raise - automerge failures are important
            self.logger.exception("Failed to enable automerge")
            raise

    @staticmethod
    def _is_graphql_node_id(value: str) -> bool:
        """
        Check if a string is a GitHub GraphQL node ID.

        GitHub GraphQL node IDs are typically base64-encoded strings that start with
        common prefixes like:
        - U_ (User IDs)
        - PR_ (Pull Request IDs)
        - MDQ6, MDExOl, MDE, etc. (legacy base64-encoded IDs)

        Args:
            value: String to check

        Returns:
            True if the string matches GraphQL node ID patterns, False otherwise
        """
        # Common GraphQL node ID patterns:
        # - New format: U_, PR_, R_, I_, etc. followed by base64-like characters
        # - Legacy format: MDQ6, MDExOl, MDE, etc. (base64 encoded)
        # - Typical length: > 10 characters
        # - Contains alphanumeric + underscore
        if len(value) < 10:
            return False

        # Check for common prefixes (case-sensitive)
        node_id_prefixes = (
            "U_",  # User
            "PR_",  # Pull Request
            "R_",  # Repository
            "I_",  # Issue
            "MDQ6",  # Legacy User
            "MDExOl",  # Legacy Repository
            "MDE",  # Legacy (various types)
            "MDU6",  # Legacy Issue
        )

        if value.startswith(node_id_prefixes):
            return True

        # Check if it matches base64-like pattern (alphanumeric + _ + / + =)
        # and doesn't look like a pure number
        if re.match(r"^[A-Za-z0-9_+/=]+$", value) and not value.isdigit():
            # Additional heuristic: GraphQL IDs typically have mixed case
            # and at least one uppercase letter (base64 characteristic)
            if any(c.isupper() for c in value):
                return True

        return False

    @staticmethod
    def _is_user_node_id(value: str) -> bool:
        """
        Check if a string is a GitHub User GraphQL node ID.

        User node IDs have specific patterns:
        - Modern format: U_kgDO... (starts with "U_")
        - Legacy format: MDQ6... (base64 encoded, starts with "MDQ6")

        This method is stricter than _is_graphql_node_id and only accepts User node IDs,
        rejecting other node types (PR_, R_, I_, etc.) to prevent security issues
        where non-user IDs could be passed to reviewer APIs.

        IMPORTANT: This method only accepts User node IDs (U_, MDQ6 prefixes).
        Pull Request IDs (PR_), Repository IDs (R_), Issue IDs (I_), and other
        non-user GraphQL node types are intentionally rejected to prevent
        incorrect API usage and potential security issues.

        Args:
            value: String to check

        Returns:
            True if the string matches User node ID patterns, False otherwise
        """
        # Minimum length check (User IDs are typically longer than 10 chars)
        if len(value) < 10:
            return False

        # Check for known User node ID prefixes (case-sensitive)
        if value.startswith("U_") or value.startswith("MDQ6"):
            # Additional safety: verify base64-like character set
            # User IDs contain alphanumeric + underscore + optional padding
            if re.match(r"^[A-Za-z0-9_+/=]+$", value):
                return True

        return False

    async def request_pr_reviews(self, pull_request: PullRequestWrapper, reviewers: list[str]) -> None:
        """Request reviews on PR via GraphQL.

        Uses: GraphQL
        Reason: requestReviews mutation is fully supported

        Reviewer ID Handling:
        - GraphQL node IDs (U_kgDOA...): Used directly
        - Usernames (str): Converted to GraphQL node IDs via get_user_id()
        - Invalid/unknown formats: Logged and skipped

        Args:
            pull_request: PR object (PullRequestWrapper)
            reviewers: List of reviewer identifiers (node IDs or usernames)

        Raises:
            GraphQLAuthenticationError: On authentication failures
            GraphQLRateLimitError: On rate limit exceeded
        """
        pr_id = pull_request.id
        reviewer_ids = []

        for reviewer in reviewers:
            # Skip numeric IDs (not supported in pure GraphQL mode)
            if isinstance(reviewer, int):
                self.logger.warning(
                    f"Numeric reviewer ID {reviewer} not supported - provide username or GraphQL node ID instead"
                )
                continue

            # Extract username from various formats
            username = None
            if isinstance(reviewer, str):
                # Check if already a GraphQL node ID
                if self._is_user_node_id(reviewer):
                    reviewer_ids.append(reviewer)
                    continue
                username = reviewer
            elif hasattr(reviewer, "login"):
                username = reviewer.login
            elif hasattr(reviewer, "user") and hasattr(reviewer.user, "login"):
                username = reviewer.user.login
            elif isinstance(reviewer, dict):
                username = reviewer.get("login") or (reviewer.get("user") or {}).get("login")
                # Check if dict has valid GraphQL node ID
                if not username and reviewer.get("id"):
                    extracted_id = str(reviewer["id"])
                    if self._is_user_node_id(extracted_id):
                        reviewer_ids.append(extracted_id)
                        continue

            if not username:
                self.logger.warning(f"Could not resolve username from reviewer: {reviewer}")
                continue

            # Convert username to GraphQL node ID
            try:
                user_id = await self.get_user_id(username)
                reviewer_ids.append(user_id)
            except (GraphQLAuthenticationError, GraphQLRateLimitError):
                # Re-raise critical errors
                raise
            except (GraphQLError, TransportConnectionFailed, TransportQueryError, TransportServerError) as ex:
                # Log and skip this reviewer if conversion fails
                self.logger.warning(f"Failed to get GraphQL node ID for reviewer '{username}': {ex}")
                continue

        # Deduplicate and request reviews
        if reviewer_ids:
            unique_reviewer_ids = list(dict.fromkeys(reviewer_ids))
            await self.request_reviews(pr_id, unique_reviewer_ids, pull_request=pull_request)

    async def add_pr_assignee(self, pull_request: PullRequestWrapper, assignee: str) -> None:
        """Add assignee to PR via unified_api."""
        try:
            pr_id = pull_request.id
            user_id = await self.get_user_id(assignee)
            await self.add_assignees(pr_id, [user_id])
        except (GraphQLError, GithubException, ValueError) as ex:
            self.logger.warning(f"Failed to add assignee {assignee}: {ex}")

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
            raise FileNotFoundError(f"File not found: {path}")  # noqa: TRY003

        # Handle binary files - production only reads text files (OWNERS, YAML configs)
        if blob.get("isBinary") or blob.get("text") is None:
            raise ValueError(f"Binary file not supported: {path}")  # noqa: TRY003

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
            self.logger.exception("Failed to add comment to %s", subject_id)
            raise
        else:
            self.logger.debug("GraphQL execute returned, extracting comment node")
            try:
                comment_node = result["addComment"]["commentEdge"]["node"]
            except KeyError:
                self.logger.exception("Failed to extract comment from GraphQL result for %s", subject_id)
                raise
            else:
                self.logger.info(f"SUCCESS: Comment added to {subject_id}, comment_id={comment_node.get('id')}")
                return comment_node

    async def create_issue_comment(self, owner: str, name: str, number: int, body: str) -> dict[str, Any]:
        """
        Add comment to PR or issue using owner/repo/number.

        This is a convenience method that wraps add_comment by first fetching the PR/issue node ID.

        Uses: GraphQL
        Reason: Two-step process: 1) Fetch PR/issue node ID, 2) Add comment

        Args:
            owner: Repository owner
            name: Repository name
            number: PR or issue number
            body: Comment text body

        Returns:
            Created comment data from GraphQL

        Note:
            This method makes 2 GraphQL calls (get PR + add comment).
            If you already have the PR node ID, use add_comment() directly for better performance.
        """
        # Fetch PR to get node ID
        pr_data = await self.get_pull_request_data(owner, name, number)
        pr_node_id = pr_data["id"]

        # Add comment using node ID
        return await self.add_comment(pr_node_id, body)

    async def add_labels(self, labelable_id: str, label_ids: list[str]) -> dict[str, Any]:
        """
        Add labels to PR or issue.

        Uses: GraphQL
        Reason: Efficient mutation

        Args:
            labelable_id: PR or issue node ID
            label_ids: List of label node IDs

        Returns:
            Mutation response containing updated labelable data
        """
        if not self.graphql_client:
            await self.initialize()

        mutation, variables = MutationBuilder.add_labels(labelable_id, label_ids)
        result = await self.graphql_client.execute(mutation, variables)  # type: ignore[union-attr]
        return result

    async def remove_labels(
        self,
        labelable_id: str,
        label_ids: list[str],
        owner: str | None = None,
        repo: str | None = None,
        number: int | None = None,
    ) -> dict[str, Any]:
        """
        Remove labels from PR or issue.

        Uses: GraphQL
        Reason: Efficient mutation

        Args:
            labelable_id: PR or issue node ID
            label_ids: List of label node IDs
            owner: Repository owner (optional, for retry on NOT_FOUND)
            repo: Repository name (optional, for retry on NOT_FOUND)
            number: PR/issue number (optional, for retry on NOT_FOUND)

        Returns:
            Mutation response containing updated labelable data
        """
        if not self.graphql_client:
            await self.initialize()

        mutation, variables = MutationBuilder.remove_labels(labelable_id, label_ids)

        try:
            result = await self.graphql_client.execute(mutation, variables)  # type: ignore[union-attr]
            return result
        except GraphQLError as ex:
            error_str = str(ex).lower()
            # Check if error is due to stale node ID
            if ("not_found" in error_str or "could not resolve to a node" in error_str) and all([
                owner,
                repo,
                number is not None,
            ]):
                self.logger.warning(
                    f"NOT_FOUND error for labelable_id {labelable_id}, "
                    f"retrying with fresh PR node ID (owner={owner}, repo={repo}, number={number})"
                )
                # Refetch PR to get fresh node ID
                pr_data = await self.get_pull_request_data(owner, repo, number)  # type: ignore[arg-type]
                fresh_labelable_id = pr_data["id"]
                self.logger.info(
                    f"Retrying remove_labels with fresh node ID: {fresh_labelable_id} (old: {labelable_id})"
                )
                # Retry mutation with fresh node ID (only once to avoid infinite loops)
                mutation, variables = MutationBuilder.remove_labels(fresh_labelable_id, label_ids)
                result = await self.graphql_client.execute(mutation, variables)  # type: ignore[union-attr]
                return result
            # Re-raise if not NOT_FOUND or if missing context for retry
            raise

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

    async def request_reviews(
        self, pull_request_id: str, user_ids: list[str], pull_request: PullRequestWrapper | None = None
    ) -> None:
        """
        Request reviews on a pull request.

        Uses: GraphQL
        Reason: Efficient mutation

        Args:
            pull_request_id: PR node ID
            user_ids: List of user node IDs to request reviews from
            pull_request: PullRequestWrapper object (optional, for retry on NOT_FOUND)
        """
        if not self.graphql_client:
            await self.initialize()

        mutation, variables = MutationBuilder.request_reviews(pull_request_id, user_ids)

        try:
            await self.graphql_client.execute(mutation, variables)  # type: ignore[union-attr]
        except GraphQLError as ex:
            error_str = str(ex).lower()
            # Check if error is due to stale node ID
            if ("not_found" in error_str or "could not resolve to a node" in error_str) and pull_request is not None:
                owner = pull_request.baseRepository.owner.login
                repo = pull_request.baseRepository.name
                number = pull_request.number
                self.logger.warning(
                    f"NOT_FOUND error for pull_request_id {pull_request_id}, "
                    f"retrying with fresh PR node ID (owner={owner}, repo={repo}, number={number})"
                )
                # Refetch PR to get fresh node ID
                pr_data = await self.get_pull_request_data(owner, repo, number)
                fresh_pr_id = pr_data["id"]
                self.logger.info(f"Retrying request_reviews with fresh node ID: {fresh_pr_id} (old: {pull_request_id})")
                # Retry mutation with fresh node ID (only once to avoid infinite loops)
                mutation, variables = MutationBuilder.request_reviews(fresh_pr_id, user_ids)
                await self.graphql_client.execute(mutation, variables)  # type: ignore[union-attr]
            else:
                # Re-raise if not NOT_FOUND or if missing pull_request for retry
                raise

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
        Get user node ID from login using GraphQL.

        Uses: GraphQL
        Reason: User query is fully supported

        Args:
            login: User login name

        Returns:
            User node ID

        Raises:
            GraphQLError: If user not found or GraphQL query fails
        """
        if not self.graphql_client:
            await self.initialize()

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
              Expected GraphQL query:
              pullRequest(number: X) { files(first: 100) { nodes { path, additions, deletions } } }

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

    async def get_open_pull_requests_with_details(
        self, owner: str, repo: str, max_prs: int = 100
    ) -> list[PullRequestWrapper]:
        """
        Get all open PRs with full details (labels, mergeable state) in ONE GraphQL query.

        Replaces N+1 pattern:
        - OLD: get_open_pull_requests() + get_pull_request_data() for each PR
        - NEW: Single batched query with all data

        Uses: GraphQL
        Reason: Eliminates N+1 queries - fetches all open PRs with labels/state in single request

        Args:
            owner: Repository owner
            repo: Repository name
            max_prs: Maximum number of PRs to fetch (default: 100)

        Returns:
            List of PullRequestWrapper objects with labels and merge state already populated

        Example:
            >>> prs = await api.get_open_pull_requests_with_details("owner", "repo")
            >>> for pr in prs:
            ...     # No additional API calls needed - labels already loaded
            ...     labels = pr.get_labels()
            ...     merge_state = pr.mergeable_state

        Performance:
            If N open PRs exist:
            - OLD approach: N+1 API calls (1 to list + N to fetch details)
            - NEW approach: 1 API call (batched query)
            - Savings: N API calls eliminated
        """
        if not self.graphql_client:
            await self.initialize()

        query, variables = QueryBuilder.get_open_pull_requests_with_labels(owner, repo, first=max_prs)
        result = await self.graphql_client.execute(query, variables)  # type: ignore[union-attr]

        pr_nodes = result.get("repository", {}).get("pullRequests", {}).get("nodes", [])

        return [PullRequestWrapper(pr_data, owner, repo) for pr_data in pr_nodes]

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

        Uses: GraphQL
        Reason: Migrated from REST as part of GraphQL-first approach

        Args:
            owner: Repository owner
            name: Repository name
            number: PR number
            assignees: List of user logins
        """
        # Get PR node ID via GraphQL
        pr_data = await self.get_pull_request_data(owner, name, number)
        pr_id = pr_data["id"]

        # Convert usernames to GraphQL node IDs
        assignee_ids = []
        for username in assignees:
            try:
                user_id = await self.get_user_id(username)
                assignee_ids.append(user_id)
            except GraphQLError as ex:
                self.logger.warning(f"Failed to get user ID for assignee '{username}': {ex}")
                continue

        # Add assignees via GraphQL mutation
        if assignee_ids:
            await self.add_assignees(pr_id, assignee_ids)

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

    async def get_contributors(
        self, owner: str, name: str, repository_data: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """
        Get repository contributors.

        Uses: GraphQL
        Reason: GraphQL migration - fetches contributors via mentionableUsers query

        Args:
            owner: Repository owner
            name: Repository name
            repository_data: Optional pre-fetched repository data (from webhook context)

        Returns:
            List of contributor data (dicts with id, login, name, etc.)
        """
        # Use pre-fetched data if provided (webhook context)
        if repository_data is not None:
            self.logger.debug(f"Using pre-fetched contributors for {owner}/{name}")
            return repository_data["mentionableUsers"]["nodes"]

        # Fallback to individual query (standalone usage, backwards compatibility)
        if not self.graphql_client:
            await self.initialize()

        query = """
            query($owner: String!, $name: String!) {
                repository(owner: $owner, name: $name) {
                    mentionableUsers(first: 100) {
                        nodes {
                            id
                            login
                            name
                            email
                            avatarUrl
                        }
                    }
                }
            }
        """
        variables = {"owner": owner, "name": name}
        result = await self.graphql_client.execute(query, variables)  # type: ignore[union-attr]
        return result["repository"]["mentionableUsers"]["nodes"]

    async def get_collaborators(
        self, owner: str, name: str, repository_data: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """
        Get repository collaborators.

        Uses: GraphQL
        Reason: GraphQL migration - fetches collaborators with permissions via collaborators query

        Args:
            owner: Repository owner
            name: Repository name
            repository_data: Optional pre-fetched repository data (from webhook context)

        Returns:
            List of collaborator data (dicts with permission, node with user info)
        """
        # Use pre-fetched data if provided (webhook context)
        if repository_data is not None:
            self.logger.debug(f"Using pre-fetched collaborators for {owner}/{name}")
            return repository_data["collaborators"]["edges"]

        # Fallback to individual query (standalone usage, backwards compatibility)
        if not self.graphql_client:
            await self.initialize()

        query = """
            query($owner: String!, $name: String!) {
                repository(owner: $owner, name: $name) {
                    collaborators(first: 100) {
                        edges {
                            permission
                            node {
                                id
                                login
                                name
                                email
                                avatarUrl
                            }
                        }
                    }
                }
            }
        """
        variables = {"owner": owner, "name": name}
        result = await self.graphql_client.execute(query, variables)  # type: ignore[union-attr]
        return result["repository"]["collaborators"]["edges"]

    async def get_branch(self, owner: str, name: str, branch: str) -> bool:
        """
        Check if branch exists using GraphQL.

        Uses: GraphQL repository.ref() query

        Args:
            owner: Repository owner
            name: Repository name
            branch: Branch name (without refs/heads/ prefix)

        Returns:
            bool: True if branch exists, False if not found

        Raises:
            GraphQLError: For auth failures, rate limits, or network errors
            RuntimeError: If GraphQL client initialization fails

        Note: Changed from returning Branch object to bool for efficiency.
              All current usages only check existence, not branch data.
              Only NOT_FOUND errors return False; critical errors propagate.
        """
        if not self.graphql_client:
            await self.initialize()
            if not self.graphql_client:
                raise RuntimeError("Failed to initialize GraphQL client")

        query = """
        query($owner: String!, $name: String!, $ref: String!) {
          repository(owner: $owner, name: $name) {
            ref(qualifiedName: $ref) {
              id
            }
          }
        }
        """
        variables = {"owner": owner, "name": name, "ref": f"refs/heads/{branch}"}

        try:
            result = await self.graphql_client.execute(query, variables)
            return result.get("repository", {}).get("ref") is not None
        except GraphQLError as ex:
            # Only return False for NOT_FOUND errors (branch doesn't exist)
            # Re-raise auth, rate-limit, network, and other critical errors
            error_str = str(ex).lower()

            # Check for NOT_FOUND indicators in error message
            if "not found" in error_str or "could not resolve" in error_str:
                return False

            # Re-raise all other errors (auth, rate limit, network, etc.)
            raise

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

    async def get_issues(
        self, owner: str, name: str, states: list[str] | None = None, repository_data: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """
        Get repository issues.

        Uses: GraphQL
        Reason: GraphQL migration - fetches issues with states filter via issues query

        Args:
            owner: Repository owner
            name: Repository name
            states: Issue states (OPEN, CLOSED) - defaults to OPEN if not specified
            repository_data: Optional pre-fetched repository data (from webhook context)

        Returns:
            List of issue data (dicts with id, number, title, state, etc.)
        """
        # Default to OPEN issues if not specified (matches REST behavior)
        issue_states = states if states else ["OPEN"]

        # Use pre-fetched data if provided AND requesting only OPEN issues
        # Note: repository_data only contains OPEN issues
        if repository_data is not None and issue_states == ["OPEN"]:
            self.logger.debug(f"Using pre-fetched issues for {owner}/{name}")
            return repository_data["issues"]["nodes"]

        # Fallback to individual query (standalone usage, non-OPEN states, backwards compatibility)
        if not self.graphql_client:
            await self.initialize()

        query = """
            query($owner: String!, $name: String!, $states: [IssueState!]) {
                repository(owner: $owner, name: $name) {
                    issues(first: 100, states: $states) {
                        nodes {
                            id
                            number
                            title
                            body
                            state
                            createdAt
                            updatedAt
                            author {
                                login
                            }
                            labels(first: 10) {
                                nodes {
                                    id
                                    name
                                }
                            }
                        }
                    }
                }
            }
        """
        variables = {"owner": owner, "name": name, "states": issue_states}
        result = await self.graphql_client.execute(query, variables)  # type: ignore[union-attr]
        return result["repository"]["issues"]["nodes"]

    async def edit_issue(self, issue: Any, state: str) -> None:
        """
        Edit issue state (close or reopen).

        Uses: GraphQL
        Reason: Migrated from REST - closeIssue/reopenIssue mutations available

        Args:
            issue: Issue object (REST or has node_id attribute)
            state: "closed" or "open"
        """
        if not self.graphql_client:
            await self.initialize()

        # Extract node ID from issue object
        issue_id = issue.node_id if hasattr(issue, "node_id") else issue.id

        # Use appropriate GraphQL mutation based on state
        if state.lower() == "closed":
            mutation = """
                mutation($issueId: ID!) {
                    closeIssue(input: {issueId: $issueId}) {
                        issue {
                            id
                            state
                        }
                    }
                }
            """
        else:  # state == "open" or "OPEN"
            mutation = """
                mutation($issueId: ID!) {
                    reopenIssue(input: {issueId: $issueId}) {
                        issue {
                            id
                            state
                        }
                    }
                }
            """

        variables = {"issueId": issue_id}
        await self.graphql_client.execute(mutation, variables)  # type: ignore[union-attr]

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

    def _build_tree_entries_fragment(self, depth: int, max_depth: int) -> str:
        """
        Build recursive GraphQL tree entries fragment.

        Args:
            depth: Current nesting depth (0-based)
            max_depth: Maximum depth to traverse

        Returns:
            GraphQL fragment string with nested tree entries
        """
        if depth >= max_depth:
            # At max depth, just return basic entry info
            return """
            name
            type
            mode
            object {
                ... on Blob {
                    oid
                    byteSize
                }
                ... on Tree {
                    oid
                }
            }
        """

        # Recursive fragment - goes deeper
        nested_fragment = self._build_tree_entries_fragment(depth + 1, max_depth)
        return f"""
            name
            type
            mode
            object {{
                ... on Blob {{
                    oid
                    byteSize
                }}
                ... on Tree {{
                    oid
                    entries {{
                        {nested_fragment}
                    }}
                }}
            }}
        """

    def _flatten_tree_entries(self, entries: list[dict[str, Any]], parent_path: str = "") -> list[dict[str, Any]]:
        """
        Flatten nested tree structure into flat list with full paths.

        Args:
            entries: Tree entries from GraphQL response
            parent_path: Parent directory path

        Returns:
            Flat list of entries with full paths
        """
        result = []

        for entry in entries:
            # Build full path
            full_path = f"{parent_path}/{entry['name']}" if parent_path else entry["name"]

            # Add current entry with full path
            flat_entry = {
                "path": full_path,
                "mode": entry["mode"],
                "type": entry["type"].lower(),  # BLOB -> blob, TREE -> tree
                "sha": entry["object"]["oid"] if entry["object"] else None,
            }

            # Add size for blobs
            if entry["type"] == "BLOB" and entry["object"]:
                flat_entry["size"] = entry["object"].get("byteSize")

            result.append(flat_entry)

            # Recursively process subdirectories
            if entry["type"] == "TREE" and entry["object"] and "entries" in entry["object"]:
                result.extend(self._flatten_tree_entries(entry["object"]["entries"], full_path))

        return result

    async def get_git_tree(self, owner: str, name: str, ref: str) -> dict[str, Any]:
        """
        Get git tree with recursive traversal.

        Uses: GraphQL
        Reason: GraphQL with dynamic nested fragments - gets full tree in 1 API call

        Args:
            owner: Repository owner
            name: Repository name
            ref: Git reference (branch, tag, commit SHA)

        Returns:
            Tree data (dict with sha, tree entries with full paths)

        Note:
            Uses dynamic query building to traverse tree to configurable depth.
            Default: 9 levels (max safe value for GitHub's 25 depth limit).
            Configure via: graphql.tree-max-depth in config.yaml
        """
        if not self.graphql_client:
            await self.initialize()

        # Get max depth from config (default: 9 levels - max safe value for GitHub's 25 depth limit)
        max_depth = self.config.get_value("graphql.tree-max-depth", return_on_none=9)

        # Build recursive query with configured depth
        entries_fragment = self._build_tree_entries_fragment(0, max_depth)

        query = f"""
            query($owner: String!, $name: String!, $expression: String!) {{
                repository(owner: $owner, name: $name) {{
                    object(expression: $expression) {{
                        ... on Tree {{
                            oid
                            entries {{
                                {entries_fragment}
                            }}
                        }}
                    }}
                }}
            }}
        """

        variables = {"owner": owner, "name": name, "expression": f"{ref}:"}
        result = await self.graphql_client.execute(query, variables)  # type: ignore[union-attr]

        tree_data = result["repository"]["object"]
        if not tree_data:
            raise ValueError(f"Reference '{ref}' not found in repository {owner}/{name}")  # noqa: TRY003

        # Flatten nested structure to REST-compatible format with full paths
        flattened_entries = self._flatten_tree_entries(tree_data["entries"])

        # Check if we hit max depth (warn if last level has Tree entries)
        trees_at_max_depth = [
            e for e in flattened_entries if e["type"] == "tree" and e["path"].count("/") >= max_depth - 1
        ]
        if trees_at_max_depth and hasattr(self, "logger"):
            self.logger.warning(
                f"Tree traversal reached max depth ({max_depth}). "
                f"Found {len(trees_at_max_depth)} directories at depth limit. "
                f"Consider increasing 'graphql.tree-max-depth' in config if files are missing."
            )

        return {
            "sha": tree_data["oid"],
            "tree": flattened_entries,
        }

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
              GraphQL: mutation {
                enablePullRequestAutomerge(input: {pullRequestId: PR_ID, mergeMethod: SQUASH}) { ... }
              }
              This enables auto-merge (PR merges when checks pass), not immediate merge like REST.
              For immediate merge, REST is currently the only option.
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        pr = await asyncio.to_thread(repo.get_pull, number)
        await asyncio.to_thread(pr.merge, merge_method=merge_method)

    async def get_pulls_from_commit(
        self, commit: Any, owner: str | None = None, name: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Get pull requests associated with a commit.

        Uses: GraphQL (preferred) with REST fallback
        Reason: GraphQL migration - fetches associated PRs via associatedPullRequests query

        Args:
            commit: REST Commit object or CommitWrapper (or any object with sha attribute)
            owner: Repository owner (required for GraphQL, optional for REST commit objects)
            name: Repository name (required for GraphQL, optional for REST commit objects)

        Returns:
            List of pull request data (dicts with PR information)

        Note:
            If owner/name provided, uses GraphQL for better performance.
            Otherwise, falls back to REST API via commit.get_pulls() method.
        """
        # If owner and name provided, use GraphQL with commit SHA
        if owner and name and hasattr(commit, "sha"):
            return await self.get_pulls_from_commit_sha(owner, name, commit.sha)

        # Fallback to REST API for backward compatibility
        if hasattr(commit, "get_pulls") and callable(commit.get_pulls):
            return await asyncio.to_thread(lambda: list(commit.get_pulls()))

        # If we have sha but no get_pulls method, and no owner/name - cannot proceed
        self.logger.warning(
            f"Unable to get PRs for commit (type={type(commit).__name__}, has_sha={hasattr(commit, 'sha')}, "
            f"owner={owner}, name={name}). Provide owner/name for GraphQL lookup or use REST commit object."
        )
        return []

    async def get_pulls_from_commit_sha(self, owner: str, name: str, sha: str) -> list[dict[str, Any]]:
        """
        Get pull requests associated with a commit SHA.

        Uses: GraphQL
        Reason: GraphQL migration - fetches associated PRs via associatedPullRequests query

        Args:
            owner: Repository owner
            name: Repository name
            sha: Commit SHA

        Returns:
            List of pull request data (dicts with PR information)
        """
        if not self.graphql_client:
            await self.initialize()

        query = """
            query($owner: String!, $name: String!, $oid: GitObjectID!) {
                repository(owner: $owner, name: $name) {
                    object(oid: $oid) {
                        ... on Commit {
                            associatedPullRequests(first: 10) {
                                nodes {
                                    id
                                    number
                                    title
                                    state
                                    baseRefName
                                    headRefName
                                    author {
                                        login
                                    }
                                    createdAt
                                    updatedAt
                                    mergedAt
                                    closedAt
                                }
                            }
                        }
                    }
                }
            }
        """
        variables = {"owner": owner, "name": name, "oid": sha}
        result = await self.graphql_client.execute(query, variables)  # type: ignore[union-attr]

        commit_data = result["repository"]["object"]
        if not commit_data:
            raise ValueError(f"Commit '{sha}' not found in repository {owner}/{name}")  # noqa: TRY003

        return commit_data["associatedPullRequests"]["nodes"]

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
        if operation in graphql_preferred:
            return APIType.GRAPHQL
        return APIType.HYBRID


# API Selection Documentation
