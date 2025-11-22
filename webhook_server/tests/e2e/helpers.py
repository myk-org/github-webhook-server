"""Helper functions for E2E testing with myk-org/for-testing-only repository.

This module provides helper functions for E2E tests:
- Branch management (delete)
- Pull request lifecycle (create, comment, close)
- Check run monitoring and waiting
- Label monitoring and waiting
- Configuration toggling in test repository

All operations use `gh` CLI for GitHub API calls and local git for repository operations.
"""

import json
import re
import subprocess
from typing import Any

from simple_logger.logger import get_logger
from timeout_sampler import TimeoutSampler

# Check runs that should PASS
PASSING_CHECK_RUNS = [
    "build-container",
    "pre-commit",
    "python-module-install",
    "tox",
]

# Check runs that should FAIL (not mergeable without approval)
FAILING_CHECK_RUNS = [
    "can-be-merged",
]

# Check runs that should be QUEUED (waiting)
QUEUED_CHECK_RUNS = [
    "verified",
]

# Configure logging for E2E test helpers
logger = get_logger(name="e2e-test-helpers")


def delete_branch(branch_name: str, test_repo: str) -> None:
    """Delete a branch using GitHub API.

    This is a cleanup function - logs errors but doesn't raise exceptions.

    Args:
        branch_name: Name of the branch to delete
        test_repo: Test repository in format owner/repo-name
    """
    logger.info(f"Deleting branch '{branch_name}' from {test_repo}")

    try:
        subprocess.run(
            ["gh", "api", "-X", "DELETE", f"repos/{test_repo}/git/refs/heads/{branch_name}"],
            capture_output=True,
            text=True,
            check=True,
        )
        logger.info(f"Branch '{branch_name}' deleted successfully")
    except subprocess.CalledProcessError as ex:
        logger.error(f"Failed to delete branch '{branch_name}': {ex.stderr}")


def create_pr(title: str, branch: str, body: str = "", base: str = "main", test_repo: str = "") -> str:
    """Create a pull request from branch to base.

    Args:
        title: PR title
        branch: Head branch for the PR
        body: PR body/description (default: "")
        base: Base branch to merge into (default: "main")
        test_repo: Test repository in format owner/repo-name

    Returns:
        str: PR number extracted from the created PR URL

    Raises:
        subprocess.CalledProcessError: If PR creation fails
    """
    logger.info(f"Creating PR: '{title}' ({branch} -> {base}) in {test_repo}")

    result = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            test_repo,
            "--title",
            title,
            "--body",
            body,
            "--head",
            branch,
            "--base",
            base,
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    # Extract PR number from URL (e.g., https://github.com/owner/repo/pull/123)
    pr_url = result.stdout.strip()
    match = re.search(r"/pull/(\d+)$", pr_url)
    if not match:
        raise ValueError(f"Failed to extract PR number from URL: {pr_url}")

    pr_number = match.group(1)
    logger.info(f"PR #{pr_number} created successfully: {pr_url}")
    return pr_number


def get_check_runs(pr_number: str, test_repo: str) -> list[dict[str, Any]]:
    """Get all check runs for a pull request.

    Args:
        pr_number: PR number to get check runs for
        test_repo: Test repository in format owner/repo-name

    Returns:
        list[dict[str, Any]]: List of check run dictionaries from GitHub API

    Raises:
        subprocess.CalledProcessError: If check run retrieval fails
    """
    logger.debug(f"Getting check runs for PR #{pr_number}")

    # Get PR head SHA
    result = subprocess.run(
        ["gh", "pr", "view", pr_number, "--repo", test_repo, "--json", "headRefOid", "--jq", ".headRefOid"],
        capture_output=True,
        text=True,
        check=True,
    )
    head_sha = result.stdout.strip()
    logger.debug(f"PR #{pr_number} head SHA: {head_sha}")

    # Get check runs for the commit
    result = subprocess.run(
        ["gh", "api", f"repos/{test_repo}/commits/{head_sha}/check-runs", "--jq", ".check_runs"],
        capture_output=True,
        text=True,
        check=True,
    )

    check_runs: list[dict[str, Any]] = json.loads(result.stdout)
    logger.debug(f"Found {len(check_runs)} check runs for PR #{pr_number}")
    return check_runs


def wait_for_check_runs(
    pr_number: str,
    test_repo: str,
    expected_checks: list[str],
    timeout: int = 180,
) -> None:
    """Wait for check runs to complete using TimeoutSampler.

    Args:
        pr_number: PR number to monitor
        test_repo: Test repository in format owner/repo-name
        expected_checks: List of expected check run names
        timeout: Timeout in seconds (default: 180)

    Raises:
        TimeoutExpiredError: If check runs don't complete within timeout
    """
    checks_to_wait = expected_checks
    logger.info(f"Waiting for check runs on PR #{pr_number}: {checks_to_wait}")

    def _check_completed() -> bool:
        """Check if all expected check runs are completed."""
        check_runs = get_check_runs(pr_number, test_repo)

        # Build map of check run name -> status
        check_status: dict[str, str] = {}
        for check_run in check_runs:
            name = check_run.get("name", "")
            status = check_run.get("status", "")
            check_status[name] = status

        # Check if all expected checks are completed
        for check_name in checks_to_wait:
            if check_name not in check_status:
                logger.debug(f"Check run '{check_name}' not found yet")
                return False
            if check_status[check_name] != "completed":
                logger.debug(f"Check run '{check_name}' status: {check_status[check_name]}")
                return False

        logger.info(f"All expected check runs completed for PR #{pr_number}")
        return True

    # Use TimeoutSampler to wait for completion
    for sample in TimeoutSampler(
        wait_timeout=timeout,
        sleep=5,
        func=_check_completed,
    ):
        if sample:
            break


def get_pr_labels(pr_number: str, test_repo: str) -> list[str]:
    """Get all labels for a pull request.

    Args:
        pr_number: PR number to get labels for
        test_repo: Test repository in format owner/repo-name

    Returns:
        list[str]: List of label names

    Raises:
        subprocess.CalledProcessError: If label retrieval fails
    """
    logger.debug(f"Getting labels for PR #{pr_number}")

    result = subprocess.run(
        ["gh", "pr", "view", pr_number, "--repo", test_repo, "--json", "labels", "--jq", ".labels[].name"],
        capture_output=True,
        text=True,
        check=True,
    )

    labels = result.stdout.strip().split("\n") if result.stdout.strip() else []
    logger.debug(f"Found {len(labels)} labels for PR #{pr_number}: {labels}")
    return labels


def wait_for_labels(
    pr_number: str,
    test_repo: str,
    expected_labels: list[str],
    timeout: int = 180,
) -> None:
    """Wait for expected labels to be added to PR using TimeoutSampler.

    Args:
        pr_number: PR number to monitor
        test_repo: Test repository in format owner/repo-name
        expected_labels: List of expected label names
        timeout: Timeout in seconds (default: 180)

    Raises:
        TimeoutExpiredError: If labels are not added within timeout
    """
    logger.info(f"Waiting for labels on PR #{pr_number}: {expected_labels}")

    def _check_labels_present() -> bool:
        """Check if all expected labels are present."""
        current_labels = get_pr_labels(pr_number, test_repo)

        # Check if all expected labels exist
        for label in expected_labels:
            if label not in current_labels:
                logger.debug(f"Label '{label}' not found yet")
                return False

        logger.info(f"All expected labels present on PR #{pr_number}")
        return True

    # Use TimeoutSampler to wait for labels
    for sample in TimeoutSampler(
        wait_timeout=timeout,
        sleep=5,
        func=_check_labels_present,
    ):
        if sample:
            break


def close_pr(pr_number: str, test_repo: str) -> None:
    """Close a pull request without merging.

    This is a cleanup function - logs errors but doesn't raise exceptions.

    Args:
        pr_number: PR number to close
        test_repo: Test repository in format owner/repo-name
    """
    logger.info(f"Closing PR #{pr_number}")

    try:
        subprocess.run(
            ["gh", "pr", "close", pr_number, "--repo", test_repo],
            capture_output=True,
            text=True,
            check=True,
        )
        logger.info(f"PR #{pr_number} closed successfully")
    except subprocess.CalledProcessError as ex:
        logger.exception(f"Failed to close PR #{pr_number}: {ex.stderr}")


def cleanup_pr(pr_number: str, branch: str, test_repo: str) -> None:
    """Cleanup helper: close PR and delete branch.

    This is a cleanup function - never raises exceptions.

    Args:
        pr_number: PR number to close
        branch: Branch to delete
        test_repo: Test repository in format owner/repo-name
    """
    logger.info(f"Cleaning up PR #{pr_number} and branch '{branch}'")
    close_pr(pr_number, test_repo)
    delete_branch(branch, test_repo)
    logger.info(f"Cleanup complete for PR #{pr_number}")


def get_repo_issues(test_repo: str) -> list[dict[str, Any]]:
    """Get all issues linked/referenced by a PR.

    Args:
        test_repo: Test repository in format owner/repo-name

    Returns:
        list[dict[str, Any]]: List of linked issue dictionaries

    Raises:
        subprocess.CalledProcessError: If API call fails
    """
    logger.debug(f"Getting {test_repo} issues")

    # Get all issues in the repository
    result = subprocess.run(
        ["gh", "api", f"repos/{test_repo}/issues", "--jq", ".[]"],
        capture_output=True,
        text=True,
        check=True,
    )

    if not result.stdout.strip():
        return []

    # Parse all issues
    all_issues = []
    for line in result.stdout.strip().split("\n"):
        if line.strip():
            try:
                issue = json.loads(line)
                all_issues.append(issue)
            except json.JSONDecodeError:
                continue

    logger.debug(f"Found {len(all_issues)} issues for {test_repo}")
    return all_issues
