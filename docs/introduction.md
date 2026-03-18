# Introduction

`github-webhook-server` is a self-hosted FastAPI service that receives GitHub webhooks and turns them into repository and pull request automation.

If you maintain several repositories and want one place to manage reviewer assignment, labels, checks, merge rules, cherry-picks, and release behavior, this is what the server is built for. You configure it once, connect repositories to it, and it applies the same workflow consistently across your GitHub organization.

It is not just a passive webhook receiver. On startup, it reads a central `config.yaml`, applies repository settings and labels, updates protected branch rules, resets stale in-progress checks, and creates or updates webhooks for every configured repository. After that, each incoming event is routed to the right handler for PRs, reviews, comments, checks, status updates, and tag pushes.

> **Note:** The webhook endpoint returns `200 OK` as soon as the payload is validated, then processes the event in the background. That keeps GitHub deliveries from timing out while the server clones repositories, runs checks, builds containers, or performs cherry-picks.

## What This Server Is For

This project is a good fit for:

- Teams maintaining multiple GitHub repositories and wanting one place to define automation.
- Platform, release, or DevOps engineers who want consistent labels, branch protection, and PR policy across repos.
- Projects that use `OWNERS` files and want reviewer and approver rules enforced automatically.
- Maintainers who want user-facing PR commands such as `/retest`, `/approve`, `/cherry-pick`, and `/build-and-push-container`.

## What It Automates

### Across repositories

At the repository level, the server can:

- Create or update GitHub webhooks for the events you configure per repository.
- Apply repository defaults such as delete-on-merge and auto-merge support.
- Create standard labels and colors, including review labels, merge-state labels, size labels, and cherry-pick labels.
- Configure protected branches and required status checks from your central configuration.
- Support optional release behavior such as package publishing, container builds, and Slack notifications.

### On pull requests

For pull requests, the server acts like a shared workflow layer. It can:

- Post a welcome comment when a PR opens or becomes ready for review.
- Create a tracking issue for a new PR and close it automatically when the PR is closed or merged.
- Assign reviewers from `OWNERS` files, including path-specific `OWNERS` files inside the repository.
- Add labels for PR size, target branch, merge conflicts, rebase-needed state, verification, hold/WIP state, review status, auto-merge, and cherry-pick requests.
- Queue and run built-in checks such as `tox`, `pre-commit`, `build-container`, `python-module-install`, and `conventional-title`.
- Run user-defined `custom-check-runs`, with optional checks that do not have to block merges.
- Calculate a `can-be-merged` check from approvals, status checks, blocker labels, mergeability, unresolved review conversations, and any extra required labels you configured.
- Auto-merge the PR when the `automerge` label is present and the `can-be-merged` check succeeds.

The core PR setup is explicit in the handler:

```779:857:webhook_server/libs/handlers/pull_request_handler.py
async def process_opened_or_synchronize_pull_request(self, pull_request: PullRequest) -> None:
    # Stage 1: Initial setup and check queue tasks
    setup_tasks: list[Coroutine[Any, Any, Any]] = []

    setup_tasks.append(self.owners_file_handler.assign_reviewers(pull_request=pull_request))
    setup_tasks.append(
        self.labels_handler._add_label(
            pull_request=pull_request,
            label=f"{BRANCH_LABEL_PREFIX}{pull_request.base.ref}",
        )
    )
    setup_tasks.append(self.label_pull_request_by_merge_state(pull_request=pull_request))
    setup_tasks.append(self.check_run_handler.set_check_queued(name=CAN_BE_MERGED_STR))
    # ... queue tox / pre-commit / python-module-install / build-container / verified / size ...

    ci_tasks.append(self.runner_handler.run_tox(pull_request=pull_request))
    ci_tasks.append(self.runner_handler.run_pre_commit(pull_request=pull_request))
    ci_tasks.append(self.runner_handler.run_install_python_module(pull_request=pull_request))
    ci_tasks.append(self.runner_handler.run_build_container(pull_request=pull_request))
```

In this repository's end-to-end tests, a normal PR is expected to end up with successful `build-container`, `pre-commit`, `python-module-install`, and `tox` checks; a queued `verified`; a failing `can-be-merged` until approval and policy requirements are satisfied; and labels such as `size/M` and `branch-main`.

### From comments and reviews

Contributors and maintainers can control automation directly from PR comments. In addition to label-driven commands such as `/wip`, `/hold`, `/verified`, `/lgtm`, `/approve`, and `/automerge`, the comment handler supports a set of built-in workflow commands:

```154:202:webhook_server/libs/handlers/issue_comment_handler.py
available_commands: list[str] = [
    COMMAND_RETEST_STR,
    COMMAND_REPROCESS_STR,
    COMMAND_CHERRY_PICK_STR,
    COMMAND_ASSIGN_REVIEWERS_STR,
    COMMAND_CHECK_CAN_MERGE_STR,
    BUILD_AND_PUSH_CONTAINER_STR,
    COMMAND_ASSIGN_REVIEWER_STR,
    COMMAND_ADD_ALLOWED_USER_STR,
    COMMAND_REGENERATE_WELCOME_STR,
    COMMAND_TEST_ORACLE_STR,
]

# ...

if _command not in available_commands + list(USER_LABELS_DICT.keys()):
    self.logger.debug(f"{self.log_prefix} Command {command} is not supported.")
    return
```

In practice, that means users can do things like:

- `/assign-reviewers` or `/assign-reviewer @username`
- `/retest tox`, `/retest pre-commit`, or `/retest all`
- `/reprocess` to rebuild the whole PR workflow
- `/check-can-merge` to force a mergeability recalculation
- `/build-and-push-container` to publish a PR image on demand
- `/cherry-pick <branch>` to queue or perform backports
- `/test-oracle` to request AI-generated test recommendations when configured
- `/regenerate-welcome` to refresh the onboarding comment

Reviews matter too. The server tracks review state with labels such as `approved-*`, `lgtm-*`, `changes-requested-*`, and `commented-*`, and it also understands `/approve` when it appears inside a review body.

> **Note:** In this project, `/approve` and `/lgtm` are part of the merge logic, not just convenient comments. The server converts them into labels and uses those labels when deciding whether `can-be-merged` should pass.

### On tags, releases, and backports

The automation is not limited to PRs.

On tag pushes, the server can:

- Build a Python distribution with `uv build`
- Validate and upload it to PyPI with `twine`
- Build and push release container images when `container.release: true` is set
- Send Slack notifications for successful publish or push operations

On merged PRs, it can also:

- Detect `cherry-pick-<branch>` labels
- Create cherry-pick branches and PRs automatically
- Optionally use AI to resolve cherry-pick conflicts
- Mark AI-resolved cherry-picks for manual verification instead of auto-verifying them

### Optional AI-assisted features

The server also includes optional AI integrations:

- `test-oracle` connects to an external service that analyzes a PR and recommends which tests to run.
- `ai-features` can suggest or auto-fix PR titles to match your `conventional-title` rules.
- The same `ai-features` block can enable AI-assisted cherry-pick conflict resolution.

## Configuration Model

The configuration model is layered so you can set organization-wide defaults without losing per-repository flexibility.

Settings are resolved in this order:

```132:153:webhook_server/libs/config.py
def get_value(self, value: str, return_on_none: Any = None, extra_dict: dict[str, Any] | None = None) -> Any:
    """
    Get value from config

    Supports dot notation for nested values (e.g., "docker.username", "pypi.token")

    Order of getting value:
        1. Local repository file (.github-webhook-server.yaml)
        2. Repository level global config file (config.yaml)
        3. Root level global config file (config.yaml)
    """
    if extra_dict:
        result = self._get_nested_value(value, extra_dict)
        if result is not None:
            return result

    for scope in (self.repository_data, self.root_data):
        result = self._get_nested_value(value, scope)
        if result is not None:
            return result
```

That gives you three useful layers:

- Root-level defaults in the central `config.yaml`
- Per-repository overrides inside the `repositories` map in that same file
- Repository-local overrides in `.github-webhook-server.yaml`

> **Tip:** Keep shared policy in the central `config.yaml`, then use `.github-webhook-server.yaml` only for repositories that truly need exceptions.

A real example from `examples/config.yaml` shows the kind of repository-level behavior you can enable:

```139:183:examples/config.yaml
repositories:
  my-repository:
    name: my-org/my-repository
    log-level: DEBUG # Override global log-level for repository
    log-file: my-repository.log # Override global log-file for repository
    slack-webhook-url: <Slack webhook url> # Send notification to slack on several operations
    verified-job: true

    events: # To listen to all events do not send events
      - push
      - pull_request
      - pull_request_review
      - pull_request_review_thread
      - issue_comment
      - check_run
      - status

    tox:
      main: all # Run all tests in tox.ini when pull request parent branch is main
      dev: testenv1,testenv2 # Run testenv1 and testenv2 tests in tox.ini when pull request parent branch is dev

    pre-commit: true # Run pre-commit check

    protected-branches:
      dev: []
      main: # set [] in order to set all defaults run included
        include-runs:
          - "pre-commit.ci - pr"
          - "WIP"
        exclude-runs:
          - "SonarCloud Code Analysis"

    container:
      username: <registry username>
      password: <registry_password>
      repository: <registry_repository_full_path>
      tag: <image_tag>
      release: true # Push image to registry on new release with release as the tag
```

At the top level, the example configuration also includes sections such as `labels`, `pr-size-thresholds`, `branch-protection`, `test-oracle`, and `ai-features`, so one server can apply different automation profiles to different repositories without duplicating everything.

A few especially important settings to know early:

- `webhook-ip` must be a full URL, including the `/webhook_server` path.
- `webhook-secret` enables GitHub signature verification.
- `allow-commands-on-draft-prs` controls whether slash commands are blocked or allowed on draft PRs.
- `conventional-title` validates PR titles against a Conventional Commits-style pattern.
- `set-auto-merge-prs` and `auto-verified-and-merged-users` control automatic merge behavior.
- `custom-check-runs` lets you add your own shell commands as first-class check runs.

## OWNERS-Driven Reviews

Reviewer and approver logic is path-aware. The server reads `OWNERS` files from the cloned repository, matches them against the files changed in the PR, and requests the right reviewers automatically.

The root `OWNERS` file in this repository uses the expected YAML shape:

```1:6:OWNERS
approvers:
  - myakove
  - rnetser
reviewers:
  - myakove
  - rnetser
```

Subdirectories can have their own `OWNERS` files too. When a PR touches files under those paths, the server uses those path-specific approvers and reviewers. If a path-level `OWNERS` file sets `root-approvers: false`, root approvers are not automatically required for that area.

## Operational Notes

The server also writes structured webhook logs and can expose an optional internal log viewer and log APIs for troubleshooting PR flow, status checks, and failures.

> **Warning:** If you enable the optional log viewer, keep it on a trusted network. The project treats those endpoints as internal operational tooling, not a public-facing dashboard.

Taken together, `github-webhook-server` is best understood as a shared automation layer for GitHub: contributors interact with simple PR comments and labels, while maintainers get consistent policy, repeatable release automation, and one place to operate everything.
