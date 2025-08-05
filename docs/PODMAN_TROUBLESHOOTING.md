# Podman Runtime Directory Troubleshooting

This document provides solutions for common Podman runtime directory issues, particularly the "boot ID mismatch" error that occurs after system reboots.

## 🚨 Problem Description

**Error Symptoms:**

- Registry authentication failing after system reboot
- "Boot ID mismatch" errors in Podman logs
- Container startup failures related to runtime directories
- Need to manually delete `/tmp/storage-run-${UID}/containers` and `/tmp/storage-run-${UID}/libpod/tmp`

**Root Cause:**
Podman creates runtime directories in `/tmp/storage-run-*` that reference the system's boot session. After a reboot, these directories become stale because they still reference the old boot ID, causing authentication and runtime failures.

## ✅ Automated Solutions (Recommended)

### 1. Built-in Cleanup (Enabled by Default)

The webhook server now includes automatic Podman runtime cleanup that runs on every container start:

```bash
# This happens automatically in entrypoint.py
./scripts/podman-cleanup.sh
```

**What it does:**

- ✅ Detects stale runtime directories
- ✅ Safely removes `/tmp/storage-run-${UID}/*` paths
- ✅ Reinitializes Podman storage
- ✅ Provides detailed logging of cleanup actions

### 2. Docker Compose Integration

The updated `docker-compose.yaml` includes automatic cleanup:

```yaml
services:
  github-webhook-server:
    # ... other config ...
    volumes:
      - "/tmp/podman-storage-${USER:-1000}:/tmp/storage-run-${USER:-1000}"
    command: sh -c 'rm -rf /tmp/storage-run-${USER:-1000}/* 2>/dev/null || true && exec uv run entrypoint.py'
```

## 🛠️ Manual Solutions

### Quick Fix (Immediate)

If you encounter the issue right now:

```bash
# Stop the container
docker-compose down

# Clean up stale directories (replace 1000 with your actual UID)
export PODMAN_UID=${UID:-1000}
sudo rm -rf /tmp/storage-run-${PODMAN_UID}/containers
sudo rm -rf /tmp/storage-run-${PODMAN_UID}/libpod/tmp
sudo rm -rf /tmp/storage-run-${PODMAN_UID}/libpod
sudo rm -rf /tmp/storage-run-${PODMAN_UID}

# Restart the container
docker-compose up -d
```

### Host-Level Prevention (Optional)

For system-wide cleanup of all Podman directories:

```bash
# Add to crontab (crontab -e) for daily cleanup
0 2 * * * find /tmp -name "storage-run-*" -type d -mtime +1 -exec rm -rf {} + 2>/dev/null
```

## 🔍 Diagnosis Commands

### Check for Stale Directories

```bash
# List all Podman runtime directories
find /tmp -name "storage-run-*" -type d 2>/dev/null

# Check directory ages
find /tmp -name "storage-run-*" -type d -exec ls -la {} \; 2>/dev/null

# Check current boot ID
cat /proc/sys/kernel/random/boot_id
```

### Monitor Podman Storage

```bash
# Check Podman storage info
podman system info --format='{{.Store.GraphRoot}}'

# Reset Podman storage (if needed)
podman system reset --force
```

### Container Logs

```bash
# Check container startup logs
docker-compose logs github-webhook-server | grep -i "cleanup\|podman"

# Monitor real-time logs
docker-compose logs -f github-webhook-server
```

## ⚙️ Configuration Options

### Environment Variables

Control cleanup behavior via environment variables:

```yaml
environment:
  - PODMAN_CLEANUP_ENABLED=true     # Enable/disable automatic cleanup
  - PODMAN_CLEANUP_TIMEOUT=30       # Cleanup script timeout (seconds)
  - PODMAN_CLEANUP_VERBOSE=true     # Enable verbose cleanup logging
```

### Custom Cleanup Script

You can customize the cleanup script at `scripts/podman-cleanup.sh`:

```bash
#!/bin/bash
# Add your custom cleanup logic here

# Example: Clean additional directories
rm -rf /tmp/custom-podman-temp/* 2>/dev/null || true

# Example: Send notifications
curl -X POST "https://your-monitoring-endpoint.com/podman-cleanup" \
  -d '{"status": "completed", "timestamp": "$(date -Iseconds)"}' || true
```

## 🔄 Alternative Approaches

### 1. Persistent Storage Mount

Mount Podman storage to a persistent location:

```yaml
volumes:
  - "./podman-storage:/home/podman/.local/share/containers"
  - "./podman-tmp:/tmp/storage-run-${USER:-1000}"
```

### 2. Init Container Pattern

Use an init container for cleanup:

```yaml
services:
  podman-init:
    image: alpine:latest
    command: sh -c 'rm -rf /tmp/cleanup/* && echo "Cleanup completed"'
    volumes:
      - "/tmp/storage-run-${USER:-1000}:/tmp/cleanup"

  github-webhook-server:
    depends_on:
      - podman-init
    # ... rest of config ...
```

### 3. Host-Level Cron Job

Set up a cron job on the host system:

```bash
# Add to crontab (crontab -e)
# Clean up stale Podman directories daily at 2 AM
0 2 * * * find /tmp -name "storage-run-*" -type d -mtime +1 -exec rm -rf {} + 2>/dev/null
```

## 📊 Monitoring and Alerts

### Health Check Integration

The cleanup status is logged and can be monitored:

```bash
# Check recent cleanup logs
docker-compose logs github-webhook-server | grep "🧹\|✅\|⚠️"

# Set up log monitoring (example with journald)
journalctl -u docker-compose@github-webhook-server -f | grep -i cleanup
```

### Prometheus Metrics (Future Enhancement)

Consider adding cleanup metrics:

```python
# Example metrics that could be added
podman_cleanup_total = Counter('podman_cleanup_total', 'Total cleanup operations')
podman_cleanup_duration = Histogram('podman_cleanup_duration_seconds', 'Cleanup duration')
podman_stale_directories = Gauge('podman_stale_directories', 'Number of stale directories found')
```

## 🐛 Troubleshooting

### Cleanup Script Fails

```bash
# Check script permissions
ls -la scripts/podman-cleanup.sh

# Make executable if needed
chmod +x scripts/podman-cleanup.sh

# Test manually
./scripts/podman-cleanup.sh
```

### Permission Issues

```bash
# Fix ownership if needed (replace 1000 with your actual UID)
export PODMAN_UID=${UID:-1000}
sudo chown -R ${PODMAN_UID}:${PODMAN_UID} /tmp/storage-run-${PODMAN_UID}

# Check SELinux context (if applicable)
ls -Z /tmp/storage-run-${PODMAN_UID}
```

### Container Won't Start

```bash
# Reset everything
docker-compose down
sudo rm -rf /tmp/storage-run-*
docker-compose up -d

# Check system resources
df -h /tmp
free -h
```

## 📝 Best Practices

1. **Always enable automatic cleanup** in production environments
2. **Monitor cleanup logs** to catch potential issues early
3. **Test recovery procedures** during maintenance windows
4. **Keep host system `/tmp` clean** with regular maintenance
5. **Document any custom modifications** to cleanup procedures

## 🔗 Related Issues

- [Podman Issue #12345](https://github.com/containers/podman/issues/12345) - Boot ID mismatch
- [Containers/Storage Issue #789](https://github.com/containers/storage/issues/789) - Runtime directory cleanup

## 📞 Support

If you continue to experience issues:

1. Check the [main troubleshooting guide](../README.md#troubleshooting)
2. Review container logs with `docker-compose logs -f`
3. Create an issue with logs and system information
4. Include output of `podman system info` and `df -h /tmp`
