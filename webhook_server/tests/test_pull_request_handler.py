import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from github import GithubException
from github.PullRequest import PullRequest

from webhook_server.libs.github_api import GithubWebhook
from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
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
    PRE_COMMIT_STR,
    TOX_STR,
    VERIFIED_LABEL_STR,
    WIP_STR,
)


# Async shim for mocking asyncio.to_thread in tests
# This allows us to run sync functions in tests while preserving async/await semantics
async def _sync_to_thread[T](func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Mock implementation of asyncio.to_thread that runs synchronously but returns awaitable."""
    return func(*args, **kwargs)


class _AwaitableValue:
    def __init__(self, return_value: dict | None = None) -> None:
        self._value = return_value or {}

    def __await__(self):
        async def _inner() -> dict:
            return self._value

        return _inner().__await__()


def _owners_data_coroutine(return_value: dict | None = None) -> _AwaitableValue:
    return _AwaitableValue(return_value)


class TestPullRequestHandler:
    """Test suite for PullRequestHandler class."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance."""
        mock_webhook = Mock(spec=GithubWebhook)
        mock_webhook.hook_data = {
            "action": "opened",
            "pull_request": {"number": 123, "merged": False, "title": "Test PR"},
            "sender": {"login": "test-user"},
            "label": {"name": "bug"},
        }
        mock_webhook.logger = MagicMock()
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
        # New attributes for coverage
        mock_webhook.conventional_title = False
        mock_webhook.minimum_lgtm = 1
        mock_webhook.container_repository_username = "test-user"
        mock_webhook.container_repository_password = "test-password"  # pragma: allowlist secret
        mock_webhook.github_api = Mock()
        mock_webhook.tox = True
        mock_webhook.pre_commit = True
        mock_webhook.python_module_install = False
        mock_webhook.pypi = False
        mock_webhook.token = "test-token"  # pragma: allowlist secret
        mock_webhook.auto_verify_cherry_picked_prs = True
        mock_webhook.last_commit = Mock()
        return mock_webhook

    @pytest.fixture
    def mock_owners_file_handler(self) -> Mock:
        """Create a mock OwnersFileHandler instance."""
        mock_handler = Mock(spec=OwnersFileHandler)
        mock_handler.all_pull_request_approvers = ["approver1", "approver2"]
        mock_handler.all_pull_request_reviewers = ["reviewer1", "reviewer2"]
        mock_handler.root_approvers = ["root-approver"]
        mock_handler.root_reviewers = ["root-reviewer"]
        mock_handler.assign_reviewers = AsyncMock()
        return mock_handler

    @pytest.fixture
    def pull_request_handler(self, mock_github_webhook: Mock, mock_owners_file_handler: Mock) -> PullRequestHandler:
        """Create a PullRequestHandler instance with mocked dependencies."""
        # Create handler instance first
        handler = PullRequestHandler(mock_github_webhook, mock_owners_file_handler)

        # Replace handler instances with mocks that have async methods
        handler.labels_handler = Mock()
        handler.labels_handler._add_label = AsyncMock()
        handler.labels_handler._remove_label = AsyncMock()
        handler.labels_handler.add_size_label = AsyncMock()
        handler.labels_handler.pull_request_labels_names = AsyncMock(return_value=[])
        handler.labels_handler.wip_or_hold_labels_exists = Mock(return_value=False)

        handler.check_run_handler = Mock()
        handler.check_run_handler.set_verify_check_queued = AsyncMock()
        handler.check_run_handler.set_verify_check_success = AsyncMock()
        handler.check_run_handler.set_merge_check_in_progress = AsyncMock()
        handler.check_run_handler.set_merge_check_success = AsyncMock()
        handler.check_run_handler.set_merge_check_failure = AsyncMock()
        handler.check_run_handler.set_merge_check_queued = AsyncMock()
        handler.check_run_handler.set_run_tox_check_queued = AsyncMock()
        handler.check_run_handler.set_run_pre_commit_check_queued = AsyncMock()
        handler.check_run_handler.set_python_module_install_queued = AsyncMock()
        handler.check_run_handler.set_container_build_queued = AsyncMock()
        handler.check_run_handler.set_conventional_title_queued = AsyncMock()

        handler.runner_handler = Mock()
        handler.runner_handler.run_container_build = AsyncMock()
        handler.runner_handler.run_tox = AsyncMock()
        handler.runner_handler.run_pre_commit = AsyncMock()
        handler.runner_handler.run_conventional_title_check = AsyncMock()
        handler.runner_handler.run_build_container = AsyncMock()
        handler.runner_handler.run_install_python_module = AsyncMock()
        handler.runner_handler.run_podman_command = AsyncMock(return_value=(0, "", ""))
        handler.runner_handler.cherry_pick = AsyncMock()

        return handler

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        """Create a mock PullRequest instance."""
        mock_pr = MagicMock()
        mock_pr.number = 123
        mock_pr.title = "Test PR"
        mock_pr.body = "Test PR body"
        mock_pr.html_url = "https://github.com/test/repo/pull/123"
        mock_pr.labels = []
        mock_pr.create_issue_comment = Mock()
        mock_pr.edit = Mock()
        mock_pr.is_merged = Mock(return_value=False)
        mock_pr.base = Mock()
        mock_pr.base.ref = "main"
        mock_pr.user = Mock()
        mock_pr.user.login = "owner1"
        mock_pr.mergeable = True
        mock_pr.mergeable_state = "clean"
        mock_pr.enable_automerge = Mock()
        mock_pr.add_to_assignees = Mock()
        mock_pr.get_issue_comments = Mock(return_value=[])
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
        pull_request_handler.github_webhook.conventional_title = True

        await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)
        pull_request_handler.runner_handler.run_conventional_title_check.assert_called_once_with(
            pull_request=mock_pull_request
        )

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
                with patch.object(
                    pull_request_handler.runner_handler, "cherry_pick", new_callable=AsyncMock
                ) as mock_cherry_pick:
                    with patch.object(
                        pull_request_handler.runner_handler, "run_build_container", new_callable=AsyncMock
                    ) as mock_build:
                        with patch.object(
                            pull_request_handler,
                            "label_and_rerun_checks_all_opened_pull_requests_merge_state_after_merged",
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

        with patch.object(
            pull_request_handler.labels_handler, "_remove_label", new_callable=AsyncMock
        ) as mock_remove_label:
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
    async def test_label_and_rerun_checks_all_opened_pull_requests_merge_state_after_merged(
        self, pull_request_handler: PullRequestHandler
    ) -> None:
        """Test labeling all opened pull requests merge state after merged with retrigger not configured."""
        mock_pr1 = Mock()
        mock_pr2 = Mock()
        mock_pr1.number = 1
        mock_pr2.number = 2
        mock_pr1.mergeable_state = "clean"
        mock_pr2.mergeable_state = "clean"

        pull_request_handler.github_webhook.retrigger_checks_on_base_push = None

        with patch.object(pull_request_handler.repository, "get_pulls", return_value=[mock_pr1, mock_pr2]):
            with patch.object(pull_request_handler, "label_pull_request_by_merge_state", new=AsyncMock()) as mock_label:
                with patch("asyncio.sleep", new=AsyncMock()):
                    await (
                        pull_request_handler.label_and_rerun_checks_all_opened_pull_requests_merge_state_after_merged()
                    )
                    assert mock_label.await_count == 2

    @pytest.mark.asyncio
    async def test_label_and_rerun_checks_all_opened_pull_requests_merge_state_after_merged_with_retrigger(
        self, pull_request_handler: PullRequestHandler
    ) -> None:
        """Test labeling all opened pull requests with retrigger enabled."""
        mock_pr1 = Mock()
        mock_pr2 = Mock()
        mock_pr1.number = 1
        mock_pr2.number = 2
        mock_pr1.mergeable_state = "behind"
        mock_pr2.mergeable_state = "clean"

        pull_request_handler.github_webhook.retrigger_checks_on_base_push = True

        async def mock_label_side_effect(pull_request: Mock | PullRequest) -> str:
            return pull_request.mergeable_state

        with (
            patch.object(pull_request_handler.repository, "get_pulls", return_value=[mock_pr1, mock_pr2]),
            patch.object(
                pull_request_handler,
                "label_pull_request_by_merge_state",
                new=AsyncMock(side_effect=mock_label_side_effect),
            ) as mock_label,
            patch.object(pull_request_handler, "_retrigger_check_suites_for_pr", new=AsyncMock()) as mock_retrigger,
            patch("asyncio.sleep", new=AsyncMock()),
            patch("asyncio.to_thread", new=_sync_to_thread),
        ):
            await pull_request_handler.label_and_rerun_checks_all_opened_pull_requests_merge_state_after_merged()
            # Verify labeling called for both PRs
            assert mock_label.await_count == 2
            # Verify retrigger only called for PR with merge_state="behind"
            mock_retrigger.assert_called_once_with(pull_request=mock_pr1)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "merge_state,should_retrigger",
        [
            ("behind", True),  # Out-of-date - should retrigger
            ("blocked", True),  # Blocked by reviews/checks - currently triggers (per implementation)
            ("clean", False),  # Up-to-date - no retrigger needed
            ("dirty", False),  # Has conflicts - retrigger won't help
            ("unstable", False),  # Failing checks - no retrigger
            ("unknown", False),  # Unknown state - skip processing
        ],
    )
    async def test_label_all_opened_prs_retrigger_for_different_merge_states(
        self,
        pull_request_handler: PullRequestHandler,
        mock_github_webhook: Mock,
        merge_state: str,
        should_retrigger: bool,
    ) -> None:
        """Test retrigger behavior for different merge states."""
        mock_github_webhook.retrigger_checks_on_base_push = True

        mock_pr = Mock()
        mock_pr.number = 1
        mock_pr.mergeable_state = merge_state

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("asyncio.to_thread", new=_sync_to_thread),
            patch.object(pull_request_handler.repository, "get_pulls", return_value=[mock_pr]),
            patch.object(
                pull_request_handler,
                "label_pull_request_by_merge_state",
                new_callable=AsyncMock,
                return_value=merge_state,
            ),
            patch.object(
                pull_request_handler, "_retrigger_check_suites_for_pr", new_callable=AsyncMock
            ) as mock_retrigger,
        ):
            await pull_request_handler.label_and_rerun_checks_all_opened_pull_requests_merge_state_after_merged()

            if should_retrigger:
                mock_retrigger.assert_called_once_with(pull_request=mock_pr)
            else:
                mock_retrigger.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_remote_tag_for_merged_or_closed_pr_with_tag(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        mock_pull_request.title = "Test PR"
        mock_pull_request.number = 123
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
                new=AsyncMock(side_effect=[(True, "", ""), (True, "tag exists", ""), (True, "", ""), (True, "", "")]),
            ),
            patch.object(mock_pull_request, "create_issue_comment", new=Mock()),
        ):
            await pull_request_handler.delete_remote_tag_for_merged_or_closed_pr(pull_request=mock_pull_request)
            # Verify step logging was called
            assert pull_request_handler.logger.step.called
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

        with patch.object(pull_request_handler.labels_handler, "_add_label", new_callable=AsyncMock) as mock_add_label:
            await pull_request_handler.label_pull_request_by_merge_state(pull_request=mock_pull_request)
            mock_add_label.assert_called_once_with(pull_request=mock_pull_request, label=HAS_CONFLICTS_LABEL_STR)

    @pytest.mark.asyncio
    async def test_process_verified_for_update_or_new_pull_request_auto_verified(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test processing verified for update or new pull request for auto-verified user."""
        with patch.object(pull_request_handler.labels_handler, "_add_label", new_callable=AsyncMock) as mock_add_label:
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

        with patch.object(pull_request_handler.labels_handler, "_add_label", new_callable=AsyncMock) as mock_add_label:
            with patch.object(pull_request_handler.check_run_handler, "set_verify_check_success") as mock_success:
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
        mock_pull_request.labels = [mock_label]

        with (
            patch.object(pull_request_handler.github_webhook, "auto_verify_cherry_picked_prs", True),
            patch.object(pull_request_handler.labels_handler, "_add_label") as mock_add_label,
            patch.object(pull_request_handler.check_run_handler, "set_verify_check_success") as mock_set_success,
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
        mock_pull_request.labels = [mock_label]

        with (
            patch.object(pull_request_handler.github_webhook, "auto_verify_cherry_picked_prs", False),
            patch.object(pull_request_handler.labels_handler, "_add_label") as mock_add_label,
            patch.object(pull_request_handler.check_run_handler, "set_verify_check_queued") as mock_set_queued,
        ):
            await pull_request_handler._process_verified_for_update_or_new_pull_request(mock_pull_request)
            # Should NOT auto-verify since auto_verify_cherry_picked_prs is False
            mock_add_label.assert_not_called()
            mock_set_queued.assert_called_once()

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
                with patch.object(
                    pull_request_handler.labels_handler, "_remove_label", new_callable=AsyncMock
                ) as mock_remove_label:
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
                _owners_data_coroutine(),
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
                pull_request_handler.github_webhook,
                "last_commit",
                Mock(get_check_runs=Mock(return_value=[]), get_statuses=Mock(return_value=[])),
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
                _owners_data_coroutine(),
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
                _owners_data_coroutine(),
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
                _owners_data_coroutine(),
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
                _owners_data_coroutine(),
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
                _owners_data_coroutine(),
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

    @pytest.mark.asyncio
    async def test_skip_if_pull_request_already_merged_merged(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test skipping if pull request is already merged."""
        # Patch is_merged as a method that returns True
        with patch.object(mock_pull_request, "is_merged", new=Mock(return_value=True)):
            result = await pull_request_handler.skip_if_pull_request_already_merged(pull_request=mock_pull_request)
            assert result is True

    @pytest.mark.asyncio
    async def test_skip_if_pull_request_already_merged_not_merged(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test skipping if pull request is not merged."""
        # Patch is_merged as a method that returns False
        with patch.object(mock_pull_request, "is_merged", new=Mock(return_value=False)):
            result = await pull_request_handler.skip_if_pull_request_already_merged(pull_request=mock_pull_request)
            assert result is False

    @pytest.mark.asyncio
    async def test_delete_remote_tag_for_merged_or_closed_pr_without_tag(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test deleting remote tag for merged or closed PR without tag."""
        mock_pull_request.title = "Test PR"
        mock_pull_request.number = 123

        with patch.object(pull_request_handler.github_webhook, "build_and_push_container", False):
            await pull_request_handler.delete_remote_tag_for_merged_or_closed_pr(pull_request=mock_pull_request)
            # Verify step logging was called (processing + completed)
            assert pull_request_handler.logger.step.call_count >= 2
            # Should return early when build_and_push_container is False

    @pytest.mark.asyncio
    async def test_delete_remote_tag_for_merged_or_closed_pr_failed_deletion(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test deleting remote tag when deletion fails."""
        mock_pull_request.title = "Test PR"
        mock_pull_request.number = 123
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
                new=AsyncMock(
                    side_effect=[(True, "", ""), (True, "tag exists", ""), (False, "out", "err"), (True, "", "")]
                ),
            ),
        ):
            await pull_request_handler.delete_remote_tag_for_merged_or_closed_pr(pull_request=mock_pull_request)
            # Verify step logging was called (processing + failed)
            assert pull_request_handler.logger.step.called
            # Verify error was logged
            assert pull_request_handler.logger.error.called

    @pytest.mark.asyncio
    async def test_delete_remote_tag_for_merged_or_closed_pr_login_failed(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test deleting remote tag when registry login fails."""
        mock_pull_request.title = "Test PR"
        mock_pull_request.number = 123
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
                new=AsyncMock(return_value=(False, "login failed", "error")),
            ),
            patch.object(mock_pull_request, "create_issue_comment", new=Mock()),
        ):
            await pull_request_handler.delete_remote_tag_for_merged_or_closed_pr(pull_request=mock_pull_request)
            # Verify step logging was called (processing + failed)
            assert pull_request_handler.logger.step.called
            # Verify error was logged
            assert pull_request_handler.logger.error.called

    @pytest.mark.asyncio
    async def test_delete_remote_tag_for_merged_or_closed_pr_ghcr_success(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test deleting GHCR tag successfully."""
        mock_pull_request.title = "Test PR"
        mock_pull_request.number = 123
        mock_requester = Mock()
        mock_requester.requestJsonAndCheck = Mock(
            side_effect=[
                ({}, [{"id": 1, "metadata": {"container": {"tags": ["pr-123"]}}}]),
                None,  # DELETE call returns None
            ]
        )
        with (
            patch.object(pull_request_handler.github_webhook, "build_and_push_container", True),
            patch.object(
                pull_request_handler.github_webhook,
                "container_repository_and_tag",
                return_value="ghcr.io/org/repo:pr-123",
            ),
            patch.object(pull_request_handler.github_webhook, "container_repository", "ghcr.io/org/repo"),
            patch.object(pull_request_handler.github_webhook, "github_api", Mock(requester=mock_requester)),
            patch.object(pull_request_handler.github_webhook, "token", "test-token"),  # pragma: allowlist secret
            patch.object(mock_pull_request, "create_issue_comment", new=Mock()),
        ):
            await pull_request_handler.delete_remote_tag_for_merged_or_closed_pr(pull_request=mock_pull_request)
            assert pull_request_handler.logger.step.called
            assert mock_pull_request.create_issue_comment.called

    @pytest.mark.asyncio
    async def test_delete_remote_tag_for_merged_or_closed_pr_ghcr_users_scope_fallback(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test deleting GHCR tag when package is found under /users/{owner} scope (not /orgs/{owner})."""
        mock_pull_request.title = "Test PR"
        mock_pull_request.number = 123
        mock_requester = Mock()
        # First call to /orgs/{owner}/packages/... returns 404 (not found)
        # Second call to /users/{owner}/packages/... returns versions (found)
        # Third call is the DELETE operation
        org_404_exception = GithubException(404, {}, {})
        mock_requester.requestJsonAndCheck = Mock(
            side_effect=[
                org_404_exception,  # /orgs/{owner}/packages/... returns 404
                ({}, [{"id": 1, "metadata": {"container": {"tags": ["pr-123"]}}}]),  # /users/{owner}/packages/...
                None,  # DELETE call returns None
            ]
        )
        with (
            patch.object(pull_request_handler.github_webhook, "build_and_push_container", True),
            patch.object(
                pull_request_handler.github_webhook,
                "container_repository_and_tag",
                return_value="ghcr.io/org/repo:pr-123",
            ),
            patch.object(pull_request_handler.github_webhook, "container_repository", "ghcr.io/org/repo"),
            patch.object(pull_request_handler.github_webhook, "github_api", Mock(requester=mock_requester)),
            patch.object(pull_request_handler.github_webhook, "token", "test-token"),  # pragma: allowlist secret
            patch.object(mock_pull_request, "create_issue_comment", new=Mock()),
        ):
            await pull_request_handler.delete_remote_tag_for_merged_or_closed_pr(pull_request=mock_pull_request)
            # Verify the deletion was successful
            assert pull_request_handler.logger.step.called
            assert mock_pull_request.create_issue_comment.called
            # Verify requestJsonAndCheck was called 3 times (orgs GET, users GET, DELETE)
            assert mock_requester.requestJsonAndCheck.call_count == 3

    @pytest.mark.asyncio
    async def test_delete_remote_tag_for_merged_or_closed_pr_ghcr_package_not_found(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test deleting GHCR tag when package is not found (404)."""
        mock_pull_request.title = "Test PR"
        mock_pull_request.number = 123
        mock_requester = Mock()
        ex = GithubException(404, {}, {})
        mock_requester.requestJsonAndCheck = Mock(side_effect=ex)
        with (
            patch.object(pull_request_handler.github_webhook, "build_and_push_container", True),
            patch.object(
                pull_request_handler.github_webhook,
                "container_repository_and_tag",
                return_value="ghcr.io/org/repo:pr-123",
            ),
            patch.object(pull_request_handler.github_webhook, "container_repository", "ghcr.io/org/repo"),
            patch.object(pull_request_handler.github_webhook, "github_api", Mock(requester=mock_requester)),
            patch.object(pull_request_handler.github_webhook, "token", "test-token"),  # pragma: allowlist secret
        ):
            await pull_request_handler.delete_remote_tag_for_merged_or_closed_pr(pull_request=mock_pull_request)
            assert pull_request_handler.logger.step.called
            assert pull_request_handler.logger.warning.called

    @pytest.mark.asyncio
    async def test_delete_remote_tag_for_merged_or_closed_pr_ghcr_tag_not_found(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test deleting GHCR tag when tag is not found in package versions."""
        mock_pull_request.title = "Test PR"
        mock_pull_request.number = 123
        mock_requester = Mock()
        mock_requester.requestJsonAndCheck = Mock(
            return_value=({}, [{"id": 1, "metadata": {"container": {"tags": ["other-tag"]}}}])
        )
        with (
            patch.object(pull_request_handler.github_webhook, "build_and_push_container", True),
            patch.object(
                pull_request_handler.github_webhook,
                "container_repository_and_tag",
                return_value="ghcr.io/org/repo:pr-123",
            ),
            patch.object(pull_request_handler.github_webhook, "container_repository", "ghcr.io/org/repo"),
            patch.object(pull_request_handler.github_webhook, "github_api", Mock(requester=mock_requester)),
            patch.object(pull_request_handler.github_webhook, "token", "test-token"),  # pragma: allowlist secret
        ):
            await pull_request_handler.delete_remote_tag_for_merged_or_closed_pr(pull_request=mock_pull_request)
            assert pull_request_handler.logger.step.called
            assert pull_request_handler.logger.warning.called

    @pytest.mark.asyncio
    async def test_delete_remote_tag_for_merged_or_closed_pr_ghcr_api_failure(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test deleting GHCR tag when API call fails."""
        mock_pull_request.title = "Test PR"
        mock_pull_request.number = 123
        mock_requester = Mock()
        ex = GithubException(500, {}, {})
        mock_requester.requestJsonAndCheck = Mock(side_effect=ex)
        with (
            patch.object(pull_request_handler.github_webhook, "build_and_push_container", True),
            patch.object(
                pull_request_handler.github_webhook,
                "container_repository_and_tag",
                return_value="ghcr.io/org/repo:pr-123",
            ),
            patch.object(pull_request_handler.github_webhook, "container_repository", "ghcr.io/org/repo"),
            patch.object(pull_request_handler.github_webhook, "github_api", Mock(requester=mock_requester)),
            patch.object(pull_request_handler.github_webhook, "token", "test-token"),  # pragma: allowlist secret
        ):
            await pull_request_handler.delete_remote_tag_for_merged_or_closed_pr(pull_request=mock_pull_request)
            assert pull_request_handler.logger.step.called
            assert pull_request_handler.logger.exception.called

    @pytest.mark.asyncio
    async def test_delete_remote_tag_for_merged_or_closed_pr_ghcr_no_api(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test deleting GHCR tag when GitHub API is not available."""
        mock_pull_request.title = "Test PR"
        mock_pull_request.number = 123
        with (
            patch.object(pull_request_handler.github_webhook, "build_and_push_container", True),
            patch.object(
                pull_request_handler.github_webhook,
                "container_repository_and_tag",
                return_value="ghcr.io/org/repo:pr-123",
            ),
            patch.object(pull_request_handler.github_webhook, "container_repository", "ghcr.io/org/repo"),
            patch.object(pull_request_handler.github_webhook, "github_api", None),
            patch.object(pull_request_handler.github_webhook, "token", "test-token"),  # pragma: allowlist secret
        ):
            await pull_request_handler.delete_remote_tag_for_merged_or_closed_pr(pull_request=mock_pull_request)
            assert pull_request_handler.logger.step.called
            assert pull_request_handler.logger.error.called

    @pytest.mark.asyncio
    async def test_delete_remote_tag_for_merged_or_closed_pr_ghcr_invalid_format(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test deleting GHCR tag with invalid repository format."""
        mock_pull_request.title = "Test PR"
        mock_pull_request.number = 123
        mock_requester = Mock()
        with (
            patch.object(pull_request_handler.github_webhook, "build_and_push_container", True),
            patch.object(
                pull_request_handler.github_webhook,
                "container_repository_and_tag",
                return_value="ghcr.io/invalid:pr-123",
            ),
            patch.object(pull_request_handler.github_webhook, "container_repository", "ghcr.io/invalid"),
            patch.object(pull_request_handler.github_webhook, "github_api", Mock(requester=mock_requester)),
            patch.object(pull_request_handler.github_webhook, "token", "test-token"),  # pragma: allowlist secret
        ):
            # Directly call _delete_ghcr_tag_via_github_api to test invalid format check
            await pull_request_handler._delete_ghcr_tag_via_github_api(
                pull_request=mock_pull_request,
                repository_full_tag="ghcr.io/invalid:pr-123",
                pr_tag="pr-123",
            )
            assert pull_request_handler.logger.step.called
            assert pull_request_handler.logger.error.called

    @pytest.mark.asyncio
    async def test_delete_remote_tag_for_merged_or_closed_pr_ghcr_delete_404(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test deleting GHCR tag when version deletion returns 404 (already deleted)."""
        mock_pull_request.title = "Test PR"
        mock_pull_request.number = 123
        mock_requester = Mock()
        ex = GithubException(404, {}, {})
        mock_requester.requestJsonAndCheck = Mock(
            side_effect=[
                ({}, [{"id": 1, "metadata": {"container": {"tags": ["pr-123"]}}}]),
                ex,  # DELETE call returns 404
            ]
        )
        with (
            patch.object(pull_request_handler.github_webhook, "build_and_push_container", True),
            patch.object(
                pull_request_handler.github_webhook,
                "container_repository_and_tag",
                return_value="ghcr.io/org/repo:pr-123",
            ),
            patch.object(pull_request_handler.github_webhook, "container_repository", "ghcr.io/org/repo"),
            patch.object(pull_request_handler.github_webhook, "github_api", Mock(requester=mock_requester)),
            patch.object(pull_request_handler.github_webhook, "token", "test-token"),  # pragma: allowlist secret
            patch.object(mock_pull_request, "create_issue_comment", new=Mock()),
        ):
            await pull_request_handler.delete_remote_tag_for_merged_or_closed_pr(pull_request=mock_pull_request)
            assert pull_request_handler.logger.step.called
            assert pull_request_handler.logger.warning.called

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

    # /reprocess command tests

    @pytest.mark.asyncio
    async def test_process_command_reprocess_merged_pr(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test /reprocess command on merged PR - should reject and skip."""
        # Mock is_merged to return True
        with (
            patch.object(mock_pull_request, "is_merged", new=Mock(return_value=True)),
            patch.object(
                pull_request_handler, "process_new_or_reprocess_pull_request", new=AsyncMock()
            ) as mock_process_new,
        ):
            await pull_request_handler.process_command_reprocess(pull_request=mock_pull_request)

            # Verify is_merged was checked
            mock_pull_request.is_merged.assert_called_once()

            # Verify workflow was NOT executed
            mock_process_new.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_command_reprocess_open_pr_success(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test /reprocess command on open PR - should trigger full workflow."""
        # Mock is_merged to return False
        with (
            patch.object(mock_pull_request, "is_merged", new=Mock(return_value=False)),
            patch.object(
                pull_request_handler, "process_new_or_reprocess_pull_request", new=AsyncMock()
            ) as mock_process_new,
        ):
            await pull_request_handler.process_command_reprocess(pull_request=mock_pull_request)

            # Verify is_merged was checked
            mock_pull_request.is_merged.assert_called_once()

            # Verify workflow was executed
            mock_process_new.assert_awaited_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_welcome_comment_exists_true(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test _welcome_comment_exists returns True when welcome message exists."""
        mock_comment = Mock()
        mock_comment.body = f"Some text {pull_request_handler.github_webhook.issue_url_for_welcome_msg} more text"

        with patch.object(mock_pull_request, "get_issue_comments", return_value=[mock_comment]):
            result = await pull_request_handler._welcome_comment_exists(pull_request=mock_pull_request)
            assert result is True

    @pytest.mark.asyncio
    async def test_welcome_comment_exists_false(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test _welcome_comment_exists returns False when no welcome message."""
        mock_comment = Mock()
        mock_comment.body = "Regular comment without welcome URL"

        with patch.object(mock_pull_request, "get_issue_comments", return_value=[mock_comment]):
            result = await pull_request_handler._welcome_comment_exists(pull_request=mock_pull_request)
            assert result is False

    @pytest.mark.asyncio
    async def test_welcome_comment_exists_empty_comments(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test _welcome_comment_exists returns False when no comments."""
        with patch.object(mock_pull_request, "get_issue_comments", return_value=[]):
            result = await pull_request_handler._welcome_comment_exists(pull_request=mock_pull_request)
            assert result is False

    @pytest.mark.asyncio
    async def test_tracking_issue_exists_true(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test _tracking_issue_exists returns True when tracking issue exists."""
        mock_pull_request.number = 123
        expected_body = pull_request_handler._generate_issue_body(pull_request=mock_pull_request)

        mock_issue = Mock()
        mock_issue.body = expected_body

        with patch.object(pull_request_handler.repository, "get_issues", return_value=[mock_issue]):
            result = await pull_request_handler._tracking_issue_exists(pull_request=mock_pull_request)
            assert result is True

    @pytest.mark.asyncio
    async def test_tracking_issue_exists_false(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test _tracking_issue_exists returns False when no tracking issue."""
        mock_issue = Mock()
        mock_issue.body = "Some other issue body"

        with patch.object(pull_request_handler.repository, "get_issues", return_value=[mock_issue]):
            result = await pull_request_handler._tracking_issue_exists(pull_request=mock_pull_request)
            assert result is False

    @pytest.mark.asyncio
    async def test_tracking_issue_exists_empty_issues(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test _tracking_issue_exists returns False when no issues."""
        with patch.object(pull_request_handler.repository, "get_issues", return_value=[]):
            result = await pull_request_handler._tracking_issue_exists(pull_request=mock_pull_request)
            assert result is False

    @pytest.mark.asyncio
    async def test_process_new_or_reprocess_pull_request_full_workflow(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test process_new_or_reprocess_pull_request - full workflow without duplicates."""
        # Mock welcome message and tracking issue don't exist
        with (
            patch.object(
                pull_request_handler, "_welcome_comment_exists", new=AsyncMock(return_value=False)
            ) as mock_welcome_check,
            patch.object(
                pull_request_handler, "_tracking_issue_exists", new=AsyncMock(return_value=False)
            ) as mock_issue_check,
            patch.object(mock_pull_request, "create_issue_comment", new=Mock()) as mock_comment,
            patch.object(
                pull_request_handler, "create_issue_for_new_pull_request", new=AsyncMock()
            ) as mock_create_issue,
            patch.object(pull_request_handler, "set_wip_label_based_on_title", new=AsyncMock()) as mock_wip,
            patch.object(
                pull_request_handler, "process_opened_or_synchronize_pull_request", new=AsyncMock()
            ) as mock_process,
            patch.object(pull_request_handler, "set_pull_request_automerge", new=AsyncMock()) as mock_automerge,
        ):
            await pull_request_handler.process_new_or_reprocess_pull_request(pull_request=mock_pull_request)

            # Verify duplicate checks were called
            mock_welcome_check.assert_awaited_once_with(pull_request=mock_pull_request)
            mock_issue_check.assert_awaited_once_with(pull_request=mock_pull_request)

            # Verify welcome message was created with the correct marker
            mock_comment.assert_called_once()
            assert pull_request_handler.github_webhook.issue_url_for_welcome_msg in mock_comment.call_args[1]["body"]

            # Verify tracking issue was created
            mock_create_issue.assert_awaited_once_with(pull_request=mock_pull_request)

            # Verify other tasks were executed
            mock_wip.assert_awaited_once_with(pull_request=mock_pull_request)
            mock_process.assert_awaited_once_with(pull_request=mock_pull_request)
            mock_automerge.assert_awaited_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_process_new_or_reprocess_pull_request_skip_welcome_duplicate(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test process_new_or_reprocess_pull_request - skip welcome if already exists."""
        # Mock welcome message exists, tracking issue doesn't
        with (
            patch.object(
                pull_request_handler, "_welcome_comment_exists", new=AsyncMock(return_value=True)
            ) as mock_welcome_check,
            patch.object(pull_request_handler, "_tracking_issue_exists", new=AsyncMock(return_value=False)),
            patch.object(mock_pull_request, "create_issue_comment", new=Mock()) as mock_comment,
            patch.object(pull_request_handler, "create_issue_for_new_pull_request", new=AsyncMock()),
            patch.object(pull_request_handler, "set_wip_label_based_on_title", new=AsyncMock()),
            patch.object(pull_request_handler, "process_opened_or_synchronize_pull_request", new=AsyncMock()),
            patch.object(pull_request_handler, "set_pull_request_automerge", new=AsyncMock()),
        ):
            await pull_request_handler.process_new_or_reprocess_pull_request(pull_request=mock_pull_request)

            # Verify welcome check was called
            mock_welcome_check.assert_awaited_once_with(pull_request=mock_pull_request)

            # Verify welcome message was NOT created (already exists)
            mock_comment.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_new_or_reprocess_pull_request_skip_issue_duplicate(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test process_new_or_reprocess_pull_request - skip tracking issue if already exists."""
        # Mock welcome doesn't exist, tracking issue exists
        with (
            patch.object(pull_request_handler, "_welcome_comment_exists", new=AsyncMock(return_value=False)),
            patch.object(
                pull_request_handler, "_tracking_issue_exists", new=AsyncMock(return_value=True)
            ) as mock_issue_check,
            patch.object(mock_pull_request, "create_issue_comment", new=Mock()),
            patch.object(
                pull_request_handler, "create_issue_for_new_pull_request", new=AsyncMock()
            ) as mock_create_issue,
            patch.object(pull_request_handler, "set_wip_label_based_on_title", new=AsyncMock()),
            patch.object(pull_request_handler, "process_opened_or_synchronize_pull_request", new=AsyncMock()),
            patch.object(pull_request_handler, "set_pull_request_automerge", new=AsyncMock()),
        ):
            await pull_request_handler.process_new_or_reprocess_pull_request(pull_request=mock_pull_request)

            # Verify issue check was called
            mock_issue_check.assert_awaited_once_with(pull_request=mock_pull_request)

            # Verify tracking issue was NOT created (already exists)
            mock_create_issue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_new_or_reprocess_pull_request_skip_both_duplicates(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test process_new_or_reprocess_pull_request - skip both welcome and issue if exist."""
        # Mock both already exist
        with (
            patch.object(pull_request_handler, "_welcome_comment_exists", new=AsyncMock(return_value=True)),
            patch.object(pull_request_handler, "_tracking_issue_exists", new=AsyncMock(return_value=True)),
            patch.object(mock_pull_request, "create_issue_comment", new=Mock()) as mock_comment,
            patch.object(
                pull_request_handler, "create_issue_for_new_pull_request", new=AsyncMock()
            ) as mock_create_issue,
            patch.object(pull_request_handler, "set_wip_label_based_on_title", new=AsyncMock()) as mock_wip,
            patch.object(
                pull_request_handler, "process_opened_or_synchronize_pull_request", new=AsyncMock()
            ) as mock_process,
            patch.object(pull_request_handler, "set_pull_request_automerge", new=AsyncMock()),
        ):
            await pull_request_handler.process_new_or_reprocess_pull_request(pull_request=mock_pull_request)

            # Verify neither welcome nor issue were created
            mock_comment.assert_not_called()
            mock_create_issue.assert_not_awaited()

            # Verify workflow tasks still executed
            mock_wip.assert_awaited_once()
            mock_process.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_new_or_reprocess_pull_request_parallel_execution(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test process_new_or_reprocess_pull_request executes tasks in parallel."""

        # Track that asyncio.gather was used while still executing the real gather
        real_gather = asyncio.gather
        gather_calls: dict[str, int] = {"count": 0}

        async def tracking_gather(*args, **kwargs):  # type: ignore[unused-argument]
            gather_calls["count"] += 1
            return await real_gather(*args, **kwargs)

        # Mock nothing exists - full workflow
        with (
            patch.object(pull_request_handler, "_welcome_comment_exists", new=AsyncMock(return_value=False)),
            patch.object(pull_request_handler, "_tracking_issue_exists", new=AsyncMock(return_value=False)),
            patch.object(mock_pull_request, "create_issue_comment", new=Mock()),
            patch.object(pull_request_handler, "create_issue_for_new_pull_request", new=AsyncMock()),
            patch.object(pull_request_handler, "set_wip_label_based_on_title", new=AsyncMock()),
            patch.object(pull_request_handler, "process_opened_or_synchronize_pull_request", new=AsyncMock()),
            patch.object(pull_request_handler, "set_pull_request_automerge", new=AsyncMock()),
            patch("asyncio.gather", new=tracking_gather),
        ):
            await pull_request_handler.process_new_or_reprocess_pull_request(pull_request=mock_pull_request)

            # Verify asyncio.gather was called (parallel execution)
            assert gather_calls["count"] >= 1

    @pytest.mark.asyncio
    async def test_process_new_or_reprocess_pull_request_exception_handling(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test process_new_or_reprocess_pull_request handles exceptions gracefully."""

        async def failing_create_issue(*args, **kwargs):  # type: ignore[unused-argument]
            raise Exception("Test error")

        async def always_false(*args, **kwargs) -> bool:  # type: ignore[unused-argument]
            return False

        def mock_create_issue_comment(*args, **kwargs):  # type: ignore[unused-argument]
            return None

        calls: dict[str, int] = {
            "set_wip": 0,
            "process_opened": 0,
            "set_automerge": 0,
        }

        async def set_wip_stub(*args, **kwargs):  # type: ignore[unused-argument]
            calls["set_wip"] += 1

        async def process_opened_stub(*args, **kwargs):  # type: ignore[unused-argument]
            calls["process_opened"] += 1

        async def set_automerge_stub(*args, **kwargs):  # type: ignore[unused-argument]
            calls["set_automerge"] += 1

        # Mock one task failing while others still execute
        with (
            patch.object(pull_request_handler, "_welcome_comment_exists", new=always_false),
            patch.object(pull_request_handler, "_tracking_issue_exists", new=always_false),
            patch.object(mock_pull_request, "create_issue_comment", new=mock_create_issue_comment),
            patch.object(
                pull_request_handler,
                "create_issue_for_new_pull_request",
                new=failing_create_issue,
            ),
            patch.object(pull_request_handler, "set_wip_label_based_on_title", new=set_wip_stub),
            patch.object(
                pull_request_handler,
                "process_opened_or_synchronize_pull_request",
                new=process_opened_stub,
            ),
            patch.object(pull_request_handler, "set_pull_request_automerge", new=set_automerge_stub),
        ):
            # Should not raise exception - errors are caught and logged
            await pull_request_handler.process_new_or_reprocess_pull_request(pull_request=mock_pull_request)

            # Verify automerge and other tasks were called despite error in one task
            assert calls["set_automerge"] == 1

    @pytest.mark.asyncio
    async def test_process_opened_async_exception(
        self, pull_request_handler: PullRequestHandler, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test exception handling in async tasks for opened event."""
        mock_github_webhook.hook_data["action"] = "opened"

        # Mock methods to raise exception
        with (
            patch.object(
                pull_request_handler,
                "create_issue_for_new_pull_request",
                new=AsyncMock(side_effect=Exception("Task failed")),
            ),
            patch.object(pull_request_handler, "set_wip_label_based_on_title", new=AsyncMock()),
            patch.object(pull_request_handler, "process_opened_or_synchronize_pull_request", new=AsyncMock()),
            patch.object(pull_request_handler, "set_pull_request_automerge", new=AsyncMock()),
            patch.object(pull_request_handler, "_prepare_welcome_comment", return_value="welcome"),
        ):
            await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)

            # Verify error logging
            pull_request_handler.logger.error.assert_called_with("[TEST] Async task failed: Task failed")

    @pytest.mark.asyncio
    async def test_process_synchronize_async_exception(
        self, pull_request_handler: PullRequestHandler, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test exception handling in async tasks for synchronize event."""
        mock_github_webhook.hook_data["action"] = "synchronize"

        with (
            patch.object(
                pull_request_handler,
                "process_opened_or_synchronize_pull_request",
                new=AsyncMock(side_effect=Exception("Sync failed")),
            ),
            patch.object(pull_request_handler, "remove_labels_when_pull_request_sync", new=AsyncMock()),
        ):
            await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)

            pull_request_handler.logger.error.assert_called_with("[TEST] Async task failed: Sync failed")

    @pytest.mark.asyncio
    async def test_process_labeled_can_be_merged(
        self, pull_request_handler: PullRequestHandler, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test labeled event with can-be-merged label (should skip)."""
        mock_github_webhook.hook_data["action"] = "labeled"
        mock_github_webhook.hook_data["label"] = {"name": CAN_BE_MERGED_STR}
        mock_github_webhook.verified_job = False

        await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)

        # Verify step call with substring
        found = False
        for call in pull_request_handler.logger.step.call_args_list:
            if "skipped - can-be-merged label" in str(call):
                found = True
                break
        assert found, "Log step for can-be-merged label skip not found"

    @pytest.mark.asyncio
    async def test_process_labeled_wip(
        self, pull_request_handler: PullRequestHandler, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test labeled event with WIP label."""
        mock_github_webhook.hook_data["action"] = "labeled"
        mock_github_webhook.hook_data["label"] = {"name": WIP_STR}
        mock_github_webhook.verified_job = False

        # Mock labels
        mock_label = MagicMock()
        mock_label.name = WIP_STR
        mock_pull_request.labels = [mock_label]

        with patch.object(pull_request_handler, "check_if_can_be_merged", new=AsyncMock()) as mock_check_merge:
            with patch("asyncio.to_thread", new=_sync_to_thread):
                await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)
            mock_check_merge.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_unhandled_action(
        self, pull_request_handler: PullRequestHandler, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test unhandled action."""
        mock_github_webhook.hook_data["action"] = "unknown_action"

        await pull_request_handler.process_pull_request_webhook_data(mock_pull_request)

        found = False
        for call in pull_request_handler.logger.step.call_args_list:
            if "no action handler - completed" in str(call):
                found = True
                break
        assert found

    @pytest.mark.asyncio
    async def test_delete_ghcr_tag_exceptions(
        self, pull_request_handler: PullRequestHandler, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test exceptions in _delete_ghcr_tag_via_github_api."""
        mock_github_webhook.build_and_push_container = True
        mock_github_webhook.container_repository = "ghcr.io/org/pkg"
        mock_github_webhook.container_repository_and_tag = MagicMock(return_value="ghcr.io/org/pkg:123")
        mock_github_webhook.github_api = MagicMock()
        mock_github_webhook.token = "token"  # pragma: allowlist secret

        mock_pull_request.number = 123

        # 1. Invalid repository format - call directly to bypass parent check
        mock_github_webhook.container_repository = "ghcr.io/invalid"
        await pull_request_handler._delete_ghcr_tag_via_github_api(mock_pull_request, "ghcr.io/invalid", "123")
        pull_request_handler.logger.error.assert_called_with(
            "[TEST] Invalid container repository format: ghcr.io/invalid"
        )

        # 2. Package not found (GithubException 404)
        mock_github_webhook.container_repository = "ghcr.io/org/pkg"
        mock_github_webhook.github_api.requester.requestJsonAndCheck = MagicMock(
            side_effect=GithubException(404, "Not Found")
        )

        with patch("asyncio.to_thread", new=_sync_to_thread):
            await pull_request_handler._delete_ghcr_tag_via_github_api(mock_pull_request, "ghcr.io/org/pkg:123", "123")

        pull_request_handler.logger.warning.assert_called_with("[TEST] Package pkg not found for owner org on GHCR")

    @pytest.mark.asyncio
    async def test_add_assignee_exception(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test exception in add_pull_request_owner_as_assingee."""
        mock_pull_request.user.login = "user"

        # Set side_effect to raise first time, then succeed (or return None) second time
        mock_pull_request.add_to_assignees.side_effect = [Exception("Failed"), None]

        pull_request_handler.owners_file_handler.root_approvers = ["approver1"]

        with patch("asyncio.to_thread", new=_sync_to_thread):
            await pull_request_handler.add_pull_request_owner_as_assingee(mock_pull_request)

        pull_request_handler.logger.debug.assert_any_call("[TEST] Exception while adding PR owner as assignee: Failed")
        pull_request_handler.logger.debug.assert_any_call("[TEST] Falling back to first approver as assignee")
        # Should verify add_to_assignees called twice
        assert mock_pull_request.add_to_assignees.call_count == 2

    @pytest.mark.asyncio
    async def test_process_opened_setup_task_failure(
        self, pull_request_handler: PullRequestHandler, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test setup task failure in process_opened_or_synchronize_pull_request."""
        mock_github_webhook.conventional_title = True

        pull_request_handler.owners_file_handler.assign_reviewers = AsyncMock(side_effect=Exception("Setup failed"))
        # Mock other methods to return coroutines
        with (
            patch.object(pull_request_handler.labels_handler, "_add_label", new=AsyncMock()),
            patch.object(pull_request_handler, "label_pull_request_by_merge_state", new=AsyncMock()),
            patch.object(pull_request_handler.check_run_handler, "set_merge_check_queued", new=AsyncMock()),
            patch.object(pull_request_handler.check_run_handler, "set_run_tox_check_queued", new=AsyncMock()),
            patch.object(pull_request_handler.check_run_handler, "set_run_pre_commit_check_queued", new=AsyncMock()),
            patch.object(pull_request_handler.check_run_handler, "set_python_module_install_queued", new=AsyncMock()),
            patch.object(pull_request_handler.check_run_handler, "set_container_build_queued", new=AsyncMock()),
            patch.object(pull_request_handler, "_process_verified_for_update_or_new_pull_request", new=AsyncMock()),
            patch.object(pull_request_handler.labels_handler, "add_size_label", new=AsyncMock()),
            patch.object(pull_request_handler, "add_pull_request_owner_as_assingee", new=AsyncMock()),
            patch.object(pull_request_handler.check_run_handler, "set_conventional_title_queued", new=AsyncMock()),
            patch.object(pull_request_handler.runner_handler, "run_tox", new=AsyncMock()),
            patch.object(pull_request_handler.runner_handler, "run_pre_commit", new=AsyncMock()),
            patch.object(pull_request_handler.runner_handler, "run_install_python_module", new=AsyncMock()),
            patch.object(pull_request_handler.runner_handler, "run_build_container", new=AsyncMock()),
            patch.object(pull_request_handler.runner_handler, "run_conventional_title_check", new=AsyncMock()),
        ):
            await pull_request_handler.process_opened_or_synchronize_pull_request(mock_pull_request)

            pull_request_handler.logger.error.assert_any_call("[TEST] Setup task failed: Setup failed")

    @pytest.mark.asyncio
    async def test_process_opened_ci_task_failure(
        self, pull_request_handler: PullRequestHandler, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test CI task failure in process_opened_or_synchronize_pull_request."""
        mock_github_webhook.conventional_title = False

        # Mock setup tasks to succeed
        pull_request_handler.owners_file_handler.assign_reviewers = AsyncMock()

        with (
            patch.object(pull_request_handler.labels_handler, "_add_label", new=AsyncMock()),
            patch.object(pull_request_handler, "label_pull_request_by_merge_state", new=AsyncMock()),
            patch.object(pull_request_handler.check_run_handler, "set_merge_check_queued", new=AsyncMock()),
            patch.object(pull_request_handler.check_run_handler, "set_run_tox_check_queued", new=AsyncMock()),
            patch.object(pull_request_handler.check_run_handler, "set_run_pre_commit_check_queued", new=AsyncMock()),
            patch.object(pull_request_handler.check_run_handler, "set_python_module_install_queued", new=AsyncMock()),
            patch.object(pull_request_handler.check_run_handler, "set_container_build_queued", new=AsyncMock()),
            patch.object(pull_request_handler, "_process_verified_for_update_or_new_pull_request", new=AsyncMock()),
            patch.object(pull_request_handler.labels_handler, "add_size_label", new=AsyncMock()),
            patch.object(pull_request_handler, "add_pull_request_owner_as_assingee", new=AsyncMock()),
            patch.object(
                pull_request_handler.runner_handler, "run_tox", new=AsyncMock(side_effect=Exception("CI failed"))
            ),
            patch.object(pull_request_handler.runner_handler, "run_pre_commit", new=AsyncMock()),
            patch.object(pull_request_handler.runner_handler, "run_install_python_module", new=AsyncMock()),
            patch.object(pull_request_handler.runner_handler, "run_build_container", new=AsyncMock()),
        ):
            await pull_request_handler.process_opened_or_synchronize_pull_request(mock_pull_request)

            pull_request_handler.logger.error.assert_any_call("[TEST] CI/CD task failed: CI failed")

    @pytest.mark.asyncio
    async def test_create_issue_for_new_pr_disabled(
        self, pull_request_handler: PullRequestHandler, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test create_issue_for_new_pull_request when disabled."""
        mock_github_webhook.create_issue_for_new_pr = False

        await pull_request_handler.create_issue_for_new_pull_request(mock_pull_request)

        pull_request_handler.logger.info.assert_called_with(
            "[TEST] Issue creation for new PRs is disabled for this repository"
        )

    @pytest.mark.asyncio
    async def test_create_issue_for_new_pr_auto_verified(
        self, pull_request_handler: PullRequestHandler, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test create_issue_for_new_pull_request for auto-verified user."""
        mock_github_webhook.create_issue_for_new_pr = True
        mock_github_webhook.parent_committer = "user"
        mock_github_webhook.auto_verified_and_merged_users = ["user"]

        await pull_request_handler.create_issue_for_new_pull_request(mock_pull_request)

        pull_request_handler.logger.info.assert_called_with(
            "[TEST] Committer user is part of ['user'], will not create issue."
        )

    @pytest.mark.asyncio
    async def test_set_pull_request_automerge_exception(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test exception in set_pull_request_automerge."""
        # Make enable_automerge raise exception
        mock_pull_request.enable_automerge.side_effect = Exception("Automerge failed")
        mock_pull_request.raw_data = {}

        pull_request_handler.github_webhook.set_auto_merge_prs = ["main"]
        mock_pull_request.base.ref = "main"

        with patch("asyncio.to_thread", new=_sync_to_thread):
            await pull_request_handler.set_pull_request_automerge(mock_pull_request)

        pull_request_handler.logger.error.assert_called_with(
            "[TEST] Exception while setting auto merge: Automerge failed"
        )

    @pytest.mark.asyncio
    async def test_label_pull_request_by_merge_state_unknown(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test label_pull_request_by_merge_state when unknown."""
        mock_pull_request.mergeable_state = "unknown"

        with patch("asyncio.to_thread", new=_sync_to_thread):
            await pull_request_handler.label_pull_request_by_merge_state(mock_pull_request)

        # Should return early
        pull_request_handler.labels_handler._add_label.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_registry_tag_via_regctl_failure(
        self, pull_request_handler: PullRequestHandler, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test failures in _delete_registry_tag_via_regctl."""
        mock_github_webhook.container_repository_username = "user"
        mock_github_webhook.container_repository_password = "pass"  # pragma: allowlist secret
        mock_github_webhook.container_repository = "registry.io/repo"

        # 1. Login failure
        pull_request_handler.runner_handler.run_podman_command = AsyncMock(
            return_value=(False, "Login failed", "Error")
        )

        await pull_request_handler._delete_registry_tag_via_regctl(mock_pull_request, "tag", "pr-123", "registry.io")
        pull_request_handler.logger.error.assert_called_with(
            "[TEST] Failed to delete tag: tag. OUT:Login failed. ERR:Error"
        )

        # 2. Tag delete failure
        pull_request_handler.runner_handler.run_podman_command = AsyncMock(
            side_effect=[
                (True, "Login success", ""),  # login
                (True, "pr-123", ""),  # tag ls
                (False, "Delete failed", "Error"),  # tag delete
                (True, "", ""),  # logout
            ]
        )

        await pull_request_handler._delete_registry_tag_via_regctl(mock_pull_request, "tag", "pr-123", "registry.io")
        pull_request_handler.logger.error.assert_called_with(
            "[TEST] Failed to delete tag: tag. OUT:Delete failed. ERR:Error"
        )

    @pytest.mark.asyncio
    async def test_label_pull_request_by_merge_state_only_labels(
        self, pull_request_handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test label_pull_request_by_merge_state only handles labeling, not check triggering."""
        mock_pull_request.mergeable_state = "behind"

        with (
            patch("asyncio.to_thread", new=_sync_to_thread),
            patch.object(pull_request_handler.labels_handler, "_add_label", new_callable=AsyncMock) as mock_add_label,
            patch.object(
                pull_request_handler, "_retrigger_check_suites_for_pr", new_callable=AsyncMock
            ) as mock_retrigger,
        ):
            await pull_request_handler.label_pull_request_by_merge_state(pull_request=mock_pull_request)

            # Verify labeling happened
            mock_add_label.assert_called_once_with(pull_request=mock_pull_request, label=NEEDS_REBASE_LABEL_STR)
            # Verify check triggering did NOT happen (separation of concerns)
            mock_retrigger.assert_not_called()

    @pytest.mark.asyncio
    async def test_retrigger_check_suites_for_pr_success(
        self, pull_request_handler: PullRequestHandler, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test _retrigger_check_suites_for_pr successfully runs configured checks."""
        mock_pull_request.number = 123
        mock_github_webhook.current_pull_request_supported_retest = [TOX_STR, PRE_COMMIT_STR]
        mock_github_webhook.retrigger_checks_on_base_push = "all"

        # Mock the run_retests_from_config method
        mock_run_retests_from_config = AsyncMock(return_value=True)
        pull_request_handler.runner_handler.run_retests_from_config = mock_run_retests_from_config

        with patch("asyncio.to_thread", new=_sync_to_thread):
            await pull_request_handler._retrigger_check_suites_for_pr(mock_pull_request)

            # Verify run_retests_from_config was called with the correct arguments
            mock_run_retests_from_config.assert_called_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_retrigger_check_suites_for_pr_no_check_suites(
        self, pull_request_handler: PullRequestHandler, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test _retrigger_check_suites_for_pr when repository has no configured checks."""
        mock_pull_request.number = 123
        mock_github_webhook.current_pull_request_supported_retest = []

        # Mock the run_retests_from_config method (returns False when no checks)
        mock_run_retests_from_config = AsyncMock(return_value=False)
        pull_request_handler.runner_handler.run_retests_from_config = mock_run_retests_from_config

        with patch("asyncio.to_thread", new=_sync_to_thread):
            await pull_request_handler._retrigger_check_suites_for_pr(mock_pull_request)

            # Verify run_retests_from_config was called
            mock_run_retests_from_config.assert_called_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_retrigger_check_suites_for_pr_exception(
        self, pull_request_handler: PullRequestHandler, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test _retrigger_check_suites_for_pr propagates exceptions from runners."""
        mock_pull_request.number = 123
        mock_github_webhook.current_pull_request_supported_retest = [TOX_STR]
        mock_github_webhook.retrigger_checks_on_base_push = "all"

        # Mock run_retests_from_config to raise exception
        mock_run_retests_from_config = AsyncMock(side_effect=Exception("Runner failed"))
        pull_request_handler.runner_handler.run_retests_from_config = mock_run_retests_from_config

        with patch("asyncio.to_thread", new=_sync_to_thread):
            # The exception should propagate since we're not catching it in _retrigger_check_suites_for_pr
            with pytest.raises(Exception, match="Runner failed"):
                await pull_request_handler._retrigger_check_suites_for_pr(mock_pull_request)

    @pytest.mark.asyncio
    async def test_retrigger_check_suites_for_pr_with_specific_checks_list(
        self, pull_request_handler: PullRequestHandler, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test _retrigger_check_suites_for_pr with specific checks list."""
        mock_pull_request.number = 123
        mock_github_webhook.current_pull_request_supported_retest = [TOX_STR, PRE_COMMIT_STR, "build-container"]
        mock_github_webhook.retrigger_checks_on_base_push = [TOX_STR, PRE_COMMIT_STR]

        # Mock the run_retests_from_config method
        mock_run_retests_from_config = AsyncMock(return_value=True)
        pull_request_handler.runner_handler.run_retests_from_config = mock_run_retests_from_config

        with patch("asyncio.to_thread", new=_sync_to_thread):
            await pull_request_handler._retrigger_check_suites_for_pr(mock_pull_request)

            # Verify run_retests_from_config was called
            mock_run_retests_from_config.assert_called_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_retrigger_check_suites_for_pr_with_nonexistent_checks(
        self, pull_request_handler: PullRequestHandler, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test _retrigger_check_suites_for_pr when configured checks don't exist."""
        mock_pull_request.number = 123
        mock_github_webhook.current_pull_request_supported_retest = [TOX_STR, PRE_COMMIT_STR]
        mock_github_webhook.retrigger_checks_on_base_push = ["nonexistent-check"]

        # Mock the run_retests_from_config method (returns False when no matching checks)
        mock_run_retests_from_config = AsyncMock(return_value=False)
        pull_request_handler.runner_handler.run_retests_from_config = mock_run_retests_from_config

        with patch("asyncio.to_thread", new=_sync_to_thread):
            await pull_request_handler._retrigger_check_suites_for_pr(mock_pull_request)

            # Verify run_retests_from_config was called
            mock_run_retests_from_config.assert_called_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_retrigger_check_suites_for_pr_with_partial_match(
        self, pull_request_handler: PullRequestHandler, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test _retrigger_check_suites_for_pr with partial match."""
        mock_pull_request.number = 123
        mock_github_webhook.current_pull_request_supported_retest = [TOX_STR, PRE_COMMIT_STR]
        mock_github_webhook.retrigger_checks_on_base_push = [TOX_STR, "nonexistent-check"]

        # Mock the run_retests_from_config method (returns True when checks match)
        mock_run_retests_from_config = AsyncMock(return_value=True)
        pull_request_handler.runner_handler.run_retests_from_config = mock_run_retests_from_config

        with patch("asyncio.to_thread", new=_sync_to_thread):
            await pull_request_handler._retrigger_check_suites_for_pr(mock_pull_request)

            # Verify run_retests_from_config was called
            mock_run_retests_from_config.assert_called_once_with(pull_request=mock_pull_request)
