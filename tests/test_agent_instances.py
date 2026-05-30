"""Persistent agent instances + session stores (parity item 3)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from pyflue import InMemorySessionStore, SessionStore, SQLiteSessionStore
from pyflue.harnesses.base import HarnessBackend
from pyflue.server import create_app
from pyflue.types import HarnessResult, PromptModel, PromptUsage


class _FakeBackend(HarnessBackend):
    name = "fake"

    def __init__(self, responses=None):
        self.calls: list[dict] = []
        self.responses = responses or ["ok"]

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return HarnessResult(
            text=self.responses[min(len(self.calls) - 1, len(self.responses) - 1)],
            raw=SimpleNamespace(),
            metadata={"harness": "fake"},
            usage=PromptUsage(input=1, output=2, total_tokens=3),
            model=PromptModel(id=kwargs.get("config").model),
        )


def _persistent_agent_project(tmp_path):
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "assistant.py").write_text(
        "from pyflue import create_agent\n\n"
        "default = create_agent(lambda ctx: {'model': 'fake-model'})\n",
        encoding="utf-8",
    )


def _client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# ── session stores ───────────────────────────────────────────────────────────


def test_stores_satisfy_protocol():
    assert isinstance(InMemorySessionStore(), SessionStore)


@pytest.mark.asyncio
async def test_in_memory_session_store_roundtrip():
    store = InMemorySessionStore()
    assert await store.load("s") is None
    await store.save("s", {"messages": [1, 2]})
    assert await store.load("s") == {"messages": [1, 2]}
    # load returns a copy
    loaded = await store.load("s")
    loaded["messages"].append(3)
    assert await store.load("s") == {"messages": [1, 2]}
    await store.delete("s")
    assert await store.load("s") is None


@pytest.mark.asyncio
async def test_sqlite_session_store_roundtrip(tmp_path):
    store = SQLiteSessionStore(tmp_path / "sessions.sqlite3")
    await store.save("inst:default", {"turns": 1})
    await store.save("inst:default", {"turns": 2})  # upsert
    assert await store.load("inst:default") == {"turns": 2}
    assert await store.load("missing") is None
    await store.delete("inst:default")
    assert await store.load("inst:default") is None


# ── persistent agent over HTTP ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persistent_agent_prompt_returns_result_without_run(tmp_path, monkeypatch):
    _persistent_agent_project(tmp_path)
    fake = _FakeBackend(responses=["hello back"])
    monkeypatch.setattr("pyflue.core.create_backend", lambda harness: fake)

    app = create_app(tmp_path / "pyflue.toml")
    async with _client(app) as client:
        response = await client.post("/agents/assistant/inst-1", json={"message": "hi"})

    assert response.status_code == 200
    body = response.json()
    assert body["result"]["text"] == "hello back"
    assert body["result"]["usage"]["total_tokens"] == 3
    # An agent interaction is NOT a workflow run: no run id is surfaced.
    assert "x-flue-run-id" not in {k.lower() for k in response.headers}
    # Conversation state is persisted under an instance-namespaced session.
    assert (tmp_path / ".pyflue" / "sessions" / "inst-1_default.sqlite3").exists()


@pytest.mark.asyncio
async def test_persistent_agent_session_continuity(tmp_path, monkeypatch):
    _persistent_agent_project(tmp_path)
    fake = _FakeBackend(responses=["a1", "a2"])
    monkeypatch.setattr("pyflue.core.create_backend", lambda harness: fake)

    app = create_app(tmp_path / "pyflue.toml")
    async with _client(app) as client:
        await client.post("/agents/assistant/inst-1", json={"message": "first", "session": "billing"})
        await client.post("/agents/assistant/inst-1", json={"message": "second", "session": "billing"})

    # Both prompts target one stable, instance-namespaced session.
    assert fake.calls[0]["session_id"] == "inst-1:billing"
    assert fake.calls[1]["session_id"] == "inst-1:billing"
    # The second turn's built prompt carries the earlier user message (continuity).
    assert "first" in fake.calls[1]["prompt"]
    # The same cached instance served both requests.
    assert ("assistant", "inst-1") in app.state.instance_manager._instances


@pytest.mark.asyncio
async def test_distinct_instances_get_distinct_sessions(tmp_path, monkeypatch):
    _persistent_agent_project(tmp_path)
    fake = _FakeBackend(responses=["x"])
    monkeypatch.setattr("pyflue.core.create_backend", lambda harness: fake)

    app = create_app(tmp_path / "pyflue.toml")
    async with _client(app) as client:
        await client.post("/agents/assistant/alice", json={"message": "hi"})
        await client.post("/agents/assistant/bob", json={"message": "hi"})

    assert fake.calls[0]["session_id"] == "alice:default"
    assert fake.calls[1]["session_id"] == "bob:default"


@pytest.mark.asyncio
async def test_handler_agent_still_creates_run(tmp_path):
    # A file-based handler module (callable default) keeps the run lifecycle.
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "legacy.py").write_text(
        "async def default(context):\n    return {'ok': True}\n", encoding="utf-8"
    )

    app = create_app(tmp_path / "pyflue.toml")
    async with _client(app) as client:
        response = await client.post("/agents/legacy/x", json={"payload": {}})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    # The handler path still surfaces a run id (back-compat until item 4).
    assert "x-flue-run-id" in {k.lower() for k in response.headers}
