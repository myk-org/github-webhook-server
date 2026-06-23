import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import uvicorn

from webhook_server.libs.config import Config
from webhook_server.utils.github_repository_and_webhook_settings import repository_and_webhook_settings
from webhook_server.web.git_tools import GIT_TOOLS_PORT, start_git_tools_server

_config = Config()
_root_config = _config.root_data
_ip_bind = _root_config.get("ip-bind", "0.0.0.0")
_port = _root_config.get("port", 5000)
_max_workers = _root_config.get("max-workers", 10)
_webhook_secret = _root_config.get("webhook-secret")
_dev_mode = os.environ.get("WEBHOOK_SERVER_DEV_MODE", "").lower() in ("1", "true", "yes")


def run_podman_cleanup() -> None:
    """Run Podman runtime cleanup to prevent boot ID mismatch issues."""
    cleanup_script = Path(__file__).parent / "scripts" / "podman-cleanup.sh"

    if cleanup_script.exists():
        try:
            print("🧹 Running Podman runtime cleanup...")
            result = subprocess.run([str(cleanup_script)], check=True, capture_output=True, text=True, timeout=30)
            print(result.stdout)
            if result.stderr:
                print(f"⚠️  Cleanup warnings: {result.stderr}", file=sys.stderr)
        except subprocess.CalledProcessError as e:
            print(f"⚠️  Podman cleanup failed (non-critical): {e}", file=sys.stderr)
            if e.stdout:
                print(f"stdout: {e.stdout}", file=sys.stderr)
            if e.stderr:
                print(f"stderr: {e.stderr}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print("⚠️  Podman cleanup timed out (non-critical)", file=sys.stderr)
        except Exception as e:
            print(f"⚠️  Unexpected error during Podman cleanup: {e}", file=sys.stderr)
    else:
        print(f"ℹ️  Podman cleanup script not found at {cleanup_script}")


if __name__ == "__main__":
    # Run Podman cleanup before starting the application
    run_podman_cleanup()

    result = asyncio.run(repository_and_webhook_settings(webhook_secret=_webhook_secret))

    # Logging Configuration:
    # - Uvicorn uses default logging which automatically respects FORCE_COLOR environment variable
    #   for colored terminal output (useful for Docker logs with color support)
    # - Application logs use simple-logger with console=True for colored output in Docker logs
    # - Both logging systems work together: uvicorn handles HTTP request logs,
    #   while simple-logger handles application-level logs with structured formatting
    uvicorn_kwargs: dict[str, Any] = {
        "host": _ip_bind,
        "port": int(_port),
        "reload": _dev_mode,
    }
    if not _dev_mode:
        uvicorn_kwargs["workers"] = int(_max_workers)

    # Start git-tools server on separate event loop (avoids contention with CI checks)
    start_git_tools_server()
    print(f"\u2705 Git-tools server started on 127.0.0.1:{GIT_TOOLS_PORT}")

    uvicorn.run("webhook_server.app:FASTAPI_APP", **uvicorn_kwargs)
