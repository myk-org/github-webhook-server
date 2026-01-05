"""Webhook execution context tracking using ContextVars.

This module provides a thread-safe, async-safe context tracking system for webhook processing.
Each webhook execution creates a WebhookContext that captures workflow steps, timing, errors,
and API metrics.

Architecture:
- Uses ContextVar for thread-safe and async-safe context isolation
- Each webhook request gets its own isolated context
- Context persists through async operations and handler chains
- Automatically tracks workflow steps with timing and errors

Usage:
    from webhook_server.utils.context import create_context, get_context

    # Create context at webhook entry point
    ctx = create_context(
        hook_id="github-delivery-id",
        event_type="pull_request",
        repository="org/repo",
        repository_full_name="org/repo",
        action="opened",
        sender="username",
    )

    # Track workflow steps
    ctx.start_step("clone_repository", branch="main")
    try:
        await clone_repo()
        ctx.complete_step("clone_repository", commit_sha="abc123")
    except Exception as ex:
        ctx.fail_step("clone_repository", exception=ex, traceback_str=traceback.format_exc())

    # Get context anywhere in the call stack
    ctx = get_context()
    if ctx:
        ctx.start_step("assign_reviewers")
"""

from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

_webhook_context: ContextVar["WebhookContext | None"] = ContextVar("webhook_context", default=None)


@dataclass
class WebhookContext:
    """Webhook execution context with workflow tracking and metrics.

    Captures all relevant information about a webhook execution including:
    - Core webhook metadata (hook_id, event_type, repository, action, sender)
    - PR information when available (number, title, author)
    - API user making requests
    - Timing information (start, completion, step durations)
    - Workflow steps with individual status and errors
    - GitHub API token metrics (spend, rate limits)
    - Overall execution status and errors

    Attributes:
        hook_id: GitHub webhook delivery ID (X-GitHub-Delivery header)
        event_type: GitHub event type (pull_request, issue_comment, check_run, etc.)
        repository: Repository name (org/repo)
        repository_full_name: Full repository name (org/repo)
        action: Webhook action (opened, synchronize, completed, etc.)
        sender: GitHub username who triggered the webhook
        pr_number: Pull request number if applicable
        pr_title: Pull request title if applicable
        pr_author: Pull request author username if applicable
        api_user: GitHub API user making requests
        started_at: Webhook processing start time (UTC)
        completed_at: Webhook processing completion time (UTC)
        workflow_steps: Dict of workflow steps keyed by step name
        token_spend: GitHub API tokens consumed (rate_limit_before - rate_limit_after)
        initial_rate_limit: GitHub API rate limit at start
        final_rate_limit: GitHub API rate limit at end
        success: Overall execution success status
        error: Top-level error details with traceback if execution failed
    """

    # Core webhook info
    hook_id: str
    event_type: str
    repository: str
    repository_full_name: str
    action: str | None = None
    sender: str | None = None

    # PR info (populated when available)
    pr_number: int | None = None
    pr_title: str | None = None
    pr_author: str | None = None

    # API user
    api_user: str = ""

    # Timing
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    # Workflow steps - dict keyed by action name
    # e.g., {"clone_repository": {...}, "assign_reviewers": {...}}
    workflow_steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    _step_start_times: dict[str, datetime] = field(default_factory=dict)  # Internal tracking

    # Token metrics
    token_spend: int | None = None
    initial_rate_limit: int | None = None
    final_rate_limit: int | None = None

    # Final status
    success: bool = True
    error: dict[str, Any] | None = None  # Top-level error with traceback

    def start_step(self, step_name: str, **data: Any) -> None:
        """Start a workflow step.

        Records the step start time and initializes step tracking with "started" status.
        Additional step metadata can be passed as keyword arguments.

        Args:
            step_name: Unique identifier for this workflow step
            **data: Additional step metadata (e.g., branch="main", commit_sha="abc123")
        """
        now = datetime.now(UTC)
        self._step_start_times[step_name] = now
        self.workflow_steps[step_name] = {
            "timestamp": now.isoformat(),
            "status": "started",
            "error": None,
            **data,
        }

    def complete_step(self, step_name: str, **data: Any) -> None:
        """Complete a workflow step successfully.

        Marks the step as completed, calculates duration, and updates step metadata.
        Additional result data can be passed as keyword arguments.

        Args:
            step_name: Unique identifier for this workflow step
            **data: Additional step result data (e.g., reviewers_assigned=3, labels_added=["verified"])
        """
        now = datetime.now(UTC)
        start_time = self._step_start_times.get(step_name)
        duration_ms = int((now - start_time).total_seconds() * 1000) if start_time else None

        if step_name not in self.workflow_steps:
            self.workflow_steps[step_name] = {"timestamp": now.isoformat()}

        self.workflow_steps[step_name].update({
            "status": "completed",
            "duration_ms": duration_ms,
            "error": None,
            **data,
        })

    def fail_step(self, step_name: str, exception: Exception, traceback_str: str, **data: Any) -> None:
        """Mark a workflow step as failed with error details.

        Captures exception type, message, and full traceback. Sets the step status to "failed"
        and also updates the top-level context error and success flag.

        Args:
            step_name: Unique identifier for this workflow step
            exception: Exception that caused the failure
            traceback_str: Full traceback string (use traceback.format_exc())
            **data: Additional error context data
        """
        now = datetime.now(UTC)
        start_time = self._step_start_times.get(step_name)
        duration_ms = int((now - start_time).total_seconds() * 1000) if start_time else None

        error_data = {
            "type": type(exception).__name__,
            "message": str(exception),
            "traceback": traceback_str,
        }

        if step_name not in self.workflow_steps:
            self.workflow_steps[step_name] = {"timestamp": now.isoformat()}

        self.workflow_steps[step_name].update({
            "status": "failed",
            "duration_ms": duration_ms,
            "error": error_data,
            **data,
        })

        # Also set top-level error
        self.success = False
        self.error = error_data

    def to_dict(self) -> dict[str, Any]:
        """Convert context to dictionary for JSON serialization.

        Returns a complete representation of the webhook execution context including
        all workflow steps, timing information, and error details.

        Returns:
            Dict containing all context data in JSON-serializable format
        """
        return {
            "hook_id": self.hook_id,
            "event_type": self.event_type,
            "action": self.action,
            "sender": self.sender,
            "repository": self.repository,
            "repository_full_name": self.repository_full_name,
            "pr": {
                "number": self.pr_number,
                "title": self.pr_title,
                "author": self.pr_author,
            }
            if self.pr_number
            else None,
            "api_user": self.api_user,
            "timing": {
                "started_at": self.started_at.isoformat(),
                "completed_at": (self.completed_at.isoformat() if self.completed_at else None),
                "duration_ms": int((self.completed_at - self.started_at).total_seconds() * 1000)
                if self.completed_at
                else None,
            },
            "workflow_steps": self.workflow_steps,
            "token_spend": self.token_spend,
            "initial_rate_limit": self.initial_rate_limit,
            "final_rate_limit": self.final_rate_limit,
            "success": self.success,
            "error": self.error,
        }


def create_context(
    hook_id: str,
    event_type: str,
    repository: str,
    repository_full_name: str,
    action: str | None = None,
    sender: str | None = None,
    api_user: str = "",
) -> WebhookContext:
    """Create and set a new WebhookContext in the current async context.

    Creates a new context and stores it in the ContextVar for the current execution context.
    This context will be accessible to all code running in the same async task.

    Args:
        hook_id: GitHub webhook delivery ID (X-GitHub-Delivery header)
        event_type: GitHub event type (pull_request, issue_comment, check_run, etc.)
        repository: Repository name (org/repo)
        repository_full_name: Full repository name (org/repo)
        action: Webhook action (opened, synchronize, completed, etc.)
        sender: GitHub username who triggered the webhook
        api_user: GitHub API user making requests

    Returns:
        The created WebhookContext instance
    """
    ctx = WebhookContext(
        hook_id=hook_id,
        event_type=event_type,
        repository=repository,
        repository_full_name=repository_full_name,
        action=action,
        sender=sender,
        api_user=api_user,
    )
    _webhook_context.set(ctx)
    return ctx


def get_context() -> WebhookContext | None:
    """Get the current WebhookContext for this execution context.

    Returns the context associated with the current async task, or None if no context
    has been set. This allows any code in the call stack to access the current webhook
    context without explicit parameter passing.

    Returns:
        The current WebhookContext, or None if no context is set
    """
    return _webhook_context.get()


def clear_context() -> None:
    """Clear the current WebhookContext.

    Removes the context from the current execution context. Should be called at the end
    of webhook processing to prevent context leakage between requests.
    """
    _webhook_context.set(None)
