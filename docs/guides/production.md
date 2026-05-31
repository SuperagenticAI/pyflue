# Production

This page collects the production choices that matter most for deployed PyFlue
agents: persistence, asynchronous delivery, authorization, secrets, and
observability.

## Persistence Boundaries

PyFlue has separate state surfaces. Choose persistence for each one explicitly.

| State | Default | Production option |
| --- | --- | --- |
| Session history | SQLite files under `.pyflue/sessions` | `SQLiteSessionStore` or a custom `SessionStore` |
| Workflow run/event history | In-memory server store | `PYFLUE_RUN_STORE=sqlite` |
| Dispatched agent input | Process-memory admission | Application-owned durable queue |
| Chat platform state | Your application | Provider SDK state, database, or queue |

Session persistence keeps conversation history. Run persistence keeps workflow
inspection data. Dispatch durability controls whether accepted asynchronous
work survives process restarts. These are related but not interchangeable.

## Session History

For local and single-process deployments, the default SQLite-backed session
history is usually enough. For multi-process or multi-host deployments, provide
a store with shared backing storage.

```python
from pyflue import SQLiteSessionStore, create_agent

store = SQLiteSessionStore(".pyflue/sessions.sqlite3")

default = create_agent(
    lambda ctx: {
        "model": "anthropic/claude-haiku-4-5",
        "persist": store,
    }
)
```

Use a custom `SessionStore` when your deployment already has Postgres, Redis,
or another managed state layer.

## Workflow Runs

The development server keeps run/event history in memory unless configured
otherwise. Enable SQLite run history when operators need to inspect workflow
runs after a process restart.

```bash
PYFLUE_RUN_STORE=sqlite
PYFLUE_RUN_STORE_PATH=.pyflue/runs.sqlite3
```

This persists workflow run records and events for `/runs/*`, `/admin/runs`, and
`pyflue logs`. It does not replay interrupted Python code.

## Dispatch Durability

`dispatch(...)` accepts input for asynchronous processing by a persistent agent
instance and returns immediately. On the current Python path, accepted dispatch
work is held by the process. If the process exits before delivery finishes, that
work can be lost.

For production webhooks, queues, and chat integrations, put a durable boundary
before or around `dispatch(...)`:

```python
@app.post("/webhooks/chat", status_code=202)
async def chat_webhook(request):
    event = await verify_and_normalize(request)
    await durable_queue.enqueue(event)
    return {"status": "accepted"}
```

Then run a worker that pulls from the queue and calls `dispatch(...)`, or calls
`init_agent(...)` and `session.prompt(...)` directly. Make the worker
idempotent using the provider event id or queue message id.

## Authorization

HTTP and WebSocket agent routes include caller-controlled path values:

```text
/agents/{name}/{id}
```

Treat `{id}` as an application resource identifier. Verify the caller is
allowed to access that instance before prompts can run, especially when tools
close over the instance id or access customer data.

Mount `/admin` behind authentication. The admin app is read-only, but it can
expose agent names, instance ids, run metadata, errors, and event payloads.

## Secrets

Keep provider keys and platform credentials in environment variables or a
secret manager. Do not place them in skills, roles, `AGENTS.md`, or prompts.

For shell operations, PyFlue only mounts configured secrets into the sandbox
when a call requests them:

```python
await session.shell(
    "gh issue view 123",
    secrets=["GITHUB_TOKEN"],
)
```

Keep shell allowlists narrow and leave compound commands disabled for untrusted
input.

## Observability

Attach an event observer for local logging or OpenTelemetry export:

```python
from pyflue import create_opentelemetry_observer, init

agent = await init(on_event=create_opentelemetry_observer())
```

Workflow runs can be inspected through `/runs/{run_id}`, `/runs/{run_id}/events`,
`/runs/{run_id}/stream`, and `pyflue logs <run_id>`. Direct agent prompts and
dispatched inputs are agent operations, not workflow runs.

## Deployment Checklist

- Choose a session store that matches your scaling model.
- Enable SQLite run history if operators need run inspection after restarts.
- Put durable delivery in front of webhook, queue, and chat dispatch.
- Protect `/agents/*`, `/workflows/*`, and `/admin/*` with application auth.
- Store provider credentials outside source control.
- Grant shell commands and secrets only where needed.
- Export events to your tracing or logging system.
- Run `ruff`, `pytest`, `mkdocs build --strict`, and `uv build` before release.
