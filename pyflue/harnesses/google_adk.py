"""Deferred Google ADK backend."""

from __future__ import annotations

from typing import Any

from pyflue.harnesses.base import HarnessBackend
from pyflue.types import HarnessResult, PyFlueConfig, Skill


class GoogleADKBackend(HarnessBackend):
    """Optional backend for Google Agent Development Kit."""

    name = "google_adk"

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
        images: list[Any] | tuple[Any, ...] | None = None,
        stream: bool = False,
    ) -> HarnessResult:
        raise NotImplementedError(
            "Google ADK backend is planned. Use harness='deepagents' for v0.1."
        )
