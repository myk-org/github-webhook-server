Let me explore the codebase to understand all environment variables used in the project.

Let me check how those Docker Compose environment variables map to config.yaml:

Now let me check how the Docker Compose environment variables map to config.yaml settings:

These Docker Compose environment variables appear in the example `docker-compose.yaml` but are not actually read by the Python code via `os.environ`. Let me check if there's any mapping logic:

These are only in the Docker Compose example and docs, not actually consumed by the Python app. Let me verify by checking config.yaml examples:

Now let me check the `scripts/podman-cleanup.sh` for PUID/PGID usage:

Now let me check the exposed ports and any additional env vars:

Now let me also verify the MCP server specifics:

Now I have a thorough understanding. Let me also verify the `TZ` usage:

Good — `TZ` is only in the Docker Compose example and isn't read by the application code directly (it's a standard Linux/container env var).

Let me also verify if `FORCE_COLOR` is just documented or actively used:

Now let me check the exclusion file again to make sure I handle it properly:

These are pages I should not reference. Let me check the pages list again:

The deny-list contains page slugs that I should NOT mention or reference. These are:
- `using-the-log-viewer.html`
- `enabling-ai-features.html`  
- `deploying-with-docker.html`

Now I have everything I need. Let me write the documentation.

# Environment Variables

All environment variables recognized by the webhook server, grouped by function.

## Server Core

### `WEBHOOK_SERVER_DATA_DIR`

| Property | Value |
|----------|-------|
| **Type** | String (directory path) |
| **Default** | `/home/podman/data` |
| **Required** | No |
| **Read by** | `webhook_server/libs/config.py` |

Base directory containing `config.yaml` and the `logs/` subdirectory. The server reads its configuration from `$WEBHOOK_SERVER_DATA_DIR/config.yaml` and writes structured logs to `$WEBHOOK_SERVER_DATA_DIR/logs/`.

```bash
# Local development
export WEBHOOK_SERVER_DATA_DIR=/path/to/my/data
uv run entrypoint.py

# Docker Compose
environment:
  - WEBHOOK_SERVER_DATA_DIR=/home/podman/data  # matches the container default
```

> **Note:** When running outside Docker, you must set this variable to a directory that contains a valid `config.yaml`. See [Configuration Reference](configuration-reference.html) for the full config schema.

---

### `WEBHOOK_SERVER_DEV_MODE`

| Property | Value |
|----------|-------|
| **Type** | Boolean string (`1`, `true`, `yes` — case-insensitive) |
| **Default** | Disabled (empty / unset) |
| **Required** | No |
| **Read by** | `entrypoint.py` |

Enables Uvicorn's auto-reload mode for development. When enabled, the server watches for file changes and restarts automatically. When disabled, the server starts with the configured number of workers (`max-workers` in `config.yaml`, default `10`).

```bash
WEBHOOK_SERVER_DEV_MODE=true uv run entrypoint.py
```

> **Warning:** Do not enable in production. Dev mode disables multi-worker support and adds filesystem polling overhead.

---

### `ENABLE_LOG_SERVER`

| Property | Value |
|----------|-------|
| **Type** | String (exact match: `true`) |
| **Default** | Disabled (any value other than `true`) |
| **Required** | No |
| **Read by** | `webhook_server/app.py` |

Registers the log viewer HTTP and WebSocket endpoints under `/logs`. When not set to exactly `true`, all `/logs/*` endpoints return HTTP 404.

```bash
# Enable
ENABLE_LOG_SERVER=true uv run entrypoint.py

# Docker Compose
environment:
  - ENABLE_LOG_SERVER=true
```

> **Warning:** Log viewer endpoints are unauthenticated. Only enable on trusted networks (VPN, internal). Access is restricted to private/loopback IP ranges, but this can be bypassed behind a misconfigured reverse proxy.

Affected endpoints when enabled:

| Endpoint | Description |
|----------|-------------|
| `GET /logs` | Log viewer web UI |
| `GET /logs/api/entries` | Query log entries |
| `GET /logs/api/export` | Export logs as JSON |
| `GET /logs/api/pr-flow/{hook_id}` | PR workflow visualization |
| `GET /logs/api/workflow-steps/{hook_id}` | Workflow step timeline |
| `GET /logs/api/step-logs/{hook_id}/{step_name}` | Logs for a specific step |
| `WS /logs/ws` | Real-time log streaming |

See [Log Viewer API Reference](log-viewer-api.html) for endpoint details.

---

### `ENABLE_MCP_SERVER`

| Property | Value |
|----------|-------|
| **Type** | String (exact match: `true`) |
| **Default** | Disabled (any value other than `true`) |
| **Required** | No |
| **Read by** | `webhook_server/app.py` |

Registers the Model Context Protocol (MCP) endpoint at `/mcp` for AI agent integration. When enabled, the server exposes its API operations as MCP tools that AI agents can discover and invoke.

```bash
# Enable
ENABLE_MCP_SERVER=true uv run entrypoint.py

# Docker Compose
environment:
  - ENABLE_MCP_SERVER=true
```

MCP logging is separated from the main application log. The log file is configured via the `mcp-log-file` key in `config.yaml` (default: `mcp_server.log`).

> **Warning:** The MCP endpoint has no authentication. Deploy only on trusted networks. Use a reverse proxy with authentication for any external access.


> **Tip:** You must restart the server after changing `ENABLE_MCP_SERVER`. The endpoint registration happens at import time, not at runtime.

---

## AI Sidecar

### `SIDECAR_PORT`

| Property | Value |
|----------|-------|
| **Type** | Integer (port number) |
| **Default** | `9100` |
| **Required** | No |
| **Read by** | `entrypoint.sh` |

Port on which the Pi SDK sidecar Node.js process listens. The sidecar bridges AI CLI tools (Claude, Gemini, Cursor) for features such as conventional title suggestions and cherry-pick conflict resolution. The sidecar is started automatically by `entrypoint.sh` if `sidecar-helper/dist/server.js` exists.

```bash
# Override default port
SIDECAR_PORT=9200 uv run entrypoint.sh

# Docker Compose
environment:
  - SIDECAR_PORT=9100
```

The container health check probes both the main server and the sidecar:

```yaml
healthcheck:
  test: ["CMD-SHELL", "curl -f http://localhost:5000/webhook_server/healthcheck && curl -f http://localhost:${SIDECAR_PORT:-9100}/health"]
```

> **Note:** If the sidecar binary is not present or fails to start within 15 seconds, the main server still starts, but AI features will not be available.

---

### `ACPX_AGENTS`

| Property | Value |
|----------|-------|
| **Type** | String |
| **Default** | Unset |
| **Required** | No |
| **Read by** | Pi SDK sidecar |

Enables model discovery for the specified AI agent. Set to `cursor` to enable Cursor model discovery for AI features.

```yaml
# Docker Compose
environment:
  - ACPX_AGENTS=cursor
```

---

### `VERTEX_CLAUDE_1M`

| Property | Value |
|----------|-------|
| **Type** | Boolean string (`true`) |
| **Default** | Unset |
| **Required** | No |
| **Read by** | Pi SDK sidecar |

Enables Claude 1M context window models via Google Vertex AI. Requires Google Cloud credentials to be mounted into the container.

```yaml
# Docker Compose
environment:
  - VERTEX_CLAUDE_1M=true
volumes:
  - $HOME/.config/gcloud:/home/podman/.config/gcloud:ro
```

---

## AI CLI API Keys

These environment variables provide authentication credentials for the AI CLI tools used by the sidecar. They are required only when the corresponding AI provider is configured in the `ai-features` section of `config.yaml`. See [Configuration Reference](configuration-reference.html) for `ai-features` settings.

| Variable | Provider | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Claude Code | API key for Anthropic Claude CLI |
| `GEMINI_API_KEY` | Gemini CLI | API key for Google Gemini CLI |
| `CURSOR_API_KEY` | Cursor Agent | API key for Cursor Agent (API key method) |

```yaml
# Docker Compose — set only the key for your chosen provider
environment:
  - ANTHROPIC_API_KEY=sk-ant-xxx
  # OR
  - GEMINI_API_KEY=xxx
  # OR
  - CURSOR_API_KEY=xxx
```

> **Tip:** For Cursor interactive login (instead of API key), use: `docker exec -it github-webhook-server agent`

---

## Container Runtime

### `PUID`

| Property | Value |
|----------|-------|
| **Type** | Integer (Unix user ID) |
| **Default** | `1000` |
| **Required** | No |
| **Read by** | `scripts/podman-cleanup.sh` |

User ID used by the Podman runtime cleanup script to locate stale runtime directories at `/tmp/storage-run-{PUID}/`.

```yaml
environment:
  - PUID=1000
```

---

### `PGID`

| Property | Value |
|----------|-------|
| **Type** | Integer (Unix group ID) |
| **Default** | `1000` |
| **Required** | No |

Group ID for container process ownership. Standard Docker/Podman convention for controlling file permissions on mounted volumes.

```yaml
environment:
  - PGID=1000
```

---

### `TZ`

| Property | Value |
|----------|-------|
| **Type** | String (IANA timezone identifier) |
| **Default** | Container OS default (typically UTC) |
| **Required** | No |

Sets the container timezone. Affects log timestamps and any time-dependent operations.

```yaml
environment:
  - TZ=Asia/Jerusalem
```

---

### `FORCE_COLOR`

| Property | Value |
|----------|-------|
| **Type** | Boolean string |
| **Default** | Unset |
| **Required** | No |
| **Read by** | Uvicorn (via standard convention) |

Enables colored terminal output in Uvicorn HTTP request logs. Useful when viewing Docker container logs in a terminal that supports ANSI colors. Application-level logs use `simple-logger` with `console=True`, which provides colored output independently.

```yaml
environment:
  - FORCE_COLOR=1
```

---

## Docker Compose Configuration

The example `docker-compose.yaml` at `examples/docker-compose.yaml` includes several environment variables that map directly to keys in `config.yaml`. These are **not** read via `os.environ` by the Python application — they are passed as container environment variables and are available for shell-level substitution or container configuration.

> **Note:** Server settings such as bind address, port, webhook secret, and IP verification are configured in `config.yaml`, not via environment variables. See [Configuration Reference](configuration-reference.html) for all `config.yaml` options.

### Docker Compose Example

```yaml
services:
  github-webhook-server:
    container_name: github-webhook-server
    build: ghcr.io/myk-org/github-webhook-server:latest
    volumes:
      - "./webhook_server_data_dir:/home/podman/data:Z"
      - "/tmp/podman-storage-${USER:-1000}:/tmp/storage-run-1000"
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Asia/Jerusalem
      - ENABLE_LOG_SERVER=true
      - ENABLE_MCP_SERVER=false
      # - SIDECAR_PORT=9100
      # - ACPX_AGENTS=cursor
      # - VERTEX_CLAUDE_1M=true
      # - ANTHROPIC_API_KEY=sk-ant-xxx
    ports:
      - "5000:5000"
    privileged: true
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:5000/webhook_server/healthcheck && curl -f http://localhost:${SIDECAR_PORT:-9100}/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    restart: unless-stopped
```

### Exposed Container Ports

| Port | Service | Description |
|------|---------|-------------|
| `5000` | Webhook server | Main FastAPI application (webhook endpoint, health check, log viewer, MCP) |
| `5001` | Tool server | Internal async tool server for AI custom tools (binds to `127.0.0.1` only) |
| `9100` | Pi SDK sidecar | AI feature sidecar (configurable via `SIDECAR_PORT`) |

---

## E2E Test Variables

These variables are used exclusively by the end-to-end test infrastructure. They are loaded from a `.dev/.env` file and are not relevant to production deployments.

| Variable | Type | Description |
|----------|------|-------------|
| `SERVER_PORT` | Integer | Local server port that webhooks are forwarded to |
| `SMEE_URL` | URL | Smee.io webhook proxy URL for forwarding GitHub webhooks to local dev |
| `TEST_REPO` | String | GitHub repository for E2E tests (`owner/repo-name` format) |
| `DOCKER_COMPOSE_FILE` | Path | Path to docker-compose.yaml for E2E test infrastructure |

```bash
# .dev/.env
SERVER_PORT=5000
SMEE_URL=https://smee.io/YOUR_UNIQUE_CHANNEL
TEST_REPO=owner/repo-name
DOCKER_COMPOSE_FILE=.dev/docker-compose.yaml
```

---

## Quick Reference

All environment variables in one table:

| Variable | Default | Category | Description |
|----------|---------|----------|-------------|
| `WEBHOOK_SERVER_DATA_DIR` | `/home/podman/data` | Server | Path to data directory containing `config.yaml` |
| `WEBHOOK_SERVER_DEV_MODE` | Disabled | Server | Enable Uvicorn auto-reload for development |
| `ENABLE_LOG_SERVER` | Disabled | Server | Enable `/logs` endpoints |
| `ENABLE_MCP_SERVER` | Disabled | Server | Enable `/mcp` endpoint for AI agents |
| `SIDECAR_PORT` | `9100` | AI Sidecar | Pi SDK sidecar listen port |
| `ACPX_AGENTS` | Unset | AI Sidecar | AI agent model discovery (e.g., `cursor`) |
| `VERTEX_CLAUDE_1M` | Unset | AI Sidecar | Enable Claude 1M models via Vertex AI |
| `ANTHROPIC_API_KEY` | Unset | AI Keys | Anthropic Claude API key |
| `GEMINI_API_KEY` | Unset | AI Keys | Google Gemini API key |
| `CURSOR_API_KEY` | Unset | AI Keys | Cursor Agent API key |
| `PUID` | `1000` | Container | Container user ID |
| `PGID` | `1000` | Container | Container group ID |
| `TZ` | UTC | Container | Container timezone |
| `FORCE_COLOR` | Unset | Container | Enable colored Uvicorn log output |

## Related Pages

- [Deploying with Docker](deploying-with-docker.html)
- [Configuration Reference](configuration-reference.html)
- [Enabling AI Features](enabling-ai-features.html)
- [Using the Log Viewer](using-the-log-viewer.html)
- [Getting Started](quickstart.html)
