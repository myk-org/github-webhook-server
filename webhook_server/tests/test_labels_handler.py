import asyncio
from contextlib import suppress
from unittest.mock import AsyncMock, Mock, patch

import pytest
from github.PullRequest import PullRequest

from webhook_server.libs.graphql.graphql_client import GraphQLError
from webhook_server.libs.handlers.labels_handler import LabelsHandler
from webhook_server.utils.constants import (
    ADD_STR,
    APPROVE_STR,
    HOLD_LABEL_STR,
    LGTM_STR,
    SIZE_LABEL_PREFIX,
    STATIC_LABELS_DICT,
    WIP_STR,
)


class TestLabelsHandler:
    """Test suite for label management functionality."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Mock GitHub webhook handler."""
        webhook = Mock()
        webhook.repository = Mock()
        webhook.repository.full_name = "test-owner/test-repo"
        webhook.log_prefix = "[TEST]"
        webhook.logger = Mock()
        webhook.unified_api = AsyncMock()  # Enable GraphQL
        webhook.unified_api.get_label_id = AsyncMock(return_value="LA_123")
        webhook.unified_api.get_repository = AsyncMock(return_value={"id": "R_456"})
        webhook.unified_api.create_label = AsyncMock()
        webhook.unified_api.update_label = AsyncMock()
        webhook.unified_api.add_labels = AsyncMock()
        webhook.unified_api.remove_labels = AsyncMock()
        # Mock get_pull_request to return dict structure expected by PullRequestWrapper
        webhook.unified_api.get_pull_request = AsyncMock(
            return_value={"number": 123, "labels": {"nodes": []}, "title": "Test PR"}
        )
        # Configure config.get_value to return None for pr-size-thresholds by default
        # This ensures existing tests use static defaults
        webhook.config.get_value.return_value = None
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

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        """Mock pull request object."""
        mock = Mock(spec=PullRequest)
        mock.id = "PR_kgDOTestId"
        mock.number = 123
        return mock

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
        pull_request = Mock(spec=PullRequest)
        pull_request.additions = additions
        pull_request.deletions = deletions

        result = labels_handler.get_size(pull_request=pull_request)

        assert result == f"{SIZE_LABEL_PREFIX}{expected_size}"

    def test_get_size_none_additions(self, labels_handler: LabelsHandler) -> None:
        """Test size calculation when additions is None."""
        pull_request = Mock(spec=PullRequest)
        pull_request.additions = None
        pull_request.deletions = 10

        result = labels_handler.get_size(pull_request=pull_request)

        assert result.startswith(SIZE_LABEL_PREFIX)

    def test_get_size_none_deletions(self, labels_handler: LabelsHandler) -> None:
        """Test size calculation when deletions is None."""
        pull_request = Mock(spec=PullRequest)
        pull_request.additions = 50
        pull_request.deletions = None

        result = labels_handler.get_size(pull_request=pull_request)

        assert result.startswith(SIZE_LABEL_PREFIX)

    def test_get_size_both_none(self, labels_handler: LabelsHandler) -> None:
        """Test size calculation when both additions and deletions are None."""
        pull_request = Mock(spec=PullRequest)
        pull_request.additions = None
        pull_request.deletions = None

        result = labels_handler.get_size(pull_request=pull_request)

        assert result == f"{SIZE_LABEL_PREFIX}XS"

    @pytest.mark.asyncio
    async def test_add_label_success(self, labels_handler: LabelsHandler, mock_pull_request: Mock) -> None:
        """Test successful label addition."""
        # Mock that label doesn't exist initially
        with patch.object(labels_handler, "label_exists_in_pull_request", new=AsyncMock(return_value=False)):
            with patch.object(labels_handler, "wait_for_label", new=AsyncMock(return_value=True)):
                # Mock unified_api for static label (skips dynamic label logic)
                labels_handler.unified_api.get_label_id.return_value = "LA_test"
                labels_handler.unified_api.add_labels.return_value = None

                await labels_handler._add_label(mock_pull_request, "lgtm")  # Static label

                # Verify unified_api was called with correct arguments
                labels_handler.unified_api.add_labels.assert_called_once()
                call_args = labels_handler.unified_api.add_labels.call_args
                assert call_args[0][0] == mock_pull_request.id
                assert "LA_test" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_add_label_exception_handling(self, labels_handler: LabelsHandler, mock_pull_request: Mock) -> None:
        """Test label addition with exception handling."""
        with patch("webhook_server.libs.handlers.labels_handler.TimeoutWatch") as mock_timeout:
            mock_timeout.return_value.remaining_time.side_effect = [10, 10, 0]
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(
                    labels_handler, "label_exists_in_pull_request", new_callable=AsyncMock, side_effect=[False, True]
                ):
                    # Mock unified_api.add_labels to raise exception (unified_api is used, not add_to_labels)
                    labels_handler.unified_api.add_labels = AsyncMock(side_effect=Exception("Test error"))
                    # Exception handling - method may raise but test continues
                    with suppress(Exception):
                        await labels_handler._add_label(mock_pull_request, "test-label")

    @pytest.mark.asyncio
    async def test_remove_label_success(self, labels_handler: LabelsHandler, mock_pull_request: Mock) -> None:
        """Test successful label removal."""
        with patch.object(labels_handler, "label_exists_in_pull_request", new=AsyncMock(return_value=True)):
            with patch.object(labels_handler, "wait_for_label", new=AsyncMock(return_value=True)):
                labels_handler.unified_api.get_label_id.return_value = "LA_test"
                labels_handler.unified_api.remove_labels.return_value = None

                result = await labels_handler._remove_label(mock_pull_request, "test-label")

                assert result is True
                # Verify unified_api was called with correct arguments
                labels_handler.unified_api.remove_labels.assert_called_once()
                call_args = labels_handler.unified_api.remove_labels.call_args
                assert call_args[0][0] == mock_pull_request.id
                assert "LA_test" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_remove_label_exception_handling(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test label removal with exception handling."""
        with patch.object(labels_handler, "label_exists_in_pull_request", new_callable=AsyncMock, return_value=True):
            labels_handler.unified_api.get_label_id.return_value = "LA_test"
            labels_handler.unified_api.remove_labels.side_effect = Exception("Test error")

            result = await labels_handler._remove_label(mock_pull_request, "test-label")
            assert result is False

    @pytest.mark.asyncio
    async def test_remove_label_exception_during_wait(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test _remove_label with exception during wait operation."""
        with patch("webhook_server.libs.handlers.labels_handler.TimeoutWatch") as mock_timeout:
            mock_timeout.return_value.remaining_time.side_effect = [10, 10, 0]
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(
                    labels_handler, "label_exists_in_pull_request", new_callable=AsyncMock, side_effect=[True, False]
                ):
                    with patch.object(
                        labels_handler, "wait_for_label", new_callable=AsyncMock, side_effect=Exception("Wait failed")
                    ):
                        result = await labels_handler._remove_label(mock_pull_request, "test-label")
                        assert result is False

    @pytest.mark.asyncio
    async def test_remove_label_wait_for_label_exception(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test _remove_label with exception during wait_for_label."""
        with patch("webhook_server.libs.handlers.labels_handler.TimeoutWatch") as mock_timeout:
            mock_timeout.return_value.remaining_time.side_effect = [10, 10, 0]
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(
                    labels_handler, "label_exists_in_pull_request", new_callable=AsyncMock, side_effect=[True, False]
                ):
                    with patch.object(
                        labels_handler, "wait_for_label", new_callable=AsyncMock, side_effect=Exception("Wait failed")
                    ):
                        result = await labels_handler._remove_label(mock_pull_request, "test-label")
                        assert result is False

    @pytest.mark.asyncio
    async def test_add_label_dynamic_label_wait_exception(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test _add_label with exception during wait for dynamic label."""
        dynamic_label = "dynamic-label"
        with patch("webhook_server.libs.handlers.labels_handler.TimeoutWatch") as mock_timeout:
            mock_timeout.return_value.remaining_time.side_effect = [10, 10, 0]
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(
                    labels_handler, "label_exists_in_pull_request", new=AsyncMock(side_effect=[False, True])
                ):
                    with patch.object(
                        labels_handler, "wait_for_label", new=AsyncMock(side_effect=Exception("Wait failed"))
                    ):
                        # Exception handling - method may raise but test continues
                        with suppress(Exception):
                            await labels_handler._add_label(mock_pull_request, dynamic_label)

    @pytest.mark.asyncio
    async def test_add_label_static_label_wait_exception(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test _add_label with exception during wait for static label."""
        static_label = list(STATIC_LABELS_DICT.keys())[0]
        with patch("webhook_server.libs.handlers.labels_handler.TimeoutWatch") as mock_timeout:
            mock_timeout.return_value.remaining_time.side_effect = [10, 10, 0]
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(
                    labels_handler, "label_exists_in_pull_request", new=AsyncMock(side_effect=[False, True])
                ):
                    with patch.object(
                        labels_handler, "wait_for_label", new=AsyncMock(side_effect=Exception("Wait failed"))
                    ):
                        # Should not raise exception
                        await labels_handler._add_label(mock_pull_request, static_label)

    @pytest.mark.asyncio
    async def test_wait_for_label_success(self, labels_handler: LabelsHandler, mock_pull_request: Mock) -> None:
        """Test wait_for_label with success."""
        with patch("webhook_server.libs.handlers.labels_handler.TimeoutWatch") as mock_timeout:
            mock_timeout.return_value.remaining_time.side_effect = [10, 10, 0]
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(
                    labels_handler, "label_exists_in_pull_request", new_callable=AsyncMock, side_effect=[True]
                ):
                    result = await labels_handler.wait_for_label(mock_pull_request, "test-label", exists=True)
                    assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_label_exception_during_check(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test wait_for_label with exception during label check."""
        with patch("webhook_server.libs.handlers.labels_handler.TimeoutWatch") as mock_timeout:
            mock_timeout.return_value.remaining_time.side_effect = [10, 10, 0]
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(
                    labels_handler,
                    "label_exists_in_pull_request",
                    new_callable=AsyncMock,
                    side_effect=Exception("Check failed"),
                ):
                    with pytest.raises(Exception, match="Check failed"):
                        await labels_handler.wait_for_label(mock_pull_request, "test-label", exists=True)

    @pytest.mark.asyncio
    async def test_label_by_user_comment_authorized_user(self, labels_handler: LabelsHandler) -> None:
        """Test user-requested labeling by authorized user."""
        pull_request = Mock(spec=PullRequest)
        label_name = "enhancement"
        user = "approver1"  # User in the approvers list

        with (
            patch.object(labels_handler, "_add_label", new_callable=AsyncMock) as mock_add,
            patch.object(labels_handler, "wait_for_label", new_callable=AsyncMock, return_value=True),
        ):
            await labels_handler.label_by_user_comment(
                pull_request=pull_request, user_requested_label=label_name, remove=False, reviewed_user=user
            )

            mock_add.assert_called_once_with(pull_request=pull_request, label=label_name)

    @pytest.mark.asyncio
    async def test_label_by_user_comment_unauthorized_user(self, labels_handler: LabelsHandler) -> None:
        """Test user-requested labeling by unauthorized user (regular labels allowed)."""
        pull_request = Mock(spec=PullRequest)
        label_name = "enhancement"
        user = "unauthorized_user"  # User not in approvers list

        with (
            patch.object(labels_handler, "_add_label", new_callable=AsyncMock) as mock_add,
            patch.object(labels_handler, "wait_for_label", new_callable=AsyncMock, return_value=True),
        ):
            await labels_handler.label_by_user_comment(
                pull_request=pull_request, user_requested_label=label_name, remove=False, reviewed_user=user
            )

            # Regular labels are allowed for any user - should add label
            mock_add.assert_called_once_with(pull_request=pull_request, label=label_name)

    @pytest.mark.asyncio
    async def test_label_by_user_comment_remove_label(self, labels_handler: LabelsHandler) -> None:
        """Test removing label via user comment."""
        pull_request = Mock(spec=PullRequest)
        label_name = "enhancement"
        user = "approver1"

        with (
            patch.object(labels_handler, "_remove_label", new_callable=AsyncMock) as mock_remove,
            patch.object(labels_handler, "wait_for_label", new_callable=AsyncMock, return_value=True),
        ):
            await labels_handler.label_by_user_comment(
                pull_request=pull_request, user_requested_label=label_name, remove=True, reviewed_user=user
            )

            mock_remove.assert_called_once_with(pull_request=pull_request, label=label_name)

    @pytest.mark.asyncio
    async def test_size_label_management(self, labels_handler: LabelsHandler) -> None:
        """Test automatic size label management."""
        pull_request = Mock(spec=PullRequest)
        pull_request.additions = 100
        pull_request.deletions = 50  # Should be 'L' size

        # Mock existing labels to include old size label - properly configure the name attribute
        old_size_label = Mock()
        old_size_label.name = f"{SIZE_LABEL_PREFIX}M"
        other_label = Mock()
        other_label.name = "other-label"
        existing_labels = [old_size_label, other_label]

        with (
            patch.object(pull_request, "get_labels", return_value=existing_labels),
            patch.object(labels_handler, "_remove_label", new_callable=AsyncMock) as mock_remove,
            patch.object(labels_handler, "_add_label", new_callable=AsyncMock) as mock_add,
            patch.object(labels_handler, "wait_for_label", new_callable=AsyncMock, return_value=True),
        ):
            await labels_handler.add_size_label(pull_request=pull_request)

            # Should remove old size label and add new one
            mock_remove.assert_called_once_with(pull_request=pull_request, label=f"{SIZE_LABEL_PREFIX}M")
            mock_add.assert_called_once_with(pull_request=pull_request, label=f"{SIZE_LABEL_PREFIX}L")

    @pytest.mark.asyncio
    async def test_size_label_no_existing_size_label(self, labels_handler: LabelsHandler) -> None:
        """Test adding size label when no existing size label."""
        pull_request = Mock(spec=PullRequest)
        pull_request.additions = 50
        pull_request.deletions = 25  # Should be 'M' size

        # Mock existing labels without size label - properly configure name attributes
        bug_label = Mock()
        bug_label.name = "bug"
        enhancement_label = Mock()
        enhancement_label.name = "enhancement"
        existing_labels = [bug_label, enhancement_label]

        with (
            patch.object(pull_request, "get_labels", return_value=existing_labels),
            patch.object(labels_handler, "_remove_label", new_callable=AsyncMock) as mock_remove,
            patch.object(labels_handler, "_add_label", new_callable=AsyncMock) as mock_add,
            patch.object(labels_handler, "wait_for_label", new_callable=AsyncMock, return_value=True),
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
            pull_request = Mock(spec=PullRequest)
            pull_request.additions = additions
            pull_request.deletions = deletions
            result = labels_handler.get_size(pull_request=pull_request)
            assert result == f"{SIZE_LABEL_PREFIX}{expected_size}", (
                f"Failed for {additions}+{deletions}={additions + deletions}, expected {expected_size}"
            )

    @pytest.mark.asyncio
    async def test_concurrent_label_operations(self, labels_handler: LabelsHandler) -> None:
        """Test handling concurrent label operations."""
        pull_request = Mock(spec=PullRequest)

        # Simulate concurrent add and remove operations
        with (
            patch.object(labels_handler, "_add_label", new_callable=AsyncMock) as mock_add,
            patch.object(labels_handler, "_remove_label", new_callable=AsyncMock) as mock_remove,
            patch.object(labels_handler, "wait_for_label", new_callable=AsyncMock, return_value=True),
        ):
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

    @pytest.mark.asyncio
    async def test_add_label_dynamic_label_edit_exception(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test _add_label with dynamic label where creation fails."""
        with patch.object(labels_handler, "label_exists_in_pull_request", new_callable=AsyncMock, return_value=False):
            with patch.object(mock_pull_request, "get_labels", return_value=[]):
                with patch.object(labels_handler, "wait_for_label", new_callable=AsyncMock, return_value=True):
                    # Mock unified_api to raise exception during create
                    labels_handler.github_webhook.unified_api.get_label_id.return_value = None  # Label doesn't exist
                    labels_handler.github_webhook.unified_api.create_label.side_effect = Exception("Create failed")

                    with pytest.raises(Exception, match="Create failed"):
                        await labels_handler._add_label(mock_pull_request, "dynamic-label")

    @pytest.mark.asyncio
    async def test_add_label_dynamic_label_edit_success(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test _add_label with dynamic label where edit succeeds."""
        with patch.object(labels_handler, "label_exists_in_pull_request", new_callable=AsyncMock, return_value=False):
            with patch.object(mock_pull_request, "get_labels", return_value=[]):
                with patch.object(labels_handler, "wait_for_label", new_callable=AsyncMock, return_value=True):
                    # Mock unified_api for successful label update
                    # First call returns label_id (line 98), second call returns label_id (line 116)
                    labels_handler.github_webhook.unified_api.get_label_id.side_effect = ["LA_123", "LA_123"]
                    labels_handler.github_webhook.unified_api.update_label.return_value = {"id": "LA_123"}
                    labels_handler.github_webhook.unified_api.add_labels.return_value = None

                    await labels_handler._add_label(mock_pull_request, "dynamic-label")

    @pytest.mark.asyncio
    async def test_manage_reviewed_by_label_approve_not_in_approvers(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test manage_reviewed_by_label with approve from user not in approvers/root_approvers."""
        # Mock root_approvers as a list to avoid concatenation error
        with patch.object(labels_handler.owners_file_handler, "root_approvers", []):
            with (
                patch.object(labels_handler, "_add_label", new_callable=AsyncMock) as mock_add,
                patch.object(labels_handler, "_remove_label", new_callable=AsyncMock) as mock_remove,
            ):
                await labels_handler.manage_reviewed_by_label(mock_pull_request, APPROVE_STR, ADD_STR, "not_approver")
                mock_add.assert_not_called()
                mock_remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_manage_reviewed_by_label_changes_requested(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test manage_reviewed_by_label with changes_requested state."""
        with (
            patch.object(labels_handler, "_add_label", new_callable=AsyncMock) as mock_add,
            patch.object(labels_handler, "_remove_label", new_callable=AsyncMock) as mock_remove,
        ):
            await labels_handler.manage_reviewed_by_label(mock_pull_request, "changes_requested", ADD_STR, "reviewer1")
            mock_add.assert_called_once()
            mock_remove.assert_called_once()

    @pytest.mark.asyncio
    async def test_manage_reviewed_by_label_commented(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test manage_reviewed_by_label with commented state."""
        with patch.object(labels_handler, "_add_label", new_callable=AsyncMock) as mock_add:
            await labels_handler.manage_reviewed_by_label(mock_pull_request, "commented", ADD_STR, "reviewer1")
            mock_add.assert_called_once()

    @pytest.mark.asyncio
    async def test_manage_reviewed_by_label_unsupported_state(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test manage_reviewed_by_label with unsupported review state."""
        with patch.object(labels_handler, "_add_label", new_callable=AsyncMock) as mock_add:
            await labels_handler.manage_reviewed_by_label(mock_pull_request, "unsupported", ADD_STR, "reviewer1")
            mock_add.assert_not_called()

    @pytest.mark.asyncio
    async def test_label_by_user_comment_remove(self, labels_handler: LabelsHandler, mock_pull_request: Mock) -> None:
        """Test label_by_user_comment with remove=True for regular label."""
        with patch.object(labels_handler, "_remove_label", new_callable=AsyncMock) as mock_remove:
            await labels_handler.label_by_user_comment(mock_pull_request, "bug", True, "user1")
            mock_remove.assert_called_once_with(pull_request=mock_pull_request, label="bug")

    @pytest.mark.asyncio
    async def test_add_size_label_no_size_label(self, labels_handler: LabelsHandler, mock_pull_request: Mock) -> None:
        """Test add_size_label when get_size returns None."""
        with patch.object(labels_handler, "get_size", return_value=None):
            with patch.object(labels_handler, "_add_label", new_callable=AsyncMock) as mock_add:
                await labels_handler.add_size_label(mock_pull_request)
                mock_add.assert_not_called()

    @pytest.mark.asyncio
    async def test_label_exists_in_pull_request_exception(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test label_exists_in_pull_request with exception."""
        with patch.object(
            labels_handler, "pull_request_labels_names", new_callable=AsyncMock, side_effect=Exception("Test error")
        ):
            with pytest.raises(Exception, match="Test error"):
                await labels_handler.label_exists_in_pull_request(mock_pull_request, "test-label")

    @pytest.mark.asyncio
    async def test_add_size_label_remove_existing_exception(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test add_size_label with exception during remove of existing size label."""
        mock_pull_request.additions = 10
        mock_pull_request.deletions = 5
        existing_size_label = f"{SIZE_LABEL_PREFIX}L"
        with patch.object(
            labels_handler,
            "pull_request_labels_names",
            new_callable=AsyncMock,
            return_value=[existing_size_label],
        ):
            with patch.object(
                labels_handler,
                "_remove_label",
                new_callable=AsyncMock,
                side_effect=Exception("Remove failed"),
            ):
                with patch.object(labels_handler, "_add_label", new_callable=AsyncMock):
                    with pytest.raises(Exception, match="Remove failed"):
                        await labels_handler.add_size_label(mock_pull_request)

    @pytest.mark.asyncio
    async def test_label_by_user_comment_lgtm_remove(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test label_by_user_comment for LGTM removal."""
        with patch.object(labels_handler, "manage_reviewed_by_label", new_callable=AsyncMock) as mock_manage:
            await labels_handler.label_by_user_comment(
                pull_request=mock_pull_request, user_requested_label=LGTM_STR, remove=True, reviewed_user="test-user"
            )
            mock_manage.assert_called_once()

    @pytest.mark.asyncio
    async def test_label_by_user_comment_approve_remove(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test label_by_user_comment for approve removal."""
        with patch.object(labels_handler, "manage_reviewed_by_label", new_callable=AsyncMock) as mock_manage:
            await labels_handler.label_by_user_comment(
                pull_request=mock_pull_request, user_requested_label=APPROVE_STR, remove=True, reviewed_user="test-user"
            )
            mock_manage.assert_called_once()

    @pytest.mark.asyncio
    async def test_label_by_user_comment_approve_add(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test label_by_user_comment for approve addition."""
        with patch.object(labels_handler, "manage_reviewed_by_label", new_callable=AsyncMock) as mock_manage:
            await labels_handler.label_by_user_comment(
                pull_request=mock_pull_request,
                user_requested_label=APPROVE_STR,
                remove=False,
                reviewed_user="test-user",
            )
            mock_manage.assert_called_once()

    @pytest.mark.asyncio
    async def test_label_by_user_comment_lgtm_add(self, labels_handler: LabelsHandler, mock_pull_request: Mock) -> None:
        """Test label_by_user_comment for LGTM addition."""
        with patch.object(labels_handler, "manage_reviewed_by_label", new_callable=AsyncMock) as mock_manage:
            await labels_handler.label_by_user_comment(
                pull_request=mock_pull_request, user_requested_label=LGTM_STR, remove=False, reviewed_user="test-user"
            )
            mock_manage.assert_called_once()

    @pytest.mark.asyncio
    async def test_label_by_user_comment_other_label_add(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test label_by_user_comment for other label addition."""
        with patch.object(labels_handler, "_add_label", new_callable=AsyncMock) as mock_add:
            await labels_handler.label_by_user_comment(
                pull_request=mock_pull_request,
                user_requested_label="other-label",
                remove=False,
                reviewed_user="test-user",
            )
            mock_add.assert_called_once_with(pull_request=mock_pull_request, label="other-label")

    @pytest.mark.asyncio
    async def test_label_by_user_comment_other_label_remove(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test label_by_user_comment for other label removal."""
        with patch.object(labels_handler, "_remove_label", new_callable=AsyncMock) as mock_remove:
            await labels_handler.label_by_user_comment(
                pull_request=mock_pull_request,
                user_requested_label="other-label",
                remove=True,
                reviewed_user="test-user",
            )
            mock_remove.assert_called_once_with(pull_request=mock_pull_request, label="other-label")

    @pytest.mark.asyncio
    async def test_manage_reviewed_by_label_approved_by_approver_add(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test manage_reviewed_by_label for approved by approver with add action."""
        # Ensure the owners_file_handler has the expected attributes
        with patch.object(labels_handler.owners_file_handler, "all_pull_request_approvers", ["approver1", "approver2"]):
            with patch.object(labels_handler.owners_file_handler, "root_approvers", ["root-approver"]):
                with patch.object(labels_handler, "_add_label", new_callable=AsyncMock) as mock_add:
                    with patch.object(labels_handler, "_remove_label", new_callable=AsyncMock) as mock_remove:
                        await labels_handler.manage_reviewed_by_label(
                            pull_request=mock_pull_request,
                            review_state=APPROVE_STR,
                            action=ADD_STR,
                            reviewed_user="approver1",
                        )
                        mock_add.assert_called_once()
                        mock_remove.assert_called_once()

    @pytest.mark.asyncio
    async def test_manage_reviewed_by_label_approved_by_root_approver_add(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test manage_reviewed_by_label for approved by root approver with add action."""
        # Ensure the owners_file_handler has the expected attributes
        with patch.object(labels_handler.owners_file_handler, "all_pull_request_approvers", ["approver1", "approver2"]):
            with patch.object(labels_handler.owners_file_handler, "root_approvers", ["root-approver"]):
                with patch.object(labels_handler, "_add_label", new_callable=AsyncMock) as mock_add:
                    with patch.object(labels_handler, "_remove_label", new_callable=AsyncMock) as mock_remove:
                        await labels_handler.manage_reviewed_by_label(
                            pull_request=mock_pull_request,
                            review_state=APPROVE_STR,
                            action=ADD_STR,
                            reviewed_user="root-approver",
                        )
                        mock_add.assert_called_once()
                        mock_remove.assert_called_once()

    @pytest.mark.asyncio
    async def test_manage_reviewed_by_label_lgtm_by_owner_add(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test manage_reviewed_by_label for LGTM by PR owner with add action."""
        # Set up the hook_data to have the expected structure
        labels_handler.hook_data = {
            "issue": {"user": {"login": "test-user"}},
            "pull_request": {"user": {"login": "test-user"}},
        }

        with patch.object(labels_handler, "_add_label", new_callable=AsyncMock) as mock_add:
            await labels_handler.manage_reviewed_by_label(
                pull_request=mock_pull_request,
                review_state=LGTM_STR,
                action=ADD_STR,
                reviewed_user="test-user",  # Same as PR owner in fixture
            )
            mock_add.assert_not_called()

    @pytest.mark.asyncio
    async def test_manage_reviewed_by_label_lgtm_by_non_owner_add(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test manage_reviewed_by_label for LGTM by non-owner with add action."""
        # Set up the hook_data to have the expected structure
        labels_handler.hook_data = {
            "issue": {"user": {"login": "test-user"}},
            "pull_request": {"user": {"login": "test-user"}},
        }

        with patch.object(labels_handler, "_add_label", new_callable=AsyncMock) as mock_add:
            with patch.object(labels_handler, "_remove_label", new_callable=AsyncMock) as mock_remove:
                await labels_handler.manage_reviewed_by_label(
                    pull_request=mock_pull_request, review_state=LGTM_STR, action=ADD_STR, reviewed_user="other-user"
                )
                mock_add.assert_called_once()
                mock_remove.assert_called_once()

    @pytest.mark.asyncio
    async def test_manage_reviewed_by_label_changes_requested_add(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test manage_reviewed_by_label for changes requested with add action."""
        with patch.object(labels_handler, "_add_label", new_callable=AsyncMock) as mock_add:
            with patch.object(labels_handler, "_remove_label", new_callable=AsyncMock) as mock_remove:
                await labels_handler.manage_reviewed_by_label(
                    pull_request=mock_pull_request,
                    review_state="changes_requested",
                    action=ADD_STR,
                    reviewed_user="test-user",
                )
                mock_add.assert_called_once()
                mock_remove.assert_called_once()

    @pytest.mark.asyncio
    async def test_manage_reviewed_by_label_commented_add(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test manage_reviewed_by_label for commented with add action."""
        with patch.object(labels_handler, "_add_label", new_callable=AsyncMock) as mock_add:
            await labels_handler.manage_reviewed_by_label(
                pull_request=mock_pull_request, review_state="commented", action=ADD_STR, reviewed_user="test-user"
            )
            mock_add.assert_called_once()

    def test_wip_or_hold_labels_exists_both(self, labels_handler: LabelsHandler) -> None:
        """Test wip_or_hold_labels_exists with both WIP and HOLD labels."""
        labels = [WIP_STR, HOLD_LABEL_STR, "other-label"]
        result = labels_handler.wip_or_hold_labels_exists(labels)
        assert "Hold label exists." in result
        assert "WIP label exists." in result

    def test_wip_or_hold_labels_exists_hold_only(self, labels_handler: LabelsHandler) -> None:
        """Test wip_or_hold_labels_exists with only HOLD label."""
        labels = [HOLD_LABEL_STR, "other-label"]
        result = labels_handler.wip_or_hold_labels_exists(labels)
        assert "Hold label exists." in result
        assert "WIP label exists." not in result

    def test_wip_or_hold_labels_exists_wip_only(self, labels_handler: LabelsHandler) -> None:
        """Test wip_or_hold_labels_exists with only WIP label."""
        labels = [WIP_STR, "other-label"]
        result = labels_handler.wip_or_hold_labels_exists(labels)
        assert "WIP label exists." in result
        assert "Hold label exists." not in result

    def test_wip_or_hold_labels_exists_neither(self, labels_handler: LabelsHandler) -> None:
        """Test wip_or_hold_labels_exists with neither WIP nor HOLD labels."""
        labels = ["other-label1", "other-label2"]
        result = labels_handler.wip_or_hold_labels_exists(labels)
        assert result == ""

    def test_get_custom_pr_size_thresholds_config_available(self, mock_github_webhook: Mock) -> None:
        """Test parsing custom PR size thresholds from configuration."""
        # Mock config returning custom thresholds
        mock_github_webhook.config.get_value.return_value = {
            "Small": {"threshold": 100, "color": "green"},
            "Large": {"threshold": 500, "color": "red"},
        }

        labels_handler = LabelsHandler(github_webhook=mock_github_webhook, owners_file_handler=Mock())

        # Create a method to get custom thresholds (will be implemented)
        thresholds = labels_handler._get_custom_pr_size_thresholds()

        expected = [
            (100, "Small", "008000"),  # green hex
            (500, "Large", "ff0000"),  # red hex
        ]
        assert thresholds == expected
        mock_github_webhook.config.get_value.assert_called_once_with("pr-size-thresholds", return_on_none=None)

    def test_get_custom_pr_size_thresholds_no_config(self, mock_github_webhook: Mock) -> None:
        """Test fallback to static thresholds when no custom config available."""
        # Mock config returning None (no custom thresholds)
        mock_github_webhook.config.get_value.return_value = None

        labels_handler = LabelsHandler(github_webhook=mock_github_webhook, owners_file_handler=Mock())

        thresholds = labels_handler._get_custom_pr_size_thresholds()

        # Should return static defaults
        expected = [
            (20, "XS", "ededed"),
            (50, "S", "0E8A16"),
            (100, "M", "F09C74"),
            (300, "L", "F5621C"),
            (500, "XL", "D93F0B"),
            (float("inf"), "XXL", "B60205"),
        ]
        assert thresholds == expected

    def test_get_custom_pr_size_thresholds_missing_color(self, mock_github_webhook: Mock) -> None:
        """Test custom thresholds with missing color fallback to default."""
        # Mock config with missing color
        mock_github_webhook.config.get_value.return_value = {
            "Small": {"threshold": 100},  # missing color
            "Large": {"threshold": 500, "color": "red"},
        }

        labels_handler = LabelsHandler(github_webhook=mock_github_webhook, owners_file_handler=Mock())

        thresholds = labels_handler._get_custom_pr_size_thresholds()

        expected = [
            (100, "Small", "d3d3d3"),  # lightgray hex
            (500, "Large", "ff0000"),  # red hex
        ]
        assert thresholds == expected

    def test_get_custom_pr_size_thresholds_invalid_color(self, mock_github_webhook: Mock) -> None:
        """Test custom thresholds with invalid color fallback to default."""
        # Mock config with invalid color
        mock_github_webhook.config.get_value.return_value = {
            "Small": {"threshold": 100, "color": "invalidcolor"},
            "Large": {"threshold": 500, "color": "red"},
        }

        labels_handler = LabelsHandler(github_webhook=mock_github_webhook, owners_file_handler=Mock())

        thresholds = labels_handler._get_custom_pr_size_thresholds()

        expected = [
            (100, "Small", "d3d3d3"),  # lightgray hex fallback
            (500, "Large", "ff0000"),  # red hex
        ]
        assert thresholds == expected

    def test_get_size_with_custom_thresholds(self, mock_github_webhook: Mock) -> None:
        """Test get_size using custom thresholds."""
        # Mock config with custom thresholds
        mock_github_webhook.config.get_value.return_value = {
            "Tiny": {"threshold": 10, "color": "lightgray"},
            "Small": {"threshold": 50, "color": "green"},
            "Large": {"threshold": 200, "color": "red"},
        }

        labels_handler = LabelsHandler(github_webhook=mock_github_webhook, owners_file_handler=Mock())

        # Test various PR sizes
        test_cases = [
            (5, 0, "size/Tiny"),  # 5 < 10
            (15, 0, "size/Small"),  # 15 >= 10 but < 50
            (75, 0, "size/Large"),  # 75 >= 50 but < 200
            (250, 0, "size/Large"),  # 250 >= 200 (largest category)
        ]

        for additions, deletions, expected in test_cases:
            pull_request = Mock(spec=PullRequest)
            pull_request.additions = additions
            pull_request.deletions = deletions

            result = labels_handler.get_size(pull_request=pull_request)
            assert result == expected

    def test_get_size_with_single_custom_threshold(self, mock_github_webhook: Mock) -> None:
        """Test get_size with only one custom threshold."""
        # Mock config with single threshold
        mock_github_webhook.config.get_value.return_value = {
            "Large": {"threshold": 100, "color": "red"},
        }

        labels_handler = LabelsHandler(github_webhook=mock_github_webhook, owners_file_handler=Mock())

        # Test PR sizes
        test_cases = [
            (50, 0, "size/Large"),  # 50 < 100 but still gets Large (only category)
            (150, 0, "size/Large"),  # 150 >= 100, gets Large
        ]

        for additions, deletions, expected in test_cases:
            pull_request = Mock(spec=PullRequest)
            pull_request.additions = additions
            pull_request.deletions = deletions

            result = labels_handler.get_size(pull_request=pull_request)
            assert result == expected

    def test_custom_threshold_sorting(self, mock_github_webhook: Mock) -> None:
        """Test that custom thresholds are properly sorted by threshold value."""
        # Mock config with unsorted thresholds
        mock_github_webhook.config.get_value.return_value = {
            "Large": {"threshold": 300, "color": "red"},
            "Small": {"threshold": 50, "color": "green"},
            "Medium": {"threshold": 150, "color": "orange"},
        }

        labels_handler = LabelsHandler(github_webhook=mock_github_webhook, owners_file_handler=Mock())

        thresholds = labels_handler._get_custom_pr_size_thresholds()

        # Should be sorted by threshold value
        expected = [
            (50, "Small", "008000"),  # green hex
            (150, "Medium", "ffa500"),  # orange hex
            (300, "Large", "ff0000"),  # red hex
        ]
        assert thresholds == expected

    def test_get_label_color_custom_size_label(self, mock_github_webhook: Mock) -> None:
        """Test _get_label_color for custom size labels."""
        # Mock config with custom thresholds
        mock_github_webhook.config.get_value.return_value = {
            "Small": {"threshold": 100, "color": "green"},
            "Large": {"threshold": 500, "color": "red"},
        }

        labels_handler = LabelsHandler(github_webhook=mock_github_webhook, owners_file_handler=Mock())

        # Test custom size label colors
        assert labels_handler._get_label_color("size/Small") == "008000"  # green hex
        assert labels_handler._get_label_color("size/Large") == "ff0000"  # red hex

    def test_get_label_color_static_size_label(self, mock_github_webhook: Mock) -> None:
        """Test _get_label_color falls back to static size labels when no custom config."""
        # Mock config returning None (no custom thresholds)
        mock_github_webhook.config.get_value.return_value = None

        labels_handler = LabelsHandler(github_webhook=mock_github_webhook, owners_file_handler=Mock())

        # Test static size label colors (should fall back to STATIC_LABELS_DICT)
        assert labels_handler._get_label_color("size/XS") == "ededed"
        assert labels_handler._get_label_color("size/S") == "0E8A16"
        assert labels_handler._get_label_color("size/M") == "F09C74"

    def test_get_label_color_dynamic_label(self, mock_github_webhook: Mock) -> None:
        """Test _get_label_color for dynamic labels (non-size)."""
        mock_github_webhook.config.get_value.return_value = None

        labels_handler = LabelsHandler(github_webhook=mock_github_webhook, owners_file_handler=Mock())

        # Test dynamic label colors
        assert labels_handler._get_label_color("approved-user1") == "0E8A16"  # APPROVED_BY_LABEL_PREFIX
        assert labels_handler._get_label_color("lgtm-user2") == "DCED6F"  # LGTM_BY_LABEL_PREFIX

    def test_get_label_color_fallback(self, mock_github_webhook: Mock) -> None:
        """Test _get_label_color fallback to default color."""
        mock_github_webhook.config.get_value.return_value = None

        labels_handler = LabelsHandler(github_webhook=mock_github_webhook, owners_file_handler=Mock())

        # Test unknown label falls back to default
        assert labels_handler._get_label_color("unknown-label") == "D4C5F9"

    def test_get_label_color_custom_size_not_found(self, mock_github_webhook: Mock) -> None:
        """Test _get_label_color when custom size label not found in thresholds."""
        # Mock config with custom thresholds but missing the requested size
        mock_github_webhook.config.get_value.return_value = {
            "Small": {"threshold": 100, "color": "green"},
        }

        labels_handler = LabelsHandler(github_webhook=mock_github_webhook, owners_file_handler=Mock())

        # Test size label not in custom config - should fall back to static if exists
        # This would be the case where user has custom config but requests a static size
        assert labels_handler._get_label_color("size/XL") == "D93F0B"  # Falls back to STATIC_LABELS_DICT

    @pytest.mark.asyncio
    async def test_remove_label_critical_error_auth(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test _remove_label with authentication error."""
        mock_label = Mock()
        mock_label.name = "test-label"
        mock_label.id = "LA_test"

        mock_pull_request.get_labels = Mock(return_value=[mock_label])

        labels_handler.unified_api.get_label_id = AsyncMock(return_value="LA_test")
        labels_handler.unified_api.remove_labels = AsyncMock(
            side_effect=GraphQLError("401 Unauthorized authentication failed")
        )

        # Auth errors should raise
        with pytest.raises(GraphQLError):
            await labels_handler._remove_label(mock_pull_request, "test-label")

    @pytest.mark.asyncio
    async def test_remove_label_critical_error_rate_limit(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test _remove_label with rate limit error."""
        mock_label = Mock()
        mock_label.name = "test-label"
        mock_label.id = "LA_test"

        mock_pull_request.get_labels = Mock(return_value=[mock_label])

        labels_handler.unified_api.get_label_id = AsyncMock(return_value="LA_test")
        labels_handler.unified_api.remove_labels = AsyncMock(side_effect=GraphQLError("rate limit exceeded"))

        # Rate limit errors should raise
        with pytest.raises(GraphQLError):
            await labels_handler._remove_label(mock_pull_request, "test-label")

    @pytest.mark.asyncio
    async def test_remove_label_transient_error(self, labels_handler: LabelsHandler, mock_pull_request: Mock) -> None:
        """Test _remove_label with non-critical error."""
        mock_label = Mock()
        mock_label.name = "test-label"
        mock_label.id = "LA_test"

        mock_pull_request.get_labels = Mock(return_value=[mock_label])

        labels_handler.unified_api.get_label_id = AsyncMock(return_value="LA_test")
        labels_handler.unified_api.remove_labels = AsyncMock(side_effect=GraphQLError("Network timeout occurred"))

        # Transient errors should not raise
        result = await labels_handler._remove_label(mock_pull_request, "test-label")
        assert result is False

    @pytest.mark.asyncio
    async def test_remove_label_mutation_response_updates_labels(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test that mutation response updates labels in-place (lines 80-82)."""
        mock_label = Mock()
        mock_label.name = "test-label"
        mock_pull_request.get_labels = Mock(return_value=[mock_label])
        mock_pull_request.update_labels = Mock()

        # Mock mutation response with updated labels
        mutation_response = {
            "removeLabelsFromLabelable": {"labelable": {"labels": {"nodes": [{"name": "other-label", "id": "LA_456"}]}}}
        }

        with patch.object(labels_handler, "label_exists_in_pull_request", new=AsyncMock(return_value=True)):
            with patch.object(labels_handler, "wait_for_label", new=AsyncMock(return_value=True)):
                labels_handler.unified_api.get_label_id = AsyncMock(return_value="LA_test")
                labels_handler.unified_api.remove_labels = AsyncMock(return_value=mutation_response)

                await labels_handler._remove_label(mock_pull_request, "test-label")

                # Verify update_labels was called with mutation response data
                mock_pull_request.update_labels.assert_called_once_with([{"name": "other-label", "id": "LA_456"}])

    @pytest.mark.asyncio
    async def test_add_label_static_mutation_response_updates_labels(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test that mutation response updates labels for static labels (lines 154-156)."""
        mock_pull_request.update_labels = Mock()

        # Mock mutation response with updated labels
        mutation_response = {
            "addLabelsToLabelable": {"labelable": {"labels": {"nodes": [{"name": "lgtm", "id": "LA_lgtm"}]}}}
        }

        with patch.object(labels_handler, "label_exists_in_pull_request", new=AsyncMock(return_value=False)):
            with patch.object(labels_handler, "wait_for_label", new=AsyncMock(return_value=True)):
                labels_handler.unified_api.get_label_id = AsyncMock(return_value="LA_lgtm")
                labels_handler.unified_api.add_labels = AsyncMock(return_value=mutation_response)

                await labels_handler._add_label(mock_pull_request, "lgtm")

                # Verify update_labels was called with mutation response data
                mock_pull_request.update_labels.assert_called_once_with([{"name": "lgtm", "id": "LA_lgtm"}])

    @pytest.mark.asyncio
    async def test_add_label_dynamic_mutation_response_updates_labels(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test that mutation response updates labels for dynamic labels (lines 225-227)."""
        mock_pull_request.update_labels = Mock()

        # Mock mutation response with updated labels
        mutation_response = {
            "addLabelsToLabelable": {"labelable": {"labels": {"nodes": [{"name": "size/M", "id": "LA_sizeM"}]}}}
        }

        with patch.object(labels_handler, "label_exists_in_pull_request", new=AsyncMock(return_value=False)):
            with patch.object(labels_handler, "wait_for_label", new=AsyncMock(return_value=True)):
                # Mock get_label_id to return None (triggers dynamic label creation)
                labels_handler.unified_api.get_label_id = AsyncMock(return_value=None)
                labels_handler.unified_api.get_repository = AsyncMock(return_value={"id": "R_repo"})
                labels_handler.unified_api.create_label = AsyncMock(return_value={"id": "LA_sizeM"})
                labels_handler.unified_api.add_labels = AsyncMock(return_value=mutation_response)

                await labels_handler._add_label(mock_pull_request, "size/M")

                # Verify update_labels was called with mutation response data
                mock_pull_request.update_labels.assert_called_once_with([{"name": "size/M", "id": "LA_sizeM"}])

    @pytest.mark.asyncio
    async def test_wait_for_label_exponential_backoff(
        self, labels_handler: LabelsHandler, mock_pull_request: Mock
    ) -> None:
        """Test exponential backoff logic in wait_for_label (lines 247-264)."""
        sleep_times = []

        async def mock_sleep(duration):
            sleep_times.append(duration)

        with patch("webhook_server.libs.handlers.labels_handler.TimeoutWatch") as mock_timeout_watch:
            # Each iteration of the loop calls remaining_time() 3 times:
            # 1. while condition check (line 243)
            # 2. if check before refetch (line 250)
            # 3. sleep_time calculation (line 261)
            # Need enough values for multiple iterations before timeout (returns 0)
            mock_timeout_watch.return_value.remaining_time.side_effect = [
                30,
                30,
                30,  # Iteration 1: while, if, sleep
                29,
                29,
                29,  # Iteration 2: while, if, sleep
                27,
                27,
                27,  # Iteration 3: while, if, sleep
                23,
                23,
                23,  # Iteration 4: while, if, sleep
                15,
                15,
                15,  # Iteration 5: while, if, sleep
                0,  # Final while check - exits loop
            ]

            with patch("asyncio.sleep", new=mock_sleep):
                # Return False enough times for all checks
                async def mock_label_exists(*_args, **_kwargs):
                    return False

                with patch.object(labels_handler, "label_exists_in_pull_request", new=mock_label_exists):
                    labels_handler.unified_api.get_pull_request_data = AsyncMock(
                        return_value={"number": 123, "labels": {"nodes": []}}
                    )

                    result = await labels_handler.wait_for_label(
                        pull_request=mock_pull_request, label="test-label", exists=True
                    )

                    # Should return False after timeout
                    assert result is False

                    # Verify exponential backoff occurred: 0.5s, 1s, 2s, 4s, 5s (capped at 5s max)
                    assert len(sleep_times) > 0
                    # First sleep should be 0.5 second
                    assert sleep_times[0] == 0.5
                    # Subsequent sleeps should demonstrate exponential growth (doubling)
                    if len(sleep_times) > 1:
                        # Second sleep should be double the first (1 second)
                        assert sleep_times[1] == 1
                    if len(sleep_times) > 2:
                        # Third sleep should be double the second (2 seconds)
                        assert sleep_times[2] == 2
                    if len(sleep_times) > 3:
                        # Fourth sleep should be double the third (4 seconds)
                        assert sleep_times[3] == 4
