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
| `dot-folders` | Root `OWNERS` only. Optional list of dot-directories whose `OWNERS` files should be read. Dot-directories are ignored by default. |

## File discovery rules

These are exact — the parser is strict.

- The filename must be exactly `OWNERS` (uppercase, no extension). `OWNERS.yaml`, `owners`,
  `.github/CODEOWNERS`, and `OWNERS_ALIASES` are **not** read.
- Files are found recursively in the cloned repository, which is checked out at the **base
  branch** of the PR. An `OWNERS` file added only in the PR branch is ignored until merged.
- Any path with a dot-prefixed directory component (`.git`, `.venv`, `.github`, ...) is skipped,
  unless the root `OWNERS` opts that dot-directory in with [`dot-folders`](#dot-folders).
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

### `dot-folders`

`OWNERS` files inside dot-directories are ignored by default. The root `OWNERS` can opt specific
dot-directories in:

```yaml
approvers:
  - myakove
reviewers:
  - myakove
dot-folders:
  - .github
```

A listed entry covers that dot-directory's whole subtree, exactly like a normal directory: `.github`
makes both `.github/OWNERS` and `.github/workflows/OWNERS` load, so their approvers and reviewers
count for reviewer assignment, approvals, `/approve`, `/lgtm`, `/automerge`, and `/merge`
(per-directory approval and `root-approvers`).

Entries name a dot-directory segment relative to the repository root: relative, dot-prefixed, no
globs, no `..`. `.git` is never allowed. A non-list value or an invalid entry is logged and ignored.
Like `allowed-users`, the key is read only from the repo-root `OWNERS`; a `dot-folders` list in a
subdirectory is ignored.

List the dot-directory itself, not a deeper path. An entry such as `.github/workflows` matches no
directory and loads nothing — use `.github`, because the entry already enables the whole subtree.

Without the key every dot-directory is ignored exactly as before.

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

`root-approvers: false` also unlocks the `/merge` command (see
[Pull request commands](run-pull-request-commands.html)): an accepted sender can merge the PR
themselves — once, at the moment the command is sent — when `can-be-merged` is green.

The opt-out is the first condition and it is universal: **every** `OWNERS` dir matching the
changed files must set `root-approvers: false`, for every sender, effective maintainers
included. If any matched dir lacks the opt-out, or a changed folder has no matching
sub-`OWNERS`, `/merge` stays disabled.

When that holds, the sender is authorized if any of these is true:

- they are an **effective maintainer** — anyone GitHub reports with `admin` or `maintain`
  permission, org owners and team-inherited admins included — so they merge without approving
  any of the matched dirs;
- they are a **root `OWNERS` approver** — an approver listed in the repository root `.`
  `OWNERS` file — on the same terms;
- they are an approver of **every** `OWNERS` dir covering the changed files, sub-directory and
  SIG approvers included. This path is unchanged, so a repository with no maintainers can still
  merge its own pull requests through its own approvers. It is intentionally stricter than the
  first-match rule used for approver assignment above (step 4): approving `xx/yy` is not enough
  when the PR also touches `xx`.

Anyone else is rejected and root approvers remain required. On top of the `OWNERS` rules,
`/merge` is opt-in at the server level: it must be enabled with `allow-merge-command: true`
(globally or per repository) before any of this applies.

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
`/regenerate-welcome`, `/build-and-push-container`
require the commenter to be in the union of:

- repository collaborators,
- repository contributors,
- every approver in the repository (`all_repository_approvers`),
- the matched PR reviewers (`all_pull_request_reviewers`),
- root `OWNERS` `allowed-users`.

`/rebase` and `/security-override` additionally accept anyone with effective `admin` or `maintain` permission on the
repository — org owners and team-inherited admins included, direct collaboration not required. `/automerge` and
`/automerge cancel` accept the same effective maintainers on top of their existing maintainer and approver path, and
that path is unchanged, so no `OWNERS` approver loses the ability to set or cancel auto-merge. `/security-override` is
narrower than the union above: besides effective maintainers, only root `OWNERS` approvers may run it — the approvers
listed in the repository root `.` OWNERS file. Approvers of a sub-directory OWNERS file cannot override the security
checks repo-wide.

Otherwise an effective maintainer or a root `OWNERS` approver can grant access per-PR by commenting
`/add-allowed-user @someone`. Vouching follows the same rule as `/security-override`: only those two groups can vouch,
so an approver listed only in a sub-directory OWNERS file can no longer grant access repo-wide. A `/hold` label is
managed with a narrower maintainers-only gate.

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
