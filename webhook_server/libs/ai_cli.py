from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_cli_runner import call_ai_cli as _call_ai_cli

__all__ = ["call_ai_cli", "get_ai_config"]


async def call_ai_cli(
    prompt: str,
    ai_provider: str,
    ai_model: str,
    cwd: str,
    cli_flags: list[str] | None = None,
    timeout_minutes: int | None = None,
) -> tuple[bool, str]:
    """Call an AI CLI tool. Thin wrapper around ai_cli_runner.call_ai_cli.

    Accepts cwd as str (matching clone_repo_dir type) and converts to Path.
    """
    return await _call_ai_cli(
        prompt=prompt,
        ai_provider=ai_provider,
        ai_model=ai_model,
        cwd=Path(cwd),
        cli_flags=cli_flags,
        ai_cli_timeout=timeout_minutes,
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
