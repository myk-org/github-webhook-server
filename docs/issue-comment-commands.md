# Issue Comment Commands

Issue comment commands let you control pull request automation directly from the PR conversation.

> **Note:** Only newly created comments are processed. Editing or deleting a comment later does not re-run a command.

> **Tip:** Put each command at the start of its own line. If you include several commands in one comment, the server parses each `/...` line separately and runs them concurrently.

```92:115:webhook_server/libs/handlers/issue_comment_handler.py
_user_commands: list[str] = [_cmd.strip("/") for _cmd in body.strip().splitlines() if _cmd.startswith("/")]

user_login: str = self.hook_data["sender"]["login"]

# Execute all commands in parallel
if _user_commands:
    # Cache draft status once to avoid repeated API calls
    is_draft = await asyncio.to_thread(lambda: pull_request.draft)

    tasks: list[Coroutine[Any, Any, Any] | Task[Any]] = []
    for user_command in _user_commands:
        task = asyncio.create_task(
            self.user_commands(
                pull_request=pull_request,
                command=user_command,
                reviewed_user=user_login,
                issue_comment_id=self.hook_data["comment"]["id"],
                is_draft=is_draft,
            )
        )
        tasks.append(task)

    # Execute all commands concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)
```

## Permissions

The server has two different permission styles:

- Some commands are open to anyone who can comment on the PR.
- Some commands use the "valid command runner" check.

A valid command runner is any user in one of these groups:

- Repository collaborators
- Repository contributors
- Repository approvers from `OWNERS`
- Reviewers resolved for the current PR from `OWNERS`
- A user explicitly approved by a maintainer or repository approver with `/add-allowed-user @username`

In this project, a "maintainer" is a repository collaborator with GitHub `admin` or `maintain` permission.

```469:515:webhook_server/libs/handlers/owners_files_handler.py
_allowed_user_to_approve = await self.get_all_repository_maintainers() + self.all_repository_approvers
allowed_user_to_approve = list(set(_allowed_user_to_approve))
allow_user_comment = f"/{COMMAND_ADD_ALLOWED_USER_STR} @{reviewed_user}"

comment_msg = f"""
{reviewed_user} is not allowed to run retest commands.
maintainers can allow it by comment `{allow_user_comment}`
Maintainers:
 - {"\n - ".join(allowed_user_to_approve)}
"""
valid_users = await self.valid_users_to_run_commands

# ...

return set((
    *repository_collaborators,
    *repository_contributors,
    *self.all_repository_approvers,
    *self.all_pull_request_reviewers,
))
```

| Command | Who can use it | Notes |
| --- | --- | --- |
| `/retest <name>` | Valid command runner | Same model is used for `/reprocess` and `/build-and-push-container` |
| `/reprocess` | Valid command runner | Skips merged PRs |
| `/build-and-push-container` | Valid command runner | Requires container support to be configured |
| `/assign-reviewers` | No additional role check | Still subject to draft-PR rules |
| `/assign-reviewer @username` | No additional role check | Target user must already be a repository contributor |
| `/check-can-merge` | No additional role check | Still subject to draft-PR rules |
| `/test-oracle` | No additional role check | Also allowed on draft PRs |
| `/wip` and `/wip cancel` | No additional role check | Only works when the `wip` label category is enabled |
| `/verified` and `/verified cancel` | No additional role check | Only works when the `verified` label category is enabled |
| `/hold` and `/hold cancel` | PR approvers for the current PR | Uses approvers resolved from `OWNERS` |
| `/approve` and `/approve cancel` | PR approvers or root approvers | Also affects Test Oracle auto-triggers |
| `/lgtm` and `/lgtm cancel` | No additional role check | The PR author's own `/lgtm` is ignored |
| `/automerge` | Repository maintainers or repository approvers | Adds the `automerge` label; there is no dedicated `/automerge cancel` issue-comment path |
| `/add-allowed-user @username` | Must be posted by a maintainer or repository approver to actually grant access | The later permission check only trusts approval comments from those users |

### Draft PRs

> **Warning:** On draft PRs, every issue comment command except `/test-oracle` is blocked unless you allow it with `allow-commands-on-draft-prs`.

```38:45:examples/config.yaml
# Commands allowed on draft PRs (optional)
# If not set: commands are blocked on draft PRs (default behavior)
# If empty list []: all commands allowed on draft PRs
# If list with values: only those commands allowed on draft PRs
# allow-commands-on-draft-prs: []  # Uncomment to allow all commands on draft PRs
# allow-commands-on-draft-prs:     # Or allow only specific commands:
#   - build-and-push-container
#   - retest
```

Use bare command names in this list, without the leading slash. For example, use `retest`, not `/retest`.

If the setting is omitted entirely, blocked commands on a draft PR are simply not run. If the setting is present and a command is not in the allowlist, the bot replies with the list of allowed commands.

## Command reference

### `/retest <name>`

Use `/retest` to rerun one or more configured checks for the current pull request.

Supported built-in retest names come from the repository's enabled features:

- `tox`
- `build-container`
- `python-module-install`
- `pre-commit`
- `conventional-title`

Custom retests come from `custom-check-runs`, and the command name is the configured `name` exactly. If you define a custom check named `lint`, you rerun it with `/retest lint`.

```360:388:webhook_server/libs/handlers/pull_request_handler.py
if self.github_webhook.tox:
    retest_msg += f" * `/retest {TOX_STR}` - Run Python test suite with tox\n"

if self.github_webhook.build_and_push_container:
    retest_msg += f" * `/retest {BUILD_CONTAINER_STR}` - Rebuild and test container image\n"

if self.github_webhook.pypi:
    retest_msg += f" * `/retest {PYTHON_MODULE_INSTALL_STR}` - Test Python package installation\n"

if self.github_webhook.pre_commit:
    retest_msg += f" * `/retest {PRE_COMMIT_STR}` - Run pre-commit hooks and checks\n"

if self.github_webhook.conventional_title:
    retest_msg += f" * `/retest {CONVENTIONAL_TITLE_STR}` - Validate commit message format\n"

# Add custom check runs (both mandatory and optional)
for custom_check in self.github_webhook.custom_check_runs:
    check_name = custom_check["name"]
    is_mandatory = custom_check.get("mandatory", True)
    status_indicator = "" if is_mandatory else " (optional)"
    retest_msg += f" * `/retest {check_name}` - {check_name}{status_indicator}\n"

if retest_msg:
    retest_msg += " * `/retest all` - Run all available tests\n"
```

```580:593:webhook_server/config/schema.yaml
custom-check-runs:
  type: array
  description: |
    Custom check runs that execute user-defined commands on PR events.
    Commands run in the repository worktree and behave like built-in checks
    (tox, pre-commit, etc.) - if a command is not found, the check will fail.

    Examples:
      - name: lint
        command: uv tool run --from ruff ruff check
        mandatory: true
      - name: security-scan
        command: TOKEN=xyz DEBUG=true uv tool run --from bandit bandit -r .
        mandatory: false
```

Behavior to know:

- `/retest all` runs every configured retest target, including optional custom checks.
- `/retest all` cannot be combined with other names in the same command.
- If you request a mix of supported and unsupported names, the supported ones still run and the bot comments about the unsupported ones.
- `/retest` requires an argument.

```465:521:webhook_server/libs/handlers/issue_comment_handler.py
if not _target_tests:
    msg = "No test defined to retest"
    await asyncio.to_thread(pull_request.create_issue_comment, msg)
    return

if "all" in command_args:
    if len(_target_tests) > 1:
        msg = "Invalid command. `all` cannot be used with other tests"
        await asyncio.to_thread(pull_request.create_issue_comment, msg)
        return
    else:
        _supported_retests = self.github_webhook.current_pull_request_supported_retest
else:
    for _test in _target_tests:
        if _test in self.github_webhook.current_pull_request_supported_retest:
            _supported_retests.append(_test)
        else:
            _not_supported_retests.append(_test)

if _not_supported_retests:
    msg = f"No {' '.join(_not_supported_retests)} configured for this repository"
    await asyncio.to_thread(pull_request.create_issue_comment, msg)

if _supported_retests:
    await self.runner_handler.run_retests(
        supported_retests=_supported_retests,
        pull_request=pull_request,
    )
```

> **Note:** `/retest build-container` rebuilds and tests the image as a check run. It does **not** push a container image. Use `/build-and-push-container` when you want the image published.

### `/reprocess`

Use `/reprocess` to rerun the main PR workflow for an existing open pull request.

It is useful when:

- A webhook failed partway through processing
- `OWNERS` changed and you want reviewer assignment recalculated
- Repository config changed and you want the PR automation refreshed

```1455:1500:webhook_server/libs/handlers/pull_request_handler.py
async def process_new_or_reprocess_pull_request(self, pull_request: PullRequest) -> None:
    """Process a new or reprocessed PR - handles welcome message, tracking issue, and full workflow.

    This method extracts the core logic from the "opened" event handler to make it reusable
    for both new PRs and the /reprocess command. It includes duplicate prevention checks.
    """
    tasks: list[Coroutine[Any, Any, Any]] = []

    # Add welcome message if it doesn't exist yet
    if not await self._welcome_comment_exists(pull_request=pull_request):
        welcome_msg = self._prepare_welcome_comment()
        tasks.append(asyncio.to_thread(pull_request.create_issue_comment, body=welcome_msg))
    else:
        self.logger.info(f"{self.log_prefix} Welcome message already exists, skipping")

    # Add tracking issue if it doesn't exist yet
    if not await self._tracking_issue_exists(pull_request=pull_request):
        tasks.append(self.create_issue_for_new_pull_request(pull_request=pull_request))
    else:
        self.logger.info(f"{self.log_prefix} Tracking issue already exists, skipping")

    # Always run these tasks
    tasks.append(self.set_wip_label_based_on_title(pull_request=pull_request))
    tasks.append(self.process_opened_or_synchronize_pull_request(pull_request=pull_request))

async def process_command_reprocess(self, pull_request: PullRequest) -> None:
    """Handle /reprocess command - triggers full PR workflow from scratch."""
    # Check if PR is already merged - skip if merged
    if await asyncio.to_thread(lambda: pull_request.is_merged()):
        return

    await self.process_new_or_reprocess_pull_request(pull_request=pull_request)
```

What `/reprocess` does:

- Re-runs the same core workflow used for new or synchronized PRs
- Recreates the welcome comment only if it is missing
- Recreates the tracking issue only if it is missing
- Reapplies WIP-from-title handling
- Re-runs the main opened/synchronize PR processing flow
- Skips merged PRs entirely

> **Tip:** Use `/regenerate-welcome` if you only want to refresh the welcome comment itself.

### `/assign-reviewers`

Use `/assign-reviewers` to assign reviewers from the `OWNERS` files that match the paths changed in the pull request.

```442:466:webhook_server/libs/handlers/owners_files_handler.py
async def assign_reviewers(self, pull_request: PullRequest) -> None:
    self._ensure_initialized()

    _to_add: list[str] = list(set(self.all_pull_request_reviewers))

    if not _to_add:
        return

    for reviewer in _to_add:
        if reviewer != pull_request.user.login:
            try:
                await asyncio.to_thread(pull_request.create_review_request, [reviewer])
            except GithubException as ex:
                await asyncio.to_thread(
                    pull_request.create_issue_comment, f"{reviewer} can not be added as reviewer. {ex}"
                )
```

Key points:

- Reviewers are derived from matching `OWNERS` files for the current PR.
- The PR author is skipped.
- There is no extra role check beyond being able to comment on the PR.
- The command is still subject to the draft-PR rules described earlier.

### `/assign-reviewer @username`

Use `/assign-reviewer @username` to request one specific reviewer.

```373:386:webhook_server/libs/handlers/issue_comment_handler.py
async def _add_reviewer_by_user_comment(self, pull_request: PullRequest, reviewer: str) -> None:
    reviewer = reviewer.strip("@")
    repo_contributors = list(await asyncio.to_thread(self.repository.get_contributors))

    for contributer in repo_contributors:
        if contributer.login == reviewer:
            await asyncio.to_thread(pull_request.create_review_request, [reviewer])
            return

    _err = f"not adding reviewer {reviewer} by user comment, {reviewer} is not part of contributers"
    await asyncio.to_thread(pull_request.create_issue_comment, _err)
```

Key points:

- The leading `@` is optional; the command strips it before lookup.
- The target user must already be in the repository contributors list.
- If the user is not a contributor, the bot comments instead of assigning them.
- The command requires an argument.

### `/check-can-merge`

Use `/check-can-merge` to recalculate merge readiness immediately.

The command updates the `can-be-merged` check run and label based on the current PR state. In the current implementation, that evaluation includes:

- Whether the PR is mergeable
- Required checks and commit statuses
- In-progress required checks
- Blocking labels such as `wip` and `hold`
- Extra labels configured in `can-be-merged-required-labels`
- Unresolved review conversations when conversation resolution is required
- Approval and LGTM requirements

```1164:1274:webhook_server/libs/handlers/pull_request_handler.py
async def check_if_can_be_merged(self, pull_request: PullRequest) -> None:
    """
    Check if PR can be merged and set the job for it

    Check the following:
        None of the required status checks in progress.
        Has verified label.
        Has approved from one of the approvers.
        All required run check passed.
        PR status is not 'dirty'.
        PR has no changed requests from approvers.
    """
    # ...
    labels_failure_output = self.labels_handler.wip_or_hold_labels_exists(labels=_labels)
    # ...
    labels_failure_output = self._check_labels_for_can_be_merged(labels=_labels)
    # ...
    if self.github_webhook.required_conversation_resolution and _unresolved_threads:
        conversation_failure = f"PR has {len(_unresolved_threads)} unresolved review conversation(s):\n"
    # ...
    pr_approvered_failure_output = await self._check_if_pr_approved(labels=_labels)
    # ...
    if not failure_output:
        await self.labels_handler._add_label(pull_request=pull_request, label=CAN_BE_MERGED_STR)
        await self.check_run_handler.set_check_success(name=CAN_BE_MERGED_STR)
```

If the PR passes, the bot adds the `can-be-merged` label and marks the check successful. If it does not pass, the bot removes that label and publishes the failure reasons in the check output.

Repository-specific required labels are configured like this:

```193:195:examples/config.yaml
can-be-merged-required-labels: # check for extra labels to set PR as can be merged
  - my-label1
  - my-label2
```

### `/build-and-push-container`

Use `/build-and-push-container` to manually build **and push** a PR container image.

The command only works when container support is configured for the repository. Otherwise, the bot replies that no `build-and-push-container` is configured.

```400:405:webhook_server/libs/handlers/pull_request_handler.py
if self.github_webhook.build_and_push_container:
    return """
#### Container Operations
* `/build-and-push-container` - Build and push container image (tagged with PR number)
  * Supports additional build arguments: `/build-and-push-container --build-arg KEY=value`
```

```172:183:examples/config.yaml
container:
  username: <registry username>
  password: <registry_password>
  repository: <registry_repository_full_path>
  tag: <image_tag>
  release: true # Push image to registry on new release with release as the tag
  build-args: # build args to send to podman build command
    - my-build-arg1=1
    - my-build-arg2=2
  args: # args to send to podman build command
    - --format docker
```

For PR comment builds, the image tag is derived from the PR number:

```1111:1127:webhook_server/libs/github_api.py
def container_repository_and_tag(
    self, is_merged: bool = False, tag: str = "", pull_request: PullRequest | None = None
) -> str | None:
    if not tag:
        if not pull_request:
            return None

        if is_merged:
            pull_request_branch = pull_request.base.ref
            tag = (
                pull_request_branch
                if pull_request_branch not in (OTHER_MAIN_BRANCH, "main")
                else self.container_tag
            )
        else:
            tag = f"pr-{pull_request.number}"
```

What to expect:

- The command uses the repository's configured `container` settings.
- Extra arguments after the command are passed through to the build invocation.
- Default `container.build-args` and `container.args` from config are applied too.
- On success, the bot comments that a new container was published.
- On push failure, the bot comments that the build-and-push failed.

> **Warning:** This is different from `/retest build-container`. The retest command runs the build as a check; `/build-and-push-container` publishes an image.

### `/test-oracle`

Use `/test-oracle` to ask the configured PR Test Oracle service for test recommendations.

The direct issue comment command always works when `test-oracle` is configured. The `triggers` list controls automatic background runs, not the direct `/test-oracle` command.

```112:124:examples/config.yaml
# PR Test Oracle integration
# Analyzes PR diffs with AI and recommends which tests to run
# See: https://github.com/myk-org/pr-test-oracle
test-oracle:
  server-url: "http://localhost:8000"
  ai-provider: "claude" # claude | gemini | cursor
  ai-model: "claude-opus-4-6[1m]"
  test-patterns:
    - "tests/**/*.py"
  triggers: # Default: [approved]
    - approved # Run when /approve command is used
    # - pr-opened             # Run when PR is opened
    # - pr-synchronized       # Run when new commits pushed
```

```17:63:webhook_server/libs/test_oracle.py
async def call_test_oracle(
    github_webhook: GithubWebhook,
    pull_request: PullRequest,
    trigger: str | None = None,
) -> None:
    """Call the pr-test-oracle service to analyze a PR for test recommendations.

    Args:
        trigger: The event trigger (e.g., "approved", "pr-opened").
                 "approved" means the /approve command, not GitHub review state.
                 None means command-triggered (always runs if configured).
    """
    config: dict[str, Any] | None = github_webhook.config.get_value("test-oracle")
    if not config:
        return

    if trigger is not None:
        triggers: list[str] = config.get("triggers", DEFAULT_TRIGGERS)
        if trigger not in triggers:
            return

    server_url: str = config["server-url"]

    # Health check
    try:
        health_response = await client.get("/health", timeout=5.0)
        health_response.raise_for_status()
    except httpx.HTTPError as e:
        await asyncio.to_thread(
            pull_request.create_issue_comment,
            f"Test Oracle server is not responding{status_info}, skipping test analysis",
        )
        return
```

Behavior to know:

- If `test-oracle` is not configured, `/test-oracle` quietly does nothing.
- If the Oracle server fails its health check, the bot comments on the PR.
- If the analyze call fails after the health check passes, the error is logged but no PR comment is posted.
- `/test-oracle` runs asynchronously in the background.
- `/test-oracle` is the only issue comment command explicitly allowed on draft PRs even when other commands are blocked.

> **Warning:** In `test-oracle.triggers`, `approved` means the `/approve` issue comment command, not GitHub's native review approval event.

### Label commands

The current issue-comment handler exposes the built-in label-related commands below.

```425:449:webhook_server/libs/handlers/pull_request_handler.py
commands: list[str] = []

if self.labels_handler.is_label_enabled(WIP_STR):
    commands.append("* `/wip` - Mark PR as work in progress (adds WIP: prefix to title)")
    commands.append("* `/wip cancel` - Remove work in progress status")

if self.labels_handler.is_label_enabled(HOLD_LABEL_STR):
    commands.append("* `/hold` - Block PR merging (approvers only)")
    commands.append("* `/hold cancel` - Unblock PR merging")

if self.labels_handler.is_label_enabled(VERIFIED_LABEL_STR):
    commands.append("* `/verified` - Mark PR as verified")
    commands.append("* `/verified cancel` - Remove verification status")

# These commands are always available
commands.append(
    "* `/reprocess` - Trigger complete PR workflow reprocessing "
    "(useful if webhook failed or configuration changed)"
)
```

| Command | What it does | Who can use it |
| --- | --- | --- |
| `/wip` | Adds the `wip` label and prepends `WIP:` to the title if it is not already there | No additional role check |
| `/wip cancel` | Removes the `wip` label and removes a leading `WIP:` or `WIP: ` prefix from the title | No additional role check |
| `/hold` | Adds the `hold` label, which blocks merge readiness | PR approvers for the current PR |
| `/hold cancel` | Removes the `hold` label | PR approvers for the current PR |
| `/verified` | Adds the `verified` label and marks the `verified` check successful | No additional role check |
| `/verified cancel` | Removes the `verified` label and sets the `verified` check back to queued | No additional role check |
| `/approve` | Adds the `approved-<user>` review label | PR approvers or root approvers |
| `/approve cancel` | Removes the `approved-<user>` review label | PR approvers or root approvers |
| `/lgtm` | Adds the `lgtm-<user>` review label used for `minimum-lgtm` | No additional role check, but the PR author's own `/lgtm` is ignored |
| `/lgtm cancel` | Removes the `lgtm-<user>` review label | No additional role check |
| `/automerge` | Adds the `automerge` label | Repository maintainers or repository approvers |

The `automerge` label is more than a marker. When the `can-be-merged` check later completes successfully, the server performs a squash merge automatically:

```80:89:webhook_server/libs/handlers/check_run_handler.py
if check_run_name == CAN_BE_MERGED_STR:
    if getattr(self, "labels_handler", None) and pull_request and check_run_conclusion == SUCCESS_STR:
        if await self.labels_handler.label_exists_in_pull_request(
            label=AUTOMERGE_LABEL_STR, pull_request=pull_request
        ):
            try:
                await asyncio.to_thread(pull_request.merge, merge_method="SQUASH")
                self.logger.info(
                    f"{self.log_prefix} Successfully auto-merged pull request #{pull_request.number}"
                )
```

> **Tip:** `/automerge` only adds the label. The actual merge happens later, after the `can-be-merged` check reports success.

Label categories are configurable:

```47:79:examples/config.yaml
# Labels configuration - control which labels are enabled and their colors
# If not set, all labels are enabled with default colors
labels:
  # Optional: List of label categories to enable
  # If not set, all labels are enabled. If set, only listed categories are enabled.
  # Note: reviewed-by labels (approved-*, lgtm-*, etc.) are always enabled and cannot be disabled
  enabled-labels:
    - verified
    - hold
    - wip
    - needs-rebase
    - has-conflicts
    - can-be-merged
    - size
    - branch
    - cherry-pick
    - automerge
  # Optional: Custom colors for labels (CSS3 color names)
  colors:
    hold: red
    verified: green
    wip: orange
    needs-rebase: darkred
    has-conflicts: red
    can-be-merged: limegreen
    automerge: green
    # Dynamic label prefixes
    approved-: green
    lgtm-: yellowgreen
    changes-requested-: orange
    commented-: gold
    cherry-pick-: coral
    branch-: royalblue
```

> **Note:** If `labels.enabled-labels` is empty, configurable label commands such as `wip`, `hold`, `verified`, and `automerge` are effectively disabled. Review-state labels such as `approved-*` and `lgtm-*` remain enabled because the review workflow depends on them.

### `/add-allowed-user @username`

This is the permission override command.

Use it when someone outside the default valid-user set needs to run `/retest`, `/reprocess`, or `/build-and-push-container`.

How it works:

- A maintainer or repository approver comments `/add-allowed-user @username`
- Later permission checks look for that exact comment on the PR
- If the approving comment was posted by someone else, it is ignored for authorization purposes
- The command requires an argument

This is most useful for letting an occasional contributor rerun checks without changing `OWNERS` or repository membership.
