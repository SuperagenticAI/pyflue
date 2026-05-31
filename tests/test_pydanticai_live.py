"""Live verification of the Pydantic AI harness against a real model.

This makes real local inference calls through Ollama, so it is gated and skipped
by default. Run it with a running Ollama server:

    OLLAMA_BASE_URL=http://localhost:11434/v1 PYFLUE_LIVE_OLLAMA=1 \\
        uv run pytest tests/test_pydanticai_live.py -q

Set PYFLUE_LIVE_OLLAMA_MODEL to choose the model (default: ollama:qwen3:8b).
It proves the harness drives PyFlue against a real model: text generation, real
token usage, and a real tool-calling round-trip into a PyFlue tool.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("pydantic_ai")

_ENABLED = bool(os.environ.get("PYFLUE_LIVE_OLLAMA") and os.environ.get("OLLAMA_BASE_URL"))

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not _ENABLED,
        reason="set PYFLUE_LIVE_OLLAMA=1 and OLLAMA_BASE_URL to run the live Pydantic AI check",
    ),
]

MODEL = os.environ.get("PYFLUE_LIVE_OLLAMA_MODEL", "ollama:qwen3:8b")


@pytest.mark.asyncio
async def test_real_text_generation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from pyflue import init

    agent = await init(harness="pydanticai", model=MODEL)
    result = await (await agent.session("verify")).prompt("Reply with exactly the single word: PONG")
    assert "pong" in result.text.lower()
    assert result.usage.total_tokens > 0


@pytest.mark.asyncio
async def test_real_tool_call(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from pyflue import define_tool, init

    calls: dict = {}

    def multiply(args):
        calls["args"] = args
        return int(args["a"]) * int(args["b"])

    tool = define_tool(
        "multiply",
        multiply,
        description="Multiply two integers a and b. Use this for any multiplication.",
        parameters={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    )
    agent = await init(harness="pydanticai", model=MODEL, tools=[tool])
    await (await agent.session("verify")).prompt(
        "Use the multiply tool to compute 6 times 7, then report the number."
    )
    assert calls.get("args"), "the model never called the multiply tool"
    assert {int(v) for v in calls["args"].values()} == {6, 7}
