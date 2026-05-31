# Client

`PyFlueClient` connects an application to a deployed PyFlue server. It mirrors
the reference SDK client: namespaces for agents, workflows, runs, and admin, plus
WebSocket helpers for live interaction.

## Create a client

```python
from pyflue import PyFlueClient

client = PyFlueClient("http://127.0.0.1:2024")
# or the factory form, matching the reference:
# from pyflue import create_flue_client
# client = create_flue_client(base_url="http://127.0.0.1:2024")

print(await client.health())
await client.close()
```

The client owns its HTTP connection unless you pass your own `httpx.AsyncClient`.
Use it as an async context manager to close it automatically.

## Agents

Send a prompt to a persistent agent instance, stream activity, or open a
WebSocket for several prompts over one connection.

```python
# List discovered agents
agents = await client.agents()

# One prompt
result = await client.agents.invoke("support_assistant", "ticket-8472", payload={"message": "Status?"})
print(result["result"]["text"])

# Multiple prompts over one connection
async with client.agents.connect("support_assistant", "ticket-8472") as conn:
    first = await conn.prompt("Summarize the case.", session="billing")
    second = await conn.prompt("Now draft a reply.", session="billing")
```

Persistent direct agent calls return `{"result": ...}` and do not include a run
id. Runs belong to workflows.

## Workflows

Start a workflow and choose how to observe it: accept a run id, wait for the
result, stream events, or open a WebSocket for one invocation.

```python
# Accept a run id immediately
receipt = await client.workflows.invoke("summarize", {"text": "..."})
run_id = receipt["run_id"]

# Wait for the completed result
done = await client.workflows.invoke("summarize", {"text": "..."}, wait=True)
print(done["result"]["summary"])

# Stream run events
async for event in client.workflows.stream("summarize", {"text": "..."}):
    print(event.type)

# One invocation over WebSocket
async with client.workflows.connect("summarize") as conn:
    messages = await conn.run({"text": "..."})
```

## Runs

Inspect a workflow run after it starts. Runs apply to workflows only.

```python
record = await client.runs.get(run_id)

# Persisted events, optionally after an index
events = await client.runs.events(run_id, after=0)

# Replay then tail until completion
async for event in client.runs.stream(run_id):
    print(event.type)
```

## Admin

The read only admin namespace lists agents, instances, and runs for an
operations view. Mount it behind your own authentication in production.

```python
agents = await client.admin.agents.list()
instances = await client.admin.instances.list("support_assistant")
runs = await client.admin.runs.list()
record = await client.admin.runs.get(run_id)
```

## WebSocket URLs

The client derives WebSocket URLs from the base URL: `http` becomes `ws` and
`https` becomes `wss`. Pass an `https://` base URL in production so connections
use `wss://`.

## Naming

Both snake_case and camelCase entry points are available
(`create_flue_client` and `createFlueClient`) so code ported from the reference
keeps working.
