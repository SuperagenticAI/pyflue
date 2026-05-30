"""Tests for agent + workflow WebSocket surfaces (parity item 9)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient

from pyflue import PyFlueClient
from pyflue.harnesses.base import HarnessBackend
from pyflue.server import create_app
from pyflue.types import HarnessResult, PromptModel, PromptUsage


class _FakeBackend(HarnessBackend):
    name = "fake"

    async def run(self, **kwargs):
        return HarnessResult(
            text="ok",
            raw=SimpleNamespace(),
            metadata={},
            usage=PromptUsage(total_tokens=1),
            model=PromptModel(id=kwargs.get("config").model),
        )


def _persistent_project(tmp_path):
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "assistant.py").write_text(
        "from pyflue import create_agent\n\n"
        "default = create_agent(lambda ctx: {'model': 'fake-model'})\n",
        encoding="utf-8",
    )


def test_agent_websocket_multi_prompt(tmp_path, monkeypatch):
    _persistent_project(tmp_path)
    monkeypatch.setattr("pyflue.core.create_backend", lambda harness: _FakeBackend())

    app = create_app(tmp_path / "pyflue.toml")
    with TestClient(app) as client:  # noqa: SIM117
        with client.websocket_connect("/agents/assistant/inst-1") as ws:
            ws.send_json({"message": "hi", "session": "s"})
            first = ws.receive_json()
            assert first["type"] == "result"
            assert first["result"]["text"] == "ok"
            # The socket stays open for further prompts to the same instance.
            ws.send_json({"message": "again"})
            assert ws.receive_json()["type"] == "result"


def test_agent_websocket_unknown_agent(tmp_path):
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")
    app = create_app(tmp_path / "pyflue.toml")
    with TestClient(app) as client:  # noqa: SIM117
        with client.websocket_connect("/agents/missing/x") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["error"]["type"] == "not_found"


def test_workflow_websocket_streams_events_and_result(tmp_path):
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "echo.py").write_text(
        "async def run(ctx):\n"
        "    await ctx.log.info('working')\n"
        "    return {'echo': ctx.payload.get('msg', '')}\n",
        encoding="utf-8",
    )

    app = create_app(tmp_path / "pyflue.toml")
    with TestClient(app) as client:  # noqa: SIM117
        with client.websocket_connect("/workflows/echo") as ws:
            ws.send_json({"payload": {"msg": "hi"}})
            messages = []
            while True:
                message = ws.receive_json()
                messages.append(message)
                if message["type"] == "result":
                    break
            assert any(m["type"] == "run_start" for m in messages)
            assert messages[-1]["result"]["echo"] == "hi"


@pytest.mark.asyncio
async def test_client_workflows_invoke_over_http(tmp_path):
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "echo.py").write_text(
        "async def run(ctx):\n    return {'echo': ctx.payload.get('msg', '')}\n",
        encoding="utf-8",
    )
    app = create_app(tmp_path / "pyflue.toml")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        client = PyFlueClient("http://test", client=http)
        result = await client.workflows.invoke("echo", {"msg": "hi"}, wait=True)
        assert result["status"] == "completed"
        assert result["result"]["echo"] == "hi"
        # Accepted (non-wait) mode returns a run id receipt.
        receipt = await client.workflows.invoke("echo", {"msg": "x"})
        assert receipt["status"] == "accepted"
        assert receipt["run_id"].startswith("workflow:echo:")
