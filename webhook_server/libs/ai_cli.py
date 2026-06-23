from __future__ import annotations

from typing import Any

from pi_sidecar_client import AIResult, call_ai_once, check_sidecar_available

__all__ = ["AIResult", "call_ai", "get_ai_config"]


async def call_ai(
    prompt: str,
    ai_provider: str,
    ai_model: str,
    cwd: str,
    timeout_minutes: int | None = None,
    system_prompt: str = "",
    tools: list[str] | None = None,
    custom_tools: list[dict[str, Any]] | None = None,
) -> AIResult:
    """Call an AI provider via pi-sidecar. Thin wrapper around pi_sidecar_client.call_ai_once.

    Returns:
        AIResult with .success, .text, and .error attributes.
    """
    available, msg = await check_sidecar_available()
    if not available:
        return AIResult(success=False, text="", error=f"Pi-sidecar unavailable: {msg}")

    return await call_ai_once(
        prompt=prompt,
        ai_provider=ai_provider,
        ai_model=ai_model,
        cwd=cwd,
        ai_call_timeout=timeout_minutes,
        system_prompt=system_prompt,
        tools=tools,
        custom_tools=custom_tools,
    )


def get_ai_config(config_value: dict[str, Any] | None) -> tuple[str, str] | None:
    """Extract AI provider and model from ai-features config.

    Returns:
        Tuple of (ai_provider, ai_model) or None if not configured or incomplete.
    """
    if not config_value:
        return None

    ai_provider = config_value.get("ai-provider")
    ai_model = config_value.get("ai-model")

    if not ai_provider or not ai_model:
        return None

    return ai_provider, ai_model
