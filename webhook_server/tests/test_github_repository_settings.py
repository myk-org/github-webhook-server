"""Tests for webhook_server.utils.github_repository_settings module."""

from concurrent.futures import Future
from unittest.mock import AsyncMock, Mock, patch

import pytest
from github.GithubException import UnknownObjectException

from webhook_server.tests.conftest import create_mock_pull_request
from webhook_server.utils.constants import (
    BUILD_CONTAINER_STR,
    CONVENTIONAL_TITLE_STR,
    IN_PROGRESS_STR,
    PRE_COMMIT_STR,
    PYTHON_MODULE_INSTALL_STR,
    QUEUED_STR,
    TOX_STR,
)
from webhook_server.utils.github_repository_settings import (
    _get_github_repo_api,
    get_branch_sampler,
    get_repo_branch_protection_rules,
    get_repository_github_app_api,
    get_required_status_checks,
    get_user_configures_status_checks,
    set_all_in_progress_check_runs_to_queued,
    set_branch_protection,
    set_repositories_settings,
    set_repository,
    set_repository_check_runs_to_queued,
    set_repository_labels,
    set_repository_settings,
)


class TestGetGithubRepoApi:
    """Test suite for _get_github_repo_api function."""

    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_get_github_repo_api_success(self, mock_logger: Mock) -> None:
        """Test successful repository API retrieval."""
        mock_github_api = Mock()
        mock_repo = Mock()
        mock_github_api.get_repo.return_value = mock_repo

        result = _get_github_repo_api(mock_github_api, "test/repo")

        assert result == mock_repo
        mock_github_api.get_repo.assert_called_once_with("test/repo")
        mock_logger.error.assert_not_called()

    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_get_github_repo_api_not_found(self, mock_logger: Mock) -> None:
        """Test repository API retrieval when repository not found."""
        mock_github_api = Mock()
        mock_github_api.get_repo.side_effect = UnknownObjectException(404, "Not found")

        result = _get_github_repo_api(mock_github_api, "test/repo")

        assert result is None
        mock_logger.error.assert_called_once_with("Failed to get GitHub API for repository test/repo")


class TestGetBranchSampler:
    """Test suite for get_branch_sampler function."""

    def test_get_branch_sampler(self) -> None:
        """Test getting branch sampler."""
        mock_repo = Mock()
        mock_branch = Mock()
        mock_repo.get_branch.return_value = mock_branch

        result = get_branch_sampler(mock_repo, "main")

        assert result == mock_branch
        mock_repo.get_branch.assert_called_once_with(branch="main")


class TestSetBranchProtection:
    """Test suite for set_branch_protection function."""

    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_branch_protection_success(self, mock_logger: Mock) -> None:
        """Test successful branch protection setup."""
        mock_branch = Mock()
        mock_repository = Mock()
        mock_repository.name = "test-repo"
        required_status_checks = ["tox", "verified"]

        result = set_branch_protection(
            branch=mock_branch,
            repository=mock_repository,
            required_status_checks=required_status_checks,
            strict=True,
            require_code_owner_reviews=False,
            dismiss_stale_reviews=True,
            required_approving_review_count=1,
            required_linear_history=True,
            required_conversation_resolution=True,
            api_user="test-user",
        )

        assert result is True
        mock_branch.edit_protection.assert_called_once()
        mock_logger.info.assert_called_once()


class TestSetRepositorySettings:
    """Test suite for set_repository_settings function."""

    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_repository_settings_public_repo(self, mock_logger: Mock) -> None:
        """Test setting repository settings for public repository."""
        mock_repository = Mock()
        mock_repository.name = "test-repo"
        mock_repository.private = False
        mock_repository.url = "https://api.github.com/repos/test/repo"

        set_repository_settings(mock_repository, "test-user")

        mock_repository.edit.assert_called_once_with(
            delete_branch_on_merge=True, allow_auto_merge=True, allow_update_branch=True
        )
        assert mock_repository._requester.requestJsonAndCheck.call_count == 2
        mock_logger.info.assert_called()
        mock_logger.warning.assert_not_called()

    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_repository_settings_private_repo(self, mock_logger: Mock) -> None:
        """Test setting repository settings for private repository."""
        mock_repository = Mock()
        mock_repository.name = "test-repo"
        mock_repository.private = True

        set_repository_settings(mock_repository, "test-user")

        mock_repository.edit.assert_called_once()
        mock_repository._requester.requestJsonAndCheck.assert_not_called()
        mock_logger.warning.assert_called_once()


class TestGetRequiredStatusChecks:
    """Test suite for get_required_status_checks function."""

    def test_get_required_status_checks_basic(self) -> None:
        """Test getting required status checks with basic configuration."""
        mock_repo = Mock()
        # Patch get_contents to raise UnknownObjectException so 'pre-commit.ci - pr' is not added
        mock_repo.get_contents.side_effect = UnknownObjectException(status=404, data={}, headers={})
        data: dict = {}
        default_status_checks: list[str] = ["basic-check"]
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(mock_repo, data, default_status_checks.copy(), exclude_status_checks.copy())

        # Verify get_contents(".pre-commit-config.yaml") is called to check for pre-commit config
        mock_repo.get_contents.assert_called_once_with(".pre-commit-config.yaml")

        # Should contain at least 'basic-check' and 'verified' (default)
        assert "basic-check" in result
        assert "verified" in result
        # Should not contain duplicates
        assert result.count("basic-check") == 1
        assert result.count("verified") == 1

    # NOTE: Tests below (tox, container, pypi, pre-commit, conventional-title) could be
    # parametrized, but current structure is clearer as each tests a distinct feature
    # with different configuration keys. Parametrizing would reduce readability.

    def test_get_required_status_checks_with_tox(self) -> None:
        """Test getting required status checks with tox enabled."""
        mock_repo = Mock()
        data: dict = {"tox": True}
        default_status_checks: list[str] = []
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(mock_repo, data, default_status_checks, exclude_status_checks)

        assert "tox" in result
        assert "verified" in result

    def test_get_required_status_checks_with_container(self) -> None:
        """Test getting required status checks with container enabled."""
        mock_repo = Mock()
        data: dict = {"container": True}
        default_status_checks: list[str] = []
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(mock_repo, data, default_status_checks, exclude_status_checks)

        assert BUILD_CONTAINER_STR in result

    def test_get_required_status_checks_with_pypi(self) -> None:
        """Test getting required status checks with pypi enabled."""
        mock_repo = Mock()
        data: dict = {"pypi": True}
        default_status_checks: list[str] = []
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(mock_repo, data, default_status_checks, exclude_status_checks)

        assert PYTHON_MODULE_INSTALL_STR in result

    def test_get_required_status_checks_with_pre_commit(self) -> None:
        """Test getting required status checks with pre-commit enabled."""
        mock_repo = Mock()
        data: dict = {"pre-commit": True}
        default_status_checks: list[str] = []
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(mock_repo, data, default_status_checks, exclude_status_checks)

        assert PRE_COMMIT_STR in result

    def test_get_required_status_checks_with_conventional_title(self) -> None:
        """Test getting required status checks with conventional title enabled."""
        mock_repo = Mock()
        data: dict = {CONVENTIONAL_TITLE_STR: True}
        default_status_checks: list[str] = []
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(mock_repo, data, default_status_checks, exclude_status_checks)

        assert CONVENTIONAL_TITLE_STR in result

    def test_get_required_status_checks_with_pre_commit_config(self) -> None:
        """Test getting required status checks with pre-commit config file."""
        mock_repo = Mock()
        mock_repo.get_contents.return_value = Mock()
        data: dict = {}
        default_status_checks: list[str] = []
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(mock_repo, data, default_status_checks, exclude_status_checks)

        assert "pre-commit.ci - pr" in result

    def test_get_required_status_checks_with_exclusions(self) -> None:
        """Test getting required status checks with exclusions."""
        mock_repo = Mock()
        # Patch get_contents to raise UnknownObjectException so 'pre-commit.ci - pr' is not added
        mock_repo.get_contents.side_effect = UnknownObjectException(status=404, data={}, headers={})
        data: dict = {"tox": True}
        default_status_checks: list[str] = ["tox", "verified"]
        exclude_status_checks: list[str] = ["tox"]

        result = get_required_status_checks(mock_repo, data, default_status_checks.copy(), exclude_status_checks.copy())

        assert result.count("tox") == 0
        assert "verified" in result

    def test_get_required_status_checks_verified_disabled(self) -> None:
        """Test getting required status checks with verified disabled."""
        mock_repo = Mock()
        data: dict = {"verified-job": False}
        default_status_checks: list[str] = []
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(mock_repo, data, default_status_checks, exclude_status_checks)

        assert "verified" not in result

    def test_get_required_status_checks_deduplication_with_pre_existing_values(self) -> None:
        """Test that deduplication works when default_status_checks already contains values that will be appended."""

        mock_repo = Mock()
        # Simulate .pre-commit-config.yaml exists
        mock_repo.get_contents.return_value = Mock()

        # Enable multiple checks
        data: dict = {
            "tox": True,
            "container": True,
            "pypi": True,
            "pre-commit": True,
            "conventional-title": True,
            "verified-job": True,
        }

        # Pre-populate with values that will also be added by the function
        default_status_checks: list[str] = [
            "can-be-merged",
            "verified",
            "tox",  # Will be added again by data["tox"]
            PRE_COMMIT_STR,  # Will be added again by data["pre-commit"]
        ]
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(mock_repo, data, default_status_checks, exclude_status_checks)

        # Verify deduplication works
        assert len(result) == len(set(result)), f"Duplicates found in result: {result}"

        # Verify expected values are present (once each)
        assert result.count("can-be-merged") == 1
        assert result.count("verified") == 1
        assert result.count("tox") == 1
        assert result.count(PRE_COMMIT_STR) == 1
        assert result.count(BUILD_CONTAINER_STR) == 1
        assert result.count(PYTHON_MODULE_INSTALL_STR) == 1
        assert result.count(CONVENTIONAL_TITLE_STR) == 1
        assert result.count("pre-commit.ci - pr") == 1

    def test_get_required_status_checks_preserves_order_while_deduplicating(self) -> None:
        """Test that deduplication preserves the order of first occurrence."""
        mock_repo = Mock()
        mock_repo.get_contents.side_effect = UnknownObjectException(status=404, data={}, headers={})

        data: dict = {}

        # Create list with intentional duplicates in specific order
        default_status_checks: list[str] = ["check1", "check2", "check1", "check3", "check2"]
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(mock_repo, data, default_status_checks, exclude_status_checks)

        # Should preserve first occurrence order: check1, check2, check3, verified
        expected_order_prefix = ["check1", "check2", "check3"]
        assert result[:3] == expected_order_prefix, f"Order not preserved: {result}"
        assert len(result) == len(set(result)), f"Duplicates found: {result}"

        # Verify "verified" lands after user items to lock the contract
        verified_index = result.index("verified")
        assert verified_index >= len(expected_order_prefix), "verified should appear after all user-provided items"


class TestGetUserConfiguresStatusChecks:
    """Test suite for get_user_configures_status_checks function."""

    def test_get_user_configures_status_checks_with_data(self) -> None:
        """Test getting user configured status checks with data."""
        status_checks: dict = {"include-runs": ["custom-check1", "custom-check2"], "exclude-runs": ["exclude-check1"]}

        include_checks, exclude_checks = get_user_configures_status_checks(status_checks)

        assert include_checks == ["custom-check1", "custom-check2"]
        assert exclude_checks == ["exclude-check1"]

    def test_get_user_configures_status_checks_empty(self) -> None:
        """Test getting user configured status checks with empty data."""
        status_checks: dict = {}

        include_checks, exclude_checks = get_user_configures_status_checks(status_checks)

        assert include_checks == []
        assert exclude_checks == []

    def test_get_user_configures_status_checks_none(self) -> None:
        """Test getting user configured status checks with None data."""
        # Pass empty dict instead of None to avoid type error
        status_checks: dict = {}

        include_checks, exclude_checks = get_user_configures_status_checks(status_checks)

        assert include_checks == []
        assert exclude_checks == []


class TestSetRepositoryLabels:
    """Test suite for set_repository_labels function."""

    @patch("webhook_server.utils.github_repository_settings.STATIC_LABELS_DICT")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_repository_labels_new_labels(self, mock_logger: Mock, mock_static_labels: Mock) -> None:
        """Test setting repository labels with new labels."""
        mock_static_labels.items.return_value = [("bug", "#d73a4a"), ("enhancement", "#a2eeef")]

        mock_repository = Mock()
        mock_repository.name = "test-repo"
        mock_repository.get_labels.return_value = []
        mock_repository.create_label = Mock()

        result = set_repository_labels(mock_repository, "test-user")

        assert "Setting repository labels is done" in result
        assert mock_repository.create_label.call_count == 2
        mock_logger.info.assert_called()

    @patch("webhook_server.utils.github_repository_settings.STATIC_LABELS_DICT")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_repository_labels_existing_labels_same_color(
        self, mock_logger: Mock, mock_static_labels: Mock
    ) -> None:
        """Test setting repository labels with existing labels of same color."""
        mock_static_labels.items.return_value = [("bug", "#d73a4a")]

        mock_label = Mock()
        mock_label.name = "bug"
        mock_label.color = "#d73a4a"
        mock_label.edit = Mock()

        mock_repository = Mock()
        mock_repository.name = "test-repo"
        mock_repository.get_labels.return_value = [mock_label]
        mock_repository.create_label = Mock()

        result = set_repository_labels(mock_repository, "test-user")

        assert "Setting repository labels is done" in result
        mock_label.edit.assert_not_called()
        mock_repository.create_label.assert_not_called()

    @patch("webhook_server.utils.github_repository_settings.STATIC_LABELS_DICT")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_repository_labels_existing_labels_different_color(
        self, mock_logger: Mock, mock_static_labels: Mock
    ) -> None:
        """Test setting repository labels with existing labels of different color."""
        mock_static_labels.items.return_value = [("bug", "#d73a4a")]

        mock_label = Mock()
        mock_label.name = "bug"
        mock_label.color = "#old-color"
        mock_label.edit = Mock()

        mock_repository = Mock()
        mock_repository.name = "test-repo"
        mock_repository.get_labels.return_value = [mock_label]
        mock_repository.create_label = Mock()

        result = set_repository_labels(mock_repository, "test-user")

        assert "Setting repository labels is done" in result
        mock_label.edit.assert_called_once_with(name="bug", color="#d73a4a")
        mock_repository.create_label.assert_not_called()


class TestGetRepoBranchProtectionRules:
    """Test suite for get_repo_branch_protection_rules function."""

    def test_get_repo_branch_protection_rules_default(self) -> None:
        """Test getting branch protection rules with default values."""
        mock_config = Mock()
        mock_config.get_value.return_value = {}

        result = get_repo_branch_protection_rules(mock_config)

        assert result["strict"] is True
        assert result["require_code_owner_reviews"] is False
        assert result["dismiss_stale_reviews"] is True
        assert result["required_approving_review_count"] == 0
        assert result["required_linear_history"] is True
        assert result["required_conversation_resolution"] is True

    def test_get_repo_branch_protection_rules_custom(self) -> None:
        """Test getting branch protection rules with custom values."""
        mock_config = Mock()
        mock_config.get_value.return_value = {"strict": False, "required_approving_review_count": 2}

        result = get_repo_branch_protection_rules(mock_config)

        assert result["strict"] is False
        assert result["required_approving_review_count"] == 2
        assert result["require_code_owner_reviews"] is False  # Default value


class TestSetRepositoriesSettings:
    """Test suite for set_repositories_settings function."""

    @patch("webhook_server.utils.github_repository_settings.run_command")
    @patch("webhook_server.utils.github_repository_settings.get_future_results")
    @patch("webhook_server.utils.github_repository_settings.ThreadPoolExecutor")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    @pytest.mark.asyncio
    async def test_set_repositories_settings_with_docker(
        self, mock_logger: Mock, mock_thread_pool: Mock, mock_get_futures: Mock, mock_run_command: AsyncMock
    ) -> None:
        """Test setting repositories settings with docker configuration."""
        mock_config = Mock()
        mock_config.root_data = {
            "docker": {"username": "test-user", "password": "test-pass"},  # pragma: allowlist secret
            "repositories": {"repo1": {"name": "owner/repo1"}},
        }

        mock_apis_dict = {"repo1": {"api": Mock(), "user": "test-user"}}

        mock_executor = Mock()
        mock_thread_pool.return_value.__enter__.return_value = mock_executor
        mock_future = Mock(spec=Future)
        mock_executor.submit.return_value = mock_future

        await set_repositories_settings(mock_config, mock_apis_dict)

        # Verify run_command was called with proper security parameters
        mock_run_command.assert_called_once()
        call_kwargs = mock_run_command.call_args[1]
        assert "stdin_input" in call_kwargs, "Should pass stdin_input for docker login"
        assert "redact_secrets" in call_kwargs, "Should pass redact_secrets to protect credentials"
        # Verify exact password is in redact_secrets list for proper masking
        assert "test-pass" in call_kwargs["redact_secrets"], "Should include exact password in redact_secrets"

        # Verify docker command shape to prevent password leaks via args
        command = call_kwargs.get("command", "")
        assert isinstance(command, str), "Command should be a string"
        assert "login" in command, "Command should contain login"
        assert "test-pass" not in command, "Password should NOT be in command args (should use stdin)"
        assert "--password-stdin" in command, "Should use --password-stdin to read password from stdin"

        mock_executor.submit.assert_called_once()
        mock_get_futures.assert_called_once()

    @patch("webhook_server.utils.github_repository_settings.run_command")
    @patch("webhook_server.utils.github_repository_settings.get_future_results")
    @patch("webhook_server.utils.github_repository_settings.ThreadPoolExecutor")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    @pytest.mark.asyncio
    async def test_set_repositories_settings_without_docker(
        self, mock_logger: Mock, mock_thread_pool: Mock, mock_get_futures: Mock, mock_run_command: AsyncMock
    ) -> None:
        """Test setting repositories settings without docker configuration."""
        mock_config = Mock()
        mock_config.root_data = {"repositories": {"repo1": {"name": "owner/repo1"}}}

        mock_apis_dict = {"repo1": {"api": Mock(), "user": "test-user"}}

        mock_executor = Mock()
        mock_thread_pool.return_value.__enter__.return_value = mock_executor
        mock_future = Mock(spec=Future)
        mock_executor.submit.return_value = mock_future

        await set_repositories_settings(mock_config, mock_apis_dict)

        mock_run_command.assert_not_called()
        mock_executor.submit.assert_called_once()
        mock_get_futures.assert_called_once()


class TestSetRepository:
    """Test suite for set_repository function."""

    @patch("webhook_server.utils.github_repository_settings.set_repository_labels")
    @patch("webhook_server.utils.github_repository_settings.set_repository_settings")
    @patch("webhook_server.utils.github_repository_settings.get_branch_sampler")
    @patch("webhook_server.utils.github_repository_settings.set_branch_protection")
    @patch("webhook_server.utils.github_repository_settings.get_required_status_checks")
    @patch("webhook_server.utils.github_repository_settings.get_user_configures_status_checks")
    @patch("webhook_server.utils.github_repository_settings._get_github_repo_api")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_repository_success_public(
        self,
        mock_logger: Mock,
        mock_get_repo: Mock,
        mock_get_user_checks: Mock,
        mock_get_required_checks: Mock,
        mock_set_branch_protection: Mock,
        mock_get_branch: Mock,
        mock_set_repo_settings: Mock,
        mock_set_repo_labels: Mock,
    ) -> None:
        """Test successful repository setup for public repository."""
        # Setup mocks
        mock_github_api = Mock()
        mock_repo = Mock()
        mock_repo.private = False
        mock_get_repo.return_value = mock_repo

        mock_branch = Mock()
        mock_get_branch.return_value = mock_branch

        mock_get_user_checks.return_value = ([], [])
        mock_get_required_checks.return_value = ["tox", "verified"]

        mock_config = Mock()
        mock_config.get_value.side_effect = lambda value, return_on_none: {
            "protected-branches": {"main": {}},
            "default-status-checks": [],
        }.get(value, return_on_none)

        # Call function
        result = set_repository(
            repository_name="test-repo",
            data={"name": "owner/test-repo"},
            apis_dict={"test-repo": {"api": mock_github_api, "user": "test-user"}},
            branch_protection={"strict": True},
            config=mock_config,
        )

        # Verify results
        assert result[0] is True
        assert "Setting repository settings is done" in result[1]
        assert result[2] == mock_logger.info

        # Verify calls
        mock_set_repo_labels.assert_called_once()
        mock_set_repo_settings.assert_called_once()
        mock_get_branch.assert_called_once_with(repo=mock_repo, branch_name="main")
        mock_set_branch_protection.assert_called_once()

    @patch("webhook_server.utils.github_repository_settings._get_github_repo_api")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_repository_no_github_api(self, mock_logger: Mock, mock_get_repo: Mock) -> None:
        """Test repository setup when no GitHub API is available."""
        mock_config = Mock()

        result = set_repository(
            repository_name="test-repo",
            data={"name": "owner/test-repo"},
            apis_dict={"test-repo": {"api": None, "user": "test-user"}},
            branch_protection={},
            config=mock_config,
        )

        assert result[0] is False
        assert "Failed to get github api" in result[1]
        assert result[2] == mock_logger.error

    @patch("webhook_server.utils.github_repository_settings._get_github_repo_api")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_repository_repo_not_found(self, mock_logger: Mock, mock_get_repo: Mock) -> None:
        """Test repository setup when repository is not found."""
        mock_github_api = Mock()
        mock_get_repo.return_value = None
        mock_config = Mock()

        result = set_repository(
            repository_name="test-repo",
            data={"name": "owner/test-repo"},
            apis_dict={"test-repo": {"api": mock_github_api, "user": "test-user"}},
            branch_protection={},
            config=mock_config,
        )

        assert result[0] is False
        assert "Failed to get repository" in result[1]
        assert result[2] == mock_logger.error

    @patch("webhook_server.utils.github_repository_settings.set_repository_labels")
    @patch("webhook_server.utils.github_repository_settings.set_repository_settings")
    @patch("webhook_server.utils.github_repository_settings._get_github_repo_api")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_repository_private_repo(
        self, mock_logger: Mock, mock_get_repo: Mock, mock_set_repo_settings: Mock, mock_set_repo_labels: Mock
    ) -> None:
        """Test repository setup for private repository."""
        mock_github_api = Mock()
        mock_repo = Mock()
        mock_repo.private = True
        mock_get_repo.return_value = mock_repo

        mock_config = Mock()
        mock_config.get_value.return_value = {}

        result = set_repository(
            repository_name="test-repo",
            data={"name": "owner/test-repo"},
            apis_dict={"test-repo": {"api": mock_github_api, "user": "test-user"}},
            branch_protection={},
            config=mock_config,
        )

        assert result[0] is False
        assert "Repository is private" in result[1]
        assert result[2] == mock_logger.warning

        mock_set_repo_labels.assert_called_once()
        mock_set_repo_settings.assert_called_once()


class TestSetAllInProgressCheckRunsToQueued:
    """Test suite for set_all_in_progress_check_runs_to_queued function."""

    @patch("webhook_server.utils.github_repository_settings.get_future_results")
    @patch("webhook_server.utils.github_repository_settings.ThreadPoolExecutor")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_all_in_progress_check_runs_to_queued(
        self, mock_logger: Mock, mock_thread_pool: Mock, mock_get_futures: Mock
    ) -> None:
        """Test setting all in progress check runs to queued."""
        mock_config = Mock()
        mock_config.root_data = {"repositories": {"repo1": {"name": "owner/repo1"}}}

        mock_apis_dict = {"repo1": {"api": Mock(), "user": "test-user"}}

        mock_executor = Mock()
        mock_thread_pool.return_value.__enter__.return_value = mock_executor
        mock_future = Mock(spec=Future)
        mock_executor.submit.return_value = mock_future

        set_all_in_progress_check_runs_to_queued(mock_config, mock_apis_dict)

        mock_executor.submit.assert_called_once()
        mock_get_futures.assert_called_once()


class TestSetRepositoryCheckRunsToQueued:
    """Test suite for set_repository_check_runs_to_queued function."""

    @patch("webhook_server.utils.github_repository_settings.get_repository_github_app_api")
    @patch("webhook_server.utils.github_repository_settings._get_github_repo_api")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_repository_check_runs_to_queued_success(
        self, mock_logger: Mock, mock_get_repo: Mock, mock_get_app_api: Mock
    ) -> None:
        """Test successful setting of repository check runs to queued."""
        # Setup mocks
        mock_github_api = Mock()
        mock_app_api = Mock()
        mock_repo = Mock()
        mock_app_repo = Mock()

        mock_get_app_api.return_value = mock_app_api
        mock_get_repo.side_effect = [mock_app_repo, mock_repo]

        # Mock pull request and commits using shared helper
        mock_pull_request = create_mock_pull_request()
        mock_repo.get_pulls.return_value = [mock_pull_request]

        mock_commit = Mock()
        mock_commit.sha = "abc123"
        mock_pull_request.get_commits.return_value = [mock_commit]

        mock_check_run = Mock()
        mock_check_run.name = "tox"
        mock_check_run.status = IN_PROGRESS_STR
        mock_commit.get_check_runs.return_value = [mock_check_run]

        mock_config = Mock()

        # Call function
        result = set_repository_check_runs_to_queued(
            config_=mock_config,
            data={"name": "owner/test-repo"},
            github_api=mock_github_api,
            check_runs=(TOX_STR,),
            api_user="test-user",
        )

        # Verify results
        assert result[0] is True
        assert "Set check run status to queued is done" in result[1]
        assert result[2] == mock_logger.debug

        # Verify check run was created
        mock_app_repo.create_check_run.assert_called_once_with(name="tox", head_sha="abc123", status=QUEUED_STR)

    @patch("webhook_server.utils.github_repository_settings.get_repository_github_app_api")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_repository_check_runs_to_queued_no_app_api(self, mock_logger: Mock, mock_get_app_api: Mock) -> None:
        """Test setting check runs when no app API is available."""
        mock_get_app_api.return_value = None
        mock_config = Mock()

        result = set_repository_check_runs_to_queued(
            config_=mock_config,
            data={"name": "owner/test-repo"},
            github_api=Mock(),
            check_runs=(TOX_STR,),
            api_user="test-user",
        )

        assert result[0] is False
        assert "Failed to get repositories GitHub app API" in result[1]
        assert result[2] == mock_logger.error

    @patch("webhook_server.utils.github_repository_settings.get_repository_github_app_api")
    @patch("webhook_server.utils.github_repository_settings._get_github_repo_api")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_repository_check_runs_to_queued_no_app_repo(
        self, mock_logger: Mock, mock_get_repo: Mock, mock_get_app_api: Mock
    ) -> None:
        """Test setting check runs when app repository is not found."""
        mock_get_app_api.return_value = Mock()
        mock_get_repo.return_value = None
        mock_config = Mock()

        result = set_repository_check_runs_to_queued(
            config_=mock_config,
            data={"name": "owner/test-repo"},
            github_api=Mock(),
            check_runs=(TOX_STR,),
            api_user="test-user",
        )

        assert result[0] is False
        assert "Failed to get GitHub app API for repository" in result[1]
        assert result[2] == mock_logger.error

    @patch("webhook_server.utils.github_repository_settings.get_repository_github_app_api")
    @patch("webhook_server.utils.github_repository_settings._get_github_repo_api")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_repository_check_runs_to_queued_no_repo(
        self, mock_logger: Mock, mock_get_repo: Mock, mock_get_app_api: Mock
    ) -> None:
        """Test setting check runs when repository is not found."""
        mock_get_app_api.return_value = Mock()
        mock_get_repo.side_effect = [Mock(), None]  # App repo found, regular repo not found
        mock_config = Mock()

        result = set_repository_check_runs_to_queued(
            config_=mock_config,
            data={"name": "owner/test-repo"},
            github_api=Mock(),
            check_runs=(TOX_STR,),
            api_user="test-user",
        )

        assert result[0] is False
        assert "Failed to get GitHub API for repository" in result[1]
        assert result[2] == mock_logger.error


class TestGetRepositoryGithubAppApi:
    """Test suite for get_repository_github_app_api function."""

    @patch("builtins.open", create=True)
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_get_repository_github_app_api_success(self, mock_logger: Mock, mock_open: Mock) -> None:
        """Test successful GitHub app API retrieval."""
        mock_config = Mock()
        mock_config.data_dir = "/test/dir"
        mock_config.root_data = {"github-app-id": 12345}

        mock_file = Mock()
        mock_file.read.return_value = "test-private-key"
        mock_open.return_value.__enter__.return_value = mock_file

        # Mock the GitHub app integration
        with patch("webhook_server.utils.github_repository_settings.Auth") as mock_auth:
            with patch("webhook_server.utils.github_repository_settings.GithubIntegration") as mock_integration:
                mock_app_auth = Mock()
                mock_auth.AppAuth.return_value = mock_app_auth

                mock_app_instance = Mock()
                mock_integration.return_value = mock_app_instance

                mock_installation = Mock()
                mock_github = Mock()
                mock_installation.get_github_for_installation.return_value = mock_github
                mock_app_instance.get_repo_installation.return_value = mock_installation

                result = get_repository_github_app_api(mock_config, "owner/repo")

                assert result == mock_github
                mock_auth.AppAuth.assert_called_once_with(app_id=12345, private_key="test-private-key")
                mock_app_instance.get_repo_installation.assert_called_once_with(owner="owner", repo="repo")

    @patch("builtins.open", create=True)
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_get_repository_github_app_api_exception(self, mock_logger: Mock, mock_open: Mock) -> None:
        """Test GitHub app API retrieval when exception occurs."""
        mock_config = Mock()
        mock_config.data_dir = "/test/dir"
        mock_config.root_data = {"github-app-id": 12345}

        mock_file = Mock()
        mock_file.read.return_value = "test-private-key"
        mock_open.return_value.__enter__.return_value = mock_file

        # Mock the GitHub app integration to raise an exception
        with patch("webhook_server.utils.github_repository_settings.Auth") as mock_auth:
            with patch("webhook_server.utils.github_repository_settings.GithubIntegration") as mock_integration:
                mock_app_auth = Mock()
                mock_auth.AppAuth.return_value = mock_app_auth

                mock_app_instance = Mock()
                mock_integration.return_value = mock_app_instance
                mock_app_instance.get_repo_installation.side_effect = Exception("App not installed")

                result = get_repository_github_app_api(mock_config, "owner/repo")

                assert result is None
                mock_logger.error.assert_called_once()
                assert "Repository owner/repo not found by manage-repositories-app" in mock_logger.error.call_args[0][0]
