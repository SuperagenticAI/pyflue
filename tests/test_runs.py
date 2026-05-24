from __future__ import annotations

import asyncio

import pytest

from pyflue.runs import (
    InMemoryRunRegistry,
    InMemoryRunStore,
    SQLiteRunRegistry,
    SQLiteRunStore,
    decode_instance_cursor,
    decode_run_cursor,
    encode_instance_cursor,
    generate_run_id,
    get_default_run_store,
    set_default_run_store,
)


def test_generate_run_id_shape():
    rid = generate_run_id()
    assert rid.startswith("run_")
    assert len(rid) == len("run_") + 26


@pytest.mark.asyncio
async def test_store_append_and_query():
    store = InMemoryRunStore()
    run = await store.start_run(agent="hello", agent_id="abc")
    await store.append_event(run.run_id, "tool_call", {"name": "bash"})
    await store.append_event(run.run_id, "log", {"level": "info", "message": "hi"})
    await store.end_run(run.run_id, is_error=False, result={"ok": True})

    events = store.get_events(run.run_id)
    # run_start + 2 + run_end
    assert [e.type for e in events] == ["run_start", "tool_call", "log", "run_end"]
    assert [e.event_index for e in events] == [1, 2, 3, 4]

    only_logs = store.get_events(run.run_id, types=["log"])
    assert len(only_logs) == 1 and only_logs[0].type == "log"

    after = store.get_events(run.run_id, after=2)
    assert [e.event_index for e in after] == [3, 4]

    fetched = store.get_run(run.run_id)
    assert fetched is not None
    assert fetched.status == "succeeded"
    assert fetched.event_count == 4
    run_payload = fetched.to_dict()
    assert run_payload["runId"] == run.run_id
    assert run_payload["agentName"] == "hello"
    assert run_payload["instanceId"] == "abc"
    assert run_payload["startedAt"].endswith("Z")
    assert run_payload["durationMs"] is not None
    assert run_payload["isError"] is False
    assert run_payload["result"] == {"ok": True}

    event_payload = events[0].to_dict()
    assert event_payload["runId"] == run.run_id
    assert event_payload["eventIndex"] == 1


@pytest.mark.asyncio
async def test_store_records_failure():
    store = InMemoryRunStore()
    run = await store.start_run(agent="x", agent_id="1")
    await store.end_run(run.run_id, is_error=True, error={"type": "boom", "message": "x"})
    fetched = store.get_run(run.run_id)
    assert fetched is not None
    assert fetched.status == "failed"
    assert fetched.is_error is True


@pytest.mark.asyncio
async def test_subscribe_replays_backlog_and_tails_live():
    store = InMemoryRunStore()
    run = await store.start_run(agent="x", agent_id="1")
    await store.append_event(run.run_id, "tool_call", {"i": 1})

    seen = []

    async def consume():
        async for event in store.subscribe(run.run_id, after=0):
            seen.append((event.event_index, event.type))
            if event.type == "run_end":
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await store.append_event(run.run_id, "tool_call", {"i": 2})
    await store.end_run(run.run_id)
    await asyncio.wait_for(task, timeout=2)

    assert seen[0] == (1, "run_start")
    assert (2, "tool_call") in seen
    assert (3, "tool_call") in seen
    assert seen[-1][1] == "run_end"


@pytest.mark.asyncio
async def test_subscribe_after_resume_skips_backlog():
    store = InMemoryRunStore()
    run = await store.start_run(agent="x", agent_id="1")
    await store.append_event(run.run_id, "a", {})
    await store.append_event(run.run_id, "b", {})

    seen = []

    async def consume():
        async for event in store.subscribe(run.run_id, after=2):
            seen.append(event.event_index)
            if event.type == "run_end":
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await store.append_event(run.run_id, "c", {})
    await store.end_run(run.run_id)
    await asyncio.wait_for(task, timeout=2)

    # Backlog (1=run_start, 2=a, 3=b) skipped past 2; first event seen is index 3 onwards.
    assert seen[0] == 3
    assert seen[-1] >= 4


@pytest.mark.asyncio
async def test_subscribe_returns_immediately_for_terminal_run():
    store = InMemoryRunStore()
    run = await store.start_run(agent="x", agent_id="1")
    await store.end_run(run.run_id)

    seen = []
    async for event in store.subscribe(run.run_id, after=0):
        seen.append(event.type)
    assert "run_end" in seen


@pytest.mark.asyncio
async def test_sqlite_run_store_persists_runs_and_events(tmp_path):
    db_path = tmp_path / "runs.sqlite3"
    store = SQLiteRunStore(db_path)
    run = await store.start_run(agent="hello", agent_id="abc")
    await store.append_event(run.run_id, "log", {"message": "persisted"})
    await store.end_run(run.run_id, result={"ok": True})
    store.close()

    restored = SQLiteRunStore(db_path)
    try:
        fetched = restored.get_run(run.run_id)
        assert fetched is not None
        assert fetched.status == "succeeded"
        assert fetched.result == {"ok": True}
        assert restored.list_agents() == ["hello"]
        assert restored.list_instances("hello") == ["abc"]
        assert [event.type for event in restored.get_events(run.run_id)] == [
            "run_start",
            "log",
            "run_end",
        ]
        assert restored.get_events(run.run_id, after=1)[0].data == {"message": "persisted"}
    finally:
        restored.close()


def test_default_run_store_can_use_sqlite(tmp_path, monkeypatch):
    set_default_run_store(None)
    monkeypatch.setenv("PYFLUE_RUN_STORE", "sqlite")
    monkeypatch.setenv("PYFLUE_RUN_STORE_PATH", str(tmp_path / "runs.sqlite3"))
    try:
        store = get_default_run_store()
        assert isinstance(store, SQLiteRunStore)
    finally:
        store = get_default_run_store()
        if isinstance(store, SQLiteRunStore):
            store.close()
        set_default_run_store(None)


@pytest.mark.asyncio
async def test_run_registry_lists_runs_instances_and_opaque_cursors():
    registry = InMemoryRunRegistry()
    await registry.recordRunStart(
        run_id="run_1",
        agent_name="hello",
        instance_id="a",
        started_at="2026-01-01T00:00:00Z",
    )
    await registry.recordRunStart(
        run_id="run_2",
        agent_name="hello",
        instance_id="b",
        started_at="2026-01-02T00:00:00Z",
    )
    await registry.recordRunStart(
        run_id="run_3",
        agent_name="triage",
        instance_id="a",
        started_at="2026-01-03T00:00:00Z",
    )
    await registry.recordRunEnd(
        run_id="run_2",
        ended_at="2026-01-02T00:00:03Z",
        is_error=True,
    )

    first = await registry.listRuns(limit=2)
    assert [item["runId"] for item in first["items"]] == ["run_3", "run_2"]
    assert decode_run_cursor(first["nextCursor"]) == ("2026-01-02T00:00:00Z", "run_2")

    second = await registry.list_runs(limit=2, cursor=first["nextCursor"])
    assert [item["runId"] for item in second["items"]] == ["run_1"]
    assert second["nextCursor"] is None

    failed = await registry.list_runs(status="failed")
    assert failed["items"][0]["runId"] == "run_2"
    assert failed["items"][0]["durationMs"] == 3000
    assert failed["items"][0]["isError"] is True

    hello = await registry.list_runs(agent_name="hello")
    assert [item["runId"] for item in hello["items"]] == ["run_2", "run_1"]

    instances = await registry.listInstances(limit=2)
    assert [(item["agentName"], item["instanceId"]) for item in instances["items"]] == [
        ("hello", "a"),
        ("hello", "b"),
    ]
    assert decode_instance_cursor(instances["nextCursor"]) == "hello\0b"

    next_instances = await registry.list_instances(cursor=instances["nextCursor"])
    assert [(item["agentName"], item["instanceId"]) for item in next_instances["items"]] == [
        ("triage", "a"),
    ]

    encoded = encode_instance_cursor("hello\0a")
    assert decode_instance_cursor(encoded) == "hello\0a"


@pytest.mark.asyncio
async def test_sqlite_run_registry_persists_pointers(tmp_path):
    path = tmp_path / "registry.sqlite3"
    registry = SQLiteRunRegistry(path)
    await registry.record_run_start(
        run_id="run_1",
        agent_name="hello",
        instance_id="abc",
        started_at="2026-01-01T00:00:00Z",
    )
    await registry.record_run_end(
        run_id="run_1",
        ended_at="2026-01-01T00:00:01Z",
        is_error=False,
    )
    registry.close()

    restored = SQLiteRunRegistry(path)
    try:
        run = await restored.lookupRun("run_1")
        assert run is not None
        assert run.status == "succeeded"
        assert run.duration_ms == 1000

        listed = await restored.list_runs()
        assert listed["items"][0]["runId"] == "run_1"

        instances = await restored.list_instances()
        assert instances["items"] == [{
            "agent_name": "hello",
            "agentName": "hello",
            "instance_id": "abc",
            "instanceId": "abc",
        }]
    finally:
        restored.close()
