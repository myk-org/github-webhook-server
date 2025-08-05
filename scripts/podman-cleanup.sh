#!/bin/bash
# Podman Runtime Directory Cleanup Script
# Prevents boot ID mismatch issues by cleaning stale runtime directories

set -euo pipefail

USER_ID=${PUID:-1000}
CLEANUP_PATHS=(
    "/tmp/storage-run-${USER_ID}/containers"
    "/tmp/storage-run-${USER_ID}/libpod/tmp"
    "/tmp/storage-run-${USER_ID}/libpod"
    "/tmp/storage-run-${USER_ID}"
)

echo "🧹 Podman Runtime Cleanup - User ID: ${USER_ID}"

# Check if cleanup is needed
cleanup_needed=false
for path in "${CLEANUP_PATHS[@]}"; do
    if [[ -d "$path" ]]; then
        echo "   Found stale directory: $path"
        cleanup_needed=true
    fi
done

if [[ "$cleanup_needed" = true ]]; then
    echo "🗑️  Removing stale Podman runtime directories..."
    for path in "${CLEANUP_PATHS[@]}"; do
        if [[ -d "$path" ]]; then
            echo "   Removing: $path"
            rm -rf "$path" || {
                echo "   ⚠️  Warning: Could not remove $path (may not exist or permission issue)"
            }
        fi
    done
    echo "✅ Cleanup completed"
else
    echo "✅ No cleanup needed - runtime directories are clean"
fi

# Clean up stale Podman resources without destroying everything
echo "🔧 Cleaning stale Podman resources..."
# Remove stopped containers
podman container prune --force 2>/dev/null || true
# Remove dangling images (untagged images not used by any container)
podman image prune --force 2>/dev/null || true
# Remove unused volumes not attached to any container
podman volume prune --force 2>/dev/null || true
# Remove unused networks (excluding default networks)
podman network prune --force 2>/dev/null || true

# Verify Podman storage is accessible
echo "🔍 Verifying Podman storage accessibility..."
podman info --format='{{.Store.GraphRoot}}' > /dev/null 2>&1 || true

echo "🚀 Podman runtime is ready"
