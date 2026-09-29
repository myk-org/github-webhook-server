# Supported GitHub Events

The server routes eight GitHub webhook event types in `webhook_server/libs/github_api.py` (`GitHubWebHook.process()`). Which of them actually arrive depends on the `repositories.<repo-id>.events` subscription configured in `config.yaml`.

| Event | What it does |
| --- | --- |
| `ping` | Logged and acknowledged. Token usage is recorded; no further processing. |
| `push` | Only tag pushes are processed: the repository is cloned at the tag ref and the push handler runs (PyPI upload, container build). Branch pushes and branch/tag deletions are logged and skipped. |
| `pull_request` | Clones the repository, initializes the OWNERS-file handler, and hands off to the pull request handler (labels, merge eligibility, welcome message on `opened`/`ready_for_review`, CI/CD runs). |
| `issue_comment` | Clones the repository, initializes the OWNERS-file handler, and hands off to the issue-comment handler (slash commands, reviewer selection). |
| `pull_request_review` | Clones the repository, initializes the OWNERS-file handler, and hands off to the pull request review handler. |
| `check_run` | Processed only for `action: completed`, skipping the `can-be-merged` check itself when its conclusion is not `success` and skipping check runs whose `head_sha` no longer matches the PR head. Otherwise clones the repository and hands off to the check run handler, then debounces a merge-eligibility recheck. |
| `status` | Processed only for terminal commit states that match the current PR head. Re-evaluates can-be-merged (debounced). |
| `pull_request_review_thread` | Processed only for the `resolved`/`unresolved` actions, and only when `required_conversation_resolution` is enabled. Re-evaluates can-be-merged (debounced). |

Any other event type, or an event for which no pull request can be resolved, is logged and skipped without further processing.

## Restricting events per repository

`config.yaml` accepts an `events` array on each repository entry:

```yaml
repositories:
  my-org/my-repo:
    name: my-org/my-repo
    events:
      - pull_request
      - issue_comment
      - check_run
```

The schema types it as an array of strings with no enum, so the value is passed to GitHub as-is when the hook is created or updated.

Default when the key is omitted: `["*"]` — `webhook_server/utils/webhook.py` reads `data.get("events", ["*"])`, which subscribes to every event GitHub supports.

The subscription is reconciled at startup by `create_webhook()`:

- If no hook with a matching URL exists, the hook is created with the configured events.
- If a matching hook exists and its event list differs from the configured list, the hook is edited in place (`Updating webhook events: <old> -> <new>` in the log).
- If it already matches, nothing changes (`Hook already exists`).
- If the webhook URL matches but secret presence differs, the old hook is deleted and a new one created.

To verify the live subscription:

1. Check the startup log. It reports creation, update (with both event lists), or an already-correct hook for every managed repository.
2. Inspect the hook on GitHub itself — repository **Settings → Webhooks → Add Webhook → Configure**, or the hooks API endpoint for the repository, where the `events` array must match the config exactly.

Because the default is `["*"]`, a repository that only needs pull request processing will still receive every other event type; the server then skips each one at routing time.

## Events skipped early

Two filters run at the very top of `process()`, before `get_api_users()`, so skipped deliveries cost no `get_user()` API calls:

- `pull_request_review_thread` — skipped unless the payload `action` is `resolved` or `unresolved` **and** `required_conversation_resolution` is enabled. The log line reports either `action=<action>, skipped` or `required_conversation_resolution disabled`.
- `status` — skipped when the payload `state` is `pending`; only terminal states continue. Log: `status (state=pending, skipped)`.

Further per-event skips happen later, after the PR is resolved: stale `synchronize` / `check_run` / `status` payloads whose SHA is no longer the PR head, non-`completed` `check_run` actions, non-success `can-be-merged` conclusions, and `push` deletions and branch pushes.
