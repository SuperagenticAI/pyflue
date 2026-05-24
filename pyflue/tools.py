"""Custom tool helpers for PyFlue."""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from pyflue.types import ToolDef


def define_tool(
    name: str,
    execute: Any | None = None,
    *,
    description: str = "",
    parameters: dict[str, Any] | None = None,
) -> ToolDef:
    """Create a Flue-compatible custom tool definition."""
    return ToolDef(
        name=name,
        description=description,
        parameters=parameters or {"type": "object", "properties": {}},
        execute=execute,
    )


def create_tools(*tools: ToolDef | dict[str, Any] | Any) -> list[Any]:
    """Convert Flue-style tool definitions into PyFlue harness callables.

    Plain callables pass through unchanged.
    """
    if len(tools) == 1 and _is_tool_iterable(tools[0]):
        tools = tuple(tools[0])
    return [to_callable_tool(tool) for tool in tools]


createTools = create_tools


def to_callable_tool(tool: ToolDef | dict[str, Any] | Any) -> Any:
    """Return a harness-callable tool for a ToolDef-like value."""
    if isinstance(tool, ToolDef):
        return _tool_def_to_callable(tool)
    if isinstance(tool, dict) and {"name", "execute"} <= set(tool):
        return _tool_def_to_callable(
            ToolDef(
                name=str(tool["name"]),
                description=str(tool.get("description") or ""),
                parameters=dict(tool.get("parameters") or {"type": "object", "properties": {}}),
                execute=tool.get("execute"),
            )
        )
    return tool


def _tool_def_to_callable(tool: ToolDef) -> Any:
    if tool.execute is None:
        raise ValueError(f'Tool "{tool.name}" has no execute function.')

    async def run(**kwargs: Any) -> Any:
        result = _call_execute(tool.execute, kwargs)
        if inspect.isawaitable(result):
            result = await result
        return _normalize_tool_result(result)

    run.__name__ = tool.name
    run.__doc__ = tool.description
    run.__pyflue_schema__ = tool.parameters
    return run


def _call_execute(execute: Any, kwargs: dict[str, Any]) -> Any:
    try:
        signature = inspect.signature(execute)
    except (TypeError, ValueError):
        return execute(kwargs)
    if "signal" in signature.parameters:
        return execute(kwargs, signal=None)
    return execute(kwargs)


def _normalize_tool_result(result: Any) -> Any:
    if result is None:
        return ""
    if isinstance(result, BaseModel):
        return result.model_dump()
    if isinstance(result, tuple):
        return list(result)
    return result


def _is_tool_iterable(value: Any) -> bool:
    return isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict))
