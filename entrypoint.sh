#!/bin/bash

SERVER_RUN_CMD="poetry run uvicorn webhook_server_container.app:FastAPI_APP "
UVICORN_WORKERS="${UVICORN_MAX_WORKERS:=10}"

set -ep

poetry run python webhook_server_container/utils/github_repository_settings.py
poetry run python webhook_server_container/utils/webhook.py

if [[ -z $DEVELOPMENT ]]; then
	eval "${SERVER_RUN_CMD} --workers ${UVICORN_WORKERS}"
else
	eval "${SERVER_RUN_CMD} --reload"
fi
