# Webhook and Health API

`github-webhook-server` exposes two primary HTTP endpoints under the same `/webhook_server` prefix:

- `POST /webhook_server` receives GitHub webhook deliveries.
- `GET /webhook_server/healthcheck` provides a lightweight liveness check.

The webhook endpoint is intentionally designed to return quickly, then continue the real work in the background. That keeps GitHub from timing out while the server does slower tasks such as GitHub API calls, repository cloning, PR checks, labels, comments, and other automation.

## POST `/webhook_server`

Use `POST /webhook_server` as the webhook target in GitHub. The configured public URL must include the full path, not just the host:

```17:17:examples/config.yaml
webhook-ip: <HTTP://IP OR URL:PORT/webhook_server> # Full URL with path (e.g., https://your-domain.com/webhook_server or https://smee.io/your-channel)
```

Per-repository `events` in `config.yaml` control which GitHub events the server subscribes to when it creates or updates webhooks on startup:

```150:157:examples/config.yaml
    events: # To listen to all events do not send events
      - push
      - pull_request
      - pull_request_review
      - pull_request_review_thread
      - issue_comment
      - check_run
      - status
```

If you leave out `events`, the webhook registration logic falls back to all events. If you configure `webhook-secret`, the server also includes that same secret when it creates the webhook in GitHub.

### What the endpoint validates before it accepts a delivery

A webhook only needs a small set of fields to be accepted at the HTTP layer:

- `X-GitHub-Event` must be present.
- The request body must be readable and valid JSON.
- The JSON must include `repository.name` and `repository.full_name`.
- If `webhook-secret` is configured, the request must include a valid `X-Hub-Signature-256` HMAC SHA256 signature.
- If IP verification is enabled, the client IP must match the loaded GitHub and/or Cloudflare allowlist.

`X-GitHub-Delivery` is not required, but you should send it. The server echoes it back in the response and uses it for log correlation. If it is missing, the server uses `unknown-delivery`.

A successful request in the test suite uses the standard GitHub-style headers and gets back the queue acknowledgement response:

```88:102:webhook_server/tests/test_app.py
        headers = {
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "test-delivery-123",
            "x-hub-signature-256": signature,
            "Content-Type": "application/json",
        }

        response = client.post("/webhook_server", content=payload_json, headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == 200
        assert data["message"] == "Webhook queued for processing"
        assert data["delivery_id"] == "test-delivery-123"
        assert data["event_type"] == "pull_request"
```

> **Note:** The server reads the raw body and parses JSON itself. In practice, the important part is that the body is valid JSON; `Content-Type` is not what determines acceptance.

### Response codes

| Status | Meaning |
| --- | --- |
| `200 OK` | The request passed front-door validation and was queued for background processing. |
| `400 Bad Request` | Missing `X-GitHub-Event`, unreadable body, invalid JSON, missing `repository`, missing `repository.name`, missing `repository.full_name`, or invalid client IP metadata when IP filtering is active. |
| `403 Forbidden` | Missing or invalid `X-Hub-Signature-256` when `webhook-secret` is enabled, or client IP is outside the configured allowlist. |
| `500 Internal Server Error` | The server could not load the configuration it needed to validate the request. |

Tests also confirm two practical edge cases:

- A bad signature returns `403` with `Request signatures didn't match`.
- If no `webhook-secret` is configured, the same endpoint still accepts a valid JSON delivery without signature verification.

> **Warning:** If `webhook-secret` is unset, the endpoint accepts unsigned webhook payloads. That may be fine on a private test setup, but it is not a safe default for an internet-exposed deployment.

### What `200 OK` really means

The most important thing to understand about `POST /webhook_server` is that `200` means “queued,” not “finished.”

Once the request passes validation, the server starts a background task and returns immediately:

```507:529:webhook_server/app.py
    # Start background task immediately using asyncio.create_task
    # This ensures the HTTP response is sent immediately without waiting
    # Store task reference for observability and graceful shutdown
    task = asyncio.create_task(
        process_with_error_handling(
            _hook_data=hook_data,
            _headers=request.headers,
            _delivery_id=delivery_id,
            _event_type=event_type,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    # Return 200 immediately with JSONResponse for fastest serialization
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": status.HTTP_200_OK,
            "message": "Webhook queued for processing",
            "delivery_id": delivery_id,
            "event_type": event_type,
        },
    )
```

This design exists to avoid GitHub webhook timeouts. The server only does just enough validation to know the delivery can be processed. The slower work happens after the response has already been sent.

In practice, that means:

- `200 OK` means the request was valid enough to enter the processing pipeline.
- `200 OK` does **not** mean the automation finished successfully.
- Repository lookup failures, GitHub API failures, and handler errors can still happen later.
- Those later failures are logged, but they do not change the already-sent HTTP response.

> **Tip:** Treat the returned `delivery_id` as your main breadcrumb. It is the quickest way to match a GitHub delivery to the server’s background-processing logs.

### What happens after the delivery is queued

After the webhook is queued, the background processor routes the event. The code handles GitHub events including `ping`, `push`, `pull_request`, `pull_request_review`, `pull_request_review_thread`, `issue_comment`, `check_run`, and `status`.

Not every accepted delivery turns into visible automation. Some are intentionally accepted and then skipped in the background, for example:

- a `ping` event
- a `status` event that is still `pending`
- a `check_run` that is not yet `completed`
- a push deletion event
- an event that does not resolve to an open pull request

That is why the HTTP response should be treated as an acknowledgement, not a final execution result.

### Tracing a delivery

Structured webhook logs are written as daily JSONL files under the server data directory in `logs/webhooks_YYYY-MM-DD.json`. With the default data directory, that is `/home/podman/data/logs/`. Deployments can override the data directory with `WEBHOOK_SERVER_DATA_DIR`.

The `delivery_id` returned by the endpoint corresponds to the same delivery identifier used in the logs as `hook_id`, so it is the best way to trace what happened after the request was queued.

### IP allowlist behavior

If you enable `verify-github-ips` and/or `verify-cloudflare-ips`, the endpoint enforces source-IP filtering before it even parses the webhook body. When those settings are off, the webhook endpoint does not perform source-IP checks.

That hardening also affects startup: the application loads the allowlists during startup, and if IP verification is enabled but no valid networks can be loaded, the server fails closed rather than starting in an insecure state.

> **Warning:** With IP verification enabled, a startup failure to load valid GitHub and/or Cloudflare ranges means the API will not come up at all. That is intentional fail-closed behavior.

## GET `/webhook_server/healthcheck`

`GET /webhook_server/healthcheck` is a simple liveness endpoint. If the application is up and serving requests, it returns:

```307:309:webhook_server/app.py
@FASTAPI_APP.get(f"{APP_URL_ROOT_PATH}/healthcheck", operation_id="healthcheck")
def healthcheck() -> dict[str, Any]:
    return {"status": requests.codes.ok, "message": "Alive"}
```

This endpoint is intentionally lightweight. It does not do a live GitHub API call, repository lookup, or end-to-end webhook test on every request. It simply answers whether the application process is up and serving HTTP.

That makes it a good fit for container and load-balancer health checks. The project’s `Dockerfile` uses it exactly that way:

```88:90:Dockerfile
HEALTHCHECK CMD curl --fail http://127.0.0.1:5000/webhook_server/healthcheck || exit 1

ENTRYPOINT ["tini", "--", "uv", "run", "entrypoint.py"]
```

> **Tip:** Use `/webhook_server/healthcheck` to answer “is the server alive?” Use a real GitHub delivery plus the webhook logs to answer “is my automation working end to end?”

## Troubleshooting checklist

If GitHub shows `200 OK` but nothing happened, check these first:

- The configured `webhook-ip` includes the exact `/webhook_server` path.
- The repository exists in `config.yaml`.
- The repository’s `events` list includes the event GitHub actually sent.
- `webhook-secret` matches GitHub’s configured secret, if you use one.
- The request source IP is allowed, if IP verification is enabled.
- The returned `delivery_id` appears in that day’s structured log file.

The core rule for this API is simple: `POST /webhook_server` returns fast so GitHub can move on. The real answer about success or failure lives in the background processing logs, not in the HTTP `200` alone.
