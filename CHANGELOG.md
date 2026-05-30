# Changelog

## 0.2.0

This release adopts the TypeScript Flue v0.8 architecture: the **Agents vs
Workflows** split, plus observability and host-sandbox parity.

### New features

- **Composable agents.** `create_agent(initialize)` defines a deferred,
  addressable agent; `define_agent_profile()` defines reusable behaviour.
  `init_agent()` resolves a created agent; `init()` is unchanged and compatible.
- **Workflows.** Modules in `workflows/` (or `.pyflue/workflows/`) export
  `run(ctx)`; each invocation is a workflow run with a `workflow:<name>:<ulid>`
  id. `FlueContext` (formerly `PyFlueContext`, aliased) gains `ctx.id`, `ctx.req`,
  and `ctx.init(agent)`. `pyflue run <workflow>` runs one locally; `POST
  /workflows/{name}` supports accepted / `?wait=result` / SSE modes.
- **Persistent agent instances.** A module that default-exports a created agent
  is served at `POST /agents/{name}/{id}` with session continuity (no run id);
  `AgentInstanceManager` caches instances. New pluggable `SessionStore` /
  `InMemorySessionStore` / `SQLiteSessionStore`.
- **`dispatch()`.** Accepts JSON-serialisable input for asynchronous agent
  processing and returns a `DispatchReceipt`; `POST /agents/{name}/{id}/dispatch`.
- **Operation events.** Every session operation emits `operation_start` /
  `operation` with `operation_id` / `instance_id` correlation.
- **OpenTelemetry.** `pyflue.observability.create_opentelemetry_observer()`
  maps events to a workflow → operation → tool/task/compaction span tree
  (`pyflue[otel]` extra).
- **Host `local()` sandbox.** Real filesystem + subprocess shell with an opt-in
  env allowlist; `init(sandbox=...)` accepts a factory callable.
- **Subagent profiles.** `task(agent="name")` selects a declared profile
  (instructions/model/reasoning/tools); `profile_to_role()` / `role_to_profile()`
  bridge Markdown roles.
- **WebSocket surfaces.** Persistent agent (multi-prompt) and workflow (one run)
  WebSocket endpoints; client `agents.connect()` / `workflows.connect()` /
  `workflows.invoke()` / `workflows.stream()`.
- **`ToolDefinition`** is now the canonical name for `ToolDef` (aliased).
- Added a chat integration example under `examples/chat/`.

### Breaking changes

- **Runs are workflow-only.** Direct/dispatched agent prompts no longer create
  workflow runs or surface `X-Flue-Run-Id`; they correlate by instance and
  operation. File-based `default(context)` handlers remain workflow-like and
  keep their runs.

## 0.1.5

- Added Flue-style HTTP run/admin parity: SSE/webhook agent routes, run event APIs, admin OpenAPI schemas, opaque admin cursors, and `X-Flue-Run-Id` headers.
- Added Python client parity helpers: `create_flue_client()`, `createFlueClient`, Flue-style agent invoke options, and optional SDK-shaped invoke results.
- Added durable SQLite run/event history and a Flue-style run pointer registry with in-memory and SQLite implementations.
- Added Cloudflare and provider build target parity for workspace builds, including Cloudflare Containers artifacts.
- Added `pyflue.config.py` support with `define_config()`.
- Added Flue-style `session.fs` / `agent.fs` filesystem facades.
- Added Flue-style `ToolDef`, `define_tool()`, `create_tools()`, and `createTools`.
- Expanded docs and tests for the new parity surfaces.

## 0.1.4

- Added stable prompt response usage and selected-model metadata.
- Added typed prompt response wrappers with `.result`, `.usage`, `.model`, `.text`, `.metadata`, and backward-compatible attribute forwarding.
- Added `thinking_level` support on config, roles, and prompt/skill/task calls.
- Added image inputs for prompt, skill, and task calls on supported harnesses.
- Added `store_responses` provider setting for OpenAI Responses-compatible deployments.
- Improved `session.shell()` history to persist shell calls as user, assistant tool-call, and tool-result transcript entries.
- Exported MCP connection helpers from the top-level `pyflue` package.
- Added parity notes against the TypeScript Flue reference.

## 0.1.3

- Added DeepAgents backend support for sandbox-backed filesystem tools, task delegation, streaming tool events, provider settings, and scoped working directories.
- Added expanded sandbox filesystem APIs for metadata, existence checks, directory creation, removal, and binary-safe reads and writes.
- Added runtime context discovery from the active sandbox for `AGENTS.md`, `CLAUDE.md`, and local skills.
- Added directory-style skill support and relative skill-file lookup under `.agents/skills`.
- Added typed HTTP error envelopes and stricter webhook request validation.
- Added structured session history with compaction entries, task metadata, child task tracking, and recursive session cleanup.
- Added automatic token-based compaction before long prompt turns and one context-overflow recovery retry.
- Added MCP direct mode and search/execute mode with configurable server loading and tool search.
- Added agent-wide tools, agent-wide command grants, and `PyFlueCommand` for reusable shell or callable command tools.
- Added `define_command()` for concise reusable command tools with normalized callable and shell results.
- Added `session.abort()` with active operation tracking and cancellation events.
- Added max task depth limits and parent-to-child cancellation propagation for active task sessions.
- Added richer typed result extraction from delimited JSON, raw JSON, fenced JSON, and embedded JSON in free-form text.
- Added `PyFlueClient` for deployed server usage, including health, agent listing, prompt, typed prompt parsing, route calls, and SSE streaming.
- Added build plugins for Uvicorn, Docker, Lambda, and Cloud Run, plus expanded deployment target artifacts.
- Added Cloudflare Containers deployment artifacts for Workers-backed Python containers.
- Added `pyflue routes`, dev status endpoint, dashboard links, and server-side session abort endpoint.
- Added `pyflue invoke`, `.env` loading for `pyflue dev`, and clearer truncation messages for large prompt-tool outputs.
- Added release docs for MCP, configuration, built-in tools, structured commands, cancellation, client usage, deployment, and session behavior.
- Added model-free server/client smoke example under `examples/server_client/`.
- Updated package metadata, documentation links, README badges, and release version to `0.1.3`.

## 0.1.2

- Added release maintenance updates after `0.1.1`.

## 0.1.1

- Added sandbox provider adapters for Daytona, E2B, Modal, and Runloop.
- Added optional sandbox dependency extras.
- Added optional Monty Python backend for safe host-side Python execution.
- Added normalized streaming events, CLI stream output, and SSE endpoint.
- Added Markdown roles, route triggers, and file-based webhook metadata.
- Added secret grants, stricter command policy, and per-session virtual sandbox persistence.
- Added provider CLI deployment wrappers for supported targets.
- Added credential-gated live sandbox smoke tests.
- Added `pyflue add` connector guides for sandbox provider setup.
- Added remote sandbox documentation and provider tests.
- Added product-oriented example agents for issue triage, data analysis,
  coding, and support workflows.

## 0.1.0

- Initial PyFlue package scaffold.
- DeepAgents backend.
- Markdown skill loader.
- SQLite sessions.
- Virtual sandbox.
- Pydantic typed outputs.
- Typer CLI.
- MkDocs documentation site.
