# Supported GitHub Events

`github-webhook-server` automates a focused set of GitHub webhook events for pull requests, merge gating, and release workflows. You can subscribe the webhook to more events than this, but only the events on this page have built-in behavior.

> **Note:** The webhook endpoint returns `200 OK` after the payload is validated and queued. That means GitHub reached the server successfully. It does **not** mean every downstream action finished successfully.

```507:529:webhook_server/app.py
    # Start background task immediately using asyncio.create_task
    # This ensures the HTTP response is sent immediately without waiting
    # Store task reference for observability and graceful shutdown
    task = asyncio.create_task(
        process_with_error_handling(
            _hook_data=hook_data,
            _headers=request.headers,
            _delivery_id=delivery_id,
            _event_type=event_type,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    # Return 200 immediately with JSONResponse for fastest serialization
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": status.HTTP_200_OK,
            "message": "Webhook queued for processing",
            "delivery_id": delivery_id,
            "event_type": event_type,
        },
    )
```

## Configure Events

Use the `events` list in `config.yaml` or `.github-webhook-server.yaml` to control which GitHub deliveries this server should receive for a repository.

```150:157:examples/config.yaml
    events: # To listen to all events do not send events
      - push
      - pull_request
      - pull_request_review
      - pull_request_review_thread
      - issue_comment
      - check_run
      - status
```

If you want GitHub to send every event type, omit the `events` key entirely.

## Event Summary

| Event | What it is used for | Intentionally skipped cases |
| --- | --- | --- |
| `ping` | Webhook connectivity check | Always stops after logging |
| `push` | Tag-based release automation | Branch pushes, branch/tag deletions |
| `pull_request` | PR lifecycle automation | Draft PRs, unhandled PR actions |
| `issue_comment` | Slash commands on PR comments | Plain issue comments, edited/deleted comments, non-command comments, draft restrictions |
| `pull_request_review` | Review labels and `/approve` handling | Review actions other than `submitted` |
| `check_run` | Mergeability re-checks and auto-merge | `action != completed`, failing `can-be-merged` runs |
| `status` | Mergeability re-checks for commit statuses | `pending`, no matching PR |
| `pull_request_review_thread` | Conversation-resolution merge gating | Actions other than `resolved`/`unresolved`, conversation resolution disabled |

> **Note:** Several GitHub events are broader than pull requests. When the server cannot map an `issue_comment`, `status`, or `check_run` delivery back to a pull request, it logs the delivery and skips it.

## `ping`

`ping` is just GitHub's connectivity test. The server logs it and stops. No pull request lookup, labels, checks, or builds happen for this event.

## `push`

`push` is used for releases, not normal branch CI. The server only does real work for tag pushes such as `refs/tags/v1.2.3`.

When a matching tag push arrives, the server can:

- publish a package to PyPI if `pypi` is configured
- build and push a release container image if container release settings are enabled

Branch pushes are intentionally ignored. Delete events are also ignored.

```486:512:webhook_server/libs/github_api.py
            # Skip branch/tag deletions - no processing needed
            if self.hook_data.get("deleted"):
                self.logger.info(f"{self.log_prefix} Branch/tag deletion detected, skipping processing")
                token_metrics = await self._get_token_metrics()
                self.logger.info(
                    f"{self.log_prefix} Webhook processing completed: deletion event (skipped) - {token_metrics}"
                )
                await self._update_context_metrics()
                return None

            ref = self.hook_data["ref"]

            # Only clone for tag pushes - branch pushes don't require cloning
            # because PushHandler only processes tags (PyPI upload, container build)
            if ref.startswith("refs/tags/"):
                await self._clone_repository(checkout_ref=ref)
                await PushHandler(github_webhook=self).process_push_webhook_data()
                token_metrics = await self._get_token_metrics()
                self.logger.info(
                    f"{self.log_prefix} Webhook processing completed successfully: push - {token_metrics}",
                )
            else:
                self.logger.debug(f"{self.log_prefix} Skipping clone for branch push: {ref}")
                token_metrics = await self._get_token_metrics()
                self.logger.info(
                    f"{self.log_prefix} Webhook processing completed: branch push (skipped) - {token_metrics}"
                )
```

If neither PyPI publishing nor container release is configured, even a tag push is effectively a no-op.

## `pull_request`

This is the main automation event. The server has first-class behavior for these `action` values:

- `opened`
- `reopened`
- `ready_for_review`
- `synchronize`
- `closed`
- `edited`
- `labeled`
- `unlabeled`

In practice, that means:

- `opened` initializes the PR: welcome comment, optional tracking issue, reviewer assignment, labels, queued checks, CI tasks, possible auto-merge setup, and an optional Test Oracle run when `pr-opened` is enabled.
- `reopened` reruns the main PR setup flow.
- `ready_for_review` reruns the main PR setup flow and posts the welcome comment.
- `synchronize` reruns setup and CI, removes old review-state labels, and can trigger Test Oracle when `pr-synchronized` is enabled.
- `closed` closes PR tracking artifacts. If the PR was merged, it can also run queued cherry-picks, push the merged container build, and refresh merge-state labels on other open PRs.
- `edited` recalculates `wip` and reruns `conventional-title` when the PR title changed.
- `labeled` and `unlabeled` refresh `verified` and re-check mergeability when merge-control labels change.

The main setup and CI work is visible in the handler itself:

```786:853:webhook_server/libs/handlers/pull_request_handler.py
        setup_tasks.append(self.owners_file_handler.assign_reviewers(pull_request=pull_request))
        setup_tasks.append(
            self.labels_handler._add_label(
                pull_request=pull_request,
                label=f"{BRANCH_LABEL_PREFIX}{pull_request.base.ref}",
            )
        )
        setup_tasks.append(self.label_pull_request_by_merge_state(pull_request=pull_request))
        setup_tasks.append(self.check_run_handler.set_check_queued(name=CAN_BE_MERGED_STR))

        # Only queue built-in checks when their corresponding feature is enabled
        if self.github_webhook.tox:
            setup_tasks.append(self.check_run_handler.set_check_queued(name=TOX_STR))

        if self.github_webhook.pre_commit:
            setup_tasks.append(self.check_run_handler.set_check_queued(name=PRE_COMMIT_STR))

        if self.github_webhook.pypi:
            setup_tasks.append(self.check_run_handler.set_check_queued(name=PYTHON_MODULE_INSTALL_STR))

        if self.github_webhook.build_and_push_container:
            setup_tasks.append(self.check_run_handler.set_check_queued(name=BUILD_CONTAINER_STR))

        setup_tasks.append(self._process_verified_for_update_or_new_pull_request(pull_request=pull_request))
        setup_tasks.append(self.labels_handler.add_size_label(pull_request=pull_request))
        setup_tasks.append(self.add_pull_request_owner_as_assingee(pull_request=pull_request))

        if self.github_webhook.conventional_title:
            setup_tasks.append(self.check_run_handler.set_check_queued(name=CONVENTIONAL_TITLE_STR))

        # Queue custom check runs (same as built-in checks)
        # Note: custom checks are validated in GithubWebhook._validate_custom_check_runs()
        # so name is guaranteed to exist
        for custom_check in self.github_webhook.custom_check_runs:
            check_name = custom_check["name"]
            setup_tasks.append(self.check_run_handler.set_check_queued(name=check_name))

        self.logger.info(f"{self.log_prefix} Executing setup tasks")
        setup_results = await asyncio.gather(*setup_tasks, return_exceptions=True)

        for result in setup_results:
            if isinstance(result, Exception):
                self.logger.error(f"{self.log_prefix} Setup task failed: {result}")

        if self.ctx:
            self.ctx.complete_step("pr_workflow_setup")

        # Stage 2: CI/CD execution tasks
        if self.ctx:
            self.ctx.start_step("pr_cicd_execution")

        ci_tasks: list[Coroutine[Any, Any, Any]] = []

        ci_tasks.append(self.runner_handler.run_tox(pull_request=pull_request))
        ci_tasks.append(self.runner_handler.run_pre_commit(pull_request=pull_request))
        ci_tasks.append(self.runner_handler.run_install_python_module(pull_request=pull_request))
        ci_tasks.append(self.runner_handler.run_build_container(pull_request=pull_request))

        if self.github_webhook.conventional_title:
            ci_tasks.append(self.runner_handler.run_conventional_title_check(pull_request=pull_request))

        # Launch custom check runs (same as built-in checks)
        for custom_check in self.github_webhook.custom_check_runs:
            ci_tasks.append(
                self.runner_handler.run_custom_check(
                    pull_request=pull_request,
                    check_config=custom_check,
                )
            )
```

> **Note:** Draft PRs are intentionally quiet. Normal `pull_request` automation stops early for drafts. Draft-specific comment behavior is handled separately through `issue_comment`.

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

Other `pull_request` actions are currently ignored.

## `issue_comment`

GitHub sends `issue_comment` for both issues and pull requests. This server only acts when the comment belongs to a pull request.

It intentionally ignores:

- edited comments
- deleted comments
- its own welcome-message comment
- comments that do not contain slash commands on lines starting with `/`

Supported commands include `retest`, `reprocess`, `check-can-merge`, `assign-reviewers`, `assign-reviewer`, `cherry-pick`, `build-and-push-container`, `regenerate-welcome`, `test-oracle`, and label-style commands such as `wip`, `hold`, `verified`, `lgtm`, `approve`, and `automerge`.

A few especially important behaviors:

- `/retest <check>` reruns supported configured checks. `/retest all` reruns every supported check for that PR.
- `/cherry-pick <branch...>` adds cherry-pick labels on unmerged PRs, or immediately creates cherry-pick PRs for merged PRs. If AI cherry-pick conflict resolution is enabled, the immediate flow can use it.
- `/approve` is the project's label-driven approval command and can trigger Test Oracle's `approved` trigger.
- `/test-oracle` can always be run manually when Test Oracle is configured.

> **Note:** Slash commands are permission-checked. Commands such as `/retest`, `/reprocess`, `/hold`, and `/automerge` may be ignored or rejected if the commenter is not allowed to run them.

For draft PRs, the server uses `allow-commands-on-draft-prs`. An empty list means "allow all commands." A non-empty list means "allow only these commands." `/test-oracle` is the one built-in exception that bypasses this draft filter.

```174:197:webhook_server/libs/handlers/issue_comment_handler.py
        # Check if command is allowed on draft PRs
        if is_draft and _command != COMMAND_TEST_ORACLE_STR:
            allow_commands_on_draft = self.github_webhook.config.get_value("allow-commands-on-draft-prs")
            if not isinstance(allow_commands_on_draft, list):
                self.logger.debug(
                    f"{self.log_prefix} Command {_command} blocked: "
                    "draft PR and allow-commands-on-draft-prs not configured"
                )
                return
            # Empty list means all commands allowed; non-empty list means only those commands
            if len(allow_commands_on_draft) > 0:
                # Sanitize: ensure all entries are strings for safe join and comparison
                allow_commands_on_draft = [str(cmd) for cmd in allow_commands_on_draft]
                if _command not in allow_commands_on_draft:
                    self.logger.debug(
                        f"{self.log_prefix} Command {_command} is not allowed on draft PRs. "
                        f"Allowed commands: {allow_commands_on_draft}"
                    )
                    await asyncio.to_thread(
                        pull_request.create_issue_comment,
                        f"Command `/{_command}` is not allowed on draft PRs.\n"
                        f"Allowed commands on draft PRs: {', '.join(allow_commands_on_draft)}",
                    )
                    return
```

## `pull_request_review`

This event matters only when the review `action` is `submitted`. The server updates its review labels based on the review state, such as comment, approval, or requested changes.

If the review body contains a literal `/approve`, the server also applies the project's approval label and can trigger Test Oracle's `approved` trigger.

```37:78:webhook_server/libs/handlers/pull_request_review_handler.py
            if self.hook_data["action"] == "submitted":
                """
                Available actions:
                    commented
                    approved
                    changes_requested
                """
                reviewed_user = self.hook_data["review"]["user"]["login"]
                review_state = self.hook_data["review"]["state"]
                self.github_webhook.logger.debug(
                    f"{self.github_webhook.log_prefix} "
                    f"Processing pull request review for user {reviewed_user} with state {review_state}"
                )

                await self.labels_handler.manage_reviewed_by_label(
                    pull_request=pull_request,
                    review_state=review_state,
                    action=ADD_STR,
                    reviewed_user=reviewed_user,
                )

                if body := self.hook_data["review"]["body"]:
                    self.github_webhook.logger.debug(f"{self.github_webhook.log_prefix} Found review body: {body}")
                    # In this project, "approved" means a maintainer uses the /approve command
                    # (which adds an approved-<user> label), NOT GitHub's review approval state.
                    # The oracle trigger fires only when /approve is found in the review body.
                    if any(line.strip() == f"/{APPROVE_STR}" for line in body.splitlines()):
                        await self.labels_handler.label_by_user_comment(
                            pull_request=pull_request,
                            user_requested_label=APPROVE_STR,
                            remove=False,
                            reviewed_user=reviewed_user,
                        )
                        task = asyncio.create_task(
                            call_test_oracle(
                                github_webhook=self.github_webhook,
                                pull_request=pull_request,
                                trigger="approved",
                            )
                        )
                        _background_tasks.add(task)
                        task.add_done_callback(_background_tasks.discard)
```

> **Note:** In this project, GitHub's green "Approved" review state is not the same as the `/approve` command. The special `approved` Test Oracle trigger follows the command, not the raw GitHub review state.

Review actions other than `submitted`, such as `edited` or `dismissed`, are intentionally ignored.

## `check_run`

`check_run` is where the server reacts to finished GitHub checks.

When a completed `can-be-merged` check succeeds and the PR has the `automerge` label, the server attempts a squash merge. Other completed check runs cause the server to re-evaluate whether the PR can be merged.

```64:109:webhook_server/libs/handlers/check_run_handler.py
        if self.hook_data.get("action", "") != "completed":
            self.logger.debug(
                f"{self.log_prefix} check run {check_run_name} action is "
                f"{self.hook_data.get('action', 'N/A')} and not completed, skipping"
            )
            if self.ctx:
                self.ctx.complete_step("check_run_handler")
            return False

        check_run_status: str = _check_run["status"]
        check_run_conclusion: str = _check_run["conclusion"]
        self.logger.debug(
            f"{self.log_prefix} processing check_run - Name: {check_run_name} "
            f"Status: {check_run_status} Conclusion: {check_run_conclusion}"
        )

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
                        if self.ctx:
                            self.ctx.complete_step("check_run_handler")
                        return False
                    except Exception as ex:
                        self.logger.error(
                            f"{self.log_prefix} Failed to auto-merge pull request #{pull_request.number}: {ex}"
                        )
                        if self.ctx:
                            self.ctx.complete_step("check_run_handler")
                        return True

            else:
                self.logger.debug(f"{self.log_prefix} check run is {CAN_BE_MERGED_STR}, skipping")
                if self.ctx:
                    self.ctx.complete_step("check_run_handler")
                return False

        if self.ctx:
            self.ctx.complete_step("check_run_handler")
        return True
```

The server intentionally skips:

- `check_run` deliveries whose `action` is not `completed`
- extra work when the finished check is `can-be-merged` but the conclusion is not `success`

That skip behavior keeps the server from re-processing every queued or in-progress check update.

## `status`

`status` is the older commit-status event. It is useful for tools that report commit statuses instead of GitHub check runs.

When the status reaches a terminal state such as `success`, `failure`, or `error`, the server re-checks mergeability for the matching PR.

It intentionally ignores:

- `pending` statuses
- any status delivery that cannot be mapped back to a PR

## `pull_request_review_thread`

`pull_request_review_thread` is used only for conversation-resolution gating.

When a review thread becomes `resolved` or `unresolved`, the server re-evaluates `can-be-merged` so unresolved conversations can block merging when that rule is enabled.

```221:227:examples/config.yaml
    branch-protection:
      strict: True
      require_code_owner_reviews: True
      dismiss_stale_reviews: False
      required_approving_review_count: 1
      required_linear_history: True
      required_conversation_resolution: True
```

It intentionally skips:

- thread actions other than `resolved` and `unresolved`
- all review-thread deliveries when `branch-protection.required_conversation_resolution` is `false`

> **Tip:** Turn `required_conversation_resolution` off if you do not want unresolved review threads to participate in merge gating.

## Events That Are Not Automated

The webhook subscription and the application logic are not the same thing. GitHub can send an event that the server accepts but does not automate.

> **Warning:** The repo-local example file still lists `pull_request_review_comment`, but the server does not have first-class handling for that event. If GitHub sends it, the delivery is accepted and then effectively ignored.

```20:29:examples/.github-webhook-server.yaml
# GitHub events to listen to
events:
  - push
  - pull_request
  - pull_request_review
  - pull_request_review_comment
  - pull_request_review_thread
  - issue_comment
  - check_run
  - status
```

The same rule applies to any other GitHub event that is not listed on this page. If it is not one of the supported events above, the server does not have a dedicated automation path for it.
