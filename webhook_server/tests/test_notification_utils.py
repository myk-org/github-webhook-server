"""Tests for notification_utils module."""

from unittest.mock import Mock, patch

import pytest
import requests

from webhook_server.utils.notification_utils import send_slack_message


class TestSendSlackMessage:
    """Test suite for send_slack_message function."""

    @pytest.fixture
    def mock_logger(self) -> Mock:
        """Create a mock logger."""
        return Mock()

    @pytest.fixture
    def webhook_url(self) -> str:
        """Slack webhook URL for testing."""
        return "https://hooks.slack.com/services/TEST/WEBHOOK/URL"  # pragma: allowlist secret

    @patch("webhook_server.utils.notification_utils.requests.post")
    def test_successful_message_send(self, mock_post: Mock, webhook_url: str, mock_logger: Mock) -> None:
        """Test successful Slack message send with 200 response."""
        mock_response = Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        send_slack_message(
            message="Test notification",
            webhook_url=webhook_url,
            logger=mock_logger,
            log_prefix="[TEST]",
        )

        mock_logger.info.assert_called_once_with("[TEST] Sending message to slack: Test notification")
        mock_post.assert_called_once()

        # Verify the call arguments
        call_args = mock_post.call_args
        assert call_args.kwargs["timeout"] == 10
        assert call_args.kwargs["headers"] == {"Content-Type": "application/json"}
        assert '"text": "Test notification"' in call_args.kwargs["data"]

    @patch("webhook_server.utils.notification_utils.requests.post")
    def test_message_send_with_500_error(self, mock_post: Mock, webhook_url: str, mock_logger: Mock) -> None:
        """Test Slack message send with 500 server error."""
        mock_response = Mock(spec=requests.Response)
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_post.return_value = mock_response

        with pytest.raises(ValueError) as exc_info:
            send_slack_message(
                message="Test message",
                webhook_url=webhook_url,
                logger=mock_logger,
            )

        assert "Request to slack returned an error 500" in str(exc_info.value)
        assert "Internal Server Error" in str(exc_info.value)

    @patch("webhook_server.utils.notification_utils.requests.post")
    def test_message_send_with_404_error(self, mock_post: Mock, webhook_url: str, mock_logger: Mock) -> None:
        """Test Slack message send with 404 not found error."""
        mock_response = Mock(spec=requests.Response)
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_post.return_value = mock_response

        with pytest.raises(ValueError) as exc_info:
            send_slack_message(
                message="Test message",
                webhook_url=webhook_url,
                logger=mock_logger,
            )

        assert "Request to slack returned an error 404" in str(exc_info.value)
        assert "Not Found" in str(exc_info.value)

    @patch("webhook_server.utils.notification_utils.requests.post")
    def test_message_send_with_401_unauthorized(self, mock_post: Mock, webhook_url: str, mock_logger: Mock) -> None:
        """Test Slack message send with 401 unauthorized error."""
        mock_response = Mock(spec=requests.Response)
        mock_response.status_code = 401
        mock_response.text = "Unauthorized - Invalid token"
        mock_post.return_value = mock_response

        with pytest.raises(ValueError) as exc_info:
            send_slack_message(
                message="Confidential alert",
                webhook_url=webhook_url,
                logger=mock_logger,
            )

        assert "Request to slack returned an error 401" in str(exc_info.value)
        assert "Unauthorized - Invalid token" in str(exc_info.value)

    @patch("webhook_server.utils.notification_utils.requests.post")
    def test_message_send_with_timeout(self, mock_post: Mock, webhook_url: str, mock_logger: Mock) -> None:
        """Test Slack message send with connection timeout."""
        mock_post.side_effect = requests.exceptions.Timeout("Connection timeout")

        with pytest.raises(requests.exceptions.Timeout):
            send_slack_message(
                message="Test message",
                webhook_url=webhook_url,
                logger=mock_logger,
            )

        mock_post.assert_called_once()

    @patch("webhook_server.utils.notification_utils.requests.post")
    def test_message_send_with_connection_error(self, mock_post: Mock, webhook_url: str, mock_logger: Mock) -> None:
        """Test Slack message send with connection error."""
        mock_post.side_effect = requests.exceptions.ConnectionError("Failed to establish connection")

        with pytest.raises(requests.exceptions.ConnectionError):
            send_slack_message(
                message="Test message",
                webhook_url=webhook_url,
                logger=mock_logger,
            )

    @patch("webhook_server.utils.notification_utils.requests.post")
    def test_empty_message(self, mock_post: Mock, webhook_url: str, mock_logger: Mock) -> None:
        """Test sending empty message."""
        mock_response = Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        send_slack_message(
            message="",
            webhook_url=webhook_url,
            logger=mock_logger,
        )

        mock_logger.info.assert_called_once()
        assert '"text": ""' in mock_post.call_args.kwargs["data"]

    @patch("webhook_server.utils.notification_utils.requests.post")
    def test_message_with_special_characters(self, mock_post: Mock, webhook_url: str, mock_logger: Mock) -> None:
        """Test message with special characters and emoji."""
        mock_response = Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        message = 'Build failed! 🔥\nError: "timeout"\n\tDetails: \\n\\t'
        send_slack_message(
            message=message,
            webhook_url=webhook_url,
            logger=mock_logger,
        )

        mock_post.assert_called_once()

    @patch("webhook_server.utils.notification_utils.requests.post")
    def test_message_with_json_characters(self, mock_post: Mock, webhook_url: str, mock_logger: Mock) -> None:
        """Test message with JSON special characters."""
        mock_response = Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        message = '{"key": "value", "nested": {"data": true}}'
        send_slack_message(
            message=message,
            webhook_url=webhook_url,
            logger=mock_logger,
        )

        mock_post.assert_called_once()

    @patch("webhook_server.utils.notification_utils.requests.post")
    def test_long_message(self, mock_post: Mock, webhook_url: str, mock_logger: Mock) -> None:
        """Test sending very long message."""
        mock_response = Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        long_message = "A" * 10000
        send_slack_message(
            message=long_message,
            webhook_url=webhook_url,
            logger=mock_logger,
        )

        mock_post.assert_called_once()

    @patch("webhook_server.utils.notification_utils.requests.post")
    def test_without_log_prefix(self, mock_post: Mock, webhook_url: str, mock_logger: Mock) -> None:
        """Test message send without log prefix (uses empty string by default)."""
        mock_response = Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        send_slack_message(
            message="Test message",
            webhook_url=webhook_url,
            logger=mock_logger,
        )

        mock_logger.info.assert_called_once_with(" Sending message to slack: Test message")

    @patch("webhook_server.utils.notification_utils.requests.post")
    def test_request_headers_correct(self, mock_post: Mock, webhook_url: str, mock_logger: Mock) -> None:
        """Test that request includes correct headers."""
        mock_response = Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        send_slack_message(
            message="Test",
            webhook_url=webhook_url,
            logger=mock_logger,
        )

        assert mock_post.call_args.kwargs["headers"]["Content-Type"] == "application/json"

    @patch("webhook_server.utils.notification_utils.requests.post")
    def test_request_timeout_value(self, mock_post: Mock, webhook_url: str, mock_logger: Mock) -> None:
        """Test that request uses correct timeout value."""
        mock_response = Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        send_slack_message(
            message="Test",
            webhook_url=webhook_url,
            logger=mock_logger,
        )

        assert mock_post.call_args.kwargs["timeout"] == 10

    @patch("webhook_server.utils.notification_utils.requests.post")
    def test_invalid_webhook_url(self, mock_post: Mock, mock_logger: Mock) -> None:
        """Test with malformed webhook URL."""
        mock_response = Mock(spec=requests.Response)
        mock_response.status_code = 400
        mock_response.text = "Invalid URL"
        mock_post.return_value = mock_response

        with pytest.raises(ValueError):
            send_slack_message(
                message="Test",
                webhook_url="not-a-valid-url",
                logger=mock_logger,
            )

    @patch("webhook_server.utils.notification_utils.requests.post")
    def test_multiline_message(self, mock_post: Mock, webhook_url: str, mock_logger: Mock) -> None:
        """Test multiline message formatting."""
        mock_response = Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        message = """Line 1
Line 2
Line 3"""
        send_slack_message(
            message=message,
            webhook_url=webhook_url,
            logger=mock_logger,
        )

        mock_post.assert_called_once()

    @patch("webhook_server.utils.notification_utils.requests.post")
    def test_message_with_unicode(self, mock_post: Mock, webhook_url: str, mock_logger: Mock) -> None:
        """Test message with Unicode characters."""
        mock_response = Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        message = "Test 测试 тест ทดสอบ 🚀"
        send_slack_message(
            message=message,
            webhook_url=webhook_url,
            logger=mock_logger,
        )

        mock_post.assert_called_once()
