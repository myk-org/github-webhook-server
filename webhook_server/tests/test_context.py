"""Tests for webhook_server/utils/context.py.

Tests WebhookContext dataclass and module-level context management functions.
"""

from __future__ import annotations

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

    def mock_now(tz: datetime.tzinfo | None = None) -> datetime:
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


class TestCompleteStepSmartFiltering:
    """Tests for complete_step() smart filtering of verbose output."""

    def test_complete_step_filters_reason_on_success_can_merge_true(self):
        """Test complete_step() filters 'reason' field when can_merge=True."""
        ctx = WebhookContext(
            hook_id="hook-filter-1",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        ctx.start_step("check_merge_eligibility")
        ctx.complete_step("check_merge_eligibility", can_merge=True, reason="All checks passed")

        step = ctx.workflow_steps["check_merge_eligibility"]
        assert step["status"] == "completed"
        assert step["can_merge"] is True
        assert "reason" not in step  # Reason filtered out on success

    def test_complete_step_includes_reason_on_failure_can_merge_false(self):
        """Test complete_step() includes 'reason' field when can_merge=False."""
        ctx = WebhookContext(
            hook_id="hook-filter-2",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        ctx.start_step("check_merge_eligibility")
        ctx.complete_step("check_merge_eligibility", can_merge=False, reason="Missing approver")

        step = ctx.workflow_steps["check_merge_eligibility"]
        assert step["status"] == "completed"
        assert step["can_merge"] is False
        assert step["reason"] == "Missing approver"  # Reason included on failure

    def test_complete_step_filters_reason_on_success_true(self):
        """Test complete_step() filters 'reason' field when success=True."""
        ctx = WebhookContext(
            hook_id="hook-filter-3",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        ctx.start_step("validate_config")
        ctx.complete_step("validate_config", success=True, reason="Config is valid")

        step = ctx.workflow_steps["validate_config"]
        assert step["status"] == "completed"
        assert step["success"] is True
        assert "reason" not in step  # Reason filtered out on success

    def test_complete_step_includes_reason_on_success_false(self):
        """Test complete_step() includes 'reason' field when success=False."""
        ctx = WebhookContext(
            hook_id="hook-filter-4",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        ctx.start_step("validate_config")
        ctx.complete_step("validate_config", success=False, reason="Missing required field")

        step = ctx.workflow_steps["validate_config"]
        assert step["status"] == "completed"
        assert step["success"] is False
        assert step["reason"] == "Missing required field"  # Reason included on failure

    def test_complete_step_custom_verbose_fields(self):
        """Test complete_step() with custom verbose_fields parameter."""
        ctx = WebhookContext(
            hook_id="hook-filter-5",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        ctx.start_step("build_image")
        ctx.complete_step(
            "build_image",
            verbose_fields=["build_log", "debug_info"],
            success=True,
            build_log="Log output...",
            debug_info="Debug details...",
            image_tag="v1.0.0",
        )

        step = ctx.workflow_steps["build_image"]
        assert step["status"] == "completed"
        assert step["success"] is True
        assert step["image_tag"] == "v1.0.0"  # Non-verbose field included
        assert "build_log" not in step  # Verbose field filtered out
        assert "debug_info" not in step  # Verbose field filtered out

    def test_complete_step_custom_verbose_fields_on_failure(self):
        """Test complete_step() custom verbose_fields included on failure."""
        ctx = WebhookContext(
            hook_id="hook-filter-6",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        ctx.start_step("build_image")
        ctx.complete_step(
            "build_image",
            verbose_fields=["build_log", "debug_info"],
            success=False,
            build_log="Error: build failed",
            debug_info="Stack trace...",
            image_tag="v1.0.0",
        )

        step = ctx.workflow_steps["build_image"]
        assert step["status"] == "completed"
        assert step["success"] is False
        assert step["image_tag"] == "v1.0.0"
        assert step["build_log"] == "Error: build failed"  # Verbose field included on failure
        assert step["debug_info"] == "Stack trace..."  # Verbose field included on failure

    def test_complete_step_detects_success_with_suffix_patterns(self):
        """Test complete_step() detects success using _success and _failed suffix patterns."""
        ctx = WebhookContext(
            hook_id="hook-filter-7",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        # Test _success suffix
        ctx.start_step("test_success_suffix")
        ctx.complete_step("test_success_suffix", build_success=True, reason="Build succeeded")

        step = ctx.workflow_steps["test_success_suffix"]
        assert step["build_success"] is True
        assert "reason" not in step  # Filtered out because build_success=True

        # Test _failed suffix
        ctx.start_step("test_failed_suffix")
        ctx.complete_step("test_failed_suffix", build_failed=False, reason="Build succeeded")

        step = ctx.workflow_steps["test_failed_suffix"]
        assert step["build_failed"] is False
        assert "reason" not in step  # Filtered out because build_failed=False (success)

    def test_complete_step_includes_verbose_on_failure_suffix_patterns(self):
        """Test complete_step() includes verbose fields on failure using suffix patterns."""
        ctx = WebhookContext(
            hook_id="hook-filter-8",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        # Test _success=False
        ctx.start_step("test_success_false")
        ctx.complete_step("test_success_false", build_success=False, reason="Build failed")

        step = ctx.workflow_steps["test_success_false"]
        assert step["build_success"] is False
        assert step["reason"] == "Build failed"  # Included because build_success=False

        # Test _failed=True
        ctx.start_step("test_failed_true")
        ctx.complete_step("test_failed_true", build_failed=True, reason="Build failed")

        step = ctx.workflow_steps["test_failed_true"]
        assert step["build_failed"] is True
        assert step["reason"] == "Build failed"  # Included because build_failed=True

    def test_complete_step_default_success_when_no_indicators(self):
        """Test complete_step() defaults to success when no indicators present."""
        ctx = WebhookContext(
            hook_id="hook-filter-9",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        ctx.start_step("neutral_step")
        ctx.complete_step("neutral_step", count=5, reason="Processed 5 items")

        step = ctx.workflow_steps["neutral_step"]
        assert step["status"] == "completed"
        assert step["count"] == 5
        assert "reason" not in step  # Filtered out (defaults to success)

    def test_complete_step_no_filtering_when_verbose_fields_empty(self):
        """Test complete_step() with verbose_fields=[] (no filtering)."""
        ctx = WebhookContext(
            hook_id="hook-filter-10",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        ctx.start_step("no_filtering")
        ctx.complete_step("no_filtering", verbose_fields=[], can_merge=True, reason="All good")

        step = ctx.workflow_steps["no_filtering"]
        assert step["can_merge"] is True
        assert step["reason"] == "All good"  # Not filtered (verbose_fields=[])

    def test_complete_step_filters_multiple_verbose_fields(self):
        """Test complete_step() filters multiple verbose fields on success."""
        ctx = WebhookContext(
            hook_id="hook-filter-11",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        ctx.start_step("multi_verbose")
        ctx.complete_step(
            "multi_verbose",
            verbose_fields=["reason", "details", "debug"],
            can_merge=True,
            reason="Success",
            details="Details...",
            debug="Debug info...",
            count=10,
        )

        step = ctx.workflow_steps["multi_verbose"]
        assert step["can_merge"] is True
        assert step["count"] == 10
        assert "reason" not in step
        assert "details" not in step
        assert "debug" not in step

    def test_complete_step_error_indicator_overrides_can_merge(self):
        """Test complete_step() error indicator takes precedence."""
        ctx = WebhookContext(
            hook_id="hook-filter-12",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        ctx.start_step("error_override")
        ctx.complete_step("error_override", can_merge=True, error="Something went wrong", reason="Error occurred")

        step = ctx.workflow_steps["error_override"]
        assert step["can_merge"] is True
        assert step["error"] == "Something went wrong"
        assert step["reason"] == "Error occurred"  # Included because error is not None


class TestBuildSummary:
    """Tests for _build_summary() method and summary field in to_dict()."""

    def test_build_summary_with_pr_and_token_spend(self):
        """Test _build_summary() with PR number and token spend."""
        # Mock datetime for entire test to control started_at and completed_at
        start_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = start_time
            ctx = WebhookContext(
                hook_id="hook-summary-1",
                event_type="pull_request",
                repository="owner/repo",
                repository_full_name="owner/repo",
                pr_number=968,
                token_spend=4,
            )

        # Set workflow steps with durations
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            step_start = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
            mock_dt.now.return_value = step_start
            ctx.start_step("webhook_routing")

        with patch("webhook_server.utils.context.datetime") as mock_dt:
            step_end = datetime(2024, 1, 15, 10, 0, 2, 547000, tzinfo=UTC)
            mock_dt.now.return_value = step_end
            ctx.complete_step("webhook_routing")

        with patch("webhook_server.utils.context.datetime") as mock_dt:
            step_start = datetime(2024, 1, 15, 10, 0, 2, 547000, tzinfo=UTC)
            mock_dt.now.return_value = step_start
            ctx.start_step("repo_clone")

        with patch("webhook_server.utils.context.datetime") as mock_dt:
            step_end = datetime(2024, 1, 15, 10, 0, 5, 93000, tzinfo=UTC)
            mock_dt.now.return_value = step_end
            ctx.complete_step("repo_clone")

        with patch("webhook_server.utils.context.datetime") as mock_dt:
            step_start = datetime(2024, 1, 15, 10, 0, 5, 93000, tzinfo=UTC)
            mock_dt.now.return_value = step_start
            ctx.start_step("push_handler")

        with patch("webhook_server.utils.context.datetime") as mock_dt:
            step_end = datetime(2024, 1, 15, 10, 0, 5, 93000, tzinfo=UTC)
            mock_dt.now.return_value = step_end
            ctx.complete_step("push_handler")

        # Set completed_at
        completed_time = datetime(2024, 1, 15, 10, 0, 7, 712000, tzinfo=UTC)
        ctx.completed_at = completed_time

        summary = ctx._build_summary()

        # Verify format: [SUCCESS] Webhook completed PR#968 [7s712ms, tokens:4] steps=[...]
        assert summary is not None
        assert summary.startswith("[SUCCESS] Webhook completed PR#968 [7s712ms, tokens:4] steps=[")
        assert "webhook_routing:completed(2s547ms)" in summary
        assert "repo_clone:completed(2s546ms)" in summary
        assert "push_handler:completed(0ms)" in summary

    def test_build_summary_without_pr(self):
        """Test _build_summary() without PR number."""
        start_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = start_time
            ctx = WebhookContext(
                hook_id="hook-summary-2",
                event_type="check_run",
                repository="owner/repo",
                repository_full_name="owner/repo",
            )

        # Add one step
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            step_start = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
            mock_dt.now.return_value = step_start
            ctx.start_step("validate_config")

        with patch("webhook_server.utils.context.datetime") as mock_dt:
            step_end = datetime(2024, 1, 15, 10, 0, 1, 500000, tzinfo=UTC)
            mock_dt.now.return_value = step_end
            ctx.complete_step("validate_config")

        # Set completed_at
        completed_time = datetime(2024, 1, 15, 10, 0, 1, 500000, tzinfo=UTC)
        ctx.completed_at = completed_time

        summary = ctx._build_summary()

        # Verify format: [SUCCESS] Webhook completed [1s500ms] steps=[...]
        assert summary is not None
        assert summary.startswith("[SUCCESS] Webhook completed [1s500ms] steps=[")
        assert "PR#" not in summary  # No PR number
        assert "tokens:" not in summary  # No token spend
        assert "validate_config:completed(1s500ms)" in summary

    def test_build_summary_with_failed_step(self):
        """Test _build_summary() with failed workflow step."""
        start_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = start_time
            ctx = WebhookContext(
                hook_id="hook-summary-3",
                event_type="pull_request",
                repository="owner/repo",
                repository_full_name="owner/repo",
                pr_number=123,
            )

        # Add failed step
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            step_start = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
            mock_dt.now.return_value = step_start
            ctx.start_step("build_container")

        with patch("webhook_server.utils.context.datetime") as mock_dt:
            step_end = datetime(2024, 1, 15, 10, 0, 5, tzinfo=UTC)
            mock_dt.now.return_value = step_end
            exception = RuntimeError("Build failed")
            ctx.fail_step("build_container", exception=exception, traceback_str="Traceback...")

        completed_time = datetime(2024, 1, 15, 10, 0, 5, tzinfo=UTC)
        ctx.completed_at = completed_time

        summary = ctx._build_summary()

        # Verify format: [FAILED] Webhook completed PR#123 [5s] steps=[build_container:failed(5s)]
        assert summary is not None
        assert summary.startswith("[FAILED] Webhook completed PR#123 [5s] steps=[")
        assert "build_container:failed(5s)" in summary

    def test_build_summary_without_completed_at(self):
        """Test _build_summary() returns None when completed_at is not set."""
        ctx = WebhookContext(
            hook_id="hook-summary-4",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        summary = ctx._build_summary()

        assert summary is None

    def test_build_summary_without_steps(self):
        """Test _build_summary() with no workflow steps."""
        ctx = WebhookContext(
            hook_id="hook-summary-5",
            event_type="issue_comment",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        # Set completed_at
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            start_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
            mock_dt.now.return_value = start_time
            ctx.started_at = start_time

            completed_time = datetime(2024, 1, 15, 10, 0, 3, tzinfo=UTC)
            ctx.completed_at = completed_time

        summary = ctx._build_summary()

        # Verify format: [SUCCESS] Webhook completed [3s] steps=[no steps recorded]
        assert summary is not None
        assert summary == "[SUCCESS] Webhook completed [3s] steps=[no steps recorded]"

    def test_to_dict_includes_summary_field(self):
        """Test to_dict() includes summary field when completed_at is set."""
        start_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

        with patch("webhook_server.utils.context.datetime") as mock_dt:
            mock_dt.now.return_value = start_time
            ctx = WebhookContext(
                hook_id="hook-summary-6",
                event_type="pull_request",
                repository="owner/repo",
                repository_full_name="owner/repo",
                pr_number=456,
                token_spend=10,
            )

        # Add step
        with patch("webhook_server.utils.context.datetime") as mock_dt:
            step_start = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
            mock_dt.now.return_value = step_start
            ctx.start_step("test_step")

        with patch("webhook_server.utils.context.datetime") as mock_dt:
            step_end = datetime(2024, 1, 15, 10, 0, 2, tzinfo=UTC)
            mock_dt.now.return_value = step_end
            ctx.complete_step("test_step")

        completed_time = datetime(2024, 1, 15, 10, 0, 2, tzinfo=UTC)
        ctx.completed_at = completed_time

        result = ctx.to_dict()

        # Verify summary field is present and correct
        assert "summary" in result
        assert result["summary"] is not None
        assert result["summary"].startswith("[SUCCESS] Webhook completed PR#456 [2s, tokens:10] steps=[")
        assert "test_step:completed(2s)" in result["summary"]

    def test_to_dict_summary_is_none_without_completed_at(self):
        """Test to_dict() summary field is None when completed_at is not set."""
        ctx = WebhookContext(
            hook_id="hook-summary-7",
            event_type="pull_request",
            repository="owner/repo",
            repository_full_name="owner/repo",
        )

        result = ctx.to_dict()

        # Verify summary field is None
        assert "summary" in result
        assert result["summary"] is None
