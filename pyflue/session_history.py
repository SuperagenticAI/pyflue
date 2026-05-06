"""Flue-compatible session history primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

EntryType = Literal["message", "compaction", "branch_summary"]
MessageSource = Literal["prompt", "skill", "shell", "task", "retry"] | str


@dataclass(frozen=True)
class ContextEntry:
    """A message plus the history entry that produced it."""

    role: str
    content: str
    entry: dict[str, Any] | None = None


@dataclass
class SessionHistory:
    """Persistent conversation history with compaction entries."""

    entries: list[dict[str, Any]] = field(default_factory=list)
    leaf_id: str | None = None

    @classmethod
    def empty(cls) -> SessionHistory:
        return cls()

    @classmethod
    def from_data(cls, data: dict[str, Any] | None) -> SessionHistory:
        if not data:
            return cls.empty()
        return cls(
            entries=list(data.get("entries") or []),
            leaf_id=data.get("leafId"),
        )

    @classmethod
    def from_rows(cls, rows: list[tuple[str, str]]) -> SessionHistory:
        history = cls.empty()
        for role, content in rows:
            history.append_message(role, content)
        return history

    def get_active_path(self) -> list[dict[str, Any]]:
        by_id = {str(entry["id"]): entry for entry in self.entries if "id" in entry}
        path: list[dict[str, Any]] = []
        current = by_id.get(self.leaf_id or "")
        while current is not None:
            path.append(current)
            parent_id = current.get("parentId")
            current = by_id.get(str(parent_id)) if parent_id else None
        return list(reversed(path))

    def build_context_entries(self) -> list[ContextEntry]:
        path = self.get_active_path()
        latest_compaction_index = _find_latest_compaction_index(path)
        if latest_compaction_index == -1:
            return _path_to_context_entries(path)

        compaction = path[latest_compaction_index]
        first_kept_id = compaction.get("firstKeptEntryId")
        first_kept_index = next(
            (index for index, entry in enumerate(path) if entry.get("id") == first_kept_id),
            -1,
        )
        kept_start = first_kept_index if first_kept_index >= 0 else latest_compaction_index + 1
        context = [
            ContextEntry(
                role="summary",
                content=_context_summary_text(str(compaction.get("summary") or "")),
                entry=compaction,
            )
        ]
        context.extend(_path_to_context_entries(path[kept_start:latest_compaction_index]))
        context.extend(_path_to_context_entries(path[latest_compaction_index + 1 :]))
        return context

    def build_context(self) -> list[tuple[str, str]]:
        return [(item.role, item.content) for item in self.build_context_entries()]

    def message_entries(self) -> list[dict[str, Any]]:
        return [entry for entry in self.get_active_path() if entry.get("type") == "message"]

    def get_latest_compaction(self) -> dict[str, Any] | None:
        for entry in reversed(self.get_active_path()):
            if entry.get("type") == "compaction":
                return entry
        return None

    def append_message(
        self,
        role: str,
        content: str,
        source: MessageSource | None = None,
    ) -> str:
        entry: dict[str, Any] = {
            "type": "message",
            "id": _generate_entry_id(self.entries),
            "parentId": self.leaf_id,
            "timestamp": _now(),
            "message": {"role": role, "content": content},
        }
        if source is not None:
            entry["source"] = source
        self._append_entry(entry)
        return str(entry["id"])

    def append_compaction(
        self,
        *,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: dict[str, Any] | None = None,
    ) -> str:
        if first_kept_entry_id not in {str(entry.get("id")) for entry in self.entries}:
            raise ValueError(f'Cannot compact: entry "{first_kept_entry_id}" does not exist.')
        entry: dict[str, Any] = {
            "type": "compaction",
            "id": _generate_entry_id(self.entries),
            "parentId": self.leaf_id,
            "timestamp": _now(),
            "summary": summary,
            "firstKeptEntryId": first_kept_entry_id,
            "tokensBefore": tokens_before,
        }
        if details is not None:
            entry["details"] = details
        self._append_entry(entry)
        return str(entry["id"])

    def append_branch_summary(
        self,
        *,
        summary: str,
        from_id: str,
        details: Any | None = None,
    ) -> str:
        entry: dict[str, Any] = {
            "type": "branch_summary",
            "id": _generate_entry_id(self.entries),
            "parentId": self.leaf_id,
            "timestamp": _now(),
            "fromId": from_id,
            "summary": summary,
        }
        if details is not None:
            entry["details"] = details
        self._append_entry(entry)
        return str(entry["id"])

    def remove_leaf_message(self, role: str, content: str) -> bool:
        if self.leaf_id is None:
            return False
        leaf = next((entry for entry in self.entries if entry.get("id") == self.leaf_id), None)
        if leaf is None or leaf.get("type") != "message":
            return False
        message = leaf.get("message") or {}
        if message.get("role") != role or message.get("content") != content:
            return False
        self.entries = [entry for entry in self.entries if entry.get("id") != self.leaf_id]
        self.leaf_id = leaf.get("parentId")
        return True

    def to_data(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        return {
            "version": 2,
            "entries": list(self.entries),
            "leafId": self.leaf_id,
            "metadata": metadata or {},
            "createdAt": created_at or now,
            "updatedAt": updated_at or now,
        }

    def _append_entry(self, entry: dict[str, Any]) -> None:
        self.entries.append(entry)
        self.leaf_id = str(entry["id"])


def _path_to_context_entries(path: list[dict[str, Any]]) -> list[ContextEntry]:
    context: list[ContextEntry] = []
    for entry in path:
        entry_type = entry.get("type")
        if entry_type == "message":
            message = entry.get("message") or {}
            context.append(
                ContextEntry(
                    role=str(message.get("role") or "user"),
                    content=str(message.get("content") or ""),
                    entry=entry,
                )
            )
        elif entry_type == "branch_summary":
            context.append(
                ContextEntry(
                    role="summary",
                    content=f"[Branch Summary]\n\n{entry.get('summary') or ''}",
                    entry=entry,
                )
            )
    return context


def _find_latest_compaction_index(path: list[dict[str, Any]]) -> int:
    for index in range(len(path) - 1, -1, -1):
        if path[index].get("type") == "compaction":
            return index
    return -1


def _context_summary_text(summary: str) -> str:
    return summary if summary.startswith("[Context Summary]") else f"[Context Summary]\n\n{summary}"


def _generate_entry_id(entries: list[dict[str, Any]]) -> str:
    known = {str(entry.get("id")) for entry in entries}
    for _ in range(100):
        candidate = uuid4().hex[:8]
        if candidate not in known:
            return candidate
    return uuid4().hex


def _now() -> str:
    return datetime.now(UTC).isoformat()
