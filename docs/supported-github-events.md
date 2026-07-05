# Supported GitHub Events

To automate repository and pull request workflows, the webhook server listens to specific GitHub events. By configuring your GitHub App to send these events, the server can trigger CI/CD builds, label PRs, and enforce mergeability rules.

## Prerequisites
* A GitHub App installed and configured to deliver webhook payloads.
* Your repository configured in the global configuration file.

## Quick Example
When configuring your GitHub App, subscribe to the necessary events. You can optionally filter which events a specific repository processes in your configuration:

```yaml
repositories:
  my-org/my-repo:
    # Optional: explicitly filter which events this repository processes
    events:
      - pull_request
      - issue_comment
      - push
```

## Step-by-Step: Event Triggers and Actions

The server handles specific GitHub events and intentionally skips others to save API rate limits and avoid unnecessary processing.

### 1. `pull_request`
Triggers pull request automation workflows.
* **Triggers:** Welcome comments, labeling (size, WIP, hold), owner and reviewer assignment, and mergeability checks.
* **Skipped:** PRs in a Draft state are ignored immediately (unless explicitly allowed to receive commands, which primarily applies to issue comments).

### 2. `issue_comment`
Triggers ChatOps commands via PR comments.
* **Triggers:** User-issued commands such as `/retest`, `/cherry-pick`, or `/assign-reviewer`.
* **Skipped:** Commands on Draft PRs are skipped by default.

### 3. `push`
Triggers CI/CD actions for repository pushes.
* **Triggers:** Tag pushes initiate PyPI package uploads and container build/push workflows.
* **Skipped:** Branch pushes and branch/tag deletion events are skipped (no repository clone is performed).

### 4. `pull_request_review`
Evaluates pull request approval status.
* **Triggers:** Re-evaluates whether a PR can be merged by verifying if minimum LGTM/approvals are met from valid `OWNERS` file approvers.

### 5. `pull_request_review_thread`
Monitors conversational threads.
* **Triggers:** Re-evaluates mergeability based on conversation resolution.
* **Skipped:** If the action is not `resolved` or `unresolved`, or if the `required_conversation_resolution` configuration is disabled.

### 6. `check_run`
Integrates with modern GitHub Check Runs and Test Oracle.
* **Triggers:** Updates PR mergeability state when a check run completes.
* **Skipped:** Actions other than `completed` (such as `created` or `requested`) are ignored.

### 7. `status`
Handles legacy GitHub commit status updates.
* **Triggers:** Re-evaluates mergeability upon terminal status states (`success`, `failure`, or `error`).
* **Skipped:** Updates with a `pending` state are intentionally skipped to avoid redundant processing.

### 8. `ping`
Verifies webhook connectivity.
* **Triggers:** Logs successful connection and validates webhook delivery. Performs no repository actions.

## Advanced Usage

### Optimizing Rate Limits
Because the server receives a high volume of events, it uses a "fail-fast" optimization strategy. It reads the `X-GitHub-Event` header and inspects the payload state BEFORE cloning the repository or fetching users via the GitHub API. This prevents burning through rate limits on events that ultimately require no action.

### Processing Draft PRs
By default, the server strictly avoids acting on Draft PRs to minimize noise. If your workflow requires ChatOps commands on Draft PRs, configure the `allow-commands-on-draft-prs` property in your configuration to enable `issue_comment` event processing for draft states. See [Configuration Reference](configuration-reference.html) for details.

## Troubleshooting

* **Missing Actions on PRs:** If commands or actions are not triggering, ensure your GitHub App is actively subscribed to the `issue_comment` and `pull_request` events.
* **Draft PR Commands Ignored:** If you expect commands on Draft PRs to work, verify that `allow-commands-on-draft-prs` is defined as a valid list in your configuration.
* **Webhooks Timing Out:** The server is designed to immediately respond with an HTTP 200 OK after basic validation, processing events in the background. If you see GitHub delivery failures, check the [Log Viewer Guide](log-viewer-guide.html) using the webhook Delivery ID.

See [Architecture and Event Flow](architecture-and-event-flow.html) for more details on the background processing model.

## Related Pages

- [Pull Request Automation](pull-request-automation.html)
- [Issue Comment Commands](issue-comment-commands.html)
- [Architecture and Event Flow](architecture-and-event-flow.html)
