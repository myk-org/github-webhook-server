# OWNERS and Reviewer Assignment

Define project ownership by placing `OWNERS` files in your repository directories. The server uses these files to automatically request reviews, define who can approve a pull request, and control who can run automation commands.

## Prerequisites

- A repository configured to use the webhook server. See [Installation](installation.html).

## Quick Example

Place an `OWNERS` file in the root of your repository (or any sub-directory) with this YAML format:

```yaml
approvers:
  - octocat
  - admin-user
reviewers:
  - qa-tester
  - frontend-dev
```

When a pull request is opened or updated, the server automatically requests reviews from `qa-tester` and `frontend-dev`. It also grants all listed users the permission to run automation commands on the pull request.

## How It Works

### 1. Discovery and File Resolution

When a pull request modifies files, the server looks at the directory path of every changed file. It searches for an `OWNERS` file in that exact directory and walks up the directory tree until it reaches the repository root, aggregating ownership data along the way.

### 2. Reviewer Assignment

The server collects the `reviewers` list from the applicable `OWNERS` files for all modified paths. It then automatically assigns them as reviewers on the pull request.

> **Note:** The author of the pull request is never assigned as a reviewer, even if listed in the `OWNERS` file.

### 3. Command Permissions

By default, the server restricts who can trigger automation commands (like `/retest` or `/check-can-merge`). See [Issue Comment Commands](issue-comment-commands.html) for a full list of commands.

The following users are automatically permitted to run commands:
- Repository collaborators and contributors
- Any `approvers` listed in any `OWNERS` file anywhere in the repository
- Any `reviewers` derived specifically from the pull request's changed files

## Advanced Usage

### Directory-Specific Ownership

You can define fine-grained ownership for specific components by placing `OWNERS` files in subdirectories.

For example, an `OWNERS` file in `frontend/src/` applies only to files modified within that directory. By default, the server also inherits approvers and reviewers from the root `OWNERS` file.

### Disabling Root Inheritance

If a subdirectory has strict ownership and you do not want root approvers to be automatically included, set `root-approvers: false` in the subdirectory's `OWNERS` file:

```yaml
approvers:
  - strict-component-lead
reviewers:
  - component-reviewer
root-approvers: false
```

When a file in this subdirectory is modified, the server will ignore the repository's root `OWNERS` file for those specific changes.

### Authorizing External Users

If an external contributor who is not in the `OWNERS` file or a repository collaborator tries to run a command, the server will reject it and post a comment explaining the restriction.

Repository maintainers or global approvers can temporarily authorize that user on the pull request by commenting:

```text
/add-allowed-user @username
```

Once authorized, the external user can freely run commands like `/retest` on that specific pull request.

## Troubleshooting

- **Reviewers are not assigned:** Ensure your `OWNERS` file is valid YAML and the usernames match their exact GitHub login names.
- **Commands are denied for approvers:** The GitHub API checks contributor and collaborator status. If the user was recently added, they may not be recognized immediately.
- **Root approvers are unexpectedly missing:** Check if a parent directory has an `OWNERS` file with `root-approvers: false` configured.

## Related Pages

- [Pull Request Automation](pull-request-automation.html)
- [Repository Overrides](repository-overrides.html)
- [Labels, Check Runs, and Mergeability](labels-check-runs-and-mergeability.html)
