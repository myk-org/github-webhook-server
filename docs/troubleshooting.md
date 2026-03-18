# Troubleshooting

Most problems with `github-webhook-server` fall into a few buckets: the server cannot find its config, the repository key in `config.yaml` does not match what GitHub sent, draft PR commands are being blocked on purpose, Podman runtime state is stale, or the webhook was accepted but later failed during background processing.

> **Tip:** Validate the config file before restarting the service:
```bash
uv run webhook_server/tests/test_schema_validator.py /home/podman/data/config.yaml
```

## Startup Problems

By default, the server reads `config.yaml` from `WEBHOOK_SERVER_DATA_DIR`, or `/home/podman/data` if that environment variable is unset. It also fails fast if the root `repositories:` section is missing.

```20:33:webhook_server/libs/config.py
        self.data_dir: str = os.environ.get("WEBHOOK_SERVER_DATA_DIR", "/home/podman/data")
        self.config_path: str = os.path.join(self.data_dir, "config.yaml")
        self.repository = repository
        self.exists()
        self.repositories_exists()
        self.validate_labels_config()

    def exists(self) -> None:
        if not os.path.isfile(self.config_path):
            raise FileNotFoundError(f"Config file {self.config_path} not found")

    def repositories_exists(self) -> None:
        if not self.root_data.get("repositories"):
            raise ValueError(f"Config {self.config_path} does not have `repositories`")
```

If you are using the example container setup, the mounted data directory is expected to contain the server data files, including `config.yaml` and `webhook-server.private-key.pem`.

```5:8:examples/docker-compose.yaml
    volumes:
      - "./webhook_server_data_dir:/home/podman/data:Z" # Should include config.yaml and webhook-server.private-key.pem
      # Mount temporary directories to prevent boot ID mismatch issues
      - "/tmp/podman-storage-${USER:-1000}:/tmp/storage-run-1000"
```

Check these first:
- `config.yaml` exists in the mounted data directory.
- `repositories:` exists and is not empty.
- `webhook-server.private-key.pem` exists next to `config.yaml`.
- `github-app-id` matches the GitHub App you actually installed.

If startup only fails after you enable IP verification, that is usually a network reachability problem. The app intentionally fails closed when `verify-github-ips` or `verify-cloudflare-ips` is enabled but it cannot load any allowlist data.

### Settings Changed But Behavior Did Not

> **Note:** In the current startup path, `ip-bind`, `port`, `max-workers`, and `webhook-secret` are read from `config.yaml`, not from environment variables such as `WEBHOOK_SERVER_PORT` or `WEBHOOK_SECRET`.

```11:16:entrypoint.py
_config = Config()
_root_config = _config.root_data
_ip_bind = _root_config.get("ip-bind", "0.0.0.0")
_port = _root_config.get("port", 5000)
_max_workers = _root_config.get("max-workers", 10)
_webhook_secret = _root_config.get("webhook-secret")
```

Direct environment reads in the current code are limited to `WEBHOOK_SERVER_DATA_DIR`, plus optional switches such as `ENABLE_LOG_SERVER` and `ENABLE_MCP_SERVER`. If changing container env vars seems to do nothing, move those settings into `config.yaml` first.

## Webhook Accepted But No Automation Ran

The webhook endpoint is designed to return quickly and do the real work in the background. That is great for reliability, but it also means a successful GitHub delivery does not guarantee successful processing.

> **Note:** `200 OK` means “queued”, not “completed”. When something looks wrong, use the `delivery_id` from the response or the `X-GitHub-Delivery` header to trace the webhook in the logs.

Common background-only failures include:
- `Repository not found in configuration`
- GitHub API or network connectivity errors
- GitHub App installation/access problems
- Podman build or push failures

A good first step is to search the logs for the `delivery_id`, then inspect the step timeline if the log server is enabled.

## Repository Lookup Failures

A very common configuration mistake is using the full `owner/repo` as the top-level key under `repositories`. The server does not look up repositories that way. It looks up the short GitHub repository name from the webhook payload.

```89:112:webhook_server/libs/github_api.py
        self.repository_name: str = hook_data["repository"]["name"]
        self.repository_full_name: str = hook_data["repository"]["full_name"]
        self._bg_tasks: set[Task[Any]] = set()
        self.parent_committer: str = ""
        self.x_github_delivery: str = headers.get("X-GitHub-Delivery", "")
        self.github_event: str = headers["X-GitHub-Event"]
        self.config = Config(repository=self.repository_name, logger=self.logger)
        ...
        if not self.config.repository_data:
            raise RepositoryNotFoundInConfigError(f"Repository {self.repository_name} not found in config file")
```

The example config shows the intended shape clearly: the map key is the short repo name, while the nested `name:` field is the full `owner/repo`.

```139:145:examples/config.yaml
repositories:
  my-repository:
    name: my-org/my-repository
    log-level: DEBUG # Override global log-level for repository
    log-file: my-repository.log # Override global log-file for repository
    mask-sensitive-data: false # Override global setting - disable masking for debugging this specific repo (NOT recommended in production)
```

If those do not line up, you can get a successful delivery with no useful automation, because the background worker logs `Repository not found in configuration` and stops there.

If the config key is correct and you instead see errors mentioning `manage-repositories-app`, check GitHub App setup next:
- The server reads `webhook-server.private-key.pem` from the data directory.
- It uses `github-app-id` from `config.yaml`.
- The `manage-repositories-app` must be installed on the target repository.

You can also hit PR lookup failures on `status` or `check_run` deliveries. Those events are matched back to an open PR by PR number, head SHA, or commit SHA. If the PR is already closed, the SHA no longer matches, or the app/token cannot read the repo, the event may be skipped.

## Draft PR Commands Are Skipped

Draft PR behavior is intentionally conservative. If you do nothing, commands on draft PRs are blocked by default.

The example config documents the three supported modes:

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

The handler follows that config exactly:

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

That leads to a few easy-to-miss behaviors:
- No `allow-commands-on-draft-prs` setting: commands are blocked by default.
- `allow-commands-on-draft-prs: []`: all draft PR commands are allowed.
- A non-empty list: only the listed commands are allowed.
- `/test-oracle` is the exception and is allowed even on draft PRs.
- If the setting is missing or not a YAML list, a blocked command can look like a silent no-op.
- This setting only affects draft `issue_comment` command handling. Other draft PR event processing is still skipped.

> **Tip:** If you only need limited draft automation, whitelist a small set such as `retest` or `build-and-push-container` instead of allowing everything.

## Podman Runtime and Container Build Problems

The code has a built-in workaround for the Podman boot-ID cache problem. If a command fails with the well-known reboot error, the handler removes stale runtime directories and retries the command once.

```166:193:webhook_server/libs/handlers/runner_handler.py
    def is_podman_bug(self, err: str) -> bool:
        _err = "Error: current system boot ID differs from cached boot ID; an unhandled reboot has occurred"
        return _err in err.strip()

    def fix_podman_bug(self) -> None:
        self.logger.debug(f"{self.log_prefix} Fixing podman bug")
        shutil.rmtree("/tmp/storage-run-1000/containers", ignore_errors=True)
        shutil.rmtree("/tmp/storage-run-1000/libpod/tmp", ignore_errors=True)

    async def run_podman_command(self, command: str, redact_secrets: list[str] | None = None) -> tuple[bool, str, str]:
        rc, out, err = await run_command(
            command=command,
            log_prefix=self.log_prefix,
            redact_secrets=redact_secrets,
            mask_sensitive=self.github_webhook.mask_sensitive,
        )
        ...
        if self.is_podman_bug(err=err):
            self.fix_podman_bug()
            return await run_command(
                command=command,
                log_prefix=self.log_prefix,
                redact_secrets=redact_secrets,
                mask_sensitive=self.github_webhook.mask_sensitive,
            )
```

The example container setup already hints at the recommended fix by mounting `/tmp/storage-run-1000` from the host. The same example service also runs with `privileged: true`, which is worth keeping if you are running Podman inside the container.

If container builds still fail:
- Keep the `/tmp/storage-run-1000` bind mount from `examples/docker-compose.yaml`.
- Keep `privileged: true` when using Podman in the service container.
- Restart the service after a host reboot so the startup cleanup runs again.
- Check the repository `container:` block in `config.yaml`.
- If a PR comment says `No build-and-push-container configured for this repository`, add that `container:` block first.
- If build succeeds but push fails, recheck registry username, password, repository path, and any custom `build-args` or `args`.

## Signature and Payload Errors

Failures before background processing starts return real HTTP errors. The most common ones are:
- `Missing X-GitHub-Event header`
- `Missing repository in payload`
- `Missing repository.name in payload`
- `Missing repository.full_name in payload`
- `x-hub-signature-256 header is missing!`
- `Request signatures didn't match!`

If you configured `webhook-secret`, GitHub must send a matching `x-hub-signature-256` header for the exact request body the server receives. A wrong secret, a proxy/relay that rewrites the body, or hand-crafted test requests are the usual causes.

## Observability Tips

When the behavior is unclear, follow the webhook through the logs before changing config again.

The server writes structured daily webhook summaries under the data directory as JSONL files:

```74:91:webhook_server/utils/structured_logger.py
        self.log_dir = Path(self.config.data_dir) / "logs"
        ...
    def _get_log_file_path(self, date: datetime | None = None) -> Path:
        """Get log file path for the specified date.
        ...
        if date is None:
            date = datetime.now(UTC)
        date_str = date.strftime("%Y-%m-%d")
        return self.log_dir / f"webhooks_{date_str}.json"
```

That gives you a stable place to start when a webhook was accepted but did not do what you expected.

Useful routes when `ENABLE_LOG_SERVER=true`:
- `/logs` for the browser UI
- `/logs/api/entries?hook_id=<delivery_id>` for filtered log lines
- `/logs/api/workflow-steps/<delivery_id>` for the step timeline
- `/logs/api/pr-flow/<delivery_id>` for PR workflow visualization
- `/logs/api/step-logs/<delivery_id>/<step_name>` for logs within a single step
- `/logs/ws` for real-time streaming

A good investigation flow is:
1. Capture the GitHub `delivery_id`.
2. Search `webhook_server.log` or `webhooks_YYYY-MM-DD.json` for that ID.
3. If the log server is enabled, open `/logs/api/entries?hook_id=<delivery_id>`.
4. If the webhook queued but failed later, inspect `/logs/api/workflow-steps/<delivery_id>` next.

> **Warning:** Treat the log viewer as an internal tool. The project expects it to live on trusted networks, and the step-log endpoint is explicitly restricted to trusted clients.

If `/logs` returns 404, the log server is not enabled. Set `ENABLE_LOG_SERVER=true` and restart the service.

## Quick Symptom Guide

- `Config file /home/podman/data/config.yaml not found`: the data directory mount or `WEBHOOK_SERVER_DATA_DIR` is wrong.
- `Config ... does not have repositories`: `repositories:` is missing or empty.
- GitHub delivery says success, but nothing happened: the webhook was queued, then failed later in background processing.
- `Repository not found in configuration`: the top-level key under `repositories:` does not match GitHub’s short repo name.
- `Repository owner/repo not found by manage-repositories-app`: the GitHub App is not installed on that repository, or the app credentials are wrong.
- Draft PR commands do nothing: `allow-commands-on-draft-prs` is missing, mis-typed, or does not include that command.
- `Command /... is not allowed on draft PRs`: the draft whitelist is working; add the command to the list or mark the PR ready.
- `current system boot ID differs from cached boot ID`: stale Podman runtime state; keep the runtime mount and restart after cleanup.
- `No build-and-push-container configured for this repository`: the repository has no `container:` section.
- `Request signatures didn't match!`: the configured webhook secret and the GitHub webhook secret do not match.
