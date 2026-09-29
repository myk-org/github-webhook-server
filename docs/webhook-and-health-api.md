# Webhook and Health API

The server exposes exactly two routes on the public surface: a health probe and the GitHub webhook receiver. Everything else (`/logs`, `/logs/api/*`, `/mcp`, `/static`) is optional or internal and is only registered when the matching feature flag is enabled.

| Method | Path | Purpose | Registered when |
| --- | --- | --- | --- |
| `GET` | `/webhook_server/healthcheck` | Liveness probe | Always |
| `POST` | `/webhook_server` | GitHub webhook receiver | Always |
| `GET` | `/static/*` | CSS/JS assets for the log viewer UI | Always (mounted directory) |
| `GET` | `/logs`, `/logs/api/*` | Log viewer UI and API | `ENABLE_LOG_SERVER=true` |
| `WS` | `/logs/ws` | Real-time log stream | `ENABLE_LOG_SERVER=true` |
| `GET`/`POST`/`DELETE` | `/mcp` | MCP streamable HTTP transport | `ENABLE_MCP_SERVER=true` |

The base path is not configurable. In `webhook_server/app.py`:

```python
APP_URL_ROOT_PATH: str = "/webhook_server"
```

`GET` uses an f-string on that constant, so it resolves to `/webhook_server/healthcheck`:

```python
@FASTAPI_APP.get(f"{APP_URL_ROOT_PATH}/healthcheck", operation_id="healthcheck")
```

`POST` passes the constant itself — **no** trailing segment:

```python
@FASTAPI_APP.post(
    APP_URL_ROOT_PATH,          # -> POST /webhook_server
    operation_id="process_webhook",
    dependencies=[Depends(gate_by_allowlist_ips_dependency)],
    tags=["mcp_exclude"],
)
```

Because the callback path is fixed, `webhook-ip` in `config.yaml` must be the full URL including that path. The server creates the GitHub hook with that exact value and `content_type: "json"`:

```yaml
webhook-ip: https://hooks.example.com/webhook_server
```

The server binds `0.0.0.0:5000` by default (`ip-bind` and `port` in `config.yaml`; see `entrypoint.py`).

## Healthcheck

```http
GET /webhook_server/healthcheck
```

The handler is synchronous and touches no config, no database, and no GitHub API. It always returns `200` with a fixed body:

```json
{
  "status": 200,
  "message": "Alive"
}
```

`status` is `requests.codes.ok` (the integer `200`), not a string. Use it as a container liveness/readiness probe; it does **not** verify GitHub credentials or webhook reachability.

```bash
curl -s http://localhost:5000/webhook_server/healthcheck
```

## Webhook receive

```http
POST /webhook_server
Content-Type: application/json
X-GitHub-Event: pull_request
X-GitHub-Delivery: 72d3162e-cc78-11e3-81ab-4c9367dc0958
X-Hub-Signature-256: sha256=6f1ed002ab5595859014ebf0951522d9a...
```

`POST /webhook_server` is the GitHub callback. The handler validates synchronously and then returns immediately, with the real work running as an `asyncio` background task.

### Request headers

| Header | Required | Used for |
| --- | --- | --- |
| `X-GitHub-Event` | Yes | Event routing. Missing → `400`. |
| `X-GitHub-Delivery` | No | Delivery ID. Defaults to `"unknown-delivery"`; echoed in the response and used to correlate logs. |
| `X-Hub-Signature-256` | Only when `webhook-secret` is set | `sha256=` HMAC verification. Missing or mismatched → `403`. |
| `Content-Type` | — | Body is read raw as bytes for signature verification, then parsed as JSON. |

Only `X-Hub-Signature-256` is checked. The legacy SHA-1 `X-Hub-Signature` header is ignored.

### Request body

Any GitHub event payload, but the following must be present or the request is rejected with `400`:

- `repository`
- `repository.name`
- `repository.full_name`

```json
{
  "action": "opened",
  "repository": {
    "name": "github-webhook-server",
    "full_name": "myakove/github-webhook-server"
  },
  "sender": { "login": "myakove" }
}
```

### Success response

```http
HTTP/1.1 200 OK
content-type: application/json
```

```json
{
  "status": 200,
  "message": "Webhook queued for processing",
  "delivery_id": "72d3162e-cc78-11e3-81ab-4c9367dc0958",
  "event_type": "pull_request"
}
```

`delivery_id` is the `X-GitHub-Delivery` value (or `"unknown-delivery"`), so it can be used directly against the log viewer.

> **Important:** `200` means *queued*, not *processed*. The server answers as soon as validation passes (GitHub times webhooks out at 10 seconds, while processing typically takes 5–30 seconds). Config lookup, repository validation, GitHub API calls, and all handlers run in the background.

### Background processing

`process_webhook` builds a structured context, constructs `GithubWebhook`, calls `await api.process()`, and always calls `await api.cleanup()`. Errors are caught and logged; **none of them change the HTTP response**:

| Background failure | Where it shows up |
| --- | --- |
| `RepositoryNotFoundInConfigError` | Error log, plus a structured log entry with `success: false` |
| `httpx.ConnectError` / `httpx.RequestError` / `requests.ConnectionError` | Error log with traceback, structured log entry with `success: false` |
| Any other exception | Error log with traceback, structured log entry with `success: false` |
| `asyncio.CancelledError` | Re-raised (shutdown), not logged as an error |

Each run writes a structured log record (see [Debug with the Log Viewer](debug-with-the-log-viewer.html)) and logs a summary with the delivery ID prefix. During shutdown the server waits up to 30 seconds for in-flight background tasks, then cancels them.

### Error responses

All errors are FastAPI's standard JSON error shape.

| Status | `detail` | Cause |
| --- | --- | --- |
| `400` | `Missing X-GitHub-Event header` | No `X-GitHub-Event` header |
| `400` | `Failed to read request body` | Body could not be read |
| `400` | `Invalid JSON payload` | Body is not valid JSON |
| `400` | `Missing repository in payload` | No `repository` key |
| `400` | `Missing repository.name in payload` | No `repository.name` |
| `400` | `Missing repository.full_name in payload` | No `repository.full_name` |
| `400` | `Could not determine client IP address` | IP allowlist enabled, no client IP available |
| `400` | `Could not parse client IP address` | IP allowlist enabled, client IP unparseable |
| `403` | `x-hub-signature-256 header is missing!` | `webhook-secret` set, signature header absent |
| `403` | `Request signatures didn't match!` | HMAC mismatch (bad secret or tampered body) |
| `403` | `<ip> IP is not a valid ip in allowlist IPs` | Source IP not in the allowlist |
| `500` | `Configuration error` | Failure while loading config for signature verification |

```json
{ "detail": "Request signatures didn't match!" }
```

Note: signature verification failures return `403`, not `401` (verified in `webhook_server/utils/app_utils.py` and by `test_process_webhook_signature_verification_failure`).

## Authentication

Two independent layers gate `POST /webhook_server`.

### 1. HMAC signature (secret)

Set in the root `config.yaml`:

```yaml
webhook-secret: your-random-secret-string
```

At startup, `entrypoint.py` reads `webhook-secret` and passes it to `repository_and_webhook_settings(webhook_secret=...)`, which forwards it to `create_webhook` → `process_github_webhook`. The hook is created/updated with `config = {"url": webhook-ip, "content_type": "json", "secret": secret}`. If an existing hook's URL matches but its secret presence differs from the configured value, the old hook is deleted and recreated so the secret always matches.

Per request, `verify_signature` computes:

```python
hash_object = hmac.new(secret_token.encode("utf-8"), msg=payload_body, digestmod=hashlib.sha256)
expected_signature = "sha256=" + hash_object.hexdigest()
hmac.compare_digest(expected_signature, signature_header)  # constant-time compare
```

The signature is computed over the **raw request bytes**, so any proxy that re-encodes JSON (pretty-printing, reordering keys) breaks verification. Proxies must forward the body untouched and pass `X-Hub-Signature-256` through.

If `webhook-secret` is **not** set, the signature step is skipped entirely and any caller that can reach the port can post a payload. See [Secure Webhooks and Pull Requests](secure-webhooks-and-pull-requests.html).

### 2. Source-IP allowlist

Set in the root `config.yaml`:

```yaml
verify-github-ips: true
# verify-cloudflare-ips: true
```

During lifespan startup the server fetches the published CIDR ranges (GitHub meta API and/or the Cloudflare list) and builds a network tuple. `POST /webhook_server` runs `gate_by_allowlist_ips_dependency` → `gate_by_allowlist_ips(request, ALLOWED_IPS)` before the handler body:

- allowlist empty → all source IPs accepted (verification off)
- client IP inside any allowlisted network → accepted
- otherwise → `403`

If verification is enabled but no valid ranges load, startup raises `RuntimeError` and the server refuses to start rather than accepting everything. If one source fails but the other succeeds, the server logs the error and continues with what loaded.

The check uses the client IP the app sees. Behind another proxy or load balancer you would be validating that proxy, not GitHub — terminate TLS in front of the server and forward the real address, or verify at the proxy.

## Other route groups (summary)

Full reference: [Log Viewer and MCP API](log-viewer-and-mcp-api.html) and [Debug with the Log Viewer](debug-with-the-log-viewer.html).

### Log viewer — `ENABLE_LOG_SERVER=true`

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/logs` | HTML viewer page. Registered only inside the `if LOG_SERVER_ENABLED:` block. |
| `GET` | `/logs/api/entries` | Filtered, paginated log search (`limit` 1–10000, default 100; `offset` ≥ 0). `404` if the log server is disabled. |
| `GET` | `/logs/api/export` | Log export; `format_type` must match `^json$` (default `json`). |
| `GET` | `/logs/api/pr-flow/{hook_id}` | PR workflow visualization for one delivery. |
| `GET` | `/logs/api/workflow-steps/{hook_id}` | Per-step timeline for one delivery. |
| `GET` | `/logs/api/step-logs/{hook_id}/{step_name}` | Log entries correlated to one step. Adds a trusted-network check: private, loopback, or link-local client IPs only, else `403`. |
| `WS` | `/logs/ws` | Real-time log stream with the same filters as the UI. Closes with `1008` when the log server is disabled. |

Every `/logs/api/*` route depends on `require_log_server_enabled`, which returns `404` with `Log server is disabled. Set ENABLE_LOG_SERVER=true to enable.` when the flag is off.

### MCP — `ENABLE_MCP_SERVER=true`

```python
FASTAPI_APP.add_api_route(
    "/mcp",
    handle_mcp_streamable_http,
    methods=["GET", "POST", "DELETE"],
    include_in_schema=False,
    operation_id="mcp_http",
)
```

Streamable HTTP transport, stateless (`stateless=True`, `json_response=True`), so no session handshake is needed. The route is registered even if the `fastapi_mcp` import failed, so a broken MCP install yields `500` rather than `404`. Routes tagged `mcp_exclude` — which includes `POST /webhook_server` — are not exposed as MCP tools. **No authentication is configured**: deploy on a trusted network or behind an authenticating reverse proxy.

MCP import and session-manager setup are lazy and failure-tolerant: a failed import logs and continues without MCP, and a failed session manager is torn down while the app keeps serving webhooks.

### Static assets

`FASTAPI_APP.mount("/static", StaticFiles(directory="webhook_server/web/static"))` serves the log viewer CSS/JS. Startup fails fast if that directory is missing or is not a directory.

## Full request walkthrough

```bash
# The signing key is whatever you set as webhook-secret in config.yaml.
HMAC_KEY="paste-your-webhook-secret-here"
PAYLOAD='{"action":"opened","repository":{"name":"github-webhook-server","full_name":"myakove/github-webhook-server"},"sender":{"login":"myakove"}}'
SIGNATURE="sha256=$(printf '%s' "$PAYLOAD" | openssl dgst -sha256 -hmac "$HMAC_KEY" | awk '{print $2}')"

curl -i -X POST http://localhost:5000/webhook_server \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: pull_request" \
  -H "X-GitHub-Delivery: 72d3162e-cc78-11e3-81ab-4c9367dc0958" \
  -H "X-Hub-Signature-256: $SIGNATURE" \
  -d "$PAYLOAD"
```

```http
HTTP/1.1 200 OK

{"status":200,"message":"Webhook queued for processing","delivery_id":"72d3162e-cc78-11e3-81ab-4c9367dc0958","event_type":"pull_request"}
```

Then follow that `delivery_id` in the log viewer (`hook_id` filter on `/logs/api/entries`) to see whether processing actually succeeded.

For a live OpenAPI schema, `/docs` and `/redoc` are served by FastAPI (title `webhook-server`) with `POST /webhook_server` hidden from the MCP tool surface via its `mcp_exclude` tag.

## Configuration keys this API depends on

| Key | Effect on this API |
| --- | --- |
| `webhook-ip` | Full callback URL, including the `/webhook_server` path. Registered as the hook target. |
| `webhook-secret` | Enables `X-Hub-Signature-256` verification; also set on managed hooks. |
| `verify-github-ips` | Adds GitHub meta CIDRs to the source-IP allowlist. |
| `verify-cloudflare-ips` | Adds Cloudflare CIDRs to the source-IP allowlist. |
| `ip-bind`, `port`, `max-workers` | Server bind address, port (default `5000`), worker count. |
| `logs-server-log-file`, `mcp-log-file` | Log destinations for the log viewer and MCP server. |

See [Configuration Reference](configuration-reference.html) for every key and [Environment Variables](environment-variables.html) for the feature flags.
