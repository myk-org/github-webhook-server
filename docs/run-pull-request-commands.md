# Run Pull Request Commands

The webhook server reacts to slash commands posted as pull request comments. Comment `/wip` on a PR and the server
adds the `wip` label and prefixes the title with `WIP:`.

## How commands are recognized

- Only comments **created** on a PR are processed. Editing or deleting a comment does nothing.
- Every **line** of the comment that starts with `/` is treated as a command. The leading `/` is stripped, the first
  word is the command, and the rest is the argument.
- Several commands can be posted in a single comment — each line is one command, and they run concurrently.
- A command argument of exactly `cancel` turns the command into a removal (`/wip cancel`).
- The server reacts with a 👍 to the comment once a command is accepted.
- Comments containing the server's own welcome-message URL are ignored, so the bot never processes its own output.
- Unknown commands are silently ignored (logged only).

Example — one comment, three commands:

```text
/wip
/assign-reviewers
/retest tox
```

## Who can run what

| Who | Meaning |
| --- | --- |
| **Author** | The user who opened the pull request |
| **Approver** | A repository collaborator/contributor, an approver listed in `OWNERS`, a maintainer, **or** anyone a maintainer/approver has granted with `/add-allowed-user @user` |
| **Maintainer** | Listed in the `root-approvers` configuration, or otherwise resolved as a repository maintainer |
| **Anyone** | Any user who can comment, no permission check |

| Command | What it does | Who can run it |
| --- | --- | --- |
| `/wip` | Adds the `wip` label and prefixes the title with `WIP: ` | Author, Approver |
| `/wip cancel` | Removes the `wip` label and strips the `WIP:` prefix from the title | Author, Approver |
| `/verified` | Adds the `verified` label and sets the `verified` check run to success | Author, Approver |
| `/verified cancel` | Removes the `verified` label and re-queues the `verified` check run | Author, Approver |
| `/hold` | Adds the `hold` label, blocking the merge | Author, Approver |
| `/hold cancel` | Removes the `hold` label | Author, Approver |
| `/lgtm` | Adds the `lgtm-<user>` label and removes that user's `changes-requested-<user>` | Anyone (ignored on your own PR) |
| `/lgtm cancel` | Removes the `lgtm-<user>` label | Anyone (ignored on your own PR) |
| `/approve` | Adds `approved-<user>`, removes `changes-requested-<user>`, and triggers the AI test oracle | Approver |
| `/approve cancel` | Removes `approved-<user>` | Approver |
| `/automerge` | Adds the `automerge` label; the PR merges automatically once all requirements are met | Maintainer, Approver |
| `/automerge cancel` | Removes the `automerge` label | Maintainer, Approver |
| `/retest <test>` | Re-runs one or more configured checks | Approver |
| `/retest all` | Re-runs every check configured for the repository | Approver |
| `/reprocess` | Re-runs the complete PR processing workflow from scratch | Approver |
| `/rebase` | Rebases the PR branch onto its base branch and force-pushes | Approver |
| `/assign-reviewers` | Assigns reviewers according to the repository `OWNERS` file | Approver |
| `/assign-reviewer @user` | Requests a review from a specific collaborator | Approver |
| `/add-allowed-user @user` | Grants an arbitrary commenter permission to run approver commands | Maintainer, Approver |
| `/check-can-merge` | Runs the `can-be-merged` check and reports the merge blockers | Approver |
| `/build-and-push-container` | Builds and pushes the container image for the PR | Anyone |
| `/cherry-pick <branch> [branch...]` | Cherry-picks the PR to one or more target branches | Approver |
| `/cherry-pick-retry <branch>` | Retries a failed cherry-pick on a merged PR | Approver |
| `/regenerate-welcome` | Re-renders the welcome comment with the current configuration | Approver |
| `/test-oracle` | Sends the PR to the configured PR Test Oracle service | Approver |
| `/security-override` | Sets the security check runs to success | Maintainer |
| `/security-override cancel` | Re-runs the security checks to re-evaluate them | Maintainer |

## Commands on draft pull requests

Draft PRs are blocked by default. The `allow-commands-on-draft-prs` configuration key controls the exception list
(per repository, with a repository-level override of the global default):

```yaml
# .github-webhook-server.yaml
allow-commands-on-draft-prs:
  - build-and-push-container
  - retest
```

| Value | Behaviour |
| --- | --- |
| Not set (default) | All commands are blocked on draft PRs |
| `[]` (empty list) | All commands are allowed on draft PRs |
| `["retest", "wip"]` | Only the listed commands are allowed; any other command is rejected with a comment naming the allowed ones |

`/test-oracle` is the one exception to the per-command allowlist — it runs on a draft PR even when it is not listed in
`allow-commands-on-draft-prs`. That exception only applies when the key is set, even to an empty list. If the key is
absent, the draft pull request is dropped before any command is evaluated, so `/test-oracle` does not run either.

## Command details

### PR status

#### `/wip` and `/wip cancel`

Marks the PR as work in progress. `/wip` adds the `wip` label and prepends `WIP: ` to the title if the prefix is not
already there. `/wip cancel` removes the label and strips the prefix (both `WIP:` and `WIP: ` are handled).

```text
/wip
```

```text
/wip cancel
```

Available to the PR author and approvers. A user who is neither is ignored.

#### `/verified` and `/verified cancel`

Marks the change as reviewed and verified. `/verified` also sets the `verified` check run to **success**;
`/verified cancel` removes the label and puts the `verified` check run back to **queued** so it runs again.

```text
/verified
```

```text
/verified cancel
```

Available to the PR author and approvers.

#### `/hold` and `/hold cancel`

`hold` blocks the pull request from merging until it is removed. Because it is merge-blocking, it is narrower than
`/wip` and `/verified`: only the PR author and pull request approvers can set or clear it. Anyone else gets a comment
explaining that only the author or approvers may manage the label.

```text
/hold
```

```text
/hold cancel
```

### Review and approval

#### `/lgtm` and `/lgtm cancel`

Records "looks good to me" for the commenting user by adding the `lgtm-<user>` label and removing that user's
`changes-requested-<user>` label. Any commenter may use it; if the commenter is the PR author, the command is ignored
so you cannot lgtm your own pull request.

```text
/lgtm
```

```text
/lgtm cancel
```

#### `/approve` and `/approve cancel`

Records a formal approval by adding `approved-<user>` and removing `changes-requested-<user>`. Only users in the
`OWNERS` approver list (or root approvers) are accepted; a non-approver's `/approve` is silently ignored.

A successful `/approve` also kicks off the PR Test Oracle in the background, if it is configured.

```text
/approve
```

```text
/approve cancel
```

#### `/automerge` and `/automerge cancel`

`/automerge` adds the `automerge` label. Once the label is present and every merge requirement is satisfied, the server
merges the PR on its own. `/automerge cancel` removes the label.

```text
/automerge
```

```text
/automerge cancel
```

Both are restricted to maintainers and approvers; anyone else gets the comment
*"Only maintainers or approvers can set pull request to auto-merge"*.

#### `/assign-reviewers`

Assigns reviewers to the pull request based on the `OWNERS` file in the repository.

```text
/assign-reviewers
```

#### `/assign-reviewer @username`

Requests a review from one specific user. The target must be a repository collaborator, otherwise the server comments
that the user is not a collaborator.

```text
/assign-reviewer @octocat
```

#### `/check-can-merge`

Evaluates the merge requirements and reports which ones are still blocking the merge.

```text
/check-can-merge
```

#### `/add-allowed-user @username`

Lets a maintainer or approver grant command permission to someone who is not otherwise a collaborator — for example an
external contributor working on a PR. The grant is per pull request: the server looks for a comment containing
`/add-allowed-user @username` posted by a maintainer or approver.

```text
/add-allowed-user @external-contributor
```

The user can then run approver-level commands such as `/retest` or `/reprocess` on that PR.

### Testing and validation

#### `/retest <test> [test...]` and `/retest all`

Re-runs one or more checks. The test name is required — `/retest` with no argument gets a
*"retest requires an argument"* comment. Unknown names get a *"No <name> configured for this repository"* comment; the
valid ones still run.

Supported names depend on the repository configuration:

| Test name | Runs |
| --- | --- |
| `tox` | The Python test suite |
| `build-container` | Rebuilds and tests the container image |
| `python-module-install` | Tests installing the Python package |
| `pre-commit` | Runs pre-commit hooks and checks |
| `conventional-title` | Validates the commit message format |
| *custom check-run names* | Any check run defined in `custom-check-runs` |

```text
/retest tox
```

```text
/retest tox pre-commit
```

```text
/retest all
```

`all` cannot be combined with other names — `/retest all tox` is rejected with
*"Invalid command. `all` cannot be used with other tests"*.

#### `/build-and-push-container`

Builds the container image for the PR and pushes it, tagged with the PR number. Extra build arguments are supported:

```text
/build-and-push-container
```

```text
/build-and-push-container --build-arg KEY=value
```

If the repository has no container build configured, the server replies
*"No build-and-push-container configured for this repository"*. No permission check is applied.

#### `/test-oracle`

Sends the pull request data to the external PR Test Oracle service, which analyses the diff and recommends tests.
Requires `test-oracle` to be configured with a `server-url`. Runs in the background, so the command returns
immediately.

```text
/test-oracle
```

This command is also the only one that bypasses the draft-PR block.

### Branch management and cherry-picks

#### `/rebase`

Rebases the PR head branch onto its base branch and force-pushes the result.

```text
/rebase
```

Restrictions enforced by the server:

- The PR must be open.
- Fork PRs are rejected — the head branch lives in another repository, so the force-push cannot be scoped to this repo.
- For bot-owned PRs (such as cherry-pick PRs) the requester must be the cherry-pick initiator (the PR assignee) or a
  maintainer.

#### `/cherry-pick <branch> [branch...]`

Cherry-picks the change to one or more target branches. Each branch that exists gets a `cherry-pick-<branch>` label.

```text
/cherry-pick v1.0
```

```text
/cherry-pick v1.0 v2.0
```

Behaviour depends on the PR state:

- **Unmerged PR** — the labels are added as a to-do list. When the PR is merged, the server sees them and runs the
  cherry-picks.
- **Merged PR** — the cherry-picks run immediately, one per target branch, and the labels act as an audit record of
  what was back-ported.

Branches that do not exist are reported in a comment and skipped. If a `cherry-pick-<branch>` label is already
present, that branch is skipped with a note to remove the label and run the command again to re-trigger it.

#### `/cherry-pick-retry <branch>`

Retries a cherry-pick that failed. Exactly one branch name is accepted. The command validates that:

1. the PR is merged,
2. the `cherry-pick-<branch>` label already exists (otherwise it points you at `/cherry-pick`),
3. any existing cherry-pick PR created by the bot for that branch is closed, and

then re-runs the cherry-pick.

```text
/cherry-pick-retry v1.0
```

### Workflow control

#### `/reprocess`

Re-runs the complete pull request processing workflow from scratch: labels, checks, reviewers, and so on. Merged PRs
are skipped.

```text
/reprocess
```

#### `/regenerate-welcome`

Re-renders the welcome comment using the current server and repository configuration, editing the existing welcome
comment if there is one and creating it if there is not. Useful after changing the configuration — the welcome message
documents exactly which commands are active for the repository.

```text
/regenerate-welcome
```

### Security

#### `/security-override` and `/security-override cancel`

When the security checks (`security-suspicious-paths`, `security-committer-identity`) fail, a maintainer can:

- `/security-override` — set the security check runs to **success**, annotated with the maintainer's name and a
  reminder to use `cancel` to undo it.
- `/security-override cancel` — re-run both security checks so they are evaluated again from scratch.

```text
/security-override
```

```text
/security-override cancel
```

Only maintainers may use these; anyone else gets *"Only maintainers can use `/security-override`"*. If neither security
check is enabled for the repository, the server replies *"No security checks are enabled — nothing to override."*

### Generic label commands

In addition to the commands above, the server's user-facing labels can be set and cleared directly:

```text
/verified
/verified cancel
```

Adding `cancel` removes the label instead of adding it. This is the same mechanism that powers `/wip`, `/hold`,
`/verified`, `/lgtm`, `/approve`, and `/automerge` — their permission rules are described in the tables above. Any
other label name, including `needs-rebase`, `has-conflicts`, `can-be-merged`, and `size/*`, is not accepted as a
command; those labels are managed by the server itself.
