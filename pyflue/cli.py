"""PyFlue command-line interface."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Literal

import typer
from rich.console import Console

from pyflue import init
from pyflue._builder import build as build_agent
from pyflue._builder import resolve_workspace_from_cwd
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
PROMPT_OPTION = typer.Option(..., "--prompt", "-p", help="Prompt to run.")
SESSION_OPTION = typer.Option("default", "--session", "-s")
CONFIG_OPTION = typer.Option("pyflue.toml", "--config")
ALLOW_WRITE_OPTION = typer.Option(False, "--allow-write")
ALLOW_SHELL_OPTION = typer.Option(False, "--allow-shell")
PORT_OPTION = typer.Option(2024, "--port")
WORKSPACE_OPTION = typer.Option(None, "--workspace", "-w")
OUTPUT_OPTION = typer.Option(None, "--output", "-o")
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
        '[agent]\nmodel = "openai:gpt-5.5"\nharness = "deepagents"\nsandbox = "virtual"\n',
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
    prompt: str = PROMPT_OPTION,
    session: str = SESSION_OPTION,
    config: Path = CONFIG_OPTION,
    allow_write: bool = ALLOW_WRITE_OPTION,
    allow_shell: bool = ALLOW_SHELL_OPTION,
    stream: bool = typer.Option(False, "--stream", help="Print normalized stream events."),
) -> None:
    """Run one PyFlue prompt."""

    async def _run() -> None:
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

    asyncio.run(_run())


@app.command()
def dev(port: int = PORT_OPTION, config: Path = CONFIG_OPTION) -> None:
    """Start a development server with hot-reload support."""
    try:
        import uvicorn
    except Exception as exc:
        raise typer.BadParameter(
            "pyflue dev requires server dependencies. Install with: pip install 'pyflue[server]'"
        ) from exc
    console.print(f"Starting PyFlue dev server on http://127.0.0.1:{port}")
    console.print(f"Dashboard: http://127.0.0.1:{port}/__pyflue")
    console.print(f"Status: http://127.0.0.1:{port}/__pyflue/status")
    uvicorn.run(
        "pyflue.server:create_app",
        factory=True,
        host="127.0.0.1",
        port=port,
        reload=True,
        app_dir=str(config.parent.resolve()),
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


@app.command()
def build(
    target: BuildTarget = "docker",
    workspace: Path | None = WORKSPACE_OPTION,
    output: Path | None = OUTPUT_OPTION,
) -> None:
    """Generate deployment artifacts."""
    new_targets = {"uvicorn", "lambda", "docker", "cloudrun"}

    if target in new_targets:
        workspace_dir = workspace or resolve_workspace_from_cwd()
        if not workspace_dir:
            if target == "docker":
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


def _parse_payload(payload: str | None) -> dict[str, Any]:
    return json.loads(payload or "{}")
