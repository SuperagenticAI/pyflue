"""Development and webhook server helpers."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from time import time
from typing import Any

from starlette.requests import Request

from pyflue.config import load_config
from pyflue.errors import (
    MethodNotAllowedError,
    PyFlueError,
    error_envelope,
    parse_json_payload,
    validate_agent_request,
)
from pyflue.routing import discover_agent_routes, invoke_route
from pyflue.runs import get_default_run_store


def create_app(config_path: str | Path = "pyflue.toml") -> Any:
    """Create a FastAPI app with agent webhook routes."""
    try:
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
    except Exception as exc:
        raise ImportError(
            "PyFlue server support requires FastAPI. Install with: pip install 'pyflue[server]'"
        ) from exc

    if str(config_path) == "pyflue.toml":
        config_path = os.environ.get("PYFLUE_CONFIG", config_path)
    config = load_config(config_path)
    resolved_config_path = Path(config_path).expanduser().resolve()
    app = FastAPI(title="PyFlue Agent Server")
    app.state.pyflue_agent = None

    @app.exception_handler(PyFlueError)
    async def pyflue_error_handler(_request: Request, exc: PyFlueError) -> JSONResponse:
        return JSONResponse(error_envelope(exc, dev=True), status_code=exc.status)

    @app.exception_handler(Exception)
    async def internal_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        error = PyFlueError(
            type="internal_error",
            message="An internal error occurred.",
            details="The server encountered an unexpected error while handling this request.",
            dev=str(exc),
            status=500,
        )
        return JSONResponse(error_envelope(error, dev=True), status_code=500)

    async def get_agent() -> Any:
        from pyflue import init

        if app.state.pyflue_agent is None:
            app.state.pyflue_agent = await init(config_path=config_path)
        return app.state.pyflue_agent

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "framework": "pyflue"}

    @app.get("/__pyflue/status")
    async def status() -> dict[str, Any]:
        routes = discover_agent_routes(config.root, config.agents_dir)
        agent = app.state.pyflue_agent
        active = dict(getattr(agent, "_active_operations", {}) if agent is not None else {})
        skills_dir = config.skills_dir or config.root / ".agents" / "skills"
        roles_dir = config.roles_dir or config.root / ".agents" / "roles"
        return {
            "ok": True,
            "generated_at": time(),
            "root": str(config.root),
            "config_path": str(resolved_config_path),
            "config_mtime": _mtime(resolved_config_path),
            "harness": config.harness,
            "sandbox": config.sandbox,
            "route_count": len(routes),
            "routes": [_route_status(route) for route in sorted(routes.values(), key=lambda item: item.name)],
            "agents": sorted(routes),
            "skills": _markdown_status(skills_dir),
            "roles": _markdown_status(roles_dir),
            "active_sessions": [
                {"session_id": session_id, "operation": operation}
                for session_id, operation in sorted(active.items())
            ],
        }

    @app.get("/agents")
    async def list_agents() -> dict[str, Any]:
        routes = discover_agent_routes(config.root, config.agents_dir)
        return {
            "agents": [
                {
                    "name": item.name,
                    "path": item.url_path,
                    "triggers": item.triggers,
                }
                for item in routes.values()
            ]
        }

    @app.get("/__pyflue", response_class=HTMLResponse)
    async def dashboard() -> str:
        routes = discover_agent_routes(config.root, config.agents_dir)
        skills = sorted(path.name for path in (config.skills_dir or config.root / ".agents" / "skills").glob("*.md")) if (config.skills_dir or config.root / ".agents" / "skills").exists() else []
        route_items = "".join(
            f"<li><code>{route.url_path}</code> {route.triggers}</li>"
            for route in routes.values()
        )
        skill_items = "".join(f"<li><code>{skill}</code></li>" for skill in skills)
        return (
            "<!doctype html><html><head><title>PyFlue Dev</title>"
            "<style>body{background:#080808;color:#fff;font-family:Inter,system-ui,sans-serif;margin:2rem}"
            "code{color:#67e8f9}section{margin:1.5rem 0}</style></head><body>"
            "<h1>PyFlue Dev</h1>"
            f"<section><h2>Routes</h2><ul>{route_items}</ul></section>"
            f"<section><h2>Skills</h2><ul>{skill_items}</ul></section>"
            "</body></html>"
        )

    @app.api_route("/agents/{name}/{agent_id}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def run_agent(name: str, agent_id: str, request: Request) -> Any:
        if request.method != "POST":
            raise MethodNotAllowedError(request.method, ["POST"])
        routes = discover_agent_routes(config.root, config.agents_dir)
        validate_agent_request(
            name=name,
            agent_id=agent_id,
            registered_agents=sorted(routes),
            webhook_agents=sorted(
                route.name for route in routes.values() if route.triggers.get("webhook") is not False
            ),
        )
        payload = await parse_json_payload(request)
        route = routes[name]
        return await invoke_route(
            route,
            agent_id=agent_id,
            payload=payload,
            config_path=config_path,
        )

    @app.post("/prompt/{agent_id}")
    async def prompt(agent_id: str, request: Request) -> dict[str, Any]:
        payload = await parse_json_payload(request)
        prompt_text = str(payload.get("prompt", ""))
        agent = await get_agent()
        session = await agent.session(agent_id)
        result = await session.prompt(prompt_text)
        return {
            "text": result.text,
            "metadata": result.metadata,
            "usage": {
                "input": result.usage.input,
                "output": result.usage.output,
                "cache_read": result.usage.cache_read,
                "cache_write": result.usage.cache_write,
                "total_tokens": result.usage.total_tokens,
                "cost": {
                    "input": result.usage.cost.input,
                    "output": result.usage.cost.output,
                    "cache_read": result.usage.cost.cache_read,
                    "cache_write": result.usage.cost.cache_write,
                    "total": result.usage.cost.total,
                },
            },
            "model": {"id": result.model.id},
        }

    @app.post("/prompt/{agent_id}/events")
    async def prompt_events(agent_id: str, request: Request) -> Any:
        payload = await parse_json_payload(request)

        async def events() -> Any:
            prompt_text = str(payload.get("prompt", ""))
            agent = await get_agent()
            session = await agent.session(agent_id)
            try:
                async for event in session.stream(prompt_text):
                    yield f"event: {event.type}\ndata: {json.dumps(event.data)}\n\n"
            except PyFlueError as exc:
                yield f"event: error\ndata: {json.dumps(error_envelope(exc, dev=True))}\n\n"
            except Exception as exc:
                error = PyFlueError(
                    type="internal_error",
                    message="An internal error occurred.",
                    details="The server encountered an unexpected error while streaming this request.",
                    dev=str(exc),
                    status=500,
                )
                yield f"event: error\ndata: {json.dumps(error_envelope(error, dev=True))}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        store = get_default_run_store()
        run = store.get_run(run_id)
        if run is None:
            raise PyFlueError(
                type="run_not_found",
                message=f'Run "{run_id}" is not known to this server.',
                details="Run history is kept in process memory and may have been evicted.",
                status=404,
            )
        return run.to_dict()

    @app.get("/runs/{run_id}/events")
    async def get_run_events(run_id: str, request: Request) -> dict[str, Any]:
        store = get_default_run_store()
        if store.get_run(run_id) is None:
            raise PyFlueError(
                type="run_not_found",
                message=f'Run "{run_id}" is not known to this server.',
                details="Run history is kept in process memory and may have been evicted.",
                status=404,
            )
        after, limit, types = _parse_event_query(request.query_params)
        events = store.get_events(run_id, after=after, limit=limit, types=types)
        return {
            "run_id": run_id,
            "events": [event.to_dict() for event in events],
            "next_after": events[-1].event_index if events else after,
        }

    @app.get("/runs/{run_id}/stream")
    async def stream_run(run_id: str, request: Request) -> Any:
        store = get_default_run_store()
        if store.get_run(run_id) is None:
            raise PyFlueError(
                type="run_not_found",
                message=f'Run "{run_id}" is not known to this server.',
                details="Run history is kept in process memory and may have been evicted.",
                status=404,
            )
        # Respect Last-Event-ID for resume; query ?after= overrides.
        last_event_id = request.headers.get("last-event-id", "0")
        try:
            after = int(request.query_params.get("after") or last_event_id or 0)
        except ValueError:
            after = 0

        async def events() -> Any:
            import anyio

            async def heartbeat() -> Any:
                while True:
                    await anyio.sleep(15)
                    yield ": heartbeat\n\n"

            # Interleave subscriber + 15s heartbeats using a simple race loop.
            queue: asyncio.Queue[str | None] = asyncio.Queue()

            async def pump_events() -> None:
                async for event in store.subscribe(run_id, after=after):
                    line = (
                        f"id: {event.event_index}\n"
                        f"event: {event.type}\n"
                        f"data: {json.dumps(event.to_dict())}\n\n"
                    )
                    await queue.put(line)
                await queue.put(None)

            async def pump_heartbeat() -> None:
                while True:
                    await asyncio.sleep(15)
                    await queue.put(": heartbeat\n\n")

            ev_task = asyncio.create_task(pump_events())
            hb_task = asyncio.create_task(pump_heartbeat())
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        return
                    yield item
            finally:
                hb_task.cancel()
                ev_task.cancel()

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/sessions/{session_id}/abort")
    async def abort_session(session_id: str) -> dict[str, Any]:
        agent = await get_agent()
        session = await agent.session(session_id)
        aborted = await session.abort()
        return {"aborted": aborted, "session_id": session_id}

    return app


def _parse_event_query(query: Any) -> tuple[int, int, list[str] | None]:
    """Validate and return ``(after, limit, types)`` from a query mapping.

    Raises ``PyFlueError`` (status 400) on malformed values so the server's
    error handler renders the canonical envelope.
    """

    def _bad(field: str, value: str, reason: str) -> PyFlueError:
        return PyFlueError(
            type="invalid_query_param",
            message=f'Invalid value for query parameter "{field}".',
            details=reason,
            status=400,
            meta={"param": field, "value": value},
        )

    raw_after = query.get("after")
    after = 0
    if raw_after is not None and raw_after != "":
        try:
            after = int(raw_after)
        except ValueError as exc:
            raise _bad("after", str(raw_after), "Must be a non-negative integer.") from exc
        if after < 0:
            raise _bad("after", str(raw_after), "Must be a non-negative integer.")

    raw_limit = query.get("limit")
    limit = 1000
    if raw_limit is not None and raw_limit != "":
        try:
            limit = int(raw_limit)
        except ValueError as exc:
            raise _bad("limit", str(raw_limit), "Must be an integer in [1, 1000].") from exc
        if limit < 1 or limit > 1000:
            raise _bad("limit", str(raw_limit), "Must be an integer in [1, 1000].")

    raw_types = query.get("types")
    types: list[str] | None = None
    if raw_types:
        types = [t.strip() for t in raw_types.split(",") if t.strip()]
        if not types:
            raise _bad("types", str(raw_types), "Must be a comma-separated list of event types.")

    return after, limit, types


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _route_status(route: Any) -> dict[str, Any]:
    return {
        "name": route.name,
        "path": route.url_path,
        "source": str(route.path),
        "mtime": _mtime(Path(route.path)),
        "triggers": route.triggers,
    }


def _markdown_status(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    return [
        {
            "name": path.stem,
            "path": str(path),
            "mtime": _mtime(path),
        }
        for path in sorted(root.rglob("*.md"))
    ]
