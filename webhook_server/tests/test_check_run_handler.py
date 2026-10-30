import logging
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from github import GithubException
from github.CheckRun import CheckRun
from github.CommitStatus import CommitStatus
from starlette.datastructures import Headers

from webhook_server.libs.github_api import GithubWebhook
from webhook_server.libs.handlers.check_run_handler import CheckRunHandler
from webhook_server.utils.constants import (
    BUILD_CONTAINER_STR,
    CAN_BE_MERGED_STR,
    CHERRY_PICKED_LABEL,
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
from webhook_server.utils.helpers import TokenProbe


async def _inline_to_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a to_thread target inline so mocks never reach a real worker thread."""
    return fn(*args, **kwargs)


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
        mock_webhook.ctx = None
        mock_webhook.custom_check_runs = []
        mock_webhook.security_suspicious_paths = []
        mock_webhook.security_committer_identity_check = False
        mock_webhook.security_mandatory = False
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

    @pytest.mark.asyncio
    async def test_set_check_queued_verified(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting verified check to queued status using generic method."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_queued(name=VERIFIED_LABEL_STR)
            mock_set_status.assert_called_once_with(check_run=VERIFIED_LABEL_STR, status=QUEUED_STR, output=None)

    @pytest.mark.asyncio
    async def test_set_check_success_verified(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting verified check to success status using generic method."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_success(name=VERIFIED_LABEL_STR)
            mock_set_status.assert_called_once_with(check_run=VERIFIED_LABEL_STR, conclusion=SUCCESS_STR, output=None)

    @pytest.mark.asyncio
    async def test_set_check_queued_tox(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting tox check to queued status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_queued(name=TOX_STR)
            mock_set_status.assert_called_once_with(check_run=TOX_STR, status=QUEUED_STR, output=None)

    @pytest.mark.asyncio
    async def test_set_check_in_progress_tox(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting tox check to in progress status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_in_progress(name=TOX_STR)
            mock_set_status.assert_called_once_with(check_run=TOX_STR, status=IN_PROGRESS_STR)

    @pytest.mark.asyncio
    async def test_set_check_failure_tox(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting tox check to failure status."""
        output = {"title": "Test failed", "summary": "Test summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_failure(name=TOX_STR, output=output)
            mock_set_status.assert_called_once_with(check_run=TOX_STR, conclusion=FAILURE_STR, output=output)

    @pytest.mark.asyncio
    async def test_set_check_success_tox(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting tox check to success status."""
        output = {"title": "Test passed", "summary": "Test summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_success(name=TOX_STR, output=output)
            mock_set_status.assert_called_once_with(check_run=TOX_STR, conclusion=SUCCESS_STR, output=output)

    @pytest.mark.asyncio
    async def test_set_check_queued_pre_commit(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting pre-commit check to queued status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_queued(name=PRE_COMMIT_STR)
            mock_set_status.assert_called_once_with(check_run=PRE_COMMIT_STR, status=QUEUED_STR, output=None)

    @pytest.mark.asyncio
    async def test_set_check_in_progress_pre_commit(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting pre-commit check to in progress status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_in_progress(name=PRE_COMMIT_STR)
            mock_set_status.assert_called_once_with(check_run=PRE_COMMIT_STR, status=IN_PROGRESS_STR)

    @pytest.mark.asyncio
    async def test_set_check_failure_pre_commit(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting pre-commit check to failure status."""
        output = {"title": "Pre-commit failed", "summary": "Pre-commit summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_failure(name=PRE_COMMIT_STR, output=output)
            mock_set_status.assert_called_once_with(check_run=PRE_COMMIT_STR, conclusion=FAILURE_STR, output=output)

    @pytest.mark.asyncio
    async def test_set_check_failure_pre_commit_no_output(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting pre-commit check to failure status without output."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_failure(name=PRE_COMMIT_STR)
            mock_set_status.assert_called_once_with(check_run=PRE_COMMIT_STR, conclusion=FAILURE_STR, output=None)

    @pytest.mark.asyncio
    async def test_set_check_success_pre_commit(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting pre-commit check to success status."""
        output = {"title": "Pre-commit passed", "summary": "Pre-commit summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_success(name=PRE_COMMIT_STR, output=output)
            mock_set_status.assert_called_once_with(check_run=PRE_COMMIT_STR, conclusion=SUCCESS_STR, output=output)

    @pytest.mark.asyncio
    async def test_set_check_success_pre_commit_no_output(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting pre-commit check to success status without output."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_success(name=PRE_COMMIT_STR)
            mock_set_status.assert_called_once_with(check_run=PRE_COMMIT_STR, conclusion=SUCCESS_STR, output=None)

    @pytest.mark.asyncio
    async def test_set_check_queued_can_be_merged(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting can-be-merged check to queued status using generic method."""
        output = {"title": "Merge check", "summary": "Merge summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_queued(name=CAN_BE_MERGED_STR, output=output)
            mock_set_status.assert_called_once_with(check_run=CAN_BE_MERGED_STR, status=QUEUED_STR, output=output)

    @pytest.mark.asyncio
    async def test_set_check_queued_can_be_merged_no_output(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting can-be-merged check to queued status without output using generic method."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_queued(name=CAN_BE_MERGED_STR)
            mock_set_status.assert_called_once_with(check_run=CAN_BE_MERGED_STR, status=QUEUED_STR, output=None)

    @pytest.mark.asyncio
    async def test_set_check_in_progress_can_be_merged(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting can-be-merged check to in progress status using generic method."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_in_progress(name=CAN_BE_MERGED_STR)
            mock_set_status.assert_called_once_with(check_run=CAN_BE_MERGED_STR, status=IN_PROGRESS_STR)

    @pytest.mark.asyncio
    async def test_set_check_success_can_be_merged(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting can-be-merged check to success status using generic method."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_success(name=CAN_BE_MERGED_STR)
            mock_set_status.assert_called_once_with(check_run=CAN_BE_MERGED_STR, conclusion=SUCCESS_STR, output=None)

    @pytest.mark.asyncio
    async def test_set_check_failure_can_be_merged(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting can-be-merged check to failure status using generic method."""
        output = {"title": "Merge failed", "summary": "Merge summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_failure(name=CAN_BE_MERGED_STR, output=output)
            mock_set_status.assert_called_once_with(check_run=CAN_BE_MERGED_STR, conclusion=FAILURE_STR, output=output)

    @pytest.mark.asyncio
    async def test_set_check_queued_container_build(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting container build check to queued status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_queued(name=BUILD_CONTAINER_STR)
            mock_set_status.assert_called_once_with(check_run=BUILD_CONTAINER_STR, status=QUEUED_STR, output=None)

    @pytest.mark.asyncio
    async def test_set_check_in_progress_container_build(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting container build check to in progress status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_in_progress(name=BUILD_CONTAINER_STR)
            mock_set_status.assert_called_once_with(check_run=BUILD_CONTAINER_STR, status=IN_PROGRESS_STR)

    @pytest.mark.asyncio
    async def test_set_check_success_container_build(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting container build check to success status."""
        output = {"title": "Container built", "summary": "Container summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_success(name=BUILD_CONTAINER_STR, output=output)
            mock_set_status.assert_called_once_with(
                check_run=BUILD_CONTAINER_STR, conclusion=SUCCESS_STR, output=output
            )

    @pytest.mark.asyncio
    async def test_set_check_failure_container_build(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting container build check to failure status."""
        output = {"title": "Container build failed", "summary": "Container summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_failure(name=BUILD_CONTAINER_STR, output=output)
            mock_set_status.assert_called_once_with(
                check_run=BUILD_CONTAINER_STR, conclusion=FAILURE_STR, output=output
            )

    @pytest.mark.asyncio
    async def test_set_check_queued_python_module_install(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting python module install check to queued status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_queued(name=PYTHON_MODULE_INSTALL_STR)
            mock_set_status.assert_called_once_with(check_run=PYTHON_MODULE_INSTALL_STR, status=QUEUED_STR, output=None)

    @pytest.mark.asyncio
    async def test_set_check_in_progress_python_module_install(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting python module install check to in progress status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_in_progress(name=PYTHON_MODULE_INSTALL_STR)
            mock_set_status.assert_called_once_with(check_run=PYTHON_MODULE_INSTALL_STR, status=IN_PROGRESS_STR)

    @pytest.mark.asyncio
    async def test_set_check_success_python_module_install(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting python module install check to success status."""
        output = {"title": "Module installed", "summary": "Module summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_success(name=PYTHON_MODULE_INSTALL_STR, output=output)
            mock_set_status.assert_called_once_with(
                check_run=PYTHON_MODULE_INSTALL_STR, conclusion=SUCCESS_STR, output=output
            )

    @pytest.mark.asyncio
    async def test_set_check_failure_python_module_install(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting python module install check to failure status."""
        output = {"title": "Module install failed", "summary": "Module summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_failure(name=PYTHON_MODULE_INSTALL_STR, output=output)
            mock_set_status.assert_called_once_with(
                check_run=PYTHON_MODULE_INSTALL_STR, conclusion=FAILURE_STR, output=output
            )

    @pytest.mark.asyncio
    async def test_set_check_queued_conventional_title(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting conventional title check to queued status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_queued(name=CONVENTIONAL_TITLE_STR)
            mock_set_status.assert_called_once_with(check_run=CONVENTIONAL_TITLE_STR, status=QUEUED_STR, output=None)

    @pytest.mark.asyncio
    async def test_set_check_in_progress_conventional_title(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting conventional title check to in progress status."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_in_progress(name=CONVENTIONAL_TITLE_STR)
            mock_set_status.assert_called_once_with(check_run=CONVENTIONAL_TITLE_STR, status=IN_PROGRESS_STR)

    @pytest.mark.asyncio
    async def test_set_check_success_conventional_title(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting conventional title check to success status."""
        output = {"title": "Title valid", "summary": "Title summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_success(name=CONVENTIONAL_TITLE_STR, output=output)
            mock_set_status.assert_called_once_with(
                check_run=CONVENTIONAL_TITLE_STR, conclusion=SUCCESS_STR, output=output
            )

    @pytest.mark.asyncio
    async def test_set_check_failure_conventional_title(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting conventional title check to failure status."""
        output = {"title": "Title invalid", "summary": "Title summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_failure(name=CONVENTIONAL_TITLE_STR, output=output)
            mock_set_status.assert_called_once_with(
                check_run=CONVENTIONAL_TITLE_STR, conclusion=FAILURE_STR, output=output
            )

    @pytest.mark.asyncio
    async def test_set_check_in_progress_cherry_picked(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting cherry pick check to in progress status using generic method."""
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_in_progress(name=CHERRY_PICKED_LABEL)
            mock_set_status.assert_called_once_with(check_run=CHERRY_PICKED_LABEL, status=IN_PROGRESS_STR)

    @pytest.mark.asyncio
    async def test_set_check_success_cherry_picked(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting cherry pick check to success status using generic method."""
        output = {"title": "Cherry pick successful", "summary": "Cherry pick summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_success(name=CHERRY_PICKED_LABEL, output=output)
            mock_set_status.assert_called_once_with(
                check_run=CHERRY_PICKED_LABEL, conclusion=SUCCESS_STR, output=output
            )

    @pytest.mark.asyncio
    async def test_set_check_failure_cherry_picked(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting cherry pick check to failure status using generic method."""
        output = {"title": "Cherry pick failed", "summary": "Cherry pick summary"}
        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_failure(name=CHERRY_PICKED_LABEL, output=output)
            mock_set_status.assert_called_once_with(
                check_run=CHERRY_PICKED_LABEL, conclusion=FAILURE_STR, output=output
            )

    @pytest.mark.asyncio
    async def test_set_check_run_status_success(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting check run status successfully."""
        with patch.object(
            check_run_handler.github_webhook.repository_by_github_app, "create_check_run", return_value=None
        ):
            with patch.object(check_run_handler.github_webhook.logger, "info") as mock_info:
                await check_run_handler.set_check_run_status(
                    check_run="test-check", status="queued", conclusion="", output=None
                )
                mock_info.assert_not_called()  # Only called for certain conclusions

    @pytest.mark.asyncio
    async def test_set_check_run_status_with_conclusion(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting check run status with conclusion."""
        with patch.object(
            check_run_handler.github_webhook.repository_by_github_app, "create_check_run", return_value=None
        ):
            with patch.object(check_run_handler.github_webhook.logger, "info") as mock_info:
                await check_run_handler.set_check_run_status(
                    check_run="test-check", status="", conclusion="success", output=None
                )
                mock_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_check_run_status_with_output(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting check run status with output."""
        with patch.object(
            check_run_handler.github_webhook.repository_by_github_app, "create_check_run", return_value=None
        ):
            with patch.object(check_run_handler.github_webhook.logger, "info") as mock_info:
                output = {"title": "Test", "summary": "Summary"}
                await check_run_handler.set_check_run_status(
                    check_run="test-check", status="queued", conclusion="", output=output
                )
                mock_info.assert_not_called()

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

    @pytest.mark.asyncio
    async def test_set_check_run_status_fallback_drops_rejected_output(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """A payload GitHub rejected is retried without its detail so the check still completes.

        Retrying the identical oversized output fails the same way and leaves the check stuck
        in_progress with nothing reported.
        """
        attempts: list[dict[str, Any]] = []

        def create_check_run_side_effect(*args: object, **kwargs: Any) -> None:
            attempts.append(kwargs)
            if len(attempts) == 1:
                raise Exception("summary exceeds a maximum bytesize of 65535")
            return None

        with patch.object(
            check_run_handler.github_webhook.repository_by_github_app,
            "create_check_run",
            side_effect=create_check_run_side_effect,
        ):
            await check_run_handler.set_check_run_status(
                check_run="tox",
                conclusion=FAILURE_STR,
                output={"title": "Tox", "summary": "reason", "text": "x" * 70000},
            )

        assert len(attempts) == 2
        assert attempts[1]["conclusion"] == FAILURE_STR
        assert attempts[1]["status"] == "completed"
        assert attempts[1]["output"] == {"title": "Tox", "summary": "reason"}
        assert "text" not in attempts[1]["output"]

    @pytest.mark.asyncio
    async def test_set_check_run_status_fallback_failure_is_logged_not_raised(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """When even the stripped retry fails, the error is logged instead of blowing up the check."""

        def create_check_run_side_effect(*args: object, **kwargs: Any) -> None:
            raise Exception("API is down")

        with patch.object(
            check_run_handler.github_webhook.repository_by_github_app,
            "create_check_run",
            side_effect=create_check_run_side_effect,
        ):
            with patch.object(check_run_handler.logger, "exception") as mock_exception:
                await check_run_handler.set_check_run_status(
                    check_run="tox", conclusion=FAILURE_STR, output={"title": "Tox"}
                )
                assert mock_exception.call_count == 2

    def test_get_check_run_text_normal_length(self, check_run_handler: CheckRunHandler) -> None:
        """Test getting check run text with normal length."""
        err = "Error message"
        out = "Output message"

        result = check_run_handler.get_check_run_text(err, out)

        expected = "```\nError message\n\nOutput message\n```"
        assert result == expected

    def test_get_check_run_text_long_length(self, check_run_handler: CheckRunHandler) -> None:
        """Output over the GitHub limit keeps the tail, the closing fence and an elision marker."""
        # Every line carries a 3-byte glyph: at 65534 characters this used to take the payload
        # over GitHub's 65535 *byte* cap, which silently ate the end of the log.
        long_out = ("tox: OK \u2714 / FAIL \u2716\n" * 5000) + "FAILED test_x.py::test_y\n"

        result = check_run_handler.get_check_run_text("Error line", long_out)

        assert len(result.encode("utf-8")) <= 65535, "Payload must stay within GitHub's byte limit"
        assert result.startswith("```\n")
        assert result.endswith("\n```"), "Closing fence must never be truncated away"
        assert "FAILED test_x.py::test_y" in result, "Tail of the log must survive truncation"
        assert "bytes truncated from the start" in result

    def test_get_check_run_text_truncation_keeps_tail_not_head(self, check_run_handler: CheckRunHandler) -> None:
        """The tail is the verdict, so it is what survives; setup noise at the head goes first."""
        out = "SETUP-NOISE\n" + "\n".join(f"PASSED test_{i}" for i in range(6000)) + "\nREAL-FAILURE-REASON\n"

        result = check_run_handler.get_check_run_text("", out)

        assert "REAL-FAILURE-REASON" in result
        assert "SETUP-NOISE" not in result
        assert "bytes truncated from the start" in result
        assert len(result.encode("utf-8")) <= 65535

    def test_get_check_run_text_caps_a_single_huge_line(self, check_run_handler: CheckRunHandler) -> None:
        """One pathological line must not eat the budget - it is collapsed in place instead.

        A 70 000-character line used to consume the whole window, leaving room for nothing else.
        """
        out = "SETUP-NOISE\n" + ("x" * 70_000) + "\nREAL-FAILURE-REASON\n"

        result = check_run_handler.get_check_run_text("", out)

        assert "[line 70000 chars]" in result
        assert "REAL-FAILURE-REASON" in result
        assert len(result.encode("utf-8")) <= 65535

    def test_get_check_run_text_keeps_failure_section_ahead_of_the_tail(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """A failure section buried behind thousands of passing lines is kept, not truncated away."""
        out = (
            "\n".join(f"PASSED test_{i}" for i in range(5000))
            + "\n=================================== FAILURES ===================================\n"
            + "_____________________ test_buried_failure _____________________\n"
            + "tests/test_x.py:5: in test_buried_failure\n"
            + "E   AssertionError: "
            + "y" * 40_000
            + "\n"
            + "\n".join(f"COVERAGE-LINE {i}" for i in range(2000))
            + "\nVERDICT-LINE\n"
        )

        result = check_run_handler.get_check_run_text("", out)

        assert result.index("==== FAILURES ====") < 200, "failure section must come first"
        assert "test_buried_failure" in result
        assert "VERDICT-LINE" in result, "the verdict at the end must still be there"
        assert len(result.encode("utf-8")) <= 65535

    def test_get_check_run_text_failure_section_over_budget(self, check_run_handler: CheckRunHandler) -> None:
        """When the failure section itself is over budget, keep its head and name the cut.

        Its tail is the end of the log, which is kept anyway, so keeping it here would print the
        same bytes twice.
        """
        out = (
            "\n".join(f"PASSED test_{i}" for i in range(3000))
            + "\n==================== FAILURES ====================\n"
            + "\n".join(f"failure detail {i}" for i in range(20000))
            + "\nVERDICT-LINE\n"
        )

        result = check_run_handler.get_check_run_text("", out)

        assert "failure section truncated:" in result
        assert "==================== FAILURES ====================" in result
        assert result.count("VERDICT-LINE") == 1, "the log tail must not be duplicated"

        # The kept head must also appear exactly once - the old head+tail split printed it twice.
        detail = next(line for line in result.splitlines() if line.startswith("failure detail "))
        assert result.count(detail) == 1 or len(result.splitlines()) < 10
        assert len(result.encode("utf-8")) <= 65535

    def test_get_check_run_text_fills_the_budget(self, check_run_handler: CheckRunHandler) -> None:
        """A small failed-test list must not leave most of the allowance unused.

        A 300 KB failure section plus a one-line failed list used to post 28 KB of a 65 KB
        allowance because the failure section was capped independently of the list.
        """
        out = (
            "\n".join(f"PASSED test_{i}" for i in range(1000))
            + "\n==================== FAILURES ===================="
            + "\n"
            + "\n".join(f"failure detail {i} " + "z" * 60 for i in range(3000))
            + "\n=========================== short test summary info ============================"
            + "\nFAILED tests/test_one.py::test_it - AssertionError: boom"
            + "\n1 failed, 1000 passed in 2.51s"
            + "\n"
        )

        result = check_run_handler.get_check_run_text("", out)
        size = len(result.encode())

        assert size <= 65535
        assert size > 60_000, f"budget left unused: only {size} bytes posted"
        assert result.startswith("```\n==================== FAILURES ====")
        assert "FAILED tests/test_one.py::test_it" in result

    def test_get_check_run_text_logs_full_redacted_output_before_truncation(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that full redacted output is logged via debug before truncation.

        The debug log must fire AFTER redaction to avoid leaking secrets to log
        aggregation systems, and BEFORE truncation so the complete build output
        is preserved in server logs.
        """
        long_err = "\n".join("E" * 200 for _ in range(50))
        long_out = "\n".join("O" * 200 for _ in range(500))

        check_run_handler.get_check_run_text(long_err, long_out)

        # Verify debug was called with the full output
        debug_calls = [str(call) for call in check_run_handler.logger.debug.call_args_list]
        full_output_logged = any("Full check run output" in call and "GitHub limit is" in call for call in debug_calls)
        assert full_output_logged, "Expected debug log with full output before truncation"

    def test_get_check_run_text_logs_truncation_size_at_info(self, check_run_handler: CheckRunHandler) -> None:
        """Truncation is recorded at INFO so the server log says what GitHub did not receive."""
        check_run_handler.get_check_run_text("", "\n".join("O" * 200 for _ in range(500)))

        info_calls = [str(call) for call in check_run_handler.logger.info.call_args_list]
        assert any("exceeds the GitHub limit" in call for call in info_calls)
        assert any("dropped" in call for call in info_calls)

    def test_get_check_run_text_full_log_has_secrets_redacted(self, check_run_handler: CheckRunHandler) -> None:
        """Test that the full output debug log redacts secrets before logging."""
        long_out = "\n".join("O" * 200 for _ in range(500))
        err_with_secret = "Error: auth failed with token test-token and user test-user"  # pragma: allowlist secret

        check_run_handler.get_check_run_text(err_with_secret, long_out)

        # Verify the debug log does NOT contain the raw secrets
        debug_calls = [str(call) for call in check_run_handler.logger.debug.call_args_list]
        full_log_calls = [c for c in debug_calls if "Full check run output" in c]
        assert full_log_calls, "Expected full output debug log"
        for call in full_log_calls:
            assert "test-token" not in call, "Secret token leaked in debug log"
            assert "test-user" not in call, "Username leaked in debug log"
            assert "*****" in call, "Expected redacted placeholder in debug log"

    def test_get_check_run_text_no_log_when_not_truncated(self, check_run_handler: CheckRunHandler) -> None:
        """Test that no full output debug log is emitted when output fits within limit."""
        err = "Short error"
        out = "Short output"

        check_run_handler.logger.debug.reset_mock()
        check_run_handler.get_check_run_text(err, out)

        # Debug should NOT contain "Full check run output" for short text
        debug_calls = [str(call) for call in check_run_handler.logger.debug.call_args_list]
        full_output_logged = any("Full check run output" in call for call in debug_calls)
        assert not full_output_logged, "Should not log full output when no truncation needed"

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

    def test_get_check_run_summary_empty_streams(self, check_run_handler: CheckRunHandler) -> None:
        """No output means no summary rather than an empty code fence."""
        assert check_run_handler.get_check_run_summary("", "") == ""
        assert check_run_handler.get_check_run_summary("   \n", "\n") == ""

    def test_get_check_run_summary_is_tail_only(self, check_run_handler: CheckRunHandler) -> None:
        """The summary is the end of each stream - the part that says why a check failed."""
        out = "\n".join(f"PASSED test_{i}" for i in range(500)) + "\nFAILED test_last.py::test_x"

        summary = check_run_handler.get_check_run_summary("", out)

        assert summary.startswith("~~~\n")
        assert summary.endswith("\n~~~")
        assert "FAILED test_last.py::test_x" in summary
        assert "PASSED test_0" not in summary, "Summary must not carry the whole log"
        assert len(summary.encode("utf-8")) <= 65535

    def test_get_check_run_summary_includes_stderr(self, check_run_handler: CheckRunHandler) -> None:
        """Both streams can carry the reason, so the excerpt covers both."""
        summary = check_run_handler.get_check_run_summary("Traceback: boom", "collected 3 items")

        assert "collected 3 items" in summary
        assert "Traceback: boom" in summary

    def test_get_check_run_summary_leads_with_the_failure_section(self, check_run_handler: CheckRunHandler) -> None:
        """The inline summary must state the failure, not just the last lines of noise."""
        out = (
            "\n".join(f"PASSED test_{i}" for i in range(300))
            + "\n==================== FAILURES ====================\n"
            + "_____________________ test_the_real_problem _____________________\n"
            + "tests/test_x.py:9: in test_the_real_problem\n"
            + "E   AssertionError: expected 1 got 2\n"
            + "\n".join(f"coverage noise {i}" for i in range(200))
            + "\n1 failed, 300 passed\n"
        )

        summary = check_run_handler.get_check_run_summary("", out)

        assert "test_the_real_problem" in summary
        assert "expected 1 got 2" in summary
        assert summary.index("FAILURES") < 200
        assert "1 failed, 300 passed" in summary, "the verdict must still be visible"

    def test_get_check_run_title_reports_the_verdict(self, check_run_handler: CheckRunHandler) -> None:
        """The heading must not repeat the check name GitHub already shows above it."""
        assert check_run_handler.get_check_run_title("Tox", "", "16 passed", passed=True) == "Tox passed"
        assert check_run_handler.get_check_run_title("Tox", "", "boom", passed=False) == "Tox failed"

    def test_get_check_run_title_uses_the_failure_line(self, check_run_handler: CheckRunHandler) -> None:
        """On failure the heading names what failed."""
        out = "PASSED a\nFAILED tests/test_x.py::test_y\n  evaluation failed :("

        title = check_run_handler.get_check_run_title("Tox", "", out, passed=False)

        assert title == "FAILED tests/test_x.py::test_y"

    def test_get_check_run_title_counts_hundreds_of_failures(self, check_run_handler: CheckRunHandler) -> None:
        """With more failures than the window can show, the count is the useful heading."""
        out = "FAILED tests/test_x.py::test_0\nFAILED tests/test_x.py::test_399\n400 failed, 16 passed in 2.51s"

        title = check_run_handler.get_check_run_title("Tox", "", out, passed=False)

        assert title == "400 failed, 16 passed in 2.51s"

    def test_get_check_run_text_keeps_the_whole_failed_list(self, check_run_handler: CheckRunHandler) -> None:
        """Hundreds of failures: the list of what failed must survive whole, not cut mid-list."""
        failures = "\n".join(
            f"FAILED tests/test_many.py::test_failure_{i:03d} - AssertionError: E2E-FAIL-{i:03d}" for i in range(400)
        )
        out = (
            "\n".join(f"PASSED test_{i}" for i in range(2000))
            + "\n==================== FAILURES ====================\n"
            + "\n".join(f"failure detail {i} " + "z" * 60 for i in range(3000))
            + "\n=========================== short test summary info ============================\n"
            + failures
            + "\n400 failed, 16 passed in 2.51s\n"
        )

        result = check_run_handler.get_check_run_text("", out)

        assert result.count("FAILED tests/test_many.py::test_failure_") == 400, "failed list must be complete"
        assert "test_failure_000" in result and "test_failure_399" in result
        assert "==== FAILURES ====" in result, "the first failure detail must lead the page"
        assert "400 failed, 16 passed" in result
        assert len(result.encode("utf-8")) <= 65535

    def test_get_check_run_summary_redacts_secrets(self, check_run_handler: CheckRunHandler) -> None:
        """The summary is posted to GitHub, so it gets the same redaction as the text."""
        summary = check_run_handler.get_check_run_summary("failed with token test-token", "")

        assert "test-token" not in summary
        assert "*****" in summary

    def test_get_check_run_summary_survives_backtick_fences(self, check_run_handler: CheckRunHandler) -> None:
        """Output containing ``` must not break the summary fence."""
        summary = check_run_handler.get_check_run_summary("", "```\nnot a fence\n```")

        assert summary.startswith("~~~\n")
        assert summary.endswith("\n~~~")
        assert "not a fence" in summary

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
    async def test_branch_protection_requires_up_to_date_strict(self, check_run_handler: CheckRunHandler) -> None:
        """Test that the strict required status checks setting is read from GitHub."""
        mock_pull_request = Mock()
        mock_pull_request.base.ref = "main"
        mock_branch_protection = Mock()
        mock_branch_protection.required_status_checks.strict = True
        mock_branch_protection.required_status_checks.contexts = ["can-be-merged"]

        with (
            patch("asyncio.to_thread", new=_inline_to_thread),
            patch.object(check_run_handler.repository, "get_branch", return_value=Mock()),
            patch.object(
                check_run_handler.repository.get_branch.return_value,  # type: ignore[attr-defined]
                "get_protection",
                return_value=mock_branch_protection,
            ),
        ):
            assert await check_run_handler.branch_protection_requires_up_to_date(mock_pull_request) is True

    @pytest.mark.asyncio
    async def test_branch_protection_requires_up_to_date_not_strict(self, check_run_handler: CheckRunHandler) -> None:
        """Test that strict=False is reported as False."""
        mock_pull_request = Mock()
        mock_pull_request.base.ref = "main"
        mock_branch_protection = Mock()
        mock_branch_protection.required_status_checks.strict = False

        with (
            patch("asyncio.to_thread", new=_inline_to_thread),
            patch.object(check_run_handler.repository, "get_branch", return_value=Mock()),
            patch.object(
                check_run_handler.repository.get_branch.return_value,  # type: ignore[attr-defined]
                "get_protection",
                return_value=mock_branch_protection,
            ),
        ):
            assert await check_run_handler.branch_protection_requires_up_to_date(mock_pull_request) is False

    @pytest.mark.asyncio
    async def test_branch_protection_requires_up_to_date_no_required_status_checks(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that protection without required status checks cannot gate merging."""
        mock_pull_request = Mock()
        mock_pull_request.base.ref = "main"
        mock_branch_protection = Mock()
        mock_branch_protection.required_status_checks = None

        with (
            patch("asyncio.to_thread", new=_inline_to_thread),
            patch.object(check_run_handler.repository, "get_branch", return_value=Mock()),
            patch.object(
                check_run_handler.repository.get_branch.return_value,  # type: ignore[attr-defined]
                "get_protection",
                return_value=mock_branch_protection,
            ),
        ):
            assert await check_run_handler.branch_protection_requires_up_to_date(mock_pull_request) is False

    @pytest.mark.asyncio
    async def test_branch_protection_requires_up_to_date_unreadable_protection(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that unreadable branch protection returns None instead of a guessed value."""
        mock_pull_request = Mock()
        mock_pull_request.base.ref = "main"
        mock_branch = Mock()
        mock_branch.get_protection.side_effect = GithubException(404, "Branch not protected", None)

        with (
            patch("asyncio.to_thread", new=_inline_to_thread),
            patch.object(check_run_handler.repository, "get_branch", return_value=mock_branch),
        ):
            assert await check_run_handler.branch_protection_requires_up_to_date(mock_pull_request) is None

    @pytest.mark.asyncio
    async def test_branch_protection_requires_up_to_date_permission_error_is_unknown(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that a permissions error is reported as unknown, not as a missing setting."""
        mock_pull_request = Mock()
        mock_pull_request.base.ref = "main"
        mock_branch = Mock()
        mock_branch.get_protection.side_effect = GithubException(403, "Resource not accessible", None)

        with (
            patch("asyncio.to_thread", new=_inline_to_thread),
            patch.object(check_run_handler.repository, "get_branch", return_value=mock_branch),
        ):
            assert await check_run_handler.branch_protection_requires_up_to_date(mock_pull_request) is None

    @pytest.mark.asyncio
    async def test_branch_protection_requires_up_to_date_private_repo_is_not_skipped(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that private repositories still get the strict setting, unlike contexts."""
        mock_pull_request = Mock()
        mock_pull_request.base.ref = "main"
        mock_branch_protection = Mock()
        mock_branch_protection.required_status_checks.strict = True
        mock_branch = Mock()
        mock_branch.get_protection.return_value = mock_branch_protection

        with (
            patch("asyncio.to_thread", new=_inline_to_thread),
            patch.object(check_run_handler.repository, "private", True),
            patch.object(check_run_handler.repository, "get_branch", return_value=mock_branch),
        ):
            assert await check_run_handler.branch_protection_requires_up_to_date(mock_pull_request) is True

    @pytest.mark.asyncio
    async def test_branch_protection_is_fetched_once_per_handler(self, check_run_handler: CheckRunHandler) -> None:
        """Test that both the contexts and the strict setting share a single protection fetch."""
        mock_pull_request = Mock()
        mock_pull_request.base.ref = "main"
        mock_branch_protection = Mock()
        mock_branch_protection.required_status_checks.strict = True
        mock_branch_protection.required_status_checks.contexts = ["can-be-merged"]
        mock_branch = Mock()
        mock_branch.get_protection.return_value = mock_branch_protection

        with (
            patch("asyncio.to_thread", new=_inline_to_thread),
            patch.object(check_run_handler.repository, "private", False),
            patch.object(check_run_handler.repository, "get_branch", return_value=mock_branch),
        ):
            contexts = await check_run_handler.get_branch_required_status_checks(mock_pull_request)
            strict = await check_run_handler.branch_protection_requires_up_to_date(mock_pull_request)

        assert contexts == ["can-be-merged"]
        assert strict is True
        mock_branch.get_protection.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_branch_required_status_checks_protection_read_error_fails_closed(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that an unreadable branch protection raises instead of reporting no required checks.

        Returning an empty list here would let check_if_can_be_merged pass while GitHub-required
        checks were never evaluated, so the error must propagate to the failure handler.
        """
        mock_pull_request = Mock()
        mock_pull_request.base.ref = "main"
        mock_branch = Mock()
        mock_branch.get_protection.side_effect = GithubException(404, "Branch not protected", None)

        with (
            patch("asyncio.to_thread", new=_inline_to_thread),
            patch.object(check_run_handler.repository, "private", False),
            patch.object(check_run_handler.repository, "get_branch", return_value=mock_branch),
        ):
            with pytest.raises(GithubException):
                await check_run_handler.get_branch_required_status_checks(mock_pull_request)

        # The failure must not be cached as an empty answer for the rest of the event
        assert "main" not in check_run_handler._branch_required_status_checks

    @pytest.mark.asyncio
    async def test_get_branch_required_status_checks_no_required_status_checks(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that protection without required status checks yields no contexts."""
        mock_pull_request = Mock()
        mock_pull_request.base.ref = "main"
        mock_branch_protection = Mock()
        mock_branch_protection.required_status_checks = None
        mock_branch = Mock()
        mock_branch.get_protection.return_value = mock_branch_protection

        with (
            patch("asyncio.to_thread", new=_inline_to_thread),
            patch.object(check_run_handler.repository, "private", False),
            patch.object(check_run_handler.repository, "get_branch", return_value=mock_branch),
        ):
            assert await check_run_handler.get_branch_required_status_checks(mock_pull_request) == []

    @pytest.mark.asyncio
    async def test_branch_protection_caches_are_keyed_by_base_ref(self, check_run_handler: CheckRunHandler) -> None:
        """Test that two base branches in one handler never share protection rules.

        The post-merge pass reuses one handler for every open pull request, so a cache keyed by
        anything other than the base ref would evaluate a PR against another branch's settings.
        """
        pr_main = Mock()
        pr_main.base.ref = "main"
        pr_release = Mock()
        pr_release.base.ref = "release"

        main_protection = Mock()
        main_protection.required_status_checks.strict = True
        main_protection.required_status_checks.contexts = ["main-check"]
        release_protection = Mock()
        release_protection.required_status_checks.strict = False
        release_protection.required_status_checks.contexts = ["release-check"]

        main_branch = Mock()
        main_branch.get_protection.return_value = main_protection
        release_branch = Mock()
        release_branch.get_protection.return_value = release_protection

        def _branch_for(ref: str) -> Mock:
            return main_branch if ref == "main" else release_branch

        with (
            patch("asyncio.to_thread", new=_inline_to_thread),
            patch.object(check_run_handler.repository, "private", False),
            patch.object(
                check_run_handler.repository,
                "get_branch",
                side_effect=_branch_for,
            ),
        ):
            assert await check_run_handler.get_branch_required_status_checks(pr_main) == ["main-check"]
            assert await check_run_handler.get_branch_required_status_checks(pr_release) == ["release-check"]
            assert await check_run_handler.branch_protection_requires_up_to_date(pr_main) is True
            assert await check_run_handler.branch_protection_requires_up_to_date(pr_release) is False

        # Each base ref read its own protection exactly once
        main_branch.get_protection.assert_called_once()
        release_branch.get_protection.assert_called_once()

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
        mock_get_api.return_value = (Mock(), "test-token", TokenProbe(login="test-user", remaining=5000, limit=5000))
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
        mock_get_api.return_value = (Mock(), "test-token", TokenProbe(login="test-user", remaining=5000, limit=5000))
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
        mock_get_api.return_value = (Mock(), "test-token", TokenProbe(login="test-user", remaining=5000, limit=5000))
        mock_get_repo.return_value = Mock()
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"

        # Setup webhook data for completed normal check
        hook_data = {
            "action": "completed",
            "check_run": {
                "name": "test-check",
                "status": "completed",
                "conclusion": "success",
                "head_sha": "abc123",
            },
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
                            with (
                                patch("webhook_server.libs.github_api.PullRequestHandler") as mock_pr_handler_class,
                                patch(
                                    "webhook_server.libs.github_api.is_stale_for_pr",
                                    new_callable=AsyncMock,
                                    return_value=False,
                                ),
                                patch("webhook_server.libs.github_api._merge_check_debouncer") as mock_debouncer,
                            ):
                                mock_pr_handler = AsyncMock()
                                mock_pr_handler.check_if_can_be_merged = AsyncMock()
                                mock_pr_handler_class.return_value = mock_pr_handler

                                async def immediate_schedule(
                                    repo_full_name: str,
                                    pr_number: int,
                                    callback: Callable[[], Awaitable[None]],
                                    logger: logging.Logger,
                                    log_prefix: str,
                                ) -> None:
                                    await callback()

                                mock_debouncer.schedule = AsyncMock(side_effect=immediate_schedule)

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
        mock_get_api.return_value = (Mock(), "test-token", TokenProbe(login="test-user", remaining=5000, limit=5000))
        mock_get_repo.return_value = Mock()
        mock_get_app_api.return_value = Mock()
        mock_color.return_value = "test-repo"

        # Setup webhook data for can-be-merged with success conclusion
        hook_data = {
            "action": "completed",
            "check_run": {
                "name": CAN_BE_MERGED_STR,
                "status": "completed",
                "conclusion": "success",
                "head_sha": "abc123",
            },
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
                        with (
                            patch("webhook_server.libs.github_api.OwnersFileHandler") as mock_owners_class,
                            patch(
                                "webhook_server.libs.github_api.is_stale_for_pr",
                                new_callable=AsyncMock,
                                return_value=False,
                            ),
                        ):
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
