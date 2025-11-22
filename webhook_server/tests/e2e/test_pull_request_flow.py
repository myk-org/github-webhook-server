"""End-to-end tests for pull request creation workflow.

This module contains E2E tests that verify the webhook server correctly processes PRs.
Tests ONLY verify webhook server behavior - setup/teardown handled by fixtures.
"""

import subprocess

import pytest
from simple_logger.logger import get_logger

from webhook_server.tests.e2e.helpers import (
    FAILING_CHECK_RUNS,
    PASSING_CHECK_RUNS,
    QUEUED_CHECK_RUNS,
    get_check_runs,
    get_pr_labels,
    get_repo_issues,
    wait_for_check_runs,
    wait_for_labels,
)

# Configure logging for E2E PR flow tests
logger = get_logger(name="e2e-test-pr-flow")

# Expected labels for a basic PR (size/M for minimal test file, branch-main for target branch)
EXPECTED_LABELS = ["size/M", "branch-main"]


@pytest.mark.e2e
class TestPullRequestFlow:
    """Test class for pull request workflow verification.

    All tests in this class verify different aspects of webhook server behavior
    when a PR is created. Each test is independent but uses the same PR fixture.
    """

    def test_labels_added(self, e2e_server: None, pr_for_tests: str, test_repository_name: str) -> None:
        """Verify webhook server adds expected labels to PR.

        Expected labels:
        - size/M: Based on PR changes size
        - branch-main: Target branch label

        Args:
            e2e_server: E2E server infrastructure
            pr_for_tests: PR number from fixture
            test_repository_name: Test repository name from environment
        """
        logger.info(f"Waiting for webhook server to add labels to PR #{pr_for_tests}...")
        wait_for_labels(pr_for_tests, test_repository_name, expected_labels=EXPECTED_LABELS, timeout=120)
        labels = get_pr_labels(pr_for_tests, test_repository_name)

        for expected_label in EXPECTED_LABELS:
            assert expected_label in labels, (
                f"Expected label '{expected_label}' not found on PR #{pr_for_tests}. Found labels: {labels}"
            )

        logger.info(f"Labels verified: {EXPECTED_LABELS} found on PR")

    def test_passing_check_runs(self, e2e_server: None, pr_for_tests: str, test_repository_name: str) -> None:
        """Verify webhook server creates passing check runs.

        Expected passing check runs:
        - build-container: Container build check
        - pre-commit: Pre-commit hooks check
        - python-module-install: Python module installation check
        - tox: Tox tests check

        Args:
            e2e_server: E2E server infrastructure
            pr_for_tests: PR number from fixture
            test_repository_name: Test repository name from environment
        """
        logger.info(f"Waiting for passing check runs on PR #{pr_for_tests}...")
        wait_for_check_runs(pr_for_tests, test_repository_name, expected_checks=PASSING_CHECK_RUNS, timeout=120)

        check_runs = get_check_runs(pr_for_tests, test_repository_name)
        check_run_map = {run["name"]: run for run in check_runs}

        for check_name in PASSING_CHECK_RUNS:
            assert check_name in check_run_map, f"Expected check run '{check_name}' not found on PR #{pr_for_tests}"
            check_run = check_run_map[check_name]
            status = check_run.get("status", "")
            conclusion = check_run.get("conclusion", "")

            assert status == "completed", f"Check run '{check_name}' has status '{status}', expected 'completed'"
            assert conclusion == "success", (
                f"Check run '{check_name}' completed with conclusion '{conclusion}', expected 'success'"
            )

        logger.info(f"All {len(PASSING_CHECK_RUNS)} passing check runs completed successfully")

    def test_failing_check_runs(self, e2e_server: None, pr_for_tests: str, test_repository_name: str) -> None:
        """Verify webhook server creates failing check runs.

        Expected failing check runs:
        - can-be-merged: Should fail because PR is not approved yet

        Args:
            e2e_server: E2E server infrastructure
            pr_for_tests: PR number from fixture
            test_repository_name: Test repository name from environment
        """
        logger.info(f"Waiting for failing check runs on PR #{pr_for_tests}...")
        wait_for_check_runs(pr_for_tests, test_repository_name, expected_checks=FAILING_CHECK_RUNS, timeout=120)

        logger.info(f"Verifying failing check runs on PR #{pr_for_tests}...")
        check_runs = get_check_runs(pr_for_tests, test_repository_name)
        check_run_map = {run["name"]: run for run in check_runs}

        for check_name in FAILING_CHECK_RUNS:
            assert check_name in check_run_map, f"Expected check run '{check_name}' not found on PR #{pr_for_tests}"
            check_run = check_run_map[check_name]
            status = check_run.get("status", "")
            conclusion = check_run.get("conclusion", "")

            assert status == "completed", f"Check run '{check_name}' has status '{status}', expected 'completed'"
            assert conclusion == "failure", (
                f"Check run '{check_name}' completed with conclusion '{conclusion}', expected 'failure'"
            )

        logger.info(f"All {len(FAILING_CHECK_RUNS)} check runs failed as expected (not approved/mergeable)")

    def test_queued_check_runs(self, e2e_server: None, pr_for_tests: str, test_repository_name: str) -> None:
        """Verify webhook server creates queued check runs.

        Expected queued check runs:
        - verified: Should be queued/waiting

        Args:
            e2e_server: E2E server infrastructure
            pr_for_tests: PR number from fixture
            test_repository_name: Test repository name from environment
        """
        logger.info(f"Verifying queued check runs on PR #{pr_for_tests}...")
        check_runs = get_check_runs(pr_for_tests, test_repository_name)
        check_run_map = {run["name"]: run for run in check_runs}

        for check_name in QUEUED_CHECK_RUNS:
            assert check_name in check_run_map, f"Expected check run '{check_name}' not found on PR #{pr_for_tests}"
            check_run = check_run_map[check_name]
            status = check_run.get("status", "")

            assert status == "queued", f"Check run '{check_name}' has status '{status}', expected 'queued'"

        logger.info(f"All {len(QUEUED_CHECK_RUNS)} check runs are queued as expected")

    def test_issue_created(self, e2e_server: None, pr_for_tests: str, test_repository_name: str) -> None:
        """Verify webhook server creates an issue for the PR.

        Args:
            e2e_server: E2E server infrastructure
            pr_for_tests: PR number from fixture
            test_repository_name: Test repository name from environment
        """
        logger.info(f"Verifying issue was created for PR #{pr_for_tests}...")
        for issue in get_repo_issues(test_repository_name):
            if issue["body"] == f"[Auto generated]\nNumber: [#{pr_for_tests}]":
                logger.info(f"Issue created for PR {pr_for_tests} found")
                return

        pytest.fail(f"Expected at least one issue to be created for PR #{pr_for_tests}, but found none")

    def test_welcome_message(self, e2e_server: None, pr_for_tests: str, test_repository_name: str) -> None:
        """Verify webhook server posts a welcome message to the PR.

        The welcome message contains a unique identifier string that is used to detect
        if the message already exists. This test checks for that identifier.

        Args:
            e2e_server: E2E server infrastructure
            pr_for_tests: PR number from fixture
            test_repository_name: Test repository name from environment
        """
        welcome_msg_identifier = "Report bugs in [Issues](https://github.com/myakove/github-webhook-server/issues)"

        logger.info(f"Verifying welcome message on PR #{pr_for_tests}...")
        result = subprocess.run(
            ["gh", "api", f"repos/{test_repository_name}/issues/{pr_for_tests}/comments", "--jq", ".[].body"],
            capture_output=True,
            text=True,
            check=True,
        )

        assert welcome_msg_identifier in result.stdout, (
            f"Expected welcome message with identifier '{welcome_msg_identifier}' in PR #{pr_for_tests} comments, "
            f"but not found. Comments: {result.stdout[:500]}"
        )

        logger.info("Welcome message verified")
