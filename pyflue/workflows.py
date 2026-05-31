"""File-based workflow discovery and invocation (parity item 2).

A workflow is a module in ``workflows/`` or ``.pyflue/workflows/`` that exports a
callable ``run(ctx)``. The filename gives the workflow its name. Each invocation
is a *workflow run* with a distinct ``run_id`` and an inspectable lifecycle
(``run_start`` … ``run_end``), mirroring the TypeScript Flue reference.

This is the finite, result-oriented counterpart to a persistent agent: a
workflow may initialize a created agent with ``ctx.init(agent)`` and drive its
sessions, but its boundary is one bounded execution that returns a result.
"""

from __future__ import annotations

import importlib.util
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyflue.config import load_config
from pyflue.errors import PyFlueError, error_envelope
from pyflue.routing import FlueContext, _safe_env
from pyflue.runs import FlueRun, generate_run_id, get_default_run_store


@dataclass(frozen=True)
class WorkflowDef:
    """A discovered workflow module exposing ``run(ctx)``."""

    name: str
    path: Path


def discover_workflows(
    root: str | Path = ".",
    workflows_dir: str | Path | None = None,
) -> dict[str, WorkflowDef]:
    """Discover workflow modules under ``workflows/`` and ``.pyflue/workflows/``."""
    base = Path(root).expanduser().resolve()
    candidates: list[Path] = []
    if workflows_dir is not None:
        directory = Path(workflows_dir).expanduser()
        candidates.append(directory if directory.is_absolute() else base / directory)
    else:
        # Reference parity (v0.8.x): `src/` is the canonical layout for new
        # projects, alongside the legacy root and `.pyflue/` locations.
        candidates.extend(
            [base / "workflows", base / ".pyflue" / "workflows", base / "src" / "workflows"]
        )

    found: dict[str, WorkflowDef] = {}
    for directory in candidates:
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.py")):
            if path.name.startswith("_"):
                continue
            name = path.relative_to(directory).with_suffix("").as_posix().replace("/", ".")
            found[name] = WorkflowDef(name=name, path=path)
    return found


def generate_workflow_run_id(name: str) -> str:
    """Return a run id shaped ``workflow:<name>:<ulid>`` (reference shape)."""
    return f"workflow:{name}:{generate_run_id()}"


async def invoke_workflow(
    workflow: WorkflowDef,
    *,
    payload: dict[str, Any] | None = None,
    config_path: str | Path = "pyflue.toml",
    run_store: Any | None = None,
    run_id: str | None = None,
    request: Any = None,
) -> Any:
    """Invoke one workflow ``run(ctx)`` inside a run lifecycle.

    Emits ``run_start`` before ``run(...)`` executes and ``run_end`` afterwards
    with the success/failure status. Returns the value returned by ``run(...)``.
    """
    config = load_config(config_path)
    run_fn = _load_run(workflow.path)
    store = run_store or get_default_run_store()
    rid = run_id or generate_workflow_run_id(workflow.name)
    run: FlueRun = await store.start_run(agent=workflow.name, agent_id=rid, run_id=rid)
    ctx = FlueContext(
        payload=payload or {},
        env=_safe_env(),
        agent_id=rid,
        config=config,
        run_id=run.run_id,
        request=request,
        workflow_name=workflow.name,
        _run_store=store,
    )
    try:
        result = run_fn(ctx)
        if inspect.isawaitable(result):
            result = await result
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
    return result


def _load_run(path: Path) -> Callable[[FlueContext], Any]:
    spec = importlib.util.spec_from_file_location(f"pyflue_workflow_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load workflow file: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    handler = getattr(module, "run", None)
    if handler is None:
        raise AttributeError(f"{path} must define a `run(ctx)` function.")
    if not callable(handler):
        raise TypeError(f"Workflow `run` is not callable: {path}")
    return handler
