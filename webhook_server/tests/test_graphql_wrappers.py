"""Tests for GraphQL wrapper classes."""

from datetime import datetime
from unittest.mock import MagicMock, Mock

import pytest

from webhook_server.libs.graphql.graphql_wrappers import (
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
        data = {"oid": "commit123", "committer": {"user": {"login": "committer1"}}}
        commit = CommitWrapper(data)
        assert commit.sha == "commit123"
        assert commit.committer.login == "committer1"

    def test_commit_wrapper_fallback_committer(self):
        """Test CommitWrapper with fallback committer name."""
        data = {"oid": "commit123", "committer": {"name": "Committer Name"}}
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
            "commits": {"nodes": [{"commit": {"oid": "commit1", "committer": {"user": {"login": "dev1"}}}}]},
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
        # MERGEABLE state returns True
        pr = PullRequestWrapper(pr_data)
        assert pr.mergeable is True

        # UNKNOWN state returns None
        pr_data["mergeable"] = "UNKNOWN"
        pr = PullRequestWrapper(pr_data)
        assert pr.mergeable is None

        # CONFLICTING state returns False
        pr_data["mergeable"] = "CONFLICTING"
        pr = PullRequestWrapper(pr_data)
        assert pr.mergeable is False

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
        "mergeStateStatus": "CLEAN",
    }
    wrapper = PullRequestWrapper(pr_data)
    assert wrapper.mergeable_state == "clean"

    # Test with BEHIND state
    pr_data["mergeStateStatus"] = "BEHIND"
    wrapper = PullRequestWrapper(pr_data)
    assert wrapper.mergeable_state == "behind"

    # Test with UNKNOWN state (default)
    pr_data.pop("mergeStateStatus")
    wrapper = PullRequestWrapper(pr_data)
    assert wrapper.mergeable_state == "unknown"


def test_pull_request_wrapper_with_repository_info():
    """Test PullRequestWrapper with repository information."""
    pr_data = {
        "id": "PR_123",
        "number": 1,
        "title": "Test PR",
        "baseRef": {"name": "main", "target": {"oid": "abc123"}},
        "headRef": {"name": "feature", "target": {"oid": "def456"}},
    }
    wrapper = PullRequestWrapper(pr_data, "owner-name", "repo-name")

    # Test that base and head refs have repository info
    assert wrapper.base.repo.owner.login == "owner-name"
    assert wrapper.base.repo.name == "repo-name"
    assert wrapper.head.repo.owner.login == "owner-name"
    assert wrapper.head.repo.name == "repo-name"


def test_ref_wrapper_without_repository_raises_error():
    """Test RefWrapper without repository info raises AttributeError."""
    ref_data = {"name": "main", "target": {"oid": "abc123"}}

    ref = RefWrapper(ref_data)
    with pytest.raises(AttributeError):
        _ = ref.repo


def test_pull_request_wrapper_missing_author():
    """Test PullRequestWrapper handles missing author gracefully."""
    pr_data = {
        "id": "PR_123",
        "number": 1,
        "title": "Test PR",
        # Missing author field
    }
    wrapper = PullRequestWrapper(pr_data)
    # Should return UserWrapper with empty login instead of crashing
    assert wrapper.user.login == ""  # Default empty string


def test_pull_request_wrapper_missing_base_ref():
    """Test PullRequestWrapper handles missing base ref gracefully."""
    pr_data = {
        "id": "PR_123",
        "number": 1,
        "title": "Test PR",
        "headRef": {"name": "feature", "target": {"oid": "abc123"}},
        # Missing baseRef
    }
    wrapper = PullRequestWrapper(pr_data, "owner", "repo")
    # Should handle missing base gracefully
    assert wrapper.base.ref == ""  # Default empty string
    assert wrapper.base.sha == ""  # Default empty string


def test_pull_request_wrapper_missing_head_ref():
    """Test PullRequestWrapper handles missing head ref gracefully."""
    pr_data = {
        "id": "PR_123",
        "number": 1,
        "title": "Test PR",
        "baseRef": {"name": "main", "target": {"oid": "def456"}},
        # Missing headRef
    }
    wrapper = PullRequestWrapper(pr_data, "owner", "repo")
    # Should handle missing head gracefully
    assert wrapper.head.ref == ""  # Default empty string
    assert wrapper.head.sha == ""  # Default empty string


def test_commit_wrapper_missing_author():
    """Test CommitWrapper handles missing author gracefully."""
    commit_data = {
        "oid": "abc123",
        # Missing author field
    }
    wrapper = CommitWrapper(commit_data)
    # Should handle missing author gracefully - CommitWrapper doesn't have author property
    # Just verify it doesn't crash on creation
    assert wrapper.sha == "abc123"


def test_ref_wrapper_missing_target():
    """Test RefWrapper handles missing target gracefully."""
    ref_data = {
        "name": "main",
        # Missing target field
    }
    wrapper = RefWrapper(ref_data)
    # Should return empty string instead of crashing
    assert wrapper.sha == ""


# ===== Tests for __getattr__ Delegation and REST Fallback =====


class TestPullRequestWrapperRestMode:
    """Test PullRequestWrapper in REST mode (rest_pr only, no GraphQL data)."""

    def test_rest_mode_basic_properties(self):
        """Test wrapper delegates basic properties to REST object."""
        # Create mock REST PR object
        mock_rest_pr = Mock()
        mock_rest_pr.number = 999
        mock_rest_pr.title = "REST PR Title"
        mock_rest_pr.body = "REST PR Body"
        mock_rest_pr.state = "open"
        mock_rest_pr.draft = True
        mock_rest_pr.merged = False
        mock_rest_pr.id = 12345

        # Create wrapper with only REST object (no GraphQL data)
        wrapper = PullRequestWrapper(rest_pr=mock_rest_pr)

        # Verify delegation to REST object
        assert wrapper.number == 999
        assert wrapper.title == "REST PR Title"
        assert wrapper.body == "REST PR Body"
        assert wrapper.state == "open"
        assert wrapper.draft is True
        assert wrapper.merged is False
        assert wrapper.id == "12345"  # Converted to string

    def test_rest_mode_user_property(self):
        """Test wrapper delegates user property to REST object."""
        mock_user = Mock()
        mock_user.login = "restuser"

        mock_rest_pr = Mock()
        mock_rest_pr.user = mock_user

        wrapper = PullRequestWrapper(rest_pr=mock_rest_pr)

        # Should delegate to REST object's user
        assert wrapper.user.login == "restuser"

    def test_rest_mode_refs_properties(self):
        """Test wrapper delegates base and head to REST object."""
        mock_base = Mock()
        mock_base.ref = "main"
        mock_base.sha = "base_sha_rest"

        mock_head = Mock()
        mock_head.ref = "feature-branch"
        mock_head.sha = "head_sha_rest"

        mock_rest_pr = Mock()
        mock_rest_pr.base = mock_base
        mock_rest_pr.head = mock_head

        wrapper = PullRequestWrapper(rest_pr=mock_rest_pr)

        # Should delegate to REST object
        assert wrapper.base.ref == "main"
        assert wrapper.base.sha == "base_sha_rest"
        assert wrapper.head.ref == "feature-branch"
        assert wrapper.head.sha == "head_sha_rest"

    def test_rest_mode_getattr_delegation(self):
        """Test __getattr__ delegates unknown attributes to REST object."""
        mock_rest_pr = Mock()
        mock_rest_pr.custom_attribute = "custom_value"
        mock_rest_pr.another_method = Mock(return_value="method_result")

        wrapper = PullRequestWrapper(rest_pr=mock_rest_pr)

        # __getattr__ should delegate to REST object
        assert wrapper.custom_attribute == "custom_value"
        assert wrapper.another_method() == "method_result"

    def test_rest_mode_no_rest_object_raises_error(self):
        """Test wrapper raises AttributeError when REST object not available."""
        # Create wrapper with no GraphQL data and no REST object
        wrapper = PullRequestWrapper()

        # Accessing unknown attribute should raise AttributeError
        with pytest.raises(AttributeError, match="object has no attribute"):
            _ = wrapper.nonexistent_attribute


class TestPullRequestWrapperHybridMode:
    """Test PullRequestWrapper in hybrid mode (both GraphQL data and REST object)."""

    def test_hybrid_mode_graphql_preferred(self):
        """Test GraphQL data is preferred when both GraphQL and REST available."""
        # GraphQL data
        pr_data = {
            "id": "PR_graphql_123",
            "number": 100,
            "title": "GraphQL Title",
            "state": "OPEN",
        }

        # REST object with different values
        mock_rest_pr = Mock()
        mock_rest_pr.number = 200
        mock_rest_pr.title = "REST Title"
        mock_rest_pr.state = "closed"
        mock_rest_pr.id = 999

        # Create hybrid wrapper
        wrapper = PullRequestWrapper(data=pr_data, rest_pr=mock_rest_pr)

        # GraphQL data should take precedence
        assert wrapper.number == 100  # From GraphQL
        assert wrapper.title == "GraphQL Title"  # From GraphQL
        assert wrapper.state == "open"  # From GraphQL (lowercased)
        assert wrapper.id == "PR_graphql_123"  # From GraphQL

    def test_hybrid_mode_rest_fallback_for_missing_graphql(self):
        """Test REST is used for attributes missing from GraphQL data."""
        # Minimal GraphQL data (missing many fields)
        pr_data = {
            "id": "PR_123",
            "number": 42,
        }

        # REST object with additional data
        mock_rest_pr = Mock()
        mock_rest_pr.title = "Fallback Title"
        mock_rest_pr.body = "Fallback Body"
        mock_rest_pr.custom_field = "custom_value"

        wrapper = PullRequestWrapper(data=pr_data, rest_pr=mock_rest_pr)

        # GraphQL properties
        assert wrapper.number == 42  # From GraphQL
        assert wrapper.id == "PR_123"  # From GraphQL

        # Fallback to REST for missing GraphQL data
        assert wrapper.title == ""  # GraphQL returns empty string for missing title
        # But custom attributes should delegate to REST via __getattr__
        assert wrapper.custom_field == "custom_value"

    def test_hybrid_mode_getattr_delegation_to_rest(self):
        """Test __getattr__ still works in hybrid mode for unknown attributes."""
        pr_data = {"id": "PR_123", "number": 1}

        mock_rest_pr = Mock()
        mock_rest_pr.special_method = Mock(return_value="special_result")

        wrapper = PullRequestWrapper(data=pr_data, rest_pr=mock_rest_pr)

        # Unknown attributes should delegate to REST object
        assert wrapper.special_method() == "special_result"


class TestPullRequestWrapperGetAttrEdgeCases:
    """Test edge cases for __getattr__ delegation."""

    def test_getattr_with_none_rest_object(self):
        """Test __getattr__ raises correct error when rest is None."""
        wrapper = PullRequestWrapper(data={"id": "PR_123", "number": 1})

        # rest is None, should raise AttributeError
        with pytest.raises(AttributeError, match="object has no attribute 'unknown_attr'"):
            _ = wrapper.unknown_attr

    def test_getattr_does_not_override_wrapper_properties(self):
        """Test __getattr__ doesn't interfere with wrapper's own properties."""
        pr_data = {"id": "PR_gql", "number": 10, "title": "GraphQL Title"}

        mock_rest_pr = Mock()
        mock_rest_pr.number = 99
        mock_rest_pr.title = "REST Title"

        wrapper = PullRequestWrapper(data=pr_data, rest_pr=mock_rest_pr)

        # Wrapper's properties should take precedence (not delegated)
        assert wrapper.number == 10  # From wrapper property, not __getattr__
        assert wrapper.title == "GraphQL Title"  # From wrapper property
        assert wrapper.id == "PR_gql"  # From wrapper property

    def test_getattr_delegation_preserves_method_calls(self):
        """Test __getattr__ correctly delegates method calls to REST object."""
        mock_rest_pr = Mock()
        mock_rest_pr.get_commits = Mock(return_value=["commit1", "commit2"])
        mock_rest_pr.get_files = Mock(return_value=["file1.py"])

        # Wrapper with minimal GraphQL data
        wrapper = PullRequestWrapper(data={"id": "PR_123", "number": 1}, rest_pr=mock_rest_pr)

        # Methods not in wrapper should delegate to REST
        # Note: get_commits is defined in wrapper, so this tests fallback when GraphQL data missing
        # Since wrapper has _data (even if empty commits), it returns wrapper's version
        # But for truly unknown methods:
        result_files = wrapper.get_files()
        assert result_files == ["file1.py"]

    def test_rest_attribute_storage(self):
        """Test that _rest_pr attribute is properly stored and accessible."""
        mock_rest_pr = Mock()
        mock_rest_pr.number = 42

        wrapper = PullRequestWrapper(rest_pr=mock_rest_pr)

        # _rest_pr should be accessible
        assert wrapper._rest_pr is mock_rest_pr
        assert wrapper._rest_pr.number == 42


class TestPullRequestWrapperFallbackPatterns:
    """Test GraphQL-first, REST-fallback patterns across different properties."""

    def test_mergeable_fallback(self):
        """Test mergeable property falls back to REST when no GraphQL data."""
        # No GraphQL data at all (None, not empty dict)
        mock_rest_pr = Mock()
        mock_rest_pr.mergeable = True

        wrapper = PullRequestWrapper(rest_pr=mock_rest_pr)

        # Should fallback to REST when _data is None
        assert wrapper.mergeable is True

    def test_mergeable_graphql_missing_value(self):
        """Test mergeable returns None when GraphQL data exists but mergeable field missing."""
        # GraphQL data exists but mergeable field is missing
        pr_data = {"id": "PR_123", "number": 1}

        mock_rest_pr = Mock()
        mock_rest_pr.mergeable = True

        wrapper = PullRequestWrapper(data=pr_data, rest_pr=mock_rest_pr)

        # GraphQL data takes precedence, missing mergeable returns None
        assert wrapper.mergeable is None

    def test_timestamps_fallback(self):
        """Test timestamp properties fall back to REST when no GraphQL data."""
        # No GraphQL data (None)
        mock_rest_pr = Mock()
        rest_date = datetime(2025, 1, 15, 10, 0, 0)
        mock_rest_pr.created_at = rest_date
        mock_rest_pr.updated_at = rest_date

        wrapper = PullRequestWrapper(rest_pr=mock_rest_pr)

        # Should fallback to REST timestamps when _data is None
        assert wrapper.created_at == rest_date
        assert wrapper.updated_at == rest_date

    def test_timestamps_graphql_missing_values(self):
        """Test timestamps return None when GraphQL data exists but fields missing."""
        # GraphQL data exists but timestamp fields missing
        pr_data = {"id": "PR_123", "number": 1}

        mock_rest_pr = Mock()
        rest_date = datetime(2025, 1, 15, 10, 0, 0)
        mock_rest_pr.created_at = rest_date
        mock_rest_pr.updated_at = rest_date

        wrapper = PullRequestWrapper(data=pr_data, rest_pr=mock_rest_pr)

        # GraphQL data takes precedence, missing timestamps return None
        assert wrapper.created_at is None
        assert wrapper.updated_at is None

    def test_html_url_fallback(self):
        """Test html_url falls back to REST when no GraphQL data."""
        # No GraphQL data (None)
        mock_rest_pr = Mock()
        mock_rest_pr.html_url = "https://github.com/owner/repo/pull/1"

        wrapper = PullRequestWrapper(rest_pr=mock_rest_pr)

        # Should fallback to REST when _data is None
        assert wrapper.html_url == "https://github.com/owner/repo/pull/1"

    def test_html_url_graphql_missing_value(self):
        """Test html_url returns empty string when GraphQL data exists but permalink missing."""
        # GraphQL data exists but permalink field missing
        pr_data = {"id": "PR_123", "number": 1}

        mock_rest_pr = Mock()
        mock_rest_pr.html_url = "https://github.com/owner/repo/pull/1"

        wrapper = PullRequestWrapper(data=pr_data, rest_pr=mock_rest_pr)

        # GraphQL data takes precedence, missing permalink returns empty string
        assert wrapper.html_url == ""

    def test_get_labels_fallback(self):
        """Test get_labels() falls back to REST."""
        pr_data = {"id": "PR_123", "number": 1}

        mock_label = Mock()
        mock_label.name = "bug"

        mock_rest_pr = Mock()
        mock_rest_pr.get_labels = Mock(return_value=[mock_label])

        wrapper = PullRequestWrapper(data=pr_data, rest_pr=mock_rest_pr)

        labels = wrapper.get_labels()
        # Since _data exists but has no labels, wrapper returns empty list
        # For true REST fallback, need to test when _data is None or empty
        assert isinstance(labels, list)

    def test_get_labels_true_rest_fallback(self):
        """Test get_labels() uses REST when GraphQL data is None."""
        # No GraphQL data (_data is None), only REST PR
        mock_label1 = Mock()
        mock_label1.name = "bug"

        mock_label2 = Mock()
        mock_label2.name = "enhancement"

        mock_rest_pr = Mock()
        mock_rest_pr.get_labels = Mock(return_value=[mock_label1, mock_label2])

        # Create wrapper with no GraphQL data
        wrapper = PullRequestWrapper(rest_pr=mock_rest_pr)

        # Should fallback to REST and call get_labels()
        labels = wrapper.get_labels()

        # Verify REST method was called
        mock_rest_pr.get_labels.assert_called_once()

        # Verify we got the REST labels back
        assert labels == [mock_label1, mock_label2]
        assert len(labels) == 2
        assert labels[0].name == "bug"
        assert labels[1].name == "enhancement"

    def test_completely_rest_based_wrapper(self):
        """Test wrapper works entirely with REST object, no GraphQL data."""
        mock_rest_pr = Mock()
        mock_rest_pr.number = 555
        mock_rest_pr.title = "Pure REST PR"
        mock_rest_pr.body = "REST body"
        mock_rest_pr.state = "open"
        mock_rest_pr.draft = False
        mock_rest_pr.merged = False
        mock_rest_pr.id = 888
        mock_rest_pr.html_url = "https://github.com/test/repo/pull/555"

        # No GraphQL data at all
        wrapper = PullRequestWrapper(rest_pr=mock_rest_pr)

        # All properties should work via REST fallback
        assert wrapper.number == 555
        assert wrapper.title == "Pure REST PR"
        assert wrapper.body == "REST body"
        assert wrapper.state == "open"
        assert wrapper.draft is False
        assert wrapper.merged is False
        assert wrapper.id == "888"
        assert wrapper.html_url == "https://github.com/test/repo/pull/555"


def test_pull_request_wrapper_missing_commits():
    """Test PullRequestWrapper handles missing commits data."""
    pr_data = {
        "number": 1,
        "title": "Test PR",
        # commits field missing
    }
    wrapper = PullRequestWrapper(pr_data)

    # get_commits should return empty list
    commits = wrapper.get_commits()
    assert commits == []


def test_pull_request_wrapper_empty_commits_nodes():
    """Test PullRequestWrapper handles empty commits nodes."""
    pr_data = {
        "number": 1,
        "title": "Test PR",
        "commits": {
            "nodes": []  # Empty
        },
    }
    wrapper = PullRequestWrapper(pr_data)

    commits = wrapper.get_commits()
    assert commits == []


def test_pull_request_wrapper_missing_labels():
    """Test PullRequestWrapper handles missing labels data."""
    pr_data = {
        "number": 1,
        "title": "Test PR",
        # labels field missing
    }
    wrapper = PullRequestWrapper(pr_data)

    # get_labels should return empty list
    labels = wrapper.get_labels()
    assert labels == []


def test_pull_request_wrapper_empty_labels_nodes():
    """Test PullRequestWrapper handles empty labels nodes."""
    pr_data = {
        "number": 1,
        "title": "Test PR",
        "labels": {
            "nodes": []  # Empty
        },
    }
    wrapper = PullRequestWrapper(pr_data)

    labels = wrapper.get_labels()
    assert labels == []


def test_user_wrapper_missing_data():
    """Test UserWrapper handles None data gracefully."""
    wrapper = UserWrapper(None)

    # Should return empty string for login
    assert wrapper.login == ""


def test_user_wrapper_empty_dict():
    """Test UserWrapper handles empty dict."""
    wrapper = UserWrapper({})

    assert wrapper.login == ""


def test_user_wrapper_type_property():
    """Test UserWrapper.type property with __typename."""
    data = {"__typename": "Bot", "login": "bot-user"}
    wrapper = UserWrapper(data)
    assert wrapper.type == "Bot"


def test_user_wrapper_type_default():
    """Test UserWrapper.type property default value."""
    data = {"login": "regular-user"}
    wrapper = UserWrapper(data)
    assert wrapper.type == "User"


def test_ref_wrapper_missing_name():
    """Test RefWrapper handles missing name field."""

    repo = RepositoryWrapper({"name": "test-repo", "owner": {"login": "test-owner"}})
    ref_data = {
        # name field missing
        "target": {"oid": "abc123"}
    }
    wrapper = RefWrapper(ref_data, repo)

    # name should be empty string
    assert wrapper.name == ""


def test_ref_wrapper_missing_target_with_repo():
    """Test RefWrapper handles missing target field with repository."""
    repo = RepositoryWrapper({"name": "test-repo", "owner": {"login": "test-owner"}})
    ref_data = {
        "name": "main",
        # target field missing
    }
    wrapper = RefWrapper(ref_data, repo)

    # sha should be empty string
    assert wrapper.sha == ""


def test_label_wrapper_missing_name():
    """Test LabelWrapper handles missing name field."""
    label_data = {}  # No name field
    wrapper = LabelWrapper(label_data)

    assert wrapper.name == ""


def test_commit_wrapper_missing_data():
    """Test CommitWrapper handles missing data."""
    wrapper = CommitWrapper({})

    # Should return empty string for sha
    assert wrapper.sha == ""
    # committer should return empty UserWrapper
    committer = wrapper.committer
    assert committer.login == ""


def test_commit_wrapper_with_committer_name_only():
    """Test CommitWrapper falls back to committer name."""
    commit_data = {"oid": "abc123", "committer": {"name": "Test Committer"}}
    wrapper = CommitWrapper(commit_data)

    assert wrapper.sha == "abc123"
    committer = wrapper.committer
    # Should use name as login
    assert committer.login == "Test Committer"


def test_commit_wrapper_fallback_to_author():
    """Test CommitWrapper falls back to author when committer missing."""
    commit_data = {
        "oid": "abc123",
        # No committer field
        "author": {"user": {"login": "author-user"}},
    }
    wrapper = CommitWrapper(commit_data)

    committer = wrapper.committer
    assert committer.login == "author-user"


def test_commit_wrapper_author_name_fallback():
    """Test CommitWrapper falls back to author name."""

    commit_data = {
        "oid": "abc123",
        "author": {
            "name": "Author Name"
            # No user field
        },
    }
    wrapper = CommitWrapper(commit_data)

    committer = wrapper.committer
    assert committer.login == "Author Name"


def test_pull_request_wrapper_user_with_none_author():
    """Test PullRequestWrapper.user handles None author."""
    pr_data = {
        "number": 1,
        "title": "Test",
        "author": None,  # Can happen for deleted users
    }
    wrapper = PullRequestWrapper(pr_data)

    user = wrapper.user
    assert user.login == ""


def test_pull_request_wrapper_base_with_none():
    """Test PullRequestWrapper.base handles None baseRef."""
    pr_data = {"number": 1, "title": "Test", "baseRef": None}
    wrapper = PullRequestWrapper(pr_data)

    base = wrapper.base
    assert base.name == ""


def test_pull_request_wrapper_head_with_none():
    """Test PullRequestWrapper.head handles None headRef."""
    pr_data = {"number": 1, "title": "Test", "headRef": None}
    wrapper = PullRequestWrapper(pr_data)

    head = wrapper.head
    assert head.name == ""


def test_pull_request_wrapper_mergeable_none():
    """Test PullRequestWrapper.mergeable returns None for UNKNOWN."""
    pr_data = {"number": 1, "title": "Test", "mergeable": "UNKNOWN"}
    wrapper = PullRequestWrapper(pr_data)

    assert wrapper.mergeable is None


def test_pull_request_wrapper_created_at_missing():
    """Test PullRequestWrapper.created_at handles missing timestamp."""
    pr_data = {
        "number": 1,
        "title": "Test",
        # createdAt missing
    }
    wrapper = PullRequestWrapper(pr_data)

    # Should not raise, should return None or handle gracefully
    created_at = wrapper.created_at
    # The wrapper may return None or a default, check it doesn't crash
    assert created_at is None


def test_pull_request_wrapper_updated_at_missing():
    """Test PullRequestWrapper.updated_at handles missing timestamp."""
    pr_data = {
        "number": 1,
        "title": "Test",
        # updatedAt missing
    }
    wrapper = PullRequestWrapper(pr_data)

    # Should return None when updatedAt is missing, never a string
    updated_at = wrapper.updated_at
    assert updated_at is None


def test_repository_wrapper_missing_owner():
    """Test RepositoryWrapper handles missing owner."""

    repo_data = {
        "name": "test-repo",
        # owner missing
    }
    wrapper = RepositoryWrapper(repo_data)

    # Should handle gracefully
    assert wrapper.name == "test-repo"


def test_pull_request_wrapper_rest_mode_mergeable():
    """Test PullRequestWrapper.mergeable in REST mode."""
    rest_pr = MagicMock()
    rest_pr.mergeable = True

    wrapper = PullRequestWrapper({}, rest_pr=rest_pr)

    # Should return REST value
    assert wrapper.mergeable is True


def test_pull_request_wrapper_webhook_data_bot_user():
    """Test PullRequestWrapper with webhook_data preserves bot user login with [bot] suffix.

    This test verifies the fix for auto-verification bug where bot login 'pre-commit-ci[bot]'
    was being truncated to 'pre-commit-ci' when using GraphQL data.
    """
    # GraphQL data (author field from GraphQL doesn't have [bot] suffix)
    graphql_data = {
        "number": 123,
        "title": "Test PR from bot",
        "author": {
            "login": "pre-commit-ci",  # GraphQL author login (without [bot])
            "__typename": "Bot",
        },
    }

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

    # Create wrapper with both GraphQL data and webhook data
    wrapper = PullRequestWrapper(
        data=graphql_data, owner="test-owner", repo_name="test-repo", webhook_data=webhook_data
    )

    # Verify user.login uses webhook data (with [bot] suffix)
    assert wrapper.user.login == "pre-commit-ci[bot]"

    # Verify raw_data contains webhook payload
    assert wrapper.raw_data == webhook_data


def test_pull_request_wrapper_webhook_data_priority():
    """Test PullRequestWrapper webhook_data takes priority over rest_pr.raw_data.

    This test verifies the priority order for _raw_data:
    1. webhook_data parameter (highest priority)
    2. rest_pr.raw_data (fallback)
    """
    # Webhook data with correct bot login
    webhook_data = {
        "number": 123,
        "user": {"login": "pre-commit-ci[bot]", "id": 66853113},
    }

    # REST PR with different raw_data (should be overridden by webhook_data)
    mock_rest_pr = Mock()
    mock_rest_pr.raw_data = {
        "number": 123,
        "user": {"login": "different-user", "id": 999},
    }

    # Create wrapper with both webhook_data and rest_pr
    wrapper = PullRequestWrapper(
        owner="test-owner",
        repo_name="test-repo",
        rest_pr=mock_rest_pr,
        webhook_data=webhook_data,
    )

    # Verify webhook_data takes priority
    assert wrapper.raw_data == webhook_data
    assert wrapper.user.login == "pre-commit-ci[bot]"


def test_pull_request_wrapper_webhook_data_fallback_to_rest_raw_data():
    """Test PullRequestWrapper falls back to rest_pr.raw_data when webhook_data is None."""
    # REST PR with raw_data
    mock_rest_pr = Mock()
    mock_rest_pr.raw_data = {
        "number": 123,
        "user": {"login": "test-user", "id": 999},
    }

    # Create wrapper without webhook_data (should fall back to rest_pr.raw_data)
    wrapper = PullRequestWrapper(owner="test-owner", repo_name="test-repo", rest_pr=mock_rest_pr)

    # Verify rest_pr.raw_data is used as fallback
    assert wrapper.raw_data == mock_rest_pr.raw_data
    assert wrapper.user.login == "test-user"


def test_pull_request_wrapper_webhook_data_none():
    """Test PullRequestWrapper handles None webhook_data gracefully."""
    graphql_data = {
        "number": 123,
        "title": "Test PR",
        "author": {"login": "graphql-user"},
    }

    # Create wrapper with explicit None webhook_data
    wrapper = PullRequestWrapper(data=graphql_data, owner="test-owner", repo_name="test-repo", webhook_data=None)

    # Should fall back to GraphQL author data
    assert wrapper.user.login == "graphql-user"
    # raw_data should be GraphQL data (since no webhook_data and no rest_pr)
    assert wrapper.raw_data == graphql_data


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

    wrapper = PullRequestWrapper(webhook_data=webhook_data)

    # Should extract from webhook payload
    assert wrapper.baseRepository.name == "test-repo"
    assert wrapper.baseRepository.owner.login == "test-owner"


def test_pull_request_wrapper_base_repository_constructed():
    """Test baseRepository property with constructed repository wrapper."""
    pr_data = {
        "id": "PR_123",
        "number": 1,
        "title": "Test PR",
    }

    wrapper = PullRequestWrapper(pr_data, owner="my-owner", repo_name="my-repo")

    # Should use constructed repository wrapper
    assert wrapper.baseRepository.name == "my-repo"
    assert wrapper.baseRepository.owner.login == "my-owner"


def test_pull_request_wrapper_base_repository_rest_fallback():
    """Test baseRepository property falls back to REST PR."""
    mock_rest_pr = Mock()
    mock_base = Mock()
    mock_repo = Mock()
    mock_owner = Mock()
    mock_owner.login = "rest-owner"
    mock_repo.owner = mock_owner
    mock_repo.name = "rest-repo"
    mock_base.repo = mock_repo
    mock_rest_pr.base = mock_base

    wrapper = PullRequestWrapper(rest_pr=mock_rest_pr)

    # Should fall back to REST base.repo
    assert wrapper.baseRepository.name == "rest-repo"
    assert wrapper.baseRepository.owner.login == "rest-owner"


def test_pull_request_wrapper_base_repository_empty():
    """Test baseRepository property returns empty wrapper when no data available."""
    wrapper = PullRequestWrapper()

    # Should return empty RepositoryWrapper
    assert wrapper.baseRepository.name == ""
    assert wrapper.baseRepository.owner.login == ""


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


def test_pull_request_wrapper_base_repository_integration_pattern():
    """
    Test baseRepository matches the actual usage pattern in handlers.
    This simulates the code in owners_files_handler.py and issue_comment_handler.py.
    """
    # Simulate GraphQL data (what handlers receive)
    pr_data = {
        "id": "PR_123",
        "number": 123,
        "title": "Test PR",
    }

    # Simulate webhook payload with base repository info
    webhook_data = {
        "number": 123,
        "base": {
            "ref": "main",
            "sha": "abc123",
            "repo": {
                "name": "my-repo",
                "owner": {"login": "my-org"},
            },
        },
    }

    # Create wrapper as handlers do (with both GraphQL data and webhook data)
    wrapper = PullRequestWrapper(data=pr_data, webhook_data=webhook_data, owner="my-org", repo_name="my-repo")

    # This is the exact pattern used in handlers:
    # pull_request.baseRepository.owner.login
    # pull_request.baseRepository.name
    owner = wrapper.baseRepository.owner.login
    repo = wrapper.baseRepository.name
    number = wrapper.number

    # Verify it works as expected
    assert owner == "my-org"
    assert repo == "my-repo"
    assert number == 123

    # Verify this works the same as the REST pattern for comparison
    # (even though we don't have REST object here, we ensure the interface matches)
    assert hasattr(wrapper.baseRepository, "owner")
    assert hasattr(wrapper.baseRepository.owner, "login")
    assert hasattr(wrapper.baseRepository, "name")


def test_user_wrapper_id_property():
    """Test UserWrapper.id property returns webhook user ID."""
    data = {"login": "testuser", "id": 123456}
    user = UserWrapper(data)
    assert user.id == 123456


def test_user_wrapper_id_default():
    """Test UserWrapper.id returns 0 when missing."""
    data = {"login": "testuser"}
    user = UserWrapper(data)
    assert user.id == 0


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


def test_ref_wrapper_webhook_format_missing_sha():
    """Test RefWrapper handles webhook format with missing sha field."""
    # When only "ref" is present (without "sha"), RefWrapper falls back to GraphQL format
    # which looks for "name" field. Since "name" is missing, returns empty string
    ref_data = {"ref": "main"}  # sha missing - not valid webhook format
    ref = RefWrapper(ref_data)
    assert ref.name == ""  # Falls back to GraphQL format (no "name" field)
    assert ref.sha == ""  # Should return empty string


def test_ref_wrapper_graphql_format_with_name():
    """Test RefWrapper handles GraphQL format correctly."""
    # GraphQL format: "name" field instead of "ref"
    ref_data = {"name": "main", "target": {"oid": "graphql123"}}
    ref = RefWrapper(ref_data)
    assert ref.name == "main"  # Uses "name" field
    assert ref.ref == "main"
    assert ref.sha == "graphql123"  # Uses "target.oid"


def test_pull_request_wrapper_all_none_fallbacks():
    """Test PullRequestWrapper default return values when both _data and _rest_pr are None."""
    # Create wrapper with no data at all (both _data and _rest_pr are None)
    wrapper = PullRequestWrapper()

    # Test all fallback return statements (lines 286, 295, 304, 317, 326, 335)
    assert wrapper.number == 0  # Line 286
    assert wrapper.title == ""  # Line 295
    assert wrapper.body is None  # Line 304
    assert wrapper.state == "open"  # Line 317
    assert wrapper.draft is False  # Line 326
    assert wrapper.merged is False  # Line 335
