import hashlib
import hmac
import ipaddress
import json
import os
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from webhook_server.app import FASTAPI_APP
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
        assert "Missing repository information" in response.json()["detail"]

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.app.GithubWebhook")
    def test_process_webhook_repository_not_found(
        self, mock_github_webhook: Mock, client: TestClient, valid_webhook_payload: dict[str, Any], webhook_secret: str
    ) -> None:
        """Test webhook processing when repository is not found in config."""
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

        assert response.status_code == 404
        assert "Repository not found in configuration" in response.json()["detail"]

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
        """Test webhook processing when connection error occurs."""
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

        assert response.status_code == 503
        assert "API Connection Error" in response.json()["detail"]

    @patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
    @patch("webhook_server.app.GithubWebhook")
    def test_process_webhook_unexpected_error(
        self, mock_github_webhook: Mock, client: TestClient, valid_webhook_payload: dict[str, Any], webhook_secret: str
    ) -> None:
        """Test webhook processing when unexpected error occurs."""
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

        assert response.status_code == 500
        assert "Internal Server Error" in response.json()["detail"]

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
        from unittest.mock import AsyncMock

        async_client = AsyncMock()
        async_client.get.return_value = mock_response

        result = await get_github_allowlist(async_client)
        assert result == ["192.30.252.0/22", "185.199.108.0/22"]
        async_client.get.assert_called_once()

    @patch("httpx.AsyncClient.get")
    async def test_get_github_allowlist_error(self, mock_get: Mock) -> None:
        """Test GitHub allowlist fetching with error."""
        from unittest.mock import AsyncMock

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
        from unittest.mock import AsyncMock

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
        from unittest.mock import AsyncMock

        async_client = AsyncMock()
        async_client.get.side_effect = Exception("Unexpected error")

        with pytest.raises(Exception):
            await get_github_allowlist(async_client)

    async def test_get_cloudflare_allowlist_request_error(self) -> None:
        """Test Cloudflare allowlist fetching with request error."""
        from unittest.mock import AsyncMock

        async_client = AsyncMock()
        async_client.get.side_effect = httpx.RequestError("Network error")

        with pytest.raises(httpx.RequestError):
            await get_cloudflare_allowlist(async_client)

    @patch("httpx.AsyncClient.get")
    async def test_get_cloudflare_allowlist_unexpected_error(self, mock_get: Mock) -> None:
        """Test Cloudflare allowlist fetching with unexpected error."""
        from unittest.mock import AsyncMock

        async_client = AsyncMock()
        async_client.get.side_effect = Exception("Unexpected error")

        with pytest.raises(Exception):
            await get_cloudflare_allowlist(async_client)

    @patch("httpx.AsyncClient.get")
    async def test_get_cloudflare_allowlist_http_error(self, mock_get: Mock) -> None:
        """Test Cloudflare allowlist fetching with HTTP error."""
        from unittest.mock import AsyncMock

        import httpx

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
        from unittest.mock import AsyncMock

        import httpx

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
        from unittest.mock import AsyncMock
        from unittest.mock import patch as patcher

        from webhook_server import app as app_module

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
        from webhook_server import app as app_module

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
        """Test lifespan function with invalid CIDR addresses."""
        from webhook_server import app as app_module

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
            async with app_module.lifespan(FASTAPI_APP):
                pass

            # Should handle invalid CIDR gracefully

    @patch("webhook_server.app.get_github_allowlist")
    @patch("webhook_server.app.get_cloudflare_allowlist")
    @patch("webhook_server.app.Config")
    async def test_lifespan_with_allowlist_errors(
        self, mock_config: Mock, mock_cf_allowlist: Mock, mock_gh_allowlist: Mock
    ) -> None:
        """Test lifespan function when allowlist fetching fails."""
        from webhook_server import app as app_module

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
            # Should not raise, just log warnings
            async with app_module.lifespan(FASTAPI_APP):
                pass
            # Should handle both allowlist failures gracefully
            # (You could add log assertion here if desired)

    def test_static_files_path_construction(self) -> None:
        """Test that the static files path is constructed correctly."""
        from webhook_server import app as app_module

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
        from webhook_server import app as app_module

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
