#!/bin/bash
set -e

update-ca-certificates
python3 webhook.py
python3 -m flask run --host=0.0.0.0 --port=5000
