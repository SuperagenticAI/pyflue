"""Development and webhook server helpers."""

from __future__ import annotations

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
        return {"text": result.text, "metadata": result.metadata}

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

    @app.post("/sessions/{session_id}/abort")
    async def abort_session(session_id: str) -> dict[str, Any]:
        agent = await get_agent()
        session = await agent.session(session_id)
        aborted = await session.abort()
        return {"aborted": aborted, "session_id": session_id}

    return app


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
