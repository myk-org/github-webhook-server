# Architecture and Event Flow

`github-webhook-server` is built around one simple idea: accept GitHub webhooks quickly, then do the real work asynchronously. As a user, that means GitHub gets a fast response, while pull request automation, release work, and logging continue in the background.

## At a Glance
- The HTTP endpoint validates the request and returns `200 OK` immediately.
- A background task creates a `GithubWebhook` object and routes the event to specialized handlers.
- PR automation is split across focused components such as `PullRequestHandler`, `IssueCommentHandler`, `PullRequestReviewHandler`, `CheckRunHandler`, `OwnersFileHandler`, `LabelsHandler`, and `RunnerHandler`.
- Each webhook gets one temporary base clone; checks and release actions run in isolated Git worktrees created from that clone.
- Every webhook produces both normal text logs and structured JSON records, which can be searched later or viewed through the optional log viewer.

## Before the First Event

Startup does more than launch the HTTP server. `entrypoint.py` first runs repository bootstrap logic, then starts Uvicorn with the configured worker count.

```23:45:webhook_server/utils/github_repository_and_webhook_settings.py
async def repository_and_webhook_settings(webhook_secret: str | None = None) -> None:
    config = Config(logger=LOGGER)
    apis_dict: dict[str, dict[str, Any]] = {}
    ...
    await set_repositories_settings(config=config, apis_dict=apis_dict)
    set_all_in_progress_check_runs_to_queued(repo_config=config, apis_dict=apis_dict)
    create_webhook(config=config, apis_dict=apis_dict, secret=webhook_secret)
```

That startup pass does three important jobs:

- It applies repository-side settings such as labels, branch protection, and related GitHub configuration.
- It resets built-in check runs that were left in `in_progress` during a previous shutdown back to `queued`.
- It creates or updates the GitHub webhook on each configured repository so GitHub actually sends the events listed in your config.

`entrypoint.py` then starts the app with `workers=int(_max_workers)`, so worker-level parallelism is controlled by the root `max-workers` setting.

> **Note:** The `events` list under each repository is operational, not just descriptive. Startup uses it to create or update the real GitHub webhook subscription.

## Webhook Intake Pipeline

When GitHub calls `POST /webhook_server`, the server does only the minimum synchronous work required to prove the request is valid: read the body, verify the signature if configured, parse JSON, and check that the repository and event metadata are present. Once that passes, it returns `200 OK` and hands everything else to a background task.

```418:529:webhook_server/app.py
# Return 200 immediately - all validation passed, we can process this webhook
LOGGER.info(f"{log_context} Webhook validation passed, queuing for background processing")

async def process_with_error_handling(
    _hook_data: dict[Any, Any], _headers: Headers, _delivery_id: str, _event_type: str
) -> None:
    # Create structured logging context at the VERY START
    repository_name = _hook_data.get("repository", {}).get("name", "unknown")
    repository_full_name = _hook_data.get("repository", {}).get("full_name", "unknown")
    ctx = create_context(
        hook_id=_delivery_id,
        event_type=_event_type,
        repository=repository_name,
        repository_full_name=repository_full_name,
        action=_hook_data.get("action"),
        sender=_hook_data.get("sender", {}).get("login"),
    )
    ...
    try:
        _api: GithubWebhook = GithubWebhook(hook_data=_hook_data, headers=_headers, logger=_logger)
        try:
            await _api.process()
        finally:
            await _api.cleanup()
    ...
    finally:
        if ctx:
            ctx.completed_at = datetime.now(UTC)
            log_webhook_summary(ctx, _logger, _log_context)

        try:
            write_webhook_log(ctx)
        except Exception:
            _logger.exception(f"{_log_context} Failed to write webhook log")
        finally:
            clear_context()

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

In practice, the intake flow looks like this:

1. GitHub sends the event to `POST /webhook_server`.
2. The server optionally checks the source IP, verifies `x-hub-signature-256` when `webhook-secret` is set, parses the payload, and validates required fields.
3. The server returns a small JSON response containing `delivery_id` and `event_type`.
4. A background task creates the structured context, instantiates `GithubWebhook`, runs processing, performs cleanup, and always writes the final summary log.

> **Note:** A `200 OK` means "accepted and queued", not "automation finished successfully". The `delivery_id` is the key you use to trace a specific webhook through the logs.

For production deployments, the important security settings live near the top of the global config: `webhook-secret`, `verify-github-ips`, and `verify-cloudflare-ips`.

## Background Processing Model

The background model is intentionally simple:

- Uvicorn provides process-level concurrency.
- Inside each worker, webhook processing is queued with `asyncio.create_task`.
- Active tasks are tracked in memory and given up to 30 seconds to finish during shutdown before they are cancelled.
- Local work such as Git, `tox`, `pre-commit`, `podman`, `gh`, and `twine` runs as subprocesses through `run_command()`.
- PyGithub itself is synchronous, so the code regularly wraps blocking API calls and many property reads in `asyncio.to_thread()` to keep the event loop responsive.

This project does not use Celery, Redis, or an external broker. The “queue” is the application process itself.

> **Note:** Because the queue is in-process, recovery is operational rather than broker-based. If the server dies after GitHub already received `200 OK`, you recover with logs, GitHub redelivery, or the `/reprocess` command, not by checking a separate job system.

The official container image is designed around that model. It includes the toolchain the server expects to run locally, including `pre-commit`, `tox`, `gh`, `podman`, `regctl`, and the supported AI CLIs.

## Handler Architecture

`GithubWebhook.process()` is the router for the whole system. It resolves the event into either a tag flow or a pull-request-backed flow, enriches the structured context, and then dispatches to specialized handlers.

At a high level, the routes are:

- `pull_request`: initialize the PR, assign reviewers, queue and run checks, post the welcome message, create an issue if configured, and maintain merge-related labels.
- `pull_request_review`: translate review state into labels and optionally treat `/approve` in a review body as an approval command.
- `issue_comment`: parse slash commands such as `/retest`, `/assign-reviewers`, `/check-can-merge`, `/build-and-push-container`, `/cherry-pick`, `/reprocess`, and `/test-oracle`.
- `check_run`: ignore non-terminal runs, react to completed checks, and optionally auto-merge when `can-be-merged` succeeds and the PR has `automerge`.
- `status` and `pull_request_review_thread`: re-evaluate merge eligibility when a status reaches a terminal state or a review thread is resolved or unresolved.
- `push`: handle tag releases; ordinary branch pushes are intentionally skipped.

For a new or updated PR, the main handler is organized into two phases: setup first, then local CI/CD work.

```779:864:webhook_server/libs/handlers/pull_request_handler.py
async def process_opened_or_synchronize_pull_request(self, pull_request: PullRequest) -> None:
    if self.ctx:
        self.ctx.start_step("pr_workflow_setup")

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
    ...
    self.logger.info(f"{self.log_prefix} Executing setup tasks")
    setup_results = await asyncio.gather(*setup_tasks, return_exceptions=True)
    ...
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
    ...
    self.logger.info(f"{self.log_prefix} Executing CI/CD tasks")
    ci_results = await asyncio.gather(*ci_tasks, return_exceptions=True)
    ...
    if self.ctx:
        self.ctx.complete_step("pr_cicd_execution")
```

A few architectural choices are worth knowing:

- PR automation is OWNERS-driven. `OwnersFileHandler` determines reviewers, approvers, and command permissions from repository files and the changed paths in the PR.
- Merge eligibility is re-computed from current GitHub state rather than blindly trusting one earlier event. That is why `check_run`, `status`, and `pull_request_review_thread` all feed back into `check_if_can_be_merged()`.
- Optional features such as custom check runs, conventional-title validation, AI suggestions, and test-oracle calls plug into the same handler flow rather than creating a separate architecture.

On a typical new PR, the end-to-end suite expects the user-visible check state to look like this:

- `build-container`, `pre-commit`, `python-module-install`, and `tox` complete successfully when those features are configured.
- `verified` starts in `queued`.
- `can-be-merged` is expected to fail until approval, labels, status checks, and conversation rules are satisfied.

## Repository Cloning and Worktrees

The repository strategy is one of the most important architectural choices in this project.

Instead of recloning the repository for every operation, each webhook gets one temporary base clone. That clone is reused for local file inspection, and separate Git worktrees are created on demand for isolated execution.

The base clone is prepared once per webhook:

```262:393:webhook_server/libs/github_api.py
async def _clone_repository(
    self,
    pull_request: PullRequest | None = None,
    checkout_ref: str | None = None,
) -> None:
    ...
    rc, _, err = await run_command(
        command=f"git clone {clone_url_with_token} {self.clone_repo_dir}",
        log_prefix=self.log_prefix,
        redact_secrets=[github_token],
        mask_sensitive=self.mask_sensitive,
    )
    ...
    if pull_request:
        # Fetch the base branch first (needed for checkout)
        base_ref = await asyncio.to_thread(lambda: pull_request.base.ref)
        rc, _, err = await run_command(
            command=f"{git_cmd} fetch origin {base_ref}",
            log_prefix=self.log_prefix,
            mask_sensitive=self.mask_sensitive,
        )
        ...
        # Fetch only this specific PR's ref
        pr_number = await asyncio.to_thread(lambda: pull_request.number)
        rc, _, err = await run_command(
            command=f"{git_cmd} fetch origin +refs/pull/{pr_number}/head:refs/remotes/origin/pr/{pr_number}",
            log_prefix=self.log_prefix,
            mask_sensitive=self.mask_sensitive,
        )
    else:
        # For push events (tags only - branch pushes skip cloning)
        tag_name = checkout_ref.replace("refs/tags/", "")  # type: ignore[union-attr]
        fetch_refspec = f"refs/tags/{tag_name}:refs/tags/{tag_name}"
        rc, _, _ = await run_command(
            command=f"{git_cmd} fetch origin {fetch_refspec}",
            log_prefix=self.log_prefix,
            mask_sensitive=self.mask_sensitive,
        )
    ...
    rc, _, err = await run_command(
        command=f"{git_cmd} checkout {checkout_target}",
        log_prefix=self.log_prefix,
        mask_sensitive=self.mask_sensitive,
    )

    self._repo_cloned = True
    self.logger.info(f"{self.log_prefix} Repository cloned to {self.clone_repo_dir} (ref: {checkout_target})")
```

That base clone is then used for repository-aware logic such as OWNERS parsing and changed-file detection. `OwnersFileHandler` even uses local `git diff` instead of the GitHub API for changed paths, which keeps rate-limit usage down.

When the server needs an isolated execution checkout, it creates a worktree from the shared clone:

```71:164:webhook_server/libs/handlers/runner_handler.py
@contextlib.asynccontextmanager
async def _checkout_worktree(
    self,
    pull_request: PullRequest | None = None,
    is_merged: bool = False,
    checkout: str = "",
    tag_name: str = "",
) -> AsyncGenerator[tuple[bool, str, str, str]]:
    ...
    if checkout:
        checkout_target = checkout
    elif tag_name:
        checkout_target = tag_name
    elif is_merged and pull_request and base_ref is not None:
        checkout_target = base_ref
    elif pull_request and pr_number is not None:
        checkout_target = f"origin/pr/{pr_number}"
    ...
    rc, current_branch, _ = await run_command(
        command=f"git -C {repo_dir} rev-parse --abbrev-ref HEAD",
        log_prefix=self.log_prefix,
        mask_sensitive=self.github_webhook.mask_sensitive,
    )
    ...
    async with helpers_module.git_worktree_checkout(
        repo_dir=repo_dir,
        checkout=checkout_target,
        log_prefix=self.log_prefix,
        mask_sensitive=self.github_webhook.mask_sensitive,
    ) as (success, worktree_path, out, err):
        result: tuple[bool, str, str, str] = (success, worktree_path, out, err)

        # Merge base branch if needed (for PR testing)
        if success and pull_request and not is_merged and not tag_name:
            git_cmd = f"git -C {worktree_path}"
            rc, out, err = await run_command(
                command=f"{git_cmd} merge origin/{merge_ref} -m 'Merge {merge_ref}'",
                log_prefix=self.log_prefix,
                mask_sensitive=self.github_webhook.mask_sensitive,
            )
            if not rc:
                result = (False, worktree_path, out, err)

        yield result
```

This design gives the server a few advantages:

- The expensive `git clone` happens once per webhook, not once per check.
- The base clone stays on a stable checkout that is good for reading `OWNERS` files and computing diffs.
- Each execution path gets its own isolated workspace, which prevents one command from polluting another.
- PR checks are run against a worktree that merges the current base branch into the PR checkout, so validation is closer to what GitHub would merge.
- Tag-based release work can run against a tag worktree without disturbing PR-related state.

Cloning is also deliberately avoided when it is not useful:

- Branch pushes skip cloning entirely.
- Tag pushes clone because release actions need a real checkout.
- `check_run` events are ignored unless the action is `completed`.
- A failed `can-be-merged` check run does not trigger another clone-and-recheck cycle.

> **Tip:** This shared-clone-plus-worktree model is what lets the server run `tox`, `pre-commit`, Python packaging, container builds, `gh` commands, and AI-assisted flows locally without paying the cost of repeated full clones.

## Structured Logging Flow

Every webhook carries a structured execution context from the moment background processing starts to the moment the final summary is written.

The flow looks like this:

1. `create_context()` stores a `WebhookContext` in a `ContextVar`.
2. Handlers call `start_step()`, `complete_step()`, and `fail_step()` for major workflow stages such as `repo_clone`, `pr_workflow_setup`, `pr_cicd_execution`, `check_merge_eligibility`, and `push_handler`.
3. Normal log messages are still written, but `JsonLogHandler` also serializes them as JSON `log_entry` records and enriches them with webhook metadata from the current context.
4. At the end of processing, `write_webhook_log()` writes one `webhook_summary` record with timing, PR metadata, token usage, workflow steps, and overall success or failure.

The summary writer stores those records as one JSON object per line in daily files:

```93:152:webhook_server/utils/structured_logger.py
def write_log(self, context: WebhookContext) -> None:
    """Write webhook context as JSONL entry to date-based log file."""
    completed_at = context.completed_at if context.completed_at else datetime.now(UTC)

    # Get context dict and update timing locally (without mutating context)
    context_dict = context.to_dict()
    context_dict["type"] = "webhook_summary"
    if "timing" in context_dict:
        context_dict["timing"]["completed_at"] = completed_at.isoformat()
        if context.started_at:
            duration_ms = int((completed_at - context.started_at).total_seconds() * 1000)
            context_dict["timing"]["duration_ms"] = duration_ms

    # Get log file path
    log_file = self._get_log_file_path(completed_at)

    # Serialize context to JSON (compact JSONL format - single line, no indentation)
    log_entry = json.dumps(context_dict, ensure_ascii=False)
    ...
    # Write JSON entry with single newline (JSONL format)
    os.write(temp_fd, f"{log_entry}\n".encode())
    ...
    with open(log_file, "a") as log_fd:
        ...
        log_fd.write(data.decode("utf-8"))
```

For operators, the important outputs are:

- Text logs for day-to-day reading.
- `log_entry` JSON records for individual log messages.
- `webhook_summary` JSON records for the complete end-to-end outcome of one delivery.
- Daily files named `webhooks_YYYY-MM-DD.json` under `{data_dir}/logs`.

If you enable `ENABLE_LOG_SERVER=true`, the application also exposes a log viewer and related APIs that read these same structured files for filtering, export, workflow-step drill-down, and live streaming.

> **Warning:** Treat the log viewer as an internal operations surface. It is only mounted when `ENABLE_LOG_SERVER=true`, and it should be exposed only on a trusted network boundary.

## Configuration That Changes the Flow

These root settings shape intake, logging, and bootstrap behavior:

```3:17:examples/config.yaml
log-level: INFO # Set global log level, change take effect immediately without server restart
log-file: webhook-server.log # Set global log file, change take effect immediately without server restart
mcp-log-file: mcp_server.log # Set global MCP log file, change take effect immediately without server restart
logs-server-log-file: logs_server.log # Set global Logs Server log file, change take effect immediately without server restart
mask-sensitive-data: true # Mask sensitive data in logs (default: true). Set to false for debugging (NOT recommended in production)

# Server configuration
disable-ssl-warnings: true # Disable SSL warnings (useful in production to reduce log noise from SSL certificate issues)

# ...
webhook-ip: <HTTP://IP OR URL:PORT/webhook_server> # Full URL with path (e.g., https://your-domain.com/webhook_server or https://smee.io/your-channel)
```

These repository settings determine which events are registered and what a PR or tag push actually does when it arrives:

```139:182:examples/config.yaml
repositories:
  my-repository:
    name: my-org/my-repository
    log-level: DEBUG # Override global log-level for repository
    log-file: my-repository.log # Override global log-file for repository
    mask-sensitive-data: false # Override global setting - disable masking for debugging this specific repo (NOT recommended in production)
    slack-webhook-url: <Slack webhook url> # Send notification to slack on several operations
    verified-job: true
    pypi:
      token: <PYPI TOKEN>

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
      build-args: # build args to send to podman build command
        - my-build-arg1=1
        - my-build-arg2=2
      args: # args to send to podman build command
        - --format docker
```

A few configuration rules are especially important when you are reasoning about the event flow:

- `repositories.<repo>.events` controls what GitHub sends to the server after startup sync.
- `tox`, `pre-commit`, `pypi`, `container`, `conventional-title`, and custom check-run settings decide which checks are queued and which local commands actually run.
- `protected-branches` shapes the status-check list that `can-be-merged` evaluates against.
- `mask-sensitive-data` controls whether secrets are scrubbed from text logs.
- `slack-webhook-url`, `test-oracle`, and AI features add side effects around the main PR pipeline, but they still fit into the same handler model.

> **Note:** Repository-local `.github-webhook-server.yaml` overrides matching values from the global `config.yaml`. That lets one server instance manage repositories with different PR rules, labels, checks, and release behavior without changing the intake architecture.

Put together, the architecture is straightforward: validate fast, process in the background, route by event type, work from one shared clone, isolate side effects in worktrees, and leave a structured trail behind for every delivery. That is what makes `github-webhook-server` feel responsive to GitHub while still doing substantial repository automation under the hood.# Architecture and Event Flow

`github-webhook-server` is built around one simple idea: accept GitHub webhooks quickly, then do the real work asynchronously. As a user, that means GitHub gets a fast response, while pull request automation, release work, and logging continue in the background.

## At a Glance
- The HTTP endpoint validates the request and returns `200 OK` immediately.
- A background task creates a `GithubWebhook` object and routes the event to specialized handlers.
- PR automation is split across focused components such as `PullRequestHandler`, `IssueCommentHandler`, `PullRequestReviewHandler`, `CheckRunHandler`, `OwnersFileHandler`, `LabelsHandler`, and `RunnerHandler`.
- Each webhook gets one temporary base clone; checks and release actions run in isolated Git worktrees created from that clone.
- Every webhook produces both normal text logs and structured JSON records, which can be searched later or viewed through the optional log viewer.

## Before the First Event

Startup does more than launch the HTTP server. `entrypoint.py` first runs repository bootstrap logic, then starts Uvicorn with the configured worker count.

```23:45:webhook_server/utils/github_repository_and_webhook_settings.py
async def repository_and_webhook_settings(webhook_secret: str | None = None) -> None:
    config = Config(logger=LOGGER)
    apis_dict: dict[str, dict[str, Any]] = {}
    ...
    await set_repositories_settings(config=config, apis_dict=apis_dict)
    set_all_in_progress_check_runs_to_queued(repo_config=config, apis_dict=apis_dict)
    create_webhook(config=config, apis_dict=apis_dict, secret=webhook_secret)
```

That startup pass does three important jobs:

- It applies repository-side settings such as labels, branch protection, and related GitHub configuration.
- It resets built-in check runs that were left in `in_progress` during a previous shutdown back to `queued`.
- It creates or updates the GitHub webhook on each configured repository so GitHub actually sends the events listed in your config.

`entrypoint.py` then starts the app with `workers=int(_max_workers)`, so worker-level parallelism is controlled by the root `max-workers` setting.

> **Note:** The `events` list under each repository is operational, not just descriptive. Startup uses it to create or update the real GitHub webhook subscription.

## Webhook Intake Pipeline

When GitHub calls `POST /webhook_server`, the server does only the minimum synchronous work required to prove the request is valid: read the body, verify the signature if configured, parse JSON, and check that the repository and event metadata are present. Once that passes, it returns `200 OK` and hands everything else to a background task.

```418:529:webhook_server/app.py
# Return 200 immediately - all validation passed, we can process this webhook
LOGGER.info(f"{log_context} Webhook validation passed, queuing for background processing")

async def process_with_error_handling(
    _hook_data: dict[Any, Any], _headers: Headers, _delivery_id: str, _event_type: str
) -> None:
    # Create structured logging context at the VERY START
    repository_name = _hook_data.get("repository", {}).get("name", "unknown")
    repository_full_name = _hook_data.get("repository", {}).get("full_name", "unknown")
    ctx = create_context(
        hook_id=_delivery_id,
        event_type=_event_type,
        repository=repository_name,
        repository_full_name=repository_full_name,
        action=_hook_data.get("action"),
        sender=_hook_data.get("sender", {}).get("login"),
    )
    ...
    try:
        _api: GithubWebhook = GithubWebhook(hook_data=_hook_data, headers=_headers, logger=_logger)
        try:
            await _api.process()
        finally:
            await _api.cleanup()
    ...
    finally:
        if ctx:
            ctx.completed_at = datetime.now(UTC)
            log_webhook_summary(ctx, _logger, _log_context)

        try:
            write_webhook_log(ctx)
        except Exception:
            _logger.exception(f"{_log_context} Failed to write webhook log")
        finally:
            clear_context()

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

In practice, the intake flow looks like this:

1. GitHub sends the event to `POST /webhook_server`.
2. The server optionally checks the source IP, verifies `x-hub-signature-256` when `webhook-secret` is set, parses the payload, and validates required fields.
3. The server returns a small JSON response containing `delivery_id` and `event_type`.
4. A background task creates the structured context, instantiates `GithubWebhook`, runs processing, performs cleanup, and always writes the final summary log.

> **Note:** A `200 OK` means "accepted and queued", not "automation finished successfully". The `delivery_id` is the key you use to trace a specific webhook through the logs.

For production deployments, the important security settings live near the top of the global config: `webhook-secret`, `verify-github-ips`, and `verify-cloudflare-ips`.

## Background Processing Model

The background model is intentionally simple:

- Uvicorn provides process-level concurrency.
- Inside each worker, webhook processing is queued with `asyncio.create_task`.
- Active tasks are tracked in memory and given up to 30 seconds to finish during shutdown before they are cancelled.
- Local work such as Git, `tox`, `pre-commit`, `podman`, `gh`, and `twine` runs as subprocesses through `run_command()`.
- PyGithub itself is synchronous, so the code regularly wraps blocking API calls and many property reads in `asyncio.to_thread()` to keep the event loop responsive.

This project does not use Celery, Redis, or an external broker. The “queue” is the application process itself.

> **Note:** Because the queue is in-process, recovery is operational rather than broker-based. If the server dies after GitHub already received `200 OK`, you recover with logs, GitHub redelivery, or the `/reprocess` command, not by checking a separate job system.

The official container image is designed around that model. It includes the toolchain the server expects to run locally, including `pre-commit`, `tox`, `gh`, `podman`, `regctl`, and the supported AI CLIs.

## Handler Architecture

`GithubWebhook.process()` is the router for the whole system. It resolves the event into either a tag flow or a pull-request-backed flow, enriches the structured context, and then dispatches to specialized handlers.

At a high level, the routes are:

- `pull_request`: initialize the PR, assign reviewers, queue and run checks, post the welcome message, create an issue if configured, and maintain merge-related labels.
- `pull_request_review`: translate review state into labels and optionally treat `/approve` in a review body as an approval command.
- `issue_comment`: parse slash commands such as `/retest`, `/assign-reviewers`, `/check-can-merge`, `/build-and-push-container`, `/cherry-pick`, `/reprocess`, and `/test-oracle`.
- `check_run`: ignore non-terminal runs, react to completed checks, and optionally auto-merge when `can-be-merged` succeeds and the PR has `automerge`.
- `status` and `pull_request_review_thread`: re-evaluate merge eligibility when a status reaches a terminal state or a review thread is resolved or unresolved.
- `push`: handle tag releases; ordinary branch pushes are intentionally skipped.

For a new or updated PR, the main handler is organized into two phases: setup first, then local CI/CD work.

```779:864:webhook_server/libs/handlers/pull_request_handler.py
async def process_opened_or_synchronize_pull_request(self, pull_request: PullRequest) -> None:
    if self.ctx:
        self.ctx.start_step("pr_workflow_setup")

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
    ...
    self.logger.info(f"{self.log_prefix} Executing setup tasks")
    setup_results = await asyncio.gather(*setup_tasks, return_exceptions=True)
    ...
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
    ...
    self.logger.info(f"{self.log_prefix} Executing CI/CD tasks")
    ci_results = await asyncio.gather(*ci_tasks, return_exceptions=True)
    ...
    if self.ctx:
        self.ctx.complete_step("pr_cicd_execution")
```

A few architectural choices are worth knowing:

- PR automation is OWNERS-driven. `OwnersFileHandler` determines reviewers, approvers, and command permissions from repository files and the changed paths in the PR.
- Merge eligibility is re-computed from current GitHub state rather than blindly trusting one earlier event. That is why `check_run`, `status`, and `pull_request_review_thread` all feed back into `check_if_can_be_merged()`.
- Optional features such as custom check runs, conventional-title validation, AI suggestions, and test-oracle calls plug into the same handler flow rather than creating a separate architecture.

On a typical new PR, the end-to-end suite expects the user-visible check state to look like this:

- `build-container`, `pre-commit`, `python-module-install`, and `tox` complete successfully when those features are configured.
- `verified` starts in `queued`.
- `can-be-merged` is expected to fail until approval, labels, status checks, and conversation rules are satisfied.

## Repository Cloning and Worktrees

The repository strategy is one of the most important architectural choices in this project.

Instead of recloning the repository for every operation, each webhook gets one temporary base clone. That clone is reused for local file inspection, and separate Git worktrees are created on demand for isolated execution.

The base clone is prepared once per webhook:

```262:393:webhook_server/libs/github_api.py
async def _clone_repository(
    self,
    pull_request: PullRequest | None = None,
    checkout_ref: str | None = None,
) -> None:
    ...
    rc, _, err = await run_command(
        command=f"git clone {clone_url_with_token} {self.clone_repo_dir}",
        log_prefix=self.log_prefix,
        redact_secrets=[github_token],
        mask_sensitive=self.mask_sensitive,
    )
    ...
    if pull_request:
        # Fetch the base branch first (needed for checkout)
        base_ref = await asyncio.to_thread(lambda: pull_request.base.ref)
        rc, _, err = await run_command(
            command=f"{git_cmd} fetch origin {base_ref}",
            log_prefix=self.log_prefix,
            mask_sensitive=self.mask_sensitive,
        )
        ...
        # Fetch only this specific PR's ref
        pr_number = await asyncio.to_thread(lambda: pull_request.number)
        rc, _, err = await run_command(
            command=f"{git_cmd} fetch origin +refs/pull/{pr_number}/head:refs/remotes/origin/pr/{pr_number}",
            log_prefix=self.log_prefix,
            mask_sensitive=self.mask_sensitive,
        )
    else:
        # For push events (tags only - branch pushes skip cloning)
        tag_name = checkout_ref.replace("refs/tags/", "")  # type: ignore[union-attr]
        fetch_refspec = f"refs/tags/{tag_name}:refs/tags/{tag_name}"
        rc, _, _ = await run_command(
            command=f"{git_cmd} fetch origin {fetch_refspec}",
            log_prefix=self.log_prefix,
            mask_sensitive=self.mask_sensitive,
        )
    ...
    rc, _, err = await run_command(
        command=f"{git_cmd} checkout {checkout_target}",
        log_prefix=self.log_prefix,
        mask_sensitive=self.mask_sensitive,
    )

    self._repo_cloned = True
    self.logger.info(f"{self.log_prefix} Repository cloned to {self.clone_repo_dir} (ref: {checkout_target})")
```

That base clone is then used for repository-aware logic such as OWNERS parsing and changed-file detection. `OwnersFileHandler` even uses local `git diff` instead of the GitHub API for changed paths, which keeps rate-limit usage down.

When the server needs an isolated execution checkout, it creates a worktree from the shared clone:

```71:164:webhook_server/libs/handlers/runner_handler.py
@contextlib.asynccontextmanager
async def _checkout_worktree(
    self,
    pull_request: PullRequest | None = None,
    is_merged: bool = False,
    checkout: str = "",
    tag_name: str = "",
) -> AsyncGenerator[tuple[bool, str, str, str]]:
    ...
    if checkout:
        checkout_target = checkout
    elif tag_name:
        checkout_target = tag_name
    elif is_merged and pull_request and base_ref is not None:
        checkout_target = base_ref
    elif pull_request and pr_number is not None:
        checkout_target = f"origin/pr/{pr_number}"
    ...
    rc, current_branch, _ = await run_command(
        command=f"git -C {repo_dir} rev-parse --abbrev-ref HEAD",
        log_prefix=self.log_prefix,
        mask_sensitive=self.github_webhook.mask_sensitive,
    )
    ...
    async with helpers_module.git_worktree_checkout(
        repo_dir=repo_dir,
        checkout=checkout_target,
        log_prefix=self.log_prefix,
        mask_sensitive=self.github_webhook.mask_sensitive,
    ) as (success, worktree_path, out, err):
        result: tuple[bool, str, str, str] = (success, worktree_path, out, err)

        # Merge base branch if needed (for PR testing)
        if success and pull_request and not is_merged and not tag_name:
            git_cmd = f"git -C {worktree_path}"
            rc, out, err = await run_command(
                command=f"{git_cmd} merge origin/{merge_ref} -m 'Merge {merge_ref}'",
                log_prefix=self.log_prefix,
                mask_sensitive=self.github_webhook.mask_sensitive,
            )
            if not rc:
                result = (False, worktree_path, out, err)

        yield result
```

This design gives the server a few advantages:

- The expensive `git clone` happens once per webhook, not once per check.
- The base clone stays on a stable checkout that is good for reading `OWNERS` files and computing diffs.
- Each execution path gets its own isolated workspace, which prevents one command from polluting another.
- PR checks are run against a worktree that merges the current base branch into the PR checkout, so validation is closer to what GitHub would merge.
- Tag-based release work can run against a tag worktree without disturbing PR-related state.

Cloning is also deliberately avoided when it is not useful:

- Branch pushes skip cloning entirely.
- Tag pushes clone because release actions need a real checkout.
- `check_run` events are ignored unless the action is `completed`.
- A failed `can-be-merged` check run does not trigger another clone-and-recheck cycle.

> **Tip:** This shared-clone-plus-worktree model is what lets the server run `tox`, `pre-commit`, Python packaging, container builds, `gh` commands, and AI-assisted flows locally without paying the cost of repeated full clones.

## Structured Logging Flow

Every webhook carries a structured execution context from the moment background processing starts to the moment the final summary is written.

The flow looks like this:

1. `create_context()` stores a `WebhookContext` in a `ContextVar`.
2. Handlers call `start_step()`, `complete_step()`, and `fail_step()` for major workflow stages such as `repo_clone`, `pr_workflow_setup`, `pr_cicd_execution`, `check_merge_eligibility`, and `push_handler`.
3. Normal log messages are still written, but `JsonLogHandler` also serializes them as JSON `log_entry` records and enriches them with webhook metadata from the current context.
4. At the end of processing, `write_webhook_log()` writes one `webhook_summary` record with timing, PR metadata, token usage, workflow steps, and overall success or failure.

The summary writer stores those records as one JSON object per line in daily files:

```93:152:webhook_server/utils/structured_logger.py
def write_log(self, context: WebhookContext) -> None:
    """Write webhook context as JSONL entry to date-based log file."""
    completed_at = context.completed_at if context.completed_at else datetime.now(UTC)

    # Get context dict and update timing locally (without mutating context)
    context_dict = context.to_dict()
    context_dict["type"] = "webhook_summary"
    if "timing" in context_dict:
        context_dict["timing"]["completed_at"] = completed_at.isoformat()
        if context.started_at:
            duration_ms = int((completed_at - context.started_at).total_seconds() * 1000)
            context_dict["timing"]["duration_ms"] = duration_ms

    # Get log file path
    log_file = self._get_log_file_path(completed_at)

    # Serialize context to JSON (compact JSONL format - single line, no indentation)
    log_entry = json.dumps(context_dict, ensure_ascii=False)
    ...
    # Write JSON entry with single newline (JSONL format)
    os.write(temp_fd, f"{log_entry}\n".encode())
    ...
    with open(log_file, "a") as log_fd:
        ...
        log_fd.write(data.decode("utf-8"))
```

For operators, the important outputs are:

- Text logs for day-to-day reading.
- `log_entry` JSON records for individual log messages.
- `webhook_summary` JSON records for the complete end-to-end outcome of one delivery.
- Daily files named `webhooks_YYYY-MM-DD.json` under `{data_dir}/logs`.

If you enable `ENABLE_LOG_SERVER=true`, the application also exposes a log viewer and related APIs that read these same structured files for filtering, export, workflow-step drill-down, and live streaming.

> **Warning:** Treat the log viewer as an internal operations surface. It is only mounted when `ENABLE_LOG_SERVER=true`, and it should be exposed only on a trusted network boundary.

## Configuration That Changes the Flow

These root settings shape intake, logging, and bootstrap behavior:

```3:17:examples/config.yaml
log-level: INFO # Set global log level, change take effect immediately without server restart
log-file: webhook-server.log # Set global log file, change take effect immediately without server restart
mcp-log-file: mcp_server.log # Set global MCP log file, change take effect immediately without server restart
logs-server-log-file: logs_server.log # Set global Logs Server log file, change take effect immediately without server restart
mask-sensitive-data: true # Mask sensitive data in logs (default: true). Set to false for debugging (NOT recommended in production)

# Server configuration
disable-ssl-warnings: true # Disable SSL warnings (useful in production to reduce log noise from SSL certificate issues)

# ...
webhook-ip: <HTTP://IP OR URL:PORT/webhook_server> # Full URL with path (e.g., https://your-domain.com/webhook_server or https://smee.io/your-channel)
```

These repository settings determine which events are registered and what a PR or tag push actually does when it arrives:

```139:182:examples/config.yaml
repositories:
  my-repository:
    name: my-org/my-repository
    log-level: DEBUG # Override global log-level for repository
    log-file: my-repository.log # Override global log-file for repository
    mask-sensitive-data: false # Override global setting - disable masking for debugging this specific repo (NOT recommended in production)
    slack-webhook-url: <Slack webhook url> # Send notification to slack on several operations
    verified-job: true
    pypi:
      token: <PYPI TOKEN>

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
      build-args: # build args to send to podman build command
        - my-build-arg1=1
        - my-build-arg2=2
      args: # args to send to podman build command
        - --format docker
```

A few configuration rules are especially important when you are reasoning about the event flow:

- `repositories.<repo>.events` controls what GitHub sends to the server after startup sync.
- `tox`, `pre-commit`, `pypi`, `container`, `conventional-title`, and custom check-run settings decide which checks are queued and which local commands actually run.
- `protected-branches` shapes the status-check list that `can-be-merged` evaluates against.
- `mask-sensitive-data` controls whether secrets are scrubbed from text logs.
- `slack-webhook-url`, `test-oracle`, and AI features add side effects around the main PR pipeline, but they still fit into the same handler model.

> **Note:** Repository-local `.github-webhook-server.yaml` overrides matching values from the global `config.yaml`. That lets one server instance manage repositories with different PR rules, labels, checks, and release behavior without changing the intake architecture.

Put together, the architecture is straightforward: validate fast, process in the background, route by event type, work from one shared clone, isolate side effects in worktrees, and leave a structured trail behind for every delivery. That is what makes `github-webhook-server` feel responsive to GitHub while still doing substantial repository automation under the hood.
