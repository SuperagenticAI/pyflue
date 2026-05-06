# Sessions

Sessions give an agent persistent conversation history.

```python
session = await agent.session("issue-123")
```

If a session already exists, PyFlue resumes it. If it does not exist, PyFlue
creates it.

## Storage

Sessions are stored as SQLite databases under:

```text
.pyflue/sessions
```

Each session stores:

- user messages
- assistant messages
- tool messages
- structured history entries
- compaction entries and active context summaries

## Prompt History

PyFlue includes recent session history in future turns:

```python
await session.prompt("Inspect the failure")
await session.prompt("Now suggest the smallest fix")
```

The second prompt includes the recent conversation so the harness can continue
from the same context.

## Runtime Context

Before prompt, stream, and skill calls, PyFlue checks the active sandbox for:

- `AGENTS.md`
- `CLAUDE.md`
- `.agents/skills/**/*.md`
- `.agents/skills/<name>/SKILL.md`

When present, these sandbox files update the system prompt and available skills
for the call. This is useful for child tasks scoped with `cwd` and for
workspaces prepared by tools before the model turn.

## Automatic Compaction

PyFlue compacts long sessions automatically when the estimated history size
exceeds the configured context threshold:

```text
context_window_tokens - reserve_tokens
```

Older messages are summarized into a `[Context Summary]` entry while recent
messages are kept verbatim. `session.compact()` is still available for explicit
compaction, and `prompt()` performs one overflow recovery retry if the backend
raises a context-length error.

```python
agent = await init(
    compaction_context_window_tokens=128000,
    compaction_reserve_tokens=16384,
    compaction_keep_recent_tokens=20000,
)
```

Compaction emits `compaction_start` and `compaction_end` through `on_event`.

## Session Methods

| Method | Status | Purpose |
| --- | --- | --- |
| `prompt(text, result=None, role=None, model=None)` | Implemented | Run a direct prompt. |
| `skill(name, args=None, result=None, role=None, model=None)` | Implemented | Run a Markdown skill. |
| `task(prompt, result=None, role=None, model=None, cwd=None)` | Implemented | Run an isolated child task using the same sandbox. |
| `subagent(prompt, result=None, cwd=None)` | Implemented | Alias-style helper for child sessions. |
| `shell(command, timeout=120, cwd=None, env=None)` | Implemented | Run shell through sandbox policy. |
| `read_file(path)` | Implemented | Read a sandbox file. |
| `read_bytes(path)` | Implemented | Read a sandbox file as bytes. |
| `write_file(path, content)` | Implemented | Write a sandbox file when enabled. |
| `write_bytes(path, content)` | Implemented | Write bytes to a sandbox file when enabled. |
| `stat_file(path)` | Implemented | Return normalized file or directory metadata. |
| `exists(path)` | Implemented | Check whether a sandbox path exists. |
| `mkdir(path, recursive=True)` | Implemented | Create a sandbox directory when enabled. |
| `rm(path, recursive=False, force=False)` | Implemented | Remove a sandbox file or directory when enabled. |

## Built-In Prompt Tools

Every prompt receives built-in tools backed by the session sandbox:

| Tool | Purpose |
| --- | --- |
| `read` | Read a file or list a directory. |
| `write` | Write a file when write policy allows it. |
| `edit` | Replace exact text in a file. |
| `stat` | Return file or directory metadata. |
| `exists` | Check whether a path exists. |
| `mkdir` | Create a directory when write policy allows it. |
| `rm` | Remove a file or directory when write policy allows it. |
| `bash` | Run a shell command when shell policy allows it. Supports `cwd` and `env`. |
| `grep` | Search files by regular expression. |
| `glob` | Find files by glob pattern. |
| `task` | Delegate focused work to a child session, optionally with `cwd`. |

Custom per-call tools cannot reuse these names.

Agent-wide tools can be supplied when the agent is initialized:

```python
async def lookup_issue(number: int) -> str:
    return f"Issue #{number}"

agent = await init(tools=[lookup_issue])
```

Per-call tools are still supported and are added after agent-wide tools.

## Tasks

Tasks give you a child history while keeping the same sandbox:

```python
result = await session.task(
    "Inspect only the failing tests",
    role="coder",
    cwd="packages/api",
)
```

This is useful when the parent agent needs focused work without mixing every
intermediate step into the parent conversation.

When `cwd` is set, the child sees file paths relative to that directory. With
the virtual sandbox, PyFlue also loads `AGENTS.md`, skills, and roles from the
scoped directory, so package-specific instructions can guide the child task.

Task sessions store parent and child metadata. Calling
`agent.sessions.delete(...)` on a parent session removes the recorded child task
tree as well as the parent session state.

Nested tasks are limited by `max_task_depth`, which defaults to `8`. Calling
`session.abort()` on a parent session also requests cancellation for active
child task sessions.
