from __future__ import annotations

import httpx
import pytest

from pyflue.runs import InMemoryRunStore, set_default_run_store
from pyflue.server import create_app


async def _client(root):
    app = create_app(root / "pyflue.toml")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def _reset_store():
    set_default_run_store(InMemoryRunStore())
    yield
    set_default_run_store(None)


@pytest.mark.asyncio
async def test_run_id_returned_and_events_logged(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "hello.py").write_text(
        "triggers = {'webhook': True}\n"
        "async def default(context):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")

    async with await _client(tmp_path) as client:
        resp = await client.post("/agents/hello/demo", json={"payload": {}})
        assert resp.status_code == 200
        body = resp.json()
        run_id = body["_meta"]["run_id"]
        assert run_id.startswith("run_")

        run = await client.get(f"/runs/{run_id}")
        assert run.status_code == 200
        assert run.json()["status"] == "succeeded"

        events = await client.get(f"/runs/{run_id}/events")
        assert events.status_code == 200
        types = [e["type"] for e in events.json()["events"]]
        assert types[0] == "run_start"
        assert types[-1] == "run_end"


@pytest.mark.asyncio
async def test_run_lookup_returns_404_for_unknown(tmp_path):
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")
    async with await _client(tmp_path) as client:
        resp = await client.get("/runs/run_DOESNOTEXIST")
        assert resp.status_code == 404
        assert resp.json()["error"]["type"] == "run_not_found"


@pytest.mark.asyncio
async def test_events_query_param_validation(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "h.py").write_text(
        "triggers = {'webhook': True}\n"
        "async def default(context):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")

    async with await _client(tmp_path) as client:
        resp = await client.post("/agents/h/x", json={"payload": {}})
        run_id = resp.json()["_meta"]["run_id"]

        bad_limit = await client.get(f"/runs/{run_id}/events", params={"limit": "0"})
        assert bad_limit.status_code == 400
        assert bad_limit.json()["error"]["type"] == "invalid_query_param"

        bad_after = await client.get(f"/runs/{run_id}/events", params={"after": "-1"})
        assert bad_after.status_code == 400

        bad_int = await client.get(f"/runs/{run_id}/events", params={"after": "abc"})
        assert bad_int.status_code == 400


@pytest.mark.asyncio
async def test_failed_handler_records_run_end_with_error(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "boom.py").write_text(
        "triggers = {'webhook': True}\n"
        "async def default(context):\n"
        "    raise RuntimeError('kaboom')\n",
        encoding="utf-8",
    )
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")

    async with await _client(tmp_path) as client:
        resp = await client.post("/agents/boom/x", json={"payload": {}})
        assert resp.status_code == 500

        # Find the run via list (most recent).
        from pyflue.runs import get_default_run_store

        runs = get_default_run_store().list_runs(limit=5)
        assert runs and runs[0].status == "failed"
        run_id = runs[0].run_id

        events = await client.get(f"/runs/{run_id}/events")
        types = [e["type"] for e in events.json()["events"]]
        assert types[-1] == "run_end"
        last = events.json()["events"][-1]
        assert last["data"]["is_error"] is True
