"""Tests for webhook_server.web.git_tools internal endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from webhook_server.app import FASTAPI_APP
from webhook_server.libs.handlers.runner_handler import _build_git_custom_tools

MOCK_TARGET = "webhook_server.web.git_tools.asyncio.create_subprocess_exec"


class TestGitToolsEndpoint:
    """Test suite for /internal/git-tools/run endpoint."""

    @pytest.fixture
    def client(self) -> TestClient:
        return TestClient(FASTAPI_APP)

    def test_allowed_command_diff(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.return_value = (b"file.py | 2 +-\n", b"")
            process.returncode = 0
            mock_proc.return_value = process

            resp = client.post(
                "/internal/git-tools/run",
                json={"cwd": "/tmp/test-repo", "args": "diff origin/main --stat"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "file.py" in data["output"]

    def test_allowed_command_log(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.return_value = (b"abc1234 feat: add feature\n", b"")
            process.returncode = 0
            mock_proc.return_value = process

            resp = client.post(
                "/internal/git-tools/run",
                json={"cwd": "/tmp/test-repo", "args": "log origin/main..HEAD --oneline"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "feat: add feature" in data["output"]

    def test_allowed_command_show(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.return_value = (b"commit abc1234\nAuthor: test\n", b"")
            process.returncode = 0
            mock_proc.return_value = process

            resp = client.post(
                "/internal/git-tools/run",
                json={"cwd": "/tmp/test-repo", "args": "show HEAD"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_allowed_command_status(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.return_value = (b"On branch main\nnothing to commit\n", b"")
            process.returncode = 0
            mock_proc.return_value = process

            resp = client.post(
                "/internal/git-tools/run",
                json={"cwd": "/tmp/test-repo", "args": "status"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_allowed_command_rev_parse(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.return_value = (b"abc1234def5678\n", b"")
            process.returncode = 0
            mock_proc.return_value = process

            resp = client.post(
                "/internal/git-tools/run",
                json={"cwd": "/tmp/test-repo", "args": "rev-parse HEAD"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_blocked_command_push(self, client: TestClient) -> None:
        resp = client.post(
            "/internal/git-tools/run",
            json={"cwd": "/tmp/test-repo", "args": "push origin main"},
        )
        assert resp.status_code == 403
        assert "push" in resp.json()["detail"]
        assert "not allowed" in resp.json()["detail"]

    def test_blocked_command_checkout(self, client: TestClient) -> None:
        resp = client.post(
            "/internal/git-tools/run",
            json={"cwd": "/tmp/test-repo", "args": "checkout main"},
        )
        assert resp.status_code == 403

    def test_blocked_command_reset(self, client: TestClient) -> None:
        resp = client.post(
            "/internal/git-tools/run",
            json={"cwd": "/tmp/test-repo", "args": "reset --hard HEAD~1"},
        )
        assert resp.status_code == 403

    def test_blocked_command_rm(self, client: TestClient) -> None:
        resp = client.post(
            "/internal/git-tools/run",
            json={"cwd": "/tmp/test-repo", "args": "rm file.py"},
        )
        assert resp.status_code == 403

    def test_empty_args(self, client: TestClient) -> None:
        resp = client.post(
            "/internal/git-tools/run",
            json={"cwd": "/tmp/test-repo", "args": ""},
        )
        assert resp.status_code == 400
        assert "Empty" in resp.json()["detail"]

    def test_git_command_failure_returns_stderr(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.return_value = (b"", b"fatal: not a git repository\n")
            process.returncode = 128
            mock_proc.return_value = process

            resp = client.post(
                "/internal/git-tools/run",
                json={"cwd": "/tmp/not-a-repo", "args": "diff HEAD"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "not a git repository" in data["output"]

    def test_timeout_kills_process(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.side_effect = TimeoutError()
            process.kill = Mock()
            process.wait = AsyncMock()
            mock_proc.return_value = process

            resp = client.post(
                "/internal/git-tools/run",
                json={"cwd": "/tmp/test-repo", "args": "diff HEAD"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "timed out" in data["output"]
        process.kill.assert_called_once()

    def test_output_capped_at_50k(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            large_output = b"x" * 100_000
            process.communicate.return_value = (large_output, b"")
            process.returncode = 0
            mock_proc.return_value = process

            resp = client.post(
                "/internal/git-tools/run",
                json={"cwd": "/tmp/test-repo", "args": "diff HEAD"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["output"]) == 50_000

    def test_oserror_exception(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            mock_proc.side_effect = OSError("No such file or directory")

            resp = client.post(
                "/internal/git-tools/run",
                json={"cwd": "/tmp/test-repo", "args": "diff HEAD"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "No such file" in data["output"]

    def test_cwd_passed_as_exec_arg(self, client: TestClient) -> None:
        """Verify cwd with spaces is passed as a separate exec argument (no shell quoting needed)."""
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.return_value = (b"ok\n", b"")
            process.returncode = 0
            mock_proc.return_value = process

            resp = client.post(
                "/internal/git-tools/run",
                json={"cwd": "/tmp/path with spaces/repo", "args": "status"},
            )

        assert resp.status_code == 200
        # Verify cwd is passed as a separate argument (no shell quoting)
        call_args = mock_proc.call_args[0]
        assert call_args == ("git", "-C", "/tmp/path with spaces/repo", "status")


class TestBuildGitCustomTools:
    """Test suite for _build_git_custom_tools helper."""

    def test_builds_four_tools(self) -> None:
        tools = _build_git_custom_tools("/tmp/wt")
        assert len(tools) == 4
        names = [t["name"] for t in tools]
        assert names == ["git_diff", "git_log", "git_show", "git_status"]

    def test_tool_structure(self) -> None:
        tools = _build_git_custom_tools("/tmp/my-worktree")
        tool = tools[0]  # git_diff
        assert tool["name"] == "git_diff"
        assert "description" in tool
        assert tool["parameters"]["type"] == "object"
        assert "args" in tool["parameters"]["properties"]
        assert tool["parameters"]["required"] == ["args"]
        assert tool["http"]["method"] == "POST"
        assert tool["http"]["url"] == "http://127.0.0.1:5000/internal/git-tools/run"
        assert tool["http"]["body_template"]["cwd"] == "/tmp/my-worktree"
        assert "diff" in tool["http"]["body_template"]["args"]

    def test_custom_server_port(self) -> None:
        tools = _build_git_custom_tools("/tmp/wt", server_port=8080)
        assert tools[0]["http"]["url"] == "http://127.0.0.1:8080/internal/git-tools/run"

    def test_default_port(self) -> None:
        tools = _build_git_custom_tools("/tmp/wt")
        assert tools[0]["http"]["url"] == "http://127.0.0.1:5000/internal/git-tools/run"

    def test_worktree_path_in_body(self) -> None:
        tools = _build_git_custom_tools("/data/worktrees/abc123")
        for tool in tools:
            assert tool["http"]["body_template"]["cwd"] == "/data/worktrees/abc123"
