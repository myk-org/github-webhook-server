# Security Configuration

A secure production deployment usually needs four things: a webhook secret, the right source-IP allowlist, masked logs, and strict network boundaries for optional admin/debug endpoints.

> **Note:** The server reads `config.yaml` from `WEBHOOK_SERVER_DATA_DIR` (default `/home/podman/data`). `webhook-secret`, `verify-github-ips`, and `verify-cloudflare-ips` are global settings. `mask-sensitive-data` can be global or per repository. `ENABLE_LOG_SERVER` and `ENABLE_MCP_SERVER` are environment variables.

> **Tip:** Use secret validation and IP allowlisting together. The secret proves the payload was signed by GitHub. The allowlist limits who can reach the endpoint at all.

## Webhook Secret Validation

If `webhook-secret` is set, the server validates the incoming `x-hub-signature-256` header before it queues the webhook for background processing. The comparison is HMAC-SHA256 over the raw request body:

```python
if not signature_header:
    raise HTTPException(status_code=403, detail="x-hub-signature-256 header is missing!")

hash_object = hmac.new(secret_token.encode("utf-8"), msg=payload_body, digestmod=hashlib.sha256)
expected_signature = "sha256=" + hash_object.hexdigest()

if not hmac.compare_digest(expected_signature, signature_header):
    raise HTTPException(status_code=403, detail="Request signatures didn't match!")
```

In practice:

- Set the same value in GitHub and in the server's root `config.yaml` as `webhook-secret`.
- A missing or invalid signature is rejected with `403`.
- If `webhook-secret` is not set, signature checking is skipped.

> **Warning:** Do not leave `webhook-secret` unset on an internet-facing server.

If the server manages repository webhooks for you, it also includes that secret when creating the hook:

```python
config_: dict[str, str] = {"url": webhook_ip, "content_type": "json"}

if secret:
    config_["secret"] = secret
```

There is one important rotation caveat. The webhook-management code can detect when you moved between "no secret" and "secret configured", but it does not compare one non-empty secret value to another:

```python
secret_presence_mismatch = bool(_hook.config.get("secret")) != bool(secret)
if secret_presence_mismatch:
    LOGGER.info(f"[API user {api_user}] - {full_repository_name}: Deleting old webhook")
    _hook.delete()
```

> **Note:** If you rotate from one non-empty secret to another, update the GitHub-side webhook secret too. Otherwise GitHub can keep signing with the old value while the server starts validating against the new one.

## GitHub And Cloudflare IP Allowlists

The server can optionally restrict the webhook endpoint to GitHub or Cloudflare source networks. This check is applied to the webhook endpoint, not to the optional log or MCP endpoints.

Use the mode that matches your traffic path:

- Enable `verify-github-ips` when GitHub delivers webhooks directly to the server.
- Enable `verify-cloudflare-ips` when Cloudflare proxies traffic to the server.
- Enable both if you intentionally accept both delivery paths. The server merges both CIDR sets and accepts a request if it matches either source.
- Leave both unset or `false` if you do not want source-IP filtering.

At startup, the app loads the enabled CIDR lists and fails closed if verification was requested but no valid networks were available:

```python
if networks:
    ALLOWED_IPS = tuple(networks)
    LOGGER.info(f"IP allowlist initialized successfully with {len(ALLOWED_IPS)} networks.")
elif verify_github_ips or verify_cloudflare_ips:
    # Fail-close: If IP verification is enabled but no networks loaded, reject all requests
    LOGGER.error("IP verification enabled but no valid IPs loaded - failing closed for security")
    raise RuntimeError(
        "IP verification enabled but no allowlist loaded. "
        "Cannot start server in insecure state. "
        "Check network connectivity to GitHub/Cloudflare API endpoints."
    )
```

The upstream sources are:

- GitHub: `https://api.github.com/meta` using the `hooks` CIDR list
- Cloudflare: `https://api.cloudflare.com/client/v4/ips`

> **Warning:** The allowlist check uses the client IP the app actually sees in `request.client.host`. If another reverse proxy or load balancer sits in front of the app, you may end up validating the proxy IP instead of GitHub or Cloudflare.

> **Note:** The CIDR lists are fetched during startup, not continuously. Restart the service if you need to pick up upstream IP-range changes.

> **Note:** These allowlists protect only the `POST /webhook_server` webhook endpoint. They do not secure `/logs/*` or `/mcp`.

## Sensitive-Data Masking

The logging layer masks sensitive data by default. That includes common credential-like values such as passwords, secrets, tokens, private keys, webhook URLs, and similar auth-related fields. This is a logging safeguard, not a replacement for webhook signature validation or IP allowlisting.

The example configuration keeps masking enabled globally:

```yaml
mask-sensitive-data: true # Mask sensitive data in logs (default: true). Set to false for debugging (NOT recommended in production)
```

You can override it per repository inside the main `config.yaml`:

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    log-level: DEBUG # Override global log-level for repository
    log-file: my-repository.log # Override global log-file for repository
    mask-sensitive-data: false # Override global setting - disable masking for debugging this specific repo (NOT recommended in production)
```

This is useful when you are debugging a single repository, but it should be temporary.

> **Warning:** Turning masking off can leak credentials into normal log files and exported JSON webhook logs. Use it only for short-lived debugging on a trusted system, then turn it back on.

## Restrict Optional Admin Endpoints

The server also has optional log-viewer and MCP endpoints. These are operationally useful, but they should be treated as internal-only.

The compose example shows how they are enabled:

```yaml
- ENABLE_LOG_SERVER=true # Enable log viewer endpoints (default: false)
- ENABLE_MCP_SERVER=false # Enable MCP server for AI agent integration (default: false)
```

The app checks for the literal string `true` when enabling either feature.

When `ENABLE_LOG_SERVER=true`, the app exposes the log viewer routes, including:

- `/logs`
- `/logs/api/entries`
- `/logs/api/export`
- `/logs/api/pr-flow/{hook_id}`
- `/logs/api/workflow-steps/{hook_id}`
- `/logs/api/step-logs/{hook_id}/{step_name}`
- `/logs/ws`

When `ENABLE_MCP_SERVER=true`, the app exposes `/mcp`.

The only log endpoint with an extra built-in network restriction is the step-log route:

```python
@FASTAPI_APP.get(
    "/logs/api/step-logs/{hook_id}/{step_name}",
    operation_id="get_step_logs",
    dependencies=[Depends(require_log_server_enabled), Depends(require_trusted_network)],
)
```

That trusted-network check allows only private, loopback, or link-local client addresses. The rest of the log viewer is feature-flagged but not protected by that extra network gate. The MCP endpoint is also created without built-in authentication.

> **Warning:** Treat the entire log viewer as unauthenticated internal tooling. Most `/logs/*` routes are not protected by anything beyond the feature flag.

> **Warning:** The `/mcp` endpoint is unauthenticated as well. If you enable it, keep it on a VPN or internal network, or put it behind a reverse proxy with authentication and TLS.

> **Tip:** The safest production default is to leave `ENABLE_LOG_SERVER` and `ENABLE_MCP_SERVER` disabled unless you actively need them.

## Production Checklist

- Set `webhook-secret` and keep the same value in GitHub.
- Enable `verify-github-ips` or `verify-cloudflare-ips` to match your network path.
- Leave `mask-sensitive-data: true`.
- Keep `/logs/*` and `/mcp` off the public internet.
- If remote access is unavoidable, add authentication at the reverse proxy and keep origin access limited to trusted networks.
