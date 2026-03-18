# Repository Overrides

Repository overrides let you change behavior for one repository without changing the default behavior for every repo the server manages.

This project has two per-repository configuration layers:

1. `config.yaml` on the server, under `repositories.<repo>`
2. `.github-webhook-server.yaml` in the root of the repository itself

For runtime settings that the webhook reads from the repository, precedence is:

1. `.github-webhook-server.yaml`
2. The matching repository entry in `config.yaml`
3. Top-level defaults in `config.yaml`

> **Note:** The server looks for `.github-webhook-server.yaml` in the repository root. If that file is missing, the repository falls back to its `config.yaml` entry, then to global defaults.

> **Tip:** Keep repo-owned workflow behavior in `.github-webhook-server.yaml`, and keep server-owned settings such as credentials, webhook events, and protected branch setup in `config.yaml`.

| Area | `.github-webhook-server.yaml` | `config.yaml` `repositories.<repo>` | Notes |
| --- | --- | --- | --- |
| `labels.enabled-labels`, `labels.colors` | Yes | Yes | Repo-local values win. |
| `pr-size-thresholds` | No | Yes | Current runtime reads PR size buckets from `config.yaml`. |
| `tox`, `tox-python-version`, `pre-commit`, `verified-job`, `conventional-title`, `custom-check-runs`, `set-auto-merge-prs`, `can-be-merged-required-labels`, `minimum-lgtm` | Yes | Yes | Repo-local values win. |
| `pypi`, `container` | Yes | Yes | Repo-local values win. |
| `github-tokens` | No | Yes | Needed before the repo-local file can be read. |
| `protected-branches` and branch protection sync | No | Yes | Applied by the server when it configures repositories. |
| `branch-protection.required_conversation_resolution` | Yes | Yes | Also affects the runtime `can-be-merged` gate. |
| `events`, `test-oracle`, `allow-commands-on-draft-prs` | No | Yes | Current code reads these from `config.yaml`. |

## Labels

Per-repo label overrides are useful when one repository wants fewer automation labels, different colors, or different PR size buckets than the global defaults.

The repo-specific label shape in `config.yaml` looks like this:

```236:255:examples/config.yaml
labels:
  enabled-labels:
    - verified
    - hold
    - size
  colors:
    hold: purple

pr-size-thresholds:
  Express:
    threshold: 25 # PRs with 0-24 lines changed
    color: lightblue
  Standard:
    threshold: 100 # PRs with 25-99 lines changed
    color: green
  Premium:
    threshold: 500 # PRs with 100-499 lines changed
    color: orange # PRs with 500+ lines changed get this category
```

`labels.enabled-labels` is a whitelist.

- Leave it unset to keep all configurable label categories enabled.
- Set it to `[]` to disable configurable label categories for that repo.
- Review labels such as `approved-*`, `lgtm-*`, `changes-requested-*`, and `commented-*` still stay enabled.
- The exact `lgtm` and `approve` labels also stay enabled.

`labels.colors` overrides only the keys you provide, so you can inherit global colors and replace just a few. The code also supports dynamic prefixes such as `approved-` and `branch-`, not just exact label names.

> **Note:** `pr-size-thresholds` is per-repository, but in the current code it belongs in `config.yaml`, not `.github-webhook-server.yaml`.

## Checks And Merge Rules

This is the area where repo-local overrides are most useful. You can change what checks run, how strict the merge gate is, and whether auto-merge is enabled for selected branches.

The repo-local example shows branch-specific `tox`, an optional Python version for `tox`, and `pre-commit`:

```13:41:examples/.github-webhook-server.yaml
verified-job: true # Enable/disable verified job functionality

# ... other repo-local settings ...

tox:
  main: "tests,linting" # Commands for main branch
  develop: "tests" # Commands for develop branch
  feature/*: ["tests", "quick-lint"] # Array format also supported

tox-python-version: "3.11"

pre-commit: true
```

The same repo-local file also shows merge-gate controls:

```87:124:examples/.github-webhook-server.yaml
set-auto-merge-prs:
  - main
  - develop

can-be-merged-required-labels:
  - "approved"
  - "tests-passed"
  - "security-reviewed"

conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"

minimum-lgtm: 2

create-issue-for-new-pr: true # Create tracking issues for new PRs
```

These keys change behavior in practical ways:

- `verified-job` enables the `verified` check flow for that repository.
- `tox` maps base branches to the `tox` envs that should run.
- `tox-python-version` chooses the Python version passed to `tox`.
- `pre-commit` enables the `pre-commit` check.
- `conventional-title` validates PR titles against the configured Conventional Commit types.
- `can-be-merged-required-labels` adds extra labels that must be present before `can-be-merged` passes.
- `minimum-lgtm` raises the LGTM threshold before a PR is considered approved.
- `set-auto-merge-prs` enables GitHub auto-merge automatically when the PR targets one of those base branches.
- `create-issue-for-new-pr` controls automatic tracking issue creation per repository.

Custom checks are configured as `custom-check-runs`:

```580:613:webhook_server/config/schema.yaml
custom-check-runs:
  # Examples from the schema:
  - name: lint
    command: uv tool run --from ruff ruff check
    mandatory: true
  - name: security-scan
    command: TOKEN=xyz DEBUG=true uv tool run --from bandit bandit -r .
    mandatory: false
  - name: complex-check
    command: |
      uv run python -c "
      import sys
      print('Running complex check')
      sys.exit(0)
      "
```

`custom-check-runs` behave like built-in checks, with two especially useful details:

- The check name is used exactly as configured.
- `mandatory: false` means the check still runs, but it does not block `can-be-merged`.

> **Note:** Custom checks run in the repository worktree and support shell syntax, including environment variable prefixes.

> **Warning:** Custom checks are validated when they are loaded. The check name cannot collide with built-in checks such as `tox`, `pre-commit`, `build-container`, `python-module-install`, `conventional-title`, or `can-be-merged`, and the executable in `command` must already exist on the server. Invalid checks are skipped.

One more merge-related setting is worth calling out: `branch-protection.required_conversation_resolution`. The runtime merge gate reads that flag and, when enabled, unresolved review threads will cause `can-be-merged` to fail.

Other supported repo-local workflow overrides include `auto-verified-and-merged-users`, `auto-verify-cherry-picked-prs`, `cherry-pick-assign-to-pr-author`, `slack-webhook-url`, and `ai-features`.

## Tokens And Protected Branches

Some settings are still per-repository, but they remain server-side because the server needs them before it can read `.github-webhook-server.yaml`.

The repo-specific server config supports branch protection setup, per-repo tokens, and branch protection policy:

```164:227:examples/config.yaml
protected-branches:
  dev: []
  main: # set [] in order to set all defaults run included
    include-runs:
      - "pre-commit.ci - pr"
      - "WIP"
    exclude-runs:
      - "SonarCloud Code Analysis"

# ... other repo-specific settings ...

github-tokens: # override GitHub tokens per repository
  - <GITHUB TOKEN1>
  - <GITHUB TOKEN2>

# ... other repo-specific settings ...

branch-protection:
  strict: True
  require_code_owner_reviews: True
  dismiss_stale_reviews: False
  required_approving_review_count: 1
  required_linear_history: True
  required_conversation_resolution: True
```

Use `github-tokens` when one repository needs its own API budget or a different permission set than the global default. The server will build GitHub clients from that repo's token list and select the token with the highest remaining rate limit.

> **Warning:** Put `github-tokens` in `config.yaml`, not in `.github-webhook-server.yaml`. Token selection happens before the server reads the repo-local file.

`protected-branches` controls which branches get protection and what required checks are applied.

- `[]` means "protect this branch with the computed default required checks".
- `include-runs` gives an explicit required-check list for that branch.
- `exclude-runs` removes checks from the computed default list.

> **Note:** If `include-runs` is present, the server uses that explicit list. If it is not present, the server builds the list from `default-status-checks`, enabled repo features such as `tox` and `pre-commit`, and `can-be-merged`, then removes anything listed in `exclude-runs`.

`branch-protection` controls the actual GitHub branch protection settings for that repository. Per-repo values override global values field by field, so you can change only `strict`, only `required_approving_review_count`, or only `required_conversation_resolution` without redefining everything.

## Release Settings

Release behavior is also override-friendly. A repository can decide whether tag pushes should publish packages, push container images, or both.

The repo-local example shows both `pypi` and `container`:

```16:63:examples/.github-webhook-server.yaml
pypi:
  token: pypi-your-token-here

# ... other repo-local settings ...

container:
  username: your-registry-username
  password: your-registry-password # pragma: allowlist secret
  repository: quay.io/your-org/your-repo
  tag: latest
  release: true # Push on new releases
  build-args:
    - "BUILD_ARG=value"
  args:
    - "--platform=linux/amd64"
```

These keys affect release flow like this:

- If `pypi` is configured, a tag push triggers a build and upload to PyPI.
- If `container` is configured, the repo gets container build behavior.
- If `container.release` is `true`, tag pushes also push the built image to the configured registry.
- `container.build-args` and `container.args` are passed through to the container build command.

> **Tip:** On tag pushes, the published container image uses the Git tag name. For PR builds, the image tag is `pr-<number>`. For merged builds to `main` or `master`, the server uses `container.tag`.

## Recommended Split

A good working pattern is:

- Keep `github-tokens`, `protected-branches`, `branch-protection`, `events`, `pr-size-thresholds`, `test-oracle`, and `allow-commands-on-draft-prs` in `config.yaml`.
- Keep `labels`, `tox`, `pre-commit`, `custom-check-runs`, `conventional-title`, `set-auto-merge-prs`, `can-be-merged-required-labels`, `minimum-lgtm`, `pypi`, `container`, and other PR workflow behavior in `.github-webhook-server.yaml`.

That gives you the best of both worlds: the server keeps control over secrets and GitHub setup, while each repository can own its day-to-day workflow behavior in version control.
