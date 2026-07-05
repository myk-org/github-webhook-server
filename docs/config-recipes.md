Let me explore the repository structure and key files to write accurate documentation.

Now let me look at how the config resolution works for repo-specific overrides and more details on specific features:

Now let me look at specific handler implementations for custom check runs and protected branches:

Let me look at the runner handler for how custom check runs are executed and the `.github-webhook-server.yaml` format:

Now let me look at the `_validate_custom_check_runs` method and the auto-merge flow more closely:

Now I have enough information to write the comprehensive Configuration Recipes page. Let me check a couple more things:

Now I have comprehensive knowledge of the codebase. Let me write the Configuration Recipes page:

# Configuration Recipes

Ready-to-use `config.yaml` snippets for the most common github-webhook-server setups. Each recipe is self-contained and copy-paste ready.

For full option documentation, see the [Configuration Reference](configuration-reference.html). For per-repository file overrides, see [Configuring Repositories](configuring-repositories.html).

---

## Minimal Working Configuration

**The smallest config that will accept and process webhooks.**

```yaml
github-tokens:
  - ghp_your_github_token_here

webhook-ip: https://your-domain.com/webhook_server

repositories:
  my-repo:
    name: my-org/my-repo
```

This is the bare minimum: one GitHub token, a webhook URL, and one repository. All label categories are enabled by default, and the server listens on `0.0.0.0:5000`.

---

## Bot Auto-Merge (Renovate, Dependabot, pre-commit-ci)

**Automatically verify and merge PRs from trusted bots.**

```yaml
auto-verified-and-merged-users:
  - "renovate[bot]"
  - "pre-commit-ci[bot]"
  - "dependabot[bot]"

github-tokens:
  - ghp_token_one
  - ghp_token_two

webhook-ip: https://your-domain.com/webhook_server

repositories:
  my-repo:
    name: my-org/my-repo
    set-auto-merge-prs:
      - main
```

When a user listed in `auto-verified-and-merged-users` opens a PR, it is automatically verified and GitHub's auto-merge is enabled. The `set-auto-merge-prs` setting enables auto-merge for *all* PRs targeting the listed branches (not just bot PRs). Both settings work independently.

- `auto-verified-and-merged-users` is global — override per-repo by adding the same key under a repository.
- AI-resolved cherry-picks are **never** auto-merged regardless of these settings.
- PRs modifying [security-sensitive paths](enabling-security-checks.html) have auto-merge blocked automatically.

---

## Multi-Token Failover

**Use multiple GitHub tokens for automatic rate-limit failover.**

```yaml
github-tokens:
  - ghp_primary_token
  - ghp_secondary_token
  - ghp_tertiary_token

repositories:
  my-repo:
    name: my-org/my-repo
```

On every webhook, the server checks the rate limit of each token and selects the one with the highest remaining quota. If a single token is configured, it's used directly without rate-limit comparison. Invalid tokens (rate limit = 60) are automatically skipped.

- Override tokens per-repository with `github-tokens` under the repository block.
- Token order doesn't matter — selection is based on remaining rate limit.

### Per-Repository Token Override

```yaml
github-tokens:
  - ghp_org_wide_token

repositories:
  private-repo:
    name: my-org/private-repo
    github-tokens:
      - ghp_private_repo_token_1
      - ghp_private_repo_token_2
```

> **Tip:** Use repository-scoped tokens for private repos that require different access credentials.

---

## Conventional Commits Enforcement

**Require PR titles to follow the Conventional Commits specification.**

```yaml
repositories:
  my-repo:
    name: my-org/my-repo
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
```

A `conventional-title` check run is created for each PR. Titles must match the format `<type>[optional scope]: <description>` where the type is one of the listed values. Invalid titles fail the check.

- Use `"*"` as a wildcard to accept any type while still enforcing the format structure.
- Breaking changes (`feat!: description`) and scopes (`fix(api): description`) are supported.

### With AI-Powered Auto-Fix

```yaml
repositories:
  my-repo:
    name: my-org/my-repo
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"

ai-features:
  ai-provider: "claude"
  ai-model: "sonnet"
  conventional-title:
    enabled: true
    mode: fix            # auto-fix invalid titles (use "suggest" for suggestions only)
    timeout-minutes: 10
```

When `mode: fix` is set, the server automatically updates the PR title using AI if it doesn't match the conventional format. Use `mode: suggest` to show a suggestion in the check run output without modifying the title. See [Enabling AI Features](enabling-ai-features.html) for provider setup.

---

## Custom Check Runs

**Run your own commands as GitHub check runs on every PR.**

```yaml
repositories:
  my-repo:
    name: my-org/my-repo
    custom-check-runs:
      - name: lint
        command: uv tool run --from ruff ruff check
        mandatory: true
      - name: security-scan
        command: uv tool run --from bandit bandit -r .
        mandatory: false
```

Each custom check runs the specified command in the repository worktree. Mandatory checks (`mandatory: true`, the default) block merging if they fail. Non-mandatory checks run but don't affect the `can-be-merged` status.

- Commands support environment variables and shell syntax: `TOKEN=xyz uv tool run --from bandit bandit -r .`
- Check names must not collide with built-in names (`tox`, `pre-commit`, `build-container`, `python-module-install`, `conventional-title`, `can-be-merged`, `security-suspicious-paths`, `security-committer-identity`).
- Duplicate check names are detected and the second occurrence is skipped.
- Custom checks can be retested with `/retest lint` in a PR comment.

### Multi-Line Command

```yaml
repositories:
  my-repo:
    name: my-org/my-repo
    custom-check-runs:
      - name: integration-test
        command: |
          uv run python -c "
          import sys
          print('Running integration tests')
          sys.exit(0)
          "
```

> **Warning:** The command executable must be installed on the webhook server. The server validates executables at startup with `shutil.which()` and skips checks with missing executables.

---

## Repository-Specific Overrides

**Override global settings for individual repositories.**

```yaml
# Global defaults
log-level: INFO
auto-verified-and-merged-users:
  - "renovate[bot]"
default-status-checks:
  - "WIP"
  - "can-be-merged"
create-issue-for-new-pr: true
labels:
  enabled-labels:
    - verified
    - hold
    - size
    - can-be-merged

repositories:
  strict-repo:
    name: my-org/strict-repo
    log-level: DEBUG                          # Override log level
    default-status-checks:                    # Override status checks
      - "WIP"
      - "can-be-merged"
      - "ci/integration"
    create-issue-for-new-pr: false            # Disable tracking issues
    auto-verified-and-merged-users:           # Override auto-verified users
      - "my-bot[bot]"
    labels:                                   # Override labels
      enabled-labels:
        - verified
        - hold
        - wip
        - size
        - can-be-merged
      colors:
        hold: purple

  relaxed-repo:
    name: my-org/relaxed-repo
    # Inherits all global defaults
```

Config values are resolved in priority order: (1) `.github-webhook-server.yaml` in the repository, (2) repository section in `config.yaml`, (3) root level in `config.yaml`. Repository-level settings completely replace (not merge with) their global counterparts.

> **Tip:** Place a `.github-webhook-server.yaml` file in a repository's root to let repository maintainers control their own settings without access to the server's `config.yaml`. See [Configuring Repositories](configuring-repositories.html).

---

## Tox CI Per Branch

**Run different tox test environments depending on the PR's target branch.**

```yaml
repositories:
  my-repo:
    name: my-org/my-repo
    tox:
      main: all                       # Run all tox envs for PRs targeting main
      dev: "testenv1,testenv2"        # Run specific envs for PRs targeting dev
      args: "-p -v"                   # Extra CLI args passed to tox
      python-version: "3.12"          # Python version for tox execution
```

The `tox` key maps branch names to tox environments. Use `all` to run every environment in `tox.ini`, or a comma-separated string for specific ones. The `args` and `python-version` sub-keys apply to all branches.

---

## Protected Branches with Required Checks

**Configure which status checks are required for specific branches.**

```yaml
repositories:
  my-repo:
    name: my-org/my-repo
    protected-branches:
      main:
        include-runs:
          - "pre-commit.ci - pr"
          - "WIP"
        exclude-runs:
          - "SonarCloud Code Analysis"
      dev: []                         # All default checks, no customization
```

The `include-runs` list specifies external checks that must pass. The `exclude-runs` list removes checks from requirements. Use an empty array `[]` to accept all default checks without modification.

---

## Branch Protection Rules

**Set GitHub branch protection settings managed by the webhook server.**

```yaml
branch-protection:
  strict: true
  require_code_owner_reviews: true
  dismiss_stale_reviews: false
  required_approving_review_count: 1
  required_linear_history: true
  required_conversation_resolution: true

repositories:
  my-repo:
    name: my-org/my-repo
    branch-protection:
      strict: true
      require_code_owner_reviews: true
      dismiss_stale_reviews: true              # Override: dismiss stale reviews
      required_approving_review_count: 2       # Override: require 2 approvals
      required_linear_history: true
      required_conversation_resolution: true
```

Global `branch-protection` applies to all repositories. Override any field per-repository. The `required_conversation_resolution` setting also controls whether the server processes `pull_request_review_thread` webhook events. See [Cherry-Picking and Branch Protection](cherry-picking-and-branching.html) for details.

---

## Custom PR Size Labels

**Define custom size categories and thresholds for PR size labels.**

```yaml
pr-size-thresholds:
  Tiny:
    threshold: 10
    color: lightgray
  Small:
    threshold: 50
    color: green
  Medium:
    threshold: 150
    color: orange
  Large:
    threshold: 300
    color: red
  Massive:
    threshold: inf
    color: darkred
```

Thresholds define the *minimum* number of total changed lines (additions + deletions) for each category. Use `inf` for the unbounded largest category — it always sorts last regardless of definition order. Override per-repository under the repository block.

### Repository-Specific Size Thresholds

```yaml
repositories:
  docs-repo:
    name: my-org/docs-repo
    pr-size-thresholds:
      Express:
        threshold: 25
        color: lightblue
      Standard:
        threshold: 100
        color: green
      Premium:
        threshold: 500
        color: orange
```

See [Configuring Labels and PR Size Thresholds](configuring-labels-and-size.html) for full details on label customization.

---

## Label Customization

**Control which label categories are enabled and set custom colors.**

```yaml
labels:
  enabled-labels:
    - verified
    - hold
    - size
    - can-be-merged
    - cherry-pick
  colors:
    hold: red
    verified: green
    can-be-merged: limegreen
    approved-: green           # Prefix for dynamic labels (approved-username)
    lgtm-: yellowgreen
    cherry-pick-: coral
    branch-: royalblue
```

If `enabled-labels` is not set, all categories are enabled. Reviewed-by labels (`approved-*`, `lgtm-*`, `changes-requested-*`, `commented-*`) are always enabled and cannot be disabled. Colors use CSS3 color names.

> **Note:** Available categories: `verified`, `hold`, `wip`, `needs-rebase`, `has-conflicts`, `can-be-merged`, `size`, `branch`, `cherry-pick`, `automerge`.

---

## Security Checks

**Detect suspicious file modifications and committer identity mismatches.**

```yaml
security-checks:
  mandatory: true
  suspicious-paths:
    - ".github/workflows/"
    - ".github/actions/"
    - ".claude/"
    - ".vscode/"
    - ".cursor/"
    - ".devcontainer/"
  committer-identity-check: true
  trusted-committers:
    - "pre-commit-ci[bot]"
```

When `mandatory: true` (default), failed security checks block the `can-be-merged` status. Set to `false` for advisory-only mode. The GitHub App bot, `web-flow`, and API token users are automatically trusted — only add additional external committers to `trusted-committers`. See [Enabling Security Checks](enabling-security-checks.html).

### Advisory-Only Security (Non-Blocking)

```yaml
security-checks:
  mandatory: false
  committer-identity-check: true
  suspicious-paths:
    - ".github/workflows/"
```

---

## Container Build and Push

**Build and push container images on PR events and releases.**

```yaml
repositories:
  my-repo:
    name: my-org/my-repo
    container:
      username: myuser
      password: registry-secret-token
      repository: ghcr.io/my-org/my-repo
      tag: latest
      release: true
      build-args:
        - BUILD_ENV=production
        - VERSION=1.0
      args:
        - --format docker
      context: ""              # Repo root (default). Use "src" for subdirectory.
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

The `release: true` flag pushes the image with the release tag on new tag pushes. Container builds can be triggered manually with `/build-and-push-container` in a PR comment. See [Setting Up CI Checks](setting-up-ci-checks.html) for details.

---

## Slack Notifications

**Send webhook processing notifications to Slack.**

```yaml
repositories:
  my-repo:
    name: my-org/my-repo
    slack-webhook-url: https://slack-webhook-url/replace-with-your-webhook-url
```

Notifications are sent for PR merges, container builds, PyPI uploads, and other processing events. See [Setting Up Slack Notifications](setting-up-notifications.html) for setup instructions.

---

## Cherry-Pick Auto-Verification

**Control whether cherry-picked PRs are automatically verified.**

```yaml
# Global: auto-verify all cherry-picks (default)
auto-verify-cherry-picked-prs: true

repositories:
  critical-repo:
    name: my-org/critical-repo
    auto-verify-cherry-picked-prs: false   # Require manual verification
```

When enabled (default), cherry-picked PRs receive the `verified` label automatically. Disable per-repository for critical repos that require manual review of every cherry-pick. AI-resolved cherry-picks are **never** auto-verified regardless of this setting.

---

## Required Labels for Merge

**Require specific labels before a PR can be marked as mergeable.**

```yaml
repositories:
  my-repo:
    name: my-org/my-repo
    can-be-merged-required-labels:
      - qa-approved
      - docs-reviewed
```

The `can-be-merged` check run will not pass until all listed labels are present on the PR, in addition to all other merge requirements (approvals, passing checks, etc.).

---

## Draft PR Command Allowlist

**Allow specific commands on draft PRs.**

```yaml
repositories:
  my-repo:
    name: my-org/my-repo
    allow-commands-on-draft-prs:
      - build-and-push-container
      - retest
```

By default, all commands are blocked on draft PRs. Set an empty list `[]` to allow all commands. Set a list of specific command names to allow only those. See [Managing Pull Requests](managing-pull-requests.html) for available commands.

---

## LGTM Requirements

**Require multiple LGTM approvals before a PR can be merged.**

```yaml
repositories:
  my-repo:
    name: my-org/my-repo
    minimum-lgtm: 2
```

The PR must receive at least the specified number of `/lgtm` commands from different users before it is approved. The default is `0` (no minimum).

---

## PR Welcome Message Customization

**Add custom information to the automated PR welcome comment.**

```yaml
welcome-extra-info: |
  **Note:** Please review the contribution guide before merging.
  - Ensure tests pass
  - Update documentation if needed

repositories:
  my-repo:
    name: my-org/my-repo
    welcome-extra-info: |
      **Project-specific notes:**
      - Run `make docs` if you changed API endpoints
      - Tag @platform-team for infrastructure changes
```

The `welcome-extra-info` content is appended to the end of the PR welcome comment as raw markdown. Repository-level settings override the global value. Set an empty string `""` to explicitly clear an inherited value. Maximum size is 10 KB.

> **Tip:** You can also place a `.github-webhook-server-welcome-message.md` file in the repository root — it takes priority over all config-based settings.

---

## Full Production Setup

**A complete production-ready configuration combining multiple features.**

```yaml
log-level: INFO
log-file: webhook-server.log
mask-sensitive-data: true
disable-ssl-warnings: true

github-app-id: 123456
github-tokens:
  - ghp_primary_token
  - ghp_secondary_token

webhook-ip: https://webhooks.example.com/webhook_server
webhook-secret: your-webhook-secret-here
verify-github-ips: true
ip-bind: "0.0.0.0"
port: 5000

default-status-checks:
  - "WIP"
  - "can-be-merged"

auto-verified-and-merged-users:
  - "renovate[bot]"
  - "pre-commit-ci[bot]"

branch-protection:
  strict: true
  require_code_owner_reviews: true
  dismiss_stale_reviews: false
  required_approving_review_count: 1
  required_linear_history: true
  required_conversation_resolution: true

security-checks:
  mandatory: true
  committer-identity-check: true
  trusted-committers:
    - "pre-commit-ci[bot]"

ai-features:
  ai-provider: "claude"
  ai-model: "sonnet"
  conventional-title:
    enabled: true
    mode: suggest
  resolve-cherry-pick-conflicts-with-ai:
    enabled: true

repositories:
  backend-api:
    name: my-org/backend-api
    slack-webhook-url: https://slack-webhook-url/replace-with-your-webhook-url
    verified-job: true
    pre-commit: true
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
    minimum-lgtm: 1
    set-auto-merge-prs:
      - main
    tox:
      main: all
      dev: "unit,integration"
      python-version: "3.12"
    container:
      username: myuser
      password: registry-token
      repository: ghcr.io/my-org/backend-api
      tag: latest
      release: true
    custom-check-runs:
      - name: lint
        command: uv tool run --from ruff ruff check
      - name: type-check
        command: uv tool run --from mypy mypy src/
    protected-branches:
      main:
        include-runs:
          - "pre-commit.ci - pr"
      dev: []
```

> **Warning:** Never commit tokens or secrets directly in `config.yaml`. Use environment variables or a secret management system. See [Environment Variables](environment-variables.html).

## Related Pages

- [Configuration Reference](configuration-reference.html)
- [Configuring Repositories](configuring-repositories.html)
- [Setting Up CI Checks](setting-up-ci-checks.html)
- [Enabling Security Checks](enabling-security-checks.html)
- [Configuring Labels and PR Size Thresholds](configuring-labels-and-size.html)
