# Python API

The Python API centers on `init`, `PyFlueAgent`, and `PyFlueSession`.

## `init`

```python
from pyflue import PyFlueCommand, define_command, init

agent = await init(
    model="openai:gpt-5.5",
    harness="deepagents",
    sandbox="virtual",
    python_backend="monty",
    skills_dir=".agents/skills",
    roles_dir=".agents/roles",
    allow_write=False,
    allow_shell=False,
    allowed_commands=("pytest", "git"),
    commands=(
        "ruff",
        PyFlueCommand(
            name="test",
            description="Run the test suite.",
            command="pytest -q",
            timeout=300,
        ),
        define_command("lint", "ruff check ."),
    ),
    tools=[],
    on_event=lambda event: print(event.type, event.data),
)
```

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `model` | `str | None` | config value | Model identifier passed to the backend. |
| `thinking_level` | `str | None` | config value | Reasoning effort hint (`off`, `minimal`, `low`, `medium`, `high`, or `xhigh`) where the backend/model supports it. |
| `harness` | `str | None` | `deepagents` | Harness backend name. |
| `sandbox` | `str | None` | `virtual` | Sandbox name. |
| `python_backend` | `str | None` | config value | Optional Python execution backend, such as `monty`. |
| `skills_dir` | `str | Path | None` | `.agents/skills` | Skill directory. |
| `roles_dir` | `str | Path | None` | `.agents/roles` | Role directory. |
| `config_path` | `str | Path | None` | `pyflue.toml` | Config file path. |
| `env` | `dict[str, str] | None` | `{}` | Runtime environment metadata. |
| `allow_write` | `bool` | `False` | Enable sandbox writes. |
| `allow_shell` | `bool` | `False` | Enable sandbox shell execution. |
| `allowed_commands` | `tuple[str, ...] \| list[str] \| None` | config value | Optional command grant list. |
| `allow_compound_commands` | `bool \| None` | config value | Allow shell operators and redirects. |
| `max_task_depth` | `int \| None` | config value | Maximum nested child task depth. |
| `commands` | `tuple[str \| PyFlueCommand, ...] \| list[str \| PyFlueCommand] \| None` | `None` | Agent-wide command grants and structured command tools. |
| `tools` | `list[Any] \| tuple[Any, ...] \| None` | `None` | Agent-wide custom tools available to every prompt, skill, and task call. |
| `providers` | `dict[str, dict] \| None` | config value | Provider endpoint, header, and API key overrides. |
| `mcp_servers` | `dict[str, dict] \| None` | config value | MCP server definitions. |
| `mcp_mode` | `"direct" \| "search_execute" \| None` | config value | MCP tool exposure mode. |
| `mcp_search_limit` | `int \| None` | config value | Number of MCP tools returned by search mode. |
| `mcp_search_backend` | `"bm25" \| "semantic" \| None` | config value | MCP search implementation. |
| `compaction_enabled` | `bool \| None` | config value | Enable or disable session history compaction. |
| `compaction_context_window_tokens` | `int \| None` | config value | Estimated model context window for automatic compaction. |
| `compaction_reserve_tokens` | `int \| None` | config value | Tokens to keep free before automatic compaction. |
| `compaction_keep_recent_tokens` | `int \| None` | config value | Recent tokens to keep verbatim during compaction. |
| `on_event` | `Callable[[PyFlueEvent], Any] \| None` | `None` | Optional callback for session lifecycle, task, command, and compaction events. |

## `PyFlueAgent`

### `session`

```python
session = await agent.session("issue-123")
```

`config_path` can point to `pyflue.toml` or `pyflue.config.py`. When
`pyflue.toml` is absent, `load_config()` and `init()` fall back to
`pyflue.config.py`.

If no session id is supplied, PyFlue uses `default`.

### `sessions`

Use explicit lifecycle helpers when you need create/load/delete semantics:

```python
created = await agent.sessions.create("issue-123")
loaded = await agent.sessions.get("issue-123")
await agent.sessions.delete("issue-123")
```

Deleting a session also deletes child task sessions recorded under that session.

### `shell`

Run a shell command through the default or named session:

```python
result = await agent.shell(
    "pytest -q",
    session_id="issue-123",
    cwd="packages/api",
    env={"PYTHONWARNINGS": "error"},
    commands=["pytest"],
)
```

Shell execution still follows sandbox policy.

### `destroy`

Close agent-level resources such as MCP connections:

```python
await agent.destroy()
```

## `PyFlueSession.prompt`

```python
result = await session.prompt("Summarize this repository")
print(result.text)
```

With typed output:

```python
result = await session.prompt(
    "Return a JSON triage result",
    result=TriageResult,
)
print(result.result)
print(result.usage.total_tokens)
print(result.model.id)
```

Typed responses expose `.result`, `.text`, `.usage`, `.model`, `.metadata`, and
`.raw`. Attribute access falls back to the parsed value, so existing
`result.summary` style code continues to work for Pydantic models.

Use a role:

```python
result = await session.prompt(
    "Review this patch",
    role="coder",
)
```

Override the model for a single call:

```python
result = await session.prompt(
    "Use a larger model for this review",
    model="openai:gpt-5.5",
)
```

Set reasoning effort or attach images for one call:

```python
result = await session.prompt(
    "Describe this screenshot",
    thinking_level="high",
    images=[{"type": "image_url", "image_url": {"url": "https://example.com/screen.png"}}],
)
```

## `PyFlueSession.skill`

```python
result = await session.skill(
    "triage",
    args={"issue_number": 123},
    result=TriageResult,
)
```

## `PyFlueSession.stream`

```python
async for event in session.stream("Review this project"):
    print(event.type, event.data)
```

The stream emits normalized events:

```text
start
delta
end
error
```

## `PyFlueSession.abort`

Cancel the active operation for a session:

```python
aborted = await session.abort()
```

`abort()` returns `True` when an active prompt, stream, task, or shell operation
was cancelled. It also cancels active child task sessions started by the
session. It returns `False` when the session is already idle. Cancellation emits
`abort_requested` and `aborted` events through `on_event`.

## `PyFlueSession.subagent`

```python
result = await session.subagent("Inspect the tests in isolation")
```

`subagent` creates a child PyFlue session with isolated history and the same
sandbox. Pass `cwd` to scope the child to a sandbox subdirectory:

```python
result = await session.subagent(
    "Inspect this package",
    cwd="packages/api",
)
```

## `PyFlueSession.task`

```python
result = await session.task(
    "Analyze the data files",
    role="data_analyst",
    cwd="datasets/may",
)
```

`task` is the child-agent primitive. It shares the parent sandbox
and uses an isolated child history. When `cwd` is set, file operations are
relative to that directory. For the virtual sandbox, PyFlue also reloads
`AGENTS.md`, skills, and roles from the scoped directory.

Task sessions record their parent session, task id, role, `cwd`, and child
task ids so lifecycle cleanup can remove the full task tree.

## Built-In Prompt Tools

Prompt calls automatically expose sandbox-backed tools to the harness:

| Tool | Description |
| --- | --- |
| `read(path, offset=None, limit=None)` | Read a file or list a directory. |
| `write(path, content)` | Write a file when write policy allows it. |
| `edit(path, old_text, new_text, replace_all=False)` | Replace exact text in a file. |
| `stat(path)` | Return file or directory metadata. |
| `exists(path)` | Check whether a path exists. |
| `mkdir(path, recursive=True)` | Create a directory when write policy allows it. |
| `rm(path, recursive=False, force=False)` | Remove a file or directory when write policy allows it. |
| `bash(command, timeout=120, cwd=None, env=None)` | Run a shell command when shell policy allows it. |
| `grep(pattern, path=".", include=None)` | Search files by regular expression. |
| `glob(pattern)` | Find files by glob pattern. |
| `task(prompt, description=None, role=None, cwd=None)` | Delegate focused work to a child session. |

Per-call custom tools are appended after built-ins:

```python
async def lookup_package(name: str) -> str:
    return f"Package: {name}"

await session.prompt("Use lookup_package if useful", tools=[lookup_package])
```

Custom tools cannot reuse built-in names.

For Flue-style tool definitions, use `ToolDef` or `define_tool`. `execute`
receives the model-provided arguments as one dictionary:

```python
from pyflue import ToolDef, create_tools, define_tool

async def lookup_issue(args: dict[str, str]) -> str:
    return f"Issue {args['number']}"

issue_tool = ToolDef(
    name="lookup_issue",
    description="Look up an issue by number.",
    parameters={
        "type": "object",
        "properties": {"number": {"type": "string"}},
        "required": ["number"],
    },
    execute=lookup_issue,
)

agent = await init(tools=[issue_tool])

# Helpers are available when you want callable tools explicitly.
tools = create_tools([
    define_tool("lookup_issue", lookup_issue, parameters=issue_tool.parameters)
])
await session.prompt("Use lookup_issue if useful", tools=tools)
```

Agent-wide tools are passed to `init` and are available to every prompt, skill,
and task:

```python
async def lookup_issue(number: int) -> str:
    return f"Issue #{number}"

agent = await init(tools=[lookup_issue])
```

Tool order is built-ins, agent-wide tools, MCP tools, then per-call tools.

## Structured Commands

Use `PyFlueCommand` for reusable named commands that appear as prompt tools:

```python
agent = await init(
    allow_shell=True,
    commands=[
        PyFlueCommand(
            name="test",
            description="Run the test suite.",
            command="pytest -q",
            timeout=300,
        )
    ],
)
```

Commands can also wrap Python callables:

```python
async def lookup_issue(number: int) -> str:
    return f"Issue #{number}"

agent = await init(
    commands=[
        PyFlueCommand(
            name="lookup_issue",
            description="Look up an issue.",
            callable=lookup_issue,
            schema={
                "type": "object",
                "properties": {"number": {"type": "integer"}},
            },
        )
    ],
)
```

Shell command objects support `cwd`, `env`, and `timeout`.

`define_command` is a shorter helper for common cases:

```python
agent = await init(
    commands=[
        define_command("test", "pytest -q", timeout=300),
        define_command("lookup_issue", lookup_issue),
        define_command(
            "lint",
            {
                "description": "Run lint checks.",
                "command": "ruff check .",
            },
        ),
    ],
)
```

Callable commands normalize common return values before they are sent back to
the harness. `None` becomes an empty string, Pydantic models become
dictionaries, exceptions become structured error dictionaries, and shell
commands return the sandbox result with `stdout`, `stderr`, and `exit_code`.

## Events

Pass `on_event` to `init` or `PyFlueAgent` to observe session activity:

```python
def handle_event(event):
    print(event.type, event.data)

agent = await init(on_event=handle_event)
```

Event types include:

| Event | When emitted |
| --- | --- |
| `agent_start` | A prompt or stream turn starts. |
| `text_delta` | Streaming text is produced. |
| `tool_start` / `tool_end` | A streamed backend tool call starts or finishes. |
| `turn_end` | A prompt or stream turn completes. |
| `command_start` / `command_end` | `shell` or built-in `bash` runs. |
| `task_start` / `task_end` | A child task starts or finishes. |
| `compaction_start` / `compaction_end` | Session history is compacted. |
| `abort_requested` / `aborted` | A running operation is cancelled. |
| `idle` | An operation leaves the session idle. |
| `error` | An operation raises an error. |

`session.stream()` still yields its own normalized stream events: `start`,
`delta`, `end`, and `error`.

## Python Backend

When a Python backend is configured, use `run_python`:

```python
result = await session.run_python(
    "sum(items)",
    inputs={"items": [1, 2, 3]},
)
```

## Filesystem Helpers

```python
content = await session.read_file("README.md")
await session.write_file("report.txt", "Summary")
metadata = await session.stat_file("report.txt")
exists = await session.exists("report.txt")
await session.mkdir("reports")
await session.rm("reports", recursive=True)
```

Writes, directory creation, and removal require `allow_write=True`.

Binary-safe helpers are available when a workflow needs exact bytes:

```python
data = await session.read_bytes("image.png")
await session.write_bytes("copy.png", data)
```

For parity with Flue's out-of-band filesystem surface, agents and sessions also
expose `fs`:

```python
await session.fs.write_file("report.txt", "Summary")
content = await session.fs.read_file("report.txt")
entries = await session.fs.readdir(".")

await agent.fs.writeFile("shared.txt", "available in the default session")
```

`fs` methods do not add messages to the conversation transcript. The Pythonic
snake_case methods have Flue-compatible camelCase aliases such as `readFile`,
`readFileBuffer`, and `writeFile`.

## Shell Helper

```python
result = await session.shell("pytest -q")
print(result["stdout"])
```

Shell execution requires `allow_shell=True`.

Run inside a subdirectory or add per-call environment variables:

```python
result = await session.shell(
    "pytest -q",
    cwd="packages/api",
    env={"PYTHONWARNINGS": "error"},
)
```

Grant secrets only for the command that needs them:

```python
await session.shell(
    "python -c 'import os; print(os.getenv(\"TOKEN\"))'",
    secrets=["TOKEN"],
)
```

Use `commands` for agent-wide command grants and per-call `commands` for one
operation:

```python
agent = await init(
    allow_shell=True,
    commands=["pytest"],
)

await session.shell("pytest -q")
await session.shell("ruff check .", commands=["ruff"])
```

## Compaction

PyFlue compacts long sessions automatically before turns when estimated history
exceeds the configured threshold. You can also compact explicitly:

```python
result = await session.compact()
```

Use `keep_recent` for message-count based compaction:

```python
result = await session.compact(keep_recent=6)
```

## Client

Use `PyFlueClient` to call a running PyFlue server from Python:

```python
from pyflue import PyFlueClient

async with PyFlueClient("http://127.0.0.1:2024") as client:
    result = await client.prompt("Summarize this repo", session_id="demo")
    print(result.text)
```

For Flue SDK-style code, use the factory alias:

```python
from pyflue import create_flue_client

async with create_flue_client(baseUrl="http://127.0.0.1:2024") as client:
    run = await client.agents.invoke("triage", "issue-123", {"mode": "webhook"})
```

`create_flue_client()` defaults agent invocation results to Flue SDK-style
shapes: sync calls return `{"result": ..., "runId": ...}` and webhook calls
return `{"runId": ...}`. `PyFlueClient` keeps raw PyFlue responses by default.
Pass `agentResponseFormat="raw"` to the factory, or `{"responseFormat": "raw"}`
to one `agents.invoke()` call, to keep the raw response.

Typed prompt results use the same Pydantic extraction behavior as local
sessions:

```python
typed = await client.prompt(
    "Return a triage result",
    session_id="demo",
    result=TriageResult,
)
```

The client also supports `health()`, `agents()`, file-based `agent(...)`
routes, and `stream(...)`. For Flue-style route invocation, use the agent and
run namespaces:

```python
result = await client.agents.invoke("triage", "issue-123", payload={"prompt": "Review"})
accepted = await client.agents.invoke("triage", "issue-123", mode="webhook")

async for event in client.agents.stream("triage", "issue-123", payload={"prompt": "Review"}):
    print(event.type, event.data)

# Flue SDK-style options dictionaries are also accepted.
result = await client.agents.invoke(
    "triage",
    "issue-123",
    {"mode": "sync", "payload": {"prompt": "Review"}},
)

async for event in client.agents.invoke(
    "triage",
    "issue-123",
    {"mode": "stream", "payload": {"prompt": "Review"}},
):
    print(event.type, event.data)

run = await client.runs.get(accepted["run_id"])
events = await client.runs.events(accepted["run_id"], types=["log", "run_end"])
```

Admin endpoints mounted at `/admin` are available through `client.admin`:

```python
agents = await client.admin.agents.list()
runs = await client.admin.runs.list(limit=20, status="errored")
next_page = await client.admin.runs.list(limit=20, cursor=runs["nextCursor"])
run = await client.admin.runs.get("run_...")
```

Treat `nextCursor` as an opaque token. Agent route responses also expose the
same run id in the `X-Flue-Run-Id` header for Flue SDK compatibility.

Run and event payloads include both Pythonic snake_case fields (`run_id`,
`event_index`, `started_at`) and Flue-style camelCase fields (`runId`,
`eventIndex`, `startedAt`) for compatibility.

For durable run/event history in a server process, use the SQLite run store:

```python
from pyflue import SQLiteRunRegistry, SQLiteRunStore

store = SQLiteRunStore(".pyflue/runs.sqlite3")
```

The default server store can also be selected with
`PYFLUE_RUN_STORE=sqlite` and `PYFLUE_RUN_STORE_PATH=.pyflue/runs.sqlite3`.

For deployment-wide run pointers, use the Flue-style run registry:

```python
registry = SQLiteRunRegistry(".pyflue/run-registry.sqlite3")
await registry.recordRunStart(
    run_id="run_...",
    agent_name="triage",
    instance_id="issue-123",
)
await registry.recordRunEnd(run_id="run_...", is_error=False)

page = await registry.listRuns(limit=20)
next_page = await registry.listRuns(cursor=page["nextCursor"])
```

The FastAPI apps expose OpenAPI documents at `/openapi.json` and
`/admin/openapi.json`, including schemas for run records, event lists, admin
list responses, and error envelopes.
