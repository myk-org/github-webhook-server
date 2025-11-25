"""
Metrics tracking for GitHub webhook events and processing statistics.

Provides comprehensive metrics collection including:
- Webhook event storage with full payload
- Processing time and performance metrics
- API usage tracking
- Error tracking and status monitoring

Architecture:
- Async database operations using asyncpg connection pool
- No defensive checks on required parameters (fail-fast principle)
- Proper error handling with structured logging
- Integration with DatabaseManager
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from webhook_server.libs.database import DatabaseManager


class MetricsTracker:
    """
    Tracks webhook events and processing metrics in PostgreSQL database.

    Stores comprehensive metrics including:
    - Webhook event metadata and payloads
    - Processing duration and performance
    - API usage and rate limit consumption
    - Success/failure status with error details

    Architecture guarantees:
    - db_manager is ALWAYS provided (required parameter) - no defensive checks
    - logger is ALWAYS provided (required parameter) - no defensive checks

    Example:
        tracker = MetricsTracker(db_manager, logger)
        await tracker.track_webhook_event(
            delivery_id="abc123",
            repository="org/repo",
            event_type="pull_request",
            action="opened",
            pr_number=42,
            sender="user",
            payload={"key": "value"},
            processing_time_ms=150,
            status="success",
        )
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        logger: logging.Logger,
    ) -> None:
        """
        Initialize metrics tracker.

        Args:
            db_manager: Database connection manager for metrics storage
            logger: Logger instance for metrics tracking events

        Note:
            No defensive checks - all parameters are required and ALWAYS provided.
            Architecture guarantees these are initialized before MetricsTracker.
        """
        self.db_manager = db_manager
        self.logger = logger

    async def track_webhook_event(
        self,
        delivery_id: str,
        repository: str,
        event_type: str,
        action: str,
        sender: str,
        payload: dict[str, Any],
        processing_time_ms: int,
        status: str,
        pr_number: int | None = None,
        error_message: str | None = None,
        api_calls_count: int = 0,
        token_spend: int = 0,
        token_remaining: int = 0,
        metrics_available: bool = True,
    ) -> None:
        """
        Track webhook event with comprehensive metrics.

        Stores webhook event in database with processing metrics including:
        - Event metadata (delivery ID, repository, event type, action)
        - Processing metrics (duration, API calls, token usage)
        - Status tracking (success, error, partial)
        - Full payload for debugging and analytics

        Uses DatabaseManager.execute() for centralized pool management and
        precondition checking. All database operations go through DatabaseManager
        to avoid duplicated connection handling logic.

        Args:
            delivery_id: GitHub webhook delivery ID (X-GitHub-Delivery header)
            repository: Repository in org/repo format
            event_type: GitHub event type (pull_request, issue_comment, etc.)
            action: Event action (opened, synchronize, closed, etc.)
            sender: GitHub username who triggered the event
            payload: Full webhook payload from GitHub
            processing_time_ms: Processing duration in milliseconds
            status: Processing status (success, error, partial)
            pr_number: PR number if applicable (optional)
            error_message: Error message if processing failed (optional)
            api_calls_count: Number of GitHub API calls made (default: 0)
            token_spend: GitHub API calls consumed (default: 0)
            token_remaining: Rate limit remaining after processing (default: 0)
            metrics_available: Whether API metrics are available (default: True)

        Raises:
            asyncpg.PostgresError: If database insert fails
            ValueError: If database pool not initialized

        Example:
            await tracker.track_webhook_event(
                delivery_id="abc123",
                repository="myorg/myrepo",
                event_type="pull_request",
                action="opened",
                pr_number=42,
                sender="johndoe",
                payload=webhook_payload,
                processing_time_ms=150,
                status="success",
                api_calls_count=3,
                token_spend=3,
                token_remaining=4997,
                metrics_available=True,
            )
        """
        try:
            # Serialize payload to JSON string for JSONB storage
            # Use default=str for defensive handling of non-serializable types
            # (datetime, UUID, etc.) to prevent TypeError
            payload_json = json.dumps(payload, default=str)

            # Insert webhook event into database using DatabaseManager.execute()
            # This centralizes pool management and precondition checks
            # Note: processed_at is auto-populated by database via server_default=func.now()
            await self.db_manager.execute(
                """
                INSERT INTO webhooks (
                    id, delivery_id, repository, event_type, action,
                    pr_number, sender, payload, duration_ms,
                    status, error_message, api_calls_count, token_spend, token_remaining,
                    metrics_available
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                """,
                uuid4(),
                delivery_id,
                repository,
                event_type,
                action,
                pr_number,
                sender,
                payload_json,
                processing_time_ms,
                status,
                error_message,
                api_calls_count,
                token_spend,
                token_remaining,
                metrics_available,
            )

            self.logger.info(
                f"Webhook event tracked successfully: delivery_id={delivery_id}, "
                f"repository={repository}, event_type={event_type}, action={action}, "
                f"status={status}, processing_time_ms={processing_time_ms}"
            )

        except Exception:
            self.logger.exception(
                f"Failed to track webhook event: delivery_id={delivery_id}, "
                f"repository={repository}, event_type={event_type}"
            )
            raise
