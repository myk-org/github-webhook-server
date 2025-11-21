# End-to-End (E2E) Testing Guide

## Purpose and Audience

This guide helps developers set up and run end-to-end tests for the GitHub webhook server. E2E tests verify the complete workflow: GitHub sends webhooks → smee.io proxies them → server processes them → changes appear in GitHub.

**Target audience:** Developers contributing to the webhook server who need to validate full integration behavior.

## Overview

The E2E testing infrastructure provides:

- **Automated Infrastructure Setup**: Fixtures automatically start smee client and Docker container
- **Real GitHub Integration**: Tests interact with actual GitHub repositories using `gh` CLI
- **Health Monitoring**: Waits for server to be healthy before running tests
- **Automatic Cleanup**: Ensures resources are properly cleaned up after tests complete or fail
- **Isolation**: Uses pytest markers to prevent accidental execution

## Prerequisites

Before running E2E tests, ensure you have these tools installed:

### 1. Docker and Docker Compose

```bash
# Verify Docker is installed
docker info

# Verify Docker Compose is installed
docker compose version
```

### 2. Node.js and smee-client

```bash
# Install smee-client globally
npm install -g smee-client

# Verify installation
which smee
smee --version
```

### 3. GitHub CLI

**CRITICAL:** All GitHub operations use `gh` CLI (NOT PyGithub, NOT direct HTTP calls).

```bash
# Install gh CLI (if not already installed)
# macOS: brew install gh
# Linux: See https://github.com/cli/cli/blob/trunk/docs/install_linux.md

# Verify installation
gh --version

# Authenticate with GitHub
gh auth login

# Verify authentication
gh auth status
```

### 4. Python Dependencies

```bash
# Install all dependencies including test group
uv sync
```

## Configuration

### Required: .dev/.env File

**CRITICAL:** E2E tests require a `.dev/.env` file with configuration. Tests will fail if this file is missing.

Create `.dev/.env` in the project root:

```bash
# .dev/.env
SERVER_PORT=5000
SMEE_URL=https://smee.io/YOUR_UNIQUE_CHANNEL
TEST_REPO=owner/repo-name
DOCKER_COMPOSE_FILE=.dev/docker-compose.yaml
TZ=America/New_York
```

**How to get a Smee URL:**

1. Visit https://smee.io/
2. Click "Start a new channel"
3. Copy the generated URL (e.g., `https://smee.io/abc123def456`)
4. Add to `.dev/.env` as `SMEE_URL=https://smee.io/webhook_server`

**Configuration details:**

- `SERVER_PORT`: Local server port that webhooks are forwarded to (default: `5000`)
  - Port `5000` maps to container port `5000` (see docker-compose.yaml)
  - Smee client forwards to `localhost:5000/webhook_server`
- `SMEE_URL`: Webhook proxy URL from smee.io (get your own at https://smee.io/)
- `TEST_REPO`: GitHub repository for E2E tests (format: `owner/repo-name`)
  - Default test repository: `myk-org/for-testing-only`
  - Must have write access to this repository
- `DOCKER_COMPOSE_FILE`: Path to docker-compose.yaml file (relative to project root)
  - Default: `.dev/docker-compose.yaml`
- `TZ`: Timezone for server logs (optional, defaults to UTC)

**Why .dev/.env instead of environment variables?**

- Persistent configuration across sessions
- Easy to update without re-exporting variables
- Version-controlled example without secrets
- Consistent with Docker Compose environment setup

## Running E2E Tests

### CRITICAL: Pytest Marker Requirement

**All E2E tests MUST have the `@pytest.mark.e2e` decorator and MUST be run with `-m e2e` flag.**

E2E tests will NOT run without the marker flag to prevent accidental execution during regular test runs.

### Run All E2E Tests

```bash
# From project root
uv run --group tests pytest webhook_server/tests/e2e/ -v -m e2e
```

### Run Specific Test File

```bash
# Example: Run pull request flow tests
uv run --group tests pytest webhook_server/tests/e2e/test_pull_request_flow.py -v -m e2e
```

### Run Specific Test Case

```bash
# Example: Run a single test function
uv run --group tests pytest webhook_server/tests/e2e/test_pull_request_flow.py::test_create_pr_basic_flow -v -m e2e
```

### What Happens During Test Execution

1. **Fixture Setup** (once per session):
   - Loads `.dev/.env` configuration
   - Validates `SMEE_URL` and `SERVER_PORT` exist
   - Starts smee client to proxy webhooks from smee.io to local server port
   - Starts Docker container with webhook server
   - Waits for container health check (via Docker healthcheck, max 60 seconds)

2. **Test Execution**:
   - Tests use `gh` CLI to interact with GitHub (create PRs, add comments, etc.)
   - GitHub sends webhooks to smee.io
   - Smee client proxies webhooks to local server
   - Server processes webhooks and performs actions
   - Tests verify results in GitHub using `gh` CLI

3. **Cleanup** (automatic):
   - Stops smee client gracefully (5-second timeout, then kill)
   - Stops Docker Compose container
   - Cleanup happens even if tests fail or are interrupted (Ctrl+C)

## Fixtures

### `server_envs` (session-scoped)

Loads and validates environment variables from `.dev/.env`.

**Returns:** `dict` with:
- `server_port`: Local server port (e.g., `"5000"`)
- `smee_url`: Smee.io webhook proxy URL (e.g., `https://smee.io/abc123`)
- `test_repo`: Test repository name (e.g., `"owner/repo-name"`)
- `project_root`: Absolute path to project root
- `docker_compose_file`: Absolute path to `docker-compose.yaml`

**Raises:**
- `E2EInfrastructureError` if `.dev/.env` file does not exist
- `E2EInfrastructureError` if `SERVER_PORT` or `SMEE_URL` are missing

**Example usage:** Typically not used directly (consumed by `e2e_server` fixture).

### `e2e_server` (session-scoped)

Main fixture that manages complete E2E infrastructure lifecycle.

**Returns:** `None` (tests interact with GitHub via `gh` CLI, not server directly)

**Lifecycle:**
1. Starts smee client (automatic)
2. Starts Docker Compose container (automatic)
3. Waits for container health via Docker healthcheck (automatic)
4. Yields to tests
5. Cleanup: stops smee + Docker Compose (automatic)

**Example usage:**

```python
import pytest

@pytest.mark.e2e
def test_webhook_processing(e2e_server):
    """Infrastructure is running, ready to test."""
    # Use gh CLI to interact with GitHub
    # Server processes webhooks automatically
    # Verify results in GitHub
    pass
```

## Writing E2E Tests

### Test Structure Requirements

**MANDATORY:** All E2E tests must:
1. Have `@pytest.mark.e2e` decorator
2. Accept `e2e_server` fixture parameter
3. Use `gh` CLI for GitHub operations (NOT PyGithub, NOT HTTP calls)
4. Clean up created resources (PRs, branches)

### Basic Test Template

```python
import pytest
import subprocess

@pytest.mark.e2e
def test_webhook_flow(e2e_server):
    """Test description explaining what is being validated."""
    # Infrastructure is running (smee + docker-compose)

    # Step 1: Perform GitHub action using gh CLI
    result = subprocess.run(
        ["gh", "pr", "create", "--title", "Test PR", "--body", "Test body"],
        capture_output=True,
        text=True,
        check=True,
    )
    pr_url = result.stdout.strip()
    pr_number = pr_url.split("/")[-1]

    # Step 2: Wait for webhook processing (if needed)
    import time
    time.sleep(5)  # Give server time to process webhook

    # Step 3: Verify results using gh CLI
    result = subprocess.run(
        ["gh", "pr", "view", pr_number, "--json", "labels"],
        capture_output=True,
        text=True,
        check=True,
    )

    # Step 4: Clean up
    subprocess.run(
        ["gh", "pr", "close", pr_number, "--delete-branch"],
        check=True,
    )
```

### GitHub CLI Usage Patterns

**Creating Pull Requests:**

```python
# Create PR with title and body
result = subprocess.run(
    ["gh", "pr", "create", "--title", "Test PR", "--body", "Test description"],
    capture_output=True,
    text=True,
    check=True,
)
pr_url = result.stdout.strip()
```

**Viewing PR Data:**

```python
# Get PR data as JSON
result = subprocess.run(
    ["gh", "pr", "view", pr_number, "--json", "labels,state,comments"],
    capture_output=True,
    text=True,
    check=True,
)
pr_data = json.loads(result.stdout)
```

**Adding Comments:**

```python
# Add comment to PR
subprocess.run(
    ["gh", "pr", "comment", pr_number, "--body", "/verify-owners"],
    check=True,
)
```

**Closing PRs:**

```python
# Close PR and delete branch
subprocess.run(
    ["gh", "pr", "close", pr_number, "--delete-branch"],
    check=True,
)
```

**API Calls:**

```python
# Use GitHub API directly
result = subprocess.run(
    ["gh", "api", f"/repos/owner/repo/pulls/{pr_number}/reviews"],
    capture_output=True,
    text=True,
    check=True,
)
reviews = json.loads(result.stdout)
```

### Test Repository

E2E tests use the `myk-org/for-testing-only` repository:

- **Purpose:** Dedicated test repository with controlled configuration
- **OWNERS Files:** Contains nested OWNERS files for ownership testing
- **Validation Toggle:** Tests can enable/disable validation via `tests/config.py`
- **Pre-commit Hooks:** Can be configured to pass/fail for testing check runs
- **Tox Tests:** Can be configured to pass/fail for testing status checks

**Repository structure:**

```
for-testing-only/
├── src/
│   └── OWNERS                    # Nested OWNERS file
├── tests/
│   ├── config.py                 # VALIDATION_ENABLED toggle
│   └── OWNERS                    # Nested OWNERS file
├── OWNERS                        # Root OWNERS file
├── .pre-commit-config.yaml       # Pre-commit configuration
└── tox.ini                       # Tox configuration
```

## Troubleshooting

### Test Discovery: No Tests Run

**Symptom:** `collected 0 items` or tests are skipped

**Cause:** Missing `-m e2e` marker flag

**Solution:**

```bash
# WRONG - tests won't run
uv run --group tests pytest webhook_server/tests/e2e/ -v

# CORRECT - tests will run
uv run --group tests pytest webhook_server/tests/e2e/ -v -m e2e
```

### Configuration Error: .dev/.env Not Found

**Symptom:** `E2EInfrastructureError: Required .dev/.env file not found`

**Cause:** Missing `.dev/.env` configuration file

**Solution:**

```bash
# Create .dev/.env file
cat > .dev/.env << 'EOF'
SERVER_PORT=5000
SMEE_URL=https://smee.io/webhook_server
TEST_REPO=owner/repo-name
DOCKER_COMPOSE_FILE=.dev/docker-compose.yaml
TZ=America/New_York
EOF

# Get your Smee URL from https://smee.io/
```

### Configuration Error: Missing SMEE_URL or SERVER_PORT

**Symptom:** `E2EInfrastructureError: SMEE_URL environment variable is required`

**Cause:** `.dev/.env` exists but missing required variables

**Solution:** Edit `.dev/.env` and ensure all required variables are set:

```bash
SERVER_PORT=5000
SMEE_URL=https://smee.io/abc123def456
TEST_REPO=owner/repo-name
DOCKER_COMPOSE_FILE=.dev/docker-compose.yaml
```

### Infrastructure Error: smee-client Not Found

**Symptom:** `E2EInfrastructureError: smee client not found`

**Cause:** smee-client not installed or not in PATH

**Solution:**

```bash
# Install smee-client globally
npm install -g smee-client

# Verify installation
which smee
smee --version
```

### Infrastructure Error: Container Health Check Failed

**Symptom:** `E2EInfrastructureError: Webhook server container health check failed`

**Cause:** Docker container did not become healthy within 60 seconds

**Solutions:**

1. Check Docker is running:
   ```bash
   docker info
   ```

2. Check container status:
   ```bash
   docker compose --file .dev/docker-compose.yaml ps
   ```

3. Check container logs for errors:
   ```bash
   docker compose --file .dev/docker-compose.yaml logs
   ```

4. Verify configuration file exists:
   ```bash
   ls -la .dev/data/config.yaml
   ```

5. Verify port is not in use:
   ```bash
   lsof -i :5000
   ```

6. Rebuild container:
   ```bash
   docker compose --file .dev/docker-compose.yaml down
   docker compose --file .dev/docker-compose.yaml build
   docker compose --file .dev/docker-compose.yaml up -d
   ```

### GitHub CLI Error: Not Authenticated

**Symptom:** `gh: authentication required` or `gh: could not determine authenticated user`

**Cause:** GitHub CLI not authenticated

**Solution:**

```bash
# Authenticate with GitHub
gh auth login

# Follow interactive prompts to authenticate

# Verify authentication
gh auth status
```

### Port Conflict Error

**Symptom:** `port is already allocated` or `address already in use`

**Cause:** Port 5000 is already in use by another process

**Solutions:**

1. Stop existing containers:
   ```bash
   docker compose --file .dev/docker-compose.yaml down
   ```

2. Find process using port:
   ```bash
   lsof -i :5000
   ```

3. Kill process or change port in `docker-compose.yaml`

### Smee Client Issues

**Symptom:** Webhooks not reaching local server

**Cause:** Smee client not running or incorrect URL configuration

**Solutions:**

1. Check smee process is running:
   ```bash
   ps aux | grep smee
   ```

2. Verify SMEE_URL is correct in `.dev/.env`

3. Test smee URL in browser - should show "Ready" message at https://smee.io/webhook_server

4. Check smee client logs (printed during test setup)

### Docker Compose Fails to Start

**Symptom:** `E2EInfrastructureError: Failed to start docker-compose`

**Solutions:**

1. Verify Docker is running:
   ```bash
   docker info
   ```

2. Validate docker-compose file:
   ```bash
   docker compose --file .dev/docker-compose.yaml config
   ```

3. Check for syntax errors in docker-compose.yaml

4. Ensure data directory exists:
   ```bash
   mkdir -p .dev/data
   ```

5. Check Docker permissions:
   ```bash
   # Linux: add user to docker group
   sudo usermod -aG docker $USER
   # Logout and login for changes to take effect
   ```

## Architecture

### Infrastructure Components

1. **Smee Client** (smee-client via npm)
   - Proxies webhooks from smee.io to local server
   - Automatically started by fixture
   - Runs as subprocess with PID tracking
   - Graceful shutdown with 5-second timeout

2. **Docker Compose** (docker-compose.yaml)
   - Runs webhook server in container
   - Mounts `.dev/data` for configuration
   - Exposes port 5000 (maps to container port 5000)
   - Has health check endpoint for monitoring

3. **GitHub CLI** (gh)
   - All GitHub operations use `gh` CLI
   - Authenticated with user's GitHub account
   - Provides JSON output for parsing
   - More reliable than direct API calls

### Startup Sequence

1. `server_envs` fixture:
   - Loads `.dev/.env` file
   - Validates `SMEE_URL` and `SERVER_PORT`
   - Returns environment configuration

2. `e2e_server` fixture:
   - Starts smee client (subprocess)
   - Starts Docker Compose container
   - Waits for container health check (max 60 seconds)
   - Yields control to tests

3. Test execution:
   - Tests use `gh` CLI to interact with GitHub
   - GitHub sends webhooks to smee.io
   - Smee proxies webhooks to local server
   - Server processes webhooks
   - Tests verify results in GitHub

4. Cleanup (automatic):
   - Stops smee client (terminate → wait 5s → kill if needed)
   - Stops Docker Compose container
   - Cleanup runs even if tests fail

### Cleanup Guarantees

Cleanup is guaranteed to run even if:
- Tests fail with assertions
- Tests raise exceptions
- Tests are interrupted (Ctrl+C)
- Setup fails partway through

This is achieved using pytest's fixture yield mechanism with proper error handling.

## Performance Considerations

### Session-Scoped Fixtures

Infrastructure starts ONCE per test session, not per test:

- Faster test execution (no repeated setup/teardown)
- Consistent environment across all tests
- Shared Docker container reduces resource usage

### Parallel Execution

**NOT RECOMMENDED:** Running E2E tests in parallel is not supported:

- Only one server instance can bind to port 5000
- Smee client can only proxy to one server
- GitHub webhooks are serialized by nature
- Tests may interfere with each other

**Run tests sequentially:**

```bash
# CORRECT - sequential execution
uv run --group tests pytest webhook_server/tests/e2e/ -v -m e2e

# WRONG - parallel execution will fail
uv run --group tests pytest webhook_server/tests/e2e/ -v -m e2e -n auto
```

### Test Isolation

While infrastructure is shared, tests should be isolated:

- Create unique PRs/branches per test
- Clean up created resources after test
- Use unique identifiers in test data
- Don't rely on specific GitHub state

## Security Considerations

### Smee.io Webhook Visibility

**WARNING:** Webhooks proxied through smee.io are publicly visible.

- Anyone with your smee URL can view webhook payloads
- Webhook payloads contain repository data, user information
- Each test run should use a unique smee channel

**For sensitive testing:**

1. Use a fresh smee channel per session (https://smee.io/)
2. Don't use smee.io - set up private webhook relay
3. Use test repositories with no sensitive data

### GitHub Authentication

- Tests use your personal GitHub authentication (`gh auth`)
- Tests run with your GitHub permissions
- Tests interact with real GitHub repositories
- Be careful with destructive operations

### Log Viewer Access

**CRITICAL:** The webhook server log viewer (`/logs/*`) has NO authentication.

- Deploy only on trusted networks (VPN, localhost)
- Never expose to public internet
- Logs contain sensitive data (tokens, webhook payloads)
- Use reverse proxy with authentication for external access

## File Structure

```
webhook_server/tests/e2e/
├── __init__.py                      # Package marker
├── conftest.py                      # Session fixtures (server_envs, e2e_server)
├── server_utils.py                  # Utility functions (smee, docker-compose, health)
├── test_pull_request_flow.py        # PR workflow tests
├── test_issue_comment_commands.py   # Comment command tests (/verify-owners, etc.)
├── test_check_runs.py               # Check run and status tests
├── test_owners_discovery.py         # OWNERS file discovery tests
├── test_reviews.py                  # PR review and approval tests
└── README.md                        # This file
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: E2E Tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  e2e-tests:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Docker
        uses: docker/setup-buildx-action@v3

      - name: Install Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install smee-client
        run: npm install -g smee-client

      - name: Install GitHub CLI
        run: |
          # Ubuntu includes gh in apt repositories
          sudo apt-get update
          sudo apt-get install -y gh

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install uv
        run: pip install uv

      - name: Install dependencies
        run: uv sync

      - name: Create .dev/.env
        run: |
          mkdir -p .dev
          cat > .dev/.env << EOF
          SERVER_PORT=5000
          SMEE_URL=${{ secrets.SMEE_URL }}
          TEST_REPO=${{ secrets.TEST_REPO }}
          DOCKER_COMPOSE_FILE=.dev/docker-compose.yaml
          TZ=UTC
          EOF

      - name: Authenticate GitHub CLI
        run: |
          echo "${{ secrets.GH_TOKEN }}" | gh auth login --with-token

      - name: Run E2E tests
        run: uv run --group tests pytest webhook_server/tests/e2e/ -v -m e2e
```

**Required secrets:**
- `SMEE_URL`: Your smee.io channel URL
- `GH_TOKEN`: GitHub personal access token for `gh` CLI

## Related Documentation

- [Main test suite documentation](../README.md)
- [Docker Compose configuration](../../../.dev/docker-compose.yaml)
- [Smee.io documentation](https://smee.io/)
- [GitHub CLI documentation](https://cli.github.com/manual/)
- [pytest fixtures documentation](https://docs.pytest.org/en/stable/fixture.html)
- [pytest markers documentation](https://docs.pytest.org/en/stable/example/markers.html)

## Quick Reference

### Commands Cheat Sheet

```bash
# Run all E2E tests
uv run --group tests pytest webhook_server/tests/e2e/ -v -m e2e

# Run specific test file
uv run --group tests pytest webhook_server/tests/e2e/test_pull_request_flow.py -v -m e2e

# Run specific test
uv run --group tests pytest webhook_server/tests/e2e/test_file.py::test_name -v -m e2e

# Check Docker container status
docker compose --file .dev/docker-compose.yaml ps

# View Docker container logs
docker compose --file .dev/docker-compose.yaml logs

# Restart Docker container
docker compose --file .dev/docker-compose.yaml restart

# Stop infrastructure manually
docker compose --file .dev/docker-compose.yaml down

# Verify gh authentication
gh auth status

# Get new smee channel
# Visit https://smee.io/ and click "Start a new channel"
```

### Required .dev/.env Template

```bash
# .dev/.env
SERVER_PORT=5000
SMEE_URL=https://smee.io/webhook_server
TEST_REPO=owner/repo-name
DOCKER_COMPOSE_FILE=.dev/docker-compose.yaml
TZ=America/New_York
```

### Pytest Marker Requirement

```python
# REQUIRED - all E2E tests must have this decorator
@pytest.mark.e2e
def test_something(e2e_server):
    pass
```
