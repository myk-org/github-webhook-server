# config.yaml
github-app-id: 123456
github-tokens:
  - ghp_admin_token
webhook-ip: https://hooks.example.com/webhook_server

repositories:
  github-webhook-server:
    name: my-org/github-webhook-server
    pre-commit: true
    conventional-title: "feat,fix,docs"

# .github-webhook-server.yaml
pre-commit: false
conventional-title: "feat,fix,docs,refactor"
```

## Global Keys

### Logging

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `log-level` | `string` | `INFO` | Main application log level. Allowed values: `INFO`, `DEBUG`. | Controls webhook server log verbosity. |
| `log-file` | `string` | unset | Main application log file path. Relative paths resolve under `<data-dir>/logs/`. | Writes main server logs to this file; omit for console-only main logs. |
| `mcp-log-file` | `string` | `mcp_server.log` | MCP server log file path. Relative paths resolve under `<data-dir>/logs/`. | Writes `/mcp` server logs when MCP is enabled. |
| `logs-server-log-file` | `string` | `logs_server.log` | Log viewer server log file path. Relative paths resolve under `<data-dir>/logs/`. | Writes `/logs` server logs when the log viewer is enabled. |
| `mask-sensitive-data` | `boolean` | `true` | Redacts tokens, passwords, webhook secrets, registry credentials, and similar values from logs. | Applies log masking across the server unless a repo-level `config.yaml` override is present. |

```yaml
log-level: INFO
log-file: webhook-server.log
mcp-log-file: mcp_server.log
logs-server-log-file: logs_server.log
mask-sensitive-data: true
```

### GitHub Access and Network

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `github-app-id` | `integer` | unset | GitHub App ID used by the server. | Identifies the GitHub App when the server looks up app metadata and manages repositories. |
| `github-tokens` | `array<string>` | unset | Personal access tokens the server can use for repository API calls. | The server picks the token with the highest remaining rate limit. |
| `webhook-ip` | `string` | unset | Full webhook callback URL, including path. | Registered on managed repositories as the webhook target URL. |
| `webhook-secret` | `string` | unset | Shared webhook secret. | Enables HMAC-SHA256 validation of incoming GitHub webhook payloads. |
| `verify-github-ips` | `boolean` | `false` | Restrict incoming requests to GitHub’s published webhook IP ranges. | Loads GitHub CIDRs at startup and rejects requests outside the allowlist. |
| `verify-cloudflare-ips` | `boolean` | `false` | Restrict incoming requests to Cloudflare’s published IP ranges. | Loads Cloudflare CIDRs at startup and rejects requests outside the allowlist. |
| `disable-ssl-warnings` | `boolean` | `false` | Disable urllib3 SSL warnings. | Suppresses SSL warning noise in logs. |
| `ip-bind` | `string` | `0.0.0.0` | Interface address for the HTTP server. | Controls which network interface the server listens on. |
| `port` | `integer` | `500` | HTTP server port. | Controls the listening port for webhook and API endpoints. |
| `max-workers` | `integer` | `10` | Maximum Uvicorn worker count. | Used in production mode; ignored when dev reload mode is enabled. |

> **Warning:** If `verify-github-ips` or `verify-cloudflare-ips` is enabled and no allowlist loads successfully, the server fails closed and does not start.

```yaml
github-app-id: 123456
github-tokens:
  - ghp_primary_token
  - ghp_fallback_token
webhook-ip: https://hooks.example.com/webhook_server
webhook-secret: <webhook-secret>
verify-github-ips: true
verify-cloudflare-ips: true
disable-ssl-warnings: false
ip-bind: 0.0.0.0
port: 500
max-workers: 10
```

### Global Repository Defaults

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `default-status-checks` | `array<string>` | `[]` | Seed list of required status checks for protected branches. | When a branch is listed under `protected-branches`, the server starts with this list, always adds `can-be-merged`, then appends built-in checks implied by repo settings. |
| `auto-verified-and-merged-users` | `array<string>` | `[]` | Users whose PRs are treated as auto-verified. | Applies as the global fallback user list; API users from `github-tokens` are added at runtime. |
| `auto-verify-cherry-picked-prs` | `boolean` | `true` | Global default for cherry-picked PR auto-verification. | Repo-level value can disable or re-enable automatic verification of cherry-picked PRs. |
| `create-issue-for-new-pr` | `boolean` | `true` | Global default for PR issue creation. | Controls whether new PRs create a tracking issue by default. |
| `cherry-pick-assign-to-pr-author` | `boolean` | `true` | Global default for cherry-pick assignee behavior. | Controls whether cherry-pick PRs are assigned to the original PR author by default. |
| `allow-commands-on-draft-prs` | `array<string>` | unset | Global draft-PR command allowlist. Use slash-command names without `/`. | Omitted blocks draft-PR commands, `[]` allows all, and a non-empty list allows only the listed commands. |

```yaml
default-status-checks:
  - ci/external
  - policy/manual-approval
auto-verified-and-merged-users:
  - renovate[bot]
auto-verify-cherry-picked-prs: true
create-issue-for-new-pr: true
cherry-pick-assign-to-pr-author: true
allow-commands-on-draft-prs:
  - retest
  - build-and-push-container
```

### `docker`

Where: `Global`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `docker.username` | `string` | unset | Docker Hub username. Required when the `docker` block is present. | Used for startup `docker.io` login. |
| `docker.password` | `string` | unset | Docker Hub password or token. Required when the `docker` block is present. | Used for startup `docker.io` login. |

```yaml
docker:
  username: dockerhub-user
  password: <dockerhub-token>
```

## Repository Registration

### `repositories`

Where: `Global`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `repositories` | `object` | none | Non-empty map of managed repositories. | Required. Registers the repositories the server manages. |
| `repositories.<repo-id>` | `object` | none | Per-repository configuration block. `<repo-id>` must match the GitHub repository name from the webhook payload, not `owner/repo`. | Selects the correct repo config at webhook time. |
| `repositories.<repo-id>.name` | `string` | none | Full repository name in `owner/repo` format. | Used for GitHub API access, webhook registration, and repo setup. |

```yaml
repositories:
  github-webhook-server:
    name: my-org/github-webhook-server
  docsfy:
    name: my-org/docsfy
```

## Repository Keys

### Repo Keys Read From `config.yaml` Only

Where: `Repo`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `log-level` | `string` | inherits global; else `INFO` | Per-repo log level. Allowed values: `INFO`, `DEBUG`. | Overrides global log verbosity for this repository. |
| `log-file` | `string` | inherits global | Per-repo log file path. Relative paths resolve under `<data-dir>/logs/`. | Overrides the main log destination for this repository. |
| `mask-sensitive-data` | `boolean` | inherits global; else `true` | Per-repo log masking override. | Enables or disables secret redaction for this repository’s logs. |
| `github-tokens` | `array<string>` | inherits global | Per-repo token list. | Replaces the global token list for this repository’s API selection. |
| `events` | `array<string>` | `["*"]` | GitHub webhook event names to subscribe to for this repository. | Controls the repository webhook subscription created or updated at startup. |
| `default-status-checks` | `array<string>` | inherits global; else `[]` | Per-repo replacement for the global seed list. | Used when protected branches are configured for this repository. |
| `allow-commands-on-draft-prs` | `array<string>` | inherits global; else unset | Per-repo draft-PR command allowlist. Use slash-command names without `/`. | Omitted blocks draft-PR commands, `[]` allows all, non-empty list allows only the listed commands. |

> **Note:** For event behavior after delivery, see [Supported GitHub Events](supported-github-events.html).

```yaml
repositories:
  github-webhook-server:
    name: my-org/github-webhook-server
    log-level: DEBUG
    log-file: github-webhook-server.log
    mask-sensitive-data: true
    github-tokens:
      - ghp_repo_specific_token
    events:
      - pull_request
      - issue_comment
      - push
      - check_run
      - status
    default-status-checks:
      - ci/external
    allow-commands-on-draft-prs:
      - retest
      - build-and-push-container
```

### Repo Keys Read From `config.yaml` or `.github-webhook-server.yaml`

Where: `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `slack-webhook-url` | `string` | unset | Slack incoming webhook URL. | Sends Slack notifications for container and PyPI publish results and failures. |
| `verified-job` | `boolean` | `true` | Enable the `verified` workflow. | Adds the `verified` check to merge logic and required-check generation. |
| `pre-commit` | `boolean` | `false` | Enable pre-commit execution for PR checks. | Adds the `pre-commit` check run for this repository. |
| `tox-python-version` | `string` | unset | Legacy tox Python version key. | Used only when `tox.python-version` is absent; emits a deprecation warning. |
| `auto-verified-and-merged-users` | `array<string>` | inherits global; else `[]` | Per-repo replacement for the auto-verified user list. | Limits auto-verification to the listed users for this repository. |
| `auto-verify-cherry-picked-prs` | `boolean` | inherits global; else `true` | Per-repo cherry-pick auto-verification setting. | Controls whether eligible cherry-picked PRs are auto-verified. |
| `set-auto-merge-prs` | `array<string>` | `[]` | Exact base branch names that should have auto-merge enabled. | If a PR targets a listed branch and becomes mergeable, the server enables GitHub auto-merge. |
| `can-be-merged-required-labels` | `array<string>` | `[]` | Labels that must be present before a PR can be marked mergeable. | Adds extra label gates to the `can-be-merged` workflow. |
| `conventional-title` | `string` | unset | Comma-separated allowed Conventional Commit types, or `*` for any valid type. | Enables the `conventional-title` check run for this repository. |
| `minimum-lgtm` | `integer` | `0` | Minimum LGTM count. | Requires this many LGTM approvals before the PR can satisfy merge rules. |
| `create-issue-for-new-pr` | `boolean` | inherits global; else `true` | Per-repo tracking-issue setting. | Overrides the global issue-creation behavior for new PRs. |
| `cherry-pick-assign-to-pr-author` | `boolean` | inherits global; else `true` | Per-repo cherry-pick assignee setting. | Overrides whether cherry-pick PRs are assigned to the original PR author. |

> **Warning:** `pre-commit` is runtime-disabled until you set it to `true`, even though the schema advertises a `true` default.

```yaml
# Either under repositories.<repo-id> in config.yaml
# or at the root of .github-webhook-server.yaml
slack-webhook-url: https://hooks.slack.com/services/TEAM/CHANNEL/TOKEN
verified-job: true
pre-commit: true
conventional-title: "feat,fix,docs,refactor"
minimum-lgtm: 2
set-auto-merge-prs:
  - main
can-be-merged-required-labels:
  - approved
  - security-reviewed
create-issue-for-new-pr: false
cherry-pick-assign-to-pr-author: true
```

## Shared Blocks

### `branch-protection`

Where: `Global` or `Repo`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `branch-protection.strict` | `boolean` | `true` | Use strict status checks. | GitHub requires the branch to be up to date before merging. |
| `branch-protection.require_code_owner_reviews` | `boolean` | `false` | Require code owner reviews. | GitHub enforces code owner review approval before merge. |
| `branch-protection.dismiss_stale_reviews` | `boolean` | `true` | Dismiss stale reviews after new commits. | GitHub invalidates earlier approvals on newer commits. |
| `branch-protection.required_approving_review_count` | `integer` | `0` | Required GitHub approval count. | GitHub enforces the numeric approval threshold. |
| `branch-protection.required_linear_history` | `boolean` | `true` | Require linear commit history. | GitHub blocks non-linear merge history. |
| `branch-protection.required_conversation_resolution` | `boolean` | `true` | Require resolved review conversations. | GitHub enforces conversation resolution, and the webhook runtime listens to review-thread events only when this is enabled. |

> **Note:** Repo `branch-protection` values overlay global values field by field.

```yaml
branch-protection:
  strict: true
  require_code_owner_reviews: false
  dismiss_stale_reviews: true
  required_approving_review_count: 1
  required_linear_history: true
  required_conversation_resolution: true

repositories:
  github-webhook-server:
    name: my-org/github-webhook-server
    branch-protection:
      require_code_owner_reviews: true
      required_approving_review_count: 2
```

### `labels`

Where: `Global` or `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `labels.enabled-labels` | `array<string>` | all configurable categories enabled | Enabled label categories. Valid values: `verified`, `hold`, `wip`, `needs-rebase`, `has-conflicts`, `can-be-merged`, `size`, `branch`, `cherry-pick`, `automerge`. | Restricts which auto-managed label families the server creates and updates. |
| `labels.colors` | `object<string,string>` | `{}` | Color overrides using CSS3 color names. | Overrides default label colors. |
| `labels.colors.<label-or-prefix>` | `string` | unset | Exact label name such as `hold`, or a dynamic label prefix ending in `-` such as `approved-` or `branch-`. | Applies the configured color when matching labels are created or updated. |

> **Note:** Reviewed-by labels such as `approved-*`, `lgtm-*`, `commented-*`, and `changes-requested-*` are always enabled.


> **Tip:** Use `pr-size-thresholds` to control `size/*` label names and colors.

```yaml
labels:
  enabled-labels:
    - verified
    - hold
    - size
    - branch
  colors:
    hold: red
    verified: green
    approved-: blue
    branch-: darkorange
```

### `welcome-extra-info`

Where: `Global` or `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `welcome-extra-info` | `string` | inherits outer scope; else empty string | Markdown appended to the PR welcome comment. Maximum runtime size is 10 KB UTF-8. An empty string explicitly clears an inherited value. | Adds extra guidance to the welcome comment unless a repo file overrides it. |

> **Note:** If `.github-webhook-server-welcome-message.md` exists in the repository, its contents replace `welcome-extra-info`. An empty file suppresses configured welcome text.

```yaml
welcome-extra-info: |
  Please link the tracking issue.
  Review the release checklist before merging.
```

### `security-checks`

Where: `Global` or `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `security-checks.mandatory` | `boolean` | `true` | Make security checks blocking instead of advisory. | When `true`, security checks join required merge checks. |
| `security-checks.suspicious-paths` | `array<string>` | built-in sensitive-path set | Path prefixes treated as sensitive. | PRs that modify matching paths fail the `security-suspicious-paths` check. |
| `security-checks.committer-identity-check` | `boolean` | `true` | Compare the PR author with the last commit committer. | Fails the `security-committer-identity` check when the identities do not match and no trust exception applies. |
| `security-checks.trusted-committers` | `array<string>` | `[]` | Additional trusted committer logins. | Allows listed committers to pass the identity check; entries are normalized to lowercase. |

> **Note:** Built-in `suspicious-paths` defaults are `.claude/`, `.vscode/`, `.cursor/`, `.devcontainer/`, `.pi/`, `.github/workflows/`, and `.github/actions/`.


> **Note:** The server automatically trusts the GitHub App bot login, `web-flow`, and the API users behind `github-tokens`.

```yaml
security-checks:
  mandatory: true
  suspicious-paths:
    - .github/workflows/
    - .github/actions/
    - Dockerfile
  committer-identity-check: true
  trusted-committers:
    - pre-commit-ci[bot]
    - release-bot
```

### `ai-features`

Where: `Global` or `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `ai-features.ai-provider` | `string` | none | AI provider. Allowed values: `claude`, `gemini`, `cursor`. Required when the block is present. | Selects the provider for all AI features in the block. |
| `ai-features.ai-model` | `string` | none | Model identifier. Required when the block is present. | Selects the model for all AI features in the block. |
| `ai-features.conventional-title.enabled` | `boolean` | none | Enable AI assistance for the `conventional-title` check. Required when the sub-block is present. | Turns AI title suggestions or auto-fixes on for Conventional Commit validation failures. |
| `ai-features.conventional-title.mode` | `string` | `suggest` | Allowed values: `suggest`, `fix`. | `suggest` writes a suggestion into the check run; `fix` updates the PR title automatically. |
| `ai-features.conventional-title.timeout-minutes` | `integer` | `10` | AI CLI timeout in minutes. Minimum `1`. | Limits how long the AI title step can run. |
| `ai-features.resolve-cherry-pick-conflicts-with-ai.enabled` | `boolean` | none | Enable AI cherry-pick conflict resolution. Required when the sub-block is present. | Lets the server attempt AI conflict resolution for cherry-pick failures. |
| `ai-features.resolve-cherry-pick-conflicts-with-ai.timeout-minutes` | `integer` | `10` | AI CLI timeout in minutes. Minimum `1`. | Limits how long the AI cherry-pick resolution step can run. |

> **Warning:** Repo/local `ai-features` replaces the global block. Repeat `ai-provider` and `ai-model` in every override.


> **Note:** Cherry-picks resolved with AI are never auto-verified.

```yaml
ai-features:
  ai-provider: claude
  ai-model: sonnet
  conventional-title:
    enabled: true
    mode: suggest
    timeout-minutes: 10
  resolve-cherry-pick-conflicts-with-ai:
    enabled: true
    timeout-minutes: 10
```

### `test-oracle`

Where: `Global` or `Repo`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `test-oracle.server-url` | `string` | none | Base URL of the test oracle service. Required when the block is present. | The webhook server calls this service for recommendations. |
| `test-oracle.ai-provider` | `string` | none | Provider name. Allowed values: `claude`, `gemini`, `cursor`. Required when the block is present. | Sent to the oracle service for model selection. |
| `test-oracle.ai-model` | `string` | none | Model identifier. Required when the block is present. | Sent to the oracle service for model selection. |
| `test-oracle.test-patterns` | `array<string>` | service defaults | Test file globs. | Restricts which test paths the oracle recommends from. |
| `test-oracle.triggers` | `array<string>` | `["approved"]` | Automatic trigger names. Allowed values: `approved`, `pr-opened`, `pr-synchronized`. | Controls when the server runs automatic oracle analysis. |

> **Note:** `approved` refers to the `/approve` command path used by this server. The `/test-oracle` command works whenever the block is configured.


> **Warning:** Repo `test-oracle` replaces the global block. `.github-webhook-server.yaml` does not override this block.

```yaml
test-oracle:
  server-url: http://test-oracle.internal:800
  ai-provider: claude
  ai-model: sonnet
  test-patterns:
    - tests/**/*.py
  triggers:
    - approved
    - pr-opened
```

### `pr-size-thresholds`

Where: `Global` or `Repo`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `pr-size-thresholds.<label>.threshold` | `integer` or `string` | none | Exclusive upper bound for the bucket. Use a positive integer or the string `inf`. | The first threshold greater than total additions plus deletions wins. |
| `pr-size-thresholds.<label>.color` | `string` | `lightgray` | CSS3 color name for `size/<label>`. | Sets the label color for the bucket. |

> **Note:** Built-in thresholds are `size/XS` for `<20`, `size/S` for `<50`, `size/M` for `<100`, `size/L` for `<300`, `size/XL` for `<500`, and `size/XXL` otherwise.


> **Warning:** Repo `pr-size-thresholds` replaces the global block. `.github-webhook-server.yaml` does not override this block.

```yaml
pr-size-thresholds:
  XS:
    threshold: 20
    color: lightgray
  M:
    threshold: 100
    color: orange
  XXL:
    threshold: inf
    color: darkred
```

## Repository-Only Blocks

### `tox`

Where: `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `tox.<base-branch>` | `string` | unset | Exact PR base branch name mapped to a tox env list, or the literal value `all`. | If the PR base branch matches exactly, the value becomes the `tox -e` selection; `all` runs tox without `-e`. |
| `tox.args` | `string` | empty string | Extra CLI arguments appended to the generated tox command. | Modifies the tox invocation for every PR in this repository. |
| `tox.python-version` | `string` | unset | Python version passed to `uvx` as `--python=<version>`. | Selects the Python runtime used to launch tox. |
| `tox-python-version` | `string` | unset | Deprecated legacy form of `tox.python-version`. | Used only when `tox.python-version` is absent. |

> **Warning:** Use exact branch names and string env lists. The runtime does not expand branch globs, and array branch values are not normalized before execution.


> **Note:** If a `tox` block exists but no branch key matches the PR base branch, tox still runs with the repository’s default tox configuration.

```yaml
tox:
  main: all
  develop: unit,lint
  args: "-p -v"
  python-version: "3.11"
```

### `protected-branches`

Where: `Repo`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `protected-branches.<branch>` | `object` | unset | Exact branch name to configure at startup. Use `{}` for automatic required checks. | The startup repository setup applies GitHub branch protection to this branch. |
| `protected-branches.<branch>.include-runs` | `array<string>` | `[]` | Explicit required status checks. | If non-empty, this becomes the branch’s full required-check list. |
| `protected-branches.<branch>.exclude-runs` | `array<string>` | `[]` | Status checks to remove from the automatic required-check list. | Applied only when `include-runs` is empty. |

> **Warning:** Use exact branch names and the object form shown below. The schema accepts array shorthand, but the startup branch-settings path reads the object form.

```yaml
repositories:
  github-webhook-server:
    name: my-org/github-webhook-server
    protected-branches:
      main: {}
      develop:
        include-runs:
          - can-be-merged
          - verified
          - tox
        exclude-runs:
          - pre-commit.ci - pr
```

### `pypi`

Where: `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `pypi.token` | `string` | unset | PyPI token. Required when the `pypi` block is present. | Enables package publishing on the repository’s release/tag workflow. |

```yaml
pypi:
  token: <pypi-token>
```

### `container`

Where: `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `container.username` | `string` | none | Registry username. Required when the `container` block is present. | Used for image push credentials. |
| `container.password` | `string` | none | Registry password or token. Required when the `container` block is present. | Used for image push credentials. |
| `container.repository` | `string` | none | Full image repository name. Required when the `container` block is present. | Target image repository for builds and pushes. |
| `container.tag` | `string` | `latest` | Default main/master release tag. | Used for merged PRs targeting `main` or `master`; PR builds use `pr-<number>`. |
| `container.release` | `boolean` | `false` | Push images on release/tag workflows. | Enables publish behavior in release flows. |
| `container.build-args` | `array<string>` | `[]` | Build arguments passed to the container build command. | Adds `--build-arg` inputs to the build. |
| `container.args` | `array<string>` | `[]` | Extra build command arguments. | Appends additional arguments such as `--platform` or `--pull`. |
| `container.context` | `string` | empty string | Build context subdirectory, relative to repo root. Allowed characters: letters, numbers, `.`, `_`, `-`, `/`. | Uses the given subdirectory as the build context. |
| `container.dockerfile` | `string` | `Dockerfile` | Dockerfile path. Supported by the runtime even though it is not declared in the schema. | Selects the Dockerfile used for the build. |

```yaml
container:
  username: quay-user
  password: <quay-token>
  repository: quay.io/example/my-image
  tag: latest
  release: true
  build-args:
    - VERSION=1.2.3
  args:
    - --platform=linux/amd64
  context: src/app
  dockerfile: Dockerfile
```

#### `container.oci-annotations`

Where: `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `container.oci-annotations.enabled` | `boolean` | `false` | Turn OCI annotations on. | Adds OCI annotations to built images. |
| `container.oci-annotations.static.<key>` | `string` | unset | Static annotation value. Use reverse-domain keys such as `org.opencontainers.image.vendor`. | Adds fixed annotations to every built image. |
| `container.oci-annotations.auto.created` | `boolean` | `true` | Auto-populate `org.opencontainers.image.created`. | Adds the build timestamp annotation. |
| `container.oci-annotations.auto.source` | `boolean` | `true` | Auto-populate `org.opencontainers.image.source`. | Adds the source repository URL annotation. |
| `container.oci-annotations.auto.revision` | `boolean` | `true` | Auto-populate `org.opencontainers.image.revision`. | Adds the commit SHA annotation. |
| `container.oci-annotations.auto.version` | `boolean` | `true` | Auto-populate `org.opencontainers.image.version`. | Adds the pushed tag on release builds. |
| `container.oci-annotations.auto.title` | `boolean` | `true` | Auto-populate `org.opencontainers.image.title`. | Adds the repository name annotation. |

> **Warning:** `container.context` must stay under the repository root. Path traversal is rejected at runtime.

```yaml
container:
  username: quay-user
  password: <quay-token>
  repository: quay.io/example/my-image
  oci-annotations:
    enabled: true
    static:
      org.opencontainers.image.vendor: Example Corp
    auto:
      created: true
      source: true
      revision: true
      version: true
      title: true
```

### `custom-check-runs`

Where: `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `custom-check-runs[].name` | `string` | none | Check run name. Required. Use only `A-Z`, `a-z`, `0-9`, `.`, `_`, `-`, maximum length `64`. | Creates a GitHub check run with this exact name and exposes `/retest <name>`. |
| `custom-check-runs[].command` | `string` | none | Shell command to run in the repository worktree. Required. Leading `VAR=value` assignments are allowed. | Executes the custom check command for PR workflows. |
| `custom-check-runs[].mandatory` | `boolean` | `true` | Whether this custom check is required for merge. | Mandatory checks join the required-check list; optional checks still run but do not block merge. |

> **Warning:** Custom check names must be unique and cannot collide with built-in check names: `tox`, `pre-commit`, `build-container`, `python-module-install`, `conventional-title`, `can-be-merged`, `security-suspicious-paths`, and `security-committer-identity`.


> **Note:** The server validates that the command has an executable after any leading environment assignments, and that the executable exists on the server.

```yaml
custom-check-runs:
  - name: lint
    command: uv tool run --from ruff ruff check
    mandatory: true
  - name: security-scan
    command: TOKEN=value uv tool run --from bandit bandit -r .
    mandatory: false
```# Configuration Reference

Reference for `config.yaml`, repository entries under `repositories.<repo-id>`, and repository-local `.github-webhook-server.yaml` overrides. See [Configure Repositories](configure-repositories.html) for rollout patterns, [Set Up Checks and Release Workflows](set-up-checks-and-release-workflows.html) for workflow examples, [Enable AI Features](enable-ai-features.html) for AI setup, [Secure Webhooks and Pull Requests](secure-webhooks-and-pull-requests.html) for hardening, [Supported GitHub Events](supported-github-events.html) for event coverage, and [Environment Variables](environment-variables.html) for non-YAML settings.

## Files and Resolution

| Scope label | Location |
|---|---|
| `Global` | Root of `config.yaml` |
| `Repo` | `config.yaml` under `repositories.<repo-id>` |
| `Local` | Root of `.github-webhook-server.yaml` |

| File | Purpose | Effect |
|---|---|---|
| `config.yaml` | Server-wide settings and per-repository registration | Required. Defines global defaults, managed repositories, webhook subscriptions, branch settings, and runtime behavior. |
| `.github-webhook-server.yaml` | Repository-local runtime overrides | Optional. Overrides only the keys the webhook runtime reads from the repository after startup. |

| Resolution rule | Effect |
|---|---|
| Scalar and array values | First defined value wins: `Local` -> `Repo` -> `Global`. |
| `branch-protection` | Repo values overlay global values property by property. |
| `labels` | Repo/local values overlay global values; `labels.colors` entries merge by key. |
| `welcome-extra-info` | Empty string clears an inherited value. A repo file named `.github-webhook-server-welcome-message.md` overrides configured text entirely. |
| `ai-features`, `security-checks`, `test-oracle`, `pr-size-thresholds` | A repo-scoped block replaces the global block instead of deep-merging it. Repeat required subkeys when overriding. |

> **Warning:** `.github-webhook-server.yaml` is not consulted for `name`, `log-level`, `log-file`, `mask-sensitive-data`, `github-tokens`, `events`, `default-status-checks`, `protected-branches`, `allow-commands-on-draft-prs`, `test-oracle`, or `pr-size-thresholds`. Put those keys in `config.yaml`.

> **Note:** A repo-local `branch-protection` block only affects webhook-time `required_conversation_resolution` handling. GitHub branch protection updates at startup still come from `config.yaml`.

```yaml
# config.yaml
github-app-id: 123456
github-tokens:
  - ghp_admin_token
webhook-ip: https://hooks.example.com/webhook_server

repositories:
  github-webhook-server:
    name: my-org/github-webhook-server
    pre-commit: true
    conventional-title: "feat,fix,docs"

# .github-webhook-server.yaml
pre-commit: false
conventional-title: "feat,fix,docs,refactor"
```

## Global Keys

### Logging

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `log-level` | `string` | `INFO` | Main application log level. Allowed values: `INFO`, `DEBUG`. | Controls webhook server log verbosity. |
| `log-file` | `string` | unset | Main application log file path. Relative paths resolve under `<data-dir>/logs/`. | Writes main server logs to this file; omit for console-only main logs. |
| `mcp-log-file` | `string` | `mcp_server.log` | MCP server log file path. Relative paths resolve under `<data-dir>/logs/`. | Writes `/mcp` server logs when MCP is enabled. |
| `logs-server-log-file` | `string` | `logs_server.log` | Log viewer server log file path. Relative paths resolve under `<data-dir>/logs/`. | Writes `/logs` server logs when the log viewer is enabled. |
| `mask-sensitive-data` | `boolean` | `true` | Redacts tokens, passwords, webhook secrets, registry credentials, and similar values from logs. | Applies log masking across the server unless a repo-level `config.yaml` override is present. |

```yaml
log-level: INFO
log-file: webhook-server.log
mcp-log-file: mcp_server.log
logs-server-log-file: logs_server.log
mask-sensitive-data: true
```

### GitHub Access and Network

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `github-app-id` | `integer` | unset | GitHub App ID used by the server. | Identifies the GitHub App when the server looks up app metadata and manages repositories. |
| `github-tokens` | `array<string>` | unset | Personal access tokens the server can use for repository API calls. | The server picks the token with the highest remaining rate limit. |
| `webhook-ip` | `string` | unset | Full webhook callback URL, including path. | Registered on managed repositories as the webhook target URL. |
| `webhook-secret` | `string` | unset | Shared webhook secret. | Enables HMAC-SHA256 validation of incoming GitHub webhook payloads. |
| `verify-github-ips` | `boolean` | `false` | Restrict incoming requests to GitHub’s published webhook IP ranges. | Loads GitHub CIDRs at startup and rejects requests outside the allowlist. |
| `verify-cloudflare-ips` | `boolean` | `false` | Restrict incoming requests to Cloudflare’s published IP ranges. | Loads Cloudflare CIDRs at startup and rejects requests outside the allowlist. |
| `disable-ssl-warnings` | `boolean` | `false` | Disable urllib3 SSL warnings. | Suppresses SSL warning noise in logs. |
| `ip-bind` | `string` | `0.0.0.0` | Interface address for the HTTP server. | Controls which network interface the server listens on. |
| `port` | `integer` | `5000` | HTTP server port. | Controls the listening port for webhook and API endpoints. |
| `max-workers` | `integer` | `10` | Maximum Uvicorn worker count. | Used in production mode; ignored when dev reload mode is enabled. |

> **Warning:** If `verify-github-ips` or `verify-cloudflare-ips` is enabled and no allowlist loads successfully, the server fails closed and does not start.

```yaml
github-app-id: 123456
github-tokens:
  - ghp_primary_token
  - ghp_fallback_token
webhook-ip: https://hooks.example.com/webhook_server
webhook-secret: <webhook-secret>
verify-github-ips: true
verify-cloudflare-ips: true
disable-ssl-warnings: false
ip-bind: 0.0.0.0
port: 5000
max-workers: 10
```

### Global Repository Defaults

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `default-status-checks` | `array<string>` | `[]` | Seed list of required status checks for protected branches. | When a branch is listed under `protected-branches`, the server starts with this list, always adds `can-be-merged`, then appends built-in checks implied by repo settings. |
| `auto-verified-and-merged-users` | `array<string>` | `[]` | Users whose PRs are treated as auto-verified. | Applies as the global fallback user list; API users from `github-tokens` are added at runtime. |
| `auto-verify-cherry-picked-prs` | `boolean` | `true` | Global default for cherry-picked PR auto-verification. | Repo-level value can disable or re-enable automatic verification of cherry-picked PRs. |
| `create-issue-for-new-pr` | `boolean` | `true` | Global default for PR issue creation. | Controls whether new PRs create a tracking issue by default. |
| `cherry-pick-assign-to-pr-author` | `boolean` | `true` | Global default for cherry-pick assignee behavior. | Controls whether cherry-pick PRs are assigned to the original PR author by default. |
| `allow-commands-on-draft-prs` | `array<string>` | unset | Global draft-PR command allowlist. Use slash-command names without `/`. | Omitted blocks draft-PR commands, `[]` allows all, and a non-empty list allows only the listed commands. |

```yaml
default-status-checks:
  - ci/external
  - policy/manual-approval
auto-verified-and-merged-users:
  - renovate[bot]
auto-verify-cherry-picked-prs: true
create-issue-for-new-pr: true
cherry-pick-assign-to-pr-author: true
allow-commands-on-draft-prs:
  - retest
  - build-and-push-container
```

### `docker`

Where: `Global`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `docker.username` | `string` | unset | Docker Hub username. Required when the `docker` block is present. | Used for startup `docker.io` login. |
| `docker.password` | `string` | unset | Docker Hub password or token. Required when the `docker` block is present. | Used for startup `docker.io` login. |

```yaml
docker:
  username: dockerhub-user
  password: <dockerhub-token>
```

## Repository Registration

### `repositories`

Where: `Global`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `repositories` | `object` | none | Non-empty map of managed repositories. | Required. Registers the repositories the server manages. |
| `repositories.<repo-id>` | `object` | none | Per-repository configuration block. `<repo-id>` must match the GitHub repository name from the webhook payload, not `owner/repo`. | Selects the correct repo config at webhook time. |
| `repositories.<repo-id>.name` | `string` | none | Full repository name in `owner/repo` format. | Used for GitHub API access, webhook registration, and repo setup. |

```yaml
repositories:
  github-webhook-server:
    name: my-org/github-webhook-server
  docsfy:
    name: my-org/docsfy
```

## Repository Keys

### Repo Keys Read From `config.yaml` Only

Where: `Repo`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `log-level` | `string` | inherits global; else `INFO` | Per-repo log level. Allowed values: `INFO`, `DEBUG`. | Overrides global log verbosity for this repository. |
| `log-file` | `string` | inherits global | Per-repo log file path. Relative paths resolve under `<data-dir>/logs/`. | Overrides the main log destination for this repository. |
| `mask-sensitive-data` | `boolean` | inherits global; else `true` | Per-repo log masking override. | Enables or disables secret redaction for this repository’s logs. |
| `github-tokens` | `array<string>` | inherits global | Per-repo token list. | Replaces the global token list for this repository’s API selection. |
| `events` | `array<string>` | `["*"]` | GitHub webhook event names to subscribe to for this repository. | Controls the repository webhook subscription created or updated at startup. |
| `default-status-checks` | `array<string>` | inherits global; else `[]` | Per-repo replacement for the global seed list. | Used when protected branches are configured for this repository. |
| `allow-commands-on-draft-prs` | `array<string>` | inherits global; else unset | Per-repo draft-PR command allowlist. Use slash-command names without `/`. | Omitted blocks draft-PR commands, `[]` allows all, non-empty list allows only the listed commands. |

> **Note:** For event behavior after delivery, see [Supported GitHub Events](supported-github-events.html).

```yaml
repositories:
  github-webhook-server:
    name: my-org/github-webhook-server
    log-level: DEBUG
    log-file: github-webhook-server.log
    mask-sensitive-data: true
    github-tokens:
      - ghp_repo_specific_token
    events:
      - pull_request
      - issue_comment
      - push
      - check_run
      - status
    default-status-checks:
      - ci/external
    allow-commands-on-draft-prs:
      - retest
      - build-and-push-container
```

### Repo Keys Read From `config.yaml` or `.github-webhook-server.yaml`

Where: `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `slack-webhook-url` | `string` | unset | Slack incoming webhook URL. | Sends Slack notifications for container and PyPI publish results and failures. |
| `verified-job` | `boolean` | `true` | Enable the `verified` workflow. | Adds the `verified` check to merge logic and required-check generation. |
| `pre-commit` | `boolean` | `false` | Enable pre-commit execution for PR checks. | Adds the `pre-commit` check run for this repository. |
| `tox-python-version` | `string` | unset | Legacy tox Python version key. | Used only when `tox.python-version` is absent; emits a deprecation warning. |
| `auto-verified-and-merged-users` | `array<string>` | inherits global; else `[]` | Per-repo replacement for the auto-verified user list. | Limits auto-verification to the listed users for this repository. |
| `auto-verify-cherry-picked-prs` | `boolean` | inherits global; else `true` | Per-repo cherry-pick auto-verification setting. | Controls whether eligible cherry-picked PRs are auto-verified. |
| `set-auto-merge-prs` | `array<string>` | `[]` | Exact base branch names that should have auto-merge enabled. | If a PR targets a listed branch and becomes mergeable, the server enables GitHub auto-merge. |
| `can-be-merged-required-labels` | `array<string>` | `[]` | Labels that must be present before a PR can be marked mergeable. | Adds extra label gates to the `can-be-merged` workflow. |
| `conventional-title` | `string` | unset | Comma-separated allowed Conventional Commit types, or `*` for any valid type. | Enables the `conventional-title` check run for this repository. |
| `minimum-lgtm` | `integer` | `0` | Minimum LGTM count. | Requires this many LGTM approvals before the PR can satisfy merge rules. |
| `create-issue-for-new-pr` | `boolean` | inherits global; else `true` | Per-repo tracking-issue setting. | Overrides the global issue-creation behavior for new PRs. |
| `cherry-pick-assign-to-pr-author` | `boolean` | inherits global; else `true` | Per-repo cherry-pick assignee setting. | Overrides whether cherry-pick PRs are assigned to the original PR author. |

> **Warning:** `pre-commit` is runtime-disabled until you set it to `true`, even though the schema advertises a `true` default.

```yaml
# Either under repositories.<repo-id> in config.yaml
# or at the root of .github-webhook-server.yaml
slack-webhook-url: https://hooks.slack.com/services/TEAM/CHANNEL/TOKEN
verified-job: true
pre-commit: true
conventional-title: "feat,fix,docs,refactor"
minimum-lgtm: 2
set-auto-merge-prs:
  - main
can-be-merged-required-labels:
  - approved
  - security-reviewed
create-issue-for-new-pr: false
cherry-pick-assign-to-pr-author: true
```

## Shared Blocks

### `branch-protection`

Where: `Global` or `Repo`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `branch-protection.strict` | `boolean` | `true` | Use strict status checks. | GitHub requires the branch to be up to date before merging. |
| `branch-protection.require_code_owner_reviews` | `boolean` | `false` | Require code owner reviews. | GitHub enforces code owner review approval before merge. |
| `branch-protection.dismiss_stale_reviews` | `boolean` | `true` | Dismiss stale reviews after new commits. | GitHub invalidates earlier approvals on newer commits. |
| `branch-protection.required_approving_review_count` | `integer` | `0` | Required GitHub approval count. | GitHub enforces the numeric approval threshold. |
| `branch-protection.required_linear_history` | `boolean` | `true` | Require linear commit history. | GitHub blocks non-linear merge history. |
| `branch-protection.required_conversation_resolution` | `boolean` | `true` | Require resolved review conversations. | GitHub enforces conversation resolution, and the webhook runtime listens to review-thread events only when this is enabled. |

> **Note:** Repo `branch-protection` values overlay global values field by field.

```yaml
branch-protection:
  strict: true
  require_code_owner_reviews: false
  dismiss_stale_reviews: true
  required_approving_review_count: 1
  required_linear_history: true
  required_conversation_resolution: true

repositories:
  github-webhook-server:
    name: my-org/github-webhook-server
    branch-protection:
      require_code_owner_reviews: true
      required_approving_review_count: 2
```

### `labels`

Where: `Global` or `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `labels.enabled-labels` | `array<string>` | all configurable categories enabled | Enabled label categories. Valid values: `verified`, `hold`, `wip`, `needs-rebase`, `has-conflicts`, `can-be-merged`, `size`, `branch`, `cherry-pick`, `automerge`. | Restricts which auto-managed label families the server creates and updates. |
| `labels.colors` | `object<string,string>` | `{}` | Color overrides using CSS3 color names. | Overrides default label colors. |
| `labels.colors.<label-or-prefix>` | `string` | unset | Exact label name such as `hold`, or a dynamic label prefix ending in `-` such as `approved-` or `branch-`. | Applies the configured color when matching labels are created or updated. |

> **Note:** Reviewed-by labels such as `approved-*`, `lgtm-*`, `commented-*`, and `changes-requested-*` are always enabled.


> **Tip:** Use `pr-size-thresholds` to control `size/*` label names and colors.

```yaml
labels:
  enabled-labels:
    - verified
    - hold
    - size
    - branch
  colors:
    hold: red
    verified: green
    approved-: blue
    branch-: darkorange
```

### `welcome-extra-info`

Where: `Global` or `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `welcome-extra-info` | `string` | inherits outer scope; else empty string | Markdown appended to the PR welcome comment. Maximum runtime size is 10 KB UTF-8. An empty string explicitly clears an inherited value. | Adds extra guidance to the welcome comment unless a repo file overrides it. |

> **Note:** If `.github-webhook-server-welcome-message.md` exists in the repository, its contents replace `welcome-extra-info`. An empty file suppresses configured welcome text.

```yaml
welcome-extra-info: |
  Please link the tracking issue.
  Review the release checklist before merging.
```

### `security-checks`

Where: `Global` or `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `security-checks.mandatory` | `boolean` | `true` | Make security checks blocking instead of advisory. | When `true`, security checks join required merge checks. |
| `security-checks.suspicious-paths` | `array<string>` | built-in sensitive-path set | Path prefixes treated as sensitive. | PRs that modify matching paths fail the `security-suspicious-paths` check. |
| `security-checks.committer-identity-check` | `boolean` | `true` | Compare the PR author with the last commit committer. | Fails the `security-committer-identity` check when the identities do not match and no trust exception applies. |
| `security-checks.trusted-committers` | `array<string>` | `[]` | Additional trusted committer logins. | Allows listed committers to pass the identity check; entries are normalized to lowercase. |

> **Note:** Built-in `suspicious-paths` defaults are `.claude/`, `.vscode/`, `.cursor/`, `.devcontainer/`, `.pi/`, `.github/workflows/`, and `.github/actions/`.


> **Note:** The server automatically trusts the GitHub App bot login, `web-flow`, and the API users behind `github-tokens`.

```yaml
security-checks:
  mandatory: true
  suspicious-paths:
    - .github/workflows/
    - .github/actions/
    - Dockerfile
  committer-identity-check: true
  trusted-committers:
    - pre-commit-ci[bot]
    - release-bot
```

### `ai-features`

Where: `Global` or `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `ai-features.ai-provider` | `string` | none | AI provider. Allowed values: `claude`, `gemini`, `cursor`. Required when the block is present. | Selects the provider for all AI features in the block. |
| `ai-features.ai-model` | `string` | none | Model identifier. Required when the block is present. | Selects the model for all AI features in the block. |
| `ai-features.conventional-title.enabled` | `boolean` | none | Enable AI assistance for the `conventional-title` check. Required when the sub-block is present. | Turns AI title suggestions or auto-fixes on for Conventional Commit validation failures. |
| `ai-features.conventional-title.mode` | `string` | `suggest` | Allowed values: `suggest`, `fix`. | `suggest` writes a suggestion into the check run; `fix` updates the PR title automatically. |
| `ai-features.conventional-title.timeout-minutes` | `integer` | `10` | AI CLI timeout in minutes. Minimum `1`. | Limits how long the AI title step can run. |
| `ai-features.resolve-cherry-pick-conflicts-with-ai.enabled` | `boolean` | none | Enable AI cherry-pick conflict resolution. Required when the sub-block is present. | Lets the server attempt AI conflict resolution for cherry-pick failures. |
| `ai-features.resolve-cherry-pick-conflicts-with-ai.timeout-minutes` | `integer` | `10` | AI CLI timeout in minutes. Minimum `1`. | Limits how long the AI cherry-pick resolution step can run. |

> **Warning:** Repo/local `ai-features` replaces the global block. Repeat `ai-provider` and `ai-model` in every override.


> **Note:** Cherry-picks resolved with AI are never auto-verified.

```yaml
ai-features:
  ai-provider: claude
  ai-model: sonnet
  conventional-title:
    enabled: true
    mode: suggest
    timeout-minutes: 10
  resolve-cherry-pick-conflicts-with-ai:
    enabled: true
    timeout-minutes: 10
```

### `test-oracle`

Where: `Global` or `Repo`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `test-oracle.server-url` | `string` | none | Base URL of the test oracle service. Required when the block is present. | The webhook server calls this service for recommendations. |
| `test-oracle.ai-provider` | `string` | none | Provider name. Allowed values: `claude`, `gemini`, `cursor`. Required when the block is present. | Sent to the oracle service for model selection. |
| `test-oracle.ai-model` | `string` | none | Model identifier. Required when the block is present. | Sent to the oracle service for model selection. |
| `test-oracle.test-patterns` | `array<string>` | service defaults | Test file globs. | Restricts which test paths the oracle recommends from. |
| `test-oracle.triggers` | `array<string>` | `["approved"]` | Automatic trigger names. Allowed values: `approved`, `pr-opened`, `pr-synchronized`. | Controls when the server runs automatic oracle analysis. |

> **Note:** `approved` refers to the `/approve` command path used by this server. The `/test-oracle` command works whenever the block is configured.


> **Warning:** Repo `test-oracle` replaces the global block. `.github-webhook-server.yaml` does not override this block.

```yaml
test-oracle:
  server-url: http://test-oracle.internal:8000
  ai-provider: claude
  ai-model: sonnet
  test-patterns:
    - tests/**/*.py
  triggers:
    - approved
    - pr-opened
```

### `pr-size-thresholds`

Where: `Global` or `Repo`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `pr-size-thresholds.<label>.threshold` | `integer` or `string` | none | Exclusive upper bound for the bucket. Use a positive integer or the string `inf`. | The first threshold greater than total additions plus deletions wins. |
| `pr-size-thresholds.<label>.color` | `string` | `lightgray` | CSS3 color name for `size/<label>`. | Sets the label color for the bucket. |

> **Note:** Built-in thresholds are `size/XS` for `<20`, `size/S` for `<50`, `size/M` for `<100`, `size/L` for `<300`, `size/XL` for `<500`, and `size/XXL` otherwise.


> **Warning:** Repo `pr-size-thresholds` replaces the global block. `.github-webhook-server.yaml` does not override this block.

```yaml
pr-size-thresholds:
  XS:
    threshold: 20
    color: lightgray
  M:
    threshold: 100
    color: orange
  XXL:
    threshold: inf
    color: darkred
```

## Repository-Only Blocks

### `tox`

Where: `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `tox.<base-branch>` | `string` | unset | Exact PR base branch name mapped to a tox env list, or the literal value `all`. | If the PR base branch matches exactly, the value becomes the `tox -e` selection; `all` runs tox without `-e`. |
| `tox.args` | `string` | empty string | Extra CLI arguments appended to the generated tox command. | Modifies the tox invocation for every PR in this repository. |
| `tox.python-version` | `string` | unset | Python version passed to `uvx` as `--python=<version>`. | Selects the Python runtime used to launch tox. |
| `tox-python-version` | `string` | unset | Deprecated legacy form of `tox.python-version`. | Used only when `tox.python-version` is absent. |

> **Warning:** Use exact branch names and string env lists. The runtime does not expand branch globs, and array branch values are not normalized before execution.


> **Note:** If a `tox` block exists but no branch key matches the PR base branch, tox still runs with the repository’s default tox configuration.

```yaml
tox:
  main: all
  develop: unit,lint
  args: "-p -v"
  python-version: "3.11"
```

### `protected-branches`

Where: `Repo`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `protected-branches.<branch>` | `object` | unset | Exact branch name to configure at startup. Use `{}` for automatic required checks. | The startup repository setup applies GitHub branch protection to this branch. |
| `protected-branches.<branch>.include-runs` | `array<string>` | `[]` | Explicit required status checks. | If non-empty, this becomes the branch’s full required-check list. |
| `protected-branches.<branch>.exclude-runs` | `array<string>` | `[]` | Status checks to remove from the automatic required-check list. | Applied only when `include-runs` is empty. |

> **Warning:** Use exact branch names and the object form shown below. The schema accepts array shorthand, but the startup branch-settings path reads the object form.

```yaml
repositories:
  github-webhook-server:
    name: my-org/github-webhook-server
    protected-branches:
      main: {}
      develop:
        include-runs:
          - can-be-merged
          - verified
          - tox
        exclude-runs:
          - pre-commit.ci - pr
```

### `pypi`

Where: `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `pypi.token` | `string` | unset | PyPI token. Required when the `pypi` block is present. | Enables package publishing on the repository’s release/tag workflow. |

```yaml
pypi:
  token: <pypi-token>
```

### `container`

Where: `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `container.username` | `string` | none | Registry username. Required when the `container` block is present. | Used for image push credentials. |
| `container.password` | `string` | none | Registry password or token. Required when the `container` block is present. | Used for image push credentials. |
| `container.repository` | `string` | none | Full image repository name. Required when the `container` block is present. | Target image repository for builds and pushes. |
| `container.tag` | `string` | `latest` | Default main/master release tag. | Used for merged PRs targeting `main` or `master`; PR builds use `pr-<number>`. |
| `container.release` | `boolean` | `false` | Push images on release/tag workflows. | Enables publish behavior in release flows. |
| `container.build-args` | `array<string>` | `[]` | Build arguments passed to the container build command. | Adds `--build-arg` inputs to the build. |
| `container.args` | `array<string>` | `[]` | Extra build command arguments. | Appends additional arguments such as `--platform` or `--pull`. |
| `container.context` | `string` | empty string | Build context subdirectory, relative to repo root. Allowed characters: letters, numbers, `.`, `_`, `-`, `/`. | Uses the given subdirectory as the build context. |
| `container.dockerfile` | `string` | `Dockerfile` | Dockerfile path. Supported by the runtime even though it is not declared in the schema. | Selects the Dockerfile used for the build. |

```yaml
container:
  username: quay-user
  password: <quay-token>
  repository: quay.io/example/my-image
  tag: latest
  release: true
  build-args:
    - VERSION=1.2.3
  args:
    - --platform=linux/amd64
  context: src/app
  dockerfile: Dockerfile
```

#### `container.oci-annotations`

Where: `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `container.oci-annotations.enabled` | `boolean` | `false` | Turn OCI annotations on. | Adds OCI annotations to built images. |
| `container.oci-annotations.static.<key>` | `string` | unset | Static annotation value. Use reverse-domain keys such as `org.opencontainers.image.vendor`. | Adds fixed annotations to every built image. |
| `container.oci-annotations.auto.created` | `boolean` | `true` | Auto-populate `org.opencontainers.image.created`. | Adds the build timestamp annotation. |
| `container.oci-annotations.auto.source` | `boolean` | `true` | Auto-populate `org.opencontainers.image.source`. | Adds the source repository URL annotation. |
| `container.oci-annotations.auto.revision` | `boolean` | `true` | Auto-populate `org.opencontainers.image.revision`. | Adds the commit SHA annotation. |
| `container.oci-annotations.auto.version` | `boolean` | `true` | Auto-populate `org.opencontainers.image.version`. | Adds the pushed tag on release builds. |
| `container.oci-annotations.auto.title` | `boolean` | `true` | Auto-populate `org.opencontainers.image.title`. | Adds the repository name annotation. |

> **Warning:** `container.context` must stay under the repository root. Path traversal is rejected at runtime.

```yaml
container:
  username: quay-user
  password: <quay-token>
  repository: quay.io/example/my-image
  oci-annotations:
    enabled: true
    static:
      org.opencontainers.image.vendor: Example Corp
    auto:
      created: true
      source: true
      revision: true
      version: true
      title: true
```

### `custom-check-runs`

Where: `Repo/local`

| Key | Type | Default | Description | Effect |
|---|---|---|---|---|
| `custom-check-runs[].name` | `string` | none | Check run name. Required. Use only `A-Z`, `a-z`, `0-9`, `.`, `_`, `-`, maximum length `64`. | Creates a GitHub check run with this exact name and exposes `/retest <name>`. |
| `custom-check-runs[].command` | `string` | none | Shell command to run in the repository worktree. Required. Leading `VAR=value` assignments are allowed. | Executes the custom check command for PR workflows. |
| `custom-check-runs[].mandatory` | `boolean` | `true` | Whether this custom check is required for merge. | Mandatory checks join the required-check list; optional checks still run but do not block merge. |

> **Warning:** Custom check names must be unique and cannot collide with built-in check names: `tox`, `pre-commit`, `build-container`, `python-module-install`, `conventional-title`, `can-be-merged`, `security-suspicious-paths`, and `security-committer-identity`.


> **Note:** The server validates that the command has an executable after any leading environment assignments, and that the executable exists on the server.

```yaml
custom-check-runs:
  - name: lint
    command: uv tool run --from ruff ruff check
    mandatory: true
  - name: security-scan
    command: TOKEN=value uv tool run --from bandit bandit -r .
    mandatory: false
```

## Related Pages

- [Configure Repositories](configure-repositories.html)
- [Set Up Checks and Release Workflows](set-up-checks-and-release-workflows.html)
- [Enable AI Features](enable-ai-features.html)
- [Secure Webhooks and Pull Requests](secure-webhooks-and-pull-requests.html)
- [Environment Variables](environment-variables.html)
