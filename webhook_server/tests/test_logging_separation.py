import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import webhook_server.app
from webhook_server.app import get_log_viewer_controller, lifespan
from webhook_server.libs.config import Config
from webhook_server.libs.log_parser import LogEntry
from webhook_server.utils.helpers import get_log_file_path, get_logger_with_params
from webhook_server.utils.json_log_handler import JsonLogHandler
from webhook_server.web.log_viewer import LogViewerController


def test_get_log_file_path_absolute():
    config = MagicMock(spec=Config)
    config.data_dir = "/tmp/data"
    path = get_log_file_path(config, "/absolute/path.log")
    assert path == "/absolute/path.log"


def test_get_log_file_path_relative():
    config = MagicMock(spec=Config)
    config.data_dir = "/tmp/data"
    # Mock os.path.isdir to force makedirs path, and os.makedirs to avoid filesystem side effects
    with patch("os.path.isdir", return_value=False), patch("os.makedirs") as mock_makedirs:
        path = get_log_file_path(config, "server.log")
        assert path == "/tmp/data/logs/server.log"
        mock_makedirs.assert_called_once_with("/tmp/data/logs", exist_ok=True)


def test_get_log_file_path_none():
    config = MagicMock(spec=Config)
    path = get_log_file_path(config, None)
    assert path is None


@pytest.mark.asyncio
async def test_mcp_logging_configuration():
    # Mock dependencies
    mock_app = MagicMock()

    # Mock asyncio.wait to return immediately (prevents 30-second shutdown timeout)
    async def mock_wait(tasks, timeout=None, return_when=None):
        # Cancel all tasks immediately to prevent blocking
        for task in tasks:
            task.cancel()
        # Return empty done set and the tasks as pending
        return set(), tasks

    with (
        patch("webhook_server.app.Config") as MockConfig,
        patch("webhook_server.app.MCP_SERVER_ENABLED", True),
        patch("webhook_server.app.logging.getLogger") as mock_get_logger,
        patch("webhook_server.app.get_logger_with_params") as mock_get_logger_params,
        patch("webhook_server.app.get_github_allowlist", new_callable=AsyncMock),
        patch("webhook_server.app.get_cloudflare_allowlist", new_callable=AsyncMock),
        patch("webhook_server.app.httpx.AsyncClient") as MockClient,
        patch("webhook_server.app.LOGGER"),
        patch("webhook_server.app.asyncio.wait", side_effect=mock_wait),
    ):
        # Setup mocks
        mock_config_instance = MockConfig.return_value
        mock_config_instance.root_data = {"mcp-log-file": "mcp_server.log", "verify-github-ips": False}

        mock_client_instance = MockClient.return_value
        mock_client_instance.aclose = AsyncMock()

        mcp_logger = MagicMock()
        mcp_logger.filters = []
        mock_get_logger.return_value = mcp_logger

        # Mock the logger returned by get_logger_with_params
        mcp_file_logger = MagicMock()
        mcp_handler = MagicMock()
        mcp_file_logger.handlers = [mcp_handler]
        mcp_file_logger.name = "mcp_logger"
        mock_get_logger_params.return_value = mcp_file_logger

        # Run lifespan
        async with lifespan(mock_app):
            pass

        # Verify configuration
        # Check if get_logger_with_params was called with correct log file
        mock_get_logger_params.assert_any_call(log_file_name="mcp_server.log")

        # Check if handler was added to the main MCP logger
        mcp_logger.addHandler.assert_called_with(mcp_handler)

        # Check if propagation was disabled
        assert mcp_logger.propagate is False


def test_log_viewer_controller_logging_separation():
    with (
        patch("webhook_server.app.Config") as MockConfig,
        patch("webhook_server.app.get_logger_with_params") as mock_get_logger_params,
        patch("webhook_server.app.LogViewerController") as MockController,
        patch("webhook_server.app.LOGGER"),
    ):
        # Reset singleton
        webhook_server.app._log_viewer_controller_singleton = None

        # Setup config
        mock_config_instance = MockConfig.return_value
        mock_config_instance.get_value.side_effect = lambda value, return_on_none=None: (
            "logs_server.log" if value == "logs-server-log-file" else return_on_none
        )

        # Setup logger
        mock_logger = MagicMock()
        mock_get_logger_params.return_value = mock_logger

        # Call function
        get_log_viewer_controller()

        # Verify
        mock_get_logger_params.assert_called_with(log_file_name="logs_server.log")
        MockController.assert_called_with(logger=mock_logger)


class TestJsonLogHandlerNotAttachedForInfrastructureLoggers:
    """Verify that JsonLogHandler is NOT attached when log_file_name is explicit.

    Infrastructure loggers (MCP server, log viewer) use explicit log_file_name
    parameters. They must not write to webhooks_*.json to avoid polluting
    webhook log queries with noise entries.
    """

    def test_no_json_handler_when_log_file_name_explicit(self) -> None:
        """get_logger_with_params with explicit log_file_name must skip JsonLogHandler."""
        with (
            patch("webhook_server.utils.helpers.Config") as MockConfig,
            patch("webhook_server.utils.helpers.get_logger") as mock_get_logger,
        ):
            mock_config_instance = MockConfig.return_value
            mock_config_instance.data_dir = "/tmp/data"
            mock_config_instance.get_value.side_effect = lambda value, return_on_none=None: return_on_none

            mock_logger = MagicMock()
            mock_logger.handlers = []
            mock_get_logger.return_value = mock_logger

            get_logger_with_params(log_file_name="mcp_server.log")

            # JsonLogHandler must NOT be added
            mock_logger.addHandler.assert_not_called()

    def test_json_handler_attached_when_log_file_name_default(self) -> None:
        """get_logger_with_params without log_file_name must attach JsonLogHandler."""
        with (
            patch("webhook_server.utils.helpers.Config") as MockConfig,
            patch("webhook_server.utils.helpers.get_logger") as mock_get_logger,
            patch("os.path.isdir", return_value=False),
            patch("os.makedirs"),
        ):
            mock_config_instance = MockConfig.return_value
            mock_config_instance.data_dir = "/tmp/data"
            # Return a log file config so a file handler exists
            mock_config_instance.get_value.side_effect = lambda value, return_on_none=None: (
                "webhook_server.log" if value == "log-file" else return_on_none
            )

            mock_logger = MagicMock()
            mock_logger.handlers = []  # No existing handlers
            mock_get_logger.return_value = mock_logger

            get_logger_with_params()  # No explicit log_file_name

            # JsonLogHandler MUST be added
            mock_logger.addHandler.assert_called_once()

            added_handler = mock_logger.addHandler.call_args[0][0]
            assert isinstance(added_handler, JsonLogHandler)


class TestIsInfrastructureNoise:
    """Verify _is_infrastructure_noise correctly identifies noise entries."""

    @staticmethod
    def _make_entry(logger_name: str, hook_id: str | None = None) -> LogEntry:
        return LogEntry(
            timestamp=datetime.datetime.now(tz=datetime.UTC),
            level="INFO",
            logger_name=logger_name,
            message="test message",
            hook_id=hook_id,
        )

    def test_mcp_logger_without_hook_id_is_noise(self) -> None:
        entry = self._make_entry("mcp.server.streamable_http", hook_id=None)
        assert LogViewerController._is_infrastructure_noise(entry) is True

    def test_logs_server_logger_without_hook_id_is_noise(self) -> None:
        entry = self._make_entry("logs_server.log", hook_id=None)
        assert LogViewerController._is_infrastructure_noise(entry) is True

    def test_mcp_server_log_without_hook_id_is_noise(self) -> None:
        entry = self._make_entry("mcp_server.log", hook_id=None)
        assert LogViewerController._is_infrastructure_noise(entry) is True

    def test_log_parser_without_hook_id_is_noise(self) -> None:
        entry = self._make_entry("log_parser", hook_id=None)
        assert LogViewerController._is_infrastructure_noise(entry) is True

    def test_infra_logger_with_hook_id_is_not_noise(self) -> None:
        """Infrastructure logger entry WITH hook_id should be preserved."""
        entry = self._make_entry("mcp.server.streamable_http", hook_id="abc-123")
        assert LogViewerController._is_infrastructure_noise(entry) is False

    def test_webhook_logger_without_hook_id_is_not_noise(self) -> None:
        """Non-infrastructure logger without hook_id is kept (could be startup log)."""
        entry = self._make_entry("GithubWebhook", hook_id=None)
        assert LogViewerController._is_infrastructure_noise(entry) is False

    def test_webhook_logger_with_hook_id_is_not_noise(self) -> None:
        entry = self._make_entry("GithubWebhook", hook_id="abc-123")
        assert LogViewerController._is_infrastructure_noise(entry) is False
