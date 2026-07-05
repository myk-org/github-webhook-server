# Architecture and Event Flow

Track how incoming GitHub webhooks are routed, processed in the background, and executed so you can effectively monitor and audit repository automation. This guide walks you through the lifecycle of an event from initial intake to structured log output.

* Server configured and receiving events from GitHub.
* Access to the data directory (`WEBHOOK_SERVER_DATA_DIR`) for log auditing.

Send a payload to the webhook endpoint to see how the server accepts it for asynchronous processing:

```bash
curl -X POST http://127.0.0.1:5000/webhook_server \
  -H "X-GitHub-Event: pull_request" \
  -H "X-GitHub-Delivery: 72d3162e-cc78-11e3-81ab-4c9367dc0958" \
  -H "X-Hub-Signature-256: sha256=YOUR_SECRET_HASH" \
  -d '{"action": "opened", "repository": {"full_name": "org/repo"}, "pull_request": {"number": 1}}'
```

Output:
```json
{"status": "accepted", "message": "Event queued for background processing"}
```

1. **Webhook Intake Pipeline**
   When GitHub sends a webhook, the server validates the secret signature and immediately returns an HTTP 200 response. This fail-fast intake ensures GitHub never times out waiting for long-running automations. See [Webhook and Health API](webhook-and-health-api.html) for endpoint details.

2. **Background Processing Model**
   Once accepted, the event payload is placed into an in-memory background queue. Background workers pull events from this queue, ensuring that heavy operations do not block new incoming webhooks.

3. **Handler Architecture**
   The worker inspects the `X-GitHub-Event` header and the JSON `action` field to route the payload to the correct handler. If an event is not configured or lacks a handler, it is safely dropped. See [Supported GitHub Events](supported-github-events.html) for a complete list of handled actions.

4. **Repository Cloning and Worktrees**
   If an automation requires modifying files (such as AI auto-fixes or merge conflict resolution), the server clones the target repository into its data directory. To handle multiple concurrent pull requests safely, it uses isolated `git worktree` environments instead of modifying the main checkout.

5. **Structured Logging Flow**
   As the handler executes, it tracks its progress using step-scoped contexts. Every action is recorded in structured JSONL format, automatically attaching the repository name, pull request number, and event delivery ID.

## Advanced Usage

### Concurrency and Rate Limits
The background processing model automatically scales up to the configured worker limit. When interacting with GitHub, the system handles API rate limits by automatically pausing or seamlessly failing over to secondary GitHub tokens if configured. See [Configuration Reference](configuration-reference.html) for token failover setup.

### Bypassing Cache for Fresh Data
Because webhooks can arrive out of order, the server aggressively uses live API queries rather than relying on stale webhook payloads for critical checks (like verifying if a PR is draft or checking current labels).

### Sidecar Bridge Integration
For AI-driven features that require complex prompt generation or execution environments, the background workers delegate tasks to a separate helper bridge rather than running them in the main Python process.

## Troubleshooting

* **Webhook returns 200 but nothing happens:** The event might be intentionally ignored by the handler logic (e.g., processing a draft PR when draft processing is disabled). Check the step logs in your data directory to see the skip reason.
* **Git worktree creation fails:** Verify the server has sufficient disk space in `WEBHOOK_SERVER_DATA_DIR` and that the GitHub app credentials have read/write access to repository contents.
* **Missing log output:** Ensure you are inspecting the correct repository's JSONL file. Logs are separated by repository and date. See [Log Viewer Guide](log-viewer-guide.html) to filter streams effectively. For general issues, check [Troubleshooting](troubleshooting.html).

## Related Pages

- [Introduction](introduction.html)
- [Configuration Model](configuration-model.html)
- [Webhook and Health API](webhook-and-health-api.html)
