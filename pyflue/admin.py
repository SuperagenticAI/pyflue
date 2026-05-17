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

from typing import Any

from pyflue.errors import PyFlueError, error_envelope
from pyflue.runs import InMemoryRunStore, get_default_run_store


def create_admin_app(store: InMemoryRunStore | None = None) -> Any:
    """Return a FastAPI app exposing read-only admin routes."""
    try:
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
        from starlette.requests import Request
    except Exception as exc:
        raise ImportError(
            "PyFlue admin support requires FastAPI. Install with: pip install 'pyflue[server]'"
        ) from exc

    app = FastAPI(title="PyFlue Admin")

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

    @app.get("/agents")
    async def list_agents() -> dict[str, Any]:
        s = _store()
        return {"agents": [{"name": name} for name in s.list_agents()]}

    @app.get("/agents/{name}/instances")
    async def list_instances(name: str) -> dict[str, Any]:
        s = _store()
        if name not in set(s.list_agents()):
            raise _not_found("agent", name)
        return {
            "agent": name,
            "instances": [{"agent_id": agent_id} for agent_id in s.list_instances(name)],
        }

    @app.get("/agents/{name}/instances/{agent_id}/runs")
    async def list_instance_runs(name: str, agent_id: str, limit: int = 100) -> dict[str, Any]:
        s = _store()
        if name not in set(s.list_agents()):
            raise _not_found("agent", name)
        if agent_id not in set(s.list_instances(name)):
            raise _not_found("instance", agent_id)
        runs = s.list_runs_for_instance(name, agent_id, limit=limit)
        return {
            "agent": name,
            "agent_id": agent_id,
            "runs": [run.to_dict() for run in runs],
        }

    @app.get("/runs")
    async def list_runs(limit: int = 100) -> dict[str, Any]:
        s = _store()
        if limit < 1 or limit > 1000:
            raise PyFlueError(
                type="invalid_query_param",
                message='Invalid value for query parameter "limit".',
                details="Must be an integer in [1, 1000].",
                status=400,
                meta={"param": "limit", "value": str(limit)},
            )
        return {"runs": [run.to_dict() for run in s.list_runs(limit=limit)]}

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        s = _store()
        run = s.get_run(run_id)
        if run is None:
            raise _not_found("run", run_id)
        return run.to_dict()

    return app
