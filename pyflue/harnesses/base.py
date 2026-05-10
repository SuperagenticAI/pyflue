"""Harness backend interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from pyflue.types import HarnessResult, PyFlueConfig, PyFlueEvent, Skill


class HarnessBackend(ABC):
    """Backend contract implemented by all harness integrations."""

    name: str

    @abstractmethod
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
        """Run one prompt turn."""

    async def shell(
        self,
        command: str,
        *,
        sandbox: Any,
        timeout: int | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Run a shell command through the configured sandbox."""
        return sandbox.shell(command, timeout=timeout, cwd=cwd, env=env)

    async def stream(
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
    ) -> AsyncIterator[PyFlueEvent]:
        """Stream one prompt turn, falling back to one complete delta."""
        result = await self.run(
            prompt=prompt,
            system_prompt=system_prompt,
            config=config,
            skills=skills,
            sandbox=sandbox,
            session_id=session_id,
            python_backend=python_backend,
            tools=tools,
            images=images,
            stream=True,
        )
        if result.text:
            yield PyFlueEvent("delta", {"text": result.text})
        yield PyFlueEvent("end", {"text": result.text, "metadata": result.metadata})
