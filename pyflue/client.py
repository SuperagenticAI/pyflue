"""Python client for PyFlue HTTP servers."""

from __future__ import annotations

import contextlib
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
        admin_base_path: str = "/admin",
        agent_response_format: str = "raw",
    ):
        self.base_url = base_url.rstrip("/")
        self.admin_base_path = _normalize_base_path(admin_base_path)
        self.agent_response_format = _normalize_agent_response_format(agent_response_format)
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self.agents = _AgentsClient(self)
        self.workflows = _WorkflowsClient(self)
        self.runs = _RunsClient(self)
        self.admin = _AdminClient(self)

    def _ws_url(self, path: str) -> str:
        """Convert the configured base URL to a ws:// or wss:// URL for ``path``."""
        base = self.base_url
        if base.startswith("https://"):
            return "wss://" + base[len("https://") :] + path
        if base.startswith("http://"):
            return "ws://" + base[len("http://") :] + path
        return base + path

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
        data = await self.agents.invoke(
            name,
            agent_id,
            mode="sync",
            payload=payload,
            response_format="raw",
        )
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


def create_flue_client(
    base_url: str | None = None,
    **options: Any,
) -> PyFlueClient:
    """Create a PyFlue HTTP client.

    This mirrors Flue's SDK factory while keeping Python's class API available.
    Use ``base_url`` or the Flue-style ``baseUrl`` option.
    """
    if base_url is None:
        base_url = options.pop("baseUrl", None)
    else:
        options.pop("baseUrl", None)
    if base_url is None:
        raise TypeError("create_flue_client() requires base_url or baseUrl")
    if "adminBasePath" in options and "admin_base_path" not in options:
        options["admin_base_path"] = options.pop("adminBasePath")
    if "agentResponseFormat" in options and "agent_response_format" not in options:
        options["agent_response_format"] = options.pop("agentResponseFormat")
    options.setdefault("agent_response_format", "flue")
    return PyFlueClient(base_url, **options)


createFlueClient = create_flue_client


class _AgentsClient:
    """Agent-route client namespace.

    The object is callable so existing ``await client.agents()`` code keeps
    listing routes while newer code can use ``client.agents.invoke(...)``.
    """

    def __init__(self, root: PyFlueClient):
        self._root = root

    async def __call__(self) -> list[dict[str, Any]]:
        """List available HTTP agent routes."""
        response = await self._root._client.get(f"{self._root.base_url}/agents")
        response.raise_for_status()
        return list(response.json().get("agents", []))

    @contextlib.asynccontextmanager
    async def connect(self, name: str, agent_id: str) -> AsyncIterator[_AgentConnection]:
        """Open a persistent WebSocket to an agent instance for multiple prompts.

            async with client.agents.connect("assistant", "inst-1") as conn:
                reply = await conn.prompt("hello")
        """
        import websockets

        url = self._root._ws_url(f"/agents/{name}/{agent_id}")
        async with websockets.connect(url) as socket:
            yield _AgentConnection(socket)

    def invoke(
        self,
        name: str,
        agent_id: str,
        options: dict[str, Any] | None = None,
        *,
        mode: str | None = None,
        payload: dict[str, Any] | None = None,
        response_format: str | None = None,
    ) -> Any:
        """Invoke an agent route in sync, webhook, or stream mode.

        Supports both Pythonic keyword arguments and the Flue SDK-style
        options dict: ``client.agents.invoke(name, id, {"mode": "sync",
        "payload": {...}})``.
        """
        resolved = _invoke_options(
            options,
            mode=mode,
            payload=payload,
            response_format=response_format,
        )
        if resolved["mode"] == "stream":
            return self.stream(name, agent_id, payload=resolved["payload"])
        return self._invoke_json(
            name,
            agent_id,
            mode=resolved["mode"],
            payload=resolved["payload"],
            response_format=resolved["response_format"],
        )

    async def _invoke_json(
        self,
        name: str,
        agent_id: str,
        *,
        mode: str = "sync",
        payload: dict[str, Any] | None = None,
        response_format: str | None = None,
    ) -> Any:
        """Invoke an agent route in sync, webhook, or stream mode."""
        if mode == "stream":
            return self.stream(name, agent_id, payload=payload)
        headers = {"x-webhook": "true"} if mode == "webhook" else None
        if mode not in {"sync", "webhook"}:
            raise ValueError("mode must be one of: sync, stream, webhook")
        response = await self._root._client.post(
            f"{self._root.base_url}/agents/{name}/{agent_id}",
            json={"payload": payload or {}},
            headers=headers,
        )
        response.raise_for_status()
        body = response.json()
        resolved_format = response_format or self._root.agent_response_format
        if resolved_format == "raw":
            return body
        return _shape_agent_response(
            body,
            mode=mode,
            header_run_id=response.headers.get("x-flue-run-id"),
        )

    async def stream(
        self,
        name: str,
        agent_id: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> AsyncIterator[PyFlueEvent]:
        """Stream run events for one agent route invocation."""
        async with self._root._client.stream(
            "POST",
            f"{self._root.base_url}/agents/{name}/{agent_id}",
            json={"payload": payload or {}},
            headers={"accept": "text/event-stream"},
        ) as response:
            response.raise_for_status()
            async for event in _iter_sse_events(response):
                yield event


class _WorkflowsClient:
    """Workflow client namespace: HTTP invocation/streaming and WebSocket runs."""

    def __init__(self, root: PyFlueClient):
        self._root = root

    async def invoke(
        self,
        name: str,
        payload: dict[str, Any] | None = None,
        *,
        wait: bool = False,
    ) -> dict[str, Any]:
        """Start a workflow.

        Returns a ``{status, run_id}`` receipt, or the completed
        ``{status, run_id, result}`` envelope when ``wait=True``.
        """
        params = {"wait": "result"} if wait else None
        response = await self._root._client.post(
            f"{self._root.base_url}/workflows/{name}",
            json={"payload": payload or {}},
            params=params,
        )
        response.raise_for_status()
        return response.json()

    async def stream(
        self, name: str, payload: dict[str, Any] | None = None
    ) -> AsyncIterator[PyFlueEvent]:
        """Start a workflow and stream its run events over SSE."""
        async with self._root._client.stream(
            "POST",
            f"{self._root.base_url}/workflows/{name}",
            json={"payload": payload or {}},
            headers={"accept": "text/event-stream"},
        ) as response:
            response.raise_for_status()
            async for event in _iter_sse_events(response):
                yield event

    @contextlib.asynccontextmanager
    async def connect(self, name: str) -> AsyncIterator[_WorkflowConnection]:
        """Open a WebSocket to run one workflow and read its events + result."""
        import websockets

        url = self._root._ws_url(f"/workflows/{name}")
        async with websockets.connect(url) as socket:
            yield _WorkflowConnection(socket)


class _AgentConnection:
    """A live WebSocket connection to a persistent agent instance."""

    def __init__(self, socket: Any):
        self._socket = socket

    async def prompt(self, message: str, *, session: str = "default") -> dict[str, Any]:
        """Send one prompt and return the agent's result message."""
        await self._socket.send(
            json.dumps({"type": "prompt", "message": message, "session": session})
        )
        return json.loads(await self._socket.recv())


class _WorkflowConnection:
    """A live WebSocket connection to one workflow invocation."""

    def __init__(self, socket: Any):
        self._socket = socket

    async def run(self, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Send the payload and collect messages through the terminal result/error."""
        await self._socket.send(json.dumps({"payload": payload or {}}))
        messages: list[dict[str, Any]] = []
        while True:
            message = json.loads(await self._socket.recv())
            messages.append(message)
            if message.get("type") in ("result", "error"):
                break
        return messages


class _RunsClient:
    """Run inspection client namespace."""

    def __init__(self, root: PyFlueClient):
        self._root = root

    async def get(self, run_id: str) -> dict[str, Any]:
        response = await self._root._client.get(f"{self._root.base_url}/runs/{run_id}")
        response.raise_for_status()
        return response.json()

    async def events(
        self,
        run_id: str,
        *,
        after: int | None = None,
        types: list[str] | tuple[str, ...] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if after is not None:
            params["after"] = after
        if types is not None:
            params["types"] = ",".join(types)
        if limit is not None:
            params["limit"] = limit
        response = await self._root._client.get(
            f"{self._root.base_url}/runs/{run_id}/events",
            params=params,
        )
        response.raise_for_status()
        return response.json()

    async def stream(self, run_id: str, *, after: int | None = None) -> AsyncIterator[PyFlueEvent]:
        headers = {"accept": "text/event-stream"}
        if after is not None:
            headers["last-event-id"] = str(after)
        async with self._root._client.stream(
            "GET",
            f"{self._root.base_url}/runs/{run_id}/stream",
            headers=headers,
        ) as response:
            response.raise_for_status()
            async for event in _iter_sse_events(response):
                yield event


class _AdminClient:
    """Read-only admin API client namespace."""

    def __init__(self, root: PyFlueClient):
        self._root = root
        self.agents = _AdminAgentsClient(root)
        self.instances = _AdminInstancesClient(root)
        self.runs = _AdminRunsClient(root)


class _AdminAgentsClient:
    def __init__(self, root: PyFlueClient):
        self._root = root

    async def list(self, *, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
        return await _admin_get(
            self._root,
            "/agents",
            params=_list_params(cursor=cursor, limit=limit),
        )


class _AdminInstancesClient:
    def __init__(self, root: PyFlueClient):
        self._root = root

    async def list(
        self,
        agent_name: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return await _admin_get(
            self._root,
            f"/agents/{agent_name}/instances",
            params=_list_params(cursor=cursor, limit=limit),
        )


class _AdminRunsClient:
    def __init__(self, root: PyFlueClient):
        self._root = root

    async def list(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        status: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        params = _list_params(cursor=cursor, limit=limit)
        if status is not None:
            params["status"] = status
        if agent_name is not None:
            params["agentName"] = agent_name
        return await _admin_get(self._root, "/runs", params=params or None)

    async def list_for_instance(
        self,
        agent_name: str,
        agent_id: str,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        params = _list_params(cursor=cursor, limit=limit)
        if status is not None:
            params["status"] = status
        return await _admin_get(
            self._root,
            f"/agents/{agent_name}/instances/{agent_id}/runs",
            params=params or None,
        )

    async def get(self, run_id: str) -> dict[str, Any]:
        return await _admin_get(self._root, f"/runs/{run_id}")


async def _admin_get(
    root: PyFlueClient,
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await root._client.get(
        f"{root.base_url}{root.admin_base_path}{path}",
        params=params,
    )
    response.raise_for_status()
    return response.json()


def _normalize_base_path(path: str) -> str:
    stripped = path.strip("/")
    return f"/{stripped}" if stripped else ""


def _list_params(*, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if cursor is not None:
        params["cursor"] = cursor
    if limit is not None:
        params["limit"] = limit
    return params


def _invoke_options(
    options: dict[str, Any] | None,
    *,
    mode: str | None,
    payload: dict[str, Any] | None,
    response_format: str | None,
) -> dict[str, Any]:
    if options is not None and not isinstance(options, dict):
        raise TypeError("options must be a dict when provided")
    resolved = dict(options or {})
    if mode is not None:
        resolved["mode"] = mode
    if payload is not None:
        resolved["payload"] = payload
    if response_format is not None:
        resolved["response_format"] = response_format
    if "responseFormat" in resolved and "response_format" not in resolved:
        resolved["response_format"] = resolved["responseFormat"]
    resolved_mode = str(resolved.get("mode") or "sync")
    if resolved_mode not in {"sync", "stream", "webhook"}:
        raise ValueError("mode must be one of: sync, stream, webhook")
    return {
        "mode": resolved_mode,
        "payload": resolved.get("payload"),
        "response_format": (
            _normalize_agent_response_format(str(resolved["response_format"]))
            if "response_format" in resolved
            else None
        ),
    }


def _normalize_agent_response_format(value: str) -> str:
    normalized = value.replace("-", "_").lower()
    if normalized in {"raw", "pyflue"}:
        return "raw"
    if normalized in {"flue", "sdk"}:
        return "flue"
    raise ValueError("agent response format must be one of: raw, flue")


def _shape_agent_response(body: dict[str, Any], *, mode: str, header_run_id: str | None) -> dict[str, Any]:
    meta = body.get("_meta") if isinstance(body.get("_meta"), dict) else {}
    run_id = (
        meta.get("runId")
        or meta.get("run_id")
        or body.get("runId")
        or body.get("run_id")
        or header_run_id
    )
    if not run_id:
        raise ValueError("Flue response did not include a runId.")
    if mode == "webhook":
        return {"runId": run_id}
    result = body.get("result") if "result" in body else {
        key: value
        for key, value in body.items()
        if key not in {"_meta", "runId", "run_id"}
    }
    return {"result": result, "runId": run_id}


async def _iter_sse_events(response: httpx.Response) -> AsyncIterator[PyFlueEvent]:
    event_type = "message"
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                data = json.loads("\n".join(data_lines))
                yield PyFlueEvent(event_type, data)
            event_type = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").lstrip())
    if data_lines:
        data = json.loads("\n".join(data_lines))
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
