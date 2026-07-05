Let me explore the repository to understand the CI checks configuration.

Now let me look at the custom check runs feature more closely and see how the pull request handler integrates with the runner:

Now let me check the constants file to understand the check run names:

Now let me check the `.github-webhook-server.yaml` per-repo file capability:

Now I have enough information. Let me compile the documentation page.

# Setting Up CI Checks

Automate code quality and compliance checks on every pull request by configuring tox tests, pre-commit hooks, container builds, Python module installs, conventional commit validation, and custom check runs in your webhook server configuration.

## Prerequisites

- A running github-webhook-server instance (see [Getting Started](quickstart.html))
- At least one repository configured in `config.yaml` (see [Configuring Repositories](configuring-repositories.html))
- For container builds: `podman` installed on the server and registry credentials configured

## Quick Example

Add CI checks to a repository by editing the `repositories` section in `config.yaml`:

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    tox:
      main: all
    pre-commit: true
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
```

With this config, every PR targeting `main` will automatically run tox tests, pre-commit hooks, and conventional commit title validation. Results appear as GitHub check runs on the PR.

## Configuring Tox

Tox runs your project's test suite using `uvx tox`. Configure it per branch:

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    tox:
      main: all                      # Run all tox envs for PRs targeting main
      dev: testenv1,testenv2         # Run specific envs for PRs targeting dev
      args: "-p -v"                  # Extra CLI arguments (optional)
      python-version: "3.11"         # Python version for tox (optional)
```

- Set a branch name to `all` to run all environments defined in `tox.ini`.
- Specify a comma-separated list to run only those environments (e.g., `testenv1,testenv2`).
- The `args` key passes extra CLI arguments directly to tox (e.g., `"-p -v"` for parallel verbose runs).

> **Note:** The `tox-python-version` key at the repository level is deprecated. Use `python-version` nested under `tox` instead.

## Configuring Pre-commit

Enable pre-commit to run all hooks defined in your repository's `.pre-commit-config.yaml`:

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    pre-commit: true
```

When enabled, the server runs `pre-commit run --all-files` in the PR worktree. A `.pre-commit-config.yaml` file must exist in the repository root.

## Configuring Container Builds

Build and optionally push container images on every PR:

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    container:
      username: myuser
      password: my-registry-password
      repository: quay.io/myorg/myapp
      tag: latest
      release: true                  # Push on new release with release tag
      build-args:
        - my-build-arg1=value1
        - my-build-arg2=value2
      args:                          # Additional podman build arguments
        - --format docker
      context: src                   # Subdirectory as build context (default: repo root)
```

- During a PR, the server builds the container but does **not** push it. The check run reports build success or failure.
- On merge or release (when `release: true`), the image is built and pushed to the registry.
- Use the `/build-and-push-container` comment command to manually trigger a build and push. See [Managing Pull Requests](managing-pull-requests.html) for details.

> **Warning:** Container credentials are stored in `config.yaml`. Protect this file and consider using a secrets manager. Never commit credentials to version control.

## Configuring Python Module Installs

If your repository publishes to PyPI, enable the Python module install check to verify your package builds correctly:

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    pypi:
      token: pypi-your-token-here
```

When a `pypi` configuration is present, the server runs `pip wheel` against the PR worktree to validate the package builds. The PyPI token is used for publishing on release — the install check itself does not upload anything.

## Configuring Conventional Commit Validation

Enforce [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) formatting on PR titles:

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
```

This validates that every PR title follows the format `<type>[optional scope]: <description>`.

**Common configurations:**

| Config value | Behavior |
|---|---|
| `"feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"` | Allow only standard types |
| `"feat,fix,hotfix,release"` | Allow standard + custom types |
| `"*"` | Accept any type while enforcing the overall format |

Valid PR title examples:
- `feat: add user authentication`
- `fix(api): handle edge case`
- `feat!: breaking change`
- `docs: update installation guide`

> **Tip:** When combined with AI features, the server can suggest or auto-fix invalid titles. See [Enabling AI Features](enabling-ai-features.html) for setup instructions.

## Configuring Custom Check Runs

Define your own checks that run arbitrary commands on every PR:

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    custom-check-runs:
      - name: lint
        command: uv tool run --from ruff ruff check
        mandatory: true

      - name: security-scan
        command: uv tool run --from bandit bandit -r .
        mandatory: false

      - name: complex-check
        command: |
          uv run python -c "
          import sys
          print('Running complex check')
          sys.exit(0)
          "
```

Each custom check requires:

- **`name`** — Unique name displayed in the GitHub check run UI. Must contain only alphanumeric characters, dots, underscores, or hyphens (max 64 characters).
- **`command`** — Shell command to execute. Runs in the repository worktree directory. Environment variables can be included inline (e.g., `TOKEN=xyz command args`).
- **`mandatory`** (optional, default: `true`) — When `true`, the check must pass for the PR to be marked `can-be-merged`. Set to `false` for advisory checks.

**Validation rules:**

- The command's executable must be available on the server. If not found, the check is silently skipped with a log warning.
- Custom check names cannot collide with built-in check names (`tox`, `pre-commit`, `build-container`, `python-module-install`, `conventional-title`, `can-be-merged`, `security-suspicious-paths`, `security-committer-identity`).
- Duplicate names are rejected — only the first occurrence is used.

## Retesting Failed Checks

When a check fails, you can re-run it from a PR comment:

```
/retest tox
/retest pre-commit
/retest build-container
/retest python-module-install
/retest conventional-title
/retest lint
/retest all
```

The `/retest <name>` command works for all built-in and custom checks. Use `/retest all` to re-run every configured check. See [PR Comment Command Recipes](pr-command-recipes.html) for more examples.

## How Checks Affect Mergeability

The `can-be-merged` label and check run are determined by whether all **mandatory** checks pass:

| Check type | Mandatory by default? |
|---|---|
| tox | Yes |
| pre-commit | No (runs but doesn't block) |
| build-container | Yes |
| python-module-install | Yes |
| conventional-title | Yes |
| Custom check (`mandatory: true`) | Yes |
| Custom check (`mandatory: false`) | No |

You can also add external status checks to the required list using `default-status-checks`:

```yaml
# Global level
default-status-checks:
  - "WIP"
  - "can-be-merged"
  - "ci/my-external-check"

# Or per repository
repositories:
  my-repository:
    name: my-org/my-repository
    default-status-checks:
      - "WIP"
      - "can-be-merged"
      - "ci/my-external-check"
```

## Advanced Usage

### Per-Repo Configuration via `.github-webhook-server.yaml`

Instead of editing the server's `config.yaml`, repository maintainers can add a `.github-webhook-server.yaml` file to the repository root. Settings in this file override the corresponding values in `config.yaml`.

```yaml
# .github-webhook-server.yaml (in repository root)
tox:
  main: all
  args: "--parallel"
  python-version: "3.12"
pre-commit: true
conventional-title: "feat,fix,docs"
custom-check-runs:
  - name: typecheck
    command: uv tool run --from mypy mypy src/
```

See [Configuring Repositories](configuring-repositories.html) for the full precedence rules.

### Container Build Context and OCI Annotations

For monorepos or projects where the Dockerfile is not at the root, set a subdirectory as the build context:

```yaml
container:
  username: myuser
  password: my-registry-password
  repository: quay.io/myorg/myapp
  tag: latest
  context: src    # Build from <repo>/src/ instead of repo root
```

> **Note:** The `context` value must be a relative path within the repository. It cannot escape the repository root — attempts to traverse above it are rejected for security.

Add OCI-standard metadata annotations to built images:

```yaml
container:
  username: myuser
  password: my-registry-password
  repository: quay.io/myorg/myapp
  tag: latest
  oci-annotations:
    enabled: true
    static:
      org.opencontainers.image.vendor: "My Organization"
      org.opencontainers.image.licenses: "Apache-2.0"
    auto:
      created: true     # Build timestamp
      source: true      # Repository URL
      revision: true    # Commit SHA
      version: true     # Tag on release builds
      title: true       # Repository name
```

### Tox with Branch-Specific Test Environments

Run different test environments depending on the PR's target branch:

```yaml
tox:
  main: all                          # Full suite for main
  dev: unit,integration              # Only unit + integration for dev
  release-1.0: unit                  # Minimal tests for release branch
  args: "--parallel"                 # Shared across all branches
  python-version: "3.12"
```

### Combining Multiple Checks

All checks run concurrently. A typical full configuration looks like:

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    tox:
      main: all
      python-version: "3.12"
    pre-commit: true
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
    pypi:
      token: pypi-your-token
    container:
      username: myuser
      password: my-registry-password
      repository: quay.io/myorg/myapp
      tag: latest
    custom-check-runs:
      - name: lint
        command: uv tool run --from ruff ruff check
      - name: type-check
        command: uv tool run --from mypy mypy src/
      - name: security-audit
        command: uv tool run --from bandit bandit -r .
        mandatory: false
```

## Troubleshooting

**Check shows "queued" but never starts**
- Verify the server has network access to clone the repository. Check the server logs for clone or worktree errors.

**Custom check skipped with no error**
- The command executable must exist on the server. Check server logs for a warning like `executable 'xxx' not found on server`. Install the missing tool or use `uvx`/`uv tool run` to run it without pre-installing.

**Tox tests pass locally but fail on the server**
- Confirm the `python-version` in your tox config matches what's available on the server. The server uses `uvx tox` to run tests.

**Container build fails with "current system boot ID differs"**
- This is a known podman issue after server restarts. The server automatically retries the build after clearing the podman cache. If it persists, restart the podman service.

**Conventional title check fails unexpectedly**
- Ensure the PR title exactly follows `<type>[optional scope]: <description>`. A common mistake is missing the space after the colon. Check that the type is in your configured list.

## Related Pages

- [Configuring Repositories](configuring-repositories.html)
- [Managing Pull Requests](managing-pull-requests.html)
- [Configuration Reference](configuration-reference.html)
- [PR Comment Command Recipes](pr-command-recipes.html)
- [Enabling AI Features](enabling-ai-features.html)
