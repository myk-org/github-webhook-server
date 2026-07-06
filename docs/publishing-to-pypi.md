Let me explore the repository to understand the PyPI publishing functionality.

Now let me check the events configuration to understand the push event requirement:

Now let me check the test for push handler to understand failure scenarios:

Now let me check how events are configured to make sure `push` is needed:

Now I have a comprehensive understanding. Let me also check how the `.github-webhook-server.yaml` is loaded for per-repo config:

Now I have all the information I need. Let me write the documentation page.

# Publishing Packages to PyPI

Automatically publish your Python package to PyPI every time you push a Git tag, with built-in failure alerts and optional Slack notifications.

## Prerequisites

- A [PyPI API token](https://pypi.org/help/#apitoken) scoped to your project
- Your repository configured in the webhook server (see [Configuring Repositories](configuring-repositories.html))
- The `push` event enabled in your repository's events list
- Your project must be buildable with `uv build --sdist`

## Quick Example

Add the `pypi` section to your repository configuration:

```yaml
# In config.yaml, under your repository
repositories:
  my-org/my-python-package:
    pypi:
      token: pypi-AgEIcH...your-token-here
    events:
      - push
      - pull_request
      - pull_request_review
      - issue_comment
      - check_run
      - status
```

Now push a tag:

```bash
git tag v1.2.0
git push origin v1.2.0
```

The webhook server builds your package and uploads it to PyPI automatically.

## Step-by-Step Setup

### 1. Generate a PyPI API Token

1. Log in to [pypi.org](https://pypi.org) and go to **Account Settings → API Tokens**.
2. Create a token scoped to your specific project (recommended) or account-wide.
3. Copy the token — it starts with `pypi-`.

### 2. Add the Token to Your Configuration

You can configure PyPI publishing in either location:

| Config file | Scope |
|---|---|
| `config.yaml` | Centralized — managed by the server admin |
| `.github-webhook-server.yaml` (in repo root) | Per-repository — managed by repo maintainers |

**Option A: Central config (`config.yaml`)**

```yaml
repositories:
  my-org/my-python-package:
    pypi:
      token: pypi-AgEIcH...your-token-here
```

**Option B: Per-repo config (`.github-webhook-server.yaml`)**

```yaml
pypi:
  token: pypi-AgEIcH...your-token-here
```

> **Tip:** Per-repo config in `.github-webhook-server.yaml` overrides the central `config.yaml` values, so teams can manage their own PyPI tokens independently.


> **Warning:** PyPI tokens are secrets. Use environment variable substitution or a secret manager to avoid committing tokens in plain text. The schema marks the token field with `format: password`, and the server redacts it from logs when `mask-sensitive-data` is enabled (the default).

### 3. Include the `push` Event

Make sure `push` is in your repository's event list. If you omit the `events` key entirely, all events are listened to by default. If you specify events explicitly, include `push`:

```yaml
events:
  - push
  - pull_request
  - pull_request_review
  - issue_comment
  - check_run
  - status
```

### 4. Push a Git Tag to Trigger Publishing

The webhook server only processes tag pushes — regular branch pushes are skipped. Any tag format works:

```bash
# Semantic version tags
git tag v1.0.0
git push origin v1.0.0

# Tags with slashes
git tag release/v2.0.0
git push origin release/v2.0.0
```

> **Note:** Branch/tag deletions are automatically ignored — deleting a tag does not trigger a publish.

## What Happens During Publishing

When a tag push is received, the server runs these steps in order:

1. **Checkout** — The tagged commit is checked out into a temporary worktree
2. **Build** — Runs `uv build --sdist` to create a source distribution
3. **Validate** — Runs `twine check` to verify the package metadata
4. **Upload** — Runs `twine upload` with the `--skip-existing` flag to publish to PyPI

If any step fails, the process stops immediately and a GitHub issue is created in the repository (see [Failure Handling](#failure-handling) below).

> **Note:** The `--skip-existing` flag means re-pushing an already-published tag version will not cause an error — the upload is silently skipped.

## Slack Notifications on Success

When a package is successfully published and a Slack webhook URL is configured for the repository, a notification is sent:

```
my-org/my-python-package Version v1.2.0 published to PYPI.
```

See [Setting Up Slack Notifications](setting-up-notifications.html) for how to configure `slack-webhook-url`.

## The `python-module-install` PR Check

When `pypi` is configured, the webhook server automatically adds a **`python-module-install`** check run to every pull request. This check:

- Runs `pip wheel --no-cache-dir` against the PR branch to verify the package builds correctly
- Reports success or failure as a GitHub check run on the PR
- Is automatically added to required status checks for branch protection

This catches packaging errors (missing files, broken `pyproject.toml`, import issues) **before** a release tag is pushed, so you won't discover build failures at publish time.

You can re-run this check on a PR with:

```
/retest python-module-install
```

See [Setting Up CI Checks](setting-up-ci-checks.html) for more details on CI check configuration.

## Failure Handling

If any step in the publish process fails, the webhook server automatically creates a **GitHub issue** in the repository with:

- **Title**: A sanitized summary of the error (truncated to 250 characters)
- **Body**: The full error message from the failing command

This ensures publish failures are visible to the team even if no one is watching the server logs.

Common failure scenarios that trigger issue creation:

| Failure | Cause |
|---|---|
| Checkout failure | Tag doesn't exist or repo can't be cloned |
| Build failure | `uv build --sdist` fails (e.g., missing `pyproject.toml`) |
| Twine check failure | Package metadata is invalid |
| Upload failure | Invalid token, network error, or PyPI API issue |

## Advanced Usage

### Combining PyPI Publishing with Container Builds

PyPI publishing and container builds can both trigger on the same tag push. If both `pypi` and `container` (with `release: true`) are configured, the server runs PyPI upload first, then builds and pushes the container image. A PyPI upload failure stops processing — the container build will not run.

```yaml
repositories:
  my-org/my-python-package:
    pypi:
      token: pypi-AgEIcH...your-token-here
    container:
      repository: quay.io/my-org/my-package
      username: my-user
      password: my-password
      release: true
```

### Token Security

The PyPI token is redacted from all command logs. The server passes it as a `--password` argument to `twine upload` and registers it as a secret to redact, so even if a command fails and the error output is logged, the token value is masked.

To verify sensitive data masking is active for your repository:

```yaml
# In config.yaml (global) or .github-webhook-server.yaml (per-repo)
mask-sensitive-data: true  # This is the default
```

> **Warning:** Setting `mask-sensitive-data: false` disables log redaction for that repository. Only use this temporarily for debugging, and never in production.

## Troubleshooting

**Package uploads but the wrong version is published**
The server checks out the exact commit pointed to by the tag. Make sure your `pyproject.toml` version matches the tag. Consider using a tool like `release-it` or `setuptools-scm` to keep versions in sync.

**Issue created with "twine upload failed" error**
Verify your PyPI token is valid and has upload permissions for the project. Account-scoped tokens work for any project; project-scoped tokens only work for the specified project.

**`python-module-install` check fails on PRs but local builds work**
The check runs `pip wheel` in an isolated environment from the PR branch merged with the base branch. Ensure all build dependencies are declared in `pyproject.toml` (not just installed locally).

**No publish happens when I push a tag**
- Confirm `push` is in your `events` list (or that `events` is not set, which subscribes to all events)
- Confirm `pypi.token` is set in your config
- Check the server logs for the tag push event

## Related Pages

- [Setting Up CI Checks](setting-up-ci-checks.html)
- [Configuring Repositories](configuring-repositories.html)
- [Setting Up Slack Notifications](setting-up-notifications.html)
- [Configuration Reference](configuration-reference.html)
- [Configuration Recipes](config-recipes.html)
