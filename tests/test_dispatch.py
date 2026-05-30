"""Tests for dispatch() async agent input (parity item 5)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from pyflue import (
    DispatchQueue,
    DispatchReceipt,
    create_agent,
    dispatch,
    get_default_dispatch_queue,
)
from pyflue.harnesses.base import HarnessBackend
from pyflue.server import create_app
from pyflue.types import HarnessResult, PromptModel, PromptUsage


class _FakeBackend(HarnessBackend):
    name = "fake"

    def __init__(self):
        self.calls: list[dict] = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return HarnessResult(
            text="ok",
            raw=SimpleNamespace(),
            metadata={"harness": "fake"},
            usage=PromptUsage(total_tokens=1),
            model=PromptModel(id=kwargs.get("config").model),
        )


def _agent():
    return create_agent(lambda ctx: {"model": "fake-model"})


# ── validation ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_rejects_non_created_agent():
    with pytest.raises(ValueError, match="created agent"):
        await dispatch(lambda: {}, id="x", input={})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_dispatch_requires_non_empty_id():
    with pytest.raises(ValueError, match='"id"'):
        await dispatch(_agent(), id="", input={})


@pytest.mark.asyncio
async def test_dispatch_rejects_non_serializable_input():
    with pytest.raises(ValueError, match="JSON-serializable"):
        await dispatch(_agent(), id="inst", input=lambda: None)


@pytest.mark.asyncio
async def test_dispatch_rejects_empty_session():
    with pytest.raises(ValueError, match="session"):
        await dispatch(_agent(), id="inst", session="  ", input={})


# ── delivery ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_returns_receipt_and_delivers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake = _FakeBackend()
    monkeypatch.setattr("pyflue.core.create_backend", lambda harness: fake)

    queue = DispatchQueue()
    receipt = await dispatch(
        _agent(),
        id="inst-1",
        session="thread-9",
        input={"type": "chat.message", "text": "hello there"},
        queue=queue,
    )

    assert isinstance(receipt, DispatchReceipt)
    assert receipt.dispatch_id.startswith("dispatch_")
    assert receipt.accepted_at  # ISO timestamp

    await queue.join()  # wait for async delivery

    assert len(fake.calls) == 1
    assert fake.calls[0]["session_id"] == "inst-1:thread-9"
    assert "hello there" in fake.calls[0]["prompt"]  # input delivered as the prompt


@pytest.mark.asyncio
async def test_dispatch_string_input_delivered_verbatim(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake = _FakeBackend()
    monkeypatch.setattr("pyflue.core.create_backend", lambda harness: fake)

    queue = DispatchQueue()
    await dispatch(_agent(), id="inst", input="just text", queue=queue)
    await queue.join()

    assert fake.calls[0]["session_id"] == "inst:default"
    assert "just text" in fake.calls[0]["prompt"]


# ── server endpoint ─────────────────────────────────────────────────────────────


def _persistent_project(tmp_path):
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "assistant.py").write_text(
        "from pyflue import create_agent\n\n"
        "default = create_agent(lambda ctx: {'model': 'fake-model'})\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_server_dispatch_endpoint_accepts_and_delivers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _persistent_project(tmp_path)
    fake = _FakeBackend()
    monkeypatch.setattr("pyflue.core.create_backend", lambda harness: fake)

    app = create_app(tmp_path / "pyflue.toml")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/agents/assistant/inst-1/dispatch",
            json={"input": {"text": "ping"}, "session": "s1"},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "accepted"
        assert body["dispatch_id"].startswith("dispatch_")
        await get_default_dispatch_queue().join()

    assert any(call["session_id"] == "inst-1:s1" for call in fake.calls)


@pytest.mark.asyncio
async def test_server_dispatch_rejects_handler_agent(tmp_path):
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "legacy.py").write_text(
        "async def default(context):\n    return {'ok': True}\n", encoding="utf-8"
    )

    app = create_app(tmp_path / "pyflue.toml")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/agents/legacy/x/dispatch", json={"input": {}})

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request"
