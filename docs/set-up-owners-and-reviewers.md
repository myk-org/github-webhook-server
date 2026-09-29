# Set Up OWNERS and Review Rules

The webhook server does not use GitHub's CODEOWNERS. It reads its own `OWNERS` files from
your repository and uses them to decide who must review a pull request, who counts as an
*approver*, who can run commands, and which reviewers get auto-assigned.

## What `OWNERS` controls

| Key | Effect |
| --- | --- |
| `approvers` | Members of this scope who can `/approve`. An approval from this list is required for the `can-be-merged` check only when the scope has at least one approver; a path with no approvers listed imposes no approval requirement. |
| `reviewers` | Members of this scope who are eligible for reviewer auto-assignment; `/lgtm` is not gated by membership. |
| `allowed-users` | Users (in addition to collaborators and contributors) who may run commands such as `/retest`, `/cherry-pick`, `/rebase`, `/check-can-merge`, `/assign-reviewers`. |
| `root-approvers` | Optional boolean. Set to `false` to opt out of requiring an approval from the root `OWNERS` approvers. |

## File discovery rules

These are exact — the parser is strict.

- The filename must be exactly `OWNERS` (uppercase, no extension). `OWNERS.yaml`, `owners`,
  `.github/CODEOWNERS`, and `OWNERS_ALIASES` are **not** read.
- Files are found recursively in the cloned repository, which is checked out at the **base
  branch** of the PR. An `OWNERS` file added only in the PR branch is ignored until merged.
- Any path with a dot-prefixed directory component (`.git`, `.venv`, `.github`, ...) is skipped.
- At most 1000 `OWNERS` files are processed per PR.
- Each file is keyed by its **parent directory**, not by file path. `webhook_server/OWNERS`
  is stored under `webhook_server`; the repo-root `OWNERS` is stored under `.`.

## Supported syntax

Each `OWNERS` file is a YAML **mapping**. If it is not a mapping, or if `approvers`,
`reviewers`, or `allowed-users` is present but is not a list of strings, the file is rejected
(logged as `Invalid OWNERS file <path>`) and contributes nothing. Unknown keys are allowed
and ignored by the validator, with the exception of `root-approvers`, which is meaningful.

```yaml
approvers:
  - alice
  - bob
reviewers:
  - carol
  - dave
```

A real example — this repository's own root `OWNERS`:

```yaml
approvers:
  - myakove
  - rnetser
reviewers:
  - myakove
  - rnetser
```

A subdirectory file with the opt-out flag:

```yaml
root-approvers: false
approvers:
  - docs-team
reviewers:
  - writer1
  - writer2
allowed-users:
  - "@contractor"
```

Notes on values:

- Usernames are **GitHub logins**, matched case-sensitively against labels such as
  `approved-alice`. Write them exactly as the login.
- `allowed-users` accepts a leading `@` (it is stripped), and is read **only from the repo-root
  `OWNERS`** — an `allowed-users` list in a subdirectory is ignored.
- `root-approvers` only affects a subdirectory file. Setting it in the root `OWNERS` has no
  effect, because the root entry is always included anyway.

## How owners are resolved for a PR

1. The webhook server clones the repo and runs
   `git diff --name-only <base_sha>...<head_sha>` to get the changed files. (It falls back to
   two-dot `..` if there is no merge base.)
2. It takes the parent directory of every changed file as the set of *changed folders*.
3. Every `OWNERS` file whose directory is equal to, or an ancestor of, a changed folder is
   matched. So `webhook_server/OWNERS` matches a change to `webhook_server/libs/config.py`, and
   also matches a change to `webhook_server/libs/OWNERS`'s subdirectories.
4. The root `OWNERS` (key `.`) is added unless a matched subdirectory file opts out with
   `root-approvers: false`. When several files match, the **first match processed** decides the
   opt-out, so don't mix opting-out and non-opting-out files in one PR.
5. `all_pull_request_approvers` / `all_pull_request_reviewers` are the de-duplicated unions of
   the approvers/reviewers from those matched files.

Separately, `all_repository_approvers` is the union of approvers from **every** `OWNERS` file in
the repository, regardless of what the PR touches. That repo-wide list is what makes a user
eligible to run commands; it is not what gates `/approve`.

## Review commands

### `/approve`

- Only works if the commenter is in `all_pull_request_approvers` (matched by the changed files)
  **or** in the root `OWNERS` approvers. Anyone else is silently ignored.
- Adds the label `approved-<login>` and removes `changes-requested-<login>`.
- `/approve cancel` removes it.
- A `/approve` line anywhere in a submitted pull-request review body does the same thing.
- Any `/approve` triggers the test oracle with `trigger="approved"`.

### `/lgtm`

- Adds `lgtm-<login>` and removes `changes-requested-<login>`.
- Available to **any** commenter except the PR author — it is not restricted to OWNERS
  reviewers. Whether the LGTM *counts* toward `minimum-lgtm` is a separate check.
- A GitHub review with state `approved` or `commented` also produces the `lgtm-<login>` label.
- A GitHub review with state `changes_requested` produces `changes-requested-<login>`, which
  blocks the merge check if that user is a matched approver.

### Who may run other commands

`/retest`, `/reprocess`, `/cherry-pick`, `/rebase`, `/check-can-merge`, `/assign-reviewer(s)`,
`/add-allowed-user`, `/regenerate-welcome`, `/build-and-push-container`, `/security-override`
require the commenter to be in the union of:

- repository collaborators,
- repository contributors,
- every approver in the repository (`all_repository_approvers`),
- the matched PR reviewers (`all_pull_request_reviewers`),
- root `OWNERS` `allowed-users`.

Otherwise a maintainer or approver can grant access per-PR by commenting
`/add-allowed-user @someone`. A `/hold` label is managed with a narrower maintainers-only gate.

`/assign-reviewers` adds every matched reviewer as a review request (the PR author is skipped).

## Configuration

These are **repository-level** settings under `repositories.<key>` in `config.yaml`.

### `minimum-lgtm`

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    minimum-lgtm: 1
```

Default `0` (no LGTM requirement). Type: integer. When greater than zero, the welcome comment
adds a "LGTM Count" line to the merge requirements.

How the count works in `check_if_can_be_merged` → `_check_if_pr_approved`:

- Eligible LGTM authors = matched PR reviewers + root approvers + root reviewers, **minus the
  PR committer**.
- `lgtm_count` counts `lgtm-<login>` labels whose login is in that eligible set. An `lgtm` from
  someone outside it is ignored.
- A PR **passes** if `lgtm_count >= minimum-lgtm`, **or** if `lgtm_count` equals the total number
  of eligible reviewers (i.e. everyone who could have LGTM'd already has). So a repo with two
  eligible reviewers and `minimum-lgtm: 2` is satisfied by two LGTMs even if one is below the
  threshold in other configurations, and `minimum-lgtm` larger than the reviewer count is never
  an impossible requirement.
- On failure the check-run reports:
  `Missing lgtm from reviewers. Minimum N required, (M given). Reviewers: a, b.`

### `can-be-merged-required-labels`

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    can-be-merged-required-labels:
      - signed-off
      - ci/green
```

Default `[]`. Type: array of strings. Every listed label must be present on the PR, otherwise
the `can-be-merged` check run fails with `Missing required labels: <label>`. Unlike OWNERS
approvals, these labels can be added by anyone who can label the PR.

### `auto-verified-and-merged-users`

```yaml
# Global default, applies to all repositories
auto-verified-and-merged-users:
  - release-bot

repositories:
  my-repository:
    name: my-org/my-repository
    auto-verified-and-merged-users:
      - alice
```

Array of strings; the repository value overrides the global one. The logins of every configured
API token are appended automatically, so the bot accounts used by the webhook server are always
auto-verified.

If the PR committer is in this list:

- the `verified` label and a passing `verified` check run are applied automatically (on
  `opened`/`synchronize`, and reset when the PR is re-processed);
- the welcome comment carries an auto-verified note;
- no tracking issue is created for the PR;
- auto-merge is enabled for the PR regardless of the base branch — unless the PR touches
  `security-checks.suspicious-paths`, in which case auto-merge is blocked and any already-enabled
  auto-merge is disabled;
- `auto-verify-cherry-picked-prs: false` disables auto-verification for cherry-picked PRs even
  for these users.

`auto-verified-and-merged-users` bypasses the *verified* step only. The `can-be-merged` check —
approvers, `minimum-lgtm`, required labels, status checks — still applies.

## Merge decision summary

`check_if_can_be_merged` runs these and adds the `can-be-merged` label when all pass:

1. PR is mergeable (no conflicts).
2. No required status check or check run is in progress.
3. No `wip` / `hold` / `has-conflicts` labels (for enabled labels).
4. No failed or missing required status checks.
5. No `changes-requested-<login>` label from a matched approver, and all
   `can-be-merged-required-labels` present.
6. No unresolved conversation threads when `required_conversation_resolution` is enabled.
7. `_check_if_pr_approved`:
   - **Approvers**: for every matched `OWNERS` file, one of its `approvers` must be in the
     `approved-<login>` labels — except that an approval from any root approver clears the
     requirement for all files at once.
   - **LGTM**: the `minimum-lgtm` rule described above.

Failure output is written to the `can-be-merged` check run, e.g.
`Missing approved from approvers: alice, bob`.
