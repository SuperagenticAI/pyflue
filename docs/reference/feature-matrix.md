# Feature Matrix

This page shows what users can rely on today and what is planned next.

| Feature | Status | Notes |
| --- | --- | --- |
| Python package | Implemented | `pyflue` package with console script. |
| DeepAgents backend | Implemented | Default backend. |
| OpenAI Agents backend | Planned | Dependency pinned, runtime not implemented. |
| Google ADK backend | Planned | Dependency pinned, runtime not implemented. |
| Pydantic AI backend | Planned | Dependency pinned, runtime not implemented. |
| Markdown skills | Implemented | `.agents/skills/**/*.md`. |
| Project instructions | Implemented | `AGENTS.md` and `CLAUDE.md`. |
| Sessions | Implemented | SQLite-backed history. |
| Automatic compaction | Implemented | Threshold compaction before turns and one prompt overflow retry. |
| Roles | Implemented | Markdown roles from `.agents/roles/**/*.md`. |
| Task sessions | Implemented | `session.task()` creates isolated child history with shared sandbox. |
| Built-in agent tools | Implemented | `read`, `write`, `edit`, `bash`, `grep`, `glob`, and `task`. |
| Abort/cancel | Implemented | `session.abort()` cancels active prompt, stream, task, and shell operations. |
| Event callbacks | Implemented | `on_event` emits lifecycle, command, task, and compaction events. |
| Virtual sandbox | Implemented | Persistent per-session workspace with path boundary checks and policy-gated shell execution. |
| Shell policy | Implemented | Requires `allow_shell=True`; optional `allowed_commands` grants and compound-command blocking. |
| Secret grants | Implemented | Secrets are only mounted into sandbox env for calls that request them. |
| Write policy | Implemented | Requires `allow_write=True`. |
| DeepAgents file transfer | Implemented | Upload and download methods. |
| Typed outputs | Implemented | Pydantic `TypeAdapter` with retry repair loop and free-form JSON extraction. |
| Structured commands | Implemented | `PyFlueCommand` exposes reusable shell or callable commands as prompt tools. |
| Python client | Implemented | `PyFlueClient` supports health, route listing, prompt, stream, and agent calls. |
| Model override | Implemented | `session.prompt(..., model="...")` and `session.skill(..., model="...")`. |
| File-based agent routing | Implemented | `agents/*.py` and `.agents/*.py` expose `/agents/{name}/{id}` routes. |
| Route triggers | Implemented | Agent files can declare `triggers = {"webhook": True}`. |
| CLI init | Implemented | Scaffolds project files. |
| CLI run | Implemented | Runs a prompt with optional `--stream` event output. |
| CLI skill new | Implemented | Scaffolds a skill. |
| CLI build | Implemented | Generates Docker/FastAPI and selected CI/platform artifacts. |
| CLI dev | Implemented | Starts the FastAPI app with Uvicorn reload. |
| CLI routes | Implemented | Lists discovered file-based agent routes. |
| CLI deploy | Implemented | Generates target artifacts and can invoke known provider CLIs when installed. |
| Remote sandboxes | Implemented | Daytona, E2B, Modal, and Runloop adapters. |
| Monty Python backend | Implemented | Safe host-side Python execution via `pyflue[monty]`. |
| Monty state dump/load | Implemented | Serialize and restore Monty REPL state. |
| Monty dataclass registry | Implemented | `session.register_python_dataclass(...)`. |
| Monty resource limits | Implemented | `resource_limits={...}` forwards to Monty. |
| Streaming/events | Implemented | `session.stream(...)`, `pyflue run --stream`, and SSE endpoint. |
| MCP | Implemented | Direct mode and search/execute mode. |

## Sandbox Providers

| Provider | Status | Notes |
| --- | --- | --- |
| Virtual | Implemented | Persistent per-session workspace with path boundary checks. |
| Daytona | Implemented | Optional dependency: `pyflue[daytona]`. |
| E2B | Implemented | Optional dependency: `pyflue[e2b]`. |
| Modal | Implemented | Optional dependency: `pyflue[modal]`. |
| Runloop | Implemented | Optional dependency: `pyflue[runloop]`. |

Live provider smoke tests are available behind `PYFLUE_LIVE_SANDBOX_TESTS=1`
and skip automatically unless the matching provider credentials are present.

## Deployment Targets

| Target | Status | Notes |
| --- | --- | --- |
| Docker/FastAPI | Implemented | Python replacement for a JavaScript server target. |
| GitHub Actions | Implemented | Generates a manual workflow. |
| GitLab CI/CD | Implemented | Generates a manual pipeline job. |
| Railway | Implemented | Uses the Docker/FastAPI app. |
| Render | Implemented | Uses the Docker/FastAPI app. |
| Fly.io | Implemented | Uses the Docker/FastAPI app. |
| Cloudflare | Partial | Generates `wrangler.toml`; full Python container guide is still needed. |
| Vercel | Implemented | Generates `vercel.json` plus Python app artifacts. |
| Netlify | Implemented | Generates `netlify.toml` plus Python app artifacts. |
