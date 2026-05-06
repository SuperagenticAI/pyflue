# Configuration

PyFlue reads `pyflue.toml` by default.

```toml
[agent]
model = "openai:gpt-5.5"
harness = "deepagents"
sandbox = "virtual"
python_backend = "monty"
skills_dir = ".agents/skills"
roles_dir = ".agents/roles"
agents_dir = "agents"
state_dir = ".pyflue/sessions"
allowed_commands = ["git", "pytest"]
allow_compound_commands = false
max_task_depth = 8
typed_retries = 3

[sandbox]
workspace = "/workspace"
options = {}

[compaction]
enabled = true
context_window_tokens = 128000
reserve_tokens = 16384
keep_recent_tokens = 20000

[mcp]
mode = "direct"
search_limit = 10
search_backend = "bm25"

[mcp.servers.docs]
command = "python"
args = ["mcp_server.py"]

[providers.openai]
base_url = "https://gateway.example.com/openai"
api_key = "gateway-key"

[providers.openai.headers]
X-Team = "agents"
```

## Agent Settings

| Key | Default | Description |
| --- | --- | --- |
| `model` | `None` | Model identifier passed to the backend. |
| `harness` | `deepagents` | Harness backend name. |
| `sandbox` | `virtual` | Sandbox name. |
| `python_backend` | `None` | Optional Python execution backend. Use `monty` for safe host-side Python. |
| `skills_dir` | `.agents/skills` | Markdown skill directory. |
| `roles_dir` | `.agents/roles` | Markdown role directory. |
| `agents_dir` | `agents` | File-based agent route directory. |
| `state_dir` | `.pyflue/sessions` | Session database directory. |
| `allowed_commands` | `[]` | Optional shell command grant list. |
| `allow_compound_commands` | `false` | Allow shell operators such as `&&`, pipes, and redirects. Keep disabled for untrusted workflows. |
| `max_task_depth` | `8` | Maximum nested `session.task()` depth. Set to `0` to disable child tasks. |
| `typed_retries` | `3` | Structured output repair attempts. |

## Secret Grants

Values passed through `env` are treated as secrets. PyFlue keeps them out of
prompts and does not mount them into the virtual sandbox unless a call requests
them.

```python
agent = await init(env={"GITHUB_TOKEN": "..."}, allow_shell=True)
session = await agent.session("issue-123")

await session.shell(
    "python -c 'import os; print(os.getenv(\"GITHUB_TOKEN\"))'",
    secrets=["GITHUB_TOKEN"],
)
```

## Sandbox Settings

The `[sandbox]` table is passed to the selected sandbox provider:

```toml
[agent]
model = "openai:gpt-5.5"
sandbox = "daytona"

[sandbox]
workspace = "/workspace"

[sandbox.options]
# provider-specific creation options
```

Provider credentials are passed through `env` in Python code or through normal
environment variables in your process:

```python
agent = await init(
    sandbox="e2b",
    env={"E2B_API_KEY": "..."},
)
```

## Runtime Overrides

Values passed to `init` override config file values:

```python
agent = await init(
    config_path="pyflue.toml",
    model="openai:gpt-5.5-mini",
    harness="deepagents",
)
```

## Dependency Extras

PyFlue tracks the current supported package lines:

| Extra | Packages |
| --- | --- |
| default | `deepagents==0.5.6` through `>=0.5.6,<0.6.0` |
| `openai` | `openai-agents>=0.15.1,<0.16.0` |
| `google` | `google-adk>=1.32.0,<1.33.0` |
| `pydanticai` | `pydantic-ai>=1.89.1,<1.90.0` |
| `daytona` | `daytona-sdk>=0.22.0` |
| `e2b` | `e2b>=2.7.0` |
| `modal` | `modal>=1.3.0` |
| `runloop` | `runloop-api-client>=0.82.0` |
| `sandboxes` | Daytona, E2B, Modal, and Runloop extras |
| `monty` | `pydantic-monty>=0.0.17,<0.0.18` |

Install with extras:

```bash
pip install "pyflue[openai]"
pip install "pyflue[google]"
pip install "pyflue[pydanticai]"
pip install "pyflue[sandboxes]"
pip install "pyflue[monty]"
```

Equivalent `uv` commands:

```bash
uv add "pyflue[openai]"
uv add "pyflue[google]"
uv add "pyflue[pydanticai]"
uv add "pyflue[sandboxes]"
uv add "pyflue[monty]"
```

DeepAgents is the default harness backend. OpenAI Agents SDK, Google ADK, and
Pydantic AI are available as optional package extras for projects that want to
build custom backends against the same PyFlue API.

## Provider Settings

Configure per-provider settings for API gateways, LiteLLM-style proxies, or enterprise endpoints:

```python
agent = await init(
    providers={
        "anthropic": {
            "base_url": "https://api.anthropic.com",
            "headers": {"X-Custom-Auth": "my-token"},
            "api_key": "override-key"
        },
        "openai": {
            "base_url": "https://litellm.example.com/openai"
        }
    }
)
```

| Setting | Description |
|---------|-------------|
| `base_url` | Override the default API endpoint for the provider. Use for API gateways or LiteLLM proxies. |
| `headers` | Additional headers sent with requests. Useful for authentication tokens or custom metadata. |
| `api_key` | Override the API key for this provider. Useful when the gateway requires a specific key format. |

Equivalent TOML:

```toml
[providers.anthropic]
base_url = "https://api.anthropic.com"
api_key = "override-key"

[providers.anthropic.headers]
X-Custom-Auth = "my-token"
```

## Compaction Settings

Configure session history compaction to manage context tokens:

```python
agent = await init(
    compaction_enabled=True,
    compaction_context_window_tokens=128000,
    compaction_reserve_tokens=16384,  # Keep 16K tokens free
    compaction_keep_recent_tokens=20000  # Preserve last 20K tokens verbatim
)
```

| Setting | Default | Description |
|---------|---------|-------------|
| `compaction_enabled` | `true` | Enable automatic compaction when session grows large. |
| `compaction_context_window_tokens` | `128000` | Estimated model context window used for automatic compaction thresholds. |
| `compaction_reserve_tokens` | `16384` | Number of tokens to keep free in context window. |
| `compaction_keep_recent_tokens` | `20000` | Recent tokens to preserve verbatim (not summarize). |

Equivalent TOML:

```toml
[compaction]
enabled = true
context_window_tokens = 128000
reserve_tokens = 16384
keep_recent_tokens = 20000
```

PyFlue uses token estimation (~4 characters per token) to summarize older conversation history while keeping recent messages intact. It compacts automatically before a turn when estimated history exceeds `context_window_tokens - reserve_tokens`, and `prompt()` retries once after context overflow errors by compacting with overflow recovery.

## MCP Settings

Configure MCP servers in `pyflue.toml` or through `init(...)`.

```toml
[mcp]
mode = "search_execute"
search_limit = 10
search_backend = "bm25"

[mcp.servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]

[mcp.servers.remote]
url = "https://example.com/mcp"
transport = "streamable-http"

[mcp.servers.remote.headers]
Authorization = "Bearer token"
```

| Setting | Default | Description |
| --- | --- | --- |
| `mode` | `direct` | Expose all MCP tools directly, or use `search_execute` to expose only search and execute tools. |
| `search_limit` | `10` | Number of tools returned by `mcp_search`. |
| `search_backend` | `bm25` | Search implementation. Use `semantic` when sentence-transformer dependencies are installed. |
