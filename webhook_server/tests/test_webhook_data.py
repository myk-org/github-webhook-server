"""Tests for GraphQL wrapper classes."""

import pytest

from webhook_server.libs.graphql.webhook_data import (
    CommitWrapper,
    LabelWrapper,
    PullRequestWrapper,
    RefWrapper,
    RepositoryWrapper,
    UserWrapper,
)


class TestUserWrapper:
    """Test UserWrapper class."""

    def test_user_wrapper_with_data(self):
        """Test UserWrapper with valid data."""
        data = {"login": "testuser"}
        user = UserWrapper(data)
        assert user.login == "testuser"


class TestRefWrapper:
    """Test RefWrapper class."""

    def test_ref_wrapper_with_data(self):
        """Test RefWrapper with valid data."""
        data = {"name": "main", "target": {"oid": "abc123"}}
        ref = RefWrapper(data)
        assert ref.name == "main"
        assert ref.ref == "main"
        assert ref.sha == "abc123"


class TestLabelWrapper:
    """Test LabelWrapper class."""

    def test_label_wrapper(self):
        """Test LabelWrapper with valid data."""
        data = {"name": "bug"}
        label = LabelWrapper(data)
        assert label.name == "bug"


class TestCommitWrapper:
    """Test CommitWrapper class."""

    def test_commit_wrapper_with_sha(self):
        """Test CommitWrapper with webhook format."""
        data = {"sha": "commit123", "author": {"login": "author1"}}
        commit = CommitWrapper(data)
        assert commit.sha == "commit123"
        assert commit.committer.login == "author1"

    def test_commit_wrapper_fallback_to_author(self):
        """Test CommitWrapper falls back to author when no committer."""
        data = {"sha": "commit123", "author": {"login": "author1"}}
        commit = CommitWrapper(data)
        assert commit.sha == "commit123"
        assert commit.committer.login == "author1"


class TestPullRequestWrapper:
    """Test PullRequestWrapper class."""

    @pytest.fixture
    def pr_data(self):
        """Sample PR data from webhook."""
        return {
            "node_id": "PR_123",
            "number": 42,
            "title": "Test PR",
            "body": "Test body",
            "state": "open",
            "draft": False,
            "merged": False,
            "mergeable": True,
            "user": {"login": "author1"},
            "base": {"ref": "main", "sha": "base123", "repo": {"owner": {"login": "owner"}, "name": "repo"}},
            "head": {"ref": "feature", "sha": "head123", "repo": {"owner": {"login": "owner"}, "name": "repo"}},
            "html_url": "https://github.com/org/repo/pull/42",
            "labels": [{"name": "bug"}],
            "commits": [{"sha": "commit1", "committer": {"login": "dev1"}}],
        }

    def test_basic_properties(self, pr_data):
        """Test basic PR properties."""
        pr = PullRequestWrapper(owner="owner", repo_name="repo", webhook_data=pr_data)
        assert pr.number == 42
        assert pr.title == "Test PR"
        assert pr.body == "Test body"
        assert pr.state == "open"  # Lowercased
        assert pr.draft is False
        assert pr.merged is False
        assert pr.id == "PR_123"

    def test_user_property(self, pr_data):
        """Test user (author) property."""
        pr = PullRequestWrapper(owner="owner", repo_name="repo", webhook_data=pr_data)
        assert pr.user.login == "author1"

    def test_refs_properties(self, pr_data):
        """Test base and head ref properties."""
        pr = PullRequestWrapper(owner="owner", repo_name="repo", webhook_data=pr_data)
        assert pr.base.name == "main"
        assert pr.base.ref == "main"
        assert pr.base.sha == "base123"
        assert pr.head.name == "feature"
        assert pr.head.sha == "head123"

    def test_mergeable_states(self, pr_data):
        """Test mergeable state handling."""
        # Mergeable=True
        pr = PullRequestWrapper(owner="owner", repo_name="repo", webhook_data=pr_data)
        assert pr.mergeable is True

        # Mergeable=None (unknown)
        pr_data["mergeable"] = None
        pr = PullRequestWrapper(owner="owner", repo_name="repo", webhook_data=pr_data)
        assert pr.mergeable is None

        # Mergeable=False (conflicting)
        pr_data["mergeable"] = False
        pr = PullRequestWrapper(owner="owner", repo_name="repo", webhook_data=pr_data)
        assert pr.mergeable is False

    def test_html_url(self, pr_data):
        """Test HTML URL property."""
        pr = PullRequestWrapper(owner="owner", repo_name="repo", webhook_data=pr_data)
        assert pr.html_url == "https://github.com/org/repo/pull/42"

    def test_get_labels(self, pr_data):
        """Test get_labels method."""
        pr = PullRequestWrapper(owner="owner", repo_name="repo", webhook_data=pr_data)
        labels = pr.get_labels()
        assert len(labels) == 1
        assert labels[0].name == "bug"

    def test_get_commits(self, pr_data):
        """Test get_commits method."""
        pr = PullRequestWrapper(owner="owner", repo_name="repo", webhook_data=pr_data)
        commits = pr.get_commits()
        assert len(commits) == 1
        assert commits[0].sha == "commit1"

    def test_repr(self, pr_data):
        """Test string representation."""
        pr = PullRequestWrapper(owner="owner", repo_name="repo", webhook_data=pr_data)
        assert "PullRequestWrapper" in repr(pr)
        assert "42" in repr(pr)
        assert "Test PR" in repr(pr)


def test_pull_request_wrapper_is_merged():
    """Test is_merged property."""
    pr_data = {
        "node_id": "PR_123",
        "number": 1,
        "title": "Test",
        "merged": True,
    }
    wrapper = PullRequestWrapper(owner="test-owner", repo_name="test-repo", webhook_data=pr_data)
    assert wrapper.merged is True


def test_pull_request_wrapper_mergeable_state():
    """Test mergeable_state property."""
    pr_data = {
        "node_id": "PR_123",
        "number": 1,
        "title": "Test",
        "mergeable_state": "clean",
    }
    wrapper = PullRequestWrapper(owner="test-owner", repo_name="test-repo", webhook_data=pr_data)
    assert wrapper.mergeable_state == "clean"

    # Test with behind state
    pr_data["mergeable_state"] = "behind"
    wrapper = PullRequestWrapper(owner="test-owner", repo_name="test-repo", webhook_data=pr_data)
    assert wrapper.mergeable_state == "behind"

    # Test with unknown state (default)
    pr_data.pop("mergeable_state")
    wrapper = PullRequestWrapper(owner="test-owner", repo_name="test-repo", webhook_data=pr_data)
    assert wrapper.mergeable_state == "unknown"


def test_ref_wrapper_without_repository_raises_error():
    """Test RefWrapper without repository info raises AttributeError."""
    ref_data = {"name": "main", "target": {"oid": "abc123"}}

    ref = RefWrapper(ref_data)
    with pytest.raises(AttributeError):
        _ = ref.repo


def test_pull_request_wrapper_missing_author():
    """Test PullRequestWrapper handles missing user gracefully."""
    pr_data = {
        "node_id": "PR_123",
        "number": 1,
        "title": "Test PR",
        # Missing user field
    }
    wrapper = PullRequestWrapper(owner="test-owner", repo_name="test-repo", webhook_data=pr_data)
    # Should raise ValueError for missing user
    with pytest.raises(ValueError, match="No user in webhook data"):
        _ = wrapper.user


def test_commit_wrapper_missing_author():
    """Test CommitWrapper handles missing author gracefully."""
    commit_data = {
        "sha": "abc123",
        # Missing author and committer fields
    }
    wrapper = CommitWrapper(commit_data)
    assert wrapper.sha == "abc123"
    assert wrapper.committer.login == "unknown"


def test_ref_wrapper_missing_target():
    """Test RefWrapper handles missing target gracefully."""
    ref_data = {
        "name": "main",
        # Missing target field (GraphQL format)
    }
    wrapper = RefWrapper(ref_data)
    # Should raise ValueError for missing sha
    with pytest.raises(ValueError, match="No sha/oid in ref data"):
        _ = wrapper.sha


def test_pull_request_wrapper_missing_commits():
    """Test PullRequestWrapper handles missing commits data."""
    pr_data = {
        "node_id": "PR_123",
        "number": 1,
        "title": "Test PR",
        # commits field missing
    }
    wrapper = PullRequestWrapper(owner="test-owner", repo_name="test-repo", webhook_data=pr_data)

    # get_commits should return empty list
    commits = wrapper.get_commits()
    assert commits == []


def test_pull_request_wrapper_empty_commits_nodes():
    """Test PullRequestWrapper handles empty commits array."""
    pr_data = {
        "node_id": "PR_123",
        "number": 1,
        "title": "Test PR",
        "commits": [],  # Empty array (webhook format)
    }
    wrapper = PullRequestWrapper(owner="test-owner", repo_name="test-repo", webhook_data=pr_data)

    commits = wrapper.get_commits()
    assert commits == []


def test_pull_request_wrapper_missing_labels():
    """Test PullRequestWrapper handles missing labels data."""
    pr_data = {
        "node_id": "PR_123",
        "number": 1,
        "title": "Test PR",
        # labels field missing
    }
    wrapper = PullRequestWrapper(owner="test-owner", repo_name="test-repo", webhook_data=pr_data)

    # get_labels should return empty list
    labels = wrapper.get_labels()
    assert labels == []


def test_pull_request_wrapper_empty_labels_nodes():
    """Test PullRequestWrapper handles empty labels array."""
    pr_data = {
        "node_id": "PR_123",
        "number": 1,
        "title": "Test PR",
        "labels": [],  # Empty array (webhook format)
    }
    wrapper = PullRequestWrapper(owner="test-owner", repo_name="test-repo", webhook_data=pr_data)

    labels = wrapper.get_labels()
    assert labels == []


def test_user_wrapper_empty_dict():
    """Test UserWrapper handles empty dict with ValueError."""
    wrapper = UserWrapper({})

    # Should raise ValueError for missing login
    with pytest.raises(ValueError, match="No login in user data"):
        _ = wrapper.login


def test_user_wrapper_type_property():
    """Test UserWrapper.type property with webhook type field."""
    data = {"type": "Bot", "login": "bot-user"}
    wrapper = UserWrapper(data)
    assert wrapper.type == "Bot"


def test_user_wrapper_type_default():
    """Test UserWrapper.type property default value."""
    data = {"login": "regular-user"}
    wrapper = UserWrapper(data)
    assert wrapper.type == "User"


def test_ref_wrapper_missing_name():
    """Test RefWrapper handles missing name field with ValueError."""

    repo = RepositoryWrapper("test-owner", "test-repo")
    ref_data = {
        # name field missing
        "target": {"oid": "abc123"}
    }
    wrapper = RefWrapper(ref_data, repo)

    # Should raise ValueError for missing name
    with pytest.raises(ValueError, match="No name/ref in ref data"):
        _ = wrapper.name


def test_ref_wrapper_missing_target_with_repo():
    """Test RefWrapper handles missing target field with ValueError."""
    repo = RepositoryWrapper("test-owner", "test-repo")
    ref_data = {
        "name": "main",
        # target field missing
    }
    wrapper = RefWrapper(ref_data, repo)

    # Should raise ValueError for missing sha
    with pytest.raises(ValueError, match="No sha/oid in ref data"):
        _ = wrapper.sha


def test_label_wrapper_missing_name():
    """Test LabelWrapper handles missing name field with ValueError."""
    label_data = {}  # No name field
    wrapper = LabelWrapper(label_data)

    # Should raise ValueError for missing name
    with pytest.raises(ValueError, match="No name in label data"):
        _ = wrapper.name


def test_commit_wrapper_fallback_to_author():
    """Test CommitWrapper falls back to author when committer missing."""
    commit_data = {
        "sha": "abc123",
        # No committer field
        "author": {"login": "author-user"},
    }
    wrapper = CommitWrapper(commit_data)

    committer = wrapper.committer
    assert committer.login == "author-user"


def test_pull_request_wrapper_mergeable_none():
    """Test PullRequestWrapper.mergeable returns None."""
    pr_data = {"number": 1, "title": "Test", "mergeable": None}
    wrapper = PullRequestWrapper(owner="test-owner", repo_name="test-repo", webhook_data=pr_data)

    assert wrapper.mergeable is None


def test_repository_wrapper_missing_owner():
    """Test RepositoryWrapper with owner and name parameters."""

    # RepositoryWrapper now takes (owner, name) parameters
    wrapper = RepositoryWrapper("test-owner", "test-repo")

    # Should handle properly
    assert wrapper.name == "test-repo"
    assert wrapper.owner.login == "test-owner"


def test_pull_request_wrapper_webhook_data_bot_user():
    """Test PullRequestWrapper with webhook_data preserves bot user login with [bot] suffix.

    This test verifies bot user login is correctly preserved in webhook format.
    """
    # Webhook payload (user field from webhook has full login with [bot] suffix)
    webhook_data = {
        "number": 123,
        "user": {
            "login": "pre-commit-ci[bot]",  # Webhook user login (with [bot])
            "id": 66853113,
            "node_id": "MDM6Qm90NjY4NTMxMTM=",
            "type": "Bot",
        },
    }

    # Create wrapper with webhook data
    wrapper = PullRequestWrapper(owner="test-owner", repo_name="test-repo", webhook_data=webhook_data)

    # Verify user.login uses webhook data (with [bot] suffix)
    assert wrapper.user.login == "pre-commit-ci[bot]"


def test_pull_request_wrapper_base_repository_webhook_data():
    """Test baseRepository property with webhook data."""
    webhook_data = {
        "base": {
            "ref": "main",
            "sha": "abc123",
            "repo": {
                "name": "test-repo",
                "owner": {"login": "test-owner"},
            },
        }
    }

    wrapper = PullRequestWrapper(owner="test-owner", repo_name="test-repo", webhook_data=webhook_data)

    # Should extract from webhook payload
    assert wrapper.baseRepository.name == "test-repo"
    assert wrapper.baseRepository.owner.login == "test-owner"


def test_pull_request_wrapper_base_repository_webhook_priority():
    """Test baseRepository prioritizes webhook data over constructed wrapper."""
    webhook_data = {
        "base": {
            "ref": "main",
            "sha": "abc123",
            "repo": {
                "name": "webhook-repo",
                "owner": {"login": "webhook-owner"},
            },
        }
    }

    # Even though we provide owner/repo_name, webhook data should take priority
    wrapper = PullRequestWrapper(webhook_data=webhook_data, owner="constructed-owner", repo_name="constructed-repo")

    # Should use webhook data (higher priority)
    assert wrapper.baseRepository.name == "webhook-repo"
    assert wrapper.baseRepository.owner.login == "webhook-owner"


def test_user_wrapper_node_id_property():
    """Test UserWrapper.node_id property returns GraphQL node ID."""
    data = {"login": "testuser", "node_id": "MDM6Qm90NjY4NTMxMTM="}
    user = UserWrapper(data)
    assert user.node_id == "MDM6Qm90NjY4NTMxMTM="


def test_user_wrapper_node_id_default():
    """Test UserWrapper.node_id returns empty string when missing."""
    data = {"login": "testuser"}
    user = UserWrapper(data)
    assert user.node_id == ""


def test_ref_wrapper_webhook_format():
    """Test RefWrapper handles webhook format (ref + sha fields)."""
    # Webhook format has "ref" and "sha" fields directly
    ref_data = {"ref": "feature-branch", "sha": "webhook123"}
    ref = RefWrapper(ref_data)
    assert ref.name == "feature-branch"  # Uses "ref" field
    assert ref.ref == "feature-branch"
    assert ref.sha == "webhook123"  # Uses "sha" field directly


def test_ref_wrapper_graphql_format_with_name():
    """Test RefWrapper handles GraphQL format correctly."""
    # GraphQL format: "name" field instead of "ref"
    ref_data = {"name": "main", "target": {"oid": "graphql123"}}
    ref = RefWrapper(ref_data)
    assert ref.name == "main"  # Uses "name" field
    assert ref.ref == "main"
    assert ref.sha == "graphql123"  # Uses "target.oid"


def test_pull_request_wrapper_webhook_data_node_id():
    """Test PullRequestWrapper uses node_id for GraphQL mutations.

    This test verifies that the wrapper correctly uses the webhook's node_id field
    for GraphQL mutations.
    """
    # Webhook payload with node_id (GraphQL node ID)
    webhook_data = {
        "number": 123,
        "node_id": "PR_kwDOABcdef123",  # GraphQL node ID (base64)
        "user": {
            "login": "test-user",
            "id": 12345,
            "node_id": "MDQ6VXNlcjEyMzQ1",
        },
    }

    # Create wrapper with webhook data
    wrapper = PullRequestWrapper(
        owner="test-owner",
        repo_name="test-repo",
        webhook_data=webhook_data,
    )

    # Verify the id property returns GraphQL node_id
    assert wrapper.id == "PR_kwDOABcdef123"
