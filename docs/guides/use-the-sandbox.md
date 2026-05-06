# Use the Sandbox

The virtual sandbox gives agents controlled access to a workspace.

## Read Files

```python
content = await session.read_file("README.md")
```

Read access is enabled by default.

## Write Files

```python
agent = await init(allow_write=True)
session = await agent.session()
await session.write_file("report.txt", "Repository summary")
```

Without `allow_write=True`, writes raise `PermissionError`.

## Run Shell Commands

```python
agent = await init(allow_shell=True)
session = await agent.session()
result = await session.shell("python --version")
```

Without `allow_shell=True`, shell commands raise `PermissionError`.

## Enable Both

```python
agent = await init(
    allow_write=True,
    allow_shell=True,
)
```

## Keep Commands Narrow

For production use, prefer command allowlists:

```python
agent = await init(
    allow_shell=True,
    allowed_commands=["python"],
    commands=["pytest"],
)
```

`allowed_commands` sets the base sandbox policy. `commands` grants commands to
every PyFlue call made through that agent. Per-call `commands` grants are
available for one operation:

```python
await session.shell("ruff check .", commands=["ruff"])
```

Run a command from a subdirectory or add environment variables for one call:

```python
await session.shell(
    "pytest -q",
    cwd="packages/api",
    env={"PYTHONWARNINGS": "error"},
)
```

Compound shell syntax is blocked by default. This protects untrusted workflows
from pipes, redirects, command substitution, and chained shell commands. Enable
compound commands only for trusted workflows:

```python
agent = await init(
    allow_shell=True,
    allow_compound_commands=True,
)
```

## Grant Secrets Per Call

Secrets passed through `env` are not added to prompts and are not mounted into
the sandbox by default.

```python
agent = await init(
    env={"GITHUB_TOKEN": "..."},
    allow_shell=True,
    allowed_commands=["python"],
)

session = await agent.session("issue-123")
await session.shell(
    "python -c 'import os; print(os.getenv(\"GITHUB_TOKEN\"))'",
    secrets=["GITHUB_TOKEN"],
)
```

## Use a Remote Sandbox

Install the provider extra:

```bash
uv add "pyflue[daytona]"
```

```bash
pip install "pyflue[daytona]"
```

Then select the provider:

```python
agent = await init(
    model="openai:gpt-5.5",
    sandbox="daytona",
    env={"DAYTONA_API_KEY": "..."},
    allow_write=True,
    allow_shell=True,
)
```

Supported provider names:

| Provider | API key |
| --- | --- |
| `daytona` | `DAYTONA_API_KEY` |
| `e2b` | `E2B_API_KEY` |
| `modal` | Modal CLI or environment authentication |
| `runloop` | `RUNLOOP_API_KEY` |

Remote provider adapters keep the same session API:

```python
session = await agent.session("code")
await session.shell("git clone https://github.com/org/repo workspace/repo")
await session.write_file("workspace/repo/report.md", "# Report\n")
content = await session.read_file("workspace/repo/report.md")
```

The provider sandbox is responsible for isolation. PyFlue is responsible for
the common read, write, edit, grep, glob, and shell interface.
