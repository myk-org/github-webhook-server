"""Tests for webhook_server.utils.github_retry module."""

import asyncio
import logging
from unittest.mock import AsyncMock, Mock, patch

import pytest
from github.GithubException import BadCredentialsException, GithubException, UnknownObjectException
from requests.exceptions import ConnectionError as RequestsConnectionError
from urllib3.exceptions import MaxRetryError, ResponseError

from webhook_server.utils.github_retry import _is_retryable, github_api_call

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LOG_PREFIX = "[test/repo #1]"


@pytest.fixture()
def mock_logger() -> logging.Logger:
    """Provide a mock logger for github_api_call tests."""
    return Mock(spec=logging.Logger)


# ---------------------------------------------------------------------------
# _is_retryable unit tests
# ---------------------------------------------------------------------------


class TestIsRetryable:
    def test_github_500_is_retryable(self):
        ex = GithubException(status=500, data={"message": "Internal Server Error"})
        assert _is_retryable(ex) is True

    def test_github_502_is_retryable(self):
        ex = GithubException(status=502, data={"message": "Bad Gateway"})
        assert _is_retryable(ex) is True

    def test_github_503_is_retryable(self):
        ex = GithubException(status=503, data={"message": "Service Unavailable"})
        assert _is_retryable(ex) is True

    def test_github_exception_504_retryable(self):
        ex = GithubException(status=504, data={"message": "Gateway Timeout"})
        assert _is_retryable(ex) is True

    def test_github_404_not_retryable(self):
        ex = UnknownObjectException(status=404, data={"message": "Not Found"})
        assert _is_retryable(ex) is False

    def test_github_401_not_retryable(self):
        ex = BadCredentialsException(status=401, data={"message": "Bad credentials"})
        assert _is_retryable(ex) is False

    def test_github_403_not_retryable(self):
        ex = GithubException(status=403, data={"message": "Forbidden"})
        assert _is_retryable(ex) is False

    def test_github_422_not_retryable(self):
        ex = GithubException(status=422, data={"message": "Unprocessable Entity"})
        assert _is_retryable(ex) is False

    def test_github_unknown_status_not_retryable(self):
        ex = GithubException(status=418, data={"message": "I'm a teapot"})
        assert _is_retryable(ex) is False

    def test_requests_connection_error_is_retryable(self):
        ex = RequestsConnectionError("Connection refused")
        assert _is_retryable(ex) is True

    def test_urllib3_max_retry_error_is_retryable(self):
        ex = MaxRetryError(pool=Mock(), url="https://api.github.com")
        assert _is_retryable(ex) is True

    def test_exception_with_500_error_responses_substring(self):
        ex = Exception("Got 500 error responses from server")
        assert _is_retryable(ex) is True

    def test_exception_with_max_retries_exceeded_substring(self):
        ex = Exception("Max retries exceeded with url: /repos/org/repo")
        assert _is_retryable(ex) is True

    def test_generic_value_error_not_retryable(self):
        ex = ValueError("something went wrong")
        assert _is_retryable(ex) is False

    def test_generic_runtime_error_not_retryable(self):
        ex = RuntimeError("unexpected failure")
        assert _is_retryable(ex) is False

    def test_urllib3_response_error_is_retryable(self):
        ex = ResponseError("connection reset by peer")
        assert _is_retryable(ex) is True


# ---------------------------------------------------------------------------
# github_api_call tests
# ---------------------------------------------------------------------------


class TestGithubApiCall:
    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_successful_call_first_attempt(self, mock_sleep, mock_logger):
        func = Mock(return_value=42)
        result = await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        assert result == 42
        func.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_successful_call_after_transient_failure(self, mock_sleep, mock_logger):
        func = Mock(
            side_effect=[
                GithubException(status=500, data={"message": "Internal Server Error"}),
                "success",
            ]
        )
        result = await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        assert result == "success"
        assert func.call_count == 2
        mock_sleep.assert_called_once_with(2)

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_on_github_500(self, mock_sleep, mock_logger):
        ex = GithubException(status=500, data={"message": "Internal Server Error"})
        func = Mock(side_effect=[ex, ex, "ok"])
        result = await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        assert result == "ok"
        assert func.call_count == 3

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_on_github_502(self, mock_sleep, mock_logger):
        ex = GithubException(status=502, data={"message": "Bad Gateway"})
        func = Mock(side_effect=[ex, "ok"])
        result = await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        assert result == "ok"
        assert func.call_count == 2

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_on_github_503(self, mock_sleep, mock_logger):
        ex = GithubException(status=503, data={"message": "Service Unavailable"})
        func = Mock(side_effect=[ex, "ok"])
        result = await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        assert result == "ok"
        assert func.call_count == 2

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_on_github_exception_504(self, mock_sleep, mock_logger):
        ex = GithubException(status=504, data={"message": "Gateway Timeout"})
        func = Mock(side_effect=[ex, "ok"])
        result = await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        assert result == "ok"
        assert func.call_count == 2

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_no_retry_on_404(self, mock_sleep, mock_logger):
        ex = UnknownObjectException(status=404, data={"message": "Not Found"})
        func = Mock(side_effect=ex)
        with pytest.raises(UnknownObjectException):
            await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        func.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_no_retry_on_401(self, mock_sleep, mock_logger):
        ex = BadCredentialsException(status=401, data={"message": "Bad credentials"})
        func = Mock(side_effect=ex)
        with pytest.raises(BadCredentialsException):
            await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        func.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_no_retry_on_403(self, mock_sleep, mock_logger):
        ex = GithubException(status=403, data={"message": "Forbidden"})
        func = Mock(side_effect=ex)
        with pytest.raises(GithubException):
            await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        func.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_no_retry_on_422(self, mock_sleep, mock_logger):
        ex = GithubException(status=422, data={"message": "Unprocessable Entity"})
        func = Mock(side_effect=ex)
        with pytest.raises(GithubException):
            await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        func.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_on_requests_connection_error(self, mock_sleep, mock_logger):
        ex = RequestsConnectionError("Connection refused")
        func = Mock(side_effect=[ex, "ok"])
        result = await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        assert result == "ok"
        assert func.call_count == 2

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_on_max_retry_error(self, mock_sleep, mock_logger):
        ex = MaxRetryError(pool=Mock(), url="https://api.github.com")
        func = Mock(side_effect=[ex, "ok"])
        result = await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        assert result == "ok"
        assert func.call_count == 2

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_on_500_error_responses_message(self, mock_sleep, mock_logger):
        ex = Exception("Got 500 error responses from server")
        func = Mock(side_effect=[ex, "ok"])
        result = await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        assert result == "ok"
        assert func.call_count == 2

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_on_max_retries_exceeded_message(self, mock_sleep, mock_logger):
        ex = Exception("Max retries exceeded with url: /repos/org/repo")
        func = Mock(side_effect=[ex, "ok"])
        result = await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        assert result == "ok"
        assert func.call_count == 2

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_max_retries_exhausted(self, mock_sleep, mock_logger):
        ex = GithubException(status=500, data={"message": "Internal Server Error"})
        func = Mock(side_effect=ex)
        with pytest.raises(GithubException, match="Internal Server Error"):
            await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        # _MAX_RETRIES + 1 total attempts = 5
        assert func.call_count == 5
        assert mock_sleep.call_count == 4

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_cancelled_error_always_reraised(self, mock_sleep, mock_logger):
        func = Mock(side_effect=asyncio.CancelledError)
        with pytest.raises(asyncio.CancelledError):
            await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        func.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_lambda_calls_work(self, mock_sleep, mock_logger):
        result = await github_api_call(lambda: "lambda_value", logger=mock_logger, log_prefix=LOG_PREFIX)
        assert result == "lambda_value"
        mock_sleep.assert_not_called()

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_kwargs_are_forwarded(self, mock_sleep, mock_logger):
        func = Mock(return_value="done")
        result = await github_api_call(
            func, "pos_arg", logger=mock_logger, log_prefix=LOG_PREFIX, key1="val1", key2="val2"
        )
        assert result == "done"
        func.assert_called_once_with("pos_arg", key1="val1", key2="val2")

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_exponential_backoff_timing(self, mock_sleep, mock_logger):
        ex = GithubException(status=500, data={"message": "Internal Server Error"})
        func = Mock(side_effect=ex)
        with pytest.raises(GithubException):
            await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)

        # Delays: 2*2^0=2, 2*2^1=4, 2*2^2=8, 2*2^3=16
        sleep_delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert sleep_delays == [2, 4, 8, 16]

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_warning_logged_on_each_retry(self, mock_sleep, mock_logger):
        ex = GithubException(status=500, data={"message": "Internal Server Error"})
        func = Mock(side_effect=[ex, ex, "ok"])

        result = await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)

        assert result == "ok"
        assert mock_logger.warning.call_count == 2
        # Verify attempt numbers in log messages
        first_call_args = mock_logger.warning.call_args_list[0]
        assert first_call_args[0][1] == f"{LOG_PREFIX} "  # log_prefix
        assert first_call_args[0][2] == 1  # attempt 1
        assert first_call_args[0][3] == 5  # total attempts (MAX_RETRIES + 1)
        second_call_args = mock_logger.warning.call_args_list[1]
        assert second_call_args[0][2] == 2  # attempt 2

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_on_response_error(self, mock_sleep, mock_logger):
        ex = ResponseError("too many 500 error responses")
        func = Mock(side_effect=[ex, "ok"])
        result = await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        assert result == "ok"
        assert func.call_count == 2

    @patch("webhook_server.utils.github_retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_non_retryable_generic_exception(self, mock_sleep, mock_logger):
        func = Mock(side_effect=ValueError("bad value"))
        with pytest.raises(ValueError, match="bad value"):
            await github_api_call(func, logger=mock_logger, log_prefix=LOG_PREFIX)
        func.assert_called_once()
        mock_sleep.assert_not_called()
