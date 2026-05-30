# Agents vs Workflows

PyFlue (v0.2.0+) mirrors the TypeScript Flue model: two boundaries for
model-driven work, chosen by **lifecycle and identity**, not by whether a model
is involved.

| | **Agent** | **Workflow** |
| --- | --- | --- |
| Defined by | `create_agent(initialize)` | `run(ctx)` in `workflows/` |
| Shape | Persistent, addressable instance | Finite, one bounded execution |
| Identity | `instance_id` + session | `run_id` (`workflow:<name>:<ulid>`) |
| Surfaces | `POST /agents/<name>/<id>`, WebSocket, `dispatch()` | `pyflue run <wf>`, `POST /workflows/<name>`, WebSocket |
| Creates a run? | **No** — correlates by instance/operation | **Yes** — `/runs/<id>`, `flue logs` |

```text
Need a continuing instance or conversation? → an agent session.
Need one bounded, observable result?         → a workflow run.
```

## Agents

A persistent agent keeps sessions across direct prompts and dispatched input:

```python
# agents/assistant.py
from pyflue import create_agent, define_tool

default = create_agent(lambda ctx: {
    "model": "anthropic/claude-haiku-4-5",
    "instructions": f"Help with support case {ctx.id}.",
})
```

Served at `POST /agents/assistant/<id>` (the `<id>` is the instance). Use
`dispatch(default, id=..., session=..., input=...)` to deliver events
asynchronously. Agent interactions are **not** runs.

## Workflows

A workflow is a finite job that may initialize an agent:

```python
# workflows/summarize.py
from pyflue import create_agent, FlueContext

agent = create_agent(lambda ctx: {"model": "anthropic/claude-haiku-4-5"})

async def run(ctx: FlueContext) -> dict:
    ctx.log.info("started")
    harness = await ctx.init(agent)
    session = await harness.session()
    res = await session.prompt(f"Summarize: {ctx.payload['text']}")
    return {"summary": res.text}
```

Each invocation is a run with a `run_id` and a `run_start … run_end` lifecycle,
inspectable via `/runs/<id>` and `pyflue run summarize --payload '{...}'`.

## Profiles and subagents

`define_agent_profile(...)` is reusable behaviour shared by agents/workflows.
Attach profiles via `create_agent(lambda ctx: {"subagents": [...]})` and select
one in a delegated task with `session.task("...", agent="reviewer")`.

## Observability

Workflow runs, agent operations, tools, tasks, and compaction emit a correlated
event stream. Export it to OpenTelemetry:

```python
from pyflue import init
from pyflue.observability import create_opentelemetry_observer

agent = await init(on_event=create_opentelemetry_observer())
```

See the [Feature Matrix](../reference/feature-matrix.md) for the full surface.
