"""Log viewer controller for serving log viewer web interface and API endpoints."""

import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any, Generator, Iterator

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse

from webhook_server.libs.config import Config
from webhook_server.libs.log_parser import LogEntry, LogFilter, LogParser


class LogViewerController:
    """Controller for log viewer functionality."""

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
            Dictionary with entries, total count, and pagination info

        Raises:
            HTTPException: 400 for invalid parameters, 500 for file access errors
        """
        try:
            # Validate parameters
            if limit < 1 or limit > 1000:
                raise ValueError("Limit must be between 1 and 1000")
            if offset < 0:
                raise ValueError("Offset must be non-negative")

            # Use memory-efficient streaming with filtering applied during iteration
            filtered_entries: list[LogEntry] = []
            total_processed = 0
            skipped = 0

            # Stream entries and apply filters incrementally
            for entry in self._stream_log_entries(max_files=15, max_entries=20000):
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
            if total_processed >= 20000:  # Hit our streaming limit
                estimated_total = f"{total_processed}+"  # Indicate there are more

            return {
                "entries": [entry.to_dict() for entry in filtered_entries],
                "total": estimated_total,  # Estimated total in system
                "filtered_total": len(filtered_entries) + offset,  # Filtered count (minimum)
                "limit": limit,
                "offset": offset,
                "is_estimate": total_processed >= 20000,  # Flag for UI to show estimation
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
        if event_type is not None and (not entry.event_type or event_type not in entry.event_type):
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
            for entry in self._stream_log_entries(max_files=20, max_entries=limit + 1000):
                if not self._entry_matches_filters(
                    entry, hook_id, pr_number, repository, event_type, github_user, level, start_time, end_time, search
                ):
                    continue

                filtered_entries.append(entry)

                # Stop when we reach the export limit
                if len(filtered_entries) >= limit:
                    break

            if len(filtered_entries) > 50000:
                raise ValueError("Result set too large")

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
                # Apply filters to new entry - if no filters provided, send all entries
                if not any([hook_id, pr_number, repository, event_type, github_user, level]):
                    # No filters, send everything
                    try:
                        await websocket.send_json(entry.to_dict())
                    except WebSocketDisconnect:
                        break
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

                    # Send entry if it passes filters
                    if filtered_entries:
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

        # Sort log files by recency (newest first) and limit for memory efficiency
        log_files.sort(key=lambda f: (f.name.count("."), f.stat().st_mtime), reverse=True)
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
        """Generate the log viewer HTML template.

        Returns:
            HTML content for log viewer interface
        """
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GitHub Webhook Server - Log Viewer</title>
    <style>
        :root {
            /* Light theme variables */
            --bg-color: #f5f5f5;
            --container-bg: #ffffff;
            --text-color: #333333;
            --border-color: #dddddd;
            --input-bg: #ffffff;
            --input-border: #dddddd;
            --button-bg: #007bff;
            --button-hover: #0056b3;
            --status-connected-bg: #d4edda;
            --status-connected-text: #155724;
            --status-connected-border: #c3e6cb;
            --status-disconnected-bg: #f8d7da;
            --status-disconnected-text: #721c24;
            --status-disconnected-border: #f5c6cb;
            --log-entry-border: #eeeeee;
            --log-info-bg: #d4f8d4;
            --log-error-bg: #ffd6d6;
            --log-warning-bg: #fff3cd;
            --log-debug-bg: #f8f9fa;
            --log-step-bg: #e3f2fd;
            --tag-bg: #e9ecef;
            --timestamp-color: #666666;
        }

        [data-theme="dark"] {
            /* Dark theme variables */
            --bg-color: #1a1a1a;
            --container-bg: #2d2d2d;
            --text-color: #e0e0e0;
            --border-color: #404040;
            --input-bg: #3d3d3d;
            --input-border: #555555;
            --button-bg: #0d6efd;
            --button-hover: #0b5ed7;
            --status-connected-bg: #155724;
            --status-connected-text: #d4edda;
            --status-connected-border: #c3e6cb;
            --status-disconnected-bg: #721c24;
            --status-disconnected-text: #f8d7da;
            --status-disconnected-border: #f5c6cb;
            --log-entry-border: #404040;
            --log-info-bg: #1e4a1e;
            --log-error-bg: #5a1e1e;
            --log-warning-bg: #5a4a1e;
            --log-debug-bg: #2a2a2a;
            --log-step-bg: #1a237e;
            --tag-bg: #4a4a4a;
            --timestamp-color: #888888;
        }

        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: var(--bg-color);
            color: var(--text-color);
            transition: background-color 0.3s ease, color 0.3s ease;
        }
        .container {
            max-width: 95vw;
            margin: 0 auto;
            background: var(--container-bg);
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            transition: background-color 0.3s ease;
        }
        .header {
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 20px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 { margin: 0; }
        .theme-toggle {
            background: var(--button-bg);
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            cursor: pointer;
            transition: background-color 0.3s ease;
        }
        .theme-toggle:hover { background: var(--button-hover); }
        .filters { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-bottom: 20px; }
        .filter-group { display: flex; flex-direction: column; }
        .filter-group label { font-weight: bold; margin-bottom: 3px; font-size: 14px; color: var(--text-color); }
        .filter-group input, .filter-group select {
            padding: 8px;
            border: 1px solid var(--input-border);
            border-radius: 4px;
            background: var(--input-bg);
            color: var(--text-color);
            transition: background-color 0.3s ease, border-color 0.3s ease;
        }
        .log-entries { border: 1px solid var(--border-color); border-radius: 4px; max-height: 70vh; overflow-y: auto; }

        /* Timeline styles */
        .timeline-section {
            margin: 20px 0;
            padding: 15px;
            background: var(--container-bg);
            border: 1px solid var(--border-color);
            border-radius: 4px;
            display: none; /* Hidden by default */
        }

        .timeline-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 1px solid var(--border-color);
        }

        .timeline-info {
            font-size: 14px;
            color: var(--timestamp-color);
        }

        .timeline-container {
            position: relative;
            overflow-x: auto;
            padding: 20px 0;
            min-height: 120px;
        }

        .timeline-svg {
            width: 100%;
            min-width: 800px;
            height: 120px;
        }

        .timeline-step {
            cursor: pointer;
        }

        .timeline-step:hover .step-circle {
            r: 8;
            stroke-width: 3;
        }

        .timeline-step:hover .step-label {
            font-weight: bold;
        }

        .step-line {
            stroke: var(--border-color);
            stroke-width: 2;
        }

        .step-circle {
            r: 8;
            stroke: #ffffff;
            stroke-width: 2;
            transition: all 0.2s ease;
            cursor: pointer;
        }

        .step-circle:hover {
            r: 10;
        }

        .step-circle.success {
            fill: #28a745;
            stroke: #ffffff;
        }

        .step-circle.failure {
            fill: #dc3545;
            stroke: #ffffff;
        }

        .step-circle.info {
            fill: #17a2b8;
            stroke: #ffffff;
        }

        .step-circle.progress {
            fill: #007bff;
            stroke: #ffffff;
        }

        .step-label {
            font-size: 12px;
            text-anchor: middle;
            fill: var(--text-color);
            transition: font-weight 0.2s ease;
        }

        .step-time {
            font-size: 10px;
            text-anchor: middle;
            fill: var(--timestamp-color);
        }

        .timeline-tooltip {
            position: absolute;
            background: var(--container-bg);
            border: 1px solid var(--border-color);
            border-radius: 4px;
            padding: 8px;
            font-size: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            z-index: 1000;
            pointer-events: none;
            display: none;
            max-width: 300px;
        }
        .log-entry {
            padding: 10px;
            border-bottom: 1px solid var(--log-entry-border);
            font-family: monospace;
            font-size: 14px;
            transition: background-color 0.3s ease;
        }
        .log-entry:last-child { border-bottom: none; }
        .log-entry.INFO { background-color: var(--log-info-bg); }
        .log-entry.ERROR { background-color: var(--log-error-bg); }
        .log-entry.WARNING { background-color: var(--log-warning-bg); }
        .log-entry.DEBUG { background-color: var(--log-debug-bg); }
        .log-entry.STEP { background-color: var(--log-step-bg); }
        .timestamp { color: var(--timestamp-color); }
        .level { font-weight: bold; }
        .message { margin-left: 10px; }
        .hook-id, .pr-number, .repository, .user {
            margin-left: 10px;
            padding: 2px 6px;
            background-color: var(--tag-bg);
            border-radius: 3px;
            font-size: 12px;
            transition: background-color 0.3s ease;
        }
        .controls { margin-bottom: 20px; }
        .btn {
            padding: 10px 20px;
            background-color: var(--button-bg);
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            margin-right: 10px;
            transition: background-color 0.3s ease;
        }
        .btn:hover { background-color: var(--button-hover); }
        .status { padding: 10px; margin-bottom: 20px; border-radius: 4px; }
        .status.connected {
            background-color: var(--status-connected-bg);
            color: var(--status-connected-text);
            border: 1px solid var(--status-connected-border);
        }
        .status.disconnected {
            background-color: var(--status-disconnected-bg);
            color: var(--status-disconnected-text);
            border: 1px solid var(--status-disconnected-border);
        }

        /* Responsive adjustments */
        @media (max-width: 768px) {
            .filters { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; }
            .filter-group label { font-size: 13px; }
            .filter-group input, .filter-group select { padding: 6px; font-size: 14px; }
            .controls { display: flex; flex-wrap: wrap; gap: 8px; }
            .btn { padding: 8px 16px; font-size: 14px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>GitHub Webhook Server - Log Viewer</h1>
                <p>Real-time log monitoring and filtering for webhook events</p>
            </div>
            <button class="theme-toggle" onclick="toggleTheme()" title="Toggle dark/light theme">
                🌙
            </button>
        </div>

        <div class="status" id="connectionStatus">
            <span id="statusText">Connecting...</span>
        </div>

        <div class="controls">
            <button class="btn" onclick="connectWebSocket()">Start Real-time</button>
            <button class="btn" onclick="disconnectWebSocket()">Stop Real-time</button>
            <button class="btn" onclick="loadHistoricalLogs()">Refresh</button>
            <button class="btn" onclick="clearFilters()">Clear Filters</button>
            <button class="btn" onclick="clearLogs()">Clear Logs</button>
            <button class="btn" onclick="exportLogs('json')">Export JSON</button>
        </div>

        <div class="filters">
            <div class="filter-group">
                <label for="hookIdFilter">Hook ID:</label>
                <input type="text" id="hookIdFilter" placeholder="delivery-id" title="x-github-delivery value">
            </div>
            <div class="filter-group">
                <label for="prNumberFilter">PR #:</label>
                <input type="number" id="prNumberFilter" placeholder="123">
            </div>
            <div class="filter-group">
                <label for="repositoryFilter">Repository:</label>
                <input type="text" id="repositoryFilter" placeholder="org/repo">
            </div>
            <div class="filter-group">
                <label for="userFilter">User:</label>
                <input type="text" id="userFilter" placeholder="username">
            </div>
            <div class="filter-group">
                <label for="levelFilter">Level:</label>
                <select id="levelFilter">
                    <option value="">All</option>
                    <option value="DEBUG">DEBUG</option>
                    <option value="INFO">INFO</option>
                    <option value="WARNING">WARNING</option>
                    <option value="ERROR">ERROR</option>
                </select>
            </div>
            <div class="filter-group">
                <label for="searchFilter">Search:</label>
                <input type="text" id="searchFilter" placeholder="text in messages...">
            </div>
        </div>

        <!-- Hook ID Flow Timeline -->
        <div class="timeline-section" id="timelineSection">
            <div class="timeline-header">
                <h3>Hook ID Flow Timeline</h3>
                <div class="timeline-info" id="timelineInfo">
                    <!-- Timeline metadata will be populated here -->
                </div>
            </div>
            <div class="timeline-container">
                <svg class="timeline-svg" id="timelineSvg">
                    <!-- Timeline visualization will be generated here -->
                </svg>
                <div class="timeline-tooltip" id="timelineTooltip"></div>
            </div>
        </div>

        <div class="log-entries" id="logEntries">
            <!-- Log entries will be populated here -->
        </div>
    </div>

    <script>
        let ws = null;
        let logEntries = [];

        function updateConnectionStatus(connected) {
            const status = document.getElementById('connectionStatus');
            const statusText = document.getElementById('statusText');

            if (connected) {
                status.className = 'status connected';
                statusText.textContent = 'Connected - Real-time updates active';
            } else {
                status.className = 'status disconnected';
                statusText.textContent = 'Disconnected - Real-time updates inactive';
            }
        }

        function connectWebSocket() {
            if (ws) {
                ws.close();
            }

            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';

            // Build WebSocket URL with current filter parameters
            const filters = new URLSearchParams();
            const hookId = document.getElementById('hookIdFilter').value.trim();
            const prNumber = document.getElementById('prNumberFilter').value.trim();
            const repository = document.getElementById('repositoryFilter').value.trim();
            const user = document.getElementById('userFilter').value.trim();
            const level = document.getElementById('levelFilter').value;

            if (hookId) filters.append('hook_id', hookId);
            if (prNumber) filters.append('pr_number', prNumber);
            if (repository) filters.append('repository', repository);
            if (user) filters.append('github_user', user);
            if (level) filters.append('level', level);

            const wsUrl = `${protocol}//${window.location.host}/logs/ws${filters.toString() ? '?' + filters.toString() : ''}`;

            ws = new WebSocket(wsUrl);

            ws.onopen = function() {
                updateConnectionStatus(true);
                console.log('WebSocket connected');
            };

            ws.onmessage = function(event) {
                const logEntry = JSON.parse(event.data);
                addLogEntry(logEntry);
            };

            ws.onclose = function() {
                updateConnectionStatus(false);
                console.log('WebSocket disconnected');
            };

            ws.onerror = function(error) {
                updateConnectionStatus(false);
                console.error('WebSocket error:', error);
            };
        }

        function disconnectWebSocket() {
            if (ws) {
                ws.close();
                ws = null;
            }
            updateConnectionStatus(false);
        }

        function addLogEntry(entry) {
            logEntries.unshift(entry);
            renderLogEntries();
        }

        function renderLogEntries() {
            const container = document.getElementById('logEntries');
            const filteredEntries = filterLogEntries(logEntries);

            container.innerHTML = filteredEntries.map(entry => `
                <div class="log-entry ${entry.level}">
                    <span class="timestamp">${new Date(entry.timestamp).toLocaleString()}</span>
                    <span class="level">[${entry.level}]</span>
                    <span class="message">${entry.message}</span>
                    ${entry.hook_id ? `<span class="hook-id">[Hook: ${entry.hook_id}]</span>` : ''}
                    ${entry.pr_number ? `<span class="pr-number">[PR: #${entry.pr_number}]</span>` : ''}
                    ${entry.repository ? `<span class="repository">[${entry.repository}]</span>` : ''}
                    ${entry.github_user ? `<span class="user">[User: ${entry.github_user}]</span>` : ''}
                </div>
            `).join('');
        }

        function filterLogEntries(entries) {
            const hookId = document.getElementById('hookIdFilter').value.trim();
            const prNumber = document.getElementById('prNumberFilter').value.trim();
            const repository = document.getElementById('repositoryFilter').value.trim();
            const user = document.getElementById('userFilter').value.trim();
            const level = document.getElementById('levelFilter').value;
            const search = document.getElementById('searchFilter').value.trim().toLowerCase();

            return entries.filter(entry => {
                if (hookId && entry.hook_id !== hookId) return false;
                if (prNumber && entry.pr_number !== parseInt(prNumber)) return false;
                if (repository && entry.repository !== repository) return false;
                if (user && entry.github_user !== user) return false;
                if (level && entry.level !== level) return false;
                if (search && !entry.message.toLowerCase().includes(search)) return false;
                return true;
            });
        }

        async function loadHistoricalLogs() {
            try {
                // Build API URL with current filter parameters
                const filters = new URLSearchParams();
                const hookId = document.getElementById('hookIdFilter').value.trim();
                const prNumber = document.getElementById('prNumberFilter').value.trim();
                const repository = document.getElementById('repositoryFilter').value.trim();
                const user = document.getElementById('userFilter').value.trim();
                const level = document.getElementById('levelFilter').value;
                const search = document.getElementById('searchFilter').value.trim();

                filters.append('limit', '500');
                if (hookId) filters.append('hook_id', hookId);
                if (prNumber) filters.append('pr_number', prNumber);
                if (repository) filters.append('repository', repository);
                if (user) filters.append('github_user', user);
                if (level) filters.append('level', level);
                if (search) filters.append('search', search);

                const response = await fetch(`/logs/api/entries?${filters.toString()}`);
                const data = await response.json();
                logEntries = data.entries;
                renderLogEntries();
            } catch (error) {
                console.error('Error loading historical logs:', error);
            }
        }

        function clearLogs() {
            logEntries = [];
            renderLogEntries();
        }

        function exportLogs(format) {
            const filters = new URLSearchParams();
            const hookId = document.getElementById('hookIdFilter').value.trim();
            const prNumber = document.getElementById('prNumberFilter').value.trim();
            const repository = document.getElementById('repositoryFilter').value.trim();
            const user = document.getElementById('userFilter').value.trim();
            const level = document.getElementById('levelFilter').value;
            const search = document.getElementById('searchFilter').value.trim();

            if (hookId) filters.append('hook_id', hookId);
            if (prNumber) filters.append('pr_number', prNumber);
            if (repository) filters.append('repository', repository);
            if (user) filters.append('github_user', user);
            if (level) filters.append('level', level);
            if (search) filters.append('search', search);
            filters.append('format', format);

            const url = `/logs/api/export?${filters.toString()}`;
            window.open(url, '_blank');
        }

        function applyFilters() {
            // Reload historical logs with new filters
            loadHistoricalLogs();

            // Reconnect WebSocket with new filters if currently connected
            if (ws && ws.readyState === WebSocket.OPEN) {
                connectWebSocket();
            }
        }

        // Set up filter event handlers with debouncing
        let filterTimeout;
        function debounceFilter() {
            // Immediate client-side filtering for fast feedback
            renderLogEntries();

            // Debounced server-side filtering for accuracy
            clearTimeout(filterTimeout);
            filterTimeout = setTimeout(() => {
                applyFilters(); // Server-side filter for accurate results
            }, 200);
        }

        function clearFilters() {
            document.getElementById('hookIdFilter').value = '';
            document.getElementById('prNumberFilter').value = '';
            document.getElementById('repositoryFilter').value = '';
            document.getElementById('userFilter').value = '';
            document.getElementById('levelFilter').value = '';
            document.getElementById('searchFilter').value = '';

            // Reload data with cleared filters
            applyFilters();
        }

        document.getElementById('hookIdFilter').addEventListener('input', debounceFilter);
        document.getElementById('prNumberFilter').addEventListener('input', debounceFilter);
        document.getElementById('repositoryFilter').addEventListener('input', debounceFilter);
        document.getElementById('userFilter').addEventListener('input', debounceFilter);
        document.getElementById('levelFilter').addEventListener('change', debounceFilter);
        document.getElementById('searchFilter').addEventListener('input', debounceFilter);

        // Theme management
        function toggleTheme() {
            const currentTheme = document.documentElement.getAttribute('data-theme');
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

            document.documentElement.setAttribute('data-theme', newTheme);

            // Update theme toggle button icon
            const themeToggle = document.querySelector('.theme-toggle');
            themeToggle.textContent = newTheme === 'dark' ? '☀️' : '🌙';

            // Store theme preference in localStorage
            localStorage.setItem('log-viewer-theme', newTheme);
        }

        // Initialize theme from localStorage or default to light
        function initializeTheme() {
            const savedTheme = localStorage.getItem('log-viewer-theme') || 'light';
            document.documentElement.setAttribute('data-theme', savedTheme);

            // Update theme toggle button icon
            const themeToggle = document.querySelector('.theme-toggle');
            themeToggle.textContent = savedTheme === 'dark' ? '☀️' : '🌙';
        }

        // Initialize theme on page load
        initializeTheme();

        // Initialize connection status
        updateConnectionStatus(false);

        // Load initial data
        loadHistoricalLogs();

        // Timeline functionality
        let currentTimelineData = null;

        function showTimeline(hookId) {
            if (!hookId) {
                hideTimeline();
                return;
            }


            // Fetch workflow steps data
            fetch(`/logs/api/workflow-steps/${hookId}`)
                .then(response => {
                    if (!response.ok) {
                        if (response.status === 404) {
                            hideTimeline();
                            return;
                        }
                        throw new Error('Failed to fetch workflow steps');
                    }
                    return response.json();
                })
                .then(data => {
                    currentTimelineData = data;
                    renderTimeline(data);
                    document.getElementById('timelineSection').style.display = 'block';
                })
                .catch(error => {
                    hideTimeline();
                });
        }

        function hideTimeline() {
            document.getElementById('timelineSection').style.display = 'none';
            currentTimelineData = null;
        }

        function renderTimeline(data) {
            const svg = document.getElementById('timelineSvg');
            const info = document.getElementById('timelineInfo');

            // Update timeline info
            const duration = data.total_duration_ms > 0 ? `${(data.total_duration_ms / 1000).toFixed(2)}s` : '< 1s';
            info.innerHTML = `
                <div>Hook ID: <strong>${data.hook_id}</strong></div>
                <div>Steps: <strong>${data.step_count}</strong></div>
                <div>Duration: <strong>${duration}</strong></div>
            `;

            if (data.steps.length === 0) {
                svg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="var(--text-color)">No workflow steps found</text>';
                return;
            }

            // Clear existing content
            svg.innerHTML = '';

            // SVG dimensions
            const width = svg.clientWidth || 800;
            const height = 120;
            const margin = { left: 60, right: 60, top: 30, bottom: 40 };
            const timelineWidth = width - margin.left - margin.right;

            // Update SVG size
            svg.setAttribute('width', width);
            svg.setAttribute('height', height);

            // Calculate positions
            const stepPositions = [];
            const maxTime = Math.max(data.total_duration_ms, 1000); // Minimum 1 second for visibility

            data.steps.forEach((step, index) => {
                const x = margin.left + (step.relative_time_ms / maxTime) * timelineWidth;
                stepPositions.push({ x, step, index });
            });

            // Draw timeline line
            const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            line.setAttribute('class', 'step-line');
            line.setAttribute('x1', margin.left);
            line.setAttribute('y1', height / 2);
            line.setAttribute('x2', margin.left + timelineWidth);
            line.setAttribute('y2', height / 2);
            svg.appendChild(line);

            // Draw steps
            stepPositions.forEach(({ x, step, index }) => {
                const group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
                group.setAttribute('class', 'timeline-step');
                group.setAttribute('data-step-index', index);

                // Step circle
                const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
                circle.setAttribute('class', `step-circle ${getStepType(step.message)}`);
                circle.setAttribute('cx', x);
                circle.setAttribute('cy', height / 2);
                svg.appendChild(circle);

                // Step label (wrapped text)
                const labelLines = wrapText(step.message, 25);
                labelLines.forEach((line, lineIndex) => {
                    const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                    label.setAttribute('class', 'step-label');
                    label.setAttribute('x', x);
                    label.setAttribute('y', height / 2 - 25 + (lineIndex * 12));
                    label.setAttribute('text-anchor', 'middle');
                    label.textContent = line;
                    svg.appendChild(label);
                    group.appendChild(label);
                });

                // Time label
                const timeLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                timeLabel.setAttribute('class', 'step-time');
                timeLabel.setAttribute('x', x);
                timeLabel.setAttribute('y', height / 2 + 35);
                timeLabel.setAttribute('text-anchor', 'middle');
                timeLabel.textContent = `+${(step.relative_time_ms / 1000).toFixed(1)}s`;
                svg.appendChild(timeLabel);

                group.appendChild(circle);
                group.appendChild(timeLabel);

                // Add hover events
                group.addEventListener('mouseenter', (e) => showTooltip(e, step));
                group.addEventListener('mouseleave', hideTooltip);
                group.addEventListener('click', () => filterByStep(step));

                svg.appendChild(group);
            });
        }

        function getStepType(message) {
            if (message.includes('completed successfully') || message.includes('success')) {
                return 'success';
            } else if (message.includes('failed') || message.includes('error')) {
                return 'failure';
            } else if (message.includes('Starting') || message.includes('Executing')) {
                return 'progress';
            } else {
                return 'info';
            }
        }

        function truncateText(text, maxLength) {
            return text.length > maxLength ? text.substring(0, maxLength) + '...' : text;
        }

        function wrapText(text, maxLineLength) {
            const words = text.split(' ');
            const lines = [];
            let currentLine = '';

            for (const word of words) {
                if ((currentLine + word).length <= maxLineLength) {
                    currentLine += (currentLine ? ' ' : '') + word;
                } else {
                    if (currentLine) lines.push(currentLine);
                    currentLine = word;
                }
            }
            if (currentLine) lines.push(currentLine);
            return lines.slice(0, 2); // Max 2 lines
        }

        function showTooltip(event, step) {
            const tooltip = document.getElementById('timelineTooltip');
            const timeFromStart = `+${(step.relative_time_ms / 1000).toFixed(2)}s`;

            tooltip.innerHTML = `
                <div><strong>Step:</strong> ${step.message}</div>
                <div><strong>Time:</strong> ${timeFromStart}</div>
                <div><strong>Timestamp:</strong> ${new Date(step.timestamp).toLocaleTimeString()}</div>
                ${step.pr_number ? `<div><strong>PR:</strong> #${step.pr_number}</div>` : ''}
                <div style="margin-top: 5px; font-size: 10px; color: var(--timestamp-color);">Click to filter logs by this step</div>
            `;

            const rect = event.target.getBoundingClientRect();
            const containerRect = document.getElementById('timelineSection').getBoundingClientRect();

            tooltip.style.left = (rect.left - containerRect.left + rect.width / 2) + 'px';
            tooltip.style.top = (rect.top - containerRect.top - tooltip.offsetHeight - 10) + 'px';
            tooltip.style.display = 'block';
        }

        function hideTooltip() {
            document.getElementById('timelineTooltip').style.display = 'none';
        }

        function filterByStep(step) {
            // Set search filter to find this specific step message
            document.getElementById('searchFilter').value = step.message.substring(0, 30);
            debounceFilter();
        }

        // Auto-show timeline when hook ID filter is applied
        function checkForTimelineDisplay() {
            const hookId = document.getElementById('hookIdFilter').value.trim();
            if (hookId) {
                showTimeline(hookId);
            } else {
                hideTimeline();
            }
        }

        // Add timeline check to hook ID filter specifically
        document.getElementById('hookIdFilter').addEventListener('input', () => {
            setTimeout(checkForTimelineDisplay, 300); // Small delay to let the value settle
        });

        // Also check on initial load
        setTimeout(checkForTimelineDisplay, 1000);
    </script>
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

        # Define common workflow stages based on log messages
        stage_patterns = [
            ("Webhook Received", r"Processing webhook"),
            ("Validation Complete", r"Signature verification successful|Processing webhook for"),
            ("Reviewers Assigned", r"Added reviewer|OWNERS file|reviewer assignment"),
            ("Labels Applied", r"label|tag"),
            ("Checks Started", r"check|test|build"),
            ("Checks Complete", r"check.*complete|test.*pass|build.*success"),
            ("Processing Complete", r"completed successfully|processing complete"),
        ]

        previous_time = start_time
        for pattern_name, pattern in stage_patterns:
            # Find first entry matching this stage
            for entry in sorted_entries:
                if any(pattern.lower() in entry.message.lower() for pattern in pattern.split("|")):
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
