from __future__ import annotations

import aiosqlite
import pytest

from pyflue.core import PyFlueAgent
from pyflue.session_history import SessionHistory
from pyflue.types import PyFlueConfig


def test_session_history_builds_active_path_with_compaction_context():
    history = SessionHistory.empty()
    history.append_message("user", "old")
    kept_id = history.append_message("assistant", "kept")
    history.append_compaction(
        summary="summary",
        first_kept_entry_id=kept_id,
        tokens_before=42,
    )
    history.append_message("user", "new")

    assert history.build_context() == [
        ("summary", "[Context Summary]\n\nsummary"),
        ("assistant", "kept"),
        ("user", "new"),
    ]


def test_session_history_round_trips_v2_data():
    history = SessionHistory.empty()
    history.append_message("user", "hello", source="prompt")
    data = history.to_data(metadata={"session_id": "s1"})

    restored = SessionHistory.from_data(data)

    assert data["version"] == 2
    assert restored.build_context() == [("user", "hello")]
    assert restored.get_active_path()[0]["source"] == "prompt"


@pytest.mark.asyncio
async def test_session_store_migrates_legacy_messages_table(tmp_path):
    state_dir = tmp_path / ".pyflue" / "sessions"
    state_dir.mkdir(parents=True)
    async with aiosqlite.connect(state_dir / "legacy.sqlite3") as db:
        await db.execute(
            "create table messages "
            "(id integer primary key autoincrement, role text not null, content text not null)"
        )
        await db.execute(
            "insert into messages(role, content) values (?, ?)",
            ("user", "hello"),
        )
        await db.commit()

    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config)
    session = await agent.session("legacy")

    assert await session._all_messages() == [("user", "hello")]
