"""Deployment artifact generation."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Literal

DeployTarget = Literal[
    "docker",
    "github-actions",
    "gitlab-ci",
    "railway",
    "render",
    "fly",
    "vercel",
    "netlify",
    "cloudflare",
]


def write_deploy_artifacts(target: DeployTarget = "docker", root: str | Path = ".") -> list[Path]:
    """Generate deployment files for a target."""
    base = Path(root)
    if target == "docker":
        return _write_docker_artifacts(base)
    if target == "github-actions":
        return [_write_github_actions_workflow(base)]
    if target == "gitlab-ci":
        return [_write_gitlab_ci(base)]
    if target == "railway":
        paths = _write_docker_artifacts(base)
        path = base / "railway.json"
        path.write_text(
            json.dumps(
                {
                    "$schema": "https://railway.app/railway.schema.json",
                    "build": {"builder": "DOCKERFILE"},
                    "deploy": {
                        "startCommand": "uvicorn app:app --host 0.0.0.0 --port $PORT"
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return [*paths, path]
    if target == "render":
        paths = _write_docker_artifacts(base)
        path = base / "render.yaml"
        path.write_text(
            "services:\n"
            "  - type: web\n"
            "    name: pyflue-agent\n"
            "    runtime: docker\n"
            "    plan: starter\n"
            "    envVars:\n"
            "      - key: PORT\n"
            "        value: 8000\n",
            encoding="utf-8",
        )
        return [*paths, path]
    if target == "fly":
        paths = _write_docker_artifacts(base)
        path = base / "fly.toml"
        path.write_text(
            'app = "pyflue-agent"\n'
            'primary_region = "iad"\n\n'
            "[http_service]\n"
            "  internal_port = 8000\n"
            "  force_https = true\n"
            "  auto_stop_machines = true\n"
            "  auto_start_machines = true\n",
            encoding="utf-8",
        )
        return [*paths, path]
    if target == "vercel":
        paths = _write_docker_artifacts(base)
        path = base / "vercel.json"
        path.write_text(
            json.dumps(
                {
                    "builds": [{"src": "app.py", "use": "@vercel/python"}],
                    "routes": [{"src": "/(.*)", "dest": "app.py"}],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return [*paths, path]
    if target == "netlify":
        paths = _write_docker_artifacts(base)
        path = base / "netlify.toml"
        path.write_text(
            "[build]\n"
            "  command = \"pip install .\"\n"
            "  publish = \".\"\n\n"
            "[functions]\n"
            "  directory = \"netlify/functions\"\n",
            encoding="utf-8",
        )
        return [*paths, path]
    if target == "cloudflare":
        paths = _write_docker_artifacts(base)
        worker = base / "worker.ts"
        wrangler = base / "wrangler.jsonc"
        package = base / "package.json"
        worker.write_text(
            'import { Container } from "@cloudflare/containers";\n\n'
            "export class PyFlueContainer extends Container {\n"
            "  defaultPort = 8000;\n"
            '  sleepAfter = "10m";\n'
            "}\n\n"
            "export default {\n"
            "  async fetch(request: Request, env: Env): Promise<Response> {\n"
            "    const url = new URL(request.url);\n"
            '    const name = url.pathname.startsWith("/agents/")\n'
            '      ? url.pathname.split("/").slice(0, 4).join("/")\n'
            '      : "default";\n'
            "    const container = env.PYFLUE_CONTAINER.getByName(name);\n"
            "    return container.fetch(request);\n"
            "  },\n"
            "};\n\n"
            "interface Env {\n"
            "  PYFLUE_CONTAINER: DurableObjectNamespace<PyFlueContainer> & {\n"
            "    getByName(name: string): DurableObjectStub;\n"
            "  };\n"
            "}\n",
            encoding="utf-8",
        )
        wrangler.write_text(
            json.dumps(
                {
                    "$schema": "node_modules/wrangler/config-schema.json",
                    "name": "pyflue-agent",
                    "main": "worker.ts",
                    "compatibility_date": "2026-05-06",
                    "containers": [
                        {
                            "class_name": "PyFlueContainer",
                            "image": "./Dockerfile",
                            "max_instances": 3,
                            "instance_type": "basic",
                        }
                    ],
                    "durable_objects": {
                        "bindings": [
                            {
                                "name": "PYFLUE_CONTAINER",
                                "class_name": "PyFlueContainer",
                            }
                        ]
                    },
                    "migrations": [
                        {
                            "tag": "v1",
                            "new_sqlite_classes": ["PyFlueContainer"],
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        package.write_text(
            json.dumps(
                {
                    "private": True,
                    "type": "module",
                    "scripts": {
                        "dev": "wrangler dev",
                        "deploy": "wrangler deploy",
                    },
                    "dependencies": {
                        "@cloudflare/containers": "^0.0.19",
                    },
                    "devDependencies": {
                        "wrangler": "^4.0.0",
                        "typescript": "^5.0.0",
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return [*paths, worker, wrangler, package]
    raise ValueError(f"Unknown deployment target: {target}")


def run_provider_deploy(target: DeployTarget, *, root: str | Path = ".") -> dict[str, object]:
    """Run the provider CLI when PyFlue knows a safe deployment command."""
    command = _provider_command(target)
    if command is None:
        return {
            "ran": False,
            "reason": f"No direct provider deploy command is defined for {target}.",
        }
    executable = command[0]
    if shutil.which(executable) is None:
        return {
            "ran": False,
            "reason": f"Provider CLI not found: {executable}",
            "command": command,
        }
    completed = subprocess.run(
        command,
        cwd=Path(root),
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "ran": True,
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _provider_command(target: DeployTarget) -> list[str] | None:
    if target == "fly":
        return ["fly", "deploy"]
    if target == "railway":
        return ["railway", "up"]
    if target == "vercel":
        return ["vercel", "deploy"]
    if target == "netlify":
        return ["netlify", "deploy"]
    if target == "cloudflare":
        return ["wrangler", "deploy"]
    return None


def _write_docker_artifacts(base: Path) -> list[Path]:
    dockerfile = base / "Dockerfile"
    app = base / "app.py"
    dockerfile.write_text(
        "FROM python:3.12-slim\n"
        "WORKDIR /app\n"
        "COPY . .\n"
        "RUN pip install . 'pyflue[server]'\n"
        'CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]\n',
        encoding="utf-8",
    )
    app.write_text(
        "from pyflue.server import create_app\n\n"
        "app = create_app()\n",
        encoding="utf-8",
    )
    return [dockerfile, app]


def _write_github_actions_workflow(base: Path) -> Path:
    path = base / ".github" / "workflows" / "pyflue-agent.yml"
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
    return path


def _write_gitlab_ci(base: Path) -> Path:
    path = base / ".gitlab-ci.yml"
    path.write_text(
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
    return path
