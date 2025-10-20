import datetime
import logging
import os
import sys
from unittest.mock import Mock, patch

import pytest

from webhook_server.libs.config import Config
from webhook_server.libs.exceptions import NoApiTokenError
from webhook_server.utils.helpers import (
    _redact_secrets,
    _truncate_output,
    extract_key_from_dict,
    get_api_with_highest_rate_limit,
    get_apis_and_tokes_from_config,
    get_future_results,
    get_github_repo_api,
    get_logger_with_params,
    log_rate_limit,
    run_command,
)

# Test tokens for security scanners
TEST_TOKEN_1 = "ghp_test1234567890abcdefghijklmnopqrstu"  # pragma: allowlist secret  # noqa: S105  # gitleaks:allow
TEST_TOKEN_2 = "ghs_test0987654321zyxwvutsrqponmlkjih"  # pragma: allowlist secret  # noqa: S105  # gitleaks:allow
TEST_SECRET_1 = "SECRET_TOKEN_12345"  # pragma: allowlist secret  # noqa: S105  # gitleaks:allow
TEST_SECRET_2 = "SECRET_TOKEN_STDERR"  # pragma: allowlist secret  # noqa: S105  # gitleaks:allow


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
        logger = get_logger_with_params()
        assert isinstance(logger, logging.Logger)
        # Logger name is now the log file path (or 'console') to ensure single handler instance
        assert logger.name  # Verify it has a name

        # Verify actual logging behavior
        assert logger.hasHandlers(), "Logger should have handlers configured"
        assert logger.level in [
            logging.DEBUG,
            logging.INFO,
            logging.WARNING,
            logging.ERROR,
            logging.CRITICAL,
        ], "Logger should have a valid log level"

        # Verify logger can write messages (test basic functionality)
        logger.info("Test message")  # Should not raise exception

    def test_get_logger_with_params_with_repository(self) -> None:
        """Test logger creation with repository name."""
        logger = get_logger_with_params(repository_name="test-repo")
        assert isinstance(logger, logging.Logger)
        # The logger should have repository-specific formatting

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    def test_get_apis_and_tokes_from_config(self) -> None:
        """Test getting APIs and tokens from configuration."""

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

        # Mock APIs with different rate limits
        mock_api1 = Mock()
        mock_api1.rate_limiting = [100, 5000]  # 100 remaining, 5000 limit
        mock_api1.get_user.return_value.login = "user1"
        mock_rate_limit1 = Mock()
        mock_rate_limit1.rate.remaining = 100
        mock_rate_limit1.rate.reset = Mock()
        mock_rate_limit1.rate.limit = 5000
        mock_api1.get_rate_limit.return_value = mock_rate_limit1

        mock_api2 = Mock()
        mock_api2.rate_limiting = [200, 5000]  # 200 remaining, 5000 limit
        mock_api2.get_user.return_value.login = "user2"
        mock_rate_limit2 = Mock()
        mock_rate_limit2.rate.remaining = 200
        mock_rate_limit2.rate.reset = Mock()
        mock_rate_limit2.rate.limit = 5000
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

        # Mock API with invalid token (rate limit 60)
        mock_api1 = Mock()
        mock_api1.rate_limiting = [30, 60]  # Invalid token indicator
        mock_api1.get_user.return_value.login = "user1"

        # Mock API with valid token
        mock_api2 = Mock()
        mock_api2.rate_limiting = [100, 5000]  # Valid token
        mock_api2.get_user.return_value.login = "user2"
        mock_rate_limit2 = Mock()
        mock_rate_limit2.rate.remaining = 100
        mock_rate_limit2.rate.reset = Mock()
        mock_rate_limit2.rate.limit = 5000
        mock_api2.get_rate_limit.return_value = mock_rate_limit2

        mock_get_apis.return_value = [(mock_api1, "invalid_token"), (mock_api2, "valid_token")]

        with patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"}):
            config = Config(repository="test-repo")
            api, token, user = get_api_with_highest_rate_limit(config=config, repository_name="test-repo")

            # Should skip invalid token and return valid one
            assert api == mock_api2
            assert token == "valid_token"
            assert user == "user2"

    def test_get_logger_with_params_log_file_path(self, tmp_path, monkeypatch):
        """Test get_logger_with_params with log_file that is not an absolute path."""
        # Patch Config.get_value to return a log file name
        with patch("webhook_server.utils.helpers.Config") as MockConfig:
            mock_config = MockConfig.return_value
            mock_config.get_value.side_effect = lambda value, **kwargs: "test.log" if value == "log-file" else "INFO"
            mock_config.data_dir = str(tmp_path)
            logger = get_logger_with_params(repository_name="repo")
            assert isinstance(logger, logging.Logger)
            log_dir = tmp_path / "logs"
            assert log_dir.exists()

    def test_get_logger_with_params_mask_sensitive_default(self, tmp_path):
        """Test get_logger_with_params masks sensitive data by default."""
        with patch("webhook_server.utils.helpers.Config") as mock_config:
            # Set up config to return default values (mask_sensitive not set)
            def get_value_side_effect(value, **kwargs):
                if value == "log-file":
                    return "test.log"
                if value == "log-level":
                    return "INFO"
                if value == "mask-sensitive-data":
                    return kwargs.get("return_on_none", True)
                return kwargs.get("return_on_none")

            mock_config.return_value.get_value.side_effect = get_value_side_effect
            mock_config.return_value.data_dir = str(tmp_path)

            with patch("webhook_server.utils.helpers.get_logger") as mock_get_logger:
                get_logger_with_params()
                # Verify mask_sensitive=True was passed
                mock_get_logger.assert_called_once()
                call_kwargs = mock_get_logger.call_args[1]
                assert call_kwargs["mask_sensitive"] is True

    def test_get_logger_with_params_mask_sensitive_disabled(self, tmp_path):
        """Test get_logger_with_params respects mask-sensitive-data=false config."""
        with patch("webhook_server.utils.helpers.Config") as mock_config:
            # Set up config to explicitly disable masking
            def get_value_side_effect(value, **kwargs):
                if value == "log-file":
                    return "test.log"
                if value == "log-level":
                    return "INFO"
                if value == "mask-sensitive-data":
                    return False  # Explicitly disabled
                return kwargs.get("return_on_none")

            mock_config.return_value.get_value.side_effect = get_value_side_effect
            mock_config.return_value.data_dir = str(tmp_path)

            with patch("webhook_server.utils.helpers.get_logger") as mock_get_logger:
                get_logger_with_params()
                # Verify mask_sensitive=False was passed
                mock_get_logger.assert_called_once()
                call_kwargs = mock_get_logger.call_args[1]
                assert call_kwargs["mask_sensitive"] is False

    def test_get_logger_with_params_mask_sensitive_enabled_explicit(self, tmp_path):
        """Test get_logger_with_params respects mask-sensitive-data=true config."""
        with patch("webhook_server.utils.helpers.Config") as mock_config:
            # Set up config to explicitly enable masking
            def get_value_side_effect(value, **kwargs):
                if value == "log-file":
                    return "test.log"
                if value == "log-level":
                    return "INFO"
                if value == "mask-sensitive-data":
                    return True  # Explicitly enabled
                return kwargs.get("return_on_none")

            mock_config.return_value.get_value.side_effect = get_value_side_effect
            mock_config.return_value.data_dir = str(tmp_path)

            with patch("webhook_server.utils.helpers.get_logger") as mock_get_logger:
                get_logger_with_params()
                # Verify mask_sensitive=True was passed
                mock_get_logger.assert_called_once()
                call_kwargs = mock_get_logger.call_args[1]
                assert call_kwargs["mask_sensitive"] is True

    @pytest.mark.asyncio
    async def test_run_command_success(self):
        """Test run_command with a successful command."""
        result = await run_command(f"{sys.executable} -c \"print('hello')\"", log_prefix="[TEST]", redact_secrets=[])
        assert result[0] is True
        assert "hello" in result[1]
        assert isinstance(result[1], str)
        assert isinstance(result[2], str)

    @pytest.mark.asyncio
    async def test_run_command_failure(self):
        """Test run_command with a failing command."""
        result = await run_command(
            f'{sys.executable} -c "import sys; sys.exit(1)"', log_prefix="[TEST]", redact_secrets=[]
        )
        assert result[0] is False
        assert isinstance(result[1], str)
        assert isinstance(result[2], str)

    @pytest.mark.asyncio
    async def test_run_command_stderr(self):
        """Test run_command with stderr and verify_stderr=True."""
        # Use python to print to stderr
        result = await run_command(
            f"{sys.executable} -c \"import sys; sys.stderr.write('err')\"",
            log_prefix="[TEST]",
            verify_stderr=True,
            redact_secrets=[],
        )
        assert result[0] is False
        assert "err" in result[2]
        assert isinstance(result[1], str)
        assert isinstance(result[2], str)

    @pytest.mark.asyncio
    async def test_run_command_exception(self):
        """Test run_command with an invalid command to trigger exception."""
        result = await run_command("nonexistent_command_xyz", log_prefix="[TEST]", redact_secrets=[])
        assert result[0] is False
        assert isinstance(result[1], str)
        assert isinstance(result[2], str)

    def test_redact_secrets_helper_basic(self):
        """Test _redact_secrets helper function with basic redaction."""
        text = "password is secret123 and token is abc456"
        secrets = ["secret123", "abc456"]
        result = _redact_secrets(text, secrets)
        assert result == "password is ***REDACTED*** and token is ***REDACTED***"

    def test_redact_secrets_helper_no_secrets(self):
        """Test _redact_secrets with None secrets list."""
        text = "no secrets here"
        result = _redact_secrets(text, None)
        assert result == "no secrets here"

    def test_redact_secrets_helper_empty_secrets(self):
        """Test _redact_secrets with empty secrets list."""
        text = "no secrets here"
        result = _redact_secrets(text, [])
        assert result == "no secrets here"

    def test_redact_secrets_helper_empty_secret_string(self):
        """Test _redact_secrets skips empty strings in secrets list."""
        text = "password is secret123"
        secrets = ["", "secret123", ""]
        result = _redact_secrets(text, secrets)
        assert result == "password is ***REDACTED***"

    def test_redact_secrets_helper_multiple_occurrences(self):
        """Test _redact_secrets redacts multiple occurrences of same secret."""
        text = "token secret123 appears here and secret123 appears again"
        secrets = ["secret123"]
        result = _redact_secrets(text, secrets)
        assert result == "token ***REDACTED*** appears here and ***REDACTED*** appears again"

    @pytest.mark.asyncio
    async def test_run_command_redaction_does_not_mutate_return_values(self):
        """Test that redaction keeps original values in return, redacts only in logs."""
        # Run a command that will output a secret in stdout
        secret = TEST_SECRET_1
        # Use Python instead of shell echo for portability
        command = f'{sys.executable} -c "print(\\"{secret}\\")"'
        result = await run_command(command, log_prefix="[TEST]", redact_secrets=[secret])

        # Verify command succeeded
        assert result[0] is True

        # CRITICAL: Verify the returned stdout is UNREDACTED (original design intent)
        # Redaction applies only to logs, not return values
        # Callers may need to parse unredacted output
        assert secret in result[1], "Return value should contain original secret (unredacted)"
        assert "***REDACTED***" not in result[1], "Return value should NOT be redacted"
        assert isinstance(result[1], str), "stdout should be a string"
        assert isinstance(result[2], str), "stderr should be a string"

    @pytest.mark.asyncio
    async def test_run_command_redaction_in_stderr(self):
        """Test that redaction keeps original stderr in return, redacts only in logs."""
        secret = TEST_SECRET_2
        # Use python to output secret to stderr
        command = f'{sys.executable} -c "import sys; sys.stderr.write(\\"{secret}\\")"'
        result = await run_command(command, log_prefix="[TEST]", redact_secrets=[secret])

        # Verify the returned stderr is UNREDACTED (original design intent)
        # Redaction applies only to logs, not return values
        assert secret in result[2], "Stderr return value should contain original secret (unredacted)"
        assert "***REDACTED***" not in result[2], "Stderr return value should NOT be redacted"
        assert isinstance(result[1], str), "stdout should be a string"
        assert isinstance(result[2], str), "stderr should be a string"

    def test_log_rate_limit_all_branches(self):
        """Test log_rate_limit for all color/warning branches."""

        # Patch logger to capture logs
        with patch("webhook_server.utils.helpers.get_logger_with_params") as mock_get_logger:
            mock_logger = Mock()
            mock_get_logger.return_value = mock_logger
            now = datetime.datetime.now(datetime.UTC)
            # RED branch (below_minimum)
            rate_core = Mock()
            rate_core.remaining = 600
            rate_core.limit = 5000
            rate_core.reset = now + datetime.timedelta(seconds=1000)
            rate_limit = Mock()
            rate_limit.rate = rate_core
            log_rate_limit(rate_limit, api_user="user1")
            # YELLOW branch
            rate_core.remaining = 1000
            log_rate_limit(rate_limit, api_user="user2")
            # GREEN branch
            rate_core.remaining = 3000
            log_rate_limit(rate_limit, api_user="user3")
            # Check that warning was called for RED branch
            assert mock_logger.warning.called
            assert mock_logger.debug.called

    def test_get_future_results_all_branches(self):
        """Test get_future_results for all result/exception branches."""

        # Success result
        class DummyFuture:
            def result(self):
                return (True, "success", lambda msg: self.log(msg))

            def exception(self):
                return None

            def log(self, msg):
                self.logged = msg

        # Failure result
        class DummyFutureFail:
            def result(self):
                return (False, "fail", lambda msg: self.log(msg))

            def exception(self):
                return None

            def log(self, msg):
                self.logged = msg

        # Exception result
        class DummyFutureException:
            def result(self):
                return (False, "fail", lambda msg: self.log(msg))

            def exception(self):
                return Exception("fail-exc")

            def log(self, msg):
                self.logged = msg

        futures = [DummyFuture(), DummyFutureFail(), DummyFutureException()]
        # Patch as_completed to just yield the futures
        with patch("webhook_server.utils.helpers.as_completed", return_value=futures):
            get_future_results(futures)

    @pytest.mark.parametrize(
        "text,max_length,expected_contains,assertion_msg",
        [
            pytest.param(
                "This is a short text",
                500,
                None,
                "Short text should not be truncated",
                id="short_text",
            ),
            pytest.param(
                "a" * 500,
                500,
                None,
                "Text at exact max_length should not be truncated",
                id="exact_length",
            ),
            pytest.param(
                "a" * 1000,
                500,
                "... [truncated 500 chars]",
                "Should include truncation message with char count",
                id="long_text",
            ),
            pytest.param(
                "a" * 200,
                100,
                "... [truncated 100 chars]",
                "Should show correct truncation count",
                id="custom_max_length",
            ),
            pytest.param(
                "line1\nline2\nline3\n" * 100,
                100,
                "truncated",
                "Should include truncation indicator",
                id="multiline_text",
            ),
        ],
    )
    def test_truncate_output(self, text: str, max_length: int, expected_contains: str | None, assertion_msg: str):
        """Test _truncate_output with various input sizes and configurations."""
        result = _truncate_output(text, max_length=max_length)

        if expected_contains is None:
            # Text should not be truncated
            assert result == text, assertion_msg
        else:
            # Text should be truncated
            assert expected_contains in result, assertion_msg
            if max_length < len(text):
                assert len(result) < len(text), "Truncated text should be shorter than original"
                assert result.startswith(text[:max_length]), f"Should start with first {max_length} chars"

    @pytest.mark.asyncio
    async def test_run_command_truncates_long_output_in_logs(self):
        """Test that run_command truncates long output in error logs."""
        # Create a command that will fail with very long output
        long_text = "a" * 1000
        # Use sys.exit() instead of exit() for reliability
        command = f'{sys.executable} -c "print(\\"{long_text}\\"); import sys; sys.exit(1)"'

        with patch("webhook_server.utils.helpers.get_logger_with_params") as mock_get_logger:
            mock_logger = Mock()
            mock_get_logger.return_value = mock_logger

            result = await run_command(command, log_prefix="[TEST]", redact_secrets=[])

            # Verify command failed
            assert result[0] is False

            # Verify error was logged
            assert mock_logger.error.called, "Error should be logged for failed command"

            # Get the logged error message
            error_msg = mock_logger.error.call_args[0][0]

            # Verify the error message is truncated (contains truncation indicator)
            assert "truncated" in error_msg, "Error log should contain truncation indicator"
            assert len(error_msg) < 2000, "Error message should be truncated to reasonable length"

    @pytest.mark.asyncio
    async def test_run_command_returns_full_output_despite_log_truncation(self):
        """Test that run_command returns full output even though logs are truncated."""
        # Create a command that will fail with long output
        long_text = "a" * 1000
        # Use sys.exit() instead of exit() for reliability
        command = f'{sys.executable} -c "print(\\"{long_text}\\"); import sys; sys.exit(1)"'

        result = await run_command(command, log_prefix="[TEST]", redact_secrets=[])

        # Verify command failed
        assert result[0] is False

        # CRITICAL: Verify the returned stdout contains the FULL output (not truncated)
        assert long_text in result[1], "Return value should contain full output, not truncated"
        assert len(result[1]) >= 1000, "Return value should have full length output"
