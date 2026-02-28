from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from github.PullRequest import PullRequest

    from webhook_server.libs.github_api import GithubWebhook

# "approved" refers to the /approve command trigger, not GitHub's review approval state.
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
        trigger: The event trigger (e.g., "approved", "pr-opened").
                 "approved" means the /approve command, not GitHub review state.
                 None means command-triggered (always runs if configured).
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

    try:
        async with httpx.AsyncClient(base_url=server_url) as client:
            # Health check
            try:
                health_response = await client.get("/health", timeout=5.0)
                health_response.raise_for_status()
            except httpx.HTTPError as e:
                status_info = ""
                if isinstance(e, httpx.HTTPStatusError):
                    status_info = f" (status {e.response.status_code})"

                msg = f"Test Oracle server at {server_url} is not responding{status_info}, skipping test analysis"
                github_webhook.logger.warning(f"{log_prefix} {msg}")
                try:
                    await asyncio.to_thread(pull_request.create_issue_comment, msg)
                except Exception:
                    github_webhook.logger.exception(f"{log_prefix} Failed to post health check comment")
                return

            # Build analyze payload
            pr_url: str = await asyncio.to_thread(lambda: pull_request.html_url)
            payload: dict[str, Any] = {
                "pr_url": pr_url,
                "ai_provider": config["ai-provider"],
                "ai_model": config["ai-model"],
                # Token is required by the oracle server to fetch PR data and post reviews.
                # Server URL is configured by the admin - they control the network setup.
                "github_token": github_webhook.token,
            }

            if "test-patterns" in config:
                payload["test_patterns"] = config["test-patterns"]

            # Call analyze
            try:
                github_webhook.logger.info(f"{log_prefix} Calling Test Oracle for {pr_url}")
                response = await client.post("/analyze", json=payload, timeout=300.0)
                response.raise_for_status()

                result = response.json()
                github_webhook.logger.info(
                    f"{log_prefix} Test Oracle analysis complete: {result.get('summary', 'no summary')}"
                )
            except httpx.HTTPError as e:
                err_detail = f": {e.response.text}" if isinstance(e, httpx.HTTPStatusError) else ""
                github_webhook.logger.error(f"{log_prefix} Test Oracle analyze request failed{err_detail}")
            except ValueError:
                github_webhook.logger.error(f"{log_prefix} Test Oracle returned invalid JSON response")
    except asyncio.CancelledError:
        raise
    except Exception:
        github_webhook.logger.exception(f"{log_prefix} Test Oracle call failed unexpectedly")
