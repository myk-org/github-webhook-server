# Pull Request Automation

This server turns a pull request into a guided workflow. When a PR is opened or moved out of draft, it can post a welcome comment, assign reviewers from `OWNERS`, add labels, queue checks, create a tracking issue, add an assignee, and optionally prepare auto-merge or cherry-pick work.

The `can-be-merged` check is the center of the workflow. It summarizes whether the current revision is ready to merge, and several other features, especially `/automerge`, wait for that check to pass.

## Welcome Comment

The welcome comment is the user-facing control panel on the PR. It explains what the server will do automatically, shows the commands available for that repository, and lists the current merge requirements and review participants.

```255:320:webhook_server/libs/handlers/pull_request_handler.py
def _prepare_welcome_comment(self) -> str:
    # ...
    return f"""
{self.github_webhook.issue_url_for_welcome_msg}

## Welcome! 🎉

This pull request will be automatically processed with the following features:{auto_verified_note}

### 🔄 Automatic Actions
* **Reviewer Assignment**: Reviewers are automatically assigned based on the OWNERS file in the repository root
* **Size Labeling**: PR size labels (XS, S, M, L, XL, XXL) are automatically applied based on changes
{issue_creation_note}
{self._prepare_pre_commit_welcome_line}\
* **Branch Labeling**: Branch-specific labels are applied to track the target branch
* **Auto-verification**: Auto-verified users have their PRs automatically marked as verified
{self._prepare_labels_config_welcome_section}\

### 📋 Available Commands
#### PR Status Management
{self._prepare_pr_status_commands_section}

#### Review & Approval
* `/lgtm` - Approve changes (looks good to me)
* `/approve` - Approve PR (approvers only)
{self._prepare_automerge_command_line}\
* `/assign-reviewers` - Assign reviewers based on OWNERS file
* `/assign-reviewer @username` - Assign specific reviewer
* `/check-can-merge` - Check if PR meets merge requirements
"""
```

The content is built from the active repository configuration. If a repo does not have container builds, custom retests, `verified`, `automerge`, or cherry-pick labels enabled, those parts disappear from the comment instead of showing dead commands.

> **Tip:** `/regenerate-welcome` refreshes the current welcome comment in place. `/reprocess` reruns the setup workflow and, on the reprocess path, skips creating duplicate welcome comments or tracking issues.

## Tracking Issues

If `create-issue-for-new-pr` is enabled, the server creates a tracking issue for the pull request and assigns it to the PR author. That issue is then closed automatically when the PR is merged or closed.

```866:916:webhook_server/libs/handlers/pull_request_handler.py
async def create_issue_for_new_pull_request(self, pull_request: PullRequest) -> None:
    if not self.github_webhook.create_issue_for_new_pr:
        self.logger.info(f"{self.log_prefix} Issue creation for new PRs is disabled for this repository")
        return

    if self.github_webhook.parent_committer in self.github_webhook.auto_verified_and_merged_users:
        self.logger.info(
            f"{self.log_prefix} Committer {self.github_webhook.parent_committer} is part of "
            f"{self.github_webhook.auto_verified_and_merged_users}, will not create issue."
        )
        return

    await asyncio.to_thread(
        self.repository.create_issue,
        title=self._generate_issue_title(pull_request=pull_request),
        body=self._generate_issue_body(pull_request=pull_request),
        assignee=pull_request.user.login,
    )

async def set_pull_request_automerge(self, pull_request: PullRequest) -> None:
    set_auto_merge_base_branch = pull_request.base.ref in self.github_webhook.set_auto_merge_prs
    parent_committer_in_auto_merge_users = (
        self.github_webhook.parent_committer in self.github_webhook.auto_verified_and_merged_users
    )
    auto_merge = set_auto_merge_base_branch or parent_committer_in_auto_merge_users

    if auto_merge and not pull_request.raw_data.get("auto_merge"):
        await asyncio.to_thread(pull_request.enable_automerge, merge_method="SQUASH")
```

> **Note:** Users listed in `auto-verified-and-merged-users` skip tracking issue creation. In practice, this is useful for trusted bots or highly automated contributor flows where you do not want an extra issue per PR.

## WIP and Hold Behavior

`wip` and `hold` are explicit merge blockers.

- `wip` is for "not ready yet". It can be set automatically from the PR title or manually with `/wip`.
- `hold` is for "ready, but do not merge". It is manual, and only approvers can set or remove it.
- Both states keep the `can-be-merged` check from succeeding until they are removed.

```227:349:webhook_server/libs/handlers/issue_comment_handler.py
if _command == AUTOMERGE_LABEL_STR:
    if reviewed_user not in (
        await self.owners_file_handler.get_all_repository_maintainers()
        + self.owners_file_handler.all_repository_approvers
    ):
        msg = "Only maintainers or approvers can set pull request to auto-merge"
        await asyncio.to_thread(pull_request.create_issue_comment, body=msg)
        return

    await self.labels_handler._add_label(pull_request=pull_request, label=AUTOMERGE_LABEL_STR)

# ...

elif _command == WIP_STR:
    wip_for_title: str = f"{WIP_STR.upper()}:"
    if remove:
        label_changed = await self.labels_handler._remove_label(pull_request=pull_request, label=WIP_STR)
        if label_changed:
            pr_title = await asyncio.to_thread(lambda: pull_request.title)
            if pr_title.upper().startswith("WIP: "):
                await asyncio.to_thread(pull_request.edit, title=pr_title[5:])
            elif pr_title.upper().startswith("WIP:"):
                await asyncio.to_thread(pull_request.edit, title=pr_title[4:])
    else:
        label_changed = await self.labels_handler._add_label(pull_request=pull_request, label=WIP_STR)
        if label_changed and not pr_title.upper().startswith("WIP:"):
            await asyncio.to_thread(pull_request.edit, title=f"{wip_for_title} {pr_title}")

elif _command == HOLD_LABEL_STR:
    if reviewed_user not in self.owners_file_handler.all_pull_request_approvers:
        await asyncio.to_thread(
            pull_request.create_issue_comment,
            f"{reviewed_user} is not part of the approver, only approvers can mark pull request with hold",
        )
    else:
        if remove:
            await self.labels_handler._remove_label(pull_request=pull_request, label=HOLD_LABEL_STR)
        else:
            await self.labels_handler._add_label(pull_request=pull_request, label=HOLD_LABEL_STR)

elif _command == VERIFIED_LABEL_STR:
    if remove:
        await self.labels_handler._remove_label(pull_request=pull_request, label=VERIFIED_LABEL_STR)
        await self.check_run_handler.set_check_queued(name=VERIFIED_LABEL_STR)
    else:
        await self.labels_handler._add_label(pull_request=pull_request, label=VERIFIED_LABEL_STR)
        await self.check_run_handler.set_check_success(name=VERIFIED_LABEL_STR)
```

A few practical details matter here:

- Editing the PR title to add or remove a `WIP:` prefix also syncs the `wip` label automatically.
- `hold` does not change the title. It is purely a merge control.
- The server also manages `has-conflicts` and `needs-rebase` labels automatically, so users can see when GitHub says the PR has conflicts or has fallen behind its base branch.
- On new commits, merge readiness is recalculated for the new revision.

> **Note:** Most comment commands are blocked on draft PRs by default. Use `allow-commands-on-draft-prs` if you want to allow all commands on draft PRs (`[]`) or only a specific allowlist.

## Auto-verification and Assignee Updates

When `verified-job` is enabled, the server maintains both the `verified` label and the `verified` check run. Trusted authors can be auto-verified, while everyone else is reset back to queued verification when new commits arrive.

```1112:1162:webhook_server/libs/handlers/pull_request_handler.py
async def _process_verified_for_update_or_new_pull_request(self, pull_request: PullRequest) -> None:
    if not self.github_webhook.verified_job:
        return

    labels = await asyncio.to_thread(lambda: list(pull_request.labels))

    is_ai_resolved = any(label.name == AI_RESOLVED_CONFLICTS_LABEL for label in labels)
    if is_ai_resolved:
        await self.labels_handler._remove_label(pull_request=pull_request, label=VERIFIED_LABEL_STR)
        await self.check_run_handler.set_check_queued(name=VERIFIED_LABEL_STR)
        return

    is_cherry_picked = any(label.name.startswith(CHERRY_PICKED_LABEL) for label in labels)
    if is_cherry_picked and not self.github_webhook.auto_verify_cherry_picked_prs:
        await self.labels_handler._remove_label(pull_request=pull_request, label=VERIFIED_LABEL_STR)
        await self.check_run_handler.set_check_queued(name=VERIFIED_LABEL_STR)
        return

    if self.github_webhook.parent_committer in self.github_webhook.auto_verified_and_merged_users:
        await self.labels_handler._add_label(pull_request=pull_request, label=VERIFIED_LABEL_STR)
        await self.check_run_handler.set_check_success(name=VERIFIED_LABEL_STR)
    else:
        await self.labels_handler._remove_label(pull_request=pull_request, label=VERIFIED_LABEL_STR)
        await self.check_run_handler.set_check_queued(name=VERIFIED_LABEL_STR)

async def add_pull_request_owner_as_assingee(self, pull_request: PullRequest) -> None:
    try:
        await asyncio.to_thread(pull_request.add_to_assignees, pull_request.user.login)
    except Exception:
        if self.owners_file_handler.root_approvers:
            await asyncio.to_thread(pull_request.add_to_assignees, self.owners_file_handler.root_approvers[0])
```

In practice, this means:

- Authors in `auto-verified-and-merged-users` are auto-verified on PR open and on later updates.
- Other authors need verification again after each new commit, because the server resets `verified` and re-queues its check.
- Standard PRs are assigned to the PR author automatically.
- If the author cannot be assigned, the server falls back to the first root approver.

> **Warning:** Cherry-picked PRs with `ai-resolved-conflicts` are never auto-verified. The server explicitly re-queues verification and expects a human to review and test that PR before it is merged.

## Auto-merge

This project has two separate auto-merge patterns, and it helps to treat them as different tools:

- Native GitHub auto-merge setup: if a base branch is listed in `set-auto-merge-prs`, or if the author is in `auto-verified-and-merged-users`, the server enables GitHub auto-merge with `SQUASH`.
- Comment-driven merge: if a maintainer or approver uses `/automerge`, the PR gets the `automerge` label. Once `can-be-merged` finishes with success, the server performs a squash merge itself.

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

The `can-be-merged` check is what enforces merge readiness. In user terms, it waits for:

- the OWNERS approval requirement to be satisfied
- the configured `minimum-lgtm` reviewer count
- all required checks to pass
- no `wip`, `hold`, or `has-conflicts`
- `verified`, if the verified job is enabled
- no blocking change-request labels from approvers
- no unresolved review conversations, if `branch-protection.required_conversation_resolution` is enabled
- any exact labels listed in `can-be-merged-required-labels`

> **Note:** In this project, `/approve` is the approver signal. A normal GitHub review marked "Approved" is not the same thing. The review webhook treats `/approve` in the review body as the approver action, while normal review approvals behave like reviewer feedback and LGTM signals.

```32:69:webhook_server/libs/handlers/pull_request_review_handler.py
await self.labels_handler.manage_reviewed_by_label(
    pull_request=pull_request,
    review_state=review_state,
    action=ADD_STR,
    reviewed_user=reviewed_user,
)

if body := self.hook_data["review"]["body"]:
    # In this project, "approved" means a maintainer uses the /approve command
    # (which adds an approved-<user> label), NOT GitHub's review approval state.
    if any(line.strip() == f"/{APPROVE_STR}" for line in body.splitlines()):
        await self.labels_handler.label_by_user_comment(
            pull_request=pull_request,
            user_requested_label=APPROVE_STR,
            remove=False,
            reviewed_user=reviewed_user,
        )
```

## Cherry-pick Workflows

Cherry-picking is label-driven and works in two modes.

- On an unmerged PR, `/cherry-pick <branch>` schedules work for later by adding `cherry-pick-<branch>` labels.
- On an already merged PR, the same command runs the cherry-pick immediately.
- In both cases, the server validates that the target branch exists and comments back if it does not.

```388:463:webhook_server/libs/handlers/issue_comment_handler.py
async def process_cherry_pick_command(
    self, pull_request: PullRequest, command_args: str, reviewed_user: str
) -> None:
    _target_branches: list[str] = command_args.split()
    _exits_target_branches: set[str] = set()
    _non_exits_target_branches_msg: str = ""

    for _target_branch in _target_branches:
        try:
            await asyncio.to_thread(self.repository.get_branch, _target_branch)
            _exits_target_branches.add(_target_branch)
        except Exception:
            _non_exits_target_branches_msg += f"Target branch `{_target_branch}` does not exist\n"

    cp_labels: list[str] = [
        f"{CHERRY_PICK_LABEL_PREFIX}{_target_branch}" for _target_branch in _exits_target_branches
    ]

    if _exits_target_branches:
        if not self.hook_data["issue"].get("pull_request", {}).get("merged_at"):
            info_msg: str = f"""
Cherry-pick requested for PR: `{pull_request.title}` by user `{reviewed_user}`
Adding label/s `{" ".join([_cp_label for _cp_label in cp_labels])}` for automatic cheery-pick once the PR is merged
"""
            await asyncio.to_thread(pull_request.create_issue_comment, info_msg)
        else:
            for _exits_target_branch in _exits_target_branches:
                await self.runner_handler.cherry_pick(
                    pull_request=pull_request,
                    target_branch=_exits_target_branch,
                    assign_to_pr_owner=self.github_webhook.cherry_pick_assign_to_pr_author,
                )

        for _cp_label in cp_labels:
            await self.labels_handler._add_label(pull_request=pull_request, label=_cp_label)
```

When the runner performs a cherry-pick successfully, it creates a new PR against the target branch, labels it with `CherryPicked-from-<source-branch>`, and tries to request review from the original PR author. If `cherry-pick-assign-to-pr-author` is enabled, the new PR is also assigned to the original PR author, not to the person who typed `/cherry-pick`.

If the cherry-pick hits conflicts and AI conflict resolution is enabled, the server attempts to resolve them, labels the new PR with `ai-resolved-conflicts`, and tells users that manual verification is required. If AI is disabled, unavailable, or fails, the original PR gets a comment with manual cherry-pick commands instead.

```1113:1127:webhook_server/libs/handlers/runner_handler.py
if cherry_pick_had_conflicts:
    ai_config = self.github_webhook.ai_features
    ai_result = get_ai_config(ai_config)
    ai_provider, ai_model = ai_result if ai_result else ("unknown", "unknown")
    await asyncio.to_thread(
        pull_request.create_issue_comment,
        f"**Cherry-pick conflicts were resolved by AI**\n\n"
        f"Cherry-picked PR {pull_request.title} into {target_branch}: {cherry_pick_pr_url}\n"
        f"Conflicts were automatically resolved by AI ({ai_provider}/{ai_model}).\n\n"
        f"**Manual verification is required** — please review the changes and test before merging.",
    )
else:
    await asyncio.to_thread(
        pull_request.create_issue_comment,
        f"Cherry-picked PR {pull_request.title} into {target_branch}: {cherry_pick_pr_url}",
    )
```

> **Tip:** If you rely on cherry-pick automation, keep the `cherry-pick` label category enabled and set `cherry-pick-assign-to-pr-author: true` if you want the follow-up PR to land on the original author by default.

## Key Configuration

Most PR automation settings can be defined globally in `config.yaml`. Many of the same keys can also be overridden per repository in `.github-webhook-server.yaml`.

A representative global setup from the example config looks like this:

```28:63:examples/config.yaml
auto-verified-and-merged-users:
  - "renovate[bot]"
  - "pre-commit-ci[bot]"

auto-verify-cherry-picked-prs: true # Default: true - automatically verify cherry-picked PRs. Set to false to require manual verification.

create-issue-for-new-pr: true # Global default: create tracking issues for new PRs

cherry-pick-assign-to-pr-author: true # Default: true - assign cherry-pick PRs to the original PR author

# Commands allowed on draft PRs (optional)
# If not set: commands are blocked on draft PRs (default behavior)
# If empty list []: all commands allowed on draft PRs
# If list with values: only those commands allowed on draft PRs
# allow-commands-on-draft-prs: []  # Uncomment to allow all commands on draft PRs

labels:
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
```

A repository-level override can tighten or relax the workflow for just one repo:

```65:90:examples/.github-webhook-server.yaml
auto-verified-and-merged-users:
  - "renovate[bot]"
  - "dependabot[bot]"
  - "trusted-user"

auto-verify-cherry-picked-prs: false # Set to false to require manual verification for cherry-picked PRs

branch-protection:
  strict: true
  require_code_owner_reviews: true
  dismiss_stale_reviews: false
  required_approving_review_count: 1
  required_linear_history: true
  required_conversation_resolution: true

# Auto-merge configuration
set-auto-merge-prs:
  - main
  - develop
```

And the same example file shows repository-specific knobs for reviewer LGTM counts, tracking issues, and cherry-pick assignee behavior:

```120:126:examples/.github-webhook-server.yaml
# Minimum LGTM count required
minimum-lgtm: 2

# Issue creation for new pull requests
create-issue-for-new-pr: true # Create tracking issues for new PRs

cherry-pick-assign-to-pr-author: true # Assign cherry-pick PRs to the original PR author (default: true)
```

The most important keys for this page are:

- `auto-verified-and-merged-users`: trusted authors whose PRs can be auto-verified and can also have GitHub auto-merge enabled automatically.
- `verified-job`: turns the `verified` label/check workflow on or off.
- `create-issue-for-new-pr`: enables or disables tracking issues.
- `allow-commands-on-draft-prs`: controls which comment commands can run before a PR leaves draft.
- `labels.enabled-labels`: turns label-driven features like `wip`, `hold`, `automerge`, and `cherry-pick` on or off.
- `minimum-lgtm`: sets the reviewer LGTM requirement.
- `set-auto-merge-prs`: enables native GitHub auto-merge on matching base branches.
- `auto-verify-cherry-picked-prs`: controls whether cherry-picked PRs can be auto-verified.
- `cherry-pick-assign-to-pr-author`: decides who gets assigned on the cherry-pick follow-up PR.
- `can-be-merged-required-labels`: adds extra exact label requirements before `can-be-merged` can succeed.

> **Warning:** `can-be-merged-required-labels` uses exact label names. It is best suited for fixed labels such as `security-reviewed` or `tests-passed`, not dynamic reviewer labels such as `approved-<user>` or `lgtm-<user>`.
