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
        assert sorted(a["name"] for a in agents["items"]) == ["hello", "triage"]
        assert agents["nextCursor"] is None

        instances = (await client.get("/agents/hello/instances")).json()
        assert sorted(i["agent_id"] for i in instances["instances"]) == ["alice", "bob"]
        assert sorted(i["instanceId"] for i in instances["items"]) == ["alice", "bob"]

        runs = (await client.get("/agents/hello/instances/alice/runs")).json()
        assert len(runs["runs"]) == 1
        assert runs["runs"][0]["run_id"] == run_a.run_id
        assert runs["items"][0]["runId"] == run_a.run_id
        assert runs["items"][0]["agentName"] == "hello"
        assert runs["items"][0]["instanceId"] == "alice"

        all_runs = (await client.get("/runs?limit=10")).json()
        assert len(all_runs["runs"]) == 3
        # Most recent first.
        assert all_runs["runs"][0]["run_id"] == run_c.run_id
        assert all_runs["items"][0]["runId"] == run_c.run_id

        one = (await client.get(f"/runs/{run_b.run_id}")).json()
        assert one["status"] == "failed"
        assert one["runId"] == run_b.run_id
        assert one["isError"] is True


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
async def test_admin_paginates_and_filters_runs():
    from pyflue.runs import get_default_run_store

    store = get_default_run_store()
    run_a = await store.start_run(agent="hello", agent_id="alice")
    await store.end_run(run_a.run_id)
    run_b = await store.start_run(agent="hello", agent_id="bob")
    await store.end_run(run_b.run_id, is_error=True, error={"type": "boom", "message": "x"})
    run_c = await store.start_run(agent="triage", agent_id="alice")
    await store.end_run(run_c.run_id)

    async with await _client() as client:
        first = (await client.get("/runs", params={"limit": "2"})).json()
        assert [item["runId"] for item in first["items"]] == [run_c.run_id, run_b.run_id]
        assert first["nextCursor"] != "2"

        second = (await client.get("/runs", params={"cursor": first["nextCursor"], "limit": "2"})).json()
        assert [item["runId"] for item in second["items"]] == [run_a.run_id]
        assert second["nextCursor"] is None

        legacy_cursor = (await client.get("/runs", params={"cursor": "2", "limit": "2"})).json()
        assert [item["runId"] for item in legacy_cursor["items"]] == [run_a.run_id]

        failed = (await client.get("/runs", params={"status": "errored"})).json()
        assert [item["runId"] for item in failed["items"]] == [run_b.run_id]

        hello = (await client.get("/runs", params={"agentName": "hello"})).json()
        assert [item["runId"] for item in hello["items"]] == [run_b.run_id, run_a.run_id]

        instance_failed = (
            await client.get("/agents/hello/instances/bob/runs", params={"status": "failed"})
        ).json()
        assert [item["runId"] for item in instance_failed["items"]] == [run_b.run_id]


@pytest.mark.asyncio
async def test_admin_paginates_agents_and_instances():
    from pyflue.runs import get_default_run_store

    store = get_default_run_store()
    for agent, agent_id in [("a", "one"), ("b", "one"), ("b", "two")]:
        run = await store.start_run(agent=agent, agent_id=agent_id)
        await store.end_run(run.run_id)

    async with await _client() as client:
        agents = (await client.get("/agents", params={"limit": "1"})).json()
        assert [item["name"] for item in agents["items"]] == ["a"]
        assert agents["nextCursor"] != "1"
        next_agents = (await client.get("/agents", params={"cursor": agents["nextCursor"]})).json()
        assert [item["name"] for item in next_agents["items"]] == ["b"]

        instances = (await client.get("/agents/b/instances", params={"limit": "1"})).json()
        assert [item["instanceId"] for item in instances["items"]] == ["one"]
        assert instances["nextCursor"] != "1"
        next_instances = (
            await client.get("/agents/b/instances", params={"cursor": instances["nextCursor"]})
        ).json()
        assert [item["instanceId"] for item in next_instances["items"]] == ["two"]


@pytest.mark.asyncio
async def test_admin_openapi_documents_admin_routes():
    async with await _client() as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    spec = response.json()
    assert spec["info"]["title"] == "PyFlue Admin"
    assert "AdminAgentsResponse" in spec["components"]["schemas"]
    assert "AdminInstancesResponse" in spec["components"]["schemas"]
    assert "AdminRunsResponse" in spec["components"]["schemas"]
    assert spec["paths"]["/runs"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"].endswith("/AdminRunsResponse")


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
