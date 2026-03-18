# Labels, Check Runs, and Mergeability

This server uses labels and GitHub check runs together to show PR state, enforce review rules, and decide when a pull request is ready to merge.

> **Note:** You can configure these features globally in `config.yaml` or per repository in `.github-webhook-server.yaml`. Repository-local settings override the global file.

## Built-in labels

These labels are applied automatically or by PR comment commands when their category is enabled.

| Label | How it is added | What it means |
| --- | --- | --- |
| `verified` | Added automatically for auto-verified users, or manually with `/verified` | The PR has been marked as verified |
| `hold` | Added with `/hold` by an approver | Blocks mergeability |
| `wip` | Added automatically when the title starts with `WIP:`, or manually with `/wip` | Marks the PR as work in progress and blocks mergeability |
| `needs-rebase` | Added automatically when the PR branch is behind or diverged from the base branch | The PR should be rebased or updated |
| `has-conflicts` | Added automatically when GitHub reports merge conflicts | The PR is not mergeable until conflicts are resolved |
| `can-be-merged` | Added automatically when all merge rules pass | The PR currently satisfies the server’s mergeability checks |
| `automerge` | Added with `/automerge` by a maintainer or approver | Tells the server to squash-merge once `can-be-merged` succeeds |
| `branch-<base-branch>` | Added automatically on PR open and update | Shows the target branch, such as `branch-main` |
| `size/<category>` | Added automatically on PR open and update | Shows PR size, such as `size/M` |

`needs-rebase` and `has-conflicts` are separate signals. A PR can be behind the base branch, have conflicts, or both.

The built-in label categories and colors are configurable:

```yaml
labels:
  enabled-labels:
    - verified
    - hold
    - wip
    - needs-rebase
    - has-conflicts
    - can-be-merged
    - size
    - branch
    - cherry-pick
    - automerge
  colors:
    hold: red
    verified: green
    wip: orange
    needs-rebase: darkred
    has-conflicts: red
    can-be-merged: limegreen
    automerge: green
    approved-: green
    lgtm-: yellowgreen
    changes-requested-: orange
    commented-: gold
    cherry-pick-: coral
    branch-: royalblue
```

> **Note:** If `labels.enabled-labels` is omitted, all configurable built-in label categories are enabled. If you set it to `[]`, all configurable built-in labels are disabled, but reviewed-by labels still remain active.

## Reviewed-by labels

The server also creates dynamic labels that reflect review activity:

- `approved-<user>`
- `lgtm-<user>`
- `changes-requested-<user>`
- `commented-<user>`

These labels are always enabled and cannot be disabled through `labels.enabled-labels`.

The distinction that matters most is this:

- GitHub’s normal “Approve review” action becomes `lgtm-<user>`.
- This project’s explicit `/approve` command becomes `approved-<user>`.
- A `changes_requested` review becomes `changes-requested-<user>`.
- A comment-only review becomes `commented-<user>`.

That is what lets the server separate “looks good” from “formal approver approval”.

> **Tip:** When new commits are pushed to a PR, the server removes existing reviewed-by labels and rebuilds mergeability from fresh review activity. This is why `approved-*` and `lgtm-*` labels disappear after a `synchronize` event.

## Size and branch labels

Every opened or updated PR gets a branch label and a size label.

The branch label uses the base branch name:

- `branch-main`
- `branch-develop`
- `branch-release-1.2`

The size label is based on `additions + deletions`. The built-in defaults come directly from the source:

```python
STATIC_PR_SIZE_THRESHOLDS: tuple[tuple[int | float, str, str], ...] = (
    (20, "XS", "ededed"),
    (50, "S", "0E8A16"),
    (100, "M", "F09C74"),
    (300, "L", "F5621C"),
    (500, "XL", "D93F0B"),
    (float("inf"), "XXL", "B60205"),
)
```

That means the default labels are:

- `size/XS` for fewer than 20 changed lines
- `size/S` for 20 to 49
- `size/M` for 50 to 99
- `size/L` for 100 to 299
- `size/XL` for 300 to 499
- `size/XXL` for 500 and up

You can replace those defaults with your own categories:

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

Even if you rename the buckets to `Tiny`, `Express`, or `Critical`, they are still controlled by the single `size` category in `labels.enabled-labels`.

> **Tip:** Use `inf` for the last size bucket so every PR larger than your biggest numeric threshold still gets a label.

## Verified Check

When `verified-job` is enabled, the server maintains a `verified` check run and uses it as one of the merge requirements.

For a normal contributor, the flow is:

- A new PR starts with `verified` in `queued`.
- Someone can mark it with `/verified`.
- The server adds the `verified` label and sets the `verified` check to `success`.
- When new commits are pushed, the server removes the `verified` label and resets the check back to `queued`.

For trusted or automation accounts, the server can do this automatically:

```yaml
verified-job: true

auto-verified-and-merged-users:
  - "renovate[bot]"
  - "dependabot[bot]"
  - "trusted-user"

auto-verify-cherry-picked-prs: false
```

A few details matter here:

- `verified-job` defaults to `true`.
- `auto-verified-and-merged-users` are auto-verified on PR open and update.
- The server also adds the users behind configured GitHub tokens to the auto-verified list automatically.
- If `auto-verify-cherry-picked-prs` is `false`, cherry-picked PRs are not auto-verified.
- AI-resolved cherry-picks are never auto-verified, even if cherry-pick auto-verification is enabled.

If you do not want verification to be part of mergeability, set `verified-job: false`.

## Can-Be-Merged Check

`can-be-merged` is the server’s final readiness check. When it succeeds, the server marks the PR with the `can-be-merged` label. When it fails, the label is removed and the check output explains why.

A PR must satisfy all of these rules:

- The PR must still be open and mergeable.
- No required checks can be in progress.
- No required checks can be missing or failed.
- `hold` and `wip` must not be present.
- Any configured `can-be-merged-required-labels` must be present.
- There must be no blocking `changes-requested-<user>` label from an approver.
- Approval and LGTM rules must pass.
- If conversation resolution is enabled, there must be no unresolved review threads.

You can also add extra label-based gates:

```yaml
can-be-merged-required-labels:
  - "approved"
  - "tests-passed"
  - "security-reviewed"
```

The check considers the following status sources:

- GitHub required status checks from the base branch’s branch protection
- Built-in server checks such as `tox`, `verified`, `build-container`, `python-module-install`, and `conventional-title`
- Mandatory custom check runs
- Legacy GitHub commit status contexts

> **Note:** `pre-commit` still runs when enabled, but `can-be-merged` only treats it as required when it appears in the branch’s required status checks.

> **Warning:** On private repositories, the runtime `can-be-merged` logic skips the live branch-protection status-check lookup. If you rely on extra required checks in private repos, make sure your server-side required checks and mandatory custom checks cover what you need.

If unresolved conversations are blocking the PR, the check output looks like this:

```text
PR has 2 unresolved review conversation(s):
  - src/main.py:42 (https://github.com/test-org/test-repo/pull/123#discussion_r100)
  - src/utils.py:10 (https://github.com/test-org/test-repo/pull/123#discussion_r101)
```

The server recalculates `can-be-merged` automatically when:

- a relevant label is added or removed
- a required check run completes
- a terminal commit status arrives
- a review thread is resolved or reopened
- you run `/check-can-merge`

> **Tip:** If the PR also has the `automerge` label, a successful `can-be-merged` check triggers an immediate squash merge.

> **Tip:** If you want the check run but not the extra label, you can disable the `can-be-merged` label category. The `can-be-merged` check still reports the result.

## Minimum LGTM And Approval Rules

`minimum-lgtm` does not replace approval. It adds an extra reviewer-consensus rule on top of approver approval.

Here is the distinction:

- `/approve` is the formal approval signal and creates `approved-<user>`.
- `/lgtm` and normal GitHub “approved” reviews create `lgtm-<user>`.
- `minimum-lgtm` counts `lgtm-*` labels from eligible reviewers.
- The PR author’s own LGTM does not count.

Example:

```yaml
minimum-lgtm: 2
```

With that setting, the PR still needs approver approval from the relevant OWNERS rules, and it also needs at least two valid LGTMs.

The rules are OWNERS-aware:

- A root approver can satisfy the approval requirement for the whole PR.
- Otherwise, the server looks at the approver sets attached to the changed files.
- A `changes-requested-<user>` label only blocks mergeability when that user is an approver for the PR.

> **Note:** The LGTM requirement is effectively capped by the number of eligible reviewers. If `minimum-lgtm` is higher than the number of valid reviewers, the server treats the requirement as satisfied once every eligible reviewer except the PR author has added LGTM.

Set `minimum-lgtm: 0` to disable the LGTM requirement entirely.

## Unresolved Conversation Checks

Conversation resolution is controlled by `branch-protection.required_conversation_resolution`, which defaults to `true`.

A repository-level example looks like this:

```yaml
branch-protection:
  strict: true
  require_code_owner_reviews: true
  dismiss_stale_reviews: false
  required_approving_review_count: 1
  required_linear_history: true
  required_conversation_resolution: true
```

When this setting is enabled:

- the server queries GitHub review threads through GraphQL
- resolved threads are ignored
- unresolved threads block `can-be-merged`
- outdated unresolved threads still count
- the check output includes the file, line, and discussion URL when available

Set `required_conversation_resolution: false` if you want `can-be-merged` to ignore unresolved review threads.

> **Note:** If you want mergeability to update immediately when a conversation is resolved or reopened, make sure the repository is configured to receive `pull_request_review_thread` events.

## Custom Check Runs

Custom check runs let you add your own PR checks without changing Python code.

A schema example from this repository looks like this:

```yaml
custom-check-runs:
  - name: lint
    command: uv tool run --from ruff ruff check
    mandatory: true
  - name: security-scan
    command: TOKEN=xyz DEBUG=true uv tool run --from bandit bandit -r .
    mandatory: false
```

How custom checks behave:

- The check name appears in GitHub exactly as configured.
- The command runs in the PR worktree.
- The command is executed through `/bin/sh -c`, so environment variables, pipes, and other shell syntax work.
- Custom checks are queued and run on PR open and PR update.
- Both mandatory and optional custom checks still run.
- Checks with `mandatory: false` are excluded from merge-blocking logic.

That makes `mandatory: false` useful for visibility-only checks:

- run a slow or advisory scan
- show results on the PR
- keep `can-be-merged` focused on the hard gates

You can retry custom checks with the same commands you use for built-in checks:

- `/retest lint`
- `/retest your-check-name`
- `/retest all`

Validation rules are strict:

- `name` and `command` are required
- names may only use letters, numbers, `.`, `_`, and `-`
- names must be 1 to 64 characters
- duplicate names are skipped after the first one
- names that collide with built-in check names are rejected
- the executable must exist on the webhook server host or container

The built-in names rejected for custom checks are:

- `tox`
- `pre-commit`
- `build-container`
- `python-module-install`
- `conventional-title`
- `can-be-merged`

> **Warning:** If the executable in a custom check is not available in the webhook server environment, the server does not load that custom check. It logs a warning and skips it.

> **Tip:** Use `mandatory: false` for advisory checks, and `mandatory: true` for checks that should block `can-be-merged`.

## Useful Commands

These are the commands most relevant to labels and mergeability:

- `/wip` and `/wip cancel`
- `/hold` and `/hold cancel`
- `/verified` and `/verified cancel`
- `/lgtm`
- `/approve`
- `/automerge`
- `/check-can-merge`
- `/retest <check-name>`
- `/retest all`

In practice, the flow usually looks like this:

1. Open or update a PR.
2. Let the server apply branch, size, and merge-state labels.
3. Wait for check runs to finish.
4. Use `/lgtm`, `/approve`, and `/verified` as needed.
5. Resolve any remaining review threads.
6. Watch for `can-be-merged` to turn green.
