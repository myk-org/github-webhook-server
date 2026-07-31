# .github-webhook-server.yaml
verified-job: true
create-issue-for-new-pr: true
minimum-lgtm: 1
set-auto-merge-prs:
  - main
can-be-merged-required-labels:
  - security-reviewed
```

```yaml
# OWNERS
approvers:
  - myakove
  - rnetser
reviewers:
  - myakove
  - rnetser
```

Open a PR to `main`, then have an approver submit a GitHub review with state `Approved` and this review body:

```text
/approve
```

With that setup, the server will:

- assign reviewers from `OWNERS`
- add a branch label such as `branch-main`
- add a size label such as `size/M`
- queue `can-be-merged` and `verified`, plus any enabled checks
- mark the PR merge-ready only after required checks, required labels, approval, and LGTM requirements are satisfied

## Step-by-Step

### 1. Open the PR or mark it ready for review

On `opened` and `ready_for_review`, the server posts a welcome comment that lists the available PR actions and the current merge requirements. It also assigns reviewers from the matching `OWNERS` rules, adds the PR author as an assignee when possible, and creates a tracking issue if `create-issue-for-new-pr: true`.

At the same time, the server applies the first round of state labels and queues check runs.

| What appears automatically | What it means |
| --- | --- |
| `branch-<base>` | Shows the target branch, such as `branch-main`. |
| `size/<bucket>` | Buckets the PR by total additions plus deletions, such as `size/M`. |
| `needs-rebase` | The PR is behind or diverged from its base branch. |
| `has-conflicts` | GitHub reports the PR is not mergeable. |
| `verified` | The PR is currently verified. This matters only when `verified-job` is enabled. |
| `can-be-merged` | The PR currently meets all merge requirements. |

Any enabled built-in or custom checks are queued at the same time. Common examples are `tox`, `pre-commit`, `build-container`, `conventional-title`, and security checks.

### 2. Review the PR

GitHub review state and explicit approver sign-off are tracked separately. A normal GitHub `Approved` review counts as reviewer LGTM feedback, while the explicit approver signal is `/approve`.

| Reviewer action | What the server records | Where it matters |
| --- | --- | --- |
| GitHub review with state `Approved` | `lgtm-<reviewer>` | Counts toward `minimum-lgtm`. |
| Review body or PR comment containing `/approve` | `approved-<approver>` | Satisfies the approver requirement from `OWNERS`. |
| Required fixed label, such as `security-reviewed` | Exact label match | Satisfies `can-be-merged-required-labels`. |

> **Note:** The PR author does not count toward LGTM counting for their own PR.

### 3. Wait for merge readiness

The `can-be-merged` check is recalculated from the current head commit. It passes only when every active requirement is satisfied.

A PR becomes merge-ready when these checks all pass:

- required status checks are green
- the PR is mergeable and has no conflict label
- required approver sign-off is present
- the configured `minimum-lgtm` count is satisfied
- every label in `can-be-merged-required-labels` is present
- the `verified` state is present if `verified-job` is enabled
- unresolved review conversations are cleared if branch protection requires conversation resolution

### 4. Push more commits

On `synchronize`, the server rechecks merge state and reruns the PR workflow for the new head commit. Review-state labels are cleared so the new revision is evaluated fresh.

If the update is only a clean rebase, the server keeps verification aligned with the new head instead of forcing a full manual re-verify.

| Push result | What usually changes |
| --- | --- |
| New code commit | review-state labels are cleared, `verified` is re-queued, and merge readiness is recalculated |
| Clean rebase | merge state is refreshed, but existing verification can be carried forward to the new head |

### 5. Merge the PR

On merge, the server closes the tracking issue it created for the PR. If cherry-pick labels were already attached, the server starts those follow-up cherry-picks after merge.

It also refreshes merge-state labels on other open PRs so stale `needs-rebase` or conflict states get revisited after the branch moves forward.

> **Tip:** If the PR targets a branch listed in `set-auto-merge-prs`, or if the author is in `auto-verified-and-merged-users`, the server can enable native GitHub auto-merge with squash merging as soon as the PR is initialized.

## Advanced Usage

Trusted authors and branch-based auto-merge change the PR lifecycle in important ways.

- `auto-verified-and-merged-users` makes PRs from those authors auto-verified on open and on later updates.
- Those same trusted authors can also get GitHub auto-merge enabled automatically.
- If `create-issue-for-new-pr` is enabled, trusted auto-verified authors skip tracking-issue creation.

Security-sensitive file paths can also override auto-merge behavior.

```yaml
security-checks:
  mandatory: true
  suspicious-paths:
    - ".github/workflows/"
    - ".github/actions/"
  committer-identity-check: true
```

- If a PR touches a configured suspicious path, native auto-merge is not enabled until the suspicious-paths check passes.
- Security checks and committer-identity checks appear as normal check runs, so contributors can see the block directly on the PR.

Optional PR automation can fire on lifecycle events too.

```yaml
test-oracle:
  server-url: "http://localhost:800"
  ai-provider: "claude"
  ai-model: "claude-opus-4-6[1m]"
  test-patterns:
    - "tests/**/*.py"
  triggers:
    - approved
    - pr-opened
```

- `pr-opened` starts test recommendation analysis when the PR is first opened.
- `approved` starts it when an approver explicitly uses `/approve`.
- See [Supported GitHub Events](supported-github-events.html) for the full event matrix.
- See [Configuration Reference](configuration-reference.html) for the exact PR-related keys and defaults.

## Troubleshooting

**`can-be-merged` is failing**

- Open the `can-be-merged` check first. It reports the missing requirement directly, such as missing approver sign-off, missing required labels, unresolved conversations, failing checks, or merge conflicts.

**The PR lost `verified` after a push**

- That is normal for a new code revision when `verified-job` is enabled. The server re-queues verification on new commits and only preserves it automatically for clean rebases or trusted auto-verified authors.

**A normal GitHub approval did not satisfy the approver requirement**

- Use `/approve` from an approver. In this workflow, GitHub review approval and approver sign-off are separate signals.

**Auto-merge did not turn on**

- Check whether the base branch is listed in `set-auto-merge-prs`, whether the author is in `auto-verified-and-merged-users`, and whether the PR touches a suspicious path that keeps auto-merge off until security checks pass.# Manage Pull Requests

You want contributors to open a pull request and immediately understand what the server will do next: who gets assigned, which labels and checks appear, and what has to turn green before the PR is ready to merge. This page walks through the default PR lifecycle so authors and reviewers can work with the automation instead of guessing at it.

## Prerequisites

- The server is already connected to the repository. See [Start Automating a Repository](quick-start.html).
- An `OWNERS` file defines approvers and reviewers. See [Set Up OWNERS and Review Rules](set-up-owners-and-reviewers.html).
- Any CI checks you expect on PRs are already configured. See [Set Up Checks and Release Workflows](set-up-checks-and-release-workflows.html).

## Quick Example

```yaml
# .github-webhook-server.yaml
verified-job: true
create-issue-for-new-pr: true
minimum-lgtm: 1
set-auto-merge-prs:
  - main
can-be-merged-required-labels:
  - security-reviewed
```

```yaml
# OWNERS
approvers:
  - myakove
  - rnetser
reviewers:
  - myakove
  - rnetser
```

Open a PR to `main`, then have an approver submit a GitHub review with state `Approved` and this review body:

```text
/approve
```

With that setup, the server will:

- assign reviewers from `OWNERS`
- add a branch label such as `branch-main`
- add a size label such as `size/M`
- queue `can-be-merged` and `verified`, plus any enabled checks
- mark the PR merge-ready only after required checks, required labels, approval, and LGTM requirements are satisfied

## Step-by-Step

### 1. Open the PR or mark it ready for review

On `opened` and `ready_for_review`, the server posts a welcome comment that lists the available PR actions and the current merge requirements. It also assigns reviewers from the matching `OWNERS` rules, adds the PR author as an assignee when possible, and creates a tracking issue if `create-issue-for-new-pr: true`.

At the same time, the server applies the first round of state labels and queues check runs.

| What appears automatically | What it means |
| --- | --- |
| `branch-<base>` | Shows the target branch, such as `branch-main`. |
| `size/<bucket>` | Buckets the PR by total additions plus deletions, such as `size/M`. |
| `needs-rebase` | The PR is behind or diverged from its base branch. |
| `has-conflicts` | GitHub reports the PR is not mergeable. |
| `verified` | The PR is currently verified. This matters only when `verified-job` is enabled. |
| `can-be-merged` | The PR currently meets all merge requirements. |

Any enabled built-in or custom checks are queued at the same time. Common examples are `tox`, `pre-commit`, `build-container`, `conventional-title`, and security checks.

### 2. Review the PR

GitHub review state and explicit approver sign-off are tracked separately. A normal GitHub `Approved` review counts as reviewer LGTM feedback, while the explicit approver signal is `/approve`.

| Reviewer action | What the server records | Where it matters |
| --- | --- | --- |
| GitHub review with state `Approved` | `lgtm-<reviewer>` | Counts toward `minimum-lgtm`. |
| Review body or PR comment containing `/approve` | `approved-<approver>` | Satisfies the approver requirement from `OWNERS`. |
| Required fixed label, such as `security-reviewed` | Exact label match | Satisfies `can-be-merged-required-labels`. |

> **Note:** The PR author does not count toward LGTM counting for their own PR.

### 3. Wait for merge readiness

The `can-be-merged` check is recalculated from the current head commit. It passes only when every active requirement is satisfied.

A PR becomes merge-ready when these checks all pass:

- required status checks are green
- the PR is mergeable and has no conflict label
- required approver sign-off is present
- the configured `minimum-lgtm` count is satisfied
- every label in `can-be-merged-required-labels` is present
- the `verified` state is present if `verified-job` is enabled
- unresolved review conversations are cleared if branch protection requires conversation resolution

### 4. Push more commits

On `synchronize`, the server rechecks merge state and reruns the PR workflow for the new head commit. Review-state labels are cleared so the new revision is evaluated fresh.

If the update is only a clean rebase, the server keeps verification aligned with the new head instead of forcing a full manual re-verify.

| Push result | What usually changes |
| --- | --- |
| New code commit | review-state labels are cleared, `verified` is re-queued, and merge readiness is recalculated |
| Clean rebase | merge state is refreshed, but existing verification can be carried forward to the new head |

### 5. Merge the PR

On merge, the server closes the tracking issue it created for the PR. If cherry-pick labels were already attached, the server starts those follow-up cherry-picks after merge.

It also refreshes merge-state labels on other open PRs so stale `needs-rebase` or conflict states get revisited after the branch moves forward.

> **Tip:** If the PR targets a branch listed in `set-auto-merge-prs`, or if the author is in `auto-verified-and-merged-users`, the server can enable native GitHub auto-merge with squash merging as soon as the PR is initialized.

## Advanced Usage

Trusted authors and branch-based auto-merge change the PR lifecycle in important ways.

- `auto-verified-and-merged-users` makes PRs from those authors auto-verified on open and on later updates.
- Those same trusted authors can also get GitHub auto-merge enabled automatically.
- If `create-issue-for-new-pr` is enabled, trusted auto-verified authors skip tracking-issue creation.

Security-sensitive file paths can also override auto-merge behavior.

```yaml
security-checks:
  mandatory: true
  suspicious-paths:
    - ".github/workflows/"
    - ".github/actions/"
  committer-identity-check: true
```

- If a PR touches a configured suspicious path, native auto-merge is not enabled until the suspicious-paths check passes.
- Security checks and committer-identity checks appear as normal check runs, so contributors can see the block directly on the PR.

Optional PR automation can fire on lifecycle events too.

```yaml
test-oracle:
  server-url: "http://localhost:8000"
  ai-provider: "claude"
  ai-model: "claude-opus-4-6[1m]"
  test-patterns:
    - "tests/**/*.py"
  triggers:
    - approved
    - pr-opened
```

- `pr-opened` starts test recommendation analysis when the PR is first opened.
- `approved` starts it when an approver explicitly uses `/approve`.
- See [Supported GitHub Events](supported-github-events.html) for the full event matrix.
- See [Configuration Reference](configuration-reference.html) for the exact PR-related keys and defaults.

## Troubleshooting

**`can-be-merged` is failing**

- Open the `can-be-merged` check first. It reports the missing requirement directly, such as missing approver sign-off, missing required labels, unresolved conversations, failing checks, or merge conflicts.

**The PR lost `verified` after a push**

- That is normal for a new code revision when `verified-job` is enabled. The server re-queues verification on new commits and only preserves it automatically for clean rebases or trusted auto-verified authors.

**A normal GitHub approval did not satisfy the approver requirement**

- Use `/approve` from an approver. In this workflow, GitHub review approval and approver sign-off are separate signals.

**Auto-merge did not turn on**

- Check whether the base branch is listed in `set-auto-merge-prs`, whether the author is in `auto-verified-and-merged-users`, and whether the PR touches a suspicious path that keeps auto-merge off until security checks pass.

## Related Pages

- [Set Up OWNERS and Review Rules](set-up-owners-and-reviewers.html)
- [Run Pull Request Commands](run-pull-request-commands.html)
- [Set Up Checks and Release Workflows](set-up-checks-and-release-workflows.html)
- [Secure Webhooks and Pull Requests](secure-webhooks-and-pull-requests.html)
- [Supported GitHub Events](supported-github-events.html)
