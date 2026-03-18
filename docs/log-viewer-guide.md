# Log Viewer Guide

The log viewer gives you one place to watch webhook activity, narrow history to the event you care about, inspect how a PR moved through the server, and export a clean slice of logs for debugging or reporting.

Quick start:
1. Set `ENABLE_LOG_SERVER=true`.
2. Start the server.
3. Open `/logs`.
4. Filter by `Hook ID` or `PR #`.
5. Click `Start Real-time` if you want live updates.
6. Click a hook or PR tag to drill into the workflow.
7. Use `Export JSON` after you narrow the result set.

> **Warning:** The log viewer routes are unauthenticated. Deploy them only on localhost, a VPN, or another trusted network, and keep `mask-sensitive-data: true` unless you are debugging in a safe environment.

## Enable the log viewer

The log viewer is enabled with an environment variable, not a YAML setting. The repository’s Docker Compose example enables it like this:

```yaml
environment:
  - PUID=1000
  - PGID=1000
  - TZ=Asia/Jerusalem
  - MAX_WORKERS=50 # Defaults to 10 if not set
  - WEBHOOK_SERVER_IP_BIND=0.0.0.0 # IP to listen
  - WEBHOOK_SERVER_PORT=5000 # Port to listen
  - WEBHOOK_SECRET=<secret> # If set verify hook is a valid hook from Github
  - VERIFY_GITHUB_IPS=1 # Verify hook request is from GitHub IPs
  - VERIFY_CLOUDFLARE_IPS=1 # Verify hook request is from Cloudflare IPs
  - ENABLE_LOG_SERVER=true # Enable log viewer endpoints (default: false)
  - ENABLE_MCP_SERVER=false # Enable MCP server for AI agent integration (default: false)
```

After startup, open `http://<host>:5000/logs`.

> **Note:** The webhook receiver is mounted at `/webhook_server`, but the log viewer page is mounted separately at `/logs`.

The viewer reads from `${WEBHOOK_SERVER_DATA_DIR}/logs`. If you do not set `WEBHOOK_SERVER_DATA_DIR`, the server uses `/home/podman/data`, so the default log directory is `/home/podman/data/logs`.

If `ENABLE_LOG_SERVER` is not set to `true`, `/logs`, `/logs/api/*`, and `/logs/ws` are unavailable.

## Choose the log files and masking behavior

The viewer uses the same logging configuration as the rest of the server. The example `config.yaml` includes these keys:

```yaml
log-level: INFO # Set global log level, change take effect immediately without server restart
log-file: webhook-server.log # Set global log file, change take effect immediately without server restart
mcp-log-file: mcp_server.log # Set global MCP log file, change take effect immediately without server restart
logs-server-log-file: logs_server.log # Set global Logs Server log file, change take effect immediately without server restart
mask-sensitive-data: true # Mask sensitive data in logs (default: true). Set to false for debugging (NOT recommended in production)
```

What those settings mean in practice:
- `log-file` is the main text log the viewer can search.
- `logs-server-log-file` keeps the viewer’s own infrastructure logging separate.
- `mask-sensitive-data` controls whether sensitive strings are redacted before they are written to logs. This affects what you see in the viewer and what gets exported.

> **Tip:** Keep `mask-sensitive-data: true` for normal use. Turn it off only for short-lived debugging on a trusted network, then turn it back on.

The viewer also reads daily structured JSON files named `webhooks_YYYY-MM-DD.json`. Those files are what make the best PR flow and workflow-step views possible. Historical searches include recent `*.log`, rotated `*.log.*`, and `webhooks_*.json` files.

The JSON files rotate by date. Cleanup and long-term retention beyond that are up to your deployment.

Unfiltered searches also stay focused on webhook activity: the viewer automatically skips uncorrelated infrastructure noise from its own logger and the MCP logger.

## Filter logs and use live streaming

The page is built for two workflows: search recent history, then switch to live mode if you want to watch new entries arrive.

The UI exposes these filters:
- `Search`
- `Hook ID`
- `PR #`
- `Repository`
- `User`
- `Level`
- `Start Time`
- `End Time`
- `Results Limit`

`Search` is a case-insensitive match against the log message text.

The level dropdown includes the usual levels plus viewer-specific ones:
- `COMPLETED`
- `DEBUG`
- `INFO`
- `WARNING`
- `ERROR`
- `STEP`
- `SUCCESS`

The UI offers these result limits:
- `100`
- `500`
- `1000`
- `5000`
- `10000`

The default is `1000`.

A few controls are especially useful:
- `Refresh` reloads historical entries from disk.
- `Start Real-time` opens a WebSocket stream to `/logs/ws`.
- `Stop Real-time` closes the live stream.
- `Auto-scroll` keeps the newest incoming entry in view.
- `Clear Logs` clears the browser view only. It does not delete log files.
- `Export JSON` downloads the currently filtered slice.

The stats row helps you judge how complete a result is:
- `Shown` is how many entries are currently displayed.
- `Total` is an estimate of how many log entries exist across the available log files.
- `Scanned` is how many entries the last query had to examine.

> **Tip:** Start with `Hook ID` or `PR #` whenever possible. Those are the fastest way to narrow a noisy system down to the webhook run you actually care about.

On very large log sets, the viewer may intentionally stop early to stay responsive. When that happens, the stats panel marks the scan as partial.

> **Note:** If the `Scanned` value shows `(partial scan)`, narrow the repository, time range, PR, or hook ID and run `Refresh` again before you treat the result as complete.

Live mode is best for new events. Historical refreshes are the better choice when you need a strict time window or a broad search across existing files.

## Inspect PR flows and workflow steps

The most useful part of the viewer is the drill-down from raw log lines into one webhook delivery or one PR’s full sequence of webhook events.

From the main log table:
- Click a `Hook` tag to open the flow view for one specific webhook delivery.
- Click a `PR` tag to open a PR view that lists all hook IDs found for that pull request.
- From the PR view, click any hook ID to inspect that single webhook run.

The flow modal shows:
- Hook ID
- Total step count
- Total duration
- Token spend
- Repository
- A success, in-progress, or error state for the overall run

Here, “Token Spend” means GitHub API calls consumed during that webhook run, not LLM token usage.

The richest step data comes from structured workflow tracking. The codebase records steps with calls like these:

```python
ctx = create_context(
    hook_id="github-delivery-id",
    event_type="pull_request",
    repository="org/repo",
    repository_full_name="org/repo",
    action="opened",
    sender="username",
)

ctx.start_step("clone_repository", branch="main")
try:
    await clone_repo()
    ctx.complete_step("clone_repository", commit_sha="abc123")
except Exception as ex:
    ctx.fail_step("clone_repository", exception=ex, traceback_str=traceback.format_exc())
```

When handlers record steps that way, the viewer can:
- order steps by time
- group related entries by task
- show step status and duration
- highlight failed steps
- fetch log lines that happened during a specific step window

Click any step in the flow view to expand it. The viewer first shows the step metadata, then loads log lines that fall inside that step’s execution window. Per-step drill-down is intentionally bounded so very chatty steps do not overwhelm the UI.

> **Note:** Step-level log drill-down returns up to 500 log entries for a selected step.

Older webhook runs can still open, but the experience may be lighter if only text logs are available. The viewer tries structured JSON data first and falls back to text log parsing when it needs to.

> **Note:** Older webhooks can show less detail. In that case the flow view may still work, but fields like token spend can appear as `N/A (older webhook)`.

## Export logs safely

Use `Export JSON` after you have narrowed the data to the smallest slice you need. The export uses the current filters and the current results limit from the UI.

Only JSON export is supported. The downloaded file name follows this pattern:

```text
webhook_logs_YYYYMMDD_HHMMSS.json
```

The export payload includes metadata alongside the log entries. The code that builds it looks like this:

```python
export_data = {
    "export_metadata": {
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "filters_applied": filters or {},
        "total_entries": len(entries),
        "export_format": "json",
    },
    "log_entries": [entry.to_dict() for entry in entries],
}
```

That means every export includes:
- when it was generated
- which filters were active
- how many entries were included
- the log entries themselves

A few practical rules make exports safer and easier to use:
- Keep `mask-sensitive-data: true` unless you have a strong reason not to.
- Filter by `Hook ID`, `PR #`, `Repository`, or a short time range before exporting.
- Increase `Results Limit` before exporting if you need more than the default 1000 entries.
- Treat the exported file like any other operational log artifact and store it accordingly.

> **Warning:** Exported logs can include repository names, usernames, PR metadata, error messages, and other operational detail. Do not share them casually or expose the export endpoint to the public internet.

The server rejects very large exports instead of trying to generate an unbounded file.

> **Tip:** If an export is too large, split it by repository, PR, hook ID, or time window and export in smaller batches. The server caps exports at 50,000 entries.

## Useful routes

These are the routes behind the viewer:
- `GET /logs` renders the main log viewer page.
- `GET /logs/api/entries` returns filtered historical entries.
- `GET /logs/api/workflow-steps/{hook_id}` returns workflow-step data for one webhook delivery.
- `GET /logs/api/step-logs/{hook_id}/{step_name}` returns log lines correlated to one workflow step.
- `GET /logs/api/export?format_type=json` downloads a JSON export.
- `WS /logs/ws` streams new log entries in real time.

> **Note:** The per-step log drill-down route is additionally intended for trusted/private network use. If the main viewer loads but step logs do not, check your network path first.
