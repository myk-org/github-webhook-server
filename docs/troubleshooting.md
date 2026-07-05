# Troubleshooting

This guide helps you identify and resolve common configuration, runtime, and GitHub integration issues when running the webhook server.

### Prerequisites
* Access to the server's data directory and configuration files.
* Network access to the log viewer (if enabled) or the host terminal.
* Repository admin access to verify webhook delivery payloads in GitHub.

### Quick Example: Inspecting Webhook Delivery Logs

When something doesn't work, the first step is to check if the server received the event and what happened during processing.

```bash
# Tail the primary application log for errors or skipped events
tail -f /path/to/data/logs/github-webhook-server.log | grep -i error

# Or check the structured JSONL logs for a specific event delivery ID
grep "YOUR_GITHUB_DELIVERY_ID" /path/to/data/logs/webhooks.jsonl
```

### Step-by-Step Troubleshooting

Follow these steps to narrow down where the failure is occurring:

1. **Verify Webhook Delivery:** Go to your GitHub Repository Settings > Webhooks. Check the "Recent Deliveries" tab. If there is a red `X`, click the delivery to see the server's response. A `401 Unauthorized` means your secret is mismatched, while a `500` indicates a server crash.
2. **Check Global Configuration Status:** If the server rejects valid GitHub webhooks, ensure `config.yaml` is correctly mounted and valid. See [Configuration Reference](configuration-reference.html).
3. **Inspect the Application Logs:** View the log files to see the processing steps. The server logs skipped events, missing configuration keys, and API errors clearly. See [Logging and Data Files](logging-and-data-files.html).
4. **Use the Log Viewer:** For complex pull request workflows, the built-in UI makes it much easier to track down the exact step that failed. See [Log Viewer Guide](log-viewer-guide.html).

### Common Problems and Solutions

#### Repository Lookup Failures

**Problem:** The server returns an error or logs `Repository not found in configuration` when receiving an event.

**Solution:** The repository must be explicitly defined in your global `config.yaml`.
* Verify the spelling matches the exact `owner/repo` string sent by GitHub.
* Ensure your configuration has been reloaded or the server restarted after adding new repositories.

#### Skipped Draft PR Commands

**Problem:** You comment `/retest` or another command on a Pull Request, but the server ignores it.

**Solution:** By design, many automation actions and commands are ignored on Draft PRs to prevent unnecessary CI usage.
* Mark the PR as "Ready for review" and try the command again.
* Check [Issue Comment Commands](issue-comment-commands.html) to see which commands are explicitly skipped for drafts.

#### Podman and Container Runtime Issues

**Problem:** The container starts but immediately exits, or cannot access the `data/` directory.

**Solution:** This is often caused by SELinux permissions or incorrect volume mounts on Linux hosts.
* When using Podman, append the `:Z` flag to your volume mount so SELinux correctly labels the directory: `-v /my/host/data:/app/data:Z`.
* Ensure the host directory is owned by the same UID/GID that the container runs as (often root by default in non-rootless modes, but check your configuration). See [Docker and Container Deployment](docker-deployment.html).

#### Missing or Overridden Configuration

**Problem:** Global labels or branch protections aren't applying, even though they are set in `config.yaml`.

**Solution:** The target repository might have a local `.github-webhook-server.yaml` file that is overriding the global settings.
* Check the repository's default branch for a local configuration file.
* Review [Configuration Model](configuration-model.html) and [Repository Overrides](repository-overrides.html) for precedence rules.

## Advanced Usage

### Local Testing and API Simulation

If you cannot reproduce an issue via the GitHub interface, you can manually simulate the webhook delivery using `curl`. This is useful for testing network paths, API rate limits, or specific payload edge cases without spamming actual repository users.

```bash
curl -X POST http://localhost:5000/webhook_server \
  -H "X-GitHub-Event: issue_comment" \
  -H "X-GitHub-Delivery: test-delivery-123" \
  -H "X-Hub-Signature-256: sha256=YOUR_COMPUTED_SIGNATURE" \
  -H "Content-Type: application/json" \
  -d @failed_payload.json
```

> **Note:** You must compute the `X-Hub-Signature-256` manually based on your `failed_payload.json` and your configured webhook secret, or temporarily disable signature validation for local testing.

### Observability and Exporting Diagnostics

For persistent issues, the Log Viewer provides an export mechanism. Instead of manually scraping text files, use the export feature to download a complete, context-aware JSON dump of a specific webhook delivery or PR lifecycle. This bundle is sanitized and safe to share for debugging. See [Log Viewer API](log-viewer-api.html) for programmatic export options.

## Related Pages

- [Logging and Data Files](logging-and-data-files.html)
- [Log Viewer Guide](log-viewer-guide.html)
- [Testing and Maintenance](testing-and-maintenance.html)
