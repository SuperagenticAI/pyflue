# Roles And Routing

PyFlue supports scoped roles and file-based agent routes.

## Roles

Roles live in `.agents/roles`.

```text
.agents/
  roles/
    coder.md
    data_analyst.md
```

Example role:

```markdown
---
name: coder
description: Careful coding role
---

You are a careful coding agent. Inspect before editing and verify your work.
```

Use the role on a prompt, skill, or task:

```python
result = await session.prompt(
    "Review this patch",
    role="coder",
)
```

## File-Based Routes

Python files in `agents/` or `.agents/` become webhook routes.

```text
agents/
  code.py
```

The route is:

```text
POST /agents/code/{agent_id}
```

Agent file:

```python
triggers = {"webhook": True}


async def default(context):
    agent = await context.init()
    session = await agent.session(context.agent_id)
    result = await session.prompt(context.payload["prompt"])
    return {"text": result.text}
```

Start the development server:

```bash
pyflue dev --port 2024
```

Call the route:

```bash
curl http://127.0.0.1:2024/agents/code/default \
  -H "Content-Type: application/json" \
  -d '{"payload": {"prompt": "Review this repository"}}'
```

HTTP errors use a stable JSON envelope:

```json
{
  "error": {
    "type": "agent_not_found",
    "message": "Agent \"missing\" is not registered.",
    "details": "Verify the agent name is correct."
  }
}
```

The server validates request method, JSON content type, JSON body shape,
registered agent names, and webhook trigger settings.

Secrets are available to host code through `context.env`, but PyFlue does not
inject those values into prompts.

## Triggers

Routes are webhook-enabled by default. You can disable the webhook route or add
metadata for your own scheduler:

```python
triggers = {
    "webhook": False,
    "schedule": "0 * * * *",
}
```

The built-in server only exposes webhook routes. Schedule metadata is available
through route discovery so applications can wire their own scheduler.
