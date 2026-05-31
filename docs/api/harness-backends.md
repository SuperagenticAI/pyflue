# Harness Backends

PyFlue uses a backend registry so the public session API can stay stable while
teams can choose the harness runtime that fits their project.

## Built-In Backends

| Backend | Status | Install |
| --- | --- | --- |
| `pydanticai` | Implemented, default | included with PyFlue |
| `deepagents` | Implemented, optional | `pip install 'pyflue[deepagents]'` |
| `openai_agents` | Extension point | `pip install 'pyflue[openai]'` |
| `google_adk` | Extension point | `pip install 'pyflue[google]'` |

Pydantic AI is the default harness: typed, model agnostic, and free of LangChain.
DeepAgents remains available as an optional extra for teams already invested in
LangChain and LangGraph. Select a backend with `init(harness="...")` or
`harness = "..."` in `pyflue.toml`. The session API is identical across backends,
so switching the harness does not change your agent or workflow code.

## DeepAgents Backend

The DeepAgents backend is optional and built on LangChain and LangGraph. Install
it and select it explicitly:

```bash
pip install 'pyflue[deepagents]'
```

```python
agent = await init(harness="deepagents")
```

The DeepAgents backend provides:

- model
- project instructions
- Markdown skills
- session continuity
- sandbox file tools
- shell execution through policy
- task-friendly agent behavior
- optional Python code tool when Monty is enabled

It is built on LangChain and LangGraph.

## Pydantic AI Backend

The Pydantic AI backend is the default. It drives PyFlue with the Pydantic AI
agent loop: typed, model agnostic, and free of LangChain or LangGraph. It is
included with PyFlue, so it needs no extra install.

```python
agent = await init(model="anthropic/claude-sonnet-4-6")  # harness="pydanticai" by default
```

The optional `pyflue[pydanticai]` extra adds the Pydantic AI Harness capability
library; the backend itself works without it.

The backend:

- builds a Pydantic AI `Agent` from the configured model and system prompt
- adapts PyFlue's merged tools (built-in sandbox file and shell tools, custom
  tools, command tools, and MCP tools) into Pydantic AI tools, using the tool's
  JSON schema where one is available
- runs the loop and returns the text result with token usage
- works with every PyFlue session operation, including `prompt`, `skill`,
  `task`, and subagent delegation, because they all run through the backend

PyFlue model strings may use `provider/model` or `provider:model`; the backend
normalizes them for Pydantic AI. Use Pydantic AI's standard provider environment
variables for credentials.

### Try it with no API key (Ollama)

The backend works with any Pydantic AI provider. A local Ollama model needs no
key, which makes it a good way to verify the backend end to end:

```bash
export OLLAMA_BASE_URL=http://localhost:11434/v1
```

```python
agent = await init(harness="pydanticai", model="ollama:qwen3:8b")
session = await agent.session("demo")
result = await session.prompt("Reply with one word: PONG")
```

This path is exercised by a gated live test that runs real local inference and a
real tool-calling round-trip:

```bash
OLLAMA_BASE_URL=http://localhost:11434/v1 PYFLUE_LIVE_OLLAMA=1 \
    uv run pytest tests/test_pydanticai_live.py -q
```

## Custom Backend Registration

```python
from pyflue import register_harness
from pyflue.harnesses.base import HarnessBackend


class CustomBackend(HarnessBackend):
    name = "custom"

    async def run(self, **kwargs):
        ...


register_harness("custom", CustomBackend)
```

Then use it:

```python
agent = await init(harness="custom")
```

## Optional Backends

Pydantic AI is implemented and selected with `harness="pydanticai"` (see above).
OpenAI Agents SDK and Google ADK are available as optional package extras and
serve as extension points for teams that want to build those backends behind the
PyFlue API.

```bash
uv add "pyflue[pydanticai]"
uv add "pyflue[openai]"
uv add "pyflue[google]"
```

```bash
pip install "pyflue[pydanticai]"
pip install "pyflue[openai]"
pip install "pyflue[google]"
```
