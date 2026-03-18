# Logging and Data Files

`github-webhook-server` keeps its persistent configuration, key material, and logs under a single data directory. By default that directory is `/home/podman/data`, and you can move it with `WEBHOOK_SERVER_DATA_DIR`.

```20:21:webhook_server/libs/config.py
self.data_dir: str = os.environ.get("WEBHOOK_SERVER_DATA_DIR", "/home/podman/data")
self.config_path: str = os.path.join(self.data_dir, "config.yaml")
```

The example container setup mounts a host directory directly to that path and calls out the files that should already exist there:

```5:20:examples/docker-compose.yaml
volumes:
  - "./webhook_server_data_dir:/home/podman/data:Z" # Should include config.yaml and webhook-server.private-key.pem
  # Mount temporary directories to prevent boot ID mismatch issues
  - "/tmp/podman-storage-${USER:-1000}:/tmp/storage-run-1000"
environment:
  - PUID=1000
  - PGID=1000
  - TZ=Asia/Jerusalem
  - MAX_WORKERS=50 # Defaults to 10 if not set
  - WEBHOOK_SERVER_IP_BIND=0.0.0.0 # IP to listen
  - WEBHOOK_SERVER_PORT=5000 # Port to listen
  - WEBHOOK_SECRET=<secret> # If set verify hook is a valid hook from Github
  - VERIFY_GITHUB_IPS=1 # Verify hook request is from GitHub IPs
  - VERIFY_CLOUDFLARE_IPS=1 # Verify hook request is from Cloudflare IPs
  - ENABLE_LOG_SERVER=true # Enable log viewer endpoints (default: false)
  - ENABLE_MCP_SERVER=false # Enable MCP server for AI agent integration (default: false)
```

## Data Directory Layout

Common files and directories you will see:

- `config.yaml`: the main server configuration file.
- `webhook-server.private-key.pem`: the GitHub App private key.
- `logs/`: the main logging directory.
- `logs/<log-file>`: the main human-readable application log.
- `logs/<log-file>.1`, `logs/<log-file>.2`, and so on: rotated text logs.
- `logs/webhooks_YYYY-MM-DD.json`: structured webhook log files, one file per UTC day.
- `logs/logs_server.log`: dedicated log viewer log when the log viewer is enabled.
- `logs/mcp_server.log`: dedicated MCP server log when MCP support is enabled.

The private key is read from the data directory root, not from `logs/`:

```410:417:webhook_server/utils/github_repository_settings.py
def get_repository_github_app_api(config_: Config, repository_name: str) -> Github | None:
    LOGGER.debug("Getting repositories GitHub app API")

    with open(os.path.join(config_.data_dir, "webhook-server.private-key.pem")) as fd:
        private_key = fd.read()

    github_app_id: int = config_.root_data["github-app-id"]
```

> **Note:** The `logs/` directory is created automatically when the server needs it.

## Configure Log Files

The example config shows the main logging settings:

```3:7:examples/config.yaml
log-level: INFO # Set global log level, change take effect immediately without server restart
log-file: webhook-server.log # Set global log file, change take effect immediately without server restart
mcp-log-file: mcp_server.log # Set global MCP log file, change take effect immediately without server restart
logs-server-log-file: logs_server.log # Set global Logs Server log file, change take effect immediately without server restart
mask-sensitive-data: true # Mask sensitive data in logs (default: true). Set to false for debugging (NOT recommended in production)
```

You can also override the text log file and masking behavior for a specific repository:

```139:144:examples/config.yaml
repositories:
  my-repository:
    name: my-org/my-repository
    log-level: DEBUG # Override global log-level for repository
    log-file: my-repository.log # Override global log-file for repository
    mask-sensitive-data: false # Override global setting - disable masking for debugging this specific repo (NOT recommended in production)
```

Relative filenames are resolved inside `<data-dir>/logs/`. If you give an absolute path, the server uses it as-is:

```130:149:webhook_server/utils/helpers.py
def get_log_file_path(config: Config, log_file_name: str | None) -> str | None:
    """
    Resolve the full path for a log file using the configuration data directory.

    Args:
        config: Config object containing data_dir
        log_file_name: Name of the log file (e.g., "server.log")

    Returns:
        Full path to the log file, or None if log_file_name is None
    """
    if log_file_name and not log_file_name.startswith("/"):
        log_file_path = os.path.join(config.data_dir, "logs")

        if not os.path.isdir(log_file_path):
            os.makedirs(log_file_path, exist_ok=True)

        return os.path.join(log_file_path, log_file_name)

    return log_file_name
```

> **Tip:** Use repository-level overrides when you need extra visibility for one repository without changing logging for every repository.

## Rotating Text Logs

Text logs are the easiest place to read day-to-day activity. They use size-based rotation and are designed not to crash the server if rotated files have already been removed.

The project swaps in a safe rotating handler and sets a 10 MiB max file size for logger-managed text files:

```36:38:webhook_server/utils/helpers.py
# Patch simple_logger to use SafeRotatingFileHandler to prevent crashes
# when backup log files are missing during rollover
simple_logger.logger.RotatingFileHandler = SafeRotatingFileHandler
```

```96:103:webhook_server/utils/helpers.py
logger = get_logger(
    name=logger_cache_key,
    filename=log_file_path_resolved,
    level=log_level,
    file_max_bytes=1024 * 1024 * 10,
    mask_sensitive=mask_sensitive,
    mask_sensitive_patterns=mask_sensitive_patterns,
    console=True,  # Enable console output for docker logs with FORCE_COLOR support
)
```

During rollover, the handler works with the standard rotated filenames such as `.1`, `.2`, and `.3`, but suppresses file-operation errors so logging can continue:

```65:111:webhook_server/utils/safe_rotating_handler.py
if self.backupCount > 0:
    # Remove backup files that exceed backupCount, handle missing files
    for i in range(self.backupCount - 1, 0, -1):
        sfn = self.rotation_filename(f"{self.baseFilename}.{i}")
        dfn = self.rotation_filename(f"{self.baseFilename}.{i + 1}")
        if os.path.exists(sfn):
            try:
                if os.path.exists(dfn):
                    os.remove(dfn)
                os.rename(sfn, dfn)
            except FileNotFoundError:
                # File was deleted between exists check and operation - ignore
                pass
            except OSError:
                # Broad suppression intentional: logging must never crash.
                # See module docstring for full rationale.
                pass

    dfn = self.rotation_filename(f"{self.baseFilename}.1")
    try:
        if os.path.exists(dfn):
            os.remove(dfn)
    except FileNotFoundError:
        # File was deleted between exists check and remove - ignore
        pass
    except OSError:
        # Broad suppression intentional: logging must never crash.
        # See module docstring for full rationale.
        pass

    try:
        self.rotate(self.baseFilename, dfn)
    except FileNotFoundError:
        # Base file was deleted - just create a new one
        pass
    except OSError:
        # Broad suppression intentional: logging must never crash.
        # See module docstring for full rationale.
        pass

if not self.delay:
    try:
        self.stream = self._open()
    except OSError:
        # Cannot open new log file - leave stream as None.
        # FileHandler.emit() will attempt to open on next log entry.
        pass
```

In practice, if `log-file` is `webhook-server.log`, expect a current file like `logs/webhook-server.log` plus rotated siblings such as `logs/webhook-server.log.1`.

## Structured JSONL Webhook Logs

The server also writes structured webhook data into daily files named `webhooks_YYYY-MM-DD.json` under `logs/`.

Despite the `.json` extension, these files use JSON Lines: one compact JSON object per line.

```79:122:webhook_server/utils/structured_logger.py
def _get_log_file_path(self, date: datetime | None = None) -> Path:
    """Get log file path for the specified date.

    Args:
        date: Date for the log file (defaults to current UTC date)

    Returns:
        Path to the log file (e.g., {log_dir}/webhooks_2026-01-05.json)
    """
    if date is None:
        date = datetime.now(UTC)
    date_str = date.strftime("%Y-%m-%d")
    return self.log_dir / f"webhooks_{date_str}.json"

def write_log(self, context: WebhookContext) -> None:
    """Write webhook context as JSONL entry to date-based log file.

    Writes a compact JSON entry (single line, no indentation) containing complete webhook execution context.
    Each entry is terminated by a newline character.
    Uses atomic write pattern (temp file + rename) with file locking for safety.

    Args:
        context: WebhookContext to serialize and write

    Note:
        Uses context.completed_at as source of truth, falls back to datetime.now(UTC)
    """
    # Prefer context.completed_at as source of truth, fall back to current time
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
```

These files contain two important entry types:

- `webhook_summary`: one end-of-webhook summary with timing, workflow steps, success state, and errors.
- `log_entry`: individual log records enriched with webhook context when that context exists.

A `log_entry` record is built like this:

```105:132:webhook_server/utils/json_log_handler.py
message = record.getMessage()
message = _ANSI_ESCAPE_RE.sub("", message)

exc_text: str | None = None
if record.exc_info and record.exc_info[0] is not None:
    exc_text = "".join(traceback.format_exception(*record.exc_info))

entry: dict[str, object] = {
    "type": "log_entry",
    "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
    "level": record.levelname,
    "logger_name": record.name,
    "message": message,
}

if exc_text:
    entry["exc_info"] = exc_text

# Enrich with webhook context when available
ctx = get_context()
if ctx is not None:
    entry["hook_id"] = ctx.hook_id
    entry["event_type"] = ctx.event_type
    entry["repository"] = ctx.repository
    entry["pr_number"] = ctx.pr_number
    entry["api_user"] = ctx.api_user
```

A `webhook_summary` carries the higher-level fields you usually want when debugging a delivery:

```374:405:webhook_server/utils/context.py
return {
    "hook_id": self.hook_id,
    "level": self._derive_level(),
    "status": self._derive_status(),
    "event_type": self.event_type,
    "action": self.action,
    "sender": self.sender,
    "repository": self.repository,
    "repository_full_name": self.repository_full_name,
    "pr": {
        "number": self.pr_number,
        "title": self.pr_title,
        "author": self.pr_author,
    }
    if self.pr_number
    else None,
    "api_user": self.api_user,
    "timing": {
        "started_at": self.started_at.isoformat(),
        "completed_at": (self.completed_at.isoformat() if self.completed_at else None),
        "duration_ms": int((self.completed_at - self.started_at).total_seconds() * 1000)
        if self.completed_at
        else None,
    },
    "workflow_steps": self.workflow_steps,
    "token_spend": self.token_spend,
    "initial_rate_limit": self.initial_rate_limit,
    "final_rate_limit": self.final_rate_limit,
    "success": self.success,
    "error": self.error,
    "summary": self._build_summary(),
}
```

> **Note:** `webhooks_*.json` is date-split, not size-rotated. The server creates a new file each UTC day, but it does not roll these files over by size. If you keep logs for a long time, plan your own retention or archival policy.

A practical detail: the server always tries to write a structured summary at the end of webhook processing, even after failures. If you need the most reliable delivery-level record, start with `webhooks_*.json`.

## How Masking Works

Masking is enabled by default with `mask-sensitive-data: true`. The logger treats common secret and credential patterns as sensitive:

```47:78:webhook_server/utils/helpers.py
mask_sensitive_patterns: list[str] = [
    # Passwords and secrets
    "container_repository_password",
    "password",
    "secret",
    # Tokens and API keys
    "token",
    "apikey",
    "api_key",
    "github_token",
    "GITHUB_TOKEN",
    "pypi",
    # Authentication credentials
    "username",
    "login",
    "-u",
    "-p",
    "--username",
    "--password",
    "--creds",
    # Private keys and sensitive IDs
    "private_key",
    "private-key",
    "webhook_secret",
    "webhook-secret",
    "github-app-id",
    # Slack webhooks (contain sensitive URLs)
    "slack-webhook-url",
    "slack_webhook_url",
    "webhook-url",
    "webhook_url",
]
```

In practice, this means:

- Tokens, passwords, webhook secrets, and similar values are masked in log output by default.
- Command helpers redact explicitly supplied secrets before writing command lines, stdout, or stderr to the logs.
- Repository-level `mask-sensitive-data` can override the global setting for one repository.

> **Warning:** Setting `mask-sensitive-data: false` can expose credentials in your logs. Use it only for short-lived debugging in a controlled environment.

## Log Separation

This project intentionally separates logs by purpose.

- The main text log is for readable application activity.
- `webhooks_*.json` is for structured webhook diagnostics and analysis.
- `logs_server.log` is for the log viewer itself.
- `mcp_server.log` is for the optional MCP server.

That separation is enforced in the logger setup. The structured JSON handler is attached only to the default webhook/application logger, not to infrastructure loggers that are created with an explicit filename:

```106:125:webhook_server/utils/helpers.py
# Attach JsonLogHandler for writing log records to the webhook JSONL file.
# Only attach when:
# - A log file path is configured (skip console-only loggers)
# - The logger is for the main webhook log (log_file_name not explicitly set)
#   Infrastructure loggers (mcp_server.log, logs_server.log) must NOT write
#   to webhooks_*.json because their entries lack webhook context (hook_id,
#   event_type, etc.) and pollute the webhook log with noise entries.
# - Only once per logger instance to avoid duplicate handlers.
# Uses _config.data_dir/logs (same directory as StructuredLogWriter) instead
# of deriving from the text log file path, which may differ for absolute paths.
if log_file_path_resolved and not log_file_name:
    log_dir = os.path.join(_config.data_dir, "logs")
    with _JSON_HANDLER_LOCK:
        if not any(isinstance(h, JsonLogHandler) and h.log_dir == Path(log_dir) for h in logger.handlers):
            logger.addHandler(
                JsonLogHandler(
                    log_dir=log_dir,
                    level=getattr(logging, log_level.upper(), logging.DEBUG),
                )
            )
```

That last comment matters: even if you point `log-file` at an absolute path somewhere else, the structured `webhooks_*.json` files still stay under `<data-dir>/logs/`.

The log viewer gets its own dedicated logger:

```547:554:webhook_server/app.py
if _log_viewer_controller_singleton is None:
    # Use global LOGGER for config operations
    config = Config(logger=LOGGER)
    logs_server_log_file = config.get_value("logs-server-log-file", return_on_none="logs_server.log")

    # Create dedicated logger for log viewer
    log_viewer_logger = get_logger_with_params(log_file_name=logs_server_log_file)
    _log_viewer_controller_singleton = LogViewerController(logger=log_viewer_logger)
```

The same pattern is used for MCP logging during startup:

```176:192:webhook_server/app.py
# Configure MCP logging separation
if MCP_SERVER_ENABLED:
    mcp_log_file = root_config.get("mcp-log-file", "mcp_server.log")

    # Use get_logger_with_params to reuse existing logging configuration logic
    # (rotation, sensitive data masking, formatting)
    # This returns a logger configured for the specific file
    mcp_file_logger = get_logger_with_params(log_file_name=mcp_log_file)

    # Add the configured handler to the actual MCP logger and stop propagation
    # This ensures MCP logs go ONLY to mcp_server.log and not webhook_server.log
    if mcp_file_logger.handlers:
        for handler in mcp_file_logger.handlers:
            mcp_logger.addHandler(handler)

        mcp_logger.propagate = False
```

## Log Viewer Files

When enabled, the log viewer reads the same files from `<data-dir>/logs/`; it does not build a separate database.

It scans current text logs, rotated text logs, and structured webhook files:

```1087:1136:webhook_server/web/log_viewer.py
# Find all log files including rotated ones and JSON files
log_files: list[Path] = []
log_files.extend(log_dir.glob("*.log"))
log_files.extend(log_dir.glob("*.log.*"))
log_files.extend(log_dir.glob("webhooks_*.json"))

# Sort log files to prioritize JSON webhook files first (primary data source),
# then other files by modification time (newest first)
# This ensures webhook data is displayed before internal log files
def sort_key(f: Path) -> tuple[int, float]:
    is_json_webhook = f.suffix == ".json" and f.name.startswith("webhooks_")
    # JSON webhook files: (0, -mtime) - highest priority, newest first
    # Other files: (1, -mtime) - lower priority, newest first
    return (0 if is_json_webhook else 1, -f.stat().st_mtime)

...
async with aiofiles.open(log_file, encoding="utf-8") as f:
    # Use appropriate parser based on file type
    if log_file.suffix == ".json":
        # JSONL files: one compact JSON object per line
        # Process both "log_entry" and "webhook_summary" entries
        # Skip infrastructure logger entries that lack webhook context
        async for line in f:
            entry = self.log_parser.parse_json_log_entry(line)
            if entry and not LogViewerController._is_infrastructure_noise(entry):
                buffer.append(entry)
    else:
        # Text log files: parse line by line
        # Skip infrastructure logger entries that lack webhook context
        async for line in f:
            entry = self.log_parser.parse_log_entry(line)
            if entry and not LogViewerController._is_infrastructure_noise(entry):
                buffer.append(entry)
```

It also filters out known infrastructure noise when those entries have no webhook context:

```302:320:webhook_server/web/log_viewer.py
@staticmethod
def _is_infrastructure_noise(entry: LogEntry) -> bool:
    """Check if a log entry is infrastructure noise that should be excluded.

    Infrastructure loggers (MCP server, log viewer) produce high-frequency
    entries without webhook context. These are filtered out to prevent them
    from drowning actual webhook processing entries in unfiltered queries.

    Only excludes entries that have NO webhook context (hook_id is None),
    preserving any infrastructure log that happens to correlate with a webhook.

    Args:
        entry: LogEntry to check

    Returns:
        True if the entry is infrastructure noise and should be excluded

    """
    return entry.logger_name in LogViewerController._INFRASTRUCTURE_LOGGERS and entry.hook_id is None
```

What this means in practice:

- The log viewer can show current and rotated text logs together with structured webhook logs.
- Structured JSON files are the primary source for webhook summaries, workflow steps, and export data.
- Text logs provide the detailed line-by-line context that summary records intentionally do not include.
- Exports are streamed to the client on demand; they are not written back into the data directory as extra files.

> **Warning:** The project does not add application-level authentication to the `/logs` endpoints. Treat the log viewer as an internal tool and protect it with trusted network placement or a reverse proxy that adds authentication.
