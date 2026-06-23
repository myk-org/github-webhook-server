"""Tests for webhook_server.web.tool_server standalone server."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from webhook_server.libs.handlers.runner_handler import _build_custom_tools
from webhook_server.web.tool_server import handle_tool_request

MOCK_TARGET = "webhook_server.web.tool_server.asyncio.create_subprocess_exec"


def _create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/tools/run", handle_tool_request)
    return app


@pytest.fixture
async def client() -> TestClient:
    app = _create_app()
    server = TestServer(app)
    _client = TestClient(server)
    await _client.start_server()
    yield _client
    await _client.close()


class TestToolServerEndpoint:
    """Test suite for /tools/run endpoint."""

    @pytest.mark.asyncio
    async def test_git_diff(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.return_value = (b"file.py | 2 +-\n", b"")
            process.returncode = 0
            mock_proc.return_value = process

            resp = await client.post(
                "/tools/run",
                json={"tool": "git_diff", "cwd": "/tmp/test-repo", "args": "origin/main --stat"},
            )

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert "file.py" in data["output"]

    @pytest.mark.asyncio
    async def test_git_log(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.return_value = (b"abc1234 feat: add feature\n", b"")
            process.returncode = 0
            mock_proc.return_value = process

            resp = await client.post(
                "/tools/run",
                json={"tool": "git_log", "cwd": "/tmp/test-repo", "args": "origin/main..HEAD --oneline"},
            )

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert "feat: add feature" in data["output"]

    @pytest.mark.asyncio
    async def test_git_show(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.return_value = (b"commit abc1234\nAuthor: test\n", b"")
            process.returncode = 0
            mock_proc.return_value = process

            resp = await client.post(
                "/tools/run",
                json={"tool": "git_show", "cwd": "/tmp/test-repo", "args": "HEAD"},
            )

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_git_status(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.return_value = (b"On branch main\nnothing to commit\n", b"")
            process.returncode = 0
            mock_proc.return_value = process

            resp = await client.post(
                "/tools/run",
                json={"tool": "git_status", "cwd": "/tmp/test-repo", "args": ""},
            )

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_404(self, client: TestClient) -> None:
        resp = await client.post(
            "/tools/run",
            json={"tool": "git_push", "cwd": "/tmp/test-repo", "args": "origin main"},
        )
        assert resp.status == 404
        data = await resp.json()
        assert "Unknown tool" in data["detail"]
        assert "git_push" in data["detail"]

    @pytest.mark.asyncio
    async def test_missing_tool_field(self, client: TestClient) -> None:
        resp = await client.post(
            "/tools/run",
            json={"cwd": "/tmp/test-repo", "args": "status"},
        )
        assert resp.status == 400
        data = await resp.json()
        assert "Missing 'tool'" in data["detail"]

    @pytest.mark.asyncio
    async def test_missing_cwd_field(self, client: TestClient) -> None:
        resp = await client.post(
            "/tools/run",
            json={"tool": "git_status", "args": ""},
        )
        assert resp.status == 400
        data = await resp.json()
        assert "Missing 'cwd'" in data["detail"]

    @pytest.mark.asyncio
    async def test_blocked_flag_exact(self, client: TestClient) -> None:
        resp = await client.post(
            "/tools/run",
            json={"tool": "git_diff", "cwd": "/tmp/test-repo", "args": "--no-index /a /b"},
        )
        assert resp.status == 403
        data = await resp.json()
        assert "not allowed" in data["detail"]

    @pytest.mark.asyncio
    async def test_blocked_flag_with_equals(self, client: TestClient) -> None:
        resp = await client.post(
            "/tools/run",
            json={"tool": "git_diff", "cwd": "/tmp/test-repo", "args": "--output=/tmp/x HEAD"},
        )
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_unblocked_tool_allows_same_flag(self, client: TestClient) -> None:
        """git_log has no blocked flags — --raw should be allowed."""
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.return_value = (b"output\n", b"")
            process.returncode = 0
            mock_proc.return_value = process

            resp = await client.post(
                "/tools/run",
                json={"tool": "git_log", "cwd": "/tmp/test-repo", "args": "--raw -5"},
            )

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_command_failure_returns_stderr(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.return_value = (b"", b"fatal: not a git repository\n")
            process.returncode = 128
            mock_proc.return_value = process

            resp = await client.post(
                "/tools/run",
                json={"tool": "git_diff", "cwd": "/tmp/not-a-repo", "args": "HEAD"},
            )

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is False
        assert "not a git repository" in data["output"]

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.side_effect = TimeoutError()
            process.kill = Mock()
            process.wait = AsyncMock()
            mock_proc.return_value = process

            resp = await client.post(
                "/tools/run",
                json={"tool": "git_diff", "cwd": "/tmp/test-repo", "args": "HEAD"},
            )

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is False
        assert "timed out" in data["output"]
        process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_output_capped_at_50k(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            large_output = b"x" * 100_000
            process.communicate.return_value = (large_output, b"")
            process.returncode = 0
            mock_proc.return_value = process

            resp = await client.post(
                "/tools/run",
                json={"tool": "git_diff", "cwd": "/tmp/test-repo", "args": "HEAD"},
            )

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert len(data["output"]) == 50_000

    @pytest.mark.asyncio
    async def test_oserror_exception(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            mock_proc.side_effect = OSError("No such file or directory")

            resp = await client.post(
                "/tools/run",
                json={"tool": "git_diff", "cwd": "/tmp/test-repo", "args": "HEAD"},
            )

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is False
        assert "No such file" in data["output"]

    @pytest.mark.asyncio
    async def test_cwd_substituted_in_command(self, client: TestClient) -> None:
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.return_value = (b"ok\n", b"")
            process.returncode = 0
            mock_proc.return_value = process

            resp = await client.post(
                "/tools/run",
                json={"tool": "git_status", "cwd": "/tmp/path with spaces/repo", "args": ""},
            )

        assert resp.status == 200
        call_args = mock_proc.call_args[0]
        assert call_args == ("git", "-C", "/tmp/path with spaces/repo", "status")

    @pytest.mark.asyncio
    async def test_diff_exit_code_1_is_success(self, client: TestClient) -> None:
        """git diff returns exit code 1 when differences exist — should be success."""
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.return_value = (b"file.py | 2 +-\n", b"")
            process.returncode = 1
            mock_proc.return_value = process

            resp = await client.post(
                "/tools/run",
                json={"tool": "git_diff", "cwd": "/tmp/test-repo", "args": "HEAD"},
            )

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_log_exit_code_1_is_failure(self, client: TestClient) -> None:
        """git log exit code 1 is a real error — should be failure."""
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.return_value = (b"", b"fatal: bad default revision\n")
            process.returncode = 1
            mock_proc.return_value = process

            resp = await client.post(
                "/tools/run",
                json={"tool": "git_log", "cwd": "/tmp/test-repo", "args": "HEAD"},
            )

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is False

    @pytest.mark.asyncio
    async def test_custom_timeout_override(self, client: TestClient) -> None:
        """Caller can override the default timeout."""
        with patch(MOCK_TARGET) as mock_proc:
            process = AsyncMock()
            process.communicate.side_effect = TimeoutError()
            process.kill = Mock()
            process.wait = AsyncMock()
            mock_proc.return_value = process

            resp = await client.post(
                "/tools/run",
                json={"tool": "git_diff", "cwd": "/tmp/test-repo", "args": "HEAD", "timeout": 5},
            )

        assert resp.status == 200
        data = await resp.json()
        assert "timed out after 5s" in data["output"]


class TestBuildCustomTools:
    """Test suite for _build_custom_tools helper."""

    def test_builds_selected_tools(self) -> None:
        tools = _build_custom_tools("/tmp/wt", ["git_diff", "git_log"])
        assert len(tools) == 2
        names = [t["name"] for t in tools]
        assert names == ["git_diff", "git_log"]

    def test_builds_all_four_tools(self) -> None:
        tools = _build_custom_tools("/tmp/wt", ["git_diff", "git_log", "git_show", "git_status"])
        assert len(tools) == 4

    def test_tool_structure(self) -> None:
        tools = _build_custom_tools("/tmp/my-worktree", ["git_diff"])
        tool = tools[0]
        assert tool["name"] == "git_diff"
        assert "description" in tool
        assert tool["parameters"]["type"] == "object"
        assert "args" in tool["parameters"]["properties"]
        assert tool["parameters"]["required"] == ["args"]
        assert tool["http"]["method"] == "POST"
        assert tool["http"]["url"] == "http://127.0.0.1:5001/tools/run"
        assert tool["http"]["body_template"]["tool"] == "git_diff"
        assert tool["http"]["body_template"]["cwd"] == "/tmp/my-worktree"
        assert tool["http"]["body_template"]["args"] == "{args}"

    def test_custom_server_port(self) -> None:
        tools = _build_custom_tools("/tmp/wt", ["git_diff"], server_port=8080)
        assert tools[0]["http"]["url"] == "http://127.0.0.1:8080/tools/run"

    def test_unknown_tool_raises_keyerror(self) -> None:
        with pytest.raises(KeyError):
            _build_custom_tools("/tmp/wt", ["nonexistent_tool"])

    def test_worktree_path_in_body(self) -> None:
        tools = _build_custom_tools("/data/worktrees/abc123", ["git_log", "git_status"])
        for tool in tools:
            assert tool["http"]["body_template"]["cwd"] == "/data/worktrees/abc123"
