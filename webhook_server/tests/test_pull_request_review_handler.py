"""Tests for webhook_server.libs.handlers.pull_request_review_handler module."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from github.PullRequest import PullRequest

from webhook_server.libs.handlers.pull_request_review_handler import PullRequestReviewHandler
from webhook_server.utils.constants import ADD_STR, APPROVE_STR


class TestPullRequestReviewHandler:
    """Test suite for PullRequestReviewHandler class."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance."""
        mock_webhook = Mock()
        mock_webhook.hook_data = {
            "action": "submitted",
            "review": {"user": {"login": "test-reviewer"}, "state": "approved", "body": "Great work! /approve"},
        }
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.ctx = None
        return mock_webhook

    @pytest.fixture
    def mock_owners_file_handler(self) -> Mock:
        """Create a mock OwnersFileHandler instance."""
        mock_handler = Mock()
        mock_handler.all_pull_request_approvers = ["approver1", "approver2"]
        mock_handler.is_user_valid_to_run_commands = AsyncMock(return_value=True)
        return mock_handler

    @pytest.fixture
    def pull_request_review_handler(
        self, mock_github_webhook: Mock, mock_owners_file_handler: Mock
    ) -> PullRequestReviewHandler:
        """Create a PullRequestReviewHandler instance with mocked dependencies."""
        return PullRequestReviewHandler(mock_github_webhook, mock_owners_file_handler)

    @pytest.mark.asyncio
    async def test_process_pull_request_review_webhook_data_submitted_action(
        self, pull_request_review_handler: PullRequestReviewHandler
    ) -> None:
        """Test processing pull request review webhook data with submitted action."""
        mock_pull_request = Mock(spec=PullRequest)

        with patch.object(
            pull_request_review_handler.labels_handler, "manage_reviewed_by_label", new_callable=AsyncMock
        ) as mock_manage_label:
            with patch.object(
                pull_request_review_handler.labels_handler, "label_by_user_comment"
            ) as mock_label_comment:
                with patch(
                    "webhook_server.libs.handlers.pull_request_review_handler.call_test_oracle",
                    new_callable=AsyncMock,
                ):
                    await pull_request_review_handler.process_pull_request_review_webhook_data(mock_pull_request)

                    mock_manage_label.assert_called_once_with(
                        pull_request=mock_pull_request,
                        review_state="approved",
                        action=ADD_STR,
                        reviewed_user="test-reviewer",
                    )
                    mock_label_comment.assert_called_once_with(
                        pull_request=mock_pull_request,
                        user_requested_label=APPROVE_STR,
                        remove=False,
                        reviewed_user="test-reviewer",
                    )

    @pytest.mark.asyncio
    async def test_process_pull_request_review_webhook_data_non_submitted_action(
        self, pull_request_review_handler: PullRequestReviewHandler
    ) -> None:
        """Test processing pull request review webhook data with non-submitted action."""
        mock_pull_request = Mock(spec=PullRequest)
        pull_request_review_handler.hook_data["action"] = "edited"

        with patch.object(
            pull_request_review_handler.labels_handler, "manage_reviewed_by_label", new_callable=AsyncMock
        ) as mock_manage_label:
            with patch.object(
                pull_request_review_handler.labels_handler, "label_by_user_comment"
            ) as mock_label_comment:
                await pull_request_review_handler.process_pull_request_review_webhook_data(mock_pull_request)

                mock_manage_label.assert_not_called()
                mock_label_comment.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_pull_request_review_webhook_data_no_body(
        self, pull_request_review_handler: PullRequestReviewHandler
    ) -> None:
        """Test processing pull request review webhook data with no review body."""
        mock_pull_request = Mock(spec=PullRequest)
        pull_request_review_handler.hook_data["review"]["body"] = None

        with patch.object(
            pull_request_review_handler.labels_handler, "manage_reviewed_by_label", new_callable=AsyncMock
        ) as mock_manage_label:
            with patch.object(
                pull_request_review_handler.labels_handler, "label_by_user_comment"
            ) as mock_label_comment:
                with patch(
                    "webhook_server.libs.handlers.pull_request_review_handler.call_test_oracle",
                    new_callable=AsyncMock,
                ):
                    await pull_request_review_handler.process_pull_request_review_webhook_data(mock_pull_request)

                    mock_manage_label.assert_called_once_with(
                        pull_request=mock_pull_request,
                        review_state="approved",
                        action=ADD_STR,
                        reviewed_user="test-reviewer",
                    )
                    mock_label_comment.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_pull_request_review_webhook_data_empty_body(
        self, pull_request_review_handler: PullRequestReviewHandler
    ) -> None:
        """Test processing pull request review webhook data with empty review body."""
        mock_pull_request = Mock(spec=PullRequest)
        pull_request_review_handler.hook_data["review"]["body"] = ""

        with patch.object(
            pull_request_review_handler.labels_handler, "manage_reviewed_by_label", new_callable=AsyncMock
        ) as mock_manage_label:
            with patch.object(
                pull_request_review_handler.labels_handler, "label_by_user_comment"
            ) as mock_label_comment:
                with patch(
                    "webhook_server.libs.handlers.pull_request_review_handler.call_test_oracle",
                    new_callable=AsyncMock,
                ):
                    await pull_request_review_handler.process_pull_request_review_webhook_data(mock_pull_request)

                    mock_manage_label.assert_called_once_with(
                        pull_request=mock_pull_request,
                        review_state="approved",
                        action=ADD_STR,
                        reviewed_user="test-reviewer",
                    )
                    mock_label_comment.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_pull_request_review_webhook_data_body_without_approve(
        self, pull_request_review_handler: PullRequestReviewHandler
    ) -> None:
        """Test processing pull request review webhook data with body that doesn't contain /approve."""
        mock_pull_request = Mock(spec=PullRequest)
        pull_request_review_handler.hook_data["review"]["body"] = "Good work, but needs some changes"

        with patch.object(
            pull_request_review_handler.labels_handler, "manage_reviewed_by_label", new_callable=AsyncMock
        ) as mock_manage_label:
            with patch.object(
                pull_request_review_handler.labels_handler, "label_by_user_comment"
            ) as mock_label_comment:
                with patch(
                    "webhook_server.libs.handlers.pull_request_review_handler.call_test_oracle",
                    new_callable=AsyncMock,
                ):
                    await pull_request_review_handler.process_pull_request_review_webhook_data(mock_pull_request)

                    mock_manage_label.assert_called_once_with(
                        pull_request=mock_pull_request,
                        review_state="approved",
                        action=ADD_STR,
                        reviewed_user="test-reviewer",
                    )
                    mock_label_comment.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_pull_request_review_webhook_data_different_review_states(
        self, pull_request_review_handler: PullRequestReviewHandler
    ) -> None:
        """Test processing pull request review webhook data with different review states."""
        mock_pull_request = Mock(spec=PullRequest)

        test_states = ["commented", "changes_requested", "dismissed"]

        for state in test_states:
            pull_request_review_handler.hook_data["review"]["state"] = state

            with patch.object(
                pull_request_review_handler.labels_handler, "manage_reviewed_by_label"
            ) as mock_manage_label:
                with patch.object(
                    pull_request_review_handler.labels_handler, "label_by_user_comment"
                ) as mock_label_comment:
                    with patch(
                        "webhook_server.libs.handlers.pull_request_review_handler.call_test_oracle",
                        new_callable=AsyncMock,
                    ):
                        await pull_request_review_handler.process_pull_request_review_webhook_data(mock_pull_request)

                        mock_manage_label.assert_called_once_with(
                            pull_request=mock_pull_request,
                            review_state=state,
                            action=ADD_STR,
                            reviewed_user="test-reviewer",
                        )
                        mock_label_comment.assert_called_once_with(
                            pull_request=mock_pull_request,
                            user_requested_label=APPROVE_STR,
                            remove=False,
                            reviewed_user="test-reviewer",
                        )

                        # Reset mocks for next iteration
                        mock_manage_label.reset_mock()
                        mock_label_comment.reset_mock()

    @pytest.mark.asyncio
    async def test_process_pull_request_review_webhook_data_different_users(
        self, pull_request_review_handler: PullRequestReviewHandler
    ) -> None:
        """Test processing pull request review webhook data with different users."""
        mock_pull_request = Mock(spec=PullRequest)

        test_users = ["user1", "user2", "maintainer", "contributor"]

        for user in test_users:
            pull_request_review_handler.hook_data["review"]["user"]["login"] = user

            with patch.object(
                pull_request_review_handler.labels_handler, "manage_reviewed_by_label"
            ) as mock_manage_label:
                with patch.object(
                    pull_request_review_handler.labels_handler, "label_by_user_comment"
                ) as mock_label_comment:
                    with patch(
                        "webhook_server.libs.handlers.pull_request_review_handler.call_test_oracle",
                        new_callable=AsyncMock,
                    ):
                        await pull_request_review_handler.process_pull_request_review_webhook_data(mock_pull_request)

                        mock_manage_label.assert_called_once_with(
                            pull_request=mock_pull_request, review_state="approved", action=ADD_STR, reviewed_user=user
                        )
                        mock_label_comment.assert_called_once_with(
                            pull_request=mock_pull_request,
                            user_requested_label=APPROVE_STR,
                            remove=False,
                            reviewed_user=user,
                        )

                        # Reset mocks for next iteration
                        mock_manage_label.reset_mock()
                        mock_label_comment.reset_mock()

    @pytest.mark.asyncio
    async def test_process_pull_request_review_webhook_data_exact_approve_match(
        self, pull_request_review_handler: PullRequestReviewHandler
    ) -> None:
        """Test processing pull request review webhook data with exact /approve match."""
        mock_pull_request = Mock(spec=PullRequest)

        test_bodies = ["/approve", "Great work! /approve", "LGTM /approve thanks", "/approve this looks good"]

        for body in test_bodies:
            pull_request_review_handler.hook_data["review"]["body"] = body

            with patch.object(
                pull_request_review_handler.labels_handler, "manage_reviewed_by_label"
            ) as mock_manage_label:
                with patch.object(
                    pull_request_review_handler.labels_handler, "label_by_user_comment"
                ) as mock_label_comment:
                    with patch(
                        "webhook_server.libs.handlers.pull_request_review_handler.call_test_oracle",
                        new_callable=AsyncMock,
                    ):
                        await pull_request_review_handler.process_pull_request_review_webhook_data(mock_pull_request)

                        mock_manage_label.assert_called_once_with(
                            pull_request=mock_pull_request,
                            review_state="approved",
                            action=ADD_STR,
                            reviewed_user="test-reviewer",
                        )
                        mock_label_comment.assert_called_once_with(
                            pull_request=mock_pull_request,
                            user_requested_label=APPROVE_STR,
                            remove=False,
                            reviewed_user="test-reviewer",
                        )

                        # Reset mocks for next iteration
                        mock_manage_label.reset_mock()
                        mock_label_comment.reset_mock()

    @pytest.mark.asyncio
    async def test_calls_test_oracle_on_approval(self, pull_request_review_handler: PullRequestReviewHandler) -> None:
        """Test that test oracle is fired as a background task when PR review is approved."""
        mock_pull_request = Mock(spec=PullRequest)

        with patch.object(
            pull_request_review_handler.labels_handler, "manage_reviewed_by_label", new_callable=AsyncMock
        ):
            with patch.object(
                pull_request_review_handler.labels_handler, "label_by_user_comment", new_callable=AsyncMock
            ):
                with patch(
                    "webhook_server.libs.handlers.pull_request_review_handler.call_test_oracle",
                ) as mock_oracle:
                    with patch("asyncio.create_task") as mock_create_task:
                        await pull_request_review_handler.process_pull_request_review_webhook_data(mock_pull_request)

                        mock_oracle.assert_called_once_with(
                            github_webhook=pull_request_review_handler.github_webhook,
                            pull_request=mock_pull_request,
                            trigger="approved",
                        )
                        mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_does_not_call_test_oracle_on_non_approval(
        self, pull_request_review_handler: PullRequestReviewHandler
    ) -> None:
        """Test that test oracle is NOT called for non-approval reviews."""
        mock_pull_request = Mock(spec=PullRequest)
        pull_request_review_handler.hook_data["review"]["state"] = "commented"
        pull_request_review_handler.hook_data["review"]["body"] = "Looks good"

        with patch.object(
            pull_request_review_handler.labels_handler, "manage_reviewed_by_label", new_callable=AsyncMock
        ):
            with patch.object(
                pull_request_review_handler.labels_handler, "label_by_user_comment", new_callable=AsyncMock
            ):
                with patch(
                    "webhook_server.libs.handlers.pull_request_review_handler.call_test_oracle",
                    new_callable=AsyncMock,
                ) as mock_oracle:
                    await pull_request_review_handler.process_pull_request_review_webhook_data(mock_pull_request)

                    mock_oracle.assert_not_called()
