from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from github.PullRequest import PullRequest

    from webhook_server.libs.github_api import GithubWebhook

DEFAULT_TRIGGERS: list[str] = ["approved"]


async def call_test_oracle(
    github_webhook: GithubWebhook,
    pull_request: PullRequest,
    trigger: str | None = None,
) -> None:
    """Call the pr-test-oracle service to analyze a PR for test recommendations.

    Args:
        github_webhook: The GithubWebhook instance with config and token.
        pull_request: The PyGithub PullRequest object.
        trigger: The event trigger (e.g., "approved", "pr-opened"). None means
                 command-triggered (always runs if configured).
    """
    config: dict[str, Any] | None = github_webhook.config.get_value("test-oracle")
    if not config:
        return

    if trigger is not None:
        triggers: list[str] = config.get("triggers", DEFAULT_TRIGGERS)
        if trigger not in triggers:
            github_webhook.logger.debug(
                f"{github_webhook.log_prefix} Test oracle trigger '{trigger}' not in configured triggers {triggers}"
            )
            return

    server_url: str = config["server-url"]
    log_prefix: str = github_webhook.log_prefix

    async with httpx.AsyncClient(base_url=server_url) as client:
        # Health check
        try:
            health_response = await client.get("/health", timeout=5.0)
            if health_response.status_code != 200:
                msg = (
                    f"Test Oracle server at {server_url} is not responding"
                    f" (status {health_response.status_code}), skipping test analysis"
                )
                github_webhook.logger.warning(f"{log_prefix} {msg}")
                await asyncio.to_thread(pull_request.create_issue_comment, msg)
                return
        except httpx.HTTPError:
            msg = f"Test Oracle server at {server_url} is not responding, skipping test analysis"
            github_webhook.logger.warning(f"{log_prefix} {msg}")
            await asyncio.to_thread(pull_request.create_issue_comment, msg)
            return

        # Build analyze payload
        payload: dict[str, Any] = {
            "pr_url": pull_request.html_url,
            "ai_provider": config["ai-provider"],
            "ai_model": config["ai-model"],
            "github_token": github_webhook.token,
        }

        if "test-patterns" in config:
            payload["test_patterns"] = config["test-patterns"]

        # Call analyze
        try:
            github_webhook.logger.info(f"{log_prefix} Calling Test Oracle for {pull_request.html_url}")
            response = await client.post("/analyze", json=payload, timeout=300.0)

            if response.status_code != 200:
                github_webhook.logger.error(
                    f"{log_prefix} Test Oracle analyze failed with status {response.status_code}: {response.text}"
                )
                return

            result = response.json()
            github_webhook.logger.info(
                f"{log_prefix} Test Oracle analysis complete: {result.get('summary', 'no summary')}"
            )
        except httpx.HTTPError:
            github_webhook.logger.error(f"{log_prefix} Test Oracle analyze request failed")
