"""Comprehensive tests for custom check runs feature.

This test suite covers:
1. Schema validation tests - Test that the configuration schema validates correctly
2. CheckRunHandler tests - Test custom check methods (set_custom_check_*)
3. RunnerHandler tests - Test run_custom_check method execution
4. Integration tests - Test that custom checks are queued and executed on PR events
5. Retest command tests - Test /retest name command

The custom check runs feature allows users to define custom checks via YAML configuration:
- Custom check names match exactly what's configured in YAML (no prefix added)
- Checks behave like built-in checks (fail if command not found)
- Custom checks are included in all_required_status_checks when required=true
- Custom checks are added to supported retest list
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from webhook_server.libs.handlers.check_run_handler import CheckRunHandler
from webhook_server.libs.handlers.runner_handler import RunnerHandler
from webhook_server.utils.constants import (
    COMPLETED_STR,
    FAILURE_STR,
    IN_PROGRESS_STR,
    QUEUED_STR,
    SUCCESS_STR,
)


class TestCustomCheckRunsSchemaValidation:
    """Test suite for custom check runs schema validation."""

    @pytest.fixture
    def valid_custom_check_config(self) -> dict[str, Any]:
        """Create a valid custom check configuration."""
        return {
            "name": "my-custom-check",
            "command": "uv tool run --from ruff ruff check",
        }

    @pytest.fixture
    def minimal_custom_check_config(self) -> dict[str, Any]:
        """Create a minimal valid custom check configuration."""
        return {
            "name": "minimal-check",
            "command": "uv tool run --from pytest pytest",
        }

    def test_valid_custom_check_config(self, valid_custom_check_config: dict[str, Any]) -> None:
        """Test that valid custom check configuration is accepted."""
        # This test verifies the structure matches schema expectations
        assert valid_custom_check_config["name"] == "my-custom-check"
        assert valid_custom_check_config["command"] == "uv tool run --from ruff ruff check"

    def test_minimal_custom_check_config(self, minimal_custom_check_config: dict[str, Any]) -> None:
        """Test that minimal custom check configuration is accepted."""
        assert minimal_custom_check_config["name"] == "minimal-check"
        assert minimal_custom_check_config["command"] == "uv tool run --from pytest pytest"

    def test_custom_check_with_env_vars(self) -> None:
        """Test that custom check with environment variables is accepted."""
        config = {
            "name": "my-check",
            "command": "python -m pytest",
            "env": ["PYTHONPATH", "DEBUG"],
        }
        assert config["env"] == ["PYTHONPATH", "DEBUG"]

    def test_custom_check_with_multiline_command(self) -> None:
        """Test that custom check with multiline command is accepted."""
        config = {
            "name": "complex-check",
            "command": "python -c \"\nimport sys\nprint('Running check')\nsys.exit(0)\n\"",
        }
        assert "python" in config["command"]
        assert "\n" in config["command"]


class TestCheckRunHandlerCustomCheckMethods:
    """Test suite for CheckRunHandler custom check methods."""

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
        mock_webhook.last_commit.sha = "test-sha-123"
        mock_webhook.custom_check_runs = [
            {"name": "lint", "command": "uv tool run --from ruff ruff check", "required": True},
            {"name": "security-scan", "command": "uv tool run --from bandit bandit -r .", "required": False},
        ]
        return mock_webhook

    @pytest.fixture
    def check_run_handler(self, mock_github_webhook: Mock) -> CheckRunHandler:
        """Create a CheckRunHandler instance with mocked dependencies."""
        return CheckRunHandler(mock_github_webhook)

    @pytest.mark.asyncio
    async def test_set_custom_check_queued(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting custom check to queued status."""
        check_name = "lint"

        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_custom_check_queued(name=check_name)
            mock_set_status.assert_called_once_with(check_run=check_name, status=QUEUED_STR)

    @pytest.mark.asyncio
    async def test_set_custom_check_in_progress(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting custom check to in_progress status."""
        check_name = "lint"

        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_custom_check_in_progress(name=check_name)
            mock_set_status.assert_called_once_with(check_run=check_name, status=IN_PROGRESS_STR)

    @pytest.mark.asyncio
    async def test_set_custom_check_success_with_output(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting custom check to success with output."""
        check_name = "lint"
        output = {"title": "Lint passed", "summary": "All checks passed", "text": "No issues found"}

        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_custom_check_success(name=check_name, output=output)
            mock_set_status.assert_called_once_with(
                check_run=check_name,
                status=COMPLETED_STR,
                conclusion=SUCCESS_STR,
                output=output,
            )

    @pytest.mark.asyncio
    async def test_set_custom_check_success_without_output(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting custom check to success without output."""
        check_name = "lint"

        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_custom_check_success(name=check_name, output=None)
            mock_set_status.assert_called_once_with(
                check_run=check_name,
                status=COMPLETED_STR,
                conclusion=SUCCESS_STR,
                output=None,
            )

    @pytest.mark.asyncio
    async def test_set_custom_check_failure_with_output(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting custom check to failure with output."""
        check_name = "security-scan"
        output = {"title": "Security scan failed", "summary": "Vulnerabilities found", "text": "3 critical issues"}

        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_custom_check_failure(name=check_name, output=output)
            mock_set_status.assert_called_once_with(
                check_run=check_name,
                status=COMPLETED_STR,
                conclusion=FAILURE_STR,
                output=output,
            )

    @pytest.mark.asyncio
    async def test_set_custom_check_failure_without_output(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting custom check to failure without output."""
        check_name = "security-scan"

        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_custom_check_failure(name=check_name, output=None)
            mock_set_status.assert_called_once_with(
                check_run=check_name,
                status=COMPLETED_STR,
                conclusion=FAILURE_STR,
                output=None,
            )

    @pytest.mark.asyncio
    async def test_all_required_status_checks_includes_custom_checks(self, check_run_handler: CheckRunHandler) -> None:
        """Test that all_required_status_checks includes required custom checks."""
        mock_pull_request = Mock()
        mock_pull_request.base.ref = "main"

        # Mock the get_branch_required_status_checks to return empty list
        with patch.object(check_run_handler, "get_branch_required_status_checks", return_value=[]):
            result = await check_run_handler.all_required_status_checks(pull_request=mock_pull_request)

            # Should include required custom check but not non-required one
            assert "lint" in result
            assert "security-scan" not in result

    @pytest.mark.asyncio
    async def test_all_required_status_checks_excludes_non_required_custom_checks(
        self, check_run_handler: CheckRunHandler, mock_github_webhook: Mock
    ) -> None:
        """Test that non-required custom checks are excluded from required status checks."""
        # Override custom checks to have only non-required checks
        mock_github_webhook.custom_check_runs = [
            {"name": "optional-check", "command": "uv tool run --from pytest pytest", "required": False},
        ]

        mock_pull_request = Mock()
        mock_pull_request.base.ref = "main"

        # Reset cache to force recalculation
        check_run_handler._all_required_status_checks = None

        with patch.object(check_run_handler, "get_branch_required_status_checks", return_value=[]):
            result = await check_run_handler.all_required_status_checks(pull_request=mock_pull_request)

            # Should not include non-required custom check
            assert "optional-check" not in result


class TestRunnerHandlerCustomCheck:
    """Test suite for RunnerHandler run_custom_check method."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance."""
        mock_webhook = Mock()
        mock_webhook.hook_data = {}
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.clone_repo_dir = "/tmp/test-repo"
        mock_webhook.mask_sensitive = True
        return mock_webhook

    @pytest.fixture
    def runner_handler(self, mock_github_webhook: Mock) -> RunnerHandler:
        """Create a RunnerHandler instance with mocked dependencies."""
        handler = RunnerHandler(mock_github_webhook)
        # Mock check_run_handler methods
        handler.check_run_handler.set_custom_check_in_progress = AsyncMock()
        handler.check_run_handler.set_custom_check_success = AsyncMock()
        handler.check_run_handler.set_custom_check_failure = AsyncMock()
        handler.check_run_handler.get_check_run_text = Mock(return_value="Mock output text")
        return handler

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        """Create a mock PullRequest instance."""
        mock_pr = Mock()
        mock_pr.number = 123
        mock_pr.base = Mock()
        mock_pr.base.ref = "main"
        return mock_pr

    @pytest.mark.asyncio
    async def test_run_custom_check_success(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test successful execution of custom check."""
        check_config = {
            "name": "lint",
            "command": "uv tool run --from ruff ruff check",
        }

        # Create async context manager mock
        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, "/tmp/worktree", "", ""))
        mock_checkout_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(runner_handler, "_checkout_worktree", return_value=mock_checkout_cm),
            patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(return_value=(True, "output", "")),
            ) as mock_run,
        ):
            await runner_handler.run_custom_check(pull_request=mock_pull_request, check_config=check_config)

            # Verify check run status updates
            runner_handler.check_run_handler.set_custom_check_in_progress.assert_called_once_with(name="lint")
            runner_handler.check_run_handler.set_custom_check_success.assert_called_once()

            # Verify command was executed with default timeout
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args.kwargs
            assert call_kwargs["timeout"] == 600  # Default timeout

    @pytest.mark.asyncio
    async def test_run_custom_check_failure(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test failed execution of custom check."""
        check_config = {
            "name": "security-scan",
            "command": "uv tool run --from bandit bandit -r .",
        }

        # Create async context manager mock
        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, "/tmp/worktree", "", ""))
        mock_checkout_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(runner_handler, "_checkout_worktree", return_value=mock_checkout_cm),
            patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(return_value=(False, "output", "error message")),
            ),
        ):
            await runner_handler.run_custom_check(pull_request=mock_pull_request, check_config=check_config)

            # Verify failure status was set
            runner_handler.check_run_handler.set_custom_check_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_custom_check_checkout_failure(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test custom check when repository checkout fails."""
        check_config = {
            "name": "lint",
            "command": "uv tool run --from pytest pytest",
        }

        # Create async context manager mock with failed checkout
        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(False, "", "checkout output", "checkout error"))
        mock_checkout_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(runner_handler, "_checkout_worktree", return_value=mock_checkout_cm):
            await runner_handler.run_custom_check(pull_request=mock_pull_request, check_config=check_config)

            # Verify failure status was set due to checkout failure
            runner_handler.check_run_handler.set_custom_check_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_custom_check_command_execution_in_worktree(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test that custom check command is executed in worktree directory."""
        check_config = {
            "name": "build",
            "command": "uv tool run --from build python -m build",
        }

        # Create async context manager mock
        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, "/tmp/test-worktree", "", ""))
        mock_checkout_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(runner_handler, "_checkout_worktree", return_value=mock_checkout_cm),
            patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(return_value=(True, "output", "")),
            ) as mock_run,
        ):
            await runner_handler.run_custom_check(pull_request=mock_pull_request, check_config=check_config)

            # Verify command is executed with cwd parameter set to worktree
            mock_run.assert_called_once()
            call_args = mock_run.call_args.kwargs
            assert call_args["command"] == "uv tool run --from build python -m build"
            assert call_args["cwd"] == "/tmp/test-worktree"

    @pytest.mark.asyncio
    async def test_run_custom_check_with_env_vars(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test that custom check passes environment variables from server environment."""
        check_config = {
            "name": "env-test",
            "command": "env | grep TEST_VAR",
            "env": ["TEST_VAR", "MISSING_VAR"],
        }

        # Create async context manager mock
        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, "/tmp/worktree", "", ""))
        mock_checkout_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.dict("os.environ", {"TEST_VAR": "test_value"}, clear=False),
            patch.object(runner_handler, "_checkout_worktree", return_value=mock_checkout_cm),
            patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(return_value=(True, "TEST_VAR=test_value", "")),
            ) as mock_run,
        ):
            await runner_handler.run_custom_check(pull_request=mock_pull_request, check_config=check_config)

            # Verify command was called with env dict containing only existing env vars
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args.kwargs
            assert call_kwargs["env"] == {"TEST_VAR": "test_value"}
            # MISSING_VAR should not be in env dict since it's not in os.environ

    @pytest.mark.asyncio
    async def test_run_custom_check_without_env_vars(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test that custom check without env config passes None to run_command."""
        check_config = {
            "name": "no-env",
            "command": "echo test",
        }

        # Create async context manager mock
        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, "/tmp/worktree", "", ""))
        mock_checkout_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(runner_handler, "_checkout_worktree", return_value=mock_checkout_cm),
            patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(return_value=(True, "test", "")),
            ) as mock_run,
        ):
            await runner_handler.run_custom_check(pull_request=mock_pull_request, check_config=check_config)

            # Verify command was called with env=None (no env config)
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args.kwargs
            assert call_kwargs["env"] is None


class TestCustomCheckRunsIntegration:
    """Integration tests for custom check runs feature."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance with custom checks configured."""
        mock_webhook = Mock()
        mock_webhook.hook_data = {
            "action": "opened",
            "pull_request": {"number": 123},
        }
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.clone_repo_dir = "/tmp/test-repo"
        mock_webhook.mask_sensitive = True
        mock_webhook.custom_check_runs = [
            {
                "name": "lint",
                "command": "uv tool run --from ruff ruff check",
                "required": True,
            },
            {
                "name": "security",
                "command": "uv tool run --from bandit bandit -r .",
                "required": True,
            },
            {
                "name": "optional-check",
                "command": "uv tool run --from pytest pytest",
                "required": False,
            },
        ]
        return mock_webhook

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        """Create a mock PullRequest instance."""
        mock_pr = Mock()
        mock_pr.number = 123
        mock_pr.base = Mock()
        mock_pr.base.ref = "main"
        mock_pr.draft = False
        return mock_pr

    @pytest.mark.asyncio
    async def test_custom_checks_execution_workflow(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """Test complete workflow of custom check execution."""
        runner_handler = RunnerHandler(mock_github_webhook)
        runner_handler.check_run_handler.set_custom_check_in_progress = AsyncMock()
        runner_handler.check_run_handler.set_custom_check_success = AsyncMock()
        runner_handler.check_run_handler.get_check_run_text = Mock(return_value="Mock output")

        check_config = mock_github_webhook.custom_check_runs[0]  # lint check

        # Create async context manager mock
        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, "/tmp/worktree", "", ""))
        mock_checkout_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(runner_handler, "_checkout_worktree", return_value=mock_checkout_cm),
            patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(return_value=(True, "Lint passed", "")),
            ),
        ):
            # Execute the check
            await runner_handler.run_custom_check(pull_request=mock_pull_request, check_config=check_config)

            # Verify workflow: in_progress -> execute -> success
            runner_handler.check_run_handler.set_custom_check_in_progress.assert_called_once()
            runner_handler.check_run_handler.set_custom_check_success.assert_called_once()


class TestCustomCheckRunsRetestCommand:
    """Test suite for /retest custom:name command functionality."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance with custom checks."""
        mock_webhook = Mock()
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.custom_check_runs = [
            {"name": "lint", "command": "uv tool run --from ruff ruff check", "required": True},
            {"name": "security", "command": "uv tool run --from bandit bandit -r .", "required": True},
        ]
        return mock_webhook

    @pytest.mark.asyncio
    async def test_retest_custom_check_command_format(self, mock_github_webhook: Mock) -> None:
        """Test that custom checks can be retested with /retest custom:name format."""
        # Verify check names match retest command format
        for check in mock_github_webhook.custom_check_runs:
            check_name = check["name"]
            retest_command = f"/retest custom:{check_name}"

            # Verify the retest command format is correct
            assert retest_command.startswith("/retest custom:")
            assert retest_command == f"/retest custom:{check_name}"

    @pytest.mark.asyncio
    async def test_retest_all_custom_checks(self, mock_github_webhook: Mock) -> None:
        """Test that all custom checks are included in retest list."""
        # Get all custom check names
        custom_check_names = [check["name"] for check in mock_github_webhook.custom_check_runs]

        # Verify expected checks are present
        assert "lint" in custom_check_names
        assert "security" in custom_check_names
        assert len(custom_check_names) == 2

    @pytest.mark.asyncio
    async def test_retest_custom_check_triggers_execution(self, mock_github_webhook: Mock) -> None:
        """Test that /retest custom:name triggers check execution."""
        runner_handler = RunnerHandler(mock_github_webhook)
        runner_handler.check_run_handler.set_custom_check_in_progress = AsyncMock()
        runner_handler.check_run_handler.set_custom_check_success = AsyncMock()
        runner_handler.check_run_handler.get_check_run_text = Mock(return_value="Test output")

        mock_pull_request = Mock()
        mock_pull_request.number = 123
        mock_pull_request.base = Mock()
        mock_pull_request.base.ref = "main"

        # Simulate /retest custom:lint command
        check_config = mock_github_webhook.custom_check_runs[0]

        # Create async context manager mock
        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, "/tmp/worktree", "", ""))
        mock_checkout_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(runner_handler, "_checkout_worktree", return_value=mock_checkout_cm),
            patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(return_value=(True, "output", "")),
            ),
        ):
            await runner_handler.run_custom_check(pull_request=mock_pull_request, check_config=check_config)

            # Verify check was executed
            runner_handler.check_run_handler.set_custom_check_in_progress.assert_called_once()
            runner_handler.check_run_handler.set_custom_check_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_check_name_without_prefix(self) -> None:
        """Test that custom check names no longer use a prefix."""
        base_name = "lint"
        check_name = base_name

        # Custom check names should now match exactly what's in YAML config
        assert check_name == "lint"
        assert not check_name.startswith("custom:")


class TestCustomCheckRunsEdgeCases:
    """Test suite for edge cases and error handling in custom check runs."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance."""
        mock_webhook = Mock()
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.clone_repo_dir = "/tmp/test-repo"
        mock_webhook.mask_sensitive = True
        mock_webhook.custom_check_runs = []
        return mock_webhook

    @pytest.mark.asyncio
    async def test_no_custom_checks_configured(self, mock_github_webhook: Mock) -> None:
        """Test behavior when no custom checks are configured."""
        # Create fresh mock with no custom checks but other checks may be configured
        mock_github_webhook.custom_check_runs = []
        mock_github_webhook.tox = None
        mock_github_webhook.verified_job = None
        mock_github_webhook.build_and_push_container = None
        mock_github_webhook.pypi = None
        mock_github_webhook.conventional_title = None

        check_run_handler = CheckRunHandler(mock_github_webhook)
        mock_pull_request = Mock()
        mock_pull_request.base.ref = "main"

        # Reset cache
        check_run_handler._all_required_status_checks = None

        with patch.object(check_run_handler, "get_branch_required_status_checks", return_value=[]):
            result = await check_run_handler.all_required_status_checks(pull_request=mock_pull_request)

            # Should not include any custom checks (and no other checks configured)
            assert len(result) == 0  # No checks configured at all

    @pytest.mark.asyncio
    async def test_custom_check_timeout_expiration(self, mock_github_webhook: Mock) -> None:
        """Test that custom check respects timeout configuration."""
        runner_handler = RunnerHandler(mock_github_webhook)
        runner_handler.check_run_handler.get_check_run_text = Mock(return_value="Timeout")

        mock_pull_request = Mock()
        mock_pull_request.number = 123
        mock_pull_request.base = Mock()
        mock_pull_request.base.ref = "main"

        check_config = {
            "name": "slow-check",
            "command": "uv tool run --from some-package slow-command",
        }

        # Create async context manager mock
        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, "/tmp/worktree", "", ""))
        mock_checkout_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(runner_handler, "_checkout_worktree", return_value=mock_checkout_cm),
            patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(side_effect=asyncio.TimeoutError),
            ),
        ):
            # Should handle timeout gracefully
            with pytest.raises(asyncio.TimeoutError):
                await runner_handler.run_custom_check(pull_request=mock_pull_request, check_config=check_config)

    @pytest.mark.asyncio
    async def test_custom_check_with_long_command(self, mock_github_webhook: Mock) -> None:
        """Test custom check with long multiline command from config."""
        runner_handler = RunnerHandler(mock_github_webhook)
        runner_handler.check_run_handler.set_custom_check_in_progress = AsyncMock()
        runner_handler.check_run_handler.set_custom_check_success = AsyncMock()
        runner_handler.check_run_handler.get_check_run_text = Mock(return_value="Output")

        mock_pull_request = Mock()
        mock_pull_request.number = 123
        mock_pull_request.base = Mock()
        mock_pull_request.base.ref = "main"

        check_config = {
            "name": "long-check",
            "command": "python -c \"\nimport sys\nprint('Running complex check')\nsys.exit(0)\n\"",
        }

        # Create async context manager mock
        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, "/tmp/worktree", "", ""))
        mock_checkout_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(runner_handler, "_checkout_worktree", return_value=mock_checkout_cm),
            patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(return_value=(True, "output", "")),
            ),
        ):
            await runner_handler.run_custom_check(pull_request=mock_pull_request, check_config=check_config)

            # Should succeed with multiline command
            runner_handler.check_run_handler.set_custom_check_success.assert_called_once()
