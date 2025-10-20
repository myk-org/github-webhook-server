"""
GraphQL response wrappers that provide PyGithub-compatible interfaces.

This module contains wrapper classes that make GraphQL dictionary responses
behave like PyGithub objects, enabling gradual migration without breaking
existing handler code.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


class UserWrapper:
    """Wrapper for GitHub user data from GraphQL responses."""

    def __init__(self, data: dict[str, Any] | None):
        self._data = data or {}

    @property
    def login(self) -> str:
        return self._data.get("login", "")


class RefWrapper:
    """Wrapper for GitHub ref (branch) data from GraphQL responses."""

    def __init__(self, data: dict[str, Any] | None):
        self._data = data or {}

    @property
    def name(self) -> str:
        return self._data.get("name", "")

    @property
    def ref(self) -> str:
        """Alias for name to match PyGithub interface."""
        return self.name

    @property
    def sha(self) -> str:
        """Get the commit SHA from target.oid."""
        target = self._data.get("target", {})
        return target.get("oid", "")


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
        commit_data = self._data.get("commit", {})
        committer_data = commit_data.get("committer", {})
        # GraphQL returns author info differently
        if "user" in committer_data:
            return UserWrapper(committer_data["user"])
        return UserWrapper({"login": committer_data.get("name", "")})


class PullRequestWrapper:
    """
    Wrapper for GitHub pull request data from GraphQL responses.

    Provides a PyGithub-compatible interface for PullRequest objects,
    allowing existing handler code to work unchanged while using
    GraphQL responses internally.
    """

    def __init__(self, data: dict[str, Any]):
        self._data = data

    @property
    def raw_data(self) -> dict[str, Any]:
        """Get raw data dict for compatibility."""
        return self._data

    @property
    def number(self) -> int:
        return self._data.get("number", 0)

    @property
    def title(self) -> str:
        return self._data.get("title", "")

    @property
    def body(self) -> str | None:
        return self._data.get("body")

    @property
    def state(self) -> str:
        """Return state in lowercase to match PyGithub (open/closed)."""
        state = self._data.get("state", "OPEN")
        return state.lower()

    @property
    def draft(self) -> bool:
        return self._data.get("isDraft", False)

    @property
    def merged(self) -> bool:
        return self._data.get("merged", False)

    @property
    def mergeable(self) -> str | None:
        """
        Return mergeable state.
        GraphQL returns: MERGEABLE, CONFLICTING, UNKNOWN
        PyGithub returns: None if unknown, otherwise string
        """
        mergeable = self._data.get("mergeable")
        if mergeable == "UNKNOWN":
            return None
        return mergeable

    @property
    def user(self) -> UserWrapper:
        """Get the pull request author."""
        return UserWrapper(self._data.get("author"))

    @property
    def base(self) -> RefWrapper:
        """Get the base (target) branch."""
        return RefWrapper(self._data.get("baseRef"))

    @property
    def head(self) -> RefWrapper:
        """Get the head (source) branch."""
        return RefWrapper(self._data.get("headRef"))

    @property
    def created_at(self) -> datetime | None:
        """Parse ISO8601 timestamp from GraphQL."""
        created = self._data.get("createdAt")
        if created:
            return datetime.fromisoformat(created.replace("Z", "+00:00"))
        return None

    @property
    def updated_at(self) -> datetime | None:
        """Parse ISO8601 timestamp from GraphQL."""
        updated = self._data.get("updatedAt")
        if updated:
            return datetime.fromisoformat(updated.replace("Z", "+00:00"))
        return None

    @property
    def closed_at(self) -> datetime | None:
        """Parse ISO8601 timestamp from GraphQL."""
        closed = self._data.get("closedAt")
        if closed:
            return datetime.fromisoformat(closed.replace("Z", "+00:00"))
        return None

    @property
    def merged_at(self) -> datetime | None:
        """Parse ISO8601 timestamp from GraphQL."""
        merged = self._data.get("mergedAt")
        if merged:
            return datetime.fromisoformat(merged.replace("Z", "+00:00"))
        return None

    @property
    def html_url(self) -> str:
        """Get the permalink (HTML URL) to the PR."""
        return self._data.get("permalink", "")

    @property
    def additions(self) -> int:
        """Get number of additions."""
        return self._data.get("additions", 0)

    @property
    def deletions(self) -> int:
        """Get number of deletions."""
        return self._data.get("deletions", 0)

    def get_labels(self) -> list[LabelWrapper]:
        """
        Get list of labels attached to the PR.

        Note: This matches PyGithub's lazy-loading pattern.
        GraphQL data should already include labels.nodes in the query.
        """
        labels_data = self._data.get("labels", {})
        nodes = labels_data.get("nodes", [])
        return [LabelWrapper(label) for label in nodes]

    def get_commits(self) -> list[CommitWrapper]:
        """
        Get list of commits in the PR.

        Note: This matches PyGithub's lazy-loading pattern.
        GraphQL data should already include commits.nodes in the query.
        """
        commits_data = self._data.get("commits", {})
        nodes = commits_data.get("nodes", [])
        # GraphQL commits are nested: nodes[].commit
        return [CommitWrapper(node.get("commit", {})) for node in nodes]

    @property
    def id(self) -> str:
        """Get the GraphQL node ID (used for mutations)."""
        return self._data.get("id", "")

    def __repr__(self) -> str:
        return f"PullRequestWrapper(number={self.number}, title='{self.title}')"
