from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock, patch

import pytest
from github.GithubException import GithubException

from webhook_server.libs.handlers.runner_handler import RunnerHandler


@contextmanager
def patch_run_command(return_value=(True, "", "")):
    """Context manager to patch run_command with a return value.

    This reduces repetition in tests that need to patch run_command.

    Args:
        return_value: Tuple of (success, stdout, stderr) to return

    Yields:
        Mock object for run_command
    """

    with patch(
        "webhook_server.libs.handlers.runner_handler.run_command",
        new=AsyncMock(return_value=return_value),
    ) as mock_run_command:
        yield mock_run_command


class TestRunnerHandler:
    """Test suite for RunnerHandler class."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance."""
        mock_webhook = Mock()
        mock_webhook.hook_data = {"action": "opened"}
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.repository.full_name = "test-owner/test-repo"
        mock_webhook.repository.clone_url = "https://github.com/test/repo.git"
        mock_webhook.repository.owner.login = "test-owner"
        mock_webhook.repository.owner.email = "test@example.com"
        mock_webhook.token = "test-token"  # pragma: allowlist secret  # noqa: S105
        mock_webhook.clone_repo_dir = None  # Will be set by fixture using tmp_path
        mock_webhook.tox = {"main": "all"}
        mock_webhook.tox_python_version = "3.12"
        mock_webhook.pre_commit = True
        mock_webhook.build_and_push_container = True
        mock_webhook.pypi = {"token": "dummy"}
        mock_webhook.conventional_title = "ci,docs,feat,fix,refactor,test,release,CherryPicked,perf,chore"
        mock_webhook.container_repository_username = "test-user"
        mock_webhook.container_repository_password = (
            "test-pass"  # pragma: allowlist secret  # noqa: S105  # gitleaks:allow
        )
        mock_webhook.slack_webhook_url = "https://hooks.slack.com/test"
        mock_webhook.repository_full_name = "test/repo"
        mock_webhook.dockerfile = "Dockerfile"
        mock_webhook.container_build_args = []
        mock_webhook.container_command_args = []
        mock_webhook.last_commit = Mock()
        mock_webhook.last_commit.sha = "abc123def456"  # pragma: allowlist secret
        mock_webhook.repository_by_github_app = Mock()
        # Add unified_api mock as AsyncMock for all async methods
        mock_webhook.unified_api = AsyncMock()
        return mock_webhook

    @pytest.fixture
    def mock_owners_file_handler(self) -> Mock:
        """Create a mock OwnersFileHandler instance."""
        mock_handler = Mock()
        mock_handler.is_user_valid_to_run_commands = AsyncMock(return_value=True)
        return mock_handler

    @pytest.fixture
    def runner_handler(self, mock_github_webhook: Mock, mock_owners_file_handler: Mock, tmp_path) -> RunnerHandler:
        """Create a RunnerHandler instance with mocked dependencies."""
        # Use tmp_path fixture instead of hardcoded /tmp/test-repo
        mock_github_webhook.clone_repo_dir = str(tmp_path / "test-repo")
        return RunnerHandler(mock_github_webhook, mock_owners_file_handler)

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        """Create a mock PullRequest instance."""
        mock_pr = Mock()
        mock_pr.number = 123
        mock_pr.title = "feat: Test PR"
        mock_pr.base.ref = "main"
        mock_pr.head.ref = "feature-branch"
        mock_pr.merge_commit_sha = "abc123"
        mock_pr.html_url = "https://github.com/test/repo/pull/123"
        mock_pr.create_issue_comment = Mock()
        return mock_pr

    @pytest.fixture(autouse=True)
    def patch_check_run_text(self) -> Generator[None, None, None]:
        with patch(
            "webhook_server.libs.handlers.check_run_handler.CheckRunHandler.get_check_run_text",
            return_value="dummy output",
        ):
            yield

    @pytest.fixture(autouse=True)
    def patch_shutil_rmtree(self) -> Generator[None, None, None]:
        with patch("shutil.rmtree"):
            yield

    def test_is_podman_bug_true(self, runner_handler: RunnerHandler) -> None:
        """Test is_podman_bug returns True for podman bug error."""
        err = "Error: current system boot ID differs from cached boot ID; an unhandled reboot has occurred"
        assert runner_handler.is_podman_bug(err) is True

    def test_is_podman_bug_false(self, runner_handler: RunnerHandler) -> None:
        """Test is_podman_bug returns False for other errors."""
        err = "Some other error message"
        assert runner_handler.is_podman_bug(err) is False

    @patch("shutil.rmtree")
    @patch("os.path.realpath")
    @patch("os.path.islink")
    @patch("os.path.exists")
    @patch("os.getuid")
    def test_fix_podman_bug(
        self,
        mock_getuid: Mock,
        mock_exists: Mock,
        mock_islink: Mock,
        mock_realpath: Mock,
        mock_rmtree: Mock,
        runner_handler: RunnerHandler,
    ) -> None:
        """Test fix_podman_bug removes podman cache directories with dynamic UID."""
        # Mock UID to 1000 for consistent test
        mock_getuid.return_value = 1000
        # Both paths exist and are not symlinks
        mock_exists.return_value = True
        mock_islink.return_value = False
        # Paths resolve to /tmp (safe to remove)
        mock_realpath.side_effect = lambda x: x  # Return path unchanged (already under /tmp)

        runner_handler.fix_podman_bug()

        # Verify rmtree called twice (once for each path)
        assert mock_rmtree.call_count == 2
        mock_rmtree.assert_any_call("/tmp/storage-run-1000/containers", ignore_errors=True)
        mock_rmtree.assert_any_call("/tmp/storage-run-1000/libpod/tmp", ignore_errors=True)

    @patch("shutil.rmtree")
    @patch("os.path.realpath")
    @patch("os.path.islink")
    @patch("os.path.exists")
    @patch("os.getuid")
    def test_fix_podman_bug_skips_symlinks(
        self,
        mock_getuid: Mock,
        mock_exists: Mock,
        mock_islink: Mock,
        mock_realpath: Mock,
        mock_rmtree: Mock,
        runner_handler: RunnerHandler,
    ) -> None:
        """Test fix_podman_bug skips symlink paths for security."""
        mock_getuid.return_value = 1000
        mock_exists.return_value = True
        # First path is a symlink, second is not
        mock_islink.side_effect = [True, False]
        mock_realpath.side_effect = lambda x: x

        runner_handler.fix_podman_bug()

        # Only one rmtree call (second path, first skipped due to symlink)
        assert mock_rmtree.call_count == 1
        mock_rmtree.assert_called_once_with("/tmp/storage-run-1000/libpod/tmp", ignore_errors=True)

    @patch("shutil.rmtree")
    @patch("os.path.realpath")
    @patch("os.path.islink")
    @patch("os.path.exists")
    @patch("os.getuid")
    def test_fix_podman_bug_skips_unsafe_paths(
        self,
        mock_getuid: Mock,
        mock_exists: Mock,
        mock_islink: Mock,
        mock_realpath: Mock,
        mock_rmtree: Mock,
        runner_handler: RunnerHandler,
    ) -> None:
        """Test fix_podman_bug skips paths outside /tmp for security."""
        mock_getuid.return_value = 1000
        mock_exists.return_value = True
        mock_islink.return_value = False
        # First path resolves outside /tmp (unsafe), second is safe
        mock_realpath.side_effect = ["/home/user/storage-run-1000/containers", "/tmp/storage-run-1000/libpod/tmp"]

        runner_handler.fix_podman_bug()

        # Only one rmtree call (second path, first skipped due to unsafe location)
        assert mock_rmtree.call_count == 1
        mock_rmtree.assert_called_once_with("/tmp/storage-run-1000/libpod/tmp", ignore_errors=True)

    @pytest.mark.asyncio
    async def test_run_podman_command_success(self, runner_handler: RunnerHandler) -> None:
        """Test run_podman_command with successful command."""
        with patch(
            "webhook_server.libs.handlers.runner_handler.run_command", new=AsyncMock(return_value=(True, "success", ""))
        ):
            rc, out, _ = await runner_handler.run_podman_command("podman build .")
            assert rc is True
            assert "success" in out  # Relaxed assertion

    @pytest.mark.asyncio
    async def test_run_podman_command_podman_bug(self, runner_handler: RunnerHandler) -> None:
        """Test run_podman_command with podman bug error and retry."""
        podman_bug_err = "Error: current system boot ID differs from cached boot ID; an unhandled reboot has occurred"
        with patch("webhook_server.libs.handlers.runner_handler.run_command", new=AsyncMock()) as mock_run:
            mock_run.side_effect = [(False, "output", podman_bug_err), (True, "success after fix", "")]
            with patch.object(runner_handler, "fix_podman_bug") as mock_fix:
                rc, out, _ = await runner_handler.run_podman_command("podman build .")
                # Verify fix_podman_bug was called
                assert mock_fix.call_count >= 1
                # Verify retry succeeded
                assert rc is True
                assert "success after fix" in out

    @pytest.mark.asyncio
    async def test_run_podman_command_other_error(self, runner_handler: RunnerHandler) -> None:
        """Test run_podman_command with other error."""
        with patch(
            "webhook_server.libs.handlers.runner_handler.run_command",
            new=AsyncMock(return_value=(False, "output", "other error")),
        ):
            with patch.object(runner_handler, "fix_podman_bug") as mock_fix:
                rc, _, _ = await runner_handler.run_podman_command("podman build .")
                assert rc is False
                # Verify fix_podman_bug was NOT called for non-podman errors
                mock_fix.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_tox_disabled(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test run_tox when tox is disabled."""
        runner_handler.github_webhook.tox = {}
        await runner_handler.run_tox(mock_pull_request)
        # Should return early without doing anything

    @pytest.mark.asyncio
    async def test_run_tox_check_in_progress(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test run_tox when check is in progress."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(
            runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=True)
        ):
            with patch.object(runner_handler.check_run_handler, "set_run_tox_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
                    # Simple mock that returns the expected tuple
                    mock_prepare.return_value = AsyncMock()
                    mock_prepare.return_value.__aenter__ = AsyncMock(return_value=(True, "", ""))
                    mock_prepare.return_value.__aexit__ = AsyncMock(return_value=None)
                    # Use helper context manager instead of repeated patch
                    with patch_run_command(return_value=(True, "success", "")):
                        await runner_handler.run_tox(mock_pull_request)
                        mock_set_progress.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_tox_prepare_failure(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test run_tox when repository preparation fails."""
        runner_handler.github_webhook.pypi = {"token": ""}
        runner_handler.github_webhook.last_commit = Mock(get_check_runs=Mock(return_value=[]))
        with patch.object(
            runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
        ):
            with patch.object(runner_handler.check_run_handler, "set_run_tox_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_run_tox_check_failure") as mock_set_failure:
                    with patch.object(runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
                        mock_prepare.return_value = AsyncMock()
                        mock_prepare.return_value.__aenter__ = AsyncMock(return_value=(False, "out", "err"))
                        mock_prepare.return_value.__aexit__ = AsyncMock(return_value=None)
                        await runner_handler.run_tox(mock_pull_request)
                        mock_set_progress.assert_called_once()
                        mock_set_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_tox_success(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test run_tox with successful execution."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(
            runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
        ):
            with patch.object(runner_handler.check_run_handler, "set_run_tox_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_run_tox_check_success") as mock_set_success:
                    with patch.object(runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
                        mock_prepare.return_value = AsyncMock()
                        mock_prepare.return_value.__aenter__ = AsyncMock(return_value=(True, "", ""))
                        mock_prepare.return_value.__aexit__ = AsyncMock(return_value=None)
                        # Use helper context manager instead of repeated patch
                        with patch_run_command(return_value=(True, "success", "")):
                            await runner_handler.run_tox(mock_pull_request)
                            mock_set_progress.assert_called_once()
                            mock_set_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_tox_failure(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test run_tox with failed execution."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(
            runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
        ):
            with patch.object(runner_handler.check_run_handler, "set_run_tox_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_run_tox_check_failure") as mock_set_failure:
                    with patch.object(runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
                        mock_prepare.return_value = AsyncMock()
                        mock_prepare.return_value.__aenter__ = AsyncMock(return_value=(True, "", ""))
                        mock_prepare.return_value.__aexit__ = AsyncMock(return_value=None)
                        # Use helper context manager instead of repeated patch
                        with patch_run_command(return_value=(False, "output", "error")):
                            await runner_handler.run_tox(mock_pull_request)
                            mock_set_progress.assert_called_once()
                            mock_set_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_pre_commit_disabled(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test run_pre_commit when pre_commit is disabled."""
        runner_handler.github_webhook.pre_commit = False
        await runner_handler.run_pre_commit(mock_pull_request)
        # Should return early without doing anything

    @pytest.mark.asyncio
    async def test_run_pre_commit_success(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test run_pre_commit with successful execution."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(
            runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
        ):
            with patch.object(
                runner_handler.check_run_handler, "set_run_pre_commit_check_in_progress"
            ) as mock_set_progress:
                with patch.object(
                    runner_handler.check_run_handler, "set_run_pre_commit_check_success"
                ) as mock_set_success:
                    with patch.object(runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
                        mock_prepare.return_value = AsyncMock()
                        mock_prepare.return_value.__aenter__ = AsyncMock(return_value=(True, "", ""))
                        mock_prepare.return_value.__aexit__ = AsyncMock(return_value=None)
                        with patch(
                            "webhook_server.libs.handlers.runner_handler.run_command",
                            new=AsyncMock(return_value=(True, "success", "")),
                        ):
                            await runner_handler.run_pre_commit(mock_pull_request)
                            mock_set_progress.assert_called_once()
                            mock_set_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_build_container_disabled(self, runner_handler: RunnerHandler) -> None:
        """Test run_build_container when build_and_push_container is disabled."""
        runner_handler.github_webhook.build_and_push_container = False
        await runner_handler.run_build_container()
        # Should return early without doing anything

    @pytest.mark.asyncio
    async def test_run_build_container_unauthorized_user(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test run_build_container with unauthorized user."""
        with patch.object(
            runner_handler.owners_file_handler, "is_user_valid_to_run_commands", new=AsyncMock(return_value=False)
        ):
            await runner_handler.run_build_container(pull_request=mock_pull_request, reviewed_user="unauthorized")
            # Should return early without doing anything

    @pytest.mark.asyncio
    async def test_run_build_container_success(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test run_build_container with successful build."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(
            runner_handler.github_webhook, "container_repository_and_tag", return_value="test/repo:latest"
        ):
            with patch.object(
                runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
            ):
                with patch.object(
                    runner_handler.check_run_handler, "set_container_build_in_progress"
                ) as mock_set_progress:
                    with patch.object(
                        runner_handler.check_run_handler, "set_container_build_success"
                    ) as mock_set_success:
                        with patch.object(runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
                            mock_prepare.return_value = AsyncMock()
                            mock_prepare.return_value.__aenter__ = AsyncMock(return_value=(True, "", ""))
                            mock_prepare.return_value.__aexit__ = AsyncMock(return_value=None)
                            with patch.object(
                                runner_handler, "run_podman_command", new=AsyncMock(return_value=(True, "success", ""))
                            ):
                                await runner_handler.run_build_container(pull_request=mock_pull_request)
                                mock_set_progress.assert_called_once()
                                mock_set_success.assert_called_once()

    @pytest.mark.asyncio
    @patch("webhook_server.libs.handlers.runner_handler.send_slack_message")
    async def test_run_build_container_with_push_success(
        self, mock_slack: Mock, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test run_build_container with successful build and push."""
        mock_slack.return_value = True
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(
            runner_handler.github_webhook, "container_repository_and_tag", return_value="test/repo:latest"
        ):
            with patch.object(
                runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
            ):
                with patch.object(
                    runner_handler.check_run_handler, "set_container_build_in_progress"
                ) as mock_set_progress:
                    with patch.object(
                        runner_handler.check_run_handler, "set_container_build_success"
                    ) as mock_set_success:
                        with patch.object(runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
                            mock_prepare.return_value = AsyncMock()
                            mock_prepare.return_value.__aenter__ = AsyncMock(return_value=(True, "", ""))
                            mock_prepare.return_value.__aexit__ = AsyncMock(return_value=None)
                            # Mock run_command for podman login
                            with patch(
                                "webhook_server.libs.handlers.runner_handler.run_command",
                                new=AsyncMock(return_value=(True, "Login Succeeded", "")),
                            ):
                                with patch.object(
                                    runner_handler,
                                    "run_podman_command",
                                    new=AsyncMock(return_value=(True, "success", "")),
                                ) as mock_run_podman:
                                    # Mock unified_api add_comment method
                                    # The code now uses pull_request.id directly via _get_pr_node_id()
                                    runner_handler.github_webhook.unified_api.add_comment = AsyncMock()
                                    await runner_handler.run_build_container(pull_request=mock_pull_request, push=True)
                                    mock_set_progress.assert_called_once()
                                    # When push=True, set_container_build_success should NOT be called after build
                                    # (it would be called after successful push instead, which is not part of this test)
                                    mock_set_success.assert_not_called()
                                    # Verify both build and push commands were executed
                                    assert mock_run_podman.call_count == 2
                                    # Verify success comment was posted using pull_request.id directly
                                    runner_handler.github_webhook.unified_api.add_comment.assert_called_once()
                                    call_args = runner_handler.github_webhook.unified_api.add_comment.call_args
                                    assert (
                                        call_args[0][0] == mock_pull_request.id
                                    )  # PR node ID from pull_request object
                                    assert "New container for test/repo:latest published" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_run_install_python_module_disabled(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test run_install_python_module when pypi is disabled."""
        # Set pypi to empty dict to trigger early return
        runner_handler.github_webhook.pypi = {}
        runner_handler.github_webhook.last_commit = Mock(get_check_runs=Mock(return_value=[]))
        with patch.object(
            runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
        ):
            await runner_handler.run_install_python_module(mock_pull_request)
            # Should return early without doing anything

    @pytest.mark.asyncio
    async def test_run_install_python_module_success(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test run_install_python_module with successful installation."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(
            runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
        ):
            with patch.object(
                runner_handler.check_run_handler, "set_python_module_install_in_progress"
            ) as mock_set_progress:
                with patch.object(
                    runner_handler.check_run_handler, "set_python_module_install_success"
                ) as mock_set_success:
                    with patch.object(runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
                        mock_prepare.return_value = AsyncMock()
                        mock_prepare.return_value.__aenter__ = AsyncMock(return_value=(True, "", ""))
                        mock_prepare.return_value.__aexit__ = AsyncMock(return_value=None)
                        with patch(
                            "webhook_server.libs.handlers.runner_handler.run_command",
                            new=AsyncMock(return_value=(True, "success", "")),
                        ):
                            await runner_handler.run_install_python_module(mock_pull_request)
                            mock_set_progress.assert_called_once()
                            mock_set_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_install_python_module_failure(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test run_install_python_module with failed installation."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(
            runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
        ):
            with patch.object(
                runner_handler.check_run_handler, "set_python_module_install_in_progress"
            ) as mock_set_progress:
                with patch.object(
                    runner_handler.check_run_handler, "set_python_module_install_failure"
                ) as mock_set_failure:
                    with patch.object(runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
                        mock_prepare.return_value = AsyncMock()
                        mock_prepare.return_value.__aenter__ = AsyncMock(return_value=(True, "", ""))
                        mock_prepare.return_value.__aexit__ = AsyncMock(return_value=None)
                        with patch(
                            "webhook_server.libs.handlers.runner_handler.run_command",
                            new=AsyncMock(return_value=(False, "output", "error")),
                        ):
                            await runner_handler.run_install_python_module(mock_pull_request)
                            mock_set_progress.assert_called_once()
                            mock_set_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_conventional_title_check_success(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test run_conventional_title_check with valid title."""
        with patch.object(
            runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
        ):
            with patch.object(
                runner_handler.check_run_handler, "set_conventional_title_in_progress"
            ) as mock_set_progress:
                with patch.object(
                    runner_handler.check_run_handler, "set_conventional_title_success"
                ) as mock_set_success:
                    await runner_handler.run_conventional_title_check(mock_pull_request)
                    mock_set_progress.assert_called_once()
                    mock_set_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_conventional_title_check_failure(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test run_conventional_title_check with invalid title."""
        mock_pull_request.title = "Invalid title"

        with patch.object(
            runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
        ):
            with patch.object(
                runner_handler.check_run_handler, "set_conventional_title_in_progress"
            ) as mock_set_progress:
                with patch.object(
                    runner_handler.check_run_handler, "set_conventional_title_failure"
                ) as mock_set_failure:
                    await runner_handler.run_conventional_title_check(mock_pull_request)
                    mock_set_progress.assert_called_once()
                    mock_set_failure.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "title,should_pass",
        [
            # Valid conventional commit formats
            ("feat: Add new feature", True),
            ("fix: Bug fix", True),
            ("docs: Update README", True),
            ("feat(scope): Feature with scope", True),
            ("fix(api): API bug fix", True),
            ("chore(deps): Update dependencies", True),
            ("fix!: Breaking change", True),
            ("feat(scope)!: Breaking with scope", True),
            ("feat:", True),  # Minimal valid format
            ("ci: CI improvement", True),
            ("test: Add tests", True),
            ("refactor: Code refactoring", True),
            ("perf: Performance improvement", True),
            ("chore: Chore task", True),
            ("release: New release", True),
            ("CherryPicked: Cherry-picked commit", True),
            # Invalid formats
            ("feature: Invalid prefix", False),
            ("Fix: Wrong case", False),
            ("FIX: Wrong case", False),
            ("feat", False),  # Missing colon
            ("feat(scope)", False),  # Missing colon after scope
            ("random: Not in allowed list", False),
            ("update: Not in allowed list", False),
            ("feat :Space before colon", False),
            ("feat : Space around colon", False),
            # Whitespace handling (should pass after stripping)
            (" feat: Leading space", True),
            ("feat: Trailing space ", True),
            ("  feat: Leading and trailing spaces  ", True),
            ("\tfeat: Tab prefix", True),
        ],
    )
    async def test_run_conventional_title_check_various_formats(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock, title: str, should_pass: bool
    ) -> None:
        """Test run_conventional_title_check with various title formats."""
        mock_pull_request.title = title

        with patch.object(
            runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
        ):
            with patch.object(runner_handler.check_run_handler, "set_conventional_title_in_progress"):
                with patch.object(
                    runner_handler.check_run_handler, "set_conventional_title_success"
                ) as mock_set_success:
                    with patch.object(
                        runner_handler.check_run_handler, "set_conventional_title_failure"
                    ) as mock_set_failure:
                        await runner_handler.run_conventional_title_check(mock_pull_request)

                        if should_pass:
                            mock_set_success.assert_called_once()
                            mock_set_failure.assert_not_called()
                        else:
                            mock_set_failure.assert_called_once()
                            mock_set_success.assert_not_called()

    @pytest.mark.asyncio
    async def test_is_branch_exists(self, runner_handler: RunnerHandler) -> None:
        """Test is_branch_exists."""
        runner_handler.github_webhook.unified_api.get_branch = AsyncMock(return_value=True)
        result = await runner_handler.is_branch_exists("main")
        assert result is True

    @pytest.mark.asyncio
    async def test_is_branch_exists_not_found(self, runner_handler: RunnerHandler) -> None:
        """Test is_branch_exists when branch does not exist (returns False)."""
        runner_handler.github_webhook.unified_api.get_branch = AsyncMock(return_value=False)
        result = await runner_handler.is_branch_exists("non-existent-branch")
        assert result is False

    @pytest.mark.asyncio
    async def test_is_branch_exists_other_error(self, runner_handler: RunnerHandler) -> None:
        """Test is_branch_exists when other GithubException occurs (should re-raise)."""

        runner_handler.github_webhook.unified_api.get_branch = AsyncMock(
            side_effect=GithubException(500, "Server Error", None)
        )
        with pytest.raises(GithubException) as exc_info:
            await runner_handler.is_branch_exists("main")
        assert exc_info.value.status == 500

    @pytest.mark.asyncio
    @patch("webhook_server.libs.handlers.runner_handler.send_slack_message")
    async def test_cherry_pick_branch_not_exists(
        self, mock_slack: Mock, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test cherry_pick when target branch doesn't exist."""
        mock_slack.return_value = True
        with patch.object(runner_handler, "is_branch_exists", new=AsyncMock(return_value=False)):
            with patch.object(
                runner_handler.github_webhook.unified_api, "add_comment", new_callable=AsyncMock
            ) as mock_comment:
                await runner_handler.cherry_pick(mock_pull_request, "non-existent-branch")
                # Verify add_comment was called with correct error message
                # Code now uses pull_request.id directly via _get_pr_node_id()
                mock_comment.assert_called_once()
                # add_comment(pr_id, body) - body is 2nd arg (index 1)
                call_args = mock_comment.call_args
                assert call_args[0][0] == mock_pull_request.id  # PR node ID
                assert "does not exist" in call_args[0][1]  # Error message

    @pytest.mark.asyncio
    async def test_cherry_pick_prepare_failure(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test cherry_pick when repository preparation fails."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        runner_handler.github_webhook.unified_api.create_issue_comment = AsyncMock()
        with patch.object(runner_handler, "is_branch_exists", new=AsyncMock(return_value=Mock())):
            with patch.object(runner_handler.check_run_handler, "set_cherry_pick_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_cherry_pick_failure") as mock_set_failure:
                    with patch.object(runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
                        mock_prepare.return_value = AsyncMock()
                        mock_prepare.return_value.__aenter__ = AsyncMock(return_value=(False, "out", "err"))
                        mock_prepare.return_value.__aexit__ = AsyncMock(return_value=None)
                        await runner_handler.cherry_pick(mock_pull_request, "main")
                        mock_set_progress.assert_called_once()
                        assert mock_set_failure.call_count >= 1

    @pytest.mark.asyncio
    async def test_cherry_pick_command_failure(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test cherry_pick when git command fails."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        runner_handler.github_webhook.unified_api.get_pull_request = AsyncMock(return_value={"id": "PR_test123"})
        runner_handler.github_webhook.unified_api.add_comment = AsyncMock()
        with patch.object(runner_handler, "is_branch_exists", new=AsyncMock(return_value=Mock())):
            with patch.object(runner_handler.check_run_handler, "set_cherry_pick_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_cherry_pick_failure") as mock_set_failure:
                    with patch.object(runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
                        mock_prepare.return_value = AsyncMock()
                        mock_prepare.return_value.__aenter__ = AsyncMock(return_value=(True, "", ""))
                        mock_prepare.return_value.__aexit__ = AsyncMock(return_value=None)
                        with patch(
                            "webhook_server.libs.handlers.runner_handler.run_command",
                            new=AsyncMock(return_value=(False, "output", "error")),
                        ):
                            await runner_handler.cherry_pick(mock_pull_request, "main")
                            mock_set_progress.assert_called_once()
                            mock_set_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_cherry_pick_success(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test cherry_pick with successful execution."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(runner_handler, "is_branch_exists", new=AsyncMock(return_value=Mock())):
            with patch.object(runner_handler.check_run_handler, "set_cherry_pick_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_cherry_pick_success") as mock_set_success:
                    with patch.object(runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
                        mock_prepare.return_value = AsyncMock()
                        mock_prepare.return_value.__aenter__ = AsyncMock(return_value=(True, "", ""))
                        mock_prepare.return_value.__aexit__ = AsyncMock(return_value=None)
                        with patch(
                            "webhook_server.libs.handlers.runner_handler.run_command",
                            new=AsyncMock(return_value=(True, "success", "")),
                        ):
                            # Mock unified_api methods
                            runner_handler.github_webhook.unified_api.get_pull_request = AsyncMock(
                                return_value={"id": "PR_test123"}
                            )
                            runner_handler.github_webhook.unified_api.add_comment = AsyncMock()
                            await runner_handler.cherry_pick(mock_pull_request, "main")
                            mock_set_progress.assert_called_once()
                            mock_set_success.assert_called_once()
                            # Verify success comment was posted
                            runner_handler.github_webhook.unified_api.add_comment.assert_called()
                            call_args = runner_handler.github_webhook.unified_api.add_comment.call_args
                            assert "cherry-picked pr" in call_args[0][1].lower()

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_success(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock, tmp_path
    ) -> None:
        """Test _prepare_cloned_repo_dir with successful preparation."""
        with patch(
            "webhook_server.libs.handlers.runner_handler.run_command", new=AsyncMock(return_value=(True, "success", ""))
        ):
            with patch.object(
                runner_handler.github_webhook, "get_pull_request", new=AsyncMock(return_value=mock_pull_request)
            ):
                async with runner_handler._prepare_cloned_repo_dir(
                    str(tmp_path / "test-repo-unique"), mock_pull_request
                ) as result:
                    success, _out, _err = result
                    assert success is True

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_clone_failure(self, runner_handler: RunnerHandler, tmp_path) -> None:
        """Test _prepare_cloned_repo_dir when clone fails."""
        with patch(
            "webhook_server.libs.handlers.runner_handler.run_command",
            new=AsyncMock(return_value=(False, "output", "error")),
        ):
            async with runner_handler._prepare_cloned_repo_dir(str(tmp_path / "test-repo-unique2")) as result:
                success, out, _err = result
                assert success is False
                assert out == "output"

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_with_checkout(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock, tmp_path
    ) -> None:
        """Test _prepare_cloned_repo_dir with checkout parameter."""
        with patch(
            "webhook_server.libs.handlers.runner_handler.run_command", new=AsyncMock(return_value=(True, "success", ""))
        ):
            async with runner_handler._prepare_cloned_repo_dir(
                str(tmp_path / "test-repo-unique3"), mock_pull_request, checkout="feature-branch"
            ) as result:
                success, _out, _err = result
                assert success is True

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_with_tag(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock, tmp_path
    ) -> None:
        """Test _prepare_cloned_repo_dir with tag_name parameter."""
        with patch(
            "webhook_server.libs.handlers.runner_handler.run_command", new=AsyncMock(return_value=(True, "success", ""))
        ):
            async with runner_handler._prepare_cloned_repo_dir(
                str(tmp_path / "test-repo-unique4"), mock_pull_request, tag_name="v1.0.0"
            ) as result:
                success, _out, _err = result
                assert success is True

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_merged_pr(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock, tmp_path
    ) -> None:
        """Test _prepare_cloned_repo_dir with merged pull request."""
        with patch(
            "webhook_server.libs.handlers.runner_handler.run_command", new=AsyncMock(return_value=(True, "success", ""))
        ):
            async with runner_handler._prepare_cloned_repo_dir(
                str(tmp_path / "test-repo-unique5"), mock_pull_request, is_merged=True
            ) as result:
                success, _out, _err = result
                assert success is True

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_git_config_user_name_failure(
        self, runner_handler, mock_pull_request, tmp_path
    ):
        # Simulate failure at git config user.name
        async def run_command_side_effect(*args, **kwargs):
            cmd = kwargs.get("command", args[0] if args else "")
            if "git clone" in cmd:
                return (True, "ok", "")
            if "config user.name" in cmd:
                return (False, "fail", "fail")
            return (True, "ok", "")

        with patch(
            "webhook_server.libs.handlers.runner_handler.run_command",
            new=AsyncMock(side_effect=run_command_side_effect),
        ):
            with patch.object(
                runner_handler.github_webhook, "get_pull_request", new=AsyncMock(return_value=mock_pull_request)
            ):
                async with runner_handler._prepare_cloned_repo_dir(
                    str(tmp_path / "test-repo-x"), mock_pull_request
                ) as result:
                    success, out, _err = result
                    assert not success
                    assert out == "fail"

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_git_config_user_email_failure(
        self, runner_handler, mock_pull_request, tmp_path
    ):
        # Simulate failure at git config user.email
        async def run_command_side_effect(*args, **kwargs):
            cmd = kwargs.get("command", args[0] if args else "")
            if "git clone" in cmd:
                return (True, "ok", "")
            if "config user.name" in cmd:
                return (True, "ok", "")
            if "config user.email" in cmd:
                return (False, "fail", "fail")
            return (True, "ok", "")

        with patch(
            "webhook_server.libs.handlers.runner_handler.run_command",
            new=AsyncMock(side_effect=run_command_side_effect),
        ):
            with patch.object(
                runner_handler.github_webhook, "get_pull_request", new=AsyncMock(return_value=mock_pull_request)
            ):
                async with runner_handler._prepare_cloned_repo_dir(
                    str(tmp_path / "test-repo-x"), mock_pull_request
                ) as result:
                    success, out, _err = result
                    assert not success
                    assert out == "fail"

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_git_config_fetch_failure(self, runner_handler, mock_pull_request, tmp_path):
        # Simulate failure at git config --local --add remote.origin.fetch
        async def run_command_side_effect(*args, **kwargs):
            cmd = kwargs.get("command", args[0] if args else "")
            if "git clone" in cmd:
                return (True, "ok", "")
            if "config user.name" in cmd or "config user.email" in cmd:
                return (True, "ok", "")
            if "config --local --add remote.origin.fetch" in cmd:
                return (False, "fail", "fail")
            return (True, "ok", "")

        with patch(
            "webhook_server.libs.handlers.runner_handler.run_command",
            new=AsyncMock(side_effect=run_command_side_effect),
        ):
            with patch.object(
                runner_handler.github_webhook, "get_pull_request", new=AsyncMock(return_value=mock_pull_request)
            ):
                async with runner_handler._prepare_cloned_repo_dir(
                    str(tmp_path / "test-repo-x"), mock_pull_request
                ) as result:
                    success, out, _err = result
                    assert not success
                    assert out == "fail"

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_git_remote_update_failure(self, runner_handler, mock_pull_request, tmp_path):
        # Simulate failure at git remote update
        async def run_command_side_effect(*args, **kwargs):
            cmd = kwargs.get("command", args[0] if args else "")
            if "git clone" in cmd:
                return (True, "ok", "")
            if (
                "config user.name" in cmd
                or "config user.email" in cmd
                or "config --local --add remote.origin.fetch" in cmd
            ):
                return (True, "ok", "")
            if "remote update" in cmd:
                return (False, "fail", "fail")
            return (True, "ok", "")

        with patch(
            "webhook_server.libs.handlers.runner_handler.run_command",
            new=AsyncMock(side_effect=run_command_side_effect),
        ):
            with patch.object(
                runner_handler.github_webhook, "get_pull_request", new=AsyncMock(return_value=mock_pull_request)
            ):
                async with runner_handler._prepare_cloned_repo_dir(
                    str(tmp_path / "test-repo-x"), mock_pull_request
                ) as result:
                    success, out, _err = result
                    assert not success
                    assert out == "fail"

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_get_pull_request_exception(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock, tmp_path
    ) -> None:
        """Test _prepare_cloned_repo_dir handles get_pull_request exceptions gracefully."""
        mock_run_command = AsyncMock(return_value=(True, "success", ""))
        with patch("webhook_server.libs.handlers.runner_handler.run_command", new=mock_run_command):
            # Make get_pull_request raise an exception
            with patch.object(
                runner_handler.github_webhook, "get_pull_request", new=AsyncMock(side_effect=Exception("Test error"))
            ):
                async with runner_handler._prepare_cloned_repo_dir(
                    str(tmp_path / "test-repo-exception"), mock_pull_request
                ) as result:
                    success, _out, _err = result
                    # Should still succeed despite exception in get_pull_request
                    assert success is True

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_git_clone_with_token(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock, tmp_path
    ) -> None:
        """Test that git clone embeds token in URL for thread-safe authentication.

        Verifies that the token is embedded directly in the clone URL using the
        x-access-token format, ensuring each concurrent clone has its own credentials.
        """
        mock_run_command = AsyncMock(return_value=(True, "success", ""))
        with patch("webhook_server.libs.handlers.runner_handler.run_command", new=mock_run_command):
            with patch.object(
                runner_handler.github_webhook, "get_pull_request", new=AsyncMock(return_value=mock_pull_request)
            ):
                async with runner_handler._prepare_cloned_repo_dir(
                    str(tmp_path / "test-repo-token-check"), mock_pull_request
                ) as result:
                    success, _out, _err = result
                    assert success is True

                    # Get the first call to run_command (git clone call)
                    clone_call = mock_run_command.call_args_list[0]
                    clone_cmd = clone_call.kwargs.get("command")

                    # Verify git clone command structure
                    assert "git clone" in clone_cmd, "Should call git clone"
                    # Token should be embedded in URL with x-access-token format
                    assert "x-access-token:" in clone_cmd, "Should use x-access-token format"
                    # Original clone_url should not be in command (since token is added)
                    assert runner_handler.github_webhook.repository.clone_url not in clone_cmd, (
                        "Should modify clone_url to include token"
                    )

                    # Verify no environment variables are passed (thread-safe approach)
                    clone_env = clone_call.kwargs.get("env")
                    assert clone_env is None, "Should not use environment variables for thread-safe cloning"

    @pytest.mark.asyncio
    @patch("webhook_server.libs.handlers.runner_handler.send_slack_message")
    async def test_run_build_container_push_failure(self, mock_slack: Mock, runner_handler, mock_pull_request):
        mock_slack.return_value = True
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        runner_handler.github_webhook.container_build_args = ["ARG1=1"]
        runner_handler.github_webhook.container_command_args = ["--cmd"]
        # Ensure pull_request is definitely not None
        assert mock_pull_request is not None
        with patch.object(
            runner_handler.github_webhook, "container_repository_and_tag", return_value="test/repo:latest"
        ):
            with patch.object(
                runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
            ):
                with patch.object(
                    runner_handler.check_run_handler, "set_container_build_in_progress"
                ) as mock_set_progress:
                    with patch.object(
                        runner_handler.check_run_handler, "set_container_build_success"
                    ) as mock_set_success:
                        with patch.object(
                            runner_handler.check_run_handler, "set_container_build_failure"
                        ) as mock_set_failure:
                            with patch.object(runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
                                mock_prepare.return_value = AsyncMock()
                                mock_prepare.return_value.__aenter__ = AsyncMock(return_value=(True, "", ""))
                                mock_prepare.return_value.__aexit__ = AsyncMock(return_value=None)
                                # Mock run_command for podman login
                                with patch(
                                    "webhook_server.libs.handlers.runner_handler.run_command",
                                    new=AsyncMock(return_value=(True, "Login Succeeded", "")),
                                ):
                                    with patch.object(
                                        runner_handler, "run_podman_command", new_callable=AsyncMock
                                    ) as mock_run_podman:
                                        # First call (build) succeeds, second call (push) fails
                                        mock_run_podman.side_effect = [
                                            (True, "build success", ""),
                                            (False, "push fail", "push error"),
                                        ]
                                        with patch.object(
                                            runner_handler.github_webhook, "slack_webhook_url", "http://slack"
                                        ):
                                            # Mock unified_api methods
                                            runner_handler.github_webhook.unified_api.get_pull_request = AsyncMock(
                                                return_value={"id": "PR_test123"}
                                            )
                                            runner_handler.github_webhook.unified_api.add_comment = AsyncMock()
                                            # Set set_check=False to avoid setting check status
                                            await runner_handler.run_build_container(
                                                pull_request=mock_pull_request, push=True, set_check=False
                                            )
                                            # Should not call set_progress because set_check=False
                                            mock_set_progress.assert_not_called()
                                            # Should not call set_success because set_check=False
                                            mock_set_success.assert_not_called()
                                            # Comment should be added when push fails
                                            runner_handler.github_webhook.unified_api.add_comment.assert_called_once()
                                        # Should be called twice: build and push
                                        assert mock_run_podman.call_count == 2, (
                                            f"Expected 2 calls, got {mock_run_podman.call_count}"
                                        )
                                        mock_set_failure.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_build_container_with_command_args(self, runner_handler, mock_pull_request):
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(
            runner_handler.github_webhook, "container_repository_and_tag", return_value="test/repo:latest"
        ):
            with patch.object(
                runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
            ):
                with patch.object(
                    runner_handler.check_run_handler, "set_container_build_in_progress"
                ) as mock_set_progress:
                    with patch.object(
                        runner_handler.check_run_handler, "set_container_build_success"
                    ) as mock_set_success:
                        with patch.object(runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
                            mock_prepare.return_value = AsyncMock()
                            mock_prepare.return_value.__aenter__ = AsyncMock(return_value=(True, "", ""))
                            mock_prepare.return_value.__aexit__ = AsyncMock(return_value=None)
                            with patch.object(
                                runner_handler,
                                "run_podman_command",
                                new_callable=AsyncMock,
                                return_value=(True, "success", ""),
                            ):
                                await runner_handler.run_build_container(
                                    pull_request=mock_pull_request, command_args="--extra-arg"
                                )
                                mock_set_progress.assert_called_once()
                                mock_set_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_cherry_pick_manual_needed(self, runner_handler, mock_pull_request):
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(runner_handler, "is_branch_exists", new=AsyncMock(return_value=Mock())):
            with patch.object(runner_handler.check_run_handler, "set_cherry_pick_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_cherry_pick_failure") as mock_set_failure:
                    with patch.object(runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
                        mock_prepare.return_value = AsyncMock()
                        mock_prepare.return_value.__aenter__ = AsyncMock(return_value=(True, "", ""))
                        mock_prepare.return_value.__aexit__ = AsyncMock(return_value=None)
                        # First command fails, triggers manual cherry-pick
                        with patch(
                            "webhook_server.libs.handlers.runner_handler.run_command",
                            side_effect=[(False, "fail", "err")],
                        ):
                            # Mock unified_api methods
                            runner_handler.github_webhook.unified_api.get_pull_request = AsyncMock(
                                return_value={"id": "PR_test123"}
                            )
                            runner_handler.github_webhook.unified_api.add_comment = AsyncMock()
                            await runner_handler.cherry_pick(mock_pull_request, "main")
                            mock_set_progress.assert_called_once()
                            mock_set_failure.assert_called_once()

    @pytest.mark.asyncio
    @patch("webhook_server.utils.notification_utils.send_slack_message")
    async def test_cherry_pick_merge_commit_sha_none_fallback_success(
        self, mock_slack: Mock, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test cherry-pick when merge_commit_sha is None but fallback succeeds."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        # Set merge_commit_sha to None to trigger fallback
        mock_pull_request.merge_commit_sha = None

        with patch.object(runner_handler, "is_branch_exists", new=AsyncMock(return_value=Mock())):
            with patch.object(runner_handler.check_run_handler, "set_cherry_pick_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_cherry_pick_success") as mock_set_success:
                    with patch.object(runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
                        mock_prepare.return_value = AsyncMock()
                        mock_prepare.return_value.__aenter__ = AsyncMock(return_value=(True, "", ""))
                        mock_prepare.return_value.__aexit__ = AsyncMock(return_value=None)
                        # Mock GraphQL fallback to return commit hash
                        runner_handler.github_webhook.unified_api.get_pull_request_data = AsyncMock(
                            return_value={
                                "id": "PR_test123",
                                "commits": {"nodes": [{"commit": {"oid": "fallback_commit_sha"}}]},
                            }
                        )
                        runner_handler.github_webhook.unified_api.add_comment = AsyncMock()
                        with patch(
                            "webhook_server.libs.handlers.runner_handler.run_command",
                            new=AsyncMock(return_value=(True, "success", "")),
                        ):
                            await runner_handler.cherry_pick(mock_pull_request, "main")
                            mock_set_progress.assert_called_once()
                            mock_set_success.assert_called_once()
                            # Verify GraphQL fallback was called once (refactoring changed this from 2 to 1)
                            assert runner_handler.github_webhook.unified_api.get_pull_request_data.call_count == 1
                            # The call should be for the fallback with include_commits=True
                            first_call = runner_handler.github_webhook.unified_api.get_pull_request_data.call_args_list[
                                0
                            ]
                            assert first_call.kwargs.get("include_commits") is True

    @pytest.mark.asyncio
    async def test_cherry_pick_merge_commit_sha_none_fallback_failure(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test cherry-pick when merge_commit_sha is None and fallback fails."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        # Set merge_commit_sha to None to trigger fallback
        mock_pull_request.merge_commit_sha = None

        with patch.object(runner_handler, "is_branch_exists", new=AsyncMock(return_value=Mock())):
            with patch.object(runner_handler.check_run_handler, "set_cherry_pick_in_progress") as mock_set_progress:
                # Mock GraphQL fallback to fail
                runner_handler.github_webhook.unified_api.get_pull_request_data = AsyncMock(
                    return_value={"id": "PR_test123", "commits": {"nodes": []}}  # No commits
                )
                runner_handler.github_webhook.unified_api.add_comment = AsyncMock()
                await runner_handler.cherry_pick(mock_pull_request, "main")
                mock_set_progress.assert_called_once()
                # Verify error comment was posted
                runner_handler.github_webhook.unified_api.add_comment.assert_called_once()
                call_args = runner_handler.github_webhook.unified_api.add_comment.call_args
                assert "has not been merged yet" in call_args[0][1]
