from __future__ import annotations

from dataclasses import dataclass

import pytest

from pyflue.mcp import McpStdioServerOptions, connect_mcp_server_stdio


def test_public_api_exports_mcp_connectors():
    import pyflue

    assert pyflue.McpServerOptions
    assert pyflue.connect_mcp_server_stdio is connect_mcp_server_stdio
    assert callable(pyflue.connect_mcp_server)
    assert pyflue.connectMcpServer is pyflue.connect_mcp_server
    assert pyflue.connectMcpServerStdio is pyflue.connect_mcp_server_stdio


@pytest.mark.asyncio
async def test_stdio_mcp_connection_stays_open_for_tool_calls(monkeypatch):
    state = {"transport_closed": False, "session_closed": False}

    @dataclass
    class FakeTool:
        name: str = "echo"
        description: str = "Echo a value"
        inputSchema: dict = None

        def __post_init__(self):
            if self.inputSchema is None:
                self.inputSchema = {"type": "object", "properties": {"value": {"type": "string"}}}

    class FakeListTools:
        tools = [FakeTool()]

    class FakeCallResult:
        content = []
        structuredContent = {"ok": True}

    class FakeSession:
        def __init__(self, _read, _write):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            state["session_closed"] = True

        async def initialize(self):
            pass

        async def list_tools(self):
            return FakeListTools()

        async def call_tool(self, _name, _args, timeout=None):
            assert timeout is None
            assert not state["transport_closed"]
            assert not state["session_closed"]
            return FakeCallResult()

    class FakeParams:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeTransport:
        async def __aenter__(self):
            return object(), object()

        async def __aexit__(self, *_args):
            state["transport_closed"] = True

    def fake_stdio_client(_params, errlog=None):
        assert errlog is not None
        return FakeTransport()

    monkeypatch.setattr(
        "pyflue.mcp._get_mcp_client",
        lambda: (FakeSession, FakeParams, fake_stdio_client),
    )

    conn = await connect_mcp_server_stdio(
        "demo",
        McpStdioServerOptions(command="python", args=["server.py"]),
    )

    assert state == {"transport_closed": False, "session_closed": False}
    assert await conn.tools[0].execute({"value": "hello"}) == 'Structured content:\n{\n  "ok": true\n}'

    await conn.close()

    assert state == {"transport_closed": True, "session_closed": True}
