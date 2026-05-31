# PyFlue → Flue Parity Roadmap

> Sequenced, file-by-file plan to bring `pyflue` (0.1.5) up to the TypeScript
> Flue reference (`reference/flue`, v0.8.1). Implement top-to-bottom; later
> items depend on earlier ones. Each item lists the reference source, the
> proposed Python API, the pyflue files touched, design notes/risks, and tests.

## Context

- **pyflue 0.1.5** faithfully mirrors Flue at **~v0.6.x**: everything is an
  "agent" with a run lifecycle, Markdown **roles**, `ToolDef`, `/runs/<id>`
  APIs, compaction tuning, admin OpenAPI, SDK client with `agents`/`runs`/`admin`.
- The reference shipped a **v0.8.0 overhaul** (Agents vs Workflows), **v0.7.0**
  (Cloudflare shell sandbox), and **v0.8.1** (OpenTelemetry). pyflue is missing
  that architecture, not the underlying primitives.
- Crucial realisation from the audit: pyflue's `PyFlueContext` in
  `routing.py` already has `.init(...)`, `.log.{info,warn,error}`, `payload`,
  `env`, and a `run_start`/`run_end` lifecycle. **That is structurally the
  reference _workflow_ model.** So workflows are largely a *reframe*; the new
  work is the composable `create_agent()` object and persistent agent instances.

## Conventions (decided up front)

- **Naming:** snake_case canonical + camelCase alias, matching pyflue's existing
  pattern (`create_tools`/`createTools`, `create_flue_client`/`createFlueClient`).
  So: `create_agent`/`createAgent`, `define_agent_profile`/`defineAgentProfile`.
- **Config fields:** snake_case Python (`thinking_level`, `reserve_tokens`,
  `keep_recent_tokens`) — not the reference's `thinkingLevel`.
- **Data shapes:** frozen `@dataclass` (consistent with `CompactionConfig`,
  `ProviderSettings`, `ToolDef`).
- **Back-compat:** every existing public API keeps working. `init(**kwargs)`,
  `Role`, `ToolDef`, file-based `default(context)` handlers all stay, with
  deprecation aliases where renamed. No breaking changes before a 0.2.0 line.
- **Validation:** mirror `agent-definition.ts` assertions (unknown-field
  rejection, unique names, no circular subagents, valid thinking levels).

## Sequencing & dependency graph

```
Phase 1 (architecture — do in order)
  1. create_agent() + profiles core            ← keystone
  2. workflows/ + run(ctx) + FlueContext.init(agent)   (depends on 1)
  3. persistent agent instances + sessions over HTTP   (depends on 1)
  4. runs = workflows only (re-scope)                  (depends on 2,3)
  5. dispatch()                                        (depends on 3)

Phase 2 (high-value, mostly parallel)
  6. OpenTelemetry adapter (+ event-model enrichment)  (better after 4)
  7. local() host sandbox                              (independent)
  8. define_agent_profile() polish + Role→profile migration  (depends on 1)

Phase 3 (surfaces & ecosystem)
  9. WebSocket (agents+workflows) + client.connect     (depends on 3,2)
 10. Chat example (webhook → dispatch → reply tool)    (depends on 5)
 11. ToolDef→ToolDefinition alias + docs rewrite       (depends on most)
```

---

## Phase 1 — Adopt the Agents/Workflows architecture

### 1. `create_agent()` + agent profiles (keystone)

**Reference:** `packages/runtime/src/agent-definition.ts` (`createAgent`,
`defineAgentProfile`, `resolveAgentProfile`, `extendAgentProfile`),
`types.ts` (`AgentCreateContext`, `AgentProfile`, `AgentRuntimeConfig`,
`CreatedAgent`).

**Goal:** a composable, deferred agent *spec* (distinct from today's eager
`init()` which builds a live agent). A created agent is a frozen object holding
an `initialize(ctx)` factory; both workflows and the server consume it.

**Proposed Python API** (new file `pyflue/agents.py`):
```python
@dataclass(frozen=True)
class AgentProfile:
    name: str | None = None
    description: str | None = None
    model: str | None | Literal[False] = None
    instructions: str | None = None
    thinking_level: ThinkingLevel | None = None
    skills: tuple[Skill, ...] | None = None
    tools: tuple[ToolDef, ...] | None = None
    subagents: tuple[AgentProfile, ...] | None = None
    compaction: CompactionConfig | Literal[False] | None = None

@dataclass(frozen=True)
class AgentRuntimeConfig(AgentProfile):       # profile fields + runtime concerns
    profile: AgentProfile | None = None
    cwd: str | None = None
    sandbox: Any | None = None                # str | SandboxFactory | local()
    persist: SessionStore | None = None       # SessionStore (item 3)

@dataclass(frozen=True)
class AgentCreateContext:
    id: str
    env: dict[str, str]
    payload: Any | None = None

@dataclass(frozen=True)
class CreatedAgent:
    __pyflue_created_agent__: bool = True
    initialize: Callable[[AgentCreateContext], AgentRuntimeConfig | Awaitable[...]]

def create_agent(initialize) -> CreatedAgent: ...
def define_agent_profile(profile: AgentProfile) -> AgentProfile: ...   # validates
def resolve_agent_profile(cfg: AgentRuntimeConfig) -> AgentProfile: ...  # merge profile+overrides
```

**Files:**
- **New** `pyflue/agents.py` — the dataclasses, `create_agent`,
  `define_agent_profile`, `resolve_agent_profile`, `extend_agent_profile`,
  validation helpers (port `assertAgentProfile` rules).
- `pyflue/core.py` — add `init_agent(created_agent, *, name="default", tools,
  skills, subagents, env, payload, id)` that resolves a `CreatedAgent` →
  existing `PyFlueAgent`. Refactor `init(**kwargs)` to build an implicit
  `CreatedAgent` and call `init_agent` (keeps `init()` 100% compatible).
- `pyflue/__init__.py` — export `create_agent`, `createAgent`,
  `define_agent_profile`, `defineAgentProfile`, `AgentProfile`,
  `AgentRuntimeConfig`, `AgentCreateContext`, `CreatedAgent`.
- `pyflue/types.py` — `ThinkingLevel` already exists; add a `Skill` re-export.

**Design notes / risks:**
- `model: False` means "no usable default" (subagent must get a task-level
  model) — mirror the reference precedence exactly.
- `init()` becomes a thin shim → make sure compaction/sandbox/mcp kwargs still
  flow through unchanged. This is the riskiest refactor; cover with the existing
  `init()` tests before refactoring.

**Tests:** `tests/test_agents.py` — validation (unknown field, dup names,
circular subagents, bad thinking level), profile merge precedence,
`create_agent` + `init_agent` round-trip, `init()` back-compat.

---

### 2. `workflows/` + `run(ctx)` + `FlueContext.init(agent)`

**Reference:** `guide/workflows.md`, `runtime/flue-app.ts`, `handle-run-routes.ts`.

**Goal:** first-class finite executions. A module in `workflows/` (or
`.pyflue/workflows/`) exporting `run(ctx)`; filename = workflow name; each
invocation is a workflow run with a `run_id`.

**What already exists:** `PyFlueContext` (init/log/payload/env) and
`invoke_route` run lifecycle in `routing.py`. This is ~70% of a workflow.

**Proposed Python API:**
```python
# workflows/summarize.py
from pyflue import create_agent, FlueContext

agent = create_agent(lambda ctx: {"model": "anthropic/claude-haiku-4-5"})

async def run(ctx: FlueContext) -> dict:
    ctx.log.info("started", audience=ctx.payload.get("audience"))
    harness = await ctx.init(agent)            # accepts a CreatedAgent now
    session = await harness.session()
    res = await session.prompt(f"Summarize: {ctx.payload['text']}", result=Summary)
    return res.data
```

**Files:**
- **New** `pyflue/workflows.py` — `discover_workflows(root)` (scan
  `workflows/`, `.pyflue/workflows/`), `WorkflowDef`, `invoke_workflow(...)`
  (rename/generalise `routing.invoke_route`'s run-lifecycle wrapper).
- `pyflue/routing.py` — rename `PyFlueContext` → `FlueContext` (keep
  `PyFlueContext` alias); extend `FlueContext.init` to accept either kwargs
  (today) **or** a `CreatedAgent` (call `init_agent`); add `ctx.id` (= run_id),
  `ctx.req` (Request) and `ctx.env` typing.
- `pyflue/cli.py` — `pyflue run <workflow> --payload …` now resolves a
  **workflow** (today `run` runs a prompt → keep that as `pyflue prompt` alias,
  add the workflow path). `flue logs <run_id>` already matches.
- `pyflue/server.py` — add `POST /workflows/{name}` with the 3 observation
  modes: `202 {status:'accepted', run_id}` (default), `?wait=result`,
  `Accept: text/event-stream` (reuse existing `/runs/{id}/stream`).
- `pyflue/__init__.py` — export `FlueContext`, `discover_workflows`.

**Design notes:** generate workflow run ids as `workflow:<name>:<ulid>`
(reference shape). Keep `ctx.id == run_id` for workflows. Validate payloads at
the route boundary, not in `run()`.

**Tests:** `tests/test_workflows.py` — discovery, `run()` lifecycle events,
`?wait=result` vs `202` vs stream, `ctx.init(created_agent)`.

---

### 3. Persistent agent instances + sessions over HTTP

**Reference:** `guide/building-agents.md`, `handle-agent.ts`. Agents are
addressable (`POST /agents/<name>/<id>`), keep sessions across calls, correlate
by `instance_id`/`operation_id` (NOT runs).

**What exists today:** `/agents/{name}/{id}` maps to a file `default(context)`
handler wrapped in a *run*. We re-point it to a **persistent created-agent
instance** with session continuity.

**Proposed model:**
- An `agents/<name>.py` module **default-exports a `CreatedAgent`** (via
  `create_agent`) and may export `route`/`websocket` middleware.
- Server resolves `(name, id)` → an agent instance keyed by `instance_id=id`,
  using a process-level instance registry; `session` param selects the thread;
  history persists via the agent's `persist` `SessionStore` (item below) or an
  in-memory default.

**Files:**
- `pyflue/routing.py` — `discover_agents()` distinguishing **CreatedAgent**
  default-exports (persistent agents) from **`run`/`default(context)`**
  (workflows). Add an instance/session manager.
- **New** `pyflue/session_store.py` — `SessionStore` protocol +
  `InMemorySessionStore` + `SQLiteSessionStore` (reuse `session_history.py`).
  Mirrors reference `SessionStore`/`SessionData` + Data-Persistence-API doc.
- `pyflue/server.py` — `POST /agents/{name}/{id}` → instance prompt (sync +
  SSE), **no run created**; respond `{result:{text,usage,model}}`. Add
  `route`/auth middleware hook (caller-selected `id` authorization).
- `pyflue/core.py` — `PyFlueAgent`/`PyFlueSession` gain
  `sessions.get/create/delete`, `session.delete()` (reference lifecycle table).

**Design notes / risks:** this is where runs and agents diverge — see item 4.
Instance identity + session persistence is the core durability story. Keep
Node-parity caveat: in-memory by default, durable only with a `persist` store.

**Tests:** `tests/test_agent_instances.py` — session continuity across two
prompts to same `id`, `sessions.create/get/delete` semantics, no run emitted.

---

### 4. Re-scope runs: **workflows only**

**Reference:** `workflows.md` ("Only workflows have runs"),
`observability.md` correlation table.

**Goal:** stop wrapping agent prompts in runs. Workflow invocations →
`run_start`/`run_end` + `run_id`. Agent prompts/dispatch → `operation_start`/
`operation`/`idle` with `instance_id`/`operation_id`/`dispatch_id`.

**Files:**
- `pyflue/routing.py` — move run lifecycle out of agent invocation into
  `invoke_workflow` (item 2). Agent invocation emits operation events instead.
- `pyflue/runs.py` — `FlueRun`/registry now keyed to workflows; add
  `workflow_name`, `owner.instance_id == run_id`. Keep stores/cursors as-is.
- `pyflue/core.py` — emit `operation_start`/`operation`/`idle` around
  `prompt`/`skill`/`task`/`shell`/`compact` with `operation_id` + `instance_id`
  (today it emits `agent_start`/`idle` only — see item 6 for the full taxonomy).
- `pyflue/server.py` — `/runs/*` describes workflow runs only; agent routes
  keep `X-Flue-Run-Id` only when invoked **inside** a workflow.

**Design notes / risks:** **behavioural change** for anyone reading `run_id`
off an agent prompt response. Gate behind 0.2.0; document in CHANGELOG +
migration note. This is the most semantically invasive item — do it after 2 & 3
land so both sides exist.

**Tests:** update `tests/` that assert agent prompts produce runs; add tests
that agent prompts produce operations (not runs) and workflows produce runs.

---

### 5. `dispatch()` — async agent input

**Reference:** `runtime/dispatch.ts`, `dispatch-queue.ts`,
`building-agents.md` (dispatch section).

**Proposed Python API** (new file `pyflue/dispatch.py`):
```python
@dataclass(frozen=True)
class DispatchReceipt:
    dispatch_id: str
    accepted_at: str

async def dispatch(agent: CreatedAgent | str, *, id: str, session: str | None = None,
                   input: Any) -> DispatchReceipt: ...
```
- Validate JSON-serialisable `input` (port `assertJsonLike`/`cloneJsonSerializable`).
- Enqueue onto a process-memory `DispatchQueue`; a worker drains it into the
  target agent instance/session as a `prompt` operation; emits
  `operation_start{dispatch_id,…}` → `operation` → `idle`.
- Returns receipt immediately (does **not** wait for model work, no run).

**Files:**
- **New** `pyflue/dispatch.py` — `dispatch()`, `DispatchQueue`, `DispatchReceipt`.
- `pyflue/server.py` — optional `POST /agents/{name}/{id}/dispatch` ingress.
- `pyflue/__init__.py` — export `dispatch`, `DispatchReceipt`.

**Design notes:** Node-parity caveat — process-memory admission is lossy on
restart; document it. Durable delivery is out of scope (see "Deferred").

**Tests:** `tests/test_dispatch.py` — receipt shape, JSON validation rejects
functions/circular, dispatched input lands in the right session, unknown agent
raises.

---

## Phase 2 — High-value standalone wins

### 6. OpenTelemetry adapter (+ event-model enrichment)

**Reference:** `packages/opentelemetry/src/index.ts` (287 lines, pure
`FlueEvent → span` mapper), `guide/observability.md`.

**Gap:** pyflue already has `observe()/unobserve()` + an event stream, but the
taxonomy is **coarser** than the reference. Today: `agent_start`, `turn_end`,
`text_delta`, `aborted`, `error`, `idle`, `task_end`, `command_start/end`,
`compaction`. Reference uses span-friendly start/end **pairs with stable ids**:
`run_start/run_end`, `operation_start/operation`, `turn_request/turn`,
`tool_start/tool_call`, `task_start/task`, `compaction_start/compaction`, `log`.

**Two-stage plan:**
- **6a — enrich events** (`pyflue/core.py`, `pyflue/types.py`): emit paired
  `operation_start/operation` (item 4 adds these), `turn_request/turn` (with
  `turn_id`, model, usage/cost), `tool_start/tool_call` (with `tool_call_id`),
  `task_start/task`, `compaction_start/compaction`, and `log`. Add a typed
  `FlueEvent` with the correlation fields the adapter reads (`run_id`,
  `instance_id`, `dispatch_id`, `operation_id`, `turn_id`, `task_id`,
  `tool_call_id`, `session`, `timestamp`).
- **6b — adapter** (**new** `pyflue/observability/otel.py`):
  `create_opentelemetry_observer(*, tracer=None, capture_content=False)` →
  callback for `observe(...)`. Port the reference span hierarchy + the
  `gen_ai.*` semantic attributes verbatim. Optional dep
  `pyflue[otel] = ["opentelemetry-api"]`.

**Files:** `pyflue/observability/otel.py` (new), `pyflue/observability/__init__.py`,
`pyflue/core.py` (event enrichment), `pyflue/types.py` (`FlueEvent` fields),
`pyproject.toml` (`otel` extra), `pyflue/__init__.py` (export
`create_opentelemetry_observer`).

**Design notes:** an MVP adapter can ship on the *current* events (workflow +
operation + log spans) and grow as 6a lands. Keep observer callbacks
non-blocking + exception-isolated (reference contract).

**Tests:** `tests/test_otel.py` with an in-memory span exporter — assert span
tree (workflow → operation → turn → tool) and `gen_ai.usage.*` attributes.

---

### 7. `local()` host sandbox factory

**Reference:** `@flue/runtime/node` `local()` (v0.6.0 changelog), `sandboxes.md`.
Host fs/shell, env **allowlist** (`PATH,HOME,USER,…`), explicit opt-in for
secrets.

**Proposed Python API** (new file `pyflue/sandboxes/local.py`):
```python
def local(*, cwd: str | None = None, env: dict[str, str] | None = None) -> SandboxFactory: ...
# init(sandbox=local(env={"GH_TOKEN": os.environ["GH_TOKEN"]}))
```
- Real-filesystem + subprocess shell sandbox implementing the existing
  `pyflue/sandboxes/base.py` protocol; default cwd = `os.getcwd()`; inherit only
  the shell-essentials allowlist from `os.environ`.

**Files:** `pyflue/sandboxes/local.py` (new), `pyflue/sandboxes/__init__.py`
(export), `pyflue/core.py` (`init(sandbox=...)` accepts a `SandboxFactory`/
callable, not just the `"virtual"` string), docs `docs/concepts/sandbox.md`.

**Design notes:** security — env allowlist is the whole point; default must not
leak host secrets into the agent's bash tool. Mirror the reference allowlist.

**Tests:** `tests/test_local_sandbox.py` — fs read/write against a tmpdir, shell
exec, env allowlist excludes a planted secret unless passed explicitly.

---

### 8. `define_agent_profile()` polish + Role → profile migration

**Reference:** `guide/subagents.md` (profiles replace roles; selection precedence
table; nested subagents; max depth 4).

**What exists:** pyflue has `Role`/`load_roles` (`.agents/roles/`),
`session.subagent()`, `session.task()`, `max_task_depth`. Closer than it looks.

**Plan:**
- Make `define_agent_profile()` (from item 1) the canonical subagent unit;
  `task(agent="name")` selects a declared profile; implement the
  selection/precedence table (instructions/skills/tools/subagents/model/
  thinking/compaction fallbacks).
- Provide `role_to_profile()` + keep `Role`/`load_roles` working with a
  deprecation note; `.agents/roles/*.md` still load, mapped to profiles.

**Files:** `pyflue/agents.py` (precedence resolver), `pyflue/skills.py`
(`Role`→profile bridge), `pyflue/core.py` (`task`/`subagent` consult declared
`subagents`; enforce depth 4), docs `docs/concepts/roles-and-routing.md` →
add subagents/profiles.

**Tests:** `tests/test_subagents.py` — named selection, precedence fallbacks,
`tools=[]`/`skills=[]` semantics, depth-4 limit, Role→profile bridge.

---

## Phase 3 — Surfaces & ecosystem

### 9. WebSocket (agents + workflows) + `client.connect`

**Reference:** `websocket-protocol.ts`, `types.ts`
(`AgentWebSocketHandler`, `WorkflowWebSocketHandler`, `*WebSocketClientMessage`/
`*ServerMessage`), `sdk/client-agents-connect.md`, `sdk/client-workflows-connect.md`.

**Files:**
- `pyflue/server.py` — `@app.websocket("/agents/{name}/{id}")` (persistent,
  multi-prompt) and `@app.websocket("/workflows/{name}")` (one finite run).
  FastAPI has native WebSocket support.
- **New** `pyflue/ws_protocol.py` — client/server message dataclasses matching
  the reference protocol.
- `pyflue/client.py` — `client.agents.connect(name, id)` and
  `client.workflows.connect(name)` async context managers (use `httpx-ws` or
  `websockets`); add `client.workflows.invoke(...)`/`.stream(...)`.
- `pyproject.toml` — `websockets` (or `httpx-ws`) dep.

**Tests:** `tests/test_websocket.py` — agent WS multi-prompt continuity;
workflow WS emits run events then closes; client connect round-trip.

---

### 10. Chat example (webhook → dispatch → reply tool)

**Reference:** `guide/chat.md`, `examples/chat-sdk/`.

**Goal:** a runnable pattern, not a new framework feature — verify a webhook
boundary normalises an event, `dispatch()`es into a thread-scoped agent session,
and replies via an explicit tool. No Chat-SDK Python dependency required.

**Files:** **new** `examples/chat/` — FastAPI app: `POST /webhooks/github`
verifies signature → `dispatch(assistant, id=thread, session=thread, input=…)`;
agent defines a `reply_to_thread` tool closing over the thread id; README.

**Tests:** `tests/test_chat_example.py` (smoke, no live network).

---

### 11. `ToolDef` → `ToolDefinition` alias + docs rewrite

**Reference:** v0.8.0 rename; `concepts/agents.mdx`, `guide/workflows.md`,
`guide/observability.md`, `introduction/why-flue.md`.

**Files:**
- `pyflue/types.py` — `ToolDefinition = ToolDef` alias (+ export both).
- `pyflue/__init__.py` — export `ToolDefinition`.
- **Docs rewrite:** new `docs/concepts/agents-vs-workflows.md`,
  `docs/concepts/workflows.md`, `docs/guides/observability.md`,
  `docs/concepts/subagents.md`; update `docs/index.md`, `mkdocs.yml` nav,
  `docs/reference/feature-matrix.md` (flip the new rows to Implemented).
- `README.md` + `CHANGELOG.md` (0.2.0 entry summarising the architecture shift).

---

## Deferred (lowest priority — map poorly to Python today)

- **Durable execution** (Cloudflare Durable Object durability, `run_resume`,
  `restartedFromRunId`). Python equivalent would need a durable queue + run
  store; revisit after Phase 1–3.
- **Cloudflare shell sandbox** (`getShellSandbox`) and the **long-tail sandbox
  connectors** (boxd, exedev, islo, mirage, smolvm, superserve, vercel).
- **Packaged `SkillReference`** bundling.

## Suggested release cuts

- **0.2.0** — Phase 1 (items 1–5): the Agents/Workflows architecture. Breaking
  (runs re-scoped); ship migration notes.
- **0.2.x** — Phase 2 (items 6–8): OTel, `local()`, profiles.
- **0.2.0** — Phase 3 (items 9–11): WebSocket, chat, docs/rename.
```
