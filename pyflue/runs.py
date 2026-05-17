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
import os
import secrets
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Iterable
from dataclasses import asdict, dataclass, field
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
            "event_index": self.event_index,
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
        return {
            "run_id": self.run_id,
            "agent": self.agent,
            "agent_id": self.agent_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "is_error": self.is_error,
            "error": dict(self.error) if self.error else None,
            "event_count": self.event_count,
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

    def list_runs(self, *, limit: int = 100) -> list[FlueRun]:
        # Most-recent first.
        return list(self._runs.values())[-limit:][::-1]

    def list_agents(self) -> list[str]:
        """Return all agent names that have at least one tracked run."""
        return sorted(self._by_agent.keys())

    def list_instances(self, agent: str) -> list[str]:
        """Return all agent_id values seen for ``agent``."""
        return sorted(self._by_agent.get(agent, set()))

    def list_runs_for_instance(
        self, agent: str, agent_id: str, *, limit: int = 100
    ) -> list[FlueRun]:
        """Return most-recent-first runs for one ``(agent, agent_id)``."""
        ids = self._by_instance.get((agent, agent_id), [])[-limit:][::-1]
        return [self._runs[rid] for rid in ids if rid in self._runs]

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
                try:
                    ids.remove(old_id)
                except ValueError:
                    pass
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
        try:
            self._global_subscribers.remove(callback)
        except ValueError:
            pass


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
