"""File-based agent routing."""

from __future__ import annotations

import importlib.util
import inspect
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyflue.config import load_config
from pyflue.core import init
from pyflue.errors import PyFlueError, error_envelope
from pyflue.runs import FlueRun, get_default_run_store
from pyflue.types import PyFlueConfig


@dataclass(frozen=True)
class AgentRoute:
    """Discovered file-based agent route."""

    name: str
    path: Path
    url_path: str
    triggers: dict[str, Any] = field(default_factory=dict)


@dataclass
class PyFlueContext:
    """Context object passed to file-based agent handlers."""

    payload: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    agent_id: str = "default"
    route: AgentRoute | None = None
    config: PyFlueConfig | None = None
    run_id: str | None = None
    _run_store: Any = None

    class _Log:
        """Structured handler logs that land in the run event stream."""

        def __init__(self, ctx: PyFlueContext) -> None:
            self._ctx = ctx

        async def _emit(self, level: str, message: str, **fields: Any) -> None:
            store = self._ctx._run_store
            rid = self._ctx.run_id
            if store is None or rid is None:
                return
            await store.append_event(rid, "log", {"level": level, "message": message, **fields})

        async def info(self, message: str, **fields: Any) -> None:
            await self._emit("info", message, **fields)

        async def warn(self, message: str, **fields: Any) -> None:
            await self._emit("warn", message, **fields)

        async def error(self, message: str, **fields: Any) -> None:
            await self._emit("error", message, **fields)

    @property
    def log(self) -> _Log:
        return PyFlueContext._Log(self)

    async def init(self, **kwargs: Any) -> Any:
        """Initialize a PyFlue agent with route config defaults."""
        if self.config is not None:
            kwargs.setdefault("config_path", self.config.root / "pyflue.toml")
        return await init(env=self.env, **kwargs)


def discover_agent_routes(
    root: str | Path = ".",
    agents_dir: str | Path | None = None,
) -> dict[str, AgentRoute]:
    """Discover Python files that should be exposed as agent webhook routes."""
    base = Path(root).expanduser().resolve()
    candidates = []
    if agents_dir is not None:
        directory = Path(agents_dir).expanduser()
        candidates.append(directory if directory.is_absolute() else base / directory)
    else:
        candidates.extend([base / "agents", base / ".agents"])

    routes: dict[str, AgentRoute] = {}
    for directory in candidates:
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.py")):
            if path.name.startswith("_"):
                continue
            name = path.relative_to(directory).with_suffix("").as_posix().replace("/", ".")
            routes[name] = AgentRoute(
                name=name,
                path=path,
                url_path=f"/agents/{name}/{{agent_id}}",
                triggers=_load_triggers(path),
            )
    return routes


async def invoke_route(
    route: AgentRoute,
    *,
    agent_id: str,
    payload: dict[str, Any] | None = None,
    config_path: str | Path = "pyflue.toml",
    run_store: Any | None = None,
    run_id: str | None = None,
) -> Any:
    """Invoke one file-based agent handler.

    Wraps the call in a run lifecycle: a ``run_start`` event is emitted before
    the handler runs, and a ``run_end`` event is emitted afterwards with the
    handler's success/failure status. Returns a dict with ``_meta.run_id``
    merged in when the handler returns a dict; otherwise returns the raw
    handler result and surfaces the run id via the store.
    """
    config = load_config(config_path)
    handler = _load_handler(route.path)
    store = run_store or get_default_run_store()
    run: FlueRun = await store.start_run(agent=route.name, agent_id=agent_id, run_id=run_id)
    context = PyFlueContext(
        payload=payload or {},
        env=_safe_env(),
        agent_id=agent_id,
        route=route,
        config=config,
        run_id=run.run_id,
        _run_store=store,
    )
    try:
        if inspect.iscoroutinefunction(handler):
            result = await handler(context)
        else:
            result = handler(context)
    except PyFlueError as exc:
        await store.end_run(run.run_id, is_error=True, error=error_envelope(exc, dev=True)["error"])
        raise
    except Exception as exc:
        await store.end_run(
            run.run_id,
            is_error=True,
            error={
                "type": "internal_error",
                "message": "An internal error occurred.",
                "details": str(exc),
            },
        )
        raise
    await store.end_run(run.run_id, is_error=False, result=result)
    if isinstance(result, dict):
        meta = dict(result.get("_meta") or {})
        meta.setdefault("run_id", run.run_id)
        out = dict(result)
        out["_meta"] = meta
        return out
    return result


def _load_handler(path: Path) -> Callable[[PyFlueContext], Any]:
    spec = importlib.util.spec_from_file_location(f"pyflue_agent_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load agent file: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    handler = getattr(module, "default", None) or getattr(module, "agent", None)
    if handler is None:
        raise AttributeError(f"{path} must define `default(context)` or `agent(context)`.")
    if not callable(handler):
        raise TypeError(f"Agent handler is not callable: {path}")
    return handler


def _load_triggers(path: Path) -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location(f"pyflue_route_meta_{path.stem}", path)
    if spec is None or spec.loader is None:
        return {"webhook": True}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    triggers = getattr(module, "triggers", {"webhook": True})
    return dict(triggers) if isinstance(triggers, dict) else {"webhook": True}


def _safe_env() -> dict[str, str]:
    """Return process env for host code while keeping it out of prompts."""
    return dict(os.environ)
