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
