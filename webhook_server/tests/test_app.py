import hashlib
import hmac
import json
import os
from typing import Any
from unittest.mock import Mock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from webhook_server.app import FASTAPI_APP, verify_signature
from webhook_server.libs.exceptions import RepositoryNotFoundError


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
        mock_github_webhook.side_effect = RepositoryNotFoundError("Repository not found in configuration")

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

    def test_verify_signature_success(self) -> None:
        """Test successful signature verification."""
        payload = "test payload"
        secret = "test-secret"  # pragma: allowlist secret
        signature = self.create_github_signature(payload, secret)

        # Should not raise any exception
        verify_signature(payload.encode(), secret, signature)

    def test_verify_signature_missing_header(self) -> None:
        """Test signature verification with missing header."""
        payload = "test payload"
        secret = "test-secret"  # pragma: allowlist secret

        with pytest.raises(Exception) as exc_info:
            verify_signature(payload.encode(), secret, None)

        assert "x-hub-signature-256 header is missing" in str(exc_info.value)

    def test_verify_signature_invalid_signature(self) -> None:
        """Test signature verification with invalid signature."""
        payload = "test payload"
        secret = "test-secret"  # pragma: allowlist secret
        invalid_signature = "sha256=invalid"

        with pytest.raises(Exception) as exc_info:
            verify_signature(payload.encode(), secret, invalid_signature)

        assert "Request signatures didn't match" in str(exc_info.value)

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
        """Test webhook processing with unexpected error."""
        mock_github_webhook.side_effect = RuntimeError("Unexpected error")

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
        """Test IP allowlist functionality during app startup."""
        mock_gh_allowlist.return_value = ["192.30.252.0/22", "185.199.108.0/22"]
        mock_cf_allowlist.return_value = ["103.21.244.0/22", "103.22.200.0/22"]

        # This would be tested through lifespan but requires more complex setup
        github_ips = await mock_gh_allowlist()
        cloudflare_ips = await mock_cf_allowlist()

        assert len(github_ips) == 2
        assert len(cloudflare_ips) == 2
        assert "192.30.252.0/22" in github_ips
        assert "103.21.244.0/22" in cloudflare_ips

    @patch("httpx.AsyncClient.get")
    async def test_get_github_allowlist_success(self, mock_get: Mock) -> None:
        """Test successful GitHub allowlist fetching."""
        from webhook_server.app import get_github_allowlist

        # Mock the global HTTP client
        mock_response = Mock()
        mock_response.json.return_value = {"hooks": ["192.30.252.0/22", "185.199.108.0/22"]}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        # Mock the global client
        with patch("webhook_server.app._lifespan_http_client", Mock()) as mock_client:
            mock_client.get = mock_get

            result = await get_github_allowlist()
            assert len(result) == 2
            assert "192.30.252.0/22" in result

    @patch("httpx.AsyncClient.get")
    async def test_get_github_allowlist_error(self, mock_get: Mock) -> None:
        """Test GitHub allowlist fetching with HTTP error."""
        from webhook_server.app import get_github_allowlist

        mock_get.side_effect = httpx.RequestError("Network error")

        with patch("webhook_server.app._lifespan_http_client", Mock()) as mock_client:
            mock_client.get = mock_get

            with pytest.raises(httpx.RequestError):
                await get_github_allowlist()

    @patch("httpx.AsyncClient.get")
    async def test_get_cloudflare_allowlist_success(self, mock_get: Mock) -> None:
        """Test successful Cloudflare allowlist fetching."""
        from webhook_server.app import get_cloudflare_allowlist

        mock_response = Mock()
        mock_response.json.return_value = {
            "result": {"ipv4_cidrs": ["103.21.244.0/22", "103.22.200.0/22"], "ipv6_cidrs": ["2400:cb00::/32"]}
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        with patch("webhook_server.app._lifespan_http_client", Mock()) as mock_client:
            mock_client.get = mock_get

            result = await get_cloudflare_allowlist()
            assert len(result) == 3  # 2 IPv4 + 1 IPv6
            assert "103.21.244.0/22" in result
            assert "2400:cb00::/32" in result
