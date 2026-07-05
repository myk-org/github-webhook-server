# Issue Comment Commands

Use issue comment commands to interact with the pull request automation directly from the GitHub conversation thread. These commands allow you to trigger workflows, assign reviewers, build container images, and manage PR labels without leaving the UI.

- **Prerequisites**:
  - You must have comment access to the repository.
  - To execute commands, you must be an authorized user (a repository maintainer, an approver defined in the repository's `OWNERS` file, or explicitly granted permission in the PR).

### Quick Example

Add a comment to an open pull request containing a slash command:
```markdown
/retest lint
```
The webhook server immediately processes the command, marks the comment with a reaction, and triggers the `lint` check.

## Core Commands

### Rerunning Workflows

Use `/retest` to rerun specific checks, or `/reprocess` to run the entire PR workflow from scratch.

- **/retest \<name>**
  Reruns a specific check run. Custom checks defined in your configuration are supported.
  ```markdown
  /retest tox
  ```

- **/retest all**
  Reruns all available check runs for the PR.
  ```markdown
  /retest all
  ```

- **/reprocess**
  Forces a complete re-evaluation of the PR state. This is useful if a webhook delivery failed or if you want to completely refresh label assignments and `OWNERS` file parsing.
  ```markdown
  /reprocess
  ```

### Review and Merge Management

Use these commands to manage reviewer assignments and merge readiness.

- **/assign-reviewers**
  Automatically assigns reviewers based on the `OWNERS` files that match the changed paths.
  ```markdown
  /assign-reviewers
  ```

- **/assign-reviewer @username**
  Manually assigns a specific GitHub user as a reviewer.
  ```markdown
  /assign-reviewer @octocat
  ```

- **/check-can-merge**
  Forces an immediate recalculation of the PR's merge readiness state. Use this if an external status check updated but the system missed the event.
  ```markdown
  /check-can-merge
  ```

### Builds and External Tests

- **/build-and-push-container**
  Manually builds and pushes a container image tagged with the PR number (e.g., `pr-123`).
  ```markdown
  /build-and-push-container
  ```
  You can also pass additional build arguments:
  ```markdown
  /build-and-push-container --build-arg ENV=staging
  ```
  > **Warning:** This is different from `/retest build-container`. The retest command runs the build as a check, but `/build-and-push-container` actually publishes the image.

- **/test-oracle**
  Asks the PR Test Oracle service (if configured) to evaluate test coverage or suggest test recommendations.
  ```markdown
  /test-oracle
  ```
  > **Note:** This command runs asynchronously in the background. If the Test Oracle service is not configured, the command quietly does nothing.

## Label Management Commands

You can add or remove specific PR labels using slash commands. To remove a label, append `cancel` to the command.

| Command | Action | Remove Command | Required Permission |
|---------|--------|----------------|---------------------|
| `/wip` | Adds the `wip` label and prepends `WIP:` to the PR title. | `/wip cancel` | Valid user |
| `/hold` | Adds the `hold` label to prevent merging. | `/hold cancel` | Approver |
| `/verified` | Adds the `verified` label and marks the verified check as successful. | `/verified cancel` | Valid user |
| `/lgtm` | Adds the `lgtm-by-<user>` label indicating review approval. | `/lgtm cancel` | Reviewer or Approver |
| `/approve` | Adds the `approved-by-<user>` label for final merge approval. | `/approve cancel` | Approver |
| `/automerge` | Adds the `automerge` label to enable automatic merging. | `/automerge cancel` | Maintainer or Approver |

## Advanced Usage

### Using Multiple Commands
Commands are parsed line by line. You can execute multiple commands in a single comment by placing them on separate lines:
```markdown
/assign-reviewers
/retest all
/wip cancel
```

### Allowing Commands on Draft PRs
By default, all issue comment commands are ignored on draft PRs except `/test-oracle`. To enable other commands on draft PRs, list them in your configuration under `allow-commands-on-draft-prs`:
```yaml
allow-commands-on-draft-prs:
  - retest
  - build-and-push-container
```
> **Tip:** Use bare command names in the list without the leading slash.

### Granting Temporary Permissions
If a contributor is not a maintainer or approver, an authorized user can grant them permission to run commands on a specific PR. The maintainer must comment:
```markdown
/add-allowed-user @username
```
After this, the specified user can run commands like `/retest` or `/build-and-push-container` on that pull request. See [OWNERS and Reviewer Assignment](owners-and-reviewer-assignment.html) for more details on user roles.

## Troubleshooting

- **Command is ignored or returns "not supported"**
  Ensure the command is spelled correctly and includes any required arguments (e.g., `/retest` requires an argument like `all` or `lint`). If the PR is a draft, verify the command is allowed in your configuration.

- **"Only approvers can mark pull request with hold"**
  The user attempting to run the command does not have the required permissions. A maintainer can use `/add-allowed-user @username` to grant them access, or an existing approver must run the command instead.

## Related Pages

- [Pull Request Automation](pull-request-automation.html)
- [Supported GitHub Events](supported-github-events.html)
- [AI Features and Test Oracle](ai-features-and-test-oracle.html)
