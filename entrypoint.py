import asyncio
import os
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


def run_database_migrations() -> None:
    """Run Alembic database migrations.

    Only runs if ENABLE_METRICS_SERVER environment variable is set to "true".
    Applies pending migrations with 'alembic upgrade head'.

    Note: Migrations must be generated manually by developers:
        alembic revision --autogenerate -m "Description"

    Raises:
        SystemExit: If migration fails (fail-fast behavior)
    """
    metrics_enabled = os.environ.get("ENABLE_METRICS_SERVER") == "true"

    if not metrics_enabled:
        print("ℹ️  Metrics server disabled - skipping database migrations")
        return

    try:
        alembic_ini = Path(__file__).parent / "alembic.ini"
        versions_dir = Path(_config.data_dir) / "migrations" / "versions"

        # Ensure versions directory exists (required for Alembic)
        versions_dir.mkdir(parents=True, exist_ok=True)

        print("⬆️  Applying database migrations...")
        result = subprocess.run(
            ["uv", "run", "alembic", "-c", str(alembic_ini), "upgrade", "head"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=Path(__file__).parent,
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        print("✅ Database migrations completed successfully")
    except subprocess.CalledProcessError as e:
        print(f"❌ FATAL: Database migration failed: {e}", file=sys.stderr)
        if e.stdout:
            print(f"stdout: {e.stdout}", file=sys.stderr)
        if e.stderr:
            print(f"stderr: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("❌ FATAL: Database migration timed out after 60 seconds", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ FATAL: Unexpected error during database migration: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    # Run Podman cleanup before starting the application
    run_podman_cleanup()

    # Run database migrations if metrics server is enabled
    run_database_migrations()

    result = asyncio.run(repository_and_webhook_settings(webhook_secret=_webhook_secret))

    # Logging Configuration:
    # - Uvicorn uses default logging which automatically respects FORCE_COLOR environment variable
    #   for colored terminal output (useful for Docker logs with color support)
    # - Application logs use simple-logger with console=True for colored output in Docker logs
    # - Both logging systems work together: uvicorn handles HTTP request logs,
    #   while simple-logger handles application-level logs with structured formatting
    uvicorn.run(
        "webhook_server.app:FASTAPI_APP",
        host=_ip_bind,
        port=int(_port),
        workers=int(_max_workers),
        reload=False,
    )
