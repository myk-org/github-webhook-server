# Set Up Checks and Release Workflows

Checks are the pull request gates this server runs itself. It does not call GitHub Actions for tests: it clones the PR, runs commands in a worktree, and writes the results back as GitHub check runs. Release workflows are the same machinery, but triggered by a tag push — a container image push and a PyPI publish.

This page covers the two halves: what checks exist and how required-versus-advisory is decided, then how the container and PyPI release blocks work.

## Prerequisites

- A running webhook server with at least one repository configured. See [Start Automating a Repository](quick-start.html) and [Configure Repositories](configure-repositories.html).
- Python `3.14`, `uv`, and `podman` on the server host — the built-in checks shell out to `uvx` and `podman`.
- A container registry account if you want `build-container`; a PyPI API token if you want PyPI publishing.
- Manage-repositories app access: branch protection, labels, and security settings are written at startup with the highest-rate-limit token, not with the webhook's app token.

## Part 1 — Checks

### How a check run gets created and reported

Every check follows the same lifecycle, implemented in `webhook_server/libs/handlers/runner_handler.py`:

1. `set_check_queued` when the PR event is received (only for checks whose feature is enabled).
2. `set_check_in_progress` when the runner starts.
3. A git worktree is created from the shared clone and the base branch is merged into it.
4. The command runs, stdout/stderr are stripped of ANSI codes and redacted, then posted to the check run output.
5. `set_check_success` or `set_check_failure`.

All check runs are written to `last_commit.sha`. Output is truncated to 65534 characters to stay under GitHub's limit, keeping the head and the tail, and the full redacted output stays in the server log. Secrets from `pypi.token`, the container registry username and password, and the GitHub token are replaced with `*****` before anything is posted.

Built-in and custom command checks share one code path, `RunnerHandler.run_check()`. Each one is described by a small `CheckConfig` record: the check-run name, the command (which may contain the `{worktree_path}` placeholder), a display title, and whether the command runs with `cwd` set to the worktree.

The CI stage is driven by `PullRequestHandler._run_pull_request_ci_tasks()`. It always schedules tox, pre-commit, python-module-install, and build-container; each of those runners returns early if its feature is not configured. Conventional-title and the two security checks are scheduled only when their config is present, and every validated custom check is scheduled unconditionally.

### The built-in checks

Check-run names are constants in `webhook_server/utils/constants.py`, so what the GitHub UI shows is exactly these strings.

| Check run | Config key | Notes |
| --- | --- | --- |
| `tox` | `tox` | Runs `uvx tox --workdir {worktree_path} --root {worktree_path} -c {worktree_path}`. |
| `pre-commit` | `pre-commit` | Runs `uvx --directory {worktree_path} prek run --all-files`. |
| `build-container` | `container` | `podman build` of the repo Dockerfile. Also used for release pushes with no check run. |
| `python-module-install` | `pypi` | `uvx pip wheel --no-cache-dir -w {worktree_path}/dist {worktree_path}`. |
| `conventional-title` | `conventional-title` | Validates the PR title against Conventional Commits v1.0.0. |
| `can-be-merged` | always on | The rollup verdict. Never counted as a required check by the server. |
| `security-suspicious-paths` | `security-checks.suspicious-paths` | Fails when a changed file is under a suspicious path prefix. |
| `security-committer-identity` | `security-checks.committer-identity-check` | Fails when the last committer is not the PR author and not trusted. |
| `verified` | `verified-job` (default `true`) | Human-driven: a check run named after the `verified` label, set to success when the label is added. |

Notes per check:

- **`tox`** is a mapping from base branch name to either a string or a list of tox environments. Empty or `all` means every environment. `tox.args` is appended verbatim to the command; `tox.python-version` adds `--python=<version>` to the `uvx` invocation. `args` and `python-version` are nested keys of the `tox` block; `tox-python-version` still works as a deprecated top-level fallback. The selected value has its spaces stripped and is appended as a single `-e <value>` argument.

- **`pre-commit`** is a boolean. The schema documents `default: true`, but the server treats an unset `pre-commit` as **false** — set it explicitly to turn the check on. Note this check is required through branch protection (see below), not through the server's own required list.

- **`verified`** is not a command check. It is queued on PR events and set to success when the `verified` label is added, and back to queued when the label is removed. Because `verified-job` defaults to true, the string `verified` is in the server's required list unless you set `verified-job: false`.

- **`can-be-merged`** is the aggregate. `PullRequestHandler.check_if_can_be_merged()` sets it in progress, then accumulates failure reasons: GitHub mergeable state, required checks still in progress, required checks failed or not started, `wip`/`hold` labels, `can-be-merged-required-labels`, unresolved review threads when `branch-protection.required_conversation_resolution` is on, and approval/verification requirements. Empty failure output means success and the `can-be-merged` label is added. It is explicitly skipped when the server evaluates required checks, so it can never deadlock itself.

- **`security-suspicious-paths`** compares `owners_file_handler.changed_files` against the configured prefixes. Default prefixes (`DEFAULT_SUSPICIOUS_PATHS`): `.claude/`, `.vscode/`, `.cursor/`, `.devcontainer/`, `.pi/`, `.github/workflows/`, `.github/actions/`. An empty list disables the check entirely.

- **`security-committer-identity`** compares the PR author (parent committer) with the last commit's committer. An unresolvable committer always fails. A mismatch fails unless the committer is in the trusted list, which is `security-checks.trusted-committers` plus the GitHub App bot, `web-flow`, and API users. A `web-flow` login with the wrong immutable user ID fails as a suspected impersonation. Maintainers can clear a security failure with `/security-override`, which forces the check runs to success.

### How required versus advisory is decided

There are two separate lists, and they are not the same list.

**1. The runtime required list** — `CheckRunHandler.all_required_status_checks()`. This is what the server consults on every webhook to decide whether `can-be-merged` should fail. It is assembled, in order, from:

- branch protection required status check contexts read from the PR's base branch (`get_branch_required_status_checks()`),
- `tox` if configured,
- `verified` if `verified-job` is on,
- `build-container` if `container` is configured,
- `python-module-install` if `pypi` is configured,
- `conventional-title` if configured,
- every custom check whose `mandatory` is `true`,
- the security checks, when `security-checks.mandatory` is true (default): `security-suspicious-paths` if a suspicious-paths list is configured, and `security-committer-identity` if `committer-identity-check` is on.

The result is deduplicated while preserving order and cached for the lifetime of the handler instance. Note that `pre-commit`, `can-be-merged`, and non-mandatory custom checks are **not** in this list — they never block `can-be-merged` through the server's own logic. Blocking them is a GitHub branch-protection job, covered next.

Branch protection contexts are only read for public repositories; for private repositories `get_branch_required_status_checks()` returns an empty list and the server relies solely on its config-derived list.

**2. GitHub branch protection** — applied at server startup, not per webhook. `repository_and_webhook_settings()` runs on boot and calls `set_repositories_settings()`, which for each repository calls `set_repository()`, which for each branch under `protected-branches` calls `set_branch_protection()`. This is the only place branch protection is written. Nothing in the webhook path re-derives or re-applies it, so a change to `protected-branches` takes effect on the next server start, not on the next PR.

`set_repository()` also creates the static labels with their configured colors, enables `delete_branch_on_merge`, `allow_auto_merge`, and `allow_update_branch`, and — for public repositories only — enables secret scanning and secret scanning push protection. Private repositories skip branch protection and security settings entirely.

The required check list written to a branch is:

- the repository's `default-status-checks` list, always plus `can-be-merged`, taken from a deep copy so per-branch exclusions cannot mutate the shared list;
- then, depending on `protected-branches.<branch>`:

**When `include-runs` is non-empty**, it *is* the branch's required-check list. Nothing is derived from config — no `tox`, no `container`, no `default-status-checks`, no `pre-commit`. The only thing appended is the security checks, again gated on `security-checks.mandatory`, deduplicated while keeping `include-runs` order. `exclude-runs` is then subtracted from the assembled list, so the two are a filter pair on this path too.

**When `include-runs` is empty or absent**, the list is derived by `get_required_status_checks()`: `tox` if configured, `verified` if `verified-job` is not false, `build-container` if `container` is configured, `python-module-install` if `pypi` is configured, `pre-commit` if `pre-commit` is true, `conventional-title` if configured, and `pre-commit.ci - pr` if `.pre-commit-config.yaml` exists in the repository. Then the security checks are appended under the same `mandatory` gate, the result is deduplicated, and finally `exclude-runs` entries are removed.

`exclude-runs` applies to both paths: it filters the automatically derived list *and* the explicit `include-runs` list, and it wins — an explicit `exclude-runs` entry is honoured for any check, including the appended security checks. Removing a security check this way is therefore possible, but if you do not want the security checks required at all, the switch is `security-checks.mandatory: false`.

Branch protection rules themselves come from `branch-protection` (global or per-repository), merged over `DEFAULT_BRANCH_PROTECTION`:

```yaml
strict: true
require_code_owner_reviews: false
dismiss_stale_reviews: true
required_approving_review_count: 0
required_linear_history: true
required_conversation_resolution: true
```

The API user that writes the settings is added to the users, teams, and apps bypass lists so the server can still operate on protected branches.

`protected-branches` accepts either a plain list or a mapping:

```yaml
repositories:
  your-repo:
    name: your-org/your-repo
    default-status-checks:
      - lint
    protected-branches:
      # Derived from config, minus `build-container`
      main:
        exclude-runs:
          - build-container
      # Explicit list — only security checks are added
      release/2.0:
        include-runs:
          - can-be-merged
          - tox
          - verified
```

A branch value given as a bare list is accepted by the schema, but it carries no `include-runs`/`exclude-runs`, so it takes the derived path.

### Custom check runs

`custom-check-runs` adds your own commands to the same lifecycle as the built-ins. `GithubWebhook._validate_custom_check_runs()` filters the list at load time; invalid entries are dropped with a warning, and only validated checks run.

```yaml
repositories:
  your-repo:
    name: your-org/your-repo
    custom-check-runs:
      - name: lint
        command: uv tool run --from ruff ruff check
        mandatory: true
      - name: security-scan
        command: BANDIT_CONFIG=ci uv tool run --from bandit bandit -r .
        mandatory: false
      - name: unit
        command: |
          uv run pytest tests -q
```

Validation rules, all enforced at startup:

- `name` is required and must be a string.
- `name` must match `^[a-zA-Z0-9._-]{1,64}$`.
- `name` must **not** be in `BUILTIN_CHECK_NAMES` — a collision is logged and the entry skipped. `BUILTIN_CHECK_NAMES` is exactly `tox`, `pre-commit`, `build-container`, `python-module-install`, `conventional-title`, `can-be-merged`, `security-suspicious-paths`, `security-committer-identity`. (`verified` is a check run but is not in that set, so a custom check may take that name.)
- `name` must be unique within the repository's list.
- `command` is required, must be a non-empty string after stripping, and must parse with `shlex`.
- The first token that is not a `VAR=value` environment assignment must resolve with `shutil.which()` on the server. If the executable is not installed, the check is skipped with a warning rather than failing later at run time.

Custom commands run through `/bin/sh -c` with the worktree as the working directory, so pipes, subshells, and inline environment variables work. `{worktree_path}` is not needed for custom checks, though it is substituted if present. The check run title is `Custom Check: <name>`.

`mandatory` defaults to `true`. A mandatory custom check is added to `all_required_status_checks()` and therefore blocks `can-be-merged`; a non-mandatory one still runs and is still retestable, it just never blocks.

### Retesting

`/retest <check>` re-runs a single check, and `/retest all` re-runs everything available. The allowed names come from `GithubWebhook._current_pull_request_supported_retest`: `tox` (if configured), `build-container` (if configured), `python-module-install` (if configured), `pre-commit` (if configured), `conventional-title` (if configured), every custom check name — mandatory or not — and both security checks when they are enabled. `can-be-merged` and `verified` are not retestable. An unknown name is logged and skipped. The welcome comment lists the supported retests for the repository.

On startup, `set_all_in_progress_check_runs_to_queued()` walks open pull requests and resets any check run in `BUILTIN_CHECK_NAMES` that is stuck in `in_progress` back to `queued`, so a server restart during a run does not leave a gate hanging. Custom checks and `verified` are not covered by that sweep.

### Related repository keys

- `set-auto-merge-prs` — list of base branches where GitHub auto-merge is enabled. Auto-merge is blocked when the PR touches a suspicious path, and an already-enabled auto-merge is disabled; a maintainer `/security-override` re-enables it. Cherry-picks whose conflicts were resolved by AI are never auto-merged.
- `create-issue-for-new-pr` — boolean, default `true`. Creates a tracking issue per PR. Set it globally and per repository; the repository value wins.
- `cherry-pick-assign-to-pr-author` — boolean, default `true`. Assigns cherry-pick PRs to the author of the original PR. Also global-with-repository-override.

## Part 2 — Release workflows

Both release paths run on a **tag push**. `PushHandler.process_push_webhook_data()` matches `refs/tags/(.+)`; if the ref is a branch, nothing release-related happens. When it is a tag, PyPI publishing runs first, then the container release.

### Container builds and pushes

Enable the `container` block. A check run named `build-container` appears on every PR.

```yaml
repositories:
  your-repo:
    name: your-org/your-repo
    container:
      username: your-registry-user
      password: your-registry-token
      repository: registry.example.com/your-org/your-repo
      dockerfile: Dockerfile
      context: ""
      tag: latest
      release: true
      build-args:
        - PYTHON_VERSION=3.14
      args:
        - --log-level=debug
      oci-annotations:
        enabled: true
        static:
          org.opencontainers.image.vendor: your-org
        auto:
          created: true
          source: true
          revision: true
          version: true
          title: true
```

Keys, exactly as the server reads them:

| Key | Default | Effect |
| --- | --- | --- |
| `username`, `password` | required | Registry credentials; used for `podman push --creds` and redacted from output. |
| `repository` | required | Image repository, without a tag. |
| `dockerfile` | `Dockerfile` | Dockerfile path relative to the worktree. |
| `context` | `""` (repo root) | Build context subdirectory. Schema restricts it to `[a-zA-Z0-9._/-]*`. |
| `tag` | `latest` | Tag for post-merge builds on the main branches. |
| `release` | `false` | Build and push an image when a tag is pushed. |
| `build-args` | `[]` | Each entry becomes a `--build-arg` flag. |
| `args` | `[]` | Extra flags prepended to the `podman build` command. |
| `oci-annotations` | disabled | See below. |

`build-args` and `args` also accept a single string, which is split with `shlex`; anything that is neither a string nor a list is ignored. `username`, `password`, and `repository` are read as required keys — a `container` block missing any of them raises on load, so it is all-or-nothing. `dockerfile` is read by the server even though the schema does not list it, so set it there and it will be honoured.

Tag selection, from `container_repository_and_tag()`:

- On a PR: `pr-<number>`.
- After merge: the base branch name, unless the base branch is `main` or `master`, in which case the configured `tag` (`latest` by default).
- On a release tag push: the tag name, as pushed.

The build context is resolved with `os.path.realpath` and rejected with a failed check run if it escapes the worktree — `context` cannot be used to reach files outside the repository. Merged and release builds add `--no-cache`; PR builds do not. `podman build` runs with `--network=host`. A known podman reboot-cache bug is detected and retried after clearing the stale storage directory.

Pushing happens only for a successful build, using `podman push --creds <user>:<password> <repo>:<tag>`. On success the PR gets a comment and, if `slack-webhook-url` is set, a Slack message; on failure a comment says the push failed. The `/build-and-push-container` comment command triggers the same path on demand and is gated on the commenter's permission to run commands.

#### OCI annotations

`oci-annotations.enabled` defaults to `false`; nothing is added when it is off. When on, each annotation becomes a `--annotation key=value` flag on `podman build`. The `auto` block populates annotations from webhook context, and every auto entry defaults to `true` when `oci-annotations` is enabled:

- `org.opencontainers.image.created` — build timestamp, UTC, `YYYY-MM-DDTHH:MM:SSZ`
- `org.opencontainers.image.source` — `https://github.com/<repo>`
- `org.opencontainers.image.revision` — PR head SHA, or the pushed commit for a tag build
- `org.opencontainers.image.version` — the image tag, only when there is one
- `org.opencontainers.image.title` — the repository name

`static` entries are free-form key-value pairs; use reverse-domain notation. Static annotations are applied last and override auto-populated ones with the same key.

### PyPI publishing

The `pypi` block is both a PR check and a release job.

```yaml
repositories:
  your-repo:
    name: your-org/your-repo
    pypi:
      token: pypi-AgEIcHlwaS5vcmc...
```

On pull requests, the `python-module-install` check runs `uvx pip wheel --no-cache-dir -w {worktree_path}/dist {worktree_path}` — the package must build and its dependencies must resolve.

On a tag push, `PushHandler.upload_to_pypi()`:

1. Checks out the tag into a worktree.
2. Runs `uv build --sdist --out-dir <worktree>/pypi-dist`.
3. Lists the dist directory and takes the resulting `.tar.gz` filename.
4. Runs `twine check` on the sdist.
5. Runs `twine upload --username __token__ --password <token> <sdist> --skip-existing`.

The token is redacted from all captured output. Any failure — checkout, build, listing, check, or upload — opens a GitHub issue titled with the first line of the error, body `Publish to PYPI failed: ...`, and stops the tag handling (the container release is skipped for that push). On success a Slack message is sent when `slack-webhook-url` is set. `--skip-existing` means re-pushing an existing version succeeds without republishing.

## Verify it worked

After restarting the server with the new configuration:

- The startup log lists each repository's branches and the checks it enabled, for example `Set branch main setting for your-org/your-repo. enabled checks: ['can-be-merged', 'tox', 'pre-commit', 'verified']`. Compare that against what you expect.
- `Loaded N custom check(s): [...]` and `Skipped N invalid custom check(s)` appear if you configured custom checks; resolve any skipped names.
- In GitHub, open the protected branch's settings and confirm the required status checks match your `include-runs`/`exclude-runs` intent.
- Open a test PR and confirm the expected check runs appear as queued and then complete, and that `can-be-merged` fails with a readable reason if something is still running.

## Related pages

- [Configuration Reference](configuration-reference.html) — every key in `config.yaml` and repository-local overrides
- [Configure Repositories](configure-repositories.html) — rollout patterns across repositories
- [Run Pull Request Commands](run-pull-request-commands.html) — `/retest`, `/security-override`, `/build-and-push-container`
- [Manage Pull Requests](manage-pull-requests.html) — labels, reviewers, and the merge flow
- [Secure Webhooks and Pull Requests](secure-webhooks-and-pull-requests.html) — the security checks in depth
