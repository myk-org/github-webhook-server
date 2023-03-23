#!/bin/bash
set -e

#poetry run python3 webhook_server_container/webhook.py
poetry run python3 -m flask --app webhook_server_container/app.py run --host=0.0.0.0 --port=5000 --no-reload
