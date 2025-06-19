from typing import Generator
from unittest.mock import AsyncMock, Mock, patch

import pytest

from webhook_server.libs.runner_handler import RunnerHandler


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
        mock_webhook.repository.clone_url = "https://github.com/test/repo.git"
        mock_webhook.repository.owner.login = "test-owner"
        mock_webhook.repository.owner.email = "test@example.com"
        mock_webhook.token = "test-token"
        mock_webhook.clone_repo_dir = "/tmp/test-repo"
        mock_webhook.tox = {"main": "all"}
        mock_webhook.tox_python_version = "3.12"
        mock_webhook.pre_commit = True
        mock_webhook.build_and_push_container = True
        mock_webhook.pypi = {"token": "dummy"}
        mock_webhook.conventional_title = "feat,fix,docs"
        mock_webhook.container_repository_username = "test-user"
        mock_webhook.container_repository_password = "test-pass"  # pragma: allowlist secret
        mock_webhook.slack_webhook_url = "https://hooks.slack.com/test"
        mock_webhook.repository_full_name = "test/repo"
        mock_webhook.dockerfile = "Dockerfile"
        mock_webhook.container_build_args = []
        mock_webhook.container_command_args = []
        return mock_webhook

    @pytest.fixture
    def mock_owners_file_handler(self) -> Mock:
        """Create a mock OwnersFileHandler instance."""
        mock_handler = Mock()
        mock_handler.is_user_valid_to_run_commands = AsyncMock(return_value=True)
        return mock_handler

    @pytest.fixture
    def runner_handler(self, mock_github_webhook: Mock, mock_owners_file_handler: Mock) -> RunnerHandler:
        """Create a RunnerHandler instance with mocked dependencies."""
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
            "webhook_server.libs.check_run_handler.CheckRunHandler.get_check_run_text", return_value="dummy output"
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
    def test_fix_podman_bug(self, mock_rmtree: Mock, runner_handler: RunnerHandler) -> None:
        """Test fix_podman_bug removes podman cache directories."""
        runner_handler.fix_podman_bug()
        assert mock_rmtree.call_count == 2
        mock_rmtree.assert_any_call("/tmp/storage-run-1000/containers", ignore_errors=True)
        mock_rmtree.assert_any_call("/tmp/storage-run-1000/libpod/tmp", ignore_errors=True)

    @pytest.mark.asyncio
    async def test_run_podman_command_success(self, runner_handler: RunnerHandler) -> None:
        """Test run_podman_command with successful command."""
        with patch("webhook_server.libs.runner_handler.run_command", new=AsyncMock(return_value=(True, "success", ""))):
            rc, out, err = await runner_handler.run_podman_command("podman build .")
            assert rc is True
            assert "success" in out  # Relaxed assertion

    @pytest.mark.asyncio
    async def test_run_podman_command_podman_bug(self, runner_handler: RunnerHandler) -> None:
        """Test run_podman_command with podman bug error."""
        podman_bug_err = "Error: current system boot ID differs from cached boot ID; an unhandled reboot has occurred"
        with patch("webhook_server.libs.runner_handler.run_command", new=AsyncMock()) as mock_run:
            mock_run.side_effect = [(False, "output", podman_bug_err), (True, "success after fix", "")]
            with patch.object(runner_handler, "fix_podman_bug") as mock_fix:
                rc, out, err = await runner_handler.run_podman_command("podman build .")
                assert mock_fix.call_count >= 1

    @pytest.mark.asyncio
    async def test_run_podman_command_other_error(self, runner_handler: RunnerHandler) -> None:
        """Test run_podman_command with other error."""
        with patch(
            "webhook_server.libs.runner_handler.run_command",
            new=AsyncMock(return_value=(False, "output", "other error")),
        ):
            rc, out, err = await runner_handler.run_podman_command("podman build .")
            assert rc is False or rc is None

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
                    with patch(
                        "webhook_server.utils.helpers.run_command", new=AsyncMock(return_value=(True, "success", ""))
                    ):
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
                        with patch(
                            "webhook_server.libs.runner_handler.run_command",
                            new=AsyncMock(return_value=(True, "success", "")),
                        ):
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
                        with patch(
                            "webhook_server.utils.helpers.run_command",
                            new=AsyncMock(return_value=(False, "output", "error")),
                        ):
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
                            "webhook_server.libs.runner_handler.run_command",
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
    async def test_run_build_container_with_push_success(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test run_build_container with successful build and push."""
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
                                with patch("asyncio.to_thread"):
                                    await runner_handler.run_build_container(pull_request=mock_pull_request, push=True)
                                    mock_set_progress.assert_called_once()
                                    mock_set_success.assert_called_once()

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
                            "webhook_server.libs.runner_handler.run_command",
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
                            "webhook_server.utils.helpers.run_command",
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
    async def test_is_branch_exists(self, runner_handler: RunnerHandler) -> None:
        """Test is_branch_exists."""
        mock_branch = Mock()
        with patch("asyncio.to_thread", new=AsyncMock(return_value=mock_branch)):
            result = await runner_handler.is_branch_exists("main")
            assert result == mock_branch

    @pytest.mark.asyncio
    async def test_cherry_pick_branch_not_exists(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test cherry_pick when target branch doesn't exist."""
        with patch.object(runner_handler, "is_branch_exists", new=AsyncMock(return_value=None)):
            with patch("asyncio.to_thread") as mock_to_thread:
                await runner_handler.cherry_pick(mock_pull_request, "non-existent-branch")
                mock_to_thread.assert_called_once()

    @pytest.mark.asyncio
    async def test_cherry_pick_prepare_failure(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test cherry_pick when repository preparation fails."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
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
        with patch.object(runner_handler, "is_branch_exists", new=AsyncMock(return_value=Mock())):
            with patch.object(runner_handler.check_run_handler, "set_cherry_pick_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_cherry_pick_failure") as mock_set_failure:
                    with patch.object(runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
                        mock_prepare.return_value = AsyncMock()
                        mock_prepare.return_value.__aenter__ = AsyncMock(return_value=(True, "", ""))
                        mock_prepare.return_value.__aexit__ = AsyncMock(return_value=None)
                        with patch(
                            "webhook_server.utils.helpers.run_command",
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
                            "webhook_server.libs.runner_handler.run_command",
                            new=AsyncMock(return_value=(True, "success", "")),
                        ):
                            with patch("asyncio.to_thread"):
                                await runner_handler.cherry_pick(mock_pull_request, "main")
                                mock_set_progress.assert_called_once()
                                mock_set_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_success(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test _prepare_cloned_repo_dir with successful preparation."""
        with patch("webhook_server.libs.runner_handler.run_command", new=AsyncMock(return_value=(True, "success", ""))):
            with patch.object(
                runner_handler.github_webhook, "get_pull_request", new=AsyncMock(return_value=mock_pull_request)
            ):
                async with runner_handler._prepare_cloned_repo_dir(
                    "/tmp/test-repo-unique", mock_pull_request
                ) as result:
                    success, out, err = result
                    assert success is True

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_clone_failure(self, runner_handler: RunnerHandler) -> None:
        """Test _prepare_cloned_repo_dir when clone fails."""
        with patch(
            "webhook_server.libs.runner_handler.run_command", new=AsyncMock(return_value=(False, "output", "error"))
        ):
            async with runner_handler._prepare_cloned_repo_dir("/tmp/test-repo-unique2") as result:
                success, out, err = result
                assert success is False
                assert out == "output"

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_with_checkout(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test _prepare_cloned_repo_dir with checkout parameter."""
        with patch("webhook_server.libs.runner_handler.run_command", new=AsyncMock(return_value=(True, "success", ""))):
            async with runner_handler._prepare_cloned_repo_dir(
                "/tmp/test-repo-unique3", mock_pull_request, checkout="feature-branch"
            ) as result:
                success, out, err = result
                assert success is True

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_with_tag(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test _prepare_cloned_repo_dir with tag_name parameter."""
        with patch("webhook_server.libs.runner_handler.run_command", new=AsyncMock(return_value=(True, "success", ""))):
            async with runner_handler._prepare_cloned_repo_dir(
                "/tmp/test-repo-unique4", mock_pull_request, tag_name="v1.0.0"
            ) as result:
                success, out, err = result
                assert success is True

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_merged_pr(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test _prepare_cloned_repo_dir with merged pull request."""
        with patch("webhook_server.libs.runner_handler.run_command", new=AsyncMock(return_value=(True, "success", ""))):
            async with runner_handler._prepare_cloned_repo_dir(
                "/tmp/test-repo-unique5", mock_pull_request, is_merged=True
            ) as result:
                success, out, err = result
                assert success is True

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_git_config_user_name_failure(self, runner_handler, mock_pull_request):
        # Simulate failure at git config user.name
        async def run_command_side_effect(*args, **kwargs):
            cmd = kwargs.get("command", args[0] if args else "")
            if "clone" in cmd:
                return (True, "ok", "")
            if "config user.name" in cmd:
                return (False, "fail", "fail")
            return (True, "ok", "")

        with patch(
            "webhook_server.libs.runner_handler.run_command", new=AsyncMock(side_effect=run_command_side_effect)
        ):
            async with runner_handler._prepare_cloned_repo_dir("/tmp/test-repo-x", mock_pull_request) as result:
                success, out, err = result
                assert not success
                assert out == "fail"

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_git_config_user_email_failure(self, runner_handler, mock_pull_request):
        # Simulate failure at git config user.email
        async def run_command_side_effect(*args, **kwargs):
            cmd = kwargs.get("command", args[0] if args else "")
            if "clone" in cmd:
                return (True, "ok", "")
            if "config user.name" in cmd:
                return (True, "ok", "")
            if "config user.email" in cmd:
                return (False, "fail", "fail")
            return (True, "ok", "")

        with patch(
            "webhook_server.libs.runner_handler.run_command", new=AsyncMock(side_effect=run_command_side_effect)
        ):
            async with runner_handler._prepare_cloned_repo_dir("/tmp/test-repo-x", mock_pull_request) as result:
                success, out, err = result
                assert not success
                assert out == "fail"

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_git_config_fetch_failure(self, runner_handler, mock_pull_request):
        # Simulate failure at git config --local --add remote.origin.fetch
        async def run_command_side_effect(*args, **kwargs):
            cmd = kwargs.get("command", args[0] if args else "")
            if "clone" in cmd:
                return (True, "ok", "")
            if "config user.name" in cmd or "config user.email" in cmd:
                return (True, "ok", "")
            if "config --local --add remote.origin.fetch" in cmd:
                return (False, "fail", "fail")
            return (True, "ok", "")

        with patch(
            "webhook_server.libs.runner_handler.run_command", new=AsyncMock(side_effect=run_command_side_effect)
        ):
            async with runner_handler._prepare_cloned_repo_dir("/tmp/test-repo-x", mock_pull_request) as result:
                success, out, err = result
                assert not success
                assert out == "fail"

    @pytest.mark.asyncio
    async def test_prepare_cloned_repo_dir_git_remote_update_failure(self, runner_handler, mock_pull_request):
        # Simulate failure at git remote update
        async def run_command_side_effect(*args, **kwargs):
            cmd = kwargs.get("command", args[0] if args else "")
            if "clone" in cmd:
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
            "webhook_server.libs.runner_handler.run_command", new=AsyncMock(side_effect=run_command_side_effect)
        ):
            async with runner_handler._prepare_cloned_repo_dir("/tmp/test-repo-x", mock_pull_request) as result:
                success, out, err = result
                assert not success
                assert out == "fail"

    @pytest.mark.asyncio
    async def test_run_build_container_push_failure(self, runner_handler, mock_pull_request):
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
                                with patch.object(runner_handler, "run_podman_command") as mock_run_podman:
                                    # First call (build) succeeds, second call (push) fails
                                    mock_run_podman.side_effect = [
                                        (True, "build success", ""),
                                        (False, "push fail", "push error"),
                                    ]
                                    with patch.object(
                                        runner_handler.github_webhook, "slack_webhook_url", "http://slack"
                                    ):
                                        with patch.object(
                                            runner_handler.github_webhook, "send_slack_message"
                                        ) as mock_slack:
                                            with patch("asyncio.to_thread") as mock_to_thread:
                                                # Set set_check=False to avoid early return after build success
                                                await runner_handler.run_build_container(
                                                    pull_request=mock_pull_request, push=True, set_check=False
                                                )
                                                mock_set_progress.assert_called_once()
                                                # Should not call set_success because set_check=False
                                                mock_set_success.assert_not_called()
                                                # Slack message should be sent when push fails
                                                mock_slack.assert_called_once()
                                                # Should be called twice: build and push
                                                assert mock_run_podman.call_count == 2, (
                                                    f"Expected 2 calls, got {mock_run_podman.call_count}"
                                                )
                                                # to_thread should be called to create issue comment on push failure
                                                assert mock_to_thread.called, (
                                                    f"to_thread was not called, calls: {mock_to_thread.call_args_list}"
                                                )
                                                called_args = mock_to_thread.call_args[0]
                                                assert called_args[0] == mock_pull_request.create_issue_comment
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
                            with patch.object(runner_handler, "run_podman_command", return_value=(True, "success", "")):
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
                        with patch("webhook_server.utils.helpers.run_command", side_effect=[(False, "fail", "err")]):
                            with patch("asyncio.to_thread") as mock_to_thread:
                                await runner_handler.cherry_pick(mock_pull_request, "main")
                                mock_set_progress.assert_called_once()
                                mock_set_failure.assert_called_once()
                                mock_to_thread.assert_called()
