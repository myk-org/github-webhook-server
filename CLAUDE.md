# CLAUDE.md

## Internal API Philosophy

**CRITICAL: This is a self-contained server application, NOT a public Python module.**

### Backward Compatibility Policy

**NO backward compatibility required for internal APIs:**

- Internal methods in `webhook_server/libs/` can change freely
- Return types can change (e.g., `Any` → `bool`)
- Method signatures can be modified without deprecation
- No version pinning or deprecation warnings needed

**Backward compatibility ONLY for:**

- User-facing configuration files (`config.yaml`, `.github-webhook-server.yaml`)
- Configuration schema changes (must support old formats or provide migration)
- Webhook payload handling (must follow GitHub webhook spec)

**Rationale:**

- This server is deployed as a single application
- All code is updated together - no external dependencies
- Internal refactoring is safe and encouraged
- Optimize for performance and clarity, not compatibility

**Examples:**

- ✅ Changing `get_branch() -> Any` to `get_branch() -> bool` - Internal API, no compatibility needed
- ✅ Refactoring internal methods - Internal implementation detail
- ❌ Changing config YAML structure - User-facing, needs migration path
- ❌ Breaking webhook event processing - GitHub spec must be followed

### Anti-Defensive Programming

**CRITICAL: Eliminate unnecessary defensive programming overhead.**

**Philosophy:**

- This server fails-fast on startup if critical dependencies are missing
- Required parameters in `__init__()` are ALWAYS provided
- Checking for None on required parameters is pure overhead
- Defensive checks are ONLY acceptable for truly optional parameters
- **Fail-fast is better than hiding bugs with fake data**

---

## WHEN Defensive Checks Are ACCEPTABLE

### 1. Destructors (`__del__`)

**Reason:** Can be called during failed initialization

```python
# ✅ CORRECT - __del__ can be called before __init__ completes
def __del__(self):
    if hasattr(self, "logger"):  # Legitimate - may not exist yet
        self.logger.debug("Cleanup")
    if hasattr(self, "rest_client") and self.rest_client:
        self.rest_client.close()
```

### 2. Optional Parameters

**Reason:** Parameter explicitly allows None

```python
# ✅ CORRECT - owner/name are optional in signature
def get_data(self, owner: str | None = None, name: str | None = None):
    if owner and name:  # Legitimate check - parameters are optional
        return await self.fetch_from_api(owner, name)
    return await self.fetch_default()
```

### 3. Lazy Initialization

**Reason:** Attribute explicitly starts as None

```python
# ✅ CORRECT - client starts as None by design
def __init__(self):
    self.client: SomeClient | None = None  # Starts uninitialized

async def query(self):
    if not self.client:  # Legitimate - lazy initialization
        await self.initialize()
```

### 4. Platform Constants

**Reason:** Constant may not exist on all platforms

```python
# ✅ CORRECT - os.O_NOFOLLOW doesn't exist on Windows
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
```

### 5. External Libraries We DON'T Control Version Of

**Reason:** Library version is truly unknown

**NOTE:** This does NOT apply to dependencies in `pyproject.toml` - we control those versions.

```python
# ✅ CORRECT - Only if library version is TRULY unknown
if hasattr(external_lib, "new_method"):  # Library version unknown
    external_lib.new_method()
else:
    external_lib.old_method()

# ❌ WRONG - PyGithub version is controlled in pyproject.toml
if hasattr(self.rest_client, "close"):  # PyGithub >=2.4.0 guaranteed
    self.rest_client.close()
```

---

## WHEN Defensive Checks Are VIOLATIONS

### 1. Required Parameters in `__init__()`

**VIOLATION:** Checking for attributes that are ALWAYS provided

```python
# ❌ WRONG - config is required parameter, ALWAYS provided
def __init__(self, token: str, logger: logging.Logger, config: Config):
    self.config = config

def some_method(self):
    if self.config:  # VIOLATION - config is always present
        value = self.config.get_value("key")

# ✅ CORRECT
def some_method(self):
    value = self.config.get_value("key")  # No check needed
```

### 2. Known Library Versions

**VIOLATION:** Version checking for controlled dependencies

We control these versions in `pyproject.toml`:

- PyGithub >=2.4.0 (`self.rest_client.close()` exists)
- gql >=3.5.0 (all expected methods exist)

```python
# ❌ WRONG - PyGithub >=2.4.0 is guaranteed in pyproject.toml
if hasattr(self.rest_client, "close"):
    self.rest_client.close()

# ✅ CORRECT
self.rest_client.close()  # PyGithub >=2.4.0 has this method
```

### 3. Architecture Guarantees

**VIOLATION:** Checking for data guaranteed by architecture

**Example:** `repository_data` is ALWAYS set before handlers instantiate (fail-fast in `GithubWebhook.process()`)

```python
# ❌ WRONG - repository_data is guaranteed by architecture
def __init__(self, github_webhook: GithubWebhook):
    self.github_webhook = github_webhook

def process_event(self, event_data: dict):
    if hasattr(self.github_webhook, "repository_data"):  # VIOLATION
        collaborators = self.github_webhook.repository_data["collaborators"]

# ✅ CORRECT
def process_event(self, event_data: dict):
    collaborators = self.github_webhook.repository_data["collaborators"]
    # No check - architecture guarantees this exists
```

### 4. Webhook Payload Fields

**VIOLATION:** Checking for fields that are ALWAYS in GitHub webhooks

GitHub webhook format is stable:

- `user.node_id` always exists for user objects
- `user.type` always exists for user objects
- `sender` always exists in webhook payloads

```python
# ❌ WRONG - user.node_id always exists in GitHub webhook
def get_user_id(self, user_data: dict) -> str:
    if "node_id" in user_data:  # VIOLATION
        return user_data["node_id"]
    return ""  # Fake data hiding bugs

# ✅ CORRECT - Let it fail if data is malformed
def get_user_id(self, user_data: dict) -> str:
    return user_data["node_id"]  # KeyError = legitimate bug
```

### 5. Type Discrimination (Use isinstance instead)

**VIOLATION:** Using hasattr for type checking

```python
# ❌ WRONG - Use isinstance for type checking
def process_pr(self, pr: PullRequest):
    if hasattr(pr, "some_attr"):  # VIOLATION
        pr_id = pr.some_attr
    else:
        pr_id = pr.node_id

# ✅ CORRECT - Direct attribute access for PyGithub objects
def process_pr(self, pr: PullRequest):
    pr_id = pr.node_id
    pr_number = pr.number
```

---

## Fail-Fast Principle

**CRITICAL:** Fail-fast is better than hiding bugs with fake data.

### ❌ WRONG: Returning Fake Defaults

**Problem:** Fake data hides bugs and causes silent failures downstream

```python
# ❌ WRONG - Returns fake empty user object
@property
def user(self):
    if self._data and "user" in self._data:
        return UserWrapper(self._data["user"])
    return UserWrapper(None)  # Creates fake empty user - HIDES BUGS

# Result: Code continues with fake user, fails mysteriously later
pr.user.login  # Returns "" instead of failing
pr.user.node_id  # Returns "" instead of failing
```

### ✅ CORRECT: Fail-Fast

**Benefit:** Immediate clear error at the source of the problem

```python
# ✅ CORRECT - Fail-fast with clear error
@property
def user(self):
    if self._data and "user" in self._data:
        return UserWrapper(self._data["user"])
    raise ValueError(
        "No user data available - webhook response incomplete"
    )

# Result: Clear error at source, easy debugging
pr.user  # Raises ValueError immediately - CLEAR ERROR
```

### Fake Data Types to Avoid

**NEVER return fake defaults to hide missing data:**

```python
# ❌ WRONG - Fake data hiding bugs
return ""           # Fake empty string
return 0            # Fake zero
return False        # Fake boolean
return None         # Fake None (when attribute should exist)
return UserWrapper(None)  # Fake empty object
return []           # Fake empty list (when data should exist)
return {}           # Fake empty dict (when data should exist)

# ✅ CORRECT - Fail-fast
raise ValueError("Data not available")  # Clear error
raise KeyError("Required field missing")  # Clear error
```

---

## Examples: Before and After

### Example 1: Required Parameter

```python
# ❌ WRONG - Defensive check on required parameter
def __init__(self, token: str, logger: logging.Logger, config: Config):
    self.config = config
    self.logger = logger

def some_method(self):
    if hasattr(self, "logger"):  # VIOLATION - logger is required
        self.logger.info("Processing...")
    if self.config:  # VIOLATION - config is required
        value = self.config.get_value("key")

# ✅ CORRECT
def __init__(self, token: str, logger: logging.Logger, config: Config):
    self.config = config
    self.logger = logger

def some_method(self):
    self.logger.info("Processing...")  # Logger always exists
    value = self.config.get_value("key")  # Config always exists
```

### Example 2: Known Library Version

```python
# ❌ WRONG - Version checking for controlled dependency
def cleanup(self):
    if hasattr(self.rest_client, "close"):  # VIOLATION
        self.rest_client.close()

# ✅ CORRECT - PyGithub >=2.4.0 guaranteed in pyproject.toml
def cleanup(self):
    self.rest_client.close()  # Method exists, no check needed
```

### Example 3: Architecture Guarantee

```python
# ❌ WRONG - Checking for architecture-guaranteed data
class SomeHandler:
    def __init__(self, github_webhook: GithubWebhook):
        self.github_webhook = github_webhook

    def process_event(self, event_data: dict):
        if hasattr(self.github_webhook, "repository_data"):  # VIOLATION
            collaborators = self.github_webhook.repository_data["collaborators"]
        else:
            collaborators = []  # Fake data hiding bugs

# ✅ CORRECT - Architecture guarantees repository_data exists
class SomeHandler:
    def __init__(self, github_webhook: GithubWebhook):
        self.github_webhook = github_webhook

    def process_event(self, event_data: dict):
        # repository_data ALWAYS exists before handlers instantiate
        collaborators = self.github_webhook.repository_data["collaborators"]
```

### Example 4: Webhook Payload Fields

```python
# ❌ WRONG - Fake data for stable webhook fields
@property
def user(self):
    if self._data and "user" in self._data:
        return UserWrapper(self._data["user"])
    return UserWrapper(None)  # Fake empty user - HIDES BUGS

def get_user_id(self, user_data: dict) -> str:
    if "node_id" in user_data:  # VIOLATION
        return user_data["node_id"]
    return ""  # Fake data hiding bugs

# ✅ CORRECT - Fail-fast for malformed data
@property
def user(self):
    if self._raw_data and "user" in self._raw_data:
        return UserWrapper(self._raw_data["user"])
    if self._data and "user" in self._data:
        return UserWrapper(self._data["user"])
    raise ValueError("No user data available - webhook incomplete")

def get_user_id(self, user_data: dict) -> str:
    return user_data["node_id"]  # KeyError = legitimate bug
```

### Example 5: Type Discrimination

```python
# ❌ WRONG - hasattr for type checking
def process_pr(self, pr: PullRequest | PullRequestWrapper):
    if hasattr(pr, "id"):  # VIOLATION - use isinstance
        pr_id = pr.id
    else:
        pr_id = pr.node_id

# ✅ CORRECT - Proper type discrimination
def process_pr(self, pr: PullRequest | PullRequestWrapper):
    if isinstance(pr, PullRequestWrapper):
        pr_id = pr.id
    else:
        pr_id = pr.node_id
```

---

## Architecture-Specific Guarantees

**Our architecture provides these guarantees - NO defensive checks needed:**

### 1. Repository Data

- `repository_data` is ALWAYS set before handlers instantiate
- Set in `GithubWebhook.process()` with fail-fast (exception propagates)
- Type: `dict[str, Any]` (NOT `dict[str, Any] | None`)

```python
# ✅ CORRECT - No check needed
def process_event(self, event_data: dict):
    collaborators = self.github_webhook.repository_data["collaborators"]
```

### 2. Webhook User Objects

- `user.node_id` always exists
- `user.type` always exists
- `sender` always exists in webhook payloads

```python
# ✅ CORRECT - No check needed
user_id = webhook_data["user"]["node_id"]
user_type = webhook_data["user"]["type"]
```

### 3. PyGithub REST API Usage

- All GitHub API operations use PyGithub (synchronous REST API)
- **🔴 CRITICAL:** PyGithub is blocking - **MUST** wrap ALL calls with `asyncio.to_thread()` to avoid blocking event loop
- Data sources are explicit and guaranteed

```python
# ✅ CORRECT - PyGithub wrapped in asyncio.to_thread()
repository = self.github_webhook.repository
pull_request = await asyncio.to_thread(repository.get_pull, number)
await asyncio.to_thread(pull_request.create_issue_comment, "Comment text")

# ❌ WRONG - Direct PyGithub calls block event loop
pull_request = repository.get_pull(number)  # BLOCKS!
pull_request.create_issue_comment("Comment text")  # BLOCKS!
```

---

## Decision Tree: When to Use Defensive Checks

**Ask yourself these questions IN ORDER:**

1. **Is this a destructor (`__del__`)?**
   - YES → Defensive check acceptable
   - NO → Continue

2. **Is the parameter/attribute optional by design (`Type | None`)?**
   - YES → Defensive check acceptable
   - NO → Continue

3. **Is this lazy initialization (starts as None)?**
   - YES → Defensive check acceptable
   - NO → Continue

4. **Is this a platform constant (e.g., `os.O_NOFOLLOW`)?**
   - YES → Defensive check acceptable
   - NO → Continue

5. **Is this an external library we DON'T control the version of?**
   - YES → Defensive check acceptable
   - NO → Continue

6. **Otherwise: NO DEFENSIVE CHECK**
   - Required parameters → ALWAYS exist
   - Controlled dependencies → Version guaranteed
   - Architecture guarantees → Data guaranteed
   - Webhook fields → Format guaranteed

---

## Enforcement

### Code Reviews MUST Catch Violations

**Reviewers must reject:**

- hasattr() checks on required parameters
- hasattr() checks on known library versions
- hasattr() checks on architecture-guaranteed data
- Fake default returns ("", 0, False, None objects, [])
- Type discrimination via hasattr instead of isinstance

### Type Hints Must Match Reality

```python
# ❌ WRONG - Type hint doesn't match reality
def __init__(self, config: Config):  # Required parameter
    self.config = config

def some_method(self):
    if self.config:  # Type hint says Config, check says Config | None - MISMATCH
        ...

# ✅ CORRECT - Type hint matches usage
def __init__(self, config: Config):  # Required parameter
    self.config = config

def some_method(self):
    value = self.config.get_value("key")  # No check - matches type hint
```

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

### Fail-Fast Principle

- **DON'T** return fake data ("", 0, False, None, UserWrapper(None))
- **DO** raise exceptions for missing data
- **Better** to crash early than hide bugs

### Enforcement

- Code reviews catch violations
- Type hints match reality
- Prek hooks automate checks
- **Zero tolerance for unnecessary defensive programming**

## Architecture Overview

This is a FastAPI-based GitHub webhook server that automates repository management and pull request workflows. The system processes GitHub webhooks and performs automated actions like PR management, container building, testing, and deployment.

### Core Architecture Components

**Event-Driven Handler Architecture:**

- `webhook_server/libs/handlers/` contains specialized handlers for different GitHub events
- Each handler (e.g., `pull_request_handler.py`, `issue_comment_handler.py`) processes specific webhook events
- Handlers are instantiated and orchestrated by the main FastAPI app (`app.py`)
- All handlers follow a common pattern: `__init__(github_webhook, ...)` → `process_event(event_data)`

**Configuration System:**

- `webhook_server/libs/config.py` manages YAML-based configuration with schema validation
- Global config at `/home/podman/data/config.yaml` with per-repository overrides via `.github-webhook-server.yaml`
- Schema validation in `webhook_server/config/schema.yaml`
- Configuration is reloaded per webhook event (no server restart needed)
- Per-repository override supported via `.github-webhook-server.yaml`

**GitHub API Integration:**

- `webhook_server/libs/github_api.py` provides the core `GithubWebhook` class
- Uses PyGithub (REST API v3) for all GitHub operations
- **🔴 CRITICAL:** PyGithub is synchronous/blocking - **MUST** wrap with `asyncio.to_thread()` to avoid blocking event loop
- Handles authentication, rate limiting, and GitHub API calls
- Supports multiple GitHub tokens with automatic failover

**Log Viewer System:**

- `webhook_server/web/log_viewer.py` contains `LogViewerController` for web-based log viewing
- Includes streaming log parsing, filtering, and real-time WebSocket updates
- **Memory-optimized**: Uses streaming/chunked processing (90% memory reduction vs bulk loading)
- Real-time log streaming via WebSocket with progressive loading

## Development Commands

### Environment Setup

```bash
# Install dependencies (preferred)
uv sync

# Activate development environment
source .venv/bin/activate
```

### Running the Server

```bash
# Development server
uv run entrypoint.py

# Production server (requires config.yaml in data directory)
WEBHOOK_SERVER_DATA_DIR=/path/to/data uv run entrypoint.py
```

### Testing

```bash
# Run all tests
 uv run --group tests pytest -n auto

# Run with coverage (90% required)
uv run --group tests pytest -n auto --cov=webhook_server

```

### Code Quality

```bash
# Format code
uv run ruff format

# Lint code
uv run ruff check

# Fix linting issues automatically
uv run ruff check --fix

# Type checking (strict mypy configuration)
uv run mypy webhook_server/

# Run all quality checks
uv run ruff check && uv run ruff format && uv run mypy webhook_server/
```

### Configuration Validation

```bash
# Validate configuration schema
uv run webhook_server/tests/test_schema_validator.py config.yaml

# Test configuration loading
uv run pytest webhook_server/tests/test_config_schema.py -v
```

## Critical Implementation Patterns

### Handler Pattern

All GitHub event processing follows this pattern:

```python
class SomeHandler:
    def __init__(self, github_webhook: GithubWebhook, ...):
        self.github_webhook = github_webhook

    def process_event(self, event_data: dict) -> None:
        # Validate event data
        # Perform GitHub API operations via unified_api
        # Log results
```

### GitHub API Usage Pattern

**🔴 CRITICAL: PyGithub is blocking - ALWAYS wrap with asyncio.to_thread() to keep server non-blocking:**

#### PyGithub Blocking Operations: Methods AND Properties

**IMPORTANT:** Both method calls AND property accesses can block the event loop!

PyGithub uses lazy loading - many properties trigger API calls when accessed. **ALL** PyGithub operations must be wrapped in `asyncio.to_thread()`.

```python
import asyncio
from github.PullRequest import PullRequest

# ✅ CORRECT: Wrap ALL PyGithub method calls in asyncio.to_thread()
repository = self.github_webhook.repository
pull_request = repository.get_pull(number)

# MANDATORY: Wrap blocking method calls to avoid freezing the event loop
await asyncio.to_thread(pull_request.create_issue_comment, "Comment text")
await asyncio.to_thread(pull_request.add_to_labels, "label-name")
await asyncio.to_thread(repository.get_branch, "main")

# ✅ CORRECT: Execute multiple calls concurrently (non-blocking)
tasks = [
    asyncio.to_thread(pull_request.create_issue_comment, "Comment"),
    asyncio.to_thread(pull_request.add_to_labels, "verified"),
    asyncio.to_thread(pull_request.get_commits),
]
results = await asyncio.gather(*tasks, return_exceptions=True)

# ❌ WRONG - NEVER call PyGithub directly (blocks event loop)
pull_request.create_issue_comment("Comment")  # BLOCKS EVENT LOOP!
repository.get_pull(number)                   # BLOCKS EVENT LOOP!
```

#### Common Blocking Property Accesses

**🔴 CRITICAL:** Many PyGithub properties trigger API calls and BLOCK the event loop!

```python
# ❌ WRONG - Property accesses that trigger API calls
is_draft = pull_request.draft  # BLOCKS - fetches PR data from API
committer = commit.committer  # BLOCKS - fetches user data from API
perms = user.permissions  # BLOCKS - fetches permission data from API
mergeable = pull_request.mergeable  # BLOCKS - checks merge status via API
state = pull_request.state  # BLOCKS - fetches PR state from API
labels = pull_request.labels  # BLOCKS - fetches label data from API
reviews = pull_request.get_reviews()  # BLOCKS - fetches review data

# ✅ CORRECT - Wrap property accesses in asyncio.to_thread()
is_draft = await asyncio.to_thread(lambda: pull_request.draft)
committer = await asyncio.to_thread(lambda: commit.committer)
perms = await asyncio.to_thread(lambda: user.permissions)
mergeable = await asyncio.to_thread(lambda: pull_request.mergeable)
state = await asyncio.to_thread(lambda: pull_request.state)
labels = await asyncio.to_thread(lambda: list(pull_request.labels))

# ✅ CORRECT - Accessing multiple properties concurrently
is_draft, mergeable, state = await asyncio.gather(
    asyncio.to_thread(lambda: pull_request.draft),
    asyncio.to_thread(lambda: pull_request.mergeable),
    asyncio.to_thread(lambda: pull_request.state),
)
```

#### Blocking Operations: Complete List

**What constitutes a blocking PyGithub operation:**

1. **Method calls** - ALL methods trigger API calls:
   - `.get_*()` - fetch data (e.g., `.get_pull()`, `.get_commits()`)
   - `.create_*()` - create resources (e.g., `.create_issue_comment()`)
   - `.edit()`, `.update()` - modify resources
   - `.add_to_*()`, `.remove_from_*()` - manage relationships

2. **Property accesses** - MANY properties trigger API calls:
   - `.draft`, `.mergeable`, `.state` - PR status properties
   - `.committer`, `.author` - user data properties
   - `.permissions` - permission data properties
   - `.labels`, `.assignees` - relationship properties
   - **ANY property not in the webhook payload**

3. **Iteration over PaginatedList** - BLOCKS during iteration:

   ```python
   # ❌ WRONG - Iterating PaginatedList blocks
   for commit in pull_request.get_commits():  # BLOCKS on each iteration
       process_commit(commit)

   # ✅ CORRECT - Wrap iteration in asyncio.to_thread()
   commits = await asyncio.to_thread(lambda: list(pull_request.get_commits()))
   for commit in commits:
       await process_commit(commit)
   ```

4. **Safe operations** - These DON'T block (data already in memory):
   - Properties from webhook payload (`.number`, `.title`, `.body` if from webhook)
   - Already-fetched cached data (rare, PyGithub caching is limited)
   - Simple attribute access on already-loaded objects

**Why this is critical:**

- PyGithub methods and properties are **synchronous/blocking** - they freeze the entire FastAPI server
- Every GitHub API call takes **100ms-2 seconds** - blocking = frozen server
- `asyncio.to_thread()` runs code in thread pool, keeping event loop responsive
- **NOT OPTIONAL** - required for correct async operation

### Type Compatibility Pattern

Methods work with PyGithub objects - always wrap calls AND property accesses in asyncio.to_thread():

```python
import asyncio
from github.PullRequest import PullRequest

async def add_pr_comment(
    self,
    pull_request: PullRequest,  # PyGithub PullRequest object
    body: str
) -> None:
    # 🔴 CRITICAL: Wrap PyGithub method calls to avoid blocking
    await asyncio.to_thread(pull_request.create_issue_comment, body)

async def check_pr_status(
    self,
    pull_request: PullRequest,
) -> tuple[bool, bool, str]:
    """Check PR draft status, mergeability, and state.

    🔴 CRITICAL: All property accesses wrapped to avoid blocking.
    """
    # ✅ CORRECT: Fetch multiple properties concurrently
    is_draft, mergeable, state = await asyncio.gather(
        asyncio.to_thread(lambda: pull_request.draft),
        asyncio.to_thread(lambda: pull_request.mergeable),
        asyncio.to_thread(lambda: pull_request.state),
    )
    return is_draft, mergeable, state

async def process_pr_commits(
    self,
    pull_request: PullRequest,
) -> list[str]:
    """Process all commits in a PR.

    🔴 CRITICAL: PaginatedList iteration wrapped to avoid blocking.
    """
    # ✅ CORRECT: Wrap PaginatedList iteration
    commits = await asyncio.to_thread(lambda: list(pull_request.get_commits()))
    commit_shas = [commit.sha for commit in commits]
    return commit_shas
```

### Repository Data Pre-Fetch Pattern

All webhook processing follows this data flow:

```python
# In GithubWebhook.process() - after PR data, before handlers
self.repository_data: dict[str, Any] = await self.unified_api.get_comprehensive_repository_data(owner, repo)

# In handlers - use pre-fetched data
collaborators = self.github_webhook.repository_data['collaborators']['edges']
contributors = self.github_webhook.repository_data['mentionableUsers']['nodes']
issues = self.github_webhook.repository_data['issues']['nodes']
```

**Key principles:**

- Fetch once per webhook, never per handler instance
- Fail-fast: Exception propagates → webhook aborts
- No caching across webhooks
- Type is `dict[str, Any]`, never `| None` (fail-fast guarantees data exists)

### Repository Cloning Optimization for check_run Events

**Optimization implemented:** Repository cloning is skipped for check_run webhooks that don't need it.

**Location:** `webhook_server/libs/github_api.py` lines 534-570

**Early exit conditions (no clone needed):**
1. **Action != "completed"**
   - Repository/organization webhooks only receive `created` and `completed` actions for check_run events
   - `created` action indicates the check run was just created, no processing needed
   - Code checks `action != "completed"` to skip clones for non-completed check runs

2. **Can-be-merged with non-success conclusion** (primary optimization)
   - Check name: `can-be-merged`
   - Conclusions: `failure`, `cancelled`, `timed_out`, `action_required`, `neutral`, `skipped`
   - Cannot automerge without success conclusion
   - This is the main optimization that prevents unnecessary repository cloning

**Implementation pattern:**

```python
elif self.github_event == "check_run":
    # Check if we need to process this check_run
    action = self.hook_data.get("action", "")
    if action != "completed":
        # Log and return early (no clone)
        return None

    # Check if this is can-be-merged with non-success conclusion
    check_run_name = self.hook_data.get("check_run", {}).get("name", "")
    check_run_conclusion = self.hook_data.get("check_run", {}).get("conclusion", "")

    if check_run_name == CAN_BE_MERGED_STR and check_run_conclusion != SUCCESS_STR:
        # Log and return early (no clone)
        return None

    # Only clone when actually needed
    await self._clone_repository(pull_request=pull_request)
    # ... rest of processing
```

**Benefits:**
- **90-95% reduction** in unnecessary repository cloning for check_run events
- **Faster webhook processing** - saves 5-30 seconds per skipped clone (depending on repo size)
- **Reduced resource usage** - less disk I/O, network I/O, and CPU usage
- **Lower server load** - especially during high webhook volume periods

**Other event types unchanged:**
- `issue_comment` - still clones before processing
- `pull_request` - still clones before processing
- `pull_request_review` - still clones before processing

**Tests:** `webhook_server/tests/test_check_run_handler.py` - `TestCheckRunRepositoryCloning` class

### Configuration Access

```python
from webhook_server.libs.config import Config

config = Config(repository="org/repo-name")
value = config.get_value("setting-name", default_value)
repo_data = config.repository_data
```

### Logging Pattern

All components use structured logging with contextual parameters:

```python
from webhook_server.utils.helpers import get_logger_with_params

logger = get_logger_with_params(
    name="component_name",
    repository="org/repo",
    hook_id="github-delivery-id"  # For webhook correlation
)

# Use appropriate log levels
logger.debug("Detailed technical information")
logger.info("General information")
logger.warning("Warning that needs attention")
logger.error("Error requiring investigation")
logger.exception("Error with full traceback")  # Preferred over logger.error(..., exc_info=True)
```

### Structured Webhook Logging

The server implements comprehensive JSON-based logging for webhook execution tracking. Each webhook generates a structured log entry containing all workflow steps, timing, errors, and API metrics.

**Overview:**

- Thread-safe context tracking using ContextVar for async isolation
- Each webhook execution gets an isolated WebhookContext instance
- Context persists through async operations and handler chains
- Automatic workflow step tracking with timing and error capture
- Pretty-printed JSON output with date-based log rotation

**Context Creation:**

Context is created in `app.py` at the start of webhook processing:

```python
from webhook_server.utils.context import create_context

# In process_with_error_handling() - before GithubWebhook instantiation
ctx = create_context(
    hook_id=hook_id,  # X-GitHub-Delivery header
    event_type="pull_request",
    repository="org/repo",
    repository_full_name="org/repo",
    action="opened",
    sender="username",
    api_user="github-api-user",
)
```

**Step Tracking Methods:**

Handlers and processing code use these methods to track workflow progress:

```python
from webhook_server.utils.context import get_context

# Get context anywhere in the call stack
ctx = get_context()

# Start a workflow step
ctx.start_step("clone_repository", branch="main")

# Complete step successfully
try:
    await clone_repo()
    ctx.complete_step("clone_repository", commit_sha="abc123")
except Exception as ex:
    # Mark step as failed with error details
    import traceback
    ctx.fail_step(
        "clone_repository",
        exception=ex,
        traceback_str=traceback.format_exc()
    )
```

**Handler Usage Pattern:**

Handlers access context via `github_webhook.ctx`:

```python
class PullRequestHandler:
    def __init__(self, github_webhook: GithubWebhook):
        self.github_webhook = github_webhook

    async def process_event(self, event_data: dict) -> None:
        # Access context
        ctx = self.github_webhook.ctx

        # Track workflow steps
        ctx.start_step("assign_reviewers", pr_number=123)
        try:
            await self.assign_reviewers(pr)
            ctx.complete_step(
                "assign_reviewers",
                reviewers_assigned=3,
                labels_added=["needs-review"]
            )
        except Exception as ex:
            ctx.fail_step(
                "assign_reviewers",
                exception=ex,
                traceback_str=traceback.format_exc(),
                pr_number=123
            )
```

**Log File Format:**

Logs are written to date-based JSON files:

- Location: `{config.data_dir}/logs/webhooks_YYYY-MM-DD.json`
- Format: Pretty-printed JSON (2-space indentation)
- Entry separator: Blank line between webhook executions
- Rotation: Daily based on UTC date
- Concurrency: File locking for safe multi-process writes

Each log entry contains:

```json
{
  "hook_id": "github-delivery-id",
  "event_type": "pull_request",
  "action": "opened",
  "sender": "username",
  "repository": "org/repo",
  "pr": {
    "number": 968,
    "title": "Add new feature",
    "author": "contributor"
  },
  "api_user": "github-api-user",
  "timing": {
    "started_at": "2026-01-05T10:30:00.123Z",
    "completed_at": "2026-01-05T10:30:07.835Z",
    "duration_ms": 7712
  },
  "workflow_steps": {
    "webhook_routing": {
      "timestamp": "2026-01-05T10:30:00.200Z",
      "status": "completed",
      "duration_ms": 2547
    },
    "clone_repository": {
      "timestamp": "2026-01-05T10:30:02.750Z",
      "status": "completed",
      "duration_ms": 4823,
      "commit_sha": "abc123"
    }
  },
  "token_spend": 4,
  "initial_rate_limit": 5000,
  "final_rate_limit": 4996,
  "success": true,
  "error": null,
  "summary": "[SUCCESS] Webhook completed PR#968 [7s712ms, tokens:4] steps=[webhook_routing:completed(2s547ms), clone_repository:completed(4s823ms)]"
}
```

### Exception Handling Pattern

```python
# ✅ CORRECT: Use logger.exception for automatic traceback
try:
    await some_operation()
except Exception:  # Can be broad for webhook handlers
    logger.exception("Failed to perform operation")
    # Handle gracefully or re-raise

# ❌ WRONG: Don't use logger.error with exc_info=True
except Exception as ex:
    logger.error(f"Failed: {ex}", exc_info=True)  # Use logger.exception instead

# ✅ BETTER: Catch specific exceptions when possible
except GithubException as ex:
    logger.exception("GitHub API operation failed")
except asyncio.CancelledError:
    logger.debug("Operation cancelled")
    raise  # Always re-raise CancelledError
```

## Critical Architectural Rules

### 🔴 MANDATORY: Non-Blocking Operations

**ALL operations must be non-blocking in this async FastAPI application:**

#### PyGithub (GitHub API) - ALWAYS Use asyncio.to_thread()

```python
# ✅ CORRECT - Wrap ALL PyGithub method calls
await asyncio.to_thread(pull_request.create_issue_comment, "Comment")
await asyncio.to_thread(pull_request.add_to_labels, "label-name")
await asyncio.to_thread(repository.get_branch, "main")
await asyncio.to_thread(repository.get_pull, pr_number)

# ✅ CORRECT - Wrap ALL PyGithub property accesses that may trigger API calls
is_draft = await asyncio.to_thread(lambda: pull_request.draft)
mergeable = await asyncio.to_thread(lambda: pull_request.mergeable)
committer = await asyncio.to_thread(lambda: commit.committer)

# ✅ CORRECT - Wrap PaginatedList iteration
commits = await asyncio.to_thread(lambda: list(pull_request.get_commits()))
for commit in commits:
    await process_commit(commit)

# ✅ CORRECT - Concurrent non-blocking operations
tasks = [
    asyncio.to_thread(pr.create_issue_comment, "Comment"),
    asyncio.to_thread(pr.add_to_labels, "verified"),
    asyncio.to_thread(lambda: list(pr.get_commits())),
    asyncio.to_thread(lambda: pr.draft),
    asyncio.to_thread(lambda: pr.mergeable),
]
results = await asyncio.gather(*tasks, return_exceptions=True)

# ❌ WRONG - NEVER call PyGithub methods directly
pull_request.create_issue_comment("Comment")  # BLOCKS EVENT LOOP!
repository.get_pull(123)                      # BLOCKS EVENT LOOP!
pr.get_commits()                              # BLOCKS EVENT LOOP!

# ❌ WRONG - NEVER access PyGithub properties directly
is_draft = pull_request.draft                 # BLOCKS EVENT LOOP!
mergeable = pull_request.mergeable            # BLOCKS EVENT LOOP!
committer = commit.committer                  # BLOCKS EVENT LOOP!

# ❌ WRONG - NEVER iterate PaginatedList directly
for commit in pull_request.get_commits():     # BLOCKS EVENT LOOP!
    process_commit(commit)
```

#### Decision Tree: Is This Operation Blocking?

**Before accessing ANY PyGithub object, ask yourself:**

1. **Is this a PyGithub object?** → YES, it may block
2. **Am I calling a method (`.get_*()`, `.create_*()`, `.edit()`, etc.)?** → **DEFINITELY BLOCKS** - wrap in `asyncio.to_thread()`
3. **Am I accessing a property (`.draft`, `.permissions`, `.committer`, `.mergeable`)?** → **MAY BLOCK** if property fetches data - wrap in `asyncio.to_thread(lambda: obj.property)`
4. **Am I iterating over a PaginatedList?** → **BLOCKS** during iteration - wrap in `asyncio.to_thread(lambda: list(...))`
5. **Am I checking object attributes from webhook payload?** → Usually safe (already in memory) - e.g., `.number`, `.title` if from webhook
6. **Am I unsure?** → **ALWAYS wrap in `asyncio.to_thread()`** - it's always safe!

**Rule of Thumb: If it's a PyGithub object and you're not 100% certain it's safe, wrap it in `asyncio.to_thread()`**

**Why this is critical:**

- **PyGithub is synchronous** - every method/property access can block for 100ms-2 seconds
- **Blocking = frozen server** - no other webhooks can be processed
- **asyncio.to_thread() is mandatory** - runs blocking code in thread pool
- **Enables concurrency** - multiple webhooks processed simultaneously
- **This is not optional** - required for correct FastAPI async operation

**Impact of blocking calls:**

- Single blocking API call freezes entire server
- Other incoming webhooks must wait
- Server appears unresponsive
- Rate limits are hit faster due to sequential processing
- User experience degrades (slow webhook processing)

### Import Organization

**MANDATORY:** All imports must be at the top of files

- No imports in the middle of functions or try/except blocks
- Exceptions: TYPE_CHECKING imports can be conditional
- Prek hooks enforce this

### Type Hints

**MANDATORY:** All functions must have complete type hints (mypy strict mode)

```python
# ✅ CORRECT
async def process_pr(
    self,
    pull_request: PullRequest,
    reviewers: list[str]
) -> None:
    ...

# ❌ WRONG
async def process_pr(self, pull_request, reviewers):  # Missing type hints
    ...
```

### Test Coverage

**MANDATORY:** 90% code coverage required

- Use `uv run --group tests pytest --cov=webhook_server` to check
- New code without tests will fail CI
- Tests must be in `webhook_server/tests/`

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

# For async operations
mock_api = AsyncMock()
mock_api.get_pull_request.return_value = mock_pr_data

# For REST operations wrapped in to_thread
with patch("asyncio.to_thread", side_effect=mock_to_thread):
    result = await unified_api.get_pr_for_check_runs(owner, repo, number)
```

### Test Token Pattern

Use centralized test tokens to avoid security warnings:

```python
# At module level
TEST_GITHUB_TOKEN = "ghp_test1234..."  # pragma: allowlist secret

# In fixtures
@pytest.fixture
def mock_github_api():
    """Create a mock GitHub API."""
    mock = Mock()
    mock.get_rate_limit.return_value = Mock(rate=Mock(remaining=5000))
    return mock
```

## Security Considerations

### Log Viewer Security

⚠️ **CRITICAL:** Log viewer endpoints (`/logs/*`) are unauthenticated by design

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
