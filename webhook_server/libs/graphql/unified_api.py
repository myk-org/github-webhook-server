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

from github import Github
from github.PullRequest import PullRequest as RestPullRequest
from github.Repository import Repository as RestRepository

from webhook_server.libs.graphql.graphql_builders import MutationBuilder, QueryBuilder
from webhook_server.libs.graphql.graphql_client import GraphQLClient


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

    def __init__(self, token: str, logger: logging.Logger) -> None:
        """
        Initialize unified API client.

        Args:
            token: GitHub personal access token or GitHub App token
            logger: Logger instance
        """
        self.token = token
        self.logger = logger

        # GraphQL client (async)
        self.graphql_client: GraphQLClient | None = None

        # REST client (sync) - kept for fallback operations
        self.rest_client: Github | None = None
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize both GraphQL and REST clients."""
        if self._initialized:
            return

        # Initialize GraphQL client
        self.graphql_client = GraphQLClient(token=self.token, logger=self.logger)

        # Initialize REST client (PyGithub)
        self.rest_client = Github(self.token)

        self._initialized = True
        self.logger.info("Unified GitHub API initialized (GraphQL + REST)")

    async def close(self) -> None:
        """Close and cleanup API clients."""
        if self.graphql_client:
            await self.graphql_client.close()

        if self.rest_client:
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

        query = QueryBuilder.get_repository(owner, name)
        result = await self.graphql_client.execute(query)  # type: ignore[union-attr]
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

        query = QueryBuilder.get_pull_request(
            owner,
            name,
            number,
            include_commits=include_commits,
            include_labels=include_labels,
            include_reviews=include_reviews,
        )
        result = await self.graphql_client.execute(query)  # type: ignore[union-attr]
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

        query = QueryBuilder.get_pull_requests(owner, name, states=states, first=first, after=after)
        result = await self.graphql_client.execute(query)  # type: ignore[union-attr]
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

        query = QueryBuilder.get_commit(owner, name, oid)
        result = await self.graphql_client.execute(query)  # type: ignore[union-attr]
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
        query = QueryBuilder.get_file_contents(owner, name, expression)
        result = await self.graphql_client.execute(query)  # type: ignore[union-attr]
        return result["repository"]["object"]["text"]

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
            await self.initialize()

        mutation, variables = MutationBuilder.add_comment(subject_id, body)
        result = await self.graphql_client.execute(mutation, variables)  # type: ignore[union-attr]
        return result["addComment"]["commentEdge"]["node"]

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

        Uses: GraphQL
        Reason: Need node ID for mutations

        Args:
            login: User login name

        Returns:
            User node ID
        """
        if not self.graphql_client:
            await self.initialize()

        query = f"""
            query {{
                user(login: "{login}") {{
                    id
                }}
            }}
        """
        result = await self.graphql_client.execute(query)  # type: ignore[union-attr]
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

        query = f"""
            query {{
                repository(owner: "{owner}", name: "{name}") {{
                    label(name: "{label_name}") {{
                        id
                    }}
                }}
            }}
        """
        result = await self.graphql_client.execute(query)  # type: ignore[union-attr]
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
        if not self.rest_client:
            raise RuntimeError("REST client not initialized. Call initialize() first.")

        return await asyncio.to_thread(self.rest_client.get_repo, f"{owner}/{name}")

    async def get_pr_for_check_runs(self, owner: str, name: str, number: int) -> RestPullRequest:
        """
        Get PR object specifically for check runs access.

        Uses: REST (wrapped in asyncio.to_thread to avoid blocking)
        Reason: Check Runs API is NOT available in GitHub GraphQL v4

        Args:
            owner: Repository owner
            name: Repository name
            number: Pull request number

        Returns:
            PyGithub PullRequest object (for check runs only)

        Note: For PR data (title, labels, commits, etc.), use get_pull_request() instead!
              This method exists ONLY because check runs aren't in GraphQL.

        Example:
            >>> # ✅ Use GraphQL for PR data
            >>> pr_data = await api.get_pull_request("owner", "repo", 123)
            >>>
            >>> # ❌ Use REST ONLY for check runs
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
        
        Args:
            owner: Repository owner
            name: Repository name
            number: Pull request number
            
        Returns:
            List of file objects
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        pr = await asyncio.to_thread(repo.get_pull, number)
        return await asyncio.to_thread(pr.get_files)
    
    async def create_issue_comment(self, owner: str, name: str, number: int, body: str) -> None:
        """
        Create a comment on a pull request or issue.
        
        Uses: REST (helper method)
        
        Args:
            owner: Repository owner
            name: Repository name
            number: PR or issue number
            body: Comment text
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        pr = await asyncio.to_thread(repo.get_pull, number)
        await asyncio.to_thread(pr.create_issue_comment, body)
    
    async def get_issue_comments(self, owner: str, name: str, number: int) -> list[Any]:
        """
        Get all comments on a pull request or issue.
        
        Uses: REST (not yet in GraphQL)
        
        Args:
            owner: Repository owner
            name: Repository name
            number: PR or issue number
            
        Returns:
            List of comment objects
        """
        repo = await self.get_repository_for_rest_operations(owner, name)
        pr = await asyncio.to_thread(repo.get_pull, number)
        return await asyncio.to_thread(pr.get_issue_comments)
    
    async def add_assignees_by_login(self, owner: str, name: str, number: int, assignees: list[str]) -> None:
        """
        Add assignees to a pull request by login name.
        
        Uses: REST (helper method)
        
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
        """Get a specific issue comment."""
        repo = await self.get_repository_for_rest_operations(owner, name)
        pr = await asyncio.to_thread(repo.get_pull, number)
        return await asyncio.to_thread(pr.get_issue_comment, comment_id)
    
    async def create_reaction(self, comment: Any, reaction: str) -> None:
        """Create a reaction on a comment."""
        await asyncio.to_thread(comment.create_reaction, reaction)
    
    async def get_contributors(self, owner: str, name: str) -> list[Any]:
        """Get repository contributors."""
        repo = await self.get_repository_for_rest_operations(owner, name)
        return list(await asyncio.to_thread(repo.get_contributors))
    
    async def get_collaborators(self, owner: str, name: str) -> list[Any]:
        """Get repository collaborators."""
        repo = await self.get_repository_for_rest_operations(owner, name)
        return list(await asyncio.to_thread(repo.get_collaborators))
    
    async def get_branch(self, owner: str, name: str, branch: str) -> Any:
        """Get branch information."""
        repo = await self.get_repository_for_rest_operations(owner, name)
        return await asyncio.to_thread(repo.get_branch, branch)
    
    async def get_branch_protection(self, owner: str, name: str, branch: str) -> Any:
        """Get branch protection rules."""
        repo = await self.get_repository_for_rest_operations(owner, name)
        branch_obj = await asyncio.to_thread(repo.get_branch, branch)
        return await asyncio.to_thread(branch_obj.get_protection)
    
    async def get_issues(self, owner: str, name: str) -> list[Any]:
        """Get repository issues."""
        repo = await self.get_repository_for_rest_operations(owner, name)
        return list(await asyncio.to_thread(repo.get_issues))
    
    async def create_issue(self, owner: str, name: str, title: str, body: str) -> None:
        """Create an issue."""
        repo = await self.get_repository_for_rest_operations(owner, name)
        await asyncio.to_thread(repo.create_issue, title=title, body=body)
    
    async def edit_issue(self, issue: Any, state: str) -> None:
        """Edit issue state."""
        await asyncio.to_thread(issue.edit, state=state)
    
    async def create_issue_comment_on_issue(self, issue: Any, body: str) -> None:
        """Create a comment on an issue object."""
        await asyncio.to_thread(issue.create_comment, body)
    
    async def get_contents(self, owner: str, name: str, path: str, ref: str) -> Any:
        """Get file contents from repository."""
        repo = await self.get_repository_for_rest_operations(owner, name)
        return await asyncio.to_thread(repo.get_contents, path, ref)
    
    async def get_git_tree(self, owner: str, name: str, ref: str, recursive: bool = True) -> Any:
        """Get git tree."""
        repo = await self.get_repository_for_rest_operations(owner, name)
        return await asyncio.to_thread(repo.get_git_tree, ref, recursive=recursive)
    
    async def get_commit_check_runs(self, commit: Any) -> list[Any]:
        """
        Get check runs for a commit.
        
        Note: This only works with REST API Commit objects, not CommitWrapper.
        CommitWrapper from GraphQL doesn't have check runs data.
        """
        # Check if this is a REST commit object (has get_check_runs method)
        if hasattr(commit, 'get_check_runs') and callable(commit.get_check_runs):
            return list(await asyncio.to_thread(commit.get_check_runs))
        # CommitWrapper from GraphQL - return empty list
        return []
    
    async def create_check_run(self, repo_by_app: Any, **kwargs: Any) -> None:
        """Create a check run using GitHub App repository."""
        await asyncio.to_thread(repo_by_app.create_check_run, **kwargs)
    
    async def merge_pull_request(self, owner: str, name: str, number: int, merge_method: str = "SQUASH") -> None:
        """Merge a pull request."""
        repo = await self.get_repository_for_rest_operations(owner, name)
        pr = await asyncio.to_thread(repo.get_pull, number)
        await asyncio.to_thread(pr.merge, merge_method=merge_method)
    
    async def is_pull_request_merged(self, owner: str, name: str, number: int) -> bool:
        """Check if pull request is merged."""
        repo = await self.get_repository_for_rest_operations(owner, name)
        pr = await asyncio.to_thread(repo.get_pull, number)
        return await asyncio.to_thread(pr.is_merged)
    
    async def get_pr_commits(self, owner: str, name: str, number: int) -> list[Any]:
        """Get all commits from a pull request."""
        pr = await self.get_pr_for_check_runs(owner, name, number)
        return list(await asyncio.to_thread(pr.get_commits))
    
    async def get_commit(self, owner: str, name: str, sha: str) -> Any:
        """Get a commit by SHA."""
        repo = await self.get_repository_for_rest_operations(owner, name)
        return await asyncio.to_thread(repo.get_commit, sha)
    
    async def get_pulls_from_commit(self, commit: Any) -> list[Any]:
        """Get pull requests associated with a commit."""
        return await asyncio.to_thread(commit.get_pulls)
    
    async def get_open_pull_requests(self, owner: str, name: str) -> list[Any]:
        """Get all open pull requests."""
        repo = await self.get_repository_for_rest_operations(owner, name)
        return await asyncio.to_thread(repo.get_pulls, state="open")

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
        }

        # Operations better in GraphQL (fewer API calls)
        graphql_preferred = {
            "get_pull_request",
            "get_pull_requests",
            "get_commit",
            "get_commits",
            "get_labels",
            "add_comment",
            "add_labels",
            "remove_labels",
            "get_file_contents",
            "get_issues",
            "create_issue",
            "get_rate_limit",
            "get_user",
        }

        if operation in rest_only:
            return APIType.REST
        elif operation in graphql_preferred:
            return APIType.GRAPHQL
        else:
            return APIType.HYBRID


# API Selection Documentation
