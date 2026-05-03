"""Deferred Pydantic AI backend."""

from __future__ import annotations

from typing import Any

from pyflue.harnesses.base import HarnessBackend
from pyflue.types import HarnessResult, PyFlueConfig, Skill


class PydanticAIBackend(HarnessBackend):
    """Optional backend for Pydantic AI."""

    name = "pydanticai"

    async def run(
        self,
        *,
        prompt: str,
        system_prompt: str,
        config: PyFlueConfig,
        skills: dict[str, Skill],
        sandbox: Any,
        session_id: str,
        python_backend: Any | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
        stream: bool = False,
    ) -> HarnessResult:
        raise NotImplementedError(
            "Pydantic AI backend is planned. Use harness='deepagents' for v0.1."
        )
