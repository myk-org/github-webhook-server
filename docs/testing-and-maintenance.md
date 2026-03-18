# Testing and Maintenance

This project uses a layered verification model. Most day-to-day changes are checked with fast local tests under `webhook_server/tests/`, while full GitHub workflow validation lives in `webhook_server/tests/e2e/`. On top of that, the repository is maintained with checked-in automation for linting, releases, dependency updates, and PR or issue hygiene.

> **Note:** Automation in this repository is driven mostly by checked-in tool and bot configuration. There are no `.github/workflows` files in the repo, so the practical "pipeline" lives in `pytest`, `tox`, `pre-commit`, `release-it`, Renovate, and repository bot settings.

## Test strategy

### Default local suite

The default `pytest` configuration is set up for normal contributor workflows: async tests work out of the box, coverage is always collected, logs are visible during the run, and E2E tests are skipped unless you opt in.

```ini
[pytest]
asyncio_mode = auto
addopts =
    --pdbcls=IPython.terminal.debugger:TerminalPdb
    --cov-config=pyproject.toml --cov-report=html --cov-report=term --cov=webhook_server
    --log-cli-level=DEBUG
    -m 'not e2e'
markers =
    e2e: "End-to-end tests that require real GitHub interactions (deselect with '-m \"not e2e\"')"
```

The main `tox` test environment runs the default suite in parallel with `pytest-xdist`, using the command from `tox.toml`: `uv run --extra tests pytest -n auto webhook_server/tests`.

> **Tip:** For everyday development, stay in the default non-E2E suite. It is faster, parallelized, and already includes coverage reporting.

### Unit tests

The unit layer focuses on isolated behavior. Tests stub GitHub objects, patch network boundaries, and assert exact outcomes for helpers, handlers, and config logic. You can see this style clearly in `webhook_server/tests/test_webhook.py`, which verifies webhook creation without talking to GitHub:

```python
@patch("webhook_server.utils.webhook.get_github_repo_api")
def test_process_github_webhook_success_no_existing_hooks(
    self,
    mock_get_repo_api: Mock,
    sample_data: dict[str, Any],
    apis_dict: dict[str, dict[str, Any]],
    mock_repo: Mock,
) -> None:
    mock_get_repo_api.return_value = mock_repo

    success, message, _ = process_github_webhook(
        repository_name="test-repo", data=sample_data, webhook_ip="http://example.com", apis_dict=apis_dict
    )

    assert success is True
    assert "Create webhook is done" in message

    mock_repo.create_hook.assert_called_once_with(
        name="web",
        config={"url": "http://example.com", "content_type": "json"},
        events=["push", "pull_request"],
        active=True,
    )
```

In practice, this unit layer covers a lot of ground:

- webhook helper functions and GitHub API wrappers
- handler behavior such as `PullRequestHandler`, `CheckRunHandler`, `IssueCommentHandler`, and `PushHandler`
- configuration loading and schema validation
- log parsing, filtering, structured logging, and API-call accounting
- performance-sensitive internals such as log parsing and memory usage

### Integration tests

The integration layer exercises larger slices of the application together. These tests usually run the real FastAPI app with `TestClient`, point it at checked-in test manifests, and stub only the true external boundary.

A representative example from `webhook_server/tests/test_app.py` posts a webhook into the app and verifies the real HTTP response:

```python
@pytest.fixture
def client(self) -> TestClient:
    return TestClient(FASTAPI_APP)

@patch.dict(os.environ, {"WEBHOOK_SERVER_DATA_DIR": "webhook_server/tests/manifests"})
@patch("webhook_server.app.GithubWebhook")
def test_process_webhook_success(
    self, mock_github_webhook: Mock, client: TestClient, valid_webhook_payload: dict[str, Any], webhook_secret: str
) -> None:
    payload_json = json.dumps(valid_webhook_payload)
    signature = self.create_github_signature(payload_json, webhook_secret)

    mock_webhook_instance = Mock()
    mock_github_webhook.return_value = mock_webhook_instance

    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "test-delivery-123",
        "x-hub-signature-256": signature,
        "Content-Type": "application/json",
    }

    response = client.post("/webhook_server", content=payload_json, headers=headers)

    assert response.status_code == 200
    assert response.json()["message"] == "Webhook queued for processing"
```

This is where the project verifies behavior such as:

- request validation and signature handling
- health endpoints and background-task behavior
- event dispatch through `GithubWebhook.process()`
- log viewer API endpoints, exports, and WebSocket handling
- configuration loading from `webhook_server/tests/manifests`

Only E2E tests have a dedicated pytest marker. Unit and integration tests live together in `webhook_server/tests/` and are distinguished by what they exercise rather than by separate markers.

### End-to-end tests

The E2E suite is intentionally closer to real life. It starts support infrastructure, uses a real GitHub repository, and verifies the observable side effects of webhook processing.

The core fixture in `webhook_server/tests/e2e/conftest.py` shows how that infrastructure is brought up:

```python
@pytest.fixture(scope="session")
def e2e_server(server_envs: dict[str, str], github_webhook_cleanup: None) -> Generator[None]:
    server_port = server_envs["server_port"]
    smee_url = server_envs["smee_url"]
    project_root = server_envs["project_root"]
    docker_compose_file = server_envs["docker_compose_file"]

    smee_process = start_smee_client(server_port=server_port, smee_url=smee_url)
    start_docker_compose(docker_compose_file=docker_compose_file, project_root=project_root)
    wait_for_container_health(
        docker_compose_file=docker_compose_file,
        project_root=project_root,
        container_name="github-webhook-server-e2e",
        timeout=60,
    )

    yield
```

The E2E tests themselves verify real GitHub outcomes. For example, `webhook_server/tests/e2e/test_pull_request_flow.py` waits for labels and check runs to appear on an actual PR instead of checking internal mocks.

The checked-in E2E guide runs the suite with `uv run --group tests pytest webhook_server/tests/e2e/ -v -m e2e`, and it expects a local `.dev/.env` file like this:

```bash
SERVER_PORT=5000
SMEE_URL=https://smee.io/YOUR_UNIQUE_CHANNEL
TEST_REPO=owner/repo-name
DOCKER_COMPOSE_FILE=.dev/docker-compose.yaml
TZ=America/New_York
```

A few practical details matter here:

- all GitHub operations in the E2E helpers use `gh` CLI
- the suite starts both a local Docker Compose stack and a `smee` relay client
- test fixtures create and clean up real branches, PRs, and webhooks
- `.dev/` is ignored by Git, so each developer keeps E2E configuration local

> **Warning:** E2E tests use real GitHub credentials, a real test repository, and a public Smee relay. Use a disposable test repo, keep `.dev/` local, and do not run the suite in parallel with `-n auto`.

### Specialized tests

The suite also includes targeted operational checks outside the usual unit/integration/E2E split:

- `test_performance_benchmarks.py` measures log parsing and filtering speed at larger scales.
- `test_memory_optimization.py` and `test_frontend_performance.py` protect log viewer scalability.
- `test_api_call_counting.py` verifies GitHub API usage tracking.
- `test_config_schema.py` and `test_schema_validator.py` cover configuration correctness from both schema and runtime angles.

## Coverage expectations

Coverage is enforced, not just reported. The threshold lives in `pyproject.toml`, and test files themselves are excluded from measurement so the percentage reflects application code.

```toml
[tool.coverage.run]
omit = ["webhook_server/tests/*"]

[tool.coverage.report]
fail_under = 90
skip_empty = true

[tool.coverage.html]
directory = ".tests_coverage"
```

That means:

- the test run fails if application coverage drops below 90%
- HTML coverage output is written to `.tests_coverage`
- the default pytest configuration already enables `--cov=webhook_server`, so you do not need to remember extra coverage flags for normal runs

## Pre-commit tooling and local maintenance

The repository uses `pre-commit` as the first quality gate before review. The configured hooks cover formatting, linting, types, JavaScript checks, and secret scanning.

A trimmed excerpt from `.pre-commit-config.yaml` shows the core stack:

```yaml
ci:
  autofix_prs: false
  autoupdate_commit_msg: "ci: [pre-commit.ci] pre-commit autoupdate"

repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: check-added-large-files
      - id: check-merge-conflict
      - id: detect-private-key
      - id: trailing-whitespace
      - id: end-of-file-fixer

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.6
    hooks:
      - id: ruff
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.19.1
    hooks:
      - id: mypy
        exclude: (tests/)

  - repo: https://github.com/pre-commit/mirrors-eslint
    rev: v10.0.3
    hooks:
      - id: eslint
        files: \.js$
```

The full config also includes:

- `flake8` with additional plugins
- `detect-secrets`
- `gitleaks`

Because `autofix_prs` is set to `false`, `pre-commit.ci` reports failures and opens hook update PRs, but it does not push automatic fixes back to contributor branches.

There is also a maintenance-focused `tox` setup in `tox.toml` with two environments: `unittests` for the main test suite and `unused-code`, which runs `pyutils-unusedcode` to catch dead code early.

One especially practical integration point is built into the server itself: when a managed repository contains `.pre-commit-config.yaml`, the project automatically adds the `pre-commit.ci - pr` status context to the default status checks. In other words, `pre-commit.ci` is treated as part of the review flow, not just as an optional extra.

## Release automation

Releases are automated with `release-it`. The checked-in configuration handles version bumping, changelog generation, Git tagging and pushing, and GitHub Release creation.

```json
{
  "npm": {
    "publish": false
  },
  "git": {
    "commit": true,
    "commitMessage": "Release ${version}",
    "tag": true,
    "tagAnnotation": "Release ${version}",
    "push": true,
    "pushArgs": ["--follow-tags"],
    "changelog": "uv run scripts/generate_changelog.py ${from} ${to}"
  },
  "github": {
    "release": true,
    "releaseName": "Release ${version}",
    "tokenRef": "GITHUB_TOKEN"
  },
  "plugins": {
    "@release-it/bumper": {
      "in": "pyproject.toml",
      "out": { "file": "pyproject.toml", "path": "project.version" }
    }
  },
  "hooks": {
    "after:bump": "uv sync"
  }
}
```

In practice, that automation does the following:

- updates `project.version` in `pyproject.toml`
- regenerates dependencies after the bump with `uv sync`
- creates a release commit and annotated tag
- pushes commits and tags together
- creates a GitHub Release using `GITHUB_TOKEN`

The release notes text comes from `scripts/generate_changelog.py`, which groups commits by conventional prefixes such as `feat`, `fix`, `docs`, `test`, and `ci`.

> **Tip:** Conventional commit prefixes make release notes much easier to read in this project, because the changelog generator groups entries by prefix.

## Dependency updates and repository bots

Dependency updates are handled by Renovate, and the repository has several other bots configured for review and hygiene.

The Renovate configuration is intentionally simple and low-noise:

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": [
    ":dependencyDashboard",
    ":maintainLockFilesWeekly",
    ":prHourlyLimitNone",
    ":semanticCommitTypeAll(ci )"
  ],
  "prConcurrentLimit": 0,
  "lockFileMaintenance": {
    "enabled": true
  },
  "packageRules": [
    {
      "matchPackagePatterns": ["*"],
      "groupName": "python-deps"
    }
  ]
}
```

That setup means:

- Renovate keeps a dependency dashboard.
- Lock file maintenance is enabled weekly.
- Dependency PRs are not throttled by hourly or concurrent limits.
- Updates are grouped into a single `python-deps` stream instead of a flood of unrelated PRs.

The rest of the repository bot setup looks like this:

- `pre-commit.ci` is configured through `.pre-commit-config.yaml` and is part of the expected status-check flow.
- CodeRabbit is configured in `.coderabbit.yaml` with auto-review on non-draft PRs targeting `main`, `request_changes_workflow: true`, and tool integrations including Ruff, Pylint, ESLint, ShellCheck, Yamllint, Gitleaks, Semgrep, Actionlint, and Hadolint.
- The stale bot is configured in `.github/stale.yml` to mark inactive items stale after 60 days and close them 7 days later, while exempting `pinned` and `security`.
- The In Solidarity bot is configured in `.github/in-solidarity.yml` to enforce inclusive-language checks at failure level.

If you use `github-webhook-server` to manage your own repositories, the shipped example config also treats common automation accounts as trusted bots by listing `renovate[bot]` and `pre-commit-ci[bot]` under `auto-verified-and-merged-users`. That is a good starting point if you want dependency and hook-update PRs to fit cleanly into an automated review flow.
