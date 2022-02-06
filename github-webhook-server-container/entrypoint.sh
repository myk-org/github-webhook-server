#!/bin/bash
set -e

python3 webhook.py
python3 -m flask run --host=0.0.0.0 --port=5151