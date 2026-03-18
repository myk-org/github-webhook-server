## What goes in an `OWNERS` file

Each `OWNERS` file applies to the directory that contains it.

The repository's own root `OWNERS` file looks like this:

```yaml
approvers:
  - myakove
  - rnetser
reviewers:
  - myakove
  - rnetser
```

Use these keys:

- `reviewers`: users who should be automatically requested for review
- `approvers`: users who can satisfy approval requirements for the affected paths
- `root-approvers: false`: optional subtree override that removes the root `OWNERS` file from the required approver set when the PR stays entirely inside that subtree

A file can define only `reviewers`, only `approvers`, both, or neither. An empty `OWNERS` file still counts as a match for that directory, but it adds no reviewers or approvers of its own.

> **Tip:** If someone should both get a review request and be allowed to approve, list them in both `reviewers` and `approvers`.

## How `OWNERS` files are discovered

For pull-request processing, the server clones the repository, checks out the PR's base branch, and walks the working tree looking for files named exactly `OWNERS`.

```python
def find_owners_files() -> list[Path]:
    return [
        p
        for p in clone_path.rglob("OWNERS")
        if not any(part.startswith(".") for part in p.relative_to(clone_path).parts)
    ]
```

That means:

- discovery is recursive
- hidden paths such as `.github/` are skipped
- the filename must be exactly `OWNERS`
- `OWNERS` data comes from the checked-out base branch, not from the PR head
- invalid YAML or invalid `approvers`/`reviewers` field types are skipped and logged
- processing stops after 1000 `OWNERS` files

Changed files are also computed locally with `git diff --name-only` between the PR base and head SHAs. The server first tries a three-dot diff and falls back to a two-dot diff if Git cannot find a merge base.

> **Warning:** A pull request that changes an `OWNERS` file does not change its own reviewer assignment or approver permissions. The server reads `OWNERS` from the base branch checkout for that PR.

## How changed files turn into reviewers and approvers

The server takes the parent directory of every changed file, then matches every `OWNERS` file whose directory is:

- the same directory, or
- an ancestor of that directory

Matching is additive. The server does not stop at the nearest `OWNERS` file.

In practice:

- a change under `folder1/file1.py` matches `folder1/OWNERS` and the root `OWNERS`
- a change under `folder/folder4/another_file.txt` matches `folder/folder4/OWNERS` and the root `OWNERS`
- a change under `folder_with_no_owners/file` falls back to the root `OWNERS`
- a change under `folder5/file` uses `folder5/OWNERS`, which in the test scenarios sets `root-approvers: false`

After matching, the server builds two pull-request-scoped sets:

- PR reviewers: the union of all matched `reviewers`
- PR approvers: the union of all matched `approvers`

Duplicates are removed, and the final lists are sorted.

### What `root-approvers: false` actually does

`root-approvers: false` is a subtree opt-out for required root approval.

If a PR stays entirely inside that subtree, the root `OWNERS` file is not added to the required approver set for that PR. If the PR also touches files outside that subtree, or paths that do not match that subtree, the root `OWNERS` file is added back.

That is why these two cases behave differently:

- `folder5/file` can be approved by `folder5` approvers without needing root approval
- `folder5/file` plus `folder_with_no_owners/file` requires root approval again

> **Note:** `root-approvers: false` removes root approval from the required set for that PR. It does not stop a root approver from approving the PR anyway.

## How review requests work

Automatic review requests use the pull request's derived `reviewers` list, not the `approvers` list.

The server requests those reviewers when a pull request is:

- opened
- reopened
- marked ready for review
- synchronized

The same automatic assignment can be rerun with `/assign-reviewers`.

A few details matter:

- the PR author is skipped, even if they appear in `reviewers`
- approvers are not automatically requested unless they also appear in `reviewers`
- reviewers are requested one at a time
- if GitHub rejects a reviewer request, the server posts a comment explaining which reviewer could not be added

There is also a manual override:

- `/assign-reviewer @username` asks GitHub to request that user directly
- the target user must be a repository contributor

## How approval and LGTM work

This project separates "LGTM" from "approver approval".

### LGTM

A normal GitHub review with state `approved` is treated like LGTM, not like approver approval. The server records that as an `lgtm-<user>` label.

The `minimum-lgtm` setting controls how many LGTM votes are required. The repository example enables it like this:

```yaml
minimum-lgtm: 2
```

LGTM counting uses:

- reviewers derived from the changed files
- root reviewers
- root approvers

The PR author does not count toward LGTM.

### Approver approval

Approver approval is driven by `/approve`, which creates an `approved-<user>` label when the commenter is allowed to approve.

A plain GitHub "Approve" review does not do that by itself. If you want a review submission to count as an approver approval, put `/approve` on its own line in the review body:

```python
if any(line.strip() == f"/{APPROVE_STR}" for line in body.splitlines()):
    await self.labels_handler.label_by_user_comment(
        pull_request=pull_request,
        user_requested_label=APPROVE_STR,
        remove=False,
        reviewed_user=reviewed_user,
    )
```

Approval works like this:

- any root approver can approve the whole PR
- otherwise, each matched `OWNERS` file needs approval from at least one of its approvers
- not every approver listed in a file has to approve; one approver from that file is enough

> **Tip:** If you rely on approver approval, tell approvers to use `/approve`, not just GitHub's standard "Approve" button.

## How `OWNERS` affects command permissions

`OWNERS` influences several slash commands, but not all of them.

### PR-scoped permissions

These depend on the current PR's derived reviewer and approver lists:

- automatic review requests use matched `reviewers`
- `/approve` works for matched PR approvers and root approvers
- `/hold` works for matched PR approvers
- LGTM counting uses matched reviewers plus root reviewers and root approvers

### Repository-wide permissions

Some commands use repository-wide roles, not just the current PR's matched paths.

A repository-wide approver here means any user listed under `approvers` in any `OWNERS` file in the repository.

Those broader rules are:

- `/automerge` works for repository maintainers and repository-wide approvers
- `/add-allowed-user @username` only takes effect when the comment author is a repository maintainer or repository-wide approver
- protected commands such as `/retest`, `/reprocess`, and `/regenerate-welcome` can be run by repository collaborators, repository contributors, repository-wide approvers, and the PR's derived reviewers

Repository maintainers are discovered from GitHub collaborator permissions. The server treats collaborators with `admin` or `maintain` permission as maintainers.

> **Note:** `/automerge` and `/add-allowed-user` are broader than PR ownership. They use repository-wide approvers, not just approvers for the files changed in the current PR.

### Temporary command access

If a user is not normally allowed to run guarded commands, a maintainer or repository-wide approver can grant access by commenting:

```text
/add-allowed-user @username
```

Only comments from a maintainer or repository-wide approver are honored for that purpose.

## Draft PR command policy

Draft PRs have an extra command gate that is configured separately from `OWNERS`.

The example global config documents it like this:

```yaml
# allow-commands-on-draft-prs: []  # Uncomment to allow all commands on draft PRs
# allow-commands-on-draft-prs:     # Or allow only specific commands:
#   - build-and-push-container
#   - retest
```

The behavior is:

- not set: commands are blocked on draft PRs
- `[]`: all commands are allowed on draft PRs
- non-empty list: only the listed commands are allowed on draft PRs

This draft-PR filter is applied in addition to the normal `OWNERS`-based permission rules.

> **Note:** Repository-specific settings can live in `.github-webhook-server.yaml` and override the global `config.yaml` values.

## Practical checklist

If reviewer assignment or approval does not behave the way you expect, check these first:

- the file is named `OWNERS`, not `owners` or `CODEOWNERS`
- the `OWNERS` file exists on the base branch, not only in the PR
- the file is not inside a hidden path
- `approvers` and `reviewers` are lists of GitHub usernames
- the PR actually touches files in the directory that the `OWNERS` file covers
- `root-approvers: false` is only used where you want to drop root approval for subtree-only changes
- approvers use `/approve` when approver approval is required
- `minimum-lgtm` matches the review policy you want

If all of those look correct, the next place to inspect is the server logs: invalid or unreadable `OWNERS` files are skipped rather than failing the whole webhook.
