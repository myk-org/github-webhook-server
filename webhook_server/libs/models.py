"""
SQLAlchemy models for GitHub Webhook Server metrics database.

Defines the complete database schema for tracking webhook events, pull requests,
reviews, labels, check runs, and API usage metrics.

Architecture:
- SQLAlchemy 2.0 declarative style with type hints
- PostgreSQL-specific types (UUID, JSONB) for optimal performance
- Comprehensive indexes on frequently queried columns
- Foreign key relationships with CASCADE delete for data integrity
- Server-side defaults for timestamps and UUIDs

Tables:
- webhooks: Webhook event store with full payload and metrics
- pull_requests: PR master records with size metrics
- pr_events: PR timeline events for analytics
- pr_reviews: Review data for approval tracking
- pr_labels: Label history for workflow tracking
- check_runs: Check run results for CI/CD metrics
- api_usage: GitHub API usage tracking for rate limit monitoring

Integration:
- Imported in webhook_server/migrations/env.py for Alembic autogenerate
- Used by DatabaseManager for query operations
- Enables comprehensive metrics and analytics collection
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func, text


class Base(DeclarativeBase):
    """
    Base class for all SQLAlchemy models.

    Provides type hints for SQLAlchemy 2.0 declarative style.
    All models inherit from this class.
    """

    pass


class Webhook(Base):
    """
    Webhook event store - tracks all incoming GitHub webhook events.

    Stores complete webhook payload and processing metrics including:
    - Event metadata (delivery ID, repository, event type, action)
    - Processing metrics (duration, API calls, token usage)
    - Status tracking (success, failure, partial)

    Indexes:
    - delivery_id (unique): Fast lookup by GitHub delivery ID
    - repository: Filter events by repository
    - event_type: Filter by event type (pull_request, issue_comment, etc.)
    - pr_number: Fast PR event lookup
    - created_at: Time-based queries for analytics

    Relationships:
    - pr_events: Timeline events for this webhook
    - check_runs: Check runs triggered by this webhook
    - api_usage: API usage metrics for this webhook
    """

    __tablename__ = "webhooks"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="Primary key UUID",
    )
    delivery_id: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
        comment="X-GitHub-Delivery header - unique webhook ID",
    )
    repository: Mapped[str] = mapped_column(
        String(255),
        index=True,
        nullable=False,
        comment="Repository in org/repo format",
    )
    event_type: Mapped[str] = mapped_column(
        String(50),
        index=True,
        nullable=False,
        comment="GitHub event type: pull_request, issue_comment, check_run, etc.",
    )
    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Event action: opened, synchronize, closed, etc.",
    )
    pr_number: Mapped[int | None] = mapped_column(
        Integer,
        index=True,
        nullable=True,
        comment="PR number if applicable to this event",
    )
    sender: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="GitHub username who triggered the event",
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Full webhook payload from GitHub",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        server_default=func.now(),
        nullable=False,
        comment="When webhook was received",
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="When webhook processing completed",
    )
    duration_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Processing duration in milliseconds",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Processing status: success, failure, partial",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if processing failed",
    )
    api_calls_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Number of GitHub API calls made during processing",
    )
    token_spend: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="GitHub API calls consumed (rate limit tokens spent)",
    )
    token_remaining: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Rate limit remaining after processing",
    )

    # Relationships
    pr_events: Mapped[list[PREvent]] = relationship(
        "PREvent",
        back_populates="webhook",
        cascade="all, delete-orphan",
    )
    check_runs: Mapped[list[CheckRun]] = relationship(
        "CheckRun",
        back_populates="webhook",
        cascade="all, delete-orphan",
    )
    api_usage: Mapped[list[APIUsage]] = relationship(
        "APIUsage",
        back_populates="webhook",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<Webhook(delivery_id='{self.delivery_id}', "
            f"repository='{self.repository}', "
            f"event_type='{self.event_type}', "
            f"status='{self.status}')>"
        )


class PullRequest(Base):
    """
    Pull request master records - tracks PR lifecycle and metrics.

    Stores PR metadata, statistics, and state changes including:
    - Basic info (title, author, timestamps)
    - Code metrics (additions, deletions, changed files)
    - Size classification (XS, S, M, L, XL, XXL)
    - State tracking (open, merged, closed)

    Indexes:
    - repository + pr_number: Fast PR lookup (composite unique)
    - author: Filter PRs by author
    - created_at: Time-based queries
    - updated_at: Recent activity tracking

    Relationships:
    - pr_events: Timeline events for this PR
    - pr_reviews: Reviews for this PR
    - pr_labels: Label history for this PR
    - check_runs: Check runs for this PR
    """

    __tablename__ = "pull_requests"
    __table_args__ = (UniqueConstraint("repository", "pr_number", name="uq_pull_requests_repository_pr_number"),)

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="Primary key UUID",
    )
    repository: Mapped[str] = mapped_column(
        String(255),
        index=True,
        nullable=False,
        comment="Repository in org/repo format",
    )
    pr_number: Mapped[int] = mapped_column(
        Integer,
        index=True,
        nullable=False,
        comment="PR number within repository",
    )
    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="PR title",
    )
    author: Mapped[str] = mapped_column(
        String(255),
        index=True,
        nullable=False,
        comment="GitHub username of PR author",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
        comment="When PR was created",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
        comment="When PR was last updated",
    )
    merged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When PR was merged (null if not merged)",
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When PR was closed (null if still open)",
    )
    state: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="PR state: open, merged, closed",
    )
    draft: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether PR is in draft state",
    )
    additions: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Lines of code added",
    )
    deletions: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Lines of code deleted",
    )
    changed_files: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Number of files changed",
    )
    size_label: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        comment="PR size classification: XS, S, M, L, XL, XXL",
    )

    # Relationships
    pr_events: Mapped[list[PREvent]] = relationship(
        "PREvent",
        back_populates="pull_request",
        cascade="all, delete-orphan",
    )
    pr_reviews: Mapped[list[PRReview]] = relationship(
        "PRReview",
        back_populates="pull_request",
        cascade="all, delete-orphan",
    )
    pr_labels: Mapped[list[PRLabel]] = relationship(
        "PRLabel",
        back_populates="pull_request",
        cascade="all, delete-orphan",
    )
    check_runs: Mapped[list[CheckRun]] = relationship(
        "CheckRun",
        back_populates="pull_request",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<PullRequest(repository='{self.repository}', "
            f"pr_number={self.pr_number}, "
            f"state='{self.state}', "
            f"title='{self.title[:50]}...')>"
        )


class PREvent(Base):
    """
    PR timeline events - tracks all events in PR lifecycle.

    Records significant events in PR timeline including:
    - Code updates (synchronize)
    - State changes (opened, closed, merged)
    - Reviews (approved, changes_requested)
    - Check runs (CI/CD pipeline events)

    Indexes:
    - pr_id: Fast event lookup by PR
    - event_type: Filter by event type
    - created_at: Time-based queries

    Relationships:
    - pull_request: PR this event belongs to
    - webhook: Webhook that triggered this event
    """

    __tablename__ = "pr_events"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="Primary key UUID",
    )
    pr_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pull_requests.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="Foreign key to pull_requests table",
    )
    webhook_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("webhooks.id", ondelete="CASCADE"),
        nullable=False,
        comment="Foreign key to webhooks table",
    )
    event_type: Mapped[str] = mapped_column(
        String(50),
        index=True,
        nullable=False,
        comment="Event type: opened, synchronize, review, check_run, etc.",
    )
    event_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Event-specific data from webhook payload",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        server_default=func.now(),
        nullable=False,
        comment="When event occurred",
    )

    # Relationships
    pull_request: Mapped[PullRequest] = relationship(
        "PullRequest",
        back_populates="pr_events",
    )
    webhook: Mapped[Webhook] = relationship(
        "Webhook",
        back_populates="pr_events",
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"<PREvent(pr_id='{self.pr_id}', event_type='{self.event_type}', created_at='{self.created_at}')>"


class PRReview(Base):
    """
    PR review data - tracks review approvals and feedback.

    Records review submissions including:
    - Reviewer identity
    - Review type (approved, changes_requested, commented)
    - Timing information

    Indexes:
    - pr_id: Fast review lookup by PR
    - reviewer: Filter reviews by reviewer
    - created_at: Time-based queries

    Relationships:
    - pull_request: PR this review belongs to
    """

    __tablename__ = "pr_reviews"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="Primary key UUID",
    )
    pr_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pull_requests.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="Foreign key to pull_requests table",
    )
    reviewer: Mapped[str] = mapped_column(
        String(255),
        index=True,
        nullable=False,
        comment="GitHub username of reviewer",
    )
    review_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="Review type: approved, changes_requested, commented",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        server_default=func.now(),
        nullable=False,
        comment="When review was submitted",
    )

    # Relationships
    pull_request: Mapped[PullRequest] = relationship(
        "PullRequest",
        back_populates="pr_reviews",
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"<PRReview(pr_id='{self.pr_id}', reviewer='{self.reviewer}', review_type='{self.review_type}')>"


class PRLabel(Base):
    """
    PR label history - tracks label additions and removals.

    Records label lifecycle including:
    - Label name
    - When label was added
    - When label was removed (if applicable)

    Enables tracking of:
    - Label-based workflows
    - Size label history
    - Review label progression

    Indexes:
    - pr_id: Fast label lookup by PR
    - label: Filter by specific label
    - added_at: Time-based queries

    Relationships:
    - pull_request: PR this label belongs to
    """

    __tablename__ = "pr_labels"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="Primary key UUID",
    )
    pr_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pull_requests.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="Foreign key to pull_requests table",
    )
    label: Mapped[str] = mapped_column(
        String(100),
        index=True,
        nullable=False,
        comment="Label name",
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        server_default=func.now(),
        nullable=False,
        comment="When label was added",
    )
    removed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When label was removed (null if still present)",
    )

    # Relationships
    pull_request: Mapped[PullRequest] = relationship(
        "PullRequest",
        back_populates="pr_labels",
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        removed_str = f", removed_at='{self.removed_at}'" if self.removed_at else ""
        return f"<PRLabel(pr_id='{self.pr_id}', label='{self.label}'{removed_str})>"


class CheckRun(Base):
    """
    Check run results - tracks CI/CD pipeline execution.

    Records check run lifecycle including:
    - Check name (tox, pre-commit, container-build, etc.)
    - Status and conclusion
    - Timing and duration metrics
    - Output summary for failures

    Indexes:
    - pr_id: Fast check run lookup by PR
    - check_name: Filter by specific check
    - started_at: Time-based queries

    Relationships:
    - pull_request: PR this check run belongs to
    - webhook: Webhook that triggered this check run
    """

    __tablename__ = "check_runs"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="Primary key UUID",
    )
    pr_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pull_requests.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="Foreign key to pull_requests table",
    )
    webhook_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("webhooks.id", ondelete="CASCADE"),
        nullable=False,
        comment="Foreign key to webhooks table",
    )
    check_name: Mapped[str] = mapped_column(
        String(255),
        index=True,
        nullable=False,
        comment="Check name: tox, pre-commit, container-build, etc.",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Status: queued, in_progress, completed",
    )
    conclusion: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Conclusion: success, failure, cancelled, etc. (null if not completed)",
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
        comment="When check run started",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When check run completed (null if not completed)",
    )
    duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Check run duration in milliseconds (null if not completed)",
    )
    output_title: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Check run output title",
    )
    output_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Check run output summary (especially for failures)",
    )

    # Relationships
    pull_request: Mapped[PullRequest] = relationship(
        "PullRequest",
        back_populates="check_runs",
    )
    webhook: Mapped[Webhook] = relationship(
        "Webhook",
        back_populates="check_runs",
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<CheckRun(pr_id='{self.pr_id}', "
            f"check_name='{self.check_name}', "
            f"status='{self.status}', "
            f"conclusion='{self.conclusion}')>"
        )


class APIUsage(Base):
    """
    GitHub API usage tracking - monitors rate limit consumption.

    Records API usage metrics per webhook including:
    - Number of API calls made
    - Rate limit before/after processing
    - Token spend (calls consumed)

    Enables:
    - Rate limit monitoring and alerting
    - API usage optimization
    - Cost analysis by repository/event type

    Indexes:
    - webhook_id: Fast usage lookup by webhook
    - repository: Filter by repository
    - event_type: Analyze usage by event type
    - created_at: Time-based queries

    Relationships:
    - webhook: Webhook this usage record belongs to
    """

    __tablename__ = "api_usage"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="Primary key UUID",
    )
    webhook_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("webhooks.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="Foreign key to webhooks table",
    )
    repository: Mapped[str] = mapped_column(
        String(255),
        index=True,
        nullable=False,
        comment="Repository in org/repo format",
    )
    event_type: Mapped[str] = mapped_column(
        String(50),
        index=True,
        nullable=False,
        comment="Event type: pull_request, issue_comment, etc.",
    )
    api_calls_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Number of GitHub API calls made",
    )
    initial_rate_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Rate limit remaining before processing",
    )
    final_rate_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Rate limit remaining after processing",
    )
    token_spend: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="GitHub API calls consumed (rate limit tokens spent)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        server_default=func.now(),
        nullable=False,
        comment="When API usage was recorded",
    )

    # Relationships
    webhook: Mapped[Webhook] = relationship(
        "Webhook",
        back_populates="api_usage",
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<APIUsage(webhook_id='{self.webhook_id}', "
            f"repository='{self.repository}', "
            f"api_calls_count={self.api_calls_count}, "
            f"token_spend={self.token_spend})>"
        )
