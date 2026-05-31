"""Pydantic AI harness backend.

Drives PyFlue with the Pydantic AI agent loop instead of DeepAgents. Unlike the
DeepAgents backend, this has no LangChain or LangGraph dependency: Pydantic AI is
a typed, model agnostic loop. Select it with ``init(harness="pydanticai")`` or
``harness = "pydanticai"`` in ``pyflue.toml``. It is registered through the same
harness registry as every other backend, so the harness layer stays extendable.

The backend gives the model PyFlue's sandbox as file and shell tools (gated by
the sandbox policy), adapts custom PyFlue tools through ``Tool.from_schema``, and
maps Pydantic AI usage onto PyFlue's ``HarnessResult``. PyFlue still owns the
sandbox, skills, sessions, and typed result parsing.
"""

from __future__ import annotations

import inspect
from typing import Any

from pyflue.harnesses.base import HarnessBackend
from pyflue.types import (
    HarnessResult,
    PromptCost,
    PromptModel,
    PromptUsage,
    PyFlueConfig,
    Skill,
)

_INSTALL_HINT = (
    "The pydanticai harness requires pydantic-ai. "
    "Install with: pip install 'pyflue[pydanticai]'"
)


class PydanticAIBackend(HarnessBackend):
    """Harness backend powered by Pydantic AI."""

    name = "pydanticai"

    async def run(
        self,
        *,
        prompt: str,
        system_prompt: str,
        config: PyFlueConfig,
        skills: dict[str, Skill],
        sandbox: Any,
        session_id: str,
        python_backend: Any | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
        images: list[Any] | tuple[Any, ...] | None = None,
        stream: bool = False,
    ) -> HarnessResult:
        try:
            from pydantic_ai import Agent
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(_INSTALL_HINT) from exc

        agent = Agent(
            _resolve_model(config),
            instructions=system_prompt or None,
            tools=_build_tools(tools, sandbox),
        )
        result = await agent.run(prompt)

        text = "" if result.output is None else str(result.output)
        model_id = (
            getattr(getattr(result, "response", None), "model_name", None)
            or _model_id(config.model)
        )
        return HarnessResult(
            text=text,
            raw=result,
            metadata={"harness": "pydanticai", "model": model_id},
            usage=_to_usage(result.usage()),
            model=PromptModel(id=model_id),
        )


def _resolve_model(config: PyFlueConfig) -> Any:
    """Return a Pydantic AI model spec from the PyFlue config.

    A non string value (for example a ``FunctionModel`` or ``TestModel``) is
    passed through unchanged. PyFlue model strings may use ``provider/model`` or
    ``provider:model``; Pydantic AI expects ``provider:model``, so a single
    leading ``/`` is normalized to ``:``.
    """
    model = config.model
    if not isinstance(model, str):
        return model
    if not model:
        return None
    if ":" not in model and "/" in model:
        provider, rest = model.split("/", 1)
        return f"{provider}:{rest}"
    return model


def _model_id(model: Any) -> str | None:
    if isinstance(model, str):
        return model or None
    return getattr(model, "model_name", None)


def _to_usage(usage: Any) -> PromptUsage:
    if usage is None:
        return PromptUsage()
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_write_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", 0) or 0) or (input_tokens + output_tokens)
    return PromptUsage(
        input=input_tokens,
        output=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
        total_tokens=total,
        cost=PromptCost(),
    )


def _build_tools(tools: Any, sandbox: Any) -> list[Any]:
    """Adapt the tools PyFlue merged for this turn into Pydantic AI tools.

    PyFlue already merges the built-in sandbox file and shell tools, custom
    tools, command tools, and MCP tools into ``tools`` (as callables) before
    calling the backend, so the backend adapts that list rather than wiring the
    sandbox itself. ``sandbox`` is accepted for the backend contract but the
    sandbox is reached through those merged tools.
    """
    built = []
    for tool in tools or ():
        adapted = _adapt_tool(tool)
        if adapted is not None:
            built.append(adapted)
    return built


def _adapt_tool(tool: Any) -> Any:
    """Adapt one PyFlue tool to a Pydantic AI tool.

    PyFlue wraps custom tools as callables that carry an explicit JSON schema in
    ``__pyflue_schema__``; those are registered with ``Tool.from_schema``. A raw
    ``ToolDefinition`` is handled the same way. The built-in sandbox tools and
    plain user functions are typed callables, so Pydantic AI infers their schema.
    """
    from pydantic_ai import Tool

    schema = getattr(tool, "__pyflue_schema__", None)
    if callable(tool) and isinstance(schema, dict):
        return Tool.from_schema(
            tool,
            name=getattr(tool, "__name__", None) or "tool",
            description=getattr(tool, "__doc__", "") or "",
            json_schema=schema,
        )

    name = getattr(tool, "name", None)
    execute = getattr(tool, "execute", None)
    parameters = getattr(tool, "parameters", None)
    if name and callable(execute) and isinstance(parameters, dict):

        async def call(**kwargs: Any) -> Any:
            result = execute(kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result

        return Tool.from_schema(
            call,
            name=name,
            description=getattr(tool, "description", "") or "",
            json_schema=parameters,
        )

    if callable(tool):
        return tool
    return None
