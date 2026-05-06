"""Development and webhook server helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from pyflue.config import load_config
from pyflue.routing import discover_agent_routes, invoke_route


class WebhookRequest(BaseModel):
    """Request model for webhook endpoints."""

    payload: dict[str, Any] = {}


def create_app(config_path: str | Path = "pyflue.toml") -> Any:
    """Create a FastAPI app with agent webhook routes."""
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import HTMLResponse, StreamingResponse
    except Exception as exc:
        raise ImportError(
            "PyFlue server support requires FastAPI. Install with: pip install 'pyflue[server]'"
        ) from exc

    config = load_config(config_path)
    app = FastAPI(title="PyFlue Agent Server")
    app.state.pyflue_agent = None

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
        return {
            "ok": True,
            "root": str(config.root),
            "harness": config.harness,
            "sandbox": config.sandbox,
            "agents": sorted(routes),
            "active_sessions": active,
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

    @app.post("/agents/{name}/{agent_id}")
    async def run_agent(name: str, agent_id: str, request: WebhookRequest) -> Any:
        routes = discover_agent_routes(config.root, config.agents_dir)
        route = routes.get(name)
        if route is None:
            raise HTTPException(status_code=404, detail=f"Unknown agent route: {name}")
        if route.triggers.get("webhook") is False:
            raise HTTPException(status_code=404, detail=f"Agent route is not webhook-enabled: {name}")
        return await invoke_route(
            route,
            agent_id=agent_id,
            payload=request.payload,
            config_path=config_path,
        )

    @app.post("/prompt/{agent_id}")
    async def prompt(agent_id: str, request: WebhookRequest) -> dict[str, Any]:
        prompt_text = str(request.payload.get("prompt", ""))
        agent = await get_agent()
        session = await agent.session(agent_id)
        result = await session.prompt(prompt_text)
        return {"text": result.text, "metadata": result.metadata}

    @app.post("/prompt/{agent_id}/events")
    async def prompt_events(agent_id: str, request: WebhookRequest) -> Any:
        async def events() -> Any:
            prompt_text = str(request.payload.get("prompt", ""))
            agent = await get_agent()
            session = await agent.session(agent_id)
            async for event in session.stream(prompt_text):
                yield f"event: {event.type}\ndata: {json.dumps(event.data)}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/sessions/{session_id}/abort")
    async def abort_session(session_id: str) -> dict[str, Any]:
        agent = await get_agent()
        session = await agent.session(session_id)
        aborted = await session.abort()
        return {"aborted": aborted, "session_id": session_id}

    return app
