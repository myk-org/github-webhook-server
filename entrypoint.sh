#!/bin/bash

set -ep

CMD=$(uv run ./entrypoint.py)
echo "$CMD"
$CMD
