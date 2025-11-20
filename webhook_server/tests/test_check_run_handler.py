from unittest.mock import AsyncMock, Mock, patch

import pytest
from github.CheckRun import CheckRun
from github.CommitStatus import CommitStatus
from starlette.datastructures import Headers

from webhook_server.libs.github_api import GithubWebhook
from webhook_server.libs.handlers.check_run_handler import CheckRunHandler
from webhook_server.utils.constants import (
    BUILD_CONTAINER_STR,
    CAN_BE_MERGED_STR,
    CHERRY_PICKED_LABEL_PREFIX,
    CONVENTIONAL_TITLE_STR,
    FAILURE_STR,
    IN_PROGRESS_STR,
    PRE_COMMIT_STR,
    PYTHON_MODULE_INSTALL_STR,
    QUEUED_STR,
    SUCCESS_STR,
    TOX_STR,
    VERIFIED_LABEL_STR,
)


class TestCheckRunHandler:
    """Test suite for CheckRunHandler class."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance."""
        mock_webhook = Mock()
        mock_webhook.hook_data = {}
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.repository_by_github_app = Mock()
        mock_webhook.last_commit = Mock()
        mock_webhook.last_commit.sha = "test-sha"
        mock_webhook.tox = True
        mock_webhook.pre_commit = True
        mock_webhook.verified_job = True
        mock_webhook.build_and_push_container = True
        mock_webhook.pypi = {"token": "test-token"}
        mock_webhook.conventional_title = "feat,fix"
        mock_webhook.token = "test-token"
        mock_webhook.container_repository_username = "test-user"
        mock_webhook.container_repository_password = "test-pass"  # pragma: allowlist secret
        return mock_webhook

    @pytest.fixture
    def check_run_handler(self, mock_github_webhook: Mock) -> CheckRunHandler:
        """Create a CheckRunHandler instance with mocked dependencies."""
        return CheckRunHandler(mock_github_webhook)

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        """Create a mock PullRequest instance."""
        mock_pr = Mock()
        mock_pr.base.ref = "main"
        return mock_pr

    @pytest.mark.asyncio
    async def test_process_pull_request_check_run_webhook_data_completed(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test processing check run webhook data when action is completed."""
        check_run_handler.hook_data = {
            "action": "completed",
            "check_run": {"name": "test-check", "status": "completed", "conclusion": "success"},
        }

        result = await check_run_handler.process_pull_request_check_run_webhook_data()
        assert result is True

    @pytest.mark.asyncio
    async def test_process_pull_request_check_run_webhook_data_not_completed(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test processing check run webhook data when action is not completed."""
        check_run_handler.hook_data = {
            "action": "created",
            "check_run": {"name": "test-check", "status": "in_progress", "conclusion": None},
        }

        result = await check_run_handler.process_pull_request_check_run_webhook_data()
        assert result is False
        # Verify completion log was called (skipping is acceptable)
        assert check_run_handler.logger.step.called  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_process_pull_request_check_run_webhook_data_can_be_merged(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test processing check run webhook data when check run is can-be-merged."""
        check_run_handler.hook_data = {
            "action": "completed",
            "check_run": {"name": CAN_BE_MERGED_STR, "status": "completed", "conclusion": "success"},
        }

        result = await check_run_handler.process_pull_request_check_run_webhook_data()
        assert result is False
        # Verify completion log was called
        assert check_run_handler.logger.step.called  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_process_pull_request_check_run_webhook_data_completed_normal(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test processing check run webhook data when action is completed (normal check run)."""
        check_run_handler.hook_data = {
            "action": "completed",
            "check_run": {"name": "test-check", "status": "completed", "conclusion": "success"},
        }

        result = await check_run_handler.process_pull_request_check_run_webhook_data()
        assert result is True
        # Verify completion log was called
        assert check_run_handler.logger.step.called  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_set_verify_check_queued(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting verify check to queued status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_verify_check_queued()
            mock_set_status.assert_called_once_with(check_run=VERIFIED_LABEL_STR, status=QUEUED_STR)

    @pytest.mark.asyncio
    async def test_set_verify_check_success(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting verify check to success status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_verify_check_success()
            mock_set_status.assert_called_once_with(check_run=VERIFIED_LABEL_STR, conclusion=SUCCESS_STR)

    @pytest.mark.asyncio
    async def test_set_run_tox_check_queued_enabled(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting tox check to queued when tox is enabled."""
        with patch.object(check_run_handler.github_webhook, "tox", True):
            with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
                await check_run_handler.set_run_tox_check_queued()
                mock_set_status.assert_called_once_with(check_run=TOX_STR, status=QUEUED_STR)

    @pytest.mark.asyncio
    async def test_set_run_tox_check_queued_disabled(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting tox check to queued when tox is disabled."""
        with patch.object(check_run_handler.github_webhook, "tox", False):
            with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
                await check_run_handler.set_run_tox_check_queued()
                mock_set_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_run_tox_check_in_progress(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting tox check to in progress status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_run_tox_check_in_progress()
            mock_set_status.assert_called_once_with(check_run=TOX_STR, status=IN_PROGRESS_STR)

    @pytest.mark.asyncio
    async def test_set_run_tox_check_failure(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting tox check to failure status."""
        output = {"title": "Test failed", "summary": "Test summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_run_tox_check_failure(output)
            mock_set_status.assert_called_once_with(check_run=TOX_STR, conclusion=FAILURE_STR, output=output)

    @pytest.mark.asyncio
    async def test_set_run_tox_check_success(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting tox check to success status."""
        output = {"title": "Test passed", "summary": "Test summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_run_tox_check_success(output)
            mock_set_status.assert_called_once_with(check_run=TOX_STR, conclusion=SUCCESS_STR, output=output)

    @pytest.mark.asyncio
    async def test_set_run_pre_commit_check_queued_enabled(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting pre-commit check to queued when pre-commit is enabled."""
        check_run_handler.github_webhook.pre_commit = True
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_run_pre_commit_check_queued()
            mock_set_status.assert_called_once_with(check_run=PRE_COMMIT_STR, status=QUEUED_STR)

    @pytest.mark.asyncio
    async def test_set_run_pre_commit_check_queued_disabled(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting pre-commit check to queued when pre-commit is disabled."""
        check_run_handler.github_webhook.pre_commit = False
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_run_pre_commit_check_queued()
            mock_set_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_run_pre_commit_check_in_progress(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting pre-commit check to in progress status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_run_pre_commit_check_in_progress()
            mock_set_status.assert_called_once_with(check_run=PRE_COMMIT_STR, status=IN_PROGRESS_STR)

    @pytest.mark.asyncio
    async def test_set_run_pre_commit_check_failure(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting pre-commit check to failure status."""
        output = {"title": "Pre-commit failed", "summary": "Pre-commit summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_run_pre_commit_check_failure(output)
            mock_set_status.assert_called_once_with(check_run=PRE_COMMIT_STR, conclusion=FAILURE_STR, output=output)

    @pytest.mark.asyncio
    async def test_set_run_pre_commit_check_failure_no_output(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting pre-commit check to failure status without output."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_run_pre_commit_check_failure()
            mock_set_status.assert_called_once_with(check_run=PRE_COMMIT_STR, conclusion=FAILURE_STR, output=None)

    @pytest.mark.asyncio
    async def test_set_run_pre_commit_check_success(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting pre-commit check to success status."""
        output = {"title": "Pre-commit passed", "summary": "Pre-commit summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_run_pre_commit_check_success(output)
            mock_set_status.assert_called_once_with(check_run=PRE_COMMIT_STR, conclusion=SUCCESS_STR, output=output)

    @pytest.mark.asyncio
    async def test_set_run_pre_commit_check_success_no_output(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting pre-commit check to success status without output."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_run_pre_commit_check_success()
            mock_set_status.assert_called_once_with(check_run=PRE_COMMIT_STR, conclusion=SUCCESS_STR, output=None)

    @pytest.mark.asyncio
    async def test_set_merge_check_queued(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting merge check to queued status."""
        output = {"title": "Merge check", "summary": "Merge summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_merge_check_queued(output)
            mock_set_status.assert_called_once_with(check_run=CAN_BE_MERGED_STR, status=QUEUED_STR, output=output)

    @pytest.mark.asyncio
    async def test_set_merge_check_queued_no_output(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting merge check to queued status without output."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_merge_check_queued()
            mock_set_status.assert_called_once_with(check_run=CAN_BE_MERGED_STR, status=QUEUED_STR, output=None)

    @pytest.mark.asyncio
    async def test_set_merge_check_in_progress(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting merge check to in progress status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_merge_check_in_progress()
            mock_set_status.assert_called_once_with(check_run=CAN_BE_MERGED_STR, status=IN_PROGRESS_STR)

    @pytest.mark.asyncio
    async def test_set_merge_check_success(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting merge check to success status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_merge_check_success()
            mock_set_status.assert_called_once_with(check_run=CAN_BE_MERGED_STR, conclusion=SUCCESS_STR)

    @pytest.mark.asyncio
    async def test_set_merge_check_failure(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting merge check to failure status."""
        output = {"title": "Merge failed", "summary": "Merge summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_merge_check_failure(output)
            mock_set_status.assert_called_once_with(check_run=CAN_BE_MERGED_STR, conclusion=FAILURE_STR, output=output)

    @pytest.mark.asyncio
    async def test_set_container_build_queued_enabled(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting container build check to queued when container build is enabled."""
        with patch.object(check_run_handler.github_webhook, "build_and_push_container", True):
            with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
                await check_run_handler.set_container_build_queued()
                mock_set_status.assert_called_once_with(check_run=BUILD_CONTAINER_STR, status=QUEUED_STR)

    @pytest.mark.asyncio
    async def test_set_container_build_queued_disabled(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting container build check to queued when container build is disabled."""
        with patch.object(check_run_handler.github_webhook, "build_and_push_container", False):
            with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
                await check_run_handler.set_container_build_queued()
                mock_set_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_container_build_in_progress(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting container build check to in progress status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_container_build_in_progress()
            mock_set_status.assert_called_once_with(check_run=BUILD_CONTAINER_STR, status=IN_PROGRESS_STR)

    @pytest.mark.asyncio
    async def test_set_container_build_success(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting container build check to success status."""
        output = {"title": "Container built", "summary": "Container summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_container_build_success(output)
            mock_set_status.assert_called_once_with(
                check_run=BUILD_CONTAINER_STR, conclusion=SUCCESS_STR, output=output
            )

    @pytest.mark.asyncio
    async def test_set_container_build_failure(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting container build check to failure status."""
        output = {"title": "Container build failed", "summary": "Container summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_container_build_failure(output)
            mock_set_status.assert_called_once_with(
                check_run=BUILD_CONTAINER_STR, conclusion=FAILURE_STR, output=output
            )

    @pytest.mark.asyncio
    async def test_set_python_module_install_queued_enabled(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting python module install check to queued when pypi is enabled."""
        check_run_handler.github_webhook.pypi = {"token": "test"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_python_module_install_queued()
            mock_set_status.assert_called_once_with(check_run=PYTHON_MODULE_INSTALL_STR, status=QUEUED_STR)

    @pytest.mark.asyncio
    async def test_set_python_module_install_queued_disabled(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting python module install check to queued when pypi is disabled."""
        with patch.object(check_run_handler.github_webhook, "pypi", None):
            with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
                await check_run_handler.set_python_module_install_queued()
                mock_set_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_python_module_install_in_progress(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting python module install check to in progress status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_python_module_install_in_progress()
            mock_set_status.assert_called_once_with(check_run=PYTHON_MODULE_INSTALL_STR, status=IN_PROGRESS_STR)

    @pytest.mark.asyncio
    async def test_set_python_module_install_success(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting python module install check to success status."""
        output = {"title": "Module installed", "summary": "Module summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_python_module_install_success(output)
            mock_set_status.assert_called_once_with(
                check_run=PYTHON_MODULE_INSTALL_STR, conclusion=SUCCESS_STR, output=output
            )

    @pytest.mark.asyncio
    async def test_set_python_module_install_failure(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting python module install check to failure status."""
        output = {"title": "Module install failed", "summary": "Module summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_python_module_install_failure(output)
            mock_set_status.assert_called_once_with(
                check_run=PYTHON_MODULE_INSTALL_STR, conclusion=FAILURE_STR, output=output
            )

    @pytest.mark.asyncio
    async def test_set_conventional_title_queued(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting conventional title check to queued status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_conventional_title_queued()
            mock_set_status.assert_called_once_with(check_run=CONVENTIONAL_TITLE_STR, status=QUEUED_STR)

    @pytest.mark.asyncio
    async def test_set_conventional_title_in_progress(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting conventional title check to in progress status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_conventional_title_in_progress()
            mock_set_status.assert_called_once_with(check_run=CONVENTIONAL_TITLE_STR, status=IN_PROGRESS_STR)

    @pytest.mark.asyncio
    async def test_set_conventional_title_success(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting conventional title check to success status."""
        output = {"title": "Title valid", "summary": "Title summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_conventional_title_success(output)
            mock_set_status.assert_called_once_with(
                check_run=CONVENTIONAL_TITLE_STR, conclusion=SUCCESS_STR, output=output
            )

    @pytest.mark.asyncio
    async def test_set_conventional_title_failure(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting conventional title check to failure status."""
        output = {"title": "Title invalid", "summary": "Title summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_conventional_title_failure(output)
            mock_set_status.assert_called_once_with(
                check_run=CONVENTIONAL_TITLE_STR, conclusion=FAILURE_STR, output=output
            )

    @pytest.mark.asyncio
    async def test_set_cherry_pick_in_progress(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting cherry pick check to in progress status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_cherry_pick_in_progress()
            mock_set_status.assert_called_once_with(check_run=CHERRY_PICKED_LABEL_PREFIX, status=IN_PROGRESS_STR)

    @pytest.mark.asyncio
    async def test_set_cherry_pick_success(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting cherry pick check to success status."""
        output = {"title": "Cherry pick successful", "summary": "Cherry pick summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_cherry_pick_success(output)
            mock_set_status.assert_called_once_with(
                check_run=CHERRY_PICKED_LABEL_PREFIX, conclusion=SUCCESS_STR, output=output
            )

    @pytest.mark.asyncio
    async def test_set_cherry_pick_failure(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting cherry pick check to failure status."""
        output = {"title": "Cherry pick failed", "summary": "Cherry pick summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_cherry_pick_failure(output)
            mock_set_status.assert_called_once_with(
                check_run=CHERRY_PICKED_LABEL_PREFIX, conclusion=FAILURE_STR, output=output
            )

    @pytest.mark.asyncio
    async def test_set_check_run_status_success(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting check run status successfully."""
        with patch.object(
            check_run_handler.github_webhook.repository_by_github_app, "create_check_run", return_value=None
        ):
            with patch.object(check_run_handler.github_webhook.logger, "success") as mock_success:
                await check_run_handler.set_check_run_status(
                    check_run="test-check", status="queued", conclusion="", output=None
                )
                mock_success.assert_not_called()  # Only called for certain conclusions

    @pytest.mark.asyncio
    async def test_set_check_run_status_with_conclusion(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting check run status with conclusion."""
        with patch.object(
            check_run_handler.github_webhook.repository_by_github_app, "create_check_run", return_value=None
        ):
            with patch.object(check_run_handler.github_webhook.logger, "success") as mock_success:
                await check_run_handler.set_check_run_status(
                    check_run="test-check", status="", conclusion="success", output=None
                )
                mock_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_check_run_status_with_output(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting check run status with output."""
        with patch.object(
            check_run_handler.github_webhook.repository_by_github_app, "create_check_run", return_value=None
        ):
            with patch.object(check_run_handler.github_webhook.logger, "success") as mock_success:
                output = {"title": "Test", "summary": "Summary"}
                await check_run_handler.set_check_run_status(
                    check_run="test-check", status="queued", conclusion="", output=output
                )
                mock_success.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_check_run_status_exception_handling(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting check run status with exception handling."""
        # Patch create_check_run as a real function that raises, then succeeds
        call_count = {"count": 0}

        def create_check_run_side_effect(*args: object, **kwargs: object) -> None:
            if call_count["count"] == 0:
                call_count["count"] += 1
                raise Exception("API Error")
            call_count["count"] += 1
            return None

        with patch.object(
            check_run_handler.github_webhook.repository_by_github_app,
            "create_check_run",
            side_effect=create_check_run_side_effect,
        ):
            with patch.object(check_run_handler.github_webhook.logger, "debug") as mock_debug:
                await check_run_handler.set_check_run_status(
                    check_run="test-check", status="queued", conclusion="", output=None
                )
                # Should be called twice - once for the original attempt, once for the fallback
                assert call_count["count"] == 2
                mock_debug.assert_called()

    def test_get_check_run_text_normal_length(self, check_run_handler: CheckRunHandler) -> None:
        """Test getting check run text with normal length."""
        err = "Error message"
        out = "Output message"

        result = check_run_handler.get_check_run_text(err, out)

        expected = "```\nError message\n\nOutput message\n```"
        assert result == expected

    def test_get_check_run_text_long_length(self, check_run_handler: CheckRunHandler) -> None:
        """Test getting check run text with length exceeding GitHub limit."""
        # Create text that exceeds 65535 characters
        long_err = "Error " * 10000
        long_out = "Output " * 10000

        result = check_run_handler.get_check_run_text(long_err, long_out)

        # Should be truncated to 65534 characters (GitHub limit safe margin)
        assert len(result) == 65534
        assert result.startswith("```\n")
        # Verify the fix: it should end with the code block closer
        assert result.endswith("\n```")

    def test_get_check_run_text_token_replacement(self, check_run_handler: CheckRunHandler) -> None:
        """Test that sensitive tokens are replaced in check run text."""
        err = "Error with token: test-token"
        out = "Output with token: test-token"

        result = check_run_handler.get_check_run_text(err, out)

        # Tokens should be replaced with *****
        assert "test-token" not in result
        assert "*****" in result

    def test_get_check_run_text_container_credentials_replacement(self, check_run_handler: CheckRunHandler) -> None:
        """Test that container credentials are replaced in check run text."""
        err = "Error with user: test-user"
        out = "Output with pass: test-pass"

        result = check_run_handler.get_check_run_text(err, out)

        # Credentials should be replaced with *****
        assert "test-user" not in result
        assert "test-pass" not in result
        assert "*****" in result

    @pytest.mark.asyncio
    async def test_is_check_run_in_progress_true(self, check_run_handler: CheckRunHandler) -> None:
        """Test checking if check run is in progress - returns True."""
        mock_check_run = Mock()
        mock_check_run.name = "test-check"
        mock_check_run.status = IN_PROGRESS_STR

        def get_check_runs() -> list:
            return [mock_check_run]

        with patch.object(check_run_handler.github_webhook.last_commit, "get_check_runs", side_effect=get_check_runs):
            result = await check_run_handler.is_check_run_in_progress("test-check")
            assert result is True

    @pytest.mark.asyncio
    async def test_is_check_run_in_progress_false(self, check_run_handler: CheckRunHandler) -> None:
        """Test checking if check run is in progress - returns False."""
        mock_check_run = Mock()
        mock_check_run.name = "test-check"
        mock_check_run.status = "completed"

        def get_check_runs() -> list:
            return [mock_check_run]

        with patch.object(check_run_handler.github_webhook.last_commit, "get_check_runs", side_effect=get_check_runs):
            result = await check_run_handler.is_check_run_in_progress("test-check")
            assert result is False

    @pytest.mark.asyncio
    async def test_is_check_run_in_progress_no_last_commit(self, check_run_handler: CheckRunHandler) -> None:
        """Test checking if check run is in progress when no last commit."""
        with patch.object(check_run_handler.github_webhook, "last_commit", None):
            result = await check_run_handler.is_check_run_in_progress("test-check")
            assert result is False

    @pytest.mark.asyncio
    async def test_required_check_failed_or_no_status(self, check_run_handler: CheckRunHandler) -> None:
        """Test checking for failed or no status checks."""
        mock_pull_request = Mock()
        mock_check_run = Mock()
        mock_check_run.name = "test-check"
        mock_check_run.conclusion = FAILURE_STR

        with patch.object(check_run_handler, "all_required_status_checks", return_value=["test-check"]):
            result = await check_run_handler.required_check_failed_or_no_status(
                mock_pull_request, [mock_check_run], [], []
            )

            assert "test-check" in result

    @pytest.mark.asyncio
    async def test_all_required_status_checks(self, check_run_handler: CheckRunHandler) -> None:
        """Test getting all required status checks."""
        mock_pull_request = Mock()

        with patch.object(check_run_handler, "get_branch_required_status_checks", return_value=["branch-check"]):
            result = await check_run_handler.all_required_status_checks(mock_pull_request)

            # Should include all enabled checks plus branch checks
            expected_checks = [
                TOX_STR,
                VERIFIED_LABEL_STR,
                BUILD_CONTAINER_STR,
                PYTHON_MODULE_INSTALL_STR,
                CONVENTIONAL_TITLE_STR,
                "branch-check",
            ]
            assert all(check in result for check in expected_checks)

    @pytest.mark.asyncio
    async def test_get_branch_required_status_checks_public_repo(self, check_run_handler: CheckRunHandler) -> None:
        """Test getting branch required status checks for public repository."""
        mock_pull_request = Mock()
        mock_pull_request.base.ref = "main"
        mock_branch = Mock()
        mock_branch_protection = Mock()
        mock_branch_protection.required_status_checks.contexts = ["branch-check-1", "branch-check-2"]
        with patch.object(check_run_handler.repository, "private", False):

            def get_branch(ref: object) -> Mock:
                return mock_branch

            def get_protection() -> Mock:
                return mock_branch_protection

            with patch.object(check_run_handler.repository, "get_branch", side_effect=get_branch):
                with patch.object(mock_branch, "get_protection", side_effect=get_protection):
                    result = await check_run_handler.get_branch_required_status_checks(mock_pull_request)
                    assert result == ["branch-check-1", "branch-check-2"]

    @pytest.mark.asyncio
    async def test_get_branch_required_status_checks_private_repo(self, check_run_handler: CheckRunHandler) -> None:
        """Test getting branch required status checks for private repository."""
        mock_pull_request = Mock()
        with patch.object(check_run_handler.repository, "private", True):
            with patch.object(check_run_handler.github_webhook.logger, "info") as mock_info:
                result = await check_run_handler.get_branch_required_status_checks(mock_pull_request)
                assert result == []
                mock_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_required_check_in_progress(self, check_run_handler: CheckRunHandler) -> None:
        """Test checking for required checks in progress."""
        mock_pull_request = Mock()
        mock_check_run = Mock()
        mock_check_run.name = "test-check"
        mock_check_run.status = IN_PROGRESS_STR

        with patch.object(check_run_handler, "all_required_status_checks", return_value=["test-check"]):
            msg, in_progress_checks = await check_run_handler.required_check_in_progress(
                mock_pull_request, [mock_check_run]
            )

            assert "test-check" in msg
            assert "test-check" in in_progress_checks

    @pytest.mark.asyncio
    async def test_required_check_in_progress_can_be_merged(self, check_run_handler: CheckRunHandler) -> None:
        """Test checking for required checks in progress excluding can-be-merged."""
        mock_pull_request = Mock()
        mock_check_run = Mock()
        mock_check_run.name = CAN_BE_MERGED_STR
        mock_check_run.status = IN_PROGRESS_STR

        with patch.object(check_run_handler, "all_required_status_checks", return_value=[CAN_BE_MERGED_STR]):
            msg, in_progress_checks = await check_run_handler.required_check_in_progress(
                mock_pull_request, [mock_check_run]
            )

            assert msg == ""
            assert in_progress_checks == []

    @pytest.mark.asyncio
    async def test_required_check_failed_or_no_status_missing_required_check(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that missing required checks are detected and appear in error message."""
        mock_pull_request = Mock()

        # Setup: "tox" is required but completely missing from check runs
        required_checks = [TOX_STR, VERIFIED_LABEL_STR]

        # Only verified check exists, tox is missing
        mock_verified_check = Mock()
        mock_verified_check.name = VERIFIED_LABEL_STR
        mock_verified_check.conclusion = SUCCESS_STR

        with patch.object(check_run_handler, "all_required_status_checks", return_value=required_checks):
            result = await check_run_handler.required_check_failed_or_no_status(
                mock_pull_request, [mock_verified_check], [], []
            )

            assert TOX_STR in result
            assert "not started" in result
            # verified check is successful, should not appear in error
            assert VERIFIED_LABEL_STR not in result

    @pytest.mark.asyncio
    async def test_required_check_failed_or_no_status_queued_required_check(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that queued required checks (status=queued, conclusion=None) appear in error message."""
        mock_pull_request = Mock()

        # Setup: "tox" is required and queued (conclusion=None)
        required_checks = [TOX_STR, VERIFIED_LABEL_STR]

        mock_tox_check = Mock()
        mock_tox_check.name = TOX_STR
        mock_tox_check.status = QUEUED_STR
        mock_tox_check.conclusion = None

        mock_verified_check = Mock()
        mock_verified_check.name = VERIFIED_LABEL_STR
        mock_verified_check.conclusion = SUCCESS_STR

        with patch.object(check_run_handler, "all_required_status_checks", return_value=required_checks):
            result = await check_run_handler.required_check_failed_or_no_status(
                mock_pull_request, [mock_tox_check, mock_verified_check], [], []
            )

            assert TOX_STR in result
            assert "not started" in result
            # verified check is successful, should not appear in error
            assert VERIFIED_LABEL_STR not in result

    @pytest.mark.asyncio
    async def test_required_check_failed_or_no_status_failed_required_check(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that failed required checks (conclusion=failure) appear in error message."""
        mock_pull_request = Mock()

        # Setup: "tox" is required and failed
        required_checks = [TOX_STR, VERIFIED_LABEL_STR]

        mock_tox_check = Mock()
        mock_tox_check.name = TOX_STR
        mock_tox_check.conclusion = FAILURE_STR

        mock_verified_check = Mock()
        mock_verified_check.name = VERIFIED_LABEL_STR
        mock_verified_check.conclusion = SUCCESS_STR

        with patch.object(check_run_handler, "all_required_status_checks", return_value=required_checks):
            result = await check_run_handler.required_check_failed_or_no_status(
                mock_pull_request, [mock_tox_check, mock_verified_check], [], []
            )

            assert TOX_STR in result
            assert "failed" in result
            # verified check is successful, should not appear in error
            assert VERIFIED_LABEL_STR not in result

    @pytest.mark.asyncio
    async def test_required_check_failed_or_no_status_all_successful(self, check_run_handler: CheckRunHandler) -> None:
        """Test that all successful required checks return empty string (no error)."""
        mock_pull_request = Mock()

        # Setup: All required checks are successful
        required_checks = [TOX_STR, VERIFIED_LABEL_STR, BUILD_CONTAINER_STR]

        mock_tox_check = Mock()
        mock_tox_check.name = TOX_STR
        mock_tox_check.conclusion = SUCCESS_STR

        mock_verified_check = Mock()
        mock_verified_check.name = VERIFIED_LABEL_STR
        mock_verified_check.conclusion = SUCCESS_STR

        mock_container_check = Mock()
        mock_container_check.name = BUILD_CONTAINER_STR
        mock_container_check.conclusion = SUCCESS_STR

        with patch.object(check_run_handler, "all_required_status_checks", return_value=required_checks):
            result = await check_run_handler.required_check_failed_or_no_status(
                mock_pull_request, [mock_tox_check, mock_verified_check, mock_container_check], [], []
            )

            # No errors expected - all checks successful
            assert result == ""

    @pytest.mark.asyncio
    async def test_required_check_failed_or_no_status_missing_non_required_check(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that missing non-required checks do NOT cause an error."""
        mock_pull_request = Mock()

        # Setup: Only TOX_STR is required, other checks are not
        required_checks = [TOX_STR]

        mock_tox_check = Mock()
        mock_tox_check.name = TOX_STR
        mock_tox_check.conclusion = SUCCESS_STR

        # BUILD_CONTAINER_STR exists but is not required
        mock_container_check = Mock()
        mock_container_check.name = BUILD_CONTAINER_STR
        mock_container_check.conclusion = SUCCESS_STR

        # VERIFIED_LABEL_STR is missing but not required

        with patch.object(check_run_handler, "all_required_status_checks", return_value=required_checks):
            result = await check_run_handler.required_check_failed_or_no_status(
                mock_pull_request, [mock_tox_check, mock_container_check], [], []
            )

            # No errors expected - only required check (tox) is successful
            assert result == ""
            # Missing non-required check should not appear in error
            assert VERIFIED_LABEL_STR not in result

    @pytest.mark.asyncio
    async def test_required_check_failed_or_no_status_multiple_missing_checks(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that multiple missing required checks all appear in error message."""
        mock_pull_request = Mock()

        # Setup: Multiple required checks, some missing
        required_checks = [TOX_STR, VERIFIED_LABEL_STR, BUILD_CONTAINER_STR, PYTHON_MODULE_INSTALL_STR]

        # Only verified check exists
        mock_verified_check = Mock()
        mock_verified_check.name = VERIFIED_LABEL_STR
        mock_verified_check.conclusion = SUCCESS_STR

        with patch.object(check_run_handler, "all_required_status_checks", return_value=required_checks):
            result = await check_run_handler.required_check_failed_or_no_status(
                mock_pull_request, [mock_verified_check], [], []
            )

            # All missing checks should appear in error
            assert TOX_STR in result
            assert BUILD_CONTAINER_STR in result
            assert PYTHON_MODULE_INSTALL_STR in result
            assert "not started" in result
            # Successful check should not appear
            assert VERIFIED_LABEL_STR not in result

    @pytest.mark.asyncio
    async def test_required_check_failed_or_no_status_mixed_states(self, check_run_handler: CheckRunHandler) -> None:
        """Test mixed check states: missing, queued, failed, and successful."""
        mock_pull_request = Mock()

        # Setup: Multiple required checks in different states
        required_checks = [TOX_STR, VERIFIED_LABEL_STR, BUILD_CONTAINER_STR, PYTHON_MODULE_INSTALL_STR]

        mock_tox_check = Mock()
        mock_tox_check.name = TOX_STR
        mock_tox_check.conclusion = FAILURE_STR  # Failed

        mock_verified_check = Mock()
        mock_verified_check.name = VERIFIED_LABEL_STR
        mock_verified_check.conclusion = None  # Queued/In progress
        mock_verified_check.status = QUEUED_STR

        mock_container_check = Mock()
        mock_container_check.name = BUILD_CONTAINER_STR
        mock_container_check.conclusion = SUCCESS_STR  # Successful

        # PYTHON_MODULE_INSTALL_STR is missing entirely

        with patch.object(check_run_handler, "all_required_status_checks", return_value=required_checks):
            result = await check_run_handler.required_check_failed_or_no_status(
                mock_pull_request, [mock_tox_check, mock_verified_check, mock_container_check], [], []
            )

            # Failed check should appear
            assert TOX_STR in result
            assert "failed" in result

            # Queued check should appear as not started
            assert VERIFIED_LABEL_STR in result
            assert "not started" in result

            # Missing check should appear as not started
            assert PYTHON_MODULE_INSTALL_STR in result

            # Successful check should not appear
            # BUILD_CONTAINER_STR should not be in the error portion
            assert result.count(BUILD_CONTAINER_STR) == 0

    @pytest.mark.asyncio
    async def test_required_check_failed_or_no_status_in_progress_excluded(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that checks in progress are excluded from failed checks list."""
        mock_pull_request = Mock()

        # Setup: Failed check that is also in progress (being rerun)
        required_checks = [TOX_STR, VERIFIED_LABEL_STR]

        mock_tox_check = Mock()
        mock_tox_check.name = TOX_STR
        mock_tox_check.conclusion = FAILURE_STR  # Failed but in progress

        mock_verified_check = Mock()
        mock_verified_check.name = VERIFIED_LABEL_STR
        mock_verified_check.conclusion = SUCCESS_STR

        # TOX_STR is in the in_progress list (being rerun)
        check_runs_in_progress = [TOX_STR]

        with patch.object(check_run_handler, "all_required_status_checks", return_value=required_checks):
            result = await check_run_handler.required_check_failed_or_no_status(
                mock_pull_request, [mock_tox_check, mock_verified_check], [], check_runs_in_progress
            )

            # Since tox is in progress, it should not appear in failed checks
            # (it's being rerun, so we wait for new result)
            assert result == "" or TOX_STR not in result

    @pytest.mark.asyncio
    async def test_required_check_failed_or_no_status_can_be_merged_ignored(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that can-be-merged check is ignored even if it fails."""
        mock_pull_request = Mock()

        # Setup: can-be-merged is in required checks but should be ignored
        required_checks = [TOX_STR, CAN_BE_MERGED_STR]

        mock_tox_check = Mock()
        mock_tox_check.name = TOX_STR
        mock_tox_check.conclusion = SUCCESS_STR

        mock_merge_check = Mock()
        mock_merge_check.name = CAN_BE_MERGED_STR
        mock_merge_check.conclusion = FAILURE_STR  # Failed, but should be ignored

        with patch.object(check_run_handler, "all_required_status_checks", return_value=required_checks):
            result = await check_run_handler.required_check_failed_or_no_status(
                mock_pull_request, [mock_tox_check, mock_merge_check], [], []
            )

            # No errors expected - can-be-merged is ignored
            assert result == ""
            assert CAN_BE_MERGED_STR not in result

    @pytest.mark.asyncio
    async def test_required_check_failed_or_no_status_with_commit_statuses(
        self, check_run_handler: CheckRunHandler, mock_pull_request: Mock
    ) -> None:
        """Test that commit statuses (legacy API) are validated alongside check runs.

        Simulates PR #928 where pre-commit.ci uses GitHub Statuses API (not Check Runs API).
        This test verifies:
        1. External services like pre-commit.ci use Statuses API, not Check Runs API
        2. Both Check Runs and Statuses are validated together
        3. Status state mapping (success/pending/failure/error) to check outcomes
        4. Non-required statuses are ignored
        5. Queued check runs are still reported correctly
        6. Combined check runs + statuses validation works
        """
        # Mock required checks including a status-based check
        required_checks = [TOX_STR, VERIFIED_LABEL_STR, "pre-commit.ci - pr", BUILD_CONTAINER_STR]

        # Create mock check runs (webhook server checks)
        mock_check_run_tox = Mock(spec=CheckRun)
        mock_check_run_tox.name = TOX_STR
        mock_check_run_tox.conclusion = SUCCESS_STR

        mock_check_run_verified = Mock(spec=CheckRun)
        mock_check_run_verified.name = VERIFIED_LABEL_STR
        mock_check_run_verified.conclusion = None  # Queued

        mock_check_run_container = Mock(spec=CheckRun)
        mock_check_run_container.name = BUILD_CONTAINER_STR
        mock_check_run_container.conclusion = SUCCESS_STR

        check_runs = [mock_check_run_tox, mock_check_run_verified, mock_check_run_container]

        # Create mock commit statuses (external service checks)
        mock_status_precommit = Mock(spec=CommitStatus)
        mock_status_precommit.context = "pre-commit.ci - pr"
        mock_status_precommit.state = "success"
        mock_status_precommit.id = 1

        mock_status_coderabbit = Mock(spec=CommitStatus)
        mock_status_coderabbit.context = "CodeRabbit"
        mock_status_coderabbit.state = "success"
        mock_status_coderabbit.id = 2

        statuses = [mock_status_precommit, mock_status_coderabbit]

        with patch.object(check_run_handler, "all_required_status_checks", new=AsyncMock(return_value=required_checks)):
            result = await check_run_handler.required_check_failed_or_no_status(
                pull_request=mock_pull_request,
                last_commit_check_runs=check_runs,
                last_commit_statuses=statuses,
                check_runs_in_progress=[],
            )

        # Verify: Should only report 'verified' as not started, NOT 'pre-commit.ci - pr'
        assert VERIFIED_LABEL_STR in result
        assert "pre-commit.ci - pr" not in result
        assert "Some check runs not started" in result
        assert VERIFIED_LABEL_STR in result
        assert "CodeRabbit" not in result  # Not required, should be ignored


class TestCheckRunRepositoryCloning:
    """Test suite for check_run repository cloning optimization.

    Tests verify the optimization that moves repository cloning into the check_run
    event handler with early exits for:
    - action != "completed" (webhook_server/libs/github_api.py lines 536-547)
    - can-be-merged with non-success conclusion (lines 549-563)
    - Clone only happens at line 570 when processing is actually needed
    """

    @pytest.fixture
    def mock_logger(self) -> Mock:
        """Create a mock logger."""
        logger = Mock()
        logger.name = "GithubWebhook"
        return logger

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        """Create a mock PullRequest instance."""
        mock_pr = Mock()
        mock_pr.number = 123
        mock_pr.base.ref = "main"
        mock_pr.draft = False  # Not a draft PR
        mock_pr.user = Mock()
        mock_pr.user.login = "test-user"
        return mock_pr

    @pytest.mark.asyncio
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.Config")
    async def test_check_run_action_not_completed_skips_clone(
        self,
        mock_config: Mock,
        mock_get_api: Mock,
        mock_get_repo: Mock,
        mock_get_app_api: Mock,
        mock_color: Mock,
        mock_logger: Mock,
        mock_pull_request: Mock,
    ) -> None:
        """Test that action != 'completed' skips repository cloning.

        When check_run action is 'queued', 'in_progress', or 'created',
        _clone_repository should NOT be called and webhook processing
        should complete successfully with skipped status.
        """
        # Setup mocks for GithubWebhook initialization
        mock_config.return_value.repository = True
        mock_config.return_value.repository_data = {"test": "data"}
        mock_config.return_value.repository_local_data.return_value = {}
        mock_get_api.return_value = (Mock(), "test-token", "test-user")
        mock_get_repo.return_value = Mock()
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"

        # Test various non-completed actions
        for action in ["queued", "in_progress", "created", "requested"]:
            # Setup webhook data for non-completed action
            hook_data = {
                "action": action,
                "check_run": {"name": "test-check", "status": action, "conclusion": None},
                "repository": {"name": "test-repo", "full_name": "my-org/test-repo"},
            }

            # Create Headers with check_run event
            headers = Headers({"X-GitHub-Event": "check_run", "X-GitHub-Delivery": "test-delivery-id"})

            # Create GithubWebhook instance
            github_webhook = GithubWebhook(hook_data=hook_data, headers=headers, logger=mock_logger)

            # Mock _clone_repository to track if it was called
            with patch.object(github_webhook, "_clone_repository", new=AsyncMock()) as mock_clone:
                # Mock _get_token_metrics to avoid API calls
                with patch.object(github_webhook, "_get_token_metrics", new=AsyncMock(return_value="mock-metrics")):
                    # Mock _get_last_commit to provide commit data (needed before check_run early exit)
                    mock_commit = Mock()
                    mock_commit.sha = "abc123"
                    mock_commit.committer = Mock()
                    mock_commit.committer.login = "test-committer"
                    with patch.object(github_webhook, "_get_last_commit", new=AsyncMock(return_value=mock_commit)):
                        # Mock get_pull_request to return mock PR
                        with patch.object(
                            github_webhook, "get_pull_request", new=AsyncMock(return_value=mock_pull_request)
                        ):
                            # Process the webhook
                            result = await github_webhook.process()

                            # Verify: _clone_repository was NOT called
                            mock_clone.assert_not_called()

                            # Verify: Result is None (skipped processing)
                            assert result is None

                            # Verify: Logger step was called with skipped status
                            assert github_webhook.logger.step.called

    @pytest.mark.asyncio
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.Config")
    async def test_can_be_merged_non_success_skips_clone(
        self,
        mock_config: Mock,
        mock_get_api: Mock,
        mock_get_repo: Mock,
        mock_get_app_api: Mock,
        mock_color: Mock,
        mock_logger: Mock,
        mock_pull_request: Mock,
    ) -> None:
        """Test that can-be-merged with non-success conclusion skips repository cloning.

        When check_run name is 'can-be-merged' and conclusion is not 'success',
        _clone_repository should NOT be called and webhook processing should
        complete successfully with skipped status.
        """
        # Setup mocks for GithubWebhook initialization
        mock_config.return_value.repository = True
        mock_config.return_value.repository_data = {"test": "data"}
        mock_config.return_value.repository_local_data.return_value = {}
        mock_get_api.return_value = (Mock(), "test-token", "test-user")
        mock_get_repo.return_value = Mock()
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"

        # Test various non-success conclusions for can-be-merged
        for conclusion in ["failure", "cancelled", "timed_out", "action_required", "neutral", "skipped"]:
            # Setup webhook data for can-be-merged with non-success conclusion
            hook_data = {
                "action": "completed",
                "check_run": {"name": CAN_BE_MERGED_STR, "status": "completed", "conclusion": conclusion},
                "repository": {"name": "test-repo", "full_name": "my-org/test-repo"},
            }

            # Create Headers with check_run event
            headers = Headers({"X-GitHub-Event": "check_run", "X-GitHub-Delivery": "test-delivery-id"})

            # Create GithubWebhook instance
            github_webhook = GithubWebhook(hook_data=hook_data, headers=headers, logger=mock_logger)

            # Mock _clone_repository to track if it was called
            with patch.object(github_webhook, "_clone_repository", new=AsyncMock()) as mock_clone:
                # Mock _get_token_metrics to avoid API calls
                with patch.object(github_webhook, "_get_token_metrics", new=AsyncMock(return_value="mock-metrics")):
                    # Mock _get_last_commit to provide commit data (needed before check_run early exit)
                    mock_commit = Mock()
                    mock_commit.sha = "abc123"
                    mock_commit.committer = Mock()
                    mock_commit.committer.login = "test-committer"
                    with patch.object(github_webhook, "_get_last_commit", new=AsyncMock(return_value=mock_commit)):
                        # Mock get_pull_request to return mock PR
                        with patch.object(
                            github_webhook, "get_pull_request", new=AsyncMock(return_value=mock_pull_request)
                        ):
                            # Process the webhook
                            result = await github_webhook.process()

                            # Verify: _clone_repository was NOT called
                            mock_clone.assert_not_called()

                            # Verify: Result is None (skipped processing)
                            assert result is None

                            # Verify: Logger step was called with skipped status
                            assert github_webhook.logger.step.called

    @pytest.mark.asyncio
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.Config")
    async def test_check_run_completed_normal_clones_repository(
        self,
        mock_config: Mock,
        mock_get_api: Mock,
        mock_get_repo: Mock,
        mock_get_app_api: Mock,
        mock_color: Mock,
        mock_logger: Mock,
        mock_pull_request: Mock,
    ) -> None:
        """Test that action='completed' with normal check DOES clone repository.

        When check_run action is 'completed' and check is not can-be-merged,
        _clone_repository should be called exactly once BEFORE processing check_run.
        """
        # Setup mocks for GithubWebhook initialization
        mock_config.return_value.repository = True
        mock_config.return_value.repository_data = {"test": "data"}
        mock_config.return_value.repository_local_data.return_value = {}
        mock_get_api.return_value = (Mock(), "test-token", "test-user")
        mock_get_repo.return_value = Mock()
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"

        # Setup webhook data for completed normal check
        hook_data = {
            "action": "completed",
            "check_run": {"name": "test-check", "status": "completed", "conclusion": "success"},
            "repository": {"name": "test-repo", "full_name": "my-org/test-repo"},
        }

        # Create Headers with check_run event
        headers = Headers({"X-GitHub-Event": "check_run", "X-GitHub-Delivery": "test-delivery-id"})

        # Create GithubWebhook instance
        github_webhook = GithubWebhook(hook_data=hook_data, headers=headers, logger=mock_logger)

        # Mock _clone_repository to track if it was called
        with patch.object(github_webhook, "_clone_repository", new=AsyncMock()) as mock_clone:
            # Mock _get_token_metrics to avoid API calls
            with patch.object(github_webhook, "_get_token_metrics", new=AsyncMock(return_value="mock-metrics")):
                # Mock _get_last_commit to provide commit data
                mock_commit = Mock()
                mock_commit.sha = "abc123"
                mock_commit.committer = Mock()
                mock_commit.committer.login = "test-committer"
                with patch.object(github_webhook, "_get_last_commit", new=AsyncMock(return_value=mock_commit)):
                    # Mock CheckRunHandler to avoid actual processing
                    with patch("webhook_server.libs.github_api.CheckRunHandler") as mock_handler_class:
                        mock_handler = AsyncMock()
                        mock_handler.process_pull_request_check_run_webhook_data = AsyncMock(return_value=True)
                        mock_handler_class.return_value = mock_handler

                        # Mock OwnersFileHandler to avoid actual initialization
                        with patch("webhook_server.libs.github_api.OwnersFileHandler") as mock_owners_class:
                            mock_owners = AsyncMock()
                            mock_owners.initialize = AsyncMock(return_value=mock_owners)
                            mock_owners_class.return_value = mock_owners

                            # Mock PullRequestHandler to avoid actual check_if_can_be_merged
                            with patch("webhook_server.libs.github_api.PullRequestHandler") as mock_pr_handler_class:
                                mock_pr_handler = AsyncMock()
                                mock_pr_handler.check_if_can_be_merged = AsyncMock()
                                mock_pr_handler_class.return_value = mock_pr_handler

                                # Mock get_pull_request to return mock PR
                                with patch.object(
                                    github_webhook, "get_pull_request", new=AsyncMock(return_value=mock_pull_request)
                                ):
                                    # Process the webhook
                                    result = await github_webhook.process()

                                    # Verify: _clone_repository was called exactly once
                                    mock_clone.assert_called_once_with(pull_request=mock_pull_request)

                                    # Verify: Result is None (successful processing)
                                    assert result is None

                                    # Verify: Logger step was called
                                    assert github_webhook.logger.step.called

    @pytest.mark.asyncio
    @patch("webhook_server.utils.helpers.get_repository_color_for_log_prefix")
    @patch("webhook_server.libs.github_api.get_repository_github_app_api")
    @patch("webhook_server.libs.github_api.get_github_repo_api")
    @patch("webhook_server.libs.github_api.get_api_with_highest_rate_limit")
    @patch("webhook_server.libs.github_api.Config")
    async def test_can_be_merged_success_clones_repository(
        self,
        mock_config: Mock,
        mock_get_api: Mock,
        mock_get_repo: Mock,
        mock_get_app_api: Mock,
        mock_color: Mock,
        mock_logger: Mock,
        mock_pull_request: Mock,
    ) -> None:
        """Test that can-be-merged with 'success' conclusion DOES clone repository.

        When check_run name is 'can-be-merged' and conclusion is 'success',
        _clone_repository should be called for potential automerge processing.
        """
        # Setup mocks for GithubWebhook initialization
        mock_config.return_value.repository = True
        mock_config.return_value.repository_data = {"test": "data"}
        mock_config.return_value.repository_local_data.return_value = {}
        mock_get_api.return_value = (Mock(), "test-token", "test-user")
        mock_get_repo.return_value = Mock()
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"

        # Setup webhook data for can-be-merged with success conclusion
        hook_data = {
            "action": "completed",
            "check_run": {"name": CAN_BE_MERGED_STR, "status": "completed", "conclusion": "success"},
            "repository": {"name": "test-repo", "full_name": "my-org/test-repo"},
        }

        # Create Headers with check_run event
        headers = Headers({"X-GitHub-Event": "check_run", "X-GitHub-Delivery": "test-delivery-id"})

        # Create GithubWebhook instance
        github_webhook = GithubWebhook(hook_data=hook_data, headers=headers, logger=mock_logger)

        # Mock _clone_repository to track if it was called
        with patch.object(github_webhook, "_clone_repository", new=AsyncMock()) as mock_clone:
            # Mock _get_token_metrics to avoid API calls
            with patch.object(github_webhook, "_get_token_metrics", new=AsyncMock(return_value="mock-metrics")):
                # Mock _get_last_commit to provide commit data
                mock_commit = Mock()
                mock_commit.sha = "abc123"
                mock_commit.committer = Mock()
                mock_commit.committer.login = "test-committer"
                with patch.object(github_webhook, "_get_last_commit", new=AsyncMock(return_value=mock_commit)):
                    # Mock CheckRunHandler to avoid actual processing
                    with patch("webhook_server.libs.github_api.CheckRunHandler") as mock_handler_class:
                        mock_handler = AsyncMock()
                        mock_handler.process_pull_request_check_run_webhook_data = AsyncMock(return_value=True)
                        mock_handler_class.return_value = mock_handler

                        # Mock OwnersFileHandler to avoid actual initialization
                        with patch("webhook_server.libs.github_api.OwnersFileHandler") as mock_owners_class:
                            mock_owners = AsyncMock()
                            mock_owners.initialize = AsyncMock(return_value=mock_owners)
                            mock_owners_class.return_value = mock_owners

                            # Mock get_pull_request to return mock PR
                            with patch.object(
                                github_webhook, "get_pull_request", new=AsyncMock(return_value=mock_pull_request)
                            ):
                                # Process the webhook
                                result = await github_webhook.process()

                                # Verify: _clone_repository was called exactly once
                                mock_clone.assert_called_once_with(pull_request=mock_pull_request)

                                # Verify: Result is None (successful processing)
                                assert result is None

                                # Verify: Logger step was called
                                assert github_webhook.logger.step.called
