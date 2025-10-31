import logging as python_logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
import yaml
from starlette.datastructures import Headers

from webhook_server.libs.graphql.webhook_data import PullRequestWrapper
from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
from webhook_server.libs.log_parser import LogEntry

os.environ["WEBHOOK_SERVER_DATA_DIR"] = "webhook_server/tests/manifests"
os.environ["ENABLE_LOG_SERVER"] = "true"
from webhook_server.libs.github_api import GithubWebhook


class Tree:
    def __init__(self, path: str):
        self.type = "blob"
        self.path = path

    @property
    def tree(self):
        """Return tree as list of dicts for GraphQL compatibility."""
        trees = []
        for _path in [
            "OWNERS",
            "folder1/OWNERS",
            "folder2/OWNERS",
            "folder/folder4/OWNERS",
            "folder5/OWNERS",
        ]:
            trees.append({"type": "blob", "path": _path})
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
        owners_data = yaml.dump({
            "approvers": ["root_approver1", "root_approver2"],
            "reviewers": ["root_reviewer1", "root_reviewer2"],
        })

        folder1_owners_data = yaml.dump({
            "approvers": ["folder1_approver1", "folder1_approver2"],
            "reviewers": ["folder1_reviewer1", "folder1_reviewer2"],
        })

        folder4_owners_data = yaml.dump({
            "approvers": ["folder4_approver1", "folder4_approver2"],
            "reviewers": ["folder4_reviewer1", "folder4_reviewer2"],
        })

        folder5_owners_data = yaml.dump({
            "root-approvers": False,
            "approvers": ["folder5_approver1", "folder5_approver2"],
            "reviewers": ["folder5_reviewer1", "folder5_reviewer2"],
        })
        if path == "OWNERS":
            return ContentFile(owners_data)

        elif path == "folder1/OWNERS":
            return ContentFile(folder1_owners_data)

        elif path == "folder2/OWNERS":
            return ContentFile(yaml.dump({}))

        elif path == "folder/folder4/OWNERS":
            return ContentFile(folder4_owners_data)

        elif path == "folder":
            return ContentFile(yaml.dump({}))

        elif path == "folder5/OWNERS":
            return ContentFile(folder5_owners_data)


@dataclass
class Label:
    name: str


@pytest.fixture(scope="function")
def pull_request():
    """Return PullRequestWrapper with webhook format data.

    Uses webhook field names (e.g., 'draft' not 'isDraft')
    to ensure test data mirrors actual GitHub webhook payloads.
    """

    webhook_data = {
        "node_id": "PR_kgDOTestId",
        "number": 123,
        "title": "Test PR",
        "body": "Test body",
        "state": "open",
        "merged": False,
        "mergeable": True,
        "draft": False,
        "additions": 100,
        "deletions": 50,
        "base": {"ref": "main", "sha": "abc123", "repo": {"owner": {"login": "test-owner"}, "name": "test-repo"}},
        "head": {"ref": "feature", "sha": "def456", "repo": {"owner": {"login": "test-owner"}, "name": "test-repo"}},
        "user": {"login": "testuser"},
        "html_url": "https://github.com/test-owner/test-repo/pull/123",
        "commits": [],
        "labels": [],
    }
    return PullRequestWrapper("test-owner", "test-repo", webhook_data)


def create_mock_pull_request(pr_id: str = "PR_kgDOTestId", pr_number: int = 123):
    """
    Shared helper to create Mock PullRequest objects with id and number.

    This helper DRYs up multiple tests that need mock PRs with consistent structure.

    Args:
        pr_id: GraphQL node ID for the PR (default: "PR_kgDOTestId")
        pr_number: PR number (default: 123)

    Returns:
        Mock object with id and number attributes
    """

    mock_pr = Mock()
    mock_pr.id = pr_id
    mock_pr.number = pr_number
    return mock_pr


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
    process_github_webhook.repository.full_name = "test-owner/test-repo"

    # Mock unified_api for all tests
    process_github_webhook.unified_api = AsyncMock()
    process_github_webhook.unified_api.get_pull_request_files = AsyncMock(return_value=[])
    process_github_webhook.unified_api.create_issue_comment = AsyncMock()
    process_github_webhook.unified_api.get_issue_comments = AsyncMock(return_value=[])
    process_github_webhook.unified_api.get_issue_comment = AsyncMock()
    process_github_webhook.unified_api.create_reaction = AsyncMock()
    process_github_webhook.unified_api.get_contributors = AsyncMock(return_value=[])
    process_github_webhook.unified_api.get_collaborators = AsyncMock(return_value=[])
    process_github_webhook.unified_api.get_branch = AsyncMock()
    process_github_webhook.unified_api.get_branch_protection = AsyncMock()
    process_github_webhook.unified_api.get_issues = AsyncMock(return_value=[])
    process_github_webhook.unified_api.create_issue = AsyncMock()
    process_github_webhook.unified_api.edit_issue = AsyncMock()
    process_github_webhook.unified_api.add_comment = AsyncMock()
    process_github_webhook.unified_api.get_contents = AsyncMock()
    # Set realistic return for get_git_tree with dict format for GraphQL compatibility
    mock_tree = {"tree": []}
    process_github_webhook.unified_api.get_git_tree = AsyncMock(return_value=mock_tree)
    process_github_webhook.unified_api.get_commit_check_runs = AsyncMock(return_value=[])
    process_github_webhook.unified_api.create_check_run = AsyncMock()
    process_github_webhook.unified_api.merge_pull_request = AsyncMock()
    mock_pr = create_mock_pull_request(pr_id="PR_node_id")
    mock_pr.merged = False
    process_github_webhook.unified_api.get_pull_request = AsyncMock(return_value=mock_pr)
    process_github_webhook.unified_api.add_assignees_by_login = AsyncMock()

    # Mock repository_data for pre-fetched data access (Task 70)
    process_github_webhook.repository_data = {
        "collaborators": {"edges": []},
        "mentionableUsers": {"nodes": []},
        "issues": {"nodes": []},
        "pullRequests": {"nodes": []},
    }

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
