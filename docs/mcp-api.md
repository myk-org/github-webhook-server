# MCP API

`github-webhook-server` optionally exposes a Model Context Protocol (MCP) endpoint that allows AI agents to interact with the server's data. You want to enable this if you are running an AI sidecar or agent that needs read-only access to webhook logs and pull request flows.

## Prerequisites

- A deployed instance of the webhook server.
- The ability to modify the server's environment variables.
- An AI client capable of communicating over the MCP standard via HTTP.

## Quick Example

The fastest way to expose the endpoint is to start the server with the feature flag enabled via environment variables:

```bash
# Start the server with MCP enabled
ENABLE_MCP_SERVER=true uv run entrypoint.py
```

If you use Docker Compose, pass it in the `environment` block:

```yaml
services:
  github-webhook-server:
    image: ghcr.io/your-org/github-webhook-server:latest
    environment:
      - ENABLE_MCP_SERVER=true
```

## Enabling the MCP Endpoint

By default, the server only processes GitHub webhooks and keeps its internal APIs closed. To open the MCP interface, you must explicitly enable it.

1. Set the environment variable `ENABLE_MCP_SERVER=true` on the host or container running the server.
2. Restart the webhook server process.
3. Point your AI client or sidecar to `http://<your-server-host>:<port>/mcp`.

> **Note:** The endpoint listens on the same port as the main webhook server but operates strictly on the `/mcp` path.

## Advanced Usage

### Customizing the MCP Log File

Because AI agent traffic can be noisy, MCP logs are kept separate from the main webhook processing logs. By default, MCP requests write to `mcp_server.log` inside your data directory. You can override this name in your global `config.yaml`.

```yaml
# config.yaml
mcp-log-file: custom_mcp.log
```

> **Tip:** You must restart the server if you change `mcp-log-file` or `ENABLE_MCP_SERVER`, as logging bindings are established at startup.

### Proxy and Ingress Configuration

If you run the server behind a reverse proxy (like Nginx, Traefik, or Cloudflare Tunnels), you must ensure it forwards the necessary HTTP methods. The MCP implementation requires your proxy to pass:
- `GET`
- `POST`
- `DELETE`

If your proxy blocks these requests or filters out payloads, the MCP integration will fail to initialize.

### Security and Network Isolation

> **Warning:** The `/mcp` endpoint has no built-in authentication. It is designed to be an internal interface.

Do not expose `/mcp` directly to the public internet. Secure the endpoint using one of these strategies:
- Run the AI sidecar on the same localhost or container network as the webhook server.
- Bind the server to a private internal network IP.
- Place an authenticated reverse proxy in front of `/mcp` that terminates TLS and enforces client certificates or token authentication.

See [Security Configuration](security-configuration.html) for more context on securing exposed ports.

## Troubleshooting

- **Endpoint returns 404:** Verify that the server environment contains exactly `ENABLE_MCP_SERVER=true` (lowercase). The feature relies on exact string matching.
- **Client times out or fails to connect:** Ensure your reverse proxy permits `GET`, `POST`, and `DELETE` requests directly to `/mcp`.
- **Can't find MCP logs:** Check for `mcp_server.log` inside the `logs/` folder of your configured data directory. If no file is created, ensure the directory is writable by the server user.

## Related Pages

- [AI Features and Test Oracle](ai-features-and-test-oracle.html)
- [Webhook and Health API](webhook-and-health-api.html)
- [Log Viewer API](log-viewer-api.html)
