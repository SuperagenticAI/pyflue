from __future__ import annotations

import httpx
import pytest

from pyflue.admin import create_admin_app
from pyflue.runs import InMemoryRunStore, observe, set_default_run_store, unobserve


@pytest.fixture(autouse=True)
def _reset_store():
    set_default_run_store(InMemoryRunStore())
    yield
    set_default_run_store(None)


async def _client():
    app = create_admin_app()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_admin_lists_agents_and_instances_and_runs():
    from pyflue.runs import get_default_run_store

    store = get_default_run_store()
    run_a = await store.start_run(agent="hello", agent_id="alice")
    await store.end_run(run_a.run_id)
    run_b = await store.start_run(agent="hello", agent_id="bob")
    await store.end_run(run_b.run_id, is_error=True, error={"type": "boom", "message": "x"})
    run_c = await store.start_run(agent="triage", agent_id="alice")
    await store.end_run(run_c.run_id)

    async with await _client() as client:
        agents = (await client.get("/agents")).json()
        assert sorted(a["name"] for a in agents["agents"]) == ["hello", "triage"]

        instances = (await client.get("/agents/hello/instances")).json()
        assert sorted(i["agent_id"] for i in instances["instances"]) == ["alice", "bob"]

        runs = (await client.get("/agents/hello/instances/alice/runs")).json()
        assert len(runs["runs"]) == 1
        assert runs["runs"][0]["run_id"] == run_a.run_id

        all_runs = (await client.get("/runs?limit=10")).json()
        assert len(all_runs["runs"]) == 3
        # Most recent first.
        assert all_runs["runs"][0]["run_id"] == run_c.run_id

        one = (await client.get(f"/runs/{run_b.run_id}")).json()
        assert one["status"] == "failed"


@pytest.mark.asyncio
async def test_admin_404s_for_unknown_agent_or_instance():
    async with await _client() as client:
        resp = await client.get("/agents/missing/instances")
        assert resp.status_code == 404
        assert resp.json()["error"]["type"] == "agent_not_found"

        resp = await client.get("/agents/missing/instances/x/runs")
        assert resp.status_code == 404

        resp = await client.get("/runs/run_DOESNOTEXIST")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_validates_runs_limit():
    async with await _client() as client:
        resp = await client.get("/runs?limit=0")
        assert resp.status_code == 400
        assert resp.json()["error"]["type"] == "invalid_query_param"


@pytest.mark.asyncio
async def test_observe_receives_events():
    from pyflue.runs import get_default_run_store

    store = get_default_run_store()
    seen: list[tuple[str, str]] = []

    def callback(run, event):
        seen.append((run.run_id, event.type))

    observe(callback)
    try:
        run = await store.start_run(agent="hello", agent_id="alice")
        await store.append_event(run.run_id, "tool_call", {"name": "bash"})
        await store.end_run(run.run_id)
    finally:
        unobserve(callback)

    types = [t for _rid, t in seen]
    assert types == ["run_start", "tool_call", "run_end"]


@pytest.mark.asyncio
async def test_observe_async_callback():
    from pyflue.runs import get_default_run_store

    store = get_default_run_store()
    seen: list[str] = []

    async def callback(run, event):
        seen.append(event.type)

    observe(callback)
    try:
        run = await store.start_run(agent="hello", agent_id="alice")
        await store.end_run(run.run_id)
        # Allow scheduled async callbacks to run.
        import asyncio

        await asyncio.sleep(0)
    finally:
        unobserve(callback)

    assert "run_start" in seen
    assert "run_end" in seen


@pytest.mark.asyncio
async def test_observe_exception_does_not_break_run():
    from pyflue.runs import get_default_run_store

    store = get_default_run_store()

    def bad(_run, _event):
        raise RuntimeError("nope")

    observe(bad)
    try:
        run = await store.start_run(agent="hello", agent_id="alice")
        await store.end_run(run.run_id)
        fetched = store.get_run(run.run_id)
        assert fetched is not None and fetched.status == "succeeded"
    finally:
        unobserve(bad)
