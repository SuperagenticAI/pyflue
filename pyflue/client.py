"""Python client for PyFlue HTTP servers."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import TypeAdapter

from pyflue.core import _parse_typed_result
from pyflue.types import (
    HarnessResult,
    PromptCost,
    PromptModel,
    PromptResultResponse,
    PromptUsage,
    PyFlueEvent,
)


class PyFlueClient:
    """Async client for deployed PyFlue servers."""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float | httpx.Timeout | None = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def close(self) -> None:
        """Close the underlying HTTP client when PyFlue created it."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> PyFlueClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def health(self) -> dict[str, Any]:
        """Fetch server health information."""
        response = await self._client.get(f"{self.base_url}/health")
        response.raise_for_status()
        return response.json()

    async def agents(self) -> list[dict[str, Any]]:
        """List available HTTP agent routes."""
        response = await self._client.get(f"{self.base_url}/agents")
        response.raise_for_status()
        return list(response.json().get("agents", []))

    async def prompt(
        self,
        prompt: str,
        *,
        session_id: str = "default",
        result: Any | None = None,
        payload: dict[str, Any] | None = None,
    ) -> HarnessResult | Any:
        """Run a prompt against `/prompt/{session_id}`."""
        body = {"payload": {"prompt": prompt, **(payload or {})}}
        response = await self._client.post(f"{self.base_url}/prompt/{session_id}", json=body)
        response.raise_for_status()
        data = response.json()
        output = HarnessResult(
            text=str(data.get("text", "")),
            metadata=dict(data.get("metadata") or {}),
            raw=data,
            usage=_prompt_usage_from_dict(data.get("usage")),
            model=PromptModel(**data["model"]) if isinstance(data.get("model"), dict) else PromptModel(),
        )
        if result is None:
            return output
        parsed = _parse_typed_result(output.text, result)
        return PromptResultResponse(
            result=parsed,
            text=output.text,
            usage=output.usage,
            model=output.model,
            raw=output.raw,
            metadata=output.metadata,
        )

    async def agent(
        self,
        name: str,
        agent_id: str,
        *,
        payload: dict[str, Any] | None = None,
        result: Any | None = None,
    ) -> Any:
        """Call a file-based agent route."""
        response = await self._client.post(
            f"{self.base_url}/agents/{name}/{agent_id}",
            json={"payload": payload or {}},
        )
        response.raise_for_status()
        data = response.json()
        if result is None:
            return data
        return TypeAdapter(result).validate_python(data)

    async def stream(
        self,
        prompt: str,
        *,
        session_id: str = "default",
        payload: dict[str, Any] | None = None,
    ) -> AsyncIterator[PyFlueEvent]:
        """Stream normalized prompt events from `/prompt/{session_id}/events`."""
        body = {"payload": {"prompt": prompt, **(payload or {})}}
        async with self._client.stream(
            "POST",
            f"{self.base_url}/prompt/{session_id}/events",
            json=body,
        ) as response:
            response.raise_for_status()
            event_type = "message"
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event_type = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    data = json.loads(line.removeprefix("data:").strip())
                    yield PyFlueEvent(event_type, data)


def _prompt_usage_from_dict(value: Any) -> PromptUsage:
    if not isinstance(value, dict):
        return PromptUsage()
    cost = value.get("cost")
    return PromptUsage(
        input=int(value.get("input") or 0),
        output=int(value.get("output") or 0),
        cache_read=int(value.get("cache_read") or 0),
        cache_write=int(value.get("cache_write") or 0),
        total_tokens=int(value.get("total_tokens") or 0),
        cost=PromptCost(
            input=float(cost.get("input") or 0) if isinstance(cost, dict) else 0.0,
            output=float(cost.get("output") or 0) if isinstance(cost, dict) else 0.0,
            cache_read=float(cost.get("cache_read") or 0) if isinstance(cost, dict) else 0.0,
            cache_write=float(cost.get("cache_write") or 0) if isinstance(cost, dict) else 0.0,
            total=float(cost.get("total") or 0) if isinstance(cost, dict) else 0.0,
        ),
    )
