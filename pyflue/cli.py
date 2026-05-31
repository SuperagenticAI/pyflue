"""PyFlue command-line interface."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Literal

import typer
from rich.console import Console

from pyflue import init
from pyflue._builder import build as build_agent
from pyflue._builder import resolve_workspace_from_cwd
from pyflue.config import load_config
from pyflue.connectors import (
    render_add_prompt,
    render_connector_listing,
    render_human_instructions,
)
from pyflue.deploy import DeployTarget, run_provider_deploy, write_deploy_artifacts

app = typer.Typer(help="PyFlue agent harness CLI.")
skill_app = typer.Typer(help="Manage Markdown skills.")
app.add_typer(skill_app, name="skill")
console = Console()
SESSION_OPTION = typer.Option("default", "--session", "-s")
CONFIG_OPTION = typer.Option("pyflue.toml", "--config")
ALLOW_WRITE_OPTION = typer.Option(False, "--allow-write")
ALLOW_SHELL_OPTION = typer.Option(False, "--allow-shell")
PORT_OPTION = typer.Option(2024, "--port")
WORKSPACE_OPTION = typer.Option(None, "--workspace", "-w")
OUTPUT_OPTION = typer.Option(None, "--output", "-o")
PAYLOAD_OPTION = typer.Option("{}", "--payload", help="JSON payload for a local route call.")
ENV_OPTION = typer.Option(None, "--env", help="Load defaults from a .env file.")
PROJECT_NAME_ARGUMENT = typer.Argument("pyflue-agent")
SKILL_NAME_ARGUMENT = typer.Argument(...)
BuildTarget = Literal[
    "uvicorn",
    "lambda",
    "docker",
    "cloudrun",
    "github-actions",
    "gitlab-ci",
    "railway",
    "render",
    "fly",
    "vercel",
    "netlify",
    "cloudflare",
]


@app.command("init")
def init_project(name: str = PROJECT_NAME_ARGUMENT, force: bool = False) -> None:
    """Scaffold a PyFlue project."""
    root = Path(name).resolve()
    if root.exists() and any(root.iterdir()) and not force:
        raise typer.BadParameter(f"{root} is not empty. Use --force to overwrite.")
    (root / ".agents" / "skills").mkdir(parents=True, exist_ok=True)
    (root / ".agents" / "roles").mkdir(parents=True, exist_ok=True)
    (root / "agents").mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text(
        "You are a careful autonomous Python agent. Keep changes scoped.\n",
        encoding="utf-8",
    )
    (root / "pyflue.toml").write_text(
        '[agent]\nmodel = "openai:gpt-5.5"\nharness = "pydanticai"\nsandbox = "virtual"\n',
        encoding="utf-8",
    )
    _write_skill(root / ".agents" / "skills" / "triage.md", "triage")
    (root / ".agents" / "roles" / "coder.md").write_text(
        "---\nname: coder\ndescription: Careful coding role\n---\n"
        "You are a careful coding agent. Inspect before editing and verify your work.\n",
        encoding="utf-8",
    )
    (root / "agents" / "default.py").write_text(
        "triggers = {'webhook': True}\n\n"
        "async def default(context):\n"
        "    agent = await context.init()\n"
        "    session = await agent.session(context.agent_id)\n"
        "    result = await session.prompt(context.payload.get('prompt', 'Hello from PyFlue'))\n"
        "    return {'text': result.text, 'metadata': result.metadata}\n",
        encoding="utf-8",
    )
    # Canonical `src/` layout (reference v0.8.x): persistent agents in
    # src/agents/ and finite workflows in src/workflows/. Both are discovered.
    (root / "src" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "src" / "workflows").mkdir(parents=True, exist_ok=True)
    (root / "src" / "agents" / "assistant.py").write_text(
        "from pyflue import create_agent\n\n"
        "default = create_agent(\n"
        "    lambda ctx: {\n"
        '        "model": "openai:gpt-5.5",\n'
        '        "instructions": f"Help with the request represented by {ctx.id}.",\n'
        "    }\n"
        ")\n",
        encoding="utf-8",
    )
    (root / "src" / "workflows" / "summarize.py").write_text(
        "from pyflue import FlueContext, create_agent\n\n"
        'agent = create_agent(lambda ctx: {"model": "openai:gpt-5.5"})\n\n\n'
        "async def run(ctx: FlueContext):\n"
        "    harness = await ctx.init(agent)\n"
        "    session = await harness.session()\n"
        '    response = await session.prompt(f"Summarize:\\n\\n{ctx.payload.get(\'text\', \'\')}")\n'
        '    return {"summary": response.text}\n',
        encoding="utf-8",
    )
    console.print(f"Created PyFlue project at {root}")


@app.command("add")
def add_connector(
    name: str | None = typer.Argument(None),
    category: str = typer.Option("sandbox", "--category", "-c"),
    print_: bool = typer.Option(False, "--print", help="Print the connector guide."),
) -> None:
    """Print connector setup instructions for a coding agent."""
    if not name:
        console.print(render_connector_listing())
        return
    if print_:
        console.print(render_add_prompt(name, category=category))
        return
    console.print(render_human_instructions(name, category=category))


@app.command()
def run(
    workflow: str | None = typer.Argument(
        None, help="Workflow name to run. Omit to run a one-off --prompt."
    ),
    prompt: str | None = typer.Option(
        None, "--prompt", "-p", help="Prompt to run when no workflow name is given."
    ),
    payload: str = PAYLOAD_OPTION,
    session: str = SESSION_OPTION,
    config: Path = CONFIG_OPTION,
    allow_write: bool = ALLOW_WRITE_OPTION,
    allow_shell: bool = ALLOW_SHELL_OPTION,
    stream: bool = typer.Option(False, "--stream", help="Print normalized stream events (prompt mode)."),
) -> None:
    """Run a workflow, or a one-off prompt with --prompt."""

    async def _run_workflow() -> None:
        from pyflue.workflows import discover_workflows, invoke_workflow

        loaded = load_config(config)
        discovered = discover_workflows(loaded.root, getattr(loaded, "workflows_dir", None))
        if workflow not in discovered:
            available = ", ".join(sorted(discovered)) or "(none)"
            raise typer.BadParameter(f"Unknown workflow: {workflow}. Available workflows: {available}")
        result = await invoke_workflow(
            discovered[workflow],
            payload=_parse_payload(payload),
            config_path=config,
        )
        if isinstance(result, dict):
            console.print_json(data=result)
        elif hasattr(result, "model_dump"):
            console.print_json(data=result.model_dump())
        else:
            console.print(str(result))

    async def _run_prompt() -> None:
        agent = await init(
            config_path=config,
            allow_write=allow_write,
            allow_shell=allow_shell,
        )
        pyflue_session = await agent.session(session)
        if stream:
            async for event in pyflue_session.stream(prompt):
                console.print_json(data={"type": event.type, **event.data})
            return
        result = await pyflue_session.prompt(prompt)
        console.print(result.text)

    if workflow:
        asyncio.run(_run_workflow())
    elif prompt is not None:
        asyncio.run(_run_prompt())
    else:
        raise typer.BadParameter(
            "Provide a workflow name, or --prompt to run a one-off prompt."
        )


@app.command()
def dev(
    port: int = PORT_OPTION,
    config: Path = CONFIG_OPTION,
    env: list[Path] | None = ENV_OPTION,
) -> None:
    """Start a development server with hot-reload support."""
    try:
        import uvicorn
    except Exception as exc:
        raise typer.BadParameter(
            "pyflue dev requires server dependencies. Install with: pip install 'pyflue[server]'"
        ) from exc
    loaded = load_config(config)
    loaded_env = _load_env_files(env or [])
    reload_dirs = _dev_reload_dirs(loaded.root, loaded.agents_dir, loaded.skills_dir, loaded.roles_dir)
    os.environ["PYFLUE_CONFIG"] = str(config.resolve())
    console.print(f"Starting PyFlue dev server on http://127.0.0.1:{port}")
    console.print(f"Dashboard: http://127.0.0.1:{port}/__pyflue")
    console.print(f"Status: http://127.0.0.1:{port}/__pyflue/status")
    console.print("Watching: " + ", ".join(str(path) for path in reload_dirs))
    if loaded_env:
        console.print("Loaded env defaults: " + ", ".join(str(path) for path in loaded_env))
    uvicorn.run(
        "pyflue.server:create_app",
        factory=True,
        host="127.0.0.1",
        port=port,
        reload=True,
        reload_dirs=[str(path) for path in reload_dirs],
        reload_includes=["*.py", "*.md", "*.toml"],
        app_dir=str(loaded.root),
    )


@app.command()
def routes(config: Path = CONFIG_OPTION) -> None:
    """List discovered agent routes for the current workspace."""
    from pyflue.config import load_config
    from pyflue.routing import discover_agent_routes

    loaded = load_config(config)
    discovered = discover_agent_routes(loaded.root, loaded.agents_dir)
    rows = [
        {"name": route.name, "path": route.url_path, "triggers": route.triggers}
        for route in discovered.values()
    ]
    console.print_json(data={"agents": rows})


@app.command("invoke")
def invoke_route_command(
    name: str = typer.Argument(..., help="Agent route name."),
    agent_id: str = typer.Argument("default", help="Agent/session id for the route call."),
    payload: str = PAYLOAD_OPTION,
    config: Path = CONFIG_OPTION,
) -> None:
    """Invoke a file-based agent route locally."""
    from pyflue.routing import discover_agent_routes, invoke_route

    async def _invoke() -> None:
        loaded = load_config(config)
        discovered = discover_agent_routes(loaded.root, loaded.agents_dir)
        if name not in discovered:
            available = ", ".join(sorted(discovered)) or "(none)"
            raise typer.BadParameter(f"Unknown route: {name}. Available routes: {available}")
        result = await invoke_route(
            discovered[name],
            agent_id=agent_id,
            payload=_parse_payload(payload),
            config_path=config,
        )
        console.print_json(data=result if isinstance(result, dict) else {"result": result})

    asyncio.run(_invoke())


@app.command()
def build(
    target: BuildTarget = "docker",
    workspace: Path | None = WORKSPACE_OPTION,
    output: Path | None = OUTPUT_OPTION,
) -> None:
    """Generate deployment artifacts."""
    new_targets = {
        "uvicorn",
        "lambda",
        "docker",
        "cloudrun",
        "railway",
        "render",
        "fly",
        "vercel",
        "netlify",
        "cloudflare",
    }

    if target in new_targets:
        workspace_dir = workspace or resolve_workspace_from_cwd()
        if not workspace_dir:
            if target not in {"uvicorn", "lambda", "cloudrun"}:
                paths = write_deploy_artifacts(target)
                console.print("Generated " + ", ".join(str(path) for path in paths))
                return
            raise typer.BadParameter(
                "No PyFlue workspace found. Run pyflue init first or use --workspace."
            )
        output_dir = output or workspace_dir

        try:
            from pyflue._builder import BuildOptions
            result = build_agent(BuildOptions(
                workspace_dir=str(workspace_dir),
                output_dir=str(output_dir),
                target=target,
            ))
            console.print(f"Generated {len(result.generated_files)} file(s) in {result.output_dir}")
        except Exception as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from exc
    else:
        paths = write_deploy_artifacts(target)
        console.print("Generated " + ", ".join(str(path) for path in paths))


@app.command("logs")
def logs(
    run_id: str = typer.Argument(..., help="Run id returned by an agent invocation."),
    server: str = typer.Option("http://127.0.0.1:2024", "--server", help="PyFlue server base URL."),
    follow: bool = typer.Option(True, "--follow/--no-follow"),
    since: int = typer.Option(0, "--since", help="Only show events with event_index > since."),
    types: str | None = typer.Option(None, "--types", help="Comma-separated event types to include."),
    limit: int = typer.Option(1000, "--limit", help="Max events when not following."),
    format: str = typer.Option("pretty", "--format", help="pretty | json | ndjson"),
) -> None:
    """Replay or tail a run's event log from a running PyFlue server."""
    if format not in {"pretty", "json", "ndjson"}:
        raise typer.BadParameter("--format must be one of: pretty, json, ndjson")

    async def _run() -> None:
        import httpx

        base = server.rstrip("/")
        async with httpx.AsyncClient(timeout=None) as client:
            if not follow:
                params: dict[str, Any] = {"after": since, "limit": limit}
                if types:
                    params["types"] = types
                resp = await client.get(f"{base}/runs/{run_id}/events", params=params)
                resp.raise_for_status()
                payload = resp.json()
                _render_events(payload.get("events", []), format)
                return

            # Follow: stream SSE.
            headers = {"Accept": "text/event-stream"}
            if since:
                headers["Last-Event-ID"] = str(since)
            type_filter = {t.strip() for t in types.split(",")} if types else None
            async with client.stream(
                "GET", f"{base}/runs/{run_id}/stream", headers=headers
            ) as resp:
                resp.raise_for_status()
                data_lines: list[str] = []
                async for raw_line in resp.aiter_lines():
                    if raw_line == "":
                        if data_lines:
                            payload = json.loads("\n".join(data_lines))
                            if type_filter is None or payload.get("type") in type_filter:
                                _render_event(payload, format)
                            if payload.get("type") == "run_end":
                                return
                        data_lines = []
                        continue
                    if raw_line.startswith(":"):
                        continue
                    if raw_line.startswith("event:"):
                        continue
                    elif raw_line.startswith("data:"):
                        data_lines.append(raw_line[len("data:"):].lstrip())

    asyncio.run(_run())


@app.command()
def deploy(target: DeployTarget = "docker", dry_run: bool = False) -> None:
    """Deploy the PyFlue agent using the configured harness."""
    paths = write_deploy_artifacts(target)
    manifest = Path(".pyflue") / "deploy.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"target": target, "artifacts": [str(path) for path in paths]}, indent=2) + "\n",
        encoding="utf-8",
    )
    if dry_run:
        console.print(f"Dry run: generated deployment manifest for {target}.")
    else:
        deploy_result = run_provider_deploy(target)
        if deploy_result.get("ran"):
            console.print_json(data=deploy_result)
        else:
            console.print(
                f"Generated deployment artifacts for {target}. "
                f"{deploy_result['reason']}"
            )


@skill_app.command("new")
def new_skill(name: str = SKILL_NAME_ARGUMENT) -> None:
    """Create a new Markdown skill."""
    path = Path(".agents") / "skills" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_skill(path, name)
    console.print(f"Created skill {path}")


def _write_skill(path: Path, name: str) -> None:
    content = {
        "name": name,
        "description": f"{name} workflow",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {"summary": {"type": "string"}}},
    }
    frontmatter = "\n".join(
        [
            "---",
            f"name: {content['name']}",
            f"description: {content['description']}",
            "input_schema:",
            "  type: object",
            "  properties: {}",
            "output_schema:",
            "  type: object",
            "  properties:",
            "    summary:",
            "      type: string",
            "---",
            "",
            "# Role",
            "You are a PyFlue skill.",
            "",
            "## Instructions",
            "Complete the requested workflow and return a concise result.",
        ]
    )
    path.write_text(frontmatter, encoding="utf-8")


def _write_docker_artifacts() -> None:
    Path("Dockerfile").write_text(
        "FROM python:3.11-slim\n"
        "WORKDIR /app\n"
        "COPY . .\n"
        "RUN pip install . fastapi uvicorn\n"
        'CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]\n',
        encoding="utf-8",
    )
    Path("app.py").write_text(
        "from fastapi import FastAPI\n"
        "from pydantic import BaseModel\n\n"
        "from pyflue import init\n\n\n"
        "class PromptRequest(BaseModel):\n"
        "    prompt: str\n"
        "    session: str = \"default\"\n\n\n"
        "app = FastAPI(title=\"PyFlue Agent\")\n\n\n"
        "@app.post(\"/prompt\")\n"
        "async def prompt(request: PromptRequest):\n"
        "    agent = await init()\n"
        "    session = await agent.session(request.session)\n"
        "    result = await session.prompt(request.prompt)\n"
        "    return {\"text\": result.text, \"metadata\": result.metadata}\n",
        encoding="utf-8",
    )


def _write_github_actions_workflow() -> None:
    path = Path(".github") / "workflows" / "pyflue-agent.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "name: PyFlue Agent\n\n"
        "on:\n"
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      prompt:\n"
        "        description: Prompt to run\n"
        "        required: true\n"
        "        default: Review this repository\n\n"
        "jobs:\n"
        "  agent:\n"
        "    runs-on: ubuntu-latest\n"
        "    permissions:\n"
        "      contents: read\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: astral-sh/setup-uv@v5\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        "          python-version: '3.12'\n"
        "      - run: uv sync\n"
        "      - name: Run PyFlue agent\n"
        "        env:\n"
        "          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}\n"
        "        run: uv run pyflue run --allow-shell --prompt \"${{ inputs.prompt }}\"\n",
        encoding="utf-8",
    )


def _write_gitlab_ci() -> None:
    Path(".gitlab-ci.yml").write_text(
        "pyflue-agent:\n"
        "  image: ghcr.io/astral-sh/uv:python3.12-bookworm-slim\n"
        "  rules:\n"
        "    - if: $CI_PIPELINE_SOURCE == \"web\"\n"
        "  variables:\n"
        "    PROMPT: \"Review this repository\"\n"
        "  script:\n"
        "    - uv sync\n"
        "    - uv run pyflue run --allow-shell --prompt \"$PROMPT\"\n",
        encoding="utf-8",
    )


def _dev_reload_dirs(
    root: Path,
    agents_dir: Path | None,
    skills_dir: Path | None,
    roles_dir: Path | None,
) -> list[Path]:
    candidates = [
        root,
        agents_dir or root / "agents",
        root / ".agents",
        skills_dir or root / ".agents" / "skills",
        roles_dir or root / ".agents" / "roles",
    ]
    result: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved.exists() and resolved not in result:
            result.append(resolved)
    return result or [root.resolve()]


def _load_env_files(paths: list[Path]) -> list[Path]:
    loaded: list[Path] = []
    merged: dict[str, str] = {}
    for raw_path in paths:
        path = raw_path.expanduser().resolve()
        if not path.exists():
            raise typer.BadParameter(f"Env file does not exist: {path}")
        merged.update(_parse_env_file(path))
        loaded.append(path)
    for key, value in merged.items():
        os.environ.setdefault(key, value)
    return loaded


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            raise typer.BadParameter(f"Invalid env line in {path}:{line_no}")
        values[key.strip()] = _strip_env_value(value.strip())
    return values


def _strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_payload(payload: str | None) -> dict[str, Any]:
    return json.loads(payload or "{}")


def _render_events(events: list[dict[str, Any]], format: str) -> None:
    if format == "json":
        console.print_json(data={"events": events})
        return
    for event in events:
        _render_event(event, format)


def _render_event(event: dict[str, Any], format: str) -> None:
    if format == "ndjson":
        console.print(json.dumps(event))
        return
    if format == "json":
        console.print_json(data=event)
        return
    # pretty
    idx = event.get("event_index", "?")
    typ = event.get("type", "?")
    data = event.get("data") or {}
    detail = ""
    if typ == "log":
        level = data.get("level", "info")
        detail = f"  [{level}] {data.get('message', '')}"
    elif typ == "run_end":
        detail = f"  status={data.get('status')} is_error={data.get('is_error')}"
    elif data:
        detail = "  " + json.dumps(data)[:200]
    console.print(f"[{idx:>4}] {typ}{detail}")
