#!/bin/bash

SERVER_RUN_CMD="uv run uvicorn webhook_server_container.app:FASTAPI_APP --host 0.0.0.0 --port 5000 "
UVICORN_WORKERS="${UVICORN_MAX_WORKERS:=10}"

set -ep

uv run webhook_server_container/utils/github_repository_settings.py
uv run webhook_server_container/utils/webhook.py

if [[ -z $DEVELOPMENT ]]; then
  eval "${SERVER_RUN_CMD} --workers ${UVICORN_WORKERS}"
else
  eval "${SERVER_RUN_CMD} --reload"
fi
