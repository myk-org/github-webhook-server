from unittest.mock import AsyncMock, Mock, patch

import pytest
from github.GithubException import GithubException

from webhook_server.libs.graphql.graphql_client import GraphQLError
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
        mock_webhook.repository.full_name = "test-owner/test-repo"
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
        # Mock unified_api
        mock_webhook.unified_api = AsyncMock()
        mock_webhook.unified_api.create_check_run = AsyncMock()
        mock_webhook.unified_api.get_commit_check_runs = AsyncMock(return_value=[])
        mock_webhook.unified_api.get_branch_protection = AsyncMock()
        return mock_webhook

    @pytest.fixture
    def check_run_handler(self, mock_github_webhook: Mock) -> CheckRunHandler:
        """Create a CheckRunHandler instance with mocked dependencies."""
        return CheckRunHandler(mock_github_webhook)

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
            # Verify assertion synchronously (not chunked streaming - all data loaded at once)
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
        with patch.object(check_run_handler.github_webhook.logger, "step") as mock_step:
            await check_run_handler.set_check_run_status(
                check_run="test-check", status="queued", conclusion="", output=None
            )
            # Verify step was called for queued status
            mock_step.assert_called()

    @pytest.mark.asyncio
    async def test_set_check_run_status_with_conclusion(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting check run status with conclusion."""
        with patch.object(check_run_handler.github_webhook.logger, "step") as mock_step:
            await check_run_handler.set_check_run_status(
                check_run="test-check", status="", conclusion="success", output=None
            )
            # Verify step was called for success conclusion
            mock_step.assert_called()

    @pytest.mark.asyncio
    async def test_set_check_run_status_with_output(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting check run status with output."""
        with patch.object(check_run_handler.github_webhook.logger, "step") as mock_step:
            output = {"title": "Test", "summary": "Summary"}
            await check_run_handler.set_check_run_status(
                check_run="test-check", status="queued", conclusion="", output=output
            )
            # Verify step was called for queued status with output
            mock_step.assert_called()

    @pytest.mark.asyncio
    async def test_set_check_run_status_exception_handling(self, check_run_handler: CheckRunHandler) -> None:
        """Test that generic exceptions don't retry (to prevent cascading failures)."""
        check_run_handler.github_webhook.unified_api.create_check_run = AsyncMock(
            side_effect=Exception("Generic API Error")
        )
        with patch.object(check_run_handler.github_webhook.logger, "exception") as mock_exception:
            await check_run_handler.set_check_run_status(
                check_run="test-check", status="queued", conclusion="", output=None
            )
            # Should be called once - no retry for generic exceptions
            assert check_run_handler.github_webhook.unified_api.create_check_run.call_count == 1
            mock_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_check_run_status_auth_error_no_retry(self, check_run_handler: CheckRunHandler) -> None:
        """Test that auth/permission errors don't retry."""

        check_run_handler.github_webhook.unified_api.create_check_run = AsyncMock(
            side_effect=GraphQLError("401 Unauthorized")
        )
        with patch.object(check_run_handler.github_webhook.logger, "exception") as mock_exception:
            with pytest.raises(GraphQLError):
                await check_run_handler.set_check_run_status(
                    check_run="test-check", status="queued", conclusion="", output=None
                )
            # Should be called once - no retry for auth errors
            assert check_run_handler.github_webhook.unified_api.create_check_run.call_count == 1
            mock_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_check_run_status_transient_error_logged_without_retry(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that non-critical GraphQL errors are logged without retry to prevent cascading failures."""

        check_run_handler.github_webhook.unified_api.create_check_run = AsyncMock(
            side_effect=GraphQLError("Network timeout")
        )
        with patch.object(check_run_handler.github_webhook.logger, "exception") as mock_exception:
            await check_run_handler.set_check_run_status(
                check_run="test-check", status="queued", conclusion="", output=None
            )
            # Should be called once only - no retry to prevent cascading failures
            check_run_handler.github_webhook.unified_api.create_check_run.assert_called_once()
            mock_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_check_run_status_in_progress_triggers_success_log(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that in-progress status triggers info logging."""
        with patch.object(check_run_handler.github_webhook.logger, "info") as mock_info:
            await check_run_handler.set_check_run_status(
                check_run="test-check", status=IN_PROGRESS_STR, conclusion="", output=None
            )
            # Should call info logger for in-progress status
            mock_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_check_run_status_creates_check_run_with_correct_parameters(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that create_check_run is called with correct parameters."""
        test_output = {"title": "Test Output", "summary": "Test summary"}

        await check_run_handler.set_check_run_status(
            check_run="test-check",
            status="queued",
            conclusion="success",
            output=test_output,
        )

        # Verify create_check_run was called with expected kwargs
        check_run_handler.github_webhook.unified_api.create_check_run.assert_called_once()
        call_args = check_run_handler.github_webhook.unified_api.create_check_run.call_args
        call_kwargs = call_args[1]

        # Assert that first positional arg is repository_by_github_app (App-scoped REST)
        assert call_args[0][0] == check_run_handler.github_webhook.repository_by_github_app

        assert call_kwargs["name"] == "test-check"
        assert call_kwargs["head_sha"] == "test-sha"
        assert call_kwargs["status"] == "queued"
        assert call_kwargs["conclusion"] == "success"
        assert call_kwargs["output"] == test_output

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

        # Should be truncated to 65534 characters
        assert len(result) == 65534
        assert result.startswith("```\n")

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

        # Mock unified_api.get_commit_check_runs instead of direct last_commit.get_check_runs
        check_run_handler.github_webhook.unified_api.get_commit_check_runs = AsyncMock(return_value=[mock_check_run])
        result = await check_run_handler.is_check_run_in_progress("test-check")
        assert result is True

    @pytest.mark.asyncio
    async def test_is_check_run_in_progress_false(self, check_run_handler: CheckRunHandler) -> None:
        """Test checking if check run is in progress - returns False."""
        mock_check_run = Mock()
        mock_check_run.name = "test-check"
        mock_check_run.status = "completed"

        # Mock unified_api.get_commit_check_runs instead of direct last_commit.get_check_runs
        check_run_handler.github_webhook.unified_api.get_commit_check_runs = AsyncMock(return_value=[mock_check_run])
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
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        mock_check_run = Mock()
        mock_check_run.name = "test-check"
        mock_check_run.conclusion = FAILURE_STR

        with patch.object(check_run_handler, "all_required_status_checks", return_value=["test-check"]):
            result = await check_run_handler.required_check_failed_or_no_status(mock_pull_request, [mock_check_run], [])

            assert "test-check" in result

    @pytest.mark.asyncio
    async def test_all_required_status_checks(self, check_run_handler: CheckRunHandler) -> None:
        """Test getting all required status checks."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123

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
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        mock_pull_request.base.ref = "main"
        mock_branch_protection = Mock()
        mock_branch_protection.required_status_checks.contexts = ["branch-check-1", "branch-check-2"]
        with patch.object(check_run_handler.repository, "private", False):
            check_run_handler.repository.full_name = "test/repo"
            check_run_handler.github_webhook.unified_api.get_branch_protection = AsyncMock(
                return_value=mock_branch_protection
            )
            result = await check_run_handler.get_branch_required_status_checks(mock_pull_request)
            assert result == ["branch-check-1", "branch-check-2"]

    @pytest.mark.asyncio
    async def test_get_branch_required_status_checks_404_not_found(self, check_run_handler: CheckRunHandler) -> None:
        """Test getting branch required status checks when branch protection returns 404."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        mock_pull_request.base.ref = "main"

        with patch.object(check_run_handler.repository, "private", False):
            check_run_handler.repository.full_name = "test/repo"
            # Simulate 404 exception when branch protection is not configured
            check_run_handler.github_webhook.unified_api.get_branch_protection = AsyncMock(
                side_effect=GithubException(status=404, data={"message": "Branch not protected"}, headers={})
            )
            with patch.object(check_run_handler.github_webhook.logger, "debug") as mock_debug:
                result = await check_run_handler.get_branch_required_status_checks(mock_pull_request)
                assert result == []
                # Verify debug log was called for 404 case
                mock_debug.assert_called()
                debug_call_args = mock_debug.call_args[0][0]
                assert "No branch protection configured" in debug_call_args

    @pytest.mark.asyncio
    async def test_get_branch_required_status_checks_private_repo(self, check_run_handler: CheckRunHandler) -> None:
        """Test getting branch required status checks for private repository."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        with patch.object(check_run_handler.repository, "private", True):
            with patch.object(check_run_handler.github_webhook.logger, "info") as mock_info:
                result = await check_run_handler.get_branch_required_status_checks(mock_pull_request)
                assert result == []
                mock_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_branch_required_status_checks_none_status_checks(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test getting branch required status checks when required_status_checks is None."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        mock_pull_request.base.ref = "main"
        mock_branch_protection = Mock()
        mock_branch_protection.required_status_checks = None  # Simulate no status checks configured
        with patch.object(check_run_handler.repository, "private", False):
            check_run_handler.repository.full_name = "test/repo"
            check_run_handler.github_webhook.unified_api.get_branch_protection = AsyncMock(
                return_value=mock_branch_protection
            )
            result = await check_run_handler.get_branch_required_status_checks(mock_pull_request)
            assert result == []

    @pytest.mark.asyncio
    async def test_get_branch_required_status_checks_none_contexts(self, check_run_handler: CheckRunHandler) -> None:
        """Test getting branch required status checks when contexts is None."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        mock_pull_request.base.ref = "main"
        mock_branch_protection = Mock()
        mock_required_status_checks = Mock()
        mock_required_status_checks.contexts = None  # Simulate contexts being None
        mock_branch_protection.required_status_checks = mock_required_status_checks
        with patch.object(check_run_handler.repository, "private", False):
            check_run_handler.repository.full_name = "test/repo"
            check_run_handler.github_webhook.unified_api.get_branch_protection = AsyncMock(
                return_value=mock_branch_protection
            )
            result = await check_run_handler.get_branch_required_status_checks(mock_pull_request)
            assert result == []

    @pytest.mark.asyncio
    async def test_required_check_in_progress(self, check_run_handler: CheckRunHandler) -> None:
        """Test checking for required checks in progress."""
        mock_pull_request = Mock()
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
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
        mock_pull_request.id = "PR_kgDOTestId"
        mock_pull_request.number = 123
        mock_check_run = Mock()
        mock_check_run.name = CAN_BE_MERGED_STR
        mock_check_run.status = IN_PROGRESS_STR

        with patch.object(check_run_handler, "all_required_status_checks", return_value=[CAN_BE_MERGED_STR]):
            msg, in_progress_checks = await check_run_handler.required_check_in_progress(
                mock_pull_request, [mock_check_run]
            )

            assert msg == ""
            assert in_progress_checks == []

    def test_get_check_run_text_strips_ansi_codes(self, check_run_handler: CheckRunHandler) -> None:
        """Test get_check_run_text strips ANSI escape codes from output."""
        # Simulate tox output with ANSI color codes
        err = "\x1b[31mERROR:\x1b[0m Test failed"
        out = "\x1b[32mSUCCESS:\x1b[0m \x1b[1mBold text\x1b[0m"

        result = check_run_handler.get_check_run_text(err, out)

        # Should not contain ANSI escape codes
        assert "\x1b" not in result
        # Should contain actual text content
        assert "ERROR: Test failed" in result
        assert "SUCCESS: Bold text" in result
        # Should be wrapped in code block
        assert result.startswith("```")
        assert result.endswith("```")
