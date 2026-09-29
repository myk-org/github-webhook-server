# Automation Recipes

Short, copy-pasteable walkthroughs of common maintainer tasks. Every key here is defined in the configuration schema; for full key listings see [Configuration Reference](configuration-reference.html).

## Restrict a repository to specific webhook events

**When you'd want this:** a docs-only or dependency repo doesn't need `push` and `issue_comment` traffic, and a smaller event set means less webhook noise and fewer API calls.

Set `repositories.<repo-id>.events` in `config.yaml`. The server creates or edits the repository webhook at startup to match exactly this list. Omit the key to get the default of `["*"]` (all events).

```yaml
# config.yaml
repositories:
  my-docs-site:
    name: org/repo
    events:
      - pull_request
      - issue_comment
```

Verify what was registered:

```bash
gh api repos/org/repo/hooks --jq '.[] | select(.config.url | contains("hooks")) | .events'
```

> **Note:** `events` is read from `config.yaml` only. A repo-local `.github-webhook-server.yaml` is not consulted for it, and the change takes effect on the next server start.

## Enable auto-merge for chosen branches

**When you'd want this:** your release branch should merge on green without a human pressing the button.

```yaml
# config.yaml
repositories:
  my-service:
    name: org/repo
    set-auto-merge-prs:
      - main
      - release
```

Branch names are matched exactly against the PR base branch. Auto-merge is set with `SQUASH` as the merge method.

### The security interaction

A PR that touches a `security-checks.suspicious-paths` prefix does **not** get auto-merge, even on a configured branch. When a match is found the server:

1. comments `Auto-merge blocked: PR modifies security-sensitive paths: ...` on the PR,
2. disables auto-merge if it was already enabled on the PR.

Default prefixes: `.claude/`, `.vscode/`, `.cursor/`, `.devcontainer/`, `.pi/`, `.github/workflows/`, `.github/actions/`.

A maintainer can clear the block with a PR comment:

```
/security-override
```

That sets the `security-suspicious-paths` check run to success, and `set_pull_request_automerge()` then allows auto-merge through. `/security-override cancel` re-runs the security checks. Only maintainers can use the command.

Narrow the prefixes per repository when a repo legitimately owns these paths:

```yaml
# config.yaml
repositories:
  my-monorepo:
    name: org/repo
    security-checks:
      suspicious-paths:
        - .github/workflows/release-only/
```

Cherry-pick PRs whose conflicts were resolved by AI are never auto-merged, even on a configured branch.

## Add custom check runs

**When you'd want this:** you want one more gate in the PR pipeline — a linter, a security scan — without writing a new check into the server.

`custom-check-runs` is a repository-scoped list. Each entry needs `name` and `command`; `mandatory` defaults to `true`.

```yaml
# config.yaml
repositories:
  my-service:
    name: org/repo
    custom-check-runs:
      - name: lint
        command: uv tool run --from ruff ruff check
        mandatory: true
      - name: security-scan
        command: TOKEN=xyz DEBUG=true uv tool run --from bandit bandit -r .
        mandatory: false
```

- The command runs in the repository worktree. Leading `VAR=value` assignments are allowed and skipped when the executable is resolved.
- The server resolves the executable with `shutil.which()` at startup. If it is not on the server, the check is skipped with a warning — the check silently does not exist.
- `mandatory: true` puts the check in the required-check list so it blocks merge. `mandatory: false` runs it and reports the result without blocking.
- Names must match `^[a-zA-Z0-9._-]{1,64}$` and be unique.
- Names must not collide with `BUILTIN_CHECK_NAMES` in `webhook_server/utils/constants.py`: `tox`, `pre-commit`, `build-container`, `python-module-install`, `conventional-title`, `can-be-merged`, `security-suspicious-paths`, `security-committer-identity`. A colliding check is skipped with a warning.
- Valid custom names work with `/retest <name>`.

Re-run a check on an open PR:

```
/retest lint
```

## Require labels before a PR can be merged

**When you'd want this:** a PR must carry a `security-reviewed` (or similar) label before `can-be-merged` passes.

```yaml
# config.yaml
repositories:
  my-service:
    name: org/repo
    can-be-merged-required-labels:
      - security-reviewed
      - ready
```

Every listed label must be present on the PR. Missing ones are reported in the `can-be-merged` check output as `Missing required labels: ...`.

## Pre-verify trusted users so their PRs merge automatically

**When you'd want this:** bots and senior maintainers should not need a second approval on every PR.

```yaml
# config.yaml
auto-verified-and-merged-users:
  - renovate[bot]
  - my-org-release-bot

repositories:
  my-service:
    name: org/repo
    # optional per-repo replacement
    auto-verified-and-merged-users:
      - renovate[bot]
      - alice
      - bob
    # cherry-picked PRs are auto-verified by default
    auto-verify-cherry-picked-prs: true
```

Behavior:

- When the PR's parent committer is in the list, the `verified` label is added, the `verified` check run is set to success, and auto-merge is enabled regardless of branch.
- A new commit pushed to the PR resets the `verified` label to queued.
- Users from `github-tokens` (the API users) are added to the list at runtime, so those PRs are auto-verified too.
- `auto-verify-cherry-picked-prs: false` removes the `verified` label from cherry-picked PRs and forces the manual path. The default is `true`.

## Override a repository's configuration locally

**When you'd want this:** a repository wants to turn off a check for itself without a server-side config change.

Precedence is first-value-wins: repo-local `.github-webhook-server.yaml` → `config.yaml` `repositories.<repo-id>` → `config.yaml` root.

```yaml
# .github-webhook-server.yaml (in org/repo, at the repository root)
pre-commit: false
minimum-lgtm: 2
set-auto-merge-prs:
  - main
```

Arrays and scalars are replaced wholesale, not merged. Two exceptions:

- `branch-protection` overlays property by property.
- `labels` overlays global, and `labels.colors` merges by key.

Blocks `ai-features`, `security-checks`, `test-oracle`, and `pr-size-thresholds` replace the global block entirely rather than deep-merging — repeat required subkeys when overriding.

`events`, `name`, `log-level`, `log-file`, `mask-sensitive-data`, `github-tokens`, `default-status-checks`, `protected-branches`, `allow-commands-on-draft-prs`, and `pr-size-thresholds` are never read from `.github-webhook-server.yaml`. They belong in `config.yaml`.

An empty string in `welcome-extra-info` clears the inherited value, and a `.github-webhook-server-welcome-message.md` file in the repository overrides the configured text entirely.
