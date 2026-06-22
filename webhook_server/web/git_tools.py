"""Internal HTTP endpoints for AI custom git tools.

These endpoints are called by the pi-sidecar as HTTP-backed custom tools
during AI sessions. They execute read-only git commands in a specified directory.

SECURITY: Bound to 127.0.0.1 only. Restricted to read-only git subcommands.
"""

from __future__ import annotations

import asyncio
import shlex

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

ALLOWED_GIT_COMMANDS = frozenset({"diff", "log", "show", "status", "rev-parse"})
BLOCKED_FLAGS = frozenset({"--no-index", "--output", "--raw"})

router = APIRouter(prefix="/internal/git-tools", tags=["internal"])


class GitCommandRequest(BaseModel):
    cwd: str
    args: str


class GitCommandResponse(BaseModel):
    success: bool
    output: str


@router.post("/run")
async def run_git_command(request: GitCommandRequest) -> GitCommandResponse:
    """Execute a read-only git command in the specified directory."""
    parts = shlex.split(request.args)
    if not parts:
        raise HTTPException(status_code=400, detail="Empty git command")

    subcommand = parts[0]
    if subcommand not in ALLOWED_GIT_COMMANDS:
        raise HTTPException(
            status_code=403,
            detail=f"Git subcommand '{subcommand}' not allowed. Allowed: {', '.join(sorted(ALLOWED_GIT_COMMANDS))}",
        )

    for part in parts[1:]:
        if part in BLOCKED_FLAGS:
            raise HTTPException(
                status_code=403,
                detail=f"Git flag '{part}' not allowed for security reasons",
            )

    # Build argument list — no shell interpolation
    cmd_args = ["git", "-C", request.cwd, *parts]
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
        output = stdout_text if stdout_text.strip() else stderr_text
        return GitCommandResponse(success=proc.returncode == 0 or bool(stdout_text.strip()), output=output[:50000])
    except TimeoutError:
        if proc:
            proc.kill()
            await proc.wait()
        return GitCommandResponse(success=False, output="Command timed out after 30s")
    except OSError as ex:
        return GitCommandResponse(success=False, output=str(ex))
