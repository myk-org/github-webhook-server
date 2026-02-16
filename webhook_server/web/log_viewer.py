"""Log viewer controller for serving log viewer web interface and API endpoints."""

import asyncio
import datetime
import json
import logging
import os
import re
from collections import deque
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse

from webhook_server.libs.config import Config
from webhook_server.libs.log_parser import LogEntry, LogFilter, LogParser


class LogViewerController:
    """Controller for log viewer functionality."""

    # Maximum log entries to return for a single step to prevent unbounded responses
    _MAX_STEP_LOGS = 500

    # Default time window in milliseconds when step duration is unknown
    # 60 seconds provides a reasonable maximum window for log correlation
    _DEFAULT_STEP_DURATION_MS = 60000

    # Workflow stage patterns for PR flow analysis
    # These patterns match log messages to identify workflow stages and can be updated
    # when log message formats change without modifying the analysis logic
    WORKFLOW_STAGE_PATTERNS = [
        ("Webhook Received", r"Processing webhook"),
        ("Validation Complete", r"Signature verification successful|Processing webhook for"),
        ("Reviewers Assigned", r"Added reviewer|OWNERS file|reviewer assignment"),
        ("Labels Applied", r"label|tag"),
        ("Checks Started", r"check|test|build"),
        ("Checks Complete", r"check.*complete|test.*pass|build.*success"),
        ("Processing Complete", r"completed successfully|processing complete"),
    ]

    def __init__(self, logger: logging.Logger) -> None:
        """Initialize the log viewer controller.

        Args:
            logger: Logger instance for this controller

        """
        self.logger = logger
        self.config = Config(logger=self.logger)
        self.log_parser = LogParser()
        self.log_filter = LogFilter()
        self._websocket_connections: set[WebSocket] = set()

    async def shutdown(self) -> None:
        """Close all active WebSocket connections during shutdown.

        This method should be called during application shutdown to properly
        close all WebSocket connections and prevent resource leaks.
        """
        self.logger.info(
            f"Shutting down LogViewerController with {len(self._websocket_connections)} active connections",
        )

        # Create a copy of the connections set to avoid modification during iteration
        connections_to_close = list(self._websocket_connections)

        for ws in connections_to_close:
            try:
                await ws.close(code=1001, reason="Server shutdown")
                self.logger.debug("Successfully closed WebSocket connection during shutdown")
            except Exception as e:
                # Log the error but continue closing other connections
                self.logger.warning(f"Error closing WebSocket connection during shutdown: {e}")

        # Clear the connections set
        self._websocket_connections.clear()
        self.logger.info("LogViewerController shutdown completed")

    async def get_log_page(self) -> HTMLResponse:
        """Serve the main log viewer HTML page.

        Returns:
            HTML response with log viewer interface

        Raises:
            HTTPException: 500 for other errors

        """
        try:
            html_content = await self._get_log_viewer_html()
            return HTMLResponse(content=html_content)
        except Exception as e:
            self.logger.exception("Error serving log viewer page")
            raise HTTPException(status_code=500, detail="Internal server error") from e

    async def get_log_entries(
        self,
        hook_id: str | None = None,
        pr_number: int | None = None,
        repository: str | None = None,
        event_type: str | None = None,
        github_user: str | None = None,
        level: str | None = None,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Retrieve historical log entries with filtering and pagination using memory-efficient streaming.

        This method implements memory-efficient log processing by streaming through log files
        and applying filters incrementally to avoid loading large datasets into memory.

        Args:
            hook_id: Filter by specific hook ID
            pr_number: Filter by PR number
            repository: Filter by repository name
            event_type: Filter by GitHub event type
            github_user: Filter by GitHub user (api_user)
            level: Filter by log level
            start_time: Start time filter
            end_time: End time filter
            search: Full-text search in log messages
            limit: Number of entries to return (max 1000)
            offset: Pagination offset

        Returns:
            Dictionary containing filtered log entries and comprehensive metadata:

            - **entries**: List of log entry dictionaries matching the applied filters
            - **entries_processed**: Number of log entries examined during this request.
              May be an integer or string with "+" suffix (e.g., "50000+") indicating
              the streaming process reached its maximum processing limit and more entries
              exist. This helps API consumers understand data completeness.
            - **filtered_count_min**: Minimum number of entries matching the current filters.
              Calculated as len(entries) + offset, representing the definitive lower bound
              of matching entries. This is useful for pagination calculations and showing
              "showing X of at least Y results" messages.
            - **total_log_count_estimate**: Statistical estimate of total log entries across
              all log files (including rotated logs). Provides context about the overall
              dataset size for UI statistics and capacity planning. Based on sampling
              the first 10 log files to balance accuracy with performance.
            - **limit**: Echo of the requested limit parameter
            - **offset**: Echo of the requested offset parameter
            - **is_partial_scan**: Boolean indicating whether the streaming process examined
              all available logs (false) or stopped at the processing limit (true)

        Raises:
            HTTPException: 400 for invalid parameters, 500 for file access errors

        """
        try:
            # Validate parameters
            if limit < 1 or limit > 10000:
                raise ValueError("Limit must be between 1 and 10000")
            if offset < 0:
                raise ValueError("Offset must be non-negative")

            # Use memory-efficient streaming with filtering applied during iteration
            filtered_entries: list[LogEntry] = []
            total_processed = 0
            skipped = 0

            # Stream entries and apply filters incrementally
            # For any filtering, we need to process more entries to find all matches
            has_filters = any([
                hook_id,
                pr_number,
                repository,
                event_type,
                github_user,
                level,
                start_time,
                end_time,
                search,
            ])
            max_entries_to_process = 50000 if has_filters else 20000

            async for entry in self._stream_log_entries(max_files=25, max_entries=max_entries_to_process):
                total_processed += 1

                # Apply filters early to reduce memory usage
                if not self._entry_matches_filters(
                    entry,
                    hook_id,
                    pr_number,
                    repository,
                    event_type,
                    github_user,
                    level,
                    start_time,
                    end_time,
                    search,
                ):
                    continue

                # Handle pagination - skip entries until we reach the offset
                if skipped < offset:
                    skipped += 1
                    continue

                # Add to results if we haven't reached the limit
                if len(filtered_entries) < limit:
                    filtered_entries.append(entry)
                else:
                    # We have enough entries, can stop processing
                    break

            # Get approximate total count by processing a sample if needed
            estimated_total: int | str = total_processed
            if total_processed >= max_entries_to_process:  # Hit our streaming limit
                estimated_total = f"{total_processed}+"  # Indicate there are more

            # Estimate total log count across all files for better UI statistics
            total_log_count_estimate = self._estimate_total_log_count()

            return {
                "entries": [entry.to_dict() for entry in filtered_entries],
                "entries_processed": estimated_total,  # Number of entries examined
                "filtered_count_min": len(filtered_entries) + offset,  # Minimum filtered count
                "total_log_count_estimate": total_log_count_estimate,  # Estimated total logs in all files
                "limit": limit,
                "offset": offset,
                "is_partial_scan": total_processed >= max_entries_to_process,  # Indicates not all logs were scanned
            }

        except asyncio.CancelledError:
            self.logger.debug("Operation cancelled")
            raise  # Always re-raise CancelledError
        except ValueError as e:
            self.logger.warning(f"Invalid parameters for log entries request: {e}")
            raise HTTPException(status_code=400, detail=str(e)) from e
        except (OSError, PermissionError) as e:
            self.logger.exception("File access error loading log entries")
            raise HTTPException(status_code=500, detail="Error accessing log files") from e
        except Exception as e:
            self.logger.exception("Unexpected error getting log entries")
            raise HTTPException(status_code=500, detail="Internal server error") from e

    def _entry_matches_filters(
        self,
        entry: LogEntry,
        hook_id: str | None = None,
        pr_number: int | None = None,
        repository: str | None = None,
        event_type: str | None = None,
        github_user: str | None = None,
        level: str | None = None,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
        search: str | None = None,
    ) -> bool:
        """Check if a single entry matches the given filters.

        This allows for early filtering during streaming to reduce memory usage.

        Args:
            entry: LogEntry to check
            **filters: Filter parameters (same as get_log_entries)

        Returns:
            True if entry matches all filters, False otherwise

        """
        if hook_id is not None and entry.hook_id != hook_id:
            return False
        if pr_number is not None and entry.pr_number != pr_number:
            return False
        if repository is not None and entry.repository != repository:
            return False
        if event_type is not None and entry.event_type != event_type:
            return False
        if github_user is not None and entry.github_user != github_user:
            return False
        if level is not None and entry.level != level:
            return False
        if start_time is not None and entry.timestamp < start_time:
            return False
        if end_time is not None and entry.timestamp > end_time:
            return False
        if search is not None and search.lower() not in entry.message.lower():
            return False

        return True

    async def export_logs(
        self,
        format_type: str,
        hook_id: str | None = None,
        pr_number: int | None = None,
        repository: str | None = None,
        event_type: str | None = None,
        github_user: str | None = None,
        level: str | None = None,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
        search: str | None = None,
        limit: int = 10000,
    ) -> StreamingResponse:
        """Export filtered logs as JSON file.

        Args:
            format_type: Export format (only "json" is supported)
            hook_id: Filter by specific hook ID
            pr_number: Filter by PR number
            repository: Filter by repository name
            event_type: Filter by GitHub event type
            github_user: Filter by GitHub user (api_user)
            level: Filter by log level
            start_time: Start time filter
            end_time: End time filter
            search: Full-text search in log messages
            limit: Maximum number of entries to export

        Returns:
            StreamingResponse with file download

        Raises:
            HTTPException: 400 for invalid format, 413 if result set too large

        """
        try:
            if format_type != "json":
                raise ValueError(f"Invalid format: {format_type}. Only 'json' is supported.")

            if limit > 50000:
                raise ValueError("Result set too large (max 50000 entries)")

            # Use memory-efficient streaming for large exports
            filtered_entries: list[LogEntry] = []

            # Stream entries and apply filters incrementally for better memory usage
            # For any filtering, increase processing limit to find all matches
            has_filters = any([
                hook_id,
                pr_number,
                repository,
                event_type,
                github_user,
                level,
                start_time,
                end_time,
                search,
            ])
            max_entries_to_process = min(limit + 20000, 100000) if has_filters else limit + 1000

            async for entry in self._stream_log_entries(max_files=25, max_entries=max_entries_to_process):
                if not self._entry_matches_filters(
                    entry,
                    hook_id,
                    pr_number,
                    repository,
                    event_type,
                    github_user,
                    level,
                    start_time,
                    end_time,
                    search,
                ):
                    continue

                filtered_entries.append(entry)

                # Stop when we reach the export limit
                if len(filtered_entries) >= limit:
                    break

            # Collect filters for metadata
            filters = {
                "hook_id": hook_id,
                "pr_number": pr_number,
                "repository": repository,
                "event_type": event_type,
                "github_user": github_user,
                "level": level,
                "start_time": start_time.isoformat() if start_time else None,
                "end_time": end_time.isoformat() if end_time else None,
                "search": search,
                "limit": limit,
            }
            # Remove None values
            filters = {k: v for k, v in filters.items() if v is not None}

            # Generate JSON export content
            content = self._generate_json_export(filtered_entries, filters)
            media_type = "application/json"
            filename = f"webhook_logs_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

            def generate() -> Generator[bytes]:
                yield content.encode("utf-8")

            return StreamingResponse(
                generate(),
                media_type=media_type,
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )

        except asyncio.CancelledError:
            self.logger.debug("Operation cancelled")
            raise  # Always re-raise CancelledError
        except ValueError as e:
            if "Result set too large" in str(e):
                self.logger.warning(f"Export request too large: {e}")
                raise HTTPException(status_code=413, detail=str(e)) from e
            self.logger.warning(f"Invalid export parameters: {e}")
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            self.logger.exception("Error generating export")
            raise HTTPException(status_code=500, detail="Export generation failed") from e

    async def handle_websocket(
        self,
        websocket: WebSocket,
        hook_id: str | None = None,
        pr_number: int | None = None,
        repository: str | None = None,
        event_type: str | None = None,
        github_user: str | None = None,
        level: str | None = None,
    ) -> None:
        """Handle WebSocket connection for real-time log streaming.

        Args:
            websocket: WebSocket connection
            hook_id: Filter by specific hook ID
            pr_number: Filter by PR number
            repository: Filter by repository name
            event_type: Filter by GitHub event type
            github_user: Filter by GitHub user (api_user)
            level: Filter by log level

        """
        await websocket.accept()
        self._websocket_connections.add(websocket)

        try:
            self.logger.info("WebSocket connection established for log streaming")

            # Get log directory path
            log_dir = self._get_log_directory()
            if not log_dir.exists():
                await websocket.send_json({"error": "Log directory not found"})
                return

            # Start monitoring log files for new entries
            async for entry in self.log_parser.monitor_log_directory(log_dir):
                should_send = False

                # Apply filters to new entry - if no filters provided, send all entries
                if not any([hook_id, pr_number, repository, event_type, github_user, level]):
                    should_send = True
                else:
                    # Apply filters
                    filtered_entries = self.log_filter.filter_entries(
                        entries=[entry],
                        hook_id=hook_id,
                        pr_number=pr_number,
                        repository=repository,
                        event_type=event_type,
                        github_user=github_user,
                        level=level,
                    )
                    should_send = bool(filtered_entries)

                if should_send:
                    try:
                        await websocket.send_json(entry.to_dict())
                    except WebSocketDisconnect:
                        break

        except WebSocketDisconnect:
            self.logger.info("WebSocket client disconnected")
        except Exception:
            self.logger.exception("Error in WebSocket handler")
            try:
                await websocket.close(code=1011, reason="Internal server error")
            except Exception:
                pass
        finally:
            self._websocket_connections.discard(websocket)

    async def get_pr_flow_data(self, hook_id: str) -> dict[str, Any]:
        """Get PR flow visualization data for a specific hook ID or PR number.

        Args:
            hook_id: Hook ID (e.g., "hook-abc123") or PR number (e.g., "pr-456")

        Returns:
            Dictionary with flow stages and timing data

        Raises:
            HTTPException: 404 if no data found for hook_id

        """
        try:
            # Parse hook_id to determine if it's a hook ID or PR number
            if hook_id.startswith("hook-"):
                actual_hook_id = hook_id[5:]  # Remove "hook-" prefix
                pr_number = None
            elif hook_id.startswith("pr-"):
                actual_hook_id = None
                pr_number = int(hook_id[3:])  # Remove "pr-" prefix
            else:
                # Try to parse as direct hook ID or PR number
                try:
                    pr_number = int(hook_id)
                    actual_hook_id = None
                except ValueError:
                    actual_hook_id = hook_id
                    pr_number = None

            # Use streaming approach for memory efficiency
            filtered_entries: list[LogEntry] = []

            # Stream entries and filter by hook_id/pr_number
            async for entry in self._stream_log_entries(max_files=15, max_entries=10000):
                if not self._entry_matches_filters(entry, hook_id=actual_hook_id, pr_number=pr_number):
                    continue
                filtered_entries.append(entry)

            if not filtered_entries:
                raise ValueError(f"No data found for hook_id: {hook_id}")

            # Analyze flow stages from log entries
            flow_data = self._analyze_pr_flow(filtered_entries, hook_id)
            return flow_data

        except asyncio.CancelledError:
            self.logger.debug("Operation cancelled")
            raise  # Always re-raise CancelledError
        except ValueError as e:
            if "No data found" in str(e):
                self.logger.warning(f"PR flow data not found: {e}")
                raise HTTPException(status_code=404, detail=str(e)) from e
            self.logger.warning(f"Invalid PR flow hook_id: {e}")
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            self.logger.exception("Error getting PR flow data")
            raise HTTPException(status_code=500, detail="Internal server error") from e

    def _build_log_prefix_from_context(
        self,
        repository: str | None,
        event_type: str | None,
        hook_id: str | None,
        github_user: str | None,
        pr_number: int | None,
    ) -> str:
        """Build log prefix from context variables for structured logging.

        Args:
            repository: Repository name
            event_type: Event type (e.g., 'pull_request', 'check_run')
            hook_id: Hook ID
            github_user: GitHub user
            pr_number: PR number

        Returns:
            Formatted log prefix string

        """
        log_prefix_parts = []
        if repository:
            log_prefix_parts.append(repository)
        if event_type and hook_id:
            log_prefix_parts.append(f"[{event_type}][{hook_id}]")
        if github_user:
            log_prefix_parts.append(f"[{github_user}]")
        if pr_number:
            log_prefix_parts.append(f"[PR {pr_number}]")
        return " ".join(log_prefix_parts) + ": " if log_prefix_parts else ""

    async def get_step_logs(self, hook_id: str, step_name: str) -> dict[str, Any]:
        """Get log entries that occurred during a specific workflow step's execution.

        Args:
            hook_id: The hook ID to get step logs for
            step_name: The name of the workflow step to get logs for

        Returns:
            Dictionary with step metadata and associated log entries:
            - step: The step metadata (name, status, timestamp, duration_ms, error)
            - logs: Array of log entries that occurred during the step
            - log_count: Number of logs found

        Raises:
            HTTPException: 404 if hook_id not found or step_name not found in workflow steps

        """
        # Get workflow steps data
        workflow_data = await self.get_workflow_steps_json(hook_id)

        # Find the step with matching step_name
        steps = workflow_data.get("steps", [])
        matching_step: dict[str, Any] | None = None
        for step in steps:
            if step.get("step_name") == step_name:
                matching_step = step
                break

        if matching_step is None:
            raise HTTPException(
                status_code=404,
                detail=f"Step '{step_name}' not found for hook ID: {hook_id}",
            )

        # Extract step metadata
        step_timestamp = matching_step.get("timestamp")
        step_duration_ms = matching_step.get("duration_ms")
        step_status = matching_step.get("task_status", "unknown")
        step_error = matching_step.get("error")

        # Calculate time window
        duration_ms = step_duration_ms if step_duration_ms is not None else self._DEFAULT_STEP_DURATION_MS

        # Parse timestamps for time window filtering - fail fast if invalid
        if not step_timestamp:
            raise HTTPException(
                status_code=500,
                detail=f"Step '{step_name}' has no timestamp - cannot determine log time window",
            )

        try:
            step_start = datetime.datetime.fromisoformat(step_timestamp.replace("Z", "+00:00"))
            step_end = step_start + datetime.timedelta(milliseconds=duration_ms)
        except (ValueError, TypeError) as ex:
            raise HTTPException(
                status_code=500,
                detail=f"Invalid timestamp '{step_timestamp}' for step '{step_name}': {ex}",
            ) from ex

        # Collect log entries within the time window
        log_entries: list[dict[str, Any]] = []

        async for entry in self._stream_log_entries(max_files=25, max_entries=50000):
            # Filter by hook_id
            if not self._entry_matches_filters(entry, hook_id=hook_id):
                continue

            # Filter by time window
            if entry.timestamp < step_start or entry.timestamp > step_end:
                continue

            log_entries.append(entry.to_dict())
            if len(log_entries) >= self._MAX_STEP_LOGS:
                break

        # Build step metadata for response
        step_metadata = {
            "name": step_name,
            "status": step_status,
            "timestamp": step_timestamp,
            "duration_ms": step_duration_ms,
            "error": step_error,
        }

        return {
            "step": step_metadata,
            "logs": log_entries,
            "log_count": len(log_entries),
        }

    async def get_workflow_steps_json(self, hook_id: str) -> dict[str, Any]:
        """Get workflow steps directly from JSON logs for a specific hook ID.

        This is more efficient than parsing text logs since JSON logs contain
        the full structured workflow data.

        Args:
            hook_id: The hook ID to get workflow steps for

        Returns:
            Dictionary with workflow steps in the format expected by the frontend:
            - hook_id: The hook ID
            - start_time: ISO timestamp of when processing started
            - total_duration_ms: Total processing duration in milliseconds
            - step_count: Number of workflow steps
            - steps: Array of step objects with timestamp, message, level, etc.
            - token_spend: Optional token consumption count
            - event_type: GitHub event type (str, e.g., "pull_request", "check_run")
            - action: Event action (str, e.g., "opened", "synchronize")
            - repository: Repository name (str, owner/repo format)
            - sender: GitHub username who triggered the event (str)
            - pr: Pull request info dict with number, title, etc. (dict or None)
            - success: Whether processing succeeded (bool)
            - error: Error message if processing failed (str or None)

        Raises:
            HTTPException: 404 if hook ID not found

        """
        # Search JSON logs for this hook_id
        async for entry in self._stream_json_log_entries(max_files=25, max_entries=50000):
            if entry.get("hook_id") == hook_id:
                # Found the entry - transform to frontend-expected format
                try:
                    return self._transform_json_entry_to_timeline(entry, hook_id)
                except ValueError:
                    # Malformed log entry - log and return 500
                    self.logger.exception(f"Malformed log entry for hook ID: {hook_id}")
                    raise HTTPException(status_code=500, detail="Malformed log entry") from None

        # Hook ID not found in any log file
        raise HTTPException(status_code=404, detail=f"No JSON log entry found for hook ID: {hook_id}")

    def _transform_json_entry_to_timeline(self, entry: dict[str, Any], hook_id: str) -> dict[str, Any]:
        """Transform JSON log entry to the timeline format expected by the frontend.

        Converts the workflow_steps dict format from JSON logs into the array format
        that matches the output of _build_workflow_timeline().

        Args:
            entry: JSON log entry with workflow_steps dict
            hook_id: The hook ID for this entry

        Returns:
            Dictionary in the format expected by renderFlowModal():
            - hook_id: The webhook delivery ID
            - start_time: ISO timestamp when processing started
            - total_duration_ms: Total processing duration in milliseconds
            - step_count: Number of workflow steps
            - steps: Array of step objects with timestamp, message, level, etc.
            - token_spend: Optional token consumption count
            - event_type: GitHub event type (e.g., "pull_request", "check_run")
            - action: Event action (e.g., "opened", "synchronize")
            - repository: Repository name (owner/repo)
            - sender: GitHub username who triggered the event
            - pr: Pull request info dict with number, title, etc. (or None)
            - success: Boolean indicating if processing succeeded
            - error: Error message if processing failed (or None)

        """
        timing = entry.get("timing")
        workflow_steps = entry.get("workflow_steps")

        # Fail-fast validation: required fields must be present and non-empty
        if not timing or not isinstance(timing, dict):
            raise ValueError(f"Malformed log entry for hook_id {hook_id}: missing or invalid 'timing' field")
        if not workflow_steps or not isinstance(workflow_steps, dict):
            raise ValueError(f"Malformed log entry for hook_id {hook_id}: missing or invalid 'workflow_steps' field")
        if "started_at" not in timing:
            raise ValueError(f"Malformed log entry for hook_id {hook_id}: timing missing 'started_at' field")
        if "duration_ms" not in timing:
            raise ValueError(f"Malformed log entry for hook_id {hook_id}: timing missing 'duration_ms' field")

        repository = entry.get("repository")
        event_type = entry.get("event_type")
        pr_info = entry.get("pr")
        # Validate pr_info type: None is valid (no PR), dict is valid, anything else is malformed
        if pr_info is not None and not isinstance(pr_info, dict):
            raise ValueError(f"Malformed log entry for hook_id {hook_id}: 'pr' field is not a dict")
        pr_number = pr_info.get("number") if pr_info else None

        # Extract timing info
        start_time = timing["started_at"]
        total_duration_ms = timing["duration_ms"]

        # Transform workflow_steps dict to array format
        # Sort by timestamp to maintain execution order
        steps_list = []
        for step_name, step_data in workflow_steps.items():
            # Validate step_data is a dict before accessing its fields
            if not isinstance(step_data, dict):
                raise ValueError(f"Malformed log entry for hook_id {hook_id}: step_data for {step_name} is not a dict")

            step_timestamp = step_data.get("timestamp")
            # Fail fast if timestamp is missing - don't flow bad data to UI
            if not step_timestamp:
                raise ValueError(f"Malformed log entry for hook_id {hook_id}: step '{step_name}' is missing timestamp")
            step_status = step_data.get("status", "unknown")
            step_duration_ms = step_data.get("duration_ms")
            step_error = step_data.get("error")

            # Build message from step name and status
            if step_error:
                error_msg = step_error.get("message", "") if isinstance(step_error, dict) else str(step_error)
                message = f"{step_name}: {step_status} - {error_msg}"
            elif step_duration_ms is not None:
                message = f"{step_name}: {step_status} ({step_duration_ms}ms)"
            else:
                message = f"{step_name}: {step_status}"

            # Determine log level from status
            level = "INFO"
            if step_status == "failed":
                level = "ERROR"
            elif step_status == "started":
                level = "DEBUG"

            steps_list.append({
                "timestamp": step_timestamp,
                "step_name": step_name,
                "message": message,
                "level": level,
                "repository": repository,
                "event_type": event_type,
                "pr_number": pr_number,
                "task_id": step_name,
                "task_type": step_data.get("task_type"),
                "task_status": step_status,
                "duration_ms": step_duration_ms,
                "error": step_error,
                "step_details": step_data,
                "relative_time_ms": 0,  # Will be calculated below
            })

        # Sort steps by timestamp and calculate relative times
        steps_list.sort(key=lambda x: x.get("timestamp") or "")
        if steps_list and start_time:
            # Track current step for error reporting
            current_step: dict[str, Any] | None = None
            current_step_ts: str | None = None
            try:
                base_time = datetime.datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                for step in steps_list:
                    current_step = step
                    current_step_ts = step.get("timestamp")
                    if current_step_ts:
                        step_time = datetime.datetime.fromisoformat(current_step_ts.replace("Z", "+00:00"))
                        step["relative_time_ms"] = int((step_time - base_time).total_seconds() * 1000)
            except (ValueError, TypeError) as ex:
                # Log parse failure for troubleshooting, keep relative_time_ms as 0
                failed_step_name = current_step.get("message", "unknown") if current_step else "unknown"
                failed_timestamp = current_step_ts or start_time
                self.logger.debug(
                    f"Failed to parse timestamp for relative time calculation: {ex}. "
                    f"hook_id={hook_id}, step={failed_step_name}, timestamp={failed_timestamp}",
                )

        return {
            "hook_id": hook_id,
            "start_time": start_time,
            "total_duration_ms": total_duration_ms,
            "step_count": len(steps_list),
            "steps": steps_list,
            "token_spend": entry.get("token_spend"),
            "event_type": event_type,
            "action": entry.get("action"),
            "repository": repository,
            "sender": entry.get("sender"),
            "pr": entry.get("pr"),
            "success": entry.get("success"),
            "error": entry.get("error"),
        }

    async def get_workflow_steps(self, hook_id: str) -> dict[str, Any]:
        """Get workflow step timeline data for a specific hook ID.

        Args:
            hook_id: The hook ID to get workflow steps for

        Returns:
            Dictionary with workflow steps and timing data

        Raises:
            HTTPException: 404 if no steps found for hook ID

        """
        try:
            # First try JSON logs (more efficient and complete)
            try:
                return await self.get_workflow_steps_json(hook_id)
            except HTTPException:
                # Fall back to text log parsing for backward compatibility
                pass
            # Use streaming approach for memory efficiency
            filtered_entries: list[LogEntry] = []

            # Stream entries and filter by hook ID
            # Increase max_files and max_entries to ensure we capture token spend logs
            # Token spend is logged at the end of webhook processing, so we need to read enough entries
            async for entry in self._stream_log_entries(max_files=25, max_entries=50000):
                if not self._entry_matches_filters(entry, hook_id=hook_id):
                    continue
                filtered_entries.append(entry)

            if not filtered_entries:
                raise ValueError(f"No data found for hook ID: {hook_id}")

            # Extract only workflow step entries (logger.step calls)
            workflow_steps = self.log_parser.extract_workflow_steps(filtered_entries, hook_id)

            if not workflow_steps:
                raise ValueError(f"No workflow steps found for hook ID: {hook_id}")

            # Extract token spend from all entries (not just workflow steps)
            # Search in reverse order (newest first) since token spend is logged at the end
            token_spend = None
            entries_with_token_spend = [e for e in filtered_entries if e.token_spend is not None]

            # Extract context from first entry for structured logging (all entries have same hook_id)
            # filtered_entries is guaranteed to be non-empty at this point
            context_entry = filtered_entries[0]
            repository = context_entry.repository
            event_type = context_entry.event_type
            github_user = context_entry.github_user
            pr_number = context_entry.pr_number

            if entries_with_token_spend:
                # Take the most recent token spend entry (should be only one per webhook, but take latest to be safe)
                token_spend = entries_with_token_spend[-1].token_spend
                # Format log message using prepare_log_prefix format so it's parseable and clickable
                log_prefix = self._build_log_prefix_from_context(
                    repository,
                    event_type,
                    hook_id,
                    github_user,
                    pr_number,
                )
                self.logger.info(
                    f"{log_prefix}Found token spend {token_spend} for hook {hook_id} "
                    f"from {len(entries_with_token_spend)} entries",
                )
            else:
                # Check if any entries contain "token" or "API calls" in message (for debugging)
                entries_with_token_keywords = [
                    e for e in filtered_entries if "token" in e.message.lower() or "API calls" in e.message
                ]
                if entries_with_token_keywords:
                    # Format log message using prepare_log_prefix format
                    log_prefix = self._build_log_prefix_from_context(
                        repository,
                        event_type,
                        hook_id,
                        github_user,
                        pr_number,
                    )
                    self.logger.warning(
                        f"{log_prefix}Found {len(entries_with_token_keywords)} entries with token keywords "
                        f"for hook {hook_id}, but token_spend is None. "
                        f"Sample: {entries_with_token_keywords[0].message[:150]}",
                    )
                    # Try to extract token spend directly from the message as fallback
                    for entry in reversed(entries_with_token_keywords):
                        extracted = self.log_parser.extract_token_spend(entry.message)
                        if extracted is not None:
                            token_spend = extracted
                            # Format log message using prepare_log_prefix format
                            log_prefix = self._build_log_prefix_from_context(
                                repository,
                                event_type,
                                hook_id,
                                github_user,
                                pr_number,
                            )
                            self.logger.info(
                                f"{log_prefix}Extracted token spend {token_spend} directly from message "
                                f"for hook {hook_id}",
                            )
                            break

            # Build timeline data
            timeline_data = self._build_workflow_timeline(workflow_steps, hook_id)
            if token_spend is not None:
                timeline_data["token_spend"] = token_spend
            return timeline_data

        except asyncio.CancelledError:
            self.logger.debug("Operation cancelled")
            raise  # Always re-raise CancelledError
        except ValueError as e:
            if "No data found" in str(e) or "No workflow steps found" in str(e):
                self.logger.warning(f"Workflow steps not found: {e}")
                raise HTTPException(status_code=404, detail=str(e)) from e
            self.logger.warning(f"Invalid hook ID: {e}")
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            self.logger.exception("Error getting workflow steps")
            raise HTTPException(status_code=500, detail="Internal server error") from e

    def _build_workflow_timeline(self, workflow_steps: list[LogEntry], hook_id: str) -> dict[str, Any]:
        """Build timeline data from workflow step entries.

        Args:
            workflow_steps: List of workflow step log entries
            hook_id: The hook ID for this timeline

        Returns:
            Dictionary with timeline data structure including task correlation fields

        """
        # Sort steps by timestamp
        sorted_steps = sorted(workflow_steps, key=lambda x: x.timestamp)

        # Extract timeline data
        timeline_steps = []
        start_time = sorted_steps[0].timestamp if sorted_steps else None

        for step in sorted_steps:
            # Calculate relative time from start
            relative_time = 0
            if start_time:
                relative_time = int((step.timestamp - start_time).total_seconds() * 1000)  # milliseconds

            timeline_steps.append({
                "timestamp": step.timestamp.isoformat(),
                "relative_time_ms": relative_time,
                "message": step.message,
                "level": step.level,
                "repository": step.repository,
                "event_type": step.event_type,
                "pr_number": step.pr_number,
                "task_id": step.task_id,
                "task_type": step.task_type,
                "task_status": step.task_status,
            })

        # Calculate total duration
        total_duration_ms = 0
        if len(sorted_steps) > 1:
            total_duration_ms = int((sorted_steps[-1].timestamp - sorted_steps[0].timestamp).total_seconds() * 1000)

        return {
            "hook_id": hook_id,
            "start_time": start_time.isoformat() if start_time else None,
            "total_duration_ms": total_duration_ms,
            "step_count": len(timeline_steps),
            "steps": timeline_steps,
        }

    async def _stream_log_entries(
        self,
        max_files: int = 10,
        _chunk_size: int = 1000,
        max_entries: int = 50000,
    ) -> AsyncGenerator[LogEntry]:
        """Stream log entries from configured log files in chunks to reduce memory usage.

        This replaces _load_log_entries() to prevent memory exhaustion from loading
        all log files simultaneously. Uses lazy evaluation and chunked processing.

        Supports both text log files (*.log) and JSONL log files (webhooks_*.json).

        Args:
            max_files: Maximum number of log files to process (newest first)
            _chunk_size: Number of entries to yield per chunk from each file (unused, reserved for future)
            max_entries: Maximum total entries to yield (safety limit)

        Yields:
            LogEntry objects in timestamp order (newest first)

        """
        log_dir = self._get_log_directory()

        if not log_dir.exists():
            self.logger.warning(f"Log directory not found: {log_dir}")
            return

        # Find all log files including rotated ones and JSON files
        log_files: list[Path] = []
        log_files.extend(log_dir.glob("*.log"))
        log_files.extend(log_dir.glob("*.log.*"))
        log_files.extend(log_dir.glob("webhooks_*.json"))

        # Sort log files to prioritize JSON webhook files first (primary data source),
        # then other files by modification time (newest first)
        # This ensures webhook data is displayed before internal log files
        def sort_key(f: Path) -> tuple[int, float]:
            is_json_webhook = f.suffix == ".json" and f.name.startswith("webhooks_")
            # JSON webhook files: (0, -mtime) - highest priority, newest first
            # Other files: (1, -mtime) - lower priority, newest first
            return (0 if is_json_webhook else 1, -f.stat().st_mtime)

        log_files.sort(key=sort_key)
        log_files = log_files[:max_files]

        self.logger.info(f"Streaming from {len(log_files)} most recent files: {[f.name for f in log_files]}")

        total_yielded = 0

        # Stream from newest files first
        for log_file in log_files:
            if total_yielded >= max_entries:
                break

            try:
                remaining_capacity = max_entries - total_yielded
                if remaining_capacity <= 0:
                    break

                buffer: deque[LogEntry] = deque(maxlen=remaining_capacity)

                async with aiofiles.open(log_file, encoding="utf-8") as f:
                    # Use appropriate parser based on file type
                    if log_file.suffix == ".json":
                        # JSONL files: one compact JSON object per line
                        async for line in f:
                            entry = self.log_parser.parse_json_log_entry(line)
                            if entry:
                                buffer.append(entry)
                    else:
                        # Text log files: parse line by line
                        async for line in f:
                            entry = self.log_parser.parse_log_entry(line)
                            if entry:
                                buffer.append(entry)

                for entry in reversed(buffer):
                    if total_yielded >= max_entries:
                        break
                    yield entry
                    total_yielded += 1

                self.logger.debug(f"Streamed entries from {log_file.name}, total so far: {total_yielded}")

            except asyncio.CancelledError:
                self.logger.debug("Operation cancelled")
                raise  # Always re-raise CancelledError
            except Exception as e:
                self.logger.warning(f"Error streaming log file {log_file}: {e}")

    async def _stream_json_log_entries(
        self,
        max_files: int = 10,
        max_entries: int = 50000,
    ) -> AsyncGenerator[dict[str, Any]]:
        """Stream raw JSON log entries from webhooks_*.json files.

        Returns raw JSON dicts instead of LogEntry objects for access to full structured data.
        Reads JSONL format (one JSON object per line).

        Args:
            max_files: Maximum number of log files to process (newest first)
            max_entries: Maximum total entries to yield (safety limit)

        Yields:
            Raw JSON dictionaries from log files (newest first)

        """
        log_dir = self._get_log_directory()

        if not log_dir.exists():
            return

        # Find JSON log files
        json_files = list(log_dir.glob("webhooks_*.json"))
        # Sort by modification time (newest first)
        json_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        json_files = json_files[:max_files]

        total_yielded = 0

        for log_file in json_files:
            if total_yielded >= max_entries:
                break

            try:
                # Stream JSONL entries incrementally without loading entire file
                remaining = max_entries - total_yielded
                line_buffer: deque[str] = deque(maxlen=remaining)

                async with aiofiles.open(log_file, encoding="utf-8") as f:
                    # JSONL format: one JSON object per line
                    async for line in f:
                        line_buffer.append(line.rstrip("\n"))

                # Process lines in reverse order (newest first)
                for line in reversed(line_buffer):
                    if total_yielded >= max_entries:
                        break

                    data = self.log_parser.get_raw_json_entry(line)
                    if data:
                        yield data
                        total_yielded += 1
            except asyncio.CancelledError:
                self.logger.debug("Operation cancelled")
                raise  # Always re-raise CancelledError
            except Exception as e:
                self.logger.warning(f"Error streaming JSON log file {log_file}: {e}")

    async def _load_log_entries(self) -> list[LogEntry]:
        """Load log entries using streaming approach for memory efficiency.

        This method now uses the streaming approach internally but returns a list
        for backward compatibility. For new code, prefer _stream_log_entries().

        Returns:
            List of parsed log entries (limited to prevent memory exhaustion)

        """
        # Use streaming with reasonable limits to prevent memory issues
        entries = [entry async for entry in self._stream_log_entries(max_files=10, max_entries=10000)]
        self.logger.info(f"Loaded {len(entries)} entries using streaming approach")
        return entries

    def _get_log_directory(self) -> Path:
        """Get the log directory path from configuration.

        Returns:
            Path to log directory

        """
        # Use the same log directory as the main application
        log_dir_path = os.path.join(self.config.data_dir, "logs")
        return Path(log_dir_path)

    async def _get_log_viewer_html(self) -> str:
        """Load and return the log viewer HTML template.

        Returns:
            HTML content for log viewer interface

        Raises:
            FileNotFoundError: If template file cannot be found
            IOError: If template file cannot be read

        """
        template_path = Path(__file__).parent / "templates" / "log_viewer.html"

        try:
            async with aiofiles.open(template_path, encoding="utf-8") as f:
                return await f.read()
        except FileNotFoundError:
            self.logger.exception(f"Log viewer template not found at {template_path}")
            return self._get_fallback_html()
        except OSError:
            self.logger.exception("Failed to read log viewer template")
            return self._get_fallback_html()

    def _get_fallback_html(self) -> str:
        """Provide a minimal fallback HTML when template loading fails.

        Returns:
            Basic HTML page with error message

        """
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GitHub Webhook Server - Log Viewer (Error)</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }
        .error-container {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            text-align: center;
        }
        .error-icon {
            font-size: 48px;
            color: #dc3545;
            margin-bottom: 20px;
        }
        .retry-btn {
            background: #007bff;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 4px;
            cursor: pointer;
            margin-top: 20px;
        }
    </style>
</head>
<body>
    <div class="error-container">
        <div class="error-icon">⚠️</div>
        <h1>Log Viewer Template Error</h1>
        <p>The log viewer template could not be loaded. Please check the server logs for details.</p>
        <button class="retry-btn" onclick="window.location.reload()">Refresh Page</button>
    </div>
</body>
</html>"""

    def _generate_json_export(self, entries: list[LogEntry], filters: dict[str, Any] | None = None) -> str:
        """Generate JSON export content from log entries.

        Args:
            entries: List of log entries to export
            filters: Dictionary of filters applied to the export

        Returns:
            JSON content as string

        """
        export_data = {
            "export_metadata": {
                "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "filters_applied": filters or {},
                "total_entries": len(entries),
                "export_format": "json",
            },
            "log_entries": [entry.to_dict() for entry in entries],
        }
        return json.dumps(export_data, indent=2)

    def _analyze_pr_flow(self, entries: list[LogEntry], hook_id: str) -> dict[str, Any]:
        """Analyze PR workflow stages from log entries.

        Args:
            entries: List of log entries for the PR/hook
            hook_id: Original hook_id used for the request

        Returns:
            Dictionary with flow stages and timing data

        """
        # Sort entries by timestamp
        sorted_entries = sorted(entries, key=lambda x: x.timestamp)

        if not sorted_entries:
            return {
                "identifier": hook_id,
                "stages": [],
                "total_duration_ms": 0,
                "success": False,
                "error": "No log entries found",
            }

        stages = []
        start_time = sorted_entries[0].timestamp
        success = True
        error_message = None

        # Use class-level workflow stage patterns for analysis
        stage_patterns = self.WORKFLOW_STAGE_PATTERNS

        previous_time = start_time
        for pattern_name, pattern in stage_patterns:
            # Find first entry matching this stage
            for entry in sorted_entries:
                if any(re.search(p, entry.message, re.IGNORECASE) for p in pattern.split("|")):
                    duration_ms = int((entry.timestamp - previous_time).total_seconds() * 1000)

                    stage = {
                        "name": pattern_name,
                        "timestamp": entry.timestamp.isoformat(),
                        "duration_ms": duration_ms if entry.timestamp != start_time else None,
                    }

                    # Check for errors in this stage
                    if entry.level == "ERROR":
                        stage["error"] = entry.message
                        success = False
                        error_message = entry.message

                    stages.append(stage)
                    previous_time = entry.timestamp
                    break

        # Check for any error entries
        error_entries = [e for e in sorted_entries if e.level == "ERROR"]
        if error_entries and success:
            success = False
            error_message = error_entries[0].message

        total_duration = int((sorted_entries[-1].timestamp - start_time).total_seconds() * 1000)

        flow_data = {
            "identifier": hook_id,
            "stages": stages,
            "total_duration_ms": total_duration,
            "success": success,
        }

        if error_message:
            flow_data["error"] = error_message

        return flow_data

    def _estimate_total_log_count(self) -> str:
        """Estimate total log count across all available log files.

        Returns:
            String representing estimated total log count

        """
        try:
            log_dir = self._get_log_directory()
            if not log_dir.exists():
                return "0"

            # Find all log files including rotated ones
            log_files: list[Path] = []
            log_files.extend(log_dir.glob("*.log"))
            log_files.extend(log_dir.glob("*.log.*"))

            if not log_files:
                return "0"

            # Quick estimation based on file sizes
            total_estimate = 0
            for log_file in log_files[:10]:  # Sample first 10 files to avoid performance impact
                try:
                    # Estimate based on file size (faster than counting lines)
                    file_size = log_file.stat().st_size
                    # Rough estimate: average log line is ~200 bytes
                    estimated_lines = file_size // 200
                    total_estimate += estimated_lines
                except (OSError, PermissionError) as ex:
                    self.logger.debug(f"Failed to stat log file {log_file}: {ex}")
                    continue

            # If we processed fewer than all files, extrapolate
            if len(log_files) > 10:
                extrapolation_factor = len(log_files) / 10
                total_estimate = int(total_estimate * extrapolation_factor)

            # Return formatted string
            if total_estimate > 1000000:
                return f"{total_estimate / 1000000:.1f}M"
            if total_estimate > 1000:
                return f"{total_estimate / 1000:.1f}K"
            return str(total_estimate)

        except Exception as e:
            self.logger.warning(f"Error estimating total log count: {e}")
            return "Unknown"
