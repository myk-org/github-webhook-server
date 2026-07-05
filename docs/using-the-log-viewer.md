# Using the Log Viewer

Browse, search, filter, and export webhook processing logs through a built-in web UI so you can debug failed webhooks, monitor PR workflows, and audit event history without SSH-ing into your server.

## Prerequisites

- A running github-webhook-server instance (see [Getting Started](quickstart.html))
- The `ENABLE_LOG_SERVER` environment variable set to `true`

## Quick Start

Add `ENABLE_LOG_SERVER=true` to your environment and open `/logs` in a browser:

```yaml
# docker-compose.yaml (environment section)
environment:
  - ENABLE_LOG_SERVER=true
```

For a non-Docker setup:

```bash
ENABLE_LOG_SERVER=true WEBHOOK_SERVER_DATA_DIR=/path/to/data uv run entrypoint.py
```

Then navigate to `http://your-server:5000/logs`.

> **Warning:** The log viewer endpoints are **unauthenticated**. Deploy only on trusted networks (VPN, internal) or behind a reverse proxy with authentication. Never expose `/logs` to the public internet.

## Enabling the Log Viewer

Set `ENABLE_LOG_SERVER=true` as an environment variable before starting the server. The value must be the literal string `true` — any other value (including `True`, `1`, or `yes`) leaves the log viewer disabled.

When the log viewer is disabled, all log viewer paths (`/logs`, `/logs/api/*`, `/logs/ws`) return a 404 response.

The viewer reads log files from `${WEBHOOK_SERVER_DATA_DIR}/logs`. If you have not set `WEBHOOK_SERVER_DATA_DIR`, the default is `/home/podman/data`, so logs are read from `/home/podman/data/logs`.

See [Environment Variables](environment-variables.html) for the full list of environment variables.

## Configuring Log Files and Masking

The log viewer uses the same logging configuration as the rest of the server. Add these keys to your `config.yaml`:

```yaml
log-level: INFO
log-file: webhook-server.log
logs-server-log-file: logs_server.log
mask-sensitive-data: true
```

| Key | What it controls | Default |
|---|---|---|
| `log-level` | Verbosity of logs (`INFO` or `DEBUG`) | `INFO` |
| `log-file` | Main log file name | `webhook-server.log` |
| `logs-server-log-file` | Separate log file for the log viewer itself | `logs_server.log` |
| `mask-sensitive-data` | Redact tokens, passwords, and secrets from log output | `true` |

> **Tip:** You can override `mask-sensitive-data` per repository in `config.yaml` for debugging a specific repo without exposing secrets across all repositories.

See [Configuration Reference](configuration-reference.html) for all available options.

## Browsing Logs in the Web UI

Open `http://your-server:5000/logs` to see the log viewer interface. The page loads the most recent log entries automatically.

### Filtering

Use the filter bar at the top of the page to narrow down results:

| Filter | Description | Example |
|---|---|---|
| **Search** | Free-text search across log messages (case-insensitive) | `container build failed` |
| **Hook ID** | GitHub webhook delivery ID (`X-GitHub-Delivery` header) | `f4b3c2d1-a9b8-...` |
| **PR #** | Pull request number | `42` |
| **Repository** | Repository in `owner/repo` format | `myorg/myrepo` |
| **User** | GitHub username who triggered the event | `octocat` |
| **Level** | Log severity level | `ERROR`, `WARNING`, `INFO`, `DEBUG` |
| **Start Time / End Time** | Restrict results to a time range | Datetime picker |
| **Results Limit** | Maximum entries to return | `100`, `500`, `1000`, `5000`, `10000` |

Filters are applied as you type (with a 300ms debounce). The server re-queries with each filter change to provide accurate, backend-filtered results.

Click **Clear Filters** to reset all filters at once.

### Reading Log Entries

Each log entry shows three columns:

1. **Timestamp** — when the event occurred (displayed in your local timezone)
2. **Level** — severity badge (`INFO`, `WARNING`, `ERROR`, `SUCCESS`, `STEP`, `DEBUG`, `COMPLETED`)
3. **Message** — the log message followed by clickable metadata tags for Hook ID, PR number, repository, and user

### Statistics Bar

Below the filters, three counters help you understand the dataset:

- **Shown** — number of entries currently displayed
- **Total** — estimated total log entries across all log files
- **Scanned** — entries the server examined for the last query (a `+` suffix and "(partial scan)" label indicate more logs exist beyond what was scanned)

## Real-Time Log Streaming

Click **Start Real-time** to open a WebSocket connection that streams new log entries as they arrive. The current filter settings are applied to the stream — you only see entries matching your active filters.

Click **Stop Real-time** to disconnect. The connection status indicator at the top of the page shows whether streaming is active.

> **Note:** Enable **Auto-scroll** (toggle in the controls) to keep the newest entries visible as they arrive. Disable it when you need to read through older entries without being scrolled away.

## Viewing Webhook Flow Timelines

Click any **Hook ID** link in a log entry to open the **Webhook Flow Timeline** modal. This shows:

- **Flow Overview** — hook ID, total steps, processing duration, token spend (API calls made), and repository
- **Step-by-step timeline** — each workflow step with its status icon (✓ success, ✗ error, ◷ in-progress), relative timing, and duration
- **Final status** — whether the flow completed successfully, with errors, or is still running

Steps are grouped by task ID. Click a group header to expand and see individual steps. Click any step to view its detailed execution metadata and associated log entries.

### Viewing All Events for a PR

Click any **PR number** link in a log entry to open the **PR Workflow** modal. This lists every unique webhook delivery ID associated with that PR. Click any event in the list to jump to its flow timeline.

## Exporting Logs

Click **Export JSON** to download the currently filtered logs as a JSON file. The export respects all active filters and the results limit.

The downloaded file includes:

- **Export metadata** — timestamp, applied filters, and entry count
- **Log entries** — the full array of matching log entries

The file is named `webhook_logs_YYYYMMDD_HHMMSS.json`.

> **Tip:** Increase the **Results Limit** before exporting if you need more than the default number of entries.

## Advanced Usage

### Switching Themes

Click the theme toggle button (🌙/☀️) in the top-right corner to switch between light and dark mode. Your preference is saved in the browser.

### Collapsing the Filter Panel

Click the **▼** button next to "Filters & Controls" to collapse the filter panel and give more screen space to log entries. The collapsed state is remembered across page loads.

### Drilling Into Step Logs

In the flow timeline modal, clicking a step fetches the actual log entries that occurred during that step's execution window. This is useful for understanding exactly what happened during a specific operation — for example, why a container build failed or which GitHub API call hit a rate limit.

Each step detail view shows:

- **Status badge** and **duration**
- **Error details** (if the step failed)
- **Execution metadata** from the structured log
- **Time-correlated log entries** from the text logs

### Using the REST API Directly

The log viewer is backed by a REST API that you can call programmatically. See [Log Viewer API Reference](log-viewer-api.html) for the complete endpoint documentation, including:

- `GET /logs/api/entries` — query and filter log entries
- `GET /logs/api/export` — export filtered logs as JSON
- `GET /logs/api/workflow-steps/{hook_id}` — get the step timeline for a webhook delivery
- `GET /logs/api/step-logs/{hook_id}/{step_name}` — get log entries for a specific step
- `GET /logs/api/pr-flow/{hook_id}` — get PR flow visualization data
- `WebSocket /logs/ws` — real-time log streaming with server-side filtering

## Troubleshooting

**Log viewer page returns 404**
- Verify that `ENABLE_LOG_SERVER=true` is set (literal string `true`). Restart the server after changing environment variables.

**No log entries appear**
- Check that `WEBHOOK_SERVER_DATA_DIR` points to a directory containing a `logs/` subdirectory with `.log` or `webhooks_*.json` files.
- Ensure the server process has read permissions on the log files.

**WebSocket disconnects immediately**
- The log viewer checks `ENABLE_LOG_SERVER` on WebSocket connect. If the environment variable was changed after startup, restart the server.

**Sensitive data visible in logs**
- Set `mask-sensitive-data: true` in your `config.yaml` (this is the default). See [Configuring Repositories](configuring-repositories.html) for per-repository overrides.

**"(partial scan)" shown in statistics**
- The server caps how many entries it scans per query for performance. Add more specific filters (hook ID, repository, time range) to narrow the scan, or increase the results limit.

## Related Pages

- [Log Viewer API Reference](log-viewer-api.html)
- [Environment Variables](environment-variables.html)
- [Deploying with Docker](deploying-with-docker.html)
- [Configuration Reference](configuration-reference.html)
- [MCP Server for AI Agents](mcp-server-integration.html)
