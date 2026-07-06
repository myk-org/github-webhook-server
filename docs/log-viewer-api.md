Let me start by reading the knowledge graph and page manifest, then explore the relevant source files.

Now let me look at the `LogEntry` model and the `parse_datetime_string` utility:

Now let me check the `APP_URL_ROOT_PATH` and routing info:

Now let me also check the `get_workflow_steps_core` reference:

Now let me look at the actual response structures more carefully by checking tests:

Let me now look at the tests related to endpoints specifically to understand the actual response format:

Now let me check the PR flow response by looking at `_analyze_pr_flow` more closely and the actual JSON response structure:

Now I have a comprehensive understanding of the API. Let me write the reference page.

# Log Viewer API Reference

All Log Viewer endpoints are served by the webhook server at the same host and port. Endpoints require the `ENABLE_LOG_SERVER=true` environment variable to be set.

> **Warning:** Log Viewer endpoints are **unauthenticated**. Never expose them to the public internet. Deploy on trusted networks only. See [Environment Variables](environment-variables.html) for configuration details.

## Prerequisites

| Requirement | Details |
|---|---|
| Environment variable | `ENABLE_LOG_SERVER=true` |
| Disabled response (REST) | `404` — `"Log server is disabled. Set ENABLE_LOG_SERVER=true to enable."` |
| Disabled response (WebSocket) | Close code `1008` (Policy Violation) — `"Log server is disabled"` |

## Endpoints Overview

| Method | Path | Description |
|---|---|---|
| `GET` | `/logs` | Log viewer web UI |
| `GET` | `/logs/api/entries` | Query log entries with filters and pagination |
| `GET` | `/logs/api/export` | Export filtered logs as a JSON file download |
| `GET` | `/logs/api/pr-flow/{hook_id}` | PR workflow flow visualization data |
| `GET` | `/logs/api/workflow-steps/{hook_id}` | Detailed workflow step timeline |
| `GET` | `/logs/api/step-logs/{hook_id}/{step_name}` | Log entries within a specific step's time window |
| `WebSocket` | `/logs/ws` | Real-time log streaming |

---

## `GET /logs`

Serves the log viewer web UI as an HTML page.

**Response:** `200` — `text/html`

```
GET /logs
```

---

## `GET /logs/api/entries`

Retrieve historical log entries with filtering and pagination. Uses memory-efficient streaming internally.

### Query Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `hook_id` | `string` | `null` | GitHub webhook delivery ID (`X-GitHub-Delivery` header value) |
| `pr_number` | `integer` | `null` | Pull request number |
| `repository` | `string` | `null` | Repository in `owner/repo` format |
| `event_type` | `string` | `null` | GitHub event type (e.g., `pull_request`, `push`, `issue_comment`, `pull_request_review`) |
| `github_user` | `string` | `null` | GitHub username who triggered the event |
| `level` | `string` | `null` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `SUCCESS` |
| `start_time` | `string` | `null` | ISO 8601 datetime (e.g., `2024-01-15T10:00:00Z`) |
| `end_time` | `string` | `null` | ISO 8601 datetime (e.g., `2024-01-15T18:00:00Z`) |
| `search` | `string` | `null` | Case-insensitive full-text search across log messages |
| `limit` | `integer` | `100` | Maximum entries to return. Range: `1`–`10000` |
| `offset` | `integer` | `0` | Number of entries to skip for pagination. Must be ≥ `0` |

### Response Body

```json
{
  "entries": [
    {
      "timestamp": "2024-01-15T14:30:25.123456",
      "level": "INFO",
      "logger_name": "webhook_server.app",
      "message": "Processing webhook for repository: myakove/test-repo",
      "hook_id": "f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f",
      "event_type": "pull_request",
      "repository": "myakove/test-repo",
      "github_user": "contributor123",
      "pr_number": 42,
      "task_id": null,
      "task_type": null,
      "task_status": null,
      "token_spend": null
    }
  ],
  "entries_processed": 1542,
  "filtered_count_min": 100,
  "total_log_count_estimate": "12.5K",
  "limit": 100,
  "offset": 0,
  "is_partial_scan": false
}
```

### Response Fields

| Field | Type | Description |
|---|---|---|
| `entries` | `array` | Log entry objects matching all applied filters |
| `entries_processed` | `integer` or `string` | Number of log entries examined. A `"+"` suffix (e.g., `"50000+"`) means the streaming limit was reached and more entries exist |
| `filtered_count_min` | `integer` | Lower bound of total matching entries (`len(entries) + offset`) |
| `total_log_count_estimate` | `string` | Estimated total entries across all log files (e.g., `"12.5K"`, `"1.3M"`, `"0"`, `"Unknown"`) |
| `limit` | `integer` | Echo of the requested `limit` |
| `offset` | `integer` | Echo of the requested `offset` |
| `is_partial_scan` | `boolean` | `true` if the scan stopped before examining all log files |

### Log Entry Object

Each entry in the `entries` array has these fields:

| Field | Type | Description |
|---|---|---|
| `timestamp` | `string` | ISO 8601 timestamp |
| `level` | `string` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `SUCCESS`) |
| `logger_name` | `string` | Name of the Python logger that emitted the entry |
| `message` | `string` | Log message text |
| `hook_id` | `string` or `null` | Webhook delivery ID |
| `event_type` | `string` or `null` | GitHub event type |
| `repository` | `string` or `null` | Repository name (`owner/repo`) |
| `pr_number` | `integer` or `null` | Pull request number |
| `github_user` | `string` or `null` | GitHub username |
| `task_id` | `string` or `null` | Workflow task identifier |
| `task_type` | `string` or `null` | Workflow task type |
| `task_status` | `string` or `null` | Workflow task status |
| `token_spend` | `integer` or `null` | GitHub API token consumption count |

### Error Responses

| Status | Condition |
|---|---|
| `400` | Invalid `limit` (outside 1–10000), negative `offset`, or malformed datetime in `start_time`/`end_time` |
| `404` | Log server is disabled |
| `500` | Log file access errors or internal server errors |

### Examples

Fetch errors from the last 24 hours:

```
GET /logs/api/entries?level=ERROR&start_time=2024-01-14T00:00:00Z&limit=50
```

Fetch logs for a specific PR:

```
GET /logs/api/entries?repository=myakove/test-repo&pr_number=42
```

Paginated access:

```
GET /logs/api/entries?repository=myakove/test-repo&limit=50&offset=100
```

Search for rate limit issues:

```
GET /logs/api/entries?search=rate%20limit&level=WARNING
```

Debug a specific webhook delivery:

```
GET /logs/api/entries?hook_id=f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f
```

> **Note:** Infrastructure logger entries (MCP server, log viewer) without webhook context are automatically excluded from results to reduce noise.

---

## `GET /logs/api/export`

Export filtered logs as a downloadable JSON file. Supports the same filter parameters as `/logs/api/entries`.

### Query Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `format_type` | `string` | `"json"` | Export format. Only `"json"` is supported |
| `hook_id` | `string` | `null` | Filter by webhook delivery ID |
| `pr_number` | `integer` | `null` | Filter by PR number |
| `repository` | `string` | `null` | Filter by repository (`owner/repo`) |
| `event_type` | `string` | `null` | Filter by GitHub event type |
| `github_user` | `string` | `null` | Filter by GitHub username |
| `level` | `string` | `null` | Filter by log level |
| `start_time` | `string` | `null` | ISO 8601 start time |
| `end_time` | `string` | `null` | ISO 8601 end time |
| `search` | `string` | `null` | Full-text search in messages |
| `limit` | `integer` | `10000` | Maximum entries to export. Range: `1`–`100000`. Hard cap: `50000` entries in the export itself |

### Response

Returns a `StreamingResponse` with file download headers:

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |
| `Content-Disposition` | `attachment; filename=webhook_logs_YYYYMMDD_HHMMSS.json` |

### Export File Format

```json
{
  "export_metadata": {
    "generated_at": "2024-01-15T14:30:25.123456+00:00",
    "filters_applied": {
      "repository": "myakove/test-repo",
      "level": "ERROR"
    },
    "total_entries": 156,
    "export_format": "json"
  },
  "log_entries": [
    {
      "timestamp": "2024-01-15T14:30:25.123456",
      "level": "ERROR",
      "logger_name": "webhook_server.app",
      "message": "Container build failed for PR #42",
      "hook_id": "delivery-id-123",
      "repository": "myakove/test-repo",
      "event_type": "pull_request",
      "github_user": "contributor",
      "pr_number": 42,
      "task_id": null,
      "task_type": null,
      "task_status": null,
      "token_spend": null
    }
  ]
}
```

### Error Responses

| Status | Condition |
|---|---|
| `400` | Invalid `format_type` (not `"json"`) or malformed datetime parameters |
| `404` | Log server is disabled |
| `413` | Export limit exceeds `50000` entries |
| `500` | File system or export generation errors |

### Examples

Export all errors for a repository:

```
GET /logs/api/export?format_type=json&repository=myakove/test-repo&level=ERROR
```

Export a month of logs:

```
GET /logs/api/export?format_type=json&start_time=2024-01-01T00:00:00Z&end_time=2024-01-31T23:59:59Z&limit=50000
```

---

## `GET /logs/api/pr-flow/{hook_id}`

Get PR workflow flow visualization data. Analyzes log entries to identify processing stages and timing.

### Path Parameters

| Parameter | Type | Description |
|---|---|---|
| `hook_id` | `string` | Identifier in one of these formats: raw hook ID, `hook-{id}` prefix, `pr-{number}` prefix, or a bare PR number |

### Hook ID Format Resolution

| Input | Interpretation |
|---|---|
| `hook-abc123` | Filters by hook ID `abc123` |
| `pr-42` | Filters by PR number `42` |
| `42` | Filters by PR number `42` |
| `abc123` | Filters by hook ID `abc123` |

### Response Body

```json
{
  "identifier": "hook-abc123",
  "stages": [
    {
      "name": "Webhook Received",
      "timestamp": "2024-01-15T14:30:25.000000",
      "duration_ms": null
    },
    {
      "name": "Validation Complete",
      "timestamp": "2024-01-15T14:30:25.050000",
      "duration_ms": 50
    },
    {
      "name": "Labels Applied",
      "timestamp": "2024-01-15T14:30:26.200000",
      "duration_ms": 1150,
      "error": "Label not found: size/XL"
    },
    {
      "name": "Processing Complete",
      "timestamp": "2024-01-15T14:30:28.000000",
      "duration_ms": 1800
    }
  ],
  "total_duration_ms": 3000,
  "success": true
}
```

### Response Fields

| Field | Type | Description |
|---|---|---|
| `identifier` | `string` | Echo of the `hook_id` path parameter |
| `stages` | `array` | Detected workflow stages in chronological order |
| `total_duration_ms` | `integer` | Total processing duration in milliseconds |
| `success` | `boolean` | `true` if no `ERROR`-level log entries were found |
| `error` | `string` | Present only when `success` is `false`; first error message |

### Workflow Stages

Stages are detected by matching log messages against these patterns:

| Stage Name | Matches Log Messages Containing |
|---|---|
| Webhook Received | `Processing webhook` |
| Validation Complete | `Signature verification successful` or `Processing webhook for` |
| Reviewers Assigned | `Added reviewer`, `OWNERS file`, or `reviewer assignment` |
| Labels Applied | `label` or `tag` |
| Checks Started | `check`, `test`, or `build` |
| Checks Complete | `check.*complete`, `test.*pass`, or `build.*success` |
| Processing Complete | `completed successfully` or `processing complete` |

> **Note:** Not all stages appear in every response. Only stages with matching log entries are included.

### Error Responses

| Status | Condition |
|---|---|
| `400` | Invalid hook ID format |
| `404` | No log data found for the given hook ID or PR number |
| `500` | Internal server error |

### Example

```
GET /logs/api/pr-flow/hook-f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f
```

```
GET /logs/api/pr-flow/pr-42
```

---

## `GET /logs/api/workflow-steps/{hook_id}`

Get a detailed timeline of individual workflow steps for a webhook processing event. Prioritizes structured JSON logs and falls back to text log parsing.

### Path Parameters

| Parameter | Type | Description |
|---|---|---|
| `hook_id` | `string` | GitHub webhook delivery ID (`X-GitHub-Delivery` header value) |

### Response Body

```json
{
  "hook_id": "test-hook-123",
  "start_time": "2025-01-05T10:00:00.000000Z",
  "total_duration_ms": 5000,
  "step_count": 3,
  "steps": [
    {
      "timestamp": "2025-01-05T10:00:01.000000Z",
      "step_name": "clone_repository",
      "message": "clone_repository: completed (1500ms)",
      "level": "INFO",
      "repository": "org/test-repo",
      "event_type": "pull_request",
      "pr_number": 456,
      "task_id": "clone_repository",
      "task_type": null,
      "task_status": "completed",
      "duration_ms": 1500,
      "error": null,
      "step_details": {
        "timestamp": "2025-01-05T10:00:01.000000Z",
        "status": "completed",
        "duration_ms": 1500
      },
      "relative_time_ms": 1000
    },
    {
      "timestamp": "2025-01-05T10:00:03.500000Z",
      "step_name": "apply_labels",
      "message": "apply_labels: failed - Label not found",
      "level": "ERROR",
      "repository": "org/test-repo",
      "event_type": "pull_request",
      "pr_number": 456,
      "task_id": "apply_labels",
      "task_type": null,
      "task_status": "failed",
      "duration_ms": 200,
      "error": {
        "type": "ValueError",
        "message": "Label not found"
      },
      "step_details": {
        "timestamp": "2025-01-05T10:00:03.500000Z",
        "status": "failed",
        "duration_ms": 200,
        "error": {
          "type": "ValueError",
          "message": "Label not found"
        }
      },
      "relative_time_ms": 3500
    }
  ],
  "token_spend": 35,
  "event_type": "pull_request",
  "action": "opened",
  "repository": "org/test-repo",
  "sender": "test-user",
  "pr": {
    "number": 456,
    "title": "Test PR",
    "url": "https://github.com/org/test-repo/pull/456"
  },
  "success": false,
  "error": null
}
```

### Response Fields

| Field | Type | Description |
|---|---|---|
| `hook_id` | `string` | Webhook delivery ID |
| `start_time` | `string` or `null` | ISO 8601 timestamp of processing start |
| `total_duration_ms` | `integer` | Total processing duration in milliseconds |
| `step_count` | `integer` | Number of workflow steps |
| `steps` | `array` | Ordered list of step objects (see below) |
| `token_spend` | `integer` or `null` | GitHub API token consumption count |
| `event_type` | `string` or `null` | GitHub event type (`pull_request`, `check_run`, etc.) |
| `action` | `string` or `null` | Event action (`opened`, `synchronize`, etc.) |
| `repository` | `string` or `null` | Repository name (`owner/repo`) |
| `sender` | `string` or `null` | GitHub username who triggered the event |
| `pr` | `object` or `null` | PR info with `number`, `title`, `url` |
| `success` | `boolean` or `null` | Whether webhook processing succeeded |
| `error` | `string` or `null` | Error message if processing failed |

### Step Object Fields

| Field | Type | Description |
|---|---|---|
| `timestamp` | `string` | ISO 8601 timestamp when step was recorded |
| `step_name` | `string` | Step identifier (e.g., `clone_repository`, `assign_reviewers`) |
| `message` | `string` | Human-readable step summary |
| `level` | `string` | Derived log level: `DEBUG` for `started`, `INFO` for `completed`, `ERROR` for `failed` |
| `repository` | `string` or `null` | Repository name |
| `event_type` | `string` or `null` | Event type |
| `pr_number` | `integer` or `null` | PR number |
| `task_id` | `string` | Same as `step_name` |
| `task_type` | `string` or `null` | Task type from the JSON log |
| `task_status` | `string` | Step status: `started`, `completed`, `failed`, or `unknown` |
| `duration_ms` | `integer` or `null` | Step execution duration in milliseconds |
| `error` | `object` or `null` | Error details with `type` and `message` fields |
| `step_details` | `object` | Raw step data from JSON log |
| `relative_time_ms` | `integer` | Milliseconds elapsed since `start_time` |

### Error Responses

| Status | Condition |
|---|---|
| `400` | Invalid hook ID |
| `404` | No workflow data or steps found for the hook ID |
| `500` | Malformed log entry or internal server error |

### Example

```
GET /logs/api/workflow-steps/f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f
```

---

## `GET /logs/api/step-logs/{hook_id}/{step_name}`

Retrieve log entries that occurred during a specific workflow step's execution time window.

> **Note:** This endpoint requires access from a trusted network (private IP ranges, loopback, or link-local addresses). Requests from public IPs receive a `403` response.

### Path Parameters

| Parameter | Type | Constraints | Description |
|---|---|---|---|
| `hook_id` | `string` | 1–100 characters | GitHub webhook delivery ID |
| `step_name` | `string` | 1–100 characters | Workflow step name (e.g., `clone_repository`, `webhook_routing`) |

### Response Body

```json
{
  "step": {
    "name": "clone_repository",
    "status": "completed",
    "timestamp": "2025-01-05T10:00:01.000000Z",
    "duration_ms": 1500,
    "error": null
  },
  "logs": [
    {
      "timestamp": "2025-01-05T10:00:01.100000",
      "level": "INFO",
      "logger_name": "webhook_server.app",
      "message": "Cloning repository org/test-repo",
      "hook_id": "test-hook-123",
      "event_type": "pull_request",
      "repository": "org/test-repo",
      "github_user": null,
      "pr_number": 456,
      "task_id": null,
      "task_type": null,
      "task_status": null,
      "token_spend": null
    }
  ],
  "log_count": 1
}
```

### Response Fields

| Field | Type | Description |
|---|---|---|
| `step` | `object` | Step metadata |
| `step.name` | `string` | Step name |
| `step.status` | `string` | Step status (`started`, `completed`, `failed`, `unknown`) |
| `step.timestamp` | `string` | ISO 8601 timestamp |
| `step.duration_ms` | `integer` or `null` | Step execution duration. When `null`, a 60-second default window is used |
| `step.error` | `object` or `null` | Error details if the step failed |
| `logs` | `array` | Log entries within the step's execution time window (max 500 entries) |
| `log_count` | `integer` | Number of log entries returned |

### Error Responses

| Status | Condition |
|---|---|
| `403` | Request from untrusted (public) IP address |
| `404` | Hook ID not found, or step name not found within the hook's workflow steps |
| `500` | Step has no timestamp, or invalid timestamp format |

### Example

```
GET /logs/api/step-logs/test-hook-123/clone_repository
```

---

## `WebSocket /logs/ws`

Real-time log streaming via WebSocket. Monitors log files for new entries and pushes them to connected clients. Supports server-side filtering.

### Connection URL

```
ws://<host>:<port>/logs/ws
```

### Query Parameters

All parameters are optional. When no filters are provided, all new log entries are streamed.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `hook_id` | `string` | `null` | Stream only entries for this webhook delivery ID |
| `pr_number` | `integer` | `null` | Stream only entries for this PR number |
| `repository` | `string` | `null` | Stream only entries for this repository |
| `event_type` | `string` | `null` | Stream only entries for this event type |
| `github_user` | `string` | `null` | Stream only entries for this GitHub user |
| `level` | `string` | `null` | Stream only entries at this log level |

### Connection Lifecycle

1. Client connects to `ws://<host>:<port>/logs/ws?<filters>`
2. Server accepts the connection
3. Server monitors the log directory for new entries
4. Matching entries are sent as JSON messages
5. Connection closes on client disconnect or server shutdown (close code `1001`)
6. On internal error, server closes with code `1011`

### Message Format

Each WebSocket message is a JSON object with the same structure as a [Log Entry Object](#log-entry-object):

```json
{
  "timestamp": "2024-01-15T14:30:25.123456",
  "level": "INFO",
  "logger_name": "webhook_server.app",
  "message": "Processing webhook for repository: myakove/test-repo",
  "hook_id": "f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f",
  "event_type": "pull_request",
  "repository": "myakove/test-repo",
  "github_user": "contributor123",
  "pr_number": 42,
  "task_id": null,
  "task_type": null,
  "task_status": null,
  "token_spend": null
}
```

### Error Messages

If the log directory is not found, the server sends an error object before the stream starts:

```json
{
  "error": "Log directory not found"
}
```

### WebSocket Close Codes

| Code | Meaning |
|---|---|
| `1001` | Server shutdown |
| `1008` | Log server is disabled (`ENABLE_LOG_SERVER` is not `true`) |
| `1011` | Internal server error during streaming |

### Examples

Connect with no filters (stream all entries):

```
ws://localhost:8080/logs/ws
```

Stream only errors for a specific repository:

```
ws://localhost:8080/logs/ws?repository=myakove/test-repo&level=ERROR
```

Monitor a specific PR:

```
ws://localhost:8080/logs/ws?pr_number=42
```

> **Tip:** The server tracks all active WebSocket connections and closes them gracefully during shutdown.

---

## Datetime Format

All datetime parameters and response fields use ISO 8601 format. The server accepts:

| Format | Example |
|---|---|
| UTC with `Z` suffix | `2024-01-15T10:00:00Z` |
| With timezone offset | `2024-01-15T10:00:00+00:00` |
| With microseconds | `2024-01-15T10:00:00.123456` |
| With microseconds and `Z` | `2024-01-15T10:00:00.123456Z` |

Internally, `Z` is converted to `+00:00` for parsing.

---

## Scanning Limits

The API uses memory-efficient streaming with processing caps to prevent resource exhaustion:

| Context | Max Files Scanned | Max Entries Processed |
|---|---|---|
| `/logs/api/entries` (no filters) | 25 | 20,000 |
| `/logs/api/entries` (with filters) | 25 | 50,000 |
| `/logs/api/export` (with filters) | 25 | up to 100,000 |
| `/logs/api/pr-flow/{hook_id}` | 15 | 10,000 |
| `/logs/api/workflow-steps/{hook_id}` | 25 | 50,000 |
| `/logs/api/step-logs/{hook_id}/{step_name}` | 25 | 50,000 |
| Step logs per step | — | 500 (hard cap) |

When the processing limit is reached, `is_partial_scan` is `true` in the entries response and `entries_processed` has a `"+"` suffix.

---

## Related Pages

- See [Using the Log Viewer](using-the-log-viewer.html) for a guide on browsing, searching, and filtering logs through the web UI.
- See [Environment Variables](environment-variables.html) for `ENABLE_LOG_SERVER` and `WEBHOOK_SERVER_DATA_DIR` configuration.
- See [Webhook Events and Handlers](webhook-events-reference.html) for the list of `event_type` values used in filters.
- See [Configuration Reference](configuration-reference.html) for the `logs-server-log-file` config key.

## Related Pages

- [Using the Log Viewer](using-the-log-viewer.html)
- [Environment Variables](environment-variables.html)
- [Webhook Events and Handlers](webhook-events-reference.html)
- [MCP Server for AI Agents](mcp-server-integration.html)
- [Configuration Reference](configuration-reference.html)
