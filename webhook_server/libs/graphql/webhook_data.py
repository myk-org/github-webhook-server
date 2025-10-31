"""
GitHub webhook data wrappers.

This module contains wrapper classes that provide a consistent interface
for working with GitHub webhook payloads across handler code.
"""

from __future__ import annotations

from typing import Any


class UserWrapper:
    """Wrapper for GitHub user data from GraphQL responses."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    @property
    def login(self) -> str:
        if "login" not in self._data:
            raise ValueError("No login in user data")
        return self._data["login"]

    @property
    def type(self) -> str:
        """
        Returns: "User", "Bot", "Organization", etc.
        """
        return self._data.get("type", "User")

    @property
    def node_id(self) -> str:
        """
        Get user node_id (GraphQL global ID) from webhook data.
        Returns: GraphQL node ID (e.g., "MDM6Qm90NjY4NTMxMTM=") from webhook payload.
        This avoids the need to make a GraphQL query for bot accounts.
        """
        if "node_id" not in self._data:
            return ""
        return self._data["node_id"]


class RepositoryWrapper:
    """Minimal wrapper for repository information."""

    def __init__(self, owner: str, name: str):
        """
        Initialize RepositoryWrapper.

        Args:
            owner: Repository owner login (required)
            name: Repository name (required)
        """
        self._owner = owner
        self._name = name

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

    def __init__(self, data: dict[str, Any], repository: RepositoryWrapper | None = None):
        self._data = data
        self._repository = repository

    @property
    def name(self) -> str:
        """
        Get branch name.
        Webhook format: {"ref": "branch-name", "sha": "..."}
        GraphQL format: {"name": "branch-name", "target": {"oid": "..."}}
        """
        if "ref" in self._data and "sha" in self._data:
            if not self._data["ref"]:
                raise ValueError("Empty ref in webhook data")
            return self._data["ref"]
        if "name" not in self._data:
            raise ValueError("No name/ref in ref data - webhook/GraphQL incomplete")
        return self._data["name"]

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
        if "sha" in self._data:
            if not self._data["sha"]:
                raise ValueError("Empty sha in webhook data")
            return self._data["sha"]
        target = self._data.get("target")
        if not target or "oid" not in target:
            raise ValueError("No sha/oid in ref data - webhook/GraphQL incomplete")
        return target["oid"]


class LabelWrapper:
    """Wrapper for GitHub label data from GraphQL responses."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    @property
    def name(self) -> str:
        if "name" not in self._data:
            raise ValueError("No name in label data - webhook/GraphQL incomplete")
        return self._data["name"]

    @property
    def id(self) -> str:
        if "id" not in self._data:
            raise ValueError("No id in label data")
        return self._data["id"]


class CommitWrapper:
    """Wrapper for GitHub commit data from webhook payloads."""

    def __init__(self, data: dict[str, Any]):
        if not data:
            raise ValueError("CommitWrapper requires non-empty data dict - webhook data missing")
        self._data = data

    @property
    def sha(self) -> str:
        if "sha" not in self._data:
            raise ValueError("No sha in commit data - webhook incomplete")
        return self._data["sha"]

    @property
    def committer(self) -> UserWrapper:
        """Get committer information."""
        data: dict[str, str] = {"login": "unknown"}

        committer_data = self._data.get("committer") or {}
        committer = committer_data.get("login", committer_data.get("username"))
        if committer:
            data["login"] = committer

        author_data = self._data.get("author") or {}
        author = author_data.get("login", author_data.get("username"))
        if author:
            data["login"] = author

        return UserWrapper(data=data)


class PullRequestWrapper:
    """
    Wrapper for GitHub pull request data from webhook payloads.

    Provides a consistent interface for working with pull request data
    from GitHub webhook payloads.

    Args:
        owner: Repository owner login (required)
        repo_name: Repository name (required)
        webhook_data: GitHub webhook payload with REST field names (required)
                     Example: {"node_id": "PR_kwABC", "number": 123, "base": {...}, "user": {...}}
                     Contains complete PR data from webhook event
    """

    def __init__(
        self,
        owner: str,
        repo_name: str,
        webhook_data: dict[str, Any],
    ):
        self._owner = owner
        self._repo_name = repo_name
        self.webhook_data: dict[str, Any] = webhook_data
        self._repository = RepositoryWrapper(owner, repo_name)

    @property
    def number(self) -> int:
        """Get PR number from webhook data."""
        if "number" not in self.webhook_data:
            raise ValueError("No number in webhook data")
        return self.webhook_data["number"]

    @property
    def title(self) -> str:
        """Get PR title from webhook data."""
        if "title" not in self.webhook_data:
            raise ValueError("No title in webhook data")
        return self.webhook_data["title"]

    @property
    def body(self) -> str | None:
        """Get PR body/description from webhook data."""
        return self.webhook_data.get("body")  # Body can legitimately be None

    @property
    def state(self) -> str:
        """Return state in lowercase to match PyGithub (open/closed)."""
        if "state" in self.webhook_data:
            return self.webhook_data["state"].lower()
        # Default to "open" - most common state for webhook processing
        return "open"

    @property
    def draft(self) -> bool:
        """Get draft status from webhook data."""
        return self.webhook_data.get("draft", False)

    @property
    def merged(self) -> bool:
        """Get merged status from webhook data."""
        return self.webhook_data.get("merged", False)

    @property
    def mergeable(self) -> bool | None:
        """
        Return mergeable state.
        Webhook returns: bool | None (True if mergeable, False if conflicting, None if unknown)
        """
        return self.webhook_data.get("mergeable")

    @property
    def user(self) -> UserWrapper | Any:
        """Get the pull request author from webhook data."""
        if "user" not in self.webhook_data:
            raise ValueError("No user in webhook data")
        return UserWrapper(self.webhook_data["user"])

    @property
    def baseRepository(self) -> RepositoryWrapper:
        """
        Get the base repository from webhook data.
        This provides direct access to repository info without going through base.repo.
        """
        # PR webhook always contains base.repo structure
        base_data = self.webhook_data["base"]
        repo_data = base_data["repo"]
        owner_login = repo_data["owner"]["login"]
        repo_name = repo_data["name"]
        return RepositoryWrapper(owner_login, repo_name)

    @property
    def base(self) -> RefWrapper | Any:
        """Get the base (target) branch from webhook data."""
        if "base" not in self.webhook_data:
            raise ValueError("No base in webhook data")
        return RefWrapper(self.webhook_data["base"], self._repository)

    @property
    def head(self) -> RefWrapper | Any:
        """Get the head (source) branch from webhook data."""
        if "head" not in self.webhook_data:
            raise ValueError("No head in webhook data")
        return RefWrapper(self.webhook_data["head"], self._repository)

    @property
    def html_url(self) -> str:
        """Get the permalink (HTML URL) to the PR from webhook data."""
        if "html_url" not in self.webhook_data:
            raise ValueError("No html_url in webhook data")
        return self.webhook_data["html_url"]

    @property
    def merge_commit_sha(self) -> str | None:
        """Get the merge commit SHA if PR is merged from webhook data."""
        return self.webhook_data.get("merge_commit_sha")

    @property
    def additions(self) -> int:
        """Get number of additions from webhook data."""
        return self.webhook_data.get("additions", 0)

    @property
    def deletions(self) -> int:
        """Get number of deletions from webhook data."""
        return self.webhook_data.get("deletions", 0)

    def get_labels(self) -> list[LabelWrapper]:
        """
        Get list of labels attached to the PR from webhook data.
        Note: This matches PyGithub's lazy-loading pattern.
        """
        labels_list = self.webhook_data.get("labels", [])
        return [LabelWrapper(label) for label in labels_list]

    def get_commits(self) -> list[CommitWrapper]:
        """
        Get list of commits in the PR from webhook data.
        Note: This matches PyGithub's lazy-loading pattern.
        """
        commits_data = self.webhook_data.get("commits", [])
        if not isinstance(commits_data, list):
            return []

        return [CommitWrapper(commit) for commit in commits_data]

    @property
    def id(self) -> str:
        """Get the GraphQL node ID (used for mutations) from webhook data."""
        if "node_id" not in self.webhook_data:
            raise ValueError("No node_id in webhook data")
        return self.webhook_data["node_id"]

    @property
    def mergeable_state(self) -> str:
        """
        Get mergeable state from webhook data.
        Returns: behind, blocked, clean, dirty, draft, has_hooks, unknown, unstable
        """
        return self.webhook_data.get("mergeable_state", "unknown")

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
        self.webhook_data["labels"] = labels_nodes

    def __repr__(self) -> str:
        number = self.webhook_data.get("number", "?")
        title = self.webhook_data.get("title", "?")
        return f"PullRequestWrapper(number={number}, title='{title}')"
