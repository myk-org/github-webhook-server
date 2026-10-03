import asyncio
import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx
import pytest
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from webhook_server import app as app_module
from webhook_server.app import (
    FASTAPI_APP,
    HTTPException,
    get_log_viewer_controller,
    healthcheck,
    require_log_server_enabled,
    status,
    websocket_log_stream,
)
from webhook_server.libs.exceptions import RepositoryNotFoundInConfigError
from webhook_server.utils.app_utils import (
    gate_by_allowlist_ips,
    get_cloudflare_allowlist,
    get_github_allowlist,
)
from webhook_server.utils.context import get_context


class TestWebhookApp:
    """Comprehensive tests for the main FastAPI webhook application."""

    @pytest.fixture
    def client(self) -> TestClient:
        """FastAPI test client."""
        return TestClient(FASTAPI_APP)

    @pytest.fixture(autouse=True)
    def reset_allowed_ips(self):
        """Ensure ALLOWED_IPS is empty to avoid IP gating issues during tests."""
        with patch("webhook_server.app.ALLOWED_IPS", ()):
            yield

    @pytest.fixture
    def valid_webhook_payload(self) -> dict[str, Any]:
        """Valid webhook payload for testing."""
        return {
            "repository": {"name": "test-repo", "full_name": "my-org/test-repo"},
            "action": "opened",
            "pull_request": {"number": 123, "title": "Test PR"},
        }

    @pytest.fixture
    def webhook_secret(self) -> str:
        """Test webhook secret."""
        return "test-webhook-secret"

    def create_github_signature(self, payload: str, secret: str) -> str:
        """Create a valid GitHub webhook signature."""
        signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return f"sha256={signature}"

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    def test_healthcheck_endpoint(self, client: TestClient) -> None:
        """Test the healthcheck endpoint returns success."""
        response = client.get("/webhook_server/healthcheck")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == 200
        assert data["message"] == "Alive"

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.app.GithubWebhook")
    def test_process_webhook_success(
        self, mock_github_webhook: Mock, client: TestClient, valid_webhook_payload: dict[str, Any], webhook_secret: str
    ) -> None:
        """Test successful webhook processing."""
        payload_json = json.dumps(valid_webhook_payload)
        signature = self.create_github_signature(payload_json, webhook_secret)

        # Mock the GithubWebhook class
        mock_webhook_instance = Mock()
        mock_github_webhook.return_value = mock_webhook_instance

        headers = {
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "test-delivery-123",
            "x-hub-signature-256": signature,
            "Content-Type": "application/json",
        }

        response = client.post("/webhook_server", content=payload_json, headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == 200
        assert data["message"] == "Webhook queued for processing"
        assert data["delivery_id"] == "test-delivery-123"
        assert data["event_type"] == "pull_request"

    @patch("webhook_server.app.GithubWebhook")
    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    def test_payload_skip_acknowledges_without_constructing_client(
        self,
        mock_github_webhook: MagicMock,
        client: TestClient,
        webhook_secret: str,
        valid_webhook_payload: dict[str, Any],
    ) -> None:
        """A payload-only skip must cost zero API calls AND still be auditable.

        Skipping before construction is what saves the 4-5 core requests, so the test
        asserts the client was never built and no background work was scheduled - while
        the delivery is still acknowledged and written to the structured webhook log.
        """
        payload_json = json.dumps(valid_webhook_payload)
        signature = self.create_github_signature(payload_json, webhook_secret)

        headers = {
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "skip-delivery-abc",
            "x-hub-signature-256": signature,
            "Content-Type": "application/json",
        }

        with (
            patch("webhook_server.app.asyncio.create_task") as mock_create_task,
            patch("webhook_server.app.write_webhook_log") as mock_write_log,
        ):
            response = client.post("/webhook_server", content=payload_json, headers=headers)

        # Skipping must not cost observability: the delivery is acknowledged, so it has to
        # be findable by its ID in the log viewer like any other webhook.
        assert mock_write_log.call_count == 1
        skipped_ctx = mock_write_log.call_args.args[0]
        assert skipped_ctx.hook_id == "skip-delivery-abc"
        assert "ping" in skipped_ctx.note
        # The log viewer rejects a record with no workflow_steps as malformed, so a
        # step-less audit record would be written but never viewable.
        assert skipped_ctx.workflow_steps, "skipped record has no workflow steps"
        assert "skip_delivery" in skipped_ctx.workflow_steps

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == 200
        assert data["message"] == "Webhook acknowledged, no processing needed"
        assert data["skip_reason"] == "ping"
        assert data["delivery_id"] == "skip-delivery-abc"

        # The whole point: no client construction, no background task, no API spend.
        mock_github_webhook.assert_not_called()
        mock_create_task.assert_not_called()

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    def test_process_webhook_invalid_json(self, client: TestClient, webhook_secret: str) -> None:
        """Test webhook processing with invalid JSON payload."""
        payload = "invalid json"
        signature = self.create_github_signature(payload, webhook_secret)

        headers = {
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "test-delivery-123",
            "x-hub-signature-256": signature,
            "Content-Type": "application/json",
        }

        response = client.post("/webhook_server", content=payload, headers=headers)

        assert response.status_code == 400
        assert "Invalid JSON payload" in response.json()["detail"]

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    def test_process_webhook_missing_repository(self, client: TestClient, webhook_secret: str) -> None:
        """Test webhook processing with missing repository information."""
        payload = {"action": "opened"}
        payload_json = json.dumps(payload)
        signature = self.create_github_signature(payload_json, webhook_secret)

        headers = {
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "test-delivery-123",
            "x-hub-signature-256": signature,
            "Content-Type": "application/json",
        }

        response = client.post("/webhook_server", content=payload_json, headers=headers)

        assert response.status_code == 400
        assert "Missing repository in payload" in response.json()["detail"]

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.app.GithubWebhook")
    def test_process_webhook_repository_not_found(
        self, mock_github_webhook: Mock, client: TestClient, valid_webhook_payload: dict[str, Any], webhook_secret: str
    ) -> None:
        """Test webhook processing when repository is not found in config.

        Note: RepositoryNotFoundInConfigError is now handled in background task,
        so the HTTP response is 200 OK. The error is logged but doesn't affect
        the webhook response to prevent GitHub webhook timeouts.
        """
        # Mock GithubWebhook to raise RepositoryNotFoundError
        mock_github_webhook.side_effect = RepositoryNotFoundInConfigError("Repository not found in configuration")

        payload_json = json.dumps(valid_webhook_payload)
        signature = self.create_github_signature(payload_json, webhook_secret)

        headers = {
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "test-delivery-123",
            "x-hub-signature-256": signature,
            "Content-Type": "application/json",
        }

        response = client.post("/webhook_server", content=payload_json, headers=headers)

        # Returns 200 OK immediately - error is handled in background
        assert response.status_code == 200
        assert response.json()["message"] == "Webhook queued for processing"

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    def test_process_webhook_signature_verification_failure(
        self, client: TestClient, valid_webhook_payload: dict[str, Any]
    ) -> None:
        """Test webhook processing with signature verification failure."""
        payload_json = json.dumps(valid_webhook_payload)
        invalid_signature = "sha256=invalid"

        headers = {
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "test-delivery-123",
            "x-hub-signature-256": invalid_signature,
            "Content-Type": "application/json",
        }

        response = client.post("/webhook_server", content=payload_json, headers=headers)

        assert response.status_code == 403
        assert "Request signatures didn't match" in response.json()["detail"]

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.app.GithubWebhook")
    def test_process_webhook_connection_error(
        self, mock_github_webhook: Mock, client: TestClient, valid_webhook_payload: dict[str, Any], webhook_secret: str
    ) -> None:
        """Test webhook processing when connection error occurs.

        Note: Connection errors are now handled in background task,
        so the HTTP response is 200 OK. The error is logged but doesn't affect
        the webhook response to prevent GitHub webhook timeouts.
        """
        mock_github_webhook.side_effect = ConnectionError("API connection failed")

        payload_json = json.dumps(valid_webhook_payload)
        signature = self.create_github_signature(payload_json, webhook_secret)

        headers = {
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "test-delivery-123",
            "x-hub-signature-256": signature,
            "Content-Type": "application/json",
        }

        response = client.post("/webhook_server", content=payload_json, headers=headers)

        # Returns 200 OK immediately - error is handled in background
        assert response.status_code == 200
        assert response.json()["message"] == "Webhook queued for processing"

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.app.GithubWebhook")
    def test_process_webhook_unexpected_error(
        self, mock_github_webhook: Mock, client: TestClient, valid_webhook_payload: dict[str, Any], webhook_secret: str
    ) -> None:
        """Test webhook processing when unexpected error occurs.

        Note: Unexpected errors are now handled in background task,
        so the HTTP response is 200 OK. The error is logged but doesn't affect
        the webhook response to prevent GitHub webhook timeouts.
        """
        mock_github_webhook.side_effect = Exception("Unexpected error")

        payload_json = json.dumps(valid_webhook_payload)
        signature = self.create_github_signature(payload_json, webhook_secret)

        headers = {
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "test-delivery-123",
            "x-hub-signature-256": signature,
            "Content-Type": "application/json",
        }

        response = client.post("/webhook_server", content=payload_json, headers=headers)

        # Returns 200 OK immediately - error is handled in background
        assert response.status_code == 200
        assert response.json()["message"] == "Webhook queued for processing"

    @patch("webhook_server.app.get_github_allowlist")
    @patch("webhook_server.app.get_cloudflare_allowlist")
    async def test_ip_allowlist_functionality(self, mock_cf_allowlist: Mock, mock_gh_allowlist: Mock) -> None:
        """Test IP allowlist functionality."""
        # Mock allowlist responses
        mock_gh_allowlist.return_value = ["192.30.252.0/22", "185.199.108.0/22"]
        mock_cf_allowlist.return_value = ["103.21.244.0/22", "2400:cb00::/32"]

        # Test that the allowlists are fetched correctly
        result = await mock_gh_allowlist()
        assert "192.30.252.0/22" in result
        assert "185.199.108.0/22" in result

        result = await mock_cf_allowlist()
        assert "103.21.244.0/22" in result
        assert "2400:cb00::/32" in result

    @patch("httpx.AsyncClient.get")
    async def test_get_github_allowlist_success(self, mock_get: Mock) -> None:
        """Test successful GitHub allowlist fetching."""
        mock_response = Mock()
        mock_response.json.return_value = {"hooks": ["192.30.252.0/22", "185.199.108.0/22"]}
        mock_response.raise_for_status.return_value = None
        # Use AsyncMock for the client
        async_client = AsyncMock()
        async_client.get.return_value = mock_response

        result = await get_github_allowlist(async_client)
        assert result == ["192.30.252.0/22", "185.199.108.0/22"]
        async_client.get.assert_called_once()

    @patch("httpx.AsyncClient.get")
    async def test_get_github_allowlist_error(self, mock_get: Mock) -> None:
        """Test GitHub allowlist fetching with error."""
        async_client = AsyncMock()
        async_client.get.side_effect = httpx.RequestError("Network error")

        with pytest.raises(httpx.RequestError):
            await get_github_allowlist(async_client)

    @patch("httpx.AsyncClient.get")
    async def test_get_cloudflare_allowlist_success(self, mock_get: Mock) -> None:
        """Test successful Cloudflare allowlist fetching."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "result": {"ipv4_cidrs": ["103.21.244.0/22"], "ipv6_cidrs": ["2400:cb00::/32"]}
        }
        mock_response.raise_for_status.return_value = None
        async_client = AsyncMock()
        async_client.get.return_value = mock_response

        result = await get_cloudflare_allowlist(async_client)
        assert result == ["103.21.244.0/22", "2400:cb00::/32"]
        async_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_gate_by_allowlist_ips_allowed(self) -> None:
        """Test gate_by_allowlist_ips with allowed IP."""
        allowed_ips = (ipaddress.ip_network("127.0.0.1/32"),)

        class DummyRequest:
            client = type("client", (), {"host": "127.0.0.1"})()

        await gate_by_allowlist_ips(DummyRequest(), allowed_ips)  # type: ignore

    @pytest.mark.asyncio
    async def test_gate_by_allowlist_ips_forbidden(self) -> None:
        """Test gate_by_allowlist_ips with forbidden IP."""
        allowed_ips = (ipaddress.ip_network("10.0.0.0/8"),)

        class DummyRequest:
            client = type("client", (), {"host": "127.0.0.1"})()

        with pytest.raises(Exception) as exc:
            await gate_by_allowlist_ips(DummyRequest(), allowed_ips)  # type: ignore
        assert "not a valid ip in allowlist" in str(exc.value)

    @pytest.mark.asyncio
    async def test_gate_by_allowlist_ips_no_client(self) -> None:
        """Test gate_by_allowlist_ips with no client."""
        allowed_ips = (ipaddress.ip_network("127.0.0.1/32"),)

        class DummyRequest:
            client = None

        with pytest.raises(Exception) as exc:
            await gate_by_allowlist_ips(DummyRequest(), allowed_ips)  # type: ignore
        assert "Could not determine client IP address" in str(exc.value)

    @pytest.mark.asyncio
    async def test_gate_by_allowlist_ips_bad_ip(self) -> None:
        """Test gate_by_allowlist_ips with bad IP."""
        allowed_ips = (ipaddress.ip_network("127.0.0.1/32"),)

        class DummyRequest:
            class client:
                host = "not-an-ip"

        with pytest.raises(Exception) as exc:
            await gate_by_allowlist_ips(DummyRequest(), allowed_ips)  # type: ignore
        assert "Could not parse client IP address" in str(exc.value)

    @pytest.mark.asyncio
    async def test_gate_by_allowlist_ips_empty_allowlist(self) -> None:
        """Test gate_by_allowlist_ips with empty allowlist."""
        allowed_ips = ()

        class DummyRequest:
            client = type("client", (), {"host": "127.0.0.1"})()

        # Should not raise when allowed_ips is empty
        await gate_by_allowlist_ips(DummyRequest(), allowed_ips)  # type: ignore

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    def test_process_webhook_request_body_error(self, client: TestClient) -> None:
        """Test webhook processing when request body reading fails."""
        # Mock the request to raise an exception when reading body
        with patch("fastapi.Request.body", side_effect=Exception("Body read error")):
            headers = {
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "test-delivery-123",
                "Content-Type": "application/json",
            }
            response = client.post("/webhook_server", content="", headers=headers)
            assert response.status_code == 400
            assert "Failed to read request body" in response.json()["detail"]

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    def test_process_webhook_configuration_error(
        self, client: TestClient, valid_webhook_payload: dict[str, Any]
    ) -> None:
        """Test webhook processing when configuration error occurs."""
        payload_json = json.dumps(valid_webhook_payload)

        with patch("webhook_server.app.Config", side_effect=Exception("Config error")):
            headers = {
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "test-delivery-123",
                "Content-Type": "application/json",
            }
            response = client.post("/webhook_server", content=payload_json, headers=headers)
            assert response.status_code == 500
            assert "Configuration error" in response.json()["detail"]

    @patch("webhook_server.app.GithubWebhook")
    def test_process_webhook_no_webhook_secret(
        self, mock_github_webhook: Mock, client: TestClient, valid_webhook_payload: dict[str, Any]
    ) -> None:
        """Test webhook processing when no webhook secret is configured."""
        payload_json = json.dumps(valid_webhook_payload)
        # Mock config to return no webhook secret
        with patch("webhook_server.app.Config") as mock_config:
            mock_config.return_value.root_data.get.return_value = None
            mock_github_webhook.return_value = Mock()
            headers = {
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "test-delivery-123",
                "Content-Type": "application/json",
            }
            response = client.post("/webhook_server", content=payload_json, headers=headers)
            # Should still process the webhook without signature verification
            assert response.status_code == 200

    @patch("httpx.AsyncClient.get")
    async def test_get_github_allowlist_unexpected_error(self, mock_get: Mock) -> None:
        """Test GitHub allowlist fetching with unexpected error."""
        async_client = AsyncMock()
        async_client.get.side_effect = Exception("Unexpected error")

        with pytest.raises(Exception, match="Unexpected error"):
            await get_github_allowlist(async_client)

    async def test_get_cloudflare_allowlist_request_error(self) -> None:
        """Test Cloudflare allowlist fetching with request error."""
        async_client = AsyncMock()
        async_client.get.side_effect = httpx.RequestError("Network error")

        with pytest.raises(httpx.RequestError):
            await get_cloudflare_allowlist(async_client)

    @patch("httpx.AsyncClient.get")
    async def test_get_cloudflare_allowlist_unexpected_error(self, mock_get: Mock) -> None:
        """Test Cloudflare allowlist fetching with unexpected error."""
        async_client = AsyncMock()
        async_client.get.side_effect = Exception("Unexpected error")

        with pytest.raises(Exception, match="Unexpected error"):
            await get_cloudflare_allowlist(async_client)

    @patch("httpx.AsyncClient.get")
    async def test_get_cloudflare_allowlist_http_error(self, mock_get: Mock) -> None:
        """Test Cloudflare allowlist fetching with HTTP error."""
        async_client = AsyncMock()
        mock_response = Mock()
        req = httpx.Request("GET", "https://api.cloudflare.com/client/v4/ips")
        resp = httpx.Response(500, request=req)
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError("HTTP Error", request=req, response=resp)
        mock_response.json = lambda: {"result": {}}
        async_client.get.return_value = mock_response

        with pytest.raises(httpx.HTTPStatusError):
            await get_cloudflare_allowlist(async_client)

    @patch("httpx.AsyncClient.get")
    async def test_get_github_allowlist_http_error(self, mock_get: Mock) -> None:
        """Test GitHub allowlist fetching with HTTP error."""
        async_client = AsyncMock()
        mock_response = Mock()
        req = httpx.Request("GET", "https://api.github.com/meta")
        resp = httpx.Response(500, request=req)
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError("HTTP Error", request=req, response=resp)
        mock_response.json = lambda: {"hooks": []}
        async_client.get.return_value = mock_response

        with pytest.raises(httpx.HTTPStatusError):
            await get_github_allowlist(async_client)

    @patch("webhook_server.app.get_github_allowlist")
    @patch("webhook_server.app.get_cloudflare_allowlist")
    @patch("webhook_server.app.Config")
    @patch("webhook_server.app.urllib3")
    async def test_lifespan_success(
        self, mock_urllib3: Mock, mock_config: Mock, mock_cf_allowlist: Mock, mock_gh_allowlist: Mock
    ) -> None:
        """Test successful lifespan function execution."""

        # Mock config
        mock_config_instance = Mock()
        mock_config_instance.root_data = {
            "verify-github-ips": True,
            "verify-cloudflare-ips": True,
            "disable-ssl-warnings": False,
        }
        mock_config.return_value = mock_config_instance
        # Mock allowlist responses
        mock_gh_allowlist.return_value = ["192.30.252.0/22"]
        mock_cf_allowlist.return_value = ["103.21.244.0/22"]
        # Mock HTTP client
        mock_client = AsyncMock()
        with patch("httpx.AsyncClient", return_value=mock_client):
            async with app_module.lifespan(FASTAPI_APP):
                pass
            mock_client.aclose.assert_called_once()

    @patch("webhook_server.app.get_github_allowlist")
    @patch("webhook_server.app.get_cloudflare_allowlist")
    @patch("webhook_server.app.Config")
    @patch("webhook_server.app.urllib3")
    async def test_lifespan_with_ssl_warnings_disabled(
        self, mock_urllib3: Mock, mock_config: Mock, mock_cf_allowlist: Mock, mock_gh_allowlist: Mock
    ) -> None:
        """Test lifespan function with SSL warnings disabled."""

        # Mock config with SSL warnings disabled
        mock_config_instance = Mock()
        mock_config_instance.root_data = {
            "verify-github-ips": False,
            "verify-cloudflare-ips": False,
            "disable-ssl-warnings": True,
        }
        mock_config.return_value = mock_config_instance

        # Mock HTTP client
        mock_client = AsyncMock()

        with patch.object(app_module, "_lifespan_http_client", mock_client):
            async with app_module.lifespan(FASTAPI_APP):
                pass

            # Verify SSL warnings were disabled
            mock_urllib3.disable_warnings.assert_called_once()

    @patch("webhook_server.app.get_github_allowlist")
    @patch("webhook_server.app.get_cloudflare_allowlist")
    @patch("webhook_server.app.Config")
    async def test_lifespan_with_invalid_cidr(
        self, mock_config: Mock, mock_cf_allowlist: Mock, mock_gh_allowlist: Mock
    ) -> None:
        """Test lifespan function with invalid CIDR addresses.

        Note: Invalid CIDR addresses are filtered out, so if IP verification
        is enabled but no valid networks are loaded, the server will fail-close
        with RuntimeError for security.
        """

        # Mock config
        mock_config_instance = Mock()
        mock_config_instance.root_data = {
            "verify-github-ips": True,
            "verify-cloudflare-ips": True,
            "disable-ssl-warnings": False,
        }
        mock_config.return_value = mock_config_instance

        # Mock allowlist responses with invalid CIDR (will be filtered out)
        mock_gh_allowlist.return_value = ["invalid-cidr"]
        mock_cf_allowlist.return_value = ["also-invalid"]

        # Mock HTTP client
        mock_client = AsyncMock()

        with patch.object(app_module, "_lifespan_http_client", mock_client):
            # Should raise RuntimeError because IP verification is enabled but no valid networks loaded
            with pytest.raises(RuntimeError, match="IP verification enabled but no allowlist loaded"):
                async with app_module.lifespan(FASTAPI_APP):
                    pass

    @patch("webhook_server.app.get_github_allowlist")
    @patch("webhook_server.app.get_cloudflare_allowlist")
    @patch("webhook_server.app.Config")
    async def test_lifespan_with_allowlist_errors(
        self, mock_config: Mock, mock_cf_allowlist: Mock, mock_gh_allowlist: Mock
    ) -> None:
        """Test lifespan function when allowlist fetching fails.

        Note: If IP verification is enabled but allowlist fetching fails,
        the server will fail-close with RuntimeError for security (fail-close
        behavior prevents insecure state).
        """

        # Mock config
        mock_config_instance = Mock()
        mock_config_instance.root_data = {
            "verify-github-ips": True,
            "verify-cloudflare-ips": True,
            "disable-ssl-warnings": False,
        }
        mock_config.return_value = mock_config_instance
        # Mock allowlist responses to fail
        mock_gh_allowlist.side_effect = Exception("GitHub API error")
        mock_cf_allowlist.side_effect = Exception("Cloudflare API error")
        # Mock HTTP client
        mock_client = AsyncMock()
        with patch.object(app_module, "_lifespan_http_client", mock_client):
            # Should raise RuntimeError because IP verification is enabled but no networks loaded
            with pytest.raises(RuntimeError, match="IP verification enabled but no allowlist loaded"):
                async with app_module.lifespan(FASTAPI_APP):
                    pass

    def test_static_files_path_construction(self) -> None:
        """Test that the static files path is constructed correctly."""

        # The static_files_path should point to webhook_server/web/static
        expected_suffix = os.path.join("webhook_server", "web", "static")
        actual_path = app_module.static_files_path

        # Check that the path contains the expected directory structure
        # Convert to string and normalize path separators for comparison
        actual_path_str = str(actual_path)
        assert actual_path_str.endswith(expected_suffix) or expected_suffix.replace(
            os.sep, "/"
        ) in actual_path_str.replace(os.sep, "/")

        # Verify path structure makes sense
        assert "webhook_server" in actual_path_str
        assert "web" in actual_path_str
        assert "static" in actual_path_str

    @patch("webhook_server.app.os.path.exists")
    @patch("webhook_server.app.os.path.isdir")
    def test_static_files_validation_logic(self, mock_isdir: Mock, mock_exists: Mock) -> None:
        """Test static files validation logic without lifespan."""

        # Test case 1: Directory exists and is valid
        mock_exists.return_value = True
        mock_isdir.return_value = True

        # This should not raise an exception
        static_path = app_module.static_files_path
        if not os.path.exists(static_path):
            raise FileNotFoundError(f"Static files directory not found: {static_path}")
        if not os.path.isdir(static_path):
            raise NotADirectoryError(f"Static files path is not a directory: {static_path}")

        # Test case 2: Directory doesn't exist
        mock_exists.return_value = False
        mock_isdir.return_value = False

        with pytest.raises(FileNotFoundError) as exc_info:
            if not os.path.exists(static_path):
                raise FileNotFoundError(
                    f"Static files directory not found: {static_path}. "
                    f"This directory is required for serving web assets (CSS/JS). "
                    f"Expected structure: webhook_server/web/static/ with css/ and js/ subdirectories."
                )

        error_msg = str(exc_info.value)
        assert "Static files directory not found" in error_msg
        assert "webhook_server/web/static" in error_msg

        # Test case 3: Path exists but is not a directory
        mock_exists.return_value = True
        mock_isdir.return_value = False

        with pytest.raises(NotADirectoryError) as exc_info:
            if not os.path.exists(static_path):
                raise FileNotFoundError(f"Path not found: {static_path}")
            if not os.path.isdir(static_path):
                raise NotADirectoryError(
                    f"Static files path exists but is not a directory: {static_path}. "
                    f"Expected a directory containing css/ and js/ subdirectories."
                )

        error_msg = str(exc_info.value)
        assert "exists but is not a directory" in error_msg
        assert "css/ and js/ subdirectories" in error_msg

    def test_require_log_server_enabled_raises(self) -> None:
        """Test that require_log_server_enabled raises 404 when disabled."""
        with patch("webhook_server.app.LOG_SERVER_ENABLED", False):
            with pytest.raises(HTTPException) as exc:
                require_log_server_enabled()
            assert exc.value.status_code == 404
            assert "Log server is disabled" in exc.value.detail

    @pytest.mark.asyncio
    async def test_websocket_log_stream_disabled(self) -> None:
        """Test websocket connection when log server is disabled."""
        mock_ws = AsyncMock()
        with patch("webhook_server.app.LOG_SERVER_ENABLED", False):
            await websocket_log_stream(mock_ws)
            mock_ws.close.assert_called_once_with(code=status.WS_1008_POLICY_VIOLATION, reason="Log server is disabled")

    @pytest.mark.asyncio
    async def test_websocket_log_stream_enabled(self) -> None:
        """Test websocket connection when log server is enabled."""
        mock_ws = AsyncMock()
        mock_controller = AsyncMock()

        with patch("webhook_server.app.LOG_SERVER_ENABLED", True):
            with patch("webhook_server.app.get_log_viewer_controller", return_value=mock_controller):
                await websocket_log_stream(mock_ws, hook_id="123")
                mock_controller.handle_websocket.assert_called_once()
                # Verify arguments
                call_args = mock_controller.handle_websocket.call_args
                assert call_args.kwargs["websocket"] == mock_ws
                assert call_args.kwargs["hook_id"] == "123"

    @patch("webhook_server.app.os.path.exists")
    async def test_lifespan_static_files_not_found(self, mock_exists: Mock) -> None:
        """Test lifespan raises FileNotFoundError when static files missing."""
        mock_exists.return_value = False
        # Mock AsyncClient to avoid connection errors if it tries to create one
        with patch("httpx.AsyncClient", return_value=AsyncMock()):
            with pytest.raises(FileNotFoundError):
                async with app_module.lifespan(FASTAPI_APP):
                    pass

    @patch("webhook_server.app.os.path.exists")
    @patch("webhook_server.app.os.path.isdir")
    async def test_lifespan_static_files_not_dir(self, mock_isdir: Mock, mock_exists: Mock) -> None:
        """Test lifespan raises NotADirectoryError when static files path is not a dir."""
        mock_exists.return_value = True
        mock_isdir.return_value = False
        with patch("httpx.AsyncClient", return_value=AsyncMock()):
            with pytest.raises(NotADirectoryError):
                async with app_module.lifespan(FASTAPI_APP):
                    pass

    def test_get_log_viewer_controller_singleton(self) -> None:
        """Test singleton behavior."""
        # Reset singleton for test by patching it to None initially
        with patch("webhook_server.app._log_viewer_controller_singleton", None):
            # Need to patch LogViewerController to avoid actual instantiation issues
            with patch("webhook_server.app.LogViewerController") as MockController:
                c1 = get_log_viewer_controller()
                c2 = get_log_viewer_controller()
                assert c1 is c2
                assert c1 is not None
                MockController.assert_called_once()

    @patch("webhook_server.app.Config")
    async def test_lifespan_background_tasks(self, mock_config: Mock) -> None:
        """Test waiting for background tasks."""

        # We need to populate _background_tasks with a dummy task
        async def dummy_coro():
            await asyncio.sleep(0.01)

        dummy_task = asyncio.create_task(dummy_coro())

        # Mock config/etc to pass startup
        mock_config.return_value.root_data = {"verify-github-ips": False, "verify-cloudflare-ips": False}

        # Use a set containing our task
        tasks_set = {dummy_task}

        with patch("webhook_server.app._background_tasks", tasks_set):
            with patch("httpx.AsyncClient", return_value=AsyncMock()):
                async with app_module.lifespan(FASTAPI_APP):
                    pass

        # Task should be done
        assert dummy_task.done()

    @patch("webhook_server.app.MCP_SERVER_ENABLED", True)
    @patch("webhook_server.app.StreamableHTTPSessionManager")
    @patch("webhook_server.app.Config")
    async def test_lifespan_mcp_init(self, mock_config: Mock, mock_stream_manager: Mock) -> None:
        """Test MCP initialization in lifespan."""
        # Mock config
        mock_config.return_value.root_data = {"verify-github-ips": False, "verify-cloudflare-ips": False}

        # Mock transport and mcp globals
        mock_transport_instance = Mock()
        mock_transport_instance._session_manager = None
        # Mock the event store which is accessed
        mock_transport_instance.event_store = Mock()

        mock_mcp_instance = Mock()
        mock_mcp_instance.server = Mock()

        # We need to inject these mocks into the module globals `http_transport` and `mcp`
        with patch("webhook_server.app.http_transport", mock_transport_instance):
            with patch("webhook_server.app.mcp", mock_mcp_instance):
                with patch("httpx.AsyncClient", return_value=AsyncMock()):
                    async with app_module.lifespan(FASTAPI_APP):
                        # Give the background task a moment to run
                        await asyncio.sleep(0.01)
                        # Verify session manager was initialized
                        # The code sets: http_transport._session_manager = StreamableHTTPSessionManager(...)
                        assert mock_transport_instance._manager_started is True

    @patch("webhook_server.app.MCP_SERVER_ENABLED", True)
    @patch("webhook_server.app.StreamableHTTPSessionManager")
    @patch("webhook_server.app.Config")
    async def test_lifespan_mcp_init_failure_does_not_raise(self, mock_config: Mock, mock_stream_manager: Mock) -> None:
        """MCP session-manager failure must not prevent lifespan from yielding."""
        mock_config.return_value.root_data = {"verify-github-ips": False, "verify-cloudflare-ips": False}
        mock_stream_manager.side_effect = RuntimeError("MCP session manager boom")

        mock_transport_instance = Mock()
        mock_transport_instance._session_manager = None
        mock_transport_instance.event_store = Mock()
        mock_transport_instance._manager_task = None
        mock_transport_instance._manager_started = False

        mock_mcp_instance = Mock()
        mock_mcp_instance.server = Mock()

        with patch("webhook_server.app.http_transport", mock_transport_instance):
            with patch("webhook_server.app.mcp", mock_mcp_instance):
                with patch("httpx.AsyncClient", return_value=AsyncMock()):
                    async with app_module.lifespan(FASTAPI_APP):
                        health = healthcheck()
                        assert health["status"] == 200
                        assert health["message"] == "Alive"

        assert mock_transport_instance._session_manager is None

    @patch("webhook_server.app.MCP_SERVER_ENABLED", True)
    @patch("webhook_server.app.StreamableHTTPSessionManager")
    @patch("webhook_server.app.Config")
    async def test_lifespan_mcp_init_partial_failure_resets_session_manager(
        self, mock_config: Mock, mock_stream_manager: Mock
    ) -> None:
        """Session manager construction success then later failure must reset _session_manager."""
        mock_config.return_value.root_data = {"verify-github-ips": False, "verify-cloudflare-ips": False}
        mock_stream_manager.return_value = Mock()

        mock_transport_instance = Mock()
        mock_transport_instance._session_manager = None
        mock_transport_instance.event_store = Mock()
        mock_transport_instance._manager_task = None
        mock_transport_instance._manager_started = False

        mock_mcp_instance = Mock()
        mock_mcp_instance.server = Mock()

        def _create_task_boom(coro: object, *args: object, **kwargs: object) -> None:
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            raise RuntimeError("create_task boom")

        with patch("webhook_server.app.http_transport", mock_transport_instance):
            with patch("webhook_server.app.mcp", mock_mcp_instance):
                with patch("httpx.AsyncClient", return_value=AsyncMock()):
                    with patch("webhook_server.app.asyncio.create_task", side_effect=_create_task_boom):
                        async with app_module.lifespan(FASTAPI_APP):
                            health = healthcheck()
                            assert health["status"] == 200
                            assert health["message"] == "Alive"
                            assert mock_transport_instance._session_manager is None

        assert mock_transport_instance._session_manager is None
        assert mock_transport_instance._manager_started is False
        assert mock_transport_instance._manager_task is None

    @patch("webhook_server.app.MCP_SERVER_ENABLED", True)
    @patch("webhook_server.app.StreamableHTTPSessionManager")
    @patch("webhook_server.app.Config")
    async def test_lifespan_mcp_init_cancels_and_awaits_manager_task(
        self, mock_config: Mock, mock_stream_manager: Mock
    ) -> None:
        """Failure after create_task must cancel and await the manager task."""
        mock_config.return_value.root_data = {"verify-github-ips": False, "verify-cloudflare-ips": False}

        class _FakeSessionCM:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *args: object) -> None:
                return None

        mock_stream_manager.return_value.run.return_value = _FakeSessionCM()

        created_tasks: list[asyncio.Task[Any]] = []
        real_create_task = asyncio.create_task

        def _tracking_create_task(coro: Any, *args: Any, **kwargs: Any) -> asyncio.Task[Any]:
            task = real_create_task(coro, *args, **kwargs)
            created_tasks.append(task)
            return task

        class BoomTransport:
            def __init__(self) -> None:
                self._session_manager = None
                self.event_store = Mock()
                self._manager_task: asyncio.Task[Any] | None = None

            def __setattr__(self, name: str, value: object) -> None:
                if name == "_manager_started" and value is True:
                    raise RuntimeError("boom after manager task created")
                object.__setattr__(self, name, value)

        mock_transport_instance = BoomTransport()
        mock_mcp_instance = Mock()
        mock_mcp_instance.server = Mock()

        with patch("webhook_server.app.http_transport", mock_transport_instance):
            with patch("webhook_server.app.mcp", mock_mcp_instance):
                with patch("httpx.AsyncClient", return_value=AsyncMock()):
                    with patch("webhook_server.app.asyncio.create_task", side_effect=_tracking_create_task):
                        async with app_module.lifespan(FASTAPI_APP):
                            health = healthcheck()
                            assert health["status"] == 200
                            assert health["message"] == "Alive"

        assert mock_transport_instance._session_manager is None
        assert mock_transport_instance._manager_started is False
        assert mock_transport_instance._manager_task is None
        assert created_tasks
        assert all(task.done() for task in created_tasks)

    @patch("webhook_server.app.MCP_SERVER_ENABLED", True)
    @patch("webhook_server.app.StreamableHTTPSessionManager")
    @patch("webhook_server.app.Config")
    async def test_lifespan_mcp_restart_recreates_session_manager(
        self, mock_config: Mock, mock_stream_manager: Mock
    ) -> None:
        """Second lifespan start after shutdown must re-create the MCP session manager."""
        mock_config.return_value.root_data = {"verify-github-ips": False, "verify-cloudflare-ips": False}

        class _FakeSessionCM:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *args: object) -> None:
                return None

        mock_stream_manager.return_value.run.return_value = _FakeSessionCM()

        mock_transport_instance = Mock()
        mock_transport_instance._session_manager = None
        mock_transport_instance.event_store = Mock()
        mock_transport_instance._manager_task = None
        mock_transport_instance._manager_started = False

        mock_mcp_instance = Mock()
        mock_mcp_instance.server = Mock()

        with patch("webhook_server.app.http_transport", mock_transport_instance):
            with patch("webhook_server.app.mcp", mock_mcp_instance):
                with patch("httpx.AsyncClient", return_value=AsyncMock()):
                    async with app_module.lifespan(FASTAPI_APP):
                        await asyncio.sleep(0.01)
                        assert mock_stream_manager.call_count == 1
                        assert mock_transport_instance._manager_started is True

                    assert mock_transport_instance._session_manager is None
                    assert mock_transport_instance._manager_started is False
                    assert mock_transport_instance._manager_task is None

                    async with app_module.lifespan(FASTAPI_APP):
                        await asyncio.sleep(0.01)
                        assert mock_stream_manager.call_count == 2
                        assert mock_transport_instance._manager_started is True

    @patch("webhook_server.app.get_cloudflare_allowlist")
    @patch("webhook_server.app.Config")
    async def test_lifespan_cloudflare_fail_only(self, mock_config: Mock, mock_cf_allowlist: Mock) -> None:
        """Test lifespan fails when only Cloudflare enabled and fails."""
        mock_config.return_value.root_data = {
            "verify-github-ips": False,
            "verify-cloudflare-ips": True,
        }
        mock_cf_allowlist.side_effect = Exception("Cloudflare error")

        with patch("httpx.AsyncClient", return_value=AsyncMock()):
            with pytest.raises(Exception, match="Cloudflare error"):
                async with app_module.lifespan(FASTAPI_APP):
                    pass

    @patch("webhook_server.app.get_github_allowlist")
    @patch("webhook_server.app.Config")
    async def test_lifespan_github_fail_only(self, mock_config: Mock, mock_gh_allowlist: Mock) -> None:
        """Test lifespan fails when only GitHub enabled and fails."""
        mock_config.return_value.root_data = {
            "verify-github-ips": True,
            "verify-cloudflare-ips": False,
        }
        mock_gh_allowlist.side_effect = Exception("GitHub error")

        with patch("httpx.AsyncClient", return_value=AsyncMock()):
            with pytest.raises(Exception, match="GitHub error"):
                async with app_module.lifespan(FASTAPI_APP):
                    pass

    @patch("webhook_server.app.Config")
    async def test_lifespan_background_tasks_timeout(self, mock_config: Mock) -> None:
        """Test cancelling background tasks on timeout."""
        # Use a mock task instead of a real one to verify cancel call
        mock_task = Mock()

        mock_config.return_value.root_data = {"verify-github-ips": False, "verify-cloudflare-ips": False}

        tasks_set = {mock_task}

        with patch("webhook_server.app._background_tasks", tasks_set):
            with patch("httpx.AsyncClient", return_value=AsyncMock()):
                # Mock asyncio.wait to return (empty, pending) to simulate timeout
                # asyncio.wait is used twice: first for completion, second for cancellation
                with patch("asyncio.wait", new_callable=AsyncMock) as mock_wait:
                    # First call returns ([], [task]), second call returns ([], [])
                    mock_wait.side_effect = [
                        (set(), {mock_task}),  # First wait times out
                        (set(), set()),  # Second wait after cancel
                    ]

                    async with app_module.lifespan(FASTAPI_APP):
                        pass

                    assert mock_wait.call_count == 2
                    # Task should have been cancelled
                    mock_task.cancel.assert_called_once()

    def test_process_webhook_missing_event_header(self, client: TestClient) -> None:
        """Test missing X-GitHub-Event header."""
        response = client.post("/webhook_server", content="{}", headers={})
        assert response.status_code == 400
        assert "Missing X-GitHub-Event header" in response.json()["detail"]

    @patch("webhook_server.app.Config")
    def test_process_webhook_missing_repo_name(self, mock_config: Mock, client: TestClient) -> None:
        """Test payload missing repository.name."""
        # Ensure no secret so verify_signature is skipped
        mock_config.return_value.root_data = {"webhook-secret": None}

        headers = {"X-GitHub-Event": "push", "Content-Type": "application/json"}
        payload = {"repository": {"full_name": "org/repo"}}
        response = client.post("/webhook_server", content=json.dumps(payload), headers=headers)
        assert response.status_code == 400
        assert "Missing repository.name in payload" in response.json()["detail"]

    @patch("webhook_server.app.Config")
    def test_process_webhook_missing_repo_full_name(self, mock_config: Mock, client: TestClient) -> None:
        """Test payload missing repository.full_name."""
        mock_config.return_value.root_data = {"webhook-secret": None}

        headers = {"X-GitHub-Event": "push", "Content-Type": "application/json"}
        payload = {"repository": {"name": "repo"}}
        response = client.post("/webhook_server", content=json.dumps(payload), headers=headers)
        assert response.status_code == 400
        assert "Missing repository.full_name in payload" in response.json()["detail"]

    @patch("webhook_server.app.LOG_SERVER_ENABLED", True)
    def test_get_log_viewer_page(self, client: TestClient) -> None:
        """Test get_log_viewer_page endpoint."""
        mock_instance = MagicMock()
        # get_log_page is async, so use AsyncMock
        mock_instance.get_log_page = AsyncMock(return_value="<html></html>")

        # Patch the singleton directly as controller_dependency captures reference to get_log_viewer_controller
        with patch("webhook_server.app._log_viewer_controller_singleton", mock_instance):
            response = client.get("/logs")
            assert response.status_code == 200

    @patch("webhook_server.app.LOG_SERVER_ENABLED", True)
    def test_get_log_entries(self, client: TestClient) -> None:
        """Test get_log_entries endpoint."""
        mock_instance = MagicMock()
        # get_log_entries is async, so use AsyncMock
        mock_instance.get_log_entries = AsyncMock(return_value={"entries": []})

        with patch("webhook_server.app._log_viewer_controller_singleton", mock_instance):
            response = client.get("/logs/api/entries")
            assert response.status_code == 200
            assert response.json() == {"entries": []}

    @patch("webhook_server.app.LOG_SERVER_ENABLED", True)
    def test_export_logs(self, client: TestClient) -> None:
        """Test export_logs endpoint."""
        mock_instance = MagicMock()

        def iter_content():
            yield b"data"

        # export_logs is async, so use AsyncMock
        mock_instance.export_logs = AsyncMock(return_value=StreamingResponse(iter_content()))

        with patch("webhook_server.app._log_viewer_controller_singleton", mock_instance):
            response = client.get("/logs/api/export?format_type=json")
            assert response.status_code == 200
            assert response.content == b"data"

    @patch("webhook_server.app.get_github_allowlist")
    @patch("webhook_server.app.get_cloudflare_allowlist")
    @patch("webhook_server.app.Config")
    async def test_lifespan_log_viewer_shutdown(
        self, mock_config: Mock, mock_cf_allowlist: Mock, mock_gh_allowlist: Mock
    ) -> None:
        """Test LogViewerController shutdown in lifespan."""
        mock_config.return_value.root_data = {"verify-github-ips": False, "verify-cloudflare-ips": False}

        mock_controller = AsyncMock()

        # Inject mock controller into singleton
        with patch("webhook_server.app._log_viewer_controller_singleton", mock_controller):
            with patch("httpx.AsyncClient", return_value=AsyncMock()):
                async with app_module.lifespan(FASTAPI_APP):
                    pass

                mock_controller.shutdown.assert_called_once()

    @patch("webhook_server.app.GithubWebhook")
    @patch("webhook_server.app.Config")
    def test_process_webhook_connect_error(self, mock_config: Mock, mock_webhook_cls: Mock, client: TestClient) -> None:
        """Test process_webhook with connection error."""
        mock_config.return_value.root_data = {"webhook-secret": None}

        mock_instance = mock_webhook_cls.return_value
        mock_instance.process = AsyncMock(side_effect=httpx.ConnectError("Connection failed"))
        mock_instance.cleanup = AsyncMock()

        with patch("webhook_server.app.get_logger_with_params") as mock_get_logger:
            mock_logger = mock_get_logger.return_value

            headers = {"X-GitHub-Event": "push", "Content-Type": "application/json", "X-GitHub-Delivery": "123"}
            # A tag ref is what a real push carries. Without it process_webhook() drops the
            # delivery pre-construction (nothing to do for a branch push) and the handler
            # error path these tests cover would never run.
            payload = {"repository": {"name": "repo", "full_name": "org/repo"}, "ref": "refs/tags/v1.0.0"}

            captured_coro = None

            # Mock create_task to return a dummy task but capture coro
            mock_task = MagicMock()

            def side_effect(coro):
                nonlocal captured_coro
                captured_coro = coro
                return mock_task

            with patch("webhook_server.app.asyncio.create_task", side_effect=side_effect):
                response = client.post("/webhook_server", content=json.dumps(payload), headers=headers)
                assert response.status_code == 200

                # Run captured coro
                if captured_coro:
                    asyncio.run(captured_coro)

                # Verify exception logging
                mock_logger.exception.assert_called()
                call_args = mock_logger.exception.call_args
                assert "API connection error - check network connectivity" in call_args[0][0]

    @patch("webhook_server.app.GithubWebhook")
    @patch("webhook_server.app.Config")
    def test_process_webhook_repo_not_found(
        self, mock_config: Mock, mock_webhook_cls: Mock, client: TestClient
    ) -> None:
        """Test process_webhook with repository not found error."""
        mock_config.return_value.root_data = {"webhook-secret": None}

        mock_instance = mock_webhook_cls.return_value
        mock_instance.process = AsyncMock(side_effect=RepositoryNotFoundInConfigError("Repo not found"))
        mock_instance.cleanup = AsyncMock()

        with patch("webhook_server.app.get_logger_with_params") as mock_get_logger:
            mock_logger = mock_get_logger.return_value

            headers = {"X-GitHub-Event": "push", "Content-Type": "application/json", "X-GitHub-Delivery": "123"}
            # A tag ref is what a real push carries. Without it process_webhook() drops the
            # delivery pre-construction (nothing to do for a branch push) and the handler
            # error path these tests cover would never run.
            payload = {"repository": {"name": "repo", "full_name": "org/repo"}, "ref": "refs/tags/v1.0.0"}

            captured_coro = None
            mock_task = MagicMock()

            def side_effect(coro):
                nonlocal captured_coro
                captured_coro = coro
                return mock_task

            with patch("webhook_server.app.asyncio.create_task", side_effect=side_effect):
                response = client.post("/webhook_server", content=json.dumps(payload), headers=headers)
                assert response.status_code == 200

                if captured_coro:
                    asyncio.run(captured_coro)

                # Verify error logging
                mock_logger.error.assert_called()
                call_args = mock_logger.error.call_args
                assert "Repository not found in configuration" in call_args[0][0]


class TestWebhookConstructionThreading:
    """GithubWebhook construction must never run on the event loop.

    It is blocking I/O and can spend up to ~30s inside github_api_call_sync retry backoff
    during a GitHub outage, so running it on the loop would stall every other in-flight
    webhook. These tests fail if that regresses to an inline constructor call.

    No test here sleeps for a fixed duration: the fake constructor blocks on an Event that
    the test always sets, and loop progress is measured with asyncio.sleep(0) yields. If a
    test fails before releasing, the 10s wait inside the worker is the backstop.
    """

    PAYLOAD: dict[str, Any] = {
        "action": "opened",
        "repository": {"name": "repo", "full_name": "org/repo"},
        "sender": {"login": "someone"},
    }
    # Only a backstop for the regression case, so a mis-wired constructor fails the test
    # instead of deadlocking it. The happy path never waits this long.
    WORKER_TIMEOUT: float = 2.0

    @classmethod
    def _request(cls) -> Mock:
        request = Mock()
        request.headers = Headers({"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "d-1"})
        request.body = AsyncMock(return_value=json.dumps(cls.PAYLOAD).encode())
        # The constructor pool is owned per app instance and resolved from request.app
        request.app = app_module.FASTAPI_APP
        return request

    @classmethod
    def _constructor(cls, recorded: dict[str, Any], release: threading.Event, block: bool) -> Any:
        def _build(**kwargs: Any) -> Mock:
            recorded["thread"] = threading.get_ident()
            recorded["started"].set()
            if block:
                release.wait(timeout=cls.WORKER_TIMEOUT)
            instance = Mock()
            instance.process = AsyncMock()
            instance.cleanup = AsyncMock()
            recorded["instance"] = instance
            return instance

        return _build

    @classmethod
    async def _submit(cls) -> asyncio.Task[Any]:
        """Fire the endpoint and hand back the background handler task it spawned.

        ``_background_tasks`` is a module-global set shared with every other test in this
        file, so pick out the task this call created rather than whichever happens to be
        first - awaiting a foreign task would hang.
        """
        before = set(app_module._background_tasks)
        response = await app_module.process_webhook(cls._request())
        assert response.status_code == 200
        new_tasks = [task for task in app_module._background_tasks if task not in before]
        assert len(new_tasks) == 1, f"expected one background task, got {len(new_tasks)}"
        return new_tasks[0]

    @classmethod
    async def _drain(cls, task: asyncio.Task[Any]) -> None:
        """Await a handler task, never blocking the suite forever if it wedges."""
        await asyncio.wait_for(asyncio.shield(task), timeout=5)

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.app.verify_signature")
    @patch("webhook_server.app.GithubWebhook")
    async def test_constructor_runs_off_the_event_loop(self, mock_webhook_cls: Mock, mock_signature: Mock) -> None:
        """The constructor must run on a worker thread, not the loop thread."""
        recorded: dict[str, Any] = {"started": threading.Event()}
        release = threading.Event()
        release.set()  # do not block; we only care which thread it ran on
        mock_webhook_cls.side_effect = self._constructor(recorded, release, block=False)

        loop_thread = threading.get_ident()
        task = await self._submit()
        try:
            await self._drain(task)
        finally:
            task.cancel()

        assert recorded["thread"] != loop_thread, "GithubWebhook construction ran on the event loop"
        assert recorded["instance"].cleanup.await_count == 1

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.app.verify_signature")
    @patch("webhook_server.app.GithubWebhook")
    async def test_event_loop_stays_responsive_during_construction(
        self, mock_webhook_cls: Mock, mock_signature: Mock
    ) -> None:
        """A constructor blocked in a worker must not stop the loop from running tasks."""
        recorded: dict[str, Any] = {"started": threading.Event()}
        release = threading.Event()
        mock_webhook_cls.side_effect = self._constructor(recorded, release, block=True)

        task = await self._submit()
        try:
            for _ in range(10_000):
                if recorded["started"].is_set():
                    break
                await asyncio.sleep(0)
            assert recorded["started"].is_set(), "constructor never started"

            # The worker is blocked inside the constructor here. Yielding the loop must keep
            # making progress; if construction ran on the event loop instead, the very first
            # yield would not return until the constructor finished.
            for _ in range(50):
                await asyncio.sleep(0)

            assert "instance" not in recorded, "event loop was blocked behind the constructor"
            release.set()
            await self._drain(task)
        finally:
            task.cancel()
            release.set()

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.app.verify_signature")
    @patch("webhook_server.app.GithubWebhook")
    async def test_cancelled_handler_cleans_up_orphaned_constructor(
        self, mock_webhook_cls: Mock, mock_signature: Mock
    ) -> None:
        """Cancelling mid-construction must still collect and clean up the instance.

        The worker runs to completion after the handler stops awaiting, and the constructor
        has already made the clone temp dir, so the orphaned instance has to be cleaned up
        explicitly or shutdown leaks a directory.
        """
        recorded: dict[str, Any] = {"started": threading.Event()}
        release = threading.Event()
        mock_webhook_cls.side_effect = self._constructor(recorded, release, block=True)

        task = await self._submit()
        # Bounded spin: never busy-wait forever if construction never starts.
        for _ in range(10_000):
            if recorded["started"].is_set():
                break
            await asyncio.sleep(0)
        assert recorded["started"].is_set(), "constructor never started"

        task.cancel()
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await task

        instance = recorded.get("instance")
        assert instance is not None, "orphaned constructor never completed"
        instance.cleanup.assert_awaited()
        instance.process.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_constructor_pool_is_bounded(self) -> None:
        """Constructor workers are capped so a webhook burst cannot spawn them unbounded."""
        pool = app_module._webhook_init_pool_for(self._request())
        assert pool._max_workers == app_module.WEBHOOK_INIT_MAX_WORKERS

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.app.verify_signature")
    @patch("webhook_server.app.GithubWebhook")
    async def test_constructor_inherits_webhook_context(self, mock_webhook_cls: Mock, mock_signature: Mock) -> None:
        """The worker must see the handler's contextvars.

        run_in_executor does not propagate them the way asyncio.to_thread does, so without
        an explicit copy_context() the constructor saves ctx=None and silently disables
        token metrics, workflow-step tracking and JSON log enrichment for every webhook.
        """
        seen: dict[str, Any] = {}

        def _constructor(**kwargs: Any) -> Mock:
            seen["ctx"] = get_context()
            instance = Mock()
            instance.process = AsyncMock()
            instance.cleanup = AsyncMock()
            return instance

        mock_webhook_cls.side_effect = _constructor

        task = await self._submit()
        await self._drain(task)

        assert seen["ctx"] is not None, "webhook context was lost in the worker - ctx would be None"
        assert seen["ctx"].hook_id == "d-1"

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.app.verify_signature")
    @patch("webhook_server.app.GithubWebhook")
    async def test_capacity_returns_503_so_github_retries(self, mock_webhook_cls: Mock, mock_signature: Mock) -> None:
        """At capacity the endpoint must reject with 503, never answer 200 and drop.

        GitHub treats a 2xx as delivered and will not redeliver, so dropping after the
        response commits loses the webhook permanently.
        """
        with patch.object(app_module, "try_admit_constructor", return_value=False):
            before = set(app_module._background_tasks)
            response = await app_module.process_webhook(self._request())

        assert response.status_code == 503
        assert response.status_code >= 500  # retryable by GitHub
        mock_webhook_cls.assert_not_called()
        # No *new* task was scheduled: compare against what was already pending.
        assert app_module._background_tasks == before

    @pytest.mark.asyncio
    async def test_admission_counts_constructors_not_background_tasks(self) -> None:
        """The reservation must bound constructors, not handler tasks.

        _background_tasks holds a task for the whole handler lifetime (process() and
        cleanup() included) and also the MCP manager task, so counting it rejected
        deliveries while every worker sat idle.
        """
        app_module._constructors_in_flight = 0
        assert app_module.try_admit_constructor() is True
        assert app_module._constructors_in_flight == 1

        # Background tasks are not constructors and must not consume reservations
        decoy = MagicMock()
        app_module._background_tasks.add(decoy)
        try:
            assert app_module.try_admit_constructor() is True
            assert app_module._constructors_in_flight == 2
        finally:
            # never leave entries in this shared module-global set
            app_module._background_tasks.discard(decoy)

        app_module.release_constructor_slot()
        app_module.release_constructor_slot()
        assert app_module._constructors_in_flight == 0

        with patch.object(app_module, "WEBHOOK_INIT_MAX_PENDING", 1):
            assert app_module.try_admit_constructor() is True
            assert app_module.try_admit_constructor() is False
            app_module.release_constructor_slot()

    @pytest.mark.asyncio
    async def test_each_app_instance_owns_its_own_pool(self) -> None:
        """One instance's shutdown must not retire another instance's pool.

        A shared module-level pool meant shutting one app down cancelled queued
        constructors belonging to a still-running instance, so its already-acknowledged
        webhook could never reach process().
        """
        first, second = Mock(), Mock()
        first.state = Mock(spec=[])
        second.state = Mock(spec=[])
        first_request, second_request = Mock(), Mock()
        first_request.app = first
        second_request.app = second

        first_pool = app_module._webhook_init_pool_for(first_request)
        second_pool = app_module._webhook_init_pool_for(second_request)

        assert first_pool is not second_pool, "both app instances resolved to the same pool"

        first_pool.shutdown(wait=False, cancel_futures=True)
        # The second instance is untouched and still accepts work
        assert second_pool.submit(lambda: 1).result(timeout=5) == 1

    @pytest.mark.asyncio
    async def test_lifespan_retires_the_pools_of_the_app_it_owns(self) -> None:
        """Shutdown must read _app.state, not FASTAPI_APP.state.

        The pool lives on the app that created it, so reading the module-level FASTAPI_APP
        left a separate instance's pool - and its queued constructors - alive on exit.
        """
        owned_app, other_app = Mock(), Mock()
        # Plain Mock, not Mock(spec=[]): a spec restricts reads, so getattr(state,
        # "webhook_init_pool") would return None and the branch under test would not run.
        owned_app.state = Mock()
        other_app.state = Mock()
        owned_pool = app_module._new_webhook_init_pool()
        other_pool = app_module._new_webhook_init_pool()
        owned_app.state.webhook_init_pool = owned_pool
        other_app.state.webhook_init_pool = other_pool

        try:
            await self._run_lifespan_to_completion(owned_app)
        except Exception:  # pragma: no cover - lifespan may fail on unrelated missing config
            pass

        assert owned_pool._shutdown is True
        assert owned_app.state.webhook_init_pool is None
        # The unrelated instance keeps its pool and can still accept work
        assert other_pool._shutdown is False
        assert other_pool.submit(lambda: 1).result(timeout=5) == 1
        other_pool.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    async def _run_lifespan_to_completion(app: Mock) -> None:
        """Drive lifespan() through startup and shutdown against *app*.

        lifespan is wrapped in @asynccontextmanager, so it must be entered with
        ``async with`` - reaching for the raw generator would skip the body entirely and the
        shutdown branch under test would never execute.
        """
        async with app_module.lifespan(app):
            pass

    @pytest.mark.asyncio
    async def test_reservation_released_when_handler_cancelled_before_running(self) -> None:
        """A handler cancelled before its body runs must still return its slot.

        The admission counter is process-global and monotonic; an unreleased reservation
        permanently shrinks capacity until every delivery is rejected with 503.
        """
        app_module._constructors_in_flight = 0
        reservation = app_module.ConstructorReservation()
        assert app_module.try_admit_constructor() is True

        task = asyncio.create_task(asyncio.sleep(0))
        task.add_done_callback(lambda _done: reservation.release())
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)  # let the done-callback run

        assert app_module._constructors_in_flight == 0

    @pytest.mark.asyncio
    async def test_reservation_release_is_idempotent(self) -> None:
        """Double release must not free a slot another webhook now owns."""
        app_module._constructors_in_flight = 0
        reservation = app_module.ConstructorReservation()
        assert app_module.try_admit_constructor() is True

        reservation.release()
        assert app_module.try_admit_constructor() is True
        assert app_module._constructors_in_flight == 1

        reservation.release()
        reservation.release()
        assert app_module._constructors_in_flight == 1, "idempotent release corrupted the counter"

        app_module._constructors_in_flight = 0
