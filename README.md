# PyFlue

[![CI](https://github.com/SuperagenticAI/pyflue/actions/workflows/ci.yml/badge.svg)](https://github.com/SuperagenticAI/pyflue/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-superagenticai.github.io%2Fpyflue-blue)](https://superagenticai.github.io/pyflue/)
[![Landing Page](https://img.shields.io/badge/landing-super--agentic.ai%2Fpyflue-black)](https://super-agentic.ai/pyflue)
[![PyPI](https://img.shields.io/pypi/v/pyflue)](https://pypi.org/project/pyflue/)
[![Python](https://img.shields.io/pypi/pyversions/pyflue)](https://pypi.org/project/pyflue/)
[![License](https://img.shields.io/pypi/l/pyflue)](https://github.com/SuperagenticAI/pyflue/blob/main/LICENSE)


PyFlue is the agent harness framework for Python. You build persistent **agents**
and finite **workflows**, with Markdown skills, stateful sessions, sandboxed
filesystem and shell access, typed Pydantic outputs, streaming events,
OpenTelemetry tracing, and deployment-ready project structure.

PyFlue adapts the agent harness model for Python teams. The harness that drives
it is pluggable: Pydantic AI by default (typed, model agnostic, no LangChain), or
DeepAgents for LangChain users, with the registry open for more backends.

> **Warning: Active Development**
>
> PyFlue is under active development. The API may change. Pin your dependencies
> and review changelogs before updating.

Use it to build coding agents, issue triage agents, data analysis agents,
support agents, and workflow agents that need controlled access to files,
commands, tools, and structured outputs.

Documentation: <https://superagenticai.github.io/pyflue/>

Landing page: <https://super-agentic.ai/pyflue>

## Install

With `uv`:

```bash
uv add pyflue
```

With `pip`:

```bash
pip install pyflue
```

Optional extras:

```bash
uv add "pyflue[deepagents]"
uv add "pyflue[monty]"
uv add "pyflue[otel]"
uv add "pyflue[sandboxes]"
```

```bash
pip install "pyflue[deepagents]"
pip install "pyflue[monty]"
pip install "pyflue[otel]"
pip install "pyflue[sandboxes]"
```

## Quick Start

```bash
pyflue init my-agent
cd my-agent
pyflue run --prompt "Review this project"
```

Run a local server/client smoke demo without a model key:

```bash
uv run python examples/server_client/run_smoke.py
```

## Agents and Workflows

PyFlue has two boundaries for model driven work. A persistent **agent** keeps
sessions over time; a finite **workflow** runs one bounded operation and returns
a result.

```python
# A persistent agent in src/agents/assistant.py
from pyflue import create_agent

default = create_agent(lambda ctx: {"model": "openai:gpt-5.5"})
```

```python
# A finite workflow in src/workflows/summarize.py
from pyflue import FlueContext, create_agent

agent = create_agent(lambda ctx: {"model": "openai:gpt-5.5"})


async def run(ctx: FlueContext) -> dict:
    harness = await ctx.init(agent)
    session = await harness.session()
    response = await session.prompt(ctx.payload["text"])
    return {"summary": response.text}
```

Agents are served at `POST /agents/<name>/<id>` and over WebSocket, and accept
asynchronous input with `dispatch(...)`. Workflows run with `pyflue run <name>`,
`POST /workflows/<name>`, or WebSocket. See the
[Agents vs Workflows](https://superagenticai.github.io/pyflue/concepts/agents-vs-workflows/)
guide.

## Choose a harness

The harness that runs the model loop is pluggable and does not change your agent
or workflow code.

```python
agent = await init(harness="pydanticai")    # default: typed, model agnostic, no LangChain
agent = await init(harness="deepagents")    # optional, for LangChain users: pip install 'pyflue[deepagents]'
```

## Python API

```python
from pydantic import BaseModel
from pyflue import init


class FixResult(BaseModel):
    fix_applied: bool
    summary: str


async def main():
    agent = await init(
        model="openai:gpt-5.5",
        sandbox="virtual",
        allow_write=True,
        allow_shell=True,
        allowed_commands=["git"],
    )
    session = await agent.session("fix-123")
    result = await session.skill(
        "triage",
        args={"issue_number": 123},
        result=FixResult,
    )
    if result.fix_applied:
        await session.shell("git status --short")
```

## What PyFlue Gives You

| Capability | What it means |
| --- | --- |
| Agents | Define persistent, addressable agents with `create_agent`, served over HTTP and WebSocket. |
| Workflows | Define finite operations with `run(ctx)`, run locally, over HTTP, or WebSocket. |
| Subagents | Delegate to declared profiles with `task(agent="name")`. |
| Dispatch | Accept asynchronous agent input with `dispatch(...)`. |
| Observability | Correlated event stream and an OpenTelemetry adapter (`pyflue[otel]`). |
| Harness backends | Pydantic AI by default, DeepAgents as an optional extra, and custom backends through a registry. |
| Models and providers | Use provider-qualified model strings, reasoning effort hints, and provider endpoint overrides. |
| Markdown skills | Put reusable workflows in `.agents/skills/*.md`. |
| Project instructions | Use `AGENTS.md` for global behavior and context. |
| Roles | Scope behavior with `.agents/roles/*.md`. |
| Sessions | Resume agent state with stable session IDs. |
| Tasks | Run focused child tasks with isolated history and shared sandbox. |
| Sandbox | Read, write, edit, grep, glob, and shell behind explicit policies. |
| Secret grants | Keep secrets out of prompts and grant them only per call. |
| Typed outputs | Validate results with Pydantic, extract JSON from text, and repair invalid JSON automatically. |
| Streaming | Use `session.stream(...)`, `pyflue run --stream`, or SSE. |
| Abort | Cancel active prompt, stream, task, and shell operations with `session.abort()`. |
| Structured commands | Expose reusable shell or callable commands with `PyFlueCommand`. |
| Python client | Call deployed PyFlue servers with `PyFlueClient`. |
| Chat integrations | Use verified webhooks, `dispatch(...)`, and explicit reply tools for chat platforms. |
| Webhooks | Expose `agents/*.py` as `/agents/{name}/{agent_id}`. |
| Python code backend | Use `pyflue[monty]` for safe host-side Python snippets. |
| Remote sandboxes | Use Daytona, E2B, Modal, or Runloop with optional extras. |
| Connector guides | Use `pyflue add` to print agent-readable setup guides for sandbox providers. |
| Deployment | Generate Docker/FastAPI, CI, Railway, Render, Fly.io, Vercel, Netlify, and Cloudflare Containers starter files. |

## Project Layout

`src/` is the canonical layout. Agents and workflows are also discovered from
the project root and from `.agents` or `.pyflue`.

```text
AGENTS.md
pyflue.toml
.agents/
  roles/
    coder.md
  skills/
    triage.md
src/
  agents/
    assistant.py
  workflows/
    summarize.py
```

## File-Based Agent (legacy)

The original file based handler model is still supported and is treated as
workflow like. New projects use `create_agent` agents and `run(ctx)` workflows
(see [Agents and Workflows](#agents-and-workflows)).

```python
triggers = {"webhook": True}


async def default(context):
    agent = await context.init()
    session = await agent.session(context.agent_id)
    result = await session.prompt(context.payload["prompt"])
    return {"text": result.text}
```

Run it locally:

```bash
pyflue dev --port 2024
```

Call it:

```bash
curl http://127.0.0.1:2024/agents/default/demo \
  -H "Content-Type: application/json" \
  -d '{"payload": {"prompt": "Review this repository"}}'
```

## Streaming

```bash
pyflue run --stream --prompt "Review this project"
```

```python
async for event in session.stream("Review this project"):
    print(event.type, event.data)
```

## Connector Guides

List available guides:

```bash
pyflue add
```

Print a guide for a known sandbox provider:

```bash
pyflue add daytona --print
```

Start from any provider documentation URL:

```bash
pyflue add https://e2b.dev/docs --category sandbox --print | codex
```

## Security Model

PyFlue starts with safe defaults:

- writes are disabled until `allow_write=True`
- shell execution is disabled until `allow_shell=True`
- compound shell syntax is blocked by default
- command allowlists are supported with `allowed_commands`
- secrets are not injected into prompts
- secrets are mounted into sandbox calls only when requested with `secrets=[...]`

For production webhooks, queues, and chat integrations, put durable delivery in
front of `dispatch(...)`. The current Python dispatch path accepts work in
process memory, so accepted work can be lost if the process exits before
delivery finishes. See the
[Production guide](https://superagenticai.github.io/pyflue/guides/production/).

## Deployment

Generate deployment files:

```bash
pyflue build --target docker
pyflue build --target railway
pyflue build --target fly
pyflue build --target vercel
pyflue build --target netlify
pyflue build --target cloudflare
```

Deploy with a supported provider CLI:

```bash
pyflue deploy --target fly
```

## Development

```bash
uv sync --extra dev --extra docs
uv run --extra dev ruff check .
uv run --extra dev pytest
uv run --extra docs mkdocs build --strict
uv build
```
