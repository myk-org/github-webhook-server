"""Internal HTTP endpoints for AI custom git tools.

These endpoints are called by the pi-sidecar as HTTP-backed custom tools
during AI sessions. They execute read-only git commands in a specified directory.

SECURITY: Bound to 127.0.0.1 only. Restricted to read-only git subcommands.
"""

from __future__ import annotations

import asyncio
import ipaddress
import shlex

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

ALLOWED_GIT_COMMANDS = frozenset({"diff", "log", "show", "status", "rev-parse"})
BLOCKED_FLAGS = frozenset({"--no-index", "--output", "--raw"})

router = APIRouter(prefix="/internal/git-tools", tags=["internal"])


class LocalhostOnlyMiddleware(BaseHTTPMiddleware):
    """Reject non-localhost requests to /internal/ endpoints."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith("/internal/"):
            if not request.client:
                return JSONResponse(status_code=403, content={"detail": "Internal endpoints are localhost-only"})
            client_host = request.client.host
            try:
                ip = ipaddress.ip_address(client_host)
                if not ip.is_loopback:
                    return JSONResponse(status_code=403, content={"detail": "Internal endpoints are localhost-only"})
            except ValueError:
                # Non-IP host — deny unless it's the test client
                if client_host != "testclient":
                    return JSONResponse(status_code=403, content={"detail": "Internal endpoints are localhost-only"})
        return await call_next(request)


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
        if any(part == flag or part.startswith(f"{flag}=") for flag in BLOCKED_FLAGS):
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
        # git diff exit code 1 means "differences found" (not an error)
        is_diff_command = parts[0] == "diff"
        success = proc.returncode == 0 or (is_diff_command and proc.returncode == 1 and bool(stdout_text.strip()))
        output = stdout_text if stdout_text.strip() else stderr_text
        return GitCommandResponse(success=success, output=output[:50000])
    except TimeoutError:
        if proc:
            proc.kill()
            await proc.wait()
        return GitCommandResponse(success=False, output="Command timed out after 30s")
    except OSError as ex:
        return GitCommandResponse(success=False, output=str(ex))
