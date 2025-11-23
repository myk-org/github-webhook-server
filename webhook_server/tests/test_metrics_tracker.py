"""Tests for MetricsTracker webhook event tracking."""

from unittest.mock import AsyncMock, Mock

import pytest

from webhook_server.libs.metrics_tracker import MetricsTracker


class TestMetricsTracker:
    """Test suite for MetricsTracker class."""

    @pytest.fixture
    def mock_db_manager(self) -> Mock:
        """Create a mock database manager."""
        mock = Mock()
        mock.pool = Mock()
        # Setup async context manager for pool.acquire()
        mock_conn = Mock()
        mock_conn.execute = AsyncMock()
        mock_acquire_cm = AsyncMock()
        mock_acquire_cm.__aenter__.return_value = mock_conn
        mock_acquire_cm.__aexit__.return_value = None
        mock.pool.acquire.return_value = mock_acquire_cm
        return mock

    @pytest.fixture
    def mock_redis_manager(self) -> Mock:
        """Create a mock Redis manager."""
        return Mock()

    @pytest.fixture
    def mock_logger(self) -> Mock:
        """Create a mock logger."""
        return Mock()

    @pytest.fixture
    def metrics_tracker(
        self,
        mock_db_manager: Mock,
        mock_redis_manager: Mock,
        mock_logger: Mock,
    ) -> MetricsTracker:
        """Create a MetricsTracker instance with mocked dependencies."""
        return MetricsTracker(mock_db_manager, mock_redis_manager, mock_logger)

    def test_metrics_tracker_init(
        self,
        mock_db_manager: Mock,
        mock_redis_manager: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test MetricsTracker initialization."""
        tracker = MetricsTracker(mock_db_manager, mock_redis_manager, mock_logger)

        assert tracker.db_manager is mock_db_manager
        assert tracker.redis_manager is mock_redis_manager
        assert tracker.logger is mock_logger

    @pytest.mark.asyncio
    async def test_track_webhook_event_success(
        self,
        metrics_tracker: MetricsTracker,
        mock_db_manager: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test tracking webhook event successfully."""
        await metrics_tracker.track_webhook_event(
            delivery_id="test-delivery-id",
            repository="org/repo",
            event_type="pull_request",
            action="opened",
            sender="testuser",
            payload={"test": "data"},
            processing_time_ms=150,
            status="success",
            pr_number=42,
        )

        # Verify pool.acquire was called
        mock_db_manager.pool.acquire.assert_called_once()

        # Verify execute was called
        mock_conn = await mock_db_manager.pool.acquire.return_value.__aenter__()
        mock_conn.execute.assert_called_once()

        # Verify the execute call parameters
        # Parameter order: uuid4(), delivery_id, repository, event_type, action,
        #                  pr_number, sender, payload_json, processed_at, duration_ms,
        #                  status, error_message, api_calls_count, token_spend, token_remaining
        call_args = mock_conn.execute.call_args
        assert "INSERT INTO webhooks" in call_args[0][0]
        assert call_args[0][2] == "test-delivery-id"  # delivery_id
        assert call_args[0][3] == "org/repo"  # repository
        assert call_args[0][4] == "pull_request"  # event_type
        assert call_args[0][5] == "opened"  # action
        assert call_args[0][6] == 42  # pr_number
        assert call_args[0][7] == "testuser"  # sender
        assert call_args[0][10] == 150  # duration_ms
        assert call_args[0][11] == "success"  # status

        # Verify log message
        mock_logger.info.assert_called_once()
        assert "test-delivery-id" in mock_logger.info.call_args[0][0]
        assert "org/repo" in mock_logger.info.call_args[0][0]
        assert "success" in mock_logger.info.call_args[0][0]

    @pytest.mark.asyncio
    async def test_track_webhook_event_with_error(
        self,
        metrics_tracker: MetricsTracker,
        mock_db_manager: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test tracking webhook event with error status."""
        await metrics_tracker.track_webhook_event(
            delivery_id="test-delivery-id",
            repository="org/repo",
            event_type="pull_request",
            action="synchronize",
            sender="testuser",
            payload={"test": "data"},
            processing_time_ms=250,
            status="error",
            error_message="Test error message",
        )

        # Verify execution
        mock_db_manager.pool.acquire.assert_called_once()

        # Verify execute was called with error message
        mock_conn = await mock_db_manager.pool.acquire.return_value.__aenter__()
        call_args = mock_conn.execute.call_args
        assert call_args[0][11] == "error"  # status
        assert call_args[0][12] == "Test error message"  # error_message

        # Verify log message
        mock_logger.info.assert_called_once()

    @pytest.mark.asyncio
    async def test_track_webhook_event_with_api_metrics(
        self,
        metrics_tracker: MetricsTracker,
        mock_db_manager: Mock,
        mock_logger: Mock,  # noqa: ARG002
    ) -> None:
        """Test tracking webhook event with API usage metrics."""
        await metrics_tracker.track_webhook_event(
            delivery_id="test-delivery-id",
            repository="org/repo",
            event_type="pull_request",
            action="opened",
            sender="testuser",
            payload={"test": "data"},
            processing_time_ms=150,
            status="success",
            api_calls_count=5,
            token_spend=10,
            token_remaining=4990,
        )

        # Verify execute was called with API metrics
        mock_conn = await mock_db_manager.pool.acquire.return_value.__aenter__()
        call_args = mock_conn.execute.call_args
        assert call_args[0][13] == 5  # api_calls_count
        assert call_args[0][14] == 10  # token_spend
        assert call_args[0][15] == 4990  # token_remaining

    @pytest.mark.asyncio
    async def test_track_webhook_event_database_error(
        self,
        metrics_tracker: MetricsTracker,
        mock_db_manager: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test handling database errors during tracking."""
        # Make execute raise an exception
        mock_conn = await mock_db_manager.pool.acquire.return_value.__aenter__()
        mock_conn.execute.side_effect = Exception("Database error")

        with pytest.raises(Exception, match="Database error"):
            await metrics_tracker.track_webhook_event(
                delivery_id="test-delivery-id",
                repository="org/repo",
                event_type="pull_request",
                action="opened",
                sender="testuser",
                payload={"test": "data"},
                processing_time_ms=150,
                status="success",
            )

        # Verify exception was logged
        mock_logger.exception.assert_called_once()
        assert "Failed to track webhook event" in mock_logger.exception.call_args[0][0]
        assert "test-delivery-id" in mock_logger.exception.call_args[0][0]
        assert "org/repo" in mock_logger.exception.call_args[0][0]

    @pytest.mark.asyncio
    async def test_track_webhook_event_pool_not_initialized(
        self,
        mock_db_manager: Mock,
        mock_redis_manager: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test error when database pool is not initialized."""
        mock_db_manager.pool = None
        tracker = MetricsTracker(mock_db_manager, mock_redis_manager, mock_logger)

        with pytest.raises(ValueError, match="Database pool not initialized"):
            await tracker.track_webhook_event(
                delivery_id="test-delivery-id",
                repository="org/repo",
                event_type="pull_request",
                action="opened",
                sender="testuser",
                payload={"test": "data"},
                processing_time_ms=150,
                status="success",
            )

        # Verify exception was logged
        mock_logger.exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_track_webhook_event_complex_payload(
        self,
        metrics_tracker: MetricsTracker,
        mock_db_manager: Mock,
        mock_logger: Mock,  # noqa: ARG002
    ) -> None:
        """Test tracking webhook event with complex payload structure."""
        complex_payload = {
            "action": "opened",
            "pull_request": {
                "id": 123,
                "number": 42,
                "title": "Test PR",
                "user": {"login": "testuser"},
                "labels": [{"name": "bug"}, {"name": "urgent"}],
            },
            "repository": {
                "name": "repo",
                "owner": {"login": "org"},
            },
        }

        await metrics_tracker.track_webhook_event(
            delivery_id="test-delivery-id",
            repository="org/repo",
            event_type="pull_request",
            action="opened",
            sender="testuser",
            payload=complex_payload,
            processing_time_ms=150,
            status="success",
            pr_number=42,
        )

        # Verify payload was serialized to JSON
        mock_conn = await mock_db_manager.pool.acquire.return_value.__aenter__()
        call_args = mock_conn.execute.call_args
        payload_json = call_args[0][8]  # payload_json parameter position
        assert "pull_request" in payload_json
        assert "repository" in payload_json
        assert "labels" in payload_json

    @pytest.mark.asyncio
    async def test_track_webhook_event_optional_pr_number(
        self,
        metrics_tracker: MetricsTracker,
        mock_db_manager: Mock,
        mock_logger: Mock,  # noqa: ARG002
    ) -> None:
        """Test tracking webhook event without PR number (e.g., issue_comment)."""
        await metrics_tracker.track_webhook_event(
            delivery_id="test-delivery-id",
            repository="org/repo",
            event_type="issue_comment",
            action="created",
            sender="testuser",
            payload={"comment": {"body": "Great work!"}},
            processing_time_ms=100,
            status="success",
            pr_number=None,
        )

        # Verify pr_number is None in execute call
        mock_conn = await mock_db_manager.pool.acquire.return_value.__aenter__()
        call_args = mock_conn.execute.call_args
        assert call_args[0][6] is None  # pr_number

    @pytest.mark.asyncio
    async def test_track_webhook_event_all_optional_params(
        self,
        metrics_tracker: MetricsTracker,
        mock_db_manager: Mock,
        mock_logger: Mock,  # noqa: ARG002
    ) -> None:
        """Test tracking webhook event with all optional parameters set."""
        await metrics_tracker.track_webhook_event(
            delivery_id="test-delivery-id",
            repository="org/repo",
            event_type="check_run",
            action="completed",
            sender="github-actions",
            payload={"check_run": {"conclusion": "success"}},
            processing_time_ms=500,
            status="success",
            pr_number=42,
            error_message=None,
            api_calls_count=3,
            token_spend=5,
            token_remaining=4995,
        )

        # Verify all parameters were passed to execute
        mock_conn = await mock_db_manager.pool.acquire.return_value.__aenter__()
        call_args = mock_conn.execute.call_args
        assert len(call_args[0]) == 16  # SQL query + 15 parameters
        assert call_args[0][6] == 42  # pr_number
        assert call_args[0][12] is None  # error_message
        assert call_args[0][13] == 3  # api_calls_count
        assert call_args[0][14] == 5  # token_spend
        assert call_args[0][15] == 4995  # token_remaining

    @pytest.mark.asyncio
    async def test_track_webhook_event_zero_api_calls(
        self,
        metrics_tracker: MetricsTracker,
        mock_db_manager: Mock,
        mock_logger: Mock,  # noqa: ARG002
    ) -> None:
        """Test tracking webhook event with zero API calls (default values)."""
        await metrics_tracker.track_webhook_event(
            delivery_id="test-delivery-id",
            repository="org/repo",
            event_type="pull_request",
            action="opened",
            sender="testuser",
            payload={"test": "data"},
            processing_time_ms=150,
            status="success",
        )

        # Verify default zero values for API metrics
        mock_conn = await mock_db_manager.pool.acquire.return_value.__aenter__()
        call_args = mock_conn.execute.call_args
        assert call_args[0][13] == 0  # api_calls_count default
        assert call_args[0][14] == 0  # token_spend default
        assert call_args[0][15] == 0  # token_remaining default
