from unittest.mock import Mock, patch

import pytest

from webhook_server.libs.labels_handler import LabelsHandler
from webhook_server.utils.constants import SIZE_LABEL_PREFIX


class MockPullRequest:
    def __init__(self, additions: int | None = 50, deletions: int | None = 10):
        self.additions = additions
        self.deletions = deletions
        self.number = 123
        self.title = "Test PR"

    def add_to_labels(self, *labels: str) -> None:
        pass

    def remove_from_labels(self, *labels: str) -> None:
        pass

    def get_labels(self) -> list[Mock]:
        return [Mock(name="existing-label")]

    def create_issue_comment(self, body: str) -> None:
        pass


class TestLabelsHandler:
    """Test suite for label management functionality."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Mock GitHub webhook handler."""
        webhook = Mock()
        webhook.repository = Mock()
        webhook.log_prefix = "[TEST]"
        webhook.logger = Mock()
        return webhook

    @pytest.fixture
    def mock_owners_handler(self) -> Mock:
        """Mock owners file handler."""
        handler = Mock()
        handler.all_pull_request_approvers = ["approver1", "approver2"]
        return handler

    @pytest.fixture
    def labels_handler(self, mock_github_webhook: Mock, mock_owners_handler: Mock) -> LabelsHandler:
        """Labels handler instance."""
        return LabelsHandler(github_webhook=mock_github_webhook, owners_file_handler=mock_owners_handler)

    @pytest.mark.parametrize(
        "additions,deletions,expected_size",
        [
            (0, 0, "XS"),  # No changes
            (10, 5, "XS"),  # Small changes (< 20 total)
            (30, 10, "S"),  # Small changes (20-49 total)
            (60, 30, "M"),  # Medium changes (50-99 total)
            (150, 100, "L"),  # Large changes (100-299 total)
            (300, 150, "XL"),  # Extra large changes (300-499 total)
            (600, 400, "XXL"),  # Extra extra large changes (500+ total)
        ],
    )
    def test_get_size_calculation(
        self, labels_handler: LabelsHandler, additions: int, deletions: int, expected_size: str
    ) -> None:
        """Test pull request size calculation with various line counts."""
        pull_request = MockPullRequest(additions=additions, deletions=deletions)

        result = labels_handler.get_size(pull_request=pull_request)

        assert result == f"{SIZE_LABEL_PREFIX}{expected_size}"

    def test_get_size_none_additions(self, labels_handler: LabelsHandler) -> None:
        """Test size calculation when additions is None."""
        pull_request = MockPullRequest(additions=None, deletions=10)

        result = labels_handler.get_size(pull_request=pull_request)

        # Should handle None additions gracefully
        assert result.startswith(SIZE_LABEL_PREFIX)

    def test_get_size_none_deletions(self, labels_handler: LabelsHandler) -> None:
        """Test size calculation when deletions is None."""
        pull_request = MockPullRequest(additions=50, deletions=None)

        result = labels_handler.get_size(pull_request=pull_request)

        # Should handle None deletions gracefully
        assert result.startswith(SIZE_LABEL_PREFIX)

    def test_get_size_both_none(self, labels_handler: LabelsHandler) -> None:
        """Test size calculation when both additions and deletions are None."""
        pull_request = MockPullRequest(additions=None, deletions=None)

        result = labels_handler.get_size(pull_request=pull_request)

        # Should default to XS when both are None
        assert result == f"{SIZE_LABEL_PREFIX}XS"

    async def test_add_label_success(self, labels_handler: LabelsHandler) -> None:
        """Test successfully adding a label to pull request."""
        pull_request = MockPullRequest()
        label_name = "bug"

        with (
            patch.object(pull_request, "add_to_labels") as mock_add,
            patch.object(labels_handler, "wait_for_label", return_value=True),
        ):
            await labels_handler._add_label(pull_request=pull_request, label=label_name)

            mock_add.assert_called_once_with(label_name)

    async def test_remove_label_success(self, labels_handler: LabelsHandler) -> None:
        """Test successfully removing a label from pull request."""
        pull_request = MockPullRequest()
        label_name = "bug"

        with (
            patch.object(pull_request, "remove_from_labels") as mock_remove,
            patch.object(labels_handler, "wait_for_label", return_value=True),
            patch.object(labels_handler, "label_exists_in_pull_request", return_value=True),
        ):
            await labels_handler._remove_label(pull_request=pull_request, label=label_name)

            mock_remove.assert_called_once_with(label_name)

    async def test_add_label_exception_handling(self, labels_handler: LabelsHandler) -> None:
        """Test exception handling when adding label fails."""
        pull_request = MockPullRequest()
        label_name = "bug"

        with (
            patch.object(pull_request, "add_to_labels", side_effect=Exception("GitHub API error")),
            patch.object(labels_handler, "wait_for_label", return_value=True),
        ):
            # Exception should propagate for this case
            with pytest.raises(Exception, match="GitHub API error"):
                await labels_handler._add_label(pull_request=pull_request, label=label_name)

    async def test_remove_label_exception_handling(self, labels_handler: LabelsHandler) -> None:
        """Test exception handling when removing label fails."""
        pull_request = MockPullRequest()
        label_name = "bug"

        with (
            patch.object(pull_request, "remove_from_labels", side_effect=Exception("GitHub API error")),
            patch.object(labels_handler, "wait_for_label", return_value=True),
        ):
            # Should handle exception gracefully without raising
            await labels_handler._remove_label(pull_request=pull_request, label=label_name)

    async def test_label_by_user_comment_authorized_user(self, labels_handler: LabelsHandler) -> None:
        """Test user-requested labeling by authorized user."""
        pull_request = MockPullRequest()
        label_name = "enhancement"
        user = "approver1"  # User in the approvers list

        with (
            patch.object(labels_handler, "_add_label") as mock_add,
            patch.object(labels_handler, "wait_for_label", return_value=True),
        ):
            await labels_handler.label_by_user_comment(
                pull_request=pull_request, user_requested_label=label_name, remove=False, reviewed_user=user
            )

            mock_add.assert_called_once_with(pull_request=pull_request, label=label_name)

    async def test_label_by_user_comment_unauthorized_user(self, labels_handler: LabelsHandler) -> None:
        """Test user-requested labeling by unauthorized user (regular labels allowed)."""
        pull_request = MockPullRequest()
        label_name = "enhancement"
        user = "unauthorized_user"  # User not in approvers list

        with (
            patch.object(labels_handler, "_add_label") as mock_add,
            patch.object(labels_handler, "wait_for_label", return_value=True),
        ):
            await labels_handler.label_by_user_comment(
                pull_request=pull_request, user_requested_label=label_name, remove=False, reviewed_user=user
            )

            # Regular labels are allowed for any user - should add label
            mock_add.assert_called_once_with(pull_request=pull_request, label=label_name)

    async def test_label_by_user_comment_remove_label(self, labels_handler: LabelsHandler) -> None:
        """Test removing label via user comment."""
        pull_request = MockPullRequest()
        label_name = "enhancement"
        user = "approver1"

        with (
            patch.object(labels_handler, "_remove_label") as mock_remove,
            patch.object(labels_handler, "wait_for_label", return_value=True),
        ):
            await labels_handler.label_by_user_comment(
                pull_request=pull_request, user_requested_label=label_name, remove=True, reviewed_user=user
            )

            mock_remove.assert_called_once_with(pull_request=pull_request, label=label_name)

    async def test_size_label_management(self, labels_handler: LabelsHandler) -> None:
        """Test automatic size label management."""
        pull_request = MockPullRequest(additions=100, deletions=50)  # Should be 'L' size

        # Mock existing labels to include old size label - properly configure the name attribute
        old_size_label = Mock()
        old_size_label.name = f"{SIZE_LABEL_PREFIX}M"
        other_label = Mock()
        other_label.name = "other-label"
        existing_labels = [old_size_label, other_label]

        with (
            patch.object(pull_request, "get_labels", return_value=existing_labels),
            patch.object(labels_handler, "_remove_label") as mock_remove,
            patch.object(labels_handler, "_add_label") as mock_add,
            patch.object(labels_handler, "wait_for_label", return_value=True),
        ):
            await labels_handler.add_size_label(pull_request=pull_request)

            # Should remove old size label and add new one
            mock_remove.assert_called_once_with(pull_request=pull_request, label=f"{SIZE_LABEL_PREFIX}M")
            mock_add.assert_called_once_with(pull_request=pull_request, label=f"{SIZE_LABEL_PREFIX}L")

    async def test_size_label_no_existing_size_label(self, labels_handler: LabelsHandler) -> None:
        """Test adding size label when no existing size label."""
        pull_request = MockPullRequest(additions=50, deletions=25)  # Should be 'M' size

        # Mock existing labels without size label - properly configure name attributes
        bug_label = Mock()
        bug_label.name = "bug"
        enhancement_label = Mock()
        enhancement_label.name = "enhancement"
        existing_labels = [bug_label, enhancement_label]

        with (
            patch.object(pull_request, "get_labels", return_value=existing_labels),
            patch.object(labels_handler, "_remove_label") as mock_remove,
            patch.object(labels_handler, "_add_label") as mock_add,
            patch.object(labels_handler, "wait_for_label", return_value=True),
        ):
            await labels_handler.add_size_label(pull_request=pull_request)

            # Should not remove any label, just add new size label
            mock_remove.assert_not_called()
            mock_add.assert_called_once_with(pull_request=pull_request, label=f"{SIZE_LABEL_PREFIX}M")

    def test_size_threshold_boundaries(self, labels_handler: LabelsHandler) -> None:
        """Test size calculation at threshold boundaries."""
        test_cases = [
            (19, 0, "XS"),  # Just under S threshold (20)
            (20, 0, "S"),  # Exactly at S threshold
            (49, 0, "S"),  # Just under M threshold (50)
            (50, 0, "M"),  # Exactly at M threshold
            (99, 0, "M"),  # Just under L threshold (100)
            (100, 0, "L"),  # Exactly at L threshold
            (299, 0, "L"),  # Just under XL threshold (300)
            (300, 0, "XL"),  # Exactly at XL threshold
            (499, 0, "XL"),  # Just under XXL threshold (500)
            (500, 0, "XXL"),  # Exactly at XXL threshold
        ]

        for additions, deletions, expected_size in test_cases:
            pull_request = MockPullRequest(additions=additions, deletions=deletions)
            result = labels_handler.get_size(pull_request=pull_request)
            assert result == f"{SIZE_LABEL_PREFIX}{expected_size}", (
                f"Failed for {additions}+{deletions}={additions + deletions}, expected {expected_size}"
            )

    async def test_concurrent_label_operations(self, labels_handler: LabelsHandler) -> None:
        """Test handling concurrent label operations."""
        pull_request = MockPullRequest()

        # Simulate concurrent add and remove operations
        with (
            patch.object(labels_handler, "_add_label") as mock_add,
            patch.object(labels_handler, "_remove_label") as mock_remove,
            patch.object(labels_handler, "wait_for_label", return_value=True),
        ):
            import asyncio

            # Run concurrent operations
            await asyncio.gather(
                labels_handler._add_label(pull_request=pull_request, label="bug"),
                labels_handler._remove_label(pull_request=pull_request, label="enhancement"),
                labels_handler._add_label(pull_request=pull_request, label="documentation"),
                return_exceptions=True,
            )

            # Verify all operations were attempted
            assert mock_add.call_count == 2
            assert mock_remove.call_count == 1
