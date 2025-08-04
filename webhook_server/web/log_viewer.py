"""Log viewer controller for serving log viewer web interface and API endpoints."""

import datetime
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Generator, Iterator

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse

from webhook_server.libs.config import Config
from webhook_server.libs.log_parser import LogEntry, LogFilter, LogParser


class LogViewerController:
    """Controller for log viewer functionality."""

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
            f"Shutting down LogViewerController with {len(self._websocket_connections)} active connections"
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

    def get_log_page(self) -> HTMLResponse:
        """Serve the main log viewer HTML page.

        Returns:
            HTML response with log viewer interface

        Raises:
            HTTPException: 404 if template not found, 500 for other errors
        """
        try:
            html_content = self._get_log_viewer_html()
            return HTMLResponse(content=html_content)
        except FileNotFoundError:
            self.logger.error("Log viewer HTML template not found")
            raise HTTPException(status_code=404, detail="Log viewer template not found")
        except Exception as e:
            self.logger.error(f"Error serving log viewer page: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")

    def get_log_entries(
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

            for entry in self._stream_log_entries(max_files=25, max_entries=max_entries_to_process):
                total_processed += 1

                # Apply filters early to reduce memory usage
                if not self._entry_matches_filters(
                    entry, hook_id, pr_number, repository, event_type, github_user, level, start_time, end_time, search
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

        except ValueError as e:
            self.logger.warning(f"Invalid parameters for log entries request: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except (OSError, PermissionError) as e:
            self.logger.error(f"File access error loading log entries: {e}")
            raise HTTPException(status_code=500, detail="Error accessing log files")
        except Exception as e:
            self.logger.error(f"Unexpected error getting log entries: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")

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

    def export_logs(
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
            max_entries_to_process = min(limit + 20000, 50000) if has_filters else limit + 1000

            for entry in self._stream_log_entries(max_files=25, max_entries=max_entries_to_process):
                if not self._entry_matches_filters(
                    entry, hook_id, pr_number, repository, event_type, github_user, level, start_time, end_time, search
                ):
                    continue

                filtered_entries.append(entry)

                # Stop when we reach the export limit
                if len(filtered_entries) >= limit:
                    break

            # Generate JSON export content
            content = self._generate_json_export(filtered_entries)
            media_type = "application/json"
            filename = f"webhook_logs_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

            def generate() -> Generator[bytes, None, None]:
                yield content.encode("utf-8")

            return StreamingResponse(
                generate(),
                media_type=media_type,
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )

        except ValueError as e:
            if "Result set too large" in str(e):
                self.logger.warning(f"Export request too large: {e}")
                raise HTTPException(status_code=413, detail=str(e))
            else:
                self.logger.warning(f"Invalid export parameters: {e}")
                raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            self.logger.error(f"Error generating export: {e}")
            raise HTTPException(status_code=500, detail="Export generation failed")

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
        except Exception as e:
            self.logger.error(f"Error in WebSocket handler: {e}")
            try:
                await websocket.close(code=1011, reason="Internal server error")
            except Exception:
                pass
        finally:
            self._websocket_connections.discard(websocket)

    def get_pr_flow_data(self, hook_id: str) -> dict[str, Any]:
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
            for entry in self._stream_log_entries(max_files=15, max_entries=10000):
                if not self._entry_matches_filters(entry, hook_id=actual_hook_id, pr_number=pr_number):
                    continue
                filtered_entries.append(entry)

            if not filtered_entries:
                raise ValueError(f"No data found for hook_id: {hook_id}")

            # Analyze flow stages from log entries
            flow_data = self._analyze_pr_flow(filtered_entries, hook_id)
            return flow_data

        except ValueError as e:
            if "No data found" in str(e):
                self.logger.warning(f"PR flow data not found: {e}")
                raise HTTPException(status_code=404, detail=str(e))
            else:
                self.logger.warning(f"Invalid PR flow hook_id: {e}")
                raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            self.logger.error(f"Error getting PR flow data: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")

    def get_workflow_steps(self, hook_id: str) -> dict[str, Any]:
        """Get workflow step timeline data for a specific hook ID.

        Args:
            hook_id: The hook ID to get workflow steps for

        Returns:
            Dictionary with workflow steps and timing data

        Raises:
            HTTPException: 404 if no steps found for hook ID
        """
        try:
            # Use streaming approach for memory efficiency
            filtered_entries: list[LogEntry] = []

            # Stream entries and filter by hook ID
            for entry in self._stream_log_entries(max_files=15, max_entries=10000):
                if not self._entry_matches_filters(entry, hook_id=hook_id):
                    continue
                filtered_entries.append(entry)

            if not filtered_entries:
                raise ValueError(f"No data found for hook ID: {hook_id}")

            # Extract only workflow step entries (logger.step calls)
            workflow_steps = self.log_parser.extract_workflow_steps(filtered_entries, hook_id)

            if not workflow_steps:
                raise ValueError(f"No workflow steps found for hook ID: {hook_id}")

            # Build timeline data
            timeline_data = self._build_workflow_timeline(workflow_steps, hook_id)
            return timeline_data

        except ValueError as e:
            if "No data found" in str(e) or "No workflow steps found" in str(e):
                self.logger.warning(f"Workflow steps not found: {e}")
                raise HTTPException(status_code=404, detail=str(e))
            else:
                self.logger.warning(f"Invalid hook ID: {e}")
                raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            self.logger.error(f"Error getting workflow steps: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")

    def _build_workflow_timeline(self, workflow_steps: list[LogEntry], hook_id: str) -> dict[str, Any]:
        """Build timeline data from workflow step entries.

        Args:
            workflow_steps: List of workflow step log entries
            hook_id: The hook ID for this timeline

        Returns:
            Dictionary with timeline data structure
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

    def _stream_log_entries(
        self, max_files: int = 10, chunk_size: int = 1000, max_entries: int = 50000
    ) -> Iterator[LogEntry]:
        """Stream log entries from configured log files in chunks to reduce memory usage.

        This replaces _load_log_entries() to prevent memory exhaustion from loading
        all log files simultaneously. Uses lazy evaluation and chunked processing.

        Args:
            max_files: Maximum number of log files to process (newest first)
            chunk_size: Number of entries to yield per chunk from each file
            max_entries: Maximum total entries to yield (safety limit)

        Yields:
            LogEntry objects in timestamp order (newest first)
        """
        log_dir = self._get_log_directory()

        if not log_dir.exists():
            self.logger.warning(f"Log directory not found: {log_dir}")
            return

        # Find all log files including rotated ones (*.log, *.log.1, *.log.2, etc.)
        log_files: list[Path] = []
        log_files.extend(log_dir.glob("*.log"))
        log_files.extend(log_dir.glob("*.log.*"))

        # Sort log files to process in correct order (current log first, then rotated by number)
        def sort_key(f: Path) -> tuple:
            name_parts = f.name.split(".")
            if len(name_parts) > 2 and name_parts[-1].isdigit():
                # Rotated file: extract rotation number
                return (1, int(name_parts[-1]))
            else:
                # Current log file
                return (0, 0)

        log_files.sort(key=sort_key)
        log_files = log_files[:max_files]

        self.logger.info(f"Streaming from {len(log_files)} most recent files: {[f.name for f in log_files]}")

        total_yielded = 0

        # Stream from newest files first
        for log_file in log_files:
            if total_yielded >= max_entries:
                break

            try:
                file_entries: list[LogEntry] = []

                # Parse file in one go (files are typically reasonable size individually)
                with open(log_file, "r", encoding="utf-8") as f:
                    for line_num, line in enumerate(f, 1):
                        if total_yielded >= max_entries:
                            break

                        entry = self.log_parser.parse_log_entry(line)
                        if entry:
                            file_entries.append(entry)

                        # Process in chunks to avoid memory buildup for large files
                        if len(file_entries) >= chunk_size:
                            # Sort chunk by timestamp (newest first) and yield
                            file_entries.sort(key=lambda x: x.timestamp, reverse=True)
                            for entry in file_entries:
                                yield entry
                                total_yielded += 1
                                if total_yielded >= max_entries:
                                    break
                            file_entries.clear()  # Free memory

                # Yield remaining entries from this file
                if file_entries and total_yielded < max_entries:
                    file_entries.sort(key=lambda x: x.timestamp, reverse=True)
                    for entry in file_entries:
                        if total_yielded >= max_entries:
                            break
                        yield entry
                        total_yielded += 1

                self.logger.debug(f"Streamed entries from {log_file.name}, total so far: {total_yielded}")

            except Exception as e:
                self.logger.warning(f"Error streaming log file {log_file}: {e}")

    def _load_log_entries(self) -> list[LogEntry]:
        """Load log entries using streaming approach for memory efficiency.

        This method now uses the streaming approach internally but returns a list
        for backward compatibility. For new code, prefer _stream_log_entries().

        Returns:
            List of parsed log entries (limited to prevent memory exhaustion)
        """
        # Use streaming with reasonable limits to prevent memory issues
        entries = list(self._stream_log_entries(max_files=10, max_entries=10000))
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

    def _get_log_viewer_html(self) -> str:
        """Load and return the log viewer HTML template.

        Returns:
            HTML content for log viewer interface

        Raises:
            FileNotFoundError: If template file cannot be found
            IOError: If template file cannot be read
        """
        template_path = Path(__file__).parent / "templates" / "log_viewer.html"

        try:
            with open(template_path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            self.logger.error(f"Log viewer template not found at {template_path}")
            return self._get_fallback_html()
        except IOError as e:
            self.logger.error(f"Failed to read log viewer template: {e}")
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

    def _generate_json_export(self, entries: list[LogEntry]) -> str:
        """Generate JSON export content from log entries.

        Args:
            entries: List of log entries to export

        Returns:
            JSON content as string
        """
        return json.dumps([entry.to_dict() for entry in entries], indent=2)

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

            # Quick estimation based on file sizes and line counts from a sample
            total_estimate = 0
            for log_file in log_files[:10]:  # Sample first 10 files to avoid performance impact
                try:
                    # Quick line count estimation
                    with open(log_file, "rb") as f:
                        line_count = sum(1 for _ in f)
                    total_estimate += line_count
                except Exception:
                    # If we can't read a file, estimate based on file size
                    try:
                        file_size = log_file.stat().st_size
                        # Rough estimate: average log line is ~200 bytes
                        estimated_lines = file_size // 200
                        total_estimate += estimated_lines
                    except Exception:
                        continue

            # If we processed fewer than all files, extrapolate
            if len(log_files) > 10:
                extrapolation_factor = len(log_files) / 10
                total_estimate = int(total_estimate * extrapolation_factor)

            # Return formatted string
            if total_estimate > 1000000:
                return f"{total_estimate // 1000000:.1f}M"
            elif total_estimate > 1000:
                return f"{total_estimate // 1000:.1f}K"
            else:
                return str(total_estimate)

        except Exception as e:
            self.logger.warning(f"Error estimating total log count: {e}")
            return "Unknown"
