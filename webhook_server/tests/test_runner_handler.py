from collections.abc import Generator
from unittest.mock import AsyncMock, Mock, patch

import pytest

from webhook_server.libs.handlers.runner_handler import CheckConfig, RunnerHandler
from webhook_server.utils.constants import (
    BUILD_CONTAINER_STR,
    CONVENTIONAL_TITLE_STR,
    PRE_COMMIT_STR,
    PYTHON_MODULE_INSTALL_STR,
    TOX_STR,
)


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
        mock_webhook.conventional_title = "feat,fix,docs,style,refactor,perf,test,build,ci,chore,revert"
        mock_webhook.container_repository_username = "test-user"
        mock_webhook.container_repository_password = "test-pass"  # pragma: allowlist secret
        mock_webhook.slack_webhook_url = "https://hooks.slack.com/test"
        mock_webhook.repository_full_name = "test/repo"
        mock_webhook.dockerfile = "Dockerfile"
        mock_webhook.container_build_args = []
        mock_webhook.container_command_args = []
        mock_webhook.ctx = None
        mock_webhook.custom_check_runs = []
        mock_webhook.cherry_pick_assign_to_pr_author = False
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
    def patch_check_run_text(self) -> Generator[None]:
        with patch(
            "webhook_server.libs.handlers.check_run_handler.CheckRunHandler.get_check_run_text",
            return_value="dummy output",
        ):
            yield

    @pytest.fixture(autouse=True)
    def patch_shutil_rmtree(self) -> Generator[None]:
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
        with patch(
            "webhook_server.libs.handlers.runner_handler.run_command", new=AsyncMock(return_value=(True, "success", ""))
        ):
            rc, out, _ = await runner_handler.run_podman_command("podman build .")
            assert rc is True
            assert "success" in out  # Relaxed assertion

    @pytest.mark.asyncio
    async def test_run_podman_command_podman_bug(self, runner_handler: RunnerHandler) -> None:
        """Test run_podman_command with podman bug error."""
        podman_bug_err = "Error: current system boot ID differs from cached boot ID; an unhandled reboot has occurred"
        with patch("webhook_server.libs.handlers.runner_handler.run_command", new=AsyncMock()) as mock_run:
            mock_run.side_effect = [(False, "output", podman_bug_err), (True, "success after fix", "")]
            with patch.object(runner_handler, "fix_podman_bug") as mock_fix:
                _, _, _ = await runner_handler.run_podman_command("podman build .")
                assert mock_fix.call_count >= 1

    @pytest.mark.asyncio
    async def test_run_podman_command_other_error(self, runner_handler: RunnerHandler) -> None:
        """Test run_podman_command with other error."""
        with patch(
            "webhook_server.libs.handlers.runner_handler.run_command",
            new=AsyncMock(return_value=(False, "output", "other error")),
        ):
            rc, _, _ = await runner_handler.run_podman_command("podman build .")
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
            with patch.object(runner_handler.check_run_handler, "set_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                    # Simple mock that returns the expected tuple
                    mock_checkout.return_value = AsyncMock()
                    mock_checkout.return_value.__aenter__ = AsyncMock(return_value=(True, "/tmp/worktree-path", "", ""))
                    mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                    with patch(
                        "webhook_server.utils.helpers.run_command", new=AsyncMock(return_value=(True, "success", ""))
                    ):
                        await runner_handler.run_tox(mock_pull_request)
                        mock_set_progress.assert_called_once_with(name=TOX_STR)

    @pytest.mark.asyncio
    async def test_run_tox_prepare_failure(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test run_tox when repository preparation fails."""
        runner_handler.github_webhook.pypi = {"token": ""}
        runner_handler.github_webhook.last_commit = Mock(get_check_runs=Mock(return_value=[]))
        with patch.object(
            runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
        ):
            with patch.object(runner_handler.check_run_handler, "set_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_check_failure") as mock_set_failure:
                    with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                        mock_checkout.return_value = AsyncMock()
                        mock_checkout.return_value.__aenter__ = AsyncMock(
                            return_value=(False, "/tmp/worktree-path", "out", "err")
                        )
                        mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                        await runner_handler.run_tox(mock_pull_request)
                        mock_set_progress.assert_called_once_with(name=TOX_STR)
                        mock_set_failure.assert_called_once_with(
                            name=TOX_STR, output={"title": "Tox", "summary": "", "text": "dummy output"}
                        )

    @pytest.mark.asyncio
    async def test_run_tox_success(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test run_tox with successful execution."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(
            runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
        ):
            with patch.object(
                runner_handler.check_run_handler, "set_check_in_progress", new_callable=AsyncMock
            ) as mock_set_progress:
                with patch.object(
                    runner_handler.check_run_handler, "set_check_success", new_callable=AsyncMock
                ) as mock_set_success:
                    with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                        mock_checkout.return_value = AsyncMock()
                        mock_checkout.return_value.__aenter__ = AsyncMock(
                            return_value=(True, "/tmp/worktree-path", "", "")
                        )
                        mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                        with patch(
                            "webhook_server.libs.handlers.runner_handler.run_command",
                            new=AsyncMock(return_value=(True, "success", "")),
                        ):
                            await runner_handler.run_tox(mock_pull_request)
                            mock_set_progress.assert_called_once_with(name=TOX_STR)
                            mock_set_success.assert_called_once_with(
                                name=TOX_STR, output={"title": "Tox", "summary": "", "text": "dummy output"}
                            )

    @pytest.mark.asyncio
    async def test_run_tox_failure(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test run_tox with failed execution."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(
            runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
        ):
            with patch.object(runner_handler.check_run_handler, "set_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_check_failure") as mock_set_failure:
                    with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                        mock_checkout.return_value = AsyncMock()
                        mock_checkout.return_value.__aenter__ = AsyncMock(
                            return_value=(True, "/tmp/worktree-path", "", "")
                        )
                        mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                        with patch(
                            "webhook_server.utils.helpers.run_command",
                            new=AsyncMock(return_value=(False, "output", "error")),
                        ):
                            await runner_handler.run_tox(mock_pull_request)
                            mock_set_progress.assert_called_once_with(name=TOX_STR)
                            mock_set_failure.assert_called_once_with(
                                name=TOX_STR, output={"title": "Tox", "summary": "", "text": "dummy output"}
                            )

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
            with patch.object(runner_handler.check_run_handler, "set_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_check_success") as mock_set_success:
                    with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                        mock_checkout.return_value = AsyncMock()
                        mock_checkout.return_value.__aenter__ = AsyncMock(
                            return_value=(True, "/tmp/worktree-path", "", "")
                        )
                        mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                        with patch(
                            "webhook_server.libs.handlers.runner_handler.run_command",
                            new=AsyncMock(return_value=(True, "success", "")),
                        ):
                            await runner_handler.run_pre_commit(mock_pull_request)
                            mock_set_progress.assert_called_once_with(name=PRE_COMMIT_STR)
                            mock_set_success.assert_called_once_with(
                                name=PRE_COMMIT_STR,
                                output={"title": "Pre-Commit", "summary": "", "text": "dummy output"},
                            )

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
                    runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()
                ) as mock_set_progress:
                    with patch.object(
                        runner_handler.check_run_handler, "set_check_success", new=AsyncMock()
                    ) as mock_set_success:
                        with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                            mock_checkout.return_value = AsyncMock()
                            mock_checkout.return_value.__aenter__ = AsyncMock(
                                return_value=(True, "/tmp/worktree-path", "", "")
                            )
                            mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                            with patch.object(
                                runner_handler, "run_podman_command", new=AsyncMock(return_value=(True, "success", ""))
                            ):
                                await runner_handler.run_build_container(pull_request=mock_pull_request)
                                mock_set_progress.assert_awaited_once_with(name=BUILD_CONTAINER_STR)
                                mock_set_success.assert_awaited_once_with(
                                    name=BUILD_CONTAINER_STR,
                                    output={"title": "Build container", "summary": "", "text": "dummy output"},
                                )

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
                    runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()
                ) as mock_set_progress:
                    with patch.object(
                        runner_handler.check_run_handler, "set_check_success", new=AsyncMock()
                    ) as mock_set_success:
                        with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                            mock_checkout.return_value = AsyncMock()
                            mock_checkout.return_value.__aenter__ = AsyncMock(
                                return_value=(True, "/tmp/worktree-path", "", "")
                            )
                            mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                            with patch.object(
                                runner_handler, "run_podman_command", new=AsyncMock(return_value=(True, "success", ""))
                            ):
                                await runner_handler.run_build_container(pull_request=mock_pull_request, push=True)
                                mock_set_progress.assert_awaited_once_with(name=BUILD_CONTAINER_STR)
                                mock_set_success.assert_awaited_once_with(
                                    name=BUILD_CONTAINER_STR,
                                    output={"title": "Build container", "summary": "", "text": "dummy output"},
                                )

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
            with patch.object(runner_handler.check_run_handler, "set_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_check_success") as mock_set_success:
                    with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                        mock_checkout.return_value = AsyncMock()
                        mock_checkout.return_value.__aenter__ = AsyncMock(
                            return_value=(True, "/tmp/worktree-path", "", "")
                        )
                        mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                        with patch(
                            "webhook_server.libs.handlers.runner_handler.run_command",
                            new=AsyncMock(return_value=(True, "success", "")),
                        ):
                            await runner_handler.run_install_python_module(mock_pull_request)
                            mock_set_progress.assert_called_once_with(name=PYTHON_MODULE_INSTALL_STR)
                            mock_set_success.assert_called_once_with(
                                name=PYTHON_MODULE_INSTALL_STR,
                                output={"title": "Python module installation", "summary": "", "text": "dummy output"},
                            )

    @pytest.mark.asyncio
    async def test_run_install_python_module_failure(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test run_install_python_module with failed installation."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(
            runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
        ):
            with patch.object(runner_handler.check_run_handler, "set_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_check_failure") as mock_set_failure:
                    with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                        mock_checkout.return_value = AsyncMock()
                        mock_checkout.return_value.__aenter__ = AsyncMock(
                            return_value=(True, "/tmp/worktree-path", "", "")
                        )
                        mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                        with patch(
                            "webhook_server.utils.helpers.run_command",
                            new=AsyncMock(return_value=(False, "output", "error")),
                        ):
                            await runner_handler.run_install_python_module(mock_pull_request)
                            mock_set_progress.assert_called_once_with(name=PYTHON_MODULE_INSTALL_STR)
                            mock_set_failure.assert_called_once_with(
                                name=PYTHON_MODULE_INSTALL_STR,
                                output={"title": "Python module installation", "summary": "", "text": "dummy output"},
                            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "title,should_pass,reason",
        [
            # Valid: Basic format
            ("feat: add authentication", True, "basic feat format"),
            ("fix: resolve parsing error", True, "basic fix format"),
            ("docs: update README", True, "basic docs format"),
            ("style: fix formatting", True, "basic style format"),
            ("refactor: improve code structure", True, "basic refactor format"),
            ("perf: optimize database queries", True, "basic perf format"),
            ("test: add unit tests", True, "basic test format"),
            ("build: update dependencies", True, "basic build format"),
            ("ci: configure GitHub Actions", True, "basic ci format"),
            ("chore: update .gitignore", True, "basic chore format"),
            ("revert: revert previous commit", True, "basic revert format"),
            # Valid: With scope
            ("feat(api): add new endpoint", True, "feat with scope"),
            ("fix(parser): handle edge case", True, "fix with scope"),
            ("docs(readme): update installation steps", True, "docs with scope"),
            ("style(css): improve button styling", True, "style with scope"),
            ("refactor(auth): simplify token handling", True, "refactor with scope"),
            ("perf(db): optimize query performance", True, "perf with scope"),
            ("test(unit): add parser tests", True, "test with scope"),
            ("build(deps): upgrade packages", True, "build with scope"),
            ("ci(actions): update workflow", True, "ci with scope"),
            ("chore(config): update settings", True, "chore with scope"),
            # Valid: With breaking change indicator
            ("feat!: breaking API change", True, "feat with breaking change"),
            ("fix!: breaking bug fix", True, "fix with breaking change"),
            ("refactor!: major refactor", True, "refactor with breaking change"),
            ("feat(api)!: breaking API change", True, "feat with scope and breaking change"),
            ("fix(core)!: breaking bug fix", True, "fix with scope and breaking change"),
            # Valid: Multi-word descriptions
            ("feat: add user authentication with OAuth2", True, "feat with multi-word description"),
            (
                "fix(parser): handle edge case in URL parsing with special characters",
                True,
                "fix with scope and long description",
            ),
            ("docs: update installation guide for Windows users", True, "docs with multi-word description"),
            # Valid: Spec examples
            ("docs: correct spelling of CHANGELOG", True, "conventional commits spec example 1"),
            ("feat(lang): add Polish language", True, "conventional commits spec example 2"),
            ("fix: prevent racing of requests", True, "conventional commits spec example 3"),
            # Valid: Special characters in description
            ("feat: add support for UTF-8 characters 日本語", True, "feat with UTF-8 characters"),
            ("fix: handle URLs with query params ?foo=bar&baz=qux", True, "fix with special characters"),
            ("docs: update guide with symbols @#$%", True, "docs with symbols"),
            # Valid: Numbers in scope
            ("feat(v2): add version 2 API", True, "feat with version number in scope"),
            ("fix(CVE-2023-1234): security patch", True, "fix with CVE number in scope"),
            ("docs(python3.12): update compatibility notes", True, "docs with version in scope"),
            # Invalid: Missing space after colon
            ("feat:no space", False, "missing space after colon"),
            ("fix(api):missing space", False, "missing space after colon with scope"),
            ("docs:test", False, "missing space after colon for docs"),
            # Invalid: Empty description
            ("feat:", False, "empty description"),
            ("feat: ", False, "empty description with space"),
            ("fix(scope): ", False, "empty description with scope"),
            ("fix(scope):", False, "empty description with scope no space"),
            # Invalid: Wrong type
            ("Feature: add authentication", False, "wrong type capitalized"),
            ("FEAT: add auth", False, "wrong type uppercase"),
            ("bugfix: fix issue", False, "wrong type bugfix instead of fix"),
            ("feature: add new feature", False, "wrong type feature instead of feat"),
            ("documentation: update docs", False, "wrong type documentation instead of docs"),
            # Invalid: Missing colon
            ("feat add auth", False, "missing colon"),
            ("fix parser error", False, "missing colon for fix"),
            ("docs update README", False, "missing colon for docs"),
            # Invalid: Invalid characters before colon
            ("feat hello: test", False, "invalid characters before colon"),
            ("fix test test: broken", False, "invalid characters before colon"),
            # Invalid: Malformed scope
            ("feat(: broken scope", False, "malformed scope - missing closing paren"),
            ("feat): broken scope", False, "malformed scope - missing opening paren"),
            ("feat(): empty scope", False, "malformed scope - empty scope"),
            ("feat(api)(auth): multiple scopes", False, "malformed scope - multiple scopes not allowed"),
            # Invalid: No description after type
            ("feat", False, "no colon or description"),
            ("fix(api)", False, "no colon or description with scope"),
            ("docs!", False, "no colon or description with breaking change indicator"),
            # Edge cases: Numbers and special characters
            ("fix: handle error #123", True, "fix with issue number"),
            ("feat: add support for v1.0.0", True, "feat with version number"),
            ("chore: update deps (security)", True, "chore with parentheses in description"),
        ],
    )
    async def test_conventional_title_validation(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock, title: str, should_pass: bool, reason: str
    ) -> None:
        """Test Conventional Commits v1.0.0 title validation.

        Tests comprehensive validation covering:
        - Valid formats (basic, with scope, with breaking change indicator)
        - Multi-word descriptions
        - Special characters and UTF-8
        - Invalid formats (missing space, empty description, wrong type, malformed scope)
        - Edge cases (numbers, symbols, etc.)
        """
        mock_pull_request.title = title

        with patch.object(
            runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
        ):
            with patch.object(
                runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()
            ) as mock_set_progress:
                with patch.object(
                    runner_handler.check_run_handler, "set_check_success", new=AsyncMock()
                ) as mock_set_success:
                    with patch.object(
                        runner_handler.check_run_handler, "set_check_failure", new=AsyncMock()
                    ) as mock_set_failure:
                        await runner_handler.run_conventional_title_check(mock_pull_request)

                        mock_set_progress.assert_awaited_once_with(name=CONVENTIONAL_TITLE_STR)

                        if should_pass:
                            assert mock_set_success.await_count == 1, (
                                f"Expected '{title}' to pass validation ({reason}), but it failed"
                            )
                            mock_set_failure.assert_not_awaited()
                        else:
                            assert mock_set_failure.await_count == 1, (
                                f"Expected '{title}' to fail validation ({reason}), but it passed"
                            )
                            mock_set_success.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_conventional_title_check_disabled(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test run_conventional_title_check when conventional_title is not configured."""
        runner_handler.github_webhook.conventional_title = ""

        with patch.object(
            runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()
        ) as mock_set_progress:
            with patch.object(
                runner_handler.check_run_handler, "set_check_success", new=AsyncMock()
            ) as mock_set_success:
                with patch.object(
                    runner_handler.check_run_handler, "set_check_failure", new=AsyncMock()
                ) as mock_set_failure:
                    await runner_handler.run_conventional_title_check(mock_pull_request)

                    # Should return early without doing anything
                    mock_set_progress.assert_not_awaited()
                    mock_set_success.assert_not_awaited()
                    mock_set_failure.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_conventional_title_check_custom_types(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test run_conventional_title_check with custom type configuration."""
        runner_handler.github_webhook.conventional_title = "my-title,hotfix,custom"

        # Valid custom types
        valid_titles = [
            "my-title: custom type example",
            "hotfix: critical production fix",
            "custom: special handling",
            "my-title(api): custom type with scope",
            "hotfix!: breaking hotfix",
        ]

        for title in valid_titles:
            mock_pull_request.title = title

            with patch.object(
                runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
            ):
                with patch.object(
                    runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()
                ) as mock_set_progress:
                    with patch.object(
                        runner_handler.check_run_handler, "set_check_success", new=AsyncMock()
                    ) as mock_set_success:
                        with patch.object(
                            runner_handler.check_run_handler, "set_check_failure", new=AsyncMock()
                        ) as mock_set_failure:
                            await runner_handler.run_conventional_title_check(mock_pull_request)

                            mock_set_progress.assert_awaited_once_with(name=CONVENTIONAL_TITLE_STR)
                            mock_set_success.assert_awaited_once()
                            mock_set_failure.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_conventional_title_check_in_progress(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test run_conventional_title_check when check is already in progress."""
        mock_pull_request.title = "feat: test feature"

        with patch.object(
            runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=True)
        ):
            with patch.object(
                runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()
            ) as mock_set_progress:
                with patch.object(
                    runner_handler.check_run_handler, "set_check_success", new=AsyncMock()
                ) as mock_set_success:
                    await runner_handler.run_conventional_title_check(mock_pull_request)

                    # Should still proceed with the check
                    mock_set_progress.assert_awaited_once_with(name=CONVENTIONAL_TITLE_STR)
                    mock_set_success.assert_awaited_once()

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
            with patch.object(mock_pull_request, "create_issue_comment", new=Mock()) as mock_comment:
                await runner_handler.cherry_pick(mock_pull_request, "non-existent-branch")
                mock_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_cherry_pick_prepare_failure(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test cherry_pick when repository preparation fails."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(runner_handler, "is_branch_exists", new=AsyncMock(return_value=Mock())):
            with patch.object(runner_handler.check_run_handler, "set_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_check_failure") as mock_set_failure:
                    with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                        mock_checkout.return_value = AsyncMock()
                        mock_checkout.return_value.__aenter__ = AsyncMock(
                            return_value=(False, "/tmp/worktree-path", "out", "err")
                        )
                        mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                        await runner_handler.cherry_pick(mock_pull_request, "main")
                        mock_set_progress.assert_called_once()
                        assert mock_set_failure.call_count >= 1

    @pytest.mark.asyncio
    async def test_cherry_pick_command_failure(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test cherry_pick when git command fails."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(runner_handler, "is_branch_exists", new=AsyncMock(return_value=Mock())):
            with patch.object(runner_handler.check_run_handler, "set_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_check_failure") as mock_set_failure:
                    with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                        mock_checkout.return_value = AsyncMock()
                        mock_checkout.return_value.__aenter__ = AsyncMock(
                            return_value=(True, "/tmp/worktree-path", "", "")
                        )
                        mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
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
            with patch.object(runner_handler.check_run_handler, "set_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_check_success") as mock_set_success:
                    with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                        mock_checkout.return_value = AsyncMock()
                        mock_checkout.return_value.__aenter__ = AsyncMock(
                            return_value=(True, "/tmp/worktree-path", "", "")
                        )
                        mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                        with patch(
                            "webhook_server.libs.handlers.runner_handler.run_command",
                            new=AsyncMock(return_value=(True, "success", "")),
                        ):
                            with patch.object(mock_pull_request, "create_issue_comment", new=Mock()) as mock_comment:
                                await runner_handler.cherry_pick(mock_pull_request, "main")
                                mock_set_progress.assert_called_once()
                                mock_set_success.assert_called_once()
                                mock_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_checkout_worktree_success(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test _checkout_worktree with successful preparation."""
        with patch("webhook_server.utils.helpers.git_worktree_checkout") as mock_git_worktree:
            mock_git_worktree.return_value.__aenter__ = AsyncMock(
                return_value=(True, "/tmp/worktree-path", "success", "")
            )
            mock_git_worktree.return_value.__aexit__ = AsyncMock(return_value=None)
            with patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(return_value=(True, "success", "")),
            ):
                async with runner_handler._checkout_worktree(pull_request=mock_pull_request) as result:
                    success, worktree_path, _, _ = result
                    assert success is True
                    assert worktree_path == "/tmp/worktree-path"

    @pytest.mark.asyncio
    async def test_checkout_worktree_failure(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test _checkout_worktree when checkout fails."""
        with patch("webhook_server.utils.helpers.git_worktree_checkout") as mock_git_worktree:
            mock_git_worktree.return_value.__aenter__ = AsyncMock(return_value=(False, "", "output", "error"))
            mock_git_worktree.return_value.__aexit__ = AsyncMock(return_value=None)
            async with runner_handler._checkout_worktree(pull_request=mock_pull_request) as result:
                success, _, out, _ = result
                assert success is False
                assert out == "output"

    @pytest.mark.asyncio
    async def test_checkout_worktree_with_checkout(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test _checkout_worktree with checkout parameter."""
        with patch("webhook_server.utils.helpers.git_worktree_checkout") as mock_git_worktree:
            mock_git_worktree.return_value.__aenter__ = AsyncMock(
                return_value=(True, "/tmp/worktree-path", "success", "")
            )
            mock_git_worktree.return_value.__aexit__ = AsyncMock(return_value=None)
            with patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(return_value=(True, "success", "")),
            ):
                async with runner_handler._checkout_worktree(
                    pull_request=mock_pull_request, checkout="feature-branch"
                ) as result:
                    success, _, _, _ = result
                    assert success is True

    @pytest.mark.asyncio
    async def test_checkout_worktree_with_tag(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test _checkout_worktree with tag_name parameter."""
        with patch("webhook_server.utils.helpers.git_worktree_checkout") as mock_git_worktree:
            mock_git_worktree.return_value.__aenter__ = AsyncMock(
                return_value=(True, "/tmp/worktree-path", "success", "")
            )
            mock_git_worktree.return_value.__aexit__ = AsyncMock(return_value=None)
            with patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(return_value=(True, "success", "")),
            ):
                async with runner_handler._checkout_worktree(
                    pull_request=mock_pull_request, tag_name="v1.0.0"
                ) as result:
                    success, _, _, _ = result
                    assert success is True

    @pytest.mark.asyncio
    async def test_checkout_worktree_merged_pr(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test _checkout_worktree with merged pull request."""
        with patch("webhook_server.utils.helpers.git_worktree_checkout") as mock_git_worktree:
            mock_git_worktree.return_value.__aenter__ = AsyncMock(
                return_value=(True, "/tmp/worktree-path", "success", "")
            )
            mock_git_worktree.return_value.__aexit__ = AsyncMock(return_value=None)
            async with runner_handler._checkout_worktree(pull_request=mock_pull_request, is_merged=True) as result:
                success, _, _, _ = result
                assert success is True

    @pytest.mark.asyncio
    async def test_checkout_worktree_merge_failure(self, runner_handler, mock_pull_request):
        """Test _checkout_worktree when merge fails."""
        with patch("webhook_server.utils.helpers.git_worktree_checkout") as mock_git_worktree:
            mock_git_worktree.return_value.__aenter__ = AsyncMock(return_value=(True, "/tmp/worktree-path", "ok", ""))
            mock_git_worktree.return_value.__aexit__ = AsyncMock(return_value=None)
            with patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(return_value=(False, "fail", "merge conflict")),
            ):
                async with runner_handler._checkout_worktree(pull_request=mock_pull_request) as result:
                    success, _, out, _ = result
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
                    runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()
                ) as mock_set_progress:
                    with patch.object(
                        runner_handler.check_run_handler, "set_check_success", new=AsyncMock()
                    ) as mock_set_success:
                        with patch.object(
                            runner_handler.check_run_handler, "set_check_failure", new=AsyncMock()
                        ) as mock_set_failure:
                            with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                                mock_checkout.return_value = AsyncMock()
                                mock_checkout.return_value.__aenter__ = AsyncMock(
                                    return_value=(True, "/tmp/worktree-path", "", "")
                                )
                                mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                                with patch.object(runner_handler, "run_podman_command") as mock_run_podman:
                                    # First call (build) succeeds, second call (push) fails
                                    mock_run_podman.side_effect = [
                                        (True, "build success", ""),
                                        (False, "push fail", "push error"),
                                    ]
                                    with patch.object(
                                        runner_handler.github_webhook, "slack_webhook_url", "http://slack"
                                    ):
                                        with patch(
                                            "webhook_server.libs.handlers.runner_handler.send_slack_message"
                                        ) as mock_slack:
                                            with patch.object(
                                                mock_pull_request,
                                                "create_issue_comment",
                                                new=Mock(),
                                            ) as mock_comment:
                                                # Set set_check=False to avoid early return after build success
                                                await runner_handler.run_build_container(
                                                    pull_request=mock_pull_request, push=True, set_check=False
                                                )
                                                # Should not call set_progress because set_check=False
                                                mock_set_progress.assert_not_awaited()
                                                # Should not call set_success because set_check=False
                                                mock_set_success.assert_not_awaited()
                                                # Slack message should be sent when push fails
                                                mock_slack.assert_called_once()
                                                # Should be called twice: build and push
                                                assert mock_run_podman.call_count == 2, (
                                                    f"Expected 2 calls, got {mock_run_podman.call_count}"
                                                )
                                                # PR comment should be created on push failure
                                                mock_comment.assert_called_once()
                                                mock_set_failure.assert_not_awaited()

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
                    runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()
                ) as mock_set_progress:
                    with patch.object(
                        runner_handler.check_run_handler, "set_check_success", new=AsyncMock()
                    ) as mock_set_success:
                        with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                            mock_checkout.return_value = AsyncMock()
                            mock_checkout.return_value.__aenter__ = AsyncMock(
                                return_value=(True, "/tmp/worktree-path", "", "")
                            )
                            mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                            with patch.object(runner_handler, "run_podman_command", return_value=(True, "success", "")):
                                await runner_handler.run_build_container(
                                    pull_request=mock_pull_request, command_args="--extra-arg"
                                )
                                mock_set_progress.assert_awaited_once_with(name=BUILD_CONTAINER_STR)
                                mock_set_success.assert_awaited_once_with(
                                    name=BUILD_CONTAINER_STR,
                                    output={"title": "Build container", "summary": "", "text": "dummy output"},
                                )

    @pytest.mark.asyncio
    async def test_cherry_pick_manual_needed(self, runner_handler, mock_pull_request):
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(runner_handler, "is_branch_exists", new=AsyncMock(return_value=Mock())):
            with patch.object(runner_handler.check_run_handler, "set_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_check_failure") as mock_set_failure:
                    with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                        mock_checkout.return_value = AsyncMock()
                        mock_checkout.return_value.__aenter__ = AsyncMock(
                            return_value=(True, "/tmp/worktree-path", "", "")
                        )
                        mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                        # First command fails, triggers manual cherry-pick
                        with patch("webhook_server.utils.helpers.run_command", side_effect=[(False, "fail", "err")]):
                            with patch.object(mock_pull_request, "create_issue_comment", new=Mock()) as mock_comment:
                                await runner_handler.cherry_pick(mock_pull_request, "main")
                                mock_set_progress.assert_called_once()
                                mock_set_failure.assert_called_once()
                                mock_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_cherry_pick_assigns_to_pr_author(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test cherry_pick assigns to the original PR author, not the requester."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        runner_handler.github_webhook.cherry_pick_assign_to_pr_author = True
        mock_pull_request.user = Mock()
        mock_pull_request.user.login = "original-pr-author"
        with patch.object(runner_handler, "is_branch_exists", new=AsyncMock(return_value=Mock())):
            with patch.object(runner_handler.check_run_handler, "set_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_check_success") as mock_set_success:
                    with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                        mock_checkout.return_value = AsyncMock()
                        mock_checkout.return_value.__aenter__ = AsyncMock(
                            return_value=(True, "/tmp/worktree-path", "", "")
                        )
                        mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                        with patch(
                            "webhook_server.libs.handlers.runner_handler.run_command",
                            new=AsyncMock(return_value=(True, "success", "")),
                        ) as mock_run_cmd:
                            with patch.object(mock_pull_request, "create_issue_comment", new=Mock()) as mock_comment:
                                with patch(
                                    "asyncio.to_thread",
                                    new=AsyncMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw) if a or kw else fn()),
                                ) as mock_to_thread:
                                    await runner_handler.cherry_pick(
                                        mock_pull_request, "main", reviewed_user="cherry-requester"
                                    )
                                    mock_set_progress.assert_called_once()
                                    mock_set_success.assert_called_once()
                                    mock_comment.assert_called_once()
                                    # Exactly 2 calls: user.login + create_issue_comment
                                    assert mock_to_thread.call_count == 2
                                    # Verify the hub command assigns to the PR author, NOT the requester
                                    last_cmd = mock_run_cmd.call_args_list[-1]
                                    hub_command = last_cmd.kwargs.get(
                                        "command", last_cmd.args[0] if last_cmd.args else ""
                                    )
                                    assert (
                                        "-a 'original-pr-author'" in hub_command
                                        or "-a original-pr-author" in hub_command
                                    )

    @pytest.mark.asyncio
    async def test_cherry_pick_always_assigns_to_pr_author_when_flag_set(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test cherry_pick always uses pull_request.user.login as assignee.

        When cherry_pick_assign_to_pr_author is True, regardless of reviewed_user.
        """
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        runner_handler.github_webhook.cherry_pick_assign_to_pr_author = True
        mock_pull_request.user = Mock()
        mock_pull_request.user.login = "pr-author-login"
        with patch.object(runner_handler, "is_branch_exists", new=AsyncMock(return_value=Mock())):
            with patch.object(runner_handler.check_run_handler, "set_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_check_success") as mock_set_success:
                    with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                        mock_checkout.return_value = AsyncMock()
                        mock_checkout.return_value.__aenter__ = AsyncMock(
                            return_value=(True, "/tmp/worktree-path", "", "")
                        )
                        mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                        with patch(
                            "webhook_server.libs.handlers.runner_handler.run_command",
                            new=AsyncMock(return_value=(True, "success", "")),
                        ) as mock_run_cmd:
                            with patch.object(mock_pull_request, "create_issue_comment", new=Mock()) as mock_comment:
                                with patch(
                                    "asyncio.to_thread",
                                    new=AsyncMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw) if a or kw else fn()),
                                ) as mock_to_thread:
                                    await runner_handler.cherry_pick(mock_pull_request, "main", reviewed_user="")
                                    mock_set_progress.assert_called_once()
                                    mock_set_success.assert_called_once()
                                    mock_comment.assert_called_once()
                                    # Exactly 2 calls: user.login + create_issue_comment
                                    assert mock_to_thread.call_count == 2
                                    # Verify assignee is always pull_request.user.login, not reviewed_user
                                    last_cmd = mock_run_cmd.call_args_list[-1]
                                    hub_command = last_cmd.kwargs.get(
                                        "command", last_cmd.args[0] if last_cmd.args else ""
                                    )
                                    assert "-a 'pr-author-login'" in hub_command or "-a pr-author-login" in hub_command

    @pytest.mark.asyncio
    async def test_cherry_pick_by_label_requested_by_format(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test cherry_pick by_label produces correct requested-by format in hub command."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        runner_handler.github_webhook.cherry_pick_assign_to_pr_author = True
        mock_pull_request.user = Mock()
        mock_pull_request.user.login = "pr-author-login"
        with patch.object(runner_handler, "is_branch_exists", new=AsyncMock(return_value=Mock())):
            with patch.object(runner_handler.check_run_handler, "set_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_check_success") as mock_set_success:
                    with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                        mock_checkout.return_value = AsyncMock()
                        mock_checkout.return_value.__aenter__ = AsyncMock(
                            return_value=(True, "/tmp/worktree-path", "", "")
                        )
                        mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                        with patch(
                            "webhook_server.libs.handlers.runner_handler.run_command",
                            new=AsyncMock(return_value=(True, "success", "")),
                        ) as mock_run_cmd:
                            with patch.object(mock_pull_request, "create_issue_comment", new=Mock()) as mock_comment:
                                with patch(
                                    "asyncio.to_thread",
                                    new=AsyncMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw) if a or kw else fn()),
                                ) as mock_to_thread:
                                    await runner_handler.cherry_pick(
                                        mock_pull_request, "main", reviewed_user="label-requester", by_label=True
                                    )
                                    mock_set_progress.assert_called_once()
                                    mock_set_success.assert_called_once()
                                    mock_comment.assert_called_once()
                                    # Verify the hub command's last -m contains the by_label format
                                    last_cmd = mock_run_cmd.call_args_list[-1]
                                    hub_command = last_cmd.kwargs.get(
                                        "command", last_cmd.args[0] if last_cmd.args else ""
                                    )
                                    assert "requested-by by label-requester with target-branch label" in hub_command
                                    assert "-a 'pr-author-login'" in hub_command or "-a pr-author-login" in hub_command
                                    assert mock_to_thread.call_count == 2

    @pytest.mark.asyncio
    async def test_cherry_pick_by_label_empty_reviewed_user_requested_by_format(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test cherry_pick by_label with empty reviewed_user produces clean requested-by string."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        runner_handler.github_webhook.cherry_pick_assign_to_pr_author = False
        with patch.object(runner_handler, "is_branch_exists", new=AsyncMock(return_value=Mock())):
            with patch.object(runner_handler.check_run_handler, "set_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_check_success") as mock_set_success:
                    with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                        mock_checkout.return_value = AsyncMock()
                        mock_checkout.return_value.__aenter__ = AsyncMock(
                            return_value=(True, "/tmp/worktree-path", "", "")
                        )
                        mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                        with patch(
                            "webhook_server.libs.handlers.runner_handler.run_command",
                            new=AsyncMock(return_value=(True, "success", "")),
                        ) as mock_run_cmd:
                            with patch.object(mock_pull_request, "create_issue_comment", new=Mock()) as mock_comment:
                                with patch(
                                    "asyncio.to_thread",
                                    new=AsyncMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw) if a or kw else fn()),
                                ):
                                    await runner_handler.cherry_pick(
                                        mock_pull_request, "main", reviewed_user="", by_label=True
                                    )
                                    mock_set_progress.assert_called_once()
                                    mock_set_success.assert_called_once()
                                    mock_comment.assert_called_once()
                                    # Verify the hub command contains clean requested-by string
                                    last_cmd = mock_run_cmd.call_args_list[-1]
                                    hub_command = last_cmd.kwargs.get(
                                        "command", last_cmd.args[0] if last_cmd.args else ""
                                    )
                                    # Should contain "requested-by by target-branch label" (no double space)
                                    assert "requested-by by target-branch label" in hub_command
                                    # The double-space bug: "by  with" should NOT appear
                                    assert "by  with" not in hub_command

    @pytest.mark.asyncio
    async def test_cherry_pick_disabled_no_assignee(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test cherry_pick does not include -a flag when cherry_pick_assign_to_pr_author is False."""
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        runner_handler.github_webhook.cherry_pick_assign_to_pr_author = False
        with patch.object(runner_handler, "is_branch_exists", new=AsyncMock(return_value=Mock())):
            with patch.object(runner_handler.check_run_handler, "set_check_in_progress") as mock_set_progress:
                with patch.object(runner_handler.check_run_handler, "set_check_success") as mock_set_success:
                    with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                        mock_checkout.return_value = AsyncMock()
                        mock_checkout.return_value.__aenter__ = AsyncMock(
                            return_value=(True, "/tmp/worktree-path", "", "")
                        )
                        mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                        with patch(
                            "webhook_server.libs.handlers.runner_handler.run_command",
                            new=AsyncMock(return_value=(True, "success", "")),
                        ) as mock_run_cmd:
                            with patch.object(mock_pull_request, "create_issue_comment", new=Mock()) as mock_comment:
                                await runner_handler.cherry_pick(
                                    mock_pull_request, "main", reviewed_user="cherry-requester"
                                )
                                mock_set_progress.assert_called_once()
                                mock_set_success.assert_called_once()
                                mock_comment.assert_called_once()
                                # Verify the hub pull-request command does NOT contain -a flag
                                last_cmd = mock_run_cmd.call_args_list[-1]
                                hub_command = last_cmd.kwargs.get("command", last_cmd.args[0] if last_cmd.args else "")
                                assert " -a " not in hub_command

    @pytest.mark.asyncio
    async def test_checkout_worktree_branch_already_checked_out(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test _checkout_worktree when target branch is already checked out in main clone.

        This tests the worktree conflict fix - when the target branch is already
        checked out in the main clone, use the main clone instead of creating a worktree.
        """
        # Mock git command to return current branch as "main"
        with patch(
            "webhook_server.libs.handlers.runner_handler.run_command",
            new=AsyncMock(return_value=(True, "main\n", "")),
        ):
            # Pass checkout="main" which matches the current branch
            async with runner_handler._checkout_worktree(pull_request=mock_pull_request, checkout="main") as result:
                success, worktree_path, out, err = result
                assert success is True
                # Should use main clone directory instead of creating worktree
                assert worktree_path == runner_handler.github_webhook.clone_repo_dir
                assert out == ""
                assert err == ""

    @pytest.mark.asyncio
    async def test_checkout_worktree_branch_already_checked_out_with_origin_prefix(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test _checkout_worktree when target branch with origin/ prefix matches current branch.

        This tests the normalization logic that strips origin/ prefix from checkout target.
        """
        # Mock git command to return current branch as "main"
        with patch(
            "webhook_server.libs.handlers.runner_handler.run_command",
            new=AsyncMock(return_value=(True, "main\n", "")),
        ):
            # Pass checkout="origin/main" which normalizes to "main" and matches current branch
            async with runner_handler._checkout_worktree(
                pull_request=mock_pull_request, checkout="origin/main"
            ) as result:
                success, worktree_path, out, err = result
                assert success is True
                # Should use main clone directory instead of creating worktree
                assert worktree_path == runner_handler.github_webhook.clone_repo_dir
                assert out == ""
                assert err == ""

    @pytest.mark.asyncio
    async def test_checkout_worktree_different_branch(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test _checkout_worktree when target branch differs from current branch.

        This tests that a worktree is created when branches don't match.
        """
        # Mock git command to return current branch as "main"
        with patch(
            "webhook_server.libs.handlers.runner_handler.run_command",
            new=AsyncMock(return_value=(True, "main\n", "")),
        ):
            with patch("webhook_server.utils.helpers.git_worktree_checkout") as mock_git_worktree:
                mock_git_worktree.return_value.__aenter__ = AsyncMock(
                    return_value=(True, "/tmp/worktree-path", "success", "")
                )
                mock_git_worktree.return_value.__aexit__ = AsyncMock(return_value=None)
                # Pass checkout="feature-branch" which differs from current "main"
                async with runner_handler._checkout_worktree(
                    pull_request=mock_pull_request, checkout="feature-branch"
                ) as result:
                    success, worktree_path, _, _ = result
                    assert success is True
                    # Should create a worktree, not use main clone
                    assert worktree_path == "/tmp/worktree-path"
                    # Verify git_worktree_checkout was called
                    mock_git_worktree.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_build_container_prepare_failure(
        self, runner_handler: RunnerHandler, mock_pull_request: Mock
    ) -> None:
        """Test run_build_container returns early when repository preparation fails.

        This tests the early return logic added in lines 385-392.
        """
        runner_handler.github_webhook.pypi = {"token": "dummy"}
        with patch.object(
            runner_handler.github_webhook, "container_repository_and_tag", return_value="test/repo:latest"
        ):
            with patch.object(
                runner_handler.check_run_handler, "is_check_run_in_progress", new=AsyncMock(return_value=False)
            ):
                with patch.object(
                    runner_handler.check_run_handler, "set_check_in_progress", new=AsyncMock()
                ) as mock_set_progress:
                    with patch.object(
                        runner_handler.check_run_handler, "set_check_failure", new=AsyncMock()
                    ) as mock_set_failure:
                        with patch.object(runner_handler, "_checkout_worktree") as mock_checkout:
                            # Repository preparation fails
                            mock_checkout.return_value = AsyncMock()
                            mock_checkout.return_value.__aenter__ = AsyncMock(
                                return_value=(False, "/tmp/worktree-path", "checkout failed", "checkout error")
                            )
                            mock_checkout.return_value.__aexit__ = AsyncMock(return_value=None)
                            with patch.object(runner_handler, "run_podman_command", new=AsyncMock()) as mock_run_podman:
                                await runner_handler.run_build_container(pull_request=mock_pull_request)
                                # Should set in progress
                                mock_set_progress.assert_awaited_once_with(name=BUILD_CONTAINER_STR)
                                # Should set failure due to repo preparation failure
                                mock_set_failure.assert_awaited_once_with(
                                    name=BUILD_CONTAINER_STR,
                                    output={"title": "Build container", "summary": "", "text": "dummy output"},
                                )
                                # Should NOT call run_podman_command (early return)
                                mock_run_podman.assert_not_called()


class TestCheckConfig:
    """Test suite for CheckConfig dataclass."""

    def test_check_config_basic(self) -> None:
        """Test CheckConfig with basic parameters."""
        config = CheckConfig(name="test-check", command="echo hello", title="Test Check")
        assert config.name == "test-check"
        assert config.command == "echo hello"
        assert config.title == "Test Check"
        assert config.use_cwd is False  # Default value

    def test_check_config_with_use_cwd(self) -> None:
        """Test CheckConfig with use_cwd enabled."""
        config = CheckConfig(name="custom", command="run test", title="Custom", use_cwd=True)
        assert config.name == "custom"
        assert config.use_cwd is True

    def test_check_config_immutable(self) -> None:
        """Test that CheckConfig is immutable (frozen)."""
        config = CheckConfig(name="test", command="cmd", title="Title")
        with pytest.raises(AttributeError):
            config.name = "new-name"  # type: ignore[misc]

    def test_check_config_with_placeholder(self) -> None:
        """Test CheckConfig with worktree_path placeholder."""
        config = CheckConfig(
            name="tox",
            command="tox --workdir {worktree_path} --root {worktree_path}",
            title="Tox",
        )
        # Verify placeholder can be formatted
        formatted = config.command.format(worktree_path="/tmp/worktree")
        assert formatted == "tox --workdir /tmp/worktree --root /tmp/worktree"


class TestRunCheck:
    """Test suite for the unified run_check method."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance."""
        mock_webhook = Mock()
        mock_webhook.hook_data = {"action": "opened"}
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.clone_repo_dir = "/tmp/test-repo"
        mock_webhook.mask_sensitive = True
        mock_webhook.token = "test-token"
        mock_webhook.ctx = None
        return mock_webhook

    @pytest.fixture
    def runner_handler(self, mock_github_webhook: Mock) -> RunnerHandler:
        """Create a RunnerHandler instance with mocked dependencies."""
        handler = RunnerHandler(mock_github_webhook)
        handler.check_run_handler.is_check_run_in_progress = AsyncMock(return_value=False)
        handler.check_run_handler.set_check_in_progress = AsyncMock()
        handler.check_run_handler.set_check_success = AsyncMock()
        handler.check_run_handler.set_check_failure = AsyncMock()
        handler.check_run_handler.get_check_run_text = Mock(return_value="output text")
        return handler

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        """Create a mock PullRequest instance."""
        mock_pr = Mock()
        mock_pr.number = 123
        mock_pr.base = Mock()
        mock_pr.base.ref = "main"
        mock_pr.head = Mock()
        mock_pr.head.ref = "feature-branch"
        return mock_pr

    @pytest.mark.asyncio
    async def test_run_check_success(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test run_check with successful command execution."""
        check_config = CheckConfig(
            name="my-check",
            command="echo {worktree_path}",
            title="My Check",
        )

        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, "/tmp/worktree", "", ""))
        mock_checkout_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(runner_handler, "_checkout_worktree", return_value=mock_checkout_cm),
            patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(return_value=(True, "success output", "")),
            ) as mock_run,
        ):
            await runner_handler.run_check(pull_request=mock_pull_request, check_config=check_config)

            runner_handler.check_run_handler.set_check_in_progress.assert_called_once_with(name="my-check")
            runner_handler.check_run_handler.set_check_success.assert_called_once()
            # Verify command was formatted with worktree_path
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args.kwargs["command"] == "echo /tmp/worktree"

    @pytest.mark.asyncio
    async def test_run_check_failure(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test run_check with failed command execution."""
        check_config = CheckConfig(
            name="failing-check",
            command="false",
            title="Failing Check",
        )

        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, "/tmp/worktree", "", ""))
        mock_checkout_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(runner_handler, "_checkout_worktree", return_value=mock_checkout_cm),
            patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(return_value=(False, "output", "error")),
            ),
        ):
            await runner_handler.run_check(pull_request=mock_pull_request, check_config=check_config)

            runner_handler.check_run_handler.set_check_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_check_checkout_failure(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test run_check when worktree checkout fails."""
        check_config = CheckConfig(
            name="test-check",
            command="echo test",
            title="Test Check",
        )

        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(False, "", "checkout failed", "error"))
        mock_checkout_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(runner_handler, "_checkout_worktree", return_value=mock_checkout_cm):
            await runner_handler.run_check(pull_request=mock_pull_request, check_config=check_config)

            runner_handler.check_run_handler.set_check_failure.assert_called_once()
            runner_handler.check_run_handler.set_check_success.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_check_with_cwd(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test run_check with use_cwd enabled."""
        check_config = CheckConfig(
            name="cwd-check",
            command="run-in-dir",  # No placeholder - uses cwd
            title="CWD Check",
            use_cwd=True,
        )

        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, "/tmp/worktree", "", ""))
        mock_checkout_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(runner_handler, "_checkout_worktree", return_value=mock_checkout_cm),
            patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(return_value=(True, "success", "")),
            ) as mock_run,
        ):
            await runner_handler.run_check(pull_request=mock_pull_request, check_config=check_config)

            # Verify cwd was passed to run_command
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args.kwargs["cwd"] == "/tmp/worktree"

    @pytest.mark.asyncio
    async def test_run_check_in_progress_rerun(self, runner_handler: RunnerHandler, mock_pull_request: Mock) -> None:
        """Test run_check when check is already in progress."""
        runner_handler.check_run_handler.is_check_run_in_progress = AsyncMock(return_value=True)

        check_config = CheckConfig(
            name="rerun-check",
            command="echo test",
            title="Rerun Check",
        )

        mock_checkout_cm = AsyncMock()
        mock_checkout_cm.__aenter__ = AsyncMock(return_value=(True, "/tmp/worktree", "", ""))
        mock_checkout_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(runner_handler, "_checkout_worktree", return_value=mock_checkout_cm),
            patch(
                "webhook_server.libs.handlers.runner_handler.run_command",
                new=AsyncMock(return_value=(True, "success", "")),
            ),
        ):
            await runner_handler.run_check(pull_request=mock_pull_request, check_config=check_config)

            # Should still run the check even if already in progress (log and re-run)
            runner_handler.check_run_handler.set_check_in_progress.assert_called_once()
            runner_handler.check_run_handler.set_check_success.assert_called_once()
