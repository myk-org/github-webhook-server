Let me explore the repository to understand the Docker deployment setup.

Now let me check how config.yaml maps environment variables to config keys:

I see — these settings are actually in `config.yaml`, not env vars. Let me check the docker-compose env vars more closely to understand the mapping:

Now let me check how the docker-compose environment variables get mapped to config. It appears they may be handled through config.yaml rather than being direct env var to code mappings:

Now let me check for additional details about ports and the GitHub App PEM key:

Now let me check the e2e test setup for docker compose details:

Let me look at the image reference more closely since the docker-compose.yaml uses `build:` instead of `image:`:

I notice the `build:` field is being used with a registry URL, which is unusual for docker-compose. This appears to be `image:` in intent. Let me verify by checking if there's a different docker-compose format being used:

Now I have enough information. Let me also verify how the docker-compose uses `image:` vs `build:`:

The example docker-compose uses `build: ghcr.io/myk-org/github-webhook-server:latest` which is technically an error - this should be `image:`. I'll document this correctly using `image:` since that's the intended semantics (pulling a pre-built image from a registry).

Now I have everything I need. Let me write the documentation:

# Deploying with Docker

Run the GitHub webhook server as a Docker container with persistent configuration, health monitoring, and automatic restarts — the recommended way to deploy for production use.

## Prerequisites

- Docker Engine 20.10+ and Docker Compose V2 installed
- A GitHub personal access token (classic or fine-grained) with repository access
- A `config.yaml` file with at least one repository configured (see [Configuring Repositories](configuring-repositories.html))
- Network access from GitHub to your server on port 5000 (direct or via reverse proxy)

## Quick Start

1. Create a data directory and add your configuration:

```bash
mkdir -p webhook_server_data_dir
```

2. Create a minimal `webhook_server_data_dir/config.yaml`:

```yaml
github-tokens:
  - ghp_your_token_here

webhook-ip: https://your-domain.com/webhook_server

repositories:
  my-repo:
    name: my-org/my-repository
```

3. Create a `docker-compose.yaml`:

```yaml
services:
  github-webhook-server:
    container_name: github-webhook-server
    image: ghcr.io/myk-org/github-webhook-server:latest
    volumes:
      - "./webhook_server_data_dir:/home/podman/data:Z"
      - "/tmp/podman-storage-${USER:-1000}:/tmp/storage-run-1000"
    ports:
      - "5000:5000"
    privileged: true
    restart: unless-stopped
```

4. Start the server:

```bash
docker compose up -d
```

5. Verify it's running:

```bash
curl http://localhost:5000/webhook_server/healthcheck
```

You should see `{"status": 200, "message": "Alive"}`.

## Step-by-Step Setup

### 1. Prepare the Data Directory

The container expects your configuration files at `/home/podman/data` inside the container. Mount a local directory to this path.

Your data directory should contain:

- **`config.yaml`** (required) — server and repository configuration
- **`webhook-server.private-key.pem`** (optional) — GitHub App private key, only needed if using a GitHub App instead of personal access tokens

```
webhook_server_data_dir/
├── config.yaml
└── webhook-server.private-key.pem   # optional
```

> **Note:** Log files are also written to this directory. Make sure the directory is writable by the container user (UID 1000 by default).

### 2. Configure docker-compose.yaml

Here is the full production-ready `docker-compose.yaml` with all available options:

```yaml
services:
  github-webhook-server:
    container_name: github-webhook-server
    image: ghcr.io/myk-org/github-webhook-server:latest
    volumes:
      - "./webhook_server_data_dir:/home/podman/data:Z"
      # Mount temporary directories to prevent boot ID mismatch issues
      - "/tmp/podman-storage-${USER:-1000}:/tmp/storage-run-1000"
      # Mount Google Cloud credentials for Vertex AI (optional)
      # - $HOME/.config/gcloud:/home/podman/.config/gcloud:ro
    environment:
      - TZ=UTC
      - ENABLE_LOG_SERVER=true
      - ENABLE_MCP_SERVER=false
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

> **Warning:** The container requires `privileged: true` because it uses Podman inside the container to build and push container images for your repositories. If you don't use container build features, the server still requires this flag for Podman runtime initialization.

### 3. Configure Volumes

Two volume mounts are important:

| Volume | Container Path | Purpose |
|--------|---------------|---------|
| Data directory | `/home/podman/data` | Config, logs, and GitHub App private key |
| Podman temp storage | `/tmp/storage-run-1000` | Prevents Podman boot ID mismatch issues on container restart |

The `:Z` suffix on the data volume sets the correct SELinux context. Omit it if you're not using SELinux.

### 4. Set Environment Variables

Configure the server behavior through environment variables in the `environment` section:

| Variable | Default | Description |
|----------|---------|-------------|
| `TZ` | System default | Timezone for log timestamps (e.g., `UTC`, `America/New_York`) |
| `ENABLE_LOG_SERVER` | `false` | Enable the built-in log viewer web UI and API |
| `ENABLE_MCP_SERVER` | `false` | Enable the MCP server for AI agent integration |
| `SIDECAR_PORT` | `9100` | Port for the AI sidecar service |

> **Tip:** Server settings like `port`, `ip-bind`, `max-workers`, `webhook-secret`, `verify-github-ips`, and `verify-cloudflare-ips` are configured in `config.yaml`, not as environment variables. See [Configuration Reference](configuration-reference.html) for all options.

For a complete reference of all environment variables, see [Environment Variables](environment-variables.html).

### 5. Expose Ports

The container exposes three ports:

| Port | Service | Expose Externally? |
|------|---------|-------------------|
| 5000 | Webhook server (main API) | Yes — GitHub sends webhooks here |
| 5001 | Internal tool server | No — binds to 127.0.0.1 inside the container |
| 9100 | AI sidecar | No — internal only |

Only port 5000 needs to be published. The other services are internal to the container.

```yaml
ports:
  - "5000:5000"
```

To use a different host port:

```yaml
ports:
  - "8080:5000"
```

### 6. Configure Health Checks

The built-in health check verifies both the main webhook server and the AI sidecar are responding:

```yaml
healthcheck:
  test: ["CMD-SHELL", "curl -f http://localhost:5000/webhook_server/healthcheck && curl -f http://localhost:${SIDECAR_PORT:-9100}/health"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 30s
```

The `start_period` gives the server 30 seconds to initialize before health checks begin failing. Increase this if your server manages many repositories and takes longer to start.

Check container health status:

```bash
docker inspect --format='{{.State.Health.Status}}' github-webhook-server
```

### 7. Start and Verify

```bash
# Start in the background
docker compose up -d

# Watch the logs
docker compose logs -f

# Check health status
docker compose ps
```

The `HEALTHY` status in `docker compose ps` confirms both services are running.

## Advanced Usage

### Configuring Webhook Security

Add these settings to your `config.yaml` to verify that incoming webhooks are genuinely from GitHub:

```yaml
webhook-secret: your-secret-here
verify-github-ips: true
verify-cloudflare-ips: true  # if behind Cloudflare
```

The `webhook-secret` must match the secret configured in your GitHub webhook settings. IP verification fetches GitHub's published IP ranges at startup and rejects requests from other sources.

> **Warning:** If IP verification is enabled but the server cannot reach the GitHub or Cloudflare API at startup, it will refuse to start rather than run in an insecure state.

### Tuning Worker Count

Control concurrency by setting `max-workers` in `config.yaml`:

```yaml
max-workers: 50
```

The default is 10 workers. Increase this for servers handling many repositories or high webhook volume.

### Enabling the Log Viewer

Set `ENABLE_LOG_SERVER=true` in your environment to activate the built-in web UI for browsing webhook processing logs:

```yaml
environment:
  - ENABLE_LOG_SERVER=true
```

> **Warning:** The log viewer endpoints are unauthenticated. Only deploy with log server enabled on trusted networks (VPN, internal network). Access is restricted to private/loopback IP ranges by default.

See [Using the Log Viewer](using-the-log-viewer.html) for details.

### Enabling AI Features

To use AI-powered features (conventional title suggestions, cherry-pick conflict resolution), provide API keys as environment variables:

```yaml
environment:
  - ANTHROPIC_API_KEY=sk-ant-xxx       # For Claude Code
  - GEMINI_API_KEY=xxx                  # For Gemini CLI
  # - CURSOR_API_KEY=xxx                # For Cursor Agent
  # - SIDECAR_PORT=9100                 # AI sidecar port (default: 9100)
```

You also need to configure the `ai-features` section in your `config.yaml`. See [Enabling AI Features](enabling-ai-features.html) for setup details.

### Mounting Google Cloud Credentials

If using Vertex AI for AI features, mount your Google Cloud credentials read-only:

```yaml
volumes:
  - "./webhook_server_data_dir:/home/podman/data:Z"
  - "/tmp/podman-storage-${USER:-1000}:/tmp/storage-run-1000"
  - "$HOME/.config/gcloud:/home/podman/.config/gcloud:ro"
```

### Updating the Container

```bash
# Pull the latest image
docker compose pull

# Recreate the container with the new image
docker compose up -d
```

Your configuration and logs persist in the mounted data directory, so updates are safe.

## Troubleshooting

### Container exits immediately

Check the logs for configuration errors:

```bash
docker compose logs
```

The most common cause is a missing or invalid `config.yaml`. The server requires at least a `repositories` section with one entry.

### Health check fails

If `docker compose ps` shows `unhealthy`:

```bash
# Check which health check is failing
docker exec github-webhook-server curl -f http://localhost:5000/webhook_server/healthcheck
docker exec github-webhook-server curl -f http://localhost:9100/health
```

The main server health check failing usually means a startup error. The sidecar health check failing means AI features won't work but doesn't affect core webhook processing.

### Podman boot ID mismatch

If you see errors about Podman boot ID mismatches after container restarts, ensure the temp storage volume is mounted:

```yaml
volumes:
  - "/tmp/podman-storage-${USER:-1000}:/tmp/storage-run-1000"
```

The container runs an automatic cleanup script on startup to clear stale Podman runtime state.

### Permission denied on data directory

The container runs as UID 1000. Ensure your data directory is owned by this user:

```bash
chown -R 1000:1000 webhook_server_data_dir/
```

## Related Pages

- [Getting Started](quickstart.html)
- [Environment Variables](environment-variables.html)
- [Configuring Repositories](configuring-repositories.html)
- [Using the Log Viewer](using-the-log-viewer.html)
- [Enabling AI Features](enabling-ai-features.html)
