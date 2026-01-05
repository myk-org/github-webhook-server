"""Tests for webhook_server/utils/context.py.

Tests WebhookContext dataclass and module-level context management functions.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from webhook_server.utils.context import (
    WebhookContext,
    clear_context,
    create_context,
    get_context,
)


@pytest.fixture
def mock_datetime():
    """Mock datetime.now(UTC) for deterministic tests."""
    base_time = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)

    def mock_now(tz=None):
        if tz == UTC:
            return base_time
        return datetime.now(tz)

    with patch("webhook_server.utils.context.datetime") as mock_dt:
        mock_dt.now = mock_now
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        yield base_time


@pytest.fixture(autouse=True)
def cleanup_context():
    """Clean up context after each test."""
    yield
    clear_context()


class TestWebhookContext:
    """Tests for WebhookContext dataclass."""

    def test_initialization_with_all_parameters(self, mock_datetime):
        """Test WebhookContext initialization with all parameters."""
        ctx = WebhookContext(
            hook_id="test-hook-123",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
            action="opened",
            sender="testuser",
            pr_number=42,
            pr_title="Test PR",
            pr_author="prauthor",
            api_user="api-bot",
            token_spend=10,
            initial_rate_limit=5000,
            final_rate_limit=4990,
        )

        assert ctx.hook_id == "test-hook-123"
        assert ctx.event_type == "pull_request"
        assert ctx.repository == "owner/repo"
        assert ctx.repository_full_name == "owner/repo"
        assert ctx.action == "opened"
        assert ctx.sender == "testuser"
        assert ctx.pr_number == 42
        assert ctx.pr_title == "Test PR"
        assert ctx.pr_author == "prauthor"
        assert ctx.api_user == "api-bot"
        assert ctx.started_at == mock_datetime
        assert ctx.completed_at is None
        assert ctx.workflow_steps == {}
        assert ctx._step_start_times == {}
        assert ctx.token_spend == 10
        assert ctx.initial_rate_limit == 5000
        assert ctx.final_rate_limit == 4990
        assert ctx.success is True
        assert ctx.error is None

    def test_initialization_with_minimal_parameters(self, mock_datetime):
        """Test WebhookContext initialization with minimal required parameters."""
        ctx = WebhookContext(
            hook_id="test-hook-456",
            event_type="check_run",
            repository="org/project",
            repository_full_name="org/project",
        )

        assert ctx.hook_id == "test-hook-456"
        assert ctx.event_type == "check_run"
        assert ctx.repository == "org/project"
        assert ctx.repository_full_name == "org/project"
        assert ctx.action is None
        assert ctx.sender is None
        assert ctx.pr_number is None
        assert ctx.pr_title is None
        assert ctx.pr_author is None
        assert ctx.api_user == ""
        assert ctx.started_at == mock_datetime
        assert ctx.success is True

    def test_start_step_creates_step_with_correct_data(self, mock_datetime):
        """Test start_step() creates step with timestamp and status 'started'."""
        ctx = WebhookContext(
            hook_id="hook-1",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        ctx.start_step("clone_repository", branch="main", url="https://github.com/owner/repo")

        assert "clone_repository" in ctx.workflow_steps
        step = ctx.workflow_steps["clone_repository"]
        assert step["timestamp"] == mock_datetime.isoformat()
        assert step["status"] == "started"
        assert step["error"] is None
        assert step["branch"] == "main"
        assert step["url"] == "https://github.com/owner/repo"
        assert ctx._step_start_times["clone_repository"] == mock_datetime

    def test_start_step_without_additional_data(self, mock_datetime):
        """Test start_step() with no additional metadata."""
        ctx = WebhookContext(
            hook_id="hook-2",
            event_type="issue_comment",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        ctx.start_step("assign_reviewers")

        assert "assign_reviewers" in ctx.workflow_steps
        step = ctx.workflow_steps["assign_reviewers"]
        assert step["timestamp"] == mock_datetime.isoformat()
        assert step["status"] == "started"
        assert step["error"] is None
        assert len(step) == 3  # timestamp, status, error

    def test_complete_step_updates_step_with_completed_status_and_duration(self, mock_datetime):
        """Test complete_step() updates step with status 'completed' and duration."""
        ctx = WebhookContext(
            hook_id="hook-3",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        # Start step
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            start_time = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
            mock_dt.now.return_value = start_time
            ctx.start_step("build_container")

        # Complete step 2.5 seconds later
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            end_time = datetime(2024, 1, 15, 10, 30, 2, 500000, tzinfo=UTC)
            mock_dt.now.return_value = end_time
            ctx.complete_step("build_container", image_tag="v1.2.3", size_mb=150)

        step = ctx.workflow_steps["build_container"]
        assert step["status"] == "completed"
        assert step["duration_ms"] == 2500
        assert step["error"] is None
        assert step["image_tag"] == "v1.2.3"
        assert step["size_mb"] == 150

    def test_complete_step_on_step_that_was_not_started(self, mock_datetime):
        """Test complete_step() on a step that wasn't started (edge case)."""
        ctx = WebhookContext(
            hook_id="hook-4",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        # Complete step without starting it first
        ctx.complete_step("never_started", result="ok")

        assert "never_started" in ctx.workflow_steps
        step = ctx.workflow_steps["never_started"]
        assert step["status"] == "completed"
        assert step["duration_ms"] is None  # No start time, so duration is None
        assert step["error"] is None
        assert step["result"] == "ok"
        assert step["timestamp"] == mock_datetime.isoformat()

    def test_fail_step_sets_error_data_at_step_level(self, mock_datetime):
        """Test fail_step() sets error data at step level."""
        ctx = WebhookContext(
            hook_id="hook-5",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        # Start step
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            start_time = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
            mock_dt.now.return_value = start_time
            ctx.start_step("run_tests")

        # Fail step 1.5 seconds later
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            end_time = datetime(2024, 1, 15, 10, 30, 1, 500000, tzinfo=UTC)
            mock_dt.now.return_value = end_time

            exception = ValueError("Test failed: assertion error")
            traceback_str = "Traceback (most recent call last):\n  File test.py line 42\nValueError: Test failed"

            ctx.fail_step(
                "run_tests",
                exception=exception,
                traceback_str=traceback_str,
                failed_test="test_authentication",
            )

        step = ctx.workflow_steps["run_tests"]
        assert step["status"] == "failed"
        assert step["duration_ms"] == 1500
        assert step["error"] is not None
        assert step["error"]["type"] == "ValueError"
        assert step["error"]["message"] == "Test failed: assertion error"
        assert step["error"]["traceback"] == traceback_str
        assert step["failed_test"] == "test_authentication"

    def test_fail_step_sets_error_at_top_level_and_success_false(self):
        """Test fail_step() sets error at top level AND sets success=False."""
        ctx = WebhookContext(
            hook_id="hook-6",
            event_type="check_run",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        ctx.start_step("deploy")

        exception = RuntimeError("Deployment failed: connection timeout")
        traceback_str = "Traceback (most recent call last):\n  File deploy.py line 100\nRuntimeError"

        ctx.fail_step("deploy", exception=exception, traceback_str=traceback_str)

        # Top-level error set
        assert ctx.error is not None
        assert ctx.error["type"] == "RuntimeError"
        assert ctx.error["message"] == "Deployment failed: connection timeout"
        assert ctx.error["traceback"] == traceback_str

        # Success flag set to False
        assert ctx.success is False

    def test_fail_step_on_step_that_was_not_started(self, mock_datetime):
        """Test fail_step() on a step that wasn't started (edge case)."""
        ctx = WebhookContext(
            hook_id="hook-7",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        exception = RuntimeError("Missing config key")
        traceback_str = "Traceback (most recent call last):\n  File config.py\nRuntimeError"

        ctx.fail_step("never_started", exception=exception, traceback_str=traceback_str)

        assert "never_started" in ctx.workflow_steps
        step = ctx.workflow_steps["never_started"]
        assert step["status"] == "failed"
        assert step["duration_ms"] is None  # No start time
        assert step["error"]["type"] == "RuntimeError"
        assert step["error"]["message"] == "Missing config key"
        assert ctx.success is False

    def test_to_dict_returns_correct_structure(self, mock_datetime):
        """Test to_dict() returns correct structure with all fields."""
        ctx = WebhookContext(
            hook_id="hook-8",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
            action="opened",
            sender="testuser",
            api_user="bot-user",
            token_spend=15,
            initial_rate_limit=5000,
            final_rate_limit=4985,
        )

        # Set completed_at
        completed_time = mock_datetime + timedelta(seconds=5)
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = completed_time
            ctx.completed_at = completed_time

        ctx.start_step("test_step")
        ctx.complete_step("test_step", result="success")

        result = ctx.to_dict()

        assert result["hook_id"] == "hook-8"
        assert result["event_type"] == "pull_request"
        assert result["action"] == "opened"
        assert result["sender"] == "testuser"
        assert result["repository"] == "owner/repo"
        assert result["repository_full_name"] == "owner/repo"
        assert result["pr"] is None  # No pr_number set
        assert result["api_user"] == "bot-user"

        # Timing
        assert result["timing"]["started_at"] == mock_datetime.isoformat()
        assert result["timing"]["completed_at"] == completed_time.isoformat()
        assert result["timing"]["duration_ms"] == 5000

        # Workflow steps
        assert "test_step" in result["workflow_steps"]

        # Metrics
        assert result["token_spend"] == 15
        assert result["initial_rate_limit"] == 5000
        assert result["final_rate_limit"] == 4985

        # Status
        assert result["success"] is True
        assert result["error"] is None

    def test_to_dict_with_pr_info(self, mock_datetime):
        """Test to_dict() with PR info (pr_number set)."""
        ctx = WebhookContext(
            hook_id="hook-9",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
            pr_number=123,
            pr_title="Add new feature",
            pr_author="contributor",
        )

        result = ctx.to_dict()

        assert result["pr"] is not None
        assert result["pr"]["number"] == 123
        assert result["pr"]["title"] == "Add new feature"
        assert result["pr"]["author"] == "contributor"

    def test_to_dict_without_pr_info(self, mock_datetime):
        """Test to_dict() without PR info (pr_number is None)."""
        ctx = WebhookContext(
            hook_id="hook-10",
            event_type="check_run",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        result = ctx.to_dict()

        assert result["pr"] is None

    def test_to_dict_without_completed_at(self, mock_datetime):
        """Test to_dict() when completed_at is None."""
        ctx = WebhookContext(
            hook_id="hook-11",
            event_type="issue_comment",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        result = ctx.to_dict()

        assert result["timing"]["started_at"] == mock_datetime.isoformat()
        assert result["timing"]["completed_at"] is None
        assert result["timing"]["duration_ms"] is None

    def test_to_dict_with_error(self, mock_datetime):
        """Test to_dict() with error information."""
        ctx = WebhookContext(
            hook_id="hook-12",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        exception = ValueError("Something went wrong")
        traceback_str = "Traceback..."
        ctx.fail_step("failed_step", exception=exception, traceback_str=traceback_str)

        result = ctx.to_dict()

        assert result["success"] is False
        assert result["error"] is not None
        assert result["error"]["type"] == "ValueError"
        assert result["error"]["message"] == "Something went wrong"


class TestContextManagement:
    """Tests for module-level context management functions."""

    def test_create_context_creates_and_stores_context(self, mock_datetime):
        """Test create_context() creates and stores context in ContextVar."""
        ctx = create_context(
            hook_id="delivery-123",
            event_type="pull_request",
            repository="myorg/myrepo",
            repository_full_name="myorg/myrepo",
            action="synchronize",
            sender="devuser",
            api_user="github-bot",
        )

        # Verify returned context
        assert isinstance(ctx, WebhookContext)
        assert ctx.hook_id == "delivery-123"
        assert ctx.event_type == "pull_request"
        assert ctx.repository == "myorg/myrepo"
        assert ctx.repository_full_name == "myorg/myrepo"
        assert ctx.action == "synchronize"
        assert ctx.sender == "devuser"
        assert ctx.api_user == "github-bot"
        assert ctx.started_at == mock_datetime

        # Verify it's stored in ContextVar
        stored_ctx = get_context()
        assert stored_ctx is ctx

    def test_create_context_with_minimal_parameters(self):
        """Test create_context() with minimal required parameters."""
        ctx = create_context(
            hook_id="delivery-456",
            event_type="check_run",
            repository="org/project",
            repository_full_name="org/project",
        )

        assert ctx.hook_id == "delivery-456"
        assert ctx.event_type == "check_run"
        assert ctx.repository == "org/project"
        assert ctx.repository_full_name == "org/project"
        assert ctx.action is None
        assert ctx.sender is None
        assert ctx.api_user == ""

    def test_get_context_retrieves_stored_context(self):
        """Test get_context() retrieves stored context."""
        # Create context
        created_ctx = create_context(
            hook_id="delivery-789",
            event_type="issue_comment",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        # Retrieve context
        retrieved_ctx = get_context()

        assert retrieved_ctx is created_ctx
        assert retrieved_ctx.hook_id == "delivery-789"
        assert retrieved_ctx.event_type == "issue_comment"

    def test_get_context_returns_none_when_no_context_set(self):
        """Test get_context() returns None when no context set."""
        # Clear any existing context
        clear_context()

        ctx = get_context()
        assert ctx is None

    def test_clear_context_removes_context_from_contextvar(self):
        """Test clear_context() removes context from ContextVar."""
        # Create context
        create_context(
            hook_id="delivery-999",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        # Verify context exists
        assert get_context() is not None

        # Clear context
        clear_context()

        # Verify context is gone
        assert get_context() is None

    def test_context_isolation_between_create_calls(self):
        """Test that creating new context replaces old context."""
        # Create first context
        ctx1 = create_context(
            hook_id="first-delivery",
            event_type="pull_request",
            repository="owner/repo1",
            repository_full_name="owner/repo1",
        )

        assert get_context() is ctx1

        # Create second context
        ctx2 = create_context(
            hook_id="second-delivery",
            event_type="check_run",
            repository="owner/repo2",
            repository_full_name="owner/repo2",
        )

        # Second context should replace first
        current_ctx = get_context()
        assert current_ctx is ctx2
        assert current_ctx is not ctx1
        assert current_ctx.hook_id == "second-delivery"


class TestWorkflowStepSequence:
    """Integration tests for complete workflow step sequences."""

    def test_complete_workflow_with_successful_steps(self):
        """Test a complete workflow with multiple successful steps."""
        ctx = create_context(
            hook_id="workflow-1",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
            action="opened",
        )

        # Step 1: Clone
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
            ctx.start_step("clone_repository", branch="main")

        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 10, 0, 5, tzinfo=UTC)
            ctx.complete_step("clone_repository", commit_sha="abc123")

        # Step 2: Build
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 10, 0, 5, tzinfo=UTC)
            ctx.start_step("build_container")

        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 10, 0, 15, tzinfo=UTC)
            ctx.complete_step("build_container", image="myimage:latest")

        # Verify workflow
        assert len(ctx.workflow_steps) == 2
        assert ctx.workflow_steps["clone_repository"]["status"] == "completed"
        assert ctx.workflow_steps["clone_repository"]["duration_ms"] == 5000
        assert ctx.workflow_steps["build_container"]["status"] == "completed"
        assert ctx.workflow_steps["build_container"]["duration_ms"] == 10000
        assert ctx.success is True
        assert ctx.error is None

    def test_workflow_with_failed_step(self):
        """Test workflow with a failed step."""
        ctx = create_context(
            hook_id="workflow-2",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        # Step 1: Success
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
            ctx.start_step("validate_config")

        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 10, 0, 1, tzinfo=UTC)
            ctx.complete_step("validate_config")

        # Step 2: Failure
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 10, 0, 1, tzinfo=UTC)
            ctx.start_step("run_tests")

        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 10, 0, 10, tzinfo=UTC)
            exception = AssertionError("Test assertion failed")
            ctx.fail_step("run_tests", exception=exception, traceback_str="Traceback...")

        # Verify workflow state
        assert len(ctx.workflow_steps) == 2
        assert ctx.workflow_steps["validate_config"]["status"] == "completed"
        assert ctx.workflow_steps["run_tests"]["status"] == "failed"
        assert ctx.success is False
        assert ctx.error is not None
        assert ctx.error["type"] == "AssertionError"
