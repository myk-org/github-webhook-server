"""Tests for clean rebase detection in PullRequestHandler.

Tests the _is_clean_rebase method and the modified synchronize handler
that preserves review labels on clean rebases.
"""

from __future__ import annotations

import asyncio
import hashlib
import shlex
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from webhook_server.libs.github_api import GithubWebhook
from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
from webhook_server.libs.handlers.pull_request_handler import PullRequestHandler
from webhook_server.tests.conftest import TEST_GITHUB_TOKEN
from webhook_server.utils.constants import (
    APPROVED_BY_LABEL_PREFIX,
    CHANGED_REQUESTED_BY_LABEL_PREFIX,
    COMMENTED_BY_LABEL_PREFIX,
    LGTM_BY_LABEL_PREFIX,
    VERIFIED_LABEL_STR,
)


@pytest.fixture
def mock_github_webhook() -> Mock:
    """Create a mock GithubWebhook instance."""
    mock_webhook = Mock(spec=GithubWebhook)
    mock_webhook.hook_data = {
        "action": "synchronize",
        "before": "aaa1111111111111111111111111111111111111",
        "after": "bbb2222222222222222222222222222222222222",
        "pull_request": {"number": 42, "merged": False, "title": "Test PR"},
        "sender": {"login": "test-user"},
        "label": {"name": "bug"},
    }
    mock_webhook.logger = MagicMock()
    mock_webhook.log_prefix = "[TEST]"
    mock_webhook.repository_full_name = "test-org/test-repo"
    mock_webhook.repository = Mock()
    mock_webhook.clone_repo_dir = "/tmp/test-clone-dir"
    mock_webhook.mask_sensitive = True
    mock_webhook.issue_url_for_welcome_msg = "welcome-message-url"
    mock_webhook.parent_committer = "test-user"
    mock_webhook.auto_verified_and_merged_users = ["test-user"]
    mock_webhook.create_issue_for_new_pr = True
    mock_webhook.verified_job = True
    mock_webhook.build_and_push_container = True
    mock_webhook.container_repository_and_tag = Mock(return_value="test-repo:pr-42")
    mock_webhook.can_be_merged_required_labels = []
    mock_webhook.set_auto_merge_prs = []
    mock_webhook.auto_merge_enabled = True
    mock_webhook.container_repository = "docker.io/org/repo"
    mock_webhook.conventional_title = False
    mock_webhook.minimum_lgtm = 1
    mock_webhook.container_repository_username = "test-user"
    mock_webhook.container_repository_password = "test-password"  # pragma: allowlist secret
    mock_webhook.github_api = Mock()
    mock_webhook.tox = True
    mock_webhook.pre_commit = True
    mock_webhook.python_module_install = False
    mock_webhook.pypi = False
    mock_webhook.token = TEST_GITHUB_TOKEN
    mock_webhook.auto_verify_cherry_picked_prs = True
    mock_webhook.cherry_pick_assign_to_pr_author = True
    mock_webhook.last_commit = Mock()
    mock_webhook.ctx = None
    mock_webhook.enabled_labels = None
    mock_webhook.custom_check_runs = []
    mock_webhook.ai_features = None
    mock_webhook.required_conversation_resolution = False
    mock_webhook.config = Mock()
    mock_webhook.config.get_value = Mock(return_value=None)
    mock_webhook._repo_cloned = True
    return mock_webhook


@pytest.fixture
def mock_owners_file_handler() -> Mock:
    """Create a mock OwnersFileHandler instance."""
    mock_handler = Mock(spec=OwnersFileHandler)
    mock_handler.all_pull_request_approvers = ["approver1", "approver2"]
    mock_handler.all_pull_request_reviewers = ["reviewer1", "reviewer2"]
    mock_handler.root_approvers = ["root-approver"]
    mock_handler.root_reviewers = ["root-reviewer"]
    mock_handler.assign_reviewers = AsyncMock()
    return mock_handler


@pytest.fixture
def handler(mock_github_webhook: Mock, mock_owners_file_handler: Mock) -> PullRequestHandler:
    """Create a PullRequestHandler instance with mocked dependencies."""
    handler = PullRequestHandler(mock_github_webhook, mock_owners_file_handler)

    handler.labels_handler = Mock()
    handler.labels_handler._add_label = AsyncMock()
    handler.labels_handler._remove_label = AsyncMock()
    handler.labels_handler.add_size_label = AsyncMock()
    handler.labels_handler.pull_request_labels_names = AsyncMock(return_value=[])
    handler.labels_handler.wip_or_hold_labels_exists = Mock(return_value=False)
    handler.labels_handler.is_label_enabled = Mock(return_value=True)

    handler.check_run_handler = Mock()
    handler.check_run_handler.set_check_queued = AsyncMock()
    handler.check_run_handler.set_check_in_progress = AsyncMock()
    handler.check_run_handler.set_check_success = AsyncMock()
    handler.check_run_handler.set_check_failure = AsyncMock()

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
def mock_pull_request() -> Mock:
    """Create a mock PullRequest instance."""
    mock_pr = MagicMock()
    mock_pr.number = 42
    mock_pr.title = "Test PR"
    mock_pr.body = "Test PR body"
    mock_pr.html_url = "https://github.com/test/repo/pull/42"
    mock_pr.labels = []
    mock_pr.create_issue_comment = Mock()
    mock_pr.edit = Mock()
    mock_pr.is_merged = Mock(return_value=False)
    mock_pr.base = Mock()
    mock_pr.base.ref = "main"
    mock_pr.head = Mock()
    mock_pr.head.ref = "feature-branch"
    mock_pr.head.user = Mock()
    mock_pr.head.user.login = "test-user"
    mock_pr.user = Mock()
    mock_pr.user.login = "owner1"
    mock_pr.mergeable = True
    mock_pr.mergeable_state = "clean"
    mock_pr.enable_automerge = Mock()
    mock_pr.add_to_assignees = Mock()
    mock_pr.get_issue_comments = Mock(return_value=[])
    mock_pr.raw_data = {}
    return mock_pr


class TestIsCleanRebase:
    """Test suite for _is_clean_rebase method."""

    @pytest.mark.asyncio
    async def test_clean_rebase_detected_when_diffs_match(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that _is_clean_rebase returns True when old and new diffs produce the same hash."""
        before_sha = handler.hook_data["before"]
        after_sha = handler.hook_data["after"]
        diff_content = "diff --git a/file.py b/file.py\n+hello\n"

        async def mock_run_command(command: str, log_prefix: str, **kwargs: Any) -> tuple[bool, str, str]:
            if f"fetch origin {before_sha}" in command:
                return (True, "", "")
            if "merge-base" in command and before_sha in command:
                return (True, "old_merge_base_sha\n", "")
            if "merge-base" in command and after_sha in command:
                return (True, "new_merge_base_sha\n", "")
            if "diff" in command and "old_merge_base_sha" in command:
                return (True, diff_content, "")
            if "diff" in command and "new_merge_base_sha" in command:
                return (True, diff_content, "")
            return (False, "", "unexpected command")

        with patch("webhook_server.libs.handlers.pull_request_handler.run_command", side_effect=mock_run_command):
            result = await handler._is_clean_rebase(mock_pull_request)

        assert result is True

    @pytest.mark.asyncio
    async def test_not_clean_rebase_when_diffs_differ(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that _is_clean_rebase returns False when old and new diffs produce different hashes."""
        before_sha = handler.hook_data["before"]
        after_sha = handler.hook_data["after"]

        async def mock_run_command(command: str, log_prefix: str, **kwargs: Any) -> tuple[bool, str, str]:
            if f"fetch origin {before_sha}" in command:
                return (True, "", "")
            if "merge-base" in command and before_sha in command:
                return (True, "old_merge_base_sha\n", "")
            if "merge-base" in command and after_sha in command:
                return (True, "new_merge_base_sha\n", "")
            if "diff" in command and "old_merge_base_sha" in command:
                return (True, "old diff content\n", "")
            if "diff" in command and "new_merge_base_sha" in command:
                return (True, "new diff content with changes\n", "")
            return (False, "", "unexpected command")

        with patch("webhook_server.libs.handlers.pull_request_handler.run_command", side_effect=mock_run_command):
            result = await handler._is_clean_rebase(mock_pull_request)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_fetch_fails(self, handler: PullRequestHandler, mock_pull_request: Mock) -> None:
        """Test that _is_clean_rebase returns False when git fetch of old SHA fails."""
        before_sha = handler.hook_data["before"]

        async def mock_run_command(command: str, log_prefix: str, **kwargs: Any) -> tuple[bool, str, str]:
            if f"fetch origin {before_sha}" in command:
                return (False, "", "error: could not fetch")
            return (True, "", "")

        with patch("webhook_server.libs.handlers.pull_request_handler.run_command", side_effect=mock_run_command):
            result = await handler._is_clean_rebase(mock_pull_request)

        assert result is False
        handler.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_returns_false_when_old_merge_base_fails(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that _is_clean_rebase returns False when merge-base for old SHA fails."""
        before_sha = handler.hook_data["before"]

        async def mock_run_command(command: str, log_prefix: str, **kwargs: Any) -> tuple[bool, str, str]:
            if f"fetch origin {before_sha}" in command:
                return (True, "", "")
            if "merge-base" in command and before_sha in command:
                return (False, "", "fatal: not a valid commit")
            return (True, "", "")

        with patch("webhook_server.libs.handlers.pull_request_handler.run_command", side_effect=mock_run_command):
            result = await handler._is_clean_rebase(mock_pull_request)

        assert result is False
        handler.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_returns_false_when_new_merge_base_fails(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that _is_clean_rebase returns False when merge-base for new SHA fails."""
        before_sha = handler.hook_data["before"]
        after_sha = handler.hook_data["after"]

        async def mock_run_command(command: str, log_prefix: str, **kwargs: Any) -> tuple[bool, str, str]:
            if f"fetch origin {before_sha}" in command:
                return (True, "", "")
            if "merge-base" in command and before_sha in command:
                return (True, "old_merge_base_sha\n", "")
            if "merge-base" in command and after_sha in command:
                return (False, "", "fatal: not a valid commit")
            return (True, "", "")

        with patch("webhook_server.libs.handlers.pull_request_handler.run_command", side_effect=mock_run_command):
            result = await handler._is_clean_rebase(mock_pull_request)

        assert result is False
        handler.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_returns_false_when_old_diff_fails(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that _is_clean_rebase returns False when git diff for old range fails."""
        before_sha = handler.hook_data["before"]
        after_sha = handler.hook_data["after"]

        async def mock_run_command(command: str, log_prefix: str, **kwargs: Any) -> tuple[bool, str, str]:
            if f"fetch origin {before_sha}" in command:
                return (True, "", "")
            if "merge-base" in command and before_sha in command:
                return (True, "old_merge_base_sha\n", "")
            if "merge-base" in command and after_sha in command:
                return (True, "new_merge_base_sha\n", "")
            if "diff" in command and "old_merge_base_sha" in command:
                return (False, "", "fatal: bad revision")
            return (True, "", "")

        with patch("webhook_server.libs.handlers.pull_request_handler.run_command", side_effect=mock_run_command):
            result = await handler._is_clean_rebase(mock_pull_request)

        assert result is False
        handler.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_returns_false_when_new_diff_fails(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that _is_clean_rebase returns False when git diff for new range fails."""
        before_sha = handler.hook_data["before"]
        after_sha = handler.hook_data["after"]

        async def mock_run_command(command: str, log_prefix: str, **kwargs: Any) -> tuple[bool, str, str]:
            if f"fetch origin {before_sha}" in command:
                return (True, "", "")
            if "merge-base" in command and before_sha in command:
                return (True, "old_merge_base_sha\n", "")
            if "merge-base" in command and after_sha in command:
                return (True, "new_merge_base_sha\n", "")
            if "diff" in command and "old_merge_base_sha" in command:
                return (True, "some diff\n", "")
            if "diff" in command and "new_merge_base_sha" in command:
                return (False, "", "fatal: bad revision")
            return (True, "", "")

        with patch("webhook_server.libs.handlers.pull_request_handler.run_command", side_effect=mock_run_command):
            result = await handler._is_clean_rebase(mock_pull_request)

        assert result is False
        handler.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_uses_correct_clone_dir(self, handler: PullRequestHandler, mock_pull_request: Mock) -> None:
        """Test that _is_clean_rebase uses the correct clone_repo_dir in git commands."""
        clone_dir = handler.github_webhook.clone_repo_dir
        commands_received: list[str] = []

        async def mock_run_command(command: str, log_prefix: str, **kwargs: Any) -> tuple[bool, str, str]:
            commands_received.append(command)
            if "fetch" in command:
                return (True, "", "")
            if "merge-base" in command:
                return (True, "merge_base_sha\n", "")
            if "diff" in command:
                return (True, "diff content\n", "")
            return (True, "", "")

        with patch("webhook_server.libs.handlers.pull_request_handler.run_command", side_effect=mock_run_command):
            await handler._is_clean_rebase(mock_pull_request)

        # All git commands should use -C with the clone dir (shlex-quoted)
        clone_dir_q = shlex.quote(clone_dir)
        for cmd in commands_received:
            assert f"git -C {clone_dir_q}" in cmd

    @pytest.mark.asyncio
    async def test_uses_correct_base_ref(self, handler: PullRequestHandler, mock_pull_request: Mock) -> None:
        """Test that _is_clean_rebase uses pull_request.base.ref for merge-base commands."""
        mock_pull_request.base.ref = "develop"
        commands_received: list[str] = []

        async def mock_run_command(command: str, log_prefix: str, **kwargs: Any) -> tuple[bool, str, str]:
            commands_received.append(command)
            if "fetch" in command:
                return (True, "", "")
            if "merge-base" in command:
                return (True, "merge_base_sha\n", "")
            if "diff" in command:
                return (True, "diff content\n", "")
            return (True, "", "")

        with patch("webhook_server.libs.handlers.pull_request_handler.run_command", side_effect=mock_run_command):
            await handler._is_clean_rebase(mock_pull_request)

        # merge-base commands should reference origin/{base_ref} (shlex-quoted)
        merge_base_cmds = [c for c in commands_received if "merge-base" in c]
        for cmd in merge_base_cmds:
            assert shlex.quote("origin/develop") in cmd

    @pytest.mark.asyncio
    async def test_hashes_diff_output_with_sha256(self, handler: PullRequestHandler, mock_pull_request: Mock) -> None:
        """Test that _is_clean_rebase hashes diff output using sha256."""
        before_sha = handler.hook_data["before"]
        after_sha = handler.hook_data["after"]
        diff_a = "diff --git a/file.py\n+line1\n"
        diff_b = "diff --git a/file.py\n+line1\n+line2\n"

        async def mock_run_command(command: str, log_prefix: str, **kwargs: Any) -> tuple[bool, str, str]:
            if "fetch origin" in command:
                return (True, "", "")
            if "merge-base" in command and before_sha in command:
                return (True, "old_merge_base\n", "")
            if "merge-base" in command and after_sha in command:
                return (True, "new_merge_base\n", "")
            if "diff" in command and "old_merge_base" in command:
                return (True, diff_a, "")
            if "diff" in command and "new_merge_base" in command:
                return (True, diff_b, "")
            return (False, "", "unexpected")

        with (
            patch("webhook_server.libs.handlers.pull_request_handler.run_command", side_effect=mock_run_command),
            patch(
                "webhook_server.libs.handlers.pull_request_handler.hashlib.sha256",
                wraps=hashlib.sha256,
            ) as mock_sha256,
        ):
            result = await handler._is_clean_rebase(mock_pull_request)

        assert result is False
        assert mock_sha256.call_count == 2

    @pytest.mark.asyncio
    async def test_returns_false_when_repo_not_cloned(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that _is_clean_rebase returns False early when repository is not cloned."""
        handler.github_webhook._repo_cloned = False

        with patch(
            "webhook_server.libs.handlers.pull_request_handler.run_command", new_callable=AsyncMock
        ) as mock_run_cmd:
            result = await handler._is_clean_rebase(mock_pull_request)

        assert result is False
        # run_command should never be called when repo is not cloned
        mock_run_cmd.assert_not_called()
        handler.logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_git_commands_use_shlex_quote(self, handler: PullRequestHandler, mock_pull_request: Mock) -> None:
        """Test that all git command arguments are properly quoted with shlex.quote()."""
        before_sha = handler.hook_data["before"]
        after_sha = handler.hook_data["after"]
        clone_dir = handler.github_webhook.clone_repo_dir
        commands_received: list[str] = []

        async def mock_run_command(command: str, log_prefix: str, **kwargs: Any) -> tuple[bool, str, str]:
            commands_received.append(command)
            if "fetch" in command:
                return (True, "", "")
            if "merge-base" in command:
                return (True, "merge_base_sha\n", "")
            if "diff" in command:
                return (True, "diff content\n", "")
            return (True, "", "")

        with patch("webhook_server.libs.handlers.pull_request_handler.run_command", side_effect=mock_run_command):
            await handler._is_clean_rebase(mock_pull_request)

        # Verify shlex.quote is used for all interpolated values
        clone_dir_q = shlex.quote(clone_dir)
        before_sha_q = shlex.quote(before_sha)
        after_sha_q = shlex.quote(after_sha)
        base_ref_q = shlex.quote("origin/main")

        # Check fetch command
        fetch_cmds = [c for c in commands_received if "fetch" in c]
        assert len(fetch_cmds) == 1
        assert f"git -C {clone_dir_q} fetch origin {before_sha_q}" == fetch_cmds[0]

        # Check merge-base commands use quoted base_ref
        merge_base_cmds = [c for c in commands_received if "merge-base" in c]
        assert len(merge_base_cmds) == 2
        assert f"git -C {clone_dir_q} merge-base {base_ref_q} {before_sha_q}" == merge_base_cmds[0]
        assert f"git -C {clone_dir_q} merge-base {base_ref_q} {after_sha_q}" == merge_base_cmds[1]

    @pytest.mark.asyncio
    async def test_returns_false_when_old_merge_base_stdout_empty(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that _is_clean_rebase returns False when old merge-base stdout is empty after strip."""
        before_sha = handler.hook_data["before"]
        after_sha = handler.hook_data["after"]

        async def mock_run_command(command: str, log_prefix: str, **kwargs: Any) -> tuple[bool, str, str]:
            if f"fetch origin {before_sha}" in command:
                return (True, "", "")
            if "merge-base" in command and before_sha in command:
                return (True, "  \n", "")  # empty after strip
            if "merge-base" in command and after_sha in command:
                return (True, "new_merge_base_sha\n", "")
            return (True, "", "")

        with patch("webhook_server.libs.handlers.pull_request_handler.run_command", side_effect=mock_run_command):
            result = await handler._is_clean_rebase(mock_pull_request)

        assert result is False
        handler.logger.warning.assert_called()
        assert "empty merge-base" in str(handler.logger.warning.call_args)

    @pytest.mark.asyncio
    async def test_returns_false_when_new_merge_base_stdout_empty(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that _is_clean_rebase returns False when new merge-base stdout is empty after strip."""
        before_sha = handler.hook_data["before"]
        after_sha = handler.hook_data["after"]

        async def mock_run_command(command: str, log_prefix: str, **kwargs: Any) -> tuple[bool, str, str]:
            if f"fetch origin {before_sha}" in command:
                return (True, "", "")
            if "merge-base" in command and before_sha in command:
                return (True, "old_merge_base_sha\n", "")
            if "merge-base" in command and after_sha in command:
                return (True, "\n", "")  # empty after strip
            return (True, "", "")

        with patch("webhook_server.libs.handlers.pull_request_handler.run_command", side_effect=mock_run_command):
            result = await handler._is_clean_rebase(mock_pull_request)

        assert result is False
        handler.logger.warning.assert_called()
        assert "empty merge-base" in str(handler.logger.warning.call_args)

    @pytest.mark.asyncio
    async def test_returns_false_on_unexpected_exception(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that _is_clean_rebase returns False and logs when an unexpected exception occurs."""

        async def mock_run_command(command: str, log_prefix: str, **kwargs: Any) -> tuple[bool, str, str]:
            raise RuntimeError("unexpected git failure")

        with patch("webhook_server.libs.handlers.pull_request_handler.run_command", side_effect=mock_run_command):
            result = await handler._is_clean_rebase(mock_pull_request)

        assert result is False
        handler.logger.exception.assert_called_once()
        assert "treating as non-clean" in str(handler.logger.exception.call_args)

    @pytest.mark.asyncio
    async def test_reraises_cancelled_error(self, handler: PullRequestHandler, mock_pull_request: Mock) -> None:
        """Test that _is_clean_rebase re-raises asyncio.CancelledError instead of catching it."""

        async def mock_run_command(command: str, log_prefix: str, **kwargs: Any) -> tuple[bool, str, str]:
            raise asyncio.CancelledError()

        with (
            patch("webhook_server.libs.handlers.pull_request_handler.run_command", side_effect=mock_run_command),
            pytest.raises(asyncio.CancelledError),
        ):
            await handler._is_clean_rebase(mock_pull_request)

    @pytest.mark.asyncio
    async def test_run_command_calls_include_timeout(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that all run_command calls include timeout=60 to prevent hanging."""
        timeouts_received: list[int | None] = []

        async def mock_run_command(command: str, log_prefix: str, **kwargs: Any) -> tuple[bool, str, str]:
            timeouts_received.append(kwargs.get("timeout"))
            if "fetch" in command:
                return (True, "", "")
            if "merge-base" in command:
                return (True, "merge_base_sha\n", "")
            if "diff" in command:
                return (True, "diff content\n", "")
            return (True, "", "")

        with patch("webhook_server.libs.handlers.pull_request_handler.run_command", side_effect=mock_run_command):
            await handler._is_clean_rebase(mock_pull_request)

        assert len(timeouts_received) == 5
        assert all(t == 60 for t in timeouts_received)

    @pytest.mark.asyncio
    async def test_diff_commands_use_binary_flag(self, handler: PullRequestHandler, mock_pull_request: Mock) -> None:
        """Test that git diff commands use --binary flag to prevent false positives with binary files."""
        before_sha = handler.hook_data["before"]
        after_sha = handler.hook_data["after"]
        clone_dir = handler.github_webhook.clone_repo_dir
        commands_received: list[str] = []

        async def mock_run_command(command: str, log_prefix: str, **kwargs: Any) -> tuple[bool, str, str]:
            commands_received.append(command)
            if "fetch" in command:
                return (True, "", "")
            if "merge-base" in command:
                return (True, "merge_base_sha\n", "")
            if "diff" in command:
                return (True, "diff content\n", "")
            return (True, "", "")

        with patch("webhook_server.libs.handlers.pull_request_handler.run_command", side_effect=mock_run_command):
            await handler._is_clean_rebase(mock_pull_request)

        clone_dir_q = shlex.quote(clone_dir)
        merge_base_q = shlex.quote("merge_base_sha")
        before_sha_q = shlex.quote(before_sha)
        after_sha_q = shlex.quote(after_sha)

        # Check diff commands include --binary flag
        diff_cmds = [c for c in commands_received if "diff" in c]
        assert len(diff_cmds) == 2
        assert f"git -C {clone_dir_q} diff --binary {merge_base_q}..{before_sha_q}" == diff_cmds[0]
        assert f"git -C {clone_dir_q} diff --binary {merge_base_q}..{after_sha_q}" == diff_cmds[1]


class TestSynchronizeWithCleanRebase:
    """Test suite for synchronize handler with clean rebase detection."""

    @pytest.mark.asyncio
    async def test_synchronize_clean_rebase_skips_label_removal(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that synchronize with clean rebase does NOT call remove_labels_when_pull_request_sync."""
        with (
            patch.object(handler, "_is_clean_rebase", new_callable=AsyncMock, return_value=True),
            patch.object(handler, "process_opened_or_synchronize_pull_request", new_callable=AsyncMock) as mock_process,
            patch.object(handler, "remove_labels_when_pull_request_sync", new_callable=AsyncMock) as mock_remove_labels,
        ):
            await handler.process_pull_request_webhook_data(mock_pull_request)

            mock_process.assert_called_once_with(pull_request=mock_pull_request, is_clean_rebase=True)
            mock_remove_labels.assert_not_called()

    @pytest.mark.asyncio
    async def test_synchronize_not_clean_rebase_removes_labels(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that synchronize without clean rebase calls both process and remove_labels."""
        with (
            patch.object(handler, "_is_clean_rebase", new_callable=AsyncMock, return_value=False),
            patch.object(handler, "process_opened_or_synchronize_pull_request", new_callable=AsyncMock) as mock_process,
            patch.object(handler, "remove_labels_when_pull_request_sync", new_callable=AsyncMock) as mock_remove_labels,
        ):
            await handler.process_pull_request_webhook_data(mock_pull_request)

            mock_process.assert_called_once_with(pull_request=mock_pull_request, is_clean_rebase=False)
            mock_remove_labels.assert_called_once_with(pull_request=mock_pull_request)

    @pytest.mark.asyncio
    async def test_synchronize_clean_rebase_posts_comment_with_preserved_labels(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that clean rebase posts a comment listing preserved review labels."""
        approved_name = f"{APPROVED_BY_LABEL_PREFIX}reviewer1"
        lgtm_name = f"{LGTM_BY_LABEL_PREFIX}reviewer2"
        handler.labels_handler.pull_request_labels_names = AsyncMock(return_value=[approved_name, lgtm_name, "bug"])

        before_sha = handler.hook_data["before"]

        with (
            patch.object(handler, "_is_clean_rebase", new_callable=AsyncMock, return_value=True),
            patch.object(handler, "process_opened_or_synchronize_pull_request", new_callable=AsyncMock),
        ):
            await handler.process_pull_request_webhook_data(mock_pull_request)

            # create_issue_comment is called via asyncio.to_thread which executes it
            mock_pull_request.create_issue_comment.assert_called_once()
            comment_body = mock_pull_request.create_issue_comment.call_args.kwargs["body"]
            assert "Clean rebase detected" in comment_body
            assert before_sha[:7] in comment_body
            assert f"`{approved_name}`" in comment_body
            assert f"`{lgtm_name}`" in comment_body
            assert "bug" not in comment_body

    @pytest.mark.asyncio
    async def test_synchronize_clean_rebase_no_review_labels_simple_comment(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that clean rebase with no review labels posts a simple comment."""
        handler.labels_handler.pull_request_labels_names = AsyncMock(return_value=["bug"])

        before_sha = handler.hook_data["before"]

        with (
            patch.object(handler, "_is_clean_rebase", new_callable=AsyncMock, return_value=True),
            patch.object(handler, "process_opened_or_synchronize_pull_request", new_callable=AsyncMock),
        ):
            await handler.process_pull_request_webhook_data(mock_pull_request)

            mock_pull_request.create_issue_comment.assert_called_once()
            comment_body = mock_pull_request.create_issue_comment.call_args.kwargs["body"]
            assert "Clean rebase detected" in comment_body
            assert before_sha[:7] in comment_body
            assert "preserved" not in comment_body

    @pytest.mark.asyncio
    async def test_synchronize_clean_rebase_preserves_verified_label(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that clean rebase recognizes verified label as a review label to preserve."""
        handler.labels_handler.pull_request_labels_names = AsyncMock(return_value=[VERIFIED_LABEL_STR])

        with (
            patch.object(handler, "_is_clean_rebase", new_callable=AsyncMock, return_value=True),
            patch.object(handler, "process_opened_or_synchronize_pull_request", new_callable=AsyncMock),
        ):
            await handler.process_pull_request_webhook_data(mock_pull_request)

            mock_pull_request.create_issue_comment.assert_called_once()
            comment_body = mock_pull_request.create_issue_comment.call_args.kwargs["body"]
            assert f"`{VERIFIED_LABEL_STR}`" in comment_body

    @pytest.mark.asyncio
    async def test_synchronize_clean_rebase_preserves_changes_requested_label(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that clean rebase recognizes changes-requested label as a review label."""
        cr_name = f"{CHANGED_REQUESTED_BY_LABEL_PREFIX}reviewer1"
        handler.labels_handler.pull_request_labels_names = AsyncMock(return_value=[cr_name])

        with (
            patch.object(handler, "_is_clean_rebase", new_callable=AsyncMock, return_value=True),
            patch.object(handler, "process_opened_or_synchronize_pull_request", new_callable=AsyncMock),
        ):
            await handler.process_pull_request_webhook_data(mock_pull_request)

            mock_pull_request.create_issue_comment.assert_called_once()
            comment_body = mock_pull_request.create_issue_comment.call_args.kwargs["body"]
            assert f"`{cr_name}`" in comment_body

    @pytest.mark.asyncio
    async def test_synchronize_clean_rebase_preserves_commented_by_label(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that clean rebase recognizes commented-by label as a review label."""
        commented_name = f"{COMMENTED_BY_LABEL_PREFIX}reviewer1"
        handler.labels_handler.pull_request_labels_names = AsyncMock(return_value=[commented_name])

        with (
            patch.object(handler, "_is_clean_rebase", new_callable=AsyncMock, return_value=True),
            patch.object(handler, "process_opened_or_synchronize_pull_request", new_callable=AsyncMock),
        ):
            await handler.process_pull_request_webhook_data(mock_pull_request)

            mock_pull_request.create_issue_comment.assert_called_once()
            comment_body = mock_pull_request.create_issue_comment.call_args.kwargs["body"]
            assert f"`{commented_name}`" in comment_body

    @pytest.mark.asyncio
    async def test_synchronize_clean_rebase_skips_verified_label_processing(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that clean rebase path skips _process_verified_for_update_or_new_pull_request.

        This is the MOST IMPORTANT fix: on clean rebase, verified label should NOT be removed
        for non-auto-verified users. The is_clean_rebase flag passed to
        process_opened_or_synchronize_pull_request should cause it to skip the verified
        label processing.
        """
        # Set parent_committer to a NON-auto-verified user
        handler.github_webhook.parent_committer = "external-contributor"
        handler.github_webhook.auto_verified_and_merged_users = ["auto-user"]

        handler.labels_handler.pull_request_labels_names = AsyncMock(return_value=[VERIFIED_LABEL_STR])

        with patch.object(handler, "_is_clean_rebase", new_callable=AsyncMock, return_value=True):
            await handler.process_pull_request_webhook_data(mock_pull_request)

        # Verified label should NOT have been removed (no _remove_label call for verified)
        remove_label_calls = handler.labels_handler._remove_label.call_args_list
        verified_removed = any(call.kwargs.get("label") == VERIFIED_LABEL_STR for call in remove_label_calls)
        assert not verified_removed, (
            "Verified label was removed during clean rebase - "
            "_process_verified_for_update_or_new_pull_request should be skipped"
        )

    @pytest.mark.asyncio
    async def test_synchronize_clean_rebase_handles_task_failure_gracefully(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that clean rebase path handles task failures via gather's return_exceptions.

        When the clean rebase path runs comment posting and process in parallel via gather,
        an exception in one task should be logged but not crash the other.
        """
        handler.labels_handler.pull_request_labels_names = AsyncMock(return_value=[])

        with (
            patch.object(handler, "_is_clean_rebase", new_callable=AsyncMock, return_value=True),
            patch.object(
                handler,
                "process_opened_or_synchronize_pull_request",
                new_callable=AsyncMock,
                side_effect=RuntimeError("test error"),
            ),
        ):
            # Should not raise even though process_opened_or_synchronize_pull_request fails
            await handler.process_pull_request_webhook_data(mock_pull_request)

            # Error should be logged
            handler.logger.error.assert_called()
            error_msg = str(handler.logger.error.call_args)
            assert "test error" in error_msg


class TestPostCleanRebaseComment:
    """Test suite for _post_clean_rebase_comment method."""

    @pytest.mark.asyncio
    async def test_posts_comment_with_review_labels(self, handler: PullRequestHandler, mock_pull_request: Mock) -> None:
        """Test that _post_clean_rebase_comment posts a comment listing preserved review labels."""
        approved_name = f"{APPROVED_BY_LABEL_PREFIX}reviewer1"
        lgtm_name = f"{LGTM_BY_LABEL_PREFIX}reviewer2"
        handler.labels_handler.pull_request_labels_names = AsyncMock(return_value=[approved_name, lgtm_name, "bug"])

        before_sha = "abc1234567890"  # pragma: allowlist secret
        await handler._post_clean_rebase_comment(pull_request=mock_pull_request, before_sha=before_sha)

        mock_pull_request.create_issue_comment.assert_called_once()
        comment_body = mock_pull_request.create_issue_comment.call_args.kwargs["body"]
        assert "Clean rebase detected" in comment_body
        assert before_sha[:7] in comment_body
        assert f"`{approved_name}`" in comment_body
        assert f"`{lgtm_name}`" in comment_body
        assert "bug" not in comment_body

    @pytest.mark.asyncio
    async def test_posts_simple_comment_without_review_labels(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that _post_clean_rebase_comment posts a simple comment when no review labels exist."""
        handler.labels_handler.pull_request_labels_names = AsyncMock(return_value=["bug"])

        before_sha = "abc1234567890"  # pragma: allowlist secret
        await handler._post_clean_rebase_comment(pull_request=mock_pull_request, before_sha=before_sha)

        mock_pull_request.create_issue_comment.assert_called_once()
        comment_body = mock_pull_request.create_issue_comment.call_args.kwargs["body"]
        assert "Clean rebase detected" in comment_body
        assert "preserved" not in comment_body

    @pytest.mark.asyncio
    async def test_logs_and_does_not_raise_on_label_fetch_failure(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that _post_clean_rebase_comment logs the error and does not raise when labels fetch fails."""
        handler.labels_handler.pull_request_labels_names = AsyncMock(side_effect=RuntimeError("API error"))

        before_sha = "abc1234567890"  # pragma: allowlist secret
        # Should not raise
        await handler._post_clean_rebase_comment(pull_request=mock_pull_request, before_sha=before_sha)

        handler.logger.exception.assert_called_once()
        assert "Failed to post clean-rebase comment" in str(handler.logger.exception.call_args)

    @pytest.mark.asyncio
    async def test_logs_and_does_not_raise_on_comment_post_failure(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that _post_clean_rebase_comment logs the error when comment posting fails."""
        handler.labels_handler.pull_request_labels_names = AsyncMock(return_value=[])
        mock_pull_request.create_issue_comment = Mock(side_effect=RuntimeError("API error"))

        before_sha = "abc1234567890"  # pragma: allowlist secret
        # Should not raise
        await handler._post_clean_rebase_comment(pull_request=mock_pull_request, before_sha=before_sha)

        handler.logger.exception.assert_called_once()
        assert "Failed to post clean-rebase comment" in str(handler.logger.exception.call_args)

    @pytest.mark.asyncio
    async def test_reraises_cancelled_error(self, handler: PullRequestHandler, mock_pull_request: Mock) -> None:
        """Test that _post_clean_rebase_comment re-raises asyncio.CancelledError."""
        handler.labels_handler.pull_request_labels_names = AsyncMock(side_effect=asyncio.CancelledError())

        before_sha = "abc1234567890"  # pragma: allowlist secret
        with pytest.raises(asyncio.CancelledError):
            await handler._post_clean_rebase_comment(pull_request=mock_pull_request, before_sha=before_sha)

    @pytest.mark.asyncio
    async def test_synchronize_continues_when_comment_fails(
        self, handler: PullRequestHandler, mock_pull_request: Mock
    ) -> None:
        """Test that synchronize handler's process_opened_or_synchronize runs even if comment fails.

        This verifies that _post_clean_rebase_comment failure does not block CI processing.
        """
        handler.labels_handler.pull_request_labels_names = AsyncMock(return_value=[])
        mock_pull_request.create_issue_comment = Mock(side_effect=RuntimeError("API error"))

        with (
            patch.object(handler, "_is_clean_rebase", new_callable=AsyncMock, return_value=True),
            patch.object(handler, "process_opened_or_synchronize_pull_request", new_callable=AsyncMock) as mock_process,
        ):
            await handler.process_pull_request_webhook_data(mock_pull_request)

            # process_opened_or_synchronize_pull_request should still have been called
            mock_process.assert_called_once_with(pull_request=mock_pull_request, is_clean_rebase=True)
