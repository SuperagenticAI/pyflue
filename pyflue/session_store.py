"""Pluggable session stores (parity with the Flue Data Persistence API).

A :class:`SessionStore` persists a session's conversation state — the
serialized history produced by :meth:`SessionHistory.to_data` — keyed by a
session identity. The reference exposes this so applications can choose where
agent conversation state lives.

pyflue's built-in per-session SQLite persistence remains the default mechanism
for :class:`~pyflue.core.PyFlueSession`. These stores are the pluggable,
standalone building blocks a created agent's ``persist`` hook selects; they are
usable directly for application-owned conversation storage.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import aiosqlite

# Serialized session conversation state (see SessionHistory.to_data()).
SessionData = dict[str, Any]


@runtime_checkable
class SessionStore(Protocol):
    """Async load/save/delete contract for one session's conversation state."""

    async def load(self, key: str) -> SessionData | None: ...

    async def save(self, key: str, data: SessionData) -> None: ...

    async def delete(self, key: str) -> None: ...


class InMemorySessionStore:
    """Process-memory session store. Useful for tests and ephemeral runtimes."""

    def __init__(self) -> None:
        self._data: dict[str, SessionData] = {}

    async def load(self, key: str) -> SessionData | None:
        value = self._data.get(key)
        return copy.deepcopy(value) if value is not None else None

    async def save(self, key: str, data: SessionData) -> None:
        self._data[key] = copy.deepcopy(data)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def keys(self) -> list[str]:
        return list(self._data)


class SQLiteSessionStore:
    """Durable session store backed by a single SQLite file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False

    async def _ensure(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS sessions (key TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            await db.commit()
        self._initialized = True

    async def load(self, key: str) -> SessionData | None:
        await self._ensure()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT data FROM sessions WHERE key = ?", (key,))
            row = await cursor.fetchone()
        return json.loads(row[0]) if row else None

    async def save(self, key: str, data: SessionData) -> None:
        await self._ensure()
        payload = json.dumps(data)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO sessions (key, data) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET data = excluded.data",
                (key, payload),
            )
            await db.commit()

    async def delete(self, key: str) -> None:
        await self._ensure()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM sessions WHERE key = ?", (key,))
            await db.commit()


__all__ = [
    "InMemorySessionStore",
    "SQLiteSessionStore",
    "SessionData",
    "SessionStore",
]
