# Debug with the Log Viewer

Use this page when a webhook was accepted but the automation still did not do what you expected. The log viewer lets you move from a GitHub delivery ID or PR number to the exact workflow steps and log lines that explain what happened.

## Prerequisites

- A running server instance. See [Start Automating a Repository](quick-start.html) if you have not deployed it yet.
- `ENABLE_LOG_SERVER=true` in the server environment.
- Access to the server from localhost, a VPN, or another trusted/private network.
- A GitHub delivery ID (`X-GitHub-Delivery`) or PR number to investigate.

## Quick Example

```bash
ENABLE_LOG_SERVER=true WEBHOOK_SERVER_DATA_DIR=/path/to/data uv run entrypoint.py
```

1. Open `http://127.0.0.1:500/logs`.
2. Paste the GitHub delivery ID into `Hook ID`.
3. Click the hook ID in the results to open the workflow timeline.
4. Click the failed step to see its status, duration, error, and matching log lines.

> **Note:** If you changed the server port in your config, use that port instead of `500`.


> **Warning:** The `/logs` endpoints are unauthenticated. Keep them on trusted networks only. See [Secure Webhooks and Pull Requests](secure-webhooks-and-pull-requests.html) for deployment guidance.

## Step-by-Step

### 1. Enable the viewer and restart the server

If you run the server directly, start it with `ENABLE_LOG_SERVER=true`.

```bash
ENABLE_LOG_SERVER=true WEBHOOK_SERVER_DATA_DIR=/path/to/data uv run entrypoint.py
```

If you run the container, add the same environment variable to the container and restart it.

> **Note:** The value must be the exact string `true`. Other values leave the viewer disabled. See [Environment Variables](environment-variables.html) for the full environment list.

### 2. Open recent history

Visit `/logs` in your browser. The page loads recent entries automatically and shows the newest items first.

Start with **Refresh** if the page is already open and you want the latest historical data from disk.

### 3. Narrow the results to the run you care about

Use the filters at the top of the page to reduce noise before you inspect a failure.

| If you know this | Use this control |
| --- | --- |
| GitHub delivery ID | `Hook ID` |
| Pull request number | `PR #` |
| Repository name | `Repository` |
| Username that triggered the event | `User` |
| Error text from a comment or status | `Search` |
| Only failures or warnings | `Level` |
| Rough time window | `Start Time` and `End Time` |

A few practical patterns work well:

1. Start with `Hook ID` if you have the delivery from GitHub.
2. Use `PR #` when one pull request generated several webhook deliveries.
3. Add `Level=ERROR` or `Level=WARNING` when you only want failing or suspicious entries.
4. Add a time range if the server manages many repositories.

The stats bar helps you judge whether the filter is tight enough:

- **Shown**: how many entries are on screen now.
- **Total**: an estimate of how many logs exist overall.
- **Scanned**: how many entries the server examined for the current query.

> **Tip:** If **Scanned** shows a `+` suffix or `(partial scan)`, narrow the query with `Hook ID`, `Repository`, `Level`, or a time window before trusting the result set.

### 4. Open the workflow for one webhook delivery

Click the hook ID shown inside a log entry. That opens the workflow timeline for that delivery.

Use the timeline when you want to answer questions like:

- Did the server stop at validation, cloning, checks, or mergeability?
- Which step failed first?
- How long did the run take?
- Was the webhook skipped on purpose?

If you only know the pull request number, click the PR number in a log entry first. The PR modal lists every unique webhook delivery ID it found for that PR, and you can open any one of them from there.

### 5. Inspect the failing step

Inside the workflow timeline, click the step that failed or looks slow. The page shows two layers of detail:

1. The step summary:
   - status
   - duration
   - recorded error, if one exists
2. The log lines that happened during that step

This is the fastest way to answer “what actually broke?” without reading the entire log stream.

> **Note:** Even if no matching log lines are found for a step, the viewer still shows the step’s own status, duration, and recorded error details.

### 6. Save or share the result

Once the page shows the right slice of data, click **Export JSON**. The export uses the current filters and the current results limit.

This is useful when you want to:

- attach evidence to an issue
- compare repeated failures offline
- hand another maintainer the exact filtered result set

> **Note:** **Clear Logs** only clears the current browser view. It does not delete log files from the server.

## Advanced Usage

### Watch a problem live

Use real-time mode when you are about to reproduce a problem.

1. Set the filters you want first.
2. Click **Start Real-time**.
3. Reproduce the action in GitHub.
4. Watch new entries appear at the top of the page.
5. Click **Stop Real-time** when you are done.

Server-side streaming is most useful with these filters:

- `Hook ID`
- `PR #`
- `Repository`
- `User`
- `Level`

> **Note:** Search text still helps while streaming because the page filters messages in the browser. Time-range filters are most useful when reloading historical entries, not when following a live stream.

### Export a filtered snapshot from the shell

If you want the same filtered data without using the button, download it directly.

```bash
curl -OJ "http://127.0.0.1:500/logs/api/export?format_type=json&hook_id=<delivery-id>&level=ERROR&limit=500"
```

If you want the step timeline directly, fetch it by delivery ID.

```bash
curl "http://127.0.0.1:500/logs/api/workflow-steps/<delivery-id>"
```

See [Log Viewer and MCP API](log-viewer-and-mcp-api.html) for the full set of routes and query parameters.

### Handle large result sets safely

The viewer is designed to avoid loading unlimited logs at once. You will get better results if you narrow first and expand second.

Use this order when the server is busy:

1. Filter by `Repository`.
2. Add `PR #` or `Hook ID`.
3. Add `Level=ERROR` if you only care about failures.
4. Increase the results limit only after the query is already specific.

> **Warning:** JSON exports over 50,000 entries are rejected. If you hit that limit, split the export by time range, repository, or PR.

## Troubleshooting

**`/logs` returns 404**

- Make sure `ENABLE_LOG_SERVER=true` is set exactly.
- Restart the server after changing environment variables.

**The page opens but no entries appear**

- Confirm `WEBHOOK_SERVER_DATA_DIR` points at the same data directory the server is using.
- Confirm that directory has a `logs/` subdirectory with recent log files.
- If GitHub showed a `200` delivery, remember that only means the webhook was accepted and queued. See [Webhook and Health API](webhook-and-health-api.html) for those semantics.

**A PR changed nothing even though the delivery succeeded**

- Search by `Hook ID` first, then open the workflow timeline.
- Some deliveries are intentionally skipped, such as certain draft-PR, pending-status, or unsupported event cases. See [Supported GitHub Events](supported-github-events.html) for the expected behavior.

**The workflow opens but step logs do not load**

- Use the viewer from localhost, a VPN, or another trusted/private network.
- If you are behind a reverse proxy, make sure the server can still determine the real client IP.

**You keep seeing `(partial scan)`**

- Narrow the query with `Hook ID`, `Repository`, `Level`, or time filters.
- Export smaller slices instead of one large all-history query.# Debug with the Log Viewer

Use this page when a webhook was accepted but the automation still did not do what you expected. The log viewer lets you move from a GitHub delivery ID or PR number to the exact workflow steps and log lines that explain what happened.

## Prerequisites

- A running server instance. See [Start Automating a Repository](quick-start.html) if you have not deployed it yet.
- `ENABLE_LOG_SERVER=true` in the server environment.
- Access to the server from localhost, a VPN, or another trusted/private network.
- A GitHub delivery ID (`X-GitHub-Delivery`) or PR number to investigate.

## Quick Example

```bash
ENABLE_LOG_SERVER=true WEBHOOK_SERVER_DATA_DIR=/path/to/data uv run entrypoint.py
```

1. Open `http://127.0.0.1:5000/logs`.
2. Paste the GitHub delivery ID into `Hook ID`.
3. Click the hook ID in the results to open the workflow timeline.
4. Click the failed step to see its status, duration, error, and matching log lines.

> **Note:** If you changed the server port in your config, use that port instead of `5000`.


> **Warning:** The `/logs` endpoints are unauthenticated. Keep them on trusted networks only. See [Secure Webhooks and Pull Requests](secure-webhooks-and-pull-requests.html) for deployment guidance.

## Step-by-Step

### 1. Enable the viewer and restart the server

If you run the server directly, start it with `ENABLE_LOG_SERVER=true`.

```bash
ENABLE_LOG_SERVER=true WEBHOOK_SERVER_DATA_DIR=/path/to/data uv run entrypoint.py
```

If you run the container, add the same environment variable to the container and restart it.

> **Note:** The value must be the exact string `true`. Other values leave the viewer disabled. See [Environment Variables](environment-variables.html) for the full environment list.

### 2. Open recent history

Visit `/logs` in your browser. The page loads recent entries automatically and shows the newest items first.

Start with **Refresh** if the page is already open and you want the latest historical data from disk.

### 3. Narrow the results to the run you care about

Use the filters at the top of the page to reduce noise before you inspect a failure.

| If you know this | Use this control |
| --- | --- |
| GitHub delivery ID | `Hook ID` |
| Pull request number | `PR #` |
| Repository name | `Repository` |
| Username that triggered the event | `User` |
| Error text from a comment or status | `Search` |
| Only failures or warnings | `Level` |
| Rough time window | `Start Time` and `End Time` |

A few practical patterns work well:

1. Start with `Hook ID` if you have the delivery from GitHub.
2. Use `PR #` when one pull request generated several webhook deliveries.
3. Add `Level=ERROR` or `Level=WARNING` when you only want failing or suspicious entries.
4. Add a time range if the server manages many repositories.

The stats bar helps you judge whether the filter is tight enough:

- **Shown**: how many entries are on screen now.
- **Total**: an estimate of how many logs exist overall.
- **Scanned**: how many entries the server examined for the current query.

> **Tip:** If **Scanned** shows a `+` suffix or `(partial scan)`, narrow the query with `Hook ID`, `Repository`, `Level`, or a time window before trusting the result set.

### 4. Open the workflow for one webhook delivery

Click the hook ID shown inside a log entry. That opens the workflow timeline for that delivery.

Use the timeline when you want to answer questions like:

- Did the server stop at validation, cloning, checks, or mergeability?
- Which step failed first?
- How long did the run take?
- Was the webhook skipped on purpose?

If you only know the pull request number, click the PR number in a log entry first. The PR modal lists every unique webhook delivery ID it found for that PR, and you can open any one of them from there.

### 5. Inspect the failing step

Inside the workflow timeline, click the step that failed or looks slow. The page shows two layers of detail:

1. The step summary:
   - status
   - duration
   - recorded error, if one exists
2. The log lines that happened during that step

This is the fastest way to answer “what actually broke?” without reading the entire log stream.

> **Note:** Even if no matching log lines are found for a step, the viewer still shows the step’s own status, duration, and recorded error details.

### 6. Save or share the result

Once the page shows the right slice of data, click **Export JSON**. The export uses the current filters and the current results limit.

This is useful when you want to:

- attach evidence to an issue
- compare repeated failures offline
- hand another maintainer the exact filtered result set

> **Note:** **Clear Logs** only clears the current browser view. It does not delete log files from the server.

## Advanced Usage

### Watch a problem live

Use real-time mode when you are about to reproduce a problem.

1. Set the filters you want first.
2. Click **Start Real-time**.
3. Reproduce the action in GitHub.
4. Watch new entries appear at the top of the page.
5. Click **Stop Real-time** when you are done.

Server-side streaming is most useful with these filters:

- `Hook ID`
- `PR #`
- `Repository`
- `User`
- `Level`

> **Note:** Search text still helps while streaming because the page filters messages in the browser. Time-range filters are most useful when reloading historical entries, not when following a live stream.

### Export a filtered snapshot from the shell

If you want the same filtered data without using the button, download it directly.

```bash
curl -OJ "http://127.0.0.1:5000/logs/api/export?format_type=json&hook_id=<delivery-id>&level=ERROR&limit=5000"
```

If you want the step timeline directly, fetch it by delivery ID.

```bash
curl "http://127.0.0.1:5000/logs/api/workflow-steps/<delivery-id>"
```

See [Log Viewer and MCP API](log-viewer-and-mcp-api.html) for the full set of routes and query parameters.

### Handle large result sets safely

The viewer is designed to avoid loading unlimited logs at once. You will get better results if you narrow first and expand second.

Use this order when the server is busy:

1. Filter by `Repository`.
2. Add `PR #` or `Hook ID`.
3. Add `Level=ERROR` if you only care about failures.
4. Increase the results limit only after the query is already specific.

> **Warning:** JSON exports over 50,000 entries are rejected. If you hit that limit, split the export by time range, repository, or PR.

## Troubleshooting

**`/logs` returns 404**

- Make sure `ENABLE_LOG_SERVER=true` is set exactly.
- Restart the server after changing environment variables.

**The page opens but no entries appear**

- Confirm `WEBHOOK_SERVER_DATA_DIR` points at the same data directory the server is using.
- Confirm that directory has a `logs/` subdirectory with recent log files.
- If GitHub showed a `200` delivery, remember that only means the webhook was accepted and queued. See [Webhook and Health API](webhook-and-health-api.html) for those semantics.

**A PR changed nothing even though the delivery succeeded**

- Search by `Hook ID` first, then open the workflow timeline.
- Some deliveries are intentionally skipped, such as certain draft-PR, pending-status, or unsupported event cases. See [Supported GitHub Events](supported-github-events.html) for the expected behavior.

**The workflow opens but step logs do not load**

- Use the viewer from localhost, a VPN, or another trusted/private network.
- If you are behind a reverse proxy, make sure the server can still determine the real client IP.

**You keep seeing `(partial scan)`**

- Narrow the query with `Hook ID`, `Repository`, `Level`, or time filters.
- Export smaller slices instead of one large all-history query.

## Related Pages

- [Log Viewer and MCP API](log-viewer-and-mcp-api.html)
- [Webhook and Health API](webhook-and-health-api.html)
- [Supported GitHub Events](supported-github-events.html)
- [Secure Webhooks and Pull Requests](secure-webhooks-and-pull-requests.html)
- [Environment Variables](environment-variables.html)
