# Log Viewer API

The log viewer exposes a browser UI at `/logs`, historical REST endpoints under `/logs/api/*`, and a live stream at `/logs/ws`. Use it to search webhook history, export filtered results, inspect PR processing at a high level, drill into one webhook’s step timeline, and fetch the raw log lines that happened during a specific step.

The examples below are copied from the shipped frontend and test fixtures in this repository.

> **Warning:** The log viewer is not authenticated by the application. Treat it as an internal tool and expose it only on trusted networks.

## Enable It

Set `ENABLE_LOG_SERVER=true` before you expect the page, REST endpoints, or WebSocket stream to be available.

```17:20:examples/docker-compose.yaml
      - VERIFY_GITHUB_IPS=1 # Verify hook request is from GitHub IPs
      - VERIFY_CLOUDFLARE_IPS=1 # Verify hook request is from Cloudflare IPs
      - ENABLE_LOG_SERVER=true # Enable log viewer endpoints (default: false)
      - ENABLE_MCP_SERVER=false # Enable MCP server for AI agent integration (default: false)
```

You can also set a dedicated log file for the log viewer itself:

```3:7:examples/config.yaml
log-level: INFO # Set global log level, change take effect immediately without server restart
log-file: webhook-server.log # Set global log file, change take effect immediately without server restart
mcp-log-file: mcp_server.log # Set global MCP log file, change take effect immediately without server restart
logs-server-log-file: logs_server.log # Set global Logs Server log file, change take effect immediately without server restart
mask-sensitive-data: true # Mask sensitive data in logs (default: true). Set to false for debugging (NOT recommended in production)
```

> **Note:** When the log server is disabled, the REST endpoints respond as unavailable, the WebSocket closes with code `1008`, and the `/logs` page is not exposed.

## What The API Reads

The log viewer scans files under `<data_dir>/logs/` and combines two sources:

- Text logs such as `webhook-server.log` and rotated `*.log.*` files.
- Structured JSONL files named `webhooks_YYYY-MM-DD.json`.

A few behaviors matter when you use the API:

- Historical queries prefer `webhooks_*.json` first, then plain `.log` files.
- Workflow-step lookups prefer structured JSON summaries and fall back to text logs only when needed.
- Step-scoped log correlation reads text `.log` files only, because that is where detailed per-operation lines live.
- Infrastructure-only noise from the log viewer, MCP server, and parser is filtered out unless it is tied to a webhook context.

> **Note:** The viewer scans `<data_dir>/logs/` only. If you move your main text log file somewhere else with an absolute path, historical text queries and step-scoped log correlation will not see it.

## Shared Log Entry Shape

Historical query results and WebSocket messages use the same entry model. The important fields are:

- `timestamp`: ISO 8601 timestamp.
- `level`: exact log level, such as `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `COMPLETED`.
- `logger_name`: the logger that emitted the line.
- `message`: rendered message text.
- `hook_id`: GitHub delivery ID.
- `event_type`: GitHub event name such as `pull_request` or `check_run`.
- `repository`: repository in `owner/repo` form when available.
- `pr_number`: pull request number when available.
- `github_user`: GitHub or API user associated with the entry.
- `task_id`, `task_type`, `task_status`: workflow-correlation fields when the original log line included them.
- `token_spend`: parsed GitHub API call usage when available.

The `search` filter only matches `message`. All other filters are exact matches.

> **Tip:** If you want one high-level row per webhook execution, filter `level=COMPLETED`. Those entries come from the structured webhook summary log.

## Endpoint Reference

### `GET /logs/api/entries`

Use this endpoint for historical search and pagination.

Supported query parameters:

- `hook_id`: exact delivery ID.
- `pr_number`: exact pull request number.
- `repository`: exact `owner/repo`.
- `event_type`: exact GitHub event type.
- `github_user`: exact user value stored on the entry.
- `level`: exact log level.
- `start_time`: inclusive ISO 8601 lower bound.
- `end_time`: inclusive ISO 8601 upper bound.
- `search`: case-insensitive substring match against `message`.
- `limit`: page size from `1` to `10000`.
- `offset`: number of matching entries to skip.

The built-in UI uses this endpoint like this:

```354:375:webhook_server/web/static/js/log_viewer.js
    const filters = new URLSearchParams();
    const hookId = document.getElementById("hookIdFilter").value.trim();
    const prNumber = document.getElementById("prNumberFilter").value.trim();
    const repository = document.getElementById("repositoryFilter").value.trim();
    const user = document.getElementById("userFilter").value.trim();
    const level = document.getElementById("levelFilter").value;
    const search = document.getElementById("searchFilter").value.trim();
    const limit = document.getElementById("limitFilter").value;

    filters.append("limit", limit);
    if (hookId) filters.append("hook_id", hookId);
    if (prNumber) filters.append("pr_number", prNumber);
    if (repository) filters.append("repository", repository);
    if (user) filters.append("github_user", user);
    if (level) filters.append("level", level);
    if (search) filters.append("search", search);
    appendTimeFilters(filters);

    const response = await fetch(`/logs/api/entries?${filters.toString()}`);
```

The response contains:

- `entries`: the current page of matching log entries.
- `entries_processed`: how many entries the server examined for this request. This may be an integer or a string such as `"50000+"`.
- `filtered_count_min`: a lower bound for total matches, not an exact total.
- `total_log_count_estimate`: a rough text-log size estimate, often formatted like `1.2K` or `1.0M`.
- `limit`: echoed page size.
- `offset`: echoed offset.
- `is_partial_scan`: `true` when the server hit its internal scan cap.

Operational details:

- Unfiltered requests scan up to `20000` entries.
- Filtered requests scan up to `50000` entries.
- The scan reads up to `25` recent log files.
- The server stops early once it has enough entries for your page.

> **Note:** There is no exact total-count field. `filtered_count_min` is intentionally conservative because the server stops as soon as it has filled your page or reached the scan cap.

### `GET /logs/api/export`

Use this endpoint when you want a downloadable copy of the same filtered data.

It accepts the same filters as `/logs/api/entries`, plus:

- `format_type`: must be `json`.
- `limit`: effective maximum `50000`.

What you get back:

- A streamed JSON download.
- `Content-Type: application/json`.
- A filename in the form `webhook_logs_YYYYMMDD_HHMMSS.json`.

The JSON file contains:

- `export_metadata.generated_at`
- `export_metadata.filters_applied`
- `export_metadata.total_entries`
- `export_metadata.export_format`
- `log_entries`

Useful behavior to know:

- The export is streamed instead of fully buffered in memory.
- Requests above `50000` entries are rejected with `413`.
- An empty match set still produces a valid JSON export.

> **Tip:** Build your filters with `/logs/api/entries` first. When the result set looks right, send the same filters to `/logs/api/export`.

### `GET /logs/api/pr-flow/{hook_id}`

Use this endpoint for a compact, stage-based flow summary.

Despite the `{hook_id}` path name, this endpoint accepts four identifier styles:

- `hook-abc123`
- `pr-42`
- `42`
- `abc123`

How to choose:

- Use a delivery ID when you want one webhook run.
- Use `pr-42` or `42` when you want a PR-wide view across all matching log entries for that PR.

The response includes:

- `identifier`
- `stages`
- `total_duration_ms`
- `success`
- optional `error`

Each stage can include:

- `name`
- `timestamp`
- `duration_ms`
- optional `error`

The stage names are matched from log messages using these buckets:

- `Webhook Received`
- `Validation Complete`
- `Reviewers Assigned`
- `Labels Applied`
- `Checks Started`
- `Checks Complete`
- `Processing Complete`

> **Note:** This endpoint is pattern-based analysis, not a strict replay of structured workflow data. If you need the exact per-step timeline for one delivery, use `/logs/api/workflow-steps/{hook_id}` instead.

### `GET /logs/api/workflow-steps/{hook_id}`

Use this endpoint for the richest single-delivery view.

This endpoint expects the raw delivery ID, such as `test-hook-123`. Unlike `/logs/api/pr-flow/{hook_id}`, it does not accept `hook-...`, `pr-...`, or bare PR-number aliases.

When structured summary data exists in `webhooks_*.json`, the response can include:

- `hook_id`
- `start_time`
- `total_duration_ms`
- `step_count`
- `steps`
- `token_spend`
- `event_type`
- `action`
- `repository`
- `sender`
- `pr`
- `success`
- `error`

Each step can include:

- `timestamp`
- `step_name`
- `message`
- `level`
- `repository`
- `event_type`
- `pr_number`
- `task_id`
- `task_type`
- `task_status`
- `duration_ms`
- `error`
- `step_details`
- `relative_time_ms`

A structured webhook summary in the test suite looks like this:

```226:265:webhook_server/tests/conftest.py
    return {
        "hook_id": "test-hook-123",
        "event_type": "pull_request",
        "action": "opened",
        "repository": "org/test-repo",
        "sender": "test-user",
        "pr": {
            "number": 456,
            "title": "Test PR",
            "url": "https://github.com/org/test-repo/pull/456",
        },
        "timing": {
            "started_at": "2025-01-05T10:00:00.000000Z",
            "completed_at": "2025-01-05T10:00:05.000000Z",
            "duration_ms": 5000,
        },
        "workflow_steps": {
            "clone_repository": {
                "timestamp": "2025-01-05T10:00:01.000000Z",
                "status": "completed",
                "duration_ms": 1500,
            },
            "assign_reviewers": {
                "timestamp": "2025-01-05T10:00:02.500000Z",
                "status": "completed",
                "duration_ms": 800,
            },
            "apply_labels": {
                "timestamp": "2025-01-05T10:00:03.500000Z",
                "status": "failed",
                "duration_ms": 200,
                "error": {"type": "ValueError", "message": "Label not found"},
            },
        },
        "token_spend": 35,
        "success": False,
        "error": {
            "type": "TestError",
            "message": "Test failure message for unit tests",
        },
    }
```

Fallback behavior:

- The server first searches structured JSON summaries.
- If it cannot find one, it falls back to text logs.
- The fallback timeline is simpler and only includes entries that carried both `task_id` and `task_status`.
- In fallback mode, `token_spend` can still be inferred from a text message such as `Token spend: 15 API calls`.

> **Tip:** This is the endpoint the built-in `/logs` UI uses for its detailed flow modal. It is the best API to call once you already know the delivery ID you care about.

### `GET /logs/api/step-logs/{hook_id}/{step_name}`

Use this endpoint when you know which workflow step you want to inspect and need the raw log lines that happened during that step.

This endpoint also expects the raw delivery ID, not the prefixed forms accepted by `/logs/api/pr-flow/{hook_id}`.

How it works:

- It loads the workflow-step timeline for the delivery.
- It finds the step whose `step_name` exactly matches your path segment.
- It builds a time window from the step’s `timestamp` and `duration_ms`.
- It scans text `.log` files for that delivery inside that time window.

The built-in UI URL-encodes both path segments when it fetches step-scoped logs:

```1991:2005:webhook_server/web/static/js/log_viewer.js
  // Fetch actual log entries for this step
  const stepName = step.step_name;
  const hookId = currentFlowData?.hook_id;

  if (stepName && hookId) {
    // Show loading indicator
    const loadingDiv = document.createElement("div");
    loadingDiv.className = "step-logs-loading";
    loadingDiv.textContent = "Loading logs...";

    try {
      const response = await fetch(
        `/logs/api/step-logs/${encodeURIComponent(hookId)}/${encodeURIComponent(stepName)}`,
        { signal: currentStepLogsController.signal }
      );
```

The response contains:

- `step`: metadata for the requested step.
- `logs`: matching log entries from the step’s execution window.
- `log_count`: number of returned log entries.

Important limits and edge cases:

- At most `500` log entries are returned.
- If `duration_ms` is missing, the server uses a default `60` second window starting at the step timestamp.
- If the step exists but nothing was logged in that window, you still get `step` plus `logs: []`.
- If the step timestamp is missing or malformed, the endpoint fails rather than guessing.

> **Warning:** This is the only log-viewer endpoint with an additional trusted-network check. Requests are allowed only from private, loopback, or link-local client addresses.

### `WS /logs/ws`

Use the WebSocket when you want live updates after you have loaded history.

Supported query parameters:

- `hook_id`
- `pr_number`
- `repository`
- `event_type`
- `github_user`
- `level`

The shipped frontend builds the connection like this:

```54:77:webhook_server/web/static/js/log_viewer.js
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";

  // Build WebSocket URL with current filter parameters
  const filters = new URLSearchParams();
  const hookId = document.getElementById("hookIdFilter").value.trim();
  const prNumber = document.getElementById("prNumberFilter").value.trim();
  const repository = document.getElementById("repositoryFilter").value.trim();
  const user = document.getElementById("userFilter").value.trim();
  const level = document.getElementById("levelFilter").value;

  if (hookId) filters.append("hook_id", hookId);
  if (prNumber) filters.append("pr_number", prNumber);
  if (repository) filters.append("repository", repository);
  if (user) filters.append("github_user", user);
  if (level) filters.append("level", level);

  const wsUrl = `${protocol}//${window.location.host}/logs/ws${
    filters.toString() ? "?" + filters.toString() : ""
  }`;

  ws = new WebSocket(wsUrl);
```

Behavior to expect:

- The stream starts at the end of the current file, so it delivers new entries only.
- It monitors the most recent current `webhooks_*.json` file.
- If no JSON webhook file exists, it falls back to the most recent current `.log` file.
- Rotated files are not tailed in real time.
- Messages use the same log-entry shape as `/logs/api/entries`.

Connection and error behavior:

- If the log directory is missing, the server sends `{"error": "Log directory not found"}`.
- If the log server is disabled, the socket closes with code `1008`.
- If the server hits an internal error while streaming, it closes with code `1011`.
- On application shutdown, open log-viewer sockets are closed with code `1001`.

> **Note:** The WebSocket only applies context filters. It does not do historical replay, time-range filtering, or free-text `search`. Use `/logs/api/entries` first if you need “history plus live updates.”

## Practical Drill-Down

A good workflow is:

1. Query `/logs/api/entries` with `repository`, `pr_number`, `hook_id`, or `search` to find the run you care about.
2. Take the raw `hook_id` from those results and call `/logs/api/workflow-steps/{hook_id}` for the precise per-step timeline.
3. If one step looks suspicious, call `/logs/api/step-logs/{hook_id}/{step_name}` for the raw log lines inside that step window.
4. Keep `/logs/ws` open if you want to watch new entries after the initial history load.

> **Tip:** If you start from a PR number, a practical first query is `/logs/api/entries?pr_number=456`. That matches how the built-in UI discovers the hook IDs attached to a PR before opening the detailed flow view.
