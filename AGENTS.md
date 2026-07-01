# AGENTS.md

## Commands
- Setup: `uv sync && source .venv/bin/activate`
- Dev server: `uv run entrypoint.py`
- Prod server: `WEBHOOK_SERVER_DATA_DIR=/path/to/data uv run entrypoint.py`
- Test: `uv run --group tests pytest -n auto`
- Test + coverage: `uv run --group tests pytest -n auto --cov=webhook_server`
- Schema tests: `uv run pytest webhook_server/tests/test_config_schema.py -v`
- Lint + format: `uv run ruff check && uv run ruff format`
- Type check: `uv run mypy webhook_server/`
- Full verify: `uv run ruff check && uv run ruff format && uv run mypy webhook_server/ && uv run --group tests pytest -n auto`

## Definition of Done
A task is complete when ALL pass:
1. `uv run ruff check` exits 0
2. `uv run ruff format --check` exits 0
3. `uv run mypy webhook_server/` exits 0
4. `uv run --group tests pytest -n auto` exits 0 — 90% coverage required, new code without tests fails CI
5. All imports at top of file, complete type hints on all functions

## When Blocked
- Tests fail after 3 attempts → stop, report failing test with full output
- PyGithub rate limit → switch token (multi-token failover is built in)
- Missing config key → check `webhook_server/config/schema.yaml` before asking
- Merge conflicts → stop, show conflicting files
- 🚫 Never delete files to fix errors, force push, skip tests, or commit secrets

## Project
FastAPI-based GitHub webhook server automating repository management and PR workflows.
Handlers in `webhook_server/libs/handlers/` process events; config via YAML with schema validation.
See `docs/` for generated architecture docs (regenerate with docsfy — **NEVER edit `docs/` manually**).

- Stack: Python 3.13, FastAPI, PyGithub, gql, aiohttp
- Internal APIs — no backward compat; only `config.yaml`, `.github-webhook-server.yaml`, and webhook payloads are stable
- Config: `webhook_server/libs/config.py` (schema: `webhook_server/config/schema.yaml`)
- GitHub API: `webhook_server/libs/github_api.py` — PyGithub REST v3, multi-token failover
- Log viewer: `webhook_server/web/log_viewer.py` — WebSocket streaming
- Sidecar: `sidecar-helper/` — Node.js pi-sidecar bridge for AI features (see `entrypoint.sh`)

## When Writing Code

### PyGithub: Always Use `github_api_call()` (blocks event loop otherwise)
```python
from webhook_server.utils.github_retry import github_api_call

# ✅ CORRECT — async, with retry for HTTP 500/502/503/504
await github_api_call(pr.create_issue_comment, "Comment", logger=self.logger, log_prefix=self.log_prefix)
is_draft = await github_api_call(lambda: pr.draft, logger=self.logger, log_prefix=self.log_prefix)
commits = await github_api_call(lambda: list(pr.get_commits()), logger=self.logger, log_prefix=self.log_prefix)

# ❌ WRONG — blocks event loop, no retry
pr.create_issue_comment("Comment")
```
1. Method call → `github_api_call(obj.method, args)`
2. Property (not from webhook payload) → `github_api_call(lambda: obj.prop)`
3. PaginatedList → `github_api_call(lambda: list(...))`
4. Webhook payload attribute (`.number`, `.title`, `.body`) → safe, no wrapping
5. Unsure → wrap it

### Anti-Defensive Programming: Fail-Fast
```python
# ❌ WRONG — config is required, ALWAYS provided
if self.config: value = self.config.get_value("key")

# ✅ CORRECT — fail-fast; KeyError = legitimate bug
value = self.config.get_value("key")
```
- Do not return fake defaults (`""`, `0`, `None`, `[]`, `{}`) to hide missing data — raise instead
- Defensive checks OK only for: `__del__`, `Type | None` params, lazy init, external libs
- Guarantees: `repository_data` always set before handlers; webhook `user.node_id`/`sender` always exist

### Patterns
- Logging: `get_logger_with_params(name=, repository=, hook_id=)` — use `logger.exception()` for tracebacks
- Config: `Config(repository="org/repo").get_value("key", default)`
- Pre-fetched data: `self.github_webhook.repository_data['collaborators']['edges']` — `dict[str, Any]`, never None
- Context tracking: `get_context()` → `start_step()` / `complete_step()` / `fail_step()` (see `app.py`)
- Always re-raise `asyncio.CancelledError`
- All imports at top — no in-function imports (`TYPE_CHECKING` conditional imports are OK)

## When Adding a Handler
1. Create `webhook_server/libs/handlers/<name>.py` — implement `__init__(self, github_webhook, ...)` + `process_event(event_data)`
2. Add tests in `webhook_server/tests/test_<name>_handler.py`
3. Register in `app.py`
4. Run: `uv run --group tests pytest -n auto --cov=webhook_server`

## When Updating Config
1. Edit `webhook_server/config/schema.yaml`
2. Run: `uv run pytest webhook_server/tests/test_config_schema.py -v`
3. Update `examples/config.yaml`

## When Testing
- Mock PyGithub: patch `asyncio.to_thread` since `github_api_call` delegates to it
- Test tokens: `TEST_GITHUB_TOKEN = "ghp_test1234..."  # pragma: allowlist secret`
- Tests location: `webhook_server/tests/`

## Security
- **NEVER expose log viewer (`/logs/*`) to public internet** — endpoints are unauthenticated; deploy on trusted networks only
- Tokens: env vars or secret management, never committed — use `mask-sensitive-data` schema option
- AI conflict resolution prompts include user-controlled commit messages — mitigated by restricting AI to read-only tools + file edit/write (no bash)

## Boundaries
- ✅ Always: run full verify before committing, type hints on all functions, wrap PyGithub in `github_api_call()`
- ⚠️ Ask first: adding dependencies, modifying `entrypoint.sh`, changing schema structure
- 🚫 Never: edit `docs/` manually (regenerate with docsfy), commit tokens/secrets, use `python`/`pip` directly (use `uv`)
