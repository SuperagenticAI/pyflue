"""Shared PyFlue types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

SandboxName = Literal["virtual", "local", "daytona", "e2b", "modal", "runloop"]


@dataclass(frozen=True)
class Skill:
    """Markdown-defined reusable agent workflow."""

    name: str
    description: str = ""
    instructions: str = ""
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    path: Path | None = None


@dataclass(frozen=True)
class Role:
    """Markdown-defined scoped behavior for a prompt or child task."""

    name: str
    instructions: str
    description: str = ""
    model: str | None = None
    path: Path | None = None


@dataclass
class PyFlueConfig:
    """Runtime configuration for one PyFlue agent."""

    model: str | None = None
    harness: str = "deepagents"
    sandbox: str = "virtual"
    python_backend: str | None = None
    root: Path = field(default_factory=Path.cwd)
    skills_dir: Path | None = None
    roles_dir: Path | None = None
    agents_dir: Path | None = None
    state_dir: Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    allowed_commands: tuple[str, ...] = ()
    allow_compound_commands: bool = False
    typed_retries: int = 3
    harness_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class HarnessResult:
    """Normalized response from a harness backend."""

    text: str
    raw: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PyFlueEvent:
    """Normalized event emitted by PyFlue streaming APIs."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
