# Workflows

A workflow is PyFlue's boundary for finite, result oriented work: a background
job, a document transformation, a code review, or a CI task. It receives an
input payload, runs ordinary Python, may initialize an agent and perform
operations, and returns a result. Each invocation is a workflow run with its own
identity and lifecycle. For continuing conversations, use an [agent](agents.md)
instead.

## Define a workflow

A workflow is a module in `src/workflows/` (or the project root `workflows/`)
that exports a `run(ctx)` function. The filename becomes the workflow name.

```python title="src/workflows/summarize.py"
from pyflue import FlueContext, create_agent

agent = create_agent(lambda ctx: {"model": "anthropic/claude-haiku-4-5"})


async def run(ctx: FlueContext) -> dict:
    ctx.log.info("summarization started", audience=ctx.payload.get("audience", "general"))
    harness = await ctx.init(agent)
    session = await harness.session()
    response = await session.prompt(f"Summarize this text:\n\n{ctx.payload['text']}")
    return {"summary": response.text}
```

The application logic stays visible in Python. The workflow decides what input
the agent receives and what value to return. An agent operation is one step
inside the orchestration, not the workflow boundary itself.

## The workflow context

`FlueContext` carries the invocation identity and helpers.

| Member | Meaning |
| --- | --- |
| `ctx.id` | The run identity. For workflows, `ctx.id` equals the run id. |
| `ctx.payload` | The input supplied for this invocation. |
| `ctx.env` | Process environment values available to host code. |
| `ctx.req` | The HTTP request when invoked over HTTP, otherwise `None`. |
| `ctx.log` | Structured `info`, `warn`, and `error` events recorded with the run. |
| `ctx.init(agent)` | Initializes a created agent for this invocation and returns its harness. |

## Initialize an agent

`ctx.init(agent)` initializes a created agent with this invocation's identity
and returns a harness. The harness gives you sessions for model work and a
filesystem and shell for staging the workspace.

```python title="src/workflows/review_document.py"
from pyflue import FlueContext, create_agent

reviewer = create_agent(lambda ctx: {"model": "anthropic/claude-sonnet-4-6", "cwd": "/workspace"})


async def run(ctx: FlueContext) -> dict:
    harness = await ctx.init(reviewer)
    await harness.fs.write_file("document.md", ctx.payload["document"])

    session = await harness.session()
    await session.prompt("Review document.md and write your findings to review.md.")
    return {"review": await harness.fs.read_file("review.md")}
```

`harness.fs` and `harness.shell(...)` are workflow controlled setup steps. They
do not add messages to the session conversation. A session is where the agent's
work accumulates context, so sequential prompts in one session build on each
other.

## Structured results

When the workflow needs dependable data rather than prose, pass a Pydantic model
as the prompt `result`. The agent must return data that satisfies the model.

```python title="src/workflows/classify_ticket.py"
from pydantic import BaseModel
from pyflue import FlueContext, create_agent

triage = create_agent(lambda ctx: {"model": "anthropic/claude-sonnet-4-6"})


class Classification(BaseModel):
    priority: str
    summary: str


async def run(ctx: FlueContext) -> dict:
    harness = await ctx.init(triage)
    session = await harness.session()
    result = await session.prompt(ctx.payload["ticket"], result=Classification)
    return result.model_dump()
```

## Run a workflow

A workflow can be invoked locally, over HTTP, or over WebSocket.

### Local

```bash
pyflue run summarize --payload '{"text": "PyFlue workflows are finite operations."}'
```

`pyflue run` discovers the workflow, executes one run, and prints the result.

### HTTP

`POST /workflows/<name>` starts a run. The caller chooses how to observe it.

| Mode | Request | Response |
| --- | --- | --- |
| Accepted (default) | `POST /workflows/summarize` | `202 {status: accepted, run_id}` |
| Wait for result | `POST /workflows/summarize?wait=result` | `200 {status: completed, run_id, result}` |
| Stream | `Accept: text/event-stream` | Server sent run events until completion |

```python
from pyflue import PyFlueClient

client = PyFlueClient("http://127.0.0.1:2024")
result = await client.workflows.invoke("summarize", {"text": "..."}, wait=True)
print(result["result"]["summary"])
```

### WebSocket

A workflow WebSocket carries one finite invocation. The client sends the payload
and receives the run events and a final result, then the socket closes.

```python
async with client.workflows.connect("summarize") as conn:
    messages = await conn.run({"text": "..."})
```

## Workflow runs

Every invocation creates one run with an id shaped `workflow:<name>:<ulid>`. The
run records its supplied payload, its completed result or error, and the events
from the operations performed inside `run(...)`. Inspect a run with the run APIs
or `pyflue logs <run_id>`. Only workflows create runs. Direct and dispatched
agent interactions correlate by instance and operation instead. See
[Observability](observability.md) for the event model.

## When to use a workflow

Choose a workflow for a bounded objective with one inspectable outcome: generate
a report, transform a document, run a finite CI operation, or accept a job and
provide a run id for later inspection. Choose an [agent](agents.md) when a
continuing instance or conversation is the core abstraction.
