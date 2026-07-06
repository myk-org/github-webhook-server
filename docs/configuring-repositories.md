Let me start by exploring the repository structure and the knowledge graph.

Now let me look at what settings can go in the `.github-webhook-server.yaml` file (the per-repo override file), and the config precedence:

Now let me look at how the config file is structured and what a minimal config looks like:

Now I have a comprehensive understanding of the configuration. Let me also check what settings can go into the per-repo `.github-webhook-server.yaml` file:

Now I have all the information I need. Let me write the documentation page.

# Configuring Repositories

Set up your webhook server to manage one or more GitHub repositories by defining global defaults and per-repository overrides in `config.yaml`, with optional in-repo settings in `.github-webhook-server.yaml`.

## Prerequisites

- A running webhook server instance (see [Getting Started](quickstart.html))
- At least one GitHub personal access token or GitHub App
- The `WEBHOOK_SERVER_DATA_DIR` environment variable pointing to your data directory (default: `/home/podman/data`)

## Quick Example

Create a `config.yaml` in your data directory with one repository:

```yaml
github-app-id: 123456
github-tokens:
  - ghp_your_token_here

webhook-ip: https://your-domain.com/webhook_server

repositories:
  my-repo:
    name: my-org/my-repo
```

That's it — the server will start processing webhooks for `my-org/my-repo` using all default settings.

## Step 1: Set Up Global Settings

Global settings in `config.yaml` apply to all repositories unless overridden. Place these at the top level of the file.

```yaml
log-level: INFO
log-file: webhook-server.log
mask-sensitive-data: true

github-app-id: 123456
github-tokens:
  - ghp_token_one
  - ghp_token_two

webhook-ip: https://your-domain.com/webhook_server
webhook-secret: your_webhook_secret

default-status-checks:
  - "WIP"
  - "dpulls"
  - "can-be-merged"

auto-verified-and-merged-users:
  - "renovate[bot]"
  - "pre-commit-ci[bot]"
```

> **Tip:** Provide multiple tokens in `github-tokens` for automatic failover — the server picks the token with the highest remaining API rate limit.

## Step 2: Add Repositories

Each repository lives under the `repositories` key. The key is a short name you choose; the `name` field must be the full `org/repo` format.

```yaml
repositories:
  my-app:
    name: my-org/my-app

  my-library:
    name: my-org/my-library
```

> **Warning:** A repository **must** have a `name` field in `org/repo` format. Without it, the server cannot locate the repository on GitHub.

## Step 3: Configure Repository-Specific Settings

Override any global setting at the repository level. Repository settings take precedence over global defaults.

```yaml
repositories:
  my-app:
    name: my-org/my-app
    log-level: DEBUG
    log-file: my-app.log
    slack-webhook-url: https://slack-webhook-url/replace-with-your-webhook-url

    github-tokens:
      - ghp_repo_specific_token

    events:
      - push
      - pull_request
      - pull_request_review
      - issue_comment
      - check_run
      - status

    verified-job: true
    pre-commit: true
    create-issue-for-new-pr: true
    minimum-lgtm: 1
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"

    auto-verified-and-merged-users:
      - "renovate[bot]"

    default-status-checks:
      - "WIP"
      - "can-be-merged"
      - "ci/my-external-check"

    can-be-merged-required-labels:
      - qa-approved
```

### Filtering Webhook Events

By default, the server listens to all events (`*`). Use the `events` key to listen only to specific events:

```yaml
repositories:
  my-app:
    name: my-org/my-app
    events:
      - push
      - pull_request
      - issue_comment
```

Omit the `events` key entirely to receive all events.

## Step 4: Use Per-Repository In-Repo Config (Optional)

For settings that repository maintainers should control themselves, add a `.github-webhook-server.yaml` file to the root of the GitHub repository. This file uses the same keys as the repository section in `config.yaml`.

```yaml
# .github-webhook-server.yaml (in the root of your GitHub repo)
pre-commit: true
conventional-title: "feat,fix,docs"
minimum-lgtm: 2
create-issue-for-new-pr: false
```

> **Note:** The `.github-webhook-server.yaml` file is read from the repository's default branch on every webhook event. Changes take effect immediately without restarting the server.

### Config Resolution Order

Settings are resolved in this order, with earlier sources taking priority:

| Priority | Source | Location |
|----------|--------|----------|
| 1 (highest) | `.github-webhook-server.yaml` | In the GitHub repository |
| 2 | Repository section in `config.yaml` | `repositories.<name>.*` |
| 3 (lowest) | Global section in `config.yaml` | Top-level keys |

For example, if `minimum-lgtm` is set to `2` in `.github-webhook-server.yaml`, `1` in the repository config, and `0` globally — the value `2` is used.

## Step 5: Customize the PR Welcome Message (Optional)

Add custom information to the bottom of the welcome message posted on new PRs. You can set this at any config level:

```yaml
# In config.yaml (global or per-repository)
welcome-extra-info: |
  **Note:** Please review the contribution guide before merging.
  - Ensure tests pass
  - Update documentation if needed
```

Alternatively, create a `.github-webhook-server-welcome-message.md` file in the repository root. This file takes the highest priority for welcome message content and supports full Markdown.

> **Note:** The welcome message file and `welcome-extra-info` value are each limited to 10 KB.

## Advanced Usage

### Multiple Token Failover

Supply multiple GitHub tokens for automatic failover. The server selects the token with the highest remaining API rate limit on each webhook event:

```yaml
# Global tokens (used by all repositories)
github-tokens:
  - ghp_primary_token
  - ghp_backup_token

repositories:
  critical-repo:
    name: my-org/critical-repo
    # Override with repo-specific tokens
    github-tokens:
      - ghp_dedicated_token_1
      - ghp_dedicated_token_2
```

### Setting Up CI: Tox and Pre-Commit

Configure tox test environments per branch and enable pre-commit checks:

```yaml
repositories:
  my-app:
    name: my-org/my-app
    pre-commit: true
    tox:
      python-version: "3.12"
      args: "-p -v"
      main: all
      dev: testenv1,testenv2
```

See [Setting Up CI Checks](setting-up-ci-checks.html) for full details on tox, pre-commit, container builds, and custom check runs.

### Protected Branches

Define which status checks are required for protected branches:

```yaml
repositories:
  my-app:
    name: my-org/my-app
    protected-branches:
      main:
        include-runs:
          - "pre-commit.ci - pr"
          - "WIP"
        exclude-runs:
          - "SonarCloud Code Analysis"
      dev: []   # all default checks
```

Use an empty array (`[]`) to apply all default status checks to a branch. Use `include-runs` and `exclude-runs` for fine-grained control.

See [Cherry-Picking and Branch Protection](cherry-picking-and-branching.html) for more on branch protection rules and OWNERS files.

### Branch Protection Rules

Configure GitHub branch protection settings that the server manages:

```yaml
branch-protection:
  strict: true
  require_code_owner_reviews: true
  dismiss_stale_reviews: false
  required_approving_review_count: 1
  required_linear_history: true
  required_conversation_resolution: true
```

These can be set globally or per-repository.

### Auto-Merge Configuration

Automatically merge PRs on specific branches when all checks pass:

```yaml
repositories:
  my-app:
    name: my-org/my-app
    set-auto-merge-prs:
      - main
    auto-verified-and-merged-users:
      - "renovate[bot]"
    auto-verify-cherry-picked-prs: true
```

### Commands on Draft PRs

By default, PR comment commands are blocked on draft PRs. Configure exceptions:

```yaml
repositories:
  my-app:
    name: my-org/my-app
    # Allow only specific commands on drafts
    allow-commands-on-draft-prs:
      - build-and-push-container
      - retest
```

Set to an empty list (`[]`) to allow all commands on draft PRs. Omit the key entirely to block all commands on drafts.

### Container Builds

Configure container image builds triggered by PR events or releases:

```yaml
repositories:
  my-app:
    name: my-org/my-app
    container:
      username: registry_user
      password: registry_password
      repository: registry.example.com/my-org/my-app
      tag: latest
      release: true
      context: src
      build-args:
        - MY_ARG=value
      args:
        - --format docker
```

See [Setting Up CI Checks](setting-up-ci-checks.html) for container build details and OCI annotations.

### Docker Registry Credentials

For pulling base images from Docker Hub during builds, set global Docker credentials:

```yaml
docker:
  username: your_docker_username
  password: your_docker_password
```

### Labels and PR Size Thresholds

Customize which label categories are active and define custom PR size categories:

```yaml
labels:
  enabled-labels:
    - verified
    - size
    - can-be-merged
  colors:
    verified: green
    hold: red

pr-size-thresholds:
  Tiny:
    threshold: 10
    color: lightgray
  Small:
    threshold: 50
    color: green
  Large:
    threshold: 300
    color: red
  Massive:
    threshold: inf
    color: darkred
```

Both can be set globally or per-repository. See [Configuring Labels and PR Size Thresholds](configuring-labels-and-size.html) for full details.

### Security Checks

Enable detection of suspicious file paths and committer identity mismatches:

```yaml
security-checks:
  mandatory: true
  suspicious-paths:
    - ".github/workflows/"
    - ".github/actions/"
    - ".vscode/"
  committer-identity-check: true
  trusted-committers:
    - "pre-commit-ci[bot]"
```

See [Enabling Security Checks](enabling-security-checks.html) for details.

### AI Features

Enable AI-powered conventional title suggestions, cherry-pick conflict resolution, and test oracle integration:

```yaml
ai-features:
  ai-provider: claude
  ai-model: sonnet
  conventional-title:
    enabled: true
    mode: suggest
  resolve-cherry-pick-conflicts-with-ai:
    enabled: true
```

See [Enabling AI Features](enabling-ai-features.html) for setup instructions.

### PyPI Publishing

Configure automatic PyPI publishing on release:

```yaml
repositories:
  my-library:
    name: my-org/my-library
    pypi:
      token: pypi-your-token-here
```

### Schema Validation

Use the YAML language server schema reference at the top of your `config.yaml` for editor autocompletion and validation:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/myk-org/github-webhook-server/refs/heads/main/webhook_server/config/schema.yaml
```

See [Configuration Reference](configuration-reference.html) for a complete list of every available option.

## Troubleshooting

**Server won't start — "Config file not found"**
- Ensure `config.yaml` exists in the directory specified by `WEBHOOK_SERVER_DATA_DIR` (default: `/home/podman/data`). See [Environment Variables](environment-variables.html).

**Server won't start — "does not have `repositories`"**
- Your `config.yaml` must include a `repositories` section with at least one repository entry.

**Repository not processing webhooks**
- Verify the `name` field uses `org/repo` format (e.g., `my-org/my-repo`, not just `my-repo`).
- Check that your GitHub token has access to the repository.
- If using `events`, confirm the relevant event type is listed.

**Changes to `config.yaml` not taking effect**
- The `config.yaml` is re-read on every webhook event — changes take effect on the next event without restarting the server. Settings like `log-level` and `log-file` also apply immediately.

**`.github-webhook-server.yaml` not being picked up**
- The file must be in the repository root on the default branch.
- Ensure it contains valid YAML. Invalid YAML will cause the server to fall back to `config.yaml` settings.

**"Invalid label categories in enabled-labels"**
- Only these categories are valid: `verified`, `hold`, `wip`, `needs-rebase`, `has-conflicts`, `can-be-merged`, `size`, `branch`, `cherry-pick`, `automerge`. Reviewed-by labels are always enabled.

## Related Pages

- [Configuration Reference](configuration-reference.html)
- [Getting Started](quickstart.html)
- [Setting Up CI Checks](setting-up-ci-checks.html)
- [Cherry-Picking and Branch Protection](cherry-picking-and-branching.html)
- [Configuring Labels and PR Size Thresholds](configuring-labels-and-size.html)
