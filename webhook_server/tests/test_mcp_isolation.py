import asyncio
import os
import subprocess
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from webhook_server import app as app_module
from webhook_server.app import FASTAPI_APP, handle_mcp_streamable_http

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFESTS_DIR = REPO_ROOT / "webhook_server" / "tests" / "manifests"


@pytest.fixture(autouse=True)
def restore_mcp_globals() -> Iterator[None]:
    """Keep optional MCP globals from leaking into other tests."""
    yield
    app_module.mcp = None
    app_module.http_transport = None
    app_module.StreamableHTTPSessionManager = None


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
print("fastapi_mcp=" + str("fastapi_mcp" in sys.modules))
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
    """ENABLE_MCP_SERVER unset/false must not import fastapi_mcp or streamable_http_manager."""
    result = _run_app_import_subprocess(enable_mcp_server)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FASTAPI_APP_OK" in result.stdout
    assert "fastapi_mcp=False" in result.stdout
    assert "streamable=False" in result.stdout


def test_mcp_route_registered_and_returns_500_when_init_fails() -> None:
    """ENABLE_MCP_SERVER=true with FastApiMCP failure: app loads, /mcp is 500 not 404."""
    extra_code = """
import types
import sys

fastapi_mcp = types.ModuleType("fastapi_mcp")

class FastApiMCP:
    def __init__(self, *args, **kwargs):
        raise TypeError("Server.__init__() takes 2 positional arguments but 3 were given")

fastapi_mcp.FastApiMCP = FastApiMCP

transport_pkg = types.ModuleType("fastapi_mcp.transport")
http_mod = types.ModuleType("fastapi_mcp.transport.http")

class FastApiHttpSessionManager:
    def __init__(self, *args, **kwargs):
        pass

http_mod.FastApiHttpSessionManager = FastApiHttpSessionManager
fastapi_mcp.transport = transport_pkg

sys.modules["fastapi_mcp"] = fastapi_mcp
sys.modules["fastapi_mcp.transport"] = transport_pkg
sys.modules["fastapi_mcp.transport.http"] = http_mod
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
assert app.http_transport is None
paths = [getattr(route, "path", None) for route in app.FASTAPI_APP.routes]
assert "/mcp" in paths, paths

with TestClient(app.FASTAPI_APP) as client:
    health = client.get("/webhook_server/healthcheck")
    assert health.status_code == 200, health.text
    mcp_response = client.post("/mcp")
    assert mcp_response.status_code == 500, mcp_response.text
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
    fastapi_mcp_init_error: BaseException | None = None,
) -> dict[str, types.ModuleType]:
    fastapi_mcp = types.ModuleType("fastapi_mcp")

    class FastApiMCP:
        def __init__(self, app: object, exclude_tags: list[str] | None = None) -> None:
            if fastapi_mcp_init_error is not None:
                raise fastapi_mcp_init_error
            self.server = MagicMock()
            self.exclude_tags = exclude_tags

    fastapi_mcp_any = cast(Any, fastapi_mcp)
    fastapi_mcp_any.FastApiMCP = FastApiMCP

    transport_pkg = types.ModuleType("fastapi_mcp.transport")
    http_mod = types.ModuleType("fastapi_mcp.transport.http")

    class FastApiHttpSessionManager:
        def __init__(self, mcp_server: object, event_store: object, json_response: bool) -> None:
            self.mcp_server = mcp_server
            self.event_store = event_store
            self.json_response = json_response
            self._session_manager = "unset"

    http_mod_any = cast(Any, http_mod)
    http_mod_any.FastApiHttpSessionManager = FastApiHttpSessionManager
    fastapi_mcp_any.transport = transport_pkg

    mcp_pkg = types.ModuleType("mcp")
    mcp_server = types.ModuleType("mcp.server")
    streamable = types.ModuleType("mcp.server.streamable_http_manager")

    class StreamableHTTPSessionManager:
        pass

    streamable_any = cast(Any, streamable)
    streamable_any.StreamableHTTPSessionManager = StreamableHTTPSessionManager

    return {
        "fastapi_mcp": fastapi_mcp,
        "fastapi_mcp.transport": transport_pkg,
        "fastapi_mcp.transport.http": http_mod,
        "mcp": mcp_pkg,
        "mcp.server": mcp_server,
        "mcp.server.streamable_http_manager": streamable,
    }


def test_initialize_mcp_success_assigns_globals() -> None:
    modules = _install_fake_mcp_modules()
    with patch.dict(sys.modules, modules):
        app_module._initialize_mcp(FASTAPI_APP)

    assert app_module.mcp is not None
    assert app_module.http_transport is not None
    assert app_module.http_transport._session_manager is None
    streamable_mod = cast(Any, modules["mcp.server.streamable_http_manager"])
    assert app_module.StreamableHTTPSessionManager is streamable_mod.StreamableHTTPSessionManager


def test_initialize_mcp_failure_does_not_raise() -> None:
    modules = _install_fake_mcp_modules(
        fastapi_mcp_init_error=TypeError("Server.__init__() takes 2 positional arguments but 3 were given")
    )
    with patch.dict(sys.modules, modules):
        app_module._initialize_mcp(FASTAPI_APP)

    assert app_module.mcp is None
    assert app_module.http_transport is None
    assert app_module.StreamableHTTPSessionManager is None


def test_initialize_mcp_clears_session_manager_class_after_fastapi_mcp_failure() -> None:
    """Import success then FastApiMCP failure must clear StreamableHTTPSessionManager."""
    modules = _install_fake_mcp_modules()
    streamable_cls = cast(Any, modules["mcp.server.streamable_http_manager"]).StreamableHTTPSessionManager
    seen_assigned: list[object] = []

    class BoomFastApiMCP:
        def __init__(self, app: object, exclude_tags: list[str] | None = None) -> None:
            seen_assigned.append(app_module.StreamableHTTPSessionManager)
            raise TypeError("boom after successful import")

    cast(Any, modules["fastapi_mcp"]).FastApiMCP = BoomFastApiMCP

    with patch.dict(sys.modules, modules):
        app_module._initialize_mcp(FASTAPI_APP)

    assert seen_assigned == [streamable_cls]
    assert app_module.StreamableHTTPSessionManager is None
    assert app_module.mcp is None
    assert app_module.http_transport is None


def test_initialize_mcp_import_error_does_not_raise() -> None:
    with patch(
        "webhook_server.app.importlib.import_module",
        side_effect=ImportError("simulated fastapi_mcp import failure"),
    ):
        app_module._initialize_mcp(FASTAPI_APP)

    assert app_module.mcp is None
    assert app_module.http_transport is None
    assert app_module.StreamableHTTPSessionManager is None


def test_initialize_mcp_reraises_cancelled_error() -> None:
    modules = _install_fake_mcp_modules(fastapi_mcp_init_error=asyncio.CancelledError())
    with patch.dict(sys.modules, modules):
        with pytest.raises(asyncio.CancelledError):
            app_module._initialize_mcp(FASTAPI_APP)


@pytest.mark.asyncio
async def test_mcp_handler_returns_500_when_transport_missing() -> None:
    with patch("webhook_server.app.http_transport", None):
        with pytest.raises(HTTPException) as exc_info:
            await handle_mcp_streamable_http(MagicMock())
    assert exc_info.value.status_code == 500
    assert "MCP server not initialized" in exc_info.value.detail


@pytest.mark.asyncio
async def test_mcp_route_returns_500_when_transport_missing() -> None:
    tmp_app = FastAPI()
    tmp_app.add_api_route(
        "/mcp",
        handle_mcp_streamable_http,
        methods=["GET", "POST", "DELETE"],
    )
    with patch("webhook_server.app.http_transport", None):
        with TestClient(tmp_app) as client:
            response = client.get("/mcp")
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_mcp_handler_delegates_to_transport() -> None:
    mock_transport = MagicMock()
    mock_transport._session_manager = MagicMock()
    expected: Any = MagicMock()
    mock_transport.handle_fastapi_request = AsyncMock(return_value=expected)
    request: Any = MagicMock()
    with patch("webhook_server.app.http_transport", mock_transport):
        result = await handle_mcp_streamable_http(request)
    assert result is expected
    mock_transport.handle_fastapi_request.assert_awaited_once_with(request)
