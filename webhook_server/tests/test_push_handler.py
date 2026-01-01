"""Tests for webhook_server.libs.handlers.push_handler module."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from webhook_server.libs.handlers.push_handler import PushHandler


def _build_checkout_context(result: tuple[bool, str, str, str]):
    """Create an async context manager that yields the provided result."""

    @asynccontextmanager
    async def _cm(*_args, **_kwargs):
        yield result

    return _cm()


def _set_checkout_result(mock_checkout: Mock, result: tuple[bool, str, str, str]) -> None:
    """Configure the checkout mock to return an async context manager."""
    mock_checkout.side_effect = lambda *_a, **_kw: _build_checkout_context(result)


class TestPushHandler:
    """Test suite for PushHandler class."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance."""
        mock_webhook = Mock()
        mock_webhook.hook_data = {"ref": "refs/tags/v1.0.0"}
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.pypi = {"token": "test-token"}
        mock_webhook.build_and_push_container = True
        mock_webhook.container_release = True
        mock_webhook.clone_repo_dir = "/tmp/test-repo"
        mock_webhook.slack_webhook_url = "https://hooks.slack.com/test"
        mock_webhook.repository_name = "test-repo"
        mock_webhook.container_repository_username = "test-user"  # Always a string
        mock_webhook.container_repository_password = "test-password"  # Always a string # pragma: allowlist secret
        mock_webhook.token = "test-token"  # Always a string
        mock_webhook.tox_max_concurrent = 5
        return mock_webhook

    @pytest.fixture
    def push_handler(self, mock_github_webhook: Mock) -> PushHandler:
        """Create a PushHandler instance with mocked dependencies."""
        handler = PushHandler(mock_github_webhook)
        # Mock check_run_handler methods used by run_retests_from_config
        handler.runner_handler.check_run_handler.set_run_tox_check_queued = AsyncMock()
        handler.runner_handler.check_run_handler.set_run_pre_commit_check_queued = AsyncMock()
        handler.runner_handler.check_run_handler.set_container_build_queued = AsyncMock()
        handler.runner_handler.check_run_handler.set_python_module_install_queued = AsyncMock()
        handler.runner_handler.check_run_handler.set_conventional_title_queued = AsyncMock()
        return handler

    @pytest.mark.asyncio
    async def test_process_push_webhook_data_with_tag_and_pypi(self, push_handler: PushHandler) -> None:
        """Test processing push webhook data with tag and pypi enabled."""
        with patch.object(push_handler, "upload_to_pypi", new_callable=AsyncMock) as mock_upload:
            with patch.object(push_handler.runner_handler, "run_build_container", new_callable=AsyncMock) as mock_build:
                await push_handler.process_push_webhook_data()

                mock_upload.assert_called_once_with(tag_name="v1.0.0")
                mock_build.assert_called_once_with(push=True, set_check=False, tag="v1.0.0")

    @pytest.mark.asyncio
    async def test_process_push_webhook_data_with_tag_no_pypi(self, push_handler: PushHandler) -> None:
        """Test processing push webhook data with tag but no pypi."""
        push_handler.github_webhook.pypi = {}  # Empty dict instead of None

        with patch.object(push_handler, "upload_to_pypi", new_callable=AsyncMock) as mock_upload:
            with patch.object(push_handler.runner_handler, "run_build_container", new_callable=AsyncMock) as mock_build:
                await push_handler.process_push_webhook_data()

                mock_upload.assert_not_called()
                mock_build.assert_called_once_with(push=True, set_check=False, tag="v1.0.0")

    @pytest.mark.asyncio
    async def test_process_push_webhook_data_with_tag_no_container(self, push_handler: PushHandler) -> None:
        """Test processing push webhook data with tag but no container build."""
        push_handler.github_webhook.build_and_push_container = False

        with patch.object(push_handler, "upload_to_pypi", new_callable=AsyncMock) as mock_upload:
            with patch.object(push_handler.runner_handler, "run_build_container", new_callable=AsyncMock) as mock_build:
                await push_handler.process_push_webhook_data()

                mock_upload.assert_called_once_with(tag_name="v1.0.0")
                mock_build.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_push_webhook_data_with_tag_no_container_release(self, push_handler: PushHandler) -> None:
        """Test processing push webhook data with tag but no container release."""
        push_handler.github_webhook.container_release = False

        with patch.object(push_handler, "upload_to_pypi", new_callable=AsyncMock) as mock_upload:
            with patch.object(push_handler.runner_handler, "run_build_container", new_callable=AsyncMock) as mock_build:
                await push_handler.process_push_webhook_data()

                mock_upload.assert_called_once_with(tag_name="v1.0.0")
                mock_build.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_push_webhook_data_no_tag(self, push_handler: PushHandler) -> None:
        """Test processing push webhook data without tag."""
        push_handler.hook_data["ref"] = "refs/heads/main"
        push_handler.github_webhook.retrigger_checks_on_base_push = None

        with patch.object(push_handler, "upload_to_pypi", new_callable=AsyncMock) as mock_upload:
            with patch.object(push_handler.runner_handler, "run_build_container", new_callable=AsyncMock) as mock_build:
                await push_handler.process_push_webhook_data()

                mock_upload.assert_not_called()
                mock_build.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_push_webhook_data_tag_with_slash(self, push_handler: PushHandler) -> None:
        """Test processing push webhook data with tag containing slash."""
        push_handler.hook_data["ref"] = "refs/tags/release/v1.0.0"

        with patch.object(push_handler, "upload_to_pypi", new_callable=AsyncMock) as mock_upload:
            with patch.object(push_handler.runner_handler, "run_build_container", new_callable=AsyncMock) as mock_build:
                await push_handler.process_push_webhook_data()

                mock_upload.assert_called_once_with(tag_name="release/v1.0.0")
                mock_build.assert_called_once_with(push=True, set_check=False, tag="release/v1.0.0")

    @pytest.mark.asyncio
    async def test_upload_to_pypi_success(self, push_handler: PushHandler) -> None:
        """Test successful upload to pypi."""
        with patch.object(push_handler.runner_handler, "_checkout_worktree") as mock_checkout:
            with patch(
                "webhook_server.libs.handlers.push_handler.run_command", new_callable=AsyncMock
            ) as mock_run_command:
                with patch("webhook_server.libs.handlers.push_handler.send_slack_message") as mock_slack:
                    # Mock successful checkout
                    _set_checkout_result(mock_checkout, (True, "/tmp/worktree-path", "", ""))

                    # Mock successful build
                    mock_run_command.side_effect = [
                        (True, "", ""),  # uv build
                        (True, "package-1.0.0.tar.gz", ""),  # ls command
                        (True, "", ""),  # twine check
                        (True, "", ""),  # twine upload
                    ]

                    await push_handler.upload_to_pypi(tag_name="v1.0.0")

                    # Verify checkout was called
                    mock_checkout.assert_called_once()

                    # Verify build command was called
                    assert mock_run_command.call_count == 4

                    # Verify slack message was sent
                    mock_slack.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_to_pypi_clone_failure(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when clone fails."""
        with patch.object(push_handler.runner_handler, "_checkout_worktree") as mock_checkout:
            with patch.object(push_handler.repository, "create_issue") as mock_create_issue:
                # Mock failed checkout
                _set_checkout_result(
                    mock_checkout,
                    (
                        False,
                        "/tmp/worktree-path",
                        "Clone failed",
                        "Error",
                    ),
                )

                await push_handler.upload_to_pypi(tag_name="v1.0.0")

                # Verify issue was created
                mock_create_issue.assert_called_once()
                call_args = mock_create_issue.call_args
                assert "Clone failed" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_build_failure(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when build fails."""
        with patch.object(push_handler.runner_handler, "_checkout_worktree") as mock_checkout:
            with patch(
                "webhook_server.libs.handlers.push_handler.run_command", new_callable=AsyncMock
            ) as mock_run_command:
                with patch.object(push_handler.repository, "create_issue") as mock_create_issue:
                    # Mock successful checkout
                    _set_checkout_result(mock_checkout, (True, "/tmp/worktree-path", "", ""))

                    # Mock failed build
                    mock_run_command.return_value = (False, "Build failed", "Error")

                    await push_handler.upload_to_pypi(tag_name="v1.0.0")

                    # Verify issue was created
                    mock_create_issue.assert_called_once()
                    call_args = mock_create_issue.call_args
                    assert "Build failed" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_ls_failure(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when ls command fails."""
        with patch.object(push_handler.runner_handler, "_checkout_worktree") as mock_checkout:
            with patch(
                "webhook_server.libs.handlers.push_handler.run_command", new_callable=AsyncMock
            ) as mock_run_command:
                with patch.object(push_handler.repository, "create_issue") as mock_create_issue:
                    # Mock successful checkout
                    _set_checkout_result(mock_checkout, (True, "/tmp/worktree-path", "", ""))

                    # Mock successful build, failed ls
                    mock_run_command.side_effect = [
                        (True, "", ""),  # uv build
                        (False, "ls failed", "Error"),  # ls command
                    ]

                    await push_handler.upload_to_pypi(tag_name="v1.0.0")

                    # Verify issue was created
                    mock_create_issue.assert_called_once()
                    call_args = mock_create_issue.call_args
                    assert "ls failed" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_twine_check_failure(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when twine check fails."""
        with patch.object(push_handler.runner_handler, "_checkout_worktree") as mock_checkout:
            with patch(
                "webhook_server.libs.handlers.push_handler.run_command", new_callable=AsyncMock
            ) as mock_run_command:
                with patch.object(push_handler.repository, "create_issue") as mock_create_issue:
                    # Mock successful checkout
                    _set_checkout_result(mock_checkout, (True, "/tmp/worktree-path", "", ""))

                    # Mock successful build and ls, failed twine check
                    mock_run_command.side_effect = [
                        (True, "", ""),  # uv build
                        (True, "package-1.0.0.tar.gz", ""),  # ls command
                        (False, "twine check failed", "Error"),  # twine check
                    ]

                    await push_handler.upload_to_pypi(tag_name="v1.0.0")

                    # Verify issue was created
                    mock_create_issue.assert_called_once()
                    call_args = mock_create_issue.call_args
                    assert "twine check failed" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_twine_upload_failure(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when twine upload fails."""
        with patch.object(push_handler.runner_handler, "_checkout_worktree") as mock_checkout:
            with patch(
                "webhook_server.libs.handlers.push_handler.run_command", new_callable=AsyncMock
            ) as mock_run_command:
                with patch.object(push_handler.repository, "create_issue") as mock_create_issue:
                    # Mock successful checkout
                    _set_checkout_result(mock_checkout, (True, "/tmp/worktree-path", "", ""))

                    # Mock successful build, ls, and twine check, failed twine upload
                    mock_run_command.side_effect = [
                        (True, "", ""),  # uv build
                        (True, "package-1.0.0.tar.gz", ""),  # ls command
                        (True, "", ""),  # twine check
                        (False, "twine upload failed", "Error"),  # twine upload
                    ]

                    await push_handler.upload_to_pypi(tag_name="v1.0.0")

                    # Verify issue was created
                    mock_create_issue.assert_called_once()
                    call_args = mock_create_issue.call_args
                    assert "twine upload failed" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_success_no_slack(self, push_handler: PushHandler) -> None:
        """Test successful upload to pypi without slack webhook."""
        push_handler.github_webhook.slack_webhook_url = ""  # Empty string instead of None

        with patch.object(push_handler.runner_handler, "_checkout_worktree") as mock_checkout:
            with patch(
                "webhook_server.libs.handlers.push_handler.run_command", new_callable=AsyncMock
            ) as mock_run_command:
                # Mock successful checkout
                _set_checkout_result(mock_checkout, (True, "/tmp/worktree-path", "", ""))

                # Mock successful build
                mock_run_command.side_effect = [
                    (True, "", ""),  # uv build
                    (True, "package-1.0.0.tar.gz", ""),  # ls command
                    (True, "", ""),  # twine check
                    (True, "", ""),  # twine upload
                ]

                with patch("webhook_server.libs.handlers.push_handler.send_slack_message") as mock_slack:
                    await push_handler.upload_to_pypi(tag_name="v1.0.0")

                    # Verify slack message was not sent
                    mock_slack.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_to_pypi_commands_execution_order(self, push_handler: PushHandler) -> None:
        """Test that commands are executed in the correct order."""
        with patch.object(push_handler.runner_handler, "_checkout_worktree") as mock_checkout:
            with patch(
                "webhook_server.libs.handlers.push_handler.run_command", new_callable=AsyncMock
            ) as mock_run_command:
                # Mock successful checkout
                _set_checkout_result(mock_checkout, (True, "/tmp/worktree-path", "", ""))

                # Mock successful all commands
                mock_run_command.side_effect = [
                    (True, "", ""),  # uv build
                    (True, "package-1.0.0.tar.gz", ""),  # ls command
                    (True, "", ""),  # twine check
                    (True, "", ""),  # twine upload
                ]

                await push_handler.upload_to_pypi(tag_name="v1.0.0")

                # Verify commands were called in correct order
                calls = mock_run_command.call_args_list
                # Each call is call(command=..., log_prefix=...)
                # The command string is in the 'command' kwarg
                assert "uv" in calls[0].kwargs["command"]
                assert "build" in calls[0].kwargs["command"]
                assert "ls" in calls[1].kwargs["command"]
                assert "twine check" in calls[2].kwargs["command"]
                assert "twine upload" in calls[3].kwargs["command"]
                assert "package-1.0.0.tar.gz" in calls[3].kwargs["command"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_checkout_with_tag(self, push_handler: PushHandler) -> None:
        """Test that checkout is called with the correct tag."""
        with patch.object(push_handler.runner_handler, "_checkout_worktree") as mock_checkout:
            with patch(
                "webhook_server.libs.handlers.push_handler.run_command", new_callable=AsyncMock
            ) as mock_run_command:
                # Mock successful checkout
                _set_checkout_result(mock_checkout, (True, "/tmp/worktree-path", "", ""))

                # Mock successful build
                mock_run_command.side_effect = [
                    (True, "", ""),  # uv build
                    (True, "package-1.0.0.tar.gz", ""),  # ls command
                    (True, "", ""),  # twine check
                    (True, "", ""),  # twine upload
                ]

                await push_handler.upload_to_pypi(tag_name="v1.0.0")

                # Verify checkout was called with correct tag
                mock_checkout.assert_called_once()
                call_args = mock_checkout.call_args
                assert call_args[1]["checkout"] == "v1.0.0"

    @pytest.mark.asyncio
    async def test_upload_to_pypi_issue_creation_format(self, push_handler: PushHandler) -> None:
        """Test that issues are created with proper format."""
        with patch.object(push_handler.runner_handler, "_checkout_worktree") as mock_checkout:
            with patch.object(push_handler.repository, "create_issue") as mock_create_issue:
                # Mock failed checkout
                _set_checkout_result(
                    mock_checkout,
                    (
                        False,
                        "/tmp/worktree-path",
                        "Clone failed",
                        "Error details",
                    ),
                )

                await push_handler.upload_to_pypi(tag_name="v1.0.0")

                # Verify issue format
                mock_create_issue.assert_called_once()
                call_args = mock_create_issue.call_args

                # The title should be sanitized (newlines replaced, backticks removed)
                # Original: "```\nError details\n\nClone failed\n```"
                # Sanitized: "Error details  Clone failed"
                expected_title = "Error details  Clone failed"
                assert call_args[1]["title"] == expected_title

    @pytest.mark.asyncio
    async def test_upload_to_pypi_slack_message_format(self, push_handler: PushHandler) -> None:
        """Test that slack messages are sent with proper format."""
        with patch.object(push_handler.runner_handler, "_checkout_worktree") as mock_checkout:
            with patch(
                "webhook_server.libs.handlers.push_handler.run_command", new_callable=AsyncMock
            ) as mock_run_command:
                with patch("webhook_server.libs.handlers.push_handler.send_slack_message") as mock_slack:
                    # Mock successful checkout
                    _set_checkout_result(mock_checkout, (True, "/tmp/worktree-path", "", ""))

                    # Mock successful build
                    mock_run_command.side_effect = [
                        (True, "", ""),  # uv build
                        (True, "package-1.0.0.tar.gz", ""),  # ls command
                        (True, "", ""),  # twine check
                        (True, "", ""),  # twine upload
                    ]

                    await push_handler.upload_to_pypi(tag_name="v1.0.0")

                    # Verify slack message format
                    mock_slack.assert_called_once()
                    call_args = mock_slack.call_args

                    assert call_args[1]["webhook_url"] == "https://hooks.slack.com/test"
                    assert "test-repo" in call_args[1]["message"]
                    assert "v1.0.0" in call_args[1]["message"]
                    assert "published to PYPI" in call_args[1]["message"]
                    assert call_args[1]["logger"] == push_handler.logger
                    assert call_args[1]["log_prefix"] == push_handler.log_prefix

    @pytest.mark.asyncio
    async def test_process_push_webhook_data_branch_push_retrigger_enabled(self, push_handler: PushHandler) -> None:
        """Test processing branch push with retrigger enabled."""
        push_handler.hook_data["ref"] = "refs/heads/main"
        push_handler.github_webhook.retrigger_checks_on_base_push = "all"

        with patch.object(
            push_handler, "_retrigger_checks_for_prs_targeting_branch", new_callable=AsyncMock
        ) as mock_retrigger:
            await push_handler.process_push_webhook_data()

            mock_retrigger.assert_called_once_with(branch_name="main")

    @pytest.mark.asyncio
    async def test_process_push_webhook_data_branch_push_retrigger_disabled(self, push_handler: PushHandler) -> None:
        """Test processing branch push with retrigger not configured."""
        push_handler.hook_data["ref"] = "refs/heads/main"
        push_handler.github_webhook.retrigger_checks_on_base_push = None

        with patch.object(
            push_handler, "_retrigger_checks_for_prs_targeting_branch", new_callable=AsyncMock
        ) as mock_retrigger:
            await push_handler.process_push_webhook_data()

            mock_retrigger.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_push_webhook_data_branch_push_feature_branch(self, push_handler: PushHandler) -> None:
        """Test processing push to feature branch."""
        push_handler.hook_data["ref"] = "refs/heads/feature/my-feature"
        push_handler.github_webhook.retrigger_checks_on_base_push = "all"

        with patch.object(
            push_handler, "_retrigger_checks_for_prs_targeting_branch", new_callable=AsyncMock
        ) as mock_retrigger:
            await push_handler.process_push_webhook_data()

            mock_retrigger.assert_called_once_with(branch_name="feature/my-feature")

    @pytest.mark.asyncio
    async def test_retrigger_checks_for_prs_targeting_branch_no_prs(self, push_handler: PushHandler) -> None:
        """Test retrigger when no PRs target the branch."""
        push_handler.github_webhook.current_pull_request_supported_retest = ["tox", "pre-commit"]

        with patch.object(push_handler.repository, "get_pulls") as mock_get_pulls:
            mock_get_pulls.return_value = []

            with patch("asyncio.sleep", new_callable=AsyncMock):
                await push_handler._retrigger_checks_for_prs_targeting_branch(branch_name="main")

            mock_get_pulls.assert_called_once_with(state="open", base="main")

    @pytest.mark.asyncio
    async def test_retrigger_checks_for_prs_targeting_branch_pr_behind(self, push_handler: PushHandler) -> None:
        """Test retrigger for PR with merge state 'behind'."""
        push_handler.github_webhook.current_pull_request_supported_retest = ["tox", "pre-commit"]
        push_handler.github_webhook.retrigger_checks_on_base_push = "all"

        mock_pr = Mock()
        mock_pr.number = 123
        mock_pr.mergeable_state = "behind"
        # Set updated_at to more than 60 seconds ago
        mock_pr.updated_at = datetime.now(UTC) - timedelta(seconds=120)

        with patch.object(push_handler.repository, "get_pulls") as mock_get_pulls:
            mock_get_pulls.return_value = [mock_pr]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(push_handler.runner_handler, "run_retests", new_callable=AsyncMock) as mock_retests:
                    await push_handler._retrigger_checks_for_prs_targeting_branch(branch_name="main")

                    mock_retests.assert_called_once_with(supported_retests=["tox", "pre-commit"], pull_request=mock_pr)

    @pytest.mark.asyncio
    async def test_retrigger_checks_for_prs_targeting_branch_pr_blocked(self, push_handler: PushHandler) -> None:
        """Test retrigger for PR with merge state 'blocked'."""
        push_handler.github_webhook.current_pull_request_supported_retest = ["tox"]
        push_handler.github_webhook.retrigger_checks_on_base_push = "all"

        mock_pr = Mock()
        mock_pr.number = 456
        mock_pr.mergeable_state = "blocked"
        # Set updated_at to more than 60 seconds ago
        mock_pr.updated_at = datetime.now(UTC) - timedelta(seconds=120)

        with patch.object(push_handler.repository, "get_pulls") as mock_get_pulls:
            mock_get_pulls.return_value = [mock_pr]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(push_handler.runner_handler, "run_retests", new_callable=AsyncMock) as mock_retests:
                    await push_handler._retrigger_checks_for_prs_targeting_branch(branch_name="main")

                    mock_retests.assert_called_once_with(supported_retests=["tox"], pull_request=mock_pr)

    @pytest.mark.asyncio
    async def test_retrigger_checks_for_prs_targeting_branch_pr_clean(self, push_handler: PushHandler) -> None:
        """Test that retrigger skips PR with merge state 'clean'."""
        push_handler.github_webhook.current_pull_request_supported_retest = ["tox"]

        mock_pr = Mock()
        mock_pr.number = 789
        mock_pr.mergeable_state = "clean"

        with patch.object(push_handler.repository, "get_pulls") as mock_get_pulls:
            mock_get_pulls.return_value = [mock_pr]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(push_handler.runner_handler, "run_retests", new_callable=AsyncMock) as mock_retests:
                    await push_handler._retrigger_checks_for_prs_targeting_branch(branch_name="main")

                    mock_retests.assert_not_called()

    @pytest.mark.asyncio
    async def test_retrigger_checks_for_prs_targeting_branch_multiple_prs(self, push_handler: PushHandler) -> None:
        """Test retrigger with multiple PRs in different states."""
        push_handler.github_webhook.current_pull_request_supported_retest = ["tox", "pre-commit"]
        push_handler.github_webhook.retrigger_checks_on_base_push = "all"

        mock_pr1 = Mock()
        mock_pr1.number = 100
        mock_pr1.mergeable_state = "behind"
        # Set updated_at to more than 60 seconds ago
        mock_pr1.updated_at = datetime.now(UTC) - timedelta(seconds=120)

        mock_pr2 = Mock()
        mock_pr2.number = 200
        mock_pr2.mergeable_state = "clean"

        mock_pr3 = Mock()
        mock_pr3.number = 300
        mock_pr3.mergeable_state = "blocked"
        # Set updated_at to more than 60 seconds ago
        mock_pr3.updated_at = datetime.now(UTC) - timedelta(seconds=120)

        with patch.object(push_handler.repository, "get_pulls") as mock_get_pulls:
            mock_get_pulls.return_value = [mock_pr1, mock_pr2, mock_pr3]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(push_handler.runner_handler, "run_retests", new_callable=AsyncMock) as mock_retests:
                    await push_handler._retrigger_checks_for_prs_targeting_branch(branch_name="main")

                    # Should be called twice: for PR 100 (behind) and PR 300 (blocked)
                    assert mock_retests.call_count == 2
                    calls = mock_retests.call_args_list
                    assert calls[0].kwargs["pull_request"] == mock_pr1
                    assert calls[1].kwargs["pull_request"] == mock_pr3

    @pytest.mark.asyncio
    async def test_retrigger_checks_for_prs_targeting_branch_no_checks_configured(
        self, push_handler: PushHandler
    ) -> None:
        """Test retrigger when no checks are configured."""
        push_handler.github_webhook.current_pull_request_supported_retest = []

        mock_pr = Mock()
        mock_pr.number = 123
        mock_pr.mergeable_state = "behind"
        # Set updated_at to more than 60 seconds ago
        mock_pr.updated_at = datetime.now(UTC) - timedelta(seconds=120)

        with patch.object(push_handler.repository, "get_pulls") as mock_get_pulls:
            mock_get_pulls.return_value = [mock_pr]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(push_handler.runner_handler, "run_retests", new_callable=AsyncMock) as mock_retests:
                    await push_handler._retrigger_checks_for_prs_targeting_branch(branch_name="main")

                    mock_retests.assert_not_called()

    @pytest.mark.asyncio
    async def test_retrigger_checks_waits_for_github(self, push_handler: PushHandler) -> None:
        """Test that retrigger waits 30 seconds for GitHub to update merge states."""
        push_handler.github_webhook.current_pull_request_supported_retest = ["tox"]

        with patch.object(push_handler.repository, "get_pulls") as mock_get_pulls:
            mock_get_pulls.return_value = []

            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await push_handler._retrigger_checks_for_prs_targeting_branch(branch_name="main")

                mock_sleep.assert_called_once_with(30)

    @pytest.mark.asyncio
    async def test_retrigger_checks_with_specific_checks_list(self, push_handler: PushHandler) -> None:
        """Test retrigger with specific checks list."""
        push_handler.github_webhook.current_pull_request_supported_retest = ["tox", "pre-commit", "build-container"]
        push_handler.github_webhook.retrigger_checks_on_base_push = ["tox", "pre-commit"]

        mock_pr = Mock()
        mock_pr.number = 123
        mock_pr.mergeable_state = "behind"
        # Set updated_at to more than 60 seconds ago
        mock_pr.updated_at = datetime.now(UTC) - timedelta(seconds=120)

        with patch.object(push_handler.repository, "get_pulls") as mock_get_pulls:
            mock_get_pulls.return_value = [mock_pr]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(push_handler.runner_handler, "run_retests", new_callable=AsyncMock) as mock_retests:
                    await push_handler._retrigger_checks_for_prs_targeting_branch(branch_name="main")

                    # Should only run configured checks, not all available
                    mock_retests.assert_called_once_with(supported_retests=["tox", "pre-commit"], pull_request=mock_pr)

    @pytest.mark.asyncio
    async def test_retrigger_checks_with_nonexistent_checks(self, push_handler: PushHandler) -> None:
        """Test retrigger when configured checks don't exist."""
        push_handler.github_webhook.current_pull_request_supported_retest = ["tox", "pre-commit"]
        push_handler.github_webhook.retrigger_checks_on_base_push = ["nonexistent-check"]

        mock_pr = Mock()
        mock_pr.number = 123
        mock_pr.mergeable_state = "behind"
        # Set updated_at to more than 60 seconds ago
        mock_pr.updated_at = datetime.now(UTC) - timedelta(seconds=120)

        with patch.object(push_handler.repository, "get_pulls") as mock_get_pulls:
            mock_get_pulls.return_value = [mock_pr]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(push_handler.runner_handler, "run_retests", new_callable=AsyncMock) as mock_retests:
                    await push_handler._retrigger_checks_for_prs_targeting_branch(branch_name="main")

                    # Should not run any checks since none match
                    mock_retests.assert_not_called()

    @pytest.mark.asyncio
    async def test_retrigger_checks_with_partial_match(self, push_handler: PushHandler) -> None:
        """Test retrigger with some configured checks matching available checks."""
        push_handler.github_webhook.current_pull_request_supported_retest = ["tox", "pre-commit"]
        push_handler.github_webhook.retrigger_checks_on_base_push = ["tox", "nonexistent-check"]

        mock_pr = Mock()
        mock_pr.number = 123
        mock_pr.mergeable_state = "behind"
        # Set updated_at to more than 60 seconds ago
        mock_pr.updated_at = datetime.now(UTC) - timedelta(seconds=120)

        with patch.object(push_handler.repository, "get_pulls") as mock_get_pulls:
            mock_get_pulls.return_value = [mock_pr]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(push_handler.runner_handler, "run_retests", new_callable=AsyncMock) as mock_retests:
                    await push_handler._retrigger_checks_for_prs_targeting_branch(branch_name="main")

                    # Should only run checks that match
                    mock_retests.assert_called_once_with(supported_retests=["tox"], pull_request=mock_pr)

    @pytest.mark.asyncio
    async def test_retrigger_checks_skips_recently_updated_pr(self, push_handler: PushHandler) -> None:
        """Test that retrigger skips PR that was updated within the last minute."""
        push_handler.github_webhook.current_pull_request_supported_retest = ["tox", "pre-commit"]
        push_handler.github_webhook.retrigger_checks_on_base_push = "all"

        mock_pr = Mock()
        mock_pr.number = 123
        mock_pr.mergeable_state = "behind"
        # Set updated_at to less than 60 seconds ago (30 seconds)
        mock_pr.updated_at = datetime.now(UTC) - timedelta(seconds=30)

        with patch.object(push_handler.repository, "get_pulls") as mock_get_pulls:
            mock_get_pulls.return_value = [mock_pr]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(push_handler.runner_handler, "run_retests", new_callable=AsyncMock) as mock_retests:
                    await push_handler._retrigger_checks_for_prs_targeting_branch(branch_name="main")

                    # Should not trigger since PR was recently updated
                    mock_retests.assert_not_called()

    @pytest.mark.asyncio
    async def test_retrigger_checks_with_empty_list(self, push_handler: PushHandler) -> None:
        """Test retrigger disabled with empty list."""
        push_handler.github_webhook.current_pull_request_supported_retest = ["tox", "pre-commit"]
        push_handler.github_webhook.retrigger_checks_on_base_push = []

        mock_pr = Mock()
        mock_pr.number = 123
        mock_pr.mergeable_state = "behind"
        # Set updated_at to more than 60 seconds ago
        mock_pr.updated_at = datetime.now(UTC) - timedelta(seconds=120)

        with patch.object(push_handler.repository, "get_pulls") as mock_get_pulls:
            mock_get_pulls.return_value = [mock_pr]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(push_handler.runner_handler, "run_retests", new_callable=AsyncMock) as mock_retests:
                    await push_handler._retrigger_checks_for_prs_targeting_branch(branch_name="main")

                    # Empty list is treated as disabled - should not trigger
                    # But the current implementation will process the PR since the check happens at webhook level
                    # This test validates current behavior
                    mock_retests.assert_not_called()

    @pytest.mark.asyncio
    async def test_retrigger_checks_for_prs_with_unknown_merge_state(self, push_handler: PushHandler) -> None:
        """Test that PRs with unknown merge state are skipped with warning.

        Note: These tests mock run_retests_from_config to exercise higher-level behavior
        and exception propagation, while run_retests is tested directly in other tests.
        """
        push_handler.github_webhook.current_pull_request_supported_retest = ["tox"]

        mock_pr = Mock()
        mock_pr.number = 999
        mock_pr.mergeable_state = "unknown"

        with patch.object(push_handler.repository, "get_pulls") as mock_get_pulls:
            mock_get_pulls.return_value = [mock_pr]
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(
                    push_handler.runner_handler, "run_retests_from_config", new_callable=AsyncMock
                ) as mock_retests:
                    await push_handler._retrigger_checks_for_prs_targeting_branch(branch_name="main")
                    mock_retests.assert_not_called()
                    push_handler.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_retrigger_checks_for_prs_with_none_merge_state(self, push_handler: PushHandler) -> None:
        """Test that PRs with None merge state are skipped with warning.

        Note: These tests mock run_retests_from_config to exercise higher-level behavior
        and exception propagation, while run_retests is tested directly in other tests.
        """
        push_handler.github_webhook.current_pull_request_supported_retest = ["tox"]

        mock_pr = Mock()
        mock_pr.number = 888
        mock_pr.mergeable_state = None

        with patch.object(push_handler.repository, "get_pulls") as mock_get_pulls:
            mock_get_pulls.return_value = [mock_pr]
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(
                    push_handler.runner_handler, "run_retests_from_config", new_callable=AsyncMock
                ) as mock_retests:
                    await push_handler._retrigger_checks_for_prs_targeting_branch(branch_name="main")
                    mock_retests.assert_not_called()
                    push_handler.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_retrigger_checks_continues_on_exception(self, push_handler: PushHandler) -> None:
        """Test that exception in one PR doesn't stop processing others.

        Note: These tests mock run_retests_from_config to exercise higher-level behavior
        and exception propagation, while run_retests is tested directly in other tests.
        """
        push_handler.github_webhook.current_pull_request_supported_retest = ["tox"]
        push_handler.github_webhook.retrigger_checks_on_base_push = "all"

        mock_pr1 = Mock()
        mock_pr1.number = 100
        mock_pr1.mergeable_state = "behind"
        # Set updated_at to more than 60 seconds ago
        mock_pr1.updated_at = datetime.now(UTC) - timedelta(seconds=120)

        mock_pr2 = Mock()
        mock_pr2.number = 200
        mock_pr2.mergeable_state = "behind"
        # Set updated_at to more than 60 seconds ago
        mock_pr2.updated_at = datetime.now(UTC) - timedelta(seconds=120)

        with patch.object(push_handler.repository, "get_pulls") as mock_get_pulls:
            mock_get_pulls.return_value = [mock_pr1, mock_pr2]
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with patch.object(
                    push_handler.runner_handler, "run_retests_from_config", new_callable=AsyncMock
                ) as mock_retests:
                    # First call raises exception, second succeeds
                    mock_retests.side_effect = [Exception("Test error"), True]
                    await push_handler._retrigger_checks_for_prs_targeting_branch(branch_name="main")
                    # Both PRs should be attempted
                    assert mock_retests.call_count == 2
                    # Exception should be logged
                    push_handler.logger.exception.assert_called()
