# CLI

PyFlue exposes the `pyflue` command.

## `pyflue init`

Create a new project:

```bash
pyflue init my-agent
```

Overwrite an existing directory:

```bash
pyflue init my-agent --force
```

Generated files:

```text
AGENTS.md
pyflue.toml
.agents/skills/triage.md
.agents/roles/coder.md
agents/default.py
```

## `pyflue run`

Run one prompt:

```bash
pyflue run --prompt "Review this project"
```

Use a named session:

```bash
pyflue run --session issue-123 --prompt "Inspect the failure"
```

Enable sandbox writes and shell:

```bash
pyflue run \
  --prompt "Write a short report" \
  --allow-write \
  --allow-shell
```

Print stream events:

```bash
pyflue run --stream --prompt "Review this project"
```

## `pyflue skill new`

Create a new Markdown skill:

```bash
pyflue skill new review
```

## `pyflue routes`

List discovered agent routes:

```bash
pyflue routes
```

Use a specific config file:

```bash
pyflue routes --config pyflue.toml
```

## `pyflue invoke`

Invoke a file-based route locally without starting the development server:

```bash
pyflue invoke default demo --payload '{"prompt": "Hello"}'
```

The first argument is the route name. The second argument is the agent id.

## `pyflue add`

Print connector setup instructions for a coding agent.

List available connector guides:

```bash
pyflue add
```

Show copyable agent commands:

```bash
pyflue add daytona
```

Print the full guide:

```bash
pyflue add daytona --print
```

Build a custom sandbox connector from provider docs:

```bash
pyflue add https://e2b.dev/docs --category sandbox --print | codex
```

The command does not install hidden dependencies. It prints a clear guide that
your coding agent can apply to your project.

## `pyflue build`

```bash
pyflue build
```

The default target is Docker/FastAPI.

```bash
pyflue build --target docker
```

Available targets:

| Target | Status | Generated files |
| --- | --- | --- |
| `uvicorn` | Implemented | `dist/server.py`, `dist/requirements.txt`, `dist/manifest.json` |
| `lambda` | Implemented | `dist/main.py`, `dist/requirements.txt`, `dist/manifest.json` |
| `cloudrun` | Implemented | `dist/server.py`, `dist/Dockerfile`, `dist/cloudbuild.yaml`, `dist/requirements.txt`, `dist/manifest.json` |
| `docker` | Implemented | `dist/server.py`, `dist/Dockerfile`, `dist/requirements.txt`, `dist/manifest.json` |
| `github-actions` | Implemented | `.github/workflows/pyflue-agent.yml` |
| `gitlab-ci` | Implemented | `.gitlab-ci.yml` |
| `railway` | Implemented | `dist/server.py`, `dist/Dockerfile`, `dist/requirements.txt`, `dist/railway.json`, `dist/manifest.json` |
| `render` | Implemented | `dist/server.py`, `dist/Dockerfile`, `dist/requirements.txt`, `dist/render.yaml`, `dist/manifest.json` |
| `fly` | Implemented | `dist/server.py`, `dist/Dockerfile`, `dist/requirements.txt`, `dist/fly.toml`, `dist/manifest.json` |
| `vercel` | Implemented | `dist/server.py`, `dist/requirements.txt`, `dist/vercel.json`, `dist/manifest.json` |
| `netlify` | Implemented | `dist/server.py`, `dist/requirements.txt`, `dist/netlify.toml`, `dist/netlify/functions/pyflue.py`, `dist/manifest.json` |
| `cloudflare` | Beta | `dist/server.py`, `dist/Dockerfile`, `dist/requirements.txt`, `dist/worker.ts`, `dist/wrangler.jsonc`, `dist/package.json`, `dist/manifest.json` |

The runtime targets use the workspace build system when a PyFlue workspace is
present. It discovers agent files recursively under `agents/` or `.agents/`,
writes a manifest, and generates a Python server entrypoint. In an empty
directory, `docker`, `railway`, `render`, `fly`, `vercel`, `netlify`, and
`cloudflare` keep the lightweight root-level scaffold fallback.

## `pyflue dev`

Start the local development server with reload:

```bash
pyflue dev --port 2024 --env .env
```

The dev server watches `pyflue.toml`, Python route files, Markdown skills, and
Markdown roles under the configured workspace. The status endpoint includes
route metadata, file modification times, loaded skills, loaded roles, and active
sessions so route changes are visible without reading server logs.

Use `--env` to load default environment values from one or more `.env` files.
Variables already set in the shell are preserved.

The server exposes:

```text
GET  /openapi.json
GET  /health
GET  /agents
GET  /__pyflue
GET  /__pyflue/status
POST /agents/{name}/{agent_id}
POST /prompt/{agent_id}
POST /prompt/{agent_id}/events
GET  /runs/{run_id}
GET  /runs/{run_id}/events
GET  /runs/{run_id}/stream
POST /sessions/{session_id}/abort
```

Agent route calls support sync JSON by default, SSE run events with
`Accept: text/event-stream`, and webhook/fire-and-forget mode with
`X-Webhook: true`. Successful sync, SSE, and webhook responses include
`X-Flue-Run-Id`.

The main server mounts the read-only admin API at `/admin`. List responses
include both the existing PyFlue keys (`agents`, `runs`, `instances`) and
Flue-style pagination keys (`items`, `nextCursor`). List endpoints accept
`limit` and opaque `cursor` values returned from `nextCursor`; run lists also
accept `status=active|completed|errored` and `/admin/runs` accepts `agentName`.

OpenAPI documents are available at `/openapi.json` and `/admin/openapi.json`.

Run history is in-memory by default. For durable run/event history across
process restarts, set:

```bash
PYFLUE_RUN_STORE=sqlite
PYFLUE_RUN_STORE_PATH=.pyflue/runs.sqlite3
```

Webhook errors return a stable JSON envelope with `error.type`,
`error.message`, and `error.details`.

## `pyflue deploy`

Generate deployment artifacts and a deployment manifest:

```bash
pyflue deploy --dry-run
```

Select a target:

```bash
pyflue deploy --target railway --dry-run
```

For supported provider CLIs, PyFlue runs the provider command when the CLI is
installed and you are logged in. For other targets, it writes project files and
`.pyflue/deploy.json` with the next step.
