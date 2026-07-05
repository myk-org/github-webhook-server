Let me start by reading the knowledge graph and understanding the project structure.

Now let me look at the repository-local config (`.github-webhook-server.yaml`) handling and more details:

Now let me check what's available in `.github-webhook-server.yaml` (what keys it can contain):

Let me check the `verify-github-ips` and `verify-cloudflare-ips` usage:

Now let me look at the `.github-webhook-server.yaml` more closely - what keys does it support:

Let me look at the `welcome-message.md` file pattern and remaining config usage:

Now let me look at tox config more closely and check events config:

Now let me check the OWNERS file support:

Let me also check the `verify-github-ips` and IP allowlist implementation:

Now let me check the `disable-ssl-warnings` key and look for all the config keys used at the app level:

Now let me check the `.github-webhook-server.yaml` in-repo config more carefully - specifically what keys it can accept. It should be basically the same as the repository-level config:

Now I have a comprehensive understanding. Let me verify the `dockerfile` key usage:

Now I have enough information. Let me write the comprehensive Configuration Reference page.

# Configuration Reference

Complete reference for all configuration options in `config.yaml` (global server config) and `.github-webhook-server.yaml` (per-repository in-repo config).

## Configuration Files Overview

| File | Location | Purpose |
|---|---|---|
| `config.yaml` | `$WEBHOOK_SERVER_DATA_DIR/config.yaml` | Main server config: global settings + per-repository settings |
| `.github-webhook-server.yaml` | Repository root (committed to repo) | Per-repository overrides (highest priority) |
| `.github-webhook-server-welcome-message.md` | Repository root (committed to repo) | Custom PR welcome message content (overrides `welcome-extra-info`) |
| `OWNERS` | Any directory in repo | Approver/reviewer definitions for OWNERS-based approval workflow |

### Config Resolution Order

Values are resolved in this order (first match wins):

1. `.github-webhook-server.yaml` (in-repo file)
2. Repository-level settings in `config.yaml` (under `repositories.<name>`)
3. Global (root-level) settings in `config.yaml`

> **Note:** Dot notation is supported for nested lookups (e.g., `docker.username`, `pypi.token`).

---

## Global Server Settings

These settings are defined at the root level of `config.yaml` only. They cannot be set in `.github-webhook-server.yaml`.

### `log-level`

| Property | Value |
|---|---|
| Type | `string` |
| Allowed values | `INFO`, `DEBUG` |
| Default | — |

Global log level. Changes take effect immediately without server restart.

```yaml
log-level: INFO
```

### `log-file`

| Property | Value |
|---|---|
| Type | `string` |
| Default | — |

File path for the main log file. Changes take effect immediately without server restart.

```yaml
log-file: webhook-server.log
```

### `mcp-log-file`

| Property | Value |
|---|---|
| Type | `string` |
| Default | `mcp_server.log` |

File path for the MCP server log file.

```yaml
mcp-log-file: mcp_server.log
```

### `logs-server-log-file`

| Property | Value |
|---|---|
| Type | `string` |
| Default | `logs_server.log` |

File path for the Logs Server log file.

```yaml
logs-server-log-file: logs_server.log
```

### `mask-sensitive-data`

| Property | Value |
|---|---|
| Type | `boolean` |
| Default | `true` |

Mask sensitive data (tokens, passwords, secrets) in logs. Can be overridden per repository.

```yaml
mask-sensitive-data: true
```

> **Warning:** Setting to `false` in production will expose secrets in log files.

### `github-app-id`

| Property | Value |
|---|---|
| Type | `integer` |
| Default | — |

The GitHub App ID used by the webhook server for repository management.

```yaml
github-app-id: 123456
```

### `github-tokens`

| Property | Value |
|---|---|
| Type | `array` of `string` |
| Default | — |

Global GitHub personal access tokens. Multiple tokens enable automatic failover — the server selects the token with the highest remaining rate limit. Can be overridden per repository.

```yaml
github-tokens:
  - ghp_token1abc123
  - ghp_token2def456
```

### `webhook-ip`

| Property | Value |
|---|---|
| Type | `string` (URI) |
| Default | — |

Full webhook URL including path. This is registered on each managed repository as the webhook endpoint.

```yaml
webhook-ip: https://your-domain.com/webhook_server
```

> **Tip:** For local development, use a [smee.io](https://smee.io) channel: `https://smee.io/your-channel`.

### `ip-bind`

| Property | Value |
|---|---|
| Type | `string` |
| Default | `0.0.0.0` |

IP address to bind the HTTP server to.

```yaml
ip-bind: 0.0.0.0
```

### `port`

| Property | Value |
|---|---|
| Type | `integer` |
| Default | `5000` |

Port to bind the HTTP server to.

```yaml
port: 5000
```

### `max-workers`

| Property | Value |
|---|---|
| Type | `integer` |
| Default | `10` |

Maximum number of uvicorn worker processes. Only used in production mode (not in dev mode with `WEBHOOK_SERVER_DEV_MODE=true`).

```yaml
max-workers: 10
```

### `webhook-secret`

| Property | Value |
|---|---|
| Type | `string` |
| Default | — |

Shared secret for validating GitHub webhook payloads via HMAC-SHA256 signature. When set, the server verifies the `x-hub-signature-256` header on every incoming request.

```yaml
webhook-secret: my-super-secret-value
```

### `verify-github-ips`

| Property | Value |
|---|---|
| Type | `boolean` |
| Default | `false` |

Restrict incoming webhooks to GitHub's published IP ranges. At startup, the server fetches the GitHub meta API to build an IP allowlist.

```yaml
verify-github-ips: true
```

### `verify-cloudflare-ips`

| Property | Value |
|---|---|
| Type | `boolean` |
| Default | `false` |

Restrict incoming webhooks to Cloudflare's published IP ranges. Use when the server sits behind a Cloudflare proxy.

```yaml
verify-cloudflare-ips: true
```

> **Note:** `verify-github-ips` and `verify-cloudflare-ips` can be combined. If both are enabled, requests from either range are accepted. If enabled but IP lists fail to load, the server refuses to start.

### `disable-ssl-warnings`

| Property | Value |
|---|---|
| Type | `boolean` |
| Default | `false` |

Disable urllib3 SSL warnings. Useful in production environments with internal CAs to reduce log noise.

```yaml
disable-ssl-warnings: true
```

### `docker`

Docker Hub credentials for pulling base images during container builds.

| Key | Type | Description |
|---|---|---|
| `username` | `string` | Docker Hub username |
| `password` | `string` | Docker Hub password |

```yaml
docker:
  username: my-docker-user
  password: my-docker-password
```

---

## Global Defaults

These settings are defined at the root level of `config.yaml` and serve as defaults. They can be overridden at the repository level in `config.yaml` or in `.github-webhook-server.yaml`.

### `default-status-checks`

| Property | Value |
|---|---|
| Type | `array` of `string` |
| Default | — |

Status checks included when configuring branch protection required status checks. The `can-be-merged` check is always appended automatically.

```yaml
default-status-checks:
  - "WIP"
  - "dpulls"
  - "can-be-merged"
```

### `auto-verified-and-merged-users`

| Property | Value |
|---|---|
| Type | `array` of `string` |
| Default | `[]` |

Users whose PRs are automatically verified and merged. Typically used for bots like Renovate or pre-commit CI.

```yaml
auto-verified-and-merged-users:
  - "renovate[bot]"
  - "pre-commit-ci[bot]"
```

### `auto-verify-cherry-picked-prs`

| Property | Value |
|---|---|
| Type | `boolean` |
| Default | `true` |

Automatically add the `verified` label to cherry-picked PRs.

```yaml
auto-verify-cherry-picked-prs: true
```

### `create-issue-for-new-pr`

| Property | Value |
|---|---|
| Type | `boolean` |
| Default | `true` |

Create a tracking issue for each new pull request.

```yaml
create-issue-for-new-pr: true
```

### `cherry-pick-assign-to-pr-author`

| Property | Value |
|---|---|
| Type | `boolean` |
| Default | `true` |

Assign cherry-pick PRs to the original PR author.

```yaml
cherry-pick-assign-to-pr-author: true
```

### `allow-commands-on-draft-prs`

| Property | Value |
|---|---|
| Type | `array` of `string` |
| Default | not set (commands blocked on drafts) |

Controls which PR comment commands are allowed on draft PRs.

| Configuration | Behavior |
|---|---|
| Not set (default) | All commands blocked on draft PRs |
| Empty list `[]` | All commands allowed on draft PRs |
| List with values | Only listed commands allowed on draft PRs |

```yaml
# Allow all commands on draft PRs
allow-commands-on-draft-prs: []

# Allow only specific commands
allow-commands-on-draft-prs:
  - build-and-push-container
  - retest
```

### `welcome-extra-info`

| Property | Value |
|---|---|
| Type | `string` |
| Max length | 10,240 bytes |
| Default | `""` |

Additional markdown content appended to the PR welcome message. An empty string explicitly clears any inherited value.

```yaml
welcome-extra-info: |
  **Note:** Please review the contribution guide before merging.
  - Ensure tests pass
  - Update documentation if needed
```

> **Tip:** For larger welcome message content, commit a `.github-webhook-server-welcome-message.md` file to the repository root. It takes priority over all config-based `welcome-extra-info` settings.

---

## Labels Configuration

Controls which labels the server manages and their colors. Can be set globally or per repository. See [Configuring Labels and PR Size Thresholds](configuring-labels-and-size.html) for usage details.

### `labels`

#### `labels.enabled-labels`

| Property | Value |
|---|---|
| Type | `array` of `string` |
| Default | all categories enabled |

List of label categories to enable. If not set, all categories are active.

| Category | Labels managed |
|---|---|
| `verified` | `verified` |
| `hold` | `hold` |
| `wip` | `wip` |
| `needs-rebase` | `needs-rebase` |
| `has-conflicts` | `has-conflicts` |
| `can-be-merged` | `can-be-merged` |
| `size` | `size/XS`, `size/S`, `size/M`, `size/L`, `size/XL`, `size/XXL` (or custom thresholds) |
| `branch` | `branch-<name>` |
| `cherry-pick` | `cherry-pick-<branch>`, `CherryPicked`, `ai-resolved-conflicts` |
| `automerge` | `automerge` |

> **Note:** Reviewed-by labels (`approved-<user>`, `lgtm-<user>`, `changes-requested-<user>`, `commented-<user>`) are always enabled and cannot be disabled.

```yaml
labels:
  enabled-labels:
    - verified
    - hold
    - size
    - can-be-merged
```

#### `labels.colors`

| Property | Value |
|---|---|
| Type | `object` (label name → CSS3 color) |
| Default | built-in color scheme |

Custom colors for labels. Use the exact label name for static labels, or the prefix for dynamic labels.

| Default label / prefix | Default color |
|---|---|
| `hold` | `B60205` (red) |
| `verified` | `0E8A16` (green) |
| `wip` | `B60205` (red) |
| `automerge` | `0E8A16` (green) |
| `needs-rebase` | `B60205` (red) |
| `can-be-merged` | `0E8A17` (green) |
| `has-conflicts` | `B60205` (red) |
| `approved-` | `0E8A16` (green) |
| `lgtm-` | `DCED6F` (yellow-green) |
| `changes-requested-` | `F5621C` (orange) |
| `commented-` | `D93F0B` (dark orange) |
| `cherry-pick-` | `F09C74` (salmon) |
| `branch-` | `1D76DB` (blue) |

```yaml
labels:
  colors:
    hold: red
    verified: green
    approved-: green
    lgtm-: yellowgreen
    cherry-pick-: coral
    branch-: royalblue
```

### `pr-size-thresholds`

| Property | Value |
|---|---|
| Type | `object` (category name → `{threshold, color}`) |
| Default | built-in XS/S/M/L/XL/XXL categories |

Custom PR size categories based on total lines changed (additions + deletions).

| Sub-key | Type | Required | Description |
|---|---|---|---|
| `threshold` | `integer` or `"inf"` | Yes | Minimum number of changed lines for this category |
| `color` | `string` | No | CSS3 color name for the label |

Categories are sorted by threshold. Each PR gets the label whose threshold it meets but whose next-higher threshold it does not. Use `"inf"` for the unbounded largest category.

```yaml
pr-size-thresholds:
  Tiny:
    threshold: 10    # 0–9 lines changed
    color: lightgray
  Small:
    threshold: 50    # 10–49 lines changed
    color: green
  Medium:
    threshold: 150   # 50–149 lines changed
    color: orange
  Large:
    threshold: 300   # 150–299 lines changed
    color: red
  Massive:
    threshold: inf   # 300+ lines changed
    color: darkred
```

---

## Branch Protection

Configures GitHub branch protection rules applied at server startup. Can be set globally or per repository. Repository-level settings override global.

### `branch-protection`

| Key | Type | Default | Description |
|---|---|---|---|
| `strict` | `boolean` | `true` | Require branches to be up to date before merging |
| `require_code_owner_reviews` | `boolean` | `false` | Require review from code owners |
| `dismiss_stale_reviews` | `boolean` | `true` | Dismiss approvals when new commits are pushed |
| `required_approving_review_count` | `integer` | `0` | Number of required approving reviews |
| `required_linear_history` | `boolean` | `true` | Require linear commit history |
| `required_conversation_resolution` | `boolean` | `true` | Require all PR review conversations to be resolved before merge |

```yaml
branch-protection:
  strict: true
  require_code_owner_reviews: true
  dismiss_stale_reviews: false
  required_approving_review_count: 1
  required_linear_history: true
  required_conversation_resolution: true
```

---

## Security Checks

Detects potentially malicious PR patterns. Can be set globally or per repository. See [Enabling Security Checks](enabling-security-checks.html) for usage details.

### `security-checks`

| Key | Type | Default | Description |
|---|---|---|---|
| `mandatory` | `boolean` | `true` | When `true`, security check failures block `can-be-merged`. When `false`, checks are advisory only. |
| `suspicious-paths` | `array` of `string` | See below | Path prefixes considered security-sensitive. PRs modifying files under these paths fail the `security-suspicious-paths` check run. |
| `committer-identity-check` | `boolean` | `true` | Compare PR author against the last commit's committer. Fails if they differ. |
| `trusted-committers` | `array` of `string` | `[]` | Committer logins always trusted for the identity check (case-insensitive). |

**Default suspicious paths:**
- `.claude/`
- `.vscode/`
- `.cursor/`
- `.devcontainer/`
- `.pi/`
- `.github/workflows/`
- `.github/actions/`

> **Note:** The GitHub App bot, `web-flow`, and API users from `github-tokens` are automatically added to the trusted committers list. Only list additional external committers.

```yaml
security-checks:
  mandatory: true
  suspicious-paths:
    - ".github/workflows/"
    - ".github/actions/"
  committer-identity-check: true
  trusted-committers:
    - "pre-commit-ci[bot]"
```

---

## AI Features

AI-powered enhancements using external AI CLI providers. Can be set globally or per repository. See [Enabling AI Features](enabling-ai-features.html) for usage details.

### `ai-features`

| Key | Type | Required | Description |
|---|---|---|---|
| `ai-provider` | `string` | Yes | AI CLI provider: `claude`, `gemini`, or `cursor` |
| `ai-model` | `string` | Yes | Model identifier (e.g., `claude-opus-4-6-1m`, `sonnet`, `gemini-2.5-pro`) |
| `conventional-title` | `object` | No | AI-powered conventional title suggestions |
| `resolve-cherry-pick-conflicts-with-ai` | `object` | No | AI-powered cherry-pick conflict resolution |

#### `ai-features.conventional-title`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | — (required) | Enable AI conventional title suggestions |
| `mode` | `string` | `suggest` | `suggest`: show suggestion in check run output. `fix`: auto-update the PR title. |
| `timeout-minutes` | `integer` | `10` | Timeout for the AI CLI process (minimum: 1) |

#### `ai-features.resolve-cherry-pick-conflicts-with-ai`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | — (required) | Enable AI conflict resolution for cherry-picks |
| `timeout-minutes` | `integer` | `10` | Timeout for the AI CLI process (minimum: 1) |

> **Note:** AI-resolved cherry-picks are never auto-verified — manual review is always required.

```yaml
ai-features:
  ai-provider: "claude"
  ai-model: "sonnet"
  conventional-title:
    enabled: true
    mode: suggest
    timeout-minutes: 10
  resolve-cherry-pick-conflicts-with-ai:
    enabled: true
    timeout-minutes: 10
```

---

## Test Oracle

PR Test Oracle integration that analyzes diffs with AI and recommends which tests to run. Can be set globally or per repository.

### `test-oracle`

| Key | Type | Required | Default | Description |
|---|---|---|---|---|
| `server-url` | `string` (URI) | Yes | — | URL of the pr-test-oracle server |
| `ai-provider` | `string` | Yes | — | AI provider: `claude`, `gemini`, or `cursor` |
| `ai-model` | `string` | Yes | — | AI model identifier |
| `test-patterns` | `array` of `string` | No | oracle defaults | Glob patterns for test files |
| `triggers` | `array` of `string` | No | `[approved]` | When to automatically run analysis |

**Trigger values:**

| Trigger | Description |
|---|---|
| `approved` | Run when a PR review is approved |
| `pr-opened` | Run when a new PR is opened |
| `pr-synchronized` | Run when new commits are pushed to a PR |

> **Tip:** The `/test-oracle` comment command always works when configured, regardless of triggers.

```yaml
test-oracle:
  server-url: "http://localhost:8000"
  ai-provider: "claude"
  ai-model: "sonnet"
  test-patterns:
    - "tests/**/*.py"
  triggers:
    - approved
    - pr-opened
```

---

## Repository Settings

Each repository is defined under the `repositories` key in `config.yaml`. The repository key is an alias; the actual GitHub repository is identified by the `name` field.

```yaml
repositories:
  my-repo-alias:
    name: my-org/my-repository
    # ... repository-specific settings
```

### `name`

| Property | Value |
|---|---|
| Type | `string` |
| Required | Yes |

Full repository name in `org/repo` format.

```yaml
name: my-org/my-repository
```

### `log-level`

| Property | Value |
|---|---|
| Type | `string` |
| Allowed values | `INFO`, `DEBUG` |
| Default | inherits global |

Override the global log level for this repository.

```yaml
log-level: DEBUG
```

### `log-file`

| Property | Value |
|---|---|
| Type | `string` |
| Default | inherits global |

Override the global log file for this repository.

```yaml
log-file: my-repository.log
```

### `mask-sensitive-data`

| Property | Value |
|---|---|
| Type | `boolean` |
| Default | `true` |

Override the global sensitive data masking for this repository.

```yaml
mask-sensitive-data: false
```

### `slack-webhook-url`

| Property | Value |
|---|---|
| Type | `string` |
| Default | — |

Slack webhook URL for notifications on PR merges, container builds, PyPI uploads, and other events. See [Setting Up Slack Notifications](setting-up-notifications.html) for details.

```yaml
slack-webhook-url: https://slack-webhook-url/replace-with-your-webhook-url
```

### `verified-job`

| Property | Value |
|---|---|
| Type | `boolean` |
| Default | `true` |

Enable the verified job check run functionality.

```yaml
verified-job: true
```

### `events`

| Property | Value |
|---|---|
| Type | `array` of `string` |
| Default | `["*"]` (all events) |

GitHub webhook events to listen to. If omitted, all events are subscribed. See [Webhook Events and Handlers](webhook-events-reference.html) for supported events.

```yaml
events:
  - push
  - pull_request
  - pull_request_review
  - pull_request_review_thread
  - issue_comment
  - check_run
  - status
```

### `github-tokens`

| Property | Value |
|---|---|
| Type | `array` of `string` |
| Default | inherits global |

Override global GitHub tokens for this repository. Supports multi-token failover.

```yaml
github-tokens:
  - ghp_repo_specific_token1
  - ghp_repo_specific_token2
```

### `default-status-checks`

| Property | Value |
|---|---|
| Type | `array` of `string` |
| Default | inherits global |

Override global default status checks for this repository.

```yaml
default-status-checks:
  - "WIP"
  - "can-be-merged"
  - "ci/my-external-check"
```

### `minimum-lgtm`

| Property | Value |
|---|---|
| Type | `integer` |
| Default | `0` |

Minimum number of LGTM approvals required before a PR can be approved.

```yaml
minimum-lgtm: 2
```

### `can-be-merged-required-labels`

| Property | Value |
|---|---|
| Type | `array` of `string` |
| Default | `[]` |

Additional labels required for a PR to receive the `can-be-merged` label.

```yaml
can-be-merged-required-labels:
  - qa-approved
  - docs-reviewed
```

### `set-auto-merge-prs`

| Property | Value |
|---|---|
| Type | `array` of `string` |
| Default | `[]` |

Branches for which auto-merge is automatically enabled on new PRs.

```yaml
set-auto-merge-prs:
  - main
  - release
```

### `conventional-title`

| Property | Value |
|---|---|
| Type | `string` |
| Default | — (disabled) |

Comma-separated list of allowed [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) types for PR title validation. Use `"*"` to accept any type while still enforcing the format `<type>[optional scope]: <description>`.

**Standard types:** `feat`, `fix`, `build`, `chore`, `ci`, `docs`, `style`, `refactor`, `perf`, `test`, `revert`

```yaml
# Specific types
conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"

# Any type (wildcard) — enforces format only
conventional-title: "*"
```

See [Setting Up CI Checks](setting-up-ci-checks.html) for details on how validation integrates with check runs.

### `pypi`

PyPI publishing configuration. When set, the server runs a Python module install check on PRs.

| Key | Type | Description |
|---|---|---|
| `token` | `string` | PyPI API token for package publishing |

```yaml
pypi:
  token: pypi-AgEIcHlwaS5vcmc...
```

---

## Tox Configuration

Configures tox test execution per branch. Defined under the repository in `config.yaml`. See [Setting Up CI Checks](setting-up-ci-checks.html) for details.

### `tox`

The `tox` key maps branch names to tox environments, with optional sub-keys for extra configuration.

| Key | Type | Description |
|---|---|---|
| `<branch-name>` | `string` | Comma-separated tox environments, or `all` for all environments |
| `args` | `string` | Additional CLI arguments passed to tox (e.g., `-p -v`) |
| `python-version` | `string` | Python version for tox execution (e.g., `3.11`) |

```yaml
tox:
  args: "-p -v"
  python-version: "3.12"
  main: all
  dev: testenv1,testenv2
  feature: lint,test
```

### `tox-python-version` (deprecated)

| Property | Value |
|---|---|
| Type | `string` |
| Default | — |

> **Warning:** Deprecated. Use `tox.python-version` instead. This key still works but logs a warning.

```yaml
# Deprecated
tox-python-version: "3.11"

# Use instead
tox:
  python-version: "3.11"
```

---

## Pre-commit

### `pre-commit`

| Property | Value |
|---|---|
| Type | `boolean` |
| Default | `false` |

Enable pre-commit checks on pull requests. When enabled, the server runs `pre-commit run --all-files` in the PR worktree.

```yaml
pre-commit: true
```

---

## Container Build Configuration

Configures container image builds using Podman. See [Setting Up CI Checks](setting-up-ci-checks.html) for details.

### `container`

| Key | Type | Required | Default | Description |
|---|---|---|---|---|
| `username` | `string` | Yes | — | Container registry username |
| `password` | `string` | Yes | — | Container registry password |
| `repository` | `string` | Yes | — | Full registry repository path (e.g., `quay.io/org/image`) |
| `tag` | `string` | No | `latest` | Image tag |
| `dockerfile` | `string` | No | `Dockerfile` | Path to Dockerfile (not in schema but supported in code) |
| `release` | `boolean` | No | `false` | Push image with release tag on new GitHub release |
| `build-args` | `array` of `string` | No | `[]` | Build arguments (e.g., `my-arg=value`) |
| `args` | `array` of `string` | No | `[]` | Additional podman build command arguments |
| `context` | `string` | No | `""` (repo root) | Subdirectory for Docker build context (alphanumeric, dots, hyphens, underscores, slashes only) |
| `oci-annotations` | `object` | No | disabled | OCI image annotation configuration |

```yaml
container:
  username: my-user
  password: my-password
  repository: quay.io/myorg/myimage
  tag: latest
  release: true
  context: src
  build-args:
    - BUILD_VERSION=1.0
  args:
    - --format docker
```

#### `container.oci-annotations`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | `false` | Enable OCI annotations on built images |
| `static` | `object` | `{}` | Static key-value annotation pairs (reverse domain notation recommended) |
| `auto` | `object` | all `true` when enabled | Auto-populated annotations from webhook context |

**Auto annotations (all default to `true` when `enabled` is `true`):**

| Key | OCI Annotation | Description |
|---|---|---|
| `created` | `org.opencontainers.image.created` | Build timestamp |
| `source` | `org.opencontainers.image.source` | Repository URL |
| `revision` | `org.opencontainers.image.revision` | Commit SHA |
| `version` | `org.opencontainers.image.version` | Tag on release builds |
| `title` | `org.opencontainers.image.title` | Repository name |

```yaml
container:
  # ...
  oci-annotations:
    enabled: true
    static:
      org.opencontainers.image.vendor: "My Organization"
      org.opencontainers.image.licenses: "Apache-2.0"
    auto:
      created: true
      source: true
      revision: true
      version: true
      title: true
```

---

## Protected Branches

Configures required status checks for branch protection per branch.

### `protected-branches`

Each key is a branch name. The value is either:
- An empty array `[]` — uses all default status checks
- An array of strings — uses those exact status checks
- An object with `include-runs` and/or `exclude-runs`

| Sub-key | Type | Description |
|---|---|---|
| `include-runs` | `array` of `string` | Explicit list of required status checks (overrides auto-detection) |
| `exclude-runs` | `array` of `string` | Status checks to exclude from auto-detected list |

```yaml
protected-branches:
  dev: []  # All default checks
  main:
    include-runs:
      - "pre-commit.ci - pr"
      - "WIP"
    exclude-runs:
      - "SonarCloud Code Analysis"
  feature:
    - "lint"
    - "test"
```

---

## Custom Check Runs

User-defined check runs that execute commands on PR events. See [Setting Up CI Checks](setting-up-ci-checks.html) for details.

### `custom-check-runs`

| Property | Value |
|---|---|
| Type | `array` of objects |
| Default | `[]` |

Each custom check run object:

| Key | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | `string` | Yes | — | Unique name displayed in the GitHub UI |
| `command` | `string` | Yes | — | Command to execute in the repository worktree. Environment variables can be inlined. |
| `mandatory` | `boolean` | No | `true` | Whether this check must pass for the PR to be mergeable |

> **Warning:** Custom check names cannot conflict with built-in check names: `tox`, `pre-commit`, `build-container`, `python-module-install`, `conventional-title`, `can-be-merged`, `security-suspicious-paths`, `security-committer-identity`.

```yaml
custom-check-runs:
  - name: lint
    command: uv tool run --from ruff ruff check
    mandatory: true
  - name: security-scan
    command: TOKEN=xyz uv tool run --from bandit bandit -r .
    mandatory: false
  - name: complex-check
    command: |
      uv run python -c "
      import sys
      print('Running complex check')
      sys.exit(0)
      "
```

---

## Per-Repository In-Repo Config

The `.github-webhook-server.yaml` file is committed to a repository's root and provides repository-specific overrides at the highest priority.

### Supported Keys

This file accepts any key that is valid under `repositories.<name>` in `config.yaml`. The value resolution order is: `.github-webhook-server.yaml` → repository config in `config.yaml` → global config in `config.yaml`.

Common keys used in `.github-webhook-server.yaml`:

- `tox`
- `pre-commit`
- `conventional-title`
- `container`
- `auto-verified-and-merged-users`
- `auto-verify-cherry-picked-prs`
- `can-be-merged-required-labels`
- `labels`
- `pr-size-thresholds`
- `ai-features`
- `security-checks`
- `custom-check-runs`
- `welcome-extra-info`
- `test-oracle`
- `set-auto-merge-prs`
- `minimum-lgtm`
- `create-issue-for-new-pr`
- `cherry-pick-assign-to-pr-author`
- `allow-commands-on-draft-prs`
- `branch-protection`

```yaml
# .github-webhook-server.yaml (in repository root)
conventional-title: "feat,fix,docs,chore"
pre-commit: true
tox:
  main: all
labels:
  enabled-labels:
    - verified
    - size
```

---

## Welcome Message File

### `.github-webhook-server-welcome-message.md`

A markdown file committed to the repository root. Its content replaces the `welcome-extra-info` config value for the Additional Information section of the PR welcome comment.

| Property | Value |
|---|---|
| Location | Repository root |
| Encoding | UTF-8 |
| Max size | 10,240 bytes (10 KB) |
| Priority | Overrides all `welcome-extra-info` config settings |

```markdown
<!-- .github-webhook-server-welcome-message.md -->
**Contribution Guidelines:**
- All PRs must include tests
- Update CHANGELOG.md for user-facing changes
- Squash commits before merging
```

---

## Full Example

```yaml
# config.yaml
log-level: INFO
log-file: webhook-server.log
mask-sensitive-data: true

github-app-id: 123456
github-tokens:
  - ghp_globaltoken1
  - ghp_globaltoken2

webhook-ip: https://webhooks.example.com/webhook_server
webhook-secret: my-secret
ip-bind: 0.0.0.0
port: 5000
max-workers: 10

verify-github-ips: true
disable-ssl-warnings: false

docker:
  username: dockeruser
  password: dockerpass

default-status-checks:
  - "WIP"
  - "can-be-merged"

auto-verified-and-merged-users:
  - "renovate[bot]"

auto-verify-cherry-picked-prs: true
create-issue-for-new-pr: true
cherry-pick-assign-to-pr-author: true

labels:
  enabled-labels:
    - verified
    - hold
    - size
    - can-be-merged
  colors:
    hold: red
    verified: green

pr-size-thresholds:
  Small:
    threshold: 50
    color: green
  Medium:
    threshold: 200
    color: orange
  Large:
    threshold: inf
    color: red

branch-protection:
  strict: true
  required_approving_review_count: 1
  required_conversation_resolution: true

security-checks:
  mandatory: true
  committer-identity-check: true
  trusted-committers:
    - "pre-commit-ci[bot]"

ai-features:
  ai-provider: claude
  ai-model: sonnet
  conventional-title:
    enabled: true
    mode: suggest

test-oracle:
  server-url: "http://localhost:8000"
  ai-provider: claude
  ai-model: sonnet
  triggers:
    - approved

repositories:
  my-service:
    name: my-org/my-service
    log-level: DEBUG
    slack-webhook-url: https://slack-webhook-url/replace-with-your-webhook-url
    events:
      - push
      - pull_request
      - issue_comment
      - check_run
    conventional-title: "feat,fix,chore,docs,ci"
    pre-commit: true
    tox:
      python-version: "3.12"
      main: all
      dev: lint,test
    container:
      username: quayuser
      password: quaypass
      repository: quay.io/myorg/my-service
      tag: latest
      release: true
    protected-branches:
      main:
        include-runs:
          - "tox"
          - "pre-commit"
    custom-check-runs:
      - name: lint
        command: uv tool run --from ruff ruff check
        mandatory: true
```

## Related Pages

- [Configuring Repositories](configuring-repositories.html)
- [Configuration Recipes](config-recipes.html)
- [Environment Variables](environment-variables.html)
- [Setting Up CI Checks](setting-up-ci-checks.html)
- [Enabling Security Checks](enabling-security-checks.html)
