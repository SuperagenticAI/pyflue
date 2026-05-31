# Parity with Flue

PyFlue is a Python port of the TypeScript [Flue](https://flueframework.com)
framework. This page records what PyFlue implements relative to the reference,
what it intentionally does not, and where the two differ by design. It is the
authoritative status reference; the [Feature Matrix](feature-matrix.md) lists
individual capabilities.

## What parity means here

PyFlue targets parity on the programming model and the public API that an
application author writes against: agents, workflows, sessions, tools, skills,
sandboxes, observability, and the client. It does not attempt to reproduce the
reference's JavaScript runtime internals or its platform specific deployment
machinery. Where a reference capability is specific to the Cloudflare Workers
runtime or to a third party JavaScript package, PyFlue provides a Python
equivalent only when one is meaningful.

## Implemented

The following layers are present and covered by tests.

- **Agents.** `create_agent(...)`, persistent addressable instances, and the
  HTTP, WebSocket, and `dispatch(...)` surfaces.
- **Chat.** Verified application webhooks can map platform threads to
  continuing agent sessions through `dispatch(...)`, with replies exposed as
  explicit scoped tools.
- **Workflows.** `run(ctx)` modules, workflow runs with a `run_id`, and local,
  HTTP, and WebSocket invocation.
- **Profiles and subagents.** `define_agent_profile(...)`, selection through
  `task(agent="name")`, and the `profile_to_role` / `role_to_profile` bridge.
- **Sessions.** Continuity across calls plus pluggable `SessionStore`,
  `InMemorySessionStore`, and `SQLiteSessionStore`.
- **Observability.** A correlated event stream (operation, generation, tool,
  task, and compaction events) and the `create_opentelemetry_observer()`
  adapter that maps it to OpenTelemetry spans.
- **Models and providers.** Provider-qualified model strings, reasoning effort
  hints, and provider endpoint overrides are available. PyFlue delegates
  catalog-level model routing to the selected Python harness backend.
- **Tools and skills.** `ToolDefinition` (aliased as `ToolDef`), Markdown
  skills, and `load_skill(path)` for importing a packaged skill in code.
- **Sandboxes.** The in memory virtual sandbox, the host bound `local()`
  factory, and the Daytona, E2B, Modal, and Runloop providers.
- **Client.** `agents`, `workflows`, `runs`, and `admin` namespaces, including
  `agents.connect(...)` and `workflows.connect(...)` over WebSocket.
- **Source layouts.** Discovery from `src/agents` and `src/workflows`, in
  addition to the legacy root and `.pyflue` or `.agents` locations.

## Intentionally not ported (Cloudflare specific)

The only reference layers PyFlue does not implement are tied to the Cloudflare
Workers runtime, which has no portable Python equivalent.

- **Durable execution.** The reference recovers interrupted workflow runs using
  Cloudflare Durable Object storage and a run registry. PyFlue records runs and
  emits a lifecycle, but it does not recover an interrupted run in a new process.
  On the current path, `dispatch(...)` uses process memory admission, so accepted
  work can be lost on restart. A durable queue and run store for Python targets
  is tracked as future work.
- **Cloudflare runtime integration.** The Cloudflare shell sandbox (project
  owned, installed in the reference with `flue add @cloudflare/shell`), the
  Workers and Containers build graph with Wrangler, and the `FlueRegistry`
  Durable Object are specific to Cloudflare. PyFlue ships its own build and
  deploy plugins for Python targets instead.

## Areas that look like gaps but are not

These were reviewed and are covered, either by PyFlue directly or because the
reference itself treats them as ecosystem rather than core.

- **Sandbox connectors.** The reference core ships no sandbox provider
  implementations. The connectors for boxd, exedev, daytona, e2b, modal, vercel,
  and the rest are spec documents plus separately installed packages. PyFlue
  ships more providers as core (virtual, local, Daytona, E2B, Modal, Runloop)
  and exposes a factory mechanism (`local()` and `create_sandbox`) for adding
  custom sandboxes.
- **Skill packaging.** `load_skill(path)` imports a skill in code. For
  deployment, PyFlue's file based targets copy the project, so skill files ship
  with the artifact. The reference's `SkillReference` bundling exists to feed a
  JavaScript build graph that PyFlue does not use.

## Design differences, not gaps

These are deliberate Python conventions rather than missing features.

- **Naming.** Public APIs use snake_case as the canonical form, with camelCase
  aliases provided for direct portability (for example `create_agent` and
  `createAgent`).
- **Web framework.** The server is built on FastAPI rather than Hono. The route
  and WebSocket surfaces match the reference paths.
- **Model resolution.** PyFlue passes model identifiers to its backends
  (DeepAgents and others) rather than resolving providers in the framework. The
  reference's internal model provider resolution does not apply. See
  [Models](../guides/models.md) for the Python behavior.
- **Generation telemetry granularity.** PyFlue emits one generation span per
  backend operation with model and token usage. The reference can emit a span
  per internal agent loop turn. Finer granularity in PyFlue depends on backend
  level instrumentation and is tracked as future work.

## Version tracking

PyFlue 0.2.0 tracks the reference at its v0.8.x line, including the agents and
workflows split, the `src/` source layout, and the OpenTelemetry integration.
The public runtime export surface is kept aligned with the reference. New
reference releases are reviewed against this page.
