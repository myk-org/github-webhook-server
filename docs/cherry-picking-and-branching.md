Let me start by reading the knowledge graph and the pages manifest, then explore the relevant source files.

Now let me look at the config schema for branch protection, cherry-pick, and OWNERS-related settings:

Now let me look at the issue_comment handler for cherry-pick commands and the PR handler's cherry-pick sections:

Now let me look at the cherry_pick method and the auto-verify cherry-pick logic:

Now let me look at the cherry-pick retry command and the welcome message cherry-pick section:

Now let me look at the protected-branches configuration and how it connects to status checks:

Let me also check the `_prepare_merge_requirements` for OWNERS file info:

Now let me look at the ROOT_APPROVERS_KEY constant and the merge requirements with OWNERS file checking:

Let me check the approve flow to understand how OWNERS file approvers control merge:

Now I have all the information I need. Let me write the documentation page.

# Cherry-Picking and Branch Protection

Automate the process of backporting merged PRs to release branches and enforce consistent branch protection rules across your repositories, using OWNERS files to control who can approve changes.

## Prerequisites

- A running webhook server instance (see [Getting Started](quickstart.html))
- At least one repository configured in `config.yaml` (see [Configuring Repositories](configuring-repositories.html))
- A GitHub App installed on your repositories with branch protection permissions

## Quick Example

Add cherry-pick targets and branch protection to your repository configuration:

```yaml
# config.yaml
cherry-pick-assign-to-pr-author: true
auto-verify-cherry-picked-prs: true

branch-protection:
  strict: true
  required_approving_review_count: 1
  required_conversation_resolution: true

repositories:
  my-repo:
    name: my-org/my-repo
    protected-branches:
      main: []
      release-1.0: []
```

Then create an `OWNERS` file in your repository root:

```yaml
approvers:
  - alice
  - bob
reviewers:
  - charlie
  - dana
```

When a PR is merged, comment `/cherry-pick release-1.0` to backport it — or add the label before merging so it happens automatically.

## Cherry-Picking PRs to Target Branches

### Scheduling a Cherry-Pick Before Merge

Comment on an open PR to queue cherry-picks for when it merges:

```
/cherry-pick release-1.0
```

This adds a `cherry-pick-release-1.0` label to the PR. When the PR merges, the server automatically cherry-picks the merge commit to the `release-1.0` branch and opens a new PR.

You can specify multiple branches at once:

```
/cherry-pick release-1.0 release-2.0 hotfix
```

Each branch gets its own `cherry-pick-<branch>` label, and each is processed independently on merge.

### Cherry-Picking an Already-Merged PR

Comment on a merged PR to cherry-pick immediately:

```
/cherry-pick release-1.0
```

For merged PRs, the cherry-pick executes right away instead of waiting. A `cherry-pick-release-1.0` label is added to track that the operation was performed.

### What Happens During a Cherry-Pick

When a cherry-pick is triggered, the server:

1. Validates the target branch exists
2. Creates a worktree and checks out the target branch
3. Runs `git cherry-pick <merge-commit-sha>` (automatically retries with `-m 1` for merge commits)
4. Restores the original PR author on the cherry-pick commit (for DCO/sign-off compliance)
5. Runs pre-commit hooks if enabled for the repository
6. Pushes the new branch and opens a PR against the target branch
7. Labels the new PR with `CherryPicked-from-<source-branch>`
8. Assigns the original PR author and requests their review

If the cherry-pick fails due to conflicts and AI conflict resolution is not enabled (or fails), the server posts a comment with manual cherry-pick instructions:

```
**Manual cherry-pick is needed**
Cherry pick failed for abc1234 to release-1.0:
To cherry-pick run:
  git remote update
  git checkout release-1.0
  git pull origin release-1.0
  git checkout -b my-feature-release-1.0
  git cherry-pick abc1234
  # If the above fails with 'is a merge but no -m option', run:
  # git cherry-pick -m 1 abc1234
  git push origin my-feature-release-1.0
```

### Retrying a Failed Cherry-Pick

If a cherry-pick fails or the resulting PR has issues, use the retry command on the original merged PR:

```
/cherry-pick-retry release-1.0
```

This command:

- Validates the PR is merged and has the `cherry-pick-release-1.0` label
- Closes any existing failed cherry-pick PR created by the bot for that branch
- Re-runs the cherry-pick operation

> **Note:** `/cherry-pick-retry` accepts exactly one branch name. If the cherry-pick label doesn't exist on the PR, use `/cherry-pick <branch>` instead.

### Duplicate Prevention

If a `cherry-pick-<branch>` label already exists on the PR, that branch is skipped. To re-trigger a cherry-pick for a branch that was already processed, remove the label and run the command again.

### Auto-Verification of Cherry-Picked PRs

By default, cherry-picked PRs from auto-verified users are automatically marked as verified. Control this behavior globally or per-repository:

```yaml
# Global setting (default: true)
auto-verify-cherry-picked-prs: true

repositories:
  my-repo:
    name: my-org/my-repo
    # Override per repository
    auto-verify-cherry-picked-prs: false
```

When set to `false`, cherry-picked PRs require manual verification even if the original author is in the `auto-verified-and-merged-users` list.

> **Warning:** Cherry-picked PRs with AI-resolved conflicts are **never** auto-verified, regardless of this setting. The `ai-resolved-conflicts` label forces manual review.

### Cherry-Pick PR Assignment

By default, cherry-pick PRs are assigned to the original PR author. Disable this globally or per-repository:

```yaml
# Global setting (default: true)
cherry-pick-assign-to-pr-author: true

repositories:
  my-repo:
    name: my-org/my-repo
    cherry-pick-assign-to-pr-author: false
```

When the original author cannot be assigned (e.g., they don't have repository access), the server falls back to assigning the first root approver from the `OWNERS` file.

## Configuring Branch Protection

Branch protection rules are applied to every branch listed under `protected-branches` for a repository. Configure the rules at the global level, the repository level, or both (repository overrides global).

### Branch Protection Settings

```yaml
branch-protection:
  strict: true
  require_code_owner_reviews: false
  dismiss_stale_reviews: true
  required_approving_review_count: 0
  required_linear_history: true
  required_conversation_resolution: true
```

| Setting | Default | Description |
|---------|---------|-------------|
| `strict` | `true` | Require branches to be up-to-date before merging |
| `require_code_owner_reviews` | `false` | Require review from code owners |
| `dismiss_stale_reviews` | `true` | Dismiss approvals when new commits are pushed |
| `required_approving_review_count` | `0` | Minimum number of GitHub review approvals |
| `required_linear_history` | `true` | Require linear commit history (no merge commits) |
| `required_conversation_resolution` | `true` | Require all review conversations to be resolved |

> **Tip:** The `required_conversation_resolution` setting also controls whether the server checks for unresolved review threads when evaluating the `can-be-merged` check run.

### Defining Protected Branches

List the branches to protect under `protected-branches` in your repository config. Each branch can specify which status checks are required:

```yaml
repositories:
  my-repo:
    name: my-org/my-repo
    protected-branches:
      main:
        include-runs:
          - "pre-commit.ci - pr"
          - "WIP"
        exclude-runs:
          - "SonarCloud Code Analysis"
      dev: []
      release-1.0: []
```

**Three formats are supported:**

| Format | Example | Behavior |
|--------|---------|----------|
| Empty list | `dev: []` | Auto-detects status checks from repo config (tox, pre-commit, container builds, etc.) |
| Include/exclude object | `main: { include-runs: [...], exclude-runs: [...] }` | Explicitly controls which checks are required |
| Simple array | `feature: ["check1", "check2"]` | Uses only the listed checks |

When you use the empty list `[]`, the server automatically builds the required status checks from your repository configuration — if `tox` is enabled it adds `tox`, if `container` is configured it adds `build-container`, and so on. The `can-be-merged` and `verified` checks are always included by default.

### Per-Repository Branch Protection Overrides

Override global branch protection at the repository level:

```yaml
branch-protection:
  strict: true
  required_approving_review_count: 0

repositories:
  strict-repo:
    name: my-org/strict-repo
    branch-protection:
      required_approving_review_count: 2
      dismiss_stale_reviews: true
    protected-branches:
      main: []
```

Repository-level settings override global settings for the same key. Any keys not specified at the repository level fall back to the global value, then to the built-in defaults.

## Using OWNERS Files for Approval Workflows

OWNERS files define who can approve and review PRs for specific parts of your codebase. The server reads these files from the repository's base branch and uses them to enforce approval requirements.

### OWNERS File Format

Create an `OWNERS` file (YAML format) in any directory:

```yaml
approvers:
  - alice
  - bob
reviewers:
  - charlie
  - dana
```

- **Approvers** can use the `/approve` command to approve PRs touching files in that directory
- **Reviewers** are automatically assigned to PRs and can use `/lgtm`

### Directory-Scoped Ownership

Place `OWNERS` files in subdirectories to define granular ownership:

```
repo-root/
├── OWNERS              # Root approvers/reviewers (apply to all PRs)
├── api/
│   └── OWNERS          # Approvers for API changes
├── frontend/
│   └── OWNERS          # Approvers for frontend changes
└── docs/
    └── OWNERS          # Approvers for documentation
```

When a PR changes files in `api/`, the server requires approval from an approver listed in `api/OWNERS`. Root approvers (from the repository root `OWNERS`) can always approve any PR.

### Controlling Root Approver Requirements

By default, root approvers are always required in addition to directory-specific approvers. Override this by adding `root-approvers: false` to a subdirectory's `OWNERS` file:

```yaml
# api/OWNERS
approvers:
  - api-lead
reviewers:
  - api-dev
root-approvers: false
```

With `root-approvers: false`, only the approvers listed in `api/OWNERS` need to approve changes under `api/` — root approvers are not required for those files.

> **Note:** If a PR changes files in both `api/` (with `root-approvers: false`) and an unmatched directory, root approvers are still required for the unmatched files.

### How Approval Checking Works

The server evaluates approvals when determining if a PR can be merged:

1. For each changed file, the server finds the most specific `OWNERS` file
2. At least one approver from each relevant `OWNERS` file must use `/approve`
3. A root approver's `/approve` satisfies all directory requirements
4. If a reviewer uses `/lgtm`, it counts toward the `minimum-lgtm` threshold but does not count as an approval
5. Change requests from approvers block the `can-be-merged` check

### Automatic Reviewer Assignment

When a PR is opened, reviewers from all relevant `OWNERS` files are automatically assigned. You can also trigger this manually:

```
/assign-reviewers
```

Or assign a specific reviewer:

```
/assign-reviewer @username
```

### Allowed Users

The `OWNERS` file in the repository root can include an `allowed-users` list to grant command execution permissions:

```yaml
# Root OWNERS file
approvers:
  - alice
reviewers:
  - bob
allowed-users:
  - external-contributor
```

Users not in the approvers, reviewers, collaborators, or contributors lists need explicit permission via `allowed-users` or a maintainer commenting `/add-allowed-user @username` on the PR.

## Advanced Usage

### AI-Powered Cherry-Pick Conflict Resolution

When a cherry-pick encounters merge conflicts, the server can use AI to attempt automatic resolution. See [Enabling AI Features](enabling-ai-features.html) for setup details.

```yaml
ai-features:
  ai-provider: claude
  ai-model: sonnet
  resolve-cherry-pick-conflicts-with-ai:
    enabled: true
    timeout-minutes: 10
```

When AI resolves conflicts:

- The cherry-pick PR is labeled with `ai-resolved-conflicts`
- The PR is **never** auto-verified — manual review is always required
- The server logs a scope verification comparing original vs. cherry-picked file counts
- If AI resolution fails, the server falls back to posting manual cherry-pick instructions

### Combining Cherry-Pick Labels with Enabled Labels

Cherry-pick labels (`cherry-pick-<branch>` and `CherryPicked`) belong to the `cherry-pick` label category. If you use `enabled-labels` to restrict which labels are active, include `cherry-pick` to keep cherry-pick functionality working:

```yaml
labels:
  enabled-labels:
    - verified
    - cherry-pick
    - can-be-merged
```

See [Configuring Labels and PR Size Thresholds](configuring-labels-and-size.html) for full label configuration options.

### Default Status Checks

Override which status checks are added by default to all protected branches:

```yaml
# Global defaults
default-status-checks:
  - "WIP"
  - "dpulls"
  - "can-be-merged"

repositories:
  my-repo:
    name: my-org/my-repo
    # Override for this repo only
    default-status-checks:
      - "WIP"
      - "can-be-merged"
      - "ci/my-external-check"
```

These checks are combined with auto-detected checks (tox, pre-commit, etc.) when a protected branch uses the empty list `[]` format.

## Troubleshooting

**Cherry-pick label exists but cherry-pick didn't run**
Cherry-picks only execute when a PR is merged (for pre-merge labels) or immediately (for post-merge `/cherry-pick` commands). If the PR was closed without merging, cherry-pick labels are ignored. Re-open and merge the PR, or use `/cherry-pick <branch>` on the merged PR.

**"Target branch does not exist" error**
The server validates that each target branch exists before adding cherry-pick labels. Check that the branch name matches exactly (branch names are case-sensitive).

**Cherry-pick PR is not auto-verified**
Check whether `auto-verify-cherry-picked-prs` is set to `false` for the repository. Also check if the cherry-pick had AI-resolved conflicts — PRs with the `ai-resolved-conflicts` label are never auto-verified.

**Branch protection not applied to a branch**
Only branches listed under `protected-branches` in the repository config receive protection rules. The repository must also be public — private repositories skip branch protection settings.

**"/approve not accepted" from a user**
The user must be listed as an `approver` in a relevant `OWNERS` file. Reviewers can use `/lgtm` but cannot `/approve`. Check which `OWNERS` file covers the changed files and verify the username spelling.

## Related Pages

- [Managing Pull Requests](managing-pull-requests.html)
- [Enabling AI Features](enabling-ai-features.html)
- [Configuring Repositories](configuring-repositories.html)
- [Configuring Labels and PR Size Thresholds](configuring-labels-and-size.html)
- [Configuration Reference](configuration-reference.html)
