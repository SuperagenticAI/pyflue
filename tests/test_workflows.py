"""Tests for workflows + run(ctx) + FlueContext.init(agent) (parity item 2)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from pyflue import FlueContext, PyFlueContext, create_agent
from pyflue.harnesses.base import HarnessBackend
from pyflue.runs import InMemoryRunStore
from pyflue.server import create_app
from pyflue.types import HarnessResult, PromptModel, PromptUsage
from pyflue.workflows import discover_workflows, invoke_workflow


class _FakeBackend(HarnessBackend):
    name = "fake"

    def __init__(self, responses=None):
        self.calls = []
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


def _write_workflow(root, name: str, body: str) -> None:
    wf_dir = root / "workflows"
    wf_dir.mkdir(exist_ok=True)
    (wf_dir / f"{name}.py").write_text(body, encoding="utf-8")


# ── discovery ────────────────────────────────────────────────────────────────


def test_discover_workflows_finds_both_dirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_workflow(tmp_path, "summarize", "def run(ctx):\n    return {}\n")
    nested = tmp_path / ".pyflue" / "workflows"
    nested.mkdir(parents=True)
    (nested / "audit.py").write_text("def run(ctx):\n    return {}\n", encoding="utf-8")
    (tmp_path / "workflows" / "_helper.py").write_text("x = 1\n", encoding="utf-8")

    found = discover_workflows(".")
    assert set(found) == {"summarize", "audit"}  # _helper.py is skipped


def test_discover_workflows_finds_src_layout(tmp_path, monkeypatch):
    # Reference v0.8.x canonical `src/` source layout.
    monkeypatch.chdir(tmp_path)
    src_workflows = tmp_path / "src" / "workflows"
    src_workflows.mkdir(parents=True)
    (src_workflows / "summarize.py").write_text("def run(ctx):\n    return {}\n", encoding="utf-8")
    assert "summarize" in discover_workflows(".")


def test_discover_agents_finds_src_layout(tmp_path, monkeypatch):
    from pyflue import discover_agent_routes

    monkeypatch.chdir(tmp_path)
    src_agents = tmp_path / "src" / "agents"
    src_agents.mkdir(parents=True)
    (src_agents / "assistant.py").write_text(
        "from pyflue import create_agent\n\ndefault = create_agent(lambda ctx: {'model': 'm'})\n",
        encoding="utf-8",
    )
    routes = discover_agent_routes(".")
    assert "assistant" in routes


# ── invoke_workflow lifecycle ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invoke_workflow_lifecycle_and_result(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_workflow(
        tmp_path,
        "double",
        "async def run(ctx):\n"
        "    await ctx.log.info('started', n=ctx.payload.get('n', 0))\n"
        "    return {'doubled': ctx.payload.get('n', 0) * 2, 'run_id': ctx.id}\n",
    )
    store = InMemoryRunStore()
    workflows = discover_workflows(".")
    result = await invoke_workflow(workflows["double"], payload={"n": 21}, run_store=store)

    assert result["doubled"] == 42
    rid = result["run_id"]
    assert rid.startswith("workflow:double:")

    run = store.get_run(rid)
    assert run.status == "succeeded"
    assert run.is_error is False
    assert run.result["doubled"] == 42

    types = [event.type for event in store.get_events(rid)]
    assert types[0] == "run_start"
    assert "log" in types
    assert types[-1] == "run_end"


@pytest.mark.asyncio
async def test_invoke_workflow_records_error_and_propagates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_workflow(tmp_path, "boom", "async def run(ctx):\n    raise RuntimeError('kaboom')\n")
    store = InMemoryRunStore()
    workflow = discover_workflows(".")["boom"]

    with pytest.raises(RuntimeError, match="kaboom"):
        await invoke_workflow(workflow, run_store=store, run_id="workflow:boom:x")

    run = store.get_run("workflow:boom:x")
    assert run.status == "failed"
    assert run.is_error is True
    assert [e.type for e in store.get_events("workflow:boom:x")][-1] == "run_end"


@pytest.mark.asyncio
async def test_invoke_workflow_supports_sync_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_workflow(tmp_path, "sync", "def run(ctx):\n    return {'ok': True}\n")
    store = InMemoryRunStore()
    result = await invoke_workflow(discover_workflows(".")["sync"], run_store=store)
    assert result == {"ok": True}


# ── FlueContext ───────────────────────────────────────────────────────────────


def test_flue_context_id_and_alias():
    assert PyFlueContext is FlueContext
    ctx = FlueContext(run_id="workflow:t:abc", agent_id="inst")
    assert ctx.id == "workflow:t:abc"
    assert FlueContext(agent_id="inst").id == "inst"  # falls back to agent_id


@pytest.mark.asyncio
async def test_flue_context_init_with_created_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = FlueContext(payload={"x": 1}, run_id="workflow:t:abc")
    agent = create_agent(lambda c: {"model": "fake-model"})

    harness = await ctx.init(agent)
    assert harness.instance_id == "workflow:t:abc"  # ctx.id flows into the agent
    assert harness.profile.model == "fake-model"

    harness.backend = _FakeBackend(responses=["from workflow"])
    result = await (await harness.session("s")).prompt("go")
    assert result.text == "from workflow"


@pytest.mark.asyncio
async def test_flue_context_init_rejects_non_agent_positional(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = FlueContext()
    with pytest.raises(ValueError, match="created agent"):
        await ctx.init("not-an-agent")


@pytest.mark.asyncio
async def test_flue_context_legacy_kwargs_init_still_works(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = FlueContext()
    agent = await ctx.init(model="fake-model")
    assert agent.config.model == "fake-model"


# ── server route ──────────────────────────────────────────────────────────────


async def _client(root):
    app = create_app(root / "pyflue.toml")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_server_workflow_wait_result(tmp_path):
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")
    _write_workflow(tmp_path, "echo", "async def run(ctx):\n    return {'echo': ctx.payload.get('msg', '')}\n")

    async with await _client(tmp_path) as client:
        response = await client.post("/workflows/echo?wait=result", json={"msg": "hi"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"]["echo"] == "hi"
    assert body["run_id"].startswith("workflow:echo:")
    assert response.headers["x-flue-run-id"] == body["run_id"]


@pytest.mark.asyncio
async def test_server_workflow_accepted_default(tmp_path):
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")
    _write_workflow(tmp_path, "echo", "async def run(ctx):\n    return {'echo': 'x'}\n")

    async with await _client(tmp_path) as client:
        response = await client.post("/workflows/echo", json={"msg": "hi"})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["run_id"].startswith("workflow:echo:")


@pytest.mark.asyncio
async def test_server_unknown_workflow_returns_404(tmp_path):
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")

    async with await _client(tmp_path) as client:
        response = await client.post("/workflows/missing?wait=result", json={})

    assert response.status_code == 404
    assert response.json()["error"]["type"] == "not_found"
