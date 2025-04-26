#!/bin/bash

SERVER_RUN_CMD="uv run gunicorn webhook_server.app:FASTAPI_APP -c ./gunicorn.conf.py"
