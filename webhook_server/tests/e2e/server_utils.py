"""Utility functions for E2E testing infrastructure.

This module provides utility functions for managing the E2E testing infrastructure:
- Smee client lifecycle management
- Docker Compose container lifecycle management
- Container health monitoring
"""

import json
import subprocess

from simple_logger.logger import get_logger
from timeout_sampler import TimeoutSampler

# Configure logging for E2E server utilities
logger = get_logger(name="e2e-server-utils")


class E2EInfrastructureError(Exception):
    """Raised when E2E infrastructure setup or teardown fails."""


def start_smee_client(server_port: str, smee_url: str) -> subprocess.Popen:
    """Start smee client to proxy webhooks from smee.io to local server.

    Args:
        server_port: Local server port to forward webhooks to (e.g., "5000")
        smee_url: Smee.io webhook proxy URL to listen to

    Returns:
        subprocess.Popen: The running smee client process

    Raises:
        E2EInfrastructureError: If smee client is not found or fails to start
    """
    logger.info(f"Starting smee client: {smee_url} -> localhost:{server_port}")
    try:
        smee_process = subprocess.Popen(
            ["smee", "-u", smee_url, "-p", server_port, "-P", "/webhook_server"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        logger.info(f"Smee client started (PID: {smee_process.pid})")
        return smee_process
    except FileNotFoundError:
        raise E2EInfrastructureError("smee client not found. Install with: npm install -g smee-client") from None


def stop_smee_client(smee_process: subprocess.Popen | None) -> None:
    """Stop smee client gracefully with timeout and fallback to kill.

    Args:
        smee_process: The running smee client process, or None if not started
    """
    if not smee_process:
        return

    logger.info(f"Stopping smee client (PID: {smee_process.pid})...")
    try:
        smee_process.terminate()
        try:
            smee_process.wait(timeout=5)
            logger.info("Smee client stopped successfully")
        except subprocess.TimeoutExpired:
            logger.warning("Smee client did not terminate, killing process...")
            smee_process.kill()
            smee_process.wait()
            logger.info("Smee client killed")
    except Exception:
        logger.exception("Error stopping smee client")


def start_docker_compose(docker_compose_file: str, project_root: str) -> None:
    """Start docker-compose container for the webhook server.

    Args:
        docker_compose_file: Path to docker-compose.yaml file
        project_root: Project root directory path

    Raises:
        E2EInfrastructureError: If docker-compose fails to start
    """
    logger.info("Starting docker-compose container...")
    result = subprocess.run(
        ["docker", "compose", "--file", docker_compose_file, "up", "-d"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise E2EInfrastructureError(
            f"Failed to start docker-compose:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )

    logger.info("Docker-compose container started successfully")


def stop_docker_compose(docker_compose_file: str, project_root: str) -> None:
    """Stop docker-compose container gracefully.

    This function logs errors but does not raise exceptions to ensure cleanup completes.

    Args:
        docker_compose_file: Path to docker-compose.yaml file
        project_root: Project root directory path
    """
    logger.info("Stopping docker-compose container...")
    try:
        result = subprocess.run(
            ["docker", "compose", "--file", docker_compose_file, "down"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

        if result.returncode != 0:
            logger.error(f"Failed to stop docker-compose:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
        else:
            logger.info("Docker-compose container stopped successfully")
    except subprocess.TimeoutExpired:
        logger.error("Docker-compose down command timed out")
    except Exception:
        logger.exception("Error stopping docker-compose")


def check_container_health(
    docker_compose_file: str,
    project_root: str,
    container_name: str = "github-webhook-server-e2e",
) -> bool:
    """Check if webhook server container is healthy.

    Args:
        docker_compose_file: Path to docker-compose.yaml file
        project_root: Project root directory path
        container_name: Name of the container to check (default: "github-webhook-server-e2e")

    Returns:
        bool: True if container is healthy, False otherwise

    Raises:
        E2EInfrastructureError: If docker command fails
    """
    result = subprocess.run(
        ["docker", "compose", "--file", docker_compose_file, "ps", "--format", "json"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise E2EInfrastructureError(
            f"Failed to check container status:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )

    containers = json.loads(result.stdout) if result.stdout.strip() else []
    if not isinstance(containers, list):
        containers = [containers]

    # Find the webhook server container specifically
    for container in containers:
        # Check by service name or container name
        if container.get("Service") == container_name or container.get("Name") == container_name:
            health = container.get("Health", "")
            logger.debug(f"Webhook server container health: {health or 'unknown'}")
            return health == "healthy"

    # Container not found yet
    logger.debug("Webhook server container not found yet")
    return False


def wait_for_container_health(
    docker_compose_file: str,
    project_root: str,
    container_name: str = "github-webhook-server-e2e",
    timeout: int = 60,
) -> None:
    """Wait for webhook server container to be healthy.

    Args:
        docker_compose_file: Path to docker-compose.yaml file
        project_root: Project root directory path
        container_name: Name of the container to check (default: "github-webhook-server-e2e")
        timeout: Maximum time to wait in seconds (default: 60)

    Raises:
        E2EInfrastructureError: If container does not become healthy within timeout
    """
    logger.info("Waiting for container to be healthy (via docker healthcheck)...")

    def _check_health() -> bool:
        """Wrapper for check_container_health to use with TimeoutSampler."""
        return check_container_health(docker_compose_file, project_root, container_name)

    try:
        for sample in TimeoutSampler(
            wait_timeout=timeout,
            sleep=2,
            func=_check_health,
            exceptions_dict={json.JSONDecodeError: []},
        ):
            if sample:
                logger.info("Webhook server container is healthy")
                break
    except Exception as ex:
        raise E2EInfrastructureError(
            f"Webhook server container health check failed: {ex}. Check docker-compose logs for details."
        ) from ex
