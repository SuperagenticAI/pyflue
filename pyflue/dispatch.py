"""Asynchronous agent input via ``dispatch()`` (parity item 5).

``dispatch(agent, id=, session=, input=)`` accepts a JSON-serialisable payload
for a persistent agent instance and returns immediately with a
:class:`DispatchReceipt`. The agent processes the input asynchronously as a
prompt operation in the targeted session. It does not block the caller and
does not create a workflow run (it correlates by instance/operation, like a
direct prompt).

This mirrors the TypeScript Flue reference's ``dispatch`` for verified webhooks,
queue messages, and chat events. On this Node-equivalent path delivery uses
process-memory admission, so accepted work can be lost on restart; use a durable
delivery architecture when restart-safe processing is required.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyflue.agents import init_agent, is_created_agent


@dataclass(frozen=True)
class DispatchReceipt:
    """Admission record returned when input is accepted for async processing."""

    dispatch_id: str
    accepted_at: str


class DispatchQueue:
    """Process-memory admission for asynchronously delivered agent input.

    Tracks the in-flight delivery tasks so they are not garbage-collected and
    can be awaited (``join``) in tests or graceful shutdown.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    def submit(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def join(self) -> None:
        """Await all in-flight deliveries (useful in tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


_default_queue = DispatchQueue()


def get_default_dispatch_queue() -> DispatchQueue:
    return _default_queue


async def dispatch(
    agent: Any,
    *,
    id: str,
    input: Any,
    session: str | None = None,
    config_path: str | Path = "pyflue.toml",
    queue: DispatchQueue | None = None,
) -> DispatchReceipt:
    """Accept ``input`` for asynchronous processing by an agent instance.

    ``agent`` is a created agent from :func:`~pyflue.create_agent`. ``id``
    selects the continuing instance and ``session`` its conversation thread.
    ``input`` must be JSON-serialisable (use ``None`` for an intentional empty
    payload). Returns a :class:`DispatchReceipt` immediately; the agent reply,
    if any, is produced by the agent's own tools, not returned here.
    """
    if not is_created_agent(agent):
        raise ValueError(
            "[pyflue] dispatch() requires a created agent from create_agent()."
        )
    if not isinstance(id, str) or not id.strip():
        raise ValueError('[pyflue] dispatch() requires a non-empty "id".')
    if session is not None and (not isinstance(session, str) or not session.strip()):
        raise ValueError('[pyflue] dispatch() "session" must be a non-empty string.')
    _assert_json_serializable(input)

    dispatch_id = f"dispatch_{uuid.uuid4().hex}"
    accepted_at = datetime.now(UTC).isoformat()
    target_queue = queue or _default_queue
    target_queue.submit(
        _deliver(
            agent,
            instance_id=id,
            session=session,
            input=input,
            config_path=config_path,
        )
    )
    return DispatchReceipt(dispatch_id=dispatch_id, accepted_at=accepted_at)


async def _deliver(
    agent: Any,
    *,
    instance_id: str,
    session: str | None,
    input: Any,
    config_path: str | Path,
) -> None:
    harness = await init_agent(agent, id=instance_id, payload=input, config_path=config_path)
    session_key = f"{instance_id}:{session or 'default'}"
    pyflue_session = await harness.session(session_key)
    message = input if isinstance(input, str) else json.dumps(input)
    await pyflue_session.prompt(message)


def _assert_json_serializable(value: Any) -> None:
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"[pyflue] dispatch() input must be JSON-serializable: {exc}"
        ) from exc


__all__ = [
    "DispatchQueue",
    "DispatchReceipt",
    "dispatch",
    "get_default_dispatch_queue",
]
