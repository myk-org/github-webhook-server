Let me explore the codebase to understand the MCP server implementation.

Now let me check what API routes are exposed (and thus available as MCP tools), particularly the ones NOT tagged with `mcp_exclude`:

Now let me check the schema for the mcp-log-file config option:

Now let me check what examples config looks like for the mcp-log-file:

Now let me also check the `require_trusted_network` dependency to understand the security model for some endpoints:

Now let me understand the available MCP tools. The `FastApiMCP` creates tools from all API routes except those tagged `mcp_exclude`. Let me list all routes and their tags:

So the tools exposed via MCP are (excluding `mcp_exclude` tag and `include_in_schema=False`):
- `healthcheck` — GET /webhook_server/healthcheck
- `get_log_entries` — GET /logs/api/entries (if log server enabled)
- `export_logs` — GET /logs/api/export (if log server enabled)
- `get_pr_flow_data` — GET /logs/api/pr-flow/{hook_id} (if log server enabled)
- `get_workflow_steps` — GET /logs/api/workflow-steps/{hook_id} (if log server enabled)
- `get_step_logs` — GET /logs/api/step-logs/{hook_id}/{step_name} (if log server enabled)
- `get_log_viewer_page` — GET /logs (if log server enabled)

The `process_webhook` has `mcp_exclude` tag and the `/mcp` endpoint has `include_in_schema=False`.

Let me check if the log viewer page endpoint is also excluded or conditionally registered:

Now let me check how Claude Desktop, Cursor, etc. would connect:

Let me also check the pyproject.toml for the fastapi-mcp version:

Now let me verify the exclusions file one more time to be very sure what I should not mention:

So I should not link to quickstart.html or environment-variables.html. Let me now check what existing docs exist that I could link to:

Now I have a comprehensive understanding. Let me write the documentation page.

# MCP Server for AI Agents

Connect AI agents to your webhook server so they can query webhook logs, inspect PR workflows, and analyze processing steps programmatically using the Model Context Protocol (MCP).

## Prerequisites

- A running instance of the webhook server.
- The log server enabled (`ENABLE_LOG_SERVER=true`) — most MCP tools expose log viewer endpoints.
- An MCP-compatible AI client (Claude Desktop, Cursor, Windsurf, or any Streamable HTTP MCP client).

## Quick Example

Start the server with MCP enabled:

```bash
ENABLE_MCP_SERVER=true ENABLE_LOG_SERVER=true uv run entrypoint.py
```

Then point your AI client to the MCP endpoint:

```
http://localhost:5000/mcp
```

That's it — the AI agent can now call tools like `get_log_entries`, `get_pr_flow_data`, and `get_workflow_steps`.

## Enabling the MCP Server

### Local development

Set the environment variable before starting the server:

```bash
ENABLE_MCP_SERVER=true uv run entrypoint.py
```

### Docker Compose

Add the variable to your `environment` block:

```yaml
services:
  github-webhook-server:
    image: ghcr.io/myk-org/github-webhook-server:latest
    environment:
      - ENABLE_MCP_SERVER=true
      - ENABLE_LOG_SERVER=true
```

> **Note:** The MCP endpoint listens on the same port as the main webhook server. It uses the `/mcp` path exclusively and does not interfere with webhook processing.

### Verifying the endpoint

After starting the server, confirm MCP is active by checking the startup logs for:

```
MCP integration initialized successfully (no authentication configured)
```

## Connecting AI Clients

The MCP server uses Streamable HTTP transport in stateless mode. Any MCP client that supports HTTP transport can connect.

### Claude Desktop

Add this to your Claude Desktop MCP configuration file:

```json
{
  "mcpServers": {
    "webhook-server": {
      "url": "http://localhost:5000/mcp"
    }
  }
}
```

### Cursor

In Cursor's MCP settings, add a new server with:

- **Type:** Streamable HTTP
- **URL:** `http://localhost:5000/mcp`

### Other MCP clients

Any client that supports the MCP Streamable HTTP transport can connect to `http://<your-server-host>:<port>/mcp` using `GET`, `POST`, and `DELETE` HTTP methods.

## Available Tools

When connected, AI agents can invoke these tools:

| Tool | Description |
|------|-------------|
| `healthcheck` | Check if the webhook server is running |
| `get_log_entries` | Query webhook processing logs with filters (hook ID, PR number, repository, event type, user, level, time range, text search) and pagination |
| `export_logs` | Export filtered logs as downloadable JSON files for offline analysis |
| `get_pr_flow_data` | Get PR workflow visualization data for a specific webhook delivery ID — tracks the full lifecycle from receipt to completion |
| `get_workflow_steps` | Retrieve step-by-step timing, status, and diagnostic data for each operation in a webhook processing flow |
| `get_step_logs` | Get log entries that occurred during a specific workflow step's execution window |

> **Note:** The `get_log_entries`, `export_logs`, `get_pr_flow_data`, `get_workflow_steps`, and `get_step_logs` tools require `ENABLE_LOG_SERVER=true`. Without the log server, only `healthcheck` is available. See [Using the Log Viewer](using-the-log-viewer.html) for log server setup.

### Example agent queries

Once connected, you can ask your AI agent questions like:

- "Show me all ERROR-level logs from the last hour"
- "What happened during the processing of webhook delivery `f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f`?"
- "Export all logs for PR #42 in `myorg/myrepo`"
- "Show me the workflow steps and timing for the last failed webhook"

The agent translates these into the appropriate tool calls automatically.

## Advanced Usage

### Customizing the MCP Log File

MCP traffic generates its own log output, kept separate from main webhook processing logs. By default, MCP logs write to `mcp_server.log` in your data directory. Override this in `config.yaml`:

```yaml
mcp-log-file: custom_mcp.log
```

> **Tip:** You must restart the server after changing `mcp-log-file` or `ENABLE_MCP_SERVER`. Logging bindings are established at startup.

See [Configuration Reference](configuration-reference.html) for all global config options.

### Stateless Session Mode

The MCP server runs in **stateless mode** — it does not track client sessions or store events between requests. Each tool invocation is independent. This means:

- No session cookies or IDs are required from clients.
- Multiple AI agents can connect simultaneously without interference.
- Server restarts do not break ongoing agent workflows (agents simply reconnect).

### Reverse Proxy Configuration

If you run the server behind a reverse proxy (Nginx, Traefik, Cloudflare Tunnel), ensure it forwards all three HTTP methods required by MCP:

- `GET`
- `POST`
- `DELETE`

If your proxy blocks or filters any of these, the MCP endpoint will not function.

### Webhook Processing Is Excluded

The webhook ingestion endpoint is intentionally excluded from MCP tools. AI agents can query and analyze webhook data but cannot trigger webhook processing through MCP.

## Security Considerations

> **Warning:** The `/mcp` endpoint has **no built-in authentication**. Never expose it to the public internet.

Secure the endpoint using one of these strategies:

- **Same-host access:** Run the AI agent on the same machine or container network as the webhook server.
- **Private network binding:** Bind the server to an internal network IP that is not publicly routable.
- **Authenticated reverse proxy:** Place an authenticating proxy (with TLS, client certificates, or token auth) in front of `/mcp`.

The same security considerations apply to the log viewer endpoints. See [Using the Log Viewer](using-the-log-viewer.html) for additional network isolation guidance.

## Troubleshooting

- **Endpoint returns 404:** Verify the environment contains exactly `ENABLE_MCP_SERVER=true` (lowercase `true`). The feature uses exact string matching.
- **Tools return errors about log server:** Ensure `ENABLE_LOG_SERVER=true` is also set. Most MCP tools depend on the log server being active.
- **Client times out or fails to connect:** Check that your reverse proxy permits `GET`, `POST`, and `DELETE` requests to `/mcp`.
- **Can't find MCP logs:** Look for `mcp_server.log` inside the `logs/` folder of your configured data directory. If the file doesn't exist, verify the directory is writable.
- **Noisy "ClosedResourceError" messages:** These are automatically suppressed. If you see them, the server is filtering client disconnect noise from the MCP transport — no action is needed.

## Related Pages

- [Using the Log Viewer](using-the-log-viewer.html)
- [Log Viewer API Reference](log-viewer-api.html)
- [Configuration Reference](configuration-reference.html)
- [Environment Variables](environment-variables.html)
- [Enabling AI Features](enabling-ai-features.html)
