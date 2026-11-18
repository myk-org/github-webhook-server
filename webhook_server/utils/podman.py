"""Recovery from known rootless podman failures.

Kept synchronous so both the async command runner and the startup settings path
can repair podman state before retrying a failed command.
"""

import os
import shutil
from pathlib import Path

from webhook_server.utils.helpers import get_logger_with_params

LOGGER = get_logger_with_params()

BOOT_ID_MISMATCH_ERROR = "Error: current system boot ID differs from cached boot ID; an unhandled reboot has occurred"
NAMESPACE_REEXEC_ERROR = "Error: cannot re-exec process to join the existing user namespace"

PODMAN_BUGS = (BOOT_ID_MISMATCH_ERROR, NAMESPACE_REEXEC_ERROR)

# Only the boot ID mismatch is a global storage problem; touching storage for the
# namespace error disturbs other in-flight builds.
_STORAGE_DIRS = ("/tmp/storage-run-1000/containers", "/tmp/storage-run-1000/libpod/tmp")


def is_podman_bug(err: str) -> bool:
    """Return True when the error text is one of the known podman failures."""
    return any(bug in err.strip() for bug in PODMAN_BUGS)


def pause_pid_path() -> Path:
    """Return the rootless podman pause pid file path."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime_dir, "libpod/tmp/pause.pid")


def is_live_pause_process(pid: str) -> bool:
    """Return True when pid names a live podman pause process.

    The rootless pause process is `catatonit -P`, so its cmdline never contains
    "pause"; check comm as well.
    """
    proc_dir = Path("/proc", pid)
    if not proc_dir.exists():
        return False
    try:
        cmdline = (proc_dir / "cmdline").read_text(errors="ignore")
        comm = (proc_dir / "comm").read_text(errors="ignore").strip()
    except OSError:
        return False
    return "pause" in cmdline or comm == "catatonit"


def _remove_stale_pause_pid(removed: list[str]) -> None:
    pid_path = pause_pid_path()
    try:
        pid = pid_path.read_text(errors="ignore").strip()
    except OSError:
        return
    if pid and is_live_pause_process(pid=pid):
        LOGGER.debug(f"Keeping {pid_path}: pause process {pid} is alive")
        return
    try:
        pid_path.unlink(missing_ok=True)
    except OSError as ex:
        LOGGER.warning(f"Failed to remove {pid_path}: {ex}")
        return
    LOGGER.debug(f"Removed stale {pid_path}")
    removed.append(str(pid_path))


def repair_podman_state(err: str) -> list[str]:
    """Remove the podman state that the given error points at.

    Returns the removed paths. Each removal is best effort: an OSError is logged
    and swallowed so the caller can still retry.
    """
    removed: list[str] = []

    if BOOT_ID_MISMATCH_ERROR in err:
        for directory in _STORAGE_DIRS:
            shutil.rmtree(directory, ignore_errors=True)
            LOGGER.debug(f"Removed {directory}")
            removed.append(directory)

    _remove_stale_pause_pid(removed=removed)
    return removed
