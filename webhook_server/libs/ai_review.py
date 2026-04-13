from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx

from webhook_server.utils.constants import AI_REVIEW_STR

if TYPE_CHECKING:
    from github.PullRequest import PullRequest

    from webhook_server.libs.github_api import GithubWebhook
    from webhook_server.libs.handlers.check_run_handler import CheckRunHandler

DEFAULT_TRIGGERS: list[str] = ["pr-opened", "pr-synchronized"]


async def call_ai_reviewer(
    github_webhook: GithubWebhook,
    pull_request: PullRequest,
    check_run_handler: CheckRunHandler,
    trigger: str | None = None,
) -> None:
    """Call the pr-ai-reviewer service to review a PR.

    Args:
        github_webhook: The GithubWebhook instance with config and token.
        pull_request: The PyGithub PullRequest object.
        check_run_handler: CheckRunHandler for updating check run status.
        trigger: The event trigger (e.g., "pr-opened", "pr-synchronized").
                 None means command-triggered (always runs if configured).
                 Command trigger is reserved for a future /ai-review comment command.
    """
    config = github_webhook.ai_review_config
    if not config:
        return

    # trigger=None means command-triggered (e.g., future /ai-review command).
    # Currently only webhook-triggered via pr-opened/pr-synchronized.

    if trigger is not None:
        triggers: list[str] = config.get("triggers", DEFAULT_TRIGGERS)
        if trigger not in triggers:
            github_webhook.logger.debug(
                f"{github_webhook.log_prefix} AI reviewer trigger '{trigger}' not in configured triggers {triggers}"
            )
            return

    server_url: str = config["server-url"]
    log_prefix: str = github_webhook.log_prefix

    check_in_progress = False
    try:
        await check_run_handler.set_check_in_progress(name=AI_REVIEW_STR)
        check_in_progress = True

        async with httpx.AsyncClient(base_url=server_url) as client:
            # Health check
            try:
                health_response = await client.get("/health", timeout=5.0)
                health_response.raise_for_status()
            except httpx.HTTPError as e:
                status_info = ""
                if isinstance(e, httpx.HTTPStatusError):
                    status_info = f" (status {e.response.status_code})"

                msg = f"AI Reviewer server at {server_url} is not responding{status_info}"
                github_webhook.logger.warning(f"{log_prefix} {msg}")
                await check_run_handler.set_check_failure(
                    name=AI_REVIEW_STR,
                    output={"title": "AI Review Failed", "summary": msg},
                )
                return

            # Build review payload
            pr_url: str = pull_request.html_url

            # Convert provider configs from YAML format to API format
            providers_config: list[dict[str, str]] = config.get("providers", [])
            providers_payload: list[dict[str, str]] = [
                {"ai_provider": p["ai-provider"], "ai_model": p["ai-model"]} for p in providers_config
            ]

            payload: dict[str, Any] = {
                "pr_url": pr_url,
                "providers": providers_payload,
                "github_token": github_webhook.token,
            }

            if "max-rounds" in config:
                payload["max_rounds"] = config["max-rounds"]

            if "timeout-minutes" in config:
                payload["timeout_minutes"] = config["timeout-minutes"]

            # Call review endpoint
            try:
                github_webhook.logger.info(f"{log_prefix} Calling AI Reviewer for {pr_url}")
                # Long timeout: AI review with peer consensus can take up to timeout_minutes * max_rounds
                timeout_minutes = config.get("timeout-minutes", 30)
                max_rounds = config.get("max-rounds", 3)
                # Worst-case: each round takes timeout_minutes (sequential AI calls) + 60s buffer
                request_timeout = float(timeout_minutes * max_rounds * 60 + 60)

                response = await client.post("/review", json=payload, timeout=request_timeout)
                response.raise_for_status()

                result = response.json()
                review_posted = result.get("review_posted", False)
                comments_count = len(result.get("comments", []))
                summary = result.get("summary", "no summary")

                github_webhook.logger.info(
                    f"{log_prefix} AI Reviewer complete: {comments_count} comment(s), "
                    f"review_posted={review_posted}, summary={summary}"
                )

                await check_run_handler.set_check_success(
                    name=AI_REVIEW_STR,
                    output={
                        "title": "AI Review Complete",
                        "summary": f"{comments_count} comment(s) posted" if comments_count else "No issues found",
                    },
                )
            except httpx.HTTPError as e:
                err_detail = ""
                if isinstance(e, httpx.HTTPStatusError):
                    err_detail = f": {e.response.text[:2000]}"
                error_msg = f"AI Reviewer request failed{err_detail}"
                github_webhook.logger.error(f"{log_prefix} {error_msg}")
                await check_run_handler.set_check_failure(
                    name=AI_REVIEW_STR,
                    output={"title": "AI Review Failed", "summary": error_msg},
                )
            except ValueError:
                github_webhook.logger.error(f"{log_prefix} AI Reviewer returned invalid JSON response")
                await check_run_handler.set_check_failure(
                    name=AI_REVIEW_STR,
                    output={"title": "AI Review Failed", "summary": "Invalid JSON response from AI Reviewer"},
                )
    except asyncio.CancelledError:
        if check_in_progress:
            try:
                await check_run_handler.set_check_failure(
                    name=AI_REVIEW_STR,
                    output={"title": "AI Review Cancelled", "summary": "Review was cancelled"},
                )
            except Exception:
                github_webhook.logger.exception(f"{log_prefix} Failed to set check run failure on cancellation")
        raise
    except Exception:
        github_webhook.logger.exception(f"{log_prefix} AI Reviewer call failed unexpectedly")
        try:
            await check_run_handler.set_check_failure(
                name=AI_REVIEW_STR,
                output={"title": "AI Review Failed", "summary": "Unexpected error"},
            )
        except Exception:
            github_webhook.logger.exception(f"{log_prefix} Failed to set check run failure")
