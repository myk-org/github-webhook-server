#!/usr/bin/env bash
set -euo pipefail

# Generate the uvicorn command from Python
cmd=$(uv run ./entrypoint.py)
echo "${cmd}"

# Replace the shell with the server process (PID 1 inside container)
exec ${cmd}
