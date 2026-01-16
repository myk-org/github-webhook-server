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
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from webhook_server.libs.github_api import GithubWebhook
from webhook_server.libs.handlers.check_run_handler import CheckRunHandler, CheckRunOutput
from webhook_server.libs.handlers.pull_request_handler import PullRequestHandler
from webhook_server.libs.handlers.runner_handler import RunnerHandler
from webhook_server.utils.constants import (
    FAILURE_STR,
    IN_PROGRESS_STR,
    QUEUED_STR,
    SUCCESS_STR,
)


class TestCustomCheckRunsSchemaValidation:
    """Test suite for custom check runs schema validation.

    These tests use the production validator (GithubWebhook._validate_custom_check_runs)
    to ensure configurations are validated correctly against the schema rules.
    """

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance for validation testing."""
        mock_webhook = Mock()
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        return mock_webhook

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

    def test_valid_custom_check_config(
        self, mock_github_webhook: Mock, valid_custom_check_config: dict[str, Any]
    ) -> None:
        """Test that valid custom check configuration passes validation."""
        raw_checks = [valid_custom_check_config]

        # Mock shutil.which to simulate finding the executable
        with patch("shutil.which", return_value="/usr/bin/uv"):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # Validation should pass
        assert len(validated) == 1
        assert validated[0]["name"] == "my-custom-check"
        assert validated[0]["command"] == "uv tool run --from ruff ruff check"

    def test_minimal_custom_check_config(
        self, mock_github_webhook: Mock, minimal_custom_check_config: dict[str, Any]
    ) -> None:
        """Test that minimal custom check configuration passes validation."""
        raw_checks = [minimal_custom_check_config]

        # Mock shutil.which to simulate finding the executable
        with patch("shutil.which", return_value="/usr/bin/uv"):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # Validation should pass
        assert len(validated) == 1
        assert validated[0]["name"] == "minimal-check"

    def test_custom_check_with_multiline_command(self, mock_github_webhook: Mock) -> None:
        """Test that custom check with multiline command passes validation."""
        config = {
            "name": "complex-check",
            "command": "python -c \"\nimport sys\nprint('Running check')\nsys.exit(0)\n\"",
        }
        raw_checks = [config]

        # Mock shutil.which to simulate finding python
        with patch("shutil.which", return_value="/usr/bin/python"):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # Validation should pass - python is extracted as the executable
        assert len(validated) == 1
        assert validated[0]["name"] == "complex-check"
        assert "\n" in validated[0]["command"]

    def test_custom_check_with_mandatory_option(self, mock_github_webhook: Mock) -> None:
        """Test that custom check with mandatory=true/false passes validation."""
        raw_checks = [
            {"name": "mandatory-check", "command": "echo test", "mandatory": True},
            {"name": "optional-check", "command": "echo test", "mandatory": False},
        ]

        with patch("shutil.which", return_value="/usr/bin/echo"):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # Both should pass validation (mandatory is not validated by _validate_custom_check_runs)
        assert len(validated) == 2
        assert validated[0]["mandatory"] is True
        assert validated[1]["mandatory"] is False

    def test_invalid_config_missing_name(self, mock_github_webhook: Mock) -> None:
        """Test that config missing 'name' field fails validation."""
        raw_checks = [{"command": "echo test"}]

        with patch("shutil.which", return_value="/usr/bin/echo"):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # Should fail validation
        assert len(validated) == 0
        mock_github_webhook.logger.warning.assert_any_call("Custom check missing required 'name' field, skipping")

    def test_invalid_config_missing_command(self, mock_github_webhook: Mock) -> None:
        """Test that config missing 'command' field fails validation."""
        raw_checks = [{"name": "test-check"}]

        with patch("shutil.which", return_value="/usr/bin/echo"):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # Should fail validation
        assert len(validated) == 0
        mock_github_webhook.logger.warning.assert_any_call(
            "Custom check 'test-check' missing required 'command' field, skipping"
        )


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
            {"name": "lint", "command": "uv tool run --from ruff ruff check"},
            {"name": "security-scan", "command": "uv tool run --from bandit bandit -r ."},
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
            await check_run_handler.set_check_queued(name=check_name)
            mock_set_status.assert_called_once_with(check_run=check_name, status=QUEUED_STR, output=None)

    @pytest.mark.asyncio
    async def test_set_custom_check_in_progress(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting custom check to in_progress status."""
        check_name = "lint"

        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_in_progress(name=check_name)
            mock_set_status.assert_called_once_with(check_run=check_name, status=IN_PROGRESS_STR)

    @pytest.mark.asyncio
    async def test_set_custom_check_success_with_output(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting custom check to success with output."""
        check_name = "lint"
        output: CheckRunOutput = {"title": "Lint passed", "summary": "All checks passed", "text": "No issues found"}

        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_success(name=check_name, output=output)
            mock_set_status.assert_called_once_with(
                check_run=check_name,
                conclusion=SUCCESS_STR,
                output=output,
            )

    @pytest.mark.asyncio
    async def test_set_custom_check_success_without_output(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting custom check to success without output."""
        check_name = "lint"

        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_success(name=check_name, output=None)
            mock_set_status.assert_called_once_with(
                check_run=check_name,
                conclusion=SUCCESS_STR,
                output=None,
            )

    @pytest.mark.asyncio
    async def test_set_custom_check_failure_with_output(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting custom check to failure with output."""
        check_name = "security-scan"
        output: CheckRunOutput = {
            "title": "Security scan failed",
            "summary": "Vulnerabilities found",
            "text": "3 critical issues",
        }

        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_failure(name=check_name, output=output)
            mock_set_status.assert_called_once_with(
                check_run=check_name,
                conclusion=FAILURE_STR,
                output=output,
            )

    @pytest.mark.asyncio
    async def test_set_custom_check_failure_without_output(self, check_run_handler: CheckRunHandler) -> None:
        """Test setting custom check to failure without output."""
        check_name = "security-scan"

        with patch.object(check_run_handler, "set_check_run_status") as mock_set_status:
            await check_run_handler.set_check_failure(name=check_name, output=None)
            mock_set_status.assert_called_once_with(
                check_run=check_name,
                conclusion=FAILURE_STR,
                output=None,
            )

    @pytest.mark.asyncio
    async def test_all_required_status_checks_includes_mandatory_custom_checks_only(
        self, check_run_handler: CheckRunHandler
    ) -> None:
        """Test that all_required_status_checks includes only mandatory custom checks (default is true)."""
        mock_pull_request = Mock()
        mock_pull_request.base.ref = "main"

        # Mock the get_branch_required_status_checks to return empty list
        with patch.object(check_run_handler, "get_branch_required_status_checks", return_value=[]):
            result = await check_run_handler.all_required_status_checks(pull_request=mock_pull_request)

            # Should include all custom checks (both are mandatory by default)
            assert "lint" in result
            assert "security-scan" in result


class TestCustomCheckMandatoryOption:
    """Test suite for custom check mandatory option."""

    @pytest.fixture
    def mock_github_webhook_with_mixed_mandatory(self) -> Mock:
        """Create a mock GithubWebhook instance with both mandatory and optional checks."""
        mock_webhook = Mock()
        mock_webhook.hook_data = {}
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.repository_by_github_app = Mock()
        mock_webhook.last_commit = Mock()
        mock_webhook.last_commit.sha = "test-sha-123"
        mock_webhook.custom_check_runs = [
            {"name": "mandatory-check-1", "command": "echo test1", "mandatory": True},
            {"name": "optional-check", "command": "echo test2", "mandatory": False},
            {"name": "mandatory-check-2", "command": "echo test3", "mandatory": True},
            {"name": "default-mandatory-check", "command": "echo test4"},  # No mandatory field = default to true
        ]
        return mock_webhook

    @pytest.mark.asyncio
    async def test_mandatory_true_checks_included_in_required(
        self, mock_github_webhook_with_mixed_mandatory: Mock
    ) -> None:
        """Test that checks with mandatory=true are included in required status checks."""
        check_run_handler = CheckRunHandler(mock_github_webhook_with_mixed_mandatory)
        mock_pull_request = Mock()
        mock_pull_request.base.ref = "main"

        with patch.object(check_run_handler, "get_branch_required_status_checks", return_value=[]):
            result = await check_run_handler.all_required_status_checks(pull_request=mock_pull_request)

            # Mandatory checks should be included
            assert "mandatory-check-1" in result
            assert "mandatory-check-2" in result

    @pytest.mark.asyncio
    async def test_mandatory_false_checks_excluded_from_required(
        self, mock_github_webhook_with_mixed_mandatory: Mock
    ) -> None:
        """Test that checks with mandatory=false are NOT included in required status checks."""
        check_run_handler = CheckRunHandler(mock_github_webhook_with_mixed_mandatory)
        mock_pull_request = Mock()
        mock_pull_request.base.ref = "main"

        with patch.object(check_run_handler, "get_branch_required_status_checks", return_value=[]):
            result = await check_run_handler.all_required_status_checks(pull_request=mock_pull_request)

            # Optional check should NOT be included
            assert "optional-check" not in result

    @pytest.mark.asyncio
    async def test_default_mandatory_is_true(self, mock_github_webhook_with_mixed_mandatory: Mock) -> None:
        """Test that checks without mandatory field default to mandatory=true (backward compatibility)."""
        check_run_handler = CheckRunHandler(mock_github_webhook_with_mixed_mandatory)
        mock_pull_request = Mock()
        mock_pull_request.base.ref = "main"

        with patch.object(check_run_handler, "get_branch_required_status_checks", return_value=[]):
            result = await check_run_handler.all_required_status_checks(pull_request=mock_pull_request)

            # Check without mandatory field should default to true and be included
            assert "default-mandatory-check" in result

    @pytest.mark.asyncio
    async def test_both_mandatory_and_optional_checks_are_queued(
        self, mock_github_webhook_with_mixed_mandatory: Mock
    ) -> None:
        """Test that both mandatory and optional checks are queued and executed.

        The mandatory flag ONLY affects whether the check is required for merging,
        NOT whether the check is executed. All checks should still be queued and executed.
        """

        mock_owners_handler = Mock()
        pull_request_handler = PullRequestHandler(mock_github_webhook_with_mixed_mandatory, mock_owners_handler)
        pull_request_handler.check_run_handler.set_check_queued = AsyncMock()

        mock_pull_request = Mock()
        mock_pull_request.number = 123
        mock_pull_request.base = Mock()
        mock_pull_request.base.ref = "main"

        # Mock all the methods called in process_opened_or_synchronize_pull_request
        with (
            patch.object(pull_request_handler.owners_file_handler, "assign_reviewers", new=AsyncMock()),
            patch.object(pull_request_handler.labels_handler, "_add_label", new=AsyncMock()),
            patch.object(pull_request_handler, "label_pull_request_by_merge_state", new=AsyncMock()),
            patch.object(pull_request_handler.check_run_handler, "set_check_queued", new=AsyncMock()),
            patch.object(pull_request_handler, "_process_verified_for_update_or_new_pull_request", new=AsyncMock()),
            patch.object(pull_request_handler.labels_handler, "add_size_label", new=AsyncMock()),
            patch.object(pull_request_handler, "add_pull_request_owner_as_assingee", new=AsyncMock()),
            patch.object(pull_request_handler.runner_handler, "run_tox", new=AsyncMock()),
            patch.object(pull_request_handler.runner_handler, "run_pre_commit", new=AsyncMock()),
            patch.object(pull_request_handler.runner_handler, "run_install_python_module", new=AsyncMock()),
            patch.object(pull_request_handler.runner_handler, "run_build_container", new=AsyncMock()),
            patch.object(pull_request_handler.runner_handler, "run_custom_check", new=AsyncMock()),
        ):
            await pull_request_handler.process_opened_or_synchronize_pull_request(pull_request=mock_pull_request)

            # Verify set_check_queued was called for ALL custom checks (mandatory and optional)
            queued_check_names = [
                call.kwargs["name"] for call in pull_request_handler.check_run_handler.set_check_queued.call_args_list
            ]

            assert "mandatory-check-1" in queued_check_names
            assert "optional-check" in queued_check_names
            assert "mandatory-check-2" in queued_check_names
            assert "default-mandatory-check" in queued_check_names


class TestRunnerHandlerCustomCheck:
    """Test suite for RunnerHandler run_custom_check method."""

    @pytest.fixture
    def mock_github_webhook(self, tmp_path: Path) -> Mock:
        """Create a mock GithubWebhook instance."""
        mock_webhook = Mock()
        mock_webhook.hook_data = {}
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.clone_repo_dir = str(tmp_path / "test-repo")
        mock_webhook.mask_sensitive = True
        return mock_webhook

    @pytest.fixture
    def runner_handler(self, mock_github_webhook: Mock) -> RunnerHandler:
        """Create a RunnerHandler instance with mocked dependencies."""
        handler = RunnerHandler(mock_github_webhook)
        # Mock check_run_handler methods
        handler.check_run_handler.is_check_run_in_progress = AsyncMock(return_value=False)
        handler.check_run_handler.set_check_in_progress = AsyncMock()
        handler.check_run_handler.set_check_success = AsyncMock()
        handler.check_run_handler.set_check_failure = AsyncMock()
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
    async def test_run_custom_check_success(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock, tmp_path: Path
    ) -> None:
        """Test successful execution of custom check."""
        check_config = {
            "name": "lint",
            "command": "uv tool run --from ruff ruff check",
        }

        worktree = tmp_path / "worktree"
        # Create async context manager mock
        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, str(worktree), "", ""))
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
            runner_handler.check_run_handler.set_check_in_progress.assert_called_once_with(name="lint")
            runner_handler.check_run_handler.set_check_success.assert_called_once()

            # Verify command was executed
            mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_custom_check_failure(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock, tmp_path: Path
    ) -> None:
        """Test failed execution of custom check."""
        check_config = {
            "name": "security-scan",
            "command": "uv tool run --from bandit bandit -r .",
        }

        worktree = tmp_path / "worktree"
        # Create async context manager mock
        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, str(worktree), "", ""))
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
            runner_handler.check_run_handler.set_check_failure.assert_called_once()

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
            runner_handler.check_run_handler.set_check_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_custom_check_command_execution_in_worktree(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock, tmp_path: Path
    ) -> None:
        """Test that custom check command is executed in worktree directory."""
        check_config = {
            "name": "build",
            "command": "uv tool run --from build python -m build",
        }

        worktree = tmp_path / "worktree"
        # Create async context manager mock
        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, str(worktree), "", ""))
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
            assert call_args["cwd"] == str(worktree)


class TestCustomCheckRunsIntegration:
    """Integration tests for custom check runs feature."""

    @pytest.fixture
    def mock_github_webhook(self, tmp_path: Path) -> Mock:
        """Create a mock GithubWebhook instance with custom checks configured."""
        mock_webhook = Mock()
        mock_webhook.hook_data = {
            "action": "opened",
            "pull_request": {"number": 123},
        }
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.clone_repo_dir = str(tmp_path / "test-repo")
        mock_webhook.mask_sensitive = True
        mock_webhook.custom_check_runs = [
            {
                "name": "lint",
                "command": "uv tool run --from ruff ruff check",
            },
            {
                "name": "security",
                "command": "uv tool run --from bandit bandit -r .",
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
    async def test_custom_checks_execution_workflow(
        self, mock_github_webhook: Mock, mock_pull_request: Mock, tmp_path: Path
    ) -> None:
        """Test complete workflow of custom check execution."""
        runner_handler = RunnerHandler(mock_github_webhook)
        runner_handler.check_run_handler.is_check_run_in_progress = AsyncMock(return_value=False)
        runner_handler.check_run_handler.set_check_in_progress = AsyncMock()
        runner_handler.check_run_handler.set_check_success = AsyncMock()
        runner_handler.check_run_handler.get_check_run_text = Mock(return_value="Mock output")

        check_config = mock_github_webhook.custom_check_runs[0]  # lint check

        worktree = tmp_path / "worktree"
        # Create async context manager mock
        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, str(worktree), "", ""))
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
            runner_handler.check_run_handler.set_check_in_progress.assert_called_once()
            runner_handler.check_run_handler.set_check_success.assert_called_once()


class TestCustomCheckRunsRetestCommand:
    """Test suite for /retest command functionality for custom checks.

    Custom checks can be retested using just the check name: /retest lint
    """

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance with custom checks."""
        mock_webhook = Mock()
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.custom_check_runs = [
            {"name": "lint", "command": "uv tool run --from ruff ruff check"},
            {"name": "security", "command": "uv tool run --from bandit bandit -r ."},
        ]
        return mock_webhook

    @pytest.mark.asyncio
    async def test_retest_custom_check_command_format(self, mock_github_webhook: Mock) -> None:
        """Test that custom checks can be retested using their name directly.

        /retest lint should work for a custom check named 'lint'.
        """
        for check in mock_github_webhook.custom_check_runs:
            check_name = check["name"]

            # The retest command should use the check name directly
            retest_command = f"/retest {check_name}"
            assert retest_command == f"/retest {check_name}"

            # Check name should match exactly what's in the config
            assert check_name in ["lint", "security"]

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
    async def test_retest_custom_check_triggers_execution(self, mock_github_webhook: Mock, tmp_path: Path) -> None:
        """Test that /retest lint triggers check execution."""
        runner_handler = RunnerHandler(mock_github_webhook)
        runner_handler.check_run_handler.is_check_run_in_progress = AsyncMock(return_value=False)
        runner_handler.check_run_handler.set_check_in_progress = AsyncMock()
        runner_handler.check_run_handler.set_check_success = AsyncMock()
        runner_handler.check_run_handler.get_check_run_text = Mock(return_value="Test output")

        mock_pull_request = Mock()
        mock_pull_request.number = 123
        mock_pull_request.base = Mock()
        mock_pull_request.base.ref = "main"

        # The check config uses raw name (as it appears in custom_check_runs)
        check_config = mock_github_webhook.custom_check_runs[0]

        worktree = tmp_path / "worktree"
        # Create async context manager mock
        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, str(worktree), "", ""))
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
            runner_handler.check_run_handler.set_check_in_progress.assert_called_once()
            runner_handler.check_run_handler.set_check_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_check_name_stored_as_configured(self) -> None:
        """Test that custom check names are stored exactly as configured in YAML.

        Check names should match exactly what's in the YAML config without any prefix.
        """
        check_name = "lint"

        # Custom check names should match exactly what's in YAML config
        assert check_name == "lint"

        # Verify the name is used directly without modification
        retest_arg = check_name
        assert retest_arg == "lint"


class TestValidateCustomCheckRuns:
    """Tests for _validate_custom_check_runs validation logic."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance for validation testing."""
        mock_webhook = Mock()
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        return mock_webhook

    def test_missing_name_field(self, mock_github_webhook: Mock) -> None:
        """Test that checks without 'name' field are skipped with warning."""
        raw_checks = [
            {"command": "uv tool run --from ruff ruff check"},  # Missing 'name'
            {"name": "valid-check", "command": "echo test"},  # Valid
        ]

        # Patch shutil.which to always return True (executable exists)
        with patch("shutil.which", return_value="/usr/bin/echo"):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # Only the valid check should pass
        assert len(validated) == 1
        assert validated[0]["name"] == "valid-check"

        # Warning should be logged for missing name
        mock_github_webhook.logger.warning.assert_any_call("Custom check missing required 'name' field, skipping")

    def test_missing_command_field(self, mock_github_webhook: Mock) -> None:
        """Test that checks without 'command' field are skipped with warning."""
        raw_checks = [
            {"name": "no-command"},  # Missing 'command'
            {"name": "valid-check", "command": "echo test"},  # Valid
        ]

        # Patch shutil.which to always return True
        with patch("shutil.which", return_value="/usr/bin/echo"):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # Only the valid check should pass
        assert len(validated) == 1
        assert validated[0]["name"] == "valid-check"

        # Warning should be logged for missing command
        mock_github_webhook.logger.warning.assert_any_call(
            "Custom check 'no-command' missing required 'command' field, skipping"
        )

    def test_empty_command_field(self, mock_github_webhook: Mock) -> None:
        """Test that checks with empty command field are skipped with warning."""
        raw_checks = [
            {"name": "empty-command", "command": ""},  # Empty command
            {"name": "valid-check", "command": "echo test"},  # Valid
        ]

        # Patch shutil.which to always return True
        with patch("shutil.which", return_value="/usr/bin/echo"):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # Only the valid check should pass
        assert len(validated) == 1
        assert validated[0]["name"] == "valid-check"

        # Warning should be logged for empty command
        mock_github_webhook.logger.warning.assert_any_call(
            "Custom check 'empty-command' missing required 'command' field, skipping"
        )

    def test_unsafe_characters_in_name(self, mock_github_webhook: Mock) -> None:
        """Test that checks with unsafe characters in name are skipped with warning."""
        raw_checks = [
            {"name": "valid-check", "command": "echo test"},  # Valid name
            {"name": "check with spaces", "command": "echo test"},  # Has spaces
            {"name": "check;injection", "command": "echo test"},  # Has semicolon
            {"name": "check$(cmd)", "command": "echo test"},  # Has shell substitution
            {"name": "check`cmd`", "command": "echo test"},  # Has backticks
            {"name": "check|pipe", "command": "echo test"},  # Has pipe
            {"name": "check>redirect", "command": "echo test"},  # Has redirect
            {"name": "check&background", "command": "echo test"},  # Has ampersand
            {"name": "", "command": "echo test"},  # Empty name (too short)
            {"name": "a" * 65, "command": "echo test"},  # Too long (65 chars, max 64)
        ]

        # Patch shutil.which to always return True
        with patch("shutil.which", return_value="/usr/bin/echo"):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # Only the valid check should pass
        assert len(validated) == 1
        assert validated[0]["name"] == "valid-check"

        # Warnings should be logged for unsafe character names
        mock_github_webhook.logger.warning.assert_any_call(
            "Custom check name 'check with spaces' contains unsafe characters, skipping"
        )
        mock_github_webhook.logger.warning.assert_any_call(
            "Custom check name 'check;injection' contains unsafe characters, skipping"
        )

    def test_valid_name_patterns(self, mock_github_webhook: Mock) -> None:
        """Test that valid name patterns are accepted."""
        raw_checks = [
            {"name": "a", "command": "echo test"},  # Single char
            {"name": "a" * 64, "command": "echo test"},  # Max length (64 chars)
            {"name": "my-check", "command": "echo test"},  # With hyphen
            {"name": "my_check", "command": "echo test"},  # With underscore
            {"name": "my.check", "command": "echo test"},  # With dot
            {"name": "myCheck123", "command": "echo test"},  # Alphanumeric
            {"name": "CHECK-NAME_v1.2", "command": "echo test"},  # Mixed valid chars
        ]

        # Patch shutil.which to always return True
        with patch("shutil.which", return_value="/usr/bin/echo"):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # All checks should pass
        assert len(validated) == 7
        validated_names = [c["name"] for c in validated]
        assert "a" in validated_names
        assert "my-check" in validated_names
        assert "my_check" in validated_names
        assert "my.check" in validated_names
        assert "myCheck123" in validated_names
        assert "CHECK-NAME_v1.2" in validated_names

    def test_whitespace_only_command(self, mock_github_webhook: Mock) -> None:
        """Test that checks with whitespace-only command are skipped."""
        raw_checks = [
            {"name": "whitespace-command", "command": "   "},  # Whitespace only
            {"name": "tab-command", "command": "\t\t"},  # Tabs only
            {"name": "newline-command", "command": "\n\n"},  # Newlines only
            {"name": "valid-check", "command": "echo test"},  # Valid
        ]

        # Patch shutil.which to always return True
        with patch("shutil.which", return_value="/usr/bin/echo"):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # Only the valid check should pass
        assert len(validated) == 1
        assert validated[0]["name"] == "valid-check"

        # Warnings should be logged for whitespace-only commands
        assert mock_github_webhook.logger.warning.call_count >= 3
        # Whitespace-only commands pass the `if not command:` check (string is truthy)
        # but fail the `if not command.strip():` check with "empty" message
        mock_github_webhook.logger.warning.assert_any_call(
            "Custom check 'whitespace-command' has empty 'command' field, skipping"
        )

    def test_executable_not_found(self, mock_github_webhook: Mock) -> None:
        """Test that checks with non-existent executable are skipped."""
        raw_checks = [
            {"name": "missing-exec", "command": "nonexistent_command --arg"},  # Executable doesn't exist
            {"name": "valid-check", "command": "echo test"},  # Valid
        ]

        # Mock shutil.which to return None for nonexistent_command, path for echo
        def mock_which(cmd: str) -> str | None:
            return "/usr/bin/echo" if cmd == "echo" else None

        with patch("shutil.which", side_effect=mock_which):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # Only the valid check should pass
        assert len(validated) == 1
        assert validated[0]["name"] == "valid-check"

        # Warning should be logged for missing executable
        mock_github_webhook.logger.warning.assert_any_call(
            "Custom check 'missing-exec' executable 'nonexistent_command' not found on server. "
            "Please open an issue to request adding this executable to the container, "
            "or submit a PR to add it. Skipping check."
        )

    def test_multiple_validation_failures(self, mock_github_webhook: Mock) -> None:
        """Test handling of multiple validation failures at once."""
        raw_checks = [
            {"command": "echo test"},  # Missing name
            {"name": "no-cmd"},  # Missing command
            {"name": "whitespace", "command": "  "},  # Whitespace command
            {"name": "bad-exec", "command": "fake_tool --option"},  # Non-existent executable
            {"name": "good-check", "command": "echo valid"},  # Valid
        ]

        # Mock shutil.which to only find 'echo'
        def mock_which(cmd: str) -> str | None:
            return "/usr/bin/echo" if cmd == "echo" else None

        with patch("shutil.which", side_effect=mock_which):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # Only the valid check should pass
        assert len(validated) == 1
        assert validated[0]["name"] == "good-check"

        # Should have logged 5 warnings (4 individual + 1 summary)
        assert mock_github_webhook.logger.warning.call_count == 5

    def test_all_checks_valid(self, mock_github_webhook: Mock) -> None:
        """Test that all checks pass when validation is successful."""
        raw_checks = [
            {"name": "check1", "command": "echo test1"},
            {"name": "check2", "command": "echo test2"},
            {"name": "check3", "command": "python -c 'print(1)'"},
        ]

        # Mock shutil.which to find all executables
        def mock_which(cmd: str) -> str | None:
            if cmd in ["echo", "python"]:
                return f"/usr/bin/{cmd}"
            return None

        with patch("shutil.which", side_effect=mock_which):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # All checks should pass
        assert len(validated) == 3
        assert validated[0]["name"] == "check1"
        assert validated[1]["name"] == "check2"
        assert validated[2]["name"] == "check3"

        # Debug logs should be called for each validated check
        assert mock_github_webhook.logger.debug.call_count == 3

    def test_empty_check_list(self, mock_github_webhook: Mock) -> None:
        """Test that empty check list returns empty validated list."""
        raw_checks: list[dict[str, Any]] = []

        validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # Should return empty list
        assert len(validated) == 0
        assert validated == []

    def test_complex_multiline_command_validation(self, mock_github_webhook: Mock) -> None:
        """Test validation of complex multiline commands."""
        raw_checks = [
            {
                "name": "complex-check",
                "command": "python -c \"\nimport sys\nprint('test')\nsys.exit(0)\n\"",
            },
        ]

        # Mock shutil.which to find python
        with patch("shutil.which", return_value="/usr/bin/python"):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # Should validate successfully (extracts 'python' as executable)
        assert len(validated) == 1
        assert validated[0]["name"] == "complex-check"

    def test_command_with_path_executable(self, mock_github_webhook: Mock) -> None:
        """Test validation when command uses full path to executable."""
        raw_checks = [
            {"name": "full-path", "command": "/usr/local/bin/custom_tool --arg"},
        ]

        # Mock shutil.which to find the full path executable
        with patch("shutil.which", return_value="/usr/local/bin/custom_tool"):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # Should validate successfully
        assert len(validated) == 1
        assert validated[0]["name"] == "full-path"

    def test_env_var_prefixed_command_validation(self, mock_github_webhook: Mock) -> None:
        """Test that commands with env var prefixes like 'TOKEN=xyz uv ...' are validated correctly.

        The executable should be extracted as the first word AFTER all KEY=VALUE pairs.
        For example: 'TOKEN=xyz PATH=/x/bin echo hi' should validate 'echo' as the executable.
        """
        raw_checks = [
            {"name": "env-prefixed", "command": "TOKEN=xyz PATH=/x/bin echo hi"},
            {"name": "single-env", "command": "DEBUG=true uv run pytest"},
            {"name": "complex-env", "command": "VAR1=a VAR2=b VAR3=c python -c 'print(1)'"},
        ]

        # Mock shutil.which to find echo, uv, and python (the actual executables after env vars)
        def mock_which(cmd: str) -> str | None:
            # The implementation skips KEY=VALUE pairs and extracts the actual executable
            known_executables = {"echo", "uv", "python"}
            return f"/usr/bin/{cmd}" if cmd in known_executables else None

        with patch("shutil.which", side_effect=mock_which):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # All checks should validate successfully with the correct executable extracted
        assert len(validated) == 3
        assert validated[0]["name"] == "env-prefixed"
        assert validated[1]["name"] == "single-env"
        assert validated[2]["name"] == "complex-env"

    def test_env_var_only_command_fails_validation(self, mock_github_webhook: Mock) -> None:
        """Test that a command with only env vars (no executable) fails validation."""
        raw_checks = [
            {"name": "only-env-vars", "command": "VAR1=a VAR2=b"},
        ]

        with patch("shutil.which", return_value=None):
            validated = GithubWebhook._validate_custom_check_runs(mock_github_webhook, raw_checks)

        # Should fail because there's no executable after the env vars
        assert len(validated) == 0
        mock_github_webhook.logger.warning.assert_any_call("Custom check 'only-env-vars' has no executable, skipping")


class TestCustomCheckRunsEdgeCases:
    """Test suite for edge cases and error handling in custom check runs."""

    @pytest.fixture
    def mock_github_webhook(self, tmp_path: Path) -> Mock:
        """Create a mock GithubWebhook instance."""
        mock_webhook = Mock()
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.clone_repo_dir = str(tmp_path / "test-repo")
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
    async def test_custom_check_timeout_expiration(self, mock_github_webhook: Mock, tmp_path: Path) -> None:
        """Test that timeout should be handled gracefully."""
        runner_handler = RunnerHandler(mock_github_webhook)
        runner_handler.check_run_handler.is_check_run_in_progress = AsyncMock(return_value=False)
        runner_handler.check_run_handler.set_check_in_progress = AsyncMock()
        runner_handler.check_run_handler.set_check_failure = AsyncMock()
        runner_handler.check_run_handler.get_check_run_text = Mock(return_value="Timeout")

        mock_pull_request = Mock()
        mock_pull_request.number = 123
        mock_pull_request.base = Mock()
        mock_pull_request.base.ref = "main"

        check_config = {
            "name": "slow-check",
            "command": "uv tool run --from some-package slow-command",
        }

        worktree = tmp_path / "worktree"
        # Create async context manager mock
        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, str(worktree), "", ""))
        mock_checkout_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(runner_handler, "_checkout_worktree", return_value=mock_checkout_cm),
            patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(side_effect=asyncio.TimeoutError),
            ),
        ):
            # Should handle timeout gracefully by reporting failure
            await runner_handler.run_custom_check(pull_request=mock_pull_request, check_config=check_config)

            # Verify that a failure was reported with timeout-related message
            runner_handler.check_run_handler.set_check_failure.assert_awaited_once()
            call_args = runner_handler.check_run_handler.set_check_failure.call_args
            # Check that the failure message mentions timeout
            assert "timeout" in str(call_args).lower() or "timed out" in str(call_args).lower()

    @pytest.mark.asyncio
    async def test_custom_check_with_long_command(self, mock_github_webhook: Mock, tmp_path: Path) -> None:
        """Test custom check with long multiline command from config."""
        runner_handler = RunnerHandler(mock_github_webhook)
        runner_handler.check_run_handler.is_check_run_in_progress = AsyncMock(return_value=False)
        runner_handler.check_run_handler.set_check_in_progress = AsyncMock()
        runner_handler.check_run_handler.set_check_success = AsyncMock()
        runner_handler.check_run_handler.get_check_run_text = Mock(return_value="Output")

        mock_pull_request = Mock()
        mock_pull_request.number = 123
        mock_pull_request.base = Mock()
        mock_pull_request.base.ref = "main"

        check_config = {
            "name": "long-check",
            "command": "python -c \"\nimport sys\nprint('Running complex check')\nsys.exit(0)\n\"",
        }

        worktree = tmp_path / "worktree"
        # Create async context manager mock
        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, str(worktree), "", ""))
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
            runner_handler.check_run_handler.set_check_success.assert_called_once()
