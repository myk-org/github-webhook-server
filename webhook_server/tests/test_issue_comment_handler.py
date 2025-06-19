from unittest.mock import AsyncMock, Mock, patch

import pytest

from webhook_server.libs.issue_comment_handler import IssueCommentHandler
from webhook_server.utils.constants import (
    BUILD_AND_PUSH_CONTAINER_STR,
    COMMAND_ASSIGN_REVIEWER_STR,
    COMMAND_ASSIGN_REVIEWERS_STR,
    COMMAND_CHECK_CAN_MERGE_STR,
    COMMAND_CHERRY_PICK_STR,
    COMMAND_RETEST_STR,
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
        self, issue_comment_handler: IssueCommentHandler
    ) -> None:
        """Test processing comment webhook data when action is deleted."""
        issue_comment_handler.hook_data["action"] = "deleted"

        with patch.object(issue_comment_handler, "user_commands") as mock_user_commands:
            await issue_comment_handler.process_comment_webhook_data(Mock())
            mock_user_commands.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_comment_webhook_data_welcome_message(
        self, issue_comment_handler: IssueCommentHandler
    ) -> None:
        """Test processing comment webhook data with welcome message."""
        issue_comment_handler.hook_data["comment"]["body"] = "welcome-message-url"

        with patch.object(issue_comment_handler, "user_commands") as mock_user_commands:
            await issue_comment_handler.process_comment_webhook_data(Mock())
            mock_user_commands.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_comment_webhook_data_normal_comment(
        self, issue_comment_handler: IssueCommentHandler
    ) -> None:
        """Test processing comment webhook data with normal comment."""
        issue_comment_handler.hook_data["comment"]["body"] = "/retest tox"

        with patch.object(issue_comment_handler, "user_commands") as mock_user_commands:
            await issue_comment_handler.process_comment_webhook_data(Mock())
            mock_user_commands.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_comment_webhook_data_multiple_commands(
        self, issue_comment_handler: IssueCommentHandler
    ) -> None:
        """Test processing comment webhook data with multiple commands."""
        issue_comment_handler.hook_data["comment"]["body"] = "/retest tox\n/assign reviewer"

        with patch.object(issue_comment_handler, "user_commands") as mock_user_commands:
            await issue_comment_handler.process_comment_webhook_data(Mock())
            assert mock_user_commands.call_count == 2

    @pytest.mark.asyncio
    async def test_user_commands_unsupported_command(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with unsupported command."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request, command="unsupported", reviewed_user="test-user", issue_comment_id=123
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
                )
                mock_comment.assert_called_once()
                mock_reaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_commands_assign_reviewer_with_args(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with assign reviewer command with arguments."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
            with patch.object(issue_comment_handler, "_add_reviewer_by_user_comment") as mock_add_reviewer:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=f"{COMMAND_ASSIGN_REVIEWER_STR} reviewer1",
                    reviewed_user="test-user",
                    issue_comment_id=123,
                )
                mock_add_reviewer.assert_called_once_with(pull_request=mock_pull_request, reviewer="reviewer1")
                mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_assign_reviewers(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with assign reviewers command."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
            with patch.object(
                issue_comment_handler.owners_file_handler, "assign_reviewers", new_callable=AsyncMock
            ) as mock_assign:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=COMMAND_ASSIGN_REVIEWERS_STR,
                    reviewed_user="test-user",
                    issue_comment_id=123,
                )
                mock_assign.assert_awaited_once_with(pull_request=mock_pull_request)
                mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_check_can_merge(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with check can merge command."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
            with patch.object(issue_comment_handler.pull_request_handler, "check_if_can_be_merged") as mock_check:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=COMMAND_CHECK_CAN_MERGE_STR,
                    reviewed_user="test-user",
                    issue_comment_id=123,
                )
                mock_check.assert_called_once_with(pull_request=mock_pull_request)
                mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_cherry_pick(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with cherry pick command."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
            with patch.object(issue_comment_handler, "process_cherry_pick_command") as mock_cherry_pick:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=f"{COMMAND_CHERRY_PICK_STR} branch1 branch2",
                    reviewed_user="test-user",
                    issue_comment_id=123,
                )
                mock_cherry_pick.assert_called_once_with(
                    pull_request=mock_pull_request, command_args="branch1 branch2", reviewed_user="test-user"
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
                )
                mock_retest.assert_called_once_with(
                    pull_request=mock_pull_request, command_args="tox", reviewed_user="test-user"
                )
                mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_build_container_enabled(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with build container command when enabled."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
            with patch.object(issue_comment_handler.runner_handler, "run_build_container") as mock_build:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=f"{BUILD_AND_PUSH_CONTAINER_STR} args",
                    reviewed_user="test-user",
                    issue_comment_id=123,
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
                    )
                    mock_comment.assert_called_once()
                    mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_wip_add(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with wip command to add."""
        mock_pull_request = Mock()
        mock_pull_request.title = "Test PR"

        with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
            with patch.object(issue_comment_handler.labels_handler, "_add_label") as mock_add_label:
                with patch.object(mock_pull_request, "edit") as mock_edit:
                    await issue_comment_handler.user_commands(
                        pull_request=mock_pull_request, command=WIP_STR, reviewed_user="test-user", issue_comment_id=123
                    )
                    mock_add_label.assert_called_once_with(pull_request=mock_pull_request, label=WIP_STR)
                    mock_edit.assert_called_once_with(title="WIP: Test PR")
                    mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_wip_remove(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with wip command to remove."""
        mock_pull_request = Mock()
        mock_pull_request.title = "WIP: Test PR"

        with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
            with patch.object(issue_comment_handler.labels_handler, "_remove_label") as mock_remove_label:
                with patch.object(mock_pull_request, "edit") as mock_edit:
                    await issue_comment_handler.user_commands(
                        pull_request=mock_pull_request,
                        command=f"{WIP_STR} cancel",
                        reviewed_user="test-user",
                        issue_comment_id=123,
                    )
                    mock_remove_label.assert_called_once_with(pull_request=mock_pull_request, label=WIP_STR)
                    # Accept both with and without leading space
                    called_args = mock_edit.call_args[1]
                    assert called_args["title"].strip() == "Test PR"
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
                )
                mock_comment.assert_called_once()
                mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_hold_authorized_user_add(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with hold command by authorized user to add."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
            with patch.object(issue_comment_handler.labels_handler, "_add_label") as mock_add_label:
                with patch.object(issue_comment_handler.pull_request_handler, "check_if_can_be_merged") as mock_check:
                    await issue_comment_handler.user_commands(
                        pull_request=mock_pull_request,
                        command=HOLD_LABEL_STR,
                        reviewed_user="approver1",
                        issue_comment_id=123,
                    )
                    mock_add_label.assert_called_once_with(pull_request=mock_pull_request, label=HOLD_LABEL_STR)
                    mock_check.assert_called_once_with(pull_request=mock_pull_request)
                    mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_hold_authorized_user_remove(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with hold command by authorized user to remove."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
            with patch.object(issue_comment_handler.labels_handler, "_remove_label") as mock_remove_label:
                with patch.object(issue_comment_handler.pull_request_handler, "check_if_can_be_merged") as mock_check:
                    await issue_comment_handler.user_commands(
                        pull_request=mock_pull_request,
                        command=f"{HOLD_LABEL_STR} cancel",
                        reviewed_user="approver1",
                        issue_comment_id=123,
                    )
                    mock_remove_label.assert_called_once_with(pull_request=mock_pull_request, label=HOLD_LABEL_STR)
                    mock_check.assert_called_once_with(pull_request=mock_pull_request)
                    mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_verified_add(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with verified command to add."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
            with patch.object(issue_comment_handler.labels_handler, "_add_label") as mock_add_label:
                with patch.object(issue_comment_handler.check_run_handler, "set_verify_check_success") as mock_success:
                    await issue_comment_handler.user_commands(
                        pull_request=mock_pull_request,
                        command=VERIFIED_LABEL_STR,
                        reviewed_user="test-user",
                        issue_comment_id=123,
                    )
                    mock_add_label.assert_called_once_with(pull_request=mock_pull_request, label=VERIFIED_LABEL_STR)
                    mock_success.assert_called_once()
                    mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_verified_remove(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with verified command to remove."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
            with patch.object(issue_comment_handler.labels_handler, "_remove_label") as mock_remove_label:
                with patch.object(issue_comment_handler.check_run_handler, "set_verify_check_queued") as mock_queued:
                    await issue_comment_handler.user_commands(
                        pull_request=mock_pull_request,
                        command=f"{VERIFIED_LABEL_STR} cancel",
                        reviewed_user="test-user",
                        issue_comment_id=123,
                    )
                    mock_remove_label.assert_called_once_with(pull_request=mock_pull_request, label=VERIFIED_LABEL_STR)
                    mock_queued.assert_called_once()
                    mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_custom_label(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with custom label command."""
        mock_pull_request = Mock()
        # Patch USER_LABELS_DICT to include 'bug'
        with patch("webhook_server.libs.issue_comment_handler.USER_LABELS_DICT", {"bug": "Bug label"}):
            with patch.object(issue_comment_handler, "create_comment_reaction") as mock_reaction:
                with patch.object(
                    issue_comment_handler.labels_handler, "label_by_user_comment", new_callable=AsyncMock
                ) as mock_label:
                    await issue_comment_handler.user_commands(
                        pull_request=mock_pull_request, command="bug", reviewed_user="test-user", issue_comment_id=123
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
                    pull_request=mock_pull_request, issue_comment_id=123, reaction=REACTIONS.ok
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
                    pull_request=mock_pull_request, reviewer="@reviewer1"
                )
                mock_create_request.assert_called_once_with(["reviewer1"])

    @pytest.mark.asyncio
    async def test_add_reviewer_by_user_comment_not_contributor(
        self, issue_comment_handler: IssueCommentHandler
    ) -> None:
        """Test adding reviewer by user comment when user is not a contributor."""
        mock_pull_request = Mock()
        mock_contributor = Mock()
        mock_contributor.login = "other-user"

        with patch.object(issue_comment_handler.repository, "get_contributors", return_value=[mock_contributor]):
            with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
                await issue_comment_handler._add_reviewer_by_user_comment(
                    pull_request=mock_pull_request, reviewer="reviewer1"
                )
                mock_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_cherry_pick_command_existing_branches(
        self, issue_comment_handler: IssueCommentHandler
    ) -> None:
        """Test processing cherry pick command with existing branches."""
        mock_pull_request = Mock()
        mock_pull_request.title = "Test PR"
        # Patch is_merged as a method
        with patch.object(mock_pull_request, "is_merged", new=Mock(return_value=False)):
            with patch.object(issue_comment_handler.repository, "get_branch") as mock_get_branch:
                with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
                    with patch.object(issue_comment_handler.labels_handler, "_add_label") as mock_add_label:
                        await issue_comment_handler.process_cherry_pick_command(
                            pull_request=mock_pull_request, command_args="branch1 branch2", reviewed_user="test-user"
                        )
                        mock_get_branch.assert_any_call("branch1")
                        mock_get_branch.assert_any_call("branch2")
                        mock_comment.assert_called_once()
                        assert mock_add_label.call_count == 2

    @pytest.mark.asyncio
    async def test_process_cherry_pick_command_non_existing_branches(
        self, issue_comment_handler: IssueCommentHandler
    ) -> None:
        """Test processing cherry pick command with non-existing branches."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler.repository, "get_branch", side_effect=Exception("Branch not found")):
            with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
                await issue_comment_handler.process_cherry_pick_command(
                    pull_request=mock_pull_request, command_args="branch1 branch2", reviewed_user="test-user"
                )
                mock_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_cherry_pick_command_merged_pr(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test processing cherry pick command for merged PR."""
        mock_pull_request = Mock()
        # Patch is_merged as a method
        with patch.object(mock_pull_request, "is_merged", new=Mock(return_value=True)):
            with patch.object(issue_comment_handler.repository, "get_branch"):
                with patch.object(issue_comment_handler.runner_handler, "cherry_pick") as mock_cherry_pick:
                    await issue_comment_handler.process_cherry_pick_command(
                        pull_request=mock_pull_request, command_args="branch1", reviewed_user="test-user"
                    )
                    mock_cherry_pick.assert_called_once_with(
                        pull_request=mock_pull_request, target_branch="branch1", reviewed_user="test-user"
                    )

    @pytest.mark.asyncio
    async def test_process_retest_command_no_target_tests(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test processing retest command with no target tests."""
        mock_pull_request = Mock()

        with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
            await issue_comment_handler.process_retest_command(
                pull_request=mock_pull_request, command_args="", reviewed_user="test-user"
            )
            mock_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_retest_command_all_with_other_tests(
        self, issue_comment_handler: IssueCommentHandler
    ) -> None:
        """Test processing retest command with 'all' and other tests."""
        mock_pull_request = Mock()

        with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
            await issue_comment_handler.process_retest_command(
                pull_request=mock_pull_request, command_args="all tox", reviewed_user="test-user"
            )
            mock_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_retest_command_all_only(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test processing retest command with 'all' only."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler.runner_handler, "run_tox") as mock_run_tox:
            await issue_comment_handler.process_retest_command(
                pull_request=mock_pull_request, command_args="all", reviewed_user="test-user"
            )
            mock_run_tox.assert_called_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_process_retest_command_specific_tests(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test processing retest command with specific tests."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler.runner_handler, "run_tox") as mock_run_tox:
            with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
                await issue_comment_handler.process_retest_command(
                    pull_request=mock_pull_request, command_args="tox unsupported-test", reviewed_user="test-user"
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
        with patch.object(
            issue_comment_handler.owners_file_handler,
            "is_user_valid_to_run_commands",
            new=AsyncMock(return_value=False),
        ):
            with patch.object(issue_comment_handler.runner_handler, "run_tox") as mock_run_tox:
                await issue_comment_handler.process_retest_command(
                    pull_request=mock_pull_request, command_args="tox", reviewed_user="test-user"
                )
                mock_run_tox.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_retest_command_async_task_exception(
        self, issue_comment_handler: IssueCommentHandler
    ) -> None:
        """Test processing retest command with async task exception."""
        mock_pull_request = Mock()

        with patch.object(issue_comment_handler.runner_handler, "run_tox", side_effect=Exception("Test error")):
            with patch.object(issue_comment_handler.logger, "error") as mock_error:
                await issue_comment_handler.process_retest_command(
                    pull_request=mock_pull_request, command_args="tox", reviewed_user="test-user"
                )
                mock_error.assert_called_once()
