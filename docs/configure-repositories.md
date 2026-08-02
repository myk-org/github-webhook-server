# config.yaml
github-app-id: 123456
github-tokens:
  - <GITHUB TOKEN1>
  - <GITHUB TOKEN2>

webhook-ip: https://your-domain.com/webhook_server

default-status-checks:
  - "WIP"
  - "can-be-merged"

repositories:
  my-repository:
    name: my-org/my-repository
    events:
      - pull_request
      - issue_comment
      - check_run
      - status
    tox:
      main: all
    pre-commit: true
    protected-branches:
      main:
        include-runs:
          - "pre-commit.ci - pr"
          - "WIP"
```

```yaml
# .github-webhook-server.yaml
conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
minimum-lgtm: 2
create-issue-for-new-pr: true
```

The first file gives the server everything it needs to bootstrap and manage the repository. The second file lets repository maintainers tune day-to-day behavior without editing the server.

## Step-by-step

### 1. Put shared defaults in `config.yaml`

Start with the settings that should apply everywhere unless a repository overrides them.

```yaml
log-level: INFO
log-file: webhook-server.log
mask-sensitive-data: true

github-app-id: 123456
github-tokens:
  - <GITHUB TOKEN1>
  - <GITHUB TOKEN2>

webhook-ip: https://your-domain.com/webhook_server

default-status-checks:
  - "WIP"
  - "dpulls"
  - "can-be-merged"

branch-protection:
  strict: true
  require_code_owner_reviews: true
  dismiss_stale_reviews: false
  required_approving_review_count: 1
  required_linear_history: true
  required_conversation_resolution: true
```

Use top-level settings for:

- Shared defaults across many repositories
- Secrets and credentials
- Startup-time behavior such as webhook creation and branch protection
- Organization-wide policies

> **Warning:** `webhook-ip` must include the full webhook path, for example `https://your-domain.com/webhook_server`.

### 2. Add each repository under `repositories`

Add one entry per managed repository. Use the short repository name as the key, and the full `owner/repo` name inside `name`.

```yaml
repositories:
  my-repository:
    name: my-org/my-repository

  another-repo:
    name: my-org/another-repo
```

Use this pattern when you want the same server to manage multiple repositories with shared defaults.

> **Warning:** The key under `repositories` should be the repository name that GitHub sends in webhook payloads, such as `my-repository`, not `my-org/my-repository`.

### 3. Add server-side overrides for repository-specific behavior

Put per-repository overrides directly inside the matching repository block when the setting is repo-specific, secret, or part of startup bootstrap.

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    log-level: DEBUG
    log-file: my-repository.log
    github-tokens:
      - <GITHUB TOKEN1>
    events:
      - push
      - pull_request
      - pull_request_review
      - pull_request_review_thread
      - issue_comment
      - check_run
      - status
    tox:
      main: all
      dev: testenv1,testenv2
    pre-commit: true
    protected-branches:
      main:
        include-runs:
          - "pre-commit.ci - pr"
          - "WIP"
        exclude-runs:
          - "SonarCloud Code Analysis"
```

This is the right place for:

- Repository-specific event subscriptions
- Dedicated tokens
- Protected branch rules
- Logging overrides
- Server-controlled credentials such as Slack webhooks, registry passwords, and PyPI tokens

If you want the repository to receive all events, omit `events` entirely instead of listing everything.

### 4. Let repository maintainers self-serve safe settings

Add a `.github-webhook-server.yaml` file at the repository root when maintainers should be able to adjust behavior without editing the server.

```yaml
conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
minimum-lgtm: 2
pre-commit: true

labels:
  enabled-labels:
    - verified
    - hold
    - size
  colors:
    hold: purple

pr-size-thresholds:
  Quick:
    threshold: 20
    color: lightgreen
  Normal:
    threshold: 100
    color: green
```

This is a good fit for:

- PR title rules
- Minimum review thresholds
- Label behavior
- PR size labels
- Draft PR command allowances
- AI feature behavior
- Security-check behavior that repository maintainers are allowed to tune

> **Warning:** Do not commit secrets to `.github-webhook-server.yaml`. Keep tokens, passwords, and other credentials in server-side `config.yaml`.

### 5. Use the right config layer for the right job

Use this split when you are deciding where a setting belongs:

| Put the setting here | Best for | Typical examples |
|---|---|---|
| Top-level `config.yaml` | Shared defaults and org-wide policy | `default-status-checks`, `branch-protection`, `mask-sensitive-data` |
| `repositories.<repo-name>` in `config.yaml` | Server-side repo overrides and secrets | `github-tokens`, `protected-branches`, `slack-webhook-url`, `pypi`, `container` |
| `.github-webhook-server.yaml` | Safe maintainer-controlled behavior | `conventional-title`, `minimum-lgtm`, `labels`, `pr-size-thresholds` |

Precedence is highest to lowest:

| Priority | Source | Use it when |
|---|---|---|
| 1 | `.github-webhook-server.yaml` | Repository maintainers should control the setting |
| 2 | `repositories.<repo-name>` in `config.yaml` | The server should override defaults for one repository |
| 3 | Top-level `config.yaml` | The setting should apply everywhere unless overridden |

### 6. Restart when bootstrap settings change

Some settings are applied during startup bootstrap, not during normal PR processing. After changing these in `config.yaml`, restart the server.

Restart after changes to settings such as:

- `webhook-ip`
- `repositories`
- `events`
- `branch-protection`
- `protected-branches`

> **Note:** The repository-local `.github-webhook-server.yaml` file is used during webhook processing, but not for startup bootstrap. Put bootstrap-critical settings in `config.yaml`.

## Advanced Usage

### Keep secrets server-side and behavior repo-side

A practical split looks like this:

| Server-side only | Safe to hand to repository maintainers |
|---|---|
| `github-tokens` | `conventional-title` |
| `pypi.token` | `minimum-lgtm` |
| `container.password` | `labels` |
| `slack-webhook-url` | `pr-size-thresholds` |
| `github-app-id` | `allow-commands-on-draft-prs` |

This keeps credentials out of Git history while still letting teams tune workflow behavior.

### Use repository-specific tokens only where needed

If most repositories can share one token pool, keep `github-tokens` at the top level. Add a repository-specific token list only when a repository needs different permissions or rate-limit isolation.

```yaml
github-tokens:
  - <GITHUB TOKEN1>
  - <GITHUB TOKEN2>

repositories:
  my-repository:
    name: my-org/my-repository
    github-tokens:
      - <REPO SPECIFIC TOKEN>
```

### Override only the keys you need

The repository-local file does not need to repeat the full server config. Add only the keys you want to change.

```yaml
# .github-webhook-server.yaml
minimum-lgtm: 1
allow-commands-on-draft-prs:
  - build-and-push-container
```

That keeps the repository config small and makes overrides easy to review.

### Use `protected-branches` to control required checks per branch

You can require all defaults, or include and exclude specific checks for one branch.

```yaml
protected-branches:
  dev: []
  main:
    include-runs:
      - "pre-commit.ci - pr"
      - "WIP"
    exclude-runs:
      - "SonarCloud Code Analysis"
```

Use an empty list when you want the default required checks for that branch. Use `include-runs` and `exclude-runs` when one branch needs a different requirement set.

### Hand off specialized settings to the right pages

Keep this page focused on where configuration lives and how it overrides. For the actual knobs in those feature areas, use the dedicated pages:

- See [Set Up Checks and Release Workflows](set-up-checks-and-release-workflows.html) for `tox`, `pre-commit`, custom checks, container builds, and PyPI settings.
- See [Enable AI Features](enable-ai-features.html) for `ai-features` and `test-oracle`.
- See [Secure Webhooks and Pull Requests](secure-webhooks-and-pull-requests.html) for `security-checks`.
- See [Set Up OWNERS and Review Rules](set-up-owners-and-reviewers.html) for `OWNERS`-driven review behavior.

## Troubleshooting

- **The server says the repository is not configured**
  - Check the key under `repositories`.
  - Make sure `name` contains the full `owner/repo`, but the map key is only the short repository name.

- **The repo-local file is ignored**
  - The file must be named exactly `.github-webhook-server.yaml`.
  - It must live at the repository root.
  - If you changed a bootstrap setting, move that change into `config.yaml` and restart the server.

- **A setting is not taking effect**
  - Check the precedence order: repo-local file overrides repository block, which overrides global defaults.
  - Remove duplicate definitions while debugging so you can see which layer is winning.

- **Branch protection or webhook subscriptions did not update**
  - Restart the server after changing `webhook-ip`, `events`, `branch-protection`, or `protected-branches`.

- **Config loads fail after editing labels**
  - Verify that `labels.enabled-labels` only uses supported label categories.
  - See [Configuration Reference](configuration-reference.html) for the allowed values.

## Related Pages

- [Configuration Reference](configuration-reference.html)
- [Set Up Checks and Release Workflows](set-up-checks-and-release-workflows.html)
- [Enable AI Features](enable-ai-features.html)
- [Secure Webhooks and Pull Requests](secure-webhooks-and-pull-requests.html)
- [Set Up OWNERS and Review Rules](set-up-owners-and-reviewers.html)
