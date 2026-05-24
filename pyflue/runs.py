"""Run identity, durable event log, and live subscribers.

A *run* is one HTTP invocation (or one CLI invocation) of an agent. Every run
gets a stable ULID-style id, a `run_start` lifecycle event, zero or more
intermediate events, and exactly one terminal `run_end` event with `is_error`
and an optional error envelope. Events carry a monotonic `event_index` so
clients can resume an SSE stream with `Last-Event-ID`.

This module is transport-agnostic: it knows nothing about FastAPI / SSE. The
server wires it into `/runs/<run_id>{,/events,/stream}` routes.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import secrets
import sqlite3
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Iterable
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ULID alphabet (Crockford Base32). We use ULID-shape ids (26 chars, time-
# sortable) without pulling a dependency. The first 10 chars encode 48 bits
# of millisecond timestamp; the last 16 encode 80 bits of randomness.
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def generate_run_id() -> str:
    """Return a ULID-shaped run id prefixed with ``run_``.

    Time-sortable across a single process; collision-resistant across many.
    """
    ms = int(time.time() * 1000)
    time_part = _encode_base32(ms, 10)
    rand_part = "".join(_ULID_ALPHABET[b % 32] for b in secrets.token_bytes(16))
    return f"run_{time_part}{rand_part}"


def _encode_base32(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ULID_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def _iso_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")


# ─── Data classes ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunEvent:
    """One event in a run's durable event log."""

    run_id: str
    event_index: int
    type: str
    timestamp: float  # seconds since epoch
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "runId": self.run_id,
            "event_index": self.event_index,
            "eventIndex": self.event_index,
            "type": self.type,
            "timestamp": self.timestamp,
            "data": dict(self.data),
        }


@dataclass
class FlueRun:
    """Metadata for one agent invocation."""

    run_id: str
    agent: str
    agent_id: str
    started_at: float
    ended_at: float | None = None
    status: str = "running"  # running | succeeded | failed
    is_error: bool = False
    error: dict[str, Any] | None = None
    result: Any = None
    event_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        duration_ms = None
        if self.ended_at is not None:
            duration_ms = max(0, int((self.ended_at - self.started_at) * 1000))
        return {
            "run_id": self.run_id,
            "runId": self.run_id,
            "agent": self.agent,
            "agentName": self.agent,
            "agent_id": self.agent_id,
            "instanceId": self.agent_id,
            "started_at": self.started_at,
            "startedAt": _iso_timestamp(self.started_at),
            "ended_at": self.ended_at,
            "endedAt": _iso_timestamp(self.ended_at) if self.ended_at is not None else None,
            "durationMs": duration_ms,
            "status": self.status,
            "is_error": self.is_error,
            "isError": self.is_error,
            "error": dict(self.error) if self.error else None,
            "result": self.result,
            "event_count": self.event_count,
        }


@dataclass(frozen=True)
class RunPointer:
    """Cross-deployment pointer to one run.

    Mirrors Flue's run registry record shape. A registry can keep lightweight
    run metadata separately from the full per-run event log.
    """

    run_id: str
    agent_name: str
    instance_id: str
    status: str
    started_at: str
    ended_at: str | None = None
    duration_ms: int | None = None
    is_error: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "runId": self.run_id,
            "agent_name": self.agent_name,
            "agentName": self.agent_name,
            "instance_id": self.instance_id,
            "instanceId": self.instance_id,
            "status": self.status,
            "started_at": self.started_at,
            "startedAt": self.started_at,
            "ended_at": self.ended_at,
            "endedAt": self.ended_at,
            "duration_ms": self.duration_ms,
            "durationMs": self.duration_ms,
            "is_error": self.is_error,
            "isError": self.is_error,
        }


@dataclass(frozen=True)
class InstancePointer:
    """Cross-deployment pointer to one agent instance."""

    agent_name: str
    instance_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "agentName": self.agent_name,
            "instance_id": self.instance_id,
            "instanceId": self.instance_id,
        }


# ─── Store ─────────────────────────────────────────────────────────────────


class InMemoryRunStore:
    """In-process run history and live event fan-out.

    - Keeps a bounded LRU of runs (default 1024 most-recent).
    - Holds each run's full ordered event log.
    - Lets subscribers tail live runs; replay-from-eventIndex is supported.

    Lifecycle ordering guarantee: ``run_end`` is appended to the durable log
    BEFORE being published to live subscribers, so a client connecting near
    completion never misses the terminal event.
    """

    def __init__(self, *, max_runs: int = 1024) -> None:
        self._max_runs = max_runs
        self._runs: OrderedDict[str, FlueRun] = OrderedDict()
        self._events: dict[str, list[RunEvent]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[RunEvent | None]]] = {}
        self._lock = asyncio.Lock()
        # Secondary indexes for admin lookups.
        self._by_agent: dict[str, set[str]] = {}  # agent -> {agent_id, ...}
        self._by_instance: dict[tuple[str, str], list[str]] = {}  # (agent, agent_id) -> [run_id]
        self._global_subscribers: list[Any] = []

    # -- read

    def get_run(self, run_id: str) -> FlueRun | None:
        return self._runs.get(run_id)

    def list_runs(
        self,
        *,
        limit: int | None = 100,
        status: str | None = None,
        agent: str | None = None,
    ) -> list[FlueRun]:
        # Most-recent first.
        runs = list(self._runs.values())[::-1]
        if status is not None:
            runs = [run for run in runs if run.status == status]
        if agent is not None:
            runs = [run for run in runs if run.agent == agent]
        return runs if limit is None else runs[:limit]

    def list_agents(self) -> list[str]:
        """Return all agent names that have at least one tracked run."""
        return sorted(self._by_agent.keys())

    def list_instances(self, agent: str) -> list[str]:
        """Return all agent_id values seen for ``agent``."""
        return sorted(self._by_agent.get(agent, set()))

    def list_runs_for_instance(
        self,
        agent: str,
        agent_id: str,
        *,
        limit: int | None = 100,
        status: str | None = None,
    ) -> list[FlueRun]:
        """Return most-recent-first runs for one ``(agent, agent_id)``."""
        ids = self._by_instance.get((agent, agent_id), [])[::-1]
        runs = [self._runs[rid] for rid in ids if rid in self._runs]
        if status is not None:
            runs = [run for run in runs if run.status == status]
        return runs if limit is None else runs[:limit]

    def get_events(
        self,
        run_id: str,
        *,
        after: int = 0,
        limit: int = 1000,
        types: Iterable[str] | None = None,
    ) -> list[RunEvent]:
        log = self._events.get(run_id) or []
        type_filter = set(types) if types else None
        out: list[RunEvent] = []
        for event in log:
            if event.event_index <= after:
                continue
            if type_filter is not None and event.type not in type_filter:
                continue
            out.append(event)
            if len(out) >= limit:
                break
        return out

    def is_terminal(self, run_id: str) -> bool:
        run = self._runs.get(run_id)
        return run is not None and run.status != "running"

    # -- write

    async def start_run(self, *, agent: str, agent_id: str, run_id: str | None = None) -> FlueRun:
        rid = run_id or generate_run_id()
        run = FlueRun(
            run_id=rid,
            agent=agent,
            agent_id=agent_id,
            started_at=time.time(),
        )
        async with self._lock:
            self._runs[rid] = run
            self._events[rid] = []
            self._subscribers.setdefault(rid, [])
            self._by_agent.setdefault(agent, set()).add(agent_id)
            self._by_instance.setdefault((agent, agent_id), []).append(rid)
            self._evict_locked()
        await self.append_event(rid, "run_start", {"agent": agent, "agent_id": agent_id})
        return run

    async def end_run(
        self,
        run_id: str,
        *,
        is_error: bool = False,
        error: dict[str, Any] | None = None,
        result: Any = None,
    ) -> None:
        run = self._runs.get(run_id)
        if run is None or run.status != "running":
            return
        run.ended_at = time.time()
        run.is_error = is_error
        run.error = error
        run.result = result
        run.status = "failed" if is_error else "succeeded"
        # Durable first, then publish (see class docstring).
        await self.append_event(
            run_id,
            "run_end",
            {"is_error": is_error, "error": error, "status": run.status},
        )
        # Close subscribers.
        async with self._lock:
            queues = self._subscribers.pop(run_id, [])
        for q in queues:
            q.put_nowait(None)

    async def append_event(
        self,
        run_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> RunEvent:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(f"Unknown run_id: {run_id}")
            log = self._events.setdefault(run_id, [])
            index = len(log) + 1
            event = RunEvent(
                run_id=run_id,
                event_index=index,
                type=event_type,
                timestamp=time.time(),
                data=dict(data or {}),
            )
            log.append(event)
            run.event_count = index
            queues = list(self._subscribers.get(run_id, ()))
        # Publish to live subscribers outside the lock.
        for q in queues:
            q.put_nowait(event)
        # Fan out to global subscribers (observe()).
        if self._global_subscribers:
            for cb in list(self._global_subscribers):
                try:
                    res = cb(run, event)
                    if asyncio.iscoroutine(res):
                        asyncio.create_task(res)
                except Exception:
                    # Observers must never break the run loop.
                    pass
        return event

    # -- subscribe

    async def subscribe(
        self,
        run_id: str,
        *,
        after: int = 0,
    ) -> AsyncIterator[RunEvent]:
        """Yield durable backlog past ``after`` then tail live events.

        Returns immediately if the run has already terminated and the backlog
        is exhausted. Caller is expected to iterate to completion or break.
        """
        # Snapshot backlog + register before yielding any backlog so we don't
        # miss events appended between snapshot and registration.
        queue: asyncio.Queue[RunEvent | None] = asyncio.Queue()
        async with self._lock:
            backlog = list(self._events.get(run_id, ()))
            terminal = self.is_terminal(run_id)
            if not terminal:
                self._subscribers.setdefault(run_id, []).append(queue)

        try:
            for event in backlog:
                if event.event_index > after:
                    yield event
            if terminal:
                return
            while True:
                event = await queue.get()
                if event is None:
                    return
                # Skip duplicates that may already be in backlog if subscriber
                # registered after backlog snapshot but before any new events.
                if event.event_index <= after:
                    continue
                # Avoid re-yielding events already in backlog.
                if backlog and event.event_index <= backlog[-1].event_index:
                    continue
                yield event
                if event.type == "run_end":
                    return
        finally:
            async with self._lock:
                subs = self._subscribers.get(run_id)
                if subs and queue in subs:
                    subs.remove(queue)

    # -- internal

    def _evict_locked(self) -> None:
        while len(self._runs) > self._max_runs:
            old_id, old_run = self._runs.popitem(last=False)
            self._events.pop(old_id, None)
            self._subscribers.pop(old_id, None)
            key = (old_run.agent, old_run.agent_id)
            ids = self._by_instance.get(key)
            if ids:
                with suppress(ValueError):
                    ids.remove(old_id)
                if not ids:
                    self._by_instance.pop(key, None)
                    instances = self._by_agent.get(old_run.agent)
                    if instances is not None and old_run.agent_id in instances:
                        instances.discard(old_run.agent_id)
                        if not instances:
                            self._by_agent.pop(old_run.agent, None)

    # -- global observe()

    def add_global_subscriber(self, callback: Any) -> None:
        """Register a callback invoked for every event in this store.

        Callbacks receive ``(run, event)``. May be sync or async.
        """
        self._global_subscribers.append(callback)

    def remove_global_subscriber(self, callback: Any) -> None:
        with suppress(ValueError):
            self._global_subscribers.remove(callback)


class SQLiteRunStore(InMemoryRunStore):
    """SQLite-backed run store with in-process live subscribers.

    Reads use the same in-memory indexes as ``InMemoryRunStore``. The store
    loads recent durable state on startup and persists each run/event mutation,
    so run history survives process restarts while active SSE subscribers still
    work inside the current process.
    """

    def __init__(self, path: str | Path, *, max_runs: int = 1024) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(max_runs=max_runs)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init_db()
        self._load_from_disk()

    def close(self) -> None:
        self._db.close()

    async def start_run(self, *, agent: str, agent_id: str, run_id: str | None = None) -> FlueRun:
        run = await super().start_run(agent=agent, agent_id=agent_id, run_id=run_id)
        self._persist_run(run)
        return run

    async def append_event(
        self,
        run_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> RunEvent:
        event = await super().append_event(run_id, event_type, data)
        self._persist_event(event)
        run = self.get_run(run_id)
        if run is not None:
            self._persist_run(run)
        return event

    async def end_run(
        self,
        run_id: str,
        *,
        is_error: bool = False,
        error: dict[str, Any] | None = None,
        result: Any = None,
    ) -> None:
        await super().end_run(run_id, is_error=is_error, error=error, result=result)
        run = self.get_run(run_id)
        if run is not None:
            self._persist_run(run)

    def _init_db(self) -> None:
        self._db.executescript(
            """
            create table if not exists runs (
                run_id text primary key,
                agent text not null,
                agent_id text not null,
                started_at real not null,
                ended_at real,
                status text not null,
                is_error integer not null,
                error_json text,
                result_json text,
                event_count integer not null
            );
            create table if not exists events (
                run_id text not null,
                event_index integer not null,
                type text not null,
                timestamp real not null,
                data_json text not null,
                primary key (run_id, event_index)
            );
            create index if not exists idx_runs_started_at on runs(started_at);
            create index if not exists idx_runs_agent_instance on runs(agent, agent_id);
            create index if not exists idx_events_run_id on events(run_id, event_index);
            """
        )
        self._db.commit()

    def _load_from_disk(self) -> None:
        rows = self._db.execute(
            """
            select * from (
                select * from runs order by started_at desc limit ?
            ) order by started_at asc
            """,
            (self._max_runs,),
        ).fetchall()
        loaded_ids: list[str] = []
        for row in rows:
            run = FlueRun(
                run_id=str(row["run_id"]),
                agent=str(row["agent"]),
                agent_id=str(row["agent_id"]),
                started_at=float(row["started_at"]),
                ended_at=float(row["ended_at"]) if row["ended_at"] is not None else None,
                status=str(row["status"]),
                is_error=bool(row["is_error"]),
                error=_json_loads(row["error_json"]),
                result=_json_loads(row["result_json"]),
                event_count=int(row["event_count"]),
            )
            self._runs[run.run_id] = run
            self._events[run.run_id] = []
            self._by_agent.setdefault(run.agent, set()).add(run.agent_id)
            self._by_instance.setdefault((run.agent, run.agent_id), []).append(run.run_id)
            loaded_ids.append(run.run_id)
        if not loaded_ids:
            return
        placeholders = ",".join("?" for _ in loaded_ids)
        event_rows = self._db.execute(
            f"select * from events where run_id in ({placeholders}) order by run_id, event_index",
            loaded_ids,
        ).fetchall()
        for row in event_rows:
            event = RunEvent(
                run_id=str(row["run_id"]),
                event_index=int(row["event_index"]),
                type=str(row["type"]),
                timestamp=float(row["timestamp"]),
                data=_json_loads(row["data_json"]) or {},
            )
            self._events.setdefault(event.run_id, []).append(event)

    def _persist_run(self, run: FlueRun) -> None:
        self._db.execute(
            """
            insert into runs (
                run_id, agent, agent_id, started_at, ended_at, status, is_error,
                error_json, result_json, event_count
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(run_id) do update set
                agent = excluded.agent,
                agent_id = excluded.agent_id,
                started_at = excluded.started_at,
                ended_at = excluded.ended_at,
                status = excluded.status,
                is_error = excluded.is_error,
                error_json = excluded.error_json,
                result_json = excluded.result_json,
                event_count = excluded.event_count
            """,
            (
                run.run_id,
                run.agent,
                run.agent_id,
                run.started_at,
                run.ended_at,
                run.status,
                1 if run.is_error else 0,
                _json_dumps(run.error),
                _json_dumps(run.result),
                run.event_count,
            ),
        )
        self._db.commit()

    def _persist_event(self, event: RunEvent) -> None:
        self._db.execute(
            """
            insert or replace into events (
                run_id, event_index, type, timestamp, data_json
            ) values (?, ?, ?, ?, ?)
            """,
            (
                event.run_id,
                event.event_index,
                event.type,
                event.timestamp,
                _json_dumps(event.data),
            ),
        )
        self._db.commit()


class InMemoryRunRegistry:
    """Flue-style run pointer registry.

    The registry stores lightweight run pointers and list cursors. It is useful
    for deployment-wide admin surfaces where the full event log may live in a
    separate store.
    """

    def __init__(self) -> None:
        self._runs: dict[str, RunPointer] = {}

    async def record_run_start(
        self,
        *,
        run_id: str,
        agent_name: str,
        instance_id: str,
        started_at: str | float | None = None,
    ) -> None:
        started = _registry_timestamp(started_at)
        self._runs[run_id] = RunPointer(
            run_id=run_id,
            agent_name=agent_name,
            instance_id=instance_id,
            status="running",
            started_at=started,
        )

    async def record_run_end(
        self,
        *,
        run_id: str,
        ended_at: str | float | None = None,
        duration_ms: int | None = None,
        is_error: bool = False,
    ) -> None:
        current = self._runs.get(run_id)
        if current is None:
            return
        ended = _registry_timestamp(ended_at)
        self._runs[run_id] = RunPointer(
            run_id=current.run_id,
            agent_name=current.agent_name,
            instance_id=current.instance_id,
            status="failed" if is_error else "succeeded",
            started_at=current.started_at,
            ended_at=ended,
            duration_ms=duration_ms if duration_ms is not None else _duration_ms(current.started_at, ended),
            is_error=is_error,
        )

    async def lookup_run(self, run_id: str) -> RunPointer | None:
        return self._runs.get(run_id)

    async def list_runs(
        self,
        *,
        status: str | None = None,
        agent_name: str | None = None,
        instance_id: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        limit = _registry_limit(limit)
        cursor_tuple = decode_run_cursor(cursor)
        runs = sorted(
            self._runs.values(),
            key=lambda run: (run.started_at, run.run_id),
            reverse=True,
        )
        if status is not None:
            runs = [run for run in runs if run.status == status]
        if agent_name is not None:
            runs = [run for run in runs if run.agent_name == agent_name]
        if instance_id is not None:
            runs = [run for run in runs if run.instance_id == instance_id]
        if cursor_tuple is not None:
            started_at, run_id = cursor_tuple
            runs = [
                run for run in runs
                if (run.started_at, run.run_id) < (started_at, run_id)
            ]
        page = runs[:limit]
        next_cursor = encode_run_cursor(page[-1]) if len(runs) > len(page) and page else None
        return {"runs": [run.to_dict() for run in page], "items": [run.to_dict() for run in page], "nextCursor": next_cursor}

    async def list_instances(
        self,
        *,
        agent_name: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        limit = _registry_limit(limit)
        cursor_key = decode_instance_cursor(cursor) if cursor else None
        seen = {
            _instance_key(run.agent_name, run.instance_id): InstancePointer(run.agent_name, run.instance_id)
            for run in self._runs.values()
            if agent_name is None or run.agent_name == agent_name
        }
        keys = sorted(seen)
        if cursor_key is not None:
            keys = [key for key in keys if key > cursor_key]
        page_keys = keys[:limit]
        page = [seen[key].to_dict() for key in page_keys]
        next_cursor = encode_instance_cursor(page_keys[-1]) if len(keys) > len(page_keys) and page_keys else None
        return {"instances": page, "items": page, "nextCursor": next_cursor}

    recordRunStart = record_run_start
    recordRunEnd = record_run_end
    lookupRun = lookup_run
    listRuns = list_runs
    listInstances = list_instances


class SQLiteRunRegistry(InMemoryRunRegistry):
    """SQLite-backed Flue-style run pointer registry."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init_db()
        self._load_from_disk()

    def close(self) -> None:
        self._db.close()

    async def record_run_start(
        self,
        *,
        run_id: str,
        agent_name: str,
        instance_id: str,
        started_at: str | float | None = None,
    ) -> None:
        await super().record_run_start(
            run_id=run_id,
            agent_name=agent_name,
            instance_id=instance_id,
            started_at=started_at,
        )
        pointer = self._runs[run_id]
        self._persist_pointer(pointer)

    async def record_run_end(
        self,
        *,
        run_id: str,
        ended_at: str | float | None = None,
        duration_ms: int | None = None,
        is_error: bool = False,
    ) -> None:
        await super().record_run_end(
            run_id=run_id,
            ended_at=ended_at,
            duration_ms=duration_ms,
            is_error=is_error,
        )
        pointer = self._runs.get(run_id)
        if pointer is not None:
            self._persist_pointer(pointer)

    def _init_db(self) -> None:
        self._db.executescript(
            """
            create table if not exists run_registry (
                run_id text primary key,
                agent_name text not null,
                instance_id text not null,
                status text not null,
                started_at text not null,
                ended_at text,
                duration_ms integer,
                is_error integer
            );
            create index if not exists idx_run_registry_started on run_registry(started_at, run_id);
            create index if not exists idx_run_registry_instance on run_registry(agent_name, instance_id);
            """
        )
        self._db.commit()

    def _load_from_disk(self) -> None:
        rows = self._db.execute("select * from run_registry").fetchall()
        for row in rows:
            pointer = RunPointer(
                run_id=str(row["run_id"]),
                agent_name=str(row["agent_name"]),
                instance_id=str(row["instance_id"]),
                status=str(row["status"]),
                started_at=str(row["started_at"]),
                ended_at=str(row["ended_at"]) if row["ended_at"] is not None else None,
                duration_ms=int(row["duration_ms"]) if row["duration_ms"] is not None else None,
                is_error=bool(row["is_error"]) if row["is_error"] is not None else None,
            )
            self._runs[pointer.run_id] = pointer

    def _persist_pointer(self, pointer: RunPointer) -> None:
        self._db.execute(
            """
            insert into run_registry (
                run_id, agent_name, instance_id, status, started_at, ended_at, duration_ms, is_error
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(run_id) do update set
                agent_name = excluded.agent_name,
                instance_id = excluded.instance_id,
                status = excluded.status,
                started_at = excluded.started_at,
                ended_at = excluded.ended_at,
                duration_ms = excluded.duration_ms,
                is_error = excluded.is_error
            """,
            (
                pointer.run_id,
                pointer.agent_name,
                pointer.instance_id,
                pointer.status,
                pointer.started_at,
                pointer.ended_at,
                pointer.duration_ms,
                None if pointer.is_error is None else int(pointer.is_error),
            ),
        )
        self._db.commit()


def encode_run_cursor(pointer: RunPointer | dict[str, Any]) -> str:
    started_at = pointer.started_at if isinstance(pointer, RunPointer) else str(pointer["startedAt"])
    run_id = pointer.run_id if isinstance(pointer, RunPointer) else str(pointer["runId"])
    return _base64_url_encode(json.dumps({"s": started_at, "r": run_id}, separators=(",", ":")))


def decode_run_cursor(cursor: str | None) -> tuple[str, str] | None:
    if not cursor:
        return None
    try:
        decoded = json.loads(_base64_url_decode(cursor))
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error):
        return None
    if isinstance(decoded, dict) and isinstance(decoded.get("s"), str) and isinstance(decoded.get("r"), str):
        return decoded["s"], decoded["r"]
    return None


def encode_instance_cursor(key: str) -> str:
    return _base64_url_encode(key)


def decode_instance_cursor(cursor: str | None) -> str | None:
    if not cursor:
        return None
    try:
        decoded = _base64_url_decode(cursor)
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
        return None
    return decoded if "\0" in decoded else None


def _instance_key(agent_name: str, instance_id: str) -> str:
    return f"{agent_name}\0{instance_id}"


def _base64_url_encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _base64_url_decode(value: str) -> str:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")


def _registry_timestamp(value: str | float | None) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        value = time.time()
    return _iso_timestamp(float(value))


def _duration_ms(started_at: str, ended_at: str) -> int:
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return max(0, int((end - start).total_seconds() * 1000))


def _registry_limit(limit: int | None) -> int:
    if limit is None:
        return 100
    return max(1, min(int(limit), 1000))


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, default=str)


def _json_loads(value: Any) -> Any:
    if value is None:
        return None
    try:
        return json.loads(str(value))
    except Exception:
        return None


# ─── Process-wide default store ────────────────────────────────────────────
#
# A single store per Python process is the right granularity for `pyflue dev`
# and `pyflue invoke`. Larger deployments that need cross-process durability
# can replace this with a SQLite-backed store later.

_default_store: InMemoryRunStore | None = None


def get_default_run_store() -> InMemoryRunStore:
    global _default_store
    if _default_store is None:
        max_runs = int(os.environ.get("PYFLUE_RUN_STORE_MAX", "1024"))
        if os.environ.get("PYFLUE_RUN_STORE", "").lower() == "sqlite":
            path = os.environ.get("PYFLUE_RUN_STORE_PATH", ".pyflue/runs.sqlite3")
            _default_store = SQLiteRunStore(path, max_runs=max_runs)
        else:
            _default_store = InMemoryRunStore(max_runs=max_runs)
    return _default_store


def set_default_run_store(store: InMemoryRunStore | None) -> None:
    """Test hook: replace the process-global store."""
    global _default_store
    _default_store = store


def event_to_dict(event: RunEvent) -> dict[str, Any]:
    return asdict(event)


def observe(callback: Any) -> Any:
    """Register a process-global subscriber for every run event.

    The callback receives ``(run, event)`` and may be sync or async. Exceptions
    raised by the callback are swallowed — observers must never break the run
    loop. Returns the callback so it can be used as a decorator.

    Intended for cross-cutting integrations like error reporting, log
    forwarding, or metrics. Per-agent or per-context wiring should use the
    per-run subscriber on ``InMemoryRunStore.subscribe`` instead.
    """
    get_default_run_store().add_global_subscriber(callback)
    return callback


def unobserve(callback: Any) -> None:
    """Remove a previously-registered ``observe()`` callback."""
    get_default_run_store().remove_global_subscriber(callback)
