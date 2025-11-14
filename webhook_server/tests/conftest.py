import logging as python_logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
import yaml
from starlette.datastructures import Headers

from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
from webhook_server.libs.log_parser import LogEntry

os.environ["WEBHOOK_SERVER_DATA_DIR"] = "webhook_server/tests/manifests"
os.environ["ENABLE_LOG_SERVER"] = "true"
from webhook_server.libs.github_api import GithubWebhook

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
