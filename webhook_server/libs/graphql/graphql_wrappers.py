"""
GraphQL response wrappers that provide PyGithub-compatible interfaces.

This module contains wrapper classes that make GraphQL dictionary responses
behave like PyGithub objects, enabling gradual migration without breaking
existing handler code.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from github.PullRequest import PullRequest


class UserWrapper:
    """Wrapper for GitHub user data from GraphQL responses."""

    def __init__(self, data: dict[str, Any] | None):
        self._data = data or {}

    @property
    def login(self) -> str:
        return self._data.get("login", "")

    @property
    def type(self) -> str:
        """
        Get user type from GraphQL __typename field.
        Returns: "User", "Bot", "Organization", etc.
        """
        return self._data.get("__typename", "User")

    @property
    def id(self) -> int:
        """
        Get user ID from webhook data.
        Returns: User ID (integer) from webhook payload.
        """
        return self._data.get("id", 0)

    @property
    def node_id(self) -> str:
        """
        Get user node_id (GraphQL global ID) from webhook data.
        Returns: GraphQL node ID (e.g., "MDM6Qm90NjY4NTMxMTM=") from webhook payload.
        This avoids the need to make a GraphQL query for bot accounts.
        """
        return self._data.get("node_id", "")


class RepositoryWrapper:
    """Minimal wrapper for repository information."""

    def __init__(self, owner: str | dict[str, Any] | None = None, name: str | None = None):
        """
        Initialize RepositoryWrapper.

        Args:
            owner: Either owner login string or dict with repository data (backward compatibility)
            name: Repository name (optional if owner is dict)
        """
        if isinstance(owner, dict):
            # Dict mode (backward compatibility)
            data = owner
            self._owner = data.get("owner", {}).get("login", "") if isinstance(data.get("owner"), dict) else ""
            self._name = data.get("name", "")
        else:
            # String mode (current API)
            self._owner = owner or ""
            self._name = name or ""

    @property
    def owner(self) -> UserWrapper:
        """Return owner as UserWrapper."""
        return UserWrapper({"login": self._owner})

    @property
    def name(self) -> str:
        """Return repository name."""
        return self._name


class RefWrapper:
    """Wrapper for GitHub ref (branch) data from GraphQL or webhook responses."""

    def __init__(self, data: dict[str, Any] | None, repository: RepositoryWrapper | None = None):
        self._data = data or {}
        self._repository = repository

    @property
    def name(self) -> str:
        """
        Get branch name.
        Webhook format: {"ref": "branch-name", "sha": "..."}
        GraphQL format: {"name": "branch-name", "target": {"oid": "..."}}
        """
        # Webhook format uses "ref" field
        if "ref" in self._data and "sha" in self._data:
            return self._data.get("ref", "")
        # GraphQL format uses "name" field
        return self._data.get("name", "")

    @property
    def ref(self) -> str:
        """Alias for name to match PyGithub interface."""
        return self.name

    @property
    def sha(self) -> str:
        """
        Get the commit SHA.
        Webhook format: {"sha": "..."}
        GraphQL format: {"target": {"oid": "..."}}
        """
        # Webhook format uses "sha" field directly
        if "sha" in self._data:
            return self._data.get("sha", "")
        # GraphQL format uses "target.oid"
        target = self._data.get("target", {})
        return target.get("oid", "")

    @property
    def repo(self) -> RepositoryWrapper:
        """Return repository wrapper for PyGithub compatibility."""
        if self._repository is None:
            raise AttributeError(
                "RefWrapper.repo: repository information not available. "
                "RefWrapper was initialized without a RepositoryWrapper object. "
                "To access repo, instantiate RefWrapper with repository parameter: "
                "RefWrapper(data, repository=RepositoryWrapper(owner, name))"
            )
        return self._repository


class LabelWrapper:
    """Wrapper for GitHub label data from GraphQL responses."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    @property
    def name(self) -> str:
        return self._data.get("name", "")

    @property
    def color(self) -> str:
        return self._data.get("color", "")

    @property
    def id(self) -> str:
        return self._data.get("id", "")


class CommitWrapper:
    """Wrapper for GitHub commit data from GraphQL responses."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    @property
    def sha(self) -> str:
        return self._data.get("oid", "")

    @property
    def committer(self) -> UserWrapper:
        """Get committer information."""
        # GraphQL commit data is already extracted (not nested under "commit" key)
        # Access committer directly from self._data - use .get() for defensive access
        committer_data = self._data.get("committer") or {}

        # Map committer.user to UserWrapper if available - check if dict
        committer_user = committer_data.get("user")
        if committer_user and isinstance(committer_user, dict):
            return UserWrapper(committer_user)

        # If committer has name but no user, use name as login
        committer_name = committer_data.get("name", "")
        if committer_name:
            return UserWrapper({"login": committer_name})

        # Fall back to author if no committer data
        author_data = self._data.get("author") or {}
        author_user = author_data.get("user")
        if author_user and isinstance(author_user, dict):
            return UserWrapper(author_user)

        # Final fallback: use author name as login
        author_name = author_data.get("name", "")
        return UserWrapper({"login": author_name})


class PullRequestWrapper:
    """
    Wrapper for GitHub pull request data from GraphQL or REST responses.

    Provides a PyGithub-compatible interface for PullRequest objects,
    allowing existing handler code to work unchanged while using
    GraphQL responses internally.

    This wrapper supports dual mode operation:
    - GraphQL mode: When `data` dict is provided (preferred, faster)
    - REST mode: When `rest_pr` PyGithub PullRequest object is provided
    - Hybrid mode: Both can coexist, GraphQL data takes precedence

    The __getattr__ method automatically delegates to the REST object
    for any attributes not explicitly defined in this wrapper, providing
    seamless compatibility with PyGithub's full API surface.

    Args:
        data: GraphQL response dictionary (optional)
        owner: Repository owner login (optional, for GraphQL mode)
        repo_name: Repository name (optional, for GraphQL mode)
        rest_pr: PyGithub PullRequest object (optional)
        webhook_data: GitHub webhook payload (optional, preferred for accurate user.login)
    """

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        owner: str | None = None,
        repo_name: str | None = None,
        rest_pr: PullRequest | None = None,
        webhook_data: dict[str, Any] | None = None,
    ):
        self._data = data or {}
        self._owner = owner
        self._repo_name = repo_name
        self._rest_pr = rest_pr
        # Extract webhook payload - prioritize webhook_data parameter over REST object
        # webhook_data comes from GitHub webhook payload and contains accurate user.login with [bot] suffix
        self._raw_data: dict[str, Any] | None = None
        if webhook_data:
            # Priority 1: Use webhook_data parameter (most accurate, contains correct user.login)
            self._raw_data = webhook_data
        elif self._rest_pr and hasattr(self._rest_pr, "raw_data"):
            # Priority 2: Extract webhook payload from REST object if available (avoid API calls)
            # Only use raw_data if it's a dict (not a Mock or other object)
            raw = self._rest_pr.raw_data
            if isinstance(raw, dict):
                self._raw_data = raw
        # Create repository wrapper if owner and repo_name provided
        self._repository = RepositoryWrapper(owner, repo_name) if owner and repo_name else None

    def __getattr__(self, name: str) -> Any:
        """
        Automatically delegate to REST object for any attribute not found in wrapper.

        This enables full PyGithub API compatibility without explicitly wrapping
        every single attribute and method. Any attribute not defined in this
        wrapper class will be looked up in the underlying REST PullRequest object.

        Args:
            name: Attribute name to look up

        Returns:
            The attribute value from the REST object

        Raises:
            AttributeError: If attribute not found in REST object or REST object not available
        """
        if self._rest_pr:
            return getattr(self._rest_pr, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    @property
    def raw_data(self) -> dict[str, Any]:
        """
        Get raw data dict for compatibility.
        Returns webhook payload if available, otherwise GraphQL data.
        """
        # Prefer webhook payload over GraphQL data
        if self._raw_data:
            return self._raw_data
        return self._data

    @property
    def number(self) -> int:
        """Get PR number (GraphQL preferred, REST fallback)."""
        if self._data:
            return self._data.get("number", 0)
        if self._rest_pr:
            return self._rest_pr.number
        return 0

    @property
    def title(self) -> str:
        """Get PR title (GraphQL preferred, REST fallback)."""
        if self._data:
            return self._data.get("title", "")
        if self._rest_pr:
            return self._rest_pr.title
        return ""

    @property
    def body(self) -> str | None:
        """Get PR body/description (GraphQL preferred, REST fallback)."""
        if self._data:
            return self._data.get("body")
        if self._rest_pr:
            return self._rest_pr.body
        return None

    @property
    def state(self) -> str:
        """
        Return state in lowercase to match PyGithub (open/closed).
        GraphQL preferred, REST fallback.
        """
        if self._data:
            state = self._data.get("state", "OPEN")
            return state.lower()
        if self._rest_pr:
            return self._rest_pr.state
        return "open"

    @property
    def draft(self) -> bool:
        """Get draft status (GraphQL preferred, REST fallback)."""
        if self._data:
            return self._data.get("isDraft", False)
        if self._rest_pr:
            return self._rest_pr.draft
        return False

    @property
    def merged(self) -> bool:
        """Get merged status (GraphQL preferred, REST fallback)."""
        if self._data:
            return self._data.get("merged", False)
        if self._rest_pr:
            return self._rest_pr.merged
        return False

    @property
    def mergeable(self) -> bool | None:
        """
        Return mergeable state.
        GraphQL returns: MERGEABLE, CONFLICTING, UNKNOWN
        PyGithub returns: bool | None (True if mergeable, False if conflicting, None if unknown)
        GraphQL preferred, REST fallback.
        """
        if self._data:
            mergeable = self._data.get("mergeable")
            if mergeable == "MERGEABLE":
                return True
            elif mergeable == "CONFLICTING":
                return False
            else:  # "UNKNOWN" or None
                return None
        if self._rest_pr:
            return self._rest_pr.mergeable
        return None

    @property
    def user(self) -> UserWrapper | Any:
        """
        Get the pull request author - webhook data first (no API calls).
        Priority: webhook payload > GraphQL data > REST API call
        """
        # 1. FIRST: Use webhook payload (fastest, no API call)
        if self._raw_data and "user" in self._raw_data:
            return UserWrapper(self._raw_data["user"])

        # 2. SECOND: Use GraphQL data (if explicitly fetched)
        if self._data and "author" in self._data:
            return UserWrapper(self._data["author"])

        # 3. LAST: Fall back to REST (only when necessary, triggers API call)
        if self._rest_pr:
            return self._rest_pr.user

        return UserWrapper(None)

    @property
    def baseRepository(self) -> RepositoryWrapper | Any:
        """
        Get the base repository directly (for compatibility with handlers).
        This provides direct access to repository info without going through base.repo.
        Priority: webhook payload > GraphQL data > REST API call > constructed wrapper
        """
        # 1. FIRST: Try webhook payload for repository info
        if self._raw_data and "base" in self._raw_data:
            base_data = self._raw_data["base"]
            if "repo" in base_data and isinstance(base_data["repo"], dict):
                repo_data = base_data["repo"]
                owner_login = repo_data.get("owner", {}).get("login", "")
                repo_name = repo_data.get("name", "")
                return RepositoryWrapper(owner_login, repo_name)

        # 2. SECOND: Use constructed repository wrapper from init
        if self._repository:
            return self._repository

        # 3. THIRD: Fall back to REST (triggers API call)
        if self._rest_pr and hasattr(self._rest_pr.base, "repo"):
            return self._rest_pr.base.repo

        # 4. LAST: Return empty repository wrapper
        return RepositoryWrapper()

    @property
    def base(self) -> RefWrapper | Any:
        """
        Get the base (target) branch - webhook data first (no API calls).
        Priority: webhook payload > GraphQL data > REST API call
        """
        # 1. FIRST: Use webhook payload (fastest, no API call)
        if self._raw_data and "base" in self._raw_data:
            return RefWrapper(self._raw_data["base"], self._repository)

        # 2. SECOND: Use GraphQL data (if explicitly fetched)
        if self._data and "baseRef" in self._data:
            return RefWrapper(self._data["baseRef"], self._repository)

        # 3. LAST: Fall back to REST (only when necessary, triggers API call)
        if self._rest_pr:
            return self._rest_pr.base

        return RefWrapper(None, None)

    @property
    def head(self) -> RefWrapper | Any:
        """
        Get the head (source) branch - webhook data first (no API calls).
        Priority: webhook payload > GraphQL data > REST API call
        """
        # 1. FIRST: Use webhook payload (fastest, no API call)
        if self._raw_data and "head" in self._raw_data:
            return RefWrapper(self._raw_data["head"], self._repository)

        # 2. SECOND: Use GraphQL data (if explicitly fetched)
        if self._data and "headRef" in self._data:
            return RefWrapper(self._data["headRef"], self._repository)

        # 3. LAST: Fall back to REST (only when necessary, triggers API call)
        if self._rest_pr:
            return self._rest_pr.head

        return RefWrapper(None, None)

    @property
    def created_at(self) -> datetime | None:
        """
        Parse ISO8601 timestamp from GraphQL or get from REST.
        GraphQL preferred, REST fallback.
        """
        if self._data:
            created = self._data.get("createdAt")
            if created:
                return datetime.fromisoformat(created.replace("Z", "+00:00"))
            return None
        if self._rest_pr:
            return self._rest_pr.created_at
        return None

    @property
    def updated_at(self) -> datetime | None:
        """
        Parse ISO8601 timestamp from GraphQL or get from REST.
        GraphQL preferred, REST fallback.
        """
        if self._data:
            updated = self._data.get("updatedAt")
            if updated:
                return datetime.fromisoformat(updated.replace("Z", "+00:00"))
            return None
        if self._rest_pr:
            return self._rest_pr.updated_at
        return None

    @property
    def closed_at(self) -> datetime | None:
        """
        Parse ISO8601 timestamp from GraphQL or get from REST.
        GraphQL preferred, REST fallback.
        """
        if self._data:
            closed = self._data.get("closedAt")
            if closed:
                return datetime.fromisoformat(closed.replace("Z", "+00:00"))
            return None
        if self._rest_pr:
            return self._rest_pr.closed_at
        return None

    @property
    def merged_at(self) -> datetime | None:
        """
        Parse ISO8601 timestamp from GraphQL or get from REST.
        GraphQL preferred, REST fallback.
        """
        if self._data:
            merged = self._data.get("mergedAt")
            if merged:
                return datetime.fromisoformat(merged.replace("Z", "+00:00"))
            return None
        if self._rest_pr:
            return self._rest_pr.merged_at
        return None

    @property
    def html_url(self) -> str:
        """
        Get the permalink (HTML URL) to the PR.
        GraphQL preferred, REST fallback.
        """
        if self._data:
            return self._data.get("permalink", "")
        if self._rest_pr:
            return self._rest_pr.html_url
        return ""

    @property
    def merge_commit_sha(self) -> str | None:
        """
        Get the merge commit SHA if PR is merged.
        GraphQL preferred, REST fallback.
        """
        if self._data:
            merge_commit = self._data.get("mergeCommit", {})
            if isinstance(merge_commit, dict):
                return merge_commit.get("oid")
            return None
        if self._rest_pr:
            return self._rest_pr.merge_commit_sha
        return None

    @property
    def additions(self) -> int:
        """
        Get number of additions.
        GraphQL preferred, REST fallback.
        """
        if self._data:
            return self._data.get("additions", 0)
        if self._rest_pr:
            return self._rest_pr.additions
        return 0

    @property
    def deletions(self) -> int:
        """
        Get number of deletions.
        GraphQL preferred, REST fallback.
        """
        if self._data:
            return self._data.get("deletions", 0)
        if self._rest_pr:
            return self._rest_pr.deletions
        return 0

    def get_labels(self) -> list[LabelWrapper] | Any:
        """
        Get list of labels attached to the PR - webhook data first (no API calls).
        Priority: webhook payload > GraphQL data > REST API call

        Note: This matches PyGithub's lazy-loading pattern.
        """
        # 1. FIRST: Use webhook payload (fastest, no API call)
        if self._raw_data and "labels" in self._raw_data:
            # Webhook data: labels is a list of label objects
            labels_list = self._raw_data["labels"]
            if isinstance(labels_list, list):
                return [LabelWrapper(label) for label in labels_list]

        # 2. SECOND: Use GraphQL data (if explicitly fetched)
        if self._data and "labels" in self._data:
            labels_data = self._data["labels"]
            nodes = labels_data.get("nodes", [])
            return [LabelWrapper(label) for label in nodes]

        # 3. LAST: Fall back to REST (only when necessary, triggers API call)
        if self._rest_pr:
            return self._rest_pr.get_labels()

        return []

    def get_commits(self) -> list[CommitWrapper] | Any:
        """
        Get list of commits in the PR.

        Note: This matches PyGithub's lazy-loading pattern.
        GraphQL data should already include commits.nodes in the query.
        GraphQL preferred, REST fallback.
        """
        if self._data:
            commits_data = self._data.get("commits", {})
            nodes = commits_data.get("nodes", [])
            # GraphQL commits are nested: nodes[].commit
            return [CommitWrapper(node.get("commit", {})) for node in nodes]
        if self._rest_pr:
            return self._rest_pr.get_commits()
        return []

    @property
    def id(self) -> str:
        """
        Get the GraphQL node ID (used for mutations) or REST ID.
        GraphQL preferred, REST fallback.
        """
        if self._data:
            return self._data.get("id", "")
        if self._rest_pr:
            return str(self._rest_pr.id)
        return ""

    @property
    def labels(self) -> list[LabelWrapper] | Any:
        """
        Property alias for get_labels() to match PyGithub interface.
        GraphQL preferred, REST fallback.
        """
        return self.get_labels()

    @property
    def mergeable_state(self) -> str:
        """
        Get mergeable state.
        GraphQL returns mergeStateStatus: BEHIND, BLOCKED, CLEAN, DIRTY, DRAFT, HAS_HOOKS, UNKNOWN, UNSTABLE
        PyGithub returns mergeable_state: behind, blocked, clean, dirty, draft, has_hooks, unknown, unstable
        GraphQL preferred, REST fallback.
        """
        if self._data:
            state = self._data.get("mergeStateStatus", "UNKNOWN")
            return state.lower()
        if self._rest_pr:
            return self._rest_pr.mergeable_state
        return "unknown"

    def is_merged(self) -> bool:
        """
        Method wrapper for merged property to match PyGithub interface.
        GraphQL preferred, REST fallback.
        """
        return self.merged

    def update_labels(self, labels_nodes: list[dict[str, Any]]) -> None:
        """
        Update labels in-place from mutation response data.

        This method allows updating the wrapper's label data without refetching
        the entire PR from the API, improving performance by using data returned
        from GraphQL mutations.

        Args:
            labels_nodes: List of label nodes from GraphQL mutation response
                         Each node should be a dict with keys: id, name, color

        Example:
            >>> mutation_result = await unified_api.add_labels(pr_id, label_ids)
            >>> updated_labels = mutation_result["addLabelsToLabelable"]["labelable"]["labels"]["nodes"]
            >>> pull_request.update_labels(updated_labels)
        """
        # Update GraphQL data if available
        if self._data:
            self._data["labels"] = {"nodes": labels_nodes}

        # Update webhook data if available (for consistency)
        if self._raw_data:
            self._raw_data["labels"] = labels_nodes

    def __repr__(self) -> str:
        # Use getattr with fallback to handle mock objects safely
        number = getattr(self, "_data", {}).get("number", "?")
        title = getattr(self, "_data", {}).get("title", "?")
        return f"PullRequestWrapper(number={number}, title='{title}')"
