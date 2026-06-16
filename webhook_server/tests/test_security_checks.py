"""Tests for PR security checks.

Tests cover:
1. Suspicious path detection check run (runner_handler)
2. Committer identity check run (runner_handler)
3. Auto-merge override for suspicious paths (pull_request_handler)
"""

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from webhook_server.libs.github_api import GithubWebhook
from webhook_server.libs.handlers.check_run_handler import CheckRunHandler
from webhook_server.libs.handlers.issue_comment_handler import IssueCommentHandler
from webhook_server.libs.handlers.pull_request_handler import PullRequestHandler
from webhook_server.libs.handlers.runner_handler import RunnerHandler
from webhook_server.utils.constants import (
    BUILTIN_CHECK_NAMES,
    COMMAND_SECURITY_OVERRIDE_STR,
    DEFAULT_SUSPICIOUS_PATHS,
    GITHUB_WEB_FLOW_LOGIN,
    GITHUB_WEB_FLOW_USER_ID,
    SECURITY_COMMITTER_IDENTITY_STR,
    SECURITY_SUSPICIOUS_PATHS_STR,
)

TEST_GITHUB_TOKEN = "ghp_testtoken123"  # pragma: allowlist secret


class TestSecuritySuspiciousPaths:
    """Test suite for suspicious path detection check run."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        mock_webhook = Mock()
        mock_webhook.hook_data = {"action": "opened"}
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.token = TEST_GITHUB_TOKEN
        mock_webhook.clone_repo_dir = "/tmp/test-repo"
        mock_webhook.tox = {}
        mock_webhook.tox_python_version = ""
        mock_webhook.tox_args = ""
        mock_webhook.pre_commit = False
        mock_webhook.build_and_push_container = {}
        mock_webhook.pypi = {}
        mock_webhook.conventional_title = ""
        mock_webhook.ctx = None
        mock_webhook.custom_check_runs = []
        mock_webhook.ai_features = None
        mock_webhook.config = Mock()
        mock_webhook.config.get_value = Mock(return_value=None)
        mock_webhook.security_suspicious_paths = DEFAULT_SUSPICIOUS_PATHS
        mock_webhook.security_committer_identity_check = True
        mock_webhook.security_trusted_committers = []
        mock_webhook.parent_committer = "test-user"
        mock_webhook.last_committer = "test-user"
        return mock_webhook

    @pytest.fixture
    def mock_owners_file_handler(self) -> Mock:
        mock_handler = Mock()
        mock_handler.is_user_valid_to_run_commands = AsyncMock(return_value=True)
        mock_handler.changed_files = []
        return mock_handler

    @pytest.fixture
    def runner_handler(self, mock_github_webhook: Mock, mock_owners_file_handler: Mock) -> RunnerHandler:
        return RunnerHandler(mock_github_webhook, mock_owners_file_handler)

    @pytest.fixture(autouse=True)
    def patch_check_run_text(self) -> Generator[None]:
        with patch(
            "webhook_server.libs.handlers.check_run_handler.CheckRunHandler.get_check_run_text",
            return_value="dummy output",
        ):
            yield

    @pytest.mark.asyncio
    async def test_suspicious_paths_no_match(
        self, runner_handler: RunnerHandler, mock_owners_file_handler: Mock
    ) -> None:
        """Check passes when no changed files match suspicious paths."""
        mock_owners_file_handler.changed_files = ["src/main.py", "tests/test_main.py", "README.md"]

        with patch.object(runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()) as mock_progress:
            with patch.object(runner_handler.check_run_handler, "set_check_success", new=AsyncMock()) as mock_success:
                await runner_handler.run_security_suspicious_paths()

                mock_progress.assert_called_once_with(name=SECURITY_SUSPICIOUS_PATHS_STR)
                mock_success.assert_called_once()
                call_args = mock_success.call_args
                assert call_args.kwargs["name"] == SECURITY_SUSPICIOUS_PATHS_STR
                assert "No security-sensitive paths modified" in call_args.kwargs["output"]["summary"]

    @pytest.mark.asyncio
    async def test_suspicious_paths_match_single(
        self, runner_handler: RunnerHandler, mock_owners_file_handler: Mock
    ) -> None:
        """Check fails when a file matches a suspicious path prefix."""
        mock_owners_file_handler.changed_files = ["src/main.py", ".github/workflows/ci.yml"]

        with patch.object(runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()):
            with patch.object(runner_handler.check_run_handler, "set_check_failure", new=AsyncMock()) as mock_failure:
                await runner_handler.run_security_suspicious_paths()

                mock_failure.assert_called_once()
                call_args = mock_failure.call_args
                assert call_args.kwargs["name"] == SECURITY_SUSPICIOUS_PATHS_STR
                assert "1 file(s)" in call_args.kwargs["output"]["summary"]
                assert ".github/workflows/ci.yml" in call_args.kwargs["output"]["text"]

    @pytest.mark.asyncio
    async def test_suspicious_paths_match_multiple(
        self, runner_handler: RunnerHandler, mock_owners_file_handler: Mock
    ) -> None:
        """Check fails listing all matched files when multiple suspicious paths are found."""
        mock_owners_file_handler.changed_files = [
            ".claude/settings.json",
            ".vscode/extensions.json",
            "src/app.py",
            ".github/workflows/deploy.yml",
        ]

        with patch.object(runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()):
            with patch.object(runner_handler.check_run_handler, "set_check_failure", new=AsyncMock()) as mock_failure:
                await runner_handler.run_security_suspicious_paths()

                mock_failure.assert_called_once()
                call_args = mock_failure.call_args
                assert call_args.kwargs["name"] == SECURITY_SUSPICIOUS_PATHS_STR
                assert "3 file(s)" in call_args.kwargs["output"]["summary"]
                output_text = call_args.kwargs["output"]["text"]
                assert ".claude/settings.json" in output_text
                assert ".vscode/extensions.json" in output_text
                assert ".github/workflows/deploy.yml" in output_text
                # Safe file should not be listed
                assert "src/app.py" not in output_text

    @pytest.mark.asyncio
    async def test_suspicious_paths_custom_config(
        self, runner_handler: RunnerHandler, mock_owners_file_handler: Mock
    ) -> None:
        """Check uses custom suspicious paths from configuration."""
        runner_handler.github_webhook.security_suspicious_paths = ["custom/sensitive/", "secret/"]
        mock_owners_file_handler.changed_files = ["custom/sensitive/config.yaml", "src/main.py"]

        with patch.object(runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()):
            with patch.object(runner_handler.check_run_handler, "set_check_failure", new=AsyncMock()) as mock_failure:
                await runner_handler.run_security_suspicious_paths()

                mock_failure.assert_called_once()
                call_args = mock_failure.call_args
                assert "custom/sensitive/config.yaml" in call_args.kwargs["output"]["text"]

    @pytest.mark.asyncio
    async def test_suspicious_paths_empty_config(self, runner_handler: RunnerHandler) -> None:
        """Check is skipped when suspicious paths list is empty."""
        runner_handler.github_webhook.security_suspicious_paths = []

        with patch.object(runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()) as mock_progress:
            await runner_handler.run_security_suspicious_paths()
            mock_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_suspicious_paths_all_default_prefixes(
        self, runner_handler: RunnerHandler, mock_owners_file_handler: Mock
    ) -> None:
        """Verify all default suspicious path prefixes are detected."""
        mock_owners_file_handler.changed_files = [
            ".claude/config.json",
            ".vscode/settings.json",
            ".cursor/rules",
            ".devcontainer/devcontainer.json",
            ".pi/config.yaml",
            ".github/workflows/ci.yml",
            ".github/actions/custom/action.yml",
        ]

        with patch.object(runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()):
            with patch.object(runner_handler.check_run_handler, "set_check_failure", new=AsyncMock()) as mock_failure:
                await runner_handler.run_security_suspicious_paths()

                mock_failure.assert_called_once()
                call_args = mock_failure.call_args
                assert "7 file(s)" in call_args.kwargs["output"]["summary"]


class TestSecurityCommitterIdentity:
    """Test suite for committer identity check run."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        mock_webhook = Mock()
        mock_webhook.hook_data = {"action": "opened"}
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.token = TEST_GITHUB_TOKEN
        mock_webhook.clone_repo_dir = "/tmp/test-repo"
        mock_webhook.tox = {}
        mock_webhook.tox_python_version = ""
        mock_webhook.tox_args = ""
        mock_webhook.pre_commit = False
        mock_webhook.build_and_push_container = {}
        mock_webhook.pypi = {}
        mock_webhook.conventional_title = ""
        mock_webhook.ctx = None
        mock_webhook.custom_check_runs = []
        mock_webhook.ai_features = None
        mock_webhook.config = Mock()
        mock_webhook.config.get_value = Mock(return_value=None)
        mock_webhook.security_suspicious_paths = DEFAULT_SUSPICIOUS_PATHS
        mock_webhook.security_committer_identity_check = True
        mock_webhook.security_trusted_committers = []
        mock_webhook.parent_committer = "test-user"
        mock_webhook.last_committer = "test-user"
        return mock_webhook

    @pytest.fixture
    def mock_owners_file_handler(self) -> Mock:
        mock_handler = Mock()
        mock_handler.is_user_valid_to_run_commands = AsyncMock(return_value=True)
        return mock_handler

    @pytest.fixture
    def runner_handler(self, mock_github_webhook: Mock, mock_owners_file_handler: Mock) -> RunnerHandler:
        return RunnerHandler(mock_github_webhook, mock_owners_file_handler)

    @pytest.fixture(autouse=True)
    def patch_check_run_text(self) -> Generator[None]:
        with patch(
            "webhook_server.libs.handlers.check_run_handler.CheckRunHandler.get_check_run_text",
            return_value="dummy output",
        ):
            yield

    @pytest.mark.asyncio
    async def test_committer_identity_match(self, runner_handler: RunnerHandler) -> None:
        """Check passes when last committer matches PR author."""
        runner_handler.github_webhook.parent_committer = "test-user"
        runner_handler.github_webhook.last_committer = "test-user"

        with patch.object(runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()) as mock_progress:
            with patch.object(runner_handler.check_run_handler, "set_check_success", new=AsyncMock()) as mock_success:
                await runner_handler.run_security_committer_identity()

                mock_progress.assert_called_once_with(name=SECURITY_COMMITTER_IDENTITY_STR)
                mock_success.assert_called_once()
                call_args = mock_success.call_args
                assert call_args.kwargs["name"] == SECURITY_COMMITTER_IDENTITY_STR
                assert "Committer identity verified" in call_args.kwargs["output"]["summary"]

    @pytest.mark.asyncio
    async def test_committer_identity_mismatch(self, runner_handler: RunnerHandler) -> None:
        """Check fails when last committer differs from PR author."""
        runner_handler.github_webhook.parent_committer = "legit-user"
        runner_handler.github_webhook.last_committer = "suspicious-bot"

        with patch.object(runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()):
            with patch.object(runner_handler.check_run_handler, "set_check_failure", new=AsyncMock()) as mock_failure:
                await runner_handler.run_security_committer_identity()

                mock_failure.assert_called_once()
                call_args = mock_failure.call_args
                assert call_args.kwargs["name"] == SECURITY_COMMITTER_IDENTITY_STR
                output = call_args.kwargs["output"]
                assert "suspicious-bot" in output["summary"]
                assert "legit-user" in output["summary"]
                assert "suspicious-bot" in output["text"]
                assert "legit-user" in output["text"]

    @pytest.mark.asyncio
    async def test_committer_identity_unknown(self, runner_handler: RunnerHandler) -> None:
        """Check fails when last committer is unknown (no GitHub user)."""
        runner_handler.github_webhook.parent_committer = "legit-user"
        runner_handler.github_webhook.last_committer = "unknown"

        with patch.object(runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()):
            with patch.object(runner_handler.check_run_handler, "set_check_failure", new=AsyncMock()) as mock_failure:
                await runner_handler.run_security_committer_identity()

                mock_failure.assert_called_once()
                call_args = mock_failure.call_args
                assert call_args.kwargs["name"] == SECURITY_COMMITTER_IDENTITY_STR
                output = call_args.kwargs["output"]
                assert "could not be verified" in output["summary"]
                assert "no associated GitHub user" in output["text"]

    @pytest.mark.asyncio
    async def test_committer_identity_web_flow(self, runner_handler: RunnerHandler) -> None:
        """Check passes when last committer is GitHub's verified web-flow account."""
        runner_handler.github_webhook.parent_committer = "legit-user"
        runner_handler.github_webhook.last_committer = GITHUB_WEB_FLOW_LOGIN
        runner_handler.github_webhook.last_committer_id = GITHUB_WEB_FLOW_USER_ID
        runner_handler.github_webhook.security_trusted_committers = ["web-flow"]

        with patch.object(runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()) as mock_progress:
            with patch.object(runner_handler.check_run_handler, "set_check_success", new=AsyncMock()) as mock_success:
                await runner_handler.run_security_committer_identity()

                mock_progress.assert_called_once_with(name=SECURITY_COMMITTER_IDENTITY_STR)
                mock_success.assert_called_once()
                call_args = mock_success.call_args
                assert call_args.kwargs["name"] == SECURITY_COMMITTER_IDENTITY_STR
                assert "trusted" in call_args.kwargs["output"]["summary"]

    @pytest.mark.asyncio
    async def test_committer_identity_web_flow_fake_id_passes_via_trusted_list(
        self, runner_handler: RunnerHandler
    ) -> None:
        """Web-flow with wrong user ID passes because web-flow is in trusted list."""
        runner_handler.github_webhook.parent_committer = "legit-user"
        runner_handler.github_webhook.last_committer = GITHUB_WEB_FLOW_LOGIN
        runner_handler.github_webhook.last_committer_id = 99999999
        runner_handler.github_webhook.security_trusted_committers = ["web-flow"]

        with patch.object(runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()) as mock_progress:
            with patch.object(runner_handler.check_run_handler, "set_check_success", new=AsyncMock()) as mock_success:
                await runner_handler.run_security_committer_identity()

                mock_progress.assert_called_once_with(name=SECURITY_COMMITTER_IDENTITY_STR)
                mock_success.assert_called_once()
                call_args = mock_success.call_args
                assert call_args.kwargs["name"] == SECURITY_COMMITTER_IDENTITY_STR
                assert "trusted" in call_args.kwargs["output"]["summary"]

    @pytest.mark.asyncio
    async def test_committer_identity_check_disabled(self, runner_handler: RunnerHandler) -> None:
        """Check is skipped when committer-identity-check is disabled."""
        runner_handler.github_webhook.security_committer_identity_check = False

        with patch.object(runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()) as mock_progress:
            await runner_handler.run_security_committer_identity()
            mock_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_committer_identity_mismatch_trusted(self, runner_handler: RunnerHandler) -> None:
        """Check passes when last committer is in trusted-committers list."""
        runner_handler.github_webhook.parent_committer = "legit-user"
        runner_handler.github_webhook.last_committer = "pre-commit-ci[bot]"
        runner_handler.github_webhook.security_trusted_committers = ["pre-commit-ci[bot]", "myorg"]

        with patch.object(runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()):
            with patch.object(runner_handler.check_run_handler, "set_check_success", new=AsyncMock()) as mock_success:
                await runner_handler.run_security_committer_identity()

                mock_success.assert_called_once()
                call_args = mock_success.call_args
                assert call_args.kwargs["name"] == SECURITY_COMMITTER_IDENTITY_STR
                assert "trusted" in call_args.kwargs["output"]["summary"]

    @pytest.mark.asyncio
    async def test_committer_identity_mismatch_not_trusted(self, runner_handler: RunnerHandler) -> None:
        """Check fails when last committer is NOT in trusted-committers list."""
        runner_handler.github_webhook.parent_committer = "legit-user"
        runner_handler.github_webhook.last_committer = "suspicious-user"
        runner_handler.github_webhook.security_trusted_committers = ["pre-commit-ci[bot]"]

        with patch.object(runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()):
            with patch.object(runner_handler.check_run_handler, "set_check_failure", new=AsyncMock()) as mock_failure:
                await runner_handler.run_security_committer_identity()

                mock_failure.assert_called_once()
                call_args = mock_failure.call_args
                assert "suspicious-user" in call_args.kwargs["output"]["summary"]

    @pytest.mark.asyncio
    async def test_committer_identity_unknown_not_bypassed_by_trusted_list(self, runner_handler: RunnerHandler) -> None:
        """Check fails for unknown committer even when 'unknown' is in trusted-committers list."""
        runner_handler.github_webhook.parent_committer = "legit-user"
        runner_handler.github_webhook.last_committer = "unknown"
        runner_handler.github_webhook.security_trusted_committers = ["unknown"]

        with patch.object(runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()):
            with patch.object(runner_handler.check_run_handler, "set_check_failure", new=AsyncMock()) as mock_failure:
                await runner_handler.run_security_committer_identity()

                mock_failure.assert_called_once()
                call_args = mock_failure.call_args
                assert call_args.kwargs["name"] == SECURITY_COMMITTER_IDENTITY_STR
                assert "could not be verified" in call_args.kwargs["output"]["summary"].lower()

    @pytest.mark.asyncio
    async def test_committer_identity_mismatch_trusted_case_insensitive(self, runner_handler: RunnerHandler) -> None:
        """Check passes when committer login differs in case from trusted-committers entry."""
        runner_handler.github_webhook.parent_committer = "legit-user"
        runner_handler.github_webhook.last_committer = "Pre-Commit-CI[bot]"
        runner_handler.github_webhook.security_trusted_committers = ["pre-commit-ci[bot]"]

        with patch.object(runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()):
            with patch.object(runner_handler.check_run_handler, "set_check_success", new=AsyncMock()) as mock_success:
                await runner_handler.run_security_committer_identity()

                mock_success.assert_called_once()
                call_args = mock_success.call_args
                assert call_args.kwargs["name"] == SECURITY_COMMITTER_IDENTITY_STR
                assert "trusted" in call_args.kwargs["output"]["summary"]


class TestAutoMergeSecurityOverride:
    """Test auto-merge is blocked when PR modifies suspicious paths."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        mock_webhook = Mock()
        mock_webhook.hook_data = {"action": "opened"}
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.repository_full_name = "test-org/test-repo"
        mock_webhook.token = TEST_GITHUB_TOKEN
        mock_webhook.parent_committer = "auto-merge-user"
        mock_webhook.auto_verified_and_merged_users = ["auto-merge-user"]
        mock_webhook.set_auto_merge_prs = []
        mock_webhook.security_suspicious_paths = DEFAULT_SUSPICIOUS_PATHS
        mock_webhook.security_committer_identity_check = True
        mock_webhook.security_trusted_committers = []
        mock_webhook.security_mandatory = True
        mock_webhook.last_commit = Mock()
        mock_webhook.ctx = None
        mock_webhook.enabled_labels = None
        mock_webhook.custom_check_runs = []
        mock_webhook.ai_features = None
        mock_webhook.config = Mock()
        mock_webhook.config.get_value = Mock(return_value=None)
        mock_webhook.tox = False
        mock_webhook.pre_commit = False
        mock_webhook.pypi = False
        mock_webhook.build_and_push_container = False
        mock_webhook.conventional_title = False
        mock_webhook.create_issue_for_new_pr = True
        mock_webhook.verified_job = True
        mock_webhook.can_be_merged_required_labels = []
        mock_webhook.minimum_lgtm = 1
        mock_webhook.auto_verify_cherry_picked_prs = True
        mock_webhook.cherry_pick_assign_to_pr_author = True
        mock_webhook.required_conversation_resolution = False
        mock_webhook.issue_url_for_welcome_msg = ""
        return mock_webhook

    @pytest.fixture
    def mock_owners_file_handler(self) -> Mock:
        mock_handler = Mock()
        mock_handler.is_user_valid_to_run_commands = AsyncMock(return_value=True)
        mock_handler.changed_files = []
        mock_handler.all_pull_request_approvers = ["approver1"]
        mock_handler.all_pull_request_reviewers = ["reviewer1"]
        mock_handler.all_repository_approvers_and_reviewers = {}
        return mock_handler

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        mock_pr = Mock()
        mock_pr.number = 123
        mock_pr.title = "Test PR"
        mock_pr.base.ref = "main"
        mock_pr.head.ref = "feature-branch"
        mock_pr.head.sha = "abc123"
        mock_pr.user.login = "auto-merge-user"
        mock_pr.labels = []
        mock_pr.raw_data = {}
        mock_pr.create_issue_comment = Mock()
        mock_pr.enable_automerge = Mock()
        return mock_pr

    @pytest.mark.asyncio
    async def test_automerge_blocked_by_suspicious_paths(
        self,
        mock_github_webhook: Mock,
        mock_owners_file_handler: Mock,
        mock_pull_request: Mock,
    ) -> None:
        """Auto-merge is blocked and comment posted when PR modifies suspicious paths."""

        mock_owners_file_handler.changed_files = [".github/workflows/ci.yml", "src/main.py"]
        handler = PullRequestHandler(mock_github_webhook, mock_owners_file_handler)

        with patch(
            "webhook_server.libs.handlers.pull_request_handler.github_api_call", new=AsyncMock()
        ) as mock_api_call:
            await handler.set_pull_request_automerge(pull_request=mock_pull_request)

            # Should have called github_api_call for labels fetch + blocking comment
            comment_calls = [
                c
                for c in mock_api_call.call_args_list
                if len(c.args) > 1 and isinstance(c.args[1], str) and "Auto-merge blocked" in c.args[1]
            ]
            assert len(comment_calls) == 1
            assert ".github/workflows/ci.yml" in comment_calls[0].args[1]

    @pytest.mark.asyncio
    async def test_automerge_allowed_without_suspicious_paths(
        self,
        mock_github_webhook: Mock,
        mock_owners_file_handler: Mock,
        mock_pull_request: Mock,
    ) -> None:
        """Auto-merge proceeds when no suspicious paths are modified."""

        mock_owners_file_handler.changed_files = ["src/main.py", "tests/test_main.py"]
        handler = PullRequestHandler(mock_github_webhook, mock_owners_file_handler)

        # github_api_call is called for labels (returns empty list) and enable_automerge
        async def mock_api_side_effect(func: Any, *args: Any, **kwargs: Any) -> Any:
            # When called with a lambda (labels fetch), return empty list
            if callable(func) and not args:
                return func()
            # Otherwise (enable_automerge), just return None
            return None

        with patch(
            "webhook_server.libs.handlers.pull_request_handler.github_api_call", new=AsyncMock(return_value=[])
        ) as mock_api_call:
            await handler.set_pull_request_automerge(pull_request=mock_pull_request)

            # Should have called github_api_call but NOT for blocking comment
            for call in mock_api_call.call_args_list:
                if len(call.args) > 1 and isinstance(call.args[1], str):
                    assert "Auto-merge blocked" not in call.args[1]

    @pytest.mark.asyncio
    async def test_automerge_not_blocked_when_security_paths_empty(
        self,
        mock_github_webhook: Mock,
        mock_owners_file_handler: Mock,
        mock_pull_request: Mock,
    ) -> None:
        """Auto-merge is not blocked when suspicious paths config is empty."""

        mock_github_webhook.security_suspicious_paths = []
        mock_owners_file_handler.changed_files = [".github/workflows/ci.yml"]
        handler = PullRequestHandler(mock_github_webhook, mock_owners_file_handler)

        with patch(
            "webhook_server.libs.handlers.pull_request_handler.github_api_call", new=AsyncMock(return_value=[])
        ) as mock_api_call:
            await handler.set_pull_request_automerge(pull_request=mock_pull_request)

            # Should NOT have posted a blocking comment
            for call in mock_api_call.call_args_list:
                if len(call.args) > 1 and isinstance(call.args[1], str):
                    assert "Auto-merge blocked" not in call.args[1]

    @pytest.mark.asyncio
    async def test_automerge_disabled_when_already_enabled_and_suspicious_paths(
        self,
        mock_github_webhook: Mock,
        mock_owners_file_handler: Mock,
        mock_pull_request: Mock,
    ) -> None:
        """Already-enabled auto-merge is disabled when PR gains suspicious paths on synchronize."""
        # User is NOT in auto-merge list, but PR already has auto-merge enabled
        mock_github_webhook.auto_verified_and_merged_users = []
        mock_github_webhook.set_auto_merge_prs = []
        mock_owners_file_handler.changed_files = [".github/workflows/ci.yml", "src/main.py"]
        mock_pull_request.raw_data = {"auto_merge": {"merge_method": "squash"}}
        handler = PullRequestHandler(mock_github_webhook, mock_owners_file_handler)

        with patch(
            "webhook_server.libs.handlers.pull_request_handler.github_api_call",
            new=AsyncMock(),
        ) as mock_api_call:
            await handler.set_pull_request_automerge(pull_request=mock_pull_request)

            # Should have posted blocking comment AND called disable_automerge
            comment_calls = [
                c
                for c in mock_api_call.call_args_list
                if len(c.args) > 1 and isinstance(c.args[1], str) and "Auto-merge blocked" in c.args[1]
            ]
            assert len(comment_calls) == 1

            disable_calls = [
                c
                for c in mock_api_call.call_args_list
                if len(c.args) > 0 and c.args[0] == mock_pull_request.disable_automerge
            ]
            assert len(disable_calls) == 1


class TestSecurityCheckConstants:
    """Test that security check constants are properly defined."""

    def test_security_constants_values(self) -> None:
        assert SECURITY_SUSPICIOUS_PATHS_STR == "security-suspicious-paths"
        assert SECURITY_COMMITTER_IDENTITY_STR == "security-committer-identity"

    def test_default_suspicious_paths(self) -> None:
        assert ".claude/" in DEFAULT_SUSPICIOUS_PATHS
        assert ".vscode/" in DEFAULT_SUSPICIOUS_PATHS
        assert ".cursor/" in DEFAULT_SUSPICIOUS_PATHS
        assert ".devcontainer/" in DEFAULT_SUSPICIOUS_PATHS
        assert ".pi/" in DEFAULT_SUSPICIOUS_PATHS
        assert ".github/workflows/" in DEFAULT_SUSPICIOUS_PATHS
        assert ".github/actions/" in DEFAULT_SUSPICIOUS_PATHS

    def test_security_checks_in_builtin_check_names(self) -> None:
        assert SECURITY_SUSPICIOUS_PATHS_STR in BUILTIN_CHECK_NAMES
        assert SECURITY_COMMITTER_IDENTITY_STR in BUILTIN_CHECK_NAMES

    def test_security_override_constants(self) -> None:
        assert COMMAND_SECURITY_OVERRIDE_STR == "security-override"


class TestSecurityConfigSanitization:
    """Test config sanitization through the production code path."""

    @pytest.fixture
    def mock_webhook_for_config(self) -> Mock:
        """Create a minimal mock GithubWebhook for _repo_data_from_config testing."""

        mock = Mock()
        mock.logger = Mock()
        mock.log_prefix = "[TEST]"
        mock.config = Mock()
        return mock

    def test_non_string_suspicious_paths_sanitized(self, mock_webhook_for_config: Mock) -> None:
        """Non-string items in suspicious-paths are converted to strings."""

        security_config: dict[str, Any] = {
            "suspicious-paths": [".github/workflows/", 123, 4.5, "", "  ", ".vscode/"],
            "committer-identity-check": True,
        }
        mock_webhook_for_config.config.get_value = Mock(
            side_effect=lambda value, **kwargs: security_config if value == "security-checks" else None
        )

        GithubWebhook._repo_data_from_config(mock_webhook_for_config, repository_config={})
        assert mock_webhook_for_config.security_suspicious_paths == [".github/workflows/", "123", "4.5", ".vscode/"]

    def test_non_list_suspicious_paths_uses_defaults(self, mock_webhook_for_config: Mock) -> None:
        """Non-list suspicious-paths falls back to defaults."""

        security_config: dict[str, Any] = {
            "suspicious-paths": "not-a-list",
            "committer-identity-check": True,
        }
        mock_webhook_for_config.config.get_value = Mock(
            side_effect=lambda value, **kwargs: security_config if value == "security-checks" else None
        )

        GithubWebhook._repo_data_from_config(mock_webhook_for_config, repository_config={})
        assert mock_webhook_for_config.security_suspicious_paths == DEFAULT_SUSPICIOUS_PATHS


class TestSecurityRequiredStatusChecks:
    """Test security checks in all_required_status_checks."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        mock_webhook = Mock()
        mock_webhook.hook_data = {"action": "opened"}
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.token = TEST_GITHUB_TOKEN
        mock_webhook.tox = {}
        mock_webhook.verified_job = True
        mock_webhook.build_and_push_container = {}
        mock_webhook.pypi = {}
        mock_webhook.conventional_title = ""
        mock_webhook.custom_check_runs = []
        mock_webhook.security_suspicious_paths = DEFAULT_SUSPICIOUS_PATHS
        mock_webhook.security_committer_identity_check = True
        mock_webhook.security_trusted_committers = []
        mock_webhook.security_mandatory = True
        mock_webhook.last_commit = Mock()
        mock_webhook.last_commit.sha = "abc123"
        mock_webhook.ctx = None
        mock_webhook.config = Mock()
        mock_webhook.config.get_value = Mock(return_value=None)
        return mock_webhook

    @pytest.fixture
    def mock_owners_file_handler(self) -> Mock:
        mock_handler = Mock()
        return mock_handler

    @pytest.fixture
    def check_run_handler(self, mock_github_webhook: Mock, mock_owners_file_handler: Mock) -> CheckRunHandler:
        return CheckRunHandler(mock_github_webhook, mock_owners_file_handler)

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        mock_pr = Mock()
        mock_pr.number = 123
        mock_pr.base.ref = "main"
        mock_pr.labels = []  # No security-override label
        return mock_pr

    @pytest.fixture(autouse=True)
    def patch_check_run_text(self) -> Generator[None]:
        with patch(
            "webhook_server.libs.handlers.check_run_handler.CheckRunHandler.get_check_run_text",
            return_value="dummy output",
        ):
            yield

    @pytest.mark.asyncio
    async def test_security_checks_required_when_mandatory(
        self, check_run_handler: CheckRunHandler, mock_pull_request: Mock
    ) -> None:
        """Security checks are in required checks when mandatory=true."""
        with patch.object(
            check_run_handler,
            "get_branch_required_status_checks",
            new=AsyncMock(return_value=[]),
        ):
            checks = await check_run_handler.all_required_status_checks(pull_request=mock_pull_request)
            assert SECURITY_SUSPICIOUS_PATHS_STR in checks
            assert SECURITY_COMMITTER_IDENTITY_STR in checks

    @pytest.mark.asyncio
    async def test_security_checks_not_required_when_not_mandatory(
        self, check_run_handler: CheckRunHandler, mock_pull_request: Mock
    ) -> None:
        """Security checks are NOT in required checks when mandatory=false."""
        check_run_handler.github_webhook.security_mandatory = False

        with patch.object(
            check_run_handler,
            "get_branch_required_status_checks",
            new=AsyncMock(return_value=[]),
        ):
            checks = await check_run_handler.all_required_status_checks(pull_request=mock_pull_request)
            assert SECURITY_SUSPICIOUS_PATHS_STR not in checks
            assert SECURITY_COMMITTER_IDENTITY_STR not in checks

    @pytest.mark.asyncio
    async def test_security_checks_partial_config(
        self, check_run_handler: CheckRunHandler, mock_pull_request: Mock
    ) -> None:
        """Only configured security checks are added to required list."""
        check_run_handler.github_webhook.security_suspicious_paths = []
        check_run_handler.github_webhook.security_committer_identity_check = True
        check_run_handler.github_webhook.security_trusted_committers = []

        with patch.object(
            check_run_handler,
            "get_branch_required_status_checks",
            new=AsyncMock(return_value=[]),
        ):
            checks = await check_run_handler.all_required_status_checks(pull_request=mock_pull_request)
            assert SECURITY_SUSPICIOUS_PATHS_STR not in checks
            assert SECURITY_COMMITTER_IDENTITY_STR in checks


class TestSecurityOverrideCommand:
    """Test /security-override command handling."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        mock_webhook = Mock()
        mock_webhook.hook_data = {"action": "created"}
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.repository_full_name = "test-org/test-repo"
        mock_webhook.token = TEST_GITHUB_TOKEN
        mock_webhook.security_mandatory = True
        mock_webhook.security_suspicious_paths = DEFAULT_SUSPICIOUS_PATHS
        mock_webhook.security_committer_identity_check = True
        mock_webhook.security_trusted_committers = []
        mock_webhook.ctx = None
        mock_webhook.config = Mock()
        mock_webhook.config.get_value = Mock(return_value=None)
        return mock_webhook

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        mock_pr = Mock()
        mock_pr.number = 123
        mock_pr.labels = []
        mock_pr.create_issue_comment = Mock()
        return mock_pr

    @pytest.mark.asyncio
    async def test_security_override_by_maintainer(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """Maintainers can set security check runs to success."""

        mock_owners = Mock()
        mock_owners.get_all_repository_maintainers = AsyncMock(return_value=["maintainer-user"])
        mock_owners.all_repository_approvers = ["approver1"]
        mock_owners.is_user_valid_to_run_commands = AsyncMock(return_value=True)

        handler = IssueCommentHandler(mock_github_webhook, mock_owners)

        with (
            patch.object(handler.check_run_handler, "set_check_success", new=AsyncMock()) as mock_success,
            patch.object(handler, "create_comment_reaction", new=AsyncMock()),
            patch(
                "webhook_server.libs.handlers.issue_comment_handler.github_api_call",
                new=AsyncMock(),
            ),
        ):
            await handler.user_commands(
                pull_request=mock_pull_request,
                command=COMMAND_SECURITY_OVERRIDE_STR,
                reviewed_user="maintainer-user",
                issue_comment_id=1,
                is_draft=False,
            )

            # Both security check runs should be set to success
            success_names = [c.kwargs["name"] for c in mock_success.call_args_list]
            assert SECURITY_SUSPICIOUS_PATHS_STR in success_names
            assert SECURITY_COMMITTER_IDENTITY_STR in success_names

    @pytest.mark.asyncio
    async def test_security_override_rejected_for_non_maintainer(
        self, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Non-maintainers cannot use /security-override."""

        mock_owners = Mock()
        mock_owners.get_all_repository_maintainers = AsyncMock(return_value=["maintainer-user"])
        mock_owners.all_repository_approvers = ["approver1"]
        mock_owners.is_user_valid_to_run_commands = AsyncMock(return_value=True)

        handler = IssueCommentHandler(mock_github_webhook, mock_owners)

        with (
            patch.object(handler.check_run_handler, "set_check_success", new=AsyncMock()) as mock_success,
            patch.object(handler, "create_comment_reaction", new=AsyncMock()),
            patch(
                "webhook_server.libs.handlers.issue_comment_handler.github_api_call",
                new=AsyncMock(),
            ),
        ):
            await handler.user_commands(
                pull_request=mock_pull_request,
                command=COMMAND_SECURITY_OVERRIDE_STR,
                reviewed_user="random-user",
                issue_comment_id=1,
                is_draft=False,
            )

            # Check runs should NOT be set to success
            mock_success.assert_not_called()

    @pytest.mark.asyncio
    async def test_security_override_cancel(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """/security-override cancel re-runs security checks."""

        mock_owners = Mock()
        mock_owners.get_all_repository_maintainers = AsyncMock(return_value=["maintainer-user"])
        mock_owners.all_repository_approvers = ["approver1"]
        mock_owners.is_user_valid_to_run_commands = AsyncMock(return_value=True)

        handler = IssueCommentHandler(mock_github_webhook, mock_owners)

        with (
            patch.object(handler.runner_handler, "run_security_suspicious_paths", new=AsyncMock()) as mock_run_paths,
            patch.object(
                handler.runner_handler, "run_security_committer_identity", new=AsyncMock()
            ) as mock_run_identity,
            patch.object(handler, "create_comment_reaction", new=AsyncMock()),
            patch(
                "webhook_server.libs.handlers.issue_comment_handler.github_api_call",
                new=AsyncMock(),
            ),
        ):
            await handler.user_commands(
                pull_request=mock_pull_request,
                command=f"{COMMAND_SECURITY_OVERRIDE_STR} cancel",
                reviewed_user="maintainer-user",
                issue_comment_id=1,
                is_draft=False,
            )

            mock_run_paths.assert_called_once()
            mock_run_identity.assert_called_once()
