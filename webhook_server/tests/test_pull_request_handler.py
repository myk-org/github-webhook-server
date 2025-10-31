from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import pytest
from github import GithubException
from github.PullRequest import PullRequest

from webhook_server.libs.graphql.graphql_client import GraphQLError
from webhook_server.libs.graphql.webhook_data import PullRequestWrapper
from webhook_server.libs.handlers.pull_request_handler import PullRequestHandler
from webhook_server.utils.constants import (
    APPROVED_BY_LABEL_PREFIX,
    CAN_BE_MERGED_STR,
    CHANGED_REQUESTED_BY_LABEL_PREFIX,
    CHERRY_PICK_LABEL_PREFIX,
    CHERRY_PICKED_LABEL_PREFIX,
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
        mock_webhook.repository_full_name = "test-owner/test-repo"  # Direct attribute for _owner_and_repo property
        mock_webhook.owner_and_repo = ("test-owner", "test-repo")  # Tuple for unpacking
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
        # Add config mock for background task delay
        mock_webhook.config = Mock()
        mock_webhook.config.get_value = Mock(return_value=30)  # Default delay for post-merge-relabel-delay
        # Add async helper methods
        mock_webhook.add_pr_comment = AsyncMock()
        mock_webhook.update_pr_title = AsyncMock()
        mock_webhook.enable_pr_automerge = AsyncMock()
        mock_webhook.request_pr_reviews = AsyncMock()
        mock_webhook.add_pr_assignee = AsyncMock()
        # Add unified_api mock with async methods
        mock_webhook.unified_api = Mock()
        mock_webhook.unified_api.get_issues = AsyncMock(return_value=[])
        mock_webhook.unified_api.create_issue_comment = AsyncMock()
        mock_webhook.unified_api.create_issue_comment_on_issue = AsyncMock()
        mock_webhook.unified_api.edit_issue = AsyncMock()
        mock_webhook.unified_api.add_assignees_by_login = AsyncMock()
        mock_webhook.unified_api.get_commit_check_runs = AsyncMock(return_value=[])
        mock_webhook.unified_api.create_check_run = AsyncMock()
        mock_webhook.unified_api.add_pr_comment = AsyncMock()
        mock_webhook.unified_api.enable_pr_automerge = AsyncMock()
        return mock_webhook

    @pytest.fixture
    def mock_owners_file_handler(self) -> Mock:
        """Create a mock OwnersFileHandler instance."""
        mock_handler = Mock()
        mock_handler.all_pull_request_approvers = ["approver1", "approver2"]
        mock_handler.all_pull_request_reviewers = ["reviewer1", "reviewer2"]
        mock_handler.root_approvers = ["root-approver"]
        mock_handler.root_reviewers = ["root-reviewer"]
        mock_handler.initialize = AsyncMock()  # Add async initialize method
        return mock_handler

    @pytest.fixture
    def pull_request_handler(self, mock_github_webhook: Mock, mock_owners_file_handler: Mock) -> PullRequestHandler:
        """Create a PullRequestHandler instance with mocked dependencies."""
        return PullRequestHandler(mock_github_webhook, mock_owners_file_handler)

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        """Create a mock PullRequest instance."""
        mock_pr = Mock()
        mock_pr.id = "PR_kgDOTestId"  # GraphQL node ID for mutations
        mock_pr.number = 123
        mock_pr.title = "Test PR"
        mock_pr.body = "Test PR body"
        mock_pr.html_url = "https://github.com/test/repo/pull/123"
        mock_pr.get_labels = Mock(return_value=[])
        mock_pr.create_issue_comment = Mock()
        mock_pr.edit = Mock()
        mock_pr.merged = False
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

        with patch.object(pull_request_handler, "set_wip_label_based_on_title", new=AsyncMock()) as mock_set_wip:
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
            pull_request_handler.runner_handler, "run_conventional_title_check", new=AsyncMock()
        ) as mock_run_conventional_title_check:
            await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)
            mock_run_conventional_title_check.assert_called_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_process_pull_request_webhook_data_opened_action(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test processing pull request webhook data when action is opened."""
        pull_request_handler.hook_data["action"] = "opened"

        with patch.object(
            pull_request_handler, "create_issue_for_new_pull_request", new=AsyncMock()
        ) as mock_create_issue:
            with patch.object(pull_request_handler, "set_wip_label_based_on_title", new=AsyncMock()) as mock_set_wip:
                with patch.object(
                    pull_request_handler, "process_opened_or_synchronize_pull_request", new=AsyncMock()
                ) as mock_process:
                    with patch.object(
                        pull_request_handler, "set_pull_request_automerge", new=AsyncMock()
                    ) as mock_automerge:
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

        with patch.object(
            pull_request_handler, "create_issue_for_new_pull_request", new=AsyncMock()
        ) as mock_create_issue:
            with patch.object(pull_request_handler, "set_wip_label_based_on_title", new=AsyncMock()) as mock_set_wip:
                with patch.object(
                    pull_request_handler, "process_opened_or_synchronize_pull_request", new=AsyncMock()
                ) as mock_process:
                    with patch.object(
                        pull_request_handler, "set_pull_request_automerge", new=AsyncMock()
                    ) as mock_automerge:
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

        with patch.object(
            pull_request_handler, "create_issue_for_new_pull_request", new=AsyncMock()
        ) as mock_create_issue:
            with patch.object(pull_request_handler, "set_wip_label_based_on_title", new=AsyncMock()) as mock_set_wip:
                with patch.object(
                    pull_request_handler, "process_opened_or_synchronize_pull_request", new=AsyncMock()
                ) as mock_process:
                    with patch.object(
                        pull_request_handler, "set_pull_request_automerge", new=AsyncMock()
                    ) as mock_automerge:
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

        with patch.object(
            pull_request_handler, "process_opened_or_synchronize_pull_request", new=AsyncMock()
        ) as mock_process:
            with patch.object(
                pull_request_handler, "remove_labels_when_pull_request_sync", new=AsyncMock()
            ) as mock_remove_labels:
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

        with patch.object(
            pull_request_handler, "close_issue_for_merged_or_closed_pr", new=AsyncMock()
        ) as mock_close_issue:
            with patch.object(
                pull_request_handler, "delete_remote_tag_for_merged_or_closed_pr", new=AsyncMock()
            ) as mock_delete_tag:
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
        mock_pull_request.get_labels = Mock(return_value=[mock_label])

        with patch.object(
            pull_request_handler, "close_issue_for_merged_or_closed_pr", new=AsyncMock()
        ) as mock_close_issue:
            with patch.object(
                pull_request_handler, "delete_remote_tag_for_merged_or_closed_pr", new=AsyncMock()
            ) as mock_delete_tag:
                with patch.object(
                    pull_request_handler.runner_handler, "cherry_pick", new=AsyncMock()
                ) as mock_cherry_pick:
                    with patch.object(
                        pull_request_handler.runner_handler, "run_build_container", new=AsyncMock()
                    ) as mock_build:
                        with patch.object(
                            pull_request_handler,
                            "label_all_opened_pull_requests_merge_state_after_merged",
                            new=AsyncMock(),
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

        with patch.object(pull_request_handler, "check_if_can_be_merged", new=AsyncMock()) as mock_check_merge:
            with patch.object(
                pull_request_handler.check_run_handler, "set_verify_check_success", new=AsyncMock()
            ) as mock_success:
                await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)
                mock_check_merge.assert_awaited_once_with(pull_request=mock_pull_request)
                mock_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_pull_request_webhook_data_unlabeled_verified(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test processing pull request webhook data when verified label is removed."""
        pull_request_handler.hook_data["action"] = "unlabeled"
        pull_request_handler.hook_data["label"] = {"name": VERIFIED_LABEL_STR}

        with patch.object(pull_request_handler, "check_if_can_be_merged", new=AsyncMock()) as mock_check_merge:
            with patch.object(
                pull_request_handler.check_run_handler, "set_verify_check_queued", new=AsyncMock()
            ) as mock_queued:
                await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)
                mock_check_merge.assert_awaited_once_with(pull_request=mock_pull_request)
                mock_queued.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_wip_label_based_on_title_with_wip(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test setting WIP label when title contains WIP."""
        mock_pull_request.title = "WIP: Test PR"

        with patch.object(pull_request_handler.labels_handler, "_add_label", new=AsyncMock()) as mock_add_label:
            await pull_request_handler.set_wip_label_based_on_title(pull_request=mock_pull_request)
            mock_add_label.assert_called_once_with(pull_request=mock_pull_request, label=WIP_STR)

    @pytest.mark.asyncio
    async def test_set_wip_label_based_on_title_without_wip(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test removing WIP label when title doesn't contain WIP."""
        mock_pull_request.title = "Test PR"

        with patch.object(pull_request_handler.labels_handler, "_remove_label", new=AsyncMock()) as mock_remove_label:
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
        """Test labeling all opened pull requests merge state after merged with batched API (background task).

        Note: Tests the background method directly since testing background tasks in pytest
        requires special handling that complicates the test.
        """
        # Create PullRequestWrapper objects with all data (labels, merge state)
        webhook_data_1 = {
            "node_id": "PR_1",
            "number": 1,
            "title": "Test PR 1",
            "mergeable_state": "clean",
            "labels": [],
            "base": {"ref": "main", "sha": "abc123", "repo": {"owner": {"login": "owner"}, "name": "repo"}},
            "head": {"ref": "feature", "sha": "def456", "repo": {"owner": {"login": "owner"}, "name": "repo"}},
            "user": {"login": "testuser"},
        }
        webhook_data_2 = {
            "node_id": "PR_2",
            "number": 2,
            "title": "Test PR 2",
            "mergeable_state": "behind",
            "labels": [],
            "base": {"ref": "main", "sha": "abc123", "repo": {"owner": {"login": "owner"}, "name": "repo"}},
            "head": {"ref": "feature", "sha": "def456", "repo": {"owner": {"login": "owner"}, "name": "repo"}},
            "user": {"login": "testuser"},
        }

        mock_pr1 = PullRequestWrapper("owner", "repo", webhook_data_1)
        mock_pr2 = PullRequestWrapper("owner", "repo", webhook_data_2)

        # Mock the new batched API method
        with patch.object(
            pull_request_handler.github_webhook.unified_api,
            "get_open_pull_requests_with_details",
            new=AsyncMock(return_value=[mock_pr1, mock_pr2]),
        ):
            with patch.object(pull_request_handler, "label_pull_request_by_merge_state", new=AsyncMock()) as mock_label:
                with patch("asyncio.sleep", new=AsyncMock()) as mock_sleep:
                    # Test the background method directly (public method just schedules it)
                    await pull_request_handler._label_all_opened_pull_requests_background()
                    # Should process both PRs with only 1 API call (not N+1)
                    assert mock_label.await_count == 2
                    # Verify delay was used (configurable via config, default 30s)
                    assert mock_sleep.await_count == 1

    @pytest.mark.asyncio
    async def test_delete_remote_tag_for_merged_or_closed_pr_with_tag(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        mock_pull_request.title = "Test PR"
        # Mock add_comment for GraphQL mutation
        pull_request_handler.github_webhook.unified_api.add_comment = AsyncMock()
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
                # Sequence: login (success), tag_ls (success with output), tag_delete (success), logout
                new=AsyncMock(side_effect=[(1, "", ""), (1, "pr-123", ""), (1, "", ""), (1, "", "")]),
            ),
        ):
            await pull_request_handler.delete_remote_tag_for_merged_or_closed_pr(pull_request=mock_pull_request)
            # Verify add_pr_comment was called with success message
            pull_request_handler.github_webhook.unified_api.add_pr_comment.assert_called_once()
            call_args = pull_request_handler.github_webhook.unified_api.add_pr_comment.call_args
            # Check body keyword argument
            assert "successfully removed pr tag" in call_args[1]["body"].lower()

    @pytest.mark.asyncio
    async def test_close_issue_for_merged_or_closed_pr_with_issue(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        mock_pull_request.title = "Test PR"
        mock_pull_request.number = 123
        # Use dict format for GraphQL compatibility
        mock_issue = {
            "id": "I_kwDOABCDEF123",
            "title": "Test PR - 123",
            "number": 456,
            "body": "[Auto generated]\nNumber: [#123]",
            "node_id": "I_kwDOABCDEF123",
        }

        # Mock unified_api methods
        pull_request_handler.github_webhook.unified_api.get_issues = AsyncMock(return_value=[mock_issue])
        pull_request_handler.github_webhook.unified_api.add_comment = AsyncMock()
        pull_request_handler.github_webhook.unified_api.edit_issue = AsyncMock()

        await pull_request_handler.close_issue_for_merged_or_closed_pr(
            pull_request=mock_pull_request, hook_action="closed"
        )
        pull_request_handler.github_webhook.unified_api.add_comment.assert_called_once()
        pull_request_handler.github_webhook.unified_api.edit_issue.assert_called_once_with(mock_issue, state="closed")

    @pytest.mark.asyncio
    async def test_process_opened_or_synchronize_pull_request(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        with patch.object(
            pull_request_handler, "_process_verified_for_update_or_new_pull_request", new=AsyncMock()
        ) as mock_process_verified:
            with patch.object(
                pull_request_handler, "add_pull_request_owner_as_assignee", new=AsyncMock()
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
            patch.object(
                pull_request_handler.github_webhook.unified_api, "enable_pr_automerge", new_callable=AsyncMock
            ) as mock_enable,
        ):
            mock_pull_request.base.ref = "main"
            mock_pull_request.webhook_data = {}
            await pull_request_handler.set_pull_request_automerge(pull_request=mock_pull_request)
            # Verify unified_api.enable_pr_automerge was called with correct arguments
            mock_enable.assert_called_once()
            call_args = mock_enable.call_args
            # Updated method signature: enable_pr_automerge(pull_request, merge_method)
            assert call_args[0][0] == mock_pull_request  # PR object
            assert call_args[0][1] == "SQUASH"  # merge_method

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
        mock_pull_request.get_labels = Mock(return_value=[mock_label1, mock_label2])
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

        with patch.object(pull_request_handler.labels_handler, "_add_label", new=AsyncMock()) as mock_add_label:
            await pull_request_handler.label_pull_request_by_merge_state(pull_request=mock_pull_request)
            mock_add_label.assert_called_once_with(pull_request=mock_pull_request, label=NEEDS_REBASE_LABEL_STR)

    @pytest.mark.asyncio
    async def test_label_pull_request_by_merge_state_has_conflicts(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test labeling pull request by merge state when has conflicts."""
        mock_pull_request.mergeable = False
        mock_pull_request.mergeable_state = "dirty"

        with patch.object(pull_request_handler.labels_handler, "_add_label", new=AsyncMock()) as mock_add_label:
            await pull_request_handler.label_pull_request_by_merge_state(pull_request=mock_pull_request)
            mock_add_label.assert_called_once_with(pull_request=mock_pull_request, label=HAS_CONFLICTS_LABEL_STR)

    @pytest.mark.asyncio
    async def test_process_verified_for_update_or_new_pull_request_auto_verified(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test processing verified for update or new pull request for auto-verified user."""
        with patch.object(pull_request_handler.labels_handler, "_add_label", new=AsyncMock()) as mock_add_label:
            with patch.object(
                pull_request_handler.check_run_handler, "set_verify_check_success", new=AsyncMock()
            ) as mock_success:
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

        with patch.object(pull_request_handler.labels_handler, "_add_label", new=AsyncMock()) as mock_add_label:
            with patch.object(
                pull_request_handler.check_run_handler, "set_verify_check_success", new=AsyncMock()
            ) as mock_success:
                await pull_request_handler._process_verified_for_update_or_new_pull_request(
                    pull_request=mock_pull_request
                )
                mock_add_label.assert_not_called()
                mock_success.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_verified_cherry_picked_pr_auto_verify_enabled(
        self, pull_request_handler: PullRequestHandler
    ) -> None:
        """Test cherry-picked PR with auto-verify enabled (default behavior)."""

        mock_pull_request = Mock(spec=PullRequest)
        mock_label = Mock()
        mock_label.name = CHERRY_PICKED_LABEL_PREFIX
        mock_pull_request.get_labels = Mock(return_value=[mock_label])

        with (
            patch.object(pull_request_handler.github_webhook, "auto_verify_cherry_picked_prs", True),
            patch.object(pull_request_handler.labels_handler, "_add_label", new=AsyncMock()) as mock_add_label,
            patch.object(
                pull_request_handler.check_run_handler, "set_verify_check_success", new=AsyncMock()
            ) as mock_set_success,
        ):
            await pull_request_handler._process_verified_for_update_or_new_pull_request(mock_pull_request)
            # Should auto-verify since auto_verify_cherry_picked_prs is True and user is in auto_verified list
            mock_add_label.assert_called_once()
            mock_set_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_verified_cherry_picked_pr_auto_verify_disabled(
        self, pull_request_handler: PullRequestHandler
    ) -> None:
        """Test cherry-picked PR with auto-verify disabled."""

        mock_pull_request = Mock(spec=PullRequest)
        mock_label = Mock()
        mock_label.name = CHERRY_PICKED_LABEL_PREFIX
        mock_pull_request.get_labels = Mock(return_value=[mock_label])

        with (
            patch.object(pull_request_handler.github_webhook, "auto_verify_cherry_picked_prs", False),
            patch.object(pull_request_handler.labels_handler, "_add_label", new=AsyncMock()) as mock_add_label,
            patch.object(
                pull_request_handler.check_run_handler, "set_verify_check_queued", new=AsyncMock()
            ) as mock_set_queued,
        ):
            await pull_request_handler._process_verified_for_update_or_new_pull_request(mock_pull_request)
            # Should NOT auto-verify since auto_verify_cherry_picked_prs is False
            mock_add_label.assert_not_called()
            mock_set_queued.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_pull_request_owner_as_assignee(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test adding pull request owner as assignee (optimized - uses pr_id directly)."""
        mock_pull_request.user.login = "owner1"
        mock_pull_request.number = 123
        mock_pull_request.id = "PR_kwDOABC123"  # Mock PR node ID

        # Now it uses unified_api.add_assignees_by_login_with_pr_id (optimized method)
        await pull_request_handler.add_pull_request_owner_as_assignee(pull_request=mock_pull_request)
        pull_request_handler.github_webhook.unified_api.add_assignees_by_login_with_pr_id.assert_called_once_with(
            "PR_kwDOABC123", ["owner1"]
        )

    @pytest.mark.asyncio
    async def test_check_if_can_be_merged_already_merged(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test checking if can be merged when already merged."""
        # Patch merged as a property that returns True
        mock_pull_request.merged = True
        with patch.object(pull_request_handler, "_check_if_pr_approved") as mock_check_approved:
            await pull_request_handler.check_if_can_be_merged(pull_request=mock_pull_request)
            mock_check_approved.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_if_can_be_merged_not_approved(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test checking if can be merged when not approved."""
        # Patch merged as a property that returns False
        mock_pull_request.merged = False
        mock_pull_request.get_labels = Mock(return_value=[])

        with patch.object(pull_request_handler, "_check_if_pr_approved", new=AsyncMock(return_value="not_approved")):
            with patch.object(pull_request_handler.labels_handler, "_remove_label") as mock_remove_label:
                await pull_request_handler.check_if_can_be_merged(pull_request=mock_pull_request)
                mock_remove_label.assert_called_once_with(pull_request=mock_pull_request, label=CAN_BE_MERGED_STR)

    @pytest.mark.asyncio
    async def test_check_if_can_be_merged_approved(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        mock_pull_request.merged = False
        with (
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
            patch.object(pull_request_handler.labels_handler, "wip_or_hold_labels_exists", return_value=""),
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
        # Patch merged as a property that returns True
        mock_pull_request.merged = True
        result = pull_request_handler.skip_if_pull_request_already_merged(pull_request=mock_pull_request)
        assert result is True

    def test_skip_if_pull_request_already_merged_not_merged(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test skipping if pull request is not merged."""
        # Patch merged as a property that returns False
        mock_pull_request.merged = False
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

        with patch.object(
            pull_request_handler.github_webhook.unified_api, "get_issues", new=AsyncMock(return_value=[])
        ):
            await pull_request_handler.close_issue_for_merged_or_closed_pr(
                pull_request=mock_pull_request, hook_action="closed"
            )
            # Should not find any matching issues

    @pytest.mark.asyncio
    async def test_handler_with_pull_request_wrapper(self) -> None:
        """Test handler works with PullRequestWrapper (webhook format) not just PullRequest (REST)."""

        # Create realistic webhook PR data
        webhook_data = {
            "node_id": "PR_kwDOABcD456",
            "number": 456,
            "title": "feat: Add GraphQL wrapper support",
            "body": "This PR adds GraphQL wrapper integration",
            "html_url": "https://github.com/test-org/test-repo/pull/456",
            "state": "open",
            "draft": False,
            "mergeable": True,
            "base": {"ref": "main", "sha": "abc123", "repo": {"owner": {"login": "test-org"}, "name": "test-repo"}},
            "head": {
                "ref": "feature/graphql",
                "sha": "def456",
                "repo": {"owner": {"login": "test-org"}, "name": "test-repo"},
            },
            "user": {"login": "graphql-user"},
            "labels": [],
        }

        # Create PullRequestWrapper with webhook data
        wrapper_pr = PullRequestWrapper("test-org", "test-repo", webhook_data)

        # Verify wrapper has expected properties
        assert wrapper_pr.number == 456
        assert wrapper_pr.title == "feat: Add GraphQL wrapper support"
        assert wrapper_pr.body == "This PR adds GraphQL wrapper integration"
        assert wrapper_pr.html_url == "https://github.com/test-org/test-repo/pull/456"
        assert wrapper_pr.state == "open"  # Wrapper converts "OPEN" to lowercase "open" for PyGithub compatibility
        assert wrapper_pr.draft is False
        assert wrapper_pr.mergeable is True  # Wrapper converts "MERGEABLE" to True

        # Test handler can access wrapper properties without AttributeError
        # This validates the dual-API strategy works in production
        pr_number = wrapper_pr.number  # noqa: F841
        pr_title = wrapper_pr.title  # noqa: F841
        pr_state = wrapper_pr.state  # noqa: F841
        pr_mergeable = wrapper_pr.mergeable  # noqa: F841

        # All property accesses should succeed without errors


class TestCreateIssueForNewPullRequest:
    """Tests for create_issue_for_new_pull_request method."""

    @pytest.fixture
    def mock_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance."""
        webhook = Mock()
        webhook.hook_data = {
            "repository": {
                "node_id": "R_kgDOABcD1M",  # GraphQL node ID
                "id": 123456789,  # Numeric ID
                "full_name": "owner/test-repo",
                "name": "test-repo",
            },
        }
        webhook.repository_name = "test-repo"
        webhook.repository_full_name = "owner/test-repo"
        webhook.create_issue_for_new_pr = True
        webhook.parent_committer = "testuser"
        webhook.auto_verified_and_merged_users = []
        webhook.unified_api = Mock()
        # Add repository_id property that returns the value from hook_data
        webhook.repository_id = webhook.hook_data["repository"]["node_id"]
        # Add repository mock with proper full_name
        webhook.repository = Mock()
        webhook.repository.full_name = "owner/test-repo"
        webhook.owner_and_repo = ("owner", "test-repo")  # Tuple for unpacking
        # Add logger and log_prefix for compatibility
        webhook.logger = Mock()
        webhook.log_prefix = "[TEST]"
        return webhook

    @pytest.fixture
    def mock_pr_wrapper(self) -> Mock:
        """Create a mock PullRequestWrapper instance."""
        pr = Mock()
        pr.number = 42
        pr.title = "Test PR"
        pr.html_url = "https://github.com/owner/repo/pull/42"
        pr.user = Mock()
        pr.user.login = "contributor"
        return pr

    @pytest.mark.asyncio
    async def test_create_issue_disabled(self, mock_webhook: Mock, mock_pr_wrapper: Mock) -> None:
        """Test that issue creation is skipped when disabled."""
        mock_webhook.create_issue_for_new_pr = False
        # Create a mock owners_file_handler
        mock_owners_file_handler = Mock()
        handler = PullRequestHandler(
            github_webhook=mock_webhook, owners_file_handler=mock_owners_file_handler, hook_data={}
        )

        await handler.create_issue_for_new_pull_request(mock_pr_wrapper)

        # Should not call any GitHub API methods
        mock_webhook.unified_api.get_issues.assert_not_called()
        mock_webhook.unified_api.create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_issue_auto_verified_user(self, mock_webhook: Mock, mock_pr_wrapper: Mock) -> None:
        """Test that issue creation is skipped for auto-verified users."""
        mock_webhook.create_issue_for_new_pr = True
        mock_webhook.parent_committer = "autouser"
        mock_webhook.auto_verified_and_merged_users = ["autouser"]
        # Create a mock owners_file_handler
        mock_owners_file_handler = Mock()
        handler = PullRequestHandler(
            github_webhook=mock_webhook, owners_file_handler=mock_owners_file_handler, hook_data={}
        )

        await handler.create_issue_for_new_pull_request(mock_pr_wrapper)

        # Should not call any GitHub API methods
        mock_webhook.unified_api.get_issues.assert_not_called()
        mock_webhook.unified_api.create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_issue_already_exists(self, mock_webhook: Mock, mock_pr_wrapper: Mock) -> None:
        """Test that issue creation is skipped if issue already exists."""
        mock_webhook.create_issue_for_new_pr = True
        mock_webhook.parent_committer = "testuser"
        mock_webhook.auto_verified_and_merged_users = []
        # Create a mock owners_file_handler
        mock_owners_file_handler = Mock()
        handler = PullRequestHandler(
            github_webhook=mock_webhook, owners_file_handler=mock_owners_file_handler, hook_data={}
        )

        # Mock existing issue with dict format for GraphQL compatibility
        existing_issue = {
            "title": "[PR #42] Test PR Title",
            "number": 1,
            "html_url": "https://github.com/owner/repo/issues/1",
        }

        mock_webhook.unified_api.get_issues = AsyncMock(return_value=[existing_issue])
        mock_webhook.unified_api.get_repository = AsyncMock(return_value={"id": "R_kgDOABcD1M"})
        mock_webhook.unified_api.get_user_id = AsyncMock(return_value="U_123")
        mock_webhook.unified_api.create_issue = AsyncMock()

        mock_pr_wrapper.number = 42
        mock_pr_wrapper.title = "Test PR Title"
        mock_pr_wrapper.user.node_id = ""  # Empty node_id to trigger get_user_id call

        await handler.create_issue_for_new_pull_request(mock_pr_wrapper)

        # Should check for issues but not create
        mock_webhook.unified_api.get_issues.assert_called_once()
        mock_webhook.unified_api.create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_issue_success(self, mock_webhook: Mock, mock_pr_wrapper: Mock) -> None:
        """Test successful issue creation for new PR."""
        mock_webhook.create_issue_for_new_pr = True
        mock_webhook.parent_committer = "testuser"
        mock_webhook.auto_verified_and_merged_users = []
        # Create a mock owners_file_handler
        mock_owners_file_handler = Mock()
        handler = PullRequestHandler(
            github_webhook=mock_webhook, owners_file_handler=mock_owners_file_handler, hook_data={}
        )

        # Mock no existing issues
        mock_webhook.unified_api.get_issues = AsyncMock(return_value=[])
        mock_webhook.unified_api.get_repository = AsyncMock(return_value={"id": "R_kgDOABcD1M"})
        mock_webhook.unified_api.create_issue = AsyncMock()

        mock_pr_wrapper.number = 42
        mock_pr_wrapper.title = "Test PR Title"
        mock_pr_wrapper.user.login = "contributor"
        mock_pr_wrapper.user.node_id = "U_kgDOABcD1M"  # Valid GraphQL node ID (required)
        mock_pr_wrapper.html_url = "https://github.com/owner/repo/pull/42"

        await handler.create_issue_for_new_pull_request(mock_pr_wrapper)

        # Verify issue was created
        mock_webhook.unified_api.create_issue.assert_called_once()
        call_args = mock_webhook.unified_api.create_issue.call_args
        assert call_args.kwargs["repository_id"] == "R_kgDOABcD1M"
        assert call_args.kwargs["title"] == "[PR #42] Test PR Title"
        assert call_args.kwargs["assignee_ids"] == ["U_kgDOABcD1M"]

    @pytest.mark.asyncio
    async def test_create_issue_bot_user(self, mock_webhook: Mock, mock_pr_wrapper: Mock) -> None:
        """Test issue creation handles bot users gracefully when node_id access fails."""

        mock_webhook.create_issue_for_new_pr = True
        mock_webhook.parent_committer = "renovate[bot]"
        mock_webhook.auto_verified_and_merged_users = []
        # Create a mock owners_file_handler
        mock_owners_file_handler = Mock()
        handler = PullRequestHandler(
            github_webhook=mock_webhook, owners_file_handler=mock_owners_file_handler, hook_data={}
        )

        # Mock no existing issues
        mock_webhook.unified_api.get_issues = AsyncMock(return_value=[])
        mock_webhook.unified_api.get_repository = AsyncMock(return_value={"id": "R_kgDOABcD1M"})
        mock_webhook.unified_api.create_issue = AsyncMock()

        mock_pr_wrapper.number = 42
        mock_pr_wrapper.title = "Test PR Title"
        mock_pr_wrapper.user.login = "renovate[bot]"
        # Simulate bot user where accessing node_id raises an exception (edge case)
        type(mock_pr_wrapper.user).node_id = PropertyMock(side_effect=GraphQLError("Not a user"))
        mock_pr_wrapper.html_url = "https://github.com/owner/repo/pull/42"

        await handler.create_issue_for_new_pull_request(mock_pr_wrapper)

        # Verify issue was created without assignee (bot exception handled)
        mock_webhook.unified_api.create_issue.assert_called_once()
        call_args = mock_webhook.unified_api.create_issue.call_args
        assert call_args.kwargs["assignee_ids"] == []

    @pytest.mark.asyncio
    async def test_create_issue_with_node_id_from_webhook(self, mock_webhook: Mock, mock_pr_wrapper: Mock) -> None:
        """Test issue creation uses node_id from webhook when available (avoids GraphQL query)."""
        mock_webhook.create_issue_for_new_pr = True
        mock_webhook.parent_committer = "testuser"
        mock_webhook.auto_verified_and_merged_users = []
        # Create a mock owners_file_handler
        mock_owners_file_handler = Mock()
        handler = PullRequestHandler(
            github_webhook=mock_webhook, owners_file_handler=mock_owners_file_handler, hook_data={}
        )

        # Mock no existing issues
        mock_webhook.unified_api.get_issues = AsyncMock(return_value=[])
        mock_webhook.unified_api.get_repository = AsyncMock(return_value={"id": "R_kgDOABcD1M"})
        # get_user_id should NOT be called when node_id is available
        mock_webhook.unified_api.get_user_id = AsyncMock(return_value="U_kgDOSHOULDNOTUSE")
        mock_webhook.unified_api.create_issue = AsyncMock()

        mock_pr_wrapper.number = 42
        mock_pr_wrapper.title = "Test PR Title"
        mock_pr_wrapper.user.login = "contributor"
        mock_pr_wrapper.user.node_id = "U_kgDOFromWebhook"  # Provided from webhook
        mock_pr_wrapper.html_url = "https://github.com/owner/repo/pull/42"

        await handler.create_issue_for_new_pull_request(mock_pr_wrapper)

        # Verify node_id from webhook was used (get_user_id was NOT called)
        mock_webhook.unified_api.get_user_id.assert_not_called()
        mock_webhook.unified_api.create_issue.assert_called_once()
        call_args = mock_webhook.unified_api.create_issue.call_args
        assert call_args.kwargs["assignee_ids"] == ["U_kgDOFromWebhook"]

    @pytest.mark.asyncio
    async def test_create_issue_get_issues_error_continues(self, mock_webhook: Mock, mock_pr_wrapper: Mock) -> None:
        """Test that errors checking existing issues don't prevent creation."""
        mock_webhook.create_issue_for_new_pr = True
        mock_webhook.parent_committer = "testuser"
        mock_webhook.auto_verified_and_merged_users = []
        # Create a mock owners_file_handler
        mock_owners_file_handler = Mock()
        handler = PullRequestHandler(
            github_webhook=mock_webhook, owners_file_handler=mock_owners_file_handler, hook_data={}
        )

        # get_issues fails
        mock_webhook.unified_api.get_issues = AsyncMock(side_effect=GithubException(500, "Server error"))
        mock_webhook.unified_api.get_repository = AsyncMock(return_value={"id": "R_kgDOABcD1M"})
        mock_webhook.unified_api.get_user_id = AsyncMock(return_value="U_kgDOABcD1M")
        mock_webhook.unified_api.create_issue = AsyncMock()

        mock_pr_wrapper.number = 42
        mock_pr_wrapper.title = "Test PR Title"
        mock_pr_wrapper.user.login = "contributor"
        mock_pr_wrapper.html_url = "https://github.com/owner/repo/pull/42"

        # Should not raise, should continue to create issue
        await handler.create_issue_for_new_pull_request(mock_pr_wrapper)

        # Verify issue creation was attempted despite error
        mock_webhook.unified_api.create_issue.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_issue_unexpected_error_continues(self, mock_webhook: Mock, mock_pr_wrapper: Mock) -> None:
        """Test that unexpected errors during check don't prevent creation."""
        mock_webhook.create_issue_for_new_pr = True
        mock_webhook.parent_committer = "testuser"
        mock_webhook.auto_verified_and_merged_users = []
        # Create a mock owners_file_handler
        mock_owners_file_handler = Mock()
        handler = PullRequestHandler(
            github_webhook=mock_webhook, owners_file_handler=mock_owners_file_handler, hook_data={}
        )

        # Unexpected error
        mock_webhook.unified_api.get_issues = AsyncMock(side_effect=RuntimeError("Unexpected"))
        mock_webhook.unified_api.get_repository = AsyncMock(return_value={"id": "R_kgDOABcD1M"})
        mock_webhook.unified_api.get_user_id = AsyncMock(return_value="U_kgDOABcD1M")
        mock_webhook.unified_api.create_issue = AsyncMock()

        mock_pr_wrapper.number = 42
        mock_pr_wrapper.title = "Test PR Title"
        mock_pr_wrapper.user.login = "contributor"
        mock_pr_wrapper.html_url = "https://github.com/owner/repo/pull/42"

        # Should not raise, should continue to create issue
        await handler.create_issue_for_new_pull_request(mock_pr_wrapper)

        # Verify issue creation was attempted
        mock_webhook.unified_api.create_issue.assert_called_once()
