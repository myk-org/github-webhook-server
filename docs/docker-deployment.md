# Docker and Container Deployment

Deploying the webhook server as a container provides an isolated environment with all dependencies pre-installed, including git, build tools, and the AI sidecar integration. This guide covers how to run the server using Docker or Podman.

## Prerequisites

* Docker Compose or Podman Compose installed.
* A prepared configuration file (`config.yaml`) and GitHub App private key (`webhook-server.private-key.pem`). See [Configuration Reference](configuration-reference.html).
* An empty directory to mount for application data and logs.

## Quick Example

Save the following configuration as `docker-compose.yaml` and start it with `docker-compose up -d`:

```yaml
services:
  github-webhook-server:
    container_name: github-webhook-server
    image: ghcr.io/myk-org/github-webhook-server:latest
    volumes:
      - "./webhook_server_data_dir:/home/podman/data:Z"
      - "/tmp/podman-storage-${USER:-1000}:/tmp/storage-run-1000"
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=UTC
      - WEBHOOK_SERVER_IP_BIND=0.0.0.0
      - WEBHOOK_SERVER_PORT=5000
      - WEBHOOK_SECRET=your_secret_here
      - VERIFY_GITHUB_IPS=1
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

## Step-by-Step Deployment

1. **Create the data directory**
   Create a local folder to hold the persistent configuration and logs.
   ```bash
   mkdir -p ./webhook_server_data_dir/logs
   ```

2. **Add configuration files**
   Place your `config.yaml` and `webhook-server.private-key.pem` files directly inside `./webhook_server_data_dir/`. The container will look for these files on startup.

3. **Start the container**
   Run the service in detached mode:
   ```bash
   docker-compose up -d
   ```

4. **Verify the deployment**
   Check that both the main webhook server and the sidecar have passed their health checks:
   ```bash
   docker ps | grep github-webhook-server
   ```
   You should see `(healthy)` in the status output indicating both services are running.

## Advanced Usage

### Security and Network Verification

By default, it is highly recommended to verify that inbound requests originate from valid sources. Enable IP validation via environment variables:
* `VERIFY_GITHUB_IPS=1`: Validates against GitHub's published webhook IP ranges.
* `VERIFY_CLOUDFLARE_IPS=1`: Validates against Cloudflare IPs if running behind a proxy.

See [Security Configuration](security-configuration.html) for detailed networking guidelines.

### Integrating AI Features

The server includes a Node.js-based "sidecar" that facilitates AI code generation and review.

Enable these features by setting specific environment variables in your compose file:
* `ENABLE_MCP_SERVER=true`: Enables the MCP server for AI agent integration.
* `VERTEX_CLAUDE_1M=true`: Enables Claude 1M context window models via Vertex AI.
* `ANTHROPIC_API_KEY=your_key`: Grants access to Claude.
* `GEMINI_API_KEY=your_key`: Grants access to Google Gemini models.

If you are using Google Cloud (Vertex AI), mount your local credentials into the container so the service can authenticate:
```yaml
    volumes:
      - $HOME/.config/gcloud:/home/podman/.config/gcloud:ro
```

> **Note:** For Cursor interactive login, you must execute into the running container using `docker exec -it github-webhook-server agent`.

### Adjusting Concurrency

If you manage a large organization with hundreds of repositories, increase the maximum number of asynchronous workers:
```yaml
    environment:
      - MAX_WORKERS=50  # Defaults to 10
```

## Troubleshooting

### Container Requires Privileged Mode
The base image requires the container to run in privileged mode (`privileged: true`) to properly handle inner container workloads, system user modifications, and repository isolation during background jobs. If you omit this, the container will crash at startup.

### Boot ID Mismatch
If you see storage initialization errors or "boot ID mismatch" failures in the container logs, ensure you are mounting a temporary directory for Podman's internal storage run path:
```yaml
    volumes:
      - "/tmp/podman-storage-${USER:-1000}:/tmp/storage-run-1000"
```
Also ensure the `:Z` flag is present on your data directory mount if you are running on an SELinux-enforced host.

### Health Check Failures
The built-in health check tests both the main application and the Node.js sidecar helper:
```bash
curl -f http://localhost:5000/webhook_server/healthcheck
curl -f http://localhost:9100/health
```
If the container shows as `unhealthy`, inspect the logs using `docker logs github-webhook-server` to determine if either the main server failed to bind to its port or the sidecar crashed. See [Troubleshooting](troubleshooting.html) for deeper application-level issues.

## Related Pages

- [Installation](installation.html)
- [Logging and Data Files](logging-and-data-files.html)
- [Security Configuration](security-configuration.html)
