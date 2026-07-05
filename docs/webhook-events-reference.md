Let me start by reading the knowledge graph and the pages manifest, then explore the codebase systematically.

Now let me look at the remaining handlers:

Now let me look at the `process()` method in `github_api.py` to understand the event routing:

Now let me look at the runner handler and constants:

Now let me look at the `_recheck_merge_eligibility` for status events, and let me also check what `get_pull_request` does:

Now let me look at the runner handler's key methods to understand what CI checks do:

Now let me look at the labels handler and owners handler briefly:

Now let me check for the `ping` event and look at the `get_pull_request` method to understand which events resolve to PRs:

Now I have enough information. Let me write the comprehensive reference page.

# Webhook Events and Handlers

This page documents every GitHub webhook event that the server processes, the handler responsible, and the actions performed for each event and action combination.

> **Note:** The server returns `200 OK` immediately upon receiving a valid webhook payload. All processing happens asynchronously in a background task. See [Getting Started](quickstart.html) for initial setup.

---

## Event Routing Overview

When a webhook arrives, the server validates the payload and routes it to the appropriate handler based on the `X-GitHub-Event` header.

| Event Type | Handler Class | Source File |
|---|---|---|
| `ping` | *(inline in `GithubWebhook.process()`)* | `webhook_server/libs/github_api.py` |
| `push` | `PushHandler` | `webhook_server/libs/handlers/push_handler.py` |
| `pull_request` | `PullRequestHandler` | `webhook_server/libs/handlers/pull_request_handler.py` |
| `issue_comment` | `IssueCommentHandler` | `webhook_server/libs/handlers/issue_comment_handler.py` |
| `check_run` | `CheckRunHandler` | `webhook_server/libs/handlers/check_run_handler.py` |
| `status` | *(inline — re-evaluates merge eligibility)* | `webhook_server/libs/github_api.py` |
| `pull_request_review` | `PullRequestReviewHandler` | `webhook_server/libs/handlers/pull_request_review_handler.py` |
| `pull_request_review_thread` | *(inline — re-evaluates merge eligibility)* | `webhook_server/libs/github_api.py` |

> **Tip:** Draft PRs are skipped entirely unless `allow-commands-on-draft-prs` is configured. When configured, only `issue_comment` events are processed on drafts. See [Configuration Reference](configuration-reference.html) for details.

---

## `ping`

Acknowledges the webhook connection. No processing is performed.

**Trigger:** GitHub sends this event when a webhook is first created or its configuration is updated.

**Actions performed:** Logs the ping event and returns.

---

## `push`

Handles tag pushes for release workflows. Branch pushes are logged but not processed.

**Handler:** `PushHandler`

### Routing Logic

| Condition | Behavior |
|---|---|
| `deleted` is `true` in payload | Skipped — branch/tag deletion |
| `ref` starts with `refs/tags/` | Clones repo, processes tag push |
| `ref` starts with `refs/heads/` | Skipped — branch push |

### Tag Push Actions

When a tag is pushed, the following actions run based on repository configuration:

#### PyPI Upload

**Condition:** `pypi` is configured for the repository.

Checks out the tag, builds a source distribution with `uv build --sdist`, validates with `twine check`, and uploads with `twine upload`. On failure, a GitHub issue is created with the error details. On success, a Slack notification is sent (if configured).

#### Container Build and Push

**Condition:** `container` is configured with `release: true`.

Builds and pushes a container image tagged with the release version.

> **Note:** See [Setting Up CI Checks](setting-up-ci-checks.html) for PyPI and container build configuration.

---

## `pull_request`

The primary event handler that manages the full PR lifecycle.

**Handler:** `PullRequestHandler`

### Actions

#### `opened`

Triggered when a new PR is created.

| Step | Description |
|---|---|
| Welcome comment | Posts a detailed welcome message with available commands, merge requirements, and reviewer info |
| Tracking issue | Creates a GitHub issue linked to the PR (if `create-issue-for-new-pr` is enabled) |
| WIP detection | Adds `wip` label if title starts with `WIP:` |
| Reviewer assignment | Assigns reviewers from OWNERS files |
| Branch label | Adds `branch-<base-ref>` label |
| Merge state check | Labels PR with `needs-rebase` or `has-conflicts` if applicable |
| Size label | Adds `size/XS` through `size/XXL` based on lines changed |
| PR owner assignee | Adds PR author as assignee |
| Verified processing | Auto-verifies if author is in `auto-verified-and-merged-users`; otherwise sets `verified` check to queued |
| CI checks queued | Queues all configured check runs (tox, pre-commit, build-container, etc.) |
| CI execution | Runs tox, pre-commit, python-module-install, container build, conventional title, security checks, and custom checks in parallel |
| Auto-merge | Enables GitHub auto-merge (squash) if author is in `auto-verified-and-merged-users` or base branch is in `set-auto-merge-prs` |
| Test oracle | Triggers AI test oracle in background (if configured) |

#### `reopened`

Same as `opened` except: no welcome comment is posted and no tracking issue is created.

#### `ready_for_review`

Same as `opened`. Triggered when a draft PR is marked as ready for review.

#### `edited`

Triggered when a PR's title or body is changed.

| Step | Description |
|---|---|
| WIP detection | Re-evaluates `wip` label based on updated title |
| Conventional title | Re-runs conventional title check if title was changed and `conventional-title` is configured |

#### `synchronize`

Triggered when new commits are pushed to the PR branch.

**Clean rebase detection:** The handler computes SHA-256 hashes of the diff between the merge-base and head for both the old and new commits. If the hashes match, the push is treated as a clean rebase (no code changes).

| Condition | Behavior |
|---|---|
| Clean rebase | Posts a comment noting preserved labels; runs CI but **preserves** review labels (`approved-*`, `lgtm-*`, `commented-*`, `changes-requested-*`, `verified`) |
| Non-clean rebase / new commits | Removes all review labels; re-runs full CI; resets verified status |

In both cases, the test oracle is triggered in background (if configured).

#### `closed`

Triggered when a PR is closed (merged or not).

| Step | Condition | Description |
|---|---|---|
| Close tracking issue | Always | Closes the linked tracking issue with a comment |
| Delete remote tag | `container` configured | Deletes the PR-specific container image tag from the registry (GHCR via API, others via `regctl`) |
| Cherry-pick | PR is merged + `cherry-pick-<branch>` labels present | Executes cherry-picks to each labeled target branch |
| Container build | PR is merged + `container` configured | Builds and pushes container image |
| Re-label open PRs | PR is merged | After 30s delay, checks all open PRs for `needs-rebase` / `has-conflicts` status |

#### `labeled` / `unlabeled`

Triggered when a label is added to or removed from a PR. Re-evaluates merge eligibility when relevant labels change.

| Label | Effect |
|---|---|
| `approved-<user>` / `lgtm-<user>` / `changes-requested-<user>` | Re-checks merge eligibility if user is an approver/reviewer |
| `verified` | Sets the `verified` check run to success (labeled) or queued (unlabeled); re-checks merge eligibility |
| `wip` / `hold` / `automerge` | Re-checks merge eligibility |
| `can-be-merged` | Ignored (prevents recursive processing) |

---

## `issue_comment`

Processes slash commands posted as comments on PRs.

**Handler:** `IssueCommentHandler`

### Routing Logic

- Only `created` action is processed; `edited` and `deleted` are ignored.
- Comments containing the welcome message marker are ignored.
- Commands must start with `/` and be on their own line.
- Multiple commands per comment are executed in parallel.

### Available Commands

#### PR Status Management

| Command | Arguments | Permission | Description |
|---|---|---|---|
| `/wip` | — | Collaborator | Adds `wip` label and prepends `WIP:` to title |
| `/wip cancel` | — | Collaborator | Removes `wip` label and `WIP:` prefix from title |
| `/hold` | — | Approver | Adds `hold` label to block merging |
| `/hold cancel` | — | Approver | Removes `hold` label |
| `/verified` | — | Collaborator | Adds `verified` label and sets check to success |
| `/verified cancel` | — | Collaborator | Removes `verified` label and sets check to queued |
| `/reprocess` | — | Collaborator | Triggers full PR workflow from scratch (skips if PR is merged) |
| `/regenerate-welcome` | — | Collaborator | Updates or creates the welcome comment |

#### Review & Approval

| Command | Arguments | Permission | Description |
|---|---|---|---|
| `/lgtm` | — | Anyone | Adds `lgtm-<user>` label |
| `/approve` | — | Approver | Adds `approved-<user>` label; triggers test oracle if configured |
| `/automerge` | — | Maintainer or Approver | Adds `automerge` label for automatic squash-merge when all requirements are met |
| `/assign-reviewers` | — | Collaborator | Re-assigns reviewers from OWNERS files |
| `/assign-reviewer` | `@username` | Collaborator | Assigns a specific collaborator as reviewer |
| `/check-can-merge` | — | Collaborator | Manually triggers the merge eligibility check |

#### Testing & Validation

| Command | Arguments | Permission | Description |
|---|---|---|---|
| `/retest tox` | check name(s) | Collaborator | Re-runs the tox test suite |
| `/retest pre-commit` | check name(s) | Collaborator | Re-runs pre-commit hooks |
| `/retest build-container` | check name(s) | Collaborator | Re-runs container build |
| `/retest python-module-install` | check name(s) | Collaborator | Re-runs Python module install check |
| `/retest conventional-title` | check name(s) | Collaborator | Re-runs conventional title validation |
| `/retest <custom-check-name>` | check name(s) | Collaborator | Re-runs a custom check |
| `/retest all` | — | Collaborator | Re-runs all configured checks |
| `/test-oracle` | — | Collaborator | Manually triggers the AI test oracle |

> **Note:** `/retest` requires at least one argument. Multiple checks can be retested in one command: `/retest tox pre-commit`. See [Managing Pull Requests](managing-pull-requests.html) for more examples.

#### Cherry-pick & Branch Operations

| Command | Arguments | Permission | Description |
|---|---|---|---|
| `/cherry-pick` | `branch1 [branch2 ...]` | Collaborator | On unmerged PRs: adds `cherry-pick-<branch>` labels for auto cherry-pick on merge. On merged PRs: executes cherry-pick immediately. |
| `/cherry-pick-retry` | `branch` | Collaborator | Retries a failed cherry-pick on a merged PR. Closes the existing failed cherry-pick PR (if found) and re-runs the cherry-pick. Requires the `cherry-pick-<branch>` label to exist. |
| `/rebase` | — | Collaborator | Rebases the PR branch onto its base branch |

#### Container Operations

| Command | Arguments | Permission | Description |
|---|---|---|---|
| `/build-and-push-container` | `[--build-arg KEY=value ...]` | Collaborator | Builds and pushes a container image tagged with the PR number |

#### Security

| Command | Arguments | Permission | Description |
|---|---|---|---|
| `/security-override` | — | Maintainer | Sets all configured security checks (suspicious paths, committer identity) to pass |
| `/security-override cancel` | — | Maintainer | Re-runs all configured security checks |

> **Note:** See [Enabling Security Checks](enabling-security-checks.html) for security check configuration.

#### Label Management

| Command | Arguments | Permission | Description |
|---|---|---|---|
| `/<label-name>` | — | Varies | Adds the specified label to the PR |
| `/<label-name> cancel` | — | Varies | Removes the specified label from the PR |

Available label names: `hold`, `verified`, `wip`, `lgtm`, `approve`, `automerge`.

#### Other

| Command | Arguments | Permission | Description |
|---|---|---|---|
| `/add-allowed-user` | `username` | Anyone | Posts a comment confirming the user is allowed to run commands |

### Draft PR Command Filtering

When `allow-commands-on-draft-prs` is configured as a list:
- An empty list (`[]`) allows all commands on draft PRs.
- A non-empty list allows only the listed commands. Other commands are rejected with a comment explaining which commands are allowed.
- `/test-oracle` is always allowed on draft PRs regardless of configuration.

See [Configuration Reference](configuration-reference.html) for the `allow-commands-on-draft-prs` option.

---

## `check_run`

Monitors GitHub check run completions and triggers merge evaluation.

**Handler:** `CheckRunHandler`

### Routing Logic

| Condition | Behavior |
|---|---|
| `action` ≠ `completed` | Skipped |
| Check run name is `can-be-merged` with non-success conclusion | Skipped |
| Check run name is `can-be-merged` with `success` conclusion + `automerge` label | Executes squash merge |
| Any other completed check run | Re-evaluates merge eligibility via `check_if_can_be_merged()` |

### Auto-merge Flow

When a `can-be-merged` check run completes with `success`:

1. Checks if the PR has the `automerge` label.
2. If present, performs a squash merge via the GitHub API.
3. If the merge fails, falls back to re-evaluating merge eligibility.

### Check Run States

The server creates and manages these check runs on PRs:

| Check Run Name | Source | Description |
|---|---|---|
| `can-be-merged` | Built-in | Aggregated merge eligibility status |
| `tox` | Config: `tox` | Python test suite result |
| `pre-commit` | Config: `pre-commit` | Pre-commit hooks result |
| `build-container` | Config: `container` | Container build result |
| `python-module-install` | Config: `pypi` | Python package install test result |
| `conventional-title` | Config: `conventional-title` | Commit message format validation |
| `verified` | Config: `verified-job` | Manual verification status |
| `security-suspicious-paths` | Config: `security-checks.suspicious-paths` | Suspicious file path detection |
| `security-committer-identity` | Config: `security-checks.committer-identity-check` | Committer/author mismatch detection |
| *(custom name)* | Config: `custom-check-runs[].name` | User-defined check command |

Each check run transitions through these states:

| State | Meaning |
|---|---|
| `queued` | Check is registered but not yet running |
| `in_progress` | Check is currently executing |
| `success` | Check completed successfully |
| `failure` | Check completed with errors |

> **Note:** See [Setting Up CI Checks](setting-up-ci-checks.html) for configuring each check type.

---

## `status`

Monitors GitHub commit status updates (legacy Status API) and re-evaluates merge eligibility.

**Handler:** Inline in `GithubWebhook.process()`

### Routing Logic

| Condition | Behavior |
|---|---|
| `state` = `pending` | Skipped (early exit before API user initialization) |
| `state` = `success`, `failure`, or `error` | Clones repository, re-evaluates `can-be-merged` |

### Effect

When a commit status reaches a terminal state, the server:

1. Clones the repository (needed for OWNERS file processing).
2. Initializes the OWNERS file handler.
3. Calls `check_if_can_be_merged()` to re-evaluate all merge conditions.

> **Tip:** This enables the server to react to external CI systems that report via the Status API (e.g., Jenkins, CircleCI) rather than the Check Runs API.

---

## `pull_request_review`

Processes PR review submissions and manages review labels.

**Handler:** `PullRequestReviewHandler`

### Routing Logic

| Condition | Behavior |
|---|---|
| `action` ≠ `submitted` | Skipped |
| `action` = `submitted` | Processes the review |

### Actions on Submitted Review

1. **Adds a review label** based on the review state:

   | Review State | Label Added |
   |---|---|
   | `approved` | `approved-<username>` (if user is an approver) or `lgtm-<username>` (if reviewer, non-PR-owner) |
   | `changes_requested` | `changes-requested-<username>` |
   | `commented` | `commented-<username>` |

2. **Processes `/approve` command** in review body: If the review body contains a line with exactly `/approve`, the handler adds the `approved-<user>` label and triggers the test oracle (if configured).

---

## `pull_request_review_thread`

Monitors review thread resolution/unresolvement for conversation resolution requirements.

**Handler:** Inline in `GithubWebhook.process()`

### Routing Logic

| Condition | Behavior |
|---|---|
| `action` not in (`resolved`, `unresolved`) | Skipped |
| `required_conversation_resolution` is disabled | Skipped |
| `action` = `resolved` or `unresolved` | Re-evaluates `can-be-merged` |

### Effect

When a review thread is resolved or unresolved and `required_conversation_resolution` is enabled in branch protection settings, the server re-evaluates all merge conditions. Unresolved threads block the `can-be-merged` check.

See [Cherry-Picking and Branch Protection](cherry-picking-and-branching.html) for configuring `required_conversation_resolution`.

---

## Merge Eligibility Check (`can-be-merged`)

The `check_if_can_be_merged()` method is called by multiple event handlers. It evaluates all merge conditions and sets the `can-be-merged` check run to success or failure.

### Conditions Evaluated

| # | Condition | Failure Message |
|---|---|---|
| 1 | PR is mergeable (no conflicts) | `PR is not mergeable: False` |
| 2 | No required check runs in progress | `Some required check runs in progress <names>` |
| 3 | No `wip` or `hold` labels | `PR has wip/hold label` |
| 4 | All required check runs passed | `Some check runs failed: <names>` or `Some check runs not started: <names>` |
| 5 | No `changes-requested-<approver>` labels | `PR has changed requests from approvers` |
| 6 | All `can-be-merged-required-labels` present | `Missing required labels: <names>` |
| 7 | No unresolved review threads (if `required_conversation_resolution` enabled) | `PR has N unresolved review conversation(s)` |
| 8 | Approved by required approvers | `Missing approved from approvers: <names>` |
| 9 | Minimum LGTM count met | `Missing lgtm from reviewers. Minimum N required, (M given)` |

When all conditions pass, the `can-be-merged` label is added and the check run is set to success.

### Required Status Checks

The list of required checks is built from:

1. **Branch protection rules** (from GitHub API, public repos only)
2. **Enabled features:** `tox`, `verified`, `build-container`, `python-module-install`, `conventional-title`
3. **Mandatory custom checks** (custom checks with `mandatory: true`, which is the default)
4. **Mandatory security checks** (when `security-checks.mandatory` is `true`)

> **Note:** Non-mandatory custom checks and non-mandatory security checks still run but do not block merging. See [Configuration Reference](configuration-reference.html) for the `mandatory` option.

---

## Labels Reference

Labels are automatically created and managed by the server. See [Configuring Labels and PR Size Thresholds](configuring-labels-and-size.html) for color customization and enabling/disabling label categories.

### Static Labels

| Label | Default Color | Description |
|---|---|---|
| `hold` | `#B60205` (red) | Blocks merging |
| `verified` | `#0E8A16` (green) | PR has been verified |
| `wip` | `#B60205` (red) | Work in progress |
| `lgtm` | `#0E8A16` (green) | Looks good to me |
| `approve` | `#0E8A16` (green) | Approved |
| `automerge` | `#0E8A16` (green) | Auto-merge enabled |
| `CherryPicked` | `#1D76DB` (blue) | PR was cherry-picked |
| `ai-resolved-conflicts` | `#FFA500` (orange) | Cherry-pick conflicts resolved by AI |
| `can-be-merged` | `#0E8A17` (green) | All merge requirements met |
| `needs-rebase` | `#B60205` (red) | PR is behind base branch |
| `has-conflicts` | `#B60205` (red) | PR has merge conflicts |
| `size/XS` | `#ededed` | ≤ 20 lines changed |
| `size/S` | `#0E8A16` | ≤ 50 lines changed |
| `size/M` | `#F09C74` | ≤ 100 lines changed |
| `size/L` | `#F5621C` | ≤ 300 lines changed |
| `size/XL` | `#D93F0B` | ≤ 500 lines changed |
| `size/XXL` | `#B60205` | > 500 lines changed |

### Dynamic Labels (prefixed)

| Label Prefix | Default Color | Example | Description |
|---|---|---|---|
| `approved-` | `#0E8A16` | `approved-alice` | PR approved by user |
| `lgtm-` | `#DCED6F` | `lgtm-bob` | LGTM from user |
| `commented-` | `#D93F0B` | `commented-carol` | Review comment from user |
| `changes-requested-` | `#F5621C` | `changes-requested-dave` | Changes requested by user |
| `cherry-pick-` | `#F09C74` | `cherry-pick-release-1.0` | Cherry-pick target branch |
| `branch-` | `#1D76DB` | `branch-main` | PR target branch |

---

## Event Processing Flowchart

The following describes the processing order for PR-related events:

1. **Webhook arrives** → validate payload, verify signature (if configured), return `200 OK`
2. **Background task starts** → create structured logging context
3. **Initialize `GithubWebhook`** → load config, validate repository
4. **Early exit checks** → filter draft PRs, skip non-actionable events
5. **Initialize API users** → fetch token user logins for auto-verify list
6. **Event routing** → dispatch to appropriate handler
7. **Handler processing** → execute event-specific logic
8. **Context finalization** → log summary, write structured log entry

> **Warning:** All processing after step 1 happens asynchronously. A `200 OK` response does **not** mean the webhook was processed successfully. Use the [Log Viewer](using-the-log-viewer.html) or the `delivery_id` in the response to track processing results.

## Related Pages

- [Managing Pull Requests](managing-pull-requests.html)
- [Setting Up CI Checks](setting-up-ci-checks.html)
- [Configuration Reference](configuration-reference.html)
- [Cherry-Picking and Branch Protection](cherry-picking-and-branching.html)
- [Configuring Labels and PR Size Thresholds](configuring-labels-and-size.html)
