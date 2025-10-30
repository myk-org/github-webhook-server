import os
import shutil
import tempfile
from typing import Any
from unittest.mock import Mock, patch

import pytest
import yaml
from github.GithubException import UnknownObjectException

from webhook_server.libs.config import Config


class TestConfig:
    """Test suite for Config class to achieve 100% coverage."""

    @pytest.fixture
    def valid_config_data(self) -> dict[str, Any]:
        """Valid configuration data for testing."""
        return {
            "github-app-id": 123456,
            "github-tokens": ["token1"],
            "webhook-ip": "http://localhost:5000",
            "repositories": {"test-repo": {"name": "org/test-repo"}},
        }

    @pytest.fixture
    def temp_config_dir(self, valid_config_data: dict[str, Any]) -> str:
        """Create a temporary directory with config.yaml file."""
        temp_dir = tempfile.mkdtemp()
        config_file = os.path.join(temp_dir, "config.yaml")

        with open(config_file, "w") as f:
            yaml.dump(valid_config_data, f)

        return temp_dir

    def test_init_with_default_logger(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test Config initialization with default logger."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config = Config()

        assert config.logger is not None
        assert config.data_dir == temp_config_dir
        assert config.config_path == os.path.join(temp_config_dir, "config.yaml")
        assert config.repository is None

    def test_init_with_custom_logger_and_repository(
        self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test Config initialization with custom logger and repository."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        mock_logger = Mock()
        config = Config(logger=mock_logger, repository="test-repo")

        assert config.logger == mock_logger
        assert config.repository == "test-repo"

    def test_init_with_custom_data_dir(
        self, valid_config_data: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test Config initialization with custom data directory."""
        # Use a temporary directory instead of /custom to avoid permission issues
        custom_dir = tempfile.mkdtemp()
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", custom_dir)

        # Create config file in custom directory
        config_file = os.path.join(custom_dir, "config.yaml")
        with open(config_file, "w") as f:
            yaml.dump(valid_config_data, f)

        try:
            config = Config()
            assert config.data_dir == custom_dir
            assert config.config_path == os.path.join(custom_dir, "config.yaml")
        finally:
            shutil.rmtree(custom_dir)

    def test_exists_file_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test exists() method when config file is not found."""
        temp_dir = tempfile.mkdtemp()
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_dir)

        try:
            with pytest.raises(FileNotFoundError, match="Config file .* not found"):
                Config()
        finally:
            shutil.rmtree(temp_dir)

    def test_repositories_exists_missing_repositories(
        self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test repositories_exists() method when repositories key is missing."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        # Create config without repositories
        config_file = os.path.join(temp_config_dir, "config.yaml")
        config_data = {
            "github-app-id": 123456,
            "github-tokens": ["token1"],
            "webhook-ip": "http://localhost:5000",
        }
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        with pytest.raises(ValueError, match="does not have `repositories`"):
            Config()

    def test_root_data_success(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test root_data property with valid config file."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config = Config()
        root_data = config.root_data

        assert root_data["github-app-id"] == 123456
        assert root_data["webhook-ip"] == "http://localhost:5000"
        assert "repositories" in root_data

    def test_root_data_empty_file(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test root_data property with empty config file."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        # Create empty config file
        config_file = os.path.join(temp_config_dir, "config.yaml")
        with open(config_file, "w") as f:
            f.write("")

        # Test root_data property directly without calling __init__
        config = Config.__new__(Config)
        config.config_path = config_file
        config.logger = Mock()

        root_data = config.root_data
        assert root_data is None or root_data == {}

    def test_root_data_corrupted_file(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test root_data property with corrupted config file."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        # Create corrupted config file
        config_file = os.path.join(temp_config_dir, "config.yaml")
        with open(config_file, "w") as f:
            f.write("invalid: yaml: content: [")

        # Test root_data property directly without calling __init__
        config = Config.__new__(Config)
        config.config_path = config_file
        config.logger = Mock()

        # Corrupted YAML should raise exception, not return empty dict
        with pytest.raises(yaml.YAMLError):
            _ = config.root_data

    def test_repository_data_with_repository(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test repository_data property when repository is specified."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config = Config(repository="test-repo")
        repo_data = config.repository_data

        assert repo_data["name"] == "org/test-repo"

    def test_repository_data_without_repository(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test repository_data property when repository is not specified."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config = Config()
        repo_data = config.repository_data

        assert repo_data == {}

    def test_repository_data_nonexistent_repository(
        self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test repository_data property with nonexistent repository."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config = Config(repository="nonexistent-repo")
        repo_data = config.repository_data

        assert repo_data == {}

    def test_repository_local_data_success(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test repository_local_data method with successful config file retrieval."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        # Mock repository and config file
        mock_repo = Mock()
        mock_config_file = Mock()
        mock_config_file.decoded_content = yaml.dump({"local-setting": "value"}).encode()
        mock_repo.get_contents.return_value = mock_config_file

        config = Config(repository="test-repo")
        mock_github_api = Mock()
        mock_github_api.get_repo.return_value = mock_repo

        result = config.repository_local_data(mock_github_api, "org/test-repo")

        assert result == {"local-setting": "value"}
        mock_github_api.get_repo.assert_called_once_with("org/test-repo")
        mock_repo.get_contents.assert_called_once_with(".github-webhook-server.yaml")

    def test_repository_local_data_list_result(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test repository_local_data method when get_contents returns a list."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        # Mock repository and config file
        mock_repo = Mock()
        mock_config_file = Mock()
        mock_config_file.decoded_content = yaml.dump({"local-setting": "value"}).encode()
        mock_repo.get_contents.return_value = [mock_config_file]  # List result

        config = Config(repository="test-repo")
        mock_github_api = Mock()
        mock_github_api.get_repo.return_value = mock_repo

        result = config.repository_local_data(mock_github_api, "org/test-repo")

        assert result == {"local-setting": "value"}

    def test_repository_local_data_file_not_found(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test repository_local_data method when config file is not found."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        # Mock repository that raises UnknownObjectException
        mock_repo = Mock()
        mock_repo.get_contents.side_effect = UnknownObjectException(404, "Not found")

        config = Config(repository="test-repo")
        mock_github_api = Mock()
        mock_github_api.get_repo.return_value = mock_repo

        result = config.repository_local_data(mock_github_api, "org/test-repo")

        assert result == {}

    def test_repository_local_data_invalid_yaml(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test repository_local_data method with invalid YAML syntax."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        # Mock repository with invalid YAML content
        mock_repo = Mock()
        mock_config_file = Mock()
        mock_config_file.decoded_content = b"invalid: yaml: content: ["
        mock_repo.get_contents.return_value = mock_config_file

        config = Config(repository="test-repo")
        mock_github_api = Mock()
        mock_github_api.get_repo.return_value = mock_repo

        # Invalid YAML should raise YAMLError, not return empty dict
        with pytest.raises(yaml.YAMLError):
            config.repository_local_data(mock_github_api, "org/test-repo")

    def test_repository_local_data_exception_handling(
        self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test repository_local_data method with exception handling."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        # Mock github_api that raises an exception
        config = Config(repository="test-repo")
        mock_github_api = Mock()
        mock_github_api.get_repo.side_effect = Exception("API Error")

        result = config.repository_local_data(mock_github_api, "org/test-repo")

        assert result == {}

    def test_repository_local_data_no_repository(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test repository_local_data method when repository is not specified."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config = Config()  # No repository specified
        mock_github_api = Mock()

        result = config.repository_local_data(mock_github_api, "")

        assert result == {}

    def test_repository_local_data_no_repository_full_name(
        self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test repository_local_data method when repository_full_name is not specified."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config = Config(repository="test-repo")
        mock_github_api = Mock()

        result = config.repository_local_data(mock_github_api, "")

        assert result == {}

    def test_get_value_from_extra_dict(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test get_value method when value is found in extra_dict."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config = Config()
        extra_dict = {"test-key": "extra-value"}

        result = config.get_value("test-key", extra_dict=extra_dict)

        assert result == "extra-value"

    def test_get_value_from_extra_dict_none(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test get_value method when value in extra_dict is None."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config = Config()
        extra_dict = {"test-key": None}

        result = config.get_value("test-key", return_on_none="default", extra_dict=extra_dict)

        assert result == "default"

    def test_get_value_from_repository_data(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test get_value method when value is found in repository_data."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config = Config(repository="test-repo")

        result = config.get_value("name")

        assert result == "org/test-repo"

    def test_get_value_from_root_data(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test get_value method when value is found in root_data."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config = Config()

        result = config.get_value("github-app-id")

        assert result == 123456

    def test_get_value_not_found(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test get_value method when value is not found anywhere."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config = Config()

        result = config.get_value("nonexistent-key", return_on_none="default")

        assert result == "default"

    def test_get_value_not_found_no_default(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test get_value method when value is not found and no default is provided."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config = Config()

        result = config.get_value("nonexistent-key")

        assert result is None

    def test_get_value_none_in_config(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test get_value method when value exists but is None in config."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        # Create config with None value
        config_file = os.path.join(temp_config_dir, "config.yaml")
        config_data = {
            "github-app-id": 123456,
            "github-tokens": ["token1"],
            "webhook-ip": "http://localhost:5000",
            "repositories": {"test-repo": {"name": "org/test-repo", "nonexistent-key": None}},
        }
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = Config(repository="test-repo")

        result = config.get_value("nonexistent-key", return_on_none="default")

        assert result == "default"

    def test_get_value_without_extra_dict(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test get_value method without extra_dict parameter."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config = Config()

        result = config.get_value("github-app-id")

        assert result == 123456

    def test_get_value_priority_order(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test get_value method priority order: extra_dict > repository_data > root_data."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        # Create config with same key in both root and repository
        config_file = os.path.join(temp_config_dir, "config.yaml")
        config_data = {
            "github-app-id": 123456,
            "github-tokens": ["token1"],
            "webhook-ip": "http://localhost:5000",
            "test-key": "root-value",
            "repositories": {"test-repo": {"name": "org/test-repo", "test-key": "repo-value"}},
        }
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = Config(repository="test-repo")

        # Test priority: extra_dict should win
        extra_dict = {"test-key": "extra-value"}
        result = config.get_value("test-key", extra_dict=extra_dict)
        assert result == "extra-value"

        # Test priority: repository_data should win over root_data
        result = config.get_value("test-key")
        assert result == "repo-value"

    def test_root_data_permission_error(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test root_data property handling PermissionError when reading config file."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config_file = os.path.join(temp_config_dir, "config.yaml")

        # Create config object without calling __init__
        config = Config.__new__(Config)
        config.config_path = config_file
        config.logger = Mock()

        # Mock open to raise PermissionError
        with patch("builtins.open", side_effect=PermissionError("Permission denied")):
            with pytest.raises(PermissionError):
                _ = config.root_data

        # Verify logger.exception was called
        config.logger.exception.assert_called_once()

    def test_root_data_file_not_found_after_init(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test root_data property handling FileNotFoundError after successful init (race condition)."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config_file = os.path.join(temp_config_dir, "config.yaml")

        # Create config object without calling __init__
        config = Config.__new__(Config)
        config.config_path = config_file
        config.logger = Mock()

        # Mock open to raise FileNotFoundError (simulating race condition)
        with patch("builtins.open", side_effect=FileNotFoundError("File disappeared")):
            with pytest.raises(FileNotFoundError):
                _ = config.root_data

        # Verify logger.exception was called
        config.logger.exception.assert_called_once()

    def test_root_data_generic_exception(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test root_data property handling generic Exception when reading config file."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config_file = os.path.join(temp_config_dir, "config.yaml")

        # Create config object without calling __init__
        config = Config.__new__(Config)
        config.config_path = config_file
        config.logger = Mock()

        # Mock open to raise generic Exception
        with patch("builtins.open", side_effect=Exception("Unexpected error")):
            with pytest.raises(Exception, match="Unexpected error"):
                _ = config.root_data

        # Verify logger.exception was called
        config.logger.exception.assert_called_once()

    # =================================================================
    # Dot Notation Tests
    # =================================================================

    def test_get_nested_value_single_level(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test _get_nested_value with single-level key."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)
        config = Config(repository="test-repo")

        data = {"graphql": {"tree-max-depth": 9}}
        result = config._get_nested_value("graphql", data)

        assert result == {"tree-max-depth": 9}

    def test_get_nested_value_two_levels(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test _get_nested_value with dot notation (two levels)."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)
        config = Config(repository="test-repo")

        data = {"graphql": {"tree-max-depth": 9}}
        result = config._get_nested_value("graphql.tree-max-depth", data)

        assert result == 9

    def test_get_nested_value_three_levels(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test _get_nested_value with three-level dot notation."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)
        config = Config(repository="test-repo")

        data = {"graphql": {"query-limits": {"collaborators": 100}}}
        result = config._get_nested_value("graphql.query-limits.collaborators", data)

        assert result == 100

    def test_get_nested_value_nonexistent_key(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test _get_nested_value with non-existent key returns None."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)
        config = Config(repository="test-repo")

        data = {"graphql": {"tree-max-depth": 9}}
        result = config._get_nested_value("graphql.nonexistent", data)

        assert result is None

    def test_get_nested_value_nonexistent_parent(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test _get_nested_value with non-existent parent key returns None."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)
        config = Config(repository="test-repo")

        data = {"graphql": {"tree-max-depth": 9}}
        result = config._get_nested_value("nonexistent.child", data)

        assert result is None

    def test_get_nested_value_non_dict_intermediate(
        self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test _get_nested_value when intermediate value is not a dict."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)
        config = Config(repository="test-repo")

        data = {"graphql": "not_a_dict"}
        result = config._get_nested_value("graphql.tree-max-depth", data)

        assert result is None

    def test_get_value_dot_notation_from_repository_data(
        self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test get_value with dot notation from repository_data."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        # Create config with nested graphql settings in repository
        config_data = {"repositories": {"test-repo": {"name": "org/test-repo", "graphql": {"tree-max-depth": 5}}}}

        config_file = os.path.join(temp_config_dir, "config.yaml")
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = Config(repository="test-repo")
        result = config.get_value("graphql.tree-max-depth")

        assert result == 5

    def test_get_value_dot_notation_from_root_data(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test get_value with dot notation from root_data."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        # Create config with nested graphql settings at root
        config_data = {
            "repositories": {"test-repo": {"name": "org/test-repo"}},
            "graphql": {"tree-max-depth": 9, "query-limits": {"collaborators": 100}},
        }

        config_file = os.path.join(temp_config_dir, "config.yaml")
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = Config(repository="test-repo")
        result = config.get_value("graphql.tree-max-depth")

        assert result == 9

    def test_get_value_dot_notation_from_extra_dict(
        self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test get_value with dot notation from extra_dict."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)
        config = Config(repository="test-repo")

        extra_dict = {"graphql": {"tree-max-depth": 12}}

        result = config.get_value("graphql.tree-max-depth", extra_dict=extra_dict)

        assert result == 12

    def test_get_value_dot_notation_priority_extra_dict(
        self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test get_value dot notation priority: extra_dict > repository_data > root_data."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        # Set up config with value in all scopes
        config_data = {
            "repositories": {
                "test-repo": {
                    "name": "org/test-repo",
                    "graphql": {"tree-max-depth": 5},  # Repository level
                }
            },
            "graphql": {"tree-max-depth": 9},  # Root level
        }

        config_file = os.path.join(temp_config_dir, "config.yaml")
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = Config(repository="test-repo")

        # extra_dict should take priority
        extra_dict = {"graphql": {"tree-max-depth": 12}}
        result = config.get_value("graphql.tree-max-depth", extra_dict=extra_dict)

        assert result == 12

    def test_get_value_dot_notation_priority_repository_data(
        self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test get_value dot notation priority: repository_data > root_data."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        # Set up config with value in repository and root
        config_data = {
            "repositories": {
                "test-repo": {
                    "name": "org/test-repo",
                    "graphql": {"tree-max-depth": 5},  # Repository level
                }
            },
            "graphql": {"tree-max-depth": 9},  # Root level
        }

        config_file = os.path.join(temp_config_dir, "config.yaml")
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = Config(repository="test-repo")

        # repository_data should take priority over root_data
        result = config.get_value("graphql.tree-max-depth")

        assert result == 5

    def test_get_value_dot_notation_not_found_returns_default(
        self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test get_value with dot notation returns default when not found."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)
        config = Config(repository="test-repo")

        result = config.get_value("graphql.nonexistent.key", return_on_none=999)

        assert result == 999

    def test_get_value_dot_notation_complex_path(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test get_value with complex multi-level dot notation path."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config_data = {
            "repositories": {"test-repo": {"name": "org/test-repo"}},
            "graphql": {"query-limits": {"pull-requests": 50, "collaborators": 100}},
        }

        config_file = os.path.join(temp_config_dir, "config.yaml")
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = Config(repository="test-repo")

        result = config.get_value("graphql.query-limits.pull-requests")
        assert result == 50

        result = config.get_value("graphql.query-limits.collaborators")
        assert result == 100

    def test_get_value_dot_notation_with_hyphens(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test get_value with keys containing hyphens."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config_data = {
            "repositories": {"test-repo": {"name": "org/test-repo"}},
            "branch-protection": {"require-code-owner-reviews": True},
        }

        config_file = os.path.join(temp_config_dir, "config.yaml")
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = Config(repository="test-repo")
        result = config.get_value("branch-protection.require-code-owner-reviews")

        assert result is True

    # =================================================================
    # Edge Case Tests for Dot Notation
    # =================================================================

    def test_get_nested_value_empty_key(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test _get_nested_value with empty string key - should return None (no key named '')."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)
        config = Config(repository="test-repo")

        data = {"graphql": {"tree-max-depth": 9}}
        result = config._get_nested_value("", data)

        # Empty string splits to [""] which looks for key named "" - returns None
        assert result is None

    def test_get_nested_value_dots_only(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test _get_nested_value with key containing only dots - should return None (no keys named '')."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)
        config = Config(repository="test-repo")

        data = {"graphql": {"tree-max-depth": 9}}
        result = config._get_nested_value("...", data)

        assert result is None

    def test_get_nested_value_leading_dot(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test _get_nested_value with leading dot - should return None (no key named '')."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)
        config = Config(repository="test-repo")

        data = {"graphql": {"tree-max-depth": 9}}
        result = config._get_nested_value(".graphql", data)

        assert result is None

    def test_get_nested_value_trailing_dot(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test _get_nested_value with trailing dot - should return None (no key named '')."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)
        config = Config(repository="test-repo")

        data = {"graphql": {"tree-max-depth": 9}}
        result = config._get_nested_value("graphql.", data)

        assert result is None

    def test_get_nested_value_multiple_consecutive_dots(
        self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test _get_nested_value with multiple consecutive dots - should return None (no key named '')."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)
        config = Config(repository="test-repo")

        data = {"graphql": {"tree-max-depth": 9}}
        result = config._get_nested_value("graphql..tree-max-depth", data)

        assert result is None

    def test_get_value_dot_notation_empty_key_returns_default(
        self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test get_value with empty string returns default."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)
        config = Config(repository="test-repo")

        result = config.get_value("", return_on_none="default_value")

        # Empty key returns None from _get_nested_value, so default is returned
        assert result == "default_value"

    def test_get_value_dot_notation_special_chars_in_value(
        self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that values with special characters work correctly."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        config_data = {
            "repositories": {"test-repo": {"name": "org/test-repo"}},
            "special": {"url": "https://example.com/path?query=value&other=123", "message": "Hello, World! @#$%^&*()"},
        }

        config_file = os.path.join(temp_config_dir, "config.yaml")
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = Config(repository="test-repo")

        url_result = config.get_value("special.url")
        assert url_result == "https://example.com/path?query=value&other=123"

        msg_result = config.get_value("special.message")
        assert msg_result == "Hello, World! @#$%^&*()"

    def test_get_nested_value_numeric_value(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test accessing numeric values (int, float)."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)
        config = Config(repository="test-repo")

        data = {"numbers": {"integer": 42, "float": 3.14159, "negative": -100}}

        int_result = config._get_nested_value("numbers.integer", data)
        assert int_result == 42
        assert isinstance(int_result, int)

        float_result = config._get_nested_value("numbers.float", data)
        assert float_result == 3.14159
        assert isinstance(float_result, float)

        neg_result = config._get_nested_value("numbers.negative", data)
        assert neg_result == -100

    def test_get_nested_value_boolean_value(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test accessing boolean values (True/False)."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)
        config = Config(repository="test-repo")

        data = {"flags": {"enabled": True, "disabled": False}}

        true_result = config._get_nested_value("flags.enabled", data)
        assert true_result is True
        assert isinstance(true_result, bool)

        false_result = config._get_nested_value("flags.disabled", data)
        assert false_result is False
        assert isinstance(false_result, bool)

    def test_get_nested_value_none_value(self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test accessing None value - should return None, not the default."""
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)
        config = Config(repository="test-repo")

        data = {"settings": {"value": None}}

        result = config._get_nested_value("settings.value", data)
        assert result is None

    # =================================================================
    # Integration Tests for Full Override Priority Chain
    # =================================================================

    def test_get_value_dot_notation_github_webhook_server_overrides_global(
        self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test full override behavior with .github-webhook-server.yaml having highest priority.

        Scenario:
        1. config.yaml has graphql.tree-max-depth: 9 at root level (global setting)
        2. config.yaml has graphql.tree-max-depth: 7 in repositories section
        3. .github-webhook-server.yaml has graphql.tree-max-depth: 5 (should win)

        Priority: .github-webhook-server.yaml > repositories section > root level
        """
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        # Create config with value in both root and repository sections
        config_data = {
            "github-app-id": 123456,
            "github-tokens": ["token1"],
            "webhook-ip": "http://localhost:5000",
            "graphql": {
                "tree-max-depth": 9  # Root level (lowest priority)
            },
            "repositories": {
                "test-repo": {
                    "name": "org/test-repo",
                    "graphql": {
                        "tree-max-depth": 7  # Repository section (middle priority)
                    },
                }
            },
        }

        config_file = os.path.join(temp_config_dir, "config.yaml")
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = Config(repository="test-repo")

        # Mock GitHub API to simulate fetching .github-webhook-server.yaml
        mock_github_api = Mock()
        mock_repo = Mock()
        mock_github_api.get_repo.return_value = mock_repo

        # Mock .github-webhook-server.yaml content with highest priority value
        local_config_yaml = yaml.dump({"graphql": {"tree-max-depth": 5}})
        mock_contents = Mock()
        mock_contents.decoded_content = local_config_yaml.encode("utf-8")
        mock_repo.get_contents.return_value = mock_contents

        # Fetch local config from .github-webhook-server.yaml
        local_repo_config = config.repository_local_data(mock_github_api, "org/test-repo")

        # Verify local config was fetched correctly
        assert local_repo_config == {"graphql": {"tree-max-depth": 5}}

        # Test priority: .github-webhook-server.yaml should win (return 5)
        result = config.get_value("graphql.tree-max-depth", extra_dict=local_repo_config)
        assert result == 5

        # Verify GitHub API was called correctly
        mock_github_api.get_repo.assert_called_once_with("org/test-repo")
        mock_repo.get_contents.assert_called_once_with(".github-webhook-server.yaml")

        # Test without extra_dict: repository section should win over root (return 7)
        result_without_local = config.get_value("graphql.tree-max-depth")
        assert result_without_local == 7

    def test_get_value_dot_notation_full_priority_chain(
        self, temp_config_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test complete priority chain with all three sources for nested value.

        Scenario:
        1. Root config.yaml: graphql.query-limits.collaborators: 100
        2. Repository section in config.yaml: graphql.query-limits.collaborators: 75
        3. .github-webhook-server.yaml: graphql.query-limits.collaborators: 50

        Expected result: 50 (from .github-webhook-server.yaml, highest priority)
        """
        monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_config_dir)

        # Create config with nested value in both root and repository sections
        config_data = {
            "github-app-id": 123456,
            "github-tokens": ["token1"],
            "webhook-ip": "http://localhost:5000",
            "graphql": {
                "query-limits": {
                    "collaborators": 100,  # Root level (lowest priority)
                    "pull-requests": 50,
                }
            },
            "repositories": {
                "test-repo": {
                    "name": "org/test-repo",
                    "graphql": {
                        "query-limits": {
                            "collaborators": 75  # Repository section (middle priority)
                        }
                    },
                }
            },
        }

        config_file = os.path.join(temp_config_dir, "config.yaml")
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = Config(repository="test-repo")

        # Mock GitHub API
        mock_github_api = Mock()
        mock_repo = Mock()
        mock_github_api.get_repo.return_value = mock_repo

        # Mock .github-webhook-server.yaml with highest priority nested value
        local_config_yaml = yaml.dump({
            "graphql": {
                "query-limits": {
                    "collaborators": 50  # Highest priority
                }
            }
        })
        mock_contents = Mock()
        mock_contents.decoded_content = local_config_yaml.encode("utf-8")
        mock_repo.get_contents.return_value = mock_contents

        # Fetch local config
        local_repo_config = config.repository_local_data(mock_github_api, "org/test-repo")

        # Verify local config structure
        assert local_repo_config == {"graphql": {"query-limits": {"collaborators": 50}}}

        # Test priority: .github-webhook-server.yaml should win (return 50)
        result = config.get_value("graphql.query-limits.collaborators", extra_dict=local_repo_config)
        assert result == 50

        # Test without extra_dict: repository section should win over root (return 75)
        result_without_local = config.get_value("graphql.query-limits.collaborators")
        assert result_without_local == 75

        # Test value that only exists in root (not overridden)
        result_root_only = config.get_value("graphql.query-limits.pull-requests", extra_dict=local_repo_config)
        assert result_root_only == 50

        # Verify API interactions
        mock_github_api.get_repo.assert_called_once_with("org/test-repo")
        mock_repo.get_contents.assert_called_once_with(".github-webhook-server.yaml")
