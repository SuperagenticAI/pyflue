"""Read-only admin sub-app for PyFlue deployments.

Ships no auth opinions. Mount this behind your own middleware:

    from fastapi import FastAPI, Depends
    from pyflue.admin import create_admin_app

    app = FastAPI()
    admin = create_admin_app()
    app.mount("/admin", admin, dependencies=[Depends(my_auth)])

Routes (relative to the mount point):

    GET /agents
    GET /agents/{name}/instances
    GET /agents/{name}/instances/{agent_id}/runs
    GET /runs
    GET /runs/{run_id}

For the per-run event/stream endpoints, mount the main server which exposes
``/runs/{run_id}/events`` and ``/runs/{run_id}/stream`` already.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from starlette.requests import Request

from pyflue.api_schemas import (
    AdminAgentsResponse,
    AdminInstancesResponse,
    AdminRunsResponse,
    ErrorEnvelope,
    RunRecordResponse,
)
from pyflue.errors import PyFlueError, error_envelope
from pyflue.runs import InMemoryRunStore, get_default_run_store


def create_admin_app(store: InMemoryRunStore | None = None) -> Any:
    """Return a FastAPI app exposing read-only admin routes."""
    try:
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
    except Exception as exc:
        raise ImportError(
            "PyFlue admin support requires FastAPI. Install with: pip install 'pyflue[server]'"
        ) from exc

    error_responses = {
        400: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
    }
    app = FastAPI(
        title="PyFlue Admin",
        description="Read-only PyFlue deployment inspection API.",
    )

    @app.exception_handler(PyFlueError)
    async def _pyflue_error(_request: Request, exc: PyFlueError) -> JSONResponse:
        return JSONResponse(error_envelope(exc, dev=True), status_code=exc.status)

    def _store() -> InMemoryRunStore:
        return store or get_default_run_store()

    def _not_found(kind: str, value: str) -> PyFlueError:
        return PyFlueError(
            type=f"{kind}_not_found",
            message=f'{kind.replace("_", " ").title()} "{value}" is not known to this server.',
            details="Admin lookups use in-process run history that may have been evicted.",
            status=404,
        )

    @app.get("/agents", response_model=AdminAgentsResponse, responses=error_responses)
    async def list_agents(request: Request) -> dict[str, Any]:
        s = _store()
        limit = _parse_limit(request.query_params.get("limit"))
        cursor = _parse_cursor(request.query_params.get("cursor"))
        page, next_cursor = _page([{"name": name} for name in s.list_agents()], cursor, limit)
        return {"agents": page, "items": page, "nextCursor": next_cursor}

    @app.get(
        "/agents/{name}/instances",
        response_model=AdminInstancesResponse,
        responses=error_responses,
    )
    async def list_instances(name: str, request: Request) -> dict[str, Any]:
        s = _store()
        if name not in set(s.list_agents()):
            raise _not_found("agent", name)
        limit = _parse_limit(request.query_params.get("limit"))
        cursor = _parse_cursor(request.query_params.get("cursor"))
        items = [
            {"agent_id": agent_id, "instanceId": agent_id}
            for agent_id in s.list_instances(name)
        ]
        page, next_cursor = _page(items, cursor, limit)
        return {
            "agent": name,
            "agentName": name,
            "instances": page,
            "items": page,
            "nextCursor": next_cursor,
        }

    @app.get(
        "/agents/{name}/instances/{agent_id}/runs",
        response_model=AdminRunsResponse,
        responses=error_responses,
    )
    async def list_instance_runs(name: str, agent_id: str, request: Request) -> dict[str, Any]:
        s = _store()
        if name not in set(s.list_agents()):
            raise _not_found("agent", name)
        if agent_id not in set(s.list_instances(name)):
            raise _not_found("instance", agent_id)
        limit = _parse_limit(request.query_params.get("limit"))
        cursor = _parse_cursor(request.query_params.get("cursor"))
        status = _parse_status(request.query_params.get("status"))
        runs = s.list_runs_for_instance(name, agent_id, limit=None, status=status)
        items = [run.to_dict() for run in runs]
        page, next_cursor = _page(items, cursor, limit)
        return {
            "agent": name,
            "agentName": name,
            "agent_id": agent_id,
            "instanceId": agent_id,
            "runs": page,
            "items": page,
            "nextCursor": next_cursor,
        }

    @app.get("/runs", response_model=AdminRunsResponse, responses=error_responses)
    async def list_runs(request: Request) -> dict[str, Any]:
        s = _store()
        limit = _parse_limit(request.query_params.get("limit"))
        cursor = _parse_cursor(request.query_params.get("cursor"))
        status = _parse_status(request.query_params.get("status"))
        agent_name = request.query_params.get("agentName") or request.query_params.get("agent_name")
        if agent_name is not None and agent_name not in set(s.list_agents()):
            raise _not_found("agent", agent_name)
        items = [
            run.to_dict()
            for run in s.list_runs(limit=None, status=status, agent=agent_name)
        ]
        page, next_cursor = _page(items, cursor, limit)
        return {"runs": page, "items": page, "nextCursor": next_cursor}

    @app.get("/runs/{run_id}", response_model=RunRecordResponse, responses=error_responses)
    async def get_run(run_id: str) -> dict[str, Any]:
        s = _store()
        run = s.get_run(run_id)
        if run is None:
            raise _not_found("run", run_id)
        return run.to_dict()

    return app


def _parse_limit(raw: str | None, *, default: int = 100) -> int:
    if raw is None or raw == "":
        return default
    try:
        limit = int(raw)
    except ValueError as exc:
        raise _invalid_query("limit", raw, "Must be an integer in [1, 1000].") from exc
    if limit < 1 or limit > 1000:
        raise _invalid_query("limit", raw, "Must be an integer in [1, 1000].")
    return limit


def _parse_cursor(raw: str | None) -> int:
    if raw is None or raw == "":
        return 0
    if raw.isdigit():
        return _validate_cursor(int(raw), raw)
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        payload = json.loads(decoded)
        cursor = int(payload["offset"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error) as exc:
        raise _invalid_query(
            "cursor",
            raw,
            "Must be a valid pagination cursor.",
        ) from exc
    return _validate_cursor(cursor, raw)


def _validate_cursor(cursor: int, raw: str) -> int:
    if cursor < 0:
        raise _invalid_query("cursor", raw, "Must be a valid pagination cursor.")
    return cursor


def _parse_status(raw: str | None) -> str | None:
    if raw is None or raw == "":
        return None
    mapping = {
        "active": "running",
        "completed": "succeeded",
        "errored": "failed",
        "running": "running",
        "succeeded": "succeeded",
        "failed": "failed",
    }
    status = mapping.get(raw)
    if status is None:
        raise _invalid_query(
            "status",
            raw,
            "Must be one of: active, completed, errored.",
        )
    return status


def _page(items: list[dict[str, Any]], cursor: int, limit: int) -> tuple[list[dict[str, Any]], str | None]:
    page = items[cursor : cursor + limit]
    next_index = cursor + len(page)
    next_cursor = _encode_cursor(next_index) if next_index < len(items) else None
    return page, next_cursor


def _encode_cursor(offset: int) -> str:
    raw = json.dumps({"offset": offset}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _invalid_query(field: str, value: str, details: str) -> PyFlueError:
    return PyFlueError(
        type="invalid_query_param",
        message=f'Invalid value for query parameter "{field}".',
        details=details,
        status=400,
        meta={"param": field, "value": value},
    )
