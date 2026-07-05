# Repository Overrides

Repository overrides let you customize pull request workflows, required labels, automated checks, and release behaviors for a specific repository by placing a configuration file directly within its codebase. This allows development teams to manage their own runtime policies in version control without needing administrative access to the central webhook server.

## Prerequisites

* Access to push changes or open a pull request to the target repository.
* A running github-webhook-server instance configured to listen to events for your repository.

## Quick Example

To apply repository-specific behavior, create a file named `.github-webhook-server.yaml` at the root of your repository.

Here is the simplest configuration to enforce a minimum of two approvals and require specific PR labels before merging:

```yaml
minimum-lgtm: 2

can-be-merged-required-labels:
  - "approved"
  - "tests-passed"

conventional-title: "feat,fix,chore,docs,style,refactor,test"
```

Once committed to your default branch, the webhook server immediately applies these rules to subsequent pull requests in this repository.

## Step-by-step: Customizing Your Repository

Repository overrides are ideal for runtime workflows like container builds, test commands, and pull request label rules.

### Step 1. Create the override file
Create a new file called `.github-webhook-server.yaml` in the top level of your project directory.

### Step 2. Define Pull Request rules
Add settings to dictate how PRs are handled. For example, if you want to require users to resolve conversations and have a specific check pass:

```yaml
# Require all comment threads to be resolved
branch-protection:
  required_conversation_resolution: true

# Specify branches that should auto-merge when checks pass
set-auto-merge-prs:
  - main
```

### Step 3. Configure automated testing (Tox)
If your repository uses `tox` for testing, you can instruct the webhook server how to run it based on the target branch.

```yaml
tox:
  main: "tests,linting"
  develop: "tests"
  feature/*: ["tests", "quick-lint"]
  python-version: "3.11"
```

### Step 4. Commit and test
Commit the file to your repository.

```bash
git add .github-webhook-server.yaml
git commit -m "chore: configure webhook server overrides"
git push
```

> **Note:** The server fetches `.github-webhook-server.yaml` dynamically when processing webhooks. You do not need to restart the server to apply these changes.

## Global vs Local Configuration

Not all settings belong in `.github-webhook-server.yaml`. Administrative settings should stay in the global `config.yaml` on the server, while workflow logic belongs in the repository.

| Configuration Area | Best Location | Reason |
| :--- | :--- | :--- |
| **PR Labels & Approvals** | `.github-webhook-server.yaml` | Developers control workflow requirements via PRs. |
| **Release Artifacts (PyPI/Docker)** | `.github-webhook-server.yaml` | Image tags and build args naturally evolve with the code. |
| **Tox / Pre-commit Rules** | `.github-webhook-server.yaml` | Test commands frequently change between project updates. |
| **Webhook Events List** | Server `config.yaml` | Performance and administrative control over payload traffic. |
| **GitHub Access Tokens** | Server `config.yaml` | Secrets should remain on the server, not in repo config. |

For a full explanation of how settings merge, see the [Configuration Model](configuration-model.html).

## Advanced Usage

### Container Image Publishing

You can automate building and pushing Docker/Podman images directly from the local repository configuration. This is useful for repositories that act as microservices or publish their own distinct images.

```yaml
container:
  repository: quay.io/your-org/your-repo
  tag: latest
  release: true
  build-args:
    - "ENABLE_DEBUG=false"
  args:
    - "--platform=linux/amd64"
```

> **Tip:** Provide registry credentials (`username` and `password`) in the central server configuration for security, while keeping the repository name and tags here. See [Container and PyPI Workflows](container-and-pypi-workflows.html).

### AI Enhancements and Conventional Commits

You can enable AI features to automatically validate or suggest standard title prefixes on your repository.

```yaml
ai-features:
  ai-provider: "claude"
  ai-model: "claude-opus-4-6[1m]"
  conventional-title:
    enabled: true
    mode: suggest  # Options: suggest (in check run) or fix (auto-update PR)
    timeout-minutes: 10
```

### Custom Sizing Labels

By default, the server calculates PR size based on total lines changed (additions + deletions). You can override these thresholds entirely for specific repositories, mapping custom sizes to UI colors.

```yaml
pr-size-thresholds:
  Tiny:
    threshold: 20
    color: lightgreen
  Average:
    threshold: 150
    color: green
  Massive:
    threshold: inf # 'inf' serves as the unbounded catch-all for large PRs
    color: black
```

For more configuration keys, check the [Configuration Reference](configuration-reference.html) and [Labels, Check Runs, and Mergeability](labels-check-runs-and-mergeability.html).

## Troubleshooting

### Settings not applying

If your pull request is not adhering to the rules you configured:
1. **Check the filename:** Ensure it is exactly `.github-webhook-server.yaml` and is placed in the repository root directory.
2. **Verify YAML syntax:** A syntax error will cause the server to skip the local file and fall back to global defaults.
3. **Check reserved keys:** If you attempted to override `github-tokens` or administrative settings, the server will ignore them when placed in `.github-webhook-server.yaml`.

### Testing behavior on feature branches

Changes to `.github-webhook-server.yaml` on a feature branch will apply only to the webhook events processed for *that specific pull request branch*. When merged, the settings become the baseline for all subsequent pull requests targeting the main branch.

## Related Pages

- [Configuration Model](configuration-model.html)
- [Configuration Reference](configuration-reference.html)
- [OWNERS and Reviewer Assignment](owners-and-reviewer-assignment.html)
