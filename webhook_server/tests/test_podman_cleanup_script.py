"""Tests for scripts/podman-cleanup.sh run against temp dirs only."""

import os
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts/podman-cleanup.sh"
# Not the real uid: every /tmp/storage-run-<PUID>* cleanup path must be a no-op.
FAKE_PUID = "99999"


@pytest.fixture
def runtime_dir(tmp_path: Path) -> Path:
    """Fake XDG_RUNTIME_DIR holding libpod/tmp/pause.pid plus an unrelated sentinel file."""
    path = tmp_path / "runtime"
    (path / "libpod/tmp").mkdir(parents=True)
    (path / "keep-me").write_text("sentinel")
    return path


@pytest.fixture
def pid_path(runtime_dir: Path) -> Path:
    return runtime_dir / "libpod/tmp/pause.pid"


@pytest.fixture
def stub_bin(tmp_path: Path) -> Path:
    """Directory holding a stub `podman` that only records its arguments."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub_log = tmp_path / "podman.log"
    stub = stub_dir / "podman"
    stub.write_text(f'#!/bin/bash\necho "$@" >> "{stub_log}"\nexit 0\n')
    stub.chmod(0o755)
    return stub_dir


@pytest.fixture
def clean_storage() -> Generator[None]:
    """Guard: the test must never have a real /tmp/storage-run-99999* to clean."""
    assert not list(Path("/tmp").glob(f"storage-run-{FAKE_PUID}*"))
    yield
    assert not list(Path("/tmp").glob(f"storage-run-{FAKE_PUID}*"))


def run_script(runtime_dir: Path, stub_bin: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{stub_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "XDG_RUNTIME_DIR": str(runtime_dir),
        "PUID": FAKE_PUID,
        "TMPDIR": str(tmp_path),
    }
    return subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60, check=False)


def assert_untouched(runtime_dir: Path, tmp_path: Path) -> None:
    assert (runtime_dir / "keep-me").read_text() == "sentinel"
    assert (tmp_path / "podman.log").read_text().split()  # every podman call went to the stub


def test_removes_dead_pid_file(runtime_dir: Path, pid_path: Path, stub_bin: Path, tmp_path: Path, clean_storage: None):
    pid_path.write_text("0")

    result = run_script(runtime_dir, stub_bin, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "Removing stale pause pid" in result.stdout
    assert not pid_path.exists()
    assert_untouched(runtime_dir, tmp_path)


@pytest.mark.parametrize("content", ["", "\n"])
def test_removes_empty_pid_file(
    runtime_dir: Path, pid_path: Path, stub_bin: Path, tmp_path: Path, clean_storage: None, content: str
):
    pid_path.write_text(content)

    result = run_script(runtime_dir, stub_bin, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "Removing stale pause pid" in result.stdout
    assert not pid_path.exists()
    assert_untouched(runtime_dir, tmp_path)


def _spawn(argv: list[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_removes_pid_file_of_live_non_pause_process(
    runtime_dir: Path, pid_path: Path, stub_bin: Path, tmp_path: Path, clean_storage: None
):
    proc = _spawn(["sleep", "60"])
    try:
        pid_path.write_text(str(proc.pid))

        result = run_script(runtime_dir, stub_bin, tmp_path)
    finally:
        proc.kill()
        proc.wait()

    assert result.returncode == 0, result.stderr
    assert "Removing stale pause pid" in result.stdout
    assert not pid_path.exists()
    assert_untouched(runtime_dir, tmp_path)


def test_keeps_pid_file_of_live_pause_process(
    runtime_dir: Path, pid_path: Path, stub_bin: Path, tmp_path: Path, clean_storage: None
):
    # "pause" in argv makes /proc/<pid>/cmdline match the script's pause check.
    proc = _spawn([sys.executable, "-c", "import time; time.sleep(60)", "pause"])
    try:
        pid_path.write_text(str(proc.pid))

        result = run_script(runtime_dir, stub_bin, tmp_path)
    finally:
        proc.kill()
        proc.wait()

    assert result.returncode == 0, result.stderr
    assert "Removing stale pause pid" not in result.stdout
    assert pid_path.read_text() == str(proc.pid)
    assert_untouched(runtime_dir, tmp_path)


def test_no_pid_file_exits_zero(runtime_dir: Path, stub_bin: Path, tmp_path: Path, clean_storage: None):
    result = run_script(runtime_dir, stub_bin, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "Removing stale pause pid" not in result.stdout
    assert_untouched(runtime_dir, tmp_path)
