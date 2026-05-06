# Sandbox

PyFlue ships with a zero-config virtual sandbox for local development and
provider adapters for remote Linux sandboxes.

The sandbox provides a safe workspace boundary and policy-controlled tools.

## Default Policy

By default:

- file reads are allowed
- file writes are disabled
- shell execution is disabled

Enable writes and shell explicitly:

```python
agent = await init(
    allow_write=True,
    allow_shell=True,
)
```

Or from the CLI:

```bash
pyflue run \
  --prompt "Inspect and write report.txt" \
  --allow-write \
  --allow-shell
```

## Sandbox Tools

| Tool | Status | Description |
| --- | --- | --- |
| `stat` | Implemented | Return normalized metadata for a file or directory. |
| `exists` | Implemented | Check whether a sandbox path exists. |
| `read_file` | Implemented | Read a file or list a directory. |
| `read_bytes` | Implemented | Read a file as bytes. |
| `write_file` | Implemented | Write text to a file when writes are enabled. |
| `write_bytes` | Implemented | Write bytes to a file when writes are enabled. |
| `mkdir` | Implemented | Create a directory when writes are enabled. |
| `rm` | Implemented | Remove a file or directory when writes are enabled. |
| `edit_file` | Implemented | Replace exact text in a file. |
| `grep` | Implemented | Search files with a regex. |
| `glob` | Implemented | Find files by glob pattern. |
| `shell` | Implemented | Run a shell command when shell is enabled. |

## Providers

| Provider | Status | Install |
| --- | --- | --- |
| `virtual` | Implemented | Included by default |
| `local` | Implemented | Alias for `virtual` with the project root |
| `daytona` | Implemented | `uv add "pyflue[daytona]"` or `pip install "pyflue[daytona]"` |
| `e2b` | Implemented | `uv add "pyflue[e2b]"` or `pip install "pyflue[e2b]"` |
| `modal` | Implemented | `uv add "pyflue[modal]"` or `pip install "pyflue[modal]"` |
| `runloop` | Implemented | `uv add "pyflue[runloop]"` or `pip install "pyflue[runloop]"` |

Use a remote provider by selecting the sandbox:

```python
agent = await init(
    model="openai:gpt-5.5",
    sandbox="daytona",
    env={"DAYTONA_API_KEY": "..."},
    allow_write=True,
    allow_shell=True,
)
```

## Add A Sandbox Connector

PyFlue includes a connector guide command for coding agents. It follows the
same workflow as modern component CLIs: the command prints instructions, and
your coding agent writes the connector or configuration into your project.

List available guides:

```bash
pyflue add
```

Print a guide for a built-in provider:

```bash
pyflue add daytona --print
```

Start from any provider documentation URL:

```bash
pyflue add https://provider.example/docs --category sandbox --print | codex
```

Use this when PyFlue does not already have a first-class provider adapter or
when your project needs provider-specific behavior.

Remote sandboxes expose the same high-level PyFlue tools. File operations are
implemented through shell commands inside the provider sandbox, so the remote
environment must include standard Linux tools such as `bash`, `python`, `cat`,
`grep`, `glob`, and `base64`.

## Metadata And File Management

Session helpers provide common filesystem operations:

```python
metadata = await session.stat_file("report.txt")
exists = await session.exists("report.txt")
await session.mkdir("reports/archive")
await session.rm("reports/archive", recursive=True)
```

`stat_file` returns a dictionary with `path`, `is_dir`, `is_file`, `size`, and
`mtime`.

Use binary helpers for files that should not be decoded as UTF-8:

```python
data = await session.read_bytes("asset.png")
await session.write_bytes("asset-copy.png", data)
```

## Path Boundary

All paths are resolved under the sandbox root. Attempts to escape the root are
rejected.

```python
await session.read_file("../secrets.txt")
```

This raises an error because the path escapes the sandbox.

## Command Allowlist

`SandboxPolicy` supports command allowlists:

```python
from pyflue.sandbox import SandboxPolicy, VirtualSandbox

sandbox = VirtualSandbox(
    policy=SandboxPolicy(
        allow_shell=True,
        allowed_commands=("python", "git"),
    )
)
```

Compound shell syntax is blocked by default. This includes operators such as
`&&`, pipes, redirects, and command substitution. Keep this default for
untrusted workflows.

```toml
[agent]
allow_compound_commands = false
```

Enable compound commands only for trusted local or remote sandboxes:

```python
agent = await init(
    allow_shell=True,
    allow_compound_commands=True,
)
```

## Secret Grants

Secrets passed through `env` are not mounted into the virtual sandbox by
default. Grant only the secrets needed for a specific shell or prompt call:

```python
await session.shell(
    "python -c 'import os; print(os.getenv(\"GITHUB_TOKEN\"))'",
    secrets=["GITHUB_TOKEN"],
)
```

## Persistent Session Workspaces

The virtual sandbox persists per session id under:

```text
.pyflue/sandboxes/{session_id}
```

Opening the same session id reuses the same workspace. Child tasks share the
parent task workspace while keeping separate conversation history.

## DeepAgents Adapter

When using the DeepAgents backend, PyFlue adapts the configured sandbox to the
DeepAgents backend protocol. The adapter supports:

- list
- read
- write
- edit
- grep
- glob
- execute
- upload files
- download files

This lets DeepAgents skills and memory load files from the PyFlue sandbox,
whether it is local or remote.
