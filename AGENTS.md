# AGENTS.md

## Internal API Philosophy

This is a self-contained server application, NOT a public Python module.
No backward compatibility required for internal APIs — methods, return types, and signatures can change freely.
Backward compatibility only for: user-facing config files (`config.yaml`, `.github-webhook-server.yaml`) and webhook payload handling (GitHub spec).

## Anti-Defensive Programming

Fail-fast on missing data. Required parameters are always provided — checking for None is overhead.
Defensive checks are acceptable ONLY for: destructors (`__del__`), optional parameters (`Type | None`),
lazy initialization (starts as None), platform constants, and external libraries we don't control the version of.

```python
# ❌ WRONG - config is required, ALWAYS provided
if self.config: value = self.config.get_value("key")

# ✅ CORRECT - fail-fast; KeyError = legitimate bug
value = self.config.get_value("key")
```

**NEVER return fake defaults** (`""`, `0`, `None`, `[]`, `{}`) to hide missing data — raise instead.

Architecture guarantees (no defensive checks needed):
- `repository_data` — always set before handlers instantiate
- Webhook user objects — `user.node_id`, `user.type`, `sender` always exist
- Known library versions — PyGithub >=2.4.0, gql >=3.5.0 (we control via `pyproject.toml`)

## Architecture Overview

FastAPI-based GitHub webhook server that automates repository management and PR workflows.

**Handler architecture:** `webhook_server/libs/handlers/` — each handler takes `__init__(github_webhook, ...)` and implements `process_event(event_data)`. Instantiated by `app.py`.

**Configuration:** `webhook_server/libs/config.py` — YAML-based with schema validation (`webhook_server/config/schema.yaml`). Global config at `/home/podman/data/config.yaml`, per-repo overrides via `.github-webhook-server.yaml`. Reloaded per webhook event.

**GitHub API:** `webhook_server/libs/github_api.py` — core `GithubWebhook` class using PyGithub (REST v3). Supports multiple tokens with automatic failover.

**Log viewer:** `webhook_server/web/log_viewer.py` — memory-optimized streaming with WebSocket support.

## Development Commands

```bash
# Setup
uv sync && source .venv/bin/activate

# Run server
uv run entrypoint.py                                          # dev
WEBHOOK_SERVER_DATA_DIR=/path/to/data uv run entrypoint.py    # prod

# Tests (90% coverage required)
uv run --group tests pytest -n auto
uv run --group tests pytest -n auto --cov=webhook_server

# Code quality
uv run ruff check && uv run ruff format && uv run mypy webhook_server/

# Schema validation
uv run pytest webhook_server/tests/test_config_schema.py -v
```

## PyGithub: Non-Blocking Operations

PyGithub is synchronous — every call blocks the async event loop and freezes the server.
ALL PyGithub operations MUST use `github_api_call()` from `webhook_server.utils.github_retry`,
which wraps `asyncio.to_thread()` with retry for transient failures (HTTP 500/502/503/504).

```python
from webhook_server.utils.github_retry import github_api_call

# ✅ CORRECT — method calls, property access, iteration
await github_api_call(pr.create_issue_comment, "Comment", logger=self.logger, log_prefix=self.log_prefix)
is_draft = await github_api_call(lambda: pr.draft, logger=self.logger, log_prefix=self.log_prefix)
commits = await github_api_call(lambda: list(pr.get_commits()), logger=self.logger, log_prefix=self.log_prefix)

# ❌ WRONG — blocks event loop, no retry
pr.create_issue_comment("Comment")
await asyncio.to_thread(pr.create_issue_comment, "Comment")  # no retry!
```

**Decision checklist:**
1. Calling a method? → wrap in `github_api_call()`
2. Accessing a property not from webhook payload? → wrap in `github_api_call(lambda: ...)`
3. Iterating PaginatedList? → wrap in `github_api_call(lambda: list(...))`
4. Webhook payload attribute (`.number`, `.title`, `.body`)? → safe, no wrapping needed
5. Unsure? → wrap it

## Implementation Patterns

### Repository Data Pre-Fetch

Fetch once per webhook in `GithubWebhook.process()`, before handlers. Type is `dict[str, Any]`, never `| None`.

```python
# In handlers — use pre-fetched data directly
collaborators = self.github_webhook.repository_data['collaborators']['edges']
```

### Repository Cloning Optimization (check_run)

Location: `webhook_server/libs/github_api.py` lines 534-570. Skips cloning for `action != "completed"` and `can-be-merged` with non-success conclusion (90-95% reduction in unnecessary clones).

### Configuration Access

```python
config = Config(repository="org/repo-name")
value = config.get_value("setting-name", default_value)
```

### Logging

```python
from webhook_server.utils.helpers import get_logger_with_params

logger = get_logger_with_params(name="component", repository="org/repo", hook_id="delivery-id")
logger.info("General information")
logger.exception("Error with traceback")  # Preferred over logger.error(..., exc_info=True)
```

### Structured Webhook Logging

JSON-based execution tracking via `ContextVar`. Created in `app.py` with `create_context()`.

```python
ctx = get_context()
ctx.start_step("clone_repository", branch="main")
try:
    await clone_repo()
    ctx.complete_step("clone_repository", commit_sha="abc123")
except Exception as ex:
    ctx.fail_step("clone_repository", exception=ex, traceback_str=traceback.format_exc())
    raise
```

Log files: `{config.data_dir}/logs/webhooks_YYYY-MM-DD.json` (daily rotation, pretty-printed JSON).

### Exception Handling

Use `logger.exception()` for automatic traceback. Catch specific exceptions when possible.
Always re-raise `asyncio.CancelledError`.

## Code Rules

- **Imports:** All at top of file. No in-function imports. `TYPE_CHECKING` imports can be conditional.
- **Type hints:** Complete type hints required (mypy strict mode).
- **Test coverage:** 90% required. New code without tests fails CI. Tests in `webhook_server/tests/`.

## Testing Patterns

```python
# Mock testing — patch asyncio.to_thread since github_api_call delegates to it
with patch("asyncio.to_thread", side_effect=mock_to_thread):
    result = await unified_api.get_pr_for_check_runs(owner, repo, number)

# Test tokens
TEST_GITHUB_TOKEN = "ghp_test1234..."  # pragma: allowlist secret
```

## Security

**Log viewer endpoints (`/logs/*`) are unauthenticated.** Deploy only on trusted networks.
Never expose to public internet. Logs contain tokens, webhook payloads, user information.

**Tokens:** Store in env vars or secret management. Use multiple tokens for rate limit distribution.
Never commit to repository. Mask sensitive data in logs (see `mask-sensitive-data` in schema).

## Common Development Tasks

### Adding a New Handler

1. Create file in `webhook_server/libs/handlers/`
2. Implement `__init__(self, github_webhook, ...)` and `process_event(event_data)`
3. Add tests in `webhook_server/tests/test_*_handler.py`
4. Update `app.py` to instantiate handler

### Updating Configuration Schema

1. Edit `webhook_server/config/schema.yaml`
2. Run `uv run pytest webhook_server/tests/test_config_schema.py -v`
3. Update `examples/config.yaml`

### PR Test Oracle

External AI test recommendation service via [pr-test-oracle](https://github.com/myk-org/pr-test-oracle).
Config: `test-oracle` in schema. Command: `/test-oracle`. Module: `webhook_server/libs/test_oracle.py`.
On error: health check failure posts PR comment, analyze errors log only. Never breaks webhook processing.

### AI Features

Config: `ai-features` (global or per-repo). Sub-features: `conventional-title`, `resolve-cherry-pick-conflicts-with-ai`.
Module: `webhook_server/libs/ai_cli.py` (pi-sidecar wrapper). On failure: error logged, flow continues.

Post-resolution verification: `_verify_cherry_pick_scope()` compares `git diff --stat` of original vs cherry-pick. Logs warning if cherry-pick has fewer file changes. Informational only.

Required env vars for pi-sidecar: `ACPX_AGENTS=cursor`, `VERTEX_CLAUDE_1M=true`, `GOOGLE_APPLICATION_CREDENTIALS`.

### Custom Commands

User-defined commands rendered in the PR welcome message. Documentation-only — the server displays them but does NOT process them. External bots/tools handle them independently.

**Schema:** `webhook_server/config/schema.yaml` (`custom-commands` at global level, `$defs.custom-command-item` for DRY)

**Config:** Three resolution layers (first match wins, no list merge): (1) repo-local `.github-webhook-server.yaml`, (2) `repositories.<repo>.custom-commands` in `config.yaml`, (3) root-level `custom-commands` in `config.yaml`. Per-repo layers can use an empty list (`custom-commands: []`) to disable global defaults; the root-level schema requires `minItems: 1` and `maxItems: 50`.

**Validation:** `GithubWebhook._validate_custom_commands()` in `webhook_server/libs/github_api.py` — validates at load time (entries must be dicts with non-empty `name` (max 100 chars) matching `^[a-zA-Z0-9_-]+$` and non-empty `description` (max 500 chars); duplicate names are rejected, keeping only the first occurrence). Invalid and duplicate entries are logged and skipped.

**Handler:** `PullRequestHandler._prepare_custom_commands_welcome_section` in `webhook_server/libs/handlers/pull_request_handler.py` — renders a "Custom Commands" section with each command as `` * `/{name}` - description ``. Descriptions are markdown-escaped via `_escape_markdown()`.

**Config loading:** `self.custom_commands` loaded in `GithubWebhook._repo_data_from_config()` via `self.config.get_value("custom-commands", ...)` with `extra_dict=repository_config` for per-repo override support.

### Sidecar Architecture

`sidecar-helper/` — Node.js pi-sidecar bridge for AI provider integration. Minimal TypeScript wrapper importing `@myk-org/pi-sidecar`.

**Container startup (`entrypoint.sh`):**
1. Start sidecar background process
2. Register cleanup trap + monitor subshell (kills PID 1 if sidecar dies)
3. Wait for sidecar readiness (polls `/health`, up to 15s)
4. `exec uv run entrypoint.py`

`SIDECAR_PORT` env var controls listen port (default: `9100`).

**Tool server:** aiohttp on port `5001` (`webhook_server/web/tool_server.py`). Dedicated thread, own event loop. `TOOL_REGISTRY` pattern — registers `git_diff`, `git_log`, `git_show`, `git_status`. Localhost-only.

**Healthcheck:** Dockerfile verifies both webhook server (`:5000`) and sidecar (`:${SIDECAR_PORT}`).

**Docker build:** Multi-stage — `sidecar-builder` (node:22-slim) runs `npm ci`, `npx tsc`, `npm prune --omit=dev`. Final stage copies `dist/`, `node_modules/` (production), `package.json`.

**Security:** AI conflict resolution prompt includes user-controlled commit messages. Mitigated by restricting AI to read-only tools + file edit/write — no bash access.

## Generated Documentation

The `docs/` directory contains AI-generated documentation from docsfy.
**NEVER edit these files manually.** To update documentation, regenerate using docsfy.
