"""Tests for webhook_server.utils.podman (rootless podman state repair)."""

import os
import shutil
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from webhook_server.utils.podman import (
    _STORAGE_DIRS,
    BOOT_ID_MISMATCH_ERROR,
    NAMESPACE_REEXEC_ERROR,
    PODMAN_BUGS,
    is_live_pause_process,
    is_podman_bug,
    pause_pid_path,
    repair_podman_state,
)


@pytest.fixture
def mock_rmtree(monkeypatch: pytest.MonkeyPatch) -> Generator[Mock]:
    """Patch shutil.rmtree so no real /tmp/storage-run-* directory is ever removed."""
    mock = Mock()
    monkeypatch.setattr(shutil, "rmtree", mock)
    yield mock


@pytest.fixture
def pid_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point pause_pid_path() at a temp XDG_RUNTIME_DIR and return the pid file path."""
    runtime_dir = tmp_path / "runtime"
    path = runtime_dir / "libpod/tmp/pause.pid"
    path.parent.mkdir(parents=True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_dir))
    return path


def _fake_proc(monkeypatch: pytest.MonkeyPatch, pid: str, *, cmdline: str, comm: str) -> None:
    """Make /proc/<pid> look alive to is_live_pause_process without touching real /proc."""
    proc_dir = f"/proc/{pid}"
    real_exists = Path.exists
    real_read_text = Path.read_text

    def fake_exists(self: Path) -> bool:
        return True if str(self) == proc_dir else real_exists(self)

    def fake_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if str(self) == f"{proc_dir}/cmdline":
            return cmdline
        if str(self) == f"{proc_dir}/comm":
            return comm
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(Path, "read_text", fake_read_text)


class TestIsPodmanBug:
    """Test is_podman_bug()."""

    @pytest.mark.parametrize("bug", PODMAN_BUGS)
    def test_true_for_known_bugs(self, bug: str) -> None:
        assert is_podman_bug(err=f"some output\n{bug}\n") is True

    @pytest.mark.parametrize(
        "err",
        ["", "Some other error message", "Error: current boot ID", "cannot re-exec process"],
    )
    def test_false_otherwise(self, err: str) -> None:
        assert is_podman_bug(err=err) is False


class TestPausePidPath:
    """Test pause_pid_path() runtime dir resolution."""

    @pytest.mark.parametrize(
        "xdg_runtime_dir",
        [
            pytest.param("/tmp/fake-xdg-runtime", id="xdg-runtime-dir-set"),
            pytest.param(None, id="fallback-to-run-user-uid"),
        ],
    )
    def test_path(self, monkeypatch: pytest.MonkeyPatch, xdg_runtime_dir: str | None) -> None:
        if xdg_runtime_dir is None:
            monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
            expected = f"/run/user/{os.getuid()}"
        else:
            monkeypatch.setenv("XDG_RUNTIME_DIR", xdg_runtime_dir)
            expected = xdg_runtime_dir
        assert pause_pid_path() == Path(expected, "libpod/tmp/pause.pid")


class TestIsLivePauseProcess:
    """Test is_live_pause_process() against fake /proc entries."""

    def test_live_catatonit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_proc(monkeypatch, "4242", cmdline="catatonit\x00-P\x00", comm="catatonit\n")
        assert is_live_pause_process(pid="4242") is True

    def test_live_process_with_pause_in_cmdline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_proc(monkeypatch, "4242", cmdline="podman\x00pause\x00", comm="podman\n")
        assert is_live_pause_process(pid="4242") is True

    def test_live_non_pause_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_proc(monkeypatch, "4242", cmdline="sleep\x00100\x00", comm="sleep\n")
        assert is_live_pause_process(pid="4242") is False

    def test_dead_process(self) -> None:
        assert is_live_pause_process(pid="0") is False


class TestRepairPodmanState:
    """Test repair_podman_state() removals."""

    def test_namespace_error_removes_only_pid_file(self, mock_rmtree: Mock, pid_path: Path) -> None:
        pid_path.write_text("0")

        removed = repair_podman_state(err=NAMESPACE_REEXEC_ERROR)

        assert removed == [str(pid_path)]
        assert not pid_path.exists()
        mock_rmtree.assert_not_called()

    def test_boot_id_error_removes_storage_dirs_and_pid_file(self, mock_rmtree: Mock, pid_path: Path) -> None:
        pid_path.write_text("0")

        removed = repair_podman_state(err=BOOT_ID_MISMATCH_ERROR)

        assert removed == [*_STORAGE_DIRS, str(pid_path)]
        assert [call.args[0] for call in mock_rmtree.call_args_list] == list(_STORAGE_DIRS)
        mock_rmtree.assert_any_call(_STORAGE_DIRS[0], ignore_errors=True)
        assert not pid_path.exists()

    @pytest.mark.parametrize(
        "cmdline, comm",
        [
            pytest.param("catatonit\x00-P\x00", "catatonit\n", id="catatonit-comm"),
            pytest.param("podman\x00pause\x00", "podman\n", id="pause-cmdline"),
        ],
    )
    def test_keeps_pid_file_for_live_pause_process(
        self, mock_rmtree: Mock, pid_path: Path, monkeypatch: pytest.MonkeyPatch, cmdline: str, comm: str
    ) -> None:
        pid_path.write_text("4242")
        _fake_proc(monkeypatch, "4242", cmdline=cmdline, comm=comm)

        removed = repair_podman_state(err=NAMESPACE_REEXEC_ERROR)

        assert removed == []
        assert pid_path.read_text() == "4242"

    def test_removes_pid_file_for_dead_process(self, mock_rmtree: Mock, pid_path: Path) -> None:
        pid_path.write_text("0")

        assert repair_podman_state(err=NAMESPACE_REEXEC_ERROR) == [str(pid_path)]
        assert not pid_path.exists()

    def test_removes_pid_file_for_live_non_pause_process(
        self, mock_rmtree: Mock, pid_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pid_path.write_text("4242")
        _fake_proc(monkeypatch, "4242", cmdline="sleep\x00100\x00", comm="sleep\n")

        assert repair_podman_state(err=NAMESPACE_REEXEC_ERROR) == [str(pid_path)]
        assert not pid_path.exists()

    @pytest.mark.parametrize("content", ["", "  \n", "\n"])
    def test_removes_empty_pid_file(self, mock_rmtree: Mock, pid_path: Path, content: str) -> None:
        """An empty pid file is stale by definition, matching scripts/podman-cleanup.sh."""
        pid_path.write_text(content)

        assert repair_podman_state(err=NAMESPACE_REEXEC_ERROR) == [str(pid_path)]
        assert not pid_path.exists()
        mock_rmtree.assert_not_called()

    def test_missing_pid_file_is_a_noop(self, mock_rmtree: Mock, pid_path: Path) -> None:
        assert repair_podman_state(err=NAMESPACE_REEXEC_ERROR) == []
        mock_rmtree.assert_not_called()

    def test_oserror_does_not_escape(self, pid_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An OSError from unlink/rmtree is swallowed: repair returns what it did remove."""
        pid_path.write_text("0")

        def failing_rmtree(path: str, ignore_errors: bool = False) -> None:
            if not ignore_errors:
                raise OSError(f"cannot remove {path}")

        def failing_unlink(self: Path, **kwargs: Any) -> None:
            raise OSError(f"cannot unlink {self}")

        monkeypatch.setattr(shutil, "rmtree", failing_rmtree)
        monkeypatch.setattr(Path, "unlink", failing_unlink)

        removed = repair_podman_state(err=BOOT_ID_MISMATCH_ERROR)

        assert removed == list(_STORAGE_DIRS)
        assert pid_path.read_text() == "0"
