"""Tests for GraphQL wrapper classes."""

import pytest

from webhook_server.libs.graphql.graphql_wrappers import (
    CommitWrapper,
    LabelWrapper,
    PullRequestWrapper,
    RefWrapper,
    UserWrapper,
)


class TestUserWrapper:
    """Test UserWrapper class."""

    def test_user_wrapper_with_data(self):
        """Test UserWrapper with valid data."""
        data = {"login": "testuser"}
        user = UserWrapper(data)
        assert user.login == "testuser"

    def test_user_wrapper_empty(self):
        """Test UserWrapper with None data."""
        user = UserWrapper(None)
        assert user.login == ""


class TestRefWrapper:
    """Test RefWrapper class."""

    def test_ref_wrapper_with_data(self):
        """Test RefWrapper with valid data."""
        data = {"name": "main", "target": {"oid": "abc123"}}
        ref = RefWrapper(data)
        assert ref.name == "main"
        assert ref.ref == "main"
        assert ref.sha == "abc123"

    def test_ref_wrapper_empty(self):
        """Test RefWrapper with None data."""
        ref = RefWrapper(None)
        assert ref.name == ""
        assert ref.sha == ""


class TestLabelWrapper:
    """Test LabelWrapper class."""

    def test_label_wrapper(self):
        """Test LabelWrapper with valid data."""
        data = {"id": "label123", "name": "bug", "color": "d73a4a"}
        label = LabelWrapper(data)
        assert label.id == "label123"
        assert label.name == "bug"
        assert label.color == "d73a4a"


class TestCommitWrapper:
    """Test CommitWrapper class."""

    def test_commit_wrapper_with_sha(self):
        """Test CommitWrapper with commit SHA."""
        data = {"oid": "commit123", "commit": {"committer": {"user": {"login": "committer1"}}}}
        commit = CommitWrapper(data)
        assert commit.sha == "commit123"
        assert commit.committer.login == "committer1"

    def test_commit_wrapper_fallback_committer(self):
        """Test CommitWrapper with fallback committer name."""
        data = {"oid": "commit123", "commit": {"committer": {"name": "Committer Name"}}}
        commit = CommitWrapper(data)
        assert commit.sha == "commit123"
        assert commit.committer.login == "Committer Name"


class TestPullRequestWrapper:
    """Test PullRequestWrapper class."""

    @pytest.fixture
    def pr_data(self):
        """Sample PR data from GraphQL."""
        return {
            "id": "PR_123",
            "number": 42,
            "title": "Test PR",
            "body": "Test body",
            "state": "OPEN",
            "isDraft": False,
            "merged": False,
            "mergeable": "MERGEABLE",
            "author": {"login": "author1"},
            "baseRef": {"name": "main", "target": {"oid": "base123"}},
            "headRef": {"name": "feature", "target": {"oid": "head123"}},
            "createdAt": "2023-01-01T10:00:00Z",
            "updatedAt": "2023-01-02T10:00:00Z",
            "closedAt": None,
            "mergedAt": None,
            "permalink": "https://github.com/org/repo/pull/42",
            "labels": {"nodes": [{"id": "L1", "name": "bug", "color": "d73a4a"}]},
            "commits": {
                "nodes": [{"commit": {"oid": "commit1", "commit": {"committer": {"user": {"login": "dev1"}}}}}]
            },
        }

    def test_basic_properties(self, pr_data):
        """Test basic PR properties."""
        pr = PullRequestWrapper(pr_data)
        assert pr.number == 42
        assert pr.title == "Test PR"
        assert pr.body == "Test body"
        assert pr.state == "open"  # Lowercased
        assert pr.draft is False
        assert pr.merged is False
        assert pr.id == "PR_123"

    def test_user_property(self, pr_data):
        """Test user (author) property."""
        pr = PullRequestWrapper(pr_data)
        assert pr.user.login == "author1"

    def test_refs_properties(self, pr_data):
        """Test base and head ref properties."""
        pr = PullRequestWrapper(pr_data)
        assert pr.base.name == "main"
        assert pr.base.ref == "main"
        assert pr.base.sha == "base123"
        assert pr.head.name == "feature"
        assert pr.head.sha == "head123"

    def test_mergeable_states(self, pr_data):
        """Test mergeable state handling."""
        # MERGEABLE state
        pr = PullRequestWrapper(pr_data)
        assert pr.mergeable == "MERGEABLE"

        # UNKNOWN state returns None
        pr_data["mergeable"] = "UNKNOWN"
        pr = PullRequestWrapper(pr_data)
        assert pr.mergeable is None

        # CONFLICTING state
        pr_data["mergeable"] = "CONFLICTING"
        pr = PullRequestWrapper(pr_data)
        assert pr.mergeable == "CONFLICTING"

    def test_timestamps(self, pr_data):
        """Test timestamp parsing."""
        pr = PullRequestWrapper(pr_data)
        assert pr.created_at is not None
        assert pr.updated_at is not None
        assert pr.closed_at is None
        assert pr.merged_at is None

    def test_html_url(self, pr_data):
        """Test HTML URL (permalink) property."""
        pr = PullRequestWrapper(pr_data)
        assert pr.html_url == "https://github.com/org/repo/pull/42"

    def test_get_labels(self, pr_data):
        """Test get_labels method."""
        pr = PullRequestWrapper(pr_data)
        labels = pr.get_labels()
        assert len(labels) == 1
        assert labels[0].name == "bug"
        assert labels[0].color == "d73a4a"

    def test_get_commits(self, pr_data):
        """Test get_commits method."""
        pr = PullRequestWrapper(pr_data)
        commits = pr.get_commits()
        assert len(commits) == 1
        assert commits[0].sha == "commit1"

    def test_repr(self, pr_data):
        """Test string representation."""
        pr = PullRequestWrapper(pr_data)
        assert "PullRequestWrapper" in repr(pr)
        assert "42" in repr(pr)
        assert "Test PR" in repr(pr)


def test_pull_request_wrapper_is_merged():
    """Test is_merged property."""
    pr_data = {
        "id": "PR_123",
        "number": 1,
        "title": "Test",
        "merged": True,
    }
    wrapper = PullRequestWrapper(pr_data)
    assert wrapper.merged is True


def test_pull_request_wrapper_mergeable_state():
    """Test mergeable_state property."""
    pr_data = {
        "id": "PR_123",
        "number": 1,
        "title": "Test",
        "mergeable": "MERGEABLE",
    }
    wrapper = PullRequestWrapper(pr_data)
    assert wrapper.mergeable == "MERGEABLE"
