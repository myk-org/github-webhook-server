# Labels, Check Runs, and Mergeability

Control how pull requests are classified, verified, and merged by automating labels and check runs. By defining clear rules for PR size, required approvals, and custom checks, you can enforce quality gates before code is merged.

## Prerequisites
* A running webhook server with your base configuration.
* See [Configuration Reference](configuration-reference.html) if you need help with basic `config.yaml` setup.

## Quick Example
Here is a configuration snippet that enables built-in labels, sets up PR size categorization, and configures the `can-be-merged` status check with a minimum approval requirement:

```yaml
labels:
  enabled-labels:
    - verified
    - wip
    - can-be-merged
    - size

pr-size-thresholds:
  Small:
    threshold: 50
    color: green
  Medium:
    threshold: 150
    color: orange
  Large:
    threshold: inf
    color: red

repositories:
  my-org/my-repo:
    minimum-lgtm: 1
    verified-job: true
    branch-protection:
      required_conversation_resolution: true
```

## Step-by-Step

### 1. Enable and Configure Labels
The server provides several built-in label categories (e.g., `verified`, `hold`, `wip`, `can-be-merged`, `size`, `branch`). Reviewed-by labels (like `approved-<user>` or `lgtm-<user>`) are automatically tracked and cannot be disabled.

You can explicitly restrict which label categories the server manages:
```yaml
labels:
  enabled-labels:
    - verified
    - wip
    - needs-rebase
    - has-conflicts
    - can-be-merged
```

### 2. Define PR Size Labels
Automatically categorize pull requests based on the total number of lines changed (additions plus deletions). Define thresholds and colors globally:
```yaml
pr-size-thresholds:
  Tiny:
    threshold: 10
    color: lightgray
  Small:
    threshold: 100
    color: green
  Massive:
    threshold: inf  # 'inf' ensures all PRs larger than the previous threshold get this label
    color: darkred
```

### 3. Require Minimum LGTM (Looks Good To Me)
Enforce code review approvals by specifying a specific number of `/lgtm` sign-offs before a PR can be merged. Set this per-repository:
```yaml
repositories:
  my-org/my-repo:
    minimum-lgtm: 2
```

### 4. Enforce Conversation Resolution
Stop pull requests from merging if there are unresolved comment threads. Configure this in your branch protection rules:
```yaml
branch-protection:
  required_conversation_resolution: true
```

### 5. Configure the `can-be-merged` Check
The server acts as a gatekeeper using a special `can-be-merged` check run. Add it to your default status checks so it blocks auto-merging until all rules (LGTM, conversations, branch protection) pass:
```yaml
default-status-checks:
  - "can-be-merged"
```

## Advanced Usage

### Custom Check Runs per Branch
You can specify which continuous integration checks must pass depending on the target branch. Use `include-runs` and `exclude-runs` to customize the expectations:
```yaml
repositories:
  my-org/my-repo:
    protected-branches:
      main:
        include-runs:
          - "pre-commit.ci - pr"
          - "WIP"
        exclude-runs:
          - "SonarCloud Code Analysis"
```

### Requiring Specific Labels to Merge
You can block the `can-be-merged` check run until specific custom labels are applied to the pull request.
```yaml
repositories:
  my-org/my-repo:
    can-be-merged-required-labels:
      - "ready-for-qa"
      - "security-cleared"
```

### Customizing Label Colors
Apply specific CSS3 colors to built-in or dynamically generated labels (like `approved-` or `branch-` prefixes) globally:
```yaml
labels:
  colors:
    hold: red
    wip: orange
    can-be-merged: limegreen
    approved-: green
    lgtm-: yellowgreen
```

### Security Checks
Mandatory security checks can block the `can-be-merged` status if suspicious paths are modified or if the committer identity doesn't match the PR author. Setting `mandatory` to `true` means failures will directly impact mergeability.
```yaml
security-checks:
  mandatory: true
  suspicious-paths:
    - ".github/workflows/"
    - ".vscode/"
  committer-identity-check: true
  trusted-committers:
    - "pre-commit-ci[bot]"
```
> **Note:** The GitHub App bot and standard integration bots are automatically trusted.

### Auto-Verified Users
Bypass manual check runs for trusted automation tools. Pull requests from these users will automatically pass basic merge checks.
```yaml
auto-verified-and-merged-users:
  - "renovate[bot]"
  - "pre-commit-ci[bot]"
```

For more details on triggering label changes via comments, see [Issue Comment Commands](issue-comment-commands.html). For setting up the automation workflows around merging, see [Pull Request Automation](pull-request-automation.html).

## Related Pages

- [Pull Request Automation](pull-request-automation.html)
- [OWNERS and Reviewer Assignment](owners-and-reviewer-assignment.html)
- [Supported GitHub Events](supported-github-events.html)
