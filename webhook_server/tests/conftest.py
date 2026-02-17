import json
import logging as python_logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from starlette.datastructures import Headers

from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
from webhook_server.libs.log_parser import LogEntry

os.environ["WEBHOOK_SERVER_DATA_DIR"] = "webhook_server/tests/manifests"
os.environ["ENABLE_LOG_SERVER"] = "true"
from webhook_server.libs.github_api import GithubWebhook

# Test token constant - single source of truth for all test mocks
TEST_GITHUB_TOKEN = "ghp_testtoken123"  # pragma: allowlist secret

# OWNERS test data - single source of truth for all test fixtures
# This constant is used by both Repository.get_contents() and owners_files_test_data fixture
OWNERS_TEST_DATA: dict[str, dict[str, list[str] | bool]] = {
    "OWNERS": {
        "approvers": ["root_approver1", "root_approver2"],
        "reviewers": ["root_reviewer1", "root_reviewer2"],
    },
    "folder1/OWNERS": {
        "approvers": ["folder1_approver1", "folder1_approver2"],
        "reviewers": ["folder1_reviewer1", "folder1_reviewer2"],
    },
    "folder2/OWNERS": {},
    "folder/folder4/OWNERS": {
        "approvers": ["folder4_approver1", "folder4_approver2"],
        "reviewers": ["folder4_reviewer1", "folder4_reviewer2"],
    },
    "folder5/OWNERS": {
        "root-approvers": False,
        "approvers": ["folder5_approver1", "folder5_approver2"],
        "reviewers": ["folder5_reviewer1", "folder5_reviewer2"],
    },
}


class Tree:
    def __init__(self, path: str):
        self.type = "blob"
        self.path = path

    @property
    def tree(self):
        trees = []
        for _path in [
            "OWNERS",
            "folder1/OWNERS",
            "folder2/OWNERS",
            "folder/folder4/OWNERS",
            "folder5/OWNERS",
        ]:
            trees.append(Tree(_path))
        return trees


class ContentFile:
    def __init__(self, content: str):
        self.content = content

    @property
    def decoded_content(self):
        return self.content


class Repository:
    def __init__(self):
        self.name = "test-repo"
        self.full_name = "my-org/test-repo"

    def get_git_tree(self, sha: str, recursive: bool):
        return Tree("")

    def get_contents(self, path: str, ref: str):
        # Use centralized OWNERS_TEST_DATA constant
        if path in OWNERS_TEST_DATA:
            return ContentFile(yaml.dump(OWNERS_TEST_DATA[path]))
        elif path == "folder":
            return ContentFile(yaml.dump({}))


@dataclass
class Label:
    name: str


class PullRequest:
    def __init__(self, additions: int | None = None, deletions: int | None = None):
        self.additions = additions
        self.deletions = deletions

    class base:
        ref = "refs/heads/main"

    def create_issue_comment(self, *args, **kwargs): ...

    def create_review_request(self, *args, **kwargs): ...

    def get_files(self): ...


@pytest.fixture(scope="function")
def pull_request():
    return PullRequest()


@pytest.fixture(scope="function")
def github_webhook(mocker, request):
    base_import_path = "webhook_server.libs.github_api"

    mocker.patch(f"{base_import_path}.get_repository_github_app_api", return_value=True)
    mocker.patch("github.AuthenticatedUser", return_value=True)
    mocker.patch(f"{base_import_path}.get_api_with_highest_rate_limit", return_value=("API", "TOKEN", "USER"))
    mocker.patch(f"{base_import_path}.get_github_repo_api", return_value=Repository())
    mocker.patch(f"{base_import_path}.GithubWebhook.add_api_users_to_auto_verified_and_merged_users", return_value=None)

    # Use standard Python logger for caplog compatibility
    test_logger = python_logging.getLogger("GithubWebhook")
    test_logger.setLevel(python_logging.DEBUG)

    process_github_webhook = GithubWebhook(
        hook_data={"repository": {"name": Repository().name, "full_name": Repository().full_name}},
        headers=Headers({"X-GitHub-Event": "test-event"}),
        logger=test_logger,
    )
    owners_file_handler = OwnersFileHandler(github_webhook=process_github_webhook)

    return process_github_webhook, owners_file_handler


@pytest.fixture(scope="function")
def process_github_webhook(github_webhook):
    return github_webhook[0]


@pytest.fixture(scope="function")
def owners_file_handler(github_webhook):
    return github_webhook[1]


# === Performance Optimization Fixtures ===


@pytest.fixture
def sample_log_entries():
    """Pre-generated sample log entries for performance tests."""
    entries = []
    base_time = datetime(2025, 7, 31, 10, 0, 0)

    for i in range(100):
        entries.append(
            LogEntry(
                timestamp=base_time + timedelta(seconds=i),
                level="INFO",
                logger_name="GithubWebhook",
                message=f"Test log entry {i}",
                hook_id=f"test-hook-{i}",
                repository=f"test-repo-{i % 10}",
                event_type="push" if i % 2 == 0 else "pull_request",
                github_user="test-user",
                pr_number=i if i % 3 == 0 else None,
            )
        )

    return entries


@pytest.fixture(autouse=True)
def optimize_test_environment():
    """Auto-applied fixture to optimize test environment."""

    # Disable unnecessary logging during tests
    python_logging.getLogger("httpx").setLevel(python_logging.WARNING)
    python_logging.getLogger("asyncio").setLevel(python_logging.WARNING)

    # Set optimal test timeouts
    original_timeout = os.environ.get("PYTEST_TIMEOUT", "60")
    os.environ["PYTEST_TIMEOUT"] = "30"

    yield

    # Restore original timeout
    os.environ["PYTEST_TIMEOUT"] = original_timeout


@pytest.fixture
def owners_files_test_data():
    """Shared OWNERS test data structure used across multiple test files.

    Returns a dict mapping file paths to YAML-serialized OWNERS content.
    This fixture eliminates duplication between test_pull_request_owners.py
    and test_owners_files_handler.py.

    Uses centralized OWNERS_TEST_DATA constant to ensure consistency.
    """
    return {path: yaml.dump(data) for path, data in OWNERS_TEST_DATA.items()}


# === Log Viewer Shared Fixtures ===


@pytest.fixture
def mock_logger():
    """Create a mock logger that mirrors production logger attributes."""
    mock = Mock(spec=python_logging.Logger)
    mock.name = "webhook_server.tests"
    mock.level = python_logging.INFO
    return mock


@pytest.fixture
def sample_json_webhook_data() -> dict:
    """Create sample JSON webhook log data with workflow steps.

    Used by test_log_viewer.py tests for JSON log parsing and workflow step retrieval.
    """
    return {
        "hook_id": "test-hook-123",
        "event_type": "pull_request",
        "action": "opened",
        "repository": "org/test-repo",
        "sender": "test-user",
        "pr": {
            "number": 456,
            "title": "Test PR",
            "url": "https://github.com/org/test-repo/pull/456",
        },
        "timing": {
            "started_at": "2025-01-05T10:00:00.000000Z",
            "completed_at": "2025-01-05T10:00:05.000000Z",
            "duration_ms": 5000,
        },
        "workflow_steps": {
            "clone_repository": {
                "timestamp": "2025-01-05T10:00:01.000000Z",
                "status": "completed",
                "duration_ms": 1500,
            },
            "assign_reviewers": {
                "timestamp": "2025-01-05T10:00:02.500000Z",
                "status": "completed",
                "duration_ms": 800,
            },
            "apply_labels": {
                "timestamp": "2025-01-05T10:00:03.500000Z",
                "status": "failed",
                "duration_ms": 200,
                "error": {"type": "ValueError", "message": "Label not found"},
            },
        },
        "token_spend": 35,
        "success": False,
        "error": {
            "type": "TestError",
            "message": "Test failure message for unit tests",
        },
    }


@pytest.fixture
def create_json_log_file():
    """Factory fixture to create test JSON log files.

    Returns a callable that accepts log_dir, filename, and entries parameters.
    Tests pass their own tmp_path to the returned factory function.

    Usage:
        def test_example(create_json_log_file, tmp_path):
            log_dir = tmp_path / "logs"
            log_dir.mkdir()
            create_json_log_file(log_dir, "webhooks_2025-01-05.json", [entry_dict])
    """

    def _create_json_log_file(log_dir: Path, filename: str, entries: list[dict]) -> Path:
        """Create a test JSON log file with entries in JSONL format.

        The log viewer expects JSONL format (JSON Lines): one compact JSON object per line.
        This matches production behavior where each webhook log entry is written as a single
        line for efficient streaming and parsing.

        Args:
            log_dir: Directory to create the log file in
            filename: Name of the log file
            entries: List of JSON webhook data dictionaries

        Returns:
            Path to created log file
        """
        log_file = log_dir / filename
        with open(log_file, "w", encoding="utf-8") as f:
            for entry in entries:
                # JSONL format: one compact JSON object per line (no indentation)
                # This matches production log format and log_viewer._stream_json_log_entries()
                f.write(json.dumps(entry) + "\n")
        return log_file

    return _create_json_log_file


@pytest.fixture
def create_text_log_file():
    """Factory fixture to create test text log files.

    Returns a callable that accepts log_dir, filename, and log_lines parameters.
    Tests pass their own tmp_path to the returned factory function.

    Usage:
        def test_example(create_text_log_file, tmp_path):
            log_dir = tmp_path / "logs"
            log_dir.mkdir()
            create_text_log_file(log_dir, "webhook-server.log", ["line1", "line2"])
    """

    def _create_text_log_file(log_dir: Path, filename: str, log_lines: list[str]) -> Path:
        """Create a test text log file with log lines.

        Args:
            log_dir: Directory to create the log file in
            filename: Name of the log file
            log_lines: List of log line strings

        Returns:
            Path to created log file
        """
        log_file = log_dir / filename
        with open(log_file, "w", encoding="utf-8") as f:
            for line in log_lines:
                f.write(line + "\n")
        return log_file

    return _create_text_log_file
