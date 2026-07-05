Let me explore the repository structure and understand the codebase first.

Now let me look at the constants to understand the exact command strings:

Now let me look at the runner handler for understanding retest, rebase, cherry-pick, and container build:

Now let me check the `current_pull_request_supported_retest` to understand what tests are retestable:

Now let me look at the welcome message builder to see what commands it displays:

Now let me look at the merge requirements and automerge sections in the welcome message:

Let me check the retest welcome comment and the add-allowed-user/test-oracle commands:

Let me see how the retest welcome comment is built:

Now let me check the `is_user_valid_to_run_commands` to understand command permissions:

Let me now check the security-override section and the merge-requirements section:

Now let me also check if there are any `allow-commands-on-draft-prs` details and check the test-oracle command:

Now let me look at the build-and-push-container command args to understand what extra args are supported:

Let me also check for multi-command support (multiple commands in a single comment):

Good - multiple commands in a single comment are parsed line by line. Now let me look at the examples config to understand what's available:

Now I have a thorough understanding of all PR comment commands. Let me also check the custom check runs configuration:

Now I have all the information I need. Let me write the complete documentation page:

# PR Comment Command Recipes

Copy-paste PR comment commands for common workflows. Post these as comments on any pull request to trigger server actions.

> **Note:** Commands must start with `/` at the beginning of a line. You can combine multiple commands in a single comment — one per line. The server reacts with 👍 to acknowledge each command.

## Re-run a Single Failing Check

Re-trigger a specific check that failed without pushing a new commit.

```
/retest tox
```

Replace `tox` with the exact check name. Built-in check names are: `tox`, `build-container`, `pre-commit`, `python-module-install`, `conventional-title`, `security-suspicious-paths`, `security-committer-identity`. Custom check names match the `name` field in your `custom-check-runs` config.

> **Tip:** The welcome message on your PR lists every retestable check for that repository.

## Re-run All Checks

Re-trigger every configured check for the PR at once.

```
/retest all
```

This runs all checks in parallel — tox, pre-commit, container build, custom checks, and security checks (whichever are configured for the repository).

> **Warning:** `/retest all` cannot be combined with individual check names. Use either `all` or specific names — not both.

## Re-run Multiple Specific Checks

Re-trigger only the checks you need.

```
/retest tox pre-commit
```

List check names separated by spaces. Any unrecognized name will be reported back as a comment on the PR.

## Cherry-Pick to a Release Branch

Schedule an automatic cherry-pick to a target branch when the PR merges.

```
/cherry-pick v1.0
```

This adds a `cherry-pick-v1.0` label to the PR. When the PR is merged, the server automatically cherry-picks the merge commit to the `v1.0` branch and opens a new PR.

- If the target branch does not exist, the server posts an error comment.
- If the PR is already merged, the cherry-pick executes immediately.
- If AI conflict resolution is configured, merge conflicts are resolved automatically. See [Enabling AI Features](enabling-ai-features.html) for setup.

## Cherry-Pick to Multiple Branches

Cherry-pick to several release branches in one command.

```
/cherry-pick v1.0 v2.0 release-3.x
```

Each branch gets its own `cherry-pick-<branch>` label and its own cherry-pick PR after merge.

## Retry a Failed Cherry-Pick

Re-run a cherry-pick that previously failed (closes the old cherry-pick PR and creates a new one).

```
/cherry-pick-retry v1.0
```

This only works on **merged PRs** where the `cherry-pick-v1.0` label already exists. It closes any existing failed cherry-pick PR created by the bot and retries the operation.

- Only accepts one branch name at a time.
- To cherry-pick to a new branch (no existing label), use `/cherry-pick <branch>` instead.

## Rebase a PR onto Its Base Branch

Rebase the PR branch onto the latest base branch and force-push.

```
/rebase
```

The server checks out the PR branch, rebases it onto `origin/<base-branch>`, and force-pushes with `--force-with-lease`. If conflicts arise, the rebase is aborted and the server posts the error output.

- Only the **PR owner** or **maintainers** can rebase user-owned PRs.
- For bot-owned PRs (e.g., cherry-pick PRs), only the **PR assignee** or **maintainers** can rebase.
- Fork PRs cannot be rebased (the head branch is in a different repository).

## Override Security Checks

Force security check runs to pass when you've reviewed the flagged changes (maintainers only).

```
/security-override
```

This sets both `security-suspicious-paths` and `security-committer-identity` check runs to success. Only repository maintainers can use this command.

> **Warning:** This bypasses security gates. Only use after manually verifying the flagged file changes or committer identity are legitimate.

## Re-enable Security Checks After Override

Remove a previous security override and re-run security checks.

```
/security-override cancel
```

This re-evaluates the PR against the configured suspicious paths and committer identity rules, restoring the original check results.

## Trigger a Container Build and Push

Build a container image from the PR and push it to the configured registry.

```
/build-and-push-container
```

The image is tagged with the PR number. The server posts a comment with the published image tag on success.

- Requires `container` to be configured for the repository. See [Setting Up CI Checks](setting-up-ci-checks.html) for configuration details.
- You can pass additional podman build arguments:

```
/build-and-push-container --no-cache
```

## Approve a PR

Mark the PR as approved (approvers and maintainers only).

```
/approve
```

This adds the `approved-<username>` label and triggers the can-be-merged evaluation. If a test oracle is configured, it also runs automatically on approval. See [Enabling AI Features](enabling-ai-features.html) for test oracle setup.

## LGTM — Looks Good to Me

Add a lightweight review signal without full approval.

```
/lgtm
```

Adds the `lgtm-<username>` label. Some repositories require a minimum number of LGTMs before a PR can be merged (configured via `minimum-lgtm`).

## Enable Auto-Merge

Automatically merge the PR once all requirements are met (maintainers and approvers only).

```
/automerge
```

The server continuously evaluates merge requirements (approval, status checks, no blockers) and merges the PR when everything passes. See [Managing Pull Requests](managing-pull-requests.html) for details on merge requirements.

## Mark PR as Work in Progress

Block the PR from being merged and prefix the title with `WIP:`.

```
/wip
```

To remove WIP status and restore the original title:

```
/wip cancel
```

## Put a PR on Hold

Block merging without changing the title (approvers only).

```
/hold
```

To release the hold:

```
/hold cancel
```

## Mark PR as Verified

Add the `verified` label and set the verified check run to success.

```
/verified
```

To remove verification (resets the check run to queued):

```
/verified cancel
```

> **Note:** The `verified` label is automatically removed when new commits are pushed, unless the server detects a clean rebase.

## Check Merge Readiness

Ask the server to evaluate whether the PR meets all merge requirements.

```
/check-can-merge
```

The server checks approval status, required checks, labels, and conflicts, then updates the `can-be-merged` check run accordingly.

## Assign Reviewers from OWNERS File

Assign reviewers automatically based on the repository's OWNERS file.

```
/assign-reviewers
```

## Assign a Specific Reviewer

Request a review from a specific collaborator.

```
/assign-reviewer @alice
```

The `@` prefix is optional — `/assign-reviewer alice` works too. The user must be a repository collaborator.

## Grant Command Access to a Non-Collaborator

Allow an external contributor to run commands on this PR.

```
/add-allowed-user @contributor-name
```

This must be posted by a maintainer or approver. After this, the named user can run commands like `/retest` on the PR.

## Re-trigger PR Processing

Force the server to reprocess the entire PR workflow from scratch.

```
/reprocess
```

Useful when a webhook delivery failed or when the server configuration changed after the PR was opened.

> **Note:** This only works on **open** PRs.

## Regenerate the Welcome Message

Update the automated welcome comment to reflect current configuration.

```
/regenerate-welcome
```

Use this after changing OWNERS files, label configuration, or enabled features so the welcome comment shows accurate information.

## Run the Test Oracle

Trigger an AI-powered analysis of PR changes to recommend which tests to run.

```
/test-oracle
```

Requires test oracle configuration. See [Enabling AI Features](enabling-ai-features.html) for setup. This is the only command allowed on draft PRs by default.

## Combine Multiple Commands

Execute several actions in a single PR comment — one command per line.

```
/retest tox
/retest pre-commit
/verified
/cherry-pick v1.0 v2.0
```

All commands run in parallel. Each command gets its own 👍 reaction.

## Allow Specific Commands on Draft PRs

By default, all commands except `/test-oracle` are blocked on draft PRs. Configure allowed commands in your `config.yaml` to change this behavior.

To allow all commands on draft PRs, add to your repository config:

```yaml
allow-commands-on-draft-prs: []
```

To allow only specific commands:

```yaml
allow-commands-on-draft-prs:
  - build-and-push-container
  - retest
```

See [Configuring Repositories](configuring-repositories.html) for full configuration options.

## Quick Reference

| Command | Arguments | Who Can Run | Works on Draft? |
|---|---|---|---|
| `/retest` | `<check> [check2...]` or `all` | Collaborators, contributors, approvers | No* |
| `/cherry-pick` | `<branch> [branch2...]` | Approvers, maintainers | No* |
| `/cherry-pick-retry` | `<branch>` | Approvers, maintainers | No* |
| `/rebase` | — | PR owner, maintainers | No* |
| `/build-and-push-container` | Optional build args | Collaborators with permission | No* |
| `/approve` | — | Approvers only | No* |
| `/lgtm` | — | Anyone with access | No* |
| `/automerge` | — | Maintainers, approvers | No* |
| `/wip` | Optional: `cancel` | Collaborators, contributors, approvers | No* |
| `/hold` | Optional: `cancel` | Approvers only | No* |
| `/verified` | Optional: `cancel` | Collaborators, contributors, approvers | No* |
| `/check-can-merge` | — | Collaborators, contributors, approvers | No* |
| `/assign-reviewers` | — | Collaborators, contributors, approvers | No* |
| `/assign-reviewer` | `@username` | Collaborators, contributors, approvers | No* |
| `/add-allowed-user` | `@username` | Maintainers, approvers | No* |
| `/security-override` | Optional: `cancel` | Maintainers only | No* |
| `/reprocess` | — | Collaborators, contributors, approvers | No* |
| `/regenerate-welcome` | — | Collaborators, contributors, approvers | No* |
| `/test-oracle` | — | Collaborators, contributors, approvers | **Yes** |

*\* Blocked on draft PRs unless configured via `allow-commands-on-draft-prs`. See [Configuring Repositories](configuring-repositories.html).*

## Related Pages

- [Managing Pull Requests](managing-pull-requests.html)
- [Setting Up CI Checks](setting-up-ci-checks.html)
- [Cherry-Picking and Branch Protection](cherry-picking-and-branching.html)
- [Enabling Security Checks](enabling-security-checks.html)
- [Configuring Repositories](configuring-repositories.html)
