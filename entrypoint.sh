#!/bin/bash
set -euo pipefail

# Start Pi SDK sidecar in background with lifecycle coupling
if [ -f "$APP_DIR/sidecar-helper/dist/server.js" ]; then
    export SIDECAR_PORT="${SIDECAR_PORT:-9100}"
    # Resolve extension paths for sidecar provider registration
    _ORCH_EXTENSIONS="$APP_DIR/sidecar-helper/node_modules/pi-orchestrator-config/extensions"
    if [ -z "${SIDECAR_ACPX_EXTENSION_PATH:-}" ]; then
        if [ -f "${_ORCH_EXTENSIONS}/acpx-provider/index.ts" ]; then
            export SIDECAR_ACPX_EXTENSION_PATH="${_ORCH_EXTENSIONS}/acpx-provider/index.ts"
        else
            echo "[sidecar] WARNING: ACPX extension not found at ${_ORCH_EXTENSIONS}/acpx-provider/index.ts" >&2
        fi
    fi
    if [ -z "${SIDECAR_CLI_PROVIDER_EXTENSION_PATH:-}" ]; then
        if [ -f "${_ORCH_EXTENSIONS}/cli-provider/index.ts" ]; then
            export SIDECAR_CLI_PROVIDER_EXTENSION_PATH="${_ORCH_EXTENSIONS}/cli-provider/index.ts"
        else
            echo "[sidecar] WARNING: CLI provider extension not found at ${_ORCH_EXTENSIONS}/cli-provider/index.ts" >&2
        fi
    fi
    if [ -z "${SIDECAR_PROVIDER_EXTENSION_PATH:-}" ]; then
        if [ -f "${_ORCH_EXTENSIONS}/providers/index.ts" ]; then
            export SIDECAR_PROVIDER_EXTENSION_PATH="${_ORCH_EXTENSIONS}/providers/index.ts"
        else
            echo "[sidecar] WARNING: Providers extension not found at ${_ORCH_EXTENSIONS}/providers/index.ts" >&2
        fi
    fi
    node "$APP_DIR/sidecar-helper/dist/server.js" &
    SIDECAR_PID=$!
    echo "[sidecar] Started Pi SDK sidecar (PID $SIDECAR_PID) on port $SIDECAR_PORT"

    # Kill sidecar when main process exits
    trap 'kill $SIDECAR_PID 2>/dev/null; wait $SIDECAR_PID 2>/dev/null' EXIT

    # Monitor sidecar — if it dies, kill the main process too
    # TERM trap prevents misleading "died" message on normal shutdown
    (trap 'exit 0' TERM; while kill -0 $SIDECAR_PID 2>/dev/null; do sleep 5; done; echo "[sidecar] Sidecar died, shutting down container"; kill 1 2>/dev/null) &

    # Wait for sidecar to be ready (up to 15s)
    for i in $(seq 1 30); do
        if curl -sf http://127.0.0.1:$SIDECAR_PORT/health > /dev/null 2>&1; then
            echo "[sidecar] Health check passed"
            break
        fi
        sleep 0.5
    done

    if ! curl -sf http://127.0.0.1:$SIDECAR_PORT/health > /dev/null 2>&1; then
        echo "[sidecar] ERROR: sidecar failed to become healthy within 15s — AI features will not work" >&2
    fi
else
    echo "[sidecar] WARNING: sidecar-helper/dist/server.js not found, AI features will not be available"
fi

# Execute the main application
exec uv run entrypoint.py
