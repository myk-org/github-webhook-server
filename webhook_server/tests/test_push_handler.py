"""Tests for webhook_server.libs.handlers.push_handler module."""

import os
from unittest.mock import AsyncMock, Mock, patch

import pytest

from webhook_server.libs.handlers.push_handler import PushHandler


class TestPushHandler:
    """Test suite for PushHandler class."""

    @pytest.fixture
    def mock_github_webhook(self, tmp_path) -> Mock:
        """Create a mock GithubWebhook instance."""
        mock_webhook = Mock()
        mock_webhook.hook_data = {"ref": "refs/tags/v1.0.0"}
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.repository.full_name = "test-owner/test-repo"
        mock_webhook.pypi = {"token": "test-token"}
        mock_webhook.build_and_push_container = True
        mock_webhook.container_release = True
        mock_webhook.clone_repo_dir = str(tmp_path / "test-repo")
        mock_webhook.slack_webhook_url = "https://hooks.slack.com/test"
        mock_webhook.repository_name = "test-repo"
        mock_webhook.send_slack_message = Mock()
        mock_webhook.container_repository_username = "test-user"  # Always a string
        mock_webhook.container_repository_password = "test-password"  # Always a string # pragma: allowlist secret
        mock_webhook.token = "test-token"  # Always a string
        # Mock unified_api for async operations
        mock_webhook.unified_api = Mock()
        mock_webhook.unified_api.create_issue_on_repository = AsyncMock()
        mock_webhook.unified_api.send_slack_message_async = AsyncMock()
        return mock_webhook

    @pytest.fixture
    def push_handler(self, mock_github_webhook: Mock) -> PushHandler:
        """Create a PushHandler instance with mocked dependencies."""
        return PushHandler(mock_github_webhook)

    @pytest.mark.asyncio
    async def test_process_push_webhook_data_with_tag_and_pypi(self, push_handler: PushHandler) -> None:
        """Test processing push webhook data with tag and pypi enabled."""
        with patch.object(push_handler, "upload_to_pypi") as mock_upload:
            with patch.object(push_handler.runner_handler, "run_build_container") as mock_build:
                await push_handler.process_push_webhook_data()

                mock_upload.assert_called_once_with(tag_name="v1.0.0")
                mock_build.assert_called_once_with(push=True, set_check=False, tag="v1.0.0")

    @pytest.mark.asyncio
    async def test_process_push_webhook_data_with_tag_no_pypi(self, push_handler: PushHandler) -> None:
        """Test processing push webhook data with tag but no pypi."""
        push_handler.github_webhook.pypi = {}  # Empty dict instead of None

        with patch.object(push_handler, "upload_to_pypi") as mock_upload:
            with patch.object(push_handler.runner_handler, "run_build_container") as mock_build:
                await push_handler.process_push_webhook_data()

                mock_upload.assert_not_called()
                mock_build.assert_called_once_with(push=True, set_check=False, tag="v1.0.0")

    @pytest.mark.asyncio
    async def test_process_push_webhook_data_with_tag_no_container(self, push_handler: PushHandler) -> None:
        """Test processing push webhook data with tag but no container build."""
        push_handler.github_webhook.build_and_push_container = False

        with patch.object(push_handler, "upload_to_pypi") as mock_upload:
            with patch.object(push_handler.runner_handler, "run_build_container") as mock_build:
                await push_handler.process_push_webhook_data()

                mock_upload.assert_called_once_with(tag_name="v1.0.0")
                mock_build.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_push_webhook_data_with_tag_no_container_release(self, push_handler: PushHandler) -> None:
        """Test processing push webhook data with tag but no container release."""
        push_handler.github_webhook.container_release = False

        with patch.object(push_handler, "upload_to_pypi") as mock_upload:
            with patch.object(push_handler.runner_handler, "run_build_container") as mock_build:
                await push_handler.process_push_webhook_data()

                mock_upload.assert_called_once_with(tag_name="v1.0.0")
                mock_build.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_push_webhook_data_no_tag(self, push_handler: PushHandler) -> None:
        """Test processing push webhook data without tag."""
        push_handler.hook_data["ref"] = "refs/heads/main"

        with patch.object(push_handler, "upload_to_pypi") as mock_upload:
            with patch.object(push_handler.runner_handler, "run_build_container") as mock_build:
                await push_handler.process_push_webhook_data()

                mock_upload.assert_not_called()
                mock_build.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_push_webhook_data_tag_with_slash(self, push_handler: PushHandler) -> None:
        """Test processing push webhook data with tag containing slash."""
        push_handler.hook_data["ref"] = "refs/tags/release/v1.0.0"

        with patch.object(push_handler, "upload_to_pypi") as mock_upload:
            with patch.object(push_handler.runner_handler, "run_build_container") as mock_build:
                await push_handler.process_push_webhook_data()

                mock_upload.assert_called_once_with(tag_name="release/v1.0.0")
                mock_build.assert_called_once_with(push=True, set_check=False, tag="release/v1.0.0")

    @pytest.mark.asyncio
    async def test_upload_to_pypi_success(self, push_handler: PushHandler) -> None:
        """Test successful upload to pypi."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with patch("webhook_server.libs.handlers.push_handler.run_command") as mock_run_command:
                with patch("webhook_server.libs.handlers.push_handler.uuid4") as mock_uuid:
                    with patch("webhook_server.libs.handlers.push_handler.Path") as mock_path:
                        with patch("os.open") as mock_os_open:
                            with patch("os.fdopen", create=True) as mock_fdopen:
                                with patch("os.remove") as mock_remove:
                                    # Mock successful clone
                                    mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                                    # Mock successful build (no find command anymore)
                                    mock_run_command.side_effect = [
                                        (True, "", ""),  # uv build
                                        (True, "", ""),  # twine check
                                        (True, "", ""),  # twine upload
                                    ]

                                    # Mock Path.glob() to return tar.gz file
                                    mock_tarball = Mock()
                                    mock_tarball.name = "package-1.0.0.tar.gz"
                                    mock_path.return_value.glob.return_value = [mock_tarball]

                                    mock_uuid.return_value = "test-uuid"

                                    # Mock os.open to return a fake file descriptor
                                    mock_os_open.return_value = 3

                                    # Mock os.fdopen to return a mock file object
                                    mock_file = Mock()
                                    mock_file.__enter__ = Mock(return_value=mock_file)
                                    mock_file.__exit__ = Mock(return_value=False)
                                    mock_fdopen.return_value = mock_file

                                    await push_handler.upload_to_pypi(tag_name="v1.0.0")

                                    # Verify clone was called
                                    mock_prepare.assert_called_once()

                                    # Verify build command was called (3 times now: build, check, upload)
                                    assert mock_run_command.call_count == 3

                                # Verify os.open was called with atomic creation flags and secure permissions
                                expected_flags = os.O_CREAT | os.O_WRONLY | os.O_EXCL
                                if hasattr(os, "O_NOFOLLOW"):
                                    expected_flags |= os.O_NOFOLLOW
                                # Get the actual path used from mock_os_open call
                                actual_pypirc_path = mock_os_open.call_args[0][0]
                                assert actual_pypirc_path.endswith("test-repo-test-uuid/.pypirc")
                                assert mock_os_open.call_args[0][1] == expected_flags
                                assert mock_os_open.call_args[0][2] == 0o600

                                # Verify os.fdopen was called with the file descriptor
                                mock_fdopen.assert_called_once_with(3, "w", encoding="utf-8")

                                # Verify .pypirc was cleaned up after successful upload
                                assert mock_remove.call_args[0][0].endswith("test-repo-test-uuid/.pypirc")

                                # Verify slack message was sent via unified_api
                                push_handler.github_webhook.unified_api.send_slack_message_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upload_to_pypi_clone_failure(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when clone fails."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            # Mock failed clone
            mock_prepare.return_value.__aenter__.return_value = (False, "Clone failed", "Error")

            await push_handler.upload_to_pypi(tag_name="v1.0.0")

            # Verify issue was created via unified_api
            push_handler.github_webhook.unified_api.create_issue_on_repository.assert_called_once()
            call_args = push_handler.github_webhook.unified_api.create_issue_on_repository.call_args
            assert "Clone failed" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_build_failure(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when build fails."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with patch("webhook_server.libs.handlers.push_handler.run_command") as mock_run_command:
                # Mock successful clone
                mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                # Mock failed build
                mock_run_command.return_value = (False, "Build failed", "Error")

                await push_handler.upload_to_pypi(tag_name="v1.0.0")

                # Verify issue was created via unified_api
                push_handler.github_webhook.unified_api.create_issue_on_repository.assert_called_once()
                call_args = push_handler.github_webhook.unified_api.create_issue_on_repository.call_args
                assert "Build failed" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_ls_failure(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when no tar.gz file found."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with patch("webhook_server.libs.handlers.push_handler.run_command") as mock_run_command:
                with patch("webhook_server.libs.handlers.push_handler.Path") as mock_path:
                    # Mock successful clone
                    mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                    # Mock successful build
                    mock_run_command.side_effect = [
                        (True, "", ""),  # uv build
                    ]

                    # Mock Path.glob() to return empty list (no tar.gz found)
                    mock_path.return_value.glob.return_value = []

                    await push_handler.upload_to_pypi(tag_name="v1.0.0")

                    # Verify issue was created via unified_api
                    push_handler.github_webhook.unified_api.create_issue_on_repository.assert_called_once()
                    call_args = push_handler.github_webhook.unified_api.create_issue_on_repository.call_args
                    assert "No .tar.gz file found" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_twine_check_failure(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when twine check fails."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with patch("webhook_server.libs.handlers.push_handler.run_command") as mock_run_command:
                with patch("webhook_server.libs.handlers.push_handler.Path") as mock_path:
                    with patch("os.open") as mock_os_open:
                        with patch("os.fdopen", create=True) as mock_fdopen:
                            # Mock successful clone
                            mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                            # Mock successful build, failed twine check
                            mock_run_command.side_effect = [
                                (True, "", ""),  # uv build
                                (False, "twine check failed", "Error"),  # twine check
                            ]

                            # Mock Path.glob() to return tar.gz file
                            mock_tarball = Mock()
                            mock_tarball.name = "package-1.0.0.tar.gz"
                            mock_path.return_value.glob.return_value = [mock_tarball]

                            # Mock os.open to return a fake file descriptor
                            mock_os_open.return_value = 3

                            # Mock os.fdopen to return a mock file object
                            mock_file = Mock()
                            mock_file.__enter__ = Mock(return_value=mock_file)
                            mock_file.__exit__ = Mock(return_value=False)
                            mock_fdopen.return_value = mock_file

                            await push_handler.upload_to_pypi(tag_name="v1.0.0")

                            # Verify issue was created
                            push_handler.github_webhook.unified_api.create_issue_on_repository.assert_called_once()
                            call_args = push_handler.github_webhook.unified_api.create_issue_on_repository.call_args
                            assert "twine check failed" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_twine_upload_failure(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when twine upload fails."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with patch("webhook_server.libs.handlers.push_handler.run_command") as mock_run_command:
                with patch("webhook_server.libs.handlers.push_handler.Path") as mock_path:
                    with patch("os.open") as mock_os_open:
                        with patch("os.fdopen", create=True) as mock_fdopen:
                            # Mock successful clone
                            mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                            # Mock successful build and twine check, failed twine upload
                            mock_run_command.side_effect = [
                                (True, "", ""),  # uv build
                                (True, "", ""),  # twine check
                                (False, "twine upload failed", "Error"),  # twine upload
                            ]

                            # Mock Path.glob() to return tar.gz file
                            mock_tarball = Mock()
                            mock_tarball.name = "package-1.0.0.tar.gz"
                            mock_path.return_value.glob.return_value = [mock_tarball]

                            # Mock os.open to return a fake file descriptor
                            mock_os_open.return_value = 3

                            # Mock os.fdopen to return a mock file object
                            mock_file = Mock()
                            mock_file.__enter__ = Mock(return_value=mock_file)
                            mock_file.__exit__ = Mock(return_value=False)
                            mock_fdopen.return_value = mock_file

                            await push_handler.upload_to_pypi(tag_name="v1.0.0")

                            # Verify issue was created
                            push_handler.github_webhook.unified_api.create_issue_on_repository.assert_called_once()
                            call_args = push_handler.github_webhook.unified_api.create_issue_on_repository.call_args
                            assert "twine upload failed" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_success_no_slack(self, push_handler: PushHandler) -> None:
        """Test successful upload to pypi without slack webhook."""
        push_handler.github_webhook.slack_webhook_url = ""  # Empty string instead of None

        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with patch("webhook_server.libs.handlers.push_handler.run_command") as mock_run_command:
                with patch("webhook_server.libs.handlers.push_handler.uuid4") as mock_uuid:
                    with patch("webhook_server.libs.handlers.push_handler.Path") as mock_path:
                        with patch("os.open") as mock_os_open:
                            with patch("os.fdopen", create=True) as mock_fdopen:
                                with patch("os.remove"):
                                    # Mock successful clone
                                    mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                                    # Mock successful build (no find command anymore)
                                    mock_run_command.side_effect = [
                                        (True, "", ""),  # uv build
                                        (True, "", ""),  # twine check
                                        (True, "", ""),  # twine upload
                                    ]

                                    # Mock Path.glob() to return tar.gz file
                                    mock_tarball = Mock()
                                    mock_tarball.name = "package-1.0.0.tar.gz"
                                    mock_path.return_value.glob.return_value = [mock_tarball]

                                    mock_uuid.return_value = "test-uuid"

                                    # Mock os.open to return a fake file descriptor
                                    mock_os_open.return_value = 3

                                    # Mock os.fdopen to return a mock file object
                                    mock_file = Mock()
                                    mock_file.__enter__ = Mock(return_value=mock_file)
                                    mock_file.__exit__ = Mock(return_value=False)
                                    mock_fdopen.return_value = mock_file

                                    await push_handler.upload_to_pypi(tag_name="v1.0.0")

                                    # Verify slack message was not sent via unified_api
                                    push_handler.github_webhook.unified_api.send_slack_message_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upload_to_pypi_commands_execution_order(self, push_handler: PushHandler) -> None:
        """Test that commands are executed in the correct order."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with patch("webhook_server.libs.handlers.push_handler.run_command") as mock_run_command:
                with patch("webhook_server.libs.handlers.push_handler.uuid4") as mock_uuid:
                    with patch("webhook_server.libs.handlers.push_handler.Path") as mock_path:
                        with patch("os.open") as mock_os_open:
                            with patch("os.fdopen", create=True) as mock_fdopen:
                                with patch("os.remove"):
                                    # Mock successful clone
                                    mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                                    # Mock successful all commands (no find command anymore)
                                    mock_run_command.side_effect = [
                                        (True, "", ""),  # uv build
                                        (True, "", ""),  # twine check
                                        (True, "", ""),  # twine upload
                                    ]

                                    # Mock Path.glob() to return tar.gz file
                                    mock_tarball = Mock()
                                    mock_tarball.name = "package-1.0.0.tar.gz"
                                    mock_path.return_value.glob.return_value = [mock_tarball]

                                    mock_uuid.return_value = "test-uuid"

                                    # Mock os.open to return a fake file descriptor
                                    mock_os_open.return_value = 3

                                    # Mock os.fdopen to return a mock file object
                                    mock_file = Mock()
                                    mock_file.__enter__ = Mock(return_value=mock_file)
                                    mock_file.__exit__ = Mock(return_value=False)
                                    mock_fdopen.return_value = mock_file

                                    await push_handler.upload_to_pypi(tag_name="v1.0.0")

                                    # Verify commands were called in correct order
                                    calls = mock_run_command.call_args_list
                                    # Each call is call(command=..., log_prefix=...)
                                    # The command string is in the 'command' kwarg
                                    assert len(calls) == 3
                                    assert "uv" in calls[0].kwargs["command"]
                                    assert "build" in calls[0].kwargs["command"]
                                    assert "twine check" in calls[1].kwargs["command"]
                                    assert "twine upload" in calls[2].kwargs["command"]
                                    assert "package-1.0.0.tar.gz" in calls[2].kwargs["command"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_unique_clone_directory(self, push_handler: PushHandler) -> None:
        """Test that each upload uses a unique clone directory."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with patch("webhook_server.libs.handlers.push_handler.run_command") as mock_run_command:
                with patch("webhook_server.libs.handlers.push_handler.uuid4") as mock_uuid:
                    with patch("webhook_server.libs.handlers.push_handler.Path") as mock_path:
                        with patch("os.open") as mock_os_open:
                            with patch("os.fdopen", create=True) as mock_fdopen:
                                with patch("os.remove"):
                                    # Mock successful clone
                                    mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                                    # Mock successful build (no find command anymore)
                                    mock_run_command.side_effect = [
                                        (True, "", ""),  # uv build
                                        (True, "", ""),  # twine check
                                        (True, "", ""),  # twine upload
                                    ]

                                    # Mock Path.glob() to return tar.gz file
                                    mock_tarball = Mock()
                                    mock_tarball.name = "package-1.0.0.tar.gz"
                                    mock_path.return_value.glob.return_value = [mock_tarball]

                                    mock_uuid.return_value = "test-uuid"

                                    # Mock os.open to return a fake file descriptor
                                    mock_os_open.return_value = 3

                                    # Mock os.fdopen to return a mock file object
                                    mock_file = Mock()
                                    mock_file.__enter__ = Mock(return_value=mock_file)
                                    mock_file.__exit__ = Mock(return_value=False)
                                    mock_fdopen.return_value = mock_file

                                    await push_handler.upload_to_pypi(tag_name="v1.0.0")

                                    # Verify clone directory includes UUID
                                    mock_prepare.assert_called_once()
                                    call_args = mock_prepare.call_args
                                    assert "test-uuid" in call_args[1]["clone_repo_dir"]
                                    assert call_args[1]["clone_repo_dir"].endswith("test-repo-test-uuid")

    @pytest.mark.asyncio
    async def test_upload_to_pypi_issue_creation_format(self, push_handler: PushHandler) -> None:
        """Test that issues are created with proper format."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            # Mock failed clone
            mock_prepare.return_value.__aenter__.return_value = (False, "Clone failed", "Error details")

            await push_handler.upload_to_pypi(tag_name="v1.0.0")

            # Verify issue format
            push_handler.github_webhook.unified_api.create_issue_on_repository.assert_called_once()
            call_args = push_handler.github_webhook.unified_api.create_issue_on_repository.call_args

            # The title should be the full formatted error text from get_check_run_text
            expected_title = "```\nError details\n\nClone failed\n```"
            assert call_args[1]["title"] == expected_title

    @pytest.mark.asyncio
    async def test_upload_to_pypi_slack_message_format(self, push_handler: PushHandler) -> None:
        """Test that slack messages are sent with proper format."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with patch("webhook_server.libs.handlers.push_handler.run_command") as mock_run_command:
                with patch("webhook_server.libs.handlers.push_handler.uuid4") as mock_uuid:
                    with patch("webhook_server.libs.handlers.push_handler.Path") as mock_path:
                        with patch("os.open") as mock_os_open:
                            with patch("os.fdopen", create=True) as mock_fdopen:
                                with patch("os.remove"):
                                    # Mock successful clone
                                    mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                                    # Mock successful build
                                    mock_run_command.side_effect = [
                                        (True, "", ""),  # uv build
                                        (True, "", ""),  # twine check
                                        (True, "", ""),  # twine upload
                                    ]

                                    # Mock Path.glob() to return tar.gz file
                                    mock_tarball = Mock()
                                    mock_tarball.name = "package-1.0.0.tar.gz"
                                    mock_path.return_value.glob.return_value = [mock_tarball]

                                    mock_uuid.return_value = "test-uuid"

                                    # Mock os.open to return a fake file descriptor
                                    mock_os_open.return_value = 3

                                    # Mock os.fdopen to return a mock file object
                                    mock_file = Mock()
                                    mock_file.__enter__ = Mock(return_value=mock_file)
                                    mock_file.__exit__ = Mock(return_value=False)
                                    mock_fdopen.return_value = mock_file

                                    await push_handler.upload_to_pypi(tag_name="v1.0.0")

                                # Verify slack message format via unified_api
                                push_handler.github_webhook.unified_api.send_slack_message_async.assert_awaited_once()
                                call_args = push_handler.github_webhook.unified_api.send_slack_message_async.call_args

                                # Verify parameters passed to send_slack_message_async
                                assert call_args.kwargs["webhook_url"] == "https://hooks.slack.com/test"
                                assert "test-repo" in call_args.kwargs["message"]
                                assert "v1.0.0" in call_args.kwargs["message"]
                                assert "published to PYPI" in call_args.kwargs["message"]
                                assert (
                                    call_args.kwargs["send_slack_message_func"]
                                    == push_handler.github_webhook.send_slack_message
                                )

    @pytest.mark.asyncio
    async def test_upload_to_pypi_missing_token(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when PyPI token is missing."""
        # Set pypi config without token
        push_handler.github_webhook.pypi = {}

        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with patch("webhook_server.libs.handlers.push_handler.run_command") as mock_run_command:
                with patch("webhook_server.libs.handlers.push_handler.Path") as mock_path:
                    # Mock successful clone
                    mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                    # Mock successful build
                    mock_run_command.side_effect = [
                        (True, "", ""),  # uv build
                    ]

                    # Mock Path.glob() to return tar.gz file
                    mock_tarball = Mock()
                    mock_tarball.name = "package-1.0.0.tar.gz"
                    mock_path.return_value.glob.return_value = [mock_tarball]

                    await push_handler.upload_to_pypi(tag_name="v1.0.0")

                    # Verify issue was created for missing token
                    push_handler.github_webhook.unified_api.create_issue_on_repository.assert_called_once()
                    call_args = push_handler.github_webhook.unified_api.create_issue_on_repository.call_args
                    assert "PyPI token is not configured" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_preexisting_pypirc(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when .pypirc file already exists."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with patch("webhook_server.libs.handlers.push_handler.run_command") as mock_run_command:
                with patch("webhook_server.libs.handlers.push_handler.Path") as mock_path:
                    with patch("os.open") as mock_os_open:
                        # Mock successful clone
                        mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                        # Mock successful build
                        mock_run_command.side_effect = [
                            (True, "", ""),  # uv build
                        ]

                        # Mock Path.glob() to return tar.gz file
                        mock_tarball = Mock()
                        mock_tarball.name = "package-1.0.0.tar.gz"
                        mock_path.return_value.glob.return_value = [mock_tarball]

                        # Simulate FileExistsError when creating .pypirc
                        mock_os_open.side_effect = FileExistsError("File exists")

                        await push_handler.upload_to_pypi(tag_name="v1.0.0")

                        # Verify issue was created for pre-existing file
                        push_handler.github_webhook.unified_api.create_issue_on_repository.assert_called_once()
                        call_args = push_handler.github_webhook.unified_api.create_issue_on_repository.call_args
                        assert ".pypirc file already exists" in call_args[1]["title"]
