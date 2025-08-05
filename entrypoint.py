import asyncio
import subprocess
import sys
from pathlib import Path

import uvicorn

from webhook_server.libs.config import Config
from webhook_server.utils.github_repository_and_webhook_settings import repository_and_webhook_settings

_config = Config()
_root_config = _config.root_data
_ip_bind = _root_config.get("ip-bind", "0.0.0.0")
_port = _root_config.get("port", 5000)
_max_workers = _root_config.get("max-workers", 10)
_webhook_secret = _root_config.get("webhook-secret")


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
    uvicorn.run(
        "webhook_server.app:FASTAPI_APP",
        host=_ip_bind,
        port=int(_port),
        workers=int(_max_workers),
        reload=False,
    )
