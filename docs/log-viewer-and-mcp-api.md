# Log Viewer and MCP API

Reference for the debugging and AI surfaces of the server: the log viewer HTML page, the `/logs/api/*` JSON routes, the `/logs/ws` live stream, and the `/mcp` Model Context Protocol endpoint.

> **⚠️ Security warning — these endpoints are unauthenticated**
>
> `GET /logs`, every `/logs/api/*` route, the `/logs/ws` WebSocket, and `/mcp` have **no authentication of any kind**. There is no auth middleware, no API key, no bearer token, and no session check anywhere in `webhook_server/app.py`. Anyone who can reach the port can read your webhook logs, which contain repository names, PR numbers, GitHub usernames, delivery IDs, error text, and stack traces.
>
> - **Never expose these paths to the public internet.** No port-forwarding them to a public host, no public load balancer in front of them, no tunnelling service.
> - Deploy on a **trusted network only** — localhost, an internal network, or a VPN.
> - If remote access is required, front the server with a **reverse proxy that adds authentication** (Basic auth, mTLS, oauth2-proxy, an identity-aware proxy) and only allow it to reach `/logs*` and `/mcp`.
> - This is the same rule the project's own `AGENTS.md` states: *"NEVER expose log viewer (`/logs/*`) to public internet — endpoints are unauthenticated; deploy on trusted networks only."*

One route, `GET /logs/api/step-logs/{hook_id}/{step_name}`, additionally requires a **private, loopback, or link-local client IP** via `require_trusted_network`. That is a network-range check on `request.client.host`, not authentication, and it is trivially bypassed by a misconfigured reverse proxy that does not preserve the client address. It is a speed bump, not a control.

## Route summary

| Method | Path | Purpose | Registered when |
| --- | --- | --- | --- |
| `GET` | `/logs` | Log viewer HTML page | `ENABLE_LOG_SERVER=true` |
| `GET` | `/logs/api/entries` | Filtered, paginated log search | Always; returns `404` if disabled |
| `GET` | `/logs/api/export` | Streamed JSON download of filtered logs | Always; returns `404` if disabled |
| `GET` | `/logs/api/pr-flow/{hook_id}` | Stage-by-stage PR flow for one delivery | Always; returns `404` if disabled |
| `GET` | `/logs/api/workflow-steps/{hook_id}` | Step timeline for one delivery | Always; returns `404` if disabled |
| `GET` | `/logs/api/step-logs/{hook_id}/{step_name}` | Log lines emitted during one step | Always; `404` if disabled, `403` from a non-private IP |
| `WS` | `/logs/ws` | Real-time log stream | Endpoint always; closes `1008` if disabled |
| `GET`/`POST`/`DELETE` | `/mcp` | MCP streamable HTTP transport | `ENABLE_MCP_SERVER=true` |
| `GET` | `/static/*` | CSS/JS for the viewer page | Always (mounted directory) |
| `POST` | `http://127.0.0.1:5001/tools/run` | Loopback tool server for the AI sidecar | Always (started by `entrypoint.py`) |

The two flags are read once at import time and the routes are then either registered or not:

```python
LOG_SERVER_ENABLED: bool = os.environ.get("ENABLE_LOG_SERVER") == "true"
MCP_SERVER_ENABLED: bool = os.environ.get("ENABLE_MCP_SERVER") == "true"
```

The value must be the exact lowercase string `true`. `TRUE`, `1`, `yes`, and an unset variable all leave the feature off.

## Enabling the log viewer

Set the flag and restart the server:

```bash
ENABLE_LOG_SERVER=true WEBHOOK_SERVER_DATA_DIR=/path/to/data uv run entrypoint.py
```

For the container, add `ENABLE_LOG_SERVER=true` to the container environment and restart it.

Then browse to `http://127.0.0.1:5000/logs` (the default bind is `0.0.0.0:5000` from `ip-bind`/`port` in `config.yaml`; change the port in the URL if you configured a different one).

`GET /logs` only exists inside the feature-flag block, so with the flag off the path is a plain `404` from the router. The `/logs/api/*` routes are always registered but are wrapped in `require_log_server_enabled`, so they answer:

```json
{"detail": "Log server is disabled. Set ENABLE_LOG_SERVER=true to enable."}
```

with status `404`.

```python
def require_log_server_enabled() -> None:
    """Dependency to ensure log server is enabled before accessing log viewer APIs."""
    if not LOG_SERVER_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Log server is disabled. Set ENABLE_LOG_SERVER=true to enable.",
        )
```

### What the page serves

`LogViewerController.get_log_page()` reads `webhook_server/web/templates/log_viewer.html` and returns it as `HTMLResponse`. If the template cannot be read, the controller returns an inline fallback error page instead of crashing — you get a `200` page that says *"Log Viewer Template Error"*, not a stack trace. Any other failure becomes `500 Internal server error`.

The controller is a process-wide singleton (`get_log_viewer_controller()`), created on first use with a logger writing to the file named by `logs-server-log-file` (default `logs_server.log`).

### Where the data comes from

Everything is read from disk, under `<data-dir>/logs`:

- `webhooks_YYYY-MM-DD.json` — JSONL, one object per line: either `type: "webhook_summary"` (the complete `WebhookContext` for one delivery) or `type: "log_entry"` (an individual log line). Written by `write_webhook_log()` in `webhook_server/utils/structured_logger.py`.
- `*.log` and rotated `*.log.*` — the human-readable text logs produced by `get_logger_with_params()`.

Reads are streamed, never slurped: `_stream_log_entries()` reads at most 25 files (JSON webhook files first, then by mtime newest-first) and caps at 20 000 entries for unfiltered queries or 50 000 when any filter is set. Infrastructure loggers — `mcp.server.streamable_http`, `logs_server.log`, `log_parser`, `mcp_server.log` — are skipped, because they carry no `hook_id` or `event_type` and would flood unfiltered results.

## `GET /logs/api/entries`

Filtered, paginated search over historical logs. Operation ID `get_log_entries`.

```http
GET /logs/api/entries?repository=owner/repo&level=ERROR&limit=100&offset=0
```

### Query parameters

| Parameter | Type | Default | Notes |
| --- | --- | --- | --- |
| `hook_id` | str | — | GitHub delivery ID (`X-GitHub-Delivery`) |
| `pr_number` | int | — | Pull request number |
| `repository` | str | — | `owner/repo` |
| `event_type` | str | — | e.g. `pull_request`, `push`, `issues`, `issue_comment` |
| `github_user` | str | — | Username who triggered the event |
| `level` | str | — | Exact, case-sensitive match on the entry level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `COMPLETED` for JSON summary entries. Not validated — a typo matches nothing. |
| `start_time` | str | — | ISO 8601, e.g. `2024-01-15T10:00:00Z` |
| `end_time` | str | — | ISO 8601 |
| `search` | str | — | Case-insensitive substring of the log message |
| `limit` | int | `100` | `1`–`10000` (FastAPI `Query`, rejects out-of-range with `422`) |
| `offset` | int | `0` | `>= 0` |

All filters are ANDed and matched exactly against the parsed entry fields; `search` is a case-insensitive substring of `message`. Time filters compare against the entry timestamp — `start_time` keeps entries at or after it, `end_time` keeps entries at or before it. `Z` suffixes are accepted (`parse_datetime_string()` rewrites a trailing `Z` to `+00:00` before calling `datetime.fromisoformat`); an unparseable value is a `400` with `Invalid start_time format: ...`.

### Response

```json
{
  "entries": [
    {
      "timestamp": "2024-01-15T14:30:25.123456+00:00",
      "level": "INFO",
      "logger_name": "GithubWebhook",
      "message": "owner/repo [pull_request][72d3162e-cc78-11e3-81ab-4c9367dc0958][alice][PR 42]: Processing webhook",
      "hook_id": "72d3162e-cc78-11e3-81ab-4c9367dc0958",
      "event_type": "pull_request",
      "repository": "owner/repo",
      "pr_number": 42,
      "github_user": "alice",
      "task_id": null,
      "task_type": null,
      "task_status": null,
      "token_spend": null
    }
  ],
  "entries_processed": 1542,
  "filtered_count_min": 100,
  "total_log_count_estimate": "48210",
  "limit": 100,
  "offset": 0,
  "is_partial_scan": false
}
```

Read the counters before you trust the result set:

- `entries_processed` — entries actually examined. A `"50000+"` string means the streaming cap was hit and more entries exist.
- `is_partial_scan` — `true` when the scan stopped at the cap rather than reading everything.
- `filtered_count_min` — `len(entries) + offset`, the **lower bound** of matches. There is no exact total count; paging with `offset` is the way to walk a large result set.
- `total_log_count_estimate` — string, sampled from file sizes across the first 10 `.log` files (~200 bytes per line assumed). It is a size for the stats bar, not a count you can page against.

Errors: `400` for an unparseable timestamp or a limit outside 1–10000 reaching the controller, `422` from FastAPI for a `limit`/`offset` outside the declared `Query` bounds. Damaged log data is not an error: a log file that cannot be read is logged at `WARNING` and skipped by the streamer, and a JSONL line that is not valid JSON is dropped by `get_raw_json_entry()`. A corrupt file therefore yields fewer entries, not a `500`.

## `GET /logs/api/export`

The same filters, streamed to a file download. Operation ID `export_logs`.

```http
GET /logs/api/export?format_type=json&level=ERROR&start_time=2024-01-01T00:00:00Z&limit=10000
```

| Parameter | Type | Default | Notes |
| --- | --- | --- | --- |
| `format_type` | str | `json` | Constrained by the pattern `^json$`; anything else is `422` |
| `hook_id`, `pr_number`, `repository`, `event_type`, `github_user`, `level`, `start_time`, `end_time`, `search` | — | — | Identical to `/logs/api/entries` |
| `limit` | int | `10000` | `1`–`100000` at the query layer |

The controller rejects `limit > 50000` with `413` ("Result set too large"), even though the query parameter accepts up to 100 000.

Response headers:

```
content-type: application/json
content-disposition: attachment; filename=webhook_logs_20240115_143025.json
```

Note the filename pattern: `webhook_logs_%Y%m%d_%H%M%S.json`.

Body:

```json
{
  "export_metadata": {
    "generated_at": "2024-01-15T14:30:25.123456+00:00",
    "filters_applied": {"repository": "owner/repo", "level": "ERROR", "limit": 10000},
    "total_entries": 156,
    "export_format": "json"
  },
  "log_entries": [ "...LogEntry objects, same shape as /logs/api/entries..." ]
}
```

`filters_applied` omits keys you did not set. The body is built in memory and yielded as a single chunk through a `StreamingResponse`; it is not incrementally streamed to the client.

Errors: `400` for a non-`json` `format_type` reaching the controller or a bad timestamp, `413` for `limit > 50000`, `422` for a `format_type` that fails the pattern or a `limit` outside `1`–`100000`, `500` on generation failure.

## `GET /logs/api/pr-flow/{hook_id}`

Stage-level flow analysis for one delivery or PR. Operation ID `get_pr_flow_data`.

```http
GET /logs/api/pr-flow/72d3162e-cc78-11e3-81ab-4c9367dc0958
```

`hook_id` is the only parameter, and it is flexible — the controller branches on the value:

| Form | Interpreted as |
| --- | --- |
| `hook-<id>` | Delivery ID, with the `hook-` prefix stripped |
| `pr-42` | PR number `42` |
| `42` | PR number `42` (bare digits) |
| anything else | Delivery ID as given |

Entries are streamed from at most 15 files / 10 000 entries and filtered by whichever identifier was recognised. Stages are then matched by regex over the log messages (`WORKFLOW_STAGE_PATTERNS`): Webhook Received, Validation Complete, Reviewers Assigned, Labels Applied, Checks Started, Checks Complete, Processing Complete.

```json
{
  "identifier": "72d3162e-cc78-11e3-81ab-4c9367dc0958",
  "stages": [
    {"name": "Webhook Received", "timestamp": "2024-01-15T14:30:25.123456+00:00", "duration_ms": null},
    {"name": "Checks Complete", "timestamp": "2024-01-15T14:31:02.500000+00:00", "error": "build failed"}
  ],
  "total_duration_ms": 37376,
  "success": false,
  "error": "build failed"
}
```

Each stage carries `name`, `timestamp`, `duration_ms` (time since the previous matched stage; `null` on the first stage), and `error` when the matched entry was at `ERROR` level. `error` at the top level is the first error message found.

Errors: `404` when no log entries match, `400` when `pr-<x>` is not a valid integer, `500` on parse failure.

## `GET /logs/api/workflow-steps/{hook_id}`

The step timeline behind the viewer's flow modal. Operation ID `get_workflow_steps`.

```http
GET /logs/api/workflow-steps/72d3162e-cc78-11e3-81ab-4c9367dc0958
```

Only parameter: `hook_id`, the delivery ID.

Data source, in order:

1. **JSON summaries first.** `_stream_json_log_entries()` scans `webhooks_*.json` for a `type: "webhook_summary"` entry whose `hook_id` matches. That entry is the serialized `WebhookContext` (see [context](#hook-id-and-the-workflow-context)), and `_transform_json_entry_to_timeline()` reshapes it.
2. **Text log fallback.** If no JSON entry matches — or the JSON path raises **any** `HTTPException`, including `500 Malformed log entry` — `get_workflow_steps()` re-reads the text `.log` files and reconstructs the timeline from `logger.step` lines. This path also recovers `token_spend`, falling back to parsing it out of the message text.

Response from the JSON path:

```json
{
  "hook_id": "72d3162e-cc78-11e3-81ab-4c9367dc0958",
  "start_time": "2024-01-15T14:30:25.123456+00:00",
  "total_duration_ms": 45230,
  "step_count": 12,
  "steps": [
    {
      "step_name": "webhook_routing",
      "timestamp": "2024-01-15T14:30:25.156789+00:00",
      "duration_ms": 25001,
      "status": "completed",
      "error": null
    }
  ],
  "token_spend": 5,
  "event_type": "check_run",
  "action": "created",
  "repository": "owner/repo",
  "sender": "alice",
  "pr": {"number": 225},
  "success": true,
  "error": null
}
```

`pr` is `null` when the delivery had no pull request. `token_spend` is only present when the delivery recorded one.

A broken summary never yields partial data here — but it also never surfaces as a `500`. Three cases, and they are not the same:

| Situation | JSON path | What the endpoint returns |
| --- | --- | --- |
| The summary line is not valid JSON | `get_raw_json_entry()` catches `json.JSONDecodeError` and returns `None`; the line is dropped silently, no error raised | Behaves exactly as if no summary existed: `404` from the JSON path, then the text fallback |
| No `webhook_summary` entry for the `hook_id` inside the scanned window | `404 No JSON log entry found for hook ID: ...` | Text fallback |
| The summary parses but fails structural validation — missing `timing`, missing `timing.started_at` or `timing.duration_ms`, missing `workflow_steps`, a non-dict `pr`, or a step without a `timestamp` | `_transform_json_entry_to_timeline()` raises `ValueError`; `get_workflow_steps_json()` converts it to `500 Malformed log entry` | That `500` is **swallowed** and the request falls through to the text logs. You get a reconstructed `200` timeline in the text-fallback shape, or `404 No data found for hook ID: ...` / `404 No workflow steps found for hook ID: ...` if the text logs do not cover the delivery |

The swallow is the backward-compatibility path, and it is unconditional — it does not distinguish a missing summary from a corrupt one:

```python
try:
    # First try JSON logs (more efficient and complete)
    try:
        return await self.get_workflow_steps_json(hook_id)
    except HTTPException:
        # Fall back to text log parsing for backward compatibility
        pass
```

So `500 Malformed log entry` is **not** a response this endpoint can produce. It belongs to `get_workflow_steps_json()` and is only reachable through `/logs/api/step-logs/{hook_id}/{step_name}`, which calls that method directly. To diagnose a corrupt summary, look for `Malformed log entry for hook ID: ...` in `logs_server.log` — the `logger.exception()` fires even though the request does not fail.

The text-fallback shape differs: steps carry `message`, `level`, `relative_time_ms`, `repository`, `event_type`, `pr_number`, `task_id`, `task_type`, `task_status` instead of the JSON-path fields, and the top level has only `hook_id`, `start_time`, `total_duration_ms`, `step_count`, `steps` (plus `token_spend` when found).

Errors: `404` when neither path finds data for the ID — `No data found for hook ID: ...` when the text logs hold nothing for it, `No workflow steps found for hook ID: ...` when they hold entries but no `logger.step` lines; `400` for an unusable identifier; `500 Internal server error` for an unexpected failure. A malformed JSON summary does **not** produce a `500` here — see the table above.

## `GET /logs/api/step-logs/{hook_id}/{step_name}`

The log lines written while one workflow step ran. Operation ID `get_step_logs`. This is the one route with a network restriction.

```http
GET /logs/api/step-logs/72d3162e-cc78-11e3-81ab-4c9367dc0958/webhook_routing
```

| Parameter | In | Constraints |
| --- | --- | --- |
| `hook_id` | path | 1–100 characters |
| `step_name` | path | 1–100 characters — must equal a `step_name` from the timeline above, e.g. `webhook_routing`, `clone_repository` |

The step is located in the workflow timeline, then a time window is computed: `step.timestamp` to `step.timestamp + duration_ms`, or a 60 000 ms default window when the step has no recorded duration. Text `.log` entries with the same `hook_id` inside that window are collected, capped at **500 entries** (`_MAX_STEP_LOGS`).

```json
{
  "step": {
    "name": "webhook_routing",
    "status": "completed",
    "timestamp": "2024-01-15T14:30:25.156789+00:00",
    "duration_ms": 25001,
    "error": null
  },
  "logs": [ "...LogEntry objects..." ],
  "log_count": 42
}
```

This route reads the JSON summaries only. It calls `get_workflow_steps_json()` directly, with no text-log fallback, which makes it the one endpoint that can actually return `500 Malformed log entry`: a matching summary that fails structural validation propagates the error instead of being abandoned. A `hook_id` with no summary at all gets `404 No JSON log entry found for hook ID: ...` — note that is *not* the same message the workflow-steps route returns, and that route may still serve the delivery from text logs.

Errors: `404` if no JSON summary matches the hook ID or the step name is not in the timeline, `403` when the client IP is not private/loopback/link-local or cannot be determined, `500 Malformed log entry` for a summary that parses but fails validation, `500` if the step has no usable timestamp or its timestamp is unparseable.

### The trusted-network check

```python
async def require_trusted_network(request: Request) -> None:
    ...
    is_trusted = client_ip.is_private or client_ip.is_loopback or client_ip.is_link_local
    if not is_trusted:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: ...")
```

The code's own docstring says it plainly: this check *"can be bypassed if the server is behind a reverse proxy that doesn't properly set X-Forwarded-For headers."* Treat it as defence in depth, not as the reason you can expose the server.

## `WS /logs/ws`

Real-time streaming of new log lines as they are written.

```javascript
const ws = new WebSocket(`ws://${window.location.host}/logs/ws?hook_id=72d3162e-cc78-11e3-81ab-4c9367dc0958&level=ERROR`);
ws.onmessage = (event) => console.log(JSON.parse(event.data));
```

| Parameter | Type | Notes |
| --- | --- | --- |
| `hook_id` | str | Delivery ID |
| `pr_number` | int | PR number |
| `repository` | str | `owner/repo` |
| `event_type` | str | GitHub event type |
| `github_user` | str | Triggering username |
| `level` | str | Log level |

These are query parameters, not message frames. There is no `start_time`, `end_time`, or `search` on the socket — the live stream only supports the six filters above, unlike the REST search.

Behaviour:

- The handler checks `LOG_SERVER_ENABLED` itself, because WebSocket routes do not take FastAPI dependencies. If disabled, the connection is closed with code **1008** and reason `"Log server is disabled"`.
- Otherwise the socket is accepted, added to the controller's connection set, and `LogParser.monitor_log_directory()` tails the log directory.
- Each new entry is pushed as a single JSON object with the same `LogEntry.to_dict()` shape used by the REST routes.
- With no filters, every entry is sent. With any filter set, `LogFilter.filter_entries()` decides per entry.
- If the log directory does not exist, the server sends `{"error": "Log directory not found"}` and returns.
- On an internal error the server closes with code **1011**. On shutdown the controller closes all open sockets with **1001** (`Server shutdown`) via `LogViewerController.shutdown()`.
- Client disconnects (`WebSocketDisconnect`) are normal and logged at `INFO`; the socket is removed from the connection set in a `finally` block.

## MCP server

Enabled with `ENABLE_MCP_SERVER=true`. One route, registered by hand:

```python
FASTAPI_APP.add_api_route(
    "/mcp",
    handle_mcp_streamable_http,
    methods=["GET", "POST", "DELETE"],
    include_in_schema=False,
    operation_id="mcp_http",
)
```

`include_in_schema=False` keeps the endpoint out of `/openapi.json`, `/docs`, and the MCP tool list.

### Setup sequence

1. `_initialize_mcp(FASTAPI_APP)` runs at import time, but every import is lazy — `fastapi_mcp`, `fastapi_mcp.transport.http`, and `mcp.server.streamable_http_manager`. A missing dependency logs `Failed to initialize MCP server; continuing without MCP` and leaves the globals as `None`; webhook processing is never blocked by a broken MCP install.
2. `FastApiMCP(app, exclude_tags=["mcp_exclude"])` wraps the FastAPI app. Because this runs at the end of `app.py`, the routes registered above it are the ones that get wrapped.
3. The HTTP transport is `FastApiHttpSessionManager(mcp_server=mcp.server, event_store=None, json_response=True)` — **stateless**, no event store, JSON responses. There is no session handshake.
4. The `/mcp` route is registered **even if the above failed**, so a broken MCP install answers `500` (`MCP server not initialized`) rather than a misleading `404`.
5. The real `StreamableHTTPSessionManager` is created during the FastAPI lifespan, with `stateless=True`, and run as a background task tracked in `_background_tasks`. A failure there logs, tears the manager down, and leaves the rest of the app running.

### Exposed tools

`FastApiMCP` converts every OpenAPI operation into a tool named after its `operation_id`, then drops anything tagged `mcp_exclude`. With both flags on, the tool list is:

| Tool | Backing route |
| --- | --- |
| `healthcheck` | `GET /webhook_server/healthcheck` |
| `get_log_viewer_page` | `GET /logs` (present only when `ENABLE_LOG_SERVER=true`) |
| `get_log_entries` | `GET /logs/api/entries` |
| `export_logs` | `GET /logs/api/export` |
| `get_pr_flow_data` | `GET /logs/api/pr-flow/{hook_id}` |
| `get_workflow_steps` | `GET /logs/api/workflow-steps/{hook_id}` |
| `get_step_logs` | `GET /logs/api/step-logs/{hook_id}/{step_name}` |

`POST /webhook_server` carries `tags=["mcp_exclude"]` and is therefore **not** exposed as a tool — an AI client cannot post webhooks through MCP. `/mcp` itself is excluded by `include_in_schema=False`. The `/static` mount and the WebSocket are not OpenAPI operations and are not tools either.

### Client configuration

Because the transport is stateless and JSON-only, point a Streamable HTTP MCP client at the base URL:

```json
{
  "mcpServers": {
    "github-webhook-server": {
      "type": "http",
      "url": "http://127.0.0.1:5000/mcp"
    }
  }
}
```

`GET` and `DELETE` are accepted for the session-management verbs, but with `stateless=True` no session state is kept between requests.

> **⚠️ No authentication.** `app.py` says so at the registration site: *"MCP server runs without auth — deploy only on trusted networks (VPN, internal), never expose to public internet - use reverse proxy with auth for external access."* Any MCP client that can reach the port gets the log-reading tools listed above. Same rule as `/logs`: trusted network, or an authenticating reverse proxy.

### Logging

When MCP is enabled, log lines from `mcp.server.streamable_http` are routed to the file named by `mcp-log-file` (default `mcp_server.log`) instead of the main webhook log, by attaching a handler and setting `propagate = False`. A `logging.Filter` suppresses the `ClosedResourceError` "Error in message router" noise that appears when clients disconnect mid-stream.

## Loopback tool server (not FastAPI)

`webhook_server/web/tool_server.py` is a separate `aiohttp` app started by `entrypoint.py` on its own daemon thread and event loop, bound to **`127.0.0.1` only**:

```python
TOOL_SERVER_PORT = 5001
...
app.router.add_post("/tools/run", handle_tool_request)
site = web.TCPSite(runner, "127.0.0.1", port)
```

It is the git-tool bridge for AI-assisted conflict resolution (`runner_handler.py` builds `http://127.0.0.1:5001/tools/run` per tool). It is unreachable from other hosts by design; do not change the bind address.

### `POST 127.0.0.1:5001/tools/run`

JSON body:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `tool` | str | Yes | Registry key; unknown key returns `404` listing the valid ones |
| `cwd` | str | Yes | Repository working directory, substituted into `{cwd}` in the command prefix |
| `args` | str | No | Argument string, `shlex.split`; empty is fine |
| `timeout` | int \| null | No | Clamped to 1–300 s; falls back to the tool default (`30`) when unparseable |

Response: `{"success": bool, "output": "..."}` — stdout, or stderr when stdout is empty, truncated to 50 000 characters. A timeout returns `{"success": false, "output": "Command timed out after Ns"}`.

### Tool registry

| Tool | Command | Blocked flags | Success exit codes |
| --- | --- | --- | --- |
| `git_diff` | `git -C {cwd} diff` | `--no-index`, `--output`, `--raw` | `0`, `1` |
| `git_log` | `git -C {cwd} log` | — | `0` |
| `git_show` | `git -C {cwd} show` | — | `0` |
| `git_status` | `git -C {cwd} status` | — | `0` |
| `git_rm` | `git -C {cwd} rm` | `--cached`, `-r`, `-f`, `--force`, `--pathspec-from-file` | `0` |

Blocked flags are rejected with `403`, including combined short options: `-rf` is caught by expanding it into `-r -f` and checking each character against the blocked short flags. This is what keeps the AI conflict-resolution path read-only apart from scoped `git rm` and file edits — no `bash`, no recursive delete.

Status codes: `400` for a non-JSON body, a missing `tool`/`cwd`, or a non-string `cwd`/`args`; `403` for a blocked flag or a disallowed subcommand; `404` for an unknown tool name; `200` for anything the subprocess produced.

## `hook_id` and the workflow context

Everything under `/logs/api/*` that takes a `hook_id` is keyed by the GitHub delivery ID — the `X-GitHub-Delivery` header of the original webhook. Per request the server builds a `WebhookContext` (`webhook_server/utils/context.py`) and, at the end of processing, writes it as one `type: "webhook_summary"` line to `webhooks_YYYY-MM-DD.json`. That summary is what the pr-flow and workflow-steps routes read.

The fields the viewer surfaces:

| Field | Meaning |
| --- | --- |
| `hook_id` | Delivery ID |
| `event_type`, `action` | GitHub event and action |
| `repository`, `repository_full_name` | Target repository |
| `sender`, `api_user` | Who triggered it; which API identity acted |
| `pr` | `{number, title, author}` or `null` |
| `timing.started_at`, `timing.completed_at`, `timing.duration_ms` | Wall-clock timing |
| `workflow_steps` | Map of step name → `{timestamp, status, error, duration_ms}` |
| `token_spend` | GitHub API calls consumed by this delivery |
| `initial_rate_limit`, `final_rate_limit` | Rate limit before and after |
| `success`, `error` | Overall outcome and top-level error with traceback |
| `summary` | One-line human-readable result |

Steps are recorded through `start_step()` / `complete_step()` / `fail_step()`. A step that started but never completed is marked `status: "started"` with `duration_ms: null` in the summary line, which the timeline reads as "incomplete".

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `/logs` is `404` | `ENABLE_LOG_SERVER` is not exactly `true`, or the server was not restarted after setting it |
| `/logs/api/*` returns `{"detail": "Log server is disabled..."}` | Flag off — the routes exist but the dependency rejects them |
| `/logs/api/step-logs/...` returns `403` | Client IP is not private/loopback/link-local, or the proxy is rewriting the client address |
| The viewer page renders "Log Viewer Template Error" | `templates/log_viewer.html` could not be read; a built-in fallback page is served |
| The page loads but shows no entries | `<data-dir>/logs` is empty or missing. Startup fails if `webhook_server/web/static/` is missing, not the logs directory — check `WEBHOOK_SERVER_DATA_DIR`. |
| `is_partial_scan: true` or a `"...+"` count | The 20 000 / 50 000 entry streaming cap was hit. Add `hook_id`, `repository`, or a time range. |
| `404` from `workflow-steps` for a delivery you can see in `/logs/api/entries` | The JSON summary has not been written yet (it is written at the end of processing), the delivery fell outside the 25-file / 50 000-entry window, or the summary is corrupt and the text logs have no `logger.step` lines to replace it — all three end in the same `404` |
| `500 Malformed log entry` from `step-logs` but a `200` (or `404`) from `workflow-steps` for the same ID | The two routes disagree by design: `step-logs` reads the JSON summary directly and surfaces the validation error, `workflow-steps` falls back to text logs. Check `logs_server.log` for `Malformed log entry for hook ID: ...` |
| `/mcp` returns `500` "MCP server not initialized" | `fastapi_mcp` failed to import or the session manager failed to start. Check `mcp_server.log` and the startup logs. |

## See also

- [Debug with the Log Viewer](debug-with-the-log-viewer.html) — task walkthrough for the UI.
- [Webhook and Health API](webhook-and-health-api.html) — the always-on public surface and the IP allowlist.
- [Environment Variables](environment-variables.html) — the full flag list.
- [Configuration Reference](configuration-reference.html) — `logs-server-log-file`, `mcp-log-file`, `mask-sensitive-data`.
- [Secure Webhooks and Pull Requests](secure-webhooks-and-pull-requests.html) — deployment and hardening guidance.
