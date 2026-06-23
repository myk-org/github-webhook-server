"""Standalone async tool server for AI custom tools.

Runs on a separate port with its own event loop thread.
Tools are defined in a registry — adding a new tool is one entry.
Callers control which tools the AI sees per session.

SECURITY: Binds to 127.0.0.1 only. Each tool defines its own
allowed arguments and blocked flags.
"""

from __future__ import annotations

import asyncio
import dataclasses
import shlex
import threading
from typing import Any

from aiohttp import web

TOOL_SERVER_PORT = 5001


@dataclasses.dataclass(frozen=True)
class ToolDef:
    """Definition of a registered tool."""

    command_prefix: list[str]  # Base command, e.g. ["git", "-C", "{cwd}"]
    description: str
    args_description: str
    timeout: int = 30  # Default timeout in seconds
    allowed_subcommands: frozenset[str] | None = None  # If set, first arg must be in this set
    blocked_flags: frozenset[str] = dataclasses.field(default_factory=frozenset)
    success_exit_codes: frozenset[int] = dataclasses.field(
        default_factory=lambda: frozenset({0})
    )  # Exit codes treated as success


# Tool registry — add new tools here, nothing else changes
TOOL_REGISTRY: dict[str, ToolDef] = {
    "git_diff": ToolDef(
        command_prefix=["git", "-C", "{cwd}", "diff"],
        description="Run git diff to see code changes. Use '--stat' for summary, or file paths for specific files.",
        args_description="Arguments for git diff (e.g., 'origin/main --stat', 'origin/main -- file.py')",
        blocked_flags=frozenset({"--no-index", "--output", "--raw"}),
        success_exit_codes=frozenset({0, 1}),  # exit 1 = differences found (not an error)
    ),
    "git_log": ToolDef(
        command_prefix=["git", "-C", "{cwd}", "log"],
        description="Run git log to see commit history. Use '--oneline' for compact output.",
        args_description="Arguments for git log (e.g., 'origin/main..HEAD --oneline', '-5')",
    ),
    "git_show": ToolDef(
        command_prefix=["git", "-C", "{cwd}", "show"],
        description="Run git show to inspect a commit or object.",
        args_description="Arguments for git show (e.g., 'HEAD', 'abc123:file.py')",
    ),
    "git_status": ToolDef(
        command_prefix=["git", "-C", "{cwd}", "status"],
        description="Run git status to see working tree state.",
        args_description="Arguments for git status (optional)",
    ),
}


async def handle_tool_request(request: web.Request) -> web.Response:
    """Execute a registered tool."""
    try:
        data: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"detail": "Invalid JSON"}, status=400)

    tool_name = data.get("tool", "")
    cwd = data.get("cwd", "")
    args = data.get("args", "")
    timeout = data.get("timeout")  # Caller can override default

    if not tool_name:
        return web.json_response({"detail": "Missing 'tool' field"}, status=400)

    tool_def = TOOL_REGISTRY.get(tool_name)
    if not tool_def:
        available = ", ".join(sorted(TOOL_REGISTRY.keys()))
        return web.json_response(
            {"detail": f"Unknown tool '{tool_name}'. Available: {available}"},
            status=404,
        )

    if not cwd:
        return web.json_response({"detail": "Missing 'cwd' field"}, status=400)

    # Parse and validate args
    try:
        parts = shlex.split(args) if args else []
    except ValueError as ex:
        return web.json_response({"success": False, "output": str(ex)})

    # Check allowed subcommands (if the tool defines them)
    if tool_def.allowed_subcommands and parts:
        if parts[0] not in tool_def.allowed_subcommands:
            allowed = ", ".join(sorted(tool_def.allowed_subcommands))
            return web.json_response(
                {"detail": f"Subcommand '{parts[0]}' not allowed. Allowed: {allowed}"},
                status=403,
            )

    # Check blocked flags
    for part in parts:
        if any(part == flag or part.startswith(f"{flag}=") for flag in tool_def.blocked_flags):
            return web.json_response(
                {"detail": f"Flag '{part}' not allowed for security reasons"},
                status=403,
            )

    # Build command — substitute {cwd} in prefix
    cmd_args = [arg.replace("{cwd}", cwd) for arg in tool_def.command_prefix] + parts

    # Use caller timeout or tool default
    effective_timeout = timeout if timeout is not None else tool_def.timeout

    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=effective_timeout)
        stdout_text = stdout.decode(errors="replace")
        stderr_text = stderr.decode(errors="replace")
        success = proc.returncode in tool_def.success_exit_codes
        output = stdout_text if stdout_text.strip() else stderr_text
        return web.json_response({"success": success, "output": output[:50000]})
    except TimeoutError:
        if proc:
            proc.kill()
            await proc.wait()
        return web.json_response({"success": False, "output": f"Command timed out after {effective_timeout}s"})
    except OSError as ex:
        return web.json_response({"success": False, "output": str(ex)})


def _run_server(port: int) -> None:
    """Run in a dedicated thread with its own event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app = web.Application()
    app.router.add_post("/tools/run", handle_tool_request)
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, "127.0.0.1", port)
    loop.run_until_complete(site.start())
    loop.run_forever()


def start_tool_server(port: int = TOOL_SERVER_PORT) -> threading.Thread:
    """Start tool server in a background daemon thread. Non-blocking."""
    thread = threading.Thread(target=_run_server, args=(port,), daemon=True, name="tool-server")
    thread.start()
    return thread
