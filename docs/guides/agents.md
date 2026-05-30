# Agents

An agent is the right boundary when a model driven assistant should keep working
under the same identity and session over time. This guide covers defining an
agent, configuring its capabilities, exposing it over HTTP and WebSocket, and
delivering events to it asynchronously. For the broader mental model, see
[Agents vs Workflows](../concepts/agents-vs-workflows.md).

## Define an agent

A persistent agent is a module whose default export is a created agent. Place it
in `src/agents/` (the canonical layout) or in the project root `agents/`
directory. The filename becomes the agent name.

```python title="src/agents/support_assistant.py"
from pyflue import create_agent

default = create_agent(
    lambda ctx: {
        "model": "anthropic/claude-haiku-4-5",
        "instructions": f"Help with the support case represented by {ctx.id}.",
    }
)
```

The initializer receives an `AgentCreateContext` with `id`, `env`, and
`payload`. The `id` identifies the continuing instance, for example a customer,
ticket, repository, or chat thread. Use it to scope the resources that belong to
that instance.

```python title="src/agents/support_assistant.py"
from pyflue import create_agent, define_tool


def build(ctx):
    ticket_id = ctx.id
    return {
        "model": "anthropic/claude-haiku-4-5",
        "instructions": "Help the customer resolve their support ticket.",
        "tools": [
            define_tool(
                "ticket_status",
                lambda args: lookup_status(ticket_id),
                description="Return the status of the current ticket.",
                parameters={"type": "object", "properties": {}},
            )
        ],
    }


default = create_agent(build)
```

## Configure the runtime

The configuration returned by the initializer accepts model facing fields
(`model`, `instructions`, `thinking_level`, `tools`, `skills`, `subagents`,
`compaction`) and runtime fields (`sandbox`, `cwd`, `persist`). Use a profile to
share model facing behavior across agents and workflows.

```python
from pyflue import create_agent, define_agent_profile
from pyflue.sandboxes import local

reviewer = define_agent_profile(
    {
        "model": "anthropic/claude-sonnet-4-6",
        "instructions": "Review the requested change and report evidence backed findings.",
    }
)

default = create_agent(
    lambda ctx: {
        "profile": reviewer,
        "sandbox": local(),
        "cwd": "/srv/repositories/catalog-service",
    }
)
```

Fields set on the configuration replace the matching profile fields. Lists of
tools, skills, and subagents are merged.

## Send a prompt over HTTP

Run the server with `pyflue dev` and send a message to an instance. The body
carries a `message` and may select a named `session`.

```bash
curl http://127.0.0.1:2024/agents/support_assistant/ticket-8472 \
  -H "Content-Type: application/json" \
  -d '{"message": "Summarize the open issues in my case.", "session": "billing"}'
```

The response is the reference result envelope. A direct prompt continues an
agent session. It does not create a workflow run and does not return a run id.

```json
{ "result": { "text": "...", "usage": {}, "model": { "id": "..." } } }
```

Each instance keeps its sessions separate. The same `id` with different
`session` values gives one instance several independent conversation threads,
each with its own history.

## Stream and connect

Request `text/event-stream` from the same endpoint to observe activity while one
prompt runs. For a client that sends several prompts over one connection, use
the WebSocket surface and the client helper.

```python
from pyflue import PyFlueClient

client = PyFlueClient("http://127.0.0.1:2024")
async with client.agents.connect("support_assistant", "ticket-8472") as conn:
    reply = await conn.prompt("What changed since yesterday?", session="billing")
    print(reply["result"]["text"])
```

See the [Client](client.md) guide for the full client surface.

## Authorize the caller

When an agent has HTTP or WebSocket exposure, the caller selects the `id`.
Verify that the caller may access that instance before continuing, especially
when tools or resources are scoped by `id`. Place that check in your own ingress
or in a wrapper around the agent, and do not let an untrusted caller select
another instance by changing the URL.

## Accept asynchronous input with dispatch

Use `dispatch(...)` when your application receives an event for an agent but the
inbound request should not stay open while the model works. Examples include
verified webhooks, queue messages, and chat events.

```python
from pyflue import dispatch
from src.agents.support_assistant import default as support_assistant


async def accept_comment(event):
    return await dispatch(
        support_assistant,
        id=event["customer_id"],
        session=event["case_id"],
        input={"type": "support.comment.created", "text": event["text"]},
    )
```

`dispatch(...)` validates that the input is JSON serializable, accepts it for
background processing, and returns a `DispatchReceipt` with `dispatch_id` and
`accepted_at`. It does not wait for a reply and does not create a workflow run.
The agent acts on the input through its own tools. See the
[chat example](https://github.com/SuperagenticAI/pyflue/tree/main/examples/chat)
for a webhook to dispatch to reply pattern.

On the current Python path, dispatch uses process memory admission, so accepted
work can be lost on restart. Choose a durable delivery path when restart safe
processing is required.

## Drive an agent from code

Outside a server, resolve a created agent into a live harness with
`init_agent(...)` and use its sessions directly.

```python
from pyflue import init_agent
from src.agents.support_assistant import default as support_assistant

harness = await init_agent(support_assistant, id="ticket-8472")
session = await harness.session("billing")
result = await session.prompt("Draft a reply to the latest comment.")
```

## When to use an agent

Choose an agent when continuing identity and sessions are central: an assistant
that receives many messages, a chat thread that accumulates context, or event
driven processing through `dispatch(...)`. Choose a [workflow](workflows.md) when
the unit of work is one bounded operation that returns a result.
