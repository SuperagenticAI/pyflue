"""Per-call cancellation handle.

Returned from ``session.prompt``, ``session.skill``, ``session.task``, and
``session.shell``. The handle is awaitable (existing code that writes
``await session.prompt(...)`` keeps working) and adds a synchronous
``.abort(reason)`` plus a ``.signal`` event for cancellation, matching the
shape Flue's TS runtime exposes via ``CallHandle<T>`` with ``AbortSignal``.

The wrapped work starts immediately when the handle is created (we schedule
the coroutine on the running loop), so dropping the handle without awaiting
it does not silently skip the call. Use ``.abort()`` to stop it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Generator
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class CallHandle(Generic[T]):
    """Awaitable wrapper around one in-flight session call.

    Attributes:
        signal: ``asyncio.Event`` set when the handle is aborted (either via
            ``.abort()`` or by an external signal passed at construction).
        reason: Optional string set by ``.abort(reason)``; ``None`` until set.
    """

    __slots__ = ("_task", "_signal", "_reason", "_external_signal", "_link_task")

    def __init__(
        self,
        coro: Coroutine[Any, Any, T],
        *,
        signal: asyncio.Event | None = None,
    ) -> None:
        self._task: asyncio.Task[T] = asyncio.ensure_future(coro)
        self._signal: asyncio.Event = asyncio.Event()
        self._reason: str | None = None
        self._external_signal = signal
        self._link_task: asyncio.Task[None] | None = None
        if signal is not None:
            self._link_task = asyncio.ensure_future(self._link_external_signal(signal))
        # Make sure linking cleans up if we finish first.
        self._task.add_done_callback(self._on_task_done)

    @property
    def signal(self) -> asyncio.Event:
        return self._signal

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def task(self) -> asyncio.Task[T]:
        """Underlying ``asyncio.Task``. Use for advanced cases only."""
        return self._task

    def abort(self, reason: str | None = None) -> None:
        """Cancel the underlying task and set ``signal``.

        Safe to call multiple times. No-op if the task already finished.
        """
        if self._signal.is_set():
            return
        self._reason = reason
        self._signal.set()
        if not self._task.done():
            self._task.cancel()

    def done(self) -> bool:
        return self._task.done()

    def __await__(self) -> Generator[Any, None, T]:
        return self._task.__await__()

    async def _link_external_signal(self, signal: asyncio.Event) -> None:
        try:
            await signal.wait()
        except asyncio.CancelledError:
            return
        self.abort("external_signal")

    def _on_task_done(self, _task: asyncio.Task[T]) -> None:
        if self._link_task is not None and not self._link_task.done():
            self._link_task.cancel()
