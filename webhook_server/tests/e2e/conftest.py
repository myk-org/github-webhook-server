"""Pytest configuration for E2E tests.

This module provides session-scoped fixtures for end-to-end testing of the GitHub webhook server.
The fixture manages the testing infrastructure including:
- Environment configuration from .dev/.env
- Smee webhook proxy lifecycle
- Docker Compose container lifecycle
- Server health monitoring
- Proper cleanup on test completion/failure
"""

import os
import subprocess
from collections.abc import Generator
from pathlib import Path

import pytest
import shortuuid
from dotenv import load_dotenv
from simple_logger.logger import get_logger

from webhook_server.tests.e2e.helpers import cleanup_pr, create_pr, delete_branch
from webhook_server.tests.e2e.server_utils import (
    E2EInfrastructureError,
    start_docker_compose,
    start_smee_client,
    stop_docker_compose,
    stop_smee_client,
    wait_for_container_health,
)

# Configure logging for E2E tests
logger = get_logger(name="e2e-tests")


@pytest.fixture(scope="session")
def server_envs() -> dict[str, str]:
    """Load and validate environment variables for E2E tests.

    This fixture:
    1. Checks if .dev/.env file exists (fails if missing)
    2. Loads environment variables from .dev/.env
    3. Validates required variables (SERVER_PORT, SMEE_URL, TEST_REPO, DOCKER_COMPOSE_FILE)
    4. Returns validated environment variables

    Returns:
        dict with keys:
            - server_port: Local server port (e.g., "19876")
            - smee_url: Smee.io webhook proxy URL
            - test_repo: Test repository name (owner/repo-name)
            - project_root: Project root directory path
            - docker_compose_file: Path to docker-compose.yaml

    Raises:
        E2EInfrastructureError: If .dev/.env file does not exist or required variables are missing
    """
    project_root = Path(__file__).parent.parent.parent.parent
    env_file = project_root / ".dev" / ".env"

    if not env_file.exists():
        raise E2EInfrastructureError(
            f"Required .dev/.env file not found at {env_file}\n"
            "Create .dev/.env with:\n"
            "  SERVER_PORT=19876\n"
            "  SMEE_URL=https://smee.io/YOUR_CHANNEL\n"
            "  TEST_REPO=owner/repo-name\n"
            "  DOCKER_COMPOSE_FILE=.dev/docker-compose.yaml"
        )

    load_dotenv(dotenv_path=env_file)
    logger.info(f"Loaded environment variables from {env_file}")

    server_port = os.environ.get("SERVER_PORT")
    smee_url = os.environ.get("SMEE_URL")
    test_repo = os.environ.get("TEST_REPO")
    docker_compose_file = os.environ.get("DOCKER_COMPOSE_FILE")

    if not server_port:
        raise E2EInfrastructureError(
            "SERVER_PORT environment variable is required. Add to .dev/.env: SERVER_PORT=19876"
        )

    if not smee_url:
        raise E2EInfrastructureError(
            "SMEE_URL environment variable is required. Add to .dev/.env: SMEE_URL=https://smee.io/YOUR_CHANNEL"
        )

    if not test_repo:
        raise E2EInfrastructureError(
            "TEST_REPO environment variable is required. Add to .dev/.env: TEST_REPO=owner/repo-name"
        )

    if not docker_compose_file:
        raise E2EInfrastructureError(
            "DOCKER_COMPOSE_FILE environment variable is required. "
            "Add to .dev/.env: DOCKER_COMPOSE_FILE=.dev/docker-compose.yaml"
        )

    # Resolve docker-compose file path relative to project root if not absolute
    compose_path = Path(docker_compose_file)
    if not compose_path.is_absolute():
        compose_path = project_root / docker_compose_file

    if not compose_path.exists():
        raise E2EInfrastructureError(
            f"Docker compose file not found at {compose_path}. Check DOCKER_COMPOSE_FILE in .dev/.env"
        )

    return {
        "server_port": server_port,
        "smee_url": smee_url,
        "test_repo": test_repo,
        "project_root": str(project_root),
        "docker_compose_file": str(compose_path),
    }


@pytest.fixture(scope="session")
def github_webhook_cleanup(server_envs: dict[str, str]) -> Generator[None, None, None]:
    """Manages GitHub webhook lifecycle (session-scoped).

    This fixture ensures the GitHub webhook is properly cleaned up after all tests complete.

    Args:
        server_envs: Validated environment variables

    Yields:
        None

    Cleanup:
        Removes webhook from GitHub repository after all tests complete
    """
    # Setup: Nothing needed - webhook is configured in repository settings manually
    yield

    # Cleanup: Remove webhook after all tests
    test_repo = server_envs["test_repo"]
    smee_url = server_envs["smee_url"]

    logger.info(f"Cleaning up GitHub webhook for {test_repo}")

    # Get webhook ID for the smee URL
    result = subprocess.run(
        ["gh", "api", f"repos/{test_repo}/hooks", "--jq", f'.[] | select(.config.url == "{smee_url}") | .id'],
        capture_output=True,
        text=True,
        check=True,
    )

    webhook_id = result.stdout.strip()
    if webhook_id:
        logger.info(f"Removing webhook {webhook_id} ({smee_url})")
        subprocess.run(
            ["gh", "api", "-X", "DELETE", f"repos/{test_repo}/hooks/{webhook_id}"],
            capture_output=True,
            text=True,
            check=True,
        )
        logger.info("GitHub webhook removed successfully")
    else:
        logger.info(f"No webhook found with URL {smee_url} - nothing to clean up")


@pytest.fixture(scope="session")
def e2e_server(server_envs: dict[str, str], github_webhook_cleanup: None) -> Generator[None, None, None]:
    """Session-scoped fixture that manages E2E testing infrastructure.

    This fixture manages the complete E2E testing infrastructure:
    1. Starts smee client to proxy webhooks
    2. Starts docker-compose container for the webhook server
    3. Waits for server to be healthy
    4. Yields control to tests
    5. Performs cleanup (stops docker-compose + smee)

    Args:
        server_envs: Fixture that provides validated environment variables

    Yields:
        None - tests interact with GitHub directly, not the server

    Raises:
        E2EInfrastructureError: If setup or teardown fails
    """
    # Get environment variables from fixture
    server_port = server_envs["server_port"]
    smee_url = server_envs["smee_url"]
    project_root = server_envs["project_root"]
    docker_compose_file = server_envs["docker_compose_file"]

    logger.info(f"Starting E2E infrastructure on port {server_port}")

    # Step 1: Start smee client (BEFORE docker-compose)
    smee_process = start_smee_client(server_port=server_port, smee_url=smee_url)

    # Step 2: Start docker-compose container
    start_docker_compose(docker_compose_file=docker_compose_file, project_root=project_root)

    # Step 3: Wait for docker container health check
    wait_for_container_health(
        docker_compose_file=docker_compose_file,
        project_root=project_root,
        container_name="github-webhook-server-e2e",
        timeout=60,
    )

    # Step 4: Yield control to tests
    logger.info("E2E infrastructure ready for testing")
    yield

    # Step 5: Cleanup (pytest handles this automatically after yield)
    logger.info("Cleaning up E2E infrastructure...")

    # Stop smee client
    stop_smee_client(smee_process)

    # Stop docker-compose
    stop_docker_compose(docker_compose_file=docker_compose_file, project_root=project_root)

    logger.info("E2E infrastructure cleanup complete")


@pytest.fixture(scope="session")
def test_repository_name(server_envs: dict[str, str]) -> str:
    """Provides the test repository name from environment.

    Args:
        server_envs: Validated environment variables

    Returns:
        str: Test repository in format owner/repo-name
    """
    return server_envs["test_repo"]


@pytest.fixture(scope="session")
def cloned_test_repo(
    tmp_path_factory: pytest.TempPathFactory, test_repository_name: str
) -> Generator[Path, None, None]:
    """Clone test repository to temporary directory (session-scoped).

    Args:
        tmp_path_factory: Pytest temporary path factory for session-scoped temp dirs
        test_repository_name: Test repository name from environment

    Yields:
        Path: Path to cloned repository

    This fixture clones the test repository once per session and provides the path.
    """
    repo_dir = tmp_path_factory.mktemp("e2e-repos") / "test-repo"
    logger.info(f"Cloning {test_repository_name} to {repo_dir}")

    # Use SSH URL to avoid authentication prompts
    ssh_url = f"git@github.com:{test_repository_name}.git"
    subprocess.run(
        ["git", "clone", ssh_url, str(repo_dir)],
        capture_output=True,
        text=True,
        check=True,
    )

    logger.info(f"Repository cloned to {repo_dir}")
    yield repo_dir


@pytest.fixture
def git_repo_reset(cloned_test_repo: Path) -> None:
    """Provides a clean, reset git repository for each test.

    Args:
        cloned_test_repo: Path to cloned repository

    This fixture ensures the repository is in a clean state:
    1. Resets any local changes
    2. Checks out main branch
    3. Pulls latest changes from remote

    Each test receives a clean, up-to-date repository.
    """
    logger.info("Resetting test repository to clean state")

    # Reset any local changes
    subprocess.run(
        ["git", "reset", "--hard", "HEAD"],
        cwd=cloned_test_repo,
        capture_output=True,
        text=True,
        check=True,
    )

    # Clean untracked files
    subprocess.run(
        ["git", "clean", "-fd"],
        cwd=cloned_test_repo,
        capture_output=True,
        text=True,
        check=True,
    )

    # Checkout main
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=cloned_test_repo,
        capture_output=True,
        text=True,
        check=True,
    )

    # Pull latest changes
    subprocess.run(
        ["git", "pull", "origin", "main"],
        cwd=cloned_test_repo,
        capture_output=True,
        text=True,
        check=True,
    )

    logger.info("Repository reset complete")


@pytest.fixture(scope="class")
def branch_for_tests(cloned_test_repo: Path, test_repository_name: str) -> Generator[str, None, None]:
    """Provides a test branch for the current test.

    Args:
        cloned_test_repo: Path to cloned repository
        test_repository_name: Test repository name from environment

    Yields:
        str: Branch name

    This fixture creates a branch and cleans it up after the test.
    """
    # Reset repository to clean state before creating branch
    logger.info("Resetting test repository to clean state before creating branch")
    subprocess.run(
        ["git", "reset", "--hard", "HEAD"],
        cwd=cloned_test_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        ["git", "clean", "-fd"],
        cwd=cloned_test_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=cloned_test_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        ["git", "pull", "origin", "main"],
        cwd=cloned_test_repo,
        capture_output=True,
        text=True,
        check=True,
    )

    branch_name = f"test-pr-{shortuuid.uuid()}"
    logger.info(f"Setting up test branch: {branch_name}")

    # Create and checkout new branch from main
    subprocess.run(
        ["git", "checkout", "-b", branch_name, "main"],
        cwd=cloned_test_repo,
        capture_output=True,
        text=True,
        check=True,
    )

    yield branch_name

    logger.info(f"Cleaning up test branch: {branch_name}")
    delete_branch(branch_name, test_repository_name)


@pytest.fixture(scope="class")
def pr_for_tests(
    cloned_test_repo: Path, branch_for_tests: str, test_repository_name: str
) -> Generator[str, None, None]:
    """Provides a test PR for the current test.

    Args:
        cloned_test_repo: Path to cloned repository
        branch_for_tests: Branch name from branch_for_tests fixture
        test_repository_name: Test repository name from environment

    Yields:
        str: PR number

    This fixture creates a PR and cleans it up after the test.
    """
    # Add commit to branch using local git
    logger.info(f"Adding commit to branch: {branch_for_tests}")
    test_file = cloned_test_repo / "README.md"
    test_file.write_text("# Test PR\n\nThis is an automated E2E test PR.\n")

    subprocess.run(
        ["git", "add", "README.md"],
        cwd=cloned_test_repo,
        capture_output=True,
        text=True,
        check=True,
    )

    subprocess.run(
        ["git", "commit", "-m", "test: automated E2E test commit"],
        cwd=cloned_test_repo,
        capture_output=True,
        text=True,
        check=True,
    )

    # Push branch to remote
    logger.info(f"Pushing branch '{branch_for_tests}' to remote")
    subprocess.run(
        ["git", "push", "-u", "origin", branch_for_tests],
        cwd=cloned_test_repo,
        capture_output=True,
        text=True,
        check=True,
    )

    # Create PR with conventional-commit title
    logger.info(f"Creating PR from branch: {branch_for_tests}")
    pr_number = create_pr(
        title="test: automated E2E test PR",
        branch=branch_for_tests,
        body="This PR was created by an automated E2E test.",
        test_repo=test_repository_name,
    )
    logger.info(f"Created PR #{pr_number}")

    yield pr_number

    logger.info(f"Cleaning up PR #{pr_number}")
    cleanup_pr(pr_number, branch_for_tests, test_repository_name)
