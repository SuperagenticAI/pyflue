"""Agent-readable connector guides for PyFlue."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass(frozen=True)
class ConnectorGuide:
    """A named connector guide entry."""

    name: str
    category: str
    website: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    built_in: bool = False


CONNECTORS: tuple[ConnectorGuide, ...] = (
    ConnectorGuide("daytona", "sandbox", "https://daytona.io", built_in=True),
    ConnectorGuide("e2b", "sandbox", "https://e2b.dev", built_in=True),
    ConnectorGuide("modal", "sandbox", "https://modal.com", built_in=True),
    ConnectorGuide("runloop", "sandbox", "https://runloop.ai", built_in=True),
    ConnectorGuide(
        "vercel",
        "sandbox",
        "https://vercel.com/sandbox",
        aliases=("@vercel/sandbox",),
    ),
)


def resolve_connector(name: str) -> ConnectorGuide | None:
    """Resolve a connector by name or alias."""
    exact = next((item for item in CONNECTORS if item.name == name or name in item.aliases), None)
    if exact is not None:
        return exact
    lower = name.lower()
    return next(
        (
            item
            for item in CONNECTORS
            if item.name.lower() == lower or any(alias.lower() == lower for alias in item.aliases)
        ),
        None,
    )


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def render_connector_listing() -> str:
    """Render the `pyflue add` listing."""
    rows = ["pyflue add <name>\n", "Available connectors:"]
    for item in CONNECTORS:
        command = f"pyflue add {item.name}"
        rows.append(f"  {command:<24} {item.category:<10} {item.website}")
    rows.extend(
        [
            "",
            "Build a sandbox connector from provider docs:",
            "  pyflue add https://provider.example/docs --category sandbox --print | codex",
        ]
    )
    return "\n".join(rows)


def render_human_instructions(name: str, *, category: str = "sandbox") -> str:
    """Render copyable instructions for a human terminal."""
    cmd = f"pyflue add {name} --category {category}"
    return "\n".join(
        [
            cmd,
            "",
            "To install this connector, pipe it to your coding agent:",
            "",
            f"  {cmd} --print | claude",
            f"  {cmd} --print | opencode",
            f"  {cmd} --print | codex",
            f"  {cmd} --print | cursor-agent",
            "",
            "Or paste this prompt into any agent:",
            "",
            f'  Run "{cmd} --print" and follow the instructions.',
        ]
    )


def render_add_prompt(name: str, *, category: str = "sandbox") -> str:
    """Render an agent-readable connector guide."""
    if category != "sandbox":
        raise ValueError(f"Unknown connector category: {category}")
    if is_url(name):
        return render_generic_sandbox_prompt(name)
    connector = resolve_connector(name)
    if connector is None:
        known = ", ".join(item.name for item in CONNECTORS)
        raise KeyError(f"Unknown connector: {name}. Known connectors: {known}")
    if connector.built_in:
        return render_builtin_sandbox_prompt(connector)
    return render_generic_sandbox_prompt(connector.website, name=connector.name)


def render_builtin_sandbox_prompt(connector: ConnectorGuide) -> str:
    """Render instructions for a sandbox that PyFlue already supports."""
    extra = connector.name if connector.name in {"daytona", "e2b", "modal", "runloop"} else "sandboxes"
    env_key = {
        "daytona": "DAYTONA_API_KEY",
        "e2b": "E2B_API_KEY",
        "modal": "Modal CLI or environment authentication",
        "runloop": "RUNLOOP_API_KEY",
    }.get(connector.name, "provider credentials")
    return f"""# Add a PyFlue Sandbox Connector: {connector.name}

You are an AI coding agent configuring the {connector.name} sandbox provider
for a PyFlue project.

PyFlue already includes this provider. Do not write a custom connector unless
the user's project needs provider-specific behavior that the built-in adapter
does not cover.

## Install

Use the package manager already used by the project.

```bash
uv add "pyflue[{extra}]"
```

```bash
pip install "pyflue[{extra}]"
```

## Configure

Update `pyflue.toml`:

```toml
[agent]
sandbox = "{connector.name}"

[sandbox]
workspace = "/workspace"
```

## Authentication

The provider needs `{env_key}`. Never invent credentials. Use the user's
existing `.env`, secret manager, CI variables, or deployment settings.

## Usage

```python
from pyflue import init


agent = await init(
    model="openai:gpt-5.5",
    sandbox="{connector.name}",
    env={{"{env_key if connector.name != "modal" else "MODAL_TOKEN_ID"}": "..."}},
    allow_write=True,
    allow_shell=True,
)
session = await agent.session("code")
result = await session.shell("python --version")
```

## Verify

1. Run `pyflue run --allow-shell --prompt "Check the sandbox runtime"`.
2. If credentials are available, run the live smoke test with
   `PYFLUE_LIVE_SANDBOX_TESTS=1`.
3. Tell the user which env vars they still need to set.
"""


def render_generic_sandbox_prompt(url: str, *, name: str | None = None) -> str:
    """Render instructions for a custom sandbox connector from arbitrary docs."""
    slug = name or _slug_from_url(url)
    return f"""# Build a PyFlue Sandbox Connector: {slug}

You are an AI coding agent adding a custom sandbox provider to a PyFlue
project. Use the provider documentation below as your starting point:

{url}

## Goal

Create a small Python connector that adapts the provider's sandbox API to
PyFlue's sandbox interface. The connector should let a PyFlue session list
files, read files, write files, edit files, grep, glob, and run shell
commands through the provider sandbox.

## Where To Write The File

Use the project layout:

- If the project has `.pyflue/connectors/`, write
  `.pyflue/connectors/{slug}.py`.
- Otherwise write `connectors/{slug}.py`.

Create missing parent directories. Do not modify unrelated files.

## Interface To Implement

The connector class must provide these methods:

```python
provider: str
policy: SandboxPolicy
id: str
list_files(path: str = ".") -> list[SandboxFileInfo]
stat(path: str) -> SandboxFileInfo
exists(path: str) -> bool
read_file(path: str, *, offset: int = 1, limit: int | None = None) -> str
read_bytes(path: str) -> bytes
write_file(path: str, content: str) -> str
write_bytes(path: str, content: bytes) -> str
mkdir(path: str, *, recursive: bool = True) -> str
rm(path: str, *, recursive: bool = False, force: bool = False) -> str
edit_file(path: str, old: str, new: str, *, replace_all: bool = False) -> str
grep(pattern: str, *, path: str = ".", include: str | None = None) -> str
glob(pattern: str) -> str
shell(command: str, *, timeout: int | None = 120) -> dict[str, object]
```

Import public PyFlue types only:

```python
from pyflue.sandboxes.base import SandboxFileInfo, SandboxPolicy, require_shell, require_write
```

## Security Rules

- Never invent API keys or tokens.
- Keep credentials in environment variables or the user's secret manager.
- Respect `SandboxPolicy`.
- Call `require_write(policy)` before writes or edits.
- Call `require_shell(policy, command)` before command execution.
- Keep paths inside the provider workspace.
- Return normalized shell results with `stdout`, `stderr`, and `exit_code`.

## Wiring

After writing the connector, show the user how to instantiate it:

```python
from connectors.{slug} import { _class_name(slug) }
from pyflue import init


agent = await init(model="openai:gpt-5.5", allow_write=True, allow_shell=True)
session = await agent.session("code")
```

If the project wants this provider available through `init(sandbox="{slug}")`,
add a small registration helper in project code. Do not patch PyFlue itself.

## Verify

1. Run the project's formatter and tests.
2. Run a smoke test that writes a file, reads it back, and executes
   `python --version` or the provider's equivalent.
3. Tell the user which package extras, provider SDKs, and environment
   variables are required.
"""


def _slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.replace("www.", "").split(":")[0]
    root = host.split(".")[0] if host else "custom"
    return "".join(char if char.isalnum() else "_" for char in root).strip("_") or "custom"


def _class_name(slug: str) -> str:
    parts = [part for part in slug.replace("-", "_").split("_") if part]
    return "".join(part.capitalize() for part in parts) + "Sandbox"
