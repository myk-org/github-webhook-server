"""Log viewer controller for serving log viewer web interface and API endpoints."""

import csv
import datetime
import json
import logging
import os
from io import StringIO
from pathlib import Path
from typing import Any, Generator

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse

from webhook_server.libs.config import Config
from webhook_server.libs.log_parser import LogEntry, LogFilter, LogParser


class LogViewerController:
    """Controller for log viewer functionality."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Initialize the log viewer controller.

        Args:
            logger: Optional logger instance for this controller
        """
        self.logger = logger or logging.getLogger(__name__)
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
        level: str | None = None,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Retrieve historical log entries with filtering and pagination.

        Args:
            hook_id: Filter by specific hook ID
            pr_number: Filter by PR number
            repository: Filter by repository name
            event_type: Filter by GitHub event type
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

            # Load log entries from files
            log_entries = self._load_log_entries()

            # Apply filters
            filtered_entries = self.log_filter.filter_entries(
                entries=log_entries,
                hook_id=hook_id,
                pr_number=pr_number,
                repository=repository,
                event_type=event_type,
                level=level,
                start_time=start_time,
                end_time=end_time,
                search_text=search,
                limit=limit,
                offset=offset,
            )

            return {
                "entries": [entry.to_dict() for entry in filtered_entries],
                "total": len(log_entries),  # Total before filtering
                "filtered_total": len(filtered_entries),
                "limit": limit,
                "offset": offset,
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

    def export_logs(
        self,
        format_type: str,
        hook_id: str | None = None,
        pr_number: int | None = None,
        repository: str | None = None,
        event_type: str | None = None,
        level: str | None = None,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
        search: str | None = None,
        limit: int = 10000,
    ) -> StreamingResponse:
        """Export filtered logs as CSV or JSON file.

        Args:
            format_type: Export format ("csv" or "json")
            hook_id: Filter by specific hook ID
            pr_number: Filter by PR number
            repository: Filter by repository name
            event_type: Filter by GitHub event type
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
            if format_type not in ("csv", "json"):
                raise ValueError(f"Invalid format: {format_type}")

            if limit > 50000:
                raise ValueError("Result set too large (max 50000 entries)")

            # Load and filter log entries
            log_entries = self._load_log_entries()
            filtered_entries = self.log_filter.filter_entries(
                entries=log_entries,
                hook_id=hook_id,
                pr_number=pr_number,
                repository=repository,
                event_type=event_type,
                level=level,
                start_time=start_time,
                end_time=end_time,
                search_text=search,
                limit=limit,
            )

            if len(filtered_entries) > 50000:
                raise ValueError("Result set too large")

            # Generate export content
            if format_type == "csv":
                content = self._generate_csv_export(filtered_entries)
                media_type = "text/csv"
                filename = f"webhook_logs_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            else:  # json
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
        level: str | None = None,
    ) -> None:
        """Handle WebSocket connection for real-time log streaming.

        Args:
            websocket: WebSocket connection
            hook_id: Filter by specific hook ID
            pr_number: Filter by PR number
            repository: Filter by repository name
            event_type: Filter by GitHub event type
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
                if not any([hook_id, pr_number, repository, event_type, level]):
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

    def get_pr_flow_data(self, identifier: str) -> dict[str, Any]:
        """Get PR flow visualization data for a specific hook ID or PR number.

        Args:
            identifier: Hook ID (e.g., "hook-abc123") or PR number (e.g., "pr-456")

        Returns:
            Dictionary with flow stages and timing data

        Raises:
            HTTPException: 404 if no data found for identifier
        """
        try:
            # Parse identifier to determine if it's a hook ID or PR number
            if identifier.startswith("hook-"):
                hook_id = identifier[5:]  # Remove "hook-" prefix
                pr_number = None
            elif identifier.startswith("pr-"):
                hook_id = None
                pr_number = int(identifier[3:])  # Remove "pr-" prefix
            else:
                # Try to parse as direct hook ID or PR number
                try:
                    pr_number = int(identifier)
                    hook_id = None
                except ValueError:
                    hook_id = identifier
                    pr_number = None

            # Load log entries and filter by identifier
            log_entries = self._load_log_entries()
            filtered_entries = self.log_filter.filter_entries(
                entries=log_entries,
                hook_id=hook_id,
                pr_number=pr_number,
            )

            if not filtered_entries:
                raise ValueError(f"No data found for identifier: {identifier}")

            # Analyze flow stages from log entries
            flow_data = self._analyze_pr_flow(filtered_entries, identifier)
            return flow_data

        except ValueError as e:
            if "No data found" in str(e):
                self.logger.warning(f"PR flow data not found: {e}")
                raise HTTPException(status_code=404, detail=str(e))
            else:
                self.logger.warning(f"Invalid PR flow identifier: {e}")
                raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            self.logger.error(f"Error getting PR flow data: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")

    def _load_log_entries(self) -> list[LogEntry]:
        """Load all log entries from configured log files.

        Returns:
            List of parsed log entries
        """
        log_entries: list[LogEntry] = []
        log_dir = self._get_log_directory()

        if not log_dir.exists():
            self.logger.warning(f"Log directory not found: {log_dir}")
            return log_entries

        # Find all log files including rotated ones (*.log, *.log.1, *.log.2, etc.)
        log_files: list[Path] = []
        log_files.extend(log_dir.glob("*.log"))
        log_files.extend(log_dir.glob("*.log.*"))

        # Sort log files to process in correct order (current log first, then rotated)
        # This ensures newer entries come first in the final sorted list
        log_files.sort(key=lambda f: (f.name.count("."), f.stat().st_mtime))

        self.logger.info(f"Loading historical logs from {len(log_files)} files: {[f.name for f in log_files]}")

        for log_file in log_files:
            try:
                file_entries = self.log_parser.parse_log_file(log_file)
                log_entries.extend(file_entries)
            except Exception as e:
                self.logger.warning(f"Error parsing log file {log_file}: {e}")

        # Sort by timestamp (newest first)
        log_entries.sort(key=lambda x: x.timestamp, reverse=True)
        return log_entries

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
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .header { border-bottom: 1px solid #ddd; padding-bottom: 20px; margin-bottom: 20px; }
        .filters { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .filter-group { display: flex; flex-direction: column; }
        .filter-group label { font-weight: bold; margin-bottom: 5px; }
        .filter-group input, .filter-group select { padding: 8px; border: 1px solid #ddd; border-radius: 4px; }
        .log-entries { border: 1px solid #ddd; border-radius: 4px; max-height: 600px; overflow-y: auto; }
        .log-entry { padding: 10px; border-bottom: 1px solid #eee; font-family: monospace; font-size: 14px; }
        .log-entry:last-child { border-bottom: none; }
        .log-entry.INFO { background-color: #f0f8ff; }
        .log-entry.ERROR { background-color: #ffe6e6; }
        .log-entry.WARNING { background-color: #fff3cd; }
        .log-entry.DEBUG { background-color: #f8f9fa; }
        .timestamp { color: #666; }
        .level { font-weight: bold; }
        .message { margin-left: 10px; }
        .controls { margin-bottom: 20px; }
        .btn { padding: 10px 20px; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; margin-right: 10px; }
        .btn:hover { background-color: #0056b3; }
        .status { padding: 10px; margin-bottom: 20px; border-radius: 4px; }
        .status.connected { background-color: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .status.disconnected { background-color: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>GitHub Webhook Server - Log Viewer</h1>
            <p>Real-time log monitoring and filtering for webhook events</p>
        </div>

        <div class="status" id="connectionStatus">
            <span id="statusText">Connecting...</span>
        </div>

        <div class="controls">
            <button class="btn" onclick="connectWebSocket()">Start Real-time</button>
            <button class="btn" onclick="disconnectWebSocket()">Stop Real-time</button>
            <button class="btn" onclick="loadHistoricalLogs()">Load Historical</button>
            <button class="btn" onclick="clearLogs()">Clear</button>
            <button class="btn" onclick="exportLogs('csv')">Export CSV</button>
            <button class="btn" onclick="exportLogs('json')">Export JSON</button>
        </div>

        <div class="filters">
            <div class="filter-group">
                <label for="hookIdFilter">Hook ID:</label>
                <input type="text" id="hookIdFilter" placeholder="hook-abc123">
            </div>
            <div class="filter-group">
                <label for="prNumberFilter">PR Number:</label>
                <input type="number" id="prNumberFilter" placeholder="123">
            </div>
            <div class="filter-group">
                <label for="repositoryFilter">Repository:</label>
                <input type="text" id="repositoryFilter" placeholder="org/repo">
            </div>
            <div class="filter-group">
                <label for="levelFilter">Log Level:</label>
                <select id="levelFilter">
                    <option value="">All Levels</option>
                    <option value="DEBUG">DEBUG</option>
                    <option value="INFO">INFO</option>
                    <option value="WARNING">WARNING</option>
                    <option value="ERROR">ERROR</option>
                </select>
            </div>
            <div class="filter-group">
                <label for="searchFilter">Search:</label>
                <input type="text" id="searchFilter" placeholder="Search messages...">
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
            const wsUrl = `${protocol}//${window.location.host}/logs/ws`;

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
                </div>
            `).join('');
        }

        function filterLogEntries(entries) {
            const hookId = document.getElementById('hookIdFilter').value.trim();
            const prNumber = document.getElementById('prNumberFilter').value.trim();
            const repository = document.getElementById('repositoryFilter').value.trim();
            const level = document.getElementById('levelFilter').value;
            const search = document.getElementById('searchFilter').value.trim().toLowerCase();

            return entries.filter(entry => {
                if (hookId && entry.hook_id !== hookId) return false;
                if (prNumber && entry.pr_number !== parseInt(prNumber)) return false;
                if (repository && entry.repository !== repository) return false;
                if (level && entry.level !== level) return false;
                if (search && !entry.message.toLowerCase().includes(search)) return false;
                return true;
            });
        }

        async function loadHistoricalLogs() {
            try {
                const response = await fetch('/logs/api/entries?limit=500');
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
            const level = document.getElementById('levelFilter').value;
            const search = document.getElementById('searchFilter').value.trim();

            if (hookId) filters.append('hook_id', hookId);
            if (prNumber) filters.append('pr_number', prNumber);
            if (repository) filters.append('repository', repository);
            if (level) filters.append('level', level);
            if (search) filters.append('search', search);
            filters.append('format', format);

            const url = `/logs/api/export?${filters.toString()}`;
            window.open(url, '_blank');
        }

        // Set up filter event handlers
        document.getElementById('hookIdFilter').addEventListener('input', renderLogEntries);
        document.getElementById('prNumberFilter').addEventListener('input', renderLogEntries);
        document.getElementById('repositoryFilter').addEventListener('input', renderLogEntries);
        document.getElementById('levelFilter').addEventListener('change', renderLogEntries);
        document.getElementById('searchFilter').addEventListener('input', renderLogEntries);

        // Initialize connection status
        updateConnectionStatus(false);

        // Load initial data
        loadHistoricalLogs();
    </script>
</body>
</html>"""

    def _generate_csv_export(self, entries: list[LogEntry]) -> str:
        """Generate CSV export content from log entries.

        Args:
            entries: List of log entries to export

        Returns:
            CSV content as string
        """
        output = StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow([
            "timestamp",
            "level",
            "logger_name",
            "message",
            "hook_id",
            "event_type",
            "repository",
            "pr_number",
        ])

        # Write data rows
        for entry in entries:
            writer.writerow([
                entry.timestamp.isoformat(),
                entry.level,
                entry.logger_name,
                entry.message,
                entry.hook_id or "",
                entry.event_type or "",
                entry.repository or "",
                entry.pr_number or "",
            ])

        return output.getvalue()

    def _generate_json_export(self, entries: list[LogEntry]) -> str:
        """Generate JSON export content from log entries.

        Args:
            entries: List of log entries to export

        Returns:
            JSON content as string
        """
        return json.dumps([entry.to_dict() for entry in entries], indent=2)

    def _analyze_pr_flow(self, entries: list[LogEntry], identifier: str) -> dict[str, Any]:
        """Analyze PR workflow stages from log entries.

        Args:
            entries: List of log entries for the PR/hook
            identifier: Original identifier used for the request

        Returns:
            Dictionary with flow stages and timing data
        """
        # Sort entries by timestamp
        sorted_entries = sorted(entries, key=lambda x: x.timestamp)

        if not sorted_entries:
            return {
                "identifier": identifier,
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
            "identifier": identifier,
            "stages": stages,
            "total_duration_ms": total_duration,
            "success": success,
        }

        if error_message:
            flow_data["error"] = error_message

        return flow_data
