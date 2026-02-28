# CLAUDE.md

## Internal API Philosophy

**CRITICAL: This is a self-contained server application, NOT a public Python module.**

### Backward Compatibility Policy

**NO backward compatibility required for internal APIs:**
- Internal methods in `webhook_server/libs/` can change freely
- Return types can change (e.g., `Any` → `bool`)
- Method signatures can be modified without deprecation

**Backward compatibility ONLY for:**
- User-facing configuration files (`config.yaml`, `.github-webhook-server.yaml`)
- Configuration schema changes (must support old formats or provide migration)
- Webhook payload handling (must follow GitHub webhook spec)

**Rationale:** This server is deployed as a single application. All code is updated together. Internal refactoring is safe and encouraged.

### Anti-Defensive Programming

**CRITICAL: Eliminate unnecessary defensive programming overhead.**

**Philosophy:**

- Server fails-fast on startup if critical dependencies are missing
- Required parameters in `__init__()` are ALWAYS provided
- Checking for None on required parameters is pure overhead
- Defensive checks ONLY acceptable for truly optional parameters
- **Fail-fast is better than hiding bugs with fake data**

---

## WHEN Defensive Checks Are ACCEPTABLE

1. **Destructors (`__del__`)** - Can be called during failed initialization

   ```python
   def __del__(self):
       if hasattr(self, "logger"): self.logger.debug("Cleanup")
   ```

2. **Optional Parameters** - Parameter explicitly allows None

   ```python
   def get_data(self, owner: str | None = None): ...
   ```

3. **Lazy Initialization** - Attribute explicitly starts as None

   ```python
   self.client: SomeClient | None = None
   ```

4. **Platform Constants** - Constant may not exist on all platforms

   ```python
   if hasattr(os, "O_NOFOLLOW"): flags |= os.O_NOFOLLOW
   ```

5. **External Libraries We DON'T Control Version Of** - Library version truly unknown

   - **NOTE:** Does NOT apply to dependencies in `pyproject.toml` - we control those versions

---

## WHEN Defensive Checks Are VIOLATIONS

### 1. Required Parameters in `__init__()`

```python
# ❌ WRONG - config is required, ALWAYS provided
def some_method(self):
    if self.config: value = self.config.get_value("key")

# ✅ CORRECT
def some_method(self):
    value = self.config.get_value("key")
```

### 2. Known Library Versions

We control these versions in `pyproject.toml`: PyGithub >=2.4.0, gql >=3.5.0

```python
# ❌ WRONG - PyGithub >=2.4.0 guaranteed
if hasattr(self.rest_client, "close"): self.rest_client.close()

# ✅ CORRECT
self.rest_client.close()
```

### 3. Architecture Guarantees

`repository_data` is ALWAYS set before handlers instantiate (fail-fast in `GithubWebhook.process()`)

```python
# ❌ WRONG - repository_data guaranteed by architecture
if hasattr(self.github_webhook, "repository_data"): ...

# ✅ CORRECT
collaborators = self.github_webhook.repository_data["collaborators"]
```

### 4. Webhook Payload Fields

GitHub webhook format is stable: `user.node_id`, `user.type`, `sender` always exist

```python
# ❌ WRONG
if "node_id" in user_data: return user_data["node_id"]

# ✅ CORRECT - Let it fail if data is malformed
return user_data["node_id"]  # KeyError = legitimate bug
```

### 5. Type Discrimination

```python
# ❌ WRONG - Use isinstance for type checking
if hasattr(pr, "some_attr"): ...

# ✅ CORRECT
pr_id = pr.node_id  # Direct attribute access
```

---

## Fail-Fast Principle

**NEVER return fake defaults to hide missing data:**

```python
# ❌ WRONG - Fake data hiding bugs
return "", 0, False, None, UserWrapper(None), [], {}

# ✅ CORRECT - Fail-fast
raise ValueError("Data not available")
raise KeyError("Required field missing")
```

**Example:**

```python
# ❌ WRONG
@property
def user(self):
    if self._data and "user" in self._data:
        return UserWrapper(self._data["user"])
    return UserWrapper(None)  # Fake empty user - HIDES BUGS

# ✅ CORRECT
@property
def user(self):
    if self._data and "user" in self._data:
        return UserWrapper(self._data["user"])
    raise ValueError("No user data available - webhook incomplete")
```

---

## Architecture-Specific Guarantees

**NO defensive checks needed for:**

1. **Repository Data** - `repository_data` ALWAYS set before handlers instantiate
2. **Webhook User Objects** - `user.node_id`, `user.type`, `sender` always exist
3. **PyGithub REST API** - **🔴 CRITICAL:** PyGithub is blocking - **MUST** wrap with `asyncio.to_thread()`

---

## Summary: Quick Reference

### ✅ ACCEPTABLE Defensive Checks

- Destructors (`__del__`)
- Optional parameters (`param: Type | None = None`)
- Lazy initialization (starts as None)
- Platform constants (`os.O_NOFOLLOW`)
- External libraries we don't control

### ❌ VIOLATIONS (NO defensive checks)

- Required parameters in `__init__()`
- Known library versions (PyGithub >=2.4.0)
- Architecture guarantees (`repository_data`)
- Webhook payload fields (`user.node_id`)
- Type discrimination (use `isinstance()`)

### Enforcement

- Code reviews catch violations
- Type hints match reality
- Prek hooks automate checks
- **Zero tolerance for unnecessary defensive programming**

---

## Architecture Overview

FastAPI-based GitHub webhook server that automates repository management and pull request workflows.

### Core Architecture Components

**Event-Driven Handler Architecture:**

- `webhook_server/libs/handlers/` contains specialized handlers
- Handlers instantiated by main FastAPI app (`app.py`)
- Pattern: `__init__(github_webhook, ...)` → `process_event(event_data)`

**Configuration System:**

- `webhook_server/libs/config.py` manages YAML-based configuration with schema validation
- Global config at `/home/podman/data/config.yaml` with per-repository overrides via `.github-webhook-server.yaml`
- Schema validation in `webhook_server/config/schema.yaml`
- Configuration reloaded per webhook event (no server restart needed)

**GitHub API Integration:**

- `webhook_server/libs/github_api.py` provides core `GithubWebhook` class
- Uses PyGithub (REST API v3) for all GitHub operations
- **🔴 CRITICAL:** PyGithub is synchronous/blocking - **MUST** wrap with `asyncio.to_thread()`
- Supports multiple GitHub tokens with automatic failover

**Log Viewer System:**

- `webhook_server/web/log_viewer.py` contains `LogViewerController`
- **Memory-optimized**: Streaming/chunked processing (90% memory reduction)
- Real-time log streaming via WebSocket

## Development Commands

### Environment Setup

```bash
uv sync
source .venv/bin/activate
```

### Running the Server

```bash
# Development
uv run entrypoint.py

# Production
WEBHOOK_SERVER_DATA_DIR=/path/to/data uv run entrypoint.py
```

### Testing

```bash
# Run all tests
uv run --group tests pytest -n auto

# With coverage (90% required)
uv run --group tests pytest -n auto --cov=webhook_server
```

### Code Quality

```bash
uv run ruff format
uv run ruff check
uv run ruff check --fix
uv run mypy webhook_server/
uv run ruff check && uv run ruff format && uv run mypy webhook_server/
```

### Configuration Validation

```bash
uv run webhook_server/tests/test_schema_validator.py config.yaml
uv run pytest webhook_server/tests/test_config_schema.py -v
```

## Critical Implementation Patterns

### Handler Pattern

```python
class SomeHandler:
    def __init__(self, github_webhook: GithubWebhook, ...):
        self.github_webhook = github_webhook

    def process_event(self, event_data: dict) -> None:
        # Validate event data
        # Perform GitHub API operations via unified_api
        # Log results
```

### 🔴 MANDATORY: Non-Blocking PyGithub Operations

**CRITICAL:** PyGithub is synchronous - ALL operations MUST use `asyncio.to_thread()`

#### What Blocks the Event Loop

1. **Method calls** - ALL trigger API calls:

   - `.get_*()`, `.create_*()`, `.edit()`, `.update()`, `.add_to_*()`, `.remove_from_*()`

2. **Property accesses** - MANY trigger API calls:

   - `.draft`, `.mergeable`, `.state`, `.committer`, `.author`, `.permissions`, `.labels`, `.assignees`
   - **ANY property not in webhook payload**

3. **PaginatedList iteration** - BLOCKS during iteration

4. **Safe operations** (don't block):

   - Properties from webhook payload (`.number`, `.title`, `.body`)
   - Already-fetched cached data (rare)

#### Correct Usage

```python
import asyncio
from github.PullRequest import PullRequest

# ✅ CORRECT - Wrap ALL method calls
await asyncio.to_thread(pull_request.create_issue_comment, "Comment")
await asyncio.to_thread(pull_request.add_to_labels, "label")
await asyncio.to_thread(repository.get_pull, number)

# ✅ CORRECT - Wrap ALL property accesses that may trigger API calls
is_draft = await asyncio.to_thread(lambda: pull_request.draft)
mergeable = await asyncio.to_thread(lambda: pull_request.mergeable)
labels = await asyncio.to_thread(lambda: list(pull_request.labels))

# ✅ CORRECT - Wrap PaginatedList iteration
commits = await asyncio.to_thread(lambda: list(pull_request.get_commits()))
for commit in commits:
    await process_commit(commit)

# ✅ CORRECT - Concurrent operations
is_draft, mergeable, state = await asyncio.gather(
    asyncio.to_thread(lambda: pull_request.draft),
    asyncio.to_thread(lambda: pull_request.mergeable),
    asyncio.to_thread(lambda: pull_request.state),
)

# ❌ WRONG - NEVER call PyGithub directly
pull_request.create_issue_comment("Comment")  # BLOCKS!
is_draft = pull_request.draft  # BLOCKS!
for commit in pull_request.get_commits(): ...  # BLOCKS!
```

#### Decision Tree

Before accessing ANY PyGithub object:

1. Is this a PyGithub object? → YES, it may block
2. Calling a method? → **DEFINITELY BLOCKS** - wrap in `asyncio.to_thread()`
3. Accessing a property? → **MAY BLOCK** - wrap in `asyncio.to_thread(lambda: obj.property)`
4. Iterating PaginatedList? → **BLOCKS** - wrap in `asyncio.to_thread(lambda: list(...))`
5. Webhook payload attribute? → Usually safe (`.number`, `.title`)
6. **Unsure? ALWAYS wrap in `asyncio.to_thread()`**

**Why this is critical:**

- PyGithub is synchronous - each operation blocks 100ms-2 seconds
- Blocking = frozen server (no other webhooks processed)
- `asyncio.to_thread()` runs code in thread pool, keeps event loop responsive
- **NOT OPTIONAL** - required for correct async operation

**Impact of blocking:**

- Single blocking call freezes entire server
- Incoming webhooks must wait
- Server appears unresponsive
- Rate limits hit faster
- Degraded user experience

### Type Compatibility Pattern

```python
async def add_pr_comment(self, pull_request: PullRequest, body: str) -> None:
    await asyncio.to_thread(pull_request.create_issue_comment, body)

async def check_pr_status(self, pull_request: PullRequest) -> tuple[bool, bool, str]:
    return await asyncio.gather(
        asyncio.to_thread(lambda: pull_request.draft),
        asyncio.to_thread(lambda: pull_request.mergeable),
        asyncio.to_thread(lambda: pull_request.state),
    )
```

### Repository Data Pre-Fetch Pattern

```python
# In GithubWebhook.process() - after PR data, before handlers
self.repository_data: dict[str, Any] = await self.unified_api.get_comprehensive_repository_data(owner, repo)

# In handlers - use pre-fetched data
collaborators = self.github_webhook.repository_data['collaborators']['edges']
```

**Key principles:**

- Fetch once per webhook, never per handler
- Fail-fast: Exception propagates → webhook aborts
- Type is `dict[str, Any]`, never `| None` (fail-fast guarantees)

### Repository Cloning Optimization for check_run Events

**Location:** `webhook_server/libs/github_api.py` lines 534-570

**Early exit conditions (no clone needed):**

1. **Action != "completed"** - Skip `created` action
2. **Can-be-merged with non-success conclusion** - Primary optimization

```python
elif self.github_event == "check_run":
    action = self.hook_data.get("action", "")
    if action != "completed":
        return None

    check_run_name = self.hook_data.get("check_run", {}).get("name", "")
    check_run_conclusion = self.hook_data.get("check_run", {}).get("conclusion", "")

    if check_run_name == CAN_BE_MERGED_STR and check_run_conclusion != SUCCESS_STR:
        return None

    await self._clone_repository(pull_request=pull_request)
```

**Benefits:**

- 90-95% reduction in unnecessary cloning
- Saves 5-30 seconds per skipped clone
- Reduced resource usage
- Lower server load

**Tests:** `webhook_server/tests/test_check_run_handler.py`

### Configuration Access

```python
from webhook_server.libs.config import Config

config = Config(repository="org/repo-name")
value = config.get_value("setting-name", default_value)
```

### Logging Pattern

```python
from webhook_server.utils.helpers import get_logger_with_params

logger = get_logger_with_params(
    name="component_name",
    repository="org/repo",
    hook_id="github-delivery-id"
)

logger.debug("Detailed technical information")
logger.info("General information")
logger.warning("Warning that needs attention")
logger.error("Error requiring investigation")
logger.exception("Error with full traceback")  # Preferred over logger.error(..., exc_info=True)
```

### Structured Webhook Logging

JSON-based logging for webhook execution tracking with thread-safe context using ContextVar.

**Context Creation (app.py):**

```python
from webhook_server.utils.context import create_context

ctx = create_context(
    hook_id=hook_id,
    event_type="pull_request",
    repository="org/repo",
    action="opened",
    sender="username",
    api_user="github-api-user",
)
```

**Step Tracking:**

```python
from webhook_server.utils.context import get_context

ctx = get_context()
ctx.start_step("clone_repository", branch="main")

try:
    await clone_repo()
    ctx.complete_step("clone_repository", commit_sha="abc123")
except Exception as ex:
    import traceback
    ctx.fail_step("clone_repository", exception=ex, traceback_str=traceback.format_exc())
```

**Handler Usage:**

```python
class PullRequestHandler:
    async def process_event(self, event_data: dict) -> None:
        ctx = self.github_webhook.ctx
        ctx.start_step("assign_reviewers", pr_number=123)
        try:
            await self.assign_reviewers(pr)
            ctx.complete_step("assign_reviewers", reviewers_assigned=3)
        except Exception as ex:
            ctx.fail_step("assign_reviewers", exception=ex, traceback_str=traceback.format_exc())
```

**Log File Format:**

- Location: `{config.data_dir}/logs/webhooks_YYYY-MM-DD.json`
- Format: Pretty-printed JSON (2-space indentation)
- Rotation: Daily based on UTC date

**Log entry structure:**

```json
{
  "hook_id": "github-delivery-id",
  "event_type": "pull_request",
  "pr": {"number": 968, "title": "Add new feature"},
  "timing": {"started_at": "2026-01-05T10:30:00.123Z", "duration_ms": 7712},
  "workflow_steps": {
    "clone_repository": {"status": "completed", "duration_ms": 4823}
  },
  "token_spend": 4,
  "success": true
}
```

### Exception Handling Pattern

```python
# ✅ CORRECT: Use logger.exception for automatic traceback
try:
    await some_operation()
except Exception:
    logger.exception("Failed to perform operation")

# ❌ WRONG: Don't use logger.error with exc_info=True
except Exception as ex:
    logger.error(f"Failed: {ex}", exc_info=True)

# ✅ BETTER: Catch specific exceptions
except GithubException as ex:
    logger.exception("GitHub API operation failed")
except asyncio.CancelledError:
    logger.debug("Operation cancelled")
    raise  # Always re-raise CancelledError
```

## Critical Architectural Rules

### Import Organization

**MANDATORY:** All imports at top of files

- No imports in functions or try/except blocks
- Exception: TYPE_CHECKING imports can be conditional
- Prek hooks enforce this

### Type Hints

**MANDATORY:** Complete type hints (mypy strict mode)

```python
# ✅ CORRECT
async def process_pr(self, pull_request: PullRequest, reviewers: list[str]) -> None: ...

# ❌ WRONG
async def process_pr(self, pull_request, reviewers): ...
```

### Test Coverage

**MANDATORY:** 90% code coverage required

- Check: `uv run --group tests pytest --cov=webhook_server`
- New code without tests fails CI
- Tests in `webhook_server/tests/`

## Testing Patterns

### Test File Organization

```bash
webhook_server/tests/
├── test_*.py                    # Unit and integration tests
├── manifests/                   # Test configuration files
│   └── config.yaml
└── test_*_handler.py            # Handler-specific tests
```

### Mock Testing Pattern

```python
from unittest.mock import AsyncMock, Mock

mock_api = AsyncMock()
mock_api.get_pull_request.return_value = mock_pr_data

with patch("asyncio.to_thread", side_effect=mock_to_thread):
    result = await unified_api.get_pr_for_check_runs(owner, repo, number)
```

### Test Token Pattern

```python
TEST_GITHUB_TOKEN = "ghp_test1234..."  # pragma: allowlist secret

@pytest.fixture
def mock_github_api():
    mock = Mock()
    mock.get_rate_limit.return_value = Mock(rate=Mock(remaining=5000))
    return mock
```

## Security Considerations

### Log Viewer Security

⚠️ **CRITICAL:** Log viewer endpoints (`/logs/*`) are unauthenticated

- Deploy only on trusted networks (VPN, internal network)
- Never expose to public internet
- Use reverse proxy with authentication for external access
- Logs contain sensitive data: tokens, webhook payloads, user information

### Token Handling

- Store tokens in environment variables or secret management systems
- Use multiple tokens for rate limit distribution
- Never commit tokens to repository
- Mask sensitive data in logs (default: `mask-sensitive-data: true`)

## Common Development Tasks

### Adding a New Handler

1. Create handler file in `webhook_server/libs/handlers/`
2. Implement `__init__(self, github_webhook, ...)` and `process_event(event_data)`
3. Use `self.github_webhook.unified_api` for GitHub operations
4. Add tests in `webhook_server/tests/test_*_handler.py`
5. Update `app.py` to instantiate handler

### Updating Configuration Schema

1. Edit `webhook_server/config/schema.yaml`
2. Run `uv run pytest webhook_server/tests/test_config_schema.py -v`
3. Update examples in `examples/config.yaml`
4. Test with `uv run webhook_server/tests/test_schema_validator.py examples/config.yaml`

### PR Test Oracle Integration

External AI service integration for test recommendations via [pr-test-oracle](https://github.com/myk-org/pr-test-oracle). Configured via `test-oracle` in config (global or per-repo).

**Config keys:** `server-url` (required), `ai-provider` (required: claude/gemini/cursor), `ai-model` (required), `test-patterns` (optional), `triggers` (optional, default: [approved])

**Trigger events:** `approved`, `pr-opened`, `pr-synchronized`

**Comment command:** `/test-oracle` (always works when configured, no trigger needed)

**Module:** `webhook_server/libs/test_oracle.py` - `call_test_oracle()` shared helper

**Error handling:**

- Health check failure: PR comment posted, continue flow
- Analyze errors: log only, no PR comment
- Never breaks webhook processing
