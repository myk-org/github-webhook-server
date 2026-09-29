# Environment Variables

The server reads the variables below at startup. Anything not listed here comes from
`config.yaml` in the data directory, not from the process environment.

## Reference

| Variable | Required? | Default | Purpose |
| --- | --- | --- | --- |
| `WEBHOOK_SERVER_DATA_DIR` | Recommended | `/home/podman/data` | Directory holding `config.yaml`, logs, and the webhook key. |
| `ENABLE_LOG_SERVER` | No | `false` | Serve the log viewer UI, log API, and log WebSocket. |
| `ENABLE_MCP_SERVER` | No | `false` | Mount the MCP endpoint at `/mcp`. |
| `WEBHOOK_SERVER_DEV_MODE` | No | unset (production) | Run uvicorn with reload and a single worker. |
| `SIDECAR_PORT` | No | `9100` | Port for the Pi SDK sidecar started by `entrypoint.sh`. |
| `SERVER_PORT` | Test-only | none | Local port the E2E smee client forwards webhooks to. |
| `SMEE_URL` | Test-only | none | Smee.io channel URL used to receive GitHub webhooks in E2E tests. |
| `TEST_REPO` | Test-only | none | `owner/repo-name` that E2E tests clone and open PRs against. |
| `DOCKER_COMPOSE_FILE` | Test-only | none | Compose file E2E tests bring the server up with. |
| `PYTEST_TIMEOUT` | Test-only | `60` (seconds) | Timeout baseline for the test session. |

## `WEBHOOK_SERVER_DATA_DIR`

Read once in `webhook_server/libs/config.py`. The directory must contain `config.yaml`;
if the file is missing, `Config` raises `FileNotFoundError` on startup. The log viewer and
the MCP log file are written under the same directory, so pointing this at a read-only
path breaks both. The container image creates `/home/podman/data`, which is why that path
is the default.

```bash
export WEBHOOK_SERVER_DATA_DIR="$HOME/webhook-server-data"
mkdir -p "$WEBHOOK_SERVER_DATA_DIR"

cat > "$WEBHOOK_SERVER_DATA_DIR/config.yaml" <<'YAML'
github-app-id: 123456
github-tokens:
  - ghp_your_token_here

webhook-ip: https://your-domain.example/webhook_server

repositories:
  your-repo:
    name: your-org/your-repo
YAML

WEBHOOK_SERVER_DATA_DIR="$WEBHOOK_SERVER_DATA_DIR" uv run entrypoint.py
```

Without `config.yaml` the startup call to `Config.exists()` raises `FileNotFoundError`, so the
directory alone is not enough. See [Quick Start](quick-start.html) for the full setup,
including the GitHub App private key.

## `ENABLE_LOG_SERVER`

Checked as an exact string match against `"true"`, so `1`, `TRUE`, and `yes` all count as
disabled. When it is off, the log viewer routes are never registered and the log WebSocket
closes with a policy-violation code. When it is on, `/logs` and the log API endpoints are
added, including the endpoints that also require the caller to be on a trusted network.

```bash
ENABLE_LOG_SERVER=true WEBHOOK_SERVER_DATA_DIR="$HOME/webhook-server-data" uv run entrypoint.py
```

## `ENABLE_MCP_SERVER`

Same exact `"true"` comparison as `ENABLE_LOG_SERVER`. When enabled, the app initializes
FastApiMCP and mounts the streamable-HTTP handler at `/mcp`, and MCP log lines are routed to
the file named by `mcp-log-file` in `config.yaml` (default `mcp_server.log`) instead of the
main webhook log. If FastApiMCP fails to import, `/mcp` is still registered and answers
with a 500 rather than a 404, so the failure is visible.

## `WEBHOOK_SERVER_DEV_MODE`

Accepted values are `1`, `true`, and `yes`, case-insensitive (`entrypoint.py`). It sets
uvicorn `reload=True` and omits `workers`, so the process stays single-worker with the
reloader in front. In production mode the app starts with `workers=10` instead.

## `SIDECAR_PORT`

`entrypoint.sh` exports `9100` unless the value is already set, then waits up to 15 seconds
for `http://127.0.0.1:$SIDECAR_PORT/health` before starting the app. The container health
check probes the same URL, so overriding the port without updating the health check will
report the container unhealthy.

## Test-only variables

These four are consumed by the end-to-end fixtures in `webhook_server/tests/e2e/conftest.py`.
The fixture first looks for `.dev/.env`; if the file is missing the run aborts, and each
missing variable raises `E2EInfrastructureError` with the line to add. They have no effect
on the running server.

- `SERVER_PORT` — port the local smee client forwards to; must match the host-side port
  published by the compose file.
- `SMEE_URL` — the smee.io channel URL, for example `https://smee.io/abc123def456`. Tests
  also use it to find and delete the GitHub webhook at teardown.
- `TEST_REPO` — `owner/repo-name`. Cloned over SSH, so the clone key must have access.
- `DOCKER_COMPOSE_FILE` — path to the compose file, absolute or relative to the project root.
  A relative path that does not exist fails the fixture before any container starts.

`PYTEST_TIMEOUT` is also test-only. The autouse fixture in
`webhook_server/tests/conftest.py` reads the current value (falling back to `60`), sets it to
`30` for the duration of each test, and restores the original value afterwards.

Example `.dev/.env`:

```bash
SERVER_PORT=19876
SMEE_URL=https://smee.io/abc123def456
TEST_REPO=owner/repo-name
DOCKER_COMPOSE_FILE=.dev/docker-compose.yaml
```
