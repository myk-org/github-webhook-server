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

    cmd = f"git -C {shlex.quote(request.cwd)} {request.args}"
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode() if proc.returncode == 0 else stderr.decode()
        return GitCommandResponse(success=proc.returncode == 0, output=output[:50000])
    except TimeoutError:
        return GitCommandResponse(success=False, output="Command timed out after 30s")
    except Exception as ex:
        return GitCommandResponse(success=False, output=str(ex))
