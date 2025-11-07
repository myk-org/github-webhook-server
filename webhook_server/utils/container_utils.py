"""Container build utilities."""

from __future__ import annotations

from logging import Logger

from github.PullRequest import PullRequest

from webhook_server.utils.constants import OTHER_MAIN_BRANCH


def get_container_repository_and_tag(
    container_repository: str,
    container_tag: str,
    is_merged: bool = False,
    tag: str = "",
    pull_request: PullRequest | None = None,
    logger: Logger | None = None,
    log_prefix: str = "",
) -> str | None:
    """
    Get container repository and tag for build.

    Args:
        container_repository: Base container repository URL
        container_tag: Default tag to use
        is_merged: Whether PR is merged
        tag: Optional explicit tag override
        pull_request: Pull request object (PyGithub PullRequest, needed if tag not provided)
        logger: Logger instance for debug output
        log_prefix: Prefix for log messages

    Returns:
        Full container repository:tag string, or None if tag cannot be determined
    """
    if not tag:
        if not pull_request:
            if logger:
                logger.error(f"{log_prefix} No pull request provided and no tag specified")
            return None

        if is_merged:
            pull_request_branch = pull_request.base.ref
            tag = pull_request_branch if pull_request_branch not in (OTHER_MAIN_BRANCH, "main") else container_tag
        else:
            tag = f"pr-{pull_request.number}"

    if tag:
        if logger:
            logger.debug(f"{log_prefix} container tag is: {tag}")
        return f"{container_repository}:{tag}"

    if logger:
        logger.error(f"{log_prefix} container tag not found")
    return None
