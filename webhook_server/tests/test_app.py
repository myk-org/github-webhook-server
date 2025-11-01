import hashlib
import hmac
import ipaddress
import json
import os
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from unittest.mock import patch as patcher

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from webhook_server import app as app_module
from webhook_server.app import FASTAPI_APP, LOG_SERVER_ENABLED, require_log_server_enabled
from webhook_server.libs.exceptions import RepositoryNotFoundInConfigError
from webhook_server.utils.app_utils import (
    gate_by_allowlist_ips,
    get_cloudflare_allowlist,
    get_github_allowlist,
)


class TestWebhookApp:
    """Comprehensive tests for the main FastAPI webhook application."""

    @pytest.fixture
    def client(self) -> TestClient:
        """FastAPI test client."""
        return TestClient(FASTAPI_APP)

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
        assert "Invalid payload structure" in response.json()["detail"]

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.app.GithubWebhook")
    def test_process_webhook_repository_not_found(
        self, mock_github_webhook: Mock, client: TestClient, valid_webhook_payload: dict[str, Any], webhook_secret: str
    ) -> None:
        """Test webhook processing when repository is not found in config.

        Note: With async background processing, errors during GithubWebhook initialization
        occur in the background task. The webhook endpoint returns 200 OK immediately,
        and errors are logged in the background task.
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

        # Webhook returns 200 OK immediately; error happens in background task
        assert response.status_code == 200
        assert response.json()["message"] == "Webhook queued for processing"
        assert response.json()["delivery_id"] == "test-delivery-123"

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

        Note: With async background processing, connection errors during GithubWebhook
        initialization occur in the background task. The webhook endpoint returns 200 OK
        immediately, and errors are logged in the background task.
        """
        mock_github_webhook.side_effect = httpx.ConnectError("API connection failed")

        payload_json = json.dumps(valid_webhook_payload)
        signature = self.create_github_signature(payload_json, webhook_secret)

        headers = {
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "test-delivery-123",
            "x-hub-signature-256": signature,
            "Content-Type": "application/json",
        }

        response = client.post("/webhook_server", content=payload_json, headers=headers)

        # Webhook returns 200 OK immediately; error happens in background task
        assert response.status_code == 200
        assert response.json()["message"] == "Webhook queued for processing"
        assert response.json()["delivery_id"] == "test-delivery-123"

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.app.GithubWebhook")
    def test_process_webhook_unexpected_error(
        self, mock_github_webhook: Mock, client: TestClient, valid_webhook_payload: dict[str, Any], webhook_secret: str
    ) -> None:
        """Test webhook processing when unexpected error occurs.

        Note: With async background processing, unexpected errors during GithubWebhook
        initialization occur in the background task. The webhook endpoint returns 200 OK
        immediately, and errors are logged in the background task.
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

        # Webhook returns 200 OK immediately; error happens in background task
        assert response.status_code == 200
        assert response.json()["message"] == "Webhook queued for processing"
        assert response.json()["delivery_id"] == "test-delivery-123"

    @patch("webhook_server.app.get_github_allowlist")
    @patch("webhook_server.app.get_cloudflare_allowlist")
    @patch("webhook_server.app.Config")
    async def test_ip_allowlist_functionality(
        self, mock_config: Mock, mock_cf_allowlist: Mock, mock_gh_allowlist: Mock
    ) -> None:
        """Test IP allowlist functionality by verifying ALLOWED_IPS is populated after lifespan runs."""
        # Mock config with verification enabled
        mock_config_instance = Mock()
        mock_config_instance.root_data = {
            "verify-github-ips": True,
            "verify-cloudflare-ips": True,
            "disable-ssl-warnings": False,
        }
        mock_config.return_value = mock_config_instance

        # Mock allowlist responses
        mock_gh_allowlist.return_value = ["192.30.252.0/22", "185.199.108.0/22"]
        mock_cf_allowlist.return_value = ["103.21.244.0/22", "2400:cb00::/32"]

        # Mock HTTP client
        mock_client = AsyncMock()

        # Run lifespan to populate ALLOWED_IPS
        with patcher("httpx.AsyncClient", return_value=mock_client):
            async with app_module.lifespan(FASTAPI_APP):
                # Verify ALLOWED_IPS is populated with expected networks
                assert len(app_module.ALLOWED_IPS) > 0
                # Convert to set of strings for easier comparison
                allowed_ips_str = {str(network) for network in app_module.ALLOWED_IPS}
                assert "192.30.252.0/22" in allowed_ips_str
                assert "185.199.108.0/22" in allowed_ips_str
                assert "103.21.244.0/22" in allowed_ips_str
                assert "2400:cb00::/32" in allowed_ips_str

            # Verify functions were called during lifespan
            mock_gh_allowlist.assert_called_once()
            mock_cf_allowlist.assert_called_once()
            mock_client.aclose.assert_called_once()

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
    @patch("webhook_server.app.ALLOWED_IPS", ())
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
    @patch("webhook_server.app.ALLOWED_IPS", ())
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

    @patch("webhook_server.app.ALLOWED_IPS", ())
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
        async_client.get.side_effect = AssertionError("Unexpected error")

        with pytest.raises(AssertionError):
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
        async_client.get.side_effect = AssertionError("Unexpected error")

        with pytest.raises(AssertionError):
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
        with patcher("httpx.AsyncClient", return_value=mock_client):
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

        When IP verification is enabled but all CIDRs are invalid, the server
        should fail-close for security (raise RuntimeError).
        """

        # Mock config
        mock_config_instance = Mock()
        mock_config_instance.root_data = {
            "verify-github-ips": True,
            "verify-cloudflare-ips": True,
            "disable-ssl-warnings": False,
        }
        mock_config.return_value = mock_config_instance

        # Mock allowlist responses with invalid CIDR
        mock_gh_allowlist.return_value = ["invalid-cidr"]
        mock_cf_allowlist.return_value = ["also-invalid"]

        # Mock HTTP client
        mock_client = AsyncMock()

        with patch.object(app_module, "_lifespan_http_client", mock_client):
            # Should raise RuntimeError when no valid networks loaded (fail-close)
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

        When IP verification is enabled but both GitHub and Cloudflare API calls fail,
        the server should fail-close for security (raise RuntimeError).
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
            # Should raise RuntimeError when no allowlist loaded (fail-close)
            with pytest.raises(RuntimeError, match="IP verification enabled but no allowlist loaded"):
                async with app_module.lifespan(FASTAPI_APP):
                    pass

    @patch("webhook_server.app.get_github_allowlist")
    @patch("webhook_server.app.get_cloudflare_allowlist")
    @patch("webhook_server.app.Config")
    @patch("webhook_server.app.os.path.exists")
    async def test_lifespan_static_files_not_found(
        self, mock_exists: Mock, mock_config: Mock, _mock_cf_allowlist: Mock, _mock_gh_allowlist: Mock
    ) -> None:
        """Test lifespan function when static files directory doesn't exist."""
        # Mock config
        mock_config_instance = Mock()
        mock_config_instance.root_data = {
            "verify-github-ips": False,
            "verify-cloudflare-ips": False,
            "disable-ssl-warnings": False,
        }
        mock_config.return_value = mock_config_instance

        # Mock static files directory not existing
        mock_exists.return_value = False

        # Mock HTTP client
        mock_client = AsyncMock()
        with patcher("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(FileNotFoundError, match="Static files directory not found"):
                async with app_module.lifespan(FASTAPI_APP):
                    pass
            # Assert client cleanup happens even on failure
            mock_client.aclose.assert_called_once()

    @patch("webhook_server.app.get_github_allowlist")
    @patch("webhook_server.app.get_cloudflare_allowlist")
    @patch("webhook_server.app.Config")
    @patch("webhook_server.app.os.path.exists")
    @patch("webhook_server.app.os.path.isdir")
    async def test_lifespan_static_files_not_directory(
        self,
        mock_isdir: Mock,
        mock_exists: Mock,
        mock_config: Mock,
        _mock_cf_allowlist: Mock,
        _mock_gh_allowlist: Mock,
    ) -> None:
        """Test lifespan function when static files path exists but is not a directory."""
        # Mock config
        mock_config_instance = Mock()
        mock_config_instance.root_data = {
            "verify-github-ips": False,
            "verify-cloudflare-ips": False,
            "disable-ssl-warnings": False,
        }
        mock_config.return_value = mock_config_instance

        # Mock static files path exists but is not a directory
        mock_exists.return_value = True
        mock_isdir.return_value = False

        # Mock HTTP client
        mock_client = AsyncMock()
        with patcher("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(NotADirectoryError, match="exists but is not a directory"):
                async with app_module.lifespan(FASTAPI_APP):
                    pass
            # Assert client cleanup happens even on failure
            mock_client.aclose.assert_called_once()

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

    @patch("webhook_server.app.get_github_allowlist")
    @patch("webhook_server.app.get_cloudflare_allowlist")
    @patch("webhook_server.app.Config")
    @patch("webhook_server.app.os.path.exists")
    @patch("webhook_server.app.os.path.isdir")
    async def test_static_files_validation_logic(
        self,
        mock_isdir: Mock,
        mock_exists: Mock,
        mock_config: Mock,
        _mock_cf_allowlist: Mock,
        _mock_gh_allowlist: Mock,
    ) -> None:
        """Test static files validation logic by exercising lifespan with different scenarios."""
        # Mock config
        mock_config_instance = Mock()
        mock_config_instance.root_data = {
            "verify-github-ips": False,
            "verify-cloudflare-ips": False,
            "disable-ssl-warnings": False,
        }
        mock_config.return_value = mock_config_instance

        # Mock HTTP client
        mock_client = AsyncMock()

        # Test case 1: Directory exists and is valid
        mock_exists.return_value = True
        mock_isdir.return_value = True

        # This should not raise an exception
        with patcher("httpx.AsyncClient", return_value=mock_client):
            async with app_module.lifespan(FASTAPI_APP):
                pass
            mock_client.aclose.assert_called_once()

        # Reset mock for next test
        mock_client.reset_mock()

        # Test case 2: Directory doesn't exist
        mock_exists.return_value = False
        mock_isdir.return_value = False

        with patcher("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(FileNotFoundError) as exc_info:
                async with app_module.lifespan(FASTAPI_APP):
                    pass

            error_msg = str(exc_info.value)
            assert "Static files directory not found" in error_msg
            assert "webhook_server/web/static" in error_msg
            mock_client.aclose.assert_called_once()

        # Reset mock for next test
        mock_client.reset_mock()

        # Test case 3: Path exists but is not a directory
        mock_exists.return_value = True
        mock_isdir.return_value = False

        with patcher("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(NotADirectoryError) as exc_info:
                async with app_module.lifespan(FASTAPI_APP):
                    pass

            error_msg = str(exc_info.value)
            assert "exists but is not a directory" in error_msg
            assert "css/ and js/ subdirectories" in error_msg
            mock_client.aclose.assert_called_once()

    def test_require_log_server_enabled_raises_when_disabled(self) -> None:
        """Test require_log_server_enabled raises HTTPException when log server is disabled."""
        # Save original value
        original_value = LOG_SERVER_ENABLED

        # Temporarily set to False
        app_module.LOG_SERVER_ENABLED = False

        try:
            with pytest.raises(HTTPException) as exc_info:
                require_log_server_enabled()
            assert exc_info.value.status_code == 404
            assert "Log server is disabled" in str(exc_info.value.detail)
        finally:
            # Restore original value
            app_module.LOG_SERVER_ENABLED = original_value

    @patch("webhook_server.app.get_github_allowlist")
    @patch("webhook_server.app.get_cloudflare_allowlist")
    @patch("webhook_server.app.Config")
    async def test_lifespan_cloudflare_fails_github_disabled_raises(
        self, mock_config: Mock, mock_cf_allowlist: Mock, mock_gh_allowlist: Mock
    ) -> None:
        """Test lifespan raises when Cloudflare fails and GitHub verification is disabled."""
        mock_config_instance = Mock()
        mock_config_instance.root_data = {
            "verify-github-ips": False,
            "verify-cloudflare-ips": True,
            "disable-ssl-warnings": False,
        }
        mock_config.return_value = mock_config_instance
        mock_cf_allowlist.side_effect = Exception("Cloudflare API error")
        mock_gh_allowlist.return_value = []

        mock_client = AsyncMock()
        with patch.object(app_module, "_lifespan_http_client", mock_client):
            with pytest.raises(Exception, match="Cloudflare API error"):
                async with app_module.lifespan(FASTAPI_APP):
                    pass

    @patch("webhook_server.app.get_github_allowlist")
    @patch("webhook_server.app.get_cloudflare_allowlist")
    @patch("webhook_server.app.Config")
    async def test_lifespan_github_fails_cloudflare_disabled_raises(
        self, mock_config: Mock, mock_cf_allowlist: Mock, mock_gh_allowlist: Mock
    ) -> None:
        """Test lifespan raises when GitHub fails and Cloudflare verification is disabled."""
        mock_config_instance = Mock()
        mock_config_instance.root_data = {
            "verify-github-ips": True,
            "verify-cloudflare-ips": False,
            "disable-ssl-warnings": False,
        }
        mock_config.return_value = mock_config_instance
        mock_gh_allowlist.side_effect = Exception("GitHub API error")
        mock_cf_allowlist.return_value = []

        mock_client = AsyncMock()
        with patch.object(app_module, "_lifespan_http_client", mock_client):
            with pytest.raises(Exception, match="GitHub API error"):
                async with app_module.lifespan(FASTAPI_APP):
                    pass
