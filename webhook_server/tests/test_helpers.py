import logging
import os
from unittest.mock import Mock, patch

import pytest

from webhook_server.utils.helpers import (
    extract_key_from_dict,
    get_logger_with_params,
    get_api_with_highest_rate_limit,
    get_apis_and_tokes_from_config,
    get_github_repo_api,
)


class TestHelpers:
    """Test suite for utility helper functions."""

    def test_extract_key_from_dict_simple(self) -> None:
        """Test extracting key from simple dictionary."""
        test_dict = {"key1": "value1", "key2": "value2"}
        result = list(extract_key_from_dict(key="key1", _dict=test_dict))
        assert result == ["value1"]

    def test_extract_key_from_dict_nested(self) -> None:
        """Test extracting key from nested dictionary."""
        test_dict = {"level1": {"key1": "nested_value1", "level2": {"key1": "nested_value2"}}, "key1": "root_value"}
        result = list(extract_key_from_dict(key="key1", _dict=test_dict))
        assert set(result) == {"nested_value1", "nested_value2", "root_value"}

    def test_extract_key_from_dict_with_lists(self) -> None:
        """Test extracting key from dictionary containing lists."""
        test_dict = {
            "items": [{"key1": "list_value1"}, {"key1": "list_value2", "other": "ignored"}],
            "key1": "root_value",
        }
        result = list(extract_key_from_dict(key="key1", _dict=test_dict))
        assert set(result) == {"list_value1", "list_value2", "root_value"}

    def test_extract_key_from_dict_not_found(self) -> None:
        """Test extracting non-existent key returns empty list."""
        test_dict = {"key1": "value1", "key2": "value2"}
        result = list(extract_key_from_dict(key="nonexistent", _dict=test_dict))
        assert result == []

    def test_extract_key_from_dict_empty_dict(self) -> None:
        """Test extracting key from empty dictionary."""
        result = list(extract_key_from_dict(key="any_key", _dict={}))
        assert result == []

    def test_extract_key_from_dict_complex_nested(self) -> None:
        """Test extracting key from complex nested structure."""
        test_dict = {
            "pull_request": {"number": 123},
            "issue": {"number": 456},
            "commits": [{"commit": {"message": "test", "number": 789}}, {"commit": {"message": "test2"}}],
        }
        result = list(extract_key_from_dict(key="number", _dict=test_dict))
        assert set(result) == {123, 456, 789}

    def test_get_logger_with_params_default(self) -> None:
        """Test logger creation with default parameters."""
        unique_name = "test_helpers_logger"
        logger = get_logger_with_params(name=unique_name)
        assert isinstance(logger, logging.Logger)
        assert logger.name == unique_name

    def test_get_logger_with_params_with_repository(self) -> None:
        """Test logger creation with repository name."""
        logger = get_logger_with_params(name="test", repository_name="test-repo")
        assert isinstance(logger, logging.Logger)
        # The logger should have repository-specific formatting

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    def test_get_apis_and_tokes_from_config(self) -> None:
        """Test getting APIs and tokens from configuration."""
        from webhook_server.libs.config import Config

        config = Config(repository="test-repo")
        apis_and_tokens = get_apis_and_tokes_from_config(config=config)

        # Should return a list of tuples (api, token)
        assert isinstance(apis_and_tokens, list)
        # Each item should be a tuple
        for api, token in apis_and_tokens:
            assert isinstance(token, str)
            # API objects should have certain attributes
            assert hasattr(api, "get_user")

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.utils.helpers.log_rate_limit")
    def test_get_api_with_highest_rate_limit(self, mock_log_rate_limit: Mock, mock_get_apis: Mock) -> None:
        """Test getting API with highest rate limit."""
        from webhook_server.libs.config import Config

        # Mock APIs with different rate limits
        mock_api1 = Mock()
        mock_api1.rate_limiting = [100, 5000]  # 100 remaining, 5000 limit
        mock_api1.get_user.return_value.login = "user1"
        mock_rate_limit1 = Mock()
        mock_rate_limit1.core.remaining = 100
        mock_rate_limit1.core.reset = Mock()
        mock_rate_limit1.core.limit = 5000
        mock_api1.get_rate_limit.return_value = mock_rate_limit1

        mock_api2 = Mock()
        mock_api2.rate_limiting = [200, 5000]  # 200 remaining, 5000 limit
        mock_api2.get_user.return_value.login = "user2"
        mock_rate_limit2 = Mock()
        mock_rate_limit2.core.remaining = 200
        mock_rate_limit2.core.reset = Mock()
        mock_rate_limit2.core.limit = 5000
        mock_api2.get_rate_limit.return_value = mock_rate_limit2

        mock_get_apis.return_value = [(mock_api1, "token1"), (mock_api2, "token2")]

        config = Config(repository="test-repo")
        api, token, user = get_api_with_highest_rate_limit(config=config, repository_name="test-repo")

        # Should return the API with higher rate limit (mock_api2)
        assert api == mock_api2
        assert token == "token2"
        assert user == "user2"

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    def test_get_api_with_highest_rate_limit_no_apis(self, mock_get_apis: Mock) -> None:
        """Test getting API when no APIs available."""
        from webhook_server.libs.config import Config
        from webhook_server.libs.exceptions import NoApiTokenError

        mock_get_apis.return_value = []

        config = Config(repository="test-repo")

        # Should raise NoApiTokenError when no APIs available
        with pytest.raises(NoApiTokenError, match="Failed to get API with highest rate limit"):
            get_api_with_highest_rate_limit(config=config, repository_name="test-repo")

    def test_get_github_repo_api(self) -> None:
        """Test getting GitHub repository API."""
        mock_github_api = Mock()
        mock_repo = Mock()
        mock_github_api.get_repo.return_value = mock_repo

        repository_name = "owner/repo"
        result = get_github_repo_api(github_app_api=mock_github_api, repository=repository_name)

        mock_github_api.get_repo.assert_called_once_with(repository_name)
        assert result == mock_repo

    def test_get_github_repo_api_exception(self) -> None:
        """Test getting GitHub repository API with exception."""
        mock_github_api = Mock()
        mock_github_api.get_repo.side_effect = Exception("Repository not found")

        repository_name = "owner/repo"

        # Should raise the exception when it occurs
        with pytest.raises(Exception, match="Repository not found"):
            get_github_repo_api(github_app_api=mock_github_api, repository=repository_name)

    def test_extract_key_from_dict_with_none_values(self) -> None:
        """Test extracting key from dictionary with None values."""
        test_dict = {"key1": None, "nested": {"key1": "value1", "key2": None}}
        result = list(extract_key_from_dict(key="key1", _dict=test_dict))
        # Should return all values including None
        assert result == [None, "value1"]

    def test_extract_key_from_dict_with_boolean_values(self) -> None:
        """Test extracting key from dictionary with boolean values."""
        test_dict = {"key1": True, "nested": {"key1": False, "key2": "string_value"}}
        result = list(extract_key_from_dict(key="key1", _dict=test_dict))
        # Should include boolean values
        assert set(result) == {True, False}

    def test_extract_key_from_dict_with_numeric_values(self) -> None:
        """Test extracting key from dictionary with numeric values."""
        test_dict = {"key1": 42, "nested": {"key1": 3.14, "key2": "ignored"}, "list": [{"key1": 0}]}
        result = list(extract_key_from_dict(key="key1", _dict=test_dict))
        # Should include all numeric values
        assert set(result) == {42, 3.14, 0}

    @patch("webhook_server.utils.helpers.get_apis_and_tokes_from_config")
    @patch("webhook_server.utils.helpers.log_rate_limit")
    def test_get_api_with_highest_rate_limit_invalid_tokens(
        self, mock_log_rate_limit: Mock, mock_get_apis: Mock
    ) -> None:
        """Test getting API with invalid tokens (rate limit 60)."""
        from webhook_server.libs.config import Config

        # Mock API with invalid token (rate limit 60)
        mock_api1 = Mock()
        mock_api1.rate_limiting = [30, 60]  # Invalid token indicator
        mock_api1.get_user.return_value.login = "user1"

        # Mock API with valid token
        mock_api2 = Mock()
        mock_api2.rate_limiting = [100, 5000]  # Valid token
        mock_api2.get_user.return_value.login = "user2"
        mock_rate_limit2 = Mock()
        mock_rate_limit2.core.remaining = 100
        mock_rate_limit2.core.reset = Mock()
        mock_rate_limit2.core.limit = 5000
        mock_api2.get_rate_limit.return_value = mock_rate_limit2

        mock_get_apis.return_value = [(mock_api1, "invalid_token"), (mock_api2, "valid_token")]

        with patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"}):
            config = Config(repository="test-repo")
            api, token, user = get_api_with_highest_rate_limit(config=config, repository_name="test-repo")

            # Should skip invalid token and return valid one
            assert api == mock_api2
            assert token == "valid_token"
            assert user == "user2"
