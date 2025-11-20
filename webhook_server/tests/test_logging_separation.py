from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import webhook_server.app
from webhook_server.app import get_log_viewer_controller, lifespan
from webhook_server.libs.config import Config
from webhook_server.utils.helpers import get_log_file_path


def test_get_log_file_path_absolute():
    config = MagicMock(spec=Config)
    config.data_dir = "/tmp/data"
    path = get_log_file_path(config, "/absolute/path.log")
    assert path == "/absolute/path.log"


def test_get_log_file_path_relative():
    config = MagicMock(spec=Config)
    config.data_dir = "/tmp/data"
    # Mock os.makedirs to avoid filesystem side effects
    with patch("os.makedirs") as mock_makedirs:
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

    with (
        patch("webhook_server.app.Config") as MockConfig,
        patch("webhook_server.app.MCP_SERVER_ENABLED", True),
        patch("webhook_server.app.logging.getLogger") as mock_get_logger,
        patch("webhook_server.app.get_logger_with_params") as mock_get_logger_params,
        patch("webhook_server.app.get_github_allowlist", new_callable=AsyncMock),
        patch("webhook_server.app.get_cloudflare_allowlist", new_callable=AsyncMock),
        patch("webhook_server.app.httpx.AsyncClient") as MockClient,
        patch("webhook_server.app.LOGGER"),
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
        mock_config_instance.get_value.side_effect = (
            lambda value, return_on_none=None: "logs_server.log" if value == "logs-server-log-file" else return_on_none
        )

        # Setup logger
        mock_logger = MagicMock()
        mock_get_logger_params.return_value = mock_logger

        # Call function
        get_log_viewer_controller()

        # Verify
        mock_get_logger_params.assert_called_with(log_file_name="logs_server.log")
        MockController.assert_called_with(logger=mock_logger)
