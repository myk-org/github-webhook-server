# AI Features and Test Oracle

This project has two separate AI-related capabilities:

- `conventional-title` validates pull request titles against the Conventional Commits format.
- `ai-features.conventional-title` adds AI help when that validation fails.
- `test-oracle` sends PR data to an external [pr-test-oracle](https://github.com/myk-org/pr-test-oracle) service that recommends which tests to run.

> **Note:** `conventional-title` and `ai-features.conventional-title` are different settings. `conventional-title` enables the rule and defines the allowed types. `ai-features.conventional-title` controls whether AI suggests or auto-fixes a title when that rule fails.

## Configuration

The shipped example configuration shows `test-oracle` and `ai-features` at the root level:

```112:134:examples/config.yaml
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

# AI Features configuration
# Enables AI-powered enhancements (e.g., conventional title suggestions)
ai-features:
  ai-provider: "claude" # claude | gemini | cursor
  ai-model: "claude-opus-4-6[1m]"
  conventional-title:
    enabled: true
    mode: suggest  # suggest: show in checkrun | fix: auto-update PR title
    timeout-minutes: 10
```

The schema also allows `test-oracle` and `ai-features` under a repository entry in `config.yaml` if you want per-repository behavior.

The title validation rule itself is configured per repository:

```197:220:examples/config.yaml
    # Conventional Commits validation
    # Enforces Conventional Commits v1.0.0 specification for PR titles
    # Format: <type>[optional scope]: <description>
    #
    # Standard types (recommended):
    #   - feat: New features (triggers MINOR version bump in semver)
    #   - fix: Bug fixes (triggers PATCH version bump in semver)
    #   - build, chore, ci, docs, style, refactor, perf, test, revert
    #
    # Custom types: You can define your own types! The spec allows any noun.
    # Examples: my-title, hotfix, release, custom
    #
    # Valid PR title examples:
    #   - feat: add user authentication
    #   - fix(parser): handle edge case in XML parsing
    #   - feat!: breaking API change to authentication
    #   - my-title: custom type example
    #   - hotfix(api): resolve production issue
    #
    # Use "*" to accept any type while enforcing the format
    # conventional-title: "*"
    #
    # Resources: https://www.conventionalcommits.org/en/v1.0.0/
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
```

`conventional-title` enforces the format `<type>[optional scope]: <description>`. In practice, that means:

- The type must match the configured comma-separated list, unless you use `*`.
- `*` keeps the format check but allows any type token.
- Scope is optional and must appear as `(scope)`.
- A breaking-change marker `!` is allowed before the colon.
- The separator must be `: `.
- The description after `: ` must not be empty.
- Custom types are valid when you list them in `conventional-title`.

Once configured, `conventional-title` becomes a built-in PR check run. It is part of normal PR processing, it is rerun when the PR title changes, and it is available as `/retest conventional-title`.

> **Tip:** Start by enabling `conventional-title` on a single repository. It gives contributors immediate feedback in GitHub without requiring any AI integration at all.

## AI suggestion and auto-fix modes

The `ai-features.conventional-title` section controls what happens after a title fails validation:

- `mode: suggest` keeps the check failing, but adds an AI-generated title recommendation to the check output.
- `mode: fix` validates the AI suggestion first. If the suggestion is valid and different from the current title, the server edits the PR title and marks the check successful.
- `timeout-minutes` defaults to `10`.

The core behavior is implemented here:

```525:580:webhook_server/libs/handlers/runner_handler.py
            # AI-suggested title (if ai-features configured)
            ai_suggestion = await self._get_ai_title_suggestion(
                pull_request=pull_request,
                title=title,
                allowed_names=allowed_names,
                is_wildcard=is_wildcard,
            )

            ai_mode = self._get_ai_conventional_title_mode()

            if ai_suggestion and ai_mode == "fix":
                # Validate the suggestion before applying
                if is_wildcard:
                    suggestion_valid = bool(re.match(r"^[\w-]+(\([^)]+\))?!?: .+", ai_suggestion))
                else:
                    suggestion_valid = any(
                        re.match(rf"^{re.escape(_name)}(\([^)]+\))?!?: .+", ai_suggestion) for _name in allowed_names
                    )

                if suggestion_valid and ai_suggestion != title:
                    self.logger.info(f"{self.log_prefix} AI fixing PR title from '{title}' to '{ai_suggestion}'")
                    try:
                        await asyncio.to_thread(pull_request.edit, title=ai_suggestion)
                        output["title"] = "Conventional Title"
                        output["summary"] = "PR title auto-fixed by AI"
                        output["text"] = (
                            f"**AI Auto-Fix Applied**\n\n"
                            f"Title updated from: `{title}`\n"
                            f"Title updated to: `{ai_suggestion}`\n"
                        )
                        return await self.check_run_handler.set_check_success(
                            name=CONVENTIONAL_TITLE_STR, output=output
                        )
                    except Exception:
                        self.logger.exception(f"{self.log_prefix} Failed to auto-fix PR title")
                        if output["text"] is not None:
                            output["text"] += (
                                f"\n\n---\n\n### AI Auto-Fix Failed\n\n"
                                f"Suggested title: `{ai_suggestion}`\n"
                                f"Failed to update PR title automatically. Please update manually."
                            )
                else:
                    self.logger.warning(
                        f"{self.log_prefix} AI suggestion invalid or unchanged, skipping auto-fix: {ai_suggestion}"
                    )
                    if output["text"] is not None:
                        output["text"] += (
                            f"\n\n---\n\n### AI Auto-Fix Skipped\n\n"
                            f"AI suggested: `{ai_suggestion}`\n"
                            f"Suggestion was invalid or unchanged."
                        )

            elif ai_suggestion and ai_mode == "suggest" and output["text"] is not None:
                output["text"] += f"\n\n---\n\n### AI-Suggested Title\n\n> {ai_suggestion}\n"

            await self.check_run_handler.set_check_failure(name=CONVENTIONAL_TITLE_STR, output=output)
```

This is worth understanding before you enable `fix` mode:

- The server does not blindly apply whatever the AI returns.
- It validates the suggestion against the same title rules you configured.
- If the suggestion is invalid, unchanged, or the GitHub edit fails, the check stays failing and explains why.

> **Note:** AI assistance is best-effort. If the AI CLI fails, times out, or returns an unusable title, the conventional-title check still completes and the normal validation result is shown.

> **Tip:** `mode: suggest` is the safer starting point. Switch to `mode: fix` only after you are comfortable letting the server edit PR titles automatically.

## Supported AI providers

Both `ai-features` and `test-oracle` support the same provider list:

- `claude`
- `gemini`
- `cursor`

Each feature also requires `ai-model`. The schema treats the model name as a string, so use the identifier expected by your provider tooling or oracle service, such as `sonnet`, `claude-opus-4-6[1m]`, or `gemini-2.5-pro`.

## PR Test Oracle

`test-oracle` is separate from title validation. Instead of checking naming rules, it calls an external service that looks at the PR and recommends which tests to run.

This is the relevant part of the implementation:

```31:85:webhook_server/libs/test_oracle.py
    config: dict[str, Any] | None = github_webhook.config.get_value("test-oracle")
    if not config:
        return

    if trigger is not None:
        triggers: list[str] = config.get("triggers", DEFAULT_TRIGGERS)
        if trigger not in triggers:
            github_webhook.logger.debug(
                f"{github_webhook.log_prefix} Test oracle trigger '{trigger}' not in configured triggers {triggers}"
            )
            return

    server_url: str = config["server-url"]
    log_prefix: str = github_webhook.log_prefix

    try:
        async with httpx.AsyncClient(base_url=server_url) as client:
            # Health check
            try:
                health_response = await client.get("/health", timeout=5.0)
                health_response.raise_for_status()
            except httpx.HTTPError as e:
                status_info = ""
                if isinstance(e, httpx.HTTPStatusError):
                    status_info = f" (status {e.response.status_code})"

                msg = f"Test Oracle server at {server_url} is not responding{status_info}, skipping test analysis"
                github_webhook.logger.warning(f"{log_prefix} {msg}")
                try:
                    await asyncio.to_thread(
                        pull_request.create_issue_comment,
                        f"Test Oracle server is not responding{status_info}, skipping test analysis",
                    )
                except Exception:
                    github_webhook.logger.exception(f"{log_prefix} Failed to post health check comment")
                return

            # Build analyze payload
            pr_url: str = await asyncio.to_thread(lambda: pull_request.html_url)
            payload: dict[str, Any] = {
                "pr_url": pr_url,
                "ai_provider": config["ai-provider"],
                "ai_model": config["ai-model"],
                # Token is required by the oracle server to fetch PR data and post reviews.
                # Server URL is configured by the admin - they control the network setup.
                "github_token": github_webhook.token,
            }

            if "test-patterns" in config:
                payload["test_patterns"] = config["test-patterns"]

            # Call analyze
            try:
                github_webhook.logger.info(f"{log_prefix} Calling Test Oracle for {pr_url}")
                response = await client.post("/analyze", json=payload, timeout=300.0)
```

For users, the important behavior is:

- If `test-oracle` is not configured, nothing runs.
- Automatic trigger filtering only applies when the function is called with a named trigger.
- The oracle service is checked with `GET /health` before analysis starts.
- The analyze request sends `pr_url`, `ai_provider`, `ai_model`, `github_token`, and optional `test_patterns`.

> **Warning:** The server sends a GitHub token to the oracle service so it can fetch PR data and post results. Only point `server-url` at a service you trust.

## Triggers and manual runs

The automatic trigger names are:

- `approved`
- `pr-opened`
- `pr-synchronized`

> **Warning:** `approved` does not mean a plain GitHub approval review. In this project, it means someone used `/approve`. A review with GitHub state `approved` but without `/approve` does not trigger the oracle.

A review body containing `/approve` triggers the `approved` path:

```58:78:webhook_server/libs/handlers/pull_request_review_handler.py
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

PR open and PR synchronize events trigger `pr-opened` and `pr-synchronized`:

```109:145:webhook_server/libs/handlers/pull_request_handler.py
            if hook_action == "opened":
                task = asyncio.create_task(
                    call_test_oracle(
                        github_webhook=self.github_webhook,
                        pull_request=pull_request,
                        trigger="pr-opened",
                    )
                )
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)

            if self.ctx:
                self.ctx.complete_step("pr_handler", action=hook_action)
            return

        if hook_action == "synchronize":
            sync_tasks: list[Coroutine[Any, Any, Any]] = []

            sync_tasks.append(self.process_opened_or_synchronize_pull_request(pull_request=pull_request))
            sync_tasks.append(self.remove_labels_when_pull_request_sync(pull_request=pull_request))

            results = await asyncio.gather(*sync_tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    self.logger.error(f"{self.log_prefix} Async task failed: {result}")

            task = asyncio.create_task(
                call_test_oracle(
                    github_webhook=self.github_webhook,
                    pull_request=pull_request,
                    trigger="pr-synchronized",
                )
            )
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
```

Manual runs are also supported:

- The `/test-oracle` issue comment command always works when `test-oracle` is configured, even if the trigger list does not include the current event.
- `/test-oracle` is intentionally exempt from the normal draft-PR command restriction.
- `approved` is the default automatic trigger if you omit `triggers`.
- `reopened` and `ready_for_review` do not automatically invoke test oracle.

## Failure behavior

The oracle integration is deliberately forgiving:

- If the health check fails, the server adds a PR comment explaining that the oracle is unavailable and skips analysis.
- If the later `/analyze` request fails or returns invalid JSON, the error is logged and webhook processing continues.
- A broken oracle does not stop the rest of the PR automation pipeline.

> **Tip:** A conservative rollout is to enable `conventional-title`, set `ai-features.conventional-title.mode: suggest`, and keep `test-oracle.triggers` at its default `approved`. Once that works well for your team, you can switch title handling to `fix` or add `pr-opened` and `pr-synchronized` for broader test analysis.
