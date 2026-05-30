# Tools

Tools are the typed, model callable actions an agent can take beyond generating
text. PyFlue gives the model built in filesystem, shell, search, and delegation
tools through the sandbox, and lets you add your own with `define_tool(...)` and
`PyFlueCommand`.

## Define a tool

`define_tool(name, execute, *, description, parameters)` returns a
`ToolDefinition` (also exported as `ToolDef`). The `execute` callable receives
the tool arguments as one dictionary.

```python
from pyflue import define_tool

lookup_weather = define_tool(
    "lookup_weather",
    lambda args: get_forecast(args["city"]),
    description="Return the current forecast for a city.",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
)
```

`execute` may be synchronous or asynchronous and should return a value the model
can read, usually a string or a JSON serializable object.

## Attach tools

Tools can be attached at several levels. They merge, so an agent's tools and a
call's tools are both available.

```python
from pyflue import create_agent

# On a created agent or profile
default = create_agent(lambda ctx: {"model": "anthropic/claude-haiku-4-5", "tools": [lookup_weather]})

# On a single prompt or task
result = await session.prompt("What should I wear in Paris?", tools=[lookup_weather])
```

Tools defined on a created agent close over the instance context. Bind per
instance resources in the initializer so the model chooses only the action, not
the target.

```python
def build(ctx):
    ticket_id = ctx.id
    return {
        "model": "anthropic/claude-haiku-4-5",
        "tools": [
            define_tool(
                "close_ticket",
                lambda args: close(ticket_id),
                description="Close the current ticket.",
                parameters={"type": "object", "properties": {}},
            )
        ],
    }


default = create_agent(build)
```

## Command tools

`PyFlueCommand` exposes a reusable shell command or Python callable as a named
tool. Use `define_command(...)` for the concise form. Shell commands run through
the active session sandbox and its policy.

```python
from pyflue import define_command

run_tests = define_command(
    "run_tests",
    "pytest -q",
    description="Run the test suite.",
)

agent = await init(
    model="anthropic/claude-sonnet-4-6",
    allow_shell=True,
    commands=[run_tests],
)
```

A callable command receives keyword arguments from the model and returns a
normalized result.

```python
summarize = define_command(
    "summarize_rows",
    lambda rows: {"count": len(rows)},
    description="Summarize the supplied rows.",
)
```

## Create several tools at once

`create_tools(...)` builds a list of tool definitions from a mapping, which is
convenient when registering a set of related actions.

```python
from pyflue import create_tools

tools = create_tools(
    {
        "get_user": {"execute": get_user, "description": "Fetch a user by id."},
        "list_orders": {"execute": list_orders, "description": "List a user's orders."},
    }
)
```

## Built in tools and the sandbox

When an agent has a sandbox, the model also has built in tools for reading,
writing, editing, searching, and running shell commands, subject to the sandbox
policy. Write access requires `allow_write=True` and shell access requires
`allow_shell=True`, with optional command allowlists. See
[Sandbox](../concepts/sandbox.md) for the execution surface and policy controls.

## Naming

`ToolDefinition` is the canonical name and matches the reference. `ToolDef`
remains available as an alias, so existing code keeps working.
