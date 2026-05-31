"""Tests for the Pydantic AI harness backend.

These run with Pydantic AI's FunctionModel, so no API key or network is needed.
The module is skipped when pydantic-ai is not installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pydantic_ai")

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart  # noqa: E402
from pydantic_ai.models.function import FunctionModel  # noqa: E402

from pyflue.harnesses.pydanticai import (  # noqa: E402
    PydanticAIBackend,
    _build_tools,
    _resolve_model,
)
from pyflue.harnesses.registry import create_backend  # noqa: E402
from pyflue.types import PromptUsage, PyFlueConfig, ToolDef  # noqa: E402


def _text_model(text: str) -> FunctionModel:
    def reply(messages, info):
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(reply)


def test_registered_in_harness_registry():
    assert isinstance(create_backend("pydanticai"), PydanticAIBackend)


@pytest.mark.asyncio
async def test_pydanticai_is_the_default_harness(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from pyflue import init

    agent = await init()
    assert agent.config.harness == "pydanticai"
    assert isinstance(agent.backend, PydanticAIBackend)


def test_resolve_model_normalizes_provider_slash():
    config = PyFlueConfig()
    config.model = "anthropic/claude-sonnet-4-6"
    assert _resolve_model(config) == "anthropic:claude-sonnet-4-6"
    config.model = "openai:gpt-5.5"
    assert _resolve_model(config) == "openai:gpt-5.5"


def test_build_tools_adapts_pyflue_tools():
    from pyflue.tools import to_callable_tool

    raw = ToolDef(
        name="raw_tool",
        description="raw",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        execute=lambda args: args["q"],
    )
    wrapped = to_callable_tool(
        ToolDef(
            name="wrapped_tool",
            description="wrapped",
            parameters={"type": "object", "properties": {"n": {"type": "integer"}}},
            execute=lambda args: args["n"],
        )
    )

    def plain(city: str) -> str:
        """A plain typed tool."""
        return city

    tools = _build_tools([raw, wrapped, plain], None)
    names = {getattr(tool, "name", None) or getattr(tool, "__name__", None) for tool in tools}
    assert {"raw_tool", "wrapped_tool", "plain"} <= names


@pytest.mark.asyncio
async def test_backend_runs_with_function_model(tmp_path):
    config = PyFlueConfig(root=tmp_path)
    config.model = _text_model("hi from pydantic-ai")
    result = await PydanticAIBackend().run(
        prompt="hello",
        system_prompt="Be helpful.",
        config=config,
        skills={},
        sandbox=None,
        session_id="s",
    )
    assert result.text == "hi from pydantic-ai"
    assert result.metadata["harness"] == "pydanticai"
    assert isinstance(result.usage, PromptUsage)


@pytest.mark.asyncio
async def test_backend_executes_custom_tool(tmp_path):
    executed: dict = {}

    def execute(args):
        executed["q"] = args["q"]
        return f"result for {args['q']}"

    custom = ToolDef(
        name="lookup",
        description="Look something up.",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        execute=execute,
    )

    turns = {"n": 0}

    def reply(messages, info):
        turns["n"] += 1
        if turns["n"] == 1:
            return ModelResponse(parts=[ToolCallPart(tool_name="lookup", args={"q": "weather"})])
        return ModelResponse(parts=[TextPart("done")])

    config = PyFlueConfig(root=tmp_path)
    config.model = FunctionModel(reply)
    result = await PydanticAIBackend().run(
        prompt="look up the weather",
        system_prompt="Use tools when needed.",
        config=config,
        skills={},
        sandbox=None,
        session_id="s",
        tools=[custom],
    )
    assert executed["q"] == "weather"  # the PyFlue tool actually ran
    assert result.text == "done"


@pytest.mark.asyncio
async def test_drives_pyflue_through_session(tmp_path, monkeypatch):
    # End to end: init(harness="pydanticai") then a normal session prompt.
    monkeypatch.chdir(tmp_path)
    from pyflue import init

    agent = await init(harness="pydanticai")
    agent.config.model = _text_model("driven by pydantic-ai")
    session = await agent.session("s")
    result = await session.prompt("hello")
    assert result.text == "driven by pydantic-ai"
