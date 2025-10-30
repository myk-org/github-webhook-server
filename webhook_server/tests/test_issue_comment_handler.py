from unittest.mock import AsyncMock, Mock, patch

import pytest
from github.GithubException import GithubException

from webhook_server.libs.handlers.issue_comment_handler import IssueCommentHandler
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
            "repository": {
                "node_id": "R_kgDOABcD1M",  # GraphQL node ID
                "id": 123456789,  # Numeric ID
                "full_name": "test-owner/test-repo",
                "name": "test-repo",
            },
        }
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.repository.full_name = "test-owner/test-repo"
        mock_webhook.repository.clone_url = "https://github.com/test-owner/test-repo.git"
        mock_webhook.issue_url_for_welcome_msg = "welcome-message-url"
        mock_webhook.build_and_push_container = True
        mock_webhook.current_pull_request_supported_retest = [TOX_STR, "pre-commit"]
        mock_webhook.token = "test-token"  # pragma: allowlist secret  # noqa: S105
        mock_webhook.clone_repo_dir = "test-repo-clone"  # Test clone directory (relative path)
        mock_webhook.pypi = {}  # Add empty pypi config to avoid subscriptable errors
        # Add new async helper methods
        mock_webhook.add_pr_comment = AsyncMock()
        mock_webhook.update_pr_title = AsyncMock()
        mock_webhook.enable_pr_automerge = AsyncMock()
        mock_webhook.request_pr_reviews = AsyncMock()
        mock_webhook.add_pr_assignee = AsyncMock()
        # Add unified_api mock with async methods
        mock_webhook.unified_api = Mock()
        mock_webhook.unified_api.get_issue_comment = AsyncMock()
        mock_webhook.unified_api.create_issue_comment = AsyncMock()
        mock_webhook.unified_api.add_assignees_by_login = AsyncMock()
        mock_webhook.unified_api.create_reaction = AsyncMock()
        mock_webhook.unified_api.create_check_run = AsyncMock()
        mock_webhook.unified_api.get_issues = AsyncMock(return_value=[])
        mock_webhook.unified_api.add_comment = AsyncMock()
        mock_webhook.unified_api.add_pr_comment = AsyncMock()
        mock_webhook.unified_api.request_pr_reviews = AsyncMock()
        mock_webhook.unified_api.edit_issue = AsyncMock()
        mock_webhook.unified_api.get_commit_check_runs = AsyncMock(return_value=[])
        return mock_webhook

    @pytest.fixture
    def mock_owners_file_handler(self) -> Mock:
        """Create a mock OwnersFileHandler instance."""
        mock_handler = Mock()
        mock_handler.all_pull_request_approvers = ["approver1", "approver2"]
        mock_handler.is_user_valid_to_run_commands = AsyncMock(return_value=True)
        return mock_handler

    @pytest.fixture
    def mock_pr(self) -> Mock:
        """Create a mock PullRequestWrapper with required attributes."""
        mock_pull_request = Mock()
        mock_pull_request.title = "Test PR"
        mock_pull_request.number = 123
        return mock_pull_request

    @pytest.fixture
    def issue_comment_handler(self, mock_github_webhook: Mock, mock_owners_file_handler: Mock) -> IssueCommentHandler:
        """Create an IssueCommentHandler instance with mocked dependencies."""
        return IssueCommentHandler(mock_github_webhook, mock_owners_file_handler)

    @pytest.mark.asyncio
    async def test_process_comment_webhook_data_edited_action(
        self, issue_comment_handler: IssueCommentHandler, mock_pr: Mock
    ) -> None:
        """Test processing comment webhook data when action is edited."""
        issue_comment_handler.hook_data["action"] = "edited"

        with patch.object(issue_comment_handler, "user_commands", new_callable=AsyncMock) as mock_user_commands:
            await issue_comment_handler.process_comment_webhook_data(mock_pr)
            mock_user_commands.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_comment_webhook_data_deleted_action(
        self, issue_comment_handler: IssueCommentHandler, mock_pr: Mock
    ) -> None:
        """Test processing comment webhook data when action is deleted."""
        issue_comment_handler.hook_data["action"] = "deleted"

        with patch.object(issue_comment_handler, "user_commands", new_callable=AsyncMock) as mock_user_commands:
            await issue_comment_handler.process_comment_webhook_data(mock_pr)
            mock_user_commands.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_comment_webhook_data_welcome_message(
        self, issue_comment_handler: IssueCommentHandler, mock_pr: Mock
    ) -> None:
        """Test processing comment webhook data with welcome message."""
        issue_comment_handler.hook_data["comment"]["body"] = "welcome-message-url"

        with patch.object(issue_comment_handler, "user_commands", new_callable=AsyncMock) as mock_user_commands:
            await issue_comment_handler.process_comment_webhook_data(mock_pr)
            mock_user_commands.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_comment_webhook_data_normal_comment(
        self, issue_comment_handler: IssueCommentHandler, mock_pr: Mock
    ) -> None:
        """Test processing comment webhook data with normal comment."""
        issue_comment_handler.hook_data["comment"]["body"] = "/retest tox"

        with patch.object(issue_comment_handler, "user_commands", new_callable=AsyncMock) as mock_user_commands:
            await issue_comment_handler.process_comment_webhook_data(mock_pr)
            mock_user_commands.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_comment_webhook_data_multiple_commands(
        self, issue_comment_handler: IssueCommentHandler, mock_pr: Mock
    ) -> None:
        """Test processing comment webhook data with multiple commands."""
        issue_comment_handler.hook_data["comment"]["body"] = "/retest tox\n/assign reviewer"

        with patch.object(issue_comment_handler, "user_commands", new_callable=AsyncMock) as mock_user_commands:
            await issue_comment_handler.process_comment_webhook_data(mock_pr)
            assert mock_user_commands.call_count == 2

    @pytest.mark.asyncio
    async def test_user_commands_unsupported_command(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with unsupported command."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock) as mock_reaction:
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request, command="unsupported", reviewed_user="test-user", issue_comment_id=123
            )
            mock_reaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_commands_retest_no_args(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with retest command without arguments."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock) as mock_reaction:
            with patch.object(
                issue_comment_handler.github_webhook.unified_api, "add_pr_comment", new_callable=AsyncMock
            ) as mock_comment:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=COMMAND_RETEST_STR,
                    reviewed_user="test-user",
                    issue_comment_id=123,
                )
                mock_comment.assert_awaited_once()
                mock_reaction.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_user_commands_assign_reviewer_no_args(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with assign reviewer command without arguments."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock) as mock_reaction:
            with patch.object(
                issue_comment_handler.github_webhook.unified_api, "add_pr_comment", new_callable=AsyncMock
            ) as mock_comment:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=COMMAND_ASSIGN_REVIEWER_STR,
                    reviewed_user="test-user",
                    issue_comment_id=123,
                )
                mock_comment.assert_awaited_once()
                mock_reaction.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_user_commands_assign_reviewer_with_args(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with assign reviewer command with arguments."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock) as mock_reaction:
            with patch.object(
                issue_comment_handler, "_add_reviewer_by_user_comment", new_callable=AsyncMock
            ) as mock_add_reviewer:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=f"{COMMAND_ASSIGN_REVIEWER_STR} reviewer1",
                    reviewed_user="test-user",
                    issue_comment_id=123,
                )
                mock_add_reviewer.assert_awaited_once_with(pull_request=mock_pull_request, reviewer="reviewer1")
                mock_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_commands_assign_reviewers(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with assign reviewers command."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock) as mock_reaction:
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
                mock_reaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_commands_check_can_merge(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with check can merge command."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock) as mock_reaction:
            with patch.object(
                issue_comment_handler.pull_request_handler, "check_if_can_be_merged", new_callable=AsyncMock
            ) as mock_check:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=COMMAND_CHECK_CAN_MERGE_STR,
                    reviewed_user="test-user",
                    issue_comment_id=123,
                )
                mock_check.assert_awaited_once_with(pull_request=mock_pull_request)
                mock_reaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_commands_cherry_pick(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with cherry pick command."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock) as mock_reaction:
            with patch.object(
                issue_comment_handler, "process_cherry_pick_command", new_callable=AsyncMock
            ) as mock_cherry_pick:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=f"{COMMAND_CHERRY_PICK_STR} branch1 branch2",
                    reviewed_user="test-user",
                    issue_comment_id=123,
                )
                mock_cherry_pick.assert_awaited_once_with(
                    pull_request=mock_pull_request, command_args="branch1 branch2", reviewed_user="test-user"
                )
                mock_reaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_commands_retest_with_args(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with retest command with arguments."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock) as mock_reaction:
            with patch.object(issue_comment_handler, "process_retest_command", new_callable=AsyncMock) as mock_retest:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=f"{COMMAND_RETEST_STR} tox",
                    reviewed_user="test-user",
                    issue_comment_id=123,
                )
                mock_retest.assert_awaited_once_with(
                    pull_request=mock_pull_request, command_args="tox", reviewed_user="test-user"
                )
                mock_reaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_commands_build_container_enabled(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with build container command when enabled."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock) as mock_reaction:
            with patch.object(
                issue_comment_handler.runner_handler, "run_build_container", new=AsyncMock()
            ) as mock_build:
                await issue_comment_handler.user_commands(
                    pull_request=mock_pull_request,
                    command=f"{BUILD_AND_PUSH_CONTAINER_STR} args",
                    reviewed_user="test-user",
                    issue_comment_id=123,
                )
                mock_build.assert_awaited_once_with(
                    push=True,
                    set_check=False,
                    command_args="args",
                    reviewed_user="test-user",
                    pull_request=mock_pull_request,
                )
                mock_reaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_commands_build_container_disabled(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with build container command when disabled."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        # Patch build_and_push_container as a bool for this test
        with patch.object(issue_comment_handler.github_webhook, "build_and_push_container", False):
            with patch.object(
                issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock
            ) as mock_reaction:
                with patch.object(
                    issue_comment_handler.github_webhook.unified_api, "add_pr_comment", new_callable=AsyncMock
                ) as mock_comment:
                    await issue_comment_handler.user_commands(
                        pull_request=mock_pull_request,
                        command=BUILD_AND_PUSH_CONTAINER_STR,
                        reviewed_user="test-user",
                        issue_comment_id=123,
                    )
                    mock_comment.assert_awaited_once()
                    mock_reaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_commands_wip_add(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with wip command to add."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        mock_pull_request.title = "Test PR"

        with patch.object(issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock) as mock_reaction:
            with patch.object(
                issue_comment_handler.labels_handler, "_add_label", new_callable=AsyncMock
            ) as mock_add_label:
                with patch.object(
                    issue_comment_handler.github_webhook.unified_api, "update_pr_title", new_callable=AsyncMock
                ) as mock_update:
                    await issue_comment_handler.user_commands(
                        pull_request=mock_pull_request, command=WIP_STR, reviewed_user="test-user", issue_comment_id=123
                    )
                    mock_add_label.assert_awaited_once_with(pull_request=mock_pull_request, label=WIP_STR)
                    # Check that update_pr_title was called with the PR and title starting with "WIP:"
                    mock_update.assert_awaited_once()
                    call_args = mock_update.call_args
                    assert call_args[0][1].startswith("WIP:")
                    mock_reaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_commands_wip_remove(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with wip command to remove."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        mock_pull_request.title = "WIP: Test PR"

        with patch.object(issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock) as mock_reaction:
            with patch.object(
                issue_comment_handler.labels_handler, "_remove_label", new_callable=AsyncMock
            ) as mock_remove_label:
                with patch.object(
                    issue_comment_handler.github_webhook.unified_api, "update_pr_title", new_callable=AsyncMock
                ) as mock_update:
                    await issue_comment_handler.user_commands(
                        pull_request=mock_pull_request,
                        command=f"{WIP_STR} cancel",
                        reviewed_user="test-user",
                        issue_comment_id=123,
                    )
                    mock_remove_label.assert_awaited_once_with(pull_request=mock_pull_request, label=WIP_STR)
                    # Verify title has "WIP:" removed
                    mock_update.assert_awaited_once()
                    call_args = mock_update.call_args
                    assert "WIP:" not in call_args[0][1]
                    assert "Test PR" in call_args[0][1]
                    mock_reaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_commands_hold_unauthorized_user(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with hold command by unauthorized user."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        # Setup mock to handle both GraphQL and REST PR types
        mock_pull_request.base.repo.owner.login = "test-owner"
        mock_pull_request.base.repo.name = "test-repo"

        with patch.object(issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock) as mock_reaction:
            await issue_comment_handler.user_commands(
                pull_request=mock_pull_request,
                command=HOLD_LABEL_STR,
                reviewed_user="unauthorized-user",
                issue_comment_id=123,
            )
            mock_reaction.assert_awaited_once()

            # Verify unauthorized user comment was posted via unified_api
            mock_add_comment = issue_comment_handler.github_webhook.unified_api.create_issue_comment
            mock_add_comment.assert_awaited_once()
            call_args = mock_add_comment.call_args
            # Arguments are: owner, repo, number, body
            comment_body = call_args[0][3]  # Fourth argument is the comment body
            assert "unauthorized-user" in comment_body
            assert "approver" in comment_body
            assert "hold" in comment_body

    @pytest.mark.asyncio
    async def test_user_commands_hold_authorized_user_add(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with hold command by authorized user to add."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock) as mock_reaction:
            with patch.object(
                issue_comment_handler.labels_handler, "_add_label", new_callable=AsyncMock
            ) as mock_add_label:
                with patch.object(
                    issue_comment_handler.pull_request_handler, "check_if_can_be_merged", new_callable=AsyncMock
                ) as mock_check:
                    await issue_comment_handler.user_commands(
                        pull_request=mock_pull_request,
                        command=HOLD_LABEL_STR,
                        reviewed_user="approver1",
                        issue_comment_id=123,
                    )
                    mock_add_label.assert_awaited_once_with(pull_request=mock_pull_request, label=HOLD_LABEL_STR)
                    mock_check.assert_awaited_once_with(pull_request=mock_pull_request)
                    mock_reaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_commands_hold_authorized_user_remove(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with hold command by authorized user to remove."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock) as mock_reaction:
            with patch.object(
                issue_comment_handler.labels_handler, "_remove_label", new_callable=AsyncMock
            ) as mock_remove_label:
                with patch.object(
                    issue_comment_handler.pull_request_handler, "check_if_can_be_merged", new_callable=AsyncMock
                ) as mock_check:
                    await issue_comment_handler.user_commands(
                        pull_request=mock_pull_request,
                        command=f"{HOLD_LABEL_STR} cancel",
                        reviewed_user="approver1",
                        issue_comment_id=123,
                    )
                    mock_remove_label.assert_awaited_once_with(pull_request=mock_pull_request, label=HOLD_LABEL_STR)
                    mock_check.assert_awaited_once_with(pull_request=mock_pull_request)
                    mock_reaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_commands_verified_add(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with verified command to add."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock) as mock_reaction:
            with patch.object(
                issue_comment_handler.labels_handler, "_add_label", new_callable=AsyncMock
            ) as mock_add_label:
                with patch.object(
                    issue_comment_handler.check_run_handler, "set_verify_check_success", new_callable=AsyncMock
                ) as mock_success:
                    await issue_comment_handler.user_commands(
                        pull_request=mock_pull_request,
                        command=VERIFIED_LABEL_STR,
                        reviewed_user="test-user",
                        issue_comment_id=123,
                    )
                    mock_add_label.assert_awaited_once_with(pull_request=mock_pull_request, label=VERIFIED_LABEL_STR)
                    mock_success.assert_awaited_once()
                    mock_reaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_commands_verified_remove(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with verified command to remove."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock) as mock_reaction:
            with patch.object(
                issue_comment_handler.labels_handler, "_remove_label", new_callable=AsyncMock
            ) as mock_remove_label:
                with patch.object(
                    issue_comment_handler.check_run_handler, "set_verify_check_queued", new_callable=AsyncMock
                ) as mock_queued:
                    await issue_comment_handler.user_commands(
                        pull_request=mock_pull_request,
                        command=f"{VERIFIED_LABEL_STR} cancel",
                        reviewed_user="test-user",
                        issue_comment_id=123,
                    )
                    mock_remove_label.assert_awaited_once_with(pull_request=mock_pull_request, label=VERIFIED_LABEL_STR)
                    mock_queued.assert_awaited_once()
                    mock_reaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_commands_custom_label(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test user commands with custom label command."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        # Patch USER_LABELS_DICT to include 'bug'
        with patch("webhook_server.libs.handlers.issue_comment_handler.USER_LABELS_DICT", {"bug": "Bug label"}):
            with patch.object(
                issue_comment_handler, "create_comment_reaction", new_callable=AsyncMock
            ) as mock_reaction:
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
                    mock_reaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_comment_reaction(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test creating comment reaction."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        mock_comment = Mock()

        # Mock unified_api methods that are actually called
        issue_comment_handler.github_webhook.unified_api.get_issue_comment = AsyncMock(return_value=mock_comment)
        issue_comment_handler.github_webhook.unified_api.create_reaction = AsyncMock()

        await issue_comment_handler.create_comment_reaction(
            pull_request=mock_pull_request, issue_comment_id=123, reaction=REACTIONS.ok
        )
        issue_comment_handler.github_webhook.unified_api.get_issue_comment.assert_awaited_once_with(
            "test-owner", "test-repo", 123, 123
        )
        issue_comment_handler.github_webhook.unified_api.create_reaction.assert_awaited_once_with(
            mock_comment, REACTIONS.ok
        )

    @pytest.mark.asyncio
    async def test_add_reviewer_by_user_comment_success(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test adding reviewer by user comment successfully."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        # Return dict format for GraphQL compatibility
        mock_contributor = {"login": "reviewer1"}

        # Patch issue_comment_handler.github_webhook.unified_api.get_contributors
        with patch.object(
            issue_comment_handler.github_webhook.unified_api,
            "get_contributors",
            new_callable=AsyncMock,
            return_value=[mock_contributor],
        ):
            await issue_comment_handler._add_reviewer_by_user_comment(
                pull_request=mock_pull_request, reviewer="@reviewer1"
            )
            # Verify unified_api.request_pr_reviews was called with correct arguments
            # New signature: request_pr_reviews(pull_request, reviewers)
            issue_comment_handler.github_webhook.unified_api.request_pr_reviews.assert_awaited_once()
            call_args = issue_comment_handler.github_webhook.unified_api.request_pr_reviews.call_args
            # Verify arguments: pull_request, reviewers
            assert call_args[0][0] == mock_pull_request
            assert "reviewer1" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_add_reviewer_by_user_comment_not_contributor(
        self, issue_comment_handler: IssueCommentHandler
    ) -> None:
        """Test adding reviewer by user comment when user is not a contributor."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        # Return dict format for GraphQL compatibility
        mock_contributor = {"login": "other-user"}

        # Patch issue_comment_handler.github_webhook.unified_api.get_contributors
        with patch.object(
            issue_comment_handler.github_webhook.unified_api,
            "get_contributors",
            new_callable=AsyncMock,
            return_value=[mock_contributor],
        ):
            await issue_comment_handler._add_reviewer_by_user_comment(
                pull_request=mock_pull_request, reviewer="reviewer1"
            )
        # Should add a comment explaining the user is not a contributor via unified_api
        issue_comment_handler.github_webhook.unified_api.add_pr_comment.assert_awaited_once()
        call_args = issue_comment_handler.github_webhook.unified_api.add_pr_comment.call_args
        # Verify the arguments: PR object, message (owner/repo removed from signature)
        assert call_args[0][0] == mock_pull_request
        # Verify the comment contains the expected error message
        comment_text = call_args[0][1]
        assert "reviewer1" in comment_text
        assert "not part of contributors" in comment_text

    @pytest.mark.asyncio
    async def test_process_cherry_pick_command_existing_branches(
        self, issue_comment_handler: IssueCommentHandler
    ) -> None:
        """Test processing cherry pick command with existing branches."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        mock_pull_request.title = "Test PR"
        # Set merged as a property (not a method)
        mock_pull_request.merged = False
        # Mock unified_api methods
        issue_comment_handler.github_webhook.unified_api.get_branch = AsyncMock()
        issue_comment_handler.github_webhook.unified_api.get_pull_request_data = AsyncMock(
            return_value={"merged": False}
        )
        with patch.object(issue_comment_handler.labels_handler, "_add_label", new_callable=AsyncMock) as mock_add_label:
            await issue_comment_handler.process_cherry_pick_command(
                pull_request=mock_pull_request, command_args="branch1 branch2", reviewed_user="test-user"
            )
            # Verify get_branch was called for both branches
            assert issue_comment_handler.github_webhook.unified_api.get_branch.call_count == 2
            issue_comment_handler.github_webhook.unified_api.add_pr_comment.assert_awaited_once()
            call_args = issue_comment_handler.github_webhook.unified_api.add_pr_comment.call_args
            # Verify the arguments: PR object, message
            assert call_args[0][0] == mock_pull_request
            # Verify the comment contains cherry-pick information
            comment_text = call_args[0][1]
            assert "Cherry-pick requested" in comment_text
            assert "test-user" in comment_text
            assert mock_add_label.await_count == 2

    @pytest.mark.asyncio
    async def test_process_cherry_pick_command_non_existing_branches(
        self, issue_comment_handler: IssueCommentHandler
    ) -> None:
        """Test processing cherry pick command with non-existing branches."""

        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        # Mock unified_api.get_branch to return False (branch doesn't exist)
        with patch.object(
            issue_comment_handler.github_webhook.unified_api,
            "get_branch",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with patch.object(
                issue_comment_handler.github_webhook.unified_api, "add_pr_comment", new_callable=AsyncMock
            ) as mock_comment:
                await issue_comment_handler.process_cherry_pick_command(
                    pull_request=mock_pull_request, command_args="branch1 branch2", reviewed_user="test-user"
                )
                mock_comment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_cherry_pick_command_github_exception_404(
        self, issue_comment_handler: IssueCommentHandler
    ) -> None:
        """Test processing cherry pick command with non-existing branches (alternate test)."""

        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        # Mock unified_api.get_branch to return False (branch doesn't exist)
        with patch.object(
            issue_comment_handler.github_webhook.unified_api,
            "get_branch",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with patch.object(
                issue_comment_handler.github_webhook.unified_api, "add_pr_comment", new_callable=AsyncMock
            ) as mock_comment:
                await issue_comment_handler.process_cherry_pick_command(
                    pull_request=mock_pull_request, command_args="branch1 branch2", reviewed_user="test-user"
                )
                # Should comment about non-existent branches
                mock_comment.assert_awaited_once()
                # Arguments are: pull_request, body
                call_args = mock_comment.call_args
                comment_body = call_args[0][1]
                assert "branch1" in comment_body
                assert "does not exist" in comment_body

    @pytest.mark.asyncio
    async def test_process_cherry_pick_command_github_exception_non_404(
        self, issue_comment_handler: IssueCommentHandler
    ) -> None:
        """Test processing cherry pick command with GithubException non-404 (should re-raise)."""

        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        # Mock unified_api.get_branch to raise GithubException with 401 (authentication error)
        with patch.object(
            issue_comment_handler.github_webhook.unified_api,
            "get_branch",
            new_callable=AsyncMock,
            side_effect=GithubException(401, {"message": "Bad credentials"}, None),
        ):
            # Should re-raise non-404 errors
            with pytest.raises(GithubException):
                await issue_comment_handler.process_cherry_pick_command(
                    pull_request=mock_pull_request, command_args="branch1", reviewed_user="test-user"
                )

    @pytest.mark.asyncio
    async def test_process_cherry_pick_command_merged_pr(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test processing cherry pick command for merged PR."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        # Set merged as a property (not a method)
        mock_pull_request.merged = True
        # Mock unified_api methods
        issue_comment_handler.github_webhook.unified_api.get_branch = AsyncMock()
        issue_comment_handler.github_webhook.unified_api.get_pull_request_data = AsyncMock(
            return_value={"merged": True}
        )
        with patch.object(
            issue_comment_handler.runner_handler, "cherry_pick", new_callable=AsyncMock
        ) as mock_cherry_pick:
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
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(
            issue_comment_handler.github_webhook.unified_api, "add_pr_comment", new_callable=AsyncMock
        ) as mock_comment:
            await issue_comment_handler.process_retest_command(
                pull_request=mock_pull_request, command_args="", reviewed_user="test-user"
            )
            mock_comment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_retest_command_all_with_other_tests(
        self, issue_comment_handler: IssueCommentHandler
    ) -> None:
        """Test processing retest command with 'all' and other tests."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(
            issue_comment_handler.github_webhook.unified_api, "add_pr_comment", new_callable=AsyncMock
        ) as mock_comment:
            await issue_comment_handler.process_retest_command(
                pull_request=mock_pull_request, command_args="all tox", reviewed_user="test-user"
            )
            mock_comment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_retest_command_all_only(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test processing retest command with 'all' only.

        Patches all runners referenced in current_pull_request_supported_retest to ensure
        fast, deterministic test execution without triggering real runner methods.
        """
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        # Patch all runners in current_pull_request_supported_retest: ["tox", "pre-commit"]
        with patch.object(issue_comment_handler.runner_handler, "run_tox", new_callable=AsyncMock) as mock_run_tox:
            with patch.object(
                issue_comment_handler.runner_handler, "run_pre_commit", new_callable=AsyncMock
            ) as mock_run_pre_commit:
                await issue_comment_handler.process_retest_command(
                    pull_request=mock_pull_request, command_args="all", reviewed_user="test-user"
                )
                mock_run_tox.assert_awaited_once_with(pull_request=mock_pull_request)
                mock_run_pre_commit.assert_awaited_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_process_retest_command_specific_tests(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test processing retest command with specific tests."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(issue_comment_handler.runner_handler, "run_tox", new_callable=AsyncMock) as mock_run_tox:
            with patch.object(
                issue_comment_handler.github_webhook.unified_api, "add_pr_comment", new_callable=AsyncMock
            ) as mock_comment:
                await issue_comment_handler.process_retest_command(
                    pull_request=mock_pull_request, command_args="tox unsupported-test", reviewed_user="test-user"
                )
                mock_run_tox.assert_awaited_once_with(pull_request=mock_pull_request)
                mock_comment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_retest_command_unsupported_tests(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test processing retest command with unsupported tests."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(
            issue_comment_handler.github_webhook.unified_api, "add_pr_comment", new_callable=AsyncMock
        ) as mock_comment:
            await issue_comment_handler.process_retest_command(
                pull_request=mock_pull_request,
                command_args="unsupported-test1 unsupported-test2",
                reviewed_user="test-user",
            )
            mock_comment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_retest_command_user_not_valid(self, issue_comment_handler: IssueCommentHandler) -> None:
        """Test processing retest command when user is not valid."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        # Patch is_user_valid_to_run_commands as AsyncMock
        with patch.object(
            issue_comment_handler.owners_file_handler,
            "is_user_valid_to_run_commands",
            new=AsyncMock(return_value=False),
        ):
            with patch.object(issue_comment_handler.runner_handler, "run_tox", new_callable=AsyncMock) as mock_run_tox:
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
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

        with patch.object(issue_comment_handler.runner_handler, "run_tox", side_effect=Exception("Test error")):
            with patch.object(issue_comment_handler.logger, "error") as mock_error:
                await issue_comment_handler.process_retest_command(
                    pull_request=mock_pull_request, command_args="tox", reviewed_user="test-user"
                )
                mock_error.assert_called_once()
