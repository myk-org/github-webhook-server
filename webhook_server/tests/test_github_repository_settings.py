"""Tests for webhook_server.utils.github_repository_settings module."""

from concurrent.futures import Future
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
import yaml
from github.GithubException import GithubException, UnknownObjectException

from webhook_server.utils.constants import (
    BUILD_CONTAINER_STR,
    CONVENTIONAL_TITLE_STR,
    IN_PROGRESS_STR,
    PRE_COMMIT_STR,
    PYTHON_MODULE_INSTALL_STR,
    QUEUED_STR,
    SECURITY_COMMITTER_IDENTITY_STR,
    SECURITY_SUSPICIOUS_PATHS_STR,
    TOX_STR,
)
from webhook_server.utils.github_repository_settings import (
    _get_github_repo_api,
    get_branch_sampler,
    get_repo_branch_protection_rules,
    get_repository_github_app_api,
    get_repository_github_app_token,
    get_required_status_checks,
    get_security_status_checks,
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


def _config(security_checks: dict[str, Any] | None = None) -> Mock:
    """Return a Config mock whose security-checks value is `security_checks`."""
    config = Mock()
    config.get_value.side_effect = lambda value, return_on_none, extra_dict=None: (
        security_checks if value == "security-checks" else return_on_none
    )
    config.repository_local_data.return_value = {}
    return config


class _FakeConfig:
    """Minimal Config stand-in resolving values like the real Config.get_value."""

    def __init__(self, repository_data: dict[str, Any], local_data: dict[str, Any]) -> None:
        self.repository_data: dict[str, Any] = repository_data
        self.root_data: dict[str, Any] = {}
        self._local_data: dict[str, Any] = local_data
        self.local_data_calls: list[str] = []

    def repository_local_data(self, github_api: Any, repository_full_name: str) -> dict[str, Any]:
        self.local_data_calls.append(repository_full_name)
        return self._local_data

    def get_value(self, value: str, return_on_none: Any = None, extra_dict: dict[str, Any] | None = None) -> Any:
        for scope in (extra_dict, self.repository_data, self.root_data):
            if scope and value in scope:
                return scope[value]

        return return_on_none


class TestGetRequiredStatusChecks:
    """Test suite for get_required_status_checks function."""

    def test_get_required_status_checks_basic(self) -> None:
        """Test getting required status checks with basic configuration."""
        mock_repo = Mock()
        # Patch get_contents to raise exception so 'pre-commit.ci - pr' is not added
        mock_repo.get_contents.side_effect = UnknownObjectException(status=404, data={}, headers={})
        data: dict = {}
        default_status_checks: list[str] = ["basic-check"]
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(
            mock_repo, data, default_status_checks, exclude_status_checks, config=_config()
        )

        # Should contain at least 'basic-check' and 'verified' (default)
        assert "basic-check" in result
        assert "verified" in result
        # Should not contain duplicates
        assert result.count("basic-check") == 1
        assert result.count("verified") == 1

    def test_get_required_status_checks_with_tox(self) -> None:
        """Test getting required status checks with tox enabled."""
        mock_repo = Mock()
        data: dict = {"tox": True}
        default_status_checks: list[str] = []
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(
            mock_repo, data, default_status_checks, exclude_status_checks, config=_config()
        )

        assert "tox" in result
        assert "verified" in result

    def test_get_required_status_checks_with_container(self) -> None:
        """Test getting required status checks with container enabled."""
        mock_repo = Mock()
        data: dict = {"container": True}
        default_status_checks: list[str] = []
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(
            mock_repo, data, default_status_checks, exclude_status_checks, config=_config()
        )

        assert BUILD_CONTAINER_STR in result

    def test_get_required_status_checks_with_pypi(self) -> None:
        """Test getting required status checks with pypi enabled."""
        mock_repo = Mock()
        data: dict = {"pypi": True}
        default_status_checks: list[str] = []
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(
            mock_repo, data, default_status_checks, exclude_status_checks, config=_config()
        )

        assert PYTHON_MODULE_INSTALL_STR in result

    def test_get_required_status_checks_with_pre_commit(self) -> None:
        """Test getting required status checks with pre-commit enabled."""
        mock_repo = Mock()
        data: dict = {"pre-commit": True}
        default_status_checks: list[str] = []
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(
            mock_repo, data, default_status_checks, exclude_status_checks, config=_config()
        )

        assert PRE_COMMIT_STR in result

    def test_get_required_status_checks_with_conventional_title(self) -> None:
        """Test getting required status checks with conventional title enabled."""
        mock_repo = Mock()
        data: dict = {CONVENTIONAL_TITLE_STR: True}
        default_status_checks: list[str] = []
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(
            mock_repo, data, default_status_checks, exclude_status_checks, config=_config()
        )

        assert CONVENTIONAL_TITLE_STR in result

    def test_get_required_status_checks_with_pre_commit_config(self) -> None:
        """Test getting required status checks with pre-commit config file."""
        mock_repo = Mock()
        mock_repo.get_contents.return_value = Mock()
        data: dict = {}
        default_status_checks: list[str] = []
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(
            mock_repo, data, default_status_checks, exclude_status_checks, config=_config()
        )

        assert "pre-commit.ci - pr" in result

    def test_get_required_status_checks_with_exclusions(self) -> None:
        """Test getting required status checks with exclusions."""
        mock_repo = Mock()
        # Patch get_contents to raise exception so 'pre-commit.ci - pr' is not added
        mock_repo.get_contents.side_effect = UnknownObjectException(status=404, data={}, headers={})
        data: dict = {"tox": True}
        default_status_checks: list[str] = ["tox", "verified"]
        exclude_status_checks: list[str] = ["tox"]

        result = get_required_status_checks(
            mock_repo, data, default_status_checks, exclude_status_checks, config=_config()
        )

        assert result.count("tox") == 0
        assert "verified" in result

    def test_get_required_status_checks_verified_disabled(self) -> None:
        """Test getting required status checks with verified disabled."""
        mock_repo = Mock()
        data: dict = {"verified-job": False}
        default_status_checks: list[str] = []
        exclude_status_checks: list[str] = []

        result = get_required_status_checks(
            mock_repo, data, default_status_checks, exclude_status_checks, config=_config()
        )

        assert "verified" not in result

    def test_get_required_status_checks_security_checks_default(self) -> None:
        """Security checks are required by default when not configured."""
        mock_repo = Mock()
        mock_repo.get_contents.side_effect = UnknownObjectException(status=404, data={}, headers={})

        result = get_required_status_checks(mock_repo, {}, [], [], config=_config())

        assert SECURITY_SUSPICIOUS_PATHS_STR in result
        assert SECURITY_COMMITTER_IDENTITY_STR in result

    def test_get_required_status_checks_security_checks_mandatory(self) -> None:
        """Security checks are required when security-checks.mandatory is true."""
        mock_repo = Mock()
        mock_repo.get_contents.side_effect = UnknownObjectException(status=404, data={}, headers={})

        result = get_required_status_checks(mock_repo, {}, [], [], config=_config({"mandatory": True}))

        assert SECURITY_SUSPICIOUS_PATHS_STR in result
        assert SECURITY_COMMITTER_IDENTITY_STR in result

    def test_get_required_status_checks_security_checks_not_mandatory(self) -> None:
        """No security checks are required when security-checks.mandatory is false."""
        mock_repo = Mock()
        mock_repo.get_contents.side_effect = UnknownObjectException(status=404, data={}, headers={})

        result = get_required_status_checks(mock_repo, {}, [], [], config=_config({"mandatory": False}))

        assert SECURITY_SUSPICIOUS_PATHS_STR not in result
        assert SECURITY_COMMITTER_IDENTITY_STR not in result

    def test_get_required_status_checks_security_checks_empty_suspicious_paths(self) -> None:
        """No suspicious-paths check when suspicious-paths is empty, committer check still added."""
        mock_repo = Mock()
        mock_repo.get_contents.side_effect = UnknownObjectException(status=404, data={}, headers={})

        result = get_required_status_checks(mock_repo, {}, [], [], config=_config({"suspicious-paths": []}))

        assert SECURITY_SUSPICIOUS_PATHS_STR not in result
        assert SECURITY_COMMITTER_IDENTITY_STR in result

    def test_get_required_status_checks_security_checks_committer_disabled(self) -> None:
        """No committer-identity check when committer-identity-check is false."""
        mock_repo = Mock()
        mock_repo.get_contents.side_effect = UnknownObjectException(status=404, data={}, headers={})

        result = get_required_status_checks(mock_repo, {}, [], [], config=_config({"committer-identity-check": False}))

        assert SECURITY_SUSPICIOUS_PATHS_STR in result
        assert SECURITY_COMMITTER_IDENTITY_STR not in result

    def test_get_required_status_checks_security_checks_excluded(self) -> None:
        """Security checks can be removed by exclude-runs."""
        mock_repo = Mock()
        mock_repo.get_contents.side_effect = UnknownObjectException(status=404, data={}, headers={})

        result = get_required_status_checks(
            mock_repo,
            {},
            [],
            [SECURITY_SUSPICIOUS_PATHS_STR, SECURITY_COMMITTER_IDENTITY_STR],
            config=_config(),
        )

        assert SECURITY_SUSPICIOUS_PATHS_STR not in result
        assert SECURITY_COMMITTER_IDENTITY_STR not in result

    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_get_required_status_checks_security_checks_malformed(self, mock_logger: Mock) -> None:
        """Malformed security-checks values log a warning and fall back to defaults."""
        mock_repo = Mock()
        mock_repo.get_contents.side_effect = UnknownObjectException(status=404, data={}, headers={})

        result = get_required_status_checks(
            mock_repo, {}, [], [], config=_config({"mandatory": "yes", "committer-identity-check": "no"})
        )

        assert SECURITY_SUSPICIOUS_PATHS_STR in result
        assert SECURITY_COMMITTER_IDENTITY_STR in result
        assert mock_logger.warning.call_count == 2

    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_get_security_status_checks_takes_repository_name_string(self, mock_logger: Mock) -> None:
        """The helper takes a plain repository name, so warnings never touch a PyGithub Repository."""
        # Malformed values on purpose, they are the only way to reach the warning messages
        result = get_security_status_checks(repository_full_name="owner/repo", config=_config({"mandatory": "yes"}))

        assert result == [SECURITY_SUSPICIOUS_PATHS_STR, SECURITY_COMMITTER_IDENTITY_STR]
        warnings = " ".join(str(call) for call in mock_logger.warning.call_args_list)
        assert "owner/repo" in warnings
        assert "owner/repo: security-checks.mandatory must be boolean" in warnings

    def test_get_required_status_checks_security_checks_blank_suspicious_paths(self) -> None:
        """Blank suspicious-paths entries are stripped, so no check is required (the runner never runs it)."""
        mock_repo = Mock()
        mock_repo.get_contents.side_effect = UnknownObjectException(status=404, data={}, headers={})

        result = get_required_status_checks(
            mock_repo, {"name": "owner/repo"}, [], [], config=_config({"suspicious-paths": [""]})
        )

        assert SECURITY_SUSPICIOUS_PATHS_STR not in result
        assert SECURITY_COMMITTER_IDENTITY_STR in result

    @pytest.mark.parametrize(
        ("suspicious_paths", "expected"),
        [(["  ", "x/"], True), (["  ", ""], False)],
        ids=["blank-mixed-with-path", "blank-only"],
    )
    def test_get_required_status_checks_security_checks_strips_blank_paths(
        self, suspicious_paths: list[str], expected: bool
    ) -> None:
        """Only blank entries are dropped, a real path next to a blank one still requires the check."""
        mock_repo = Mock()
        mock_repo.get_contents.side_effect = UnknownObjectException(status=404, data={}, headers={})

        result = get_required_status_checks(
            mock_repo, {"name": "owner/repo"}, [], [], config=_config({"suspicious-paths": suspicious_paths})
        )

        assert (SECURITY_SUSPICIOUS_PATHS_STR in result) is expected


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

        mock_run_command.assert_called_once()
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
        mock_config.get_value.side_effect = lambda value, return_on_none, extra_dict=None: {
            "protected-branches": {"main": {}},
            "default-status-checks": [],
        }.get(value, return_on_none)
        mock_config.repository_local_data.return_value = {}

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

    @pytest.mark.parametrize(
        ("security_checks", "include_runs", "expected"),
        [
            ({}, ["custom-check"], [SECURITY_SUSPICIOUS_PATHS_STR, SECURITY_COMMITTER_IDENTITY_STR]),
            (
                {},
                [SECURITY_COMMITTER_IDENTITY_STR, "custom-check"],
                [SECURITY_COMMITTER_IDENTITY_STR],
            ),
            ({"mandatory": False}, ["custom-check"], []),
        ],
        ids=["security-checks-added", "no-duplicates", "not-mandatory"],
    )
    @patch("webhook_server.utils.github_repository_settings.set_repository_labels")
    @patch("webhook_server.utils.github_repository_settings.set_repository_settings")
    @patch("webhook_server.utils.github_repository_settings.get_branch_sampler")
    @patch("webhook_server.utils.github_repository_settings.set_branch_protection")
    @patch("webhook_server.utils.github_repository_settings.get_required_status_checks")
    @patch("webhook_server.utils.github_repository_settings._get_github_repo_api")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_repository_include_runs_with_security_checks(
        self,
        mock_logger: Mock,
        mock_get_repo: Mock,
        mock_get_required_checks: Mock,
        mock_set_branch_protection: Mock,
        mock_get_branch: Mock,
        mock_set_repo_settings: Mock,
        mock_set_repo_labels: Mock,
        security_checks: dict[str, Any],
        include_runs: list[str],
        expected: list[str],
    ) -> None:
        """include-runs path keeps the security checks governed by security-checks.mandatory, without duplicates."""
        mock_repo = Mock()
        mock_repo.private = False
        mock_get_repo.return_value = mock_repo
        mock_get_branch.return_value = Mock()

        mock_config = Mock()
        mock_config.get_value.side_effect = lambda value, return_on_none, extra_dict=None: {
            "protected-branches": {"main": {"include-runs": include_runs}},
            "default-status-checks": [],
            "security-checks": security_checks,
        }.get(value, return_on_none)
        mock_config.repository_local_data.return_value = {}

        result = set_repository(
            repository_name="test-repo",
            data={"name": "owner/test-repo"},
            apis_dict={"test-repo": {"api": Mock(), "user": "test-user"}},
            branch_protection={"strict": True},
            config=mock_config,
        )

        assert result[0] is True
        mock_get_required_checks.assert_not_called()
        contexts = mock_set_branch_protection.call_args.kwargs["required_status_checks"]
        assert contexts[: len(include_runs)] == include_runs
        assert [check for check in contexts if check in expected] == expected
        assert len(contexts) == len(set(contexts))

    @pytest.mark.parametrize(
        ("include_runs", "exclude_runs", "expected"),
        [
            (["a", "b"], ["b"], ["a", SECURITY_SUSPICIOUS_PATHS_STR, SECURITY_COMMITTER_IDENTITY_STR]),
            (
                [SECURITY_COMMITTER_IDENTITY_STR, "custom-check"],
                [SECURITY_COMMITTER_IDENTITY_STR],
                ["custom-check", SECURITY_SUSPICIOUS_PATHS_STR],
            ),
        ],
        ids=["exclude-removes-include-entry", "exclude-removes-security-check"],
    )
    @patch("webhook_server.utils.github_repository_settings.set_repository_labels")
    @patch("webhook_server.utils.github_repository_settings.set_repository_settings")
    @patch("webhook_server.utils.github_repository_settings.get_branch_sampler")
    @patch("webhook_server.utils.github_repository_settings.set_branch_protection")
    @patch("webhook_server.utils.github_repository_settings.get_required_status_checks")
    @patch("webhook_server.utils.github_repository_settings._get_github_repo_api")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_repository_include_runs_applies_exclude_runs(
        self,
        mock_logger: Mock,
        mock_get_repo: Mock,
        mock_get_required_checks: Mock,
        mock_set_branch_protection: Mock,
        mock_get_branch: Mock,
        mock_set_repo_settings: Mock,
        mock_set_repo_labels: Mock,
        include_runs: list[str],
        exclude_runs: list[str],
        expected: list[str],
    ) -> None:
        """exclude-runs is subtracted from include-runs, security checks included."""
        mock_repo = Mock()
        mock_repo.private = False
        mock_get_repo.return_value = mock_repo
        mock_get_branch.return_value = Mock()

        mock_config = Mock()
        mock_config.get_value.side_effect = lambda value, return_on_none, extra_dict=None: {
            "protected-branches": {"main": {"include-runs": include_runs, "exclude-runs": exclude_runs}},
            "default-status-checks": [],
            "security-checks": {},
        }.get(value, return_on_none)
        mock_config.repository_local_data.return_value = {}

        result = set_repository(
            repository_name="test-repo",
            data={"name": "owner/test-repo"},
            apis_dict={"test-repo": {"api": Mock(), "user": "test-user"}},
            branch_protection={"strict": True},
            config=mock_config,
        )

        assert result[0] is True
        mock_get_required_checks.assert_not_called()
        assert mock_set_branch_protection.call_args.kwargs["required_status_checks"] == expected

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

    @pytest.mark.parametrize(
        ("local_security_checks", "expected"),
        [
            ({"mandatory": False}, []),
            ({"mandatory": True}, [SECURITY_SUSPICIOUS_PATHS_STR, SECURITY_COMMITTER_IDENTITY_STR]),
        ],
        ids=["repo-local-not-mandatory", "repo-local-mandatory"],
    )
    @patch("webhook_server.utils.github_repository_settings.set_repository_labels")
    @patch("webhook_server.utils.github_repository_settings.set_repository_settings")
    @patch("webhook_server.utils.github_repository_settings.get_branch_sampler")
    @patch("webhook_server.utils.github_repository_settings.set_branch_protection")
    @patch("webhook_server.utils.github_repository_settings._get_github_repo_api")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_repository_reads_repo_local_security_checks(
        self,
        mock_logger: Mock,
        mock_get_repo: Mock,
        mock_set_branch_protection: Mock,
        mock_get_branch: Mock,
        mock_set_repo_settings: Mock,
        mock_set_repo_labels: Mock,
        local_security_checks: dict[str, Any],
        expected: list[str],
    ) -> None:
        """The repo-local .github-webhook-server.yaml security-checks is read at startup and wins."""
        mock_repo = Mock()
        mock_repo.private = False
        mock_get_repo.return_value = mock_repo
        mock_get_branch.return_value = Mock()

        config = _FakeConfig(
            repository_data={
                "protected-branches": {"main": {}},
                "default-status-checks": [],
                "security-checks": {"mandatory": True},
            },
            local_data={"security-checks": local_security_checks},
        )

        result = set_repository(
            repository_name="test-repo",
            data={"name": "owner/test-repo"},
            apis_dict={"test-repo": {"api": Mock(), "user": "test-user"}},
            branch_protection={"strict": True},
            config=config,  # type: ignore[arg-type]
        )

        assert result[0] is True
        assert config.local_data_calls == ["owner/test-repo"]
        contexts = mock_set_branch_protection.call_args.kwargs["required_status_checks"]
        assert [check for check in contexts if check in expected] == expected
        assert (SECURITY_SUSPICIOUS_PATHS_STR in contexts) is bool(expected)

    @patch("webhook_server.utils.github_repository_settings.set_repository_labels")
    @patch("webhook_server.utils.github_repository_settings.set_repository_settings")
    @patch("webhook_server.utils.github_repository_settings.get_branch_sampler")
    @patch("webhook_server.utils.github_repository_settings.set_branch_protection")
    @patch("webhook_server.utils.github_repository_settings._get_github_repo_api")
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_set_repository_invalid_repo_local_yaml_does_not_abort(
        self,
        mock_logger: Mock,
        mock_get_repo: Mock,
        mock_set_branch_protection: Mock,
        mock_get_branch: Mock,
        mock_set_repo_settings: Mock,
        mock_set_repo_labels: Mock,
    ) -> None:
        """Invalid repo-local YAML is logged and ignored, startup continues with config.yaml values."""
        mock_repo = Mock()
        mock_repo.private = False
        mock_get_repo.return_value = mock_repo
        mock_get_branch.return_value = Mock()

        config = _FakeConfig(
            repository_data={
                "protected-branches": {"main": {}},
                "default-status-checks": [],
                "security-checks": {"mandatory": False},
            },
            local_data={},
        )
        config.repository_local_data = Mock(  # type: ignore[method-assign]
            side_effect=yaml.YAMLError("bad yaml"), __name__="repository_local_data"
        )

        result = set_repository(
            repository_name="test-repo",
            data={"name": "owner/test-repo"},
            apis_dict={"test-repo": {"api": Mock(), "user": "test-user"}},
            branch_protection={"strict": True},
            config=config,  # type: ignore[arg-type]
        )

        assert result[0] is True
        required = mock_set_branch_protection.call_args.kwargs["required_status_checks"]
        assert SECURITY_SUSPICIOUS_PATHS_STR not in required
        mock_logger.warning.assert_called()

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

        # Mock pull request and commits
        mock_pull_request = Mock()
        mock_pull_request.number = 123
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
        mock_config.get_value.return_value = 12345

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
                mock_config.get_value.assert_called_with("github-app-id")
                mock_auth.AppAuth.assert_called_once_with(app_id=12345, private_key="test-private-key")
                mock_app_instance.get_repo_installation.assert_called_once_with(owner="owner", repo="repo")

    @patch("builtins.open", create=True)
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_get_repository_github_app_api_exception(self, mock_logger: Mock, mock_open: Mock) -> None:
        """Test GitHub app API retrieval when exception occurs."""
        mock_config = Mock()
        mock_config.data_dir = "/test/dir"
        mock_config.root_data = {"github-app-id": 12345}
        mock_config.get_value.return_value = 12345

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
                mock_config.get_value.assert_called_with("github-app-id")
                mock_logger.error.assert_called_once()
                assert "Repository owner/repo not found by manage-repositories-app" in mock_logger.error.call_args[0][0]

    @patch("builtins.open", create=True)
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_get_repository_github_app_token_success(self, _mock_logger: Mock, mock_open: Mock) -> None:
        """Test successful GitHub app token retrieval."""
        mock_config = Mock()
        mock_config.data_dir = "/test/dir"
        mock_config.root_data = {"github-app-id": 12345}
        mock_config.get_value.return_value = 12345

        mock_file = Mock()
        mock_file.read.return_value = "test-private-key"
        mock_open.return_value.__enter__.return_value = mock_file

        with patch("webhook_server.utils.github_repository_settings.Auth") as mock_auth:
            with patch("webhook_server.utils.github_repository_settings.GithubIntegration") as mock_integration:
                mock_app_auth = Mock()
                mock_auth.AppAuth.return_value = mock_app_auth

                mock_app_instance = Mock()
                mock_integration.return_value = mock_app_instance

                mock_installation = Mock()
                mock_installation.id = 67890
                mock_app_instance.get_repo_installation.return_value = mock_installation

                mock_access_token = Mock()
                mock_access_token.token = "fake-installation-token"  # pragma: allowlist secret
                mock_app_instance.get_access_token.return_value = mock_access_token

                result = get_repository_github_app_token(mock_config, "owner/repo")

                assert result == "fake-installation-token"  # pragma: allowlist secret
                mock_config.get_value.assert_called_with("github-app-id")
                mock_auth.AppAuth.assert_called_once_with(app_id=12345, private_key="test-private-key")
                mock_app_instance.get_repo_installation.assert_called_once_with(owner="owner", repo="repo")
                mock_app_instance.get_access_token.assert_called_once_with(67890)

    @patch("builtins.open", create=True)
    @patch("webhook_server.utils.github_repository_settings.LOGGER")
    def test_get_repository_github_app_token_failure(self, mock_logger: Mock, mock_open: Mock) -> None:
        """Test GitHub app token retrieval when exception occurs."""
        mock_config = Mock()
        mock_config.data_dir = "/test/dir"
        mock_config.root_data = {"github-app-id": 12345}
        mock_config.get_value.return_value = 12345

        mock_file = Mock()
        mock_file.read.return_value = "test-private-key"
        mock_open.return_value.__enter__.return_value = mock_file

        with patch("webhook_server.utils.github_repository_settings.Auth") as mock_auth:
            with patch("webhook_server.utils.github_repository_settings.GithubIntegration") as mock_integration:
                mock_app_auth = Mock()
                mock_auth.AppAuth.return_value = mock_app_auth

                mock_app_instance = Mock()
                mock_integration.return_value = mock_app_instance
                mock_app_instance.get_repo_installation.side_effect = GithubException(404, "App not installed", None)

                result = get_repository_github_app_token(mock_config, "owner/repo")

                assert result is None
                mock_config.get_value.assert_called_with("github-app-id")
                mock_logger.exception.assert_called_once()
                assert (
                    "Failed to get GitHub App installation token for owner/repo"
                    in mock_logger.exception.call_args[0][0]
                )
