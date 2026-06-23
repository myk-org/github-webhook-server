"""Standalone async HTTP server for AI custom git tools.

Runs on a separate port (default: 5001) with its own event loop thread,
so git command execution never competes with the main webhook server's
event loop during heavy CI processing.

SECURITY: Localhost-only. Restricted to read-only git subcommands.
"""

from __future__ import annotations

import asyncio
import shlex
import threading
from typing import Any

from aiohttp import web

ALLOWED_GIT_COMMANDS = frozenset({"diff", "log", "show", "status", "rev-parse"})
BLOCKED_FLAGS = frozenset({"--no-index", "--output", "--raw"})

GIT_TOOLS_PORT = 5001


async def run_git_command(request: web.Request) -> web.Response:
    """Execute a read-only git command in the specified directory."""
    try:
        data: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"detail": "Invalid JSON"}, status=400)

    cwd = data.get("cwd", "")
    args = data.get("args", "")

    if not cwd or not args:
        return web.json_response({"detail": "Missing cwd or args"}, status=400)

    try:
        parts = shlex.split(args)
    except ValueError as ex:
        return web.json_response({"success": False, "output": str(ex)})

    if not parts:
        return web.json_response({"detail": "Empty git command"}, status=400)

    subcommand = parts[0]
    if subcommand not in ALLOWED_GIT_COMMANDS:
        allowed = ", ".join(sorted(ALLOWED_GIT_COMMANDS))
        return web.json_response(
            {"detail": f"Git subcommand '{subcommand}' not allowed. Allowed: {allowed}"},
            status=403,
        )

    for part in parts[1:]:
        if any(part == flag or part.startswith(f"{flag}=") for flag in BLOCKED_FLAGS):
            return web.json_response(
                {"detail": f"Git flag '{part}' not allowed for security reasons"},
                status=403,
            )

    cmd_args = ["git", "-C", cwd, *parts]
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        stdout_text = stdout.decode(errors="replace")
        stderr_text = stderr.decode(errors="replace")
        # git diff exit code 1 means "differences found" (not an error)
        is_diff_command = parts[0] == "diff"
        success = proc.returncode == 0 or (is_diff_command and proc.returncode == 1 and bool(stdout_text.strip()))
        output = stdout_text if stdout_text.strip() else stderr_text
        return web.json_response({"success": success, "output": output[:50000]})
    except TimeoutError:
        if proc:
            proc.kill()
            await proc.wait()
        return web.json_response({"success": False, "output": "Command timed out after 30s"})
    except OSError as ex:
        return web.json_response({"success": False, "output": str(ex)})


def _run_server(port: int) -> None:
    """Run the git-tools server in a dedicated thread with its own event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = web.Application()
    app.router.add_post("/internal/git-tools/run", run_git_command)

    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, "127.0.0.1", port)
    loop.run_until_complete(site.start())
    loop.run_forever()


def start_git_tools_server(port: int = GIT_TOOLS_PORT) -> threading.Thread:
    """Start the git-tools server in a background thread.

    Returns the thread handle. The server runs on 127.0.0.1:{port}
    with its own event loop, isolated from the main webhook server.
    """
    thread = threading.Thread(target=_run_server, args=(port,), daemon=True, name="git-tools-server")
    thread.start()
    return thread
