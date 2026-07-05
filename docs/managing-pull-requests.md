Let me explore the repository structure and knowledge graph first.

Now let me look at the constants to understand the command strings:

Now let me look at the OWNERS file handling, the welcome message, and how permissions work:

Let me look at the rebase and welcome message functionality:

Now let me look at the welcome message to understand what users see:

Now let me check the cherry-pick and automerge sections:

Now let me look at how the `allow-commands-on-draft-prs` is configured:

Now let me look at the examples config to see PR-related settings:

Now let me check the process_command_reprocess and check_if_can_be_merged:

Let me also check the `run_retests` method to understand how retests work:

Now let me check the OWNERS file format example:

Now let me also check how `lgtm` and `approve` label commands work — the label_by_user_comment method:

Let me also check what the `current_pull_request_supported_retest` includes:

Now I have all the information I need to write the documentation. Let me compile everything into the page:

# Managing Pull Requests

Control your entire PR lifecycle — from requesting reviews and running tests to cherry-picking and merging — by posting slash commands as comments on your pull requests.

## Prerequisites

- The webhook server is running and receiving events from your repository. See [Getting Started](quickstart.html).
- Your repository is configured in `config.yaml`. See [Configuring Repositories](configuring-repositories.html).
- An `OWNERS` file exists in your repository root with approvers and reviewers defined.

## Quick Example

Post any of these as a comment on a pull request:

```
/retest tox
/cherry-pick v1.0
/approve
/rebase
/automerge
/assign-reviewers
```

The server reacts to your comment with a 👍 and processes the command immediately.

## How Commands Work

When you open a PR, the webhook server posts a **welcome comment** listing all available commands and their configuration for that repository. Use it as a quick reference.

Commands are entered as PR comments. Each line starting with `/` is treated as a separate command — you can run multiple commands in a single comment:

```
/approve
/cherry-pick release-1.0 release-2.0
```

### Who Can Run Commands

Commands require appropriate permissions. The server checks whether you are a:

- **Repository collaborator or contributor** — can run most commands
- **Approver** (listed in `OWNERS` file) — required for `/approve`, `/hold`, `/automerge`
- **Maintainer** (admin or maintain permission on the repo) — required for `/security-override`, `/rebase` on other users' PRs

If you lack permission, the server posts a comment explaining who can grant access. A maintainer or approver can authorize you by commenting:

```
/add-allowed-user @your-username
```

## Command Reference

### Review and Approval

| Command | Description | Who can use it |
|---------|-------------|----------------|
| `/lgtm` | Mark the PR as "looks good to me" — adds an `lgtm-<username>` label | Any authorized user (except the PR owner) |
| `/lgtm cancel` | Remove your LGTM | The user who gave the LGTM |
| `/approve` | Approve the PR — adds an `approved-<username>` label | Approvers only (from OWNERS file) |
| `/approve cancel` | Remove your approval | The approver who approved |

> **Note:** `/lgtm` and `/approve` are different. A PR may require a minimum number of LGTMs *and* at least one approval before it can merge. See [Configuring Repositories](configuring-repositories.html) for the `minimum-lgtm` setting.

### Testing and Retesting

Rerun checks that have failed or need refreshing:

```
/retest tox
```

| Command | Description |
|---------|-------------|
| `/retest tox` | Rerun the tox test suite |
| `/retest pre-commit` | Rerun pre-commit hooks |
| `/retest build-container` | Rebuild the container image |
| `/retest python-module-install` | Retest Python package installation |
| `/retest conventional-title` | Revalidate the PR title format |
| `/retest security-suspicious-paths` | Rerun the suspicious paths check |
| `/retest security-committer-identity` | Rerun the committer identity check |
| `/retest <custom-check-name>` | Rerun a custom check run |
| `/retest all` | Rerun all configured checks |

> **Tip:** Only checks that are configured for your repository will appear in the welcome comment. The server tells you if a requested test is not configured.

You can retest multiple specific checks at once:

```
/retest tox pre-commit
```

> **Warning:** `/retest all` cannot be combined with individual test names. Use one or the other.

For details on configuring which checks are available, see [Setting Up CI Checks](setting-up-ci-checks.html).

### Cherry-Picking

Schedule or execute cherry-picks to other branches:

```
/cherry-pick v1.0
```

**On an open (unmerged) PR:**
- Adds `cherry-pick-v1.0` labels to the PR
- When the PR merges, the server automatically cherry-picks to those branches

**On a merged PR:**
- Executes the cherry-pick immediately
- Creates a new PR targeting the specified branch

Cherry-pick to multiple branches at once:

```
/cherry-pick release-1.0 release-2.0 release-3.0
```

If a cherry-pick fails (e.g., due to conflicts), the server posts a comment with manual cherry-pick instructions. If AI conflict resolution is configured, it attempts to resolve conflicts automatically. See [Enabling AI Features](enabling-ai-features.html).

#### Retrying a Failed Cherry-Pick

If a cherry-pick failed or the resulting PR has issues, use:

```
/cherry-pick-retry release-1.0
```

This command:
1. Validates the PR is merged and has the `cherry-pick-release-1.0` label
2. Closes the existing failed cherry-pick PR (if one exists)
3. Reruns the cherry-pick to create a new PR

> **Note:** `/cherry-pick-retry` accepts exactly one branch name and only works on merged PRs. Use `/cherry-pick` for new cherry-pick requests.

For more on cherry-pick configuration and branch protection, see [Cherry-Picking and Branch Protection](cherry-picking-and-branching.html).

### Rebasing

Rebase your PR branch onto its base branch:

```
/rebase
```

The server fetches the latest base branch, rebases your PR's head branch, and force-pushes the result.

**Permission rules for rebase:**

| PR type | Who can rebase |
|---------|---------------|
| Your own PR | You or any maintainer |
| Another user's PR | Maintainers only |
| Bot-created PR (e.g., cherry-pick) | The PR assignee or maintainers |

> **Warning:** `/rebase` is not supported for fork PRs — the head branch must be in the same repository.

### Automerge

Enable automatic merging once all requirements are met:

```
/automerge
```

Only maintainers and approvers can set a PR to automerge. The PR merges automatically when:

1. At least one `/approve` from an approver
2. The required minimum number of `/lgtm` reviews (if configured)
3. All required status checks pass
4. No blocking labels (`wip`, `hold`, `has-conflicts`)
5. The `verified` label is present (if verification is required)

### PR Status Labels

| Command | Effect |
|---------|--------|
| `/wip` | Marks the PR as work-in-progress — adds the `wip` label and prepends `WIP:` to the title |
| `/wip cancel` | Removes WIP status and the title prefix |
| `/hold` | Blocks merging — approvers only |
| `/hold cancel` | Unblocks merging |
| `/verified` | Marks the PR as verified — also updates the `verified` check run |
| `/verified cancel` | Removes verification status and resets the check run |

> **Tip:** The `verified` label is automatically removed when new commits are pushed, unless the server detects the push was a clean rebase (same diff, just rebased onto the latest base).

### Reviewer Assignment

| Command | Description |
|---------|-------------|
| `/assign-reviewers` | Assigns all reviewers defined in the OWNERS file for the changed files |
| `/assign-reviewer @username` | Assigns a specific user as reviewer (must be a repository collaborator) |

### Container Builds

If container builds are configured for your repository:

```
/build-and-push-container
```

Build and push a container image tagged with the PR number. You can pass additional build arguments:

```
/build-and-push-container --build-arg KEY=value
```

See [Setting Up CI Checks](setting-up-ci-checks.html) for container build configuration.

### Other Commands

| Command | Description |
|---------|-------------|
| `/reprocess` | Reruns the entire PR workflow from scratch (useful if a webhook was missed or configuration changed) |
| `/regenerate-welcome` | Regenerates the welcome comment (useful after config changes) |
| `/check-can-merge` | Manually checks whether the PR meets all merge requirements |
| `/test-oracle` | Triggers AI-powered test recommendation analysis (see [Enabling AI Features](enabling-ai-features.html)) |

### Security Override

When security checks are configured as mandatory and are blocking your PR:

```
/security-override
```

This sets all security check runs to pass. Only **maintainers** can use this command.

To re-enable security checks after an override:

```
/security-override cancel
```

See [Enabling Security Checks](enabling-security-checks.html) for full details on security configuration.

## Advanced Usage

### Canceling Any Label Command

Append `cancel` to any label command to remove it:

```
/hold cancel
/wip cancel
/verified cancel
```

### Commands on Draft PRs

By default, all commands are blocked on draft PRs (except `/test-oracle`). You can configure which commands are allowed:

```yaml
# Allow all commands on draft PRs
allow-commands-on-draft-prs: []

# Allow only specific commands
allow-commands-on-draft-prs:
  - build-and-push-container
  - retest
```

This setting can be configured globally or per repository. If a blocked command is used on a draft PR, the server posts a comment listing which commands are allowed.

### Running Multiple Commands

You can combine multiple commands in a single comment — each `/` line is processed in parallel:

```
/approve
/cherry-pick release-1.0
/automerge
```

### Cherry-Pick Duplicate Prevention

The server tracks cherry-picks with labels. If a `cherry-pick-<branch>` label already exists on the PR, that branch is skipped. To re-trigger a cherry-pick:

1. Remove the `cherry-pick-<branch>` label from the PR
2. Run `/cherry-pick <branch>` again

Or use `/cherry-pick-retry <branch>` on merged PRs, which handles cleanup automatically.

## Troubleshooting

**Command is ignored (no reaction emoji)**
- The comment must start with `/` at the beginning of a line
- The server only processes `created` comment actions — edited or deleted comments are ignored
- Check that the webhook server is receiving `issue_comment` events. See [Configuring Repositories](configuring-repositories.html).

**"Not allowed to run commands" response**
- You need to be a collaborator, contributor, or listed in the OWNERS file
- Ask a maintainer to comment `/add-allowed-user @your-username` on the PR

**"/retest <name>" says the test is not configured**
- Only checks enabled in your repository configuration can be retested. Check the welcome comment for the list of available retests.
- See [Setting Up CI Checks](setting-up-ci-checks.html) for how to configure checks.

**Cherry-pick fails with conflicts**
- The server posts manual cherry-pick instructions in a comment
- If AI features are configured, the server attempts automatic conflict resolution first. See [Enabling AI Features](enabling-ai-features.html).
- Use `/cherry-pick-retry <branch>` after fixing the issue

**"/rebase" rejected on a fork PR**
- Rebase only works when the PR's head branch is in the same repository. Fork PRs must be rebased locally.

For copy-paste command examples covering common workflows, see [PR Comment Command Recipes](pr-command-recipes.html).

## Related Pages

- [PR Comment Command Recipes](pr-command-recipes.html)
- [Cherry-Picking and Branch Protection](cherry-picking-and-branching.html)
- [Setting Up CI Checks](setting-up-ci-checks.html)
- [Enabling AI Features](enabling-ai-features.html)
- [Configuring Repositories](configuring-repositories.html)
