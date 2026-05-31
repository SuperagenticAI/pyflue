# Chat

PyFlue follows the Flue chat pattern: chat platforms connect through ordinary
application code, then verified events are dispatched to a continuing agent
instance. The agent replies through an explicit tool that your application owns.

```text
chat webhook -> verify request -> dispatch(agent, id=thread, session=thread)
             -> continuing agent session -> reply tool -> platform API
```

This keeps platform authentication, thread mapping, and outbound permissions out
of the model-facing transport. The chat provider does not choose a PyFlue route
or agent id directly; your application maps the provider event to an instance
and session after verification.

## Receive Events

Use your web framework to verify the inbound event and normalize only the fields
the agent should see.

```python
from fastapi import FastAPI, Request, Response
from pyflue import create_agent, dispatch

assistant = create_agent(
    lambda ctx: {
        "model": "anthropic/claude-haiku-4-5",
        "instructions": "Assist in this chat thread.",
    }
)

app = FastAPI()


@app.post("/webhooks/chat", status_code=202)
async def chat_webhook(request: Request):
    body = await request.json()
    if not verify_provider_signature(request):
        return Response(status_code=401)

    thread_id = str(body["thread"]["id"])
    receipt = await dispatch(
        assistant,
        id=thread_id,
        session=thread_id,
        input={
            "type": "chat.message",
            "text": body["message"]["text"],
            "sender": body["message"]["sender"],
        },
    )
    return {"status": "accepted", "dispatch_id": receipt.dispatch_id}
```

`dispatch()` accepts work for asynchronous processing. It does not create a
workflow run; it is an agent operation in the selected instance and session. On
the current Python path, dispatch admission is process-memory based. For
production chat, enqueue the verified event in durable application storage
before a worker calls `dispatch(...)`.

## Reply Explicitly

Outbound chat is a normal tool. Close over the verified thread id in the agent
initializer so the model chooses reply text, not the destination.

```python
from typing import Any
from pyflue import create_agent, define_tool


def build_chat_agent(ctx: Any):
    thread_id = ctx.id

    async def reply_to_chat_thread(args: dict[str, str]) -> str:
        await chat_client.post_message(thread_id, args["text"])
        return "Reply sent."

    return {
        "model": "anthropic/claude-haiku-4-5",
        "instructions": "Reply in the current chat thread when useful.",
        "tools": [
            define_tool(
                "reply_to_chat_thread",
                reply_to_chat_thread,
                description="Post a response into the current chat thread.",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            )
        ],
    }


assistant = create_agent(build_chat_agent)
```

## State Boundaries

A chat thread is usually the best default for both the agent instance id and the
session name. That gives each thread a continuing conversation while keeping
platform routing outside the agent.

Persist chat-side state, such as webhook deduplication and subscription data,
in the platform integration. Persist agent conversation state with a PyFlue
`SessionStore` when the conversation must survive process restarts. See
[Production](production.md) for the durable delivery checklist.

See `examples/chat/` for a runnable GitHub-style webhook example.
