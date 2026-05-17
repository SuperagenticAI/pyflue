from __future__ import annotations

import asyncio

import pytest

from pyflue.runs import InMemoryRunStore, generate_run_id


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
