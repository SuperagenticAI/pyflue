"""WebSocket message contract for agents and workflows (parity item 9).

Two connection shapes mirror the TypeScript Flue reference:

* **Agent WebSocket** — persistent. The client sends prompt messages over one
  connection and receives a result per prompt; the socket stays open for the
  continuing instance/session.
* **Workflow WebSocket** — finite. The client sends one invocation payload; the
  server streams the run's events and a final result, then closes.

The wire format is JSON objects. These dataclasses document and (on the client)
construct/parse those objects; handlers exchange plain dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentPromptMessage:
    """Client → server: prompt a persistent agent instance."""

    message: str
    session: str = "default"
    type: str = "prompt"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "message": self.message, "session": self.session}


@dataclass(frozen=True)
class AgentResultMessage:
    """Server → client: the result of one prompt."""

    result: dict[str, Any]
    type: str = "result"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "result": self.result}


@dataclass(frozen=True)
class WorkflowInvokeMessage:
    """Client → server: start a workflow run with a payload."""

    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"payload": self.payload}


@dataclass(frozen=True)
class WorkflowResultMessage:
    """Server → client: the terminal workflow result."""

    run_id: str
    result: Any
    type: str = "result"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "run_id": self.run_id, "runId": self.run_id, "result": self.result}


@dataclass(frozen=True)
class WebSocketErrorMessage:
    """Server → client: an error envelope."""

    error: dict[str, Any]
    type: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "error": self.error}


__all__ = [
    "AgentPromptMessage",
    "AgentResultMessage",
    "WebSocketErrorMessage",
    "WorkflowInvokeMessage",
    "WorkflowResultMessage",
]
