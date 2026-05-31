"""Tests for the OpenTelemetry observer (parity item 6)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pyflue import create_opentelemetry_observer
from pyflue.harnesses.base import HarnessBackend
from pyflue.types import HarnessResult, PromptModel, PromptUsage, PyFlueEvent


def _tracer_and_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


class _FakeBackend(HarnessBackend):
    name = "fake"

    async def run(self, **kwargs):
        return HarnessResult(
            text="ok",
            raw=SimpleNamespace(),
            metadata={},
            usage=PromptUsage(total_tokens=1),
            model=PromptModel(id=kwargs.get("config").model),
        )


def test_observer_builds_workflow_operation_tool_tree():
    tracer, exporter = _tracer_and_exporter()
    observe = create_opentelemetry_observer(tracer=tracer, capture_content=True)

    observe(PyFlueEvent("run_start", {"run_id": "r1", "agent": "summarize"}))
    observe(PyFlueEvent("operation_start", {"run_id": "r1", "operation_id": "op1", "operation_kind": "prompt"}))
    observe(PyFlueEvent("command_start", {"run_id": "r1", "operation_id": "op1", "command": "ls", "args": []}))
    observe(PyFlueEvent("command_end", {"run_id": "r1", "operation_id": "op1", "exitCode": 0}))
    observe(PyFlueEvent("operation", {"operation_id": "op1", "operation_kind": "prompt", "duration_ms": 5, "is_error": False}))
    observe(PyFlueEvent("run_end", {"run_id": "r1", "is_error": False, "status": "succeeded"}))

    by_name = {span.name: span for span in exporter.get_finished_spans()}
    assert {"flue.workflow summarize", "flue.operation prompt", "flue.tool bash"} <= set(by_name)

    workflow = by_name["flue.workflow summarize"]
    operation = by_name["flue.operation prompt"]
    tool = by_name["flue.tool bash"]

    assert operation.parent is not None and operation.parent.span_id == workflow.context.span_id
    assert tool.parent is not None and tool.parent.span_id == operation.context.span_id
    assert operation.attributes["flue.operation.kind"] == "prompt"
    assert operation.attributes["flue.duration_ms"] == 5
    assert tool.attributes["flue.tool.command"] == "ls"
    assert workflow.attributes["flue.workflow.name"] == "summarize"


def test_observer_creates_generation_span_with_usage():
    tracer, exporter = _tracer_and_exporter()
    observe = create_opentelemetry_observer(tracer=tracer)

    observe(PyFlueEvent("operation_start", {"operation_id": "op1", "operation_kind": "prompt"}))
    observe(PyFlueEvent("turn_request", {"operation_id": "op1", "turn_id": "t1", "purpose": "agent", "model": "m"}))
    observe(PyFlueEvent("turn", {
        "operation_id": "op1",
        "turn_id": "t1",
        "model": "m",
        "usage": {"input": 10, "output": 5, "total_tokens": 15, "cost": {"total": 0.01}},
    }))
    observe(PyFlueEvent("operation", {"operation_id": "op1", "operation_kind": "prompt"}))

    by_name = {span.name: span for span in exporter.get_finished_spans()}
    assert "gen_ai.generate" in by_name
    generation = by_name["gen_ai.generate"]
    assert generation.attributes["gen_ai.request.model"] == "m"
    assert generation.attributes["gen_ai.usage.total_tokens"] == 15
    assert generation.attributes["gen_ai.usage.cost_total"] == 0.01
    # The generation span nests under its operation.
    assert generation.parent.span_id == by_name["flue.operation prompt"].context.span_id


def test_observer_marks_errored_operation():
    tracer, exporter = _tracer_and_exporter()
    observe = create_opentelemetry_observer(tracer=tracer)

    observe(PyFlueEvent("operation_start", {"operation_id": "op1", "operation_kind": "prompt"}))
    observe(PyFlueEvent("error", {"operation_id": "op1", "error": "boom"}))
    observe(PyFlueEvent("operation", {"operation_id": "op1", "operation_kind": "prompt", "is_error": True}))

    span = exporter.get_finished_spans()[0]
    from opentelemetry.trace import StatusCode

    assert span.status.status_code == StatusCode.ERROR


def test_observer_records_task_and_compaction_spans():
    tracer, exporter = _tracer_and_exporter()
    observe = create_opentelemetry_observer(tracer=tracer)

    observe(PyFlueEvent("operation_start", {"operation_id": "op1", "operation_kind": "prompt"}))
    observe(PyFlueEvent("task_start", {"operation_id": "op1", "taskId": "t1", "prompt": "go"}))
    observe(PyFlueEvent("task_end", {"operation_id": "op1", "taskId": "t1", "isError": False}))
    observe(PyFlueEvent("compaction_start", {"operation_id": "op1", "reason": "threshold"}))
    observe(PyFlueEvent("compaction_end", {"operation_id": "op1"}))
    observe(PyFlueEvent("operation", {"operation_id": "op1", "operation_kind": "prompt"}))

    names = {span.name for span in exporter.get_finished_spans()}
    assert "flue.task" in names
    assert "flue.compaction" in names


@pytest.mark.asyncio
async def test_observer_traces_live_agent_prompt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tracer, exporter = _tracer_and_exporter()
    monkeypatch.setattr("pyflue.core.create_backend", lambda harness: _FakeBackend())

    from pyflue import init

    agent = await init(on_event=create_opentelemetry_observer(tracer=tracer))
    await (await agent.session("s")).prompt("hi")

    names = [span.name for span in exporter.get_finished_spans()]
    assert "flue.operation prompt" in names
    assert "gen_ai.generate" in names  # per-turn generation span
