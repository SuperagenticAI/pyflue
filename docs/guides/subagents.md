# Subagents

A subagent lets an agent delegate focused work to a fresh child session: inspect
a package, review a change, extract a result, or run a bounded specialist step.
The delegated work uses the same sandbox workspace while keeping its
investigation out of the parent session's history.

A task is a session operation that creates a detached child session. It is not a
workflow run. When a task runs inside a workflow, it is nested work within that
run. When it runs during agent activity, it stays agent session activity.

## Delegate an anonymous task

Call `session.task(...)` without an `agent` option when the child should use the
parent's defaults. The child gets a fresh conversation history and shared access
to the sandbox workspace.

```python
response = await session.task(
    "Inspect the auth package and identify where refresh tokens are validated. "
    "Return file paths and function names.",
    cwd="packages/auth",
)
print(response.text)
```

Give the task a self contained request. The child does not see the parent's
transcript, so include the expected output, any evidence requirements, and the
scope boundary in the task text.

## Define a reusable profile

Use `define_agent_profile(...)` when delegated work should consistently receive
a role, model, reasoning level, instructions, or tools. A profile is reusable
configuration. It is not a deployed agent and has no endpoint.

```python
from pyflue import AgentProfile, create_agent, define_agent_profile

reviewer = define_agent_profile(
    AgentProfile(
        name="reviewer",
        description="Reviews changes for concrete correctness risks.",
        model="anthropic/claude-sonnet-4-6",
        thinking_level="high",
        instructions="Report only issues with a reproducible failure scenario and file evidence.",
    )
)

coordinator = create_agent(lambda ctx: {"model": "anthropic/claude-haiku-4-5", "subagents": [reviewer]})
```

Attach profiles through `subagents` on the created agent. Every selectable
subagent needs a unique name.

## Select a named subagent

Pass `agent="name"` to choose a declared profile for a task.

```python
response = await session.task(
    "Review the pending changes in pyflue/runtime.",
    agent="reviewer",
)
```

A selected profile establishes the child's behavior. The profile's instructions,
model, reasoning level, and tools become the child's defaults. Task level
options still override the profile.

| Source | Precedence for the child |
| --- | --- |
| Instructions | Profile instructions, otherwise the parent instructions. |
| Model | Task `model`, otherwise profile model, otherwise parent model. |
| Reasoning | Task `thinking_level`, otherwise profile level, otherwise the default. |
| Tools | Profile tools plus any task local tools. |

```python
response = await session.task(
    "Audit dependency usage under pyflue/cli.",
    agent="reviewer",
    model="anthropic/claude-sonnet-4-6",
    cwd="pyflue/cli",
)
```

## Structured task results

Pass a Pydantic model as `result` to receive validated data instead of parsing
free form text.

```python
from pydantic import BaseModel


class Boundary(BaseModel):
    files: list[str]
    entrypoint: str


response = await session.task(
    "Locate the HTTP authentication boundary and explain its entrypoint.",
    result=Boundary,
    cwd="pyflue",
)
boundary = response.result
```

## Roles and profiles

PyFlue also supports Markdown roles in `.agents/roles/`, selected with
`role="name"`. Roles and profiles interoperate: a profile can be adapted to a
role and back with `profile_to_role(...)` and `role_to_profile(...)`. A profile
declared through `subagents` is selectable with `task(agent="name")`; a Markdown
role is selectable with `role="name"`. Both flow through the same child session
machinery.

## Concurrency and depth

A session permits one active operation at a time. Use separate named sessions for
independent branches rather than running concurrent operations on one session.
Delegation is bounded by a maximum task depth, so prefer shallow, explicitly
scoped delegation over long chains of handoffs.

## Related guides

- [Agents](agents.md) for created agent configuration and continuing sessions.
- [Workflows](workflows.md) for finite operations and why runs stay workflow only.
- [Tools](tools.md) for model callable capabilities.
