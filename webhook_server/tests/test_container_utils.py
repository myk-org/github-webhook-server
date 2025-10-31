"""Tests for container_utils module."""

from unittest.mock import Mock

import pytest

from webhook_server.libs.graphql.webhook_data import PullRequestWrapper
from webhook_server.utils.container_utils import get_container_repository_and_tag


class TestGetContainerRepositoryAndTag:
    """Test suite for get_container_repository_and_tag function."""

    @pytest.fixture
    def mock_logger(self) -> Mock:
        """Create a mock logger."""
        return Mock()

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        """Create a mock PullRequestWrapper."""
        pr = Mock(spec=PullRequestWrapper)
        pr.number = 123
        pr.base = Mock()
        pr.base.ref = "main"
        return pr

    def test_explicit_tag_provided(self, mock_logger: Mock) -> None:
        """Test with explicit tag provided."""
        result = get_container_repository_and_tag(
            container_repository="quay.io/myorg/myimage",
            container_tag="latest",
            tag="v1.2.3",
            logger=mock_logger,
            log_prefix="[TEST]",
        )

        assert result == "quay.io/myorg/myimage:v1.2.3"
        mock_logger.debug.assert_called_once_with("[TEST] container tag is: v1.2.3")

    def test_explicit_tag_with_hash(self, mock_logger: Mock) -> None:
        """Test with hash-based tag."""
        result = get_container_repository_and_tag(
            container_repository="docker.io/myorg/myimage",
            container_tag="latest",
            tag="abc123def456",  # pragma: allowlist secret
            logger=mock_logger,
        )

        assert result == "docker.io/myorg/myimage:abc123def456"

    def test_merged_pr_main_branch(self, mock_pull_request: Mock, mock_logger: Mock) -> None:
        """Test merged PR on main branch uses default container tag."""
        mock_pull_request.base.ref = "main"

        result = get_container_repository_and_tag(
            container_repository="ghcr.io/myorg/myimage",
            container_tag="latest",
            is_merged=True,
            pull_request=mock_pull_request,
            logger=mock_logger,
        )

        assert result == "ghcr.io/myorg/myimage:latest"

    def test_merged_pr_master_branch(self, mock_pull_request: Mock, mock_logger: Mock) -> None:
        """Test merged PR on master branch uses default container tag."""
        mock_pull_request.base.ref = "master"

        result = get_container_repository_and_tag(
            container_repository="quay.io/myorg/myimage",
            container_tag="stable",
            is_merged=True,
            pull_request=mock_pull_request,
            logger=mock_logger,
        )

        assert result == "quay.io/myorg/myimage:stable"

    def test_merged_pr_feature_branch(self, mock_pull_request: Mock, mock_logger: Mock) -> None:
        """Test merged PR on feature branch uses branch name as tag."""
        mock_pull_request.base.ref = "feature/new-api"

        result = get_container_repository_and_tag(
            container_repository="quay.io/myorg/myimage",
            container_tag="latest",
            is_merged=True,
            pull_request=mock_pull_request,
            logger=mock_logger,
        )

        assert result == "quay.io/myorg/myimage:feature/new-api"

    def test_merged_pr_release_branch(self, mock_pull_request: Mock, mock_logger: Mock) -> None:
        """Test merged PR on release branch uses branch name as tag."""
        mock_pull_request.base.ref = "release-v2.0"

        result = get_container_repository_and_tag(
            container_repository="quay.io/myorg/myimage",
            container_tag="latest",
            is_merged=True,
            pull_request=mock_pull_request,
            logger=mock_logger,
        )

        assert result == "quay.io/myorg/myimage:release-v2.0"

    def test_unmerged_pr(self, mock_pull_request: Mock, mock_logger: Mock) -> None:
        """Test unmerged PR uses pr-{number} tag format."""
        mock_pull_request.number = 456

        result = get_container_repository_and_tag(
            container_repository="quay.io/myorg/myimage",
            container_tag="latest",
            is_merged=False,
            pull_request=mock_pull_request,
            logger=mock_logger,
        )

        assert result == "quay.io/myorg/myimage:pr-456"

    def test_no_tag_no_pull_request(self, mock_logger: Mock) -> None:
        """Test returns None when no tag and no PR provided."""
        result = get_container_repository_and_tag(
            container_repository="quay.io/myorg/myimage",
            container_tag="latest",
            logger=mock_logger,
            log_prefix="[ERROR]",
        )

        assert result is None
        mock_logger.error.assert_called_once_with("[ERROR] No pull request provided and no tag specified")

    def test_repository_with_port(self, mock_logger: Mock) -> None:
        """Test repository URL with port number."""
        result = get_container_repository_and_tag(
            container_repository="registry.example.com:5000/myorg/myimage",
            container_tag="latest",
            tag="v2.0.0",
            logger=mock_logger,
        )

        assert result == "registry.example.com:5000/myorg/myimage:v2.0.0"

    def test_repository_with_nested_path(self, mock_logger: Mock) -> None:
        """Test repository with nested path."""
        result = get_container_repository_and_tag(
            container_repository="quay.io/myorg/team/myimage",
            container_tag="latest",
            tag="dev",
            logger=mock_logger,
        )

        assert result == "quay.io/myorg/team/myimage:dev"

    def test_tag_with_special_characters(self, mock_logger: Mock) -> None:
        """Test tag with special characters like dots and hyphens."""
        result = get_container_repository_and_tag(
            container_repository="quay.io/myorg/myimage",
            container_tag="latest",
            tag="v1.2.3-rc.1",
            logger=mock_logger,
        )

        assert result == "quay.io/myorg/myimage:v1.2.3-rc.1"

    def test_without_logger(self) -> None:
        """Test function works without logger (logger is optional)."""
        result = get_container_repository_and_tag(
            container_repository="quay.io/myorg/myimage",
            container_tag="latest",
            tag="v1.0.0",
        )

        assert result == "quay.io/myorg/myimage:v1.0.0"

    def test_without_logger_no_tag_no_pr(self) -> None:
        """Test returns None without logger when no tag and no PR."""
        result = get_container_repository_and_tag(
            container_repository="quay.io/myorg/myimage",
            container_tag="latest",
        )

        assert result is None

    def test_without_log_prefix(self, mock_logger: Mock) -> None:
        """Test function works without log_prefix (uses empty string by default)."""
        result = get_container_repository_and_tag(
            container_repository="quay.io/myorg/myimage",
            container_tag="latest",
            tag="test",
            logger=mock_logger,
        )

        assert result == "quay.io/myorg/myimage:test"
        mock_logger.debug.assert_called_once_with(" container tag is: test")

    def test_pr_number_zero(self, mock_pull_request: Mock, mock_logger: Mock) -> None:
        """Test PR with number 0 (edge case)."""
        mock_pull_request.number = 0

        result = get_container_repository_and_tag(
            container_repository="quay.io/myorg/myimage",
            container_tag="latest",
            is_merged=False,
            pull_request=mock_pull_request,
            logger=mock_logger,
        )

        assert result == "quay.io/myorg/myimage:pr-0"

    def test_very_long_branch_name(self, mock_pull_request: Mock, mock_logger: Mock) -> None:
        """Test with very long branch name."""
        long_branch = "feature/" + "x" * 100
        mock_pull_request.base.ref = long_branch

        result = get_container_repository_and_tag(
            container_repository="quay.io/myorg/myimage",
            container_tag="latest",
            is_merged=True,
            pull_request=mock_pull_request,
            logger=mock_logger,
        )

        assert result == f"quay.io/myorg/myimage:{long_branch}"

    def test_empty_container_repository(self, mock_logger: Mock) -> None:
        """Test with empty container repository string."""
        result = get_container_repository_and_tag(
            container_repository="",
            container_tag="latest",
            tag="v1.0.0",
            logger=mock_logger,
        )

        assert result == ":v1.0.0"

    def test_empty_tag_string(self, mock_pull_request: Mock, mock_logger: Mock) -> None:
        """Test with explicitly empty tag string (should use PR logic)."""
        result = get_container_repository_and_tag(
            container_repository="quay.io/myorg/myimage",
            container_tag="latest",
            tag="",
            is_merged=False,
            pull_request=mock_pull_request,
            logger=mock_logger,
        )

        assert result == "quay.io/myorg/myimage:pr-123"

    def test_merged_pr_main_with_empty_container_tag(self, mock_pull_request: Mock, mock_logger: Mock) -> None:
        """Test merged PR on main with empty default container tag."""
        mock_pull_request.base.ref = "main"

        result = get_container_repository_and_tag(
            container_repository="quay.io/myorg/myimage",
            container_tag="",  # Empty default tag
            is_merged=True,
            pull_request=mock_pull_request,
            logger=mock_logger,
        )

        # When merged to main with empty container_tag, tag becomes empty string
        # This triggers the final error path
        assert result is None
        mock_logger.error.assert_called_with(" container tag not found")
