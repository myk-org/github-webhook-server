import asyncio
import json
import os
import subprocess
import sys
import types
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from starlette.types import Message

from webhook_server import app as app_module
from webhook_server.app import MCPASGIHandler, mcp_asgi_handler

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFESTS_DIR = REPO_ROOT / "webhook_server" / "tests" / "manifests"
EXPECTED_MCP_TOOLS = {
    "healthcheck",
    "get_log_entries",
    "export_logs",
    "get_pr_flow_data",
    "get_workflow_steps",
    "get_step_logs",
}
MCP_REQUEST_HEADERS = {
    "Host": "webhook-server.internal:5000",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


@pytest.fixture(autouse=True)
def restore_mcp_globals() -> Iterator[None]:
    """Keep optional MCP globals and app singletons from leaking across tests."""
    app_module._log_viewer_controller_singleton = None
    yield
    app_module.mcp = None
    app_module.mcp_session_manager = None
    app_module._mcp_transport_security_cls = None
    app_module._log_viewer_controller_singleton = None


def _run_app_import_subprocess(enable_mcp_server: str | None, extra_code: str = "") -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if enable_mcp_server is None:
        env.pop("ENABLE_MCP_SERVER", None)
    else:
        env["ENABLE_MCP_SERVER"] = enable_mcp_server
    env["WEBHOOK_SERVER_DATA_DIR"] = str(MANIFESTS_DIR)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT), env.get("PYTHONPATH", "")])
    sidecar_level = env.get("PI_SIDECAR_LOG_LEVEL")
    if sidecar_level:
        env["PI_SIDECAR_LOG_LEVEL"] = sidecar_level.upper()

    script = f"""
import os
import sys

{extra_code}

import webhook_server.app as app

assert app.FASTAPI_APP is not None
print("FASTAPI_APP_OK")
print("mcp=" + str("mcp" in sys.modules))
print("mcpserver=" + str("mcp.server.mcpserver" in sys.modules))
print("streamable=" + str("mcp.server.streamable_http_manager" in sys.modules))
"""
    return subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("enable_mcp_server", [None, "false"])
def test_import_does_not_load_mcp_when_disabled(enable_mcp_server: str | None) -> None:
    """ENABLE_MCP_SERVER unset/false must not import mcp, mcpserver, or streamable_http_manager."""
    result = _run_app_import_subprocess(enable_mcp_server)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FASTAPI_APP_OK" in result.stdout
    assert "mcp=False" in result.stdout
    assert "mcpserver=False" in result.stdout
    assert "streamable=False" in result.stdout


def test_mcp_route_registered_and_returns_500_when_init_fails() -> None:
    """ENABLE_MCP_SERVER=true with MCPServer failure: app loads, /mcp is 500 not 404."""
    extra_code = """
import types
import sys

mcp_pkg = types.ModuleType("mcp")
mcp_server_pkg = types.ModuleType("mcp.server")
mcpserver_mod = types.ModuleType("mcp.server.mcpserver")
transport_security_mod = types.ModuleType("mcp.server.transport_security")

class MCPServer:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("MCPServer init boom")

class TransportSecuritySettings:
    def __init__(self, *args, **kwargs):
        pass

mcpserver_mod.MCPServer = MCPServer
transport_security_mod.TransportSecuritySettings = TransportSecuritySettings
mcp_pkg.server = mcp_server_pkg
mcp_server_pkg.mcpserver = mcpserver_mod
mcp_server_pkg.transport_security = transport_security_mod

sys.modules["mcp"] = mcp_pkg
sys.modules["mcp.server"] = mcp_server_pkg
sys.modules["mcp.server.mcpserver"] = mcpserver_mod
sys.modules["mcp.server.transport_security"] = transport_security_mod
"""
    env = os.environ.copy()
    env["ENABLE_MCP_SERVER"] = "true"
    env["WEBHOOK_SERVER_DATA_DIR"] = str(MANIFESTS_DIR)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT), env.get("PYTHONPATH", "")])
    sidecar_level = env.get("PI_SIDECAR_LOG_LEVEL")
    if sidecar_level:
        env["PI_SIDECAR_LOG_LEVEL"] = sidecar_level.upper()

    script = f"""
{extra_code}
from fastapi.testclient import TestClient
import webhook_server.app as app

assert app.FASTAPI_APP is not None
assert app.mcp is None
assert app.mcp_session_manager is None
paths = [getattr(route, "path", None) for route in app.FASTAPI_APP.routes]
assert "/mcp" in paths, paths

with TestClient(app.FASTAPI_APP) as client:
    health = client.get("/webhook_server/healthcheck")
    assert health.status_code == 200, health.text
    mcp_response = client.post("/mcp", follow_redirects=False)
    assert mcp_response.status_code == 500, mcp_response.text
    assert mcp_response.json() == {{"detail": "MCP server not initialized"}}
print("OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def _install_fake_mcp_modules(
    *,
    mcp_server_init_error: BaseException | None = None,
    tool_register_error: BaseException | None = None,
) -> dict[str, types.ModuleType]:
    mcp_pkg = types.ModuleType("mcp")
    mcp_server_pkg = types.ModuleType("mcp.server")
    mcpserver_mod = types.ModuleType("mcp.server.mcpserver")
    transport_security_mod = types.ModuleType("mcp.server.transport_security")

    class FakeMCPServer:
        def __init__(self, name: str) -> None:
            if mcp_server_init_error is not None:
                raise mcp_server_init_error
            self.name = name
            self.registered_tools: dict[str, Callable[..., Any]] = {}

        def tool(
            self,
            *,
            name: str,
            description: str = "",
        ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
            if tool_register_error is not None:
                raise tool_register_error

            def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
                self.registered_tools[name] = fn
                return fn

            return _decorator

    @dataclass
    class FakeTransportSecuritySettings:
        enable_dns_rebinding_protection: bool = True

    mcpserver_any = cast(Any, mcpserver_mod)
    mcpserver_any.MCPServer = FakeMCPServer
    transport_security_any = cast(Any, transport_security_mod)
    transport_security_any.TransportSecuritySettings = FakeTransportSecuritySettings

    return {
        "mcp": mcp_pkg,
        "mcp.server": mcp_server_pkg,
        "mcp.server.mcpserver": mcpserver_mod,
        "mcp.server.transport_security": transport_security_mod,
    }


def test_initialize_mcp_success_assigns_globals() -> None:
    modules = _install_fake_mcp_modules()
    with patch.dict(sys.modules, modules):
        app_module._initialize_mcp()

    assert app_module.mcp is not None
    assert set(app_module.mcp.registered_tools.keys()) == EXPECTED_MCP_TOOLS
    transport_mod = cast(Any, modules["mcp.server.transport_security"])
    assert app_module._mcp_transport_security_cls is transport_mod.TransportSecuritySettings


def test_initialize_mcp_failure_does_not_raise() -> None:
    app_module.mcp = MagicMock()
    app_module.mcp_session_manager = MagicMock()
    app_module._mcp_transport_security_cls = MagicMock()

    modules = _install_fake_mcp_modules(mcp_server_init_error=RuntimeError("MCPServer init boom"))
    with patch.dict(sys.modules, modules):
        app_module._initialize_mcp()

    assert app_module.mcp is None
    assert app_module.mcp_session_manager is None
    assert app_module._mcp_transport_security_cls is None


def test_initialize_mcp_tool_registration_failure_clears_globals() -> None:
    """Tool-registration failure after MCPServer instantiation must clear all MCP globals."""
    app_module.mcp = MagicMock()
    app_module.mcp_session_manager = MagicMock()
    app_module._mcp_transport_security_cls = MagicMock()

    modules = _install_fake_mcp_modules(tool_register_error=RuntimeError("tool registration boom"))
    with patch.dict(sys.modules, modules):
        app_module._initialize_mcp()

    assert app_module.mcp is None
    assert app_module.mcp_session_manager is None
    assert app_module._mcp_transport_security_cls is None


def test_initialize_mcp_import_error_does_not_raise() -> None:
    app_module.mcp = MagicMock()
    app_module.mcp_session_manager = MagicMock()
    app_module._mcp_transport_security_cls = MagicMock()

    with patch(
        "webhook_server.app.importlib.import_module",
        side_effect=ImportError("simulated mcp import failure"),
    ):
        app_module._initialize_mcp()

    assert app_module.mcp is None
    assert app_module.mcp_session_manager is None
    assert app_module._mcp_transport_security_cls is None


def test_initialize_mcp_reraises_cancelled_error() -> None:
    modules = _install_fake_mcp_modules(mcp_server_init_error=asyncio.CancelledError())
    with patch.dict(sys.modules, modules):
        with pytest.raises(asyncio.CancelledError):
            app_module._initialize_mcp()


@pytest.mark.asyncio
async def test_mcp_asgi_handler_returns_500_when_session_manager_missing() -> None:
    handler = MCPASGIHandler()
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(dict(message))

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": [],
    }

    with patch("webhook_server.app.mcp_session_manager", None):
        await handler(scope, receive, send)

    start_msg = next(msg for msg in sent if msg["type"] == "http.response.start")
    body = b"".join(msg.get("body", b"") for msg in sent if msg["type"] == "http.response.body")
    assert start_msg["status"] == 500
    assert json.loads(body.decode("utf-8")) == {"detail": "MCP server not initialized"}


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_mcp_route_returns_500_without_redirect_when_session_manager_missing(method: str) -> None:
    tmp_app = FastAPI()
    tmp_app.add_route(
        "/mcp",
        mcp_asgi_handler,
        methods=["GET", "POST", "DELETE"],
        include_in_schema=False,
    )
    with patch("webhook_server.app.mcp_session_manager", None):
        with TestClient(tmp_app) as client:
            response = client.request(method, "/mcp", follow_redirects=False)

    assert response.status_code == 500
    assert response.json() == {"detail": "MCP server not initialized"}


@pytest.mark.asyncio
async def test_mcp_asgi_handler_delegates_to_session_manager() -> None:
    mock_session_manager = MagicMock()
    mock_session_manager.handle_request = AsyncMock()
    scope: dict[str, Any] = {"type": "http", "method": "POST", "path": "/mcp"}
    receive = AsyncMock()
    send = AsyncMock()

    with patch("webhook_server.app.mcp_session_manager", mock_session_manager):
        await mcp_asgi_handler(scope, receive, send)

    mock_session_manager.handle_request.assert_awaited_once_with(scope, receive, send)


def _create_real_mcp_app() -> FastAPI:
    app_module._initialize_mcp()
    assert app_module.mcp is not None
    tmp_app = FastAPI(lifespan=app_module.lifespan)
    tmp_app.add_route(
        "/mcp",
        mcp_asgi_handler,
        methods=["GET", "POST", "DELETE"],
        include_in_schema=False,
    )
    return tmp_app


def _mcp_rpc(client: TestClient, method: str, params: dict[str, Any] | None = None, req_id: int = 1) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        payload["params"] = params
    response = client.post(
        "/mcp",
        headers=MCP_REQUEST_HEADERS,
        json=payload,
        follow_redirects=False,
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def test_real_mcp_initialize_tools_list_and_healthcheck() -> None:
    """Real MCP v2 server accepts non-localhost Host on /mcp without redirect and exposes exact 6 tools."""
    tmp_app = _create_real_mcp_app()
    mock_file_logger = MagicMock()
    mock_file_logger.handlers = []

    with (
        patch("webhook_server.app.MCP_SERVER_ENABLED", True),
        patch("webhook_server.app.Config") as mock_config,
        patch("webhook_server.app.get_logger_with_params", return_value=mock_file_logger),
        patch("httpx.AsyncClient", return_value=AsyncMock()),
    ):
        mock_config.return_value.root_data = {"verify-github-ips": False, "verify-cloudflare-ips": False}

        with TestClient(tmp_app) as client:
            init_resp = _mcp_rpc(
                client,
                "initialize",
                params={
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest-client", "version": "1.0.0"},
                },
                req_id=1,
            )
            assert init_resp["result"]["serverInfo"]["name"] == "webhook-server"

            list_resp = _mcp_rpc(client, "tools/list", req_id=2)
            tool_names = {tool["name"] for tool in list_resp["result"]["tools"]}
            assert tool_names == EXPECTED_MCP_TOOLS
            assert "process_webhook" not in tool_names
            assert "get_log_viewer_page" not in tool_names

            health_resp = _mcp_rpc(
                client,
                "tools/call",
                params={"name": "healthcheck", "arguments": {}},
                req_id=3,
            )
            assert health_resp["result"]["isError"] is False
            assert health_resp["result"]["structuredContent"] == {"status": 200, "message": "Alive"}


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("get_log_entries", {}),
        ("export_logs", {}),
        ("get_pr_flow_data", {"hook_id": "hook-123"}),
        ("get_workflow_steps", {"hook_id": "hook-123"}),
        ("get_step_logs", {"hook_id": "hook-123", "step_name": "clone"}),
    ],
)
def test_real_mcp_log_tools_return_error_when_log_server_disabled(
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    """All 5 MCP log tools return isError=True when LOG_SERVER_ENABLED is False."""
    tmp_app = _create_real_mcp_app()
    mock_file_logger = MagicMock()
    mock_file_logger.handlers = []

    with (
        patch("webhook_server.app.MCP_SERVER_ENABLED", True),
        patch("webhook_server.app.LOG_SERVER_ENABLED", False),
        patch("webhook_server.app.Config") as mock_config,
        patch("webhook_server.app.get_logger_with_params", return_value=mock_file_logger),
        patch("httpx.AsyncClient", return_value=AsyncMock()),
    ):
        mock_config.return_value.root_data = {"verify-github-ips": False, "verify-cloudflare-ips": False}

        with TestClient(tmp_app) as client:
            resp = _mcp_rpc(
                client,
                "tools/call",
                params={"name": tool_name, "arguments": arguments},
                req_id=1,
            )
            assert resp["result"]["isError"] is True


def test_real_mcp_log_tools_succeed_when_log_server_enabled() -> None:
    """All 5 MCP log tools invoke underlying helpers and return expected JSON dicts when enabled."""
    tmp_app = _create_real_mcp_app()
    mock_file_logger = MagicMock()
    mock_file_logger.handlers = []
    mock_controller = MagicMock()
    mock_controller.get_step_logs = AsyncMock(
        return_value={"hook_id": "hook-123", "step_name": "clone", "logs": [{"msg": "cloned"}]}
    )

    async def _chunked_export_stream() -> AsyncIterator[bytes | str | memoryview]:
        yield b'{"exported": '
        yield '[{"hook_id": "hook-123"}]'
        yield memoryview(b', "count": 1}')

    export_streaming_response = StreamingResponse(_chunked_export_stream(), media_type="application/json")

    with (
        patch("webhook_server.app.MCP_SERVER_ENABLED", True),
        patch("webhook_server.app.LOG_SERVER_ENABLED", True),
        patch("webhook_server.app.Config") as mock_config,
        patch("webhook_server.app.get_logger_with_params", return_value=mock_file_logger),
        patch("webhook_server.app.get_log_viewer_controller", return_value=mock_controller),
        patch(
            "webhook_server.app._get_log_entries_core",
            new=AsyncMock(return_value={"entries": [{"hook_id": "hook-123"}], "total": 1}),
        ) as mock_get_entries,
        patch(
            "webhook_server.app._export_logs_core",
            new=AsyncMock(return_value=export_streaming_response),
        ) as mock_export_logs,
        patch(
            "webhook_server.app._get_pr_flow_data_core",
            new=AsyncMock(return_value={"hook_id": "hook-123", "stages": ["opened"]}),
        ) as mock_pr_flow,
        patch(
            "webhook_server.app.get_workflow_steps_core",
            new=AsyncMock(return_value={"hook_id": "hook-123", "steps": ["clone"]}),
        ) as mock_workflow_steps,
        patch("httpx.AsyncClient", return_value=AsyncMock()),
    ):
        mock_config.return_value.root_data = {"verify-github-ips": False, "verify-cloudflare-ips": False}

        with TestClient(tmp_app) as client:
            entries_resp = _mcp_rpc(
                client,
                "tools/call",
                params={"name": "get_log_entries", "arguments": {"hook_id": "hook-123", "limit": 10}},
                req_id=1,
            )
            assert entries_resp["result"]["isError"] is False
            assert entries_resp["result"]["structuredContent"] == {"entries": [{"hook_id": "hook-123"}], "total": 1}
            mock_get_entries.assert_awaited_once()
            assert mock_get_entries.await_args is not None
            assert mock_get_entries.await_args.kwargs["controller"] is mock_controller
            assert mock_get_entries.await_args.kwargs["hook_id"] == "hook-123"
            assert mock_get_entries.await_args.kwargs["limit"] == 10

            export_resp = _mcp_rpc(
                client,
                "tools/call",
                params={"name": "export_logs", "arguments": {"hook_id": "hook-123", "format_type": "json"}},
                req_id=2,
            )
            assert export_resp["result"]["isError"] is False
            assert export_resp["result"]["structuredContent"] == {
                "exported": [{"hook_id": "hook-123"}],
                "count": 1,
            }
            mock_export_logs.assert_awaited_once()
            assert mock_export_logs.await_args is not None
            assert mock_export_logs.await_args.kwargs["controller"] is mock_controller
            assert mock_export_logs.await_args.kwargs["hook_id"] == "hook-123"

            flow_resp = _mcp_rpc(
                client,
                "tools/call",
                params={"name": "get_pr_flow_data", "arguments": {"hook_id": "hook-123"}},
                req_id=3,
            )
            assert flow_resp["result"]["isError"] is False
            assert flow_resp["result"]["structuredContent"] == {"hook_id": "hook-123", "stages": ["opened"]}
            mock_pr_flow.assert_awaited_once_with(controller=mock_controller, hook_id="hook-123")

            steps_resp = _mcp_rpc(
                client,
                "tools/call",
                params={"name": "get_workflow_steps", "arguments": {"hook_id": "hook-123"}},
                req_id=4,
            )
            assert steps_resp["result"]["isError"] is False
            assert steps_resp["result"]["structuredContent"] == {"hook_id": "hook-123", "steps": ["clone"]}
            mock_workflow_steps.assert_awaited_once_with(controller=mock_controller, hook_id="hook-123")

            step_logs_resp = _mcp_rpc(
                client,
                "tools/call",
                params={"name": "get_step_logs", "arguments": {"hook_id": "hook-123", "step_name": "clone"}},
                req_id=5,
            )
            assert step_logs_resp["result"]["isError"] is False
            assert step_logs_resp["result"]["structuredContent"] == {
                "hook_id": "hook-123",
                "step_name": "clone",
                "logs": [{"msg": "cloned"}],
            }
            mock_controller.get_step_logs.assert_awaited_once_with(hook_id="hook-123", step_name="clone")
