from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderConfig:
    """Configuration for an AI CLI provider."""

    binary: str
    build_cmd: Callable[[str, str, str], list[str]]


def _build_claude_cmd(binary: str, model: str, _cwd: str) -> list[str]:
    return [binary, "--model", model, "--dangerously-skip-permissions", "-p"]


def _build_gemini_cmd(binary: str, model: str, _cwd: str) -> list[str]:
    return [binary, "--model", model, "--yolo"]


def _build_cursor_cmd(binary: str, model: str, cwd: str) -> list[str]:
    return [binary, "--force", "--model", model, "--print", "--workspace", cwd]


PROVIDER_CONFIG: dict[str, ProviderConfig] = {
    "claude": ProviderConfig(binary="claude", build_cmd=_build_claude_cmd),
    "gemini": ProviderConfig(binary="gemini", build_cmd=_build_gemini_cmd),
    "cursor": ProviderConfig(binary="agent", build_cmd=_build_cursor_cmd),
}


async def call_ai_cli(
    prompt: str,
    ai_provider: str,
    ai_model: str,
    cwd: str,
    logger: Any,
    timeout_minutes: int = 10,
) -> tuple[bool, str]:
    """Call an AI CLI tool with a prompt via stdin.

    Args:
        prompt: The prompt text to send.
        ai_provider: Provider name (claude, gemini, cursor).
        ai_model: Model identifier.
        cwd: Working directory for the CLI (repo clone path). Required for AI
             to have access to repository context (diff, files, etc.).
        logger: Contextual logger instance for structured logging.
        timeout_minutes: Timeout in minutes.

    Returns:
        Tuple of (success, output_or_error).
    """
    provider = PROVIDER_CONFIG.get(ai_provider)
    if not provider:
        return False, f"Unknown AI provider: {ai_provider}"

    cmd = provider.build_cmd(provider.binary, ai_model, cwd)
    timeout_seconds = timeout_minutes * 60

    # For cursor, cwd is passed via --workspace flag, not subprocess cwd
    subprocess_cwd = cwd if ai_provider != "cursor" else None

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            input=prompt,
            cwd=subprocess_cwd,
        )

        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            logger.error(f"AI CLI ({ai_provider}) failed: {error}")
            return False, error

        output = result.stdout.strip()
        if not output:
            return False, "AI CLI returned empty response"

        return True, output

    except subprocess.TimeoutExpired:
        logger.exception(f"AI CLI ({ai_provider}) timed out after {timeout_minutes} minutes")
        return False, f"AI CLI timed out after {timeout_minutes} minutes"
    except FileNotFoundError:
        logger.exception(f"AI CLI binary '{provider.binary}' not found")
        return False, f"AI CLI binary '{provider.binary}' not found"
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(f"AI CLI ({ai_provider}) unexpected error")
        return False, "Unexpected error calling AI CLI"


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
