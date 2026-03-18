# Docker and Container Deployment

`github-webhook-server` ships with a container image that is built around Podman-in-container. That matters for deployment: this is not a thin FastAPI-only image. It is designed to run the webhook server itself and, when repository configuration enables it, run nested Podman commands for repository automation such as building and pushing images.

## The container image

The top-level `Dockerfile` makes the intent clear:

```dockerfile
FROM quay.io/podman/stable:v5

EXPOSE 5000

ENV USERNAME="podman"
ENV HOME_DIR="/home/$USERNAME"
ENV BIN_DIR="$HOME_DIR/.local/bin"
ENV PATH="$PATH:$BIN_DIR:$HOME_DIR/.npm-global/bin" \
  DATA_DIR="$HOME_DIR/data" \
  APP_DIR="$HOME_DIR/github-webhook-server"
```

```dockerfile
USER $USERNAME
WORKDIR $HOME_DIR

ENV UV_PYTHON=python3.13 \
  UV_COMPILE_BYTECODE=1 \
  UV_NO_SYNC=1 \
  UV_CACHE_DIR=${APP_DIR}/.cache \
  PYTHONUNBUFFERED=1

HEALTHCHECK CMD curl --fail http://127.0.0.1:5000/webhook_server/healthcheck || exit 1

ENTRYPOINT ["tini", "--", "uv", "run", "entrypoint.py"]
```

The same `Dockerfile` also installs Podman tooling, `git`, `gh`, Node/NPM, `uv`, `tini`, and several other CLIs. In other words, the image is intentionally heavier than a typical Python web image because it needs to do more than serve HTTP.

A few practical consequences:

- The server listens on port `5000`.
- It runs as the `podman` user inside the container.
- It uses `tini`, which helps with signal handling and process cleanup.
- The built-in health check calls `http://127.0.0.1:5000/webhook_server/healthcheck`.

## Persistent data and volume mounts

By default, the application reads its persistent state from `/home/podman/data`. That comes directly from the runtime configuration code:

```python
self.data_dir: str = os.environ.get("WEBHOOK_SERVER_DATA_DIR", "/home/podman/data")
self.config_path: str = os.path.join(self.data_dir, "config.yaml")
```

The GitHub App private key is also read from that same directory:

```python
with open(os.path.join(config_.data_dir, "webhook-server.private-key.pem")) as fd:
    private_key = fd.read()
```

That means your persistent data mount needs to contain at least:

- `config.yaml`
- `webhook-server.private-key.pem`
- `logs/` (created automatically if it does not exist)

A good mental model is:

| Container path | Purpose | Persist it? |
| --- | --- | --- |
| `/home/podman/data` | Main app data: config, GitHub App key, text logs, structured webhook logs | Yes |
| `/tmp/storage-run-1000` | Nested Podman runtime/storage used by in-container Podman operations | Use a dedicated disposable mount |

The structured webhook logs are written under `logs/` as daily files such as `webhooks_2026-03-18.json`. Text logs also live under `logs/`, using names from `config.yaml` such as `webhook-server.log`, `mcp_server.log`, and `logs_server.log`.

> **Tip:** If you keep the default in-container path `/home/podman/data`, you do not need to set `WEBHOOK_SERVER_DATA_DIR`. Only set that environment variable if you intentionally mount the data directory somewhere else inside the container.

> **Tip:** Keep the `:Z` suffix on the persistent bind mount on SELinux-enabled hosts. The checked-in example uses it so the container can read `config.yaml`, the private key, and log files correctly.

## The example Compose deployment

The repository includes this example in `examples/docker-compose.yaml`:

```yaml
services:
  github-webhook-server:
    container_name: github-webhook-server
    build: ghcr.io/myk-org/github-webhook-server:latest
    volumes:
      - "./webhook_server_data_dir:/home/podman/data:Z" # Should include config.yaml and webhook-server.private-key.pem
      # Mount temporary directories to prevent boot ID mismatch issues
      - "/tmp/podman-storage-${USER:-1000}:/tmp/storage-run-1000"
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
    ports:
      - "5000:5000"
    privileged: true
    restart: unless-stopped
```

What this example is doing:

- It mounts a persistent host directory into `/home/podman/data`.
- It mounts a second host directory into `/tmp/storage-run-1000` for nested Podman runtime state.
- It publishes container port `5000`.
- It runs the container in `privileged` mode.
- It uses `restart: unless-stopped` for long-running deployment.

> **Note:** The checked-in example points `ghcr.io/myk-org/github-webhook-server:latest` at the `build:` key. In standard Docker Compose semantics, a registry reference belongs under `image:`. Use `build:` only when you are pointing at a local build context such as `.`. The important deployment details in the example are the volume mounts, port mapping, and `privileged: true`.

## Health checks

The application exposes a dedicated health endpoint:

```python
@FASTAPI_APP.get(f"{APP_URL_ROOT_PATH}/healthcheck", operation_id="healthcheck")
def healthcheck() -> dict[str, Any]:
    return {"status": requests.codes.ok, "message": "Alive"}
```

The image wires that into the container health check:

```dockerfile
HEALTHCHECK CMD curl --fail http://127.0.0.1:5000/webhook_server/healthcheck || exit 1
```

A healthy container means the web process is up and answering on port `5000`. It does not mean every webhook has been processed successfully.

> **Note:** Webhook delivery handling is asynchronous. The main webhook endpoint returns `200 OK` after validation and queueing, so successful HTTP responses do not automatically mean that all downstream GitHub operations succeeded. For real troubleshooting, check the logs in the mounted `logs/` directory.

## What belongs in `config.yaml`

Most deployment settings are read from the mounted `config.yaml`, not from environment variables.

The checked-in example config shows the expected style:

```yaml
log-level: INFO # Set global log level, change take effect immediately without server restart
log-file: webhook-server.log # Set global log file, change take effect immediately without server restart
mcp-log-file: mcp_server.log # Set global MCP log file, change take effect immediately without server restart
logs-server-log-file: logs_server.log # Set global Logs Server log file, change take effect immediately without server restart
mask-sensitive-data: true

github-app-id: 123456

webhook-ip: <HTTP://IP OR URL:PORT/webhook_server> # Full URL with path
```

If you use the server's container-build automation, the per-repository container settings also live in `config.yaml`:

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    container:
      username: <registry username>
      password: <registry_password>
      repository: <registry_repository_full_path>
      tag: <image_tag>
      release: true
      build-args:
        - my-build-arg1=1
        - my-build-arg2=2
      args:
        - --format docker
```

For containerized deployments, put these runtime settings in `config.yaml`:

- `webhook-ip`
- `ip-bind`
- `port`
- `max-workers`
- `webhook-secret`
- `verify-github-ips`
- `verify-cloudflare-ips`

> **Warning:** The checked-in Compose example shows `MAX_WORKERS`, `WEBHOOK_SERVER_IP_BIND`, `WEBHOOK_SERVER_PORT`, `WEBHOOK_SECRET`, `VERIFY_GITHUB_IPS`, and `VERIFY_CLOUDFLARE_IPS` as environment variables, but the application code reads those values from `config.yaml` keys (`max-workers`, `ip-bind`, `port`, `webhook-secret`, `verify-github-ips`, and `verify-cloudflare-ips`). The environment variables consumed directly at runtime are `WEBHOOK_SERVER_DATA_DIR`, `ENABLE_LOG_SERVER`, and `ENABLE_MCP_SERVER`. The Podman cleanup script also reads `PUID`. `PGID` appears in the example, but the application code does not read it.

> **Note:** `ENABLE_LOG_SERVER` and `ENABLE_MCP_SERVER` are enabled only when they are set to the literal string `true`.

> **Note:** `webhook-ip` must be the external URL GitHub should call, and it must include the `/webhook_server` path. If you change `webhook-ip` or `webhook-secret`, restart the container so the startup webhook reconciliation can update GitHub with the new values.

## Startup behavior and operational caveats

Container startup does more than launch Uvicorn. The entrypoint runs Podman cleanup and repository/webhook setup first:

```python
if __name__ == "__main__":
    # Run Podman cleanup before starting the application
    run_podman_cleanup()

    result = asyncio.run(repository_and_webhook_settings(webhook_secret=_webhook_secret))

    uvicorn.run(
        "webhook_server.app:FASTAPI_APP",
        host=_ip_bind,
        port=int(_port),
        workers=int(_max_workers),
        reload=False,
    )
```

That leads to a few operational caveats that are worth planning for:

- Startup depends on valid mounted configuration. If `config.yaml` or `webhook-server.private-key.pem` is missing, the container will not start cleanly.
- Startup also depends on GitHub access. Before the server begins listening, it reconciles repository settings and creates or updates GitHub webhooks using the configured `webhook-ip`.
- If `verify-github-ips` or `verify-cloudflare-ips` is enabled, the app fetches allowlists at startup. If verification is enabled but no valid networks can be loaded, startup fails closed for security.
- The second volume mount is intentionally disposable. The cleanup script removes stale runtime directories under `/tmp/storage-run-${PUID}` and then prunes stopped containers, dangling images, unused volumes, and unused networks from the nested Podman environment.
- Use a dedicated host path for that nested Podman mount. Do not point it at shared or important host storage.
- The checked-in build path for repository image automation uses Podman inside the container and builds with `--network=host`. That is one reason the example deployment keeps `privileged: true`.

> **Warning:** `ENABLE_LOG_SERVER=true` exposes `/logs`, `/logs/api/*`, and `/logs/ws` without authentication. `ENABLE_MCP_SERVER=true` exposes `/mcp` without authentication. Treat both as internal-only endpoints and place them behind a trusted network or an authenticated reverse proxy.

> **Note:** The webhook receiver and health check live under `/webhook_server`, but the optional log viewer lives under `/logs` and the optional MCP endpoint lives under `/mcp`. If you deploy behind a reverse proxy or ingress, route those paths explicitly.

> **Tip:** Plan for log retention. The structured webhook logs are written as daily `webhooks_YYYY-MM-DD.json` files, and the code documents them as unbounded in size. Text logs are safer to rotate, but the JSON webhook summaries still need external cleanup or retention policies on long-running deployments.
