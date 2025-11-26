"""
Comprehensive tests for metrics API endpoints.

Tests 7 metrics endpoints:
- GET /api/metrics/webhooks - List webhook events with filtering
- GET /api/metrics/webhooks/{delivery_id} - Get specific webhook details
- GET /api/metrics/repositories - Get repository statistics
- GET /api/metrics/summary - Get overall metrics summary
- GET /api/metrics/contributors - Get PR contributors statistics
- GET /api/metrics/user-prs - Get per-user PR metrics
- GET /api/metrics/trends - Get metrics trends over time
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import webhook_server.app
from webhook_server.app import FASTAPI_APP
from webhook_server.libs.database import DatabaseManager


@pytest.fixture(autouse=True)
def enable_metrics_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable metrics server for all tests in this module."""
    monkeypatch.setattr(webhook_server.app, "METRICS_SERVER_ENABLED", True)


@pytest.fixture
def setup_db_manager(mock_db_manager: Mock, monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Set up global db_manager for metrics endpoints.

    This fixture prevents the app lifespan from constructing a real DatabaseManager
    by monkeypatching the DatabaseManager class to return the mock, ensuring that
    any DatabaseManager() instantiation during startup uses the mock and its
    connect()/disconnect() are no-ops.
    """
    # Monkeypatch DatabaseManager class to return the mock when instantiated
    # This prevents lifespan from creating a real DB connection at line 260
    monkeypatch.setattr(DatabaseManager, "__new__", lambda *_args, **_kwargs: mock_db_manager)

    # Also set the global db_manager for request handling
    monkeypatch.setattr(webhook_server.app, "db_manager", mock_db_manager)

    # Mock connect/disconnect to prevent real DB operations during lifespan
    mock_db_manager.connect = AsyncMock(return_value=None)
    mock_db_manager.disconnect = AsyncMock(return_value=None)

    return mock_db_manager


class TestMetricsAPIEndpoints:
    """Test metrics API endpoints for webhook analytics."""

    @pytest.fixture
    def client(self, setup_db_manager: Mock) -> TestClient:
        """FastAPI test client.

        Depends on setup_db_manager to ensure DatabaseManager is mocked
        before the app lifespan runs.
        """
        return TestClient(FASTAPI_APP)

    @pytest.fixture
    def mock_db_manager(self) -> Mock:
        """Mock database manager with helper methods."""
        db_manager = Mock()

        # Mock the helper methods that DatabaseManager provides
        db_manager.fetch = AsyncMock(return_value=[])
        db_manager.fetchrow = AsyncMock(return_value=None)
        db_manager.fetchval = AsyncMock(return_value=0)
        db_manager.execute = AsyncMock(return_value="INSERT 0 1")

        # Mock pool for tests that check pool existence
        db_manager.pool = Mock()

        return db_manager


class TestRequireMetricsServerEnabled(TestMetricsAPIEndpoints):
    """Test require_metrics_server_enabled dependency."""

    def test_metrics_endpoint_requires_enabled_server(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test metrics endpoints return 404 when metrics server is disabled."""
        # Override the module-level fixture to disable metrics server
        monkeypatch.setattr(webhook_server.app, "METRICS_SERVER_ENABLED", False)

        # Try all metrics endpoints
        endpoints = [
            "/api/metrics/webhooks",
            "/api/metrics/webhooks/test-delivery-123",
            "/api/metrics/repositories",
            "/api/metrics/summary",
        ]

        for endpoint in endpoints:
            response = client.get(endpoint)
            assert response.status_code == 404
            assert "Metrics server is disabled" in response.json()["detail"]


class TestGetWebhookEventsEndpoint(TestMetricsAPIEndpoints):
    """Test GET /api/metrics/webhooks endpoint."""

    def test_get_webhook_events_success_no_filters(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test getting webhook events without filters."""
        # Mock database query results
        now = datetime.now(UTC)

        # Mock fetchval (count query)
        setup_db_manager.fetchval.return_value = 2

        # Mock fetch (main query)
        setup_db_manager.fetch.return_value = [
            {
                "delivery_id": "test-delivery-1",
                "repository": "org/repo1",
                "event_type": "pull_request",
                "action": "opened",
                "pr_number": 42,
                "sender": "user1",
                "status": "success",
                "created_at": now,
                "processed_at": now + timedelta(seconds=1),
                "duration_ms": 1000,
                "api_calls_count": 5,
                "token_spend": 10,
                "token_remaining": 4990,
                "error_message": None,
            },
            {
                "delivery_id": "test-delivery-2",
                "repository": "org/repo2",
                "event_type": "issue_comment",
                "action": "created",
                "pr_number": None,
                "sender": "user2",
                "status": "error",
                "created_at": now - timedelta(minutes=5),
                "processed_at": now - timedelta(minutes=4, seconds=58),
                "duration_ms": 2000,
                "api_calls_count": 3,
                "token_spend": 5,
                "token_remaining": 4995,
                "error_message": "Processing failed",
            },
        ]

        response = client.get("/api/metrics/webhooks")

        assert response.status_code == 200
        data = response.json()

        assert len(data["data"]) == 2
        assert data["pagination"]["total"] == 2
        assert data["pagination"]["has_next"] is False

        # Verify first event
        event1 = data["data"][0]
        assert event1["delivery_id"] == "test-delivery-1"
        assert event1["repository"] == "org/repo1"
        assert event1["event_type"] == "pull_request"
        assert event1["action"] == "opened"
        assert event1["pr_number"] == 42
        assert event1["status"] == "success"
        assert event1["duration_ms"] == 1000
        assert event1["error_message"] is None

        # Verify second event
        event2 = data["data"][1]
        assert event2["status"] == "error"
        assert event2["error_message"] == "Processing failed"

    def test_get_webhook_events_with_repository_filter(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test filtering webhook events by repository."""
        setup_db_manager.fetchval.return_value = 1
        now = datetime.now(UTC)

        setup_db_manager.fetch.return_value = [
            {
                "delivery_id": "test-delivery-1",
                "repository": "org/repo1",
                "event_type": "pull_request",
                "action": "opened",
                "pr_number": 42,
                "sender": "user1",
                "status": "success",
                "created_at": now,
                "processed_at": now,
                "duration_ms": 1000,
                "api_calls_count": 5,
                "token_spend": 10,
                "token_remaining": 4990,
                "error_message": None,
            }
        ]

        response = client.get("/api/metrics/webhooks?repository=org/repo1")

        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["repository"] == "org/repo1"

    def test_get_webhook_events_with_event_type_filter(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test filtering webhook events by event type."""
        setup_db_manager.fetchval.return_value = 1
        now = datetime.now(UTC)

        setup_db_manager.fetch.return_value = [
            {
                "delivery_id": "test-delivery-1",
                "repository": "org/repo1",
                "event_type": "check_run",
                "action": "completed",
                "pr_number": 42,
                "sender": "github-actions",
                "status": "success",
                "created_at": now,
                "processed_at": now,
                "duration_ms": 500,
                "api_calls_count": 2,
                "token_spend": 2,
                "token_remaining": 4998,
                "error_message": None,
            }
        ]

        response = client.get("/api/metrics/webhooks?event_type=check_run")

        assert response.status_code == 200
        data = response.json()
        assert data["data"][0]["event_type"] == "check_run"

    def test_get_webhook_events_with_status_filter(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test filtering webhook events by status."""
        setup_db_manager.fetchval.return_value = 1
        now = datetime.now(UTC)

        setup_db_manager.fetch.return_value = [
            {
                "delivery_id": "test-delivery-error",
                "repository": "org/repo1",
                "event_type": "pull_request",
                "action": "opened",
                "pr_number": 99,
                "sender": "user1",
                "status": "error",
                "created_at": now,
                "processed_at": now,
                "duration_ms": 5000,
                "api_calls_count": 10,
                "token_spend": 10,
                "token_remaining": 4990,
                "error_message": "Connection timeout",
            }
        ]

        response = client.get("/api/metrics/webhooks?status=error")

        assert response.status_code == 200
        data = response.json()
        assert data["data"][0]["status"] == "error"
        assert data["data"][0]["error_message"] == "Connection timeout"

        # Verify DB queries were executed (fetchval for count, fetch for results)
        setup_db_manager.fetchval.assert_called_once()
        setup_db_manager.fetch.assert_called_once()

    def test_get_webhook_events_with_time_filters(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test filtering webhook events by time range."""
        setup_db_manager.fetchval.return_value = 1
        now = datetime.now(UTC)

        setup_db_manager.fetch.return_value = [
            {
                "delivery_id": "test-delivery-1",
                "repository": "org/repo1",
                "event_type": "pull_request",
                "action": "opened",
                "pr_number": 42,
                "sender": "user1",
                "status": "success",
                "created_at": now,
                "processed_at": now,
                "duration_ms": 1000,
                "api_calls_count": 5,
                "token_spend": 10,
                "token_remaining": 4990,
                "error_message": None,
            }
        ]

        start_time = quote((now - timedelta(hours=1)).isoformat())
        end_time = quote((now + timedelta(hours=1)).isoformat())

        response = client.get(f"/api/metrics/webhooks?start_time={start_time}&end_time={end_time}")

        assert response.status_code == 200

    def test_get_webhook_events_pagination(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test webhook events pagination."""
        setup_db_manager.fetchval.return_value = 150  # Total count
        now = datetime.now(UTC)

        # Generate 50 mock events
        mock_events = [
            {
                "delivery_id": f"test-delivery-{i}",
                "repository": "org/repo1",
                "event_type": "pull_request",
                "action": "opened",
                "pr_number": i,
                "sender": "user1",
                "status": "success",
                "created_at": now,
                "processed_at": now,
                "duration_ms": 1000,
                "api_calls_count": 5,
                "token_spend": 10,
                "token_remaining": 4990,
                "error_message": None,
            }
            for i in range(50)
        ]
        setup_db_manager.fetch.return_value = mock_events

        response = client.get("/api/metrics/webhooks?page=1&page_size=50")

        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 50
        assert data["pagination"]["total"] == 150
        assert data["pagination"]["page_size"] == 50
        assert data["pagination"]["has_next"] is True

    def test_get_webhook_events_db_manager_none(self, client: TestClient) -> None:
        """Test endpoint returns 500 when db_manager is None."""
        with patch("webhook_server.app.db_manager", None):
            response = client.get("/api/metrics/webhooks")

            assert response.status_code == 500
            assert "Metrics database not available" in response.json()["detail"]

    def test_get_webhook_events_pool_none(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test endpoint returns 500 when database pool is not initialized."""
        # Simulate pool not initialized - helper methods raise ValueError
        setup_db_manager.pool = None
        setup_db_manager.fetchval.side_effect = ValueError("Database pool not initialized. Call connect() first.")

        response = client.get("/api/metrics/webhooks")

        assert response.status_code == 500
        assert "Failed to fetch webhook events" in response.json()["detail"]

    def test_get_webhook_events_database_error(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test endpoint handles database errors gracefully."""
        setup_db_manager.fetchval.side_effect = Exception("Database connection lost")

        response = client.get("/api/metrics/webhooks")

        assert response.status_code == 500
        assert "Failed to fetch webhook events" in response.json()["detail"]


class TestGetWebhookEventByIdEndpoint(TestMetricsAPIEndpoints):
    """Test GET /api/metrics/webhooks/{delivery_id} endpoint."""

    def test_get_webhook_event_by_id_success(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test getting specific webhook event by delivery ID."""
        now = datetime.now(UTC)

        setup_db_manager.fetchrow.return_value = {
            "delivery_id": "test-delivery-123",
            "repository": "org/repo",
            "event_type": "pull_request",
            "action": "opened",
            "pr_number": 42,
            "sender": "user1",
            "status": "success",
            "created_at": now,
            "processed_at": now + timedelta(seconds=1),
            "duration_ms": 1000,
            "api_calls_count": 5,
            "token_spend": 10,
            "token_remaining": 4990,
            "error_message": None,
            "payload": {"key": "value", "nested": {"data": "test"}},
        }

        response = client.get("/api/metrics/webhooks/test-delivery-123")

        assert response.status_code == 200
        data = response.json()
        assert data["delivery_id"] == "test-delivery-123"
        assert data["repository"] == "org/repo"
        assert data["status"] == "success"
        assert data["payload"] == {"key": "value", "nested": {"data": "test"}}

    def test_get_webhook_event_by_id_not_found(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test getting non-existent webhook event returns 404."""
        setup_db_manager.fetchrow.return_value = None

        response = client.get("/api/metrics/webhooks/nonexistent-delivery-id")

        assert response.status_code == 404
        assert "Webhook event not found" in response.json()["detail"]

    def test_get_webhook_event_by_id_db_manager_none(self, client: TestClient) -> None:
        """Test endpoint returns 500 when db_manager is None."""
        with patch("webhook_server.app.db_manager", None):
            response = client.get("/api/metrics/webhooks/test-delivery-123")

            assert response.status_code == 500
            assert "Metrics database not available" in response.json()["detail"]

    def test_get_webhook_event_by_id_pool_none(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test endpoint returns 500 when database pool is not initialized."""
        # Simulate pool not initialized - helper methods raise ValueError
        setup_db_manager.pool = None
        setup_db_manager.fetchrow.side_effect = ValueError("Database pool not initialized. Call connect() first.")

        response = client.get("/api/metrics/webhooks/test-delivery-123")

        assert response.status_code == 500
        assert "Failed to fetch webhook event" in response.json()["detail"]

    def test_get_webhook_event_by_id_database_error(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test endpoint handles database errors gracefully."""
        setup_db_manager.fetchrow.side_effect = Exception("Database connection lost")

        response = client.get("/api/metrics/webhooks/test-delivery-123")

        assert response.status_code == 500
        assert "Failed to fetch webhook event" in response.json()["detail"]


class TestGetRepositoryStatisticsEndpoint(TestMetricsAPIEndpoints):
    """Test GET /api/metrics/repositories endpoint."""

    def test_get_repository_statistics_success(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test getting repository statistics."""
        setup_db_manager.fetchval.return_value = 2
        setup_db_manager.fetch.return_value = [
            {
                "repository": "org/repo1",
                "total_events": 100,
                "successful_events": 95,
                "failed_events": 5,
                "success_rate": 95.00,
                "avg_processing_time_ms": 1500,
                "median_processing_time_ms": 1200,
                "p95_processing_time_ms": 3000,
                "max_processing_time_ms": 5000,
                "total_api_calls": 500,
                "avg_api_calls_per_event": 5.00,
                "total_token_spend": 1000,
                "event_type_breakdown": {"pull_request": 80, "issue_comment": 20},
            },
            {
                "repository": "org/repo2",
                "total_events": 50,
                "successful_events": 48,
                "failed_events": 2,
                "success_rate": 96.00,
                "avg_processing_time_ms": 800,
                "median_processing_time_ms": 750,
                "p95_processing_time_ms": 1500,
                "max_processing_time_ms": 2000,
                "total_api_calls": 200,
                "avg_api_calls_per_event": 4.00,
                "total_token_spend": 400,
                "event_type_breakdown": {"check_run": 30, "pull_request": 20},
            },
        ]

        response = client.get("/api/metrics/repositories")

        assert response.status_code == 200
        data = response.json()
        assert data["pagination"]["total"] == 2
        assert len(data["repositories"]) == 2

        # Verify first repository
        repo1 = data["repositories"][0]
        assert repo1["repository"] == "org/repo1"
        assert repo1["total_events"] == 100
        assert repo1["success_rate"] == 95.00
        assert repo1["event_type_breakdown"] == {"pull_request": 80, "issue_comment": 20}

        # Verify second repository
        repo2 = data["repositories"][1]
        assert repo2["repository"] == "org/repo2"
        assert repo2["total_events"] == 50

    def test_get_repository_statistics_with_time_range(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test getting repository statistics with time range filter."""
        now = datetime.now(UTC)

        setup_db_manager.fetch.return_value = []

        start_time = quote((now - timedelta(days=7)).isoformat())
        end_time = quote(now.isoformat())

        response = client.get(f"/api/metrics/repositories?start_time={start_time}&end_time={end_time}")

        assert response.status_code == 200
        data = response.json()
        assert "time_range" in data
        assert data["time_range"]["start_time"] is not None
        assert data["time_range"]["end_time"] is not None

    def test_get_repository_statistics_empty(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test getting repository statistics when no data exists."""
        setup_db_manager.fetchval.return_value = 0
        setup_db_manager.fetch.return_value = []

        response = client.get("/api/metrics/repositories")

        assert response.status_code == 200
        data = response.json()
        assert data["pagination"]["total"] == 0
        assert data["repositories"] == []

    def test_get_repository_statistics_db_manager_none(self, client: TestClient) -> None:
        """Test endpoint returns 500 when db_manager is None."""
        with patch("webhook_server.app.db_manager", None):
            response = client.get("/api/metrics/repositories")

            assert response.status_code == 500
            assert "Metrics database not available" in response.json()["detail"]

    def test_get_repository_statistics_pool_none(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test endpoint returns 500 when database pool is not initialized."""
        # Simulate pool not initialized - helper methods raise ValueError
        setup_db_manager.pool = None
        setup_db_manager.fetch.side_effect = ValueError("Database pool not initialized. Call connect() first.")

        response = client.get("/api/metrics/repositories")

        assert response.status_code == 500
        assert "Failed to fetch repository statistics" in response.json()["detail"]

    def test_get_repository_statistics_database_error(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test endpoint handles database errors gracefully."""
        setup_db_manager.fetch.side_effect = Exception("Database connection lost")

        response = client.get("/api/metrics/repositories")

        assert response.status_code == 500
        assert "Failed to fetch repository statistics" in response.json()["detail"]


class TestGetMetricsSummaryEndpoint(TestMetricsAPIEndpoints):
    """Test GET /api/metrics/summary endpoint."""

    def test_get_metrics_summary_success(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test getting overall metrics summary."""
        now = datetime.now(UTC)

        # Mock summary query
        setup_db_manager.fetchrow.side_effect = [
            # Summary row
            {
                "total_events": 1000,
                "successful_events": 950,
                "failed_events": 50,
                "success_rate": 95.00,
                "avg_processing_time_ms": 1500,
                "median_processing_time_ms": 1200,
                "p95_processing_time_ms": 3000,
                "max_processing_time_ms": 8000,
                "total_api_calls": 5000,
                "avg_api_calls_per_event": 5.00,
                "total_token_spend": 10000,
            },
            # Time range row
            {
                "first_event_time": now - timedelta(days=7),
                "last_event_time": now,
            },
        ]

        # Mock top repositories query
        setup_db_manager.fetch.side_effect = [
            # Top repos
            [
                {"repository": "org/repo1", "total_events": 600, "success_rate": 96.00, "percentage": 60.00},
                {"repository": "org/repo2", "total_events": 400, "success_rate": 94.00, "percentage": 40.00},
            ],
            # Event type distribution
            [
                {"event_type": "pull_request", "event_count": 700},
                {"event_type": "issue_comment", "event_count": 200},
                {"event_type": "check_run", "event_count": 100},
            ],
        ]

        response = client.get("/api/metrics/summary")

        assert response.status_code == 200
        data = response.json()

        # Verify summary
        assert data["summary"]["total_events"] == 1000
        assert data["summary"]["successful_events"] == 950
        assert data["summary"]["success_rate"] == 95.00

        # Verify top repositories
        assert len(data["top_repositories"]) == 2
        assert data["top_repositories"][0]["repository"] == "org/repo1"

        # Verify event type distribution
        assert data["event_type_distribution"]["pull_request"] == 700
        assert data["event_type_distribution"]["issue_comment"] == 200

        # Verify event rates
        assert "hourly_event_rate" in data
        assert "daily_event_rate" in data

    def test_get_metrics_summary_with_time_range(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test getting metrics summary with time range filter."""
        now = datetime.now(UTC)

        setup_db_manager.fetchrow.side_effect = [
            # Summary row
            {
                "total_events": 100,
                "successful_events": 95,
                "failed_events": 5,
                "success_rate": 95.00,
                "avg_processing_time_ms": 1500,
                "median_processing_time_ms": 1200,
                "p95_processing_time_ms": 3000,
                "max_processing_time_ms": 5000,
                "total_api_calls": 500,
                "avg_api_calls_per_event": 5.00,
                "total_token_spend": 1000,
            },
            # Time range row
            {
                "first_event_time": now - timedelta(hours=24),
                "last_event_time": now,
            },
            # Previous period summary row (for trend calculation)
            {
                "total_events": 90,
                "successful_events": 85,
                "failed_events": 5,
                "success_rate": 94.44,
                "avg_processing_time_ms": 1600,
            },
        ]

        setup_db_manager.fetch.side_effect = [[], []]

        start_time = quote((now - timedelta(days=1)).isoformat())
        end_time = quote(now.isoformat())

        response = client.get(f"/api/metrics/summary?start_time={start_time}&end_time={end_time}")

        assert response.status_code == 200
        data = response.json()
        assert "time_range" in data
        assert data["time_range"]["start_time"] is not None

    def test_get_metrics_summary_empty(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test getting metrics summary when no data exists."""
        setup_db_manager.fetchrow.side_effect = [
            {
                "total_events": 0,
                "successful_events": 0,
                "failed_events": 0,
                "success_rate": None,
                "avg_processing_time_ms": None,
                "median_processing_time_ms": None,
                "p95_processing_time_ms": None,
                "max_processing_time_ms": None,
                "total_api_calls": None,
                "avg_api_calls_per_event": None,
                "total_token_spend": None,
            },
            None,
        ]

        setup_db_manager.fetch.side_effect = [[], []]

        response = client.get("/api/metrics/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["summary"]["total_events"] == 0
        assert data["top_repositories"] == []
        assert data["event_type_distribution"] == {}

    def test_get_metrics_summary_db_manager_none(self, client: TestClient) -> None:
        """Test endpoint returns 500 when db_manager is None."""
        with patch("webhook_server.app.db_manager", None):
            response = client.get("/api/metrics/summary")

            assert response.status_code == 500
            assert "Metrics database not available" in response.json()["detail"]

    def test_get_metrics_summary_pool_none(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test endpoint returns 500 when database pool is not initialized."""
        # Simulate pool not initialized - helper methods raise ValueError
        setup_db_manager.pool = None
        setup_db_manager.fetchrow.side_effect = ValueError("Database pool not initialized. Call connect() first.")

        response = client.get("/api/metrics/summary")

        assert response.status_code == 500
        assert "Failed to fetch metrics summary" in response.json()["detail"]

    def test_get_metrics_summary_database_error(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test endpoint handles database errors gracefully."""
        setup_db_manager.fetchrow.side_effect = Exception("Database connection lost")

        response = client.get("/api/metrics/summary")

        assert response.status_code == 500
        assert "Failed to fetch metrics summary" in response.json()["detail"]


class TestUserPullRequestsEndpoint(TestMetricsAPIEndpoints):
    """Test GET /api/metrics/user-prs endpoint."""

    def test_get_user_prs_success(self, client: TestClient, setup_db_manager: Mock) -> None:
        """Test successful retrieval of user's pull requests."""
        # Mock database responses
        setup_db_manager.fetchrow.return_value = {"total": 2}
        setup_db_manager.fetch.return_value = [
            {
                "pr_number": 123,
                "title": "Add feature X",
                "repository": "org/repo1",
                "state": "closed",
                "merged": True,
                "url": "https://github.com/org/repo1/pull/123",
                "created_at": "2024-11-20T10:00:00Z",
                "updated_at": "2024-11-21T15:30:00Z",
                "commits_count": 5,
                "head_sha": "abc123def456",  # pragma: allowlist secret
            },
            {
                "pr_number": 124,
                "title": "Fix bug Y",
                "repository": "org/repo1",
                "state": "open",
                "merged": False,
                "url": "https://github.com/org/repo1/pull/124",
                "created_at": "2024-11-22T09:00:00Z",
                "updated_at": "2024-11-22T09:00:00Z",
                "commits_count": 2,
                "head_sha": "def456abc789",  # pragma: allowlist secret
            },
        ]

        response = client.get("/api/metrics/user-prs?user=john-doe&page=1&page_size=10")

        assert response.status_code == 200
        data = response.json()

        # Check data structure
        assert "data" in data
        assert "pagination" in data
        assert len(data["data"]) == 2

        # Verify first PR
        pr1 = data["data"][0]
        assert pr1["number"] == 123
        assert pr1["title"] == "Add feature X"
        assert pr1["repository"] == "org/repo1"
        assert pr1["state"] == "closed"
        assert pr1["merged"] is True
        assert pr1["commits_count"] == 5

        # Verify pagination
        pagination = data["pagination"]
        assert pagination["total"] == 2
        assert pagination["page"] == 1
        assert pagination["page_size"] == 10
        assert pagination["total_pages"] == 1
        assert pagination["has_next"] is False
        assert pagination["has_prev"] is False

    def test_get_user_prs_with_repository_filter(self, client: TestClient, setup_db_manager: Mock) -> None:
        """Test filtering by repository."""
        setup_db_manager.fetchrow.return_value = {"total": 1}
        setup_db_manager.fetch.return_value = [
            {
                "pr_number": 123,
                "title": "Add feature X",
                "repository": "org/repo1",
                "state": "closed",
                "merged": True,
                "url": "https://github.com/org/repo1/pull/123",
                "created_at": "2024-11-20T10:00:00Z",
                "updated_at": "2024-11-21T15:30:00Z",
                "commits_count": 5,
                "head_sha": "abc123",
            }
        ]

        response = client.get("/api/metrics/user-prs?user=john-doe&repository=org/repo1")

        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["repository"] == "org/repo1"

    def test_get_user_prs_with_time_range(self, client: TestClient, setup_db_manager: Mock) -> None:
        """Test filtering by time range."""
        setup_db_manager.fetchrow.return_value = {"total": 1}
        setup_db_manager.fetch.return_value = []

        start_time = "2024-11-01T00:00:00Z"
        end_time = "2024-11-30T23:59:59Z"

        response = client.get(f"/api/metrics/user-prs?user=john-doe&start_time={start_time}&end_time={end_time}")

        assert response.status_code == 200

    def test_get_user_prs_pagination(self, client: TestClient, setup_db_manager: Mock) -> None:
        """Test pagination with multiple pages."""
        # Total of 25 PRs, page size 10
        setup_db_manager.fetchrow.return_value = {"total": 25}
        setup_db_manager.fetch.return_value = []

        # Test page 2
        response = client.get("/api/metrics/user-prs?user=john-doe&page=2&page_size=10")

        assert response.status_code == 200
        data = response.json()

        pagination = data["pagination"]
        assert pagination["total"] == 25
        assert pagination["page"] == 2
        assert pagination["page_size"] == 10
        assert pagination["total_pages"] == 3
        assert pagination["has_next"] is True
        assert pagination["has_prev"] is True

    def test_get_user_prs_empty_result(self, client: TestClient, setup_db_manager: Mock) -> None:
        """Test endpoint with no matching PRs."""
        setup_db_manager.fetchrow.return_value = {"total": 0}
        setup_db_manager.fetch.return_value = []

        response = client.get("/api/metrics/user-prs?user=nonexistent-user")

        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 0
        assert data["pagination"]["total"] == 0
        assert data["pagination"]["total_pages"] == 0

    def test_get_user_prs_no_user_parameter(self, client: TestClient, setup_db_manager: Mock) -> None:
        """Test endpoint works without user parameter (shows all PRs)."""
        setup_db_manager.fetchrow.return_value = {"total": 2}
        setup_db_manager.fetch.return_value = [
            {
                "pr_number": 123,
                "title": "Add feature X",
                "repository": "org/repo1",
                "state": "closed",
                "merged": True,
                "url": "https://github.com/org/repo1/pull/123",
                "created_at": "2024-11-20T10:00:00Z",
                "updated_at": "2024-11-21T15:30:00Z",
                "commits_count": 5,
                "head_sha": "abc123",
            },
            {
                "pr_number": 124,
                "title": "Fix bug Y",
                "repository": "org/repo2",
                "state": "open",
                "merged": False,
                "url": "https://github.com/org/repo2/pull/124",
                "created_at": "2024-11-22T09:00:00Z",
                "updated_at": "2024-11-22T09:00:00Z",
                "commits_count": 2,
                "head_sha": "def456",
            },
        ]

        response = client.get("/api/metrics/user-prs")

        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 2
        assert data["pagination"]["total"] == 2

    def test_get_user_prs_invalid_page_number(self, client: TestClient) -> None:
        """Test endpoint fails with invalid page number."""
        response = client.get("/api/metrics/user-prs?user=john-doe&page=0")

        assert response.status_code == 422  # FastAPI validation error

    def test_get_user_prs_invalid_page_size(self, client: TestClient) -> None:
        """Test endpoint fails with invalid page size."""
        # Too large
        response = client.get("/api/metrics/user-prs?user=john-doe&page_size=101")
        assert response.status_code == 422

        # Too small
        response = client.get("/api/metrics/user-prs?user=john-doe&page_size=0")
        assert response.status_code == 422

    def test_get_user_prs_database_error(self, client: TestClient, setup_db_manager: Mock) -> None:
        """Test endpoint handles database errors gracefully."""
        setup_db_manager.fetchrow.side_effect = Exception("Database connection lost")

        response = client.get("/api/metrics/user-prs?user=john-doe")

        assert response.status_code == 500
        assert "Failed to fetch user pull requests" in response.json()["detail"]

    def test_get_user_prs_null_commits_count(self, client: TestClient, setup_db_manager: Mock) -> None:
        """Test endpoint handles null commits_count gracefully."""
        setup_db_manager.fetchrow.return_value = {"total": 1}
        setup_db_manager.fetch.return_value = [
            {
                "pr_number": 123,
                "title": "Add feature X",
                "repository": "org/repo1",
                "state": "open",
                "merged": False,
                "url": "https://github.com/org/repo1/pull/123",
                "created_at": "2024-11-20T10:00:00Z",
                "updated_at": "2024-11-21T15:30:00Z",
                "commits_count": None,  # NULL from database
                "head_sha": "abc123",
            }
        ]

        response = client.get("/api/metrics/user-prs?user=john-doe")

        assert response.status_code == 200
        data = response.json()
        assert data["data"][0]["commits_count"] == 0  # NULL converted to 0

    def test_get_user_prs_metrics_server_disabled(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test endpoint returns 404 when metrics server is disabled."""
        monkeypatch.setattr(webhook_server.app, "METRICS_SERVER_ENABLED", False)

        response = client.get("/api/metrics/user-prs?user=john-doe")

        assert response.status_code == 404

    def test_get_user_prs_combined_filters(self, client: TestClient, setup_db_manager: Mock) -> None:
        """Test endpoint with all filters combined."""
        setup_db_manager.fetchrow.return_value = {"total": 1}
        setup_db_manager.fetch.return_value = []

        response = client.get(
            "/api/metrics/user-prs"
            "?user=john-doe"
            "&repository=org/repo1"
            "&start_time=2024-11-01T00:00:00Z"
            "&end_time=2024-11-30T23:59:59Z"
            "&page=1"
            "&page_size=20"
        )

        assert response.status_code == 200


class TestGetContributorsEndpoint(TestMetricsAPIEndpoints):
    """Test GET /api/metrics/contributors endpoint."""

    def test_get_contributors_success(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test getting contributors statistics with all categories."""
        # Mock count queries (fetchval calls) - 4 categories
        setup_db_manager.fetchval.side_effect = [
            5,  # pr_creators_total
            3,  # pr_reviewers_total
            4,  # pr_approvers_total
            2,  # pr_lgtm_total
        ]

        # Mock data queries (fetch calls) - 4 categories
        setup_db_manager.fetch.side_effect = [
            # pr_creators
            [
                {
                    "user": "john-doe",
                    "total_prs": 45,
                    "merged_prs": 42,
                    "closed_prs": 3,
                    "avg_commits": 3.5,
                },
                {
                    "user": "jane-smith",
                    "total_prs": 30,
                    "merged_prs": 28,
                    "closed_prs": 2,
                    "avg_commits": 2.8,
                },
            ],
            # pr_reviewers
            [
                {
                    "user": "bob-wilson",
                    "total_reviews": 78,
                    "prs_reviewed": 65,
                },
                {
                    "user": "alice-jones",
                    "total_reviews": 56,
                    "prs_reviewed": 48,
                },
            ],
            # pr_approvers
            [
                {
                    "user": "charlie-brown",
                    "total_approvals": 56,
                    "prs_approved": 54,
                },
                {
                    "user": "diana-prince",
                    "total_approvals": 40,
                    "prs_approved": 38,
                },
            ],
            # pr_lgtm
            [
                {
                    "user": "eve-adams",
                    "total_lgtm": 42,
                    "prs_lgtm": 40,
                },
                {
                    "user": "frank-miller",
                    "total_lgtm": 35,
                    "prs_lgtm": 33,
                },
            ],
        ]

        response = client.get("/api/metrics/contributors")

        assert response.status_code == 200
        data = response.json()

        # Verify structure
        assert "time_range" in data
        assert "pr_creators" in data
        assert "pr_reviewers" in data
        assert "pr_approvers" in data
        assert "pr_lgtm" in data

        # Verify pr_creators
        assert len(data["pr_creators"]["data"]) == 2
        creator1 = data["pr_creators"]["data"][0]
        assert creator1["user"] == "john-doe"
        assert creator1["total_prs"] == 45
        assert creator1["merged_prs"] == 42
        assert creator1["closed_prs"] == 3
        assert creator1["avg_commits_per_pr"] == 3.5

        # Verify pr_creators pagination
        assert data["pr_creators"]["pagination"]["total"] == 5
        assert data["pr_creators"]["pagination"]["page"] == 1
        assert data["pr_creators"]["pagination"]["page_size"] == 10
        assert data["pr_creators"]["pagination"]["has_next"] is False
        assert data["pr_creators"]["pagination"]["has_prev"] is False

        # Verify pr_reviewers
        assert len(data["pr_reviewers"]["data"]) == 2
        reviewer1 = data["pr_reviewers"]["data"][0]
        assert reviewer1["user"] == "bob-wilson"
        assert reviewer1["total_reviews"] == 78
        assert reviewer1["prs_reviewed"] == 65
        assert reviewer1["avg_reviews_per_pr"] == 1.2

        # Verify pr_approvers
        assert len(data["pr_approvers"]["data"]) == 2
        approver1 = data["pr_approvers"]["data"][0]
        assert approver1["user"] == "charlie-brown"
        assert approver1["total_approvals"] == 56
        assert approver1["prs_approved"] == 54

        # Verify pr_lgtm
        assert len(data["pr_lgtm"]["data"]) == 2
        lgtm1 = data["pr_lgtm"]["data"][0]
        assert lgtm1["user"] == "eve-adams"
        assert lgtm1["total_lgtm"] == 42
        assert lgtm1["prs_lgtm"] == 40

    def test_get_contributors_with_user_filter(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test filtering contributors by user."""
        # Mock count queries
        setup_db_manager.fetchval.side_effect = [1, 1, 1, 1]

        # Mock data queries
        setup_db_manager.fetch.side_effect = [
            # pr_creators for john-doe
            [
                {
                    "user": "john-doe",
                    "total_prs": 45,
                    "merged_prs": 42,
                    "closed_prs": 3,
                    "avg_commits": 3.5,
                }
            ],
            # pr_reviewers for john-doe
            [
                {
                    "user": "john-doe",
                    "total_reviews": 20,
                    "prs_reviewed": 18,
                }
            ],
            # pr_approvers for john-doe
            [
                {
                    "user": "john-doe",
                    "total_approvals": 15,
                    "prs_approved": 14,
                }
            ],
            # pr_lgtm for john-doe
            [
                {
                    "user": "john-doe",
                    "total_lgtm": 10,
                    "prs_lgtm": 10,
                }
            ],
        ]

        response = client.get("/api/metrics/contributors?user=john-doe")

        assert response.status_code == 200
        data = response.json()

        # Verify all categories filtered to john-doe
        assert len(data["pr_creators"]["data"]) == 1
        assert data["pr_creators"]["data"][0]["user"] == "john-doe"
        assert len(data["pr_reviewers"]["data"]) == 1
        assert data["pr_reviewers"]["data"][0]["user"] == "john-doe"
        assert len(data["pr_approvers"]["data"]) == 1
        assert data["pr_approvers"]["data"][0]["user"] == "john-doe"
        assert len(data["pr_lgtm"]["data"]) == 1
        assert data["pr_lgtm"]["data"][0]["user"] == "john-doe"

    def test_get_contributors_with_repository_filter(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test filtering contributors by repository."""
        # Mock count queries
        setup_db_manager.fetchval.side_effect = [2, 1, 1, 1]

        # Mock data queries
        setup_db_manager.fetch.side_effect = [
            # pr_creators
            [
                {
                    "user": "john-doe",
                    "total_prs": 10,
                    "merged_prs": 9,
                    "closed_prs": 1,
                    "avg_commits": 2.5,
                }
            ],
            # pr_reviewers
            [
                {
                    "user": "jane-smith",
                    "total_reviews": 15,
                    "prs_reviewed": 12,
                }
            ],
            # pr_approvers
            [],
            # pr_lgtm
            [],
        ]

        response = client.get("/api/metrics/contributors?repository=org/repo1")

        assert response.status_code == 200
        data = response.json()

        # Verify data is filtered by repository
        assert len(data["pr_creators"]["data"]) == 1
        assert len(data["pr_reviewers"]["data"]) == 1
        assert len(data["pr_approvers"]["data"]) == 0
        assert len(data["pr_lgtm"]["data"]) == 0

    def test_get_contributors_with_time_range(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test filtering contributors by time range."""
        # Mock count queries
        setup_db_manager.fetchval.side_effect = [1, 1, 0, 0]

        # Mock data queries
        setup_db_manager.fetch.side_effect = [
            [
                {
                    "user": "john-doe",
                    "total_prs": 5,
                    "merged_prs": 5,
                    "closed_prs": 0,
                    "avg_commits": 2.0,
                }
            ],
            [
                {
                    "user": "jane-smith",
                    "total_reviews": 8,
                    "prs_reviewed": 7,
                }
            ],
            [],
            [],
        ]

        start_time = "2024-11-01T00:00:00Z"
        end_time = "2024-11-30T23:59:59Z"

        response = client.get(f"/api/metrics/contributors?start_time={start_time}&end_time={end_time}")

        assert response.status_code == 200
        data = response.json()

        # Verify time range is included in response
        assert data["time_range"]["start_time"] == "2024-11-01T00:00:00+00:00"
        assert data["time_range"]["end_time"] == "2024-11-30T23:59:59+00:00"

    def test_get_contributors_pagination(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test contributors pagination with multiple pages."""
        # Mock count queries - 25 total in each category
        setup_db_manager.fetchval.side_effect = [25, 25, 25, 25]

        # Mock data queries - page 2 of size 10
        setup_db_manager.fetch.side_effect = [
            # pr_creators page 2
            [
                {
                    "user": f"user-{i}",
                    "total_prs": 10 - i,
                    "merged_prs": 9 - i,
                    "closed_prs": 1,
                    "avg_commits": 2.5,
                }
                for i in range(10, 20)
            ],
            # pr_reviewers page 2
            [
                {
                    "user": f"reviewer-{i}",
                    "total_reviews": 50 - i,
                    "prs_reviewed": 40 - i,
                }
                for i in range(10, 20)
            ],
            # pr_approvers page 2
            [],
            # pr_lgtm page 2
            [],
        ]

        response = client.get("/api/metrics/contributors?page=2&page_size=10")

        assert response.status_code == 200
        data = response.json()

        # Verify pagination for pr_creators
        pagination = data["pr_creators"]["pagination"]
        assert pagination["total"] == 25
        assert pagination["page"] == 2
        assert pagination["page_size"] == 10
        assert pagination["total_pages"] == 3
        assert pagination["has_next"] is True
        assert pagination["has_prev"] is True

        # Verify pagination for pr_reviewers
        pagination = data["pr_reviewers"]["pagination"]
        assert pagination["total"] == 25
        assert pagination["page"] == 2
        assert pagination["total_pages"] == 3
        assert pagination["has_next"] is True
        assert pagination["has_prev"] is True

    def test_get_contributors_empty_results(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test contributors endpoint with no data."""
        # Mock count queries - all zeros
        setup_db_manager.fetchval.side_effect = [0, 0, 0, 0]

        # Mock data queries - all empty
        setup_db_manager.fetch.side_effect = [[], [], [], []]

        response = client.get("/api/metrics/contributors")

        assert response.status_code == 200
        data = response.json()

        # Verify all categories are empty
        assert len(data["pr_creators"]["data"]) == 0
        assert data["pr_creators"]["pagination"]["total"] == 0
        assert data["pr_creators"]["pagination"]["total_pages"] == 0

        assert len(data["pr_reviewers"]["data"]) == 0
        assert data["pr_reviewers"]["pagination"]["total"] == 0

        assert len(data["pr_approvers"]["data"]) == 0
        assert data["pr_approvers"]["pagination"]["total"] == 0

        assert len(data["pr_lgtm"]["data"]) == 0
        assert data["pr_lgtm"]["pagination"]["total"] == 0

    def test_get_contributors_combined_filters(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test contributors endpoint with all filters combined."""
        # Mock count queries
        setup_db_manager.fetchval.side_effect = [1, 1, 1, 0]

        # Mock data queries
        setup_db_manager.fetch.side_effect = [
            [
                {
                    "user": "john-doe",
                    "total_prs": 5,
                    "merged_prs": 5,
                    "closed_prs": 0,
                    "avg_commits": 2.0,
                }
            ],
            [
                {
                    "user": "john-doe",
                    "total_reviews": 3,
                    "prs_reviewed": 3,
                }
            ],
            [
                {
                    "user": "john-doe",
                    "total_approvals": 2,
                    "prs_approved": 2,
                }
            ],
            [],
        ]

        response = client.get(
            "/api/metrics/contributors"
            "?user=john-doe"
            "&repository=org/repo1"
            "&start_time=2024-11-01T00:00:00Z"
            "&end_time=2024-11-30T23:59:59Z"
            "&page=1"
            "&page_size=20"
        )

        assert response.status_code == 200
        data = response.json()

        # Verify time range
        assert data["time_range"]["start_time"] == "2024-11-01T00:00:00+00:00"
        assert data["time_range"]["end_time"] == "2024-11-30T23:59:59+00:00"

        # Verify pagination reflects custom page_size
        assert data["pr_creators"]["pagination"]["page_size"] == 20

    def test_get_contributors_null_values_handling(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test contributors endpoint handles null values gracefully."""
        # Mock count queries
        setup_db_manager.fetchval.side_effect = [1, 1, 1, 1]

        # Mock data queries with null values
        setup_db_manager.fetch.side_effect = [
            [
                {
                    "user": "john-doe",
                    "total_prs": 10,
                    "merged_prs": None,  # NULL from database
                    "closed_prs": None,  # NULL from database
                    "avg_commits": None,  # NULL from database
                }
            ],
            [
                {
                    "user": "jane-smith",
                    "total_reviews": 5,
                    "prs_reviewed": 1,
                }
            ],
            [],
            [],
        ]

        response = client.get("/api/metrics/contributors")

        assert response.status_code == 200
        data = response.json()

        # Verify null values are converted to 0
        creator = data["pr_creators"]["data"][0]
        assert creator["merged_prs"] == 0
        assert creator["closed_prs"] == 0
        assert creator["avg_commits_per_pr"] == 0.0

        # Verify avg_reviews_per_pr calculation handles division correctly
        reviewer = data["pr_reviewers"]["data"][0]
        assert reviewer["avg_reviews_per_pr"] == 5.0  # 5 reviews / 1 PR

    def test_get_contributors_invalid_page_number(self, client: TestClient) -> None:
        """Test contributors endpoint with invalid page number."""
        response = client.get("/api/metrics/contributors?page=0")

        assert response.status_code == 422  # FastAPI validation error

    def test_get_contributors_invalid_page_size(self, client: TestClient) -> None:
        """Test contributors endpoint with invalid page size."""
        # Too large
        response = client.get("/api/metrics/contributors?page_size=101")
        assert response.status_code == 422

        # Too small
        response = client.get("/api/metrics/contributors?page_size=0")
        assert response.status_code == 422

    def test_get_contributors_db_manager_none(self, client: TestClient) -> None:
        """Test endpoint returns 500 when db_manager is None."""
        with patch("webhook_server.app.db_manager", None):
            response = client.get("/api/metrics/contributors")

            assert response.status_code == 500
            assert "Metrics database not available" in response.json()["detail"]

    def test_get_contributors_pool_none(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test endpoint returns 500 when database pool is not initialized."""
        # Simulate pool not initialized - helper methods raise ValueError
        setup_db_manager.pool = None
        setup_db_manager.fetchval.side_effect = ValueError("Database pool not initialized. Call connect() first.")

        response = client.get("/api/metrics/contributors")

        assert response.status_code == 500
        assert "Failed to fetch contributor metrics" in response.json()["detail"]

    def test_get_contributors_database_error(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test endpoint handles database errors gracefully."""
        setup_db_manager.fetchval.side_effect = Exception("Database connection lost")

        response = client.get("/api/metrics/contributors")

        assert response.status_code == 500
        assert "Failed to fetch contributor metrics" in response.json()["detail"]

    def test_get_contributors_metrics_server_disabled(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test endpoint returns 404 when metrics server is disabled."""
        # Override the module-level fixture to disable metrics server
        monkeypatch.setattr(webhook_server.app, "METRICS_SERVER_ENABLED", False)

        response = client.get("/api/metrics/contributors")

        assert response.status_code == 404
        assert "Metrics server is disabled" in response.json()["detail"]


class TestGetTrendsEndpoint(TestMetricsAPIEndpoints):
    """Test GET /api/metrics/trends endpoint."""

    def test_get_trends_success(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test getting trends data."""
        now = datetime.now(UTC)

        setup_db_manager.fetch.return_value = [
            {
                "bucket": now - timedelta(hours=2),
                "total_events": 10,
                "successful_events": 9,
                "failed_events": 1,
            },
            {
                "bucket": now - timedelta(hours=1),
                "total_events": 15,
                "successful_events": 14,
                "failed_events": 1,
            },
        ]

        response = client.get("/api/metrics/trends?bucket=hour")

        assert response.status_code == 200
        data = response.json()
        assert len(data["trends"]) == 2
        assert data["trends"][0]["total_events"] == 10
        assert data["trends"][1]["total_events"] == 15

    def test_get_trends_invalid_bucket(self, client: TestClient) -> None:
        """Test trends endpoint with invalid bucket parameter."""
        response = client.get("/api/metrics/trends?bucket=invalid")

        assert response.status_code == 422  # Validation error

    def test_get_trends_day_bucket(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test getting trends data with day bucket."""
        now = datetime.now(UTC)

        setup_db_manager.fetch.return_value = [
            {
                "bucket": now.replace(hour=0, minute=0, second=0, microsecond=0),
                "total_events": 100,
                "successful_events": 95,
                "failed_events": 5,
            },
            {
                "bucket": now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1),
                "total_events": 80,
                "successful_events": 78,
                "failed_events": 2,
            },
        ]

        response = client.get("/api/metrics/trends?bucket=day")

        assert response.status_code == 200
        data = response.json()
        assert len(data["trends"]) == 2
        assert data["trends"][0]["total_events"] == 100
        assert data["trends"][1]["total_events"] == 80

    def test_get_trends_with_time_range(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test trends endpoint with time range filtering."""
        start_time = "2024-11-01T00:00:00Z"
        end_time = "2024-11-30T23:59:59Z"

        setup_db_manager.fetch.return_value = [
            {
                "bucket": datetime(2024, 11, 15, 12, 0, 0, tzinfo=UTC),
                "total_events": 50,
                "successful_events": 48,
                "failed_events": 2,
            },
        ]

        response = client.get(f"/api/metrics/trends?bucket=hour&start_time={start_time}&end_time={end_time}")

        assert response.status_code == 200
        data = response.json()
        assert len(data["trends"]) == 1
        assert data["trends"][0]["total_events"] == 50
        # API returns ISO format with +00:00 instead of Z
        assert data["time_range"]["start_time"] == "2024-11-01T00:00:00+00:00"
        assert data["time_range"]["end_time"] == "2024-11-30T23:59:59+00:00"

    def test_get_trends_empty_results(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test trends endpoint returns empty list when no data matches."""
        setup_db_manager.fetch.return_value = []

        response = client.get("/api/metrics/trends?bucket=hour")

        assert response.status_code == 200
        data = response.json()
        assert data["trends"] == []
        assert "time_range" in data

    def test_get_trends_database_error(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test trends endpoint handles database errors gracefully."""
        setup_db_manager.fetch.side_effect = Exception("Database connection failed")

        response = client.get("/api/metrics/trends?bucket=hour")

        assert response.status_code == 500
        assert "Failed to fetch metrics trends" in response.json()["detail"]

    def test_get_trends_db_manager_none(self, client: TestClient) -> None:
        """Test endpoint returns 500 when db_manager is None."""
        with patch("webhook_server.app.db_manager", None):
            response = client.get("/api/metrics/trends?bucket=hour")

            assert response.status_code == 500
            assert "Metrics database not available" in response.json()["detail"]

    def test_get_trends_pool_none(
        self,
        client: TestClient,
        setup_db_manager: Mock,
    ) -> None:
        """Test endpoint returns 500 when database pool is not initialized."""
        # Simulate pool not initialized - helper methods raise ValueError
        setup_db_manager.pool = None
        setup_db_manager.fetch.side_effect = ValueError("Database pool not initialized. Call connect() first.")

        response = client.get("/api/metrics/trends?bucket=hour")

        assert response.status_code == 500
        assert "Failed to fetch metrics trends" in response.json()["detail"]
