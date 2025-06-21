import pytest
from unittest.mock import AsyncMock, Mock, patch

from webhook_server.libs.pull_request_handler import PullRequestHandler
from webhook_server.utils.constants import (
    APPROVED_BY_LABEL_PREFIX,
    CAN_BE_MERGED_STR,
    CHANGED_REQUESTED_BY_LABEL_PREFIX,
    CHERRY_PICK_LABEL_PREFIX,
    COMMENTED_BY_LABEL_PREFIX,
    HAS_CONFLICTS_LABEL_STR,
    LGTM_BY_LABEL_PREFIX,
    NEEDS_REBASE_LABEL_STR,
    TOX_STR,
    VERIFIED_LABEL_STR,
    WIP_STR,
)


class TestPullRequestHandler:
    """Test suite for PullRequestHandler class."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance."""
        mock_webhook = Mock()
        mock_webhook.hook_data = {
            "action": "opened",
            "pull_request": {"number": 123, "merged": False},
            "sender": {"login": "test-user"},
        }
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.issue_url_for_welcome_msg = "welcome-message-url"
        mock_webhook.parent_committer = "test-user"
        mock_webhook.auto_verified_and_merged_users = ["test-user"]
        mock_webhook.create_issue_for_new_pr = True
        mock_webhook.verified_job = True
        mock_webhook.build_and_push_container = True
        mock_webhook.container_repository_and_tag = Mock(return_value="test-repo:pr-123")
        mock_webhook.can_be_merged_required_labels = []
        mock_webhook.set_auto_merge_prs = []
        mock_webhook.auto_merge_enabled = True
        mock_webhook.container_repository = "docker.io/org/repo"
        return mock_webhook

    @pytest.fixture
    def mock_owners_file_handler(self) -> Mock:
        """Create a mock OwnersFileHandler instance."""
        mock_handler = Mock()
        mock_handler.all_pull_request_approvers = ["approver1", "approver2"]
        mock_handler.all_pull_request_reviewers = ["reviewer1", "reviewer2"]
        mock_handler.root_approvers = ["root-approver"]
        mock_handler.root_reviewers = ["root-reviewer"]
        return mock_handler

    @pytest.fixture
    def pull_request_handler(self, mock_github_webhook: Mock, mock_owners_file_handler: Mock) -> PullRequestHandler:
        """Create a PullRequestHandler instance with mocked dependencies."""
        return PullRequestHandler(mock_github_webhook, mock_owners_file_handler)

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        """Create a mock PullRequest instance."""
        mock_pr = Mock()
        mock_pr.number = 123
        mock_pr.title = "Test PR"
        mock_pr.body = "Test PR body"
        mock_pr.html_url = "https://github.com/test/repo/pull/123"
        mock_pr.labels = []
        mock_pr.create_issue_comment = Mock()
        mock_pr.edit = Mock()
        mock_pr.is_merged = False
        mock_pr.base = Mock()
        mock_pr.base.ref = "main"
        mock_pr.user = Mock()
        mock_pr.user.login = "owner1"
        mock_pr.mergeable = True
        mock_pr.mergeable_state = "clean"
        mock_pr.enable_automerge = Mock()
        mock_pr.add_to_assignees = Mock()
        return mock_pr

    @pytest.mark.asyncio
    async def test_process_pull_request_webhook_data_edited_action(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test processing pull request webhook data when action is edited."""
        pull_request_handler.hook_data["action"] = "edited"
        pull_request_handler.hook_data["changes"] = {}

        with patch.object(pull_request_handler, "set_wip_label_based_on_title") as mock_set_wip:
            await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)
            mock_set_wip.assert_called_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_process_pull_request_webhook_data_edited_action_title_changed(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test processing pull request webhook data when action is edited and title is changed."""
        pull_request_handler.hook_data["action"] = "edited"
        pull_request_handler.hook_data["changes"] = {"title": {"from": "old title"}}

        with patch.object(
            pull_request_handler.runner_handler, "run_conventional_title_check"
        ) as mock_run_conventional_title_check:
            await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)
            mock_run_conventional_title_check.assert_called_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_process_pull_request_webhook_data_opened_action(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test processing pull request webhook data when action is opened."""
        pull_request_handler.hook_data["action"] = "opened"

        with patch.object(pull_request_handler, "create_issue_for_new_pull_request") as mock_create_issue:
            with patch.object(pull_request_handler, "set_wip_label_based_on_title") as mock_set_wip:
                with patch.object(pull_request_handler, "process_opened_or_synchronize_pull_request") as mock_process:
                    with patch.object(pull_request_handler, "set_pull_request_automerge") as mock_automerge:
                        await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)
                        mock_create_issue.assert_called_once_with(pull_request=mock_pull_request)
                        mock_set_wip.assert_called_once_with(pull_request=mock_pull_request)
                        mock_process.assert_called_once_with(pull_request=mock_pull_request)
                        mock_automerge.assert_called_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_process_pull_request_webhook_data_reopened_action(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test processing pull request webhook data when action is reopened."""
        pull_request_handler.hook_data["action"] = "reopened"

        with patch.object(pull_request_handler, "create_issue_for_new_pull_request") as mock_create_issue:
            with patch.object(pull_request_handler, "set_wip_label_based_on_title") as mock_set_wip:
                with patch.object(pull_request_handler, "process_opened_or_synchronize_pull_request") as mock_process:
                    with patch.object(pull_request_handler, "set_pull_request_automerge") as mock_automerge:
                        await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)
                        mock_create_issue.assert_called_once_with(pull_request=mock_pull_request)
                        mock_set_wip.assert_called_once_with(pull_request=mock_pull_request)
                        mock_process.assert_called_once_with(pull_request=mock_pull_request)
                        mock_automerge.assert_called_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_process_pull_request_webhook_data_ready_for_review_action(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test processing pull request webhook data when action is ready_for_review."""
        pull_request_handler.hook_data["action"] = "ready_for_review"

        with patch.object(pull_request_handler, "create_issue_for_new_pull_request") as mock_create_issue:
            with patch.object(pull_request_handler, "set_wip_label_based_on_title") as mock_set_wip:
                with patch.object(pull_request_handler, "process_opened_or_synchronize_pull_request") as mock_process:
                    with patch.object(pull_request_handler, "set_pull_request_automerge") as mock_automerge:
                        await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)
                        mock_create_issue.assert_called_once_with(pull_request=mock_pull_request)
                        mock_set_wip.assert_called_once_with(pull_request=mock_pull_request)
                        mock_process.assert_called_once_with(pull_request=mock_pull_request)
                        mock_automerge.assert_called_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_process_pull_request_webhook_data_synchronize_action(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test processing pull request webhook data when action is synchronize."""
        pull_request_handler.hook_data["action"] = "synchronize"

        with patch.object(pull_request_handler, "process_opened_or_synchronize_pull_request") as mock_process:
            with patch.object(pull_request_handler, "remove_labels_when_pull_request_sync") as mock_remove_labels:
                await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)
                mock_process.assert_called_once_with(pull_request=mock_pull_request)
                mock_remove_labels.assert_called_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_process_pull_request_webhook_data_closed_action_not_merged(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test processing pull request webhook data when action is closed and not merged."""
        pull_request_handler.hook_data["action"] = "closed"
        pull_request_handler.hook_data["pull_request"]["merged"] = False

        with patch.object(pull_request_handler, "close_issue_for_merged_or_closed_pr") as mock_close_issue:
            with patch.object(pull_request_handler, "delete_remote_tag_for_merged_or_closed_pr") as mock_delete_tag:
                await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)
                mock_close_issue.assert_called_once_with(pull_request=mock_pull_request, hook_action="closed")
                mock_delete_tag.assert_called_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_process_pull_request_webhook_data_closed_action_merged(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test processing pull request webhook data when action is closed and merged."""
        pull_request_handler.hook_data["action"] = "closed"
        pull_request_handler.hook_data["pull_request"]["merged"] = True

        # Mock labels
        mock_label = Mock()
        mock_label.name = f"{CHERRY_PICK_LABEL_PREFIX}branch1"
        mock_pull_request.labels = [mock_label]

        with patch.object(pull_request_handler, "close_issue_for_merged_or_closed_pr") as mock_close_issue:
            with patch.object(pull_request_handler, "delete_remote_tag_for_merged_or_closed_pr") as mock_delete_tag:
                with patch.object(pull_request_handler.runner_handler, "cherry_pick") as mock_cherry_pick:
                    with patch.object(pull_request_handler.runner_handler, "run_build_container") as mock_build:
                        with patch.object(
                            pull_request_handler, "label_all_opened_pull_requests_merge_state_after_merged"
                        ) as mock_label_all:
                            await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)
                            mock_close_issue.assert_called_once_with(
                                pull_request=mock_pull_request, hook_action="closed"
                            )
                            mock_delete_tag.assert_called_once_with(pull_request=mock_pull_request)
                            mock_cherry_pick.assert_called_once_with(
                                pull_request=mock_pull_request, target_branch="branch1"
                            )
                            mock_build.assert_called_once_with(
                                push=True,
                                set_check=False,
                                is_merged=True,
                                pull_request=mock_pull_request,
                            )
                            mock_label_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_pull_request_webhook_data_labeled_action(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test processing pull request webhook data when action is labeled."""
        pull_request_handler.hook_data["action"] = "labeled"
        pull_request_handler.hook_data["label"] = {"name": "approved-approver1"}
        # Set up the conditions that trigger _check_for_merge = True
        with (
            patch.object(pull_request_handler.owners_file_handler, "all_pull_request_approvers", ["approver1"]),
            patch.object(pull_request_handler.owners_file_handler, "all_pull_request_reviewers", ["approver1"]),
            patch.object(pull_request_handler.owners_file_handler, "root_approvers", ["approver1"]),
            patch.object(pull_request_handler.github_webhook, "verified_job", False),
            patch.object(pull_request_handler, "check_if_can_be_merged", new=AsyncMock()) as mock_check_merge,
        ):
            await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)
            mock_check_merge.assert_awaited_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_process_pull_request_webhook_data_labeled_verified(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test processing pull request webhook data when verified label is added."""
        pull_request_handler.hook_data["action"] = "labeled"
        pull_request_handler.hook_data["label"] = {"name": VERIFIED_LABEL_STR}

        with patch.object(pull_request_handler, "check_if_can_be_merged") as mock_check_merge:
            with patch.object(pull_request_handler.check_run_handler, "set_verify_check_success") as mock_success:
                await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)
                mock_check_merge.assert_called_once_with(pull_request=mock_pull_request)
                mock_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_pull_request_webhook_data_unlabeled_verified(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test processing pull request webhook data when verified label is removed."""
        pull_request_handler.hook_data["action"] = "unlabeled"
        pull_request_handler.hook_data["label"] = {"name": VERIFIED_LABEL_STR}

        with patch.object(pull_request_handler, "check_if_can_be_merged") as mock_check_merge:
            with patch.object(pull_request_handler.check_run_handler, "set_verify_check_queued") as mock_queued:
                await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)
                mock_check_merge.assert_called_once_with(pull_request=mock_pull_request)
                mock_queued.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_wip_label_based_on_title_with_wip(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test setting WIP label when title contains WIP."""
        mock_pull_request.title = "WIP: Test PR"

        with patch.object(pull_request_handler.labels_handler, "_add_label") as mock_add_label:
            await pull_request_handler.set_wip_label_based_on_title(pull_request=mock_pull_request)
            mock_add_label.assert_called_once_with(pull_request=mock_pull_request, label=WIP_STR)

    @pytest.mark.asyncio
    async def test_set_wip_label_based_on_title_without_wip(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test removing WIP label when title doesn't contain WIP."""
        mock_pull_request.title = "Test PR"

        with patch.object(pull_request_handler.labels_handler, "_remove_label") as mock_remove_label:
            await pull_request_handler.set_wip_label_based_on_title(pull_request=mock_pull_request)
            mock_remove_label.assert_called_once_with(pull_request=mock_pull_request, label=WIP_STR)

    def test_prepare_welcome_comment_auto_verified_user(self, pull_request_handler: PullRequestHandler) -> None:
        """Test preparing welcome comment for auto-verified user."""
        result = pull_request_handler._prepare_welcome_comment()
        assert "auto-verified user" in result
        assert "Issue Creation" in result

    def test_prepare_welcome_comment_non_auto_verified_user(self, pull_request_handler: PullRequestHandler) -> None:
        """Test preparing welcome comment for non-auto-verified user."""
        pull_request_handler.github_webhook.parent_committer = "other-user"
        result = pull_request_handler._prepare_welcome_comment()
        assert "auto-verified user" not in result
        assert "Issue Creation" in result

    def test_prepare_welcome_comment_issue_creation_disabled(self, pull_request_handler: PullRequestHandler) -> None:
        """Test preparing welcome comment when issue creation is disabled."""
        pull_request_handler.github_webhook.create_issue_for_new_pr = False
        result = pull_request_handler._prepare_welcome_comment()
        assert "Disabled for this repository" in result

    def test_prepare_owners_welcome_comment(self, pull_request_handler: PullRequestHandler) -> None:
        """Test preparing owners welcome comment."""
        result = pull_request_handler._prepare_owners_welcome_comment()
        assert "Approvers" in result
        assert "approver1" in result
        assert "approver2" in result

    def test_prepare_retest_welcome_comment(self, pull_request_handler: PullRequestHandler) -> None:
        """Test preparing retest welcome comment."""
        result = pull_request_handler._prepare_retest_welcome_comment
        assert TOX_STR in result
        assert "pre-commit" in result

    @pytest.mark.asyncio
    async def test_label_all_opened_pull_requests_merge_state_after_merged(
        self, pull_request_handler: PullRequestHandler
    ) -> None:
        """Test labeling all opened pull requests merge state after merged."""
        mock_pr1 = Mock()
        mock_pr2 = Mock()
        mock_pr1.number = 1
        mock_pr2.number = 2

        with patch.object(pull_request_handler.repository, "get_pulls", return_value=[mock_pr1, mock_pr2]):
            with patch.object(pull_request_handler, "label_pull_request_by_merge_state", new=AsyncMock()) as mock_label:
                with patch("asyncio.sleep", new=AsyncMock()):
                    await pull_request_handler.label_all_opened_pull_requests_merge_state_after_merged()
                    assert mock_label.await_count == 2

    @pytest.mark.asyncio
    async def test_delete_remote_tag_for_merged_or_closed_pr_with_tag(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        mock_pull_request.title = "Test PR"
        with (
            patch.object(pull_request_handler.github_webhook, "build_and_push_container", True),
            patch.object(
                pull_request_handler.github_webhook,
                "container_repository_and_tag",
                return_value="docker.io/org/repo:pr-123",
            ),
            patch.object(pull_request_handler.github_webhook, "container_repository", "docker.io/org/repo"),
            patch.object(pull_request_handler.github_webhook, "container_repository_username", "test"),
            patch.object(pull_request_handler.github_webhook, "container_repository_password", "test"),
            patch.object(
                pull_request_handler.runner_handler,
                "run_podman_command",
                new=AsyncMock(side_effect=[(0, "", ""), (1, "tag exists", ""), (0, "", "")]),
            ),
        ):
            await pull_request_handler.delete_remote_tag_for_merged_or_closed_pr(pull_request=mock_pull_request)
            # The method uses runner_handler.run_podman_command, not repository.delete_tag

    @pytest.mark.asyncio
    async def test_close_issue_for_merged_or_closed_pr_with_issue(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        mock_pull_request.title = "Test PR"
        mock_pull_request.number = 123
        with patch.object(pull_request_handler.repository, "get_issues", return_value=[]) as mock_get_issues:
            mock_issue = Mock()
            mock_issue.title = "PR #123: Test PR"
            mock_issue.number = 456
            mock_issue.body = "[Auto generated]\nNumber: [#123]"
            mock_issue.edit = Mock()
            mock_get_issues.return_value = [mock_issue]
            await pull_request_handler.close_issue_for_merged_or_closed_pr(
                pull_request=mock_pull_request, hook_action="closed"
            )
            mock_issue.edit.assert_called_once_with(state="closed")

    @pytest.mark.asyncio
    async def test_process_opened_or_synchronize_pull_request(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        with patch.object(
            pull_request_handler, "_process_verified_for_update_or_new_pull_request", new=AsyncMock()
        ) as mock_process_verified:
            with patch.object(
                pull_request_handler, "add_pull_request_owner_as_assingee", new=AsyncMock()
            ) as mock_add_assignee:
                with patch.object(
                    pull_request_handler, "label_pull_request_by_merge_state", new=AsyncMock()
                ) as mock_label:
                    with patch.object(pull_request_handler.owners_file_handler, "assign_reviewers", new=AsyncMock()):
                        await pull_request_handler.process_opened_or_synchronize_pull_request(
                            pull_request=mock_pull_request
                        )
                        mock_process_verified.assert_awaited_once_with(pull_request=mock_pull_request)
                        mock_add_assignee.assert_awaited_once_with(pull_request=mock_pull_request)
                        mock_label.assert_awaited_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_set_pull_request_automerge_enabled(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        with (
            patch.object(pull_request_handler.github_webhook, "auto_merge_enabled", True),
            patch.object(pull_request_handler.github_webhook, "auto_verified_and_merged_users", ["test-user"]),
            patch.object(pull_request_handler.github_webhook, "parent_committer", "test-user"),
            patch.object(pull_request_handler.github_webhook, "set_auto_merge_prs", []),
        ):
            mock_pull_request.base.ref = "main"
            mock_pull_request.raw_data = {}
            mock_pull_request.enable_automerge = Mock()
            await pull_request_handler.set_pull_request_automerge(pull_request=mock_pull_request)
            mock_pull_request.enable_automerge.assert_called_once_with(merge_method="SQUASH")

    @pytest.mark.asyncio
    async def test_set_pull_request_automerge_disabled(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        with patch.object(pull_request_handler.github_webhook, "auto_merge_enabled", False):
            with patch.object(mock_pull_request, "enable_automerge", new=AsyncMock()) as mock_enable:
                await pull_request_handler.set_pull_request_automerge(pull_request=mock_pull_request)
                mock_enable.assert_not_called()

    @pytest.mark.asyncio
    async def test_remove_labels_when_pull_request_sync(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        mock_label1 = Mock()
        mock_label1.name = f"{APPROVED_BY_LABEL_PREFIX}approver1"
        mock_label2 = Mock()
        mock_label2.name = f"{LGTM_BY_LABEL_PREFIX}reviewer1"
        mock_pull_request.labels = [mock_label1, mock_label2]
        with patch.object(pull_request_handler.labels_handler, "_remove_label", new=AsyncMock()) as mock_remove_label:
            await pull_request_handler.remove_labels_when_pull_request_sync(pull_request=mock_pull_request)
            assert mock_remove_label.await_count == 2

    @pytest.mark.asyncio
    async def test_label_pull_request_by_merge_state_mergeable(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        mock_pull_request.mergeable = True
        mock_pull_request.mergeable_state = "clean"
        with patch.object(pull_request_handler.labels_handler, "_remove_label", new=AsyncMock()) as mock_remove_label:
            await pull_request_handler.label_pull_request_by_merge_state(pull_request=mock_pull_request)
            assert mock_remove_label.await_count == 2

    @pytest.mark.asyncio
    async def test_label_pull_request_by_merge_state_needs_rebase(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test labeling pull request by merge state when needs rebase."""
        mock_pull_request.mergeable = True
        mock_pull_request.mergeable_state = "behind"

        with patch.object(pull_request_handler.labels_handler, "_add_label") as mock_add_label:
            await pull_request_handler.label_pull_request_by_merge_state(pull_request=mock_pull_request)
            mock_add_label.assert_called_once_with(pull_request=mock_pull_request, label=NEEDS_REBASE_LABEL_STR)

    @pytest.mark.asyncio
    async def test_label_pull_request_by_merge_state_has_conflicts(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test labeling pull request by merge state when has conflicts."""
        mock_pull_request.mergeable = False
        mock_pull_request.mergeable_state = "dirty"

        with patch.object(pull_request_handler.labels_handler, "_add_label") as mock_add_label:
            await pull_request_handler.label_pull_request_by_merge_state(pull_request=mock_pull_request)
            mock_add_label.assert_called_once_with(pull_request=mock_pull_request, label=HAS_CONFLICTS_LABEL_STR)

    @pytest.mark.asyncio
    async def test_process_verified_for_update_or_new_pull_request_auto_verified(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test processing verified for update or new pull request for auto-verified user."""
        with patch.object(pull_request_handler.labels_handler, "_add_label") as mock_add_label:
            with patch.object(pull_request_handler.check_run_handler, "set_verify_check_success") as mock_success:
                await pull_request_handler._process_verified_for_update_or_new_pull_request(
                    pull_request=mock_pull_request
                )
                mock_add_label.assert_called_once_with(pull_request=mock_pull_request, label=VERIFIED_LABEL_STR)
                mock_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_verified_for_update_or_new_pull_request_not_auto_verified(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test processing verified for update or new pull request for non-auto-verified user."""
        pull_request_handler.github_webhook.parent_committer = "other-user"

        with patch.object(pull_request_handler.labels_handler, "_add_label") as mock_add_label:
            with patch.object(pull_request_handler.check_run_handler, "set_verify_check_success") as mock_success:
                await pull_request_handler._process_verified_for_update_or_new_pull_request(
                    pull_request=mock_pull_request
                )
                mock_add_label.assert_not_called()
                mock_success.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_pull_request_owner_as_assingee(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test adding pull request owner as assignee."""
        mock_pull_request.user.login = "owner1"

        with patch.object(mock_pull_request, "add_to_assignees") as mock_add_assignee:
            await pull_request_handler.add_pull_request_owner_as_assingee(pull_request=mock_pull_request)
            mock_add_assignee.assert_called_once_with("owner1")

    @pytest.mark.asyncio
    async def test_check_if_can_be_merged_already_merged(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test checking if can be merged when already merged."""
        # Patch is_merged as a method that returns True
        with patch.object(mock_pull_request, "is_merged", new=Mock(return_value=True)):
            with patch.object(pull_request_handler, "_check_if_pr_approved") as mock_check_approved:
                await pull_request_handler.check_if_can_be_merged(pull_request=mock_pull_request)
                mock_check_approved.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_if_can_be_merged_not_approved(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test checking if can be merged when not approved."""
        # Patch is_merged as a method that returns False
        with patch.object(mock_pull_request, "is_merged", new=Mock(return_value=False)):
            mock_pull_request.labels = []

            with patch.object(pull_request_handler, "_check_if_pr_approved", return_value="not_approved"):
                with patch.object(pull_request_handler.labels_handler, "_remove_label") as mock_remove_label:
                    await pull_request_handler.check_if_can_be_merged(pull_request=mock_pull_request)
                    mock_remove_label.assert_called_once_with(pull_request=mock_pull_request, label=CAN_BE_MERGED_STR)

    @pytest.mark.asyncio
    async def test_check_if_can_be_merged_approved(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        with (
            patch.object(mock_pull_request, "is_merged", new=Mock(return_value=False)),
            patch.object(mock_pull_request, "mergeable", True),
            patch.object(pull_request_handler, "_check_if_pr_approved", new=AsyncMock(return_value="")),
            patch.object(pull_request_handler, "_check_labels_for_can_be_merged", return_value=""),
            patch.object(pull_request_handler.labels_handler, "_add_label", new=AsyncMock()) as mock_add_label,
            patch.object(
                pull_request_handler.owners_file_handler,
                "owners_data_for_changed_files",
                new=AsyncMock(return_value={}),
            ),
            patch.object(pull_request_handler.github_webhook, "minimum_lgtm", 0),
            patch.object(pull_request_handler.check_run_handler, "set_merge_check_in_progress", new=AsyncMock()),
            patch.object(
                pull_request_handler.check_run_handler,
                "required_check_in_progress",
                new=AsyncMock(return_value=("", [])),
            ),
            patch.object(
                pull_request_handler.check_run_handler,
                "required_check_failed_or_no_status",
                new=AsyncMock(return_value=""),
            ),
            patch.object(pull_request_handler.labels_handler, "wip_or_hold_lables_exists", return_value=""),
            patch.object(
                pull_request_handler.labels_handler, "pull_request_labels_names", new=AsyncMock(return_value=[])
            ),
            patch.object(
                pull_request_handler.github_webhook, "last_commit", Mock(get_check_runs=Mock(return_value=[]))
            ),
        ):
            await pull_request_handler.check_if_can_be_merged(pull_request=mock_pull_request)
            mock_add_label.assert_awaited_once_with(pull_request=mock_pull_request, label=CAN_BE_MERGED_STR)

    @pytest.mark.asyncio
    async def test_check_if_pr_approved_no_labels(self, pull_request_handler: PullRequestHandler) -> None:
        with (
            patch.object(
                pull_request_handler.owners_file_handler,
                "owners_data_for_changed_files",
                new=AsyncMock(return_value={}),
            ),
            patch.object(pull_request_handler.github_webhook, "minimum_lgtm", 0),
            patch.object(pull_request_handler.owners_file_handler, "all_pull_request_approvers", []),
            patch.object(pull_request_handler.owners_file_handler, "root_approvers", []),
            patch.object(pull_request_handler.owners_file_handler, "root_reviewers", []),
            patch.object(pull_request_handler.owners_file_handler, "all_pull_request_reviewers", []),
        ):
            result = await pull_request_handler._check_if_pr_approved(labels=[])
            assert result == ""  # Empty string means no errors

    @pytest.mark.asyncio
    async def test_check_if_pr_approved_approved_label(self, pull_request_handler: PullRequestHandler) -> None:
        with (
            patch.object(
                pull_request_handler.owners_file_handler,
                "owners_data_for_changed_files",
                new=AsyncMock(return_value={}),
            ),
            patch.object(pull_request_handler.github_webhook, "minimum_lgtm", 0),
            patch.object(pull_request_handler.owners_file_handler, "all_pull_request_approvers", []),
            patch.object(pull_request_handler.owners_file_handler, "root_approvers", []),
            patch.object(pull_request_handler.owners_file_handler, "root_reviewers", []),
            patch.object(pull_request_handler.owners_file_handler, "all_pull_request_reviewers", []),
        ):
            result = await pull_request_handler._check_if_pr_approved(labels=[f"{APPROVED_BY_LABEL_PREFIX}approver1"])
            assert result == ""  # Empty string means no errors

    @pytest.mark.asyncio
    async def test_check_if_pr_approved_lgtm_label(self, pull_request_handler: PullRequestHandler) -> None:
        with (
            patch.object(
                pull_request_handler.owners_file_handler,
                "owners_data_for_changed_files",
                new=AsyncMock(return_value={}),
            ),
            patch.object(pull_request_handler.github_webhook, "minimum_lgtm", 0),
            patch.object(pull_request_handler.owners_file_handler, "all_pull_request_approvers", []),
            patch.object(pull_request_handler.owners_file_handler, "root_approvers", []),
            patch.object(pull_request_handler.owners_file_handler, "root_reviewers", []),
            patch.object(pull_request_handler.owners_file_handler, "all_pull_request_reviewers", []),
        ):
            result = await pull_request_handler._check_if_pr_approved(labels=[f"{LGTM_BY_LABEL_PREFIX}approver1"])
            assert result == ""  # Empty string means no errors

    @pytest.mark.asyncio
    async def test_check_if_pr_approved_changes_requested(self, pull_request_handler: PullRequestHandler) -> None:
        with (
            patch.object(
                pull_request_handler.owners_file_handler,
                "owners_data_for_changed_files",
                new=AsyncMock(return_value={}),
            ),
            patch.object(pull_request_handler.github_webhook, "minimum_lgtm", 0),
            patch.object(pull_request_handler.owners_file_handler, "all_pull_request_approvers", []),
            patch.object(pull_request_handler.owners_file_handler, "root_approvers", []),
            patch.object(pull_request_handler.owners_file_handler, "root_reviewers", []),
            patch.object(pull_request_handler.owners_file_handler, "all_pull_request_reviewers", []),
        ):
            result = await pull_request_handler._check_if_pr_approved(
                labels=[f"{CHANGED_REQUESTED_BY_LABEL_PREFIX}reviewer1"]
            )
            assert result == ""  # Empty string means no errors

    @pytest.mark.asyncio
    async def test_check_if_pr_approved_commented(self, pull_request_handler: PullRequestHandler) -> None:
        with (
            patch.object(
                pull_request_handler.owners_file_handler,
                "owners_data_for_changed_files",
                new=AsyncMock(return_value={}),
            ),
            patch.object(pull_request_handler.github_webhook, "minimum_lgtm", 0),
            patch.object(pull_request_handler.owners_file_handler, "all_pull_request_approvers", []),
            patch.object(pull_request_handler.owners_file_handler, "root_approvers", []),
            patch.object(pull_request_handler.owners_file_handler, "root_reviewers", []),
            patch.object(pull_request_handler.owners_file_handler, "all_pull_request_reviewers", []),
        ):
            result = await pull_request_handler._check_if_pr_approved(labels=[f"{COMMENTED_BY_LABEL_PREFIX}reviewer1"])
            assert result == ""  # Empty string means no errors

    def test_check_labels_for_can_be_merged_approved(self, pull_request_handler: PullRequestHandler) -> None:
        # Mock the logic to return empty string (no errors) when appropriate
        with patch.object(pull_request_handler, "_check_if_pr_approved", return_value=""):
            result = pull_request_handler._check_labels_for_can_be_merged(
                labels=[f"{APPROVED_BY_LABEL_PREFIX}approver1"]
            )
            assert result == ""  # Empty string means no errors

    def test_check_labels_for_can_be_merged_changes_requested(self, pull_request_handler: PullRequestHandler) -> None:
        # Set up the conditions that trigger the error message
        with patch.object(pull_request_handler.owners_file_handler, "all_pull_request_approvers", ["reviewer1"]):
            result = pull_request_handler._check_labels_for_can_be_merged(
                labels=[f"{CHANGED_REQUESTED_BY_LABEL_PREFIX}reviewer1"]
            )
            assert "PR has changed requests from approvers" in result

    def test_check_labels_for_can_be_merged_commented(self, pull_request_handler: PullRequestHandler) -> None:
        # Mock the logic to return empty string (no errors) when appropriate
        with patch.object(pull_request_handler, "_check_if_pr_approved", return_value=""):
            result = pull_request_handler._check_labels_for_can_be_merged(
                labels=[f"{COMMENTED_BY_LABEL_PREFIX}reviewer1"]
            )
            assert result == ""  # Empty string means no errors

    def test_check_labels_for_can_be_merged_not_approved(self, pull_request_handler: PullRequestHandler) -> None:
        # Mock the logic to return empty string (no errors) when appropriate
        with patch.object(pull_request_handler, "_check_if_pr_approved", return_value=""):
            result = pull_request_handler._check_labels_for_can_be_merged(labels=["other-label"])
            assert result == ""  # Empty string means no errors

    def test_skip_if_pull_request_already_merged_merged(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test skipping if pull request is already merged."""
        # Patch is_merged as a method that returns True
        with patch.object(mock_pull_request, "is_merged", new=Mock(return_value=True)):
            result = pull_request_handler.skip_if_pull_request_already_merged(pull_request=mock_pull_request)
            assert result is True

    def test_skip_if_pull_request_already_merged_not_merged(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test skipping if pull request is not merged."""
        # Patch is_merged as a method that returns False
        with patch.object(mock_pull_request, "is_merged", new=Mock(return_value=False)):
            result = pull_request_handler.skip_if_pull_request_already_merged(pull_request=mock_pull_request)
            assert result is False

    @pytest.mark.asyncio
    async def test_delete_remote_tag_for_merged_or_closed_pr_without_tag(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test deleting remote tag for merged or closed PR without tag."""
        mock_pull_request.title = "Test PR"

        with patch.object(pull_request_handler.github_webhook, "build_and_push_container", False):
            await pull_request_handler.delete_remote_tag_for_merged_or_closed_pr(pull_request=mock_pull_request)
            # Should return early when build_and_push_container is False

    @pytest.mark.asyncio
    async def test_close_issue_for_merged_or_closed_pr_without_issue(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test closing issue for merged or closed PR without issue."""
        mock_pull_request.title = "Test PR"

        with patch.object(pull_request_handler.repository, "get_issues", return_value=[]):
            await pull_request_handler.close_issue_for_merged_or_closed_pr(
                pull_request=mock_pull_request, hook_action="closed"
            )
            # Should not find any matching issues
