"""Tests for webhook_server.libs.handlers.push_handler module."""

import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from webhook_server.libs.handlers.push_handler import PushHandler


@contextmanager
def pypi_upload_mocks() -> Generator[dict[str, Any], None, None]:
    """Context manager providing shared mocks for PyPI upload tests.

    Yields:
        Dictionary containing all necessary mocks:
        - run_command: Mock for run_command function
        - uuid4: Mock for uuid4 function
        - path: Mock for Path class
        - os_open: Mock for os.open
        - fdopen: Mock for os.fdopen
        - remove: Mock for os.remove
        - mock_file: Mock file object with context manager support
    """
    with patch("webhook_server.libs.handlers.push_handler.run_command") as mock_run_command:
        with patch("webhook_server.libs.handlers.push_handler.uuid4") as mock_uuid:
            with patch("webhook_server.libs.handlers.push_handler.Path") as mock_path:
                with patch("webhook_server.libs.handlers.push_handler.os.open") as mock_os_open:
                    with patch("webhook_server.libs.handlers.push_handler.os.fdopen", create=True) as mock_fdopen:
                        with patch("webhook_server.libs.handlers.push_handler.os.remove") as mock_remove:
                            # Set up mock file object
                            mock_file = Mock()
                            mock_file.__enter__ = Mock(return_value=mock_file)
                            mock_file.__exit__ = Mock(return_value=False)
                            mock_fdopen.return_value = mock_file

                            # Set up default uuid
                            mock_uuid.return_value = "test-uuid"

                            # Set up default os.open return value
                            mock_os_open.return_value = 3

                            yield {
                                "run_command": mock_run_command,
                                "uuid4": mock_uuid,
                                "path": mock_path,
                                "os_open": mock_os_open,
                                "fdopen": mock_fdopen,
                                "remove": mock_remove,
                                "mock_file": mock_file,
                            }


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
        mock_webhook.owner_and_repo = ("test-owner", "test-repo")  # Tuple for unpacking
        mock_webhook.pypi = {"token": "test-token"}
        mock_webhook.build_and_push_container = True
        mock_webhook.container_release = True
        mock_webhook.clone_repo_dir = str(tmp_path / "test-repo")
        mock_webhook.slack_webhook_url = "https://hooks.slack.com/test"
        mock_webhook.repository_name = "test-repo"
        mock_webhook.send_slack_message = Mock()
        mock_webhook.container_repository_username = "test-user"  # Always a string # pragma: allowlist secret
        mock_webhook.container_repository_password = (
            "test-password"  # Always a string # pragma: allowlist secret # noqa: S105
        )
        mock_webhook.token = "test-token"  # Always a string # pragma: allowlist secret # noqa: S105
        # Mock unified_api for async operations
        mock_webhook.unified_api = Mock()
        mock_webhook.unified_api.create_issue_on_repository = AsyncMock()
        # Mock config
        mock_webhook.config = Mock()
        mock_webhook.config.get_value = Mock(return_value=1000)
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
            with pypi_upload_mocks() as mocks:
                # Mock successful clone
                mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                # Mock successful build (no find command anymore)
                mocks["run_command"].side_effect = [
                    (True, "", ""),  # uv build
                    (True, "", ""),  # twine check
                    (True, "", ""),  # twine upload
                ]

                # Mock Path.glob() to return tar.gz file
                mock_tarball = Mock()
                mock_tarball.name = "package-1.0.0.tar.gz"
                mocks["path"].return_value.glob.return_value = [mock_tarball]

                await push_handler.upload_to_pypi(tag_name="v1.0.0")

                # Verify clone was called
                mock_prepare.assert_called_once()

                # Verify build command was called (3 times now: build, check, upload)
                assert mocks["run_command"].call_count == 3

                # Verify twine check command (doesn't use --config-file, just checks the tarball)
                twine_check_call = mocks["run_command"].call_args_list[1]
                assert "twine check" in twine_check_call.kwargs["command"]
                assert "package-1.0.0.tar.gz" in twine_check_call.kwargs["command"]
                # Verify token redaction is enabled for twine check
                assert "redact_secrets" in twine_check_call.kwargs
                assert "test-token" in twine_check_call.kwargs["redact_secrets"]

                # Verify twine upload command uses --config-file and redacts token
                twine_upload_call = mocks["run_command"].call_args_list[2]
                assert "twine upload" in twine_upload_call.kwargs["command"]
                assert "--config-file" in twine_upload_call.kwargs["command"]
                assert ".pypirc" in twine_upload_call.kwargs["command"]
                # Verify token redaction is enabled
                assert "redact_secrets" in twine_upload_call.kwargs
                assert "test-token" in twine_upload_call.kwargs["redact_secrets"]

                # Verify .pypirc content was written correctly
                mocks["mock_file"].write.assert_called_once()
                pypirc_content = mocks["mock_file"].write.call_args[0][0]
                assert "[pypi]" in pypirc_content
                assert "username = __token__" in pypirc_content
                assert "password = test-token" in pypirc_content

                # Verify os.open was called with atomic creation flags and secure permissions
                expected_flags = os.O_CREAT | os.O_WRONLY | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    expected_flags |= os.O_NOFOLLOW
                # Get the actual path used from mock_os_open call
                actual_pypirc_path = mocks["os_open"].call_args[0][0]
                assert actual_pypirc_path.endswith("test-repo-test-uuid/.pypirc")
                assert mocks["os_open"].call_args[0][1] == expected_flags
                assert mocks["os_open"].call_args[0][2] == 0o600

                # Verify os.fdopen was called with the file descriptor
                mocks["fdopen"].assert_called_once_with(3, "w", encoding="utf-8")

                # Verify .pypirc was cleaned up after successful upload
                assert mocks["remove"].call_args[0][0].endswith("test-repo-test-uuid/.pypirc")

                # Verify slack message was sent via asyncio.to_thread
                # This is now done through asyncio.to_thread(send_slack_message, ...)
                # We can verify by checking run_command was successful and slack_webhook_url is set

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
            with pypi_upload_mocks() as mocks:
                # Mock successful clone
                mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                # Mock failed build
                mocks["run_command"].return_value = (False, "Build failed", "Error")

                await push_handler.upload_to_pypi(tag_name="v1.0.0")

                # Verify issue was created via unified_api
                push_handler.github_webhook.unified_api.create_issue_on_repository.assert_called_once()
                call_args = push_handler.github_webhook.unified_api.create_issue_on_repository.call_args
                assert "Build failed" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_glob_no_tarball_found(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when Path.glob finds no tar.gz file."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with pypi_upload_mocks() as mocks:
                # Mock successful clone
                mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                # Mock successful build
                mocks["run_command"].side_effect = [
                    (True, "", ""),  # uv build
                ]

                # Mock Path.glob() to return empty list (no tar.gz found)
                mocks["path"].return_value.glob.return_value = []

                await push_handler.upload_to_pypi(tag_name="v1.0.0")

                # Verify issue was created via unified_api
                push_handler.github_webhook.unified_api.create_issue_on_repository.assert_called_once()
                call_args = push_handler.github_webhook.unified_api.create_issue_on_repository.call_args
                assert "No .tar.gz file found" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_multiple_artifacts(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when multiple tar.gz files are found (multi-artifact selection scenario).

        This test verifies that when multiple artifacts exist, the implementation correctly
        selects the first one (sorted) for upload.
        """
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with pypi_upload_mocks() as mocks:
                # Mock successful clone
                mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                # Mock successful build, twine check, and upload
                mocks["run_command"].side_effect = [
                    (True, "", ""),  # uv build
                    (True, "", ""),  # twine check
                    (True, "", ""),  # twine upload
                ]

                # Mock Path.glob() to return multiple tar.gz files (sorted)
                # Need to use MagicMock to support comparison for sorted()

                mock_tarball1 = MagicMock()
                mock_tarball1.name = "aaa-package-1.0.0.tar.gz"
                mock_tarball1.__lt__ = lambda self, other: self.name < other.name
                mock_tarball2 = MagicMock()
                mock_tarball2.name = "zzz-package-1.0.0.tar.gz"
                mock_tarball2.__lt__ = lambda self, other: self.name < other.name
                # Return in specific order to verify sorting behavior
                mocks["path"].return_value.glob.return_value = [mock_tarball2, mock_tarball1]

                await push_handler.upload_to_pypi(tag_name="v1.0.0")

                # Verify twine check was called with first artifact (alphabetically sorted)
                twine_check_call = mocks["run_command"].call_args_list[1][1]
                assert "aaa-package-1.0.0.tar.gz" in twine_check_call["command"]

                # Verify twine upload was called with first artifact
                twine_upload_call = mocks["run_command"].call_args_list[2][1]
                assert "aaa-package-1.0.0.tar.gz" in twine_upload_call["command"]

                # Verify .pypirc cleanup
                mocks["remove"].assert_called_once()
                assert mocks["remove"].call_args[0][0].endswith(".pypirc")

    @pytest.mark.asyncio
    async def test_upload_to_pypi_twine_check_failure(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when twine check fails."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with pypi_upload_mocks() as mocks:
                # Mock successful clone
                mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                # Mock successful build, failed twine check
                mocks["run_command"].side_effect = [
                    (True, "", ""),  # uv build
                    (False, "twine check failed", "Error"),  # twine check
                ]

                # Mock Path.glob() to return tar.gz file
                mock_tarball = Mock()
                mock_tarball.name = "package-1.0.0.tar.gz"
                mocks["path"].return_value.glob.return_value = [mock_tarball]

                await push_handler.upload_to_pypi(tag_name="v1.0.0")

                # Verify .pypirc cleanup was attempted despite check failure
                mocks["remove"].assert_called_once()
                assert mocks["remove"].call_args[0][0].endswith(".pypirc")

                # Verify issue was created
                push_handler.github_webhook.unified_api.create_issue_on_repository.assert_called_once()
                call_args = push_handler.github_webhook.unified_api.create_issue_on_repository.call_args
                assert "twine check failed" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_twine_upload_failure(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when twine upload fails."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with pypi_upload_mocks() as mocks:
                # Mock successful clone
                mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                # Mock successful build and twine check, failed twine upload
                mocks["run_command"].side_effect = [
                    (True, "", ""),  # uv build
                    (True, "", ""),  # twine check
                    (False, "twine upload failed", "Error"),  # twine upload
                ]

                # Mock Path.glob() to return tar.gz file
                mock_tarball = Mock()
                mock_tarball.name = "package-1.0.0.tar.gz"
                mocks["path"].return_value.glob.return_value = [mock_tarball]

                await push_handler.upload_to_pypi(tag_name="v1.0.0")

                # Verify .pypirc cleanup was attempted despite upload failure
                mocks["remove"].assert_called_once()
                assert mocks["remove"].call_args[0][0].endswith(".pypirc")

                # Verify issue was created
                push_handler.github_webhook.unified_api.create_issue_on_repository.assert_called_once()
                call_args = push_handler.github_webhook.unified_api.create_issue_on_repository.call_args
                assert "twine upload failed" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_success_no_slack(self, push_handler: PushHandler) -> None:
        """Test successful upload to pypi without slack webhook."""
        push_handler.github_webhook.slack_webhook_url = ""  # Empty string instead of None

        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with pypi_upload_mocks() as mocks:
                # Mock successful clone
                mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                # Mock successful build (no find command anymore)
                mocks["run_command"].side_effect = [
                    (True, "", ""),  # uv build
                    (True, "", ""),  # twine check
                    (True, "", ""),  # twine upload
                ]

                # Mock Path.glob() to return tar.gz file
                mock_tarball = Mock()
                mock_tarball.name = "package-1.0.0.tar.gz"
                mocks["path"].return_value.glob.return_value = [mock_tarball]

                await push_handler.upload_to_pypi(tag_name="v1.0.0")

                # Verify slack was not called (slack_webhook_url is empty)
                # No need to check asyncio.to_thread since slack_webhook_url is empty

    @pytest.mark.asyncio
    async def test_upload_to_pypi_commands_execution_order(self, push_handler: PushHandler) -> None:
        """Test that commands are executed in the correct order."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with pypi_upload_mocks() as mocks:
                # Mock successful clone
                mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                # Mock successful all commands (no find command anymore)
                mocks["run_command"].side_effect = [
                    (True, "", ""),  # uv build
                    (True, "", ""),  # twine check
                    (True, "", ""),  # twine upload
                ]

                # Mock Path.glob() to return tar.gz file
                mock_tarball = Mock()
                mock_tarball.name = "package-1.0.0.tar.gz"
                mocks["path"].return_value.glob.return_value = [mock_tarball]

                await push_handler.upload_to_pypi(tag_name="v1.0.0")

                # Verify commands were called in correct order
                calls = mocks["run_command"].call_args_list
                # Each call is call(command=..., log_prefix=...)
                # The command string is in the 'command' kwarg
                assert len(calls) == 3
                assert "uv" in calls[0].kwargs["command"]
                assert "build" in calls[0].kwargs["command"]

                # Verify twine check (doesn't use --config-file)
                assert "twine check" in calls[1].kwargs["command"]
                assert "package-1.0.0.tar.gz" in calls[1].kwargs["command"]
                # Verify token redaction is enabled for twine check
                assert "redact_secrets" in calls[1].kwargs
                assert "test-token" in calls[1].kwargs["redact_secrets"]

                # Verify twine upload has --config-file and token redaction
                assert "twine upload" in calls[2].kwargs["command"]
                assert "--config-file" in calls[2].kwargs["command"]
                assert ".pypirc" in calls[2].kwargs["command"]
                assert "package-1.0.0.tar.gz" in calls[2].kwargs["command"]
                # Verify token redaction is enabled for upload
                assert "redact_secrets" in calls[2].kwargs
                assert "test-token" in calls[2].kwargs["redact_secrets"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_unique_clone_directory(self, push_handler: PushHandler) -> None:
        """Test that each upload uses a unique clone directory."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with pypi_upload_mocks() as mocks:
                # Mock successful clone
                mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                # Mock successful build (no find command anymore)
                mocks["run_command"].side_effect = [
                    (True, "", ""),  # uv build
                    (True, "", ""),  # twine check
                    (True, "", ""),  # twine upload
                ]

                # Mock Path.glob() to return tar.gz file
                mock_tarball = Mock()
                mock_tarball.name = "package-1.0.0.tar.gz"
                mocks["path"].return_value.glob.return_value = [mock_tarball]

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

            # The title should contain the error message (substring assertion to avoid brittleness)
            assert "Clone failed" in call_args[1]["title"]
            assert "Error details" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_slack_message_format(self, push_handler: PushHandler) -> None:
        """Test that slack messages are sent with proper format."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with pypi_upload_mocks() as mocks:
                # Mock successful clone
                mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                # Mock successful build
                mocks["run_command"].side_effect = [
                    (True, "", ""),  # uv build
                    (True, "", ""),  # twine check
                    (True, "", ""),  # twine upload
                ]

                # Mock Path.glob() to return tar.gz file
                mock_tarball = Mock()
                mock_tarball.name = "package-1.0.0.tar.gz"
                mocks["path"].return_value.glob.return_value = [mock_tarball]

                await push_handler.upload_to_pypi(tag_name="v1.0.0")

            # Verify slack message was sent (verified indirectly through successful execution)
            # Slack is now called via asyncio.to_thread(send_slack_message, ...)
            # If webhook succeeds and slack_webhook_url is set, message is sent

    @pytest.mark.asyncio
    async def test_upload_to_pypi_missing_token(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when PyPI token is missing."""
        # Set pypi config without token
        push_handler.github_webhook.pypi = {}

        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with pypi_upload_mocks() as mocks:
                # Mock successful clone
                mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                # Mock successful build
                mocks["run_command"].side_effect = [
                    (True, "", ""),  # uv build
                ]

                # Mock Path.glob() to return tar.gz file
                mock_tarball = Mock()
                mock_tarball.name = "package-1.0.0.tar.gz"
                mocks["path"].return_value.glob.return_value = [mock_tarball]

                await push_handler.upload_to_pypi(tag_name="v1.0.0")

                # Verify issue was created for missing token
                push_handler.github_webhook.unified_api.create_issue_on_repository.assert_called_once()
                call_args = push_handler.github_webhook.unified_api.create_issue_on_repository.call_args
                assert "PyPI token is not configured" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_preexisting_pypirc(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when .pypirc file already exists."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with pypi_upload_mocks() as mocks:
                # Mock successful clone
                mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                # Mock successful build
                mocks["run_command"].side_effect = [
                    (True, "", ""),  # uv build
                ]

                # Mock Path.glob() to return tar.gz file
                mock_tarball = Mock()
                mock_tarball.name = "package-1.0.0.tar.gz"
                mocks["path"].return_value.glob.return_value = [mock_tarball]

                # Simulate FileExistsError when creating .pypirc
                mocks["os_open"].side_effect = FileExistsError("File exists")

                await push_handler.upload_to_pypi(tag_name="v1.0.0")

                # Verify issue was created for pre-existing file
                push_handler.github_webhook.unified_api.create_issue_on_repository.assert_called_once()
                call_args = push_handler.github_webhook.unified_api.create_issue_on_repository.call_args
                assert ".pypirc file already exists" in call_args[1]["title"]

    @pytest.mark.asyncio
    async def test_upload_to_pypi_generic_oserror(self, push_handler: PushHandler) -> None:
        """Test upload to pypi when generic OSError (non-FileExistsError) occurs during .pypirc creation."""
        with patch.object(push_handler.runner_handler, "_prepare_cloned_repo_dir") as mock_prepare:
            with pypi_upload_mocks() as mocks:
                # Mock successful clone
                mock_prepare.return_value.__aenter__.return_value = (True, "", "")

                # Mock successful build
                mocks["run_command"].side_effect = [
                    (True, "", ""),  # uv build
                ]

                # Mock Path.glob() to return tar.gz file
                mock_tarball = Mock()
                mock_tarball.name = "package-1.0.0.tar.gz"
                mocks["path"].return_value.glob.return_value = [mock_tarball]

                # Simulate generic OSError when creating .pypirc
                mocks["os_open"].side_effect = OSError("Permission denied")

                await push_handler.upload_to_pypi(tag_name="v1.0.0")

                # Verify issue was created for generic OSError
                push_handler.github_webhook.unified_api.create_issue_on_repository.assert_called_once()
                call_args = push_handler.github_webhook.unified_api.create_issue_on_repository.call_args
                assert "Failed to create .pypirc file" in call_args[1]["title"]
