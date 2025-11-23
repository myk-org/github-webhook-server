"""
Comprehensive tests for SQLAlchemy models.

Tests all 7 models:
- Webhook: Webhook event store with full payload and metrics
- PullRequest: PR master records with size metrics
- PREvent: PR timeline events for analytics
- PRReview: Review data for approval tracking
- PRLabel: Label history for workflow tracking
- CheckRun: Check run results for CI/CD metrics
- APIUsage: GitHub API usage tracking for rate limit monitoring
"""

from datetime import UTC, datetime
from uuid import UUID

from webhook_server.libs.models import (
    APIUsage,
    Base,
    CheckRun,
    PREvent,
    PRLabel,
    PRReview,
    PullRequest,
    Webhook,
)


class TestBase:
    """Test the Base declarative class."""

    def test_base_is_declarative_base(self) -> None:
        """Verify Base is a valid SQLAlchemy declarative base."""
        assert hasattr(Base, "metadata")
        assert hasattr(Base, "registry")


class TestWebhookModel:
    """Test Webhook model instantiation and fields."""

    def test_webhook_model_creation(self) -> None:
        """Test creating Webhook instance with required fields."""
        webhook = Webhook(
            delivery_id="test-delivery-123",
            repository="org/repo",
            event_type="pull_request",
            action="opened",
            sender="test-user",
            payload={"key": "value"},
            processed_at=datetime.now(UTC),
            duration_ms=150,
            status="success",
        )

        assert webhook.delivery_id == "test-delivery-123"
        assert webhook.repository == "org/repo"
        assert webhook.event_type == "pull_request"
        assert webhook.action == "opened"
        assert webhook.sender == "test-user"
        assert webhook.payload == {"key": "value"}
        assert webhook.status == "success"
        assert webhook.duration_ms == 150

    def test_webhook_model_with_optional_fields(self) -> None:
        """Test Webhook with optional fields set."""
        webhook = Webhook(
            delivery_id="test-delivery-456",
            repository="org/repo",
            event_type="pull_request",
            action="synchronize",
            pr_number=42,
            sender="test-user",
            payload={"data": "test"},
            processed_at=datetime.now(UTC),
            duration_ms=200,
            status="failure",
            error_message="Test error",
            api_calls_count=5,
            token_spend=10,
            token_remaining=4990,
        )

        assert webhook.pr_number == 42
        assert webhook.error_message == "Test error"
        assert webhook.api_calls_count == 5
        assert webhook.token_spend == 10
        assert webhook.token_remaining == 4990

    def test_webhook_repr(self) -> None:
        """Test Webhook __repr__ method."""
        webhook = Webhook(
            delivery_id="test-123",
            repository="org/repo",
            event_type="push",
            action="created",
            sender="user",
            payload={},
            processed_at=datetime.now(UTC),
            duration_ms=100,
            status="success",
        )

        repr_str = repr(webhook)
        assert "Webhook" in repr_str
        assert "test-123" in repr_str
        assert "org/repo" in repr_str
        assert "push" in repr_str
        assert "success" in repr_str

    def test_webhook_relationships(self) -> None:
        """Test Webhook relationships are defined."""
        webhook = Webhook(
            delivery_id="test-rel",
            repository="org/repo",
            event_type="pull_request",
            action="opened",
            sender="user",
            payload={},
            processed_at=datetime.now(UTC),
            duration_ms=100,
            status="success",
        )

        # Verify relationships exist (lazy loaded, empty by default)
        assert hasattr(webhook, "pr_events")
        assert hasattr(webhook, "check_runs")
        assert hasattr(webhook, "api_usage")
        assert webhook.pr_events == []
        assert webhook.check_runs == []
        assert webhook.api_usage == []


class TestPullRequestModel:
    """Test PullRequest model instantiation and fields."""

    def test_pull_request_model_creation(self) -> None:
        """Test creating PullRequest instance with required fields."""
        now = datetime.now(UTC)
        pr = PullRequest(
            repository="org/repo",
            pr_number=123,
            title="Test PR",
            author="test-user",
            created_at=now,
            updated_at=now,
            state="open",
        )

        assert pr.repository == "org/repo"
        assert pr.pr_number == 123
        assert pr.title == "Test PR"
        assert pr.author == "test-user"
        assert pr.state == "open"
        assert pr.created_at == now
        assert pr.updated_at == now

    def test_pull_request_with_metrics(self) -> None:
        """Test PullRequest with code metrics."""
        now = datetime.now(UTC)
        pr = PullRequest(
            repository="org/repo",
            pr_number=456,
            title="Feature PR",
            author="dev",
            created_at=now,
            updated_at=now,
            state="open",
            draft=True,
            additions=150,
            deletions=50,
            changed_files=5,
            size_label="M",
        )

        assert pr.draft is True
        assert pr.additions == 150
        assert pr.deletions == 50
        assert pr.changed_files == 5
        assert pr.size_label == "M"

    def test_pull_request_merged(self) -> None:
        """Test PullRequest with merged state."""
        now = datetime.now(UTC)
        pr = PullRequest(
            repository="org/repo",
            pr_number=789,
            title="Merged PR",
            author="dev",
            created_at=now,
            updated_at=now,
            merged_at=now,
            state="merged",
        )

        assert pr.state == "merged"
        assert pr.merged_at == now

    def test_pull_request_closed(self) -> None:
        """Test PullRequest with closed state."""
        now = datetime.now(UTC)
        pr = PullRequest(
            repository="org/repo",
            pr_number=999,
            title="Closed PR",
            author="dev",
            created_at=now,
            updated_at=now,
            closed_at=now,
            state="closed",
        )

        assert pr.state == "closed"
        assert pr.closed_at == now

    def test_pull_request_repr(self) -> None:
        """Test PullRequest __repr__ method."""
        now = datetime.now(UTC)
        pr = PullRequest(
            repository="test-org/test-repo",
            pr_number=42,
            title="Very long PR title that should be truncated in the repr output for readability",
            author="user",
            created_at=now,
            updated_at=now,
            state="open",
        )

        repr_str = repr(pr)
        assert "PullRequest" in repr_str
        assert "test-org/test-repo" in repr_str
        assert "42" in repr_str
        assert "open" in repr_str

    def test_pull_request_relationships(self) -> None:
        """Test PullRequest relationships are defined."""
        now = datetime.now(UTC)
        pr = PullRequest(
            repository="org/repo",
            pr_number=1,
            title="Test",
            author="user",
            created_at=now,
            updated_at=now,
            state="open",
        )

        # Verify relationships exist
        assert hasattr(pr, "pr_events")
        assert hasattr(pr, "pr_reviews")
        assert hasattr(pr, "pr_labels")
        assert hasattr(pr, "check_runs")
        assert pr.pr_events == []
        assert pr.pr_reviews == []
        assert pr.pr_labels == []
        assert pr.check_runs == []


class TestPREventModel:
    """Test PREvent model instantiation and fields."""

    def test_pr_event_model_creation(self) -> None:
        """Test creating PREvent instance with required fields."""
        pr_id = UUID("12345678-1234-5678-1234-567812345678")
        webhook_id = UUID("87654321-4321-8765-4321-876543218765")

        event = PREvent(
            pr_id=pr_id,
            webhook_id=webhook_id,
            event_type="synchronize",
            event_data={"commits": 3},
        )

        assert event.pr_id == pr_id
        assert event.webhook_id == webhook_id
        assert event.event_type == "synchronize"
        assert event.event_data == {"commits": 3}

    def test_pr_event_repr(self) -> None:
        """Test PREvent __repr__ method."""
        pr_id = UUID("12345678-1234-5678-1234-567812345678")
        webhook_id = UUID("87654321-4321-8765-4321-876543218765")

        event = PREvent(
            pr_id=pr_id,
            webhook_id=webhook_id,
            event_type="opened",
            event_data={},
        )

        repr_str = repr(event)
        assert "PREvent" in repr_str
        assert str(pr_id) in repr_str
        assert "opened" in repr_str


class TestPRReviewModel:
    """Test PRReview model instantiation and fields."""

    def test_pr_review_model_creation(self) -> None:
        """Test creating PRReview instance with required fields."""
        pr_id = UUID("12345678-1234-5678-1234-567812345678")

        review = PRReview(
            pr_id=pr_id,
            reviewer="test-reviewer",
            review_type="approved",
        )

        assert review.pr_id == pr_id
        assert review.reviewer == "test-reviewer"
        assert review.review_type == "approved"

    def test_pr_review_changes_requested(self) -> None:
        """Test PRReview with changes_requested type."""
        pr_id = UUID("12345678-1234-5678-1234-567812345678")

        review = PRReview(
            pr_id=pr_id,
            reviewer="reviewer2",
            review_type="changes_requested",
        )

        assert review.review_type == "changes_requested"

    def test_pr_review_repr(self) -> None:
        """Test PRReview __repr__ method."""
        pr_id = UUID("12345678-1234-5678-1234-567812345678")

        review = PRReview(
            pr_id=pr_id,
            reviewer="john-doe",
            review_type="commented",
        )

        repr_str = repr(review)
        assert "PRReview" in repr_str
        assert str(pr_id) in repr_str
        assert "john-doe" in repr_str
        assert "commented" in repr_str


class TestPRLabelModel:
    """Test PRLabel model instantiation and fields."""

    def test_pr_label_model_creation(self) -> None:
        """Test creating PRLabel instance with required fields."""
        pr_id = UUID("12345678-1234-5678-1234-567812345678")

        label = PRLabel(
            pr_id=pr_id,
            label="verified",
        )

        assert label.pr_id == pr_id
        assert label.label == "verified"
        assert label.removed_at is None

    def test_pr_label_with_removal(self) -> None:
        """Test PRLabel with removed_at timestamp."""
        pr_id = UUID("12345678-1234-5678-1234-567812345678")
        removed_time = datetime.now(UTC)

        label = PRLabel(
            pr_id=pr_id,
            label="needs-work",
            removed_at=removed_time,
        )

        assert label.label == "needs-work"
        assert label.removed_at == removed_time

    def test_pr_label_repr_active(self) -> None:
        """Test PRLabel __repr__ for active label."""
        pr_id = UUID("12345678-1234-5678-1234-567812345678")

        label = PRLabel(
            pr_id=pr_id,
            label="size/M",
        )

        repr_str = repr(label)
        assert "PRLabel" in repr_str
        assert str(pr_id) in repr_str
        assert "size/M" in repr_str

    def test_pr_label_repr_removed(self) -> None:
        """Test PRLabel __repr__ for removed label."""
        pr_id = UUID("12345678-1234-5678-1234-567812345678")
        removed_time = datetime.now(UTC)

        label = PRLabel(
            pr_id=pr_id,
            label="wip",
            removed_at=removed_time,
        )

        repr_str = repr(label)
        assert "PRLabel" in repr_str
        assert "wip" in repr_str
        assert "removed_at" in repr_str


class TestCheckRunModel:
    """Test CheckRun model instantiation and fields."""

    def test_check_run_model_creation(self) -> None:
        """Test creating CheckRun instance with required fields."""
        pr_id = UUID("12345678-1234-5678-1234-567812345678")
        webhook_id = UUID("87654321-4321-8765-4321-876543218765")
        started = datetime.now(UTC)

        check_run = CheckRun(
            pr_id=pr_id,
            webhook_id=webhook_id,
            check_name="tox",
            status="completed",
            started_at=started,
        )

        assert check_run.pr_id == pr_id
        assert check_run.webhook_id == webhook_id
        assert check_run.check_name == "tox"
        assert check_run.status == "completed"
        assert check_run.started_at == started

    def test_check_run_with_success(self) -> None:
        """Test CheckRun with successful conclusion."""
        pr_id = UUID("12345678-1234-5678-1234-567812345678")
        webhook_id = UUID("87654321-4321-8765-4321-876543218765")
        started = datetime.now(UTC)
        completed = datetime.now(UTC)

        check_run = CheckRun(
            pr_id=pr_id,
            webhook_id=webhook_id,
            check_name="pre-commit",
            status="completed",
            conclusion="success",
            started_at=started,
            completed_at=completed,
            duration_ms=5000,
        )

        assert check_run.conclusion == "success"
        assert check_run.completed_at == completed
        assert check_run.duration_ms == 5000

    def test_check_run_with_failure(self) -> None:
        """Test CheckRun with failed conclusion and output."""
        pr_id = UUID("12345678-1234-5678-1234-567812345678")
        webhook_id = UUID("87654321-4321-8765-4321-876543218765")
        started = datetime.now(UTC)

        check_run = CheckRun(
            pr_id=pr_id,
            webhook_id=webhook_id,
            check_name="container-build",
            status="completed",
            conclusion="failure",
            started_at=started,
            output_title="Build failed",
            output_summary="Docker build failed on step 5",
        )

        assert check_run.conclusion == "failure"
        assert check_run.output_title == "Build failed"
        assert check_run.output_summary == "Docker build failed on step 5"

    def test_check_run_in_progress(self) -> None:
        """Test CheckRun in progress state."""
        pr_id = UUID("12345678-1234-5678-1234-567812345678")
        webhook_id = UUID("87654321-4321-8765-4321-876543218765")

        check_run = CheckRun(
            pr_id=pr_id,
            webhook_id=webhook_id,
            check_name="tests",
            status="in_progress",
            started_at=datetime.now(UTC),
        )

        assert check_run.status == "in_progress"
        assert check_run.conclusion is None
        assert check_run.completed_at is None
        assert check_run.duration_ms is None

    def test_check_run_repr(self) -> None:
        """Test CheckRun __repr__ method."""
        pr_id = UUID("12345678-1234-5678-1234-567812345678")
        webhook_id = UUID("87654321-4321-8765-4321-876543218765")

        check_run = CheckRun(
            pr_id=pr_id,
            webhook_id=webhook_id,
            check_name="lint",
            status="completed",
            conclusion="success",
            started_at=datetime.now(UTC),
        )

        repr_str = repr(check_run)
        assert "CheckRun" in repr_str
        assert str(pr_id) in repr_str
        assert "lint" in repr_str
        assert "completed" in repr_str
        assert "success" in repr_str


class TestAPIUsageModel:
    """Test APIUsage model instantiation and fields."""

    def test_api_usage_model_creation(self) -> None:
        """Test creating APIUsage instance with required fields."""
        webhook_id = UUID("87654321-4321-8765-4321-876543218765")

        api_usage = APIUsage(
            webhook_id=webhook_id,
            repository="org/repo",
            event_type="pull_request",
            api_calls_count=5,
            initial_rate_limit=5000,
            final_rate_limit=4995,
        )

        assert api_usage.webhook_id == webhook_id
        assert api_usage.repository == "org/repo"
        assert api_usage.event_type == "pull_request"
        assert api_usage.api_calls_count == 5
        assert api_usage.initial_rate_limit == 5000
        assert api_usage.final_rate_limit == 4995

    def test_api_usage_with_token_spend(self) -> None:
        """Test APIUsage with token_spend calculated."""
        webhook_id = UUID("87654321-4321-8765-4321-876543218765")

        api_usage = APIUsage(
            webhook_id=webhook_id,
            repository="org/repo",
            event_type="check_run",
            api_calls_count=10,
            initial_rate_limit=5000,
            final_rate_limit=4990,
            token_spend=10,
        )

        assert api_usage.token_spend == 10

    def test_api_usage_repr(self) -> None:
        """Test APIUsage __repr__ method."""
        webhook_id = UUID("87654321-4321-8765-4321-876543218765")

        api_usage = APIUsage(
            webhook_id=webhook_id,
            repository="test-org/test-repo",
            event_type="issue_comment",
            api_calls_count=3,
            initial_rate_limit=5000,
            final_rate_limit=4997,
            token_spend=3,
        )

        repr_str = repr(api_usage)
        assert "APIUsage" in repr_str
        assert str(webhook_id) in repr_str
        assert "test-org/test-repo" in repr_str
        assert "3" in repr_str


class TestModelTableNames:
    """Test that all models have correct table names."""

    def test_webhook_table_name(self) -> None:
        """Verify Webhook model has correct table name."""
        assert Webhook.__tablename__ == "webhooks"

    def test_pull_request_table_name(self) -> None:
        """Verify PullRequest model has correct table name."""
        assert PullRequest.__tablename__ == "pull_requests"

    def test_pr_event_table_name(self) -> None:
        """Verify PREvent model has correct table name."""
        assert PREvent.__tablename__ == "pr_events"

    def test_pr_review_table_name(self) -> None:
        """Verify PRReview model has correct table name."""
        assert PRReview.__tablename__ == "pr_reviews"

    def test_pr_label_table_name(self) -> None:
        """Verify PRLabel model has correct table name."""
        assert PRLabel.__tablename__ == "pr_labels"

    def test_check_run_table_name(self) -> None:
        """Verify CheckRun model has correct table name."""
        assert CheckRun.__tablename__ == "check_runs"

    def test_api_usage_table_name(self) -> None:
        """Verify APIUsage model has correct table name."""
        assert APIUsage.__tablename__ == "api_usage"
