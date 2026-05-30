"""OpenTelemetry tracing adapter (parity item 6).

``create_opentelemetry_observer()`` returns a callback that maps PyFlue's event
stream onto OpenTelemetry spans — the Python counterpart of the reference's
``@flue/opentelemetry`` ``createOpenTelemetryObserver``. Like the reference, it
is a pure event→span mapper; you wire it into the event stream yourself:

    from pyflue import init
    from pyflue.observability import create_opentelemetry_observer

    agent = await init(on_event=create_opentelemetry_observer())

For workflow run events (``run_start`` / ``run_end`` / ``log``), register it on
the run store with ``observe(create_opentelemetry_observer())``.

It builds this span tree, parenting via the ``run_id`` / ``operation_id`` /
``task_id`` correlation fields PyFlue attaches to events:

    flue.workflow <name>                 (run_start / run_end)
      └─ flue.operation <kind>           (operation_start / operation)
          ├─ flue.tool <name>            (command_* / tool_*)
          ├─ flue.task                   (task_start / task_end)
          └─ flue.compaction             (compaction_start / compaction_end)

Per-LLM-turn generation spans require deeper harness instrumentation and are a
documented follow-up; operation/tool/task/compaction spans are produced today.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def create_opentelemetry_observer(
    *,
    tracer: Any | None = None,
    capture_content: bool = False,
) -> Callable[[Any], None]:
    """Return an event observer that exports PyFlue activity as OTel spans.

    ``tracer`` defaults to ``trace.get_tracer("pyflue")``. Set
    ``capture_content=True`` to attach commands, results, and log messages to
    spans (may include sensitive data).
    """
    try:
        from opentelemetry import trace
        from opentelemetry.trace import SpanKind, Status, StatusCode
    except ImportError as exc:  # pragma: no cover - import guard
        raise ImportError(
            "PyFlue OpenTelemetry support requires opentelemetry-api. "
            "Install with: pip install 'pyflue[otel]'"
        ) from exc

    active_tracer = tracer or trace.get_tracer("pyflue")
    workflows: dict[str, Any] = {}
    operations: dict[str, Any] = {}
    tools: dict[Any, Any] = {}
    tasks: dict[str, Any] = {}
    compactions: dict[str, Any] = {}

    def _data(event: Any) -> dict[str, Any]:
        return getattr(event, "data", {}) or {}

    def _run_id(event: Any) -> str | None:
        return getattr(event, "run_id", None) or _data(event).get("run_id")

    def _identifiers(event: Any) -> dict[str, Any]:
        data = _data(event)
        candidates = {
            "flue.run_id": _run_id(event),
            "flue.instance_id": data.get("instance_id"),
            "flue.operation_id": data.get("operation_id"),
            "flue.session": data.get("session_id"),
            "flue.task_id": data.get("taskId"),
        }
        return {key: value for key, value in candidates.items() if value is not None}

    def _start(name: str, parent: Any | None, attributes: dict[str, Any]) -> Any:
        context = trace.set_span_in_context(parent) if parent is not None else None
        return active_tracer.start_span(
            name, context=context, kind=SpanKind.INTERNAL, attributes=attributes
        )

    def _operation_parent(event: Any) -> Any | None:
        return workflows.get(_run_id(event))

    def _finish(span: Any, *, is_error: bool = False, error: Any = None, duration_ms: Any = None) -> None:
        if duration_ms is not None:
            span.set_attribute("flue.duration_ms", duration_ms)
        if is_error:
            message = error if isinstance(error, str) else None
            span.set_status(Status(StatusCode.ERROR, message))
        span.end()

    def observe(event: Any) -> None:
        event_type = getattr(event, "type", None)
        data = _data(event)

        if event_type == "run_start":
            run_id = _run_id(event)
            if run_id is None:
                return
            name = data.get("agent") or "workflow"
            workflows[run_id] = _start(
                f"flue.workflow {name}", None, {**_identifiers(event), "flue.workflow.name": name}
            )
        elif event_type == "run_end":
            span = workflows.pop(_run_id(event), None)
            if span is not None:
                error = data.get("error")
                message = error.get("message") if isinstance(error, dict) else error
                _finish(span, is_error=bool(data.get("is_error")), error=message)
        elif event_type == "operation_start":
            operation_id = data.get("operation_id")
            if operation_id is None:
                return
            kind = data.get("operation_kind", "")
            operations[operation_id] = _start(
                f"flue.operation {kind}".strip(),
                _operation_parent(event),
                {**_identifiers(event), "flue.operation.kind": kind},
            )
        elif event_type == "operation":
            span = operations.pop(data.get("operation_id"), None)
            if span is not None:
                _finish(span, is_error=bool(data.get("is_error")), duration_ms=data.get("duration_ms"))
        elif event_type == "command_start":
            parent = operations.get(data.get("operation_id"))
            attributes = {**_identifiers(event), "flue.tool.name": "bash"}
            if capture_content and data.get("command"):
                attributes["flue.tool.command"] = data["command"]
            tools[("command", data.get("operation_id"))] = _start("flue.tool bash", parent, attributes)
        elif event_type == "command_end":
            span = tools.pop(("command", data.get("operation_id")), None)
            if span is not None:
                exit_code = data.get("exitCode")
                if exit_code is not None:
                    span.set_attribute("flue.tool.exit_code", exit_code)
                _finish(span, is_error=bool(exit_code))
        elif event_type == "tool_start":
            parent = operations.get(data.get("operation_id"))
            name = data.get("toolName", "")
            tools[("tool", data.get("toolCallId"))] = _start(
                f"flue.tool {name}".strip(), parent, {**_identifiers(event), "flue.tool.name": name}
            )
        elif event_type == "tool_end":
            span = tools.pop(("tool", data.get("toolCallId")), None)
            if span is not None:
                _finish(span, is_error=bool(data.get("isError")))
        elif event_type == "task_start":
            parent = operations.get(data.get("operation_id"))
            tasks[data.get("taskId")] = _start("flue.task", parent, _identifiers(event))
        elif event_type == "task_end":
            span = tasks.pop(data.get("taskId"), None)
            if span is not None:
                _finish(span, is_error=bool(data.get("isError")))
        elif event_type == "compaction_start":
            parent = operations.get(data.get("operation_id"))
            attributes = {**_identifiers(event)}
            if data.get("reason"):
                attributes["flue.compaction.reason"] = data["reason"]
            compactions[data.get("operation_id")] = _start("flue.compaction", parent, attributes)
        elif event_type == "compaction_end":
            span = compactions.pop(data.get("operation_id"), None)
            if span is not None:
                _finish(span)
        elif event_type == "log":
            span = operations.get(data.get("operation_id")) or workflows.get(_run_id(event))
            if span is not None:
                attributes: dict[str, Any] = {"flue.log.level": data.get("level")}
                if capture_content and data.get("message"):
                    attributes["flue.log.message"] = data["message"]
                span.add_event("flue.log", attributes)
        elif event_type in ("error", "aborted"):
            span = operations.get(data.get("operation_id"))
            if span is not None:
                span.set_status(
                    Status(StatusCode.ERROR, data.get("error") if event_type == "error" else "aborted")
                )

    return observe


__all__ = ["create_opentelemetry_observer"]
