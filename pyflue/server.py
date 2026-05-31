"""Development and webhook server helpers."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from pathlib import Path
from time import time
from typing import Any

from starlette.requests import Request

try:
    # Imported at module scope so FastAPI can resolve the `WebSocket` annotation
    # under `from __future__ import annotations`. Guarded so the module still
    # imports without the server extra (create_app raises a clear error then).
    from fastapi import WebSocket, WebSocketDisconnect
except ImportError:  # pragma: no cover - server extra not installed
    WebSocket = Any  # type: ignore[assignment,misc]
    WebSocketDisconnect = Exception  # type: ignore[assignment,misc]

from pyflue.agents import is_created_agent
from pyflue.api_schemas import (
    AbortSessionResponse,
    AgentListResponse,
    DevStatusResponse,
    ErrorEnvelope,
    HealthResponse,
    PromptResponse,
    RunEventListResponse,
    RunRecordResponse,
    WebhookAcceptedResponse,
)
from pyflue.config import load_config
from pyflue.errors import (
    MethodNotAllowedError,
    PyFlueError,
    error_envelope,
    parse_json_payload,
    validate_agent_request,
)
from pyflue.routing import (
    AgentInstanceManager,
    discover_agent_routes,
    invoke_route,
    load_agent_default,
)
from pyflue.runs import generate_run_id, get_default_run_store


def _prompt_result_payload(result: Any) -> dict[str, Any]:
    """Shape a prompt result into the reference ``{text, usage, model}`` envelope."""
    usage = result.usage
    return {
        "text": result.text,
        "metadata": getattr(result, "metadata", {}),
        "usage": {
            "input": usage.input,
            "output": usage.output,
            "cache_read": usage.cache_read,
            "cache_write": usage.cache_write,
            "total_tokens": usage.total_tokens,
            "cost": {
                "input": usage.cost.input,
                "output": usage.cost.output,
                "cache_read": usage.cost.cache_read,
                "cache_write": usage.cost.cache_write,
                "total": usage.cost.total,
            },
        },
        "model": {"id": result.model.id},
    }


def create_app(config_path: str | Path = "pyflue.toml") -> Any:
    """Create a FastAPI app with agent webhook routes."""
    try:
        from fastapi import FastAPI
        from fastapi.encoders import jsonable_encoder
        from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
    except Exception as exc:
        raise ImportError(
            "PyFlue server support requires FastAPI. Install with: pip install 'pyflue[server]'"
        ) from exc

    if str(config_path) == "pyflue.toml":
        config_path = os.environ.get("PYFLUE_CONFIG", config_path)
    config = load_config(config_path)
    resolved_config_path = config.config_path or Path(config_path).expanduser().resolve()
    error_responses = {
        400: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        405: {"model": ErrorEnvelope},
        415: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
    }
    app = FastAPI(
        title="PyFlue Agent Server",
        description="Public PyFlue agent invocation and run inspection API.",
    )
    app.state.pyflue_agent = None
    app.state.instance_manager = AgentInstanceManager()
    from pyflue.admin import create_admin_app

    app.mount("/admin", create_admin_app(get_default_run_store()))

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

    @app.get("/health", response_model=HealthResponse)
    async def health() -> dict[str, Any]:
        return {"ok": True, "framework": "pyflue"}

    @app.get("/__pyflue/status", response_model=DevStatusResponse)
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
            "sandbox": config.sandbox
            if isinstance(config.sandbox, str)
            else getattr(config.sandbox, "__name__", "factory"),
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

    @app.get("/agents", response_model=AgentListResponse)
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

    async def _run_persistent_agent(
        name: str, instance_id: str, request: Request, created_agent: Any
    ) -> Any:
        """Handle a direct prompt to a persistent agent instance.

        Continues the instance's session, persists conversation state, and
        returns the reference ``{result: {...}}`` envelope. This is an agent
        interaction, not a workflow run, so it creates no run id.
        """
        body = await parse_json_payload(request)
        message = str(body.get("message") or body.get("prompt") or "")
        session_name = str(body.get("session") or "default")
        pf = await app.state.instance_manager.get_or_create(
            name=name,
            created_agent=created_agent,
            instance_id=instance_id,
            config_path=config_path,
        )
        session_key = f"{instance_id}:{session_name}"

        accept = request.headers.get("accept", "")
        if "text/event-stream" in accept.lower():
            async def events() -> Any:
                session = await pf.session(session_key)
                try:
                    async for event in session.stream(message):
                        yield (
                            f"event: {event.type}\n"
                            f"data: {json.dumps({'type': event.type, **event.data})}\n\n"
                        )
                except Exception as exc:
                    error = PyFlueError(
                        type="internal_error",
                        message="An internal error occurred.",
                        details="The server encountered an unexpected error while streaming this agent.",
                        dev=str(exc),
                        status=500,
                    )
                    yield f"event: error\ndata: {json.dumps(error_envelope(error, dev=True))}\n\n"

            return StreamingResponse(events(), media_type="text/event-stream")

        session = await pf.session(session_key)
        result = await session.prompt(message)
        return JSONResponse(jsonable_encoder({"result": _prompt_result_payload(result)}))

    @app.post(
        "/agents/{name}/{agent_id}",
        responses={
            200: {
                "description": "Synchronous agent result, or SSE stream when Accept is text/event-stream.",
                "content": {
                    "application/json": {"schema": {"type": "object", "additionalProperties": True}},
                    "text/event-stream": {"schema": {"type": "string"}},
                },
            },
            202: {"model": WebhookAcceptedResponse, "description": "Webhook accepted."},
            **error_responses,
        },
    )
    async def run_agent(name: str, agent_id: str, request: Request) -> Any:
        routes = discover_agent_routes(config.root, config.agents_dir)
        validate_agent_request(
            name=name,
            agent_id=agent_id,
            registered_agents=sorted(routes),
            webhook_agents=sorted(
                route.name for route in routes.values() if route.triggers.get("webhook") is not False
            ),
        )
        route = routes[name]
        default_obj = load_agent_default(route.path)
        if is_created_agent(default_obj):
            return await _run_persistent_agent(name, agent_id, request, default_obj)
        payload = await parse_json_payload(request)
        run_id = generate_run_id()
        store = get_default_run_store()

        async def invoke() -> Any:
            return await invoke_route(
                route,
                agent_id=agent_id,
                payload=payload,
                config_path=config_path,
                run_store=store,
                run_id=run_id,
            )

        if request.headers.get("x-webhook", "").lower() == "true":
            task = asyncio.create_task(invoke())
            task.add_done_callback(_consume_background_exception)
            return JSONResponse(
                {"status": "accepted", "run_id": run_id, "runId": run_id},
                status_code=202,
                headers={"X-Flue-Run-Id": run_id},
            )

        accept = request.headers.get("accept", "")
        if "text/event-stream" in accept.lower():
            async def events() -> Any:
                task = asyncio.create_task(invoke())
                try:
                    async for event in store.subscribe(run_id, after=0):
                        payload = event.to_dict()
                        yield (
                            f"id: {event.event_index}\n"
                            f"event: {event.type}\n"
                            f"data: {json.dumps(payload)}\n\n"
                        )
                        if event.type == "run_end":
                            break
                    await task
                except Exception as exc:
                    error = PyFlueError(
                        type="internal_error",
                        message="An internal error occurred.",
                        details="The server encountered an unexpected error while streaming this request.",
                        dev=str(exc),
                        status=500,
                    )
                    yield f"event: error\ndata: {json.dumps(error_envelope(error, dev=True))}\n\n"

            return StreamingResponse(
                events(),
                media_type="text/event-stream",
                headers={"X-Flue-Run-Id": run_id},
            )

        result = await invoke_route(
            route,
            agent_id=agent_id,
            payload=payload,
            config_path=config_path,
            run_store=store,
            run_id=run_id,
        )
        return JSONResponse(
            jsonable_encoder(result),
            headers={"X-Flue-Run-Id": run_id},
        )

    @app.api_route(
        "/agents/{name}/{agent_id}",
        methods=["GET", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def reject_agent_method(name: str, agent_id: str, request: Request) -> Any:
        raise MethodNotAllowedError(request.method, ["POST"])

    @app.post(
        "/agents/{name}/{agent_id}/dispatch",
        responses={
            202: {"model": WebhookAcceptedResponse, "description": "Input accepted for async processing."},
            **error_responses,
        },
    )
    async def dispatch_agent(name: str, agent_id: str, request: Request) -> Any:
        from pyflue.dispatch import dispatch as dispatch_input

        routes = discover_agent_routes(config.root, config.agents_dir)
        if name not in routes:
            raise PyFlueError(
                type="not_found",
                message=f"Unknown agent: {name}.",
                details=f"Available agents: {', '.join(sorted(routes)) or '(none)'}.",
                status=404,
            )
        default_obj = load_agent_default(routes[name].path)
        if not is_created_agent(default_obj):
            raise PyFlueError(
                type="invalid_request",
                message="dispatch() requires a persistent agent.",
                details="This agent module is not a create_agent() default export.",
                status=400,
            )
        body = await parse_json_payload(request)
        receipt = await dispatch_input(
            default_obj,
            id=agent_id,
            session=body.get("session"),
            input=body.get("input"),
            config_path=config_path,
        )
        return JSONResponse(
            {
                "status": "accepted",
                "dispatch_id": receipt.dispatch_id,
                "dispatchId": receipt.dispatch_id,
                "accepted_at": receipt.accepted_at,
                "acceptedAt": receipt.accepted_at,
            },
            status_code=202,
        )

    @app.post(
        "/workflows/{name}",
        responses={
            200: {
                "description": "Workflow result (?wait=result) or SSE stream (Accept: text/event-stream).",
                "content": {
                    "application/json": {"schema": {"type": "object", "additionalProperties": True}},
                    "text/event-stream": {"schema": {"type": "string"}},
                },
            },
            202: {"model": WebhookAcceptedResponse, "description": "Workflow accepted; inspect via run id."},
            **error_responses,
        },
    )
    async def run_workflow(name: str, request: Request) -> Any:
        from pyflue.workflows import (
            discover_workflows,
            generate_workflow_run_id,
            invoke_workflow,
        )

        workflows = discover_workflows(config.root, getattr(config, "workflows_dir", None))
        if name not in workflows:
            available = ", ".join(sorted(workflows)) or "(none)"
            raise PyFlueError(
                type="not_found",
                message=f"Unknown workflow: {name}.",
                details=f"Available workflows: {available}.",
                status=404,
            )
        payload = await parse_json_payload(request)
        workflow = workflows[name]
        run_id = generate_workflow_run_id(name)
        store = get_default_run_store()

        async def invoke() -> Any:
            return await invoke_workflow(
                workflow,
                payload=payload,
                config_path=config_path,
                run_store=store,
                run_id=run_id,
                request=request,
            )

        accept = request.headers.get("accept", "")
        if "text/event-stream" in accept.lower():
            async def events() -> Any:
                task = asyncio.create_task(invoke())
                try:
                    async for event in store.subscribe(run_id, after=0):
                        yield (
                            f"id: {event.event_index}\n"
                            f"event: {event.type}\n"
                            f"data: {json.dumps(event.to_dict())}\n\n"
                        )
                        if event.type == "run_end":
                            break
                    await task
                except Exception as exc:
                    error = PyFlueError(
                        type="internal_error",
                        message="An internal error occurred.",
                        details="The server encountered an unexpected error while streaming this workflow.",
                        dev=str(exc),
                        status=500,
                    )
                    yield f"event: error\ndata: {json.dumps(error_envelope(error, dev=True))}\n\n"

            return StreamingResponse(
                events(),
                media_type="text/event-stream",
                headers={"X-Flue-Run-Id": run_id},
            )

        if request.query_params.get("wait") == "result":
            result = await invoke()
            return JSONResponse(
                jsonable_encoder(
                    {"status": "completed", "run_id": run_id, "runId": run_id, "result": result}
                ),
                headers={"X-Flue-Run-Id": run_id},
            )

        task = asyncio.create_task(invoke())
        task.add_done_callback(_consume_background_exception)
        return JSONResponse(
            {"status": "accepted", "run_id": run_id, "runId": run_id},
            status_code=202,
            headers={"X-Flue-Run-Id": run_id},
        )

    @app.websocket("/agents/{name}/{agent_id}")
    async def agent_websocket(websocket: WebSocket, name: str, agent_id: str) -> None:
        """Persistent agent WebSocket: multiple prompts over one connection."""
        await websocket.accept()
        routes = discover_agent_routes(config.root, config.agents_dir)
        created = load_agent_default(routes[name].path) if name in routes else None
        if not is_created_agent(created):
            await websocket.send_json(
                {"type": "error", "error": {"type": "not_found", "message": f"Unknown persistent agent: {name}."}}
            )
            await websocket.close()
            return
        pf = await app.state.instance_manager.get_or_create(
            name=name, created_agent=created, instance_id=agent_id, config_path=config_path
        )
        try:
            while True:
                message_in = await websocket.receive_json()
                message = str(message_in.get("message") or message_in.get("prompt") or "")
                session_name = str(message_in.get("session") or "default")
                session = await pf.session(f"{agent_id}:{session_name}")
                result = await session.prompt(message)
                await websocket.send_json({"type": "result", "result": _prompt_result_payload(result)})
        except WebSocketDisconnect:
            return

    @app.websocket("/workflows/{name}")
    async def workflow_websocket(websocket: WebSocket, name: str) -> None:
        """Workflow WebSocket: one invocation streams its run events, then closes."""
        from pyflue.workflows import (
            discover_workflows,
            generate_workflow_run_id,
            invoke_workflow,
        )

        await websocket.accept()
        workflows = discover_workflows(config.root, getattr(config, "workflows_dir", None))
        if name not in workflows:
            await websocket.send_json(
                {"type": "error", "error": {"type": "not_found", "message": f"Unknown workflow: {name}."}}
            )
            await websocket.close()
            return
        try:
            invoke_message = await websocket.receive_json()
        except WebSocketDisconnect:
            return
        payload = invoke_message.get("payload", invoke_message) if isinstance(invoke_message, dict) else {}
        run_id = generate_workflow_run_id(name)
        store = get_default_run_store()
        task = asyncio.create_task(
            invoke_workflow(
                workflows[name], payload=payload, config_path=config_path, run_store=store, run_id=run_id
            )
        )
        task.add_done_callback(_consume_background_exception)
        try:
            async for event in store.subscribe(run_id, after=0):
                await websocket.send_json({"type": event.type, **event.to_dict()})
                if event.type == "run_end":
                    break
            run = store.get_run(run_id)
            if run is not None and not run.is_error:
                await websocket.send_json(
                    {"type": "result", "run_id": run_id, "runId": run_id, "result": jsonable_encoder(run.result)}
                )
            elif run is not None:
                await websocket.send_json(
                    {"type": "error", "error": run.error or {"type": "internal_error", "message": "Workflow failed."}}
                )
        except WebSocketDisconnect:
            return
        finally:
            with suppress(Exception):
                await websocket.close()

    @app.post("/prompt/{agent_id}", response_model=PromptResponse, responses=error_responses)
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

    @app.post(
        "/prompt/{agent_id}/events",
        responses={
            200: {
                "description": "Prompt event stream.",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            },
            **error_responses,
        },
    )
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

    @app.get("/runs/{run_id}", response_model=RunRecordResponse, responses=error_responses)
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

    @app.get(
        "/runs/{run_id}/events",
        response_model=RunEventListResponse,
        responses=error_responses,
    )
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
            "runId": run_id,
            "events": [event.to_dict() for event in events],
            "next_after": events[-1].event_index if events else after,
            "nextAfter": events[-1].event_index if events else after,
        }

    @app.get(
        "/runs/{run_id}/stream",
        responses={
            200: {
                "description": "Run event stream.",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            },
            **error_responses,
        },
    )
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

    @app.post(
        "/sessions/{session_id}/abort",
        response_model=AbortSessionResponse,
        responses=error_responses,
    )
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


def _consume_background_exception(task: asyncio.Task[Any]) -> None:
    with suppress(Exception):
        task.result()


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
