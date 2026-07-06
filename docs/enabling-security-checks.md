Let me start by exploring the repository structure and the knowledge graph.

Now let me explore the security-related source code:

Now let me look at the security override command and the issue comment handler:

Now let me look at the security tests to understand edge cases and behaviors:

Now let me check the retest command to see how security checks can be retested:

Now let me check the per-repository security-checks override pattern:

I now have a comprehensive understanding of the security checks system. Let me write the documentation.

# Enabling Security Checks

Protect your repositories from supply-chain attacks and unauthorized commits by enabling security checks on pull requests. These checks detect modifications to sensitive file paths and verify that committer identities match PR authors.

## Prerequisites

- A running webhook server instance (see [Getting Started](quickstart.html))
- At least one repository configured in `config.yaml` (see [Configuring Repositories](configuring-repositories.html))

## Quick Example

Add this to your `config.yaml` to enable both security checks with default settings:

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
    - ".pi/"
  committer-identity-check: true
  trusted-committers:
    - "pre-commit-ci[bot]"
```

This creates two GitHub check runs on every PR:
- **`security-suspicious-paths`** — fails if any changed file starts with a monitored path prefix
- **`security-committer-identity`** — fails if the last commit's committer doesn't match the PR author

Both checks block merging by default (`mandatory: true`).

## Step-by-Step Setup

### 1. Enable Suspicious Path Detection

The suspicious path check compares every changed file in a PR against a list of path prefixes. If any file matches, the check run fails and lists all flagged files.

```yaml
security-checks:
  suspicious-paths:
    - ".github/workflows/"
    - ".github/actions/"
```

If you omit `suspicious-paths`, the server uses these defaults:

| Default Path Prefix | What It Protects |
|---|---|
| `.claude/` | Claude AI configuration |
| `.vscode/` | VS Code settings and extensions |
| `.cursor/` | Cursor editor rules |
| `.devcontainer/` | Dev container definitions |
| `.pi/` | Pi sidecar configuration |
| `.github/workflows/` | CI/CD workflow definitions |
| `.github/actions/` | Custom GitHub Actions |

> **Tip:** Set `suspicious-paths` to an empty list (`[]`) to disable this check entirely while keeping other security checks active.

### 2. Enable Committer Identity Verification

The committer identity check compares the PR author against the last commit's committer. It catches cases where someone pushes a commit to another user's PR branch.

```yaml
security-checks:
  committer-identity-check: true
```

This check is enabled by default. The check run will:
- **Pass** when the last committer matches the PR author
- **Pass** when the last committer is in the trusted committers list
- **Fail** when the last committer is a different, untrusted user
- **Fail** when the committer identity is unknown (no linked GitHub account)

> **Note:** The committer identity check also detects web-flow impersonation. If someone creates a GitHub account named `web-flow`, the check verifies the account's immutable user ID against GitHub's real web-flow system account.

### 3. Configure Trusted Committers

Bots and automation tools frequently commit to PR branches with a different identity than the PR author. Add them to `trusted-committers` to prevent false positives:

```yaml
security-checks:
  committer-identity-check: true
  trusted-committers:
    - "pre-commit-ci[bot]"
    - "renovate[bot]"
    - "my-org-bot"
```

You only need to list **external** committers here. The following are automatically trusted:
- The GitHub App bot used by the webhook server
- GitHub's `web-flow` account (used for web UI merges and edits)
- All API users from your configured `github-tokens`

> **Tip:** Trusted committer matching is case-insensitive. `Pre-Commit-CI[bot]` and `pre-commit-ci[bot]` are treated as the same identity.

### 4. Choose Mandatory or Advisory Mode

By default, security checks block the `can-be-merged` status. Set `mandatory: false` to make them advisory — the checks still run and report results, but they won't prevent merging:

| Setting | Check Runs Execute | Blocks Merge | Blocks Auto-Merge |
|---|---|---|---|
| `mandatory: true` (default) | ✅ | ✅ | ✅ |
| `mandatory: false` | ✅ | ❌ | ✅* |

\* Suspicious path detection always blocks auto-merge for flagged files, regardless of the `mandatory` setting.

```yaml
security-checks:
  mandatory: false  # Advisory only — checks run but don't block merge
  suspicious-paths:
    - ".github/workflows/"
  committer-identity-check: true
```

## Overriding Security Checks

When a maintainer has reviewed a flagged PR and determined the changes are safe, they can override the security checks using a PR comment:

```
/security-override
```

This sets both security check runs to pass. Only repository maintainers (defined in OWNERS files) can use this command.

To reverse an override and re-run the security checks:

```
/security-override cancel
```

> **Warning:** Non-maintainers who attempt `/security-override` will receive a rejection comment. The check runs remain in their original state.

You can also re-run individual security checks using the retest command:

```
/retest security-suspicious-paths
/retest security-committer-identity
```

See [Managing Pull Requests](managing-pull-requests.html) for the full list of PR comment commands.

## Advanced Usage

### Per-Repository Configuration

Override global security settings for a specific repository in `config.yaml`:

```yaml
security-checks:
  mandatory: true
  suspicious-paths:
    - ".github/workflows/"
    - ".github/actions/"
  committer-identity-check: true

repositories:
  my-repository:
    name: my-org/my-repository
    security-checks:
      suspicious-paths:
        - ".github/workflows/"  # Only monitor CI workflows for this repo
      committer-identity-check: false  # Disable identity check for this repo
```

You can also configure security checks in a repository's `.github-webhook-server.yaml` file, which takes priority over `config.yaml`. See [Configuring Repositories](configuring-repositories.html) for the override hierarchy.

### Custom Suspicious Paths

Monitor project-specific sensitive locations by adding custom path prefixes:

```yaml
security-checks:
  suspicious-paths:
    - ".github/workflows/"
    - ".github/actions/"
    - "deploy/"
    - "infra/terraform/"
    - "scripts/ci/"
    - ".npmrc"
```

Path matching is prefix-based: a prefix of `deploy/` matches `deploy/production.yml`, `deploy/scripts/rollback.sh`, and any other file under that directory.

### Auto-Merge Interaction

When suspicious path detection is enabled, the server automatically blocks auto-merge for any PR that modifies flagged files — even if auto-merge was already enabled on the PR:

1. The PR is checked for changed files matching suspicious path prefixes
2. If matches are found, auto-merge is blocked and a comment is posted listing the flagged files
3. If auto-merge was already enabled on the PR, it is automatically disabled
4. A maintainer can use `/security-override` to allow auto-merge to proceed

> **Note:** This protection applies regardless of whether `mandatory` is `true` or `false`. Suspicious paths always block auto-merge as a defense-in-depth measure.

### Welcome Message Integration

When security checks are enabled, the PR welcome message automatically includes a **Security Checks** section showing:
- Which checks are active and what they monitor
- Whether checks are mandatory or advisory
- Available override commands (for maintainers)

## Troubleshooting

**Security check shows "unknown" committer**
The last commit was made with a local Git identity that isn't linked to a GitHub account. The committer needs to add their Git email to their GitHub profile, or push a new commit with a verified email.

**Trusted committer still fails the identity check**
Verify the login name matches exactly (case doesn't matter, but special characters like `[bot]` do). Check the check run output — it shows both the PR author and last committer for comparison.

**Security checks don't appear on PRs**
Ensure `security-checks` is configured either globally or for the specific repository. If `suspicious-paths` is set to `[]` *and* `committer-identity-check` is `false`, no checks will run.

**Auto-merge blocked but security check passed**
This can happen if the security check was overridden by a maintainer but the check run result was later invalidated by a new push. Use `/security-override` again after reviewing the updated changes.

## Related Pages

- [Managing Pull Requests](managing-pull-requests.html)
- [Configuring Repositories](configuring-repositories.html)
- [Configuration Reference](configuration-reference.html)
- [PR Comment Command Recipes](pr-command-recipes.html)
- [Configuration Recipes](config-recipes.html)
