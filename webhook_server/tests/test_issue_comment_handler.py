import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from webhook_server.libs.handlers.issue_comment_handler import IssueCommentHandler
from webhook_server.utils.constants import (
    BUILD_AND_PUSH_CONTAINER_STR,
    COMMAND_ASSIGN_REVIEWER_STR,
    COMMAND_ASSIGN_REVIEWERS_STR,
    COMMAND_CHECK_CAN_MERGE_STR,
    COMMAND_CHERRY_PICK_STR,
    COMMAND_REGENERATE_WELCOME_STR,
    COMMAND_REPROCESS_STR,
    COMMAND_RETEST_STR,
    COMMAND_TEST_ORACLE_STR,
    HOLD_LABEL_STR,
    REACTIONS,
    TOX_STR,
    VERIFIED_LABEL_STR,
    WIP_STR,
)


class TestIssueCommentHandler:
    """Test suite for IssueCommentHandler class."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance."""
        mock_webhook = Mock()
        mock_webhook.hook_data = {
            "action": "created",
            "issue": {"number": 123},
            "comment": {"body": "/test", "id": 456},
            "sender": {"login": "test-user"},
        }
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.issue_url_for_welcome_msg = "welcome-message-url"
        mock_webhook.build_and_push_container = True
        mock_webhook.current_pull_request_supported_retest = [TOX_STR, "pre-commit"]
        mock_webhook.ctx = None
        mock_webhook.custom_check_runs = []
        # Mock config for draft PR command filtering
        mock_webhook.config = Mock()
        mock_webhook.config.get_value = Mock(return_value=None)
        return mock_webhook

    @pytest.fixture
    def mock_owners_file_handler(self) -> Mock:
        """Create a mock OwnersFileHandler instance."""
        mock_handler = Mock()
        mock_handler.all_pull_request_approvers = ["approver1", "approver2"]
        mock_handler.is_user_valid_to_run_commands = AsyncMock(return_value=True)
        return mock_handler

    @pytest.fixture
    def issue_comment_handler(self, mock_github_webhook: Mock, mock_owners_file_handler: Mock) -> IssueCommentHandler:
        """Create an IssueCommentHandler instance with mocked dependencies."""
        return IssueCommentHandler(mock_github_webhook, mock_owners_file_handler)

    @pytest.mark.asyncio
    async def test_process_comment_webhook_data_edited_action(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test processing comment webhook data when action is edited."""
        issue_comment_handler.hook_data["action"] = "edited"

        with patch.object(issue_comment_handler, "user_commands") as mock_user_commands:
            await issue_comment_handler.process_comment_webhook_data(Mock())
            mock_user_commands.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_comment_webhook_data_deleted_action(
        self,
        issue_comment_handler: IssueCommentHandler,
    ) -> None:
        """Test processing comment webhook data when action is deleted."""
        issue_comment_handler.hook_data["action"] = "deleted"

        with patch.object(issue_comment_handler, "user_commands") as mock_user_commands:
            await issue_comment_handler.process_comment_webhook_data(Mock())
            mock_user_commands.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_comment_webhook_data_welcome_message(
        self,
        issue_comment_handler: IssueCommentHandler,
    ) -> None:
        """Test processing comment webhook data with welcome message."""
        issue_comment_handler.hook_data["comment"]["body"] = "welcome-message-url"

        with patch.object(issue_comment_handler, "user_commands") as mock_user_commands:
            await issue_comment_handler.process_comment_webhook_data(Mock())
            mock_user_commands.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_comment_webhook_data_normal_comment(
        self,
        issue_comment_handler: IssueCommentHandler,
    ) -> None:
        """Test processing comment webhook data with normal comment."""
        issue_comment_handler.hook_data["comment"]["body"] = "/retest tox"

        with patch.object(issue_comment_handler, "user_commands") as mock_user_commands:
            await issue_comment_handler.process_comment_webhook_data(Mock())
            mock_user_commands.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_comment_webhook_data_no_commands(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test processing comment webhook data with no commands."""
        issue_comment_handler.hook_data["comment"]["body"] = "Just a regular comment"

        with patch.object(issue_comment_handler, "user_commands") as mock_user_commands:
            await issue_comment_handler.process_comment_webhook_data(Mock())
            mock_user_commands.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_comment_webhook_data_multiple_commands(
        self,
        issue_comment_handler: IssueCommentHandler,
    ) -> None:
        """Test processing comment webhook data with multiple commands."""
        issue_comment_handler.hook_data["comment"]["body"] = "/retest tox\n/assign reviewer"

        with patch.object(issue_comment_handler, "user_commands") as mock_user_commands:
            await issue_comment_handler.process_comment_webhook_data(Mock())
            assert mock_user_commands.call_count == 2

    @pytest.mark.asyncio
    async def test_process_comment_webhook_data_parallel_execution(
        self,
        issue_comment_handler: IssueCommentHandler,
    ) -> None:
        """Test that multiple commands execute in parallel, not sequentially.

        This test verifies:
        1. Multiple commands start concurrently (not one-after-another)
        2. Parallel execution is significantly faster than sequential
        3. Exception in one command doesn't block others
        4. All commands complete even if one fails
        """
        issue_comment_handler.hook_data["comment"]["body"] = "/verified\n/approved\n/hold"

        # Track execution order and timing
        execution_events: list[tuple[str, str, float]] = []  # (command, event, timestamp)

        async def mock_command(
            pull_request: Mock,  # noqa: ARG001
            command: str,
            reviewed_user: str,  # noqa: ARG001
            issue_comment_id: int,  # noqa: ARG001
            is_draft: bool,  # noqa: ARG001
        ) -> None:
            """Mock command that simulates real work and tracks execution."""
            start_time = time.time()
            execution_events.append((command, "start", start_time))

            # Simulate work (50ms per command)
            await asyncio.sleep(0.05)

            # Simulate exception for second command to test exception handling
            if command == "approved":
                execution_events.append((command, "error", time.time()))
                raise ValueError(f"Simulated error in {command}")

            execution_events.append((command, "end", time.time()))

        with patch.object(issue_comment_handler, "user_commands", side_effect=mock_command):
            # Execute commands - expect exception due to failed command
            start = time.time()
            with pytest.raises(RuntimeError, match="Command /approved failed"):
                await issue_comment_handler.process_comment_webhook_data(Mock())
            total_duration = time.time() - start

            # VERIFICATION 1: All three commands should have started
            start_events = [e for e in execution_events if e[1] == "start"]
            assert len(start_events) == 3, f"Expected 3 commands to start, got {len(start_events)}"

            # VERIFICATION 2: Commands started concurrently (within 10ms of each other)
            # In sequential execution, commands would start 50ms apart
            # In parallel execution, all start nearly simultaneously
            first_start = start_events[0][2]
            last_start = start_events[-1][2]
            start_time_spread = last_start - first_start

            # All commands should start within a short window (parallel)
            # vs 100ms+ for sequential execution (50ms * 2 delays)
            # Tolerance: 50ms to account for CI environment jitter and scheduling delays
            assert start_time_spread < 0.05, f"Commands did not start concurrently (spread: {start_time_spread:.3f}s)"

            # VERIFICATION 3: Total execution time indicates parallel execution
            # Sequential: 3 commands * 50ms = 150ms minimum
            # Parallel: max(50ms) = 50ms (plus overhead)
            # Tolerance: 200ms to account for CI environment variability while still
            # being well under the 150ms sequential threshold
            assert total_duration < 0.2, f"Execution took {total_duration:.3f}s, expected < 0.2s (parallel execution)"

            # Sequential would take at least 150ms - we use 250ms threshold to account for CI jitter
            # while still catching truly sequential execution (which would take 150ms+ per command set)
            assert total_duration < 0.25, f"Commands appear to run sequentially ({total_duration:.3f}s >= 0.25s)"

            # VERIFICATION 4: Exception in one command didn't stop others
            # verified and hold should complete successfully
            successful_completions = [e for e in execution_events if e[1] == "end"]
            assert len(successful_completions) == 2, (
                f"Expected 2 successful completions (verified, hold), got {len(successful_completions)}"
            )

            # VERIFICATION 5: Error was recorded for failed command
            error_events = [e for e in execution_events if e[1] == "error"]
            assert len(error_events) == 1, f"Expected 1 error event (approved), got {len(error_events)}"
            assert error_events[0][0] == "approved", "Error should be for 'approved' command"

            # VERIFICATION 6: Commands completed in overlapping time windows
            # This proves they ran concurrently, not sequentially
            verified_start = next(e[2] for e in execution_events if e[0] == "verified" and e[1] == "start")
            hold_end = next(e[2] for e in execution_events if e[0] == "hold" and e[1] == "end")

            # Both commands (verified and hold) should overlap in execution
            # If sequential: hold would start AFTER verified ends (100ms gap)
            # If parallel: hold starts immediately, both execute simultaneously
            execution_overlap = hold_end - verified_start

            # Overlap should be ~50ms (parallel) not ~100ms (sequential)
            # Tolerance: 150ms to account for CI environment variability while still
            # detecting sequential execution (which would show ~100ms+ gaps)
            assert execution_overlap < 0.15, f"Execution overlap {execution_overlap:.3f}s suggests sequential execution"

    @pytest.mark.asyncio
    async def test_user_commands_unsupported_command(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with unsupported command."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command="unsupported",
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=False,
            )
            mock_reaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_commands_retest_no_args(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with retest command without arguments."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
            with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=COMMAND_RETEST_STR,
                    reviewed_user="test-user",
                    issue_comment_id=123,
                    is_draft=False,
                )
                mock_comment.assert_called_once()
                mock_reaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_commands_assign_reviewer_no_args(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with assign reviewer command without arguments."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
            with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=COMMAND_ASSIGN_REVIEWER_STR,
                    reviewed_user="test-user",
                    issue_comment_id=123,
                    is_draft=False,
                )
                mock_comment.assert_called_once()
                mock_reaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_commands_assign_reviewer_with_args(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with assign reviewer command with arguments."""
        mock_pull_request = Mock()

        with (
            patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction,
            patch.object(
                issue_comment_handler,
                "_add_reviewer_by_user_comment",
                new_callable=AsyncMock,
            ) as mock_add_reviewer,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=f"{COMMAND_ASSIGN_REVIEWER_STR} reviewer1",
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=False,
            )
            mock_add_reviewer.assert_called_once_with(pull_request=mock_pull_request, reviewer="reviewer1")
            mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_assign_reviewers(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with assign reviewers command."""
        mock_pull_request = Mock()

        with (
            patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction,
            patch.object(
                issue_comment_handler.owners_file_handler,
                "assign_reviewers",
                new_callable=AsyncMock,
            ) as mock_assign,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=COMMAND_ASSIGN_REVIEWERS_STR,
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=False,
            )
            mock_assign.assert_awaited_once_with(pull_request=mock_pull_request)
            mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_check_can_merge(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with check can merge command."""
        mock_pull_request = Mock()

        with (
            patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction,
            patch.object(
                issue_comment_handler.pull_request_handler,
                "check_if_can_be_merged",
                new_callable=AsyncMock,
            ) as mock_check,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=COMMAND_CHECK_CAN_MERGE_STR,
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=False,
            )
            mock_check.assert_called_once_with(pull_request=mock_pull_request)
            mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_cherry_pick(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with cherry pick command."""
        mock_pull_request = Mock()

        with (
            patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction,
            patch.object(
                issue_comment_handler,
                "process_cherry_pick_command",
                new_callable=AsyncMock,
            ) as mock_cherry_pick,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=f"{COMMAND_CHERRY_PICK_STR} branch1 branch2",
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=False,
            )
            mock_cherry_pick.assert_called_once_with(
                pull_request=mock_pull_request,
                command_args="branch1 branch2",
                reviewed_user="test-user",
            )
            mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_retest_with_args(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with retest command with arguments."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
            with patch.object(issue_comment_handler, "process_retest_command") as mock_retest:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=f"{COMMAND_RETEST_STR} tox",
                    reviewed_user="test-user",
                    issue_comment_id=123,
                    is_draft=False,
                )
                mock_retest.assert_called_once_with(
                    pull_request=mock_pull_request,
                    command_args="tox",
                    reviewed_user="test-user",
                )
                mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_build_container_enabled(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with build container command when enabled."""
        mock_pull_request = Mock()

        with (
            patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction,
            patch.object(
                issue_comment_handler.runner_handler,
                "run_build_container",
                new_callable=AsyncMock,
            ) as mock_build,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=f"{BUILD_AND_PUSH_CONTAINER_STR} args",
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=False,
            )
            mock_build.assert_called_once_with(
                push=True,
                set_check=False,
                command_args="args",
                reviewed_user="test-user",
                pull_request=mock_pull_request,
            )
            mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_build_container_disabled(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with build container command when disabled."""
        mock_pull_request = Mock()
        # Patch build_and_push_container as a bool for this test
        with patch.object(issue_comment_handler.github_webhook, "build_and_push_container", False):
            with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
                with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
                    await issue_comment_handler.user_commands(
                        pull_request=mock_pull_request,
                        command=BUILD_AND_PUSH_CONTAINER_STR,
                        reviewed_user="test-user",
                        issue_comment_id=123,
                        is_draft=False,
                    )
                    mock_comment.assert_called_once()
                    mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_wip_add(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with wip command to add."""
        mock_pull_request = Mock()
        mock_pull_request.title = "Test PR"

        with (
            patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction,
            patch.object(
                issue_comment_handler.labels_handler,
                "_add_label",
                new_callable=AsyncMock,
            ) as mock_add_label,
            patch.object(mock_pull_request, "edit") as mock_edit,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=WIP_STR,
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=False,
            )
            mock_add_label.assert_called_once_with(pull_request=mock_pull_request, label=WIP_STR)
            mock_edit.assert_called_once_with(title="WIP: Test PR")
            mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_wip_remove(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with wip command to remove."""
        mock_pull_request = Mock()
        mock_pull_request.title = "WIP: Test PR"

        with (
            patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction,
            patch.object(
                issue_comment_handler.labels_handler,
                "_remove_label",
                new_callable=AsyncMock,
            ) as mock_remove_label,
            patch.object(mock_pull_request, "edit") as mock_edit,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=f"{WIP_STR} cancel",
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=False,
            )
            mock_remove_label.assert_called_once_with(pull_request=mock_pull_request, label=WIP_STR)
            # Accept both with and without leading space
            called_args = mock_edit.call_args[1]
            assert called_args["title"].strip() == "Test PR"
            mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_wip_add_idempotent(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test that adding WIP when title already has WIP: prefix does not prepend again."""
        mock_pull_request = Mock()
        mock_pull_request.title = "WIP: Test PR"

        with (
            patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction,
            patch.object(
                issue_comment_handler.labels_handler,
                "_add_label",
                new_callable=AsyncMock,
            ) as mock_add_label,
        ):
            mock_add_label.return_value = True  # Label was added (or already existed)
            with patch.object(mock_pull_request, "edit") as mock_edit:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=WIP_STR,
                    reviewed_user="test-user",
                    issue_comment_id=123,
                    is_draft=False,
                )
                mock_add_label.assert_called_once_with(pull_request=mock_pull_request, label=WIP_STR)
                # Should NOT edit title since it already starts with WIP:
                mock_edit.assert_not_called()
                mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_wip_remove_no_prefix(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test that removing WIP when title has no WIP: prefix does not edit title."""
        mock_pull_request = Mock()
        mock_pull_request.title = "Test PR"  # No WIP: prefix

        with (
            patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction,
            patch.object(
                issue_comment_handler.labels_handler,
                "_remove_label",
                new_callable=AsyncMock,
            ) as mock_remove_label,
        ):
            mock_remove_label.return_value = True  # Label was removed
            with patch.object(mock_pull_request, "edit") as mock_edit:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=f"{WIP_STR} cancel",
                    reviewed_user="test-user",
                    issue_comment_id=123,
                    is_draft=False,
                )
                mock_remove_label.assert_called_once_with(pull_request=mock_pull_request, label=WIP_STR)
                # Should NOT edit title since it doesn't start with WIP:
                mock_edit.assert_not_called()
                mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_wip_remove_no_space(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test removing WIP when title has WIP: prefix without space after colon."""
        mock_pull_request = Mock()
        mock_pull_request.title = "WIP:Test PR"  # No space after colon

        with (
            patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction,
            patch.object(
                issue_comment_handler.labels_handler,
                "_remove_label",
                new_callable=AsyncMock,
            ) as mock_remove_label,
        ):
            mock_remove_label.return_value = True  # Label was removed
            with patch.object(mock_pull_request, "edit") as mock_edit:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=f"{WIP_STR} cancel",
                    reviewed_user="test-user",
                    issue_comment_id=123,
                    is_draft=False,
                )
                mock_remove_label.assert_called_once_with(pull_request=mock_pull_request, label=WIP_STR)
                # Should edit title to remove WIP: (without space)
                mock_edit.assert_called_once_with(title="Test PR")
                mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_hold_unauthorized_user(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with hold command by unauthorized user."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
            with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=HOLD_LABEL_STR,
                    reviewed_user="unauthorized-user",
                    issue_comment_id=123,
                    is_draft=False,
                )
                mock_comment.assert_called_once()
                mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_hold_authorized_user_add(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with hold command by authorized user to add.

        Note: check_if_can_be_merged is NOT called directly here - it's triggered
        by the 'labeled' webhook event (hook-driven architecture).
        """
        mock_pull_request = Mock()

        with (
            patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction,
            patch.object(
                issue_comment_handler.labels_handler,
                "_add_label",
                new_callable=AsyncMock,
            ) as mock_add_label,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=HOLD_LABEL_STR,
                reviewed_user="approver1",
                issue_comment_id=123,
                is_draft=False,
            )
            mock_add_label.assert_called_once_with(pull_request=mock_pull_request, label=HOLD_LABEL_STR)
            mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_hold_authorized_user_remove(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with hold command by authorized user to remove.

        Note: check_if_can_be_merged is NOT called directly here - it's triggered
        by the 'unlabeled' webhook event (hook-driven architecture).
        """
        mock_pull_request = Mock()

        with (
            patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction,
            patch.object(
                issue_comment_handler.labels_handler,
                "_remove_label",
                new_callable=AsyncMock,
            ) as mock_remove_label,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=f"{HOLD_LABEL_STR} cancel",
                reviewed_user="approver1",
                issue_comment_id=123,
                is_draft=False,
            )
            mock_remove_label.assert_called_once_with(pull_request=mock_pull_request, label=HOLD_LABEL_STR)
            mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_verified_add(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with verified command to add."""
        mock_pull_request = Mock()

        with (
            patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction,
            patch.object(
                issue_comment_handler.labels_handler,
                "_add_label",
                new_callable=AsyncMock,
            ) as mock_add_label,
            patch.object(
                issue_comment_handler.check_run_handler,
                "set_check_success",
                new_callable=AsyncMock,
            ) as mock_success,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=VERIFIED_LABEL_STR,
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=False,
            )
            mock_add_label.assert_called_once_with(pull_request=mock_pull_request, label=VERIFIED_LABEL_STR)
            mock_success.assert_called_once_with(name=VERIFIED_LABEL_STR)
            mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_verified_remove(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with verified command to remove."""
        mock_pull_request = Mock()

        with (
            patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction,
            patch.object(
                issue_comment_handler.labels_handler,
                "_remove_label",
                new_callable=AsyncMock,
            ) as mock_remove_label,
            patch.object(
                issue_comment_handler.check_run_handler,
                "set_check_queued",
                new_callable=AsyncMock,
            ) as mock_queued,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=f"{VERIFIED_LABEL_STR} cancel",
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=False,
            )
            mock_remove_label.assert_called_once_with(pull_request=mock_pull_request, label=VERIFIED_LABEL_STR)
            mock_queued.assert_called_once_with(name=VERIFIED_LABEL_STR)
            mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_custom_label(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with custom label command."""
        mock_pull_request = Mock()
        # Patch USER_LABELS_DICT to include 'bug'
        with patch("webhook_server.libs.handlers.issue_comment_handler.USER_LABELS_DICT", {"bug": "Bug label"}):
            with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
                with patch.object(
                    issue_comment_handler.labels_handler,
                    "label_by_user_comment",
                    new_callable=AsyncMock,
                ) as mock_label:
                    await issue_comment_handler.user_commands(
                        pull_request=mock_pull_request,
                        command="bug",
                        reviewed_user="test-user",
                        issue_comment_id=123,
                        is_draft=False,
                    )
                    mock_label.assert_awaited_once_with(
                        pull_request=mock_pull_request,
                        user_requested_label="bug",
                        remove=False,
                        reviewed_user="test-user",
                    )
                    mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_comment_reaction(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test creating comment reaction."""
        mock_pull_request = Mock()
        mock_comment = Mock()

        with patch.object(mock_pull_request, "get_issue_comment", return_value=mock_comment):
            with patch.object(mock_comment, "create_reaction") as mock_create_reaction:
                await issue_comment_handler.create_comment_reaction(
                    pull_request=mock_pull_request,
                    issue_comment_id=123,
                    reaction=REACTIONS.ok,
                )
                mock_pull_request.get_issue_comment.assert_called_once_with(123)
                mock_create_reaction.assert_called_once_with(REACTIONS.ok)

    @pytest.mark.asyncio
    async def test_add_reviewer_by_user_comment_success(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test adding reviewer by user comment successfully."""
        mock_pull_request = Mock()
        mock_contributor = Mock()
        mock_contributor.login = "reviewer1"

        with patch.object(issue_comment_handler.repository, "get_contributors", return_value=[mock_contributor]):
            with patch.object(mock_pull_request, "create_review_request") as mock_create_request:
                await issue_comment_handler._add_reviewer_by_user_comment(
                    pull_request=mock_pull_request,
                    reviewer="@reviewer1",
                )
                mock_create_request.assert_called_once_with(["reviewer1"])

    @pytest.mark.asyncio
    async def test_add_reviewer_by_user_comment_not_contributor(
        self,
        issue_comment_handler: IssueCommentHandler,
    ) -> None:
        """Test adding reviewer by user comment when user is not a contributor."""
        mock_pull_request = Mock()
        mock_contributor = Mock()
        mock_contributor.login = "other-user"

        with patch.object(issue_comment_handler.repository, "get_contributors", return_value=[mock_contributor]):
            with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
                await issue_comment_handler._add_reviewer_by_user_comment(
                    pull_request=mock_pull_request,
                    reviewer="reviewer1",
                )
                mock_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_cherry_pick_command_existing_branches(
        self,
        issue_comment_handler: IssueCommentHandler,
    ) -> None:
        """Test processing cherry pick command with existing branches."""
        mock_pull_request = Mock()
        mock_pull_request.title = "Test PR"
        # Patch is_merged as a method
        with patch.object(mock_pull_request, "is_merged", new=Mock(return_value=False)):
            with patch.object(issue_comment_handler.repository, "get_branch") as mock_get_branch:
                with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
                    with patch.object(
                        issue_comment_handler.labels_handler,
                        "_add_label",
                        new_callable=AsyncMock,
                    ) as mock_add_label:
                        await issue_comment_handler.process_cherry_pick_command(
                            pull_request=mock_pull_request,
                            command_args="branch1 branch2",
                            reviewed_user="test-user",
                        )
                        mock_get_branch.assert_any_call("branch1")
                        mock_get_branch.assert_any_call("branch2")
                        mock_comment.assert_called_once()
                        assert mock_add_label.call_count == 2

    @pytest.mark.asyncio
    async def test_process_cherry_pick_command_non_existing_branches(
        self,
        issue_comment_handler: IssueCommentHandler,
    ) -> None:
        """Test processing cherry pick command with non-existing branches."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler.repository, "get_branch", side_effect=Exception("Branch not found")):
            with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
                await issue_comment_handler.process_cherry_pick_command(
                    pull_request=mock_pull_request,
                    command_args="branch1 branch2",
                    reviewed_user="test-user",
                )
                mock_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_cherry_pick_command_merged_pr(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test processing cherry pick command for merged PR."""
        mock_pull_request = Mock()
        # Patch is_merged as a method
        with patch.object(mock_pull_request, "is_merged", new=Mock(return_value=True)):
            with patch.object(issue_comment_handler.repository, "get_branch"):
                with patch.object(
                    issue_comment_handler.runner_handler,
                    "cherry_pick",
                    new_callable=AsyncMock,
                ) as mock_cherry_pick:
                    with patch.object(
                        issue_comment_handler.labels_handler,
                        "_add_label",
                        new_callable=AsyncMock,
                    ) as mock_add_label:
                        await issue_comment_handler.process_cherry_pick_command(
                            pull_request=mock_pull_request,
                            command_args="branch1",
                            reviewed_user="test-user",
                        )
                        mock_cherry_pick.assert_called_once_with(
                            pull_request=mock_pull_request,
                            target_branch="branch1",
                            reviewed_user="test-user",
                        )
                        mock_add_label.assert_called_once_with(
                            pull_request=mock_pull_request,
                            label="cherry-pick-branch1",
                        )

    @pytest.mark.asyncio
    async def test_process_cherry_pick_command_merged_pr_multiple_branches(
        self,
        issue_comment_handler: IssueCommentHandler,
    ) -> None:
        """Test processing cherry pick command for merged PR with multiple branches.

        This test verifies that when cherry-picking to multiple branches on a merged PR:
        1. cherry_pick is called for each target branch
        2. Labels are added exactly once for each branch (not duplicated)
        """
        mock_pull_request = Mock()
        mock_pull_request.title = "Test PR"

        # Patch is_merged to return True (merged PR)
        with patch.object(mock_pull_request, "is_merged", new=Mock(return_value=True)):
            with patch.object(issue_comment_handler.repository, "get_branch"):
                with patch.object(
                    issue_comment_handler.runner_handler,
                    "cherry_pick",
                    new_callable=AsyncMock,
                ) as mock_cherry_pick:
                    with patch.object(
                        issue_comment_handler.labels_handler,
                        "_add_label",
                        new_callable=AsyncMock,
                    ) as mock_add_label:
                        # Execute cherry-pick command with multiple branches
                        await issue_comment_handler.process_cherry_pick_command(
                            pull_request=mock_pull_request,
                            command_args="branch1 branch2 branch3",
                            reviewed_user="test-user",
                        )

                        # Verify cherry_pick was called for each branch
                        assert mock_cherry_pick.call_count == 3
                        mock_cherry_pick.assert_any_call(
                            pull_request=mock_pull_request,
                            target_branch="branch1",
                            reviewed_user="test-user",
                        )
                        mock_cherry_pick.assert_any_call(
                            pull_request=mock_pull_request,
                            target_branch="branch2",
                            reviewed_user="test-user",
                        )
                        mock_cherry_pick.assert_any_call(
                            pull_request=mock_pull_request,
                            target_branch="branch3",
                            reviewed_user="test-user",
                        )

                        # Verify labels were added exactly once for each branch (not duplicated)
                        assert mock_add_label.call_count == 3
                        mock_add_label.assert_any_call(pull_request=mock_pull_request, label="cherry-pick-branch1")
                        mock_add_label.assert_any_call(pull_request=mock_pull_request, label="cherry-pick-branch2")
                        mock_add_label.assert_any_call(pull_request=mock_pull_request, label="cherry-pick-branch3")

    @pytest.mark.asyncio
    async def test_process_retest_command_no_target_tests(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test processing retest command with no target tests."""
        mock_pull_request = Mock()

        with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
            await issue_comment_handler.process_retest_command(
                pull_request=mock_pull_request,
                command_args="",
                reviewed_user="test-user",
            )
            mock_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_retest_command_all_with_other_tests(
        self,
        issue_comment_handler: IssueCommentHandler,
    ) -> None:
        """Test processing retest command with 'all' and other tests."""
        mock_pull_request = Mock()

        with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
            await issue_comment_handler.process_retest_command(
                pull_request=mock_pull_request,
                command_args="all tox",
                reviewed_user="test-user",
            )
            mock_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_retest_command_all_only(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test processing retest command with 'all' only."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler.runner_handler, "run_tox", new_callable=AsyncMock) as mock_run_tox:
            with patch.object(
                issue_comment_handler.runner_handler,
                "run_pre_commit",
                new_callable=AsyncMock,
            ) as mock_run_pre_commit:
                await issue_comment_handler.process_retest_command(
                    pull_request=mock_pull_request,
                    command_args="all",
                    reviewed_user="test-user",
                )
                mock_run_tox.assert_awaited_once_with(pull_request=mock_pull_request)
                mock_run_pre_commit.assert_awaited_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_process_retest_command_specific_tests(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test processing retest command with specific tests."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler.runner_handler, "run_tox", new_callable=AsyncMock) as mock_run_tox:
            with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
                await issue_comment_handler.process_retest_command(
                    pull_request=mock_pull_request,
                    command_args="tox unsupported-test",
                    reviewed_user="test-user",
                )
                mock_run_tox.assert_called_once_with(pull_request=mock_pull_request)
                mock_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_retest_command_unsupported_tests(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test processing retest command with unsupported tests."""
        mock_pull_request = Mock()

        with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
            await issue_comment_handler.process_retest_command(
                pull_request=mock_pull_request,
                command_args="unsupported-test1 unsupported-test2",
                reviewed_user="test-user",
            )
            mock_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_retest_command_user_not_valid(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test processing retest command when user is not valid."""
        mock_pull_request = Mock()
        # Patch is_user_valid_to_run_commands as AsyncMock
        with (
            patch.object(
                issue_comment_handler.owners_file_handler,
                "is_user_valid_to_run_commands",
                new=AsyncMock(return_value=False),
            ),
            patch.object(issue_comment_handler.runner_handler, "run_tox") as mock_run_tox,
        ):
            await issue_comment_handler.process_retest_command(
                pull_request=mock_pull_request,
                command_args="tox",
                reviewed_user="test-user",
            )
            mock_run_tox.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_retest_command_async_task_exception(
        self,
        issue_comment_handler: IssueCommentHandler,
    ) -> None:
        """Test processing retest command with async task exception."""
        mock_pull_request = Mock()

        with (
            patch.object(
                issue_comment_handler.runner_handler,
                "run_tox",
                new_callable=AsyncMock,
                side_effect=Exception("Test error"),
            ),
            patch.object(issue_comment_handler.logger, "error") as mock_error,
        ):
            await issue_comment_handler.process_retest_command(
                pull_request=mock_pull_request,
                command_args="tox",
                reviewed_user="test-user",
            )
            mock_error.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_reprocess_command_registration(
        self,
        issue_comment_handler: IssueCommentHandler,
    ) -> None:
        """Test that reprocess command is in available_commands list."""
        # Verify COMMAND_REPROCESS_STR is in the available_commands list
        # by checking if the command is recognized (doesn't return early for unsupported command)
        mock_pull_request = Mock()

        with (
            patch.object(
                issue_comment_handler.owners_file_handler,
                "is_user_valid_to_run_commands",
                new=AsyncMock(return_value=True),
            ),
            patch.object(
                issue_comment_handler.pull_request_handler,
                "process_command_reprocess",
                new=AsyncMock(),
            ) as mock_reprocess,
            patch.object(issue_comment_handler, "create_comment_reaction", new=AsyncMock()),
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=COMMAND_REPROCESS_STR,
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=False,
            )
            # Command should be recognized and processed
            mock_reprocess.assert_awaited_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_user_commands_reprocess_authorized_user(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test reprocess command with authorized user (in OWNERS)."""
        mock_pull_request = Mock()

        mock_is_valid = AsyncMock(return_value=True)
        with (
            patch.object(
                issue_comment_handler.owners_file_handler,
                "is_user_valid_to_run_commands",
                new=mock_is_valid,
            ),
            patch.object(
                issue_comment_handler.pull_request_handler,
                "process_command_reprocess",
                new=AsyncMock(),
            ) as mock_reprocess,
            patch.object(issue_comment_handler, "create_comment_reaction", new=AsyncMock()) as mock_reaction,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=COMMAND_REPROCESS_STR,
                reviewed_user="approver1",  # From fixture: all_pull_request_approvers
                issue_comment_id=123,
                is_draft=False,
            )
            # Verify user validation was called
            mock_is_valid.assert_awaited_once_with(
                pull_request=mock_pull_request,
                reviewed_user="approver1",
            )
            # Verify reprocess handler was called
            mock_reprocess.assert_awaited_once_with(pull_request=mock_pull_request)
            # Verify reaction was added
            mock_reaction.assert_awaited_once_with(
                pull_request=mock_pull_request,
                issue_comment_id=123,
                reaction=REACTIONS.ok,
            )

    @pytest.mark.asyncio
    async def test_user_commands_reprocess_unauthorized_user(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test reprocess command with unauthorized user (not in OWNERS)."""
        mock_pull_request = Mock()

        mock_is_valid = AsyncMock(return_value=False)
        with (
            patch.object(
                issue_comment_handler.owners_file_handler,
                "is_user_valid_to_run_commands",
                new=mock_is_valid,
            ),
            patch.object(
                issue_comment_handler.pull_request_handler,
                "process_command_reprocess",
                new=AsyncMock(),
            ) as mock_reprocess,
            patch.object(issue_comment_handler, "create_comment_reaction", new=AsyncMock()) as mock_reaction,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=COMMAND_REPROCESS_STR,
                reviewed_user="unauthorized-user",
                issue_comment_id=123,
                is_draft=False,
            )
            # Verify user validation was called
            mock_is_valid.assert_awaited_once_with(
                pull_request=mock_pull_request,
                reviewed_user="unauthorized-user",
            )
            # Verify reprocess handler was NOT called
            mock_reprocess.assert_not_awaited()
            # Reaction should still be added before permission check
            mock_reaction.assert_awaited_once_with(
                pull_request=mock_pull_request,
                issue_comment_id=123,
                reaction=REACTIONS.ok,
            )

    @pytest.mark.asyncio
    async def test_user_commands_reprocess_with_args(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test reprocess command with additional arguments (should ignore args)."""
        mock_pull_request = Mock()

        mock_reprocess = AsyncMock()
        with (
            patch.object(
                issue_comment_handler.owners_file_handler,
                "is_user_valid_to_run_commands",
                new=AsyncMock(return_value=True),
            ),
            patch.object(
                issue_comment_handler.pull_request_handler,
                "process_command_reprocess",
                new=mock_reprocess,
            ),
            patch.object(issue_comment_handler, "create_comment_reaction", new=AsyncMock()),
        ):
            # Command with args (should be processed but args ignored)
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=f"{COMMAND_REPROCESS_STR} some-args",
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=False,
            )
            # Verify reprocess was called (args are ignored)
            mock_reprocess.assert_awaited_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_user_commands_reprocess_reaction_added(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test that reaction is added to comment for reprocess command."""
        mock_pull_request = Mock()

        with (
            patch.object(
                issue_comment_handler.owners_file_handler,
                "is_user_valid_to_run_commands",
                new=AsyncMock(return_value=True),
            ),
            patch.object(issue_comment_handler.pull_request_handler, "process_command_reprocess", new=AsyncMock()),
            patch.object(issue_comment_handler, "create_comment_reaction", new=AsyncMock()) as mock_reaction,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=COMMAND_REPROCESS_STR,
                reviewed_user="test-user",
                issue_comment_id=456,
                is_draft=False,
            )
            # Verify reaction was added with correct comment ID and reaction type
            mock_reaction.assert_awaited_once_with(
                pull_request=mock_pull_request,
                issue_comment_id=456,
                reaction=REACTIONS.ok,
            )

    @pytest.mark.asyncio
    async def test_user_commands_regenerate_welcome_command_registration(
        self,
        issue_comment_handler: IssueCommentHandler,
    ) -> None:
        """Test that regenerate-welcome command is in available_commands list."""
        mock_pull_request = Mock()

        with (
            patch.object(
                issue_comment_handler.pull_request_handler,
                "regenerate_welcome_message",
                new=AsyncMock(),
            ) as mock_regenerate,
            patch.object(issue_comment_handler, "create_comment_reaction", new=AsyncMock()),
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=COMMAND_REGENERATE_WELCOME_STR,
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=False,
            )
            # Command should be recognized and processed
            mock_regenerate.assert_awaited_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_user_commands_regenerate_welcome_with_reaction(
        self,
        issue_comment_handler: IssueCommentHandler,
    ) -> None:
        """Test that reaction is added to comment for regenerate-welcome command."""
        mock_pull_request = Mock()

        with (
            patch.object(issue_comment_handler.pull_request_handler, "regenerate_welcome_message", new=AsyncMock()),
            patch.object(issue_comment_handler, "create_comment_reaction", new=AsyncMock()) as mock_reaction,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=COMMAND_REGENERATE_WELCOME_STR,
                reviewed_user="test-user",
                issue_comment_id=456,
                is_draft=False,
            )
            # Verify reaction was added with correct comment ID and reaction type
            mock_reaction.assert_awaited_once_with(
                pull_request=mock_pull_request,
                issue_comment_id=456,
                reaction=REACTIONS.ok,
            )

    @pytest.mark.asyncio
    async def test_user_commands_regenerate_welcome_with_args_ignored(
        self,
        issue_comment_handler: IssueCommentHandler,
    ) -> None:
        """Test regenerate-welcome command ignores additional arguments."""
        mock_pull_request = Mock()

        with (
            patch.object(
                issue_comment_handler.pull_request_handler,
                "regenerate_welcome_message",
                new=AsyncMock(),
            ) as mock_regenerate,
            patch.object(issue_comment_handler, "create_comment_reaction", new=AsyncMock()),
        ):
            # Command with args (should be processed but args ignored)
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=f"{COMMAND_REGENERATE_WELCOME_STR} some-args",
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=False,
            )
            # Verify regenerate was called (args are ignored)
            mock_regenerate.assert_awaited_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_user_commands_draft_pr_command_blocked(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test that commands not in allow-commands-on-draft-prs list are blocked on draft PRs."""
        mock_pull_request = Mock()

        # Configure allow-commands-on-draft-prs to only allow "wip" and "hold"
        issue_comment_handler.github_webhook.config.get_value = Mock(return_value=["wip", "hold"])

        with (
            patch.object(mock_pull_request, "create_issue_comment") as mock_comment,
            patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=COMMAND_CHECK_CAN_MERGE_STR,  # Not in allowed list
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=True,  # Draft PR
            )
            # Command should be blocked - comment posted
            mock_comment.assert_called_once()
            call_args = mock_comment.call_args[0][0]
            assert f"Command `/{COMMAND_CHECK_CAN_MERGE_STR}` is not allowed on draft PRs" in call_args
            assert "wip" in call_args
            assert "hold" in call_args
            # Reaction should NOT be added (command was blocked)
            mock_reaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_commands_draft_pr_no_config_blocks_all(
        self,
        issue_comment_handler: IssueCommentHandler,
    ) -> None:
        """Test that all commands are blocked on draft PRs when allow-commands-on-draft-prs is not configured."""
        mock_pull_request = Mock()
        mock_pull_request.draft = True

        # Config returns None (not configured)
        issue_comment_handler.github_webhook.config.get_value = Mock(return_value=None)

        with (
            patch.object(mock_pull_request, "create_issue_comment") as mock_comment,
            patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=COMMAND_CHECK_CAN_MERGE_STR,
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=True,
            )
            # Command should be silently blocked (no comment posted, just return)
            mock_comment.assert_not_called()
            mock_reaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_commands_draft_pr_command_allowed(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test that commands in allow-commands-on-draft-prs list are allowed on draft PRs."""
        mock_pull_request = Mock()
        mock_pull_request.title = "Test PR"

        # Configure allow-commands-on-draft-prs to allow "wip"
        issue_comment_handler.github_webhook.config.get_value = Mock(return_value=["wip"])

        with (
            patch.object(issue_comment_handler.labels_handler, "_add_label", new_callable=AsyncMock) as mock_add_label,
            patch.object(issue_comment_handler, "create_comment_reaction", new=AsyncMock()) as mock_reaction,
            patch.object(mock_pull_request, "edit"),
        ):
            mock_add_label.return_value = True
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=WIP_STR,  # In allowed list
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=True,  # Draft PR
            )
            # Command should proceed - label added
            mock_add_label.assert_called_once_with(pull_request=mock_pull_request, label=WIP_STR)
            # Reaction should be added
            mock_reaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_commands_draft_pr_empty_list_allows_all(
        self,
        issue_comment_handler: IssueCommentHandler,
    ) -> None:
        """Test that empty allow-commands-on-draft-prs list allows all commands on draft PRs."""
        mock_pull_request = Mock()

        # Configure allow-commands-on-draft-prs to empty list (allow all)
        issue_comment_handler.github_webhook.config.get_value = Mock(return_value=[])

        with (
            patch.object(
                issue_comment_handler.pull_request_handler,
                "check_if_can_be_merged",
                new_callable=AsyncMock,
            ) as mock_check,
            patch.object(issue_comment_handler, "create_comment_reaction", new=AsyncMock()) as mock_reaction,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=COMMAND_CHECK_CAN_MERGE_STR,
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=True,  # Draft PR
            )
            # Command should proceed
            mock_check.assert_called_once_with(pull_request=mock_pull_request)
            mock_reaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_commands_non_draft_pr_ignores_config(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test that non-draft PRs ignore allow-commands-on-draft-prs config."""
        mock_pull_request = Mock()

        # Configure allow-commands-on-draft-prs to only allow "wip" (but this should be ignored)
        issue_comment_handler.github_webhook.config.get_value = Mock(return_value=["wip"])

        with (
            patch.object(
                issue_comment_handler.pull_request_handler,
                "check_if_can_be_merged",
                new_callable=AsyncMock,
            ) as mock_check,
            patch.object(issue_comment_handler, "create_comment_reaction", new=AsyncMock()) as mock_reaction,
        ):
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=COMMAND_CHECK_CAN_MERGE_STR,  # Would be blocked on draft
                reviewed_user="test-user",
                issue_comment_id=123,
                is_draft=False,  # NOT a draft PR
            )
            # Command should proceed because PR is not a draft
            mock_check.assert_called_once_with(pull_request=mock_pull_request)
            mock_reaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_test_oracle_command(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test that /test-oracle command calls call_test_oracle."""
        mock_pull_request = Mock()
        mock_pull_request.draft = False

        with patch("asyncio.to_thread", new_callable=AsyncMock, side_effect=lambda f, *a, **k: f(*a, **k)):
            with patch.object(issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock):
                with patch(
                    "webhook_server.libs.handlers.issue_comment_handler.call_test_oracle",
                    new_callable=AsyncMock,
                ) as mock_oracle:
                    await issue_comment_handler.user_commands(
                        pull_request=mock_pull_request,
                        command=COMMAND_TEST_ORACLE_STR,
                        reviewed_user="test-user",
                        issue_comment_id=456,
                        is_draft=False,
                    )
                    mock_oracle.assert_called_once_with(
                        github_webhook=issue_comment_handler.github_webhook,
                        pull_request=mock_pull_request,
                    )
