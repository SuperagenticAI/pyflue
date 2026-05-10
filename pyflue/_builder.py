"""Build system for PyFlue deployment artifacts."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Literal

from pyflue.config import load_config
from pyflue.routing import discover_agent_routes
from pyflue.types import (
    AgentInfo,
    BuildContext,
    BuildOptions,
    BuildPlugin,
    PyFlueConfig,
    Role,
)

BuildTarget = Literal["uvicorn", "lambda", "docker", "cloudrun"]


class BuildResult:
    """Result returned by build()."""

    def __init__(self, changed: bool, output_dir: Path, generated_files: list[Path]):
        self.changed = changed
        self.output_dir = output_dir
        self.generated_files = generated_files


def build(options: BuildOptions) -> BuildResult:
    """Build a PyFlue workspace into deployable artifacts.

    Args:
        options.workspace_dir: Directory containing agents/ and roles/
        options.output_dir: Where to write dist/ directory
        options.target: Build target (uvicorn, lambda, docker, cloudrun)
        options.plugin: Override with a custom plugin

    Returns:
        BuildResult with output information
    """
    workspace_dir = Path(options.workspace_dir).resolve()
    output_dir = Path(options.output_dir).resolve()

    plugin = _resolve_plugin(options)

    config = load_config(options.config_path or workspace_dir / "pyflue.toml")
    roles = _discover_roles(workspace_dir, config)
    agents = _discover_agents(workspace_dir, config)

    if not agents:
        agents_path = config.agents_dir if config.agents_dir else "agents"
        raise ValueError(
            f"No agent files found.\n\n"
            f"Expected at: {workspace_dir / agents_path}/\n"
            f"Add at least one agent file (e.g., default.py)."
        )

    [a for a in agents if a.triggers.get("webhook")]
    [a for a in agents if a.triggers.get("cron")]

    print(f"[pyflue] Building workspace: {workspace_dir}")
    print(f"[pyflue] Output: {output_dir}")
    print(f"[pyflue] Target: {plugin.name}")
    print(f"[pyflue] Found {len(roles)} role(s): {', '.join(roles.keys()) or '(none)'}")
    print(f"[pyflue] Found {len(agents)} agent(s): {[a.name for a in agents]}")

    dist_dir = output_dir / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "agents": [
            {"name": a.name, "triggers": a.triggers}
            for a in agents
        ],
    }
    manifest_path = dist_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[pyflue] Generated: {manifest_path}")

    ctx = BuildContext(
        agents=agents,
        roles=roles,
        workspace_dir=workspace_dir,
        output_dir=output_dir,
        config=config,
    )

    entry_code = plugin.generate_entry_point(ctx)
    entry_filename = "main.py" if plugin.name == "lambda" else "server.py"
    entry_path = dist_dir / entry_filename

    existing_content = entry_path.read_text() if entry_path.exists() else None
    if existing_content != entry_code:
        entry_path.write_text(entry_code, encoding="utf-8")
        print(f"[pyflue] Generated: {entry_path}")
        changed = True
    else:
        print(f"[pyflue] Unchanged: {entry_path}")
        changed = False

    generated_files = [manifest_path, entry_path]

    additional = plugin.additional_outputs(ctx)
    for filename, content in additional.items():
        file_path = dist_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        existing = file_path.read_text() if file_path.exists() else None
        if existing != content:
            file_path.write_text(content, encoding="utf-8")
            print(f"[pyflue] Generated: {file_path}")
            changed = True
        generated_files.append(file_path)

    print(f"[pyflue] Build complete. Output: {dist_dir}")
    return BuildResult(changed=changed, output_dir=dist_dir, generated_files=generated_files)


def _resolve_plugin(options: BuildOptions) -> BuildPlugin:
    if options.plugin:
        return options.plugin

    if not options.target:
        raise ValueError(
            "[pyflue] No build target specified. Use --target to choose:\n"
            "  pyflue build --target uvicorn\n"
            "  pyflue build --target lambda\n"
            "  pyflue build --target docker\n"
            "  pyflue build --target cloudrun"
        )

    from pyflue.builder.plugins.cloudrun import CloudRunPlugin
    from pyflue.builder.plugins.docker import DockerPlugin
    from pyflue.builder.plugins.lambda_ import LambdaPlugin
    from pyflue.builder.plugins.uvicorn import UvicornPlugin

    plugins = {
        "uvicorn": UvicornPlugin,
        "lambda": LambdaPlugin,
        "docker": DockerPlugin,
        "cloudrun": CloudRunPlugin,
    }

    if options.target not in plugins:
        raise ValueError(
            f'[pyflue] Unknown target: "{options.target}". '
            f"Supported targets: {', '.join(plugins.keys())}"
        )

    return plugins[options.target]()


def _discover_roles(workspace_dir: Path, config: PyFlueConfig) -> dict[str, Role]:
    roles_path = config.roles_dir if config.roles_dir else ".agents/roles"
    roles_dir = workspace_dir / roles_path
    if not roles_dir.exists():
        return {}

    roles = {}
    for file_path in roles_dir.glob("*.md"):
        name = file_path.stem
        content = file_path.read_text(encoding="utf-8")
        parsed = _parse_frontmatter(content)
        roles[name] = Role(
            name=name,
            description=parsed.get("description", ""),
            instructions=parsed.get("body", ""),
            model=parsed.get("model"),
            thinking_level=parsed.get("thinking_level"),
            path=file_path,
        )

    return roles


def _discover_agents(workspace_dir: Path, config: PyFlueConfig) -> list[AgentInfo]:
    routes = discover_agent_routes(workspace_dir, config.agents_dir)
    return [
        AgentInfo(
            name=route.name,
            file_path=route.path,
            triggers=route.triggers,
        )
        for route in sorted(routes.values(), key=lambda item: item.name)
    ]


def _parse_triggers(source: str) -> dict[str, bool | str]:
    """Parse triggers from agent source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "triggers" for target in node.targets):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {
            str(key): item
            for key, item in value.items()
            if isinstance(item, bool | str)
        }
    return {}


def _parse_frontmatter(content: str) -> dict[str, Any]:
    """Parse YAML frontmatter from markdown."""
    if not content.startswith("---"):
        return {"body": content}

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {"body": content}

    import yaml
    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
    except Exception:
        frontmatter = {}

    return {
        "name": frontmatter.get("name", ""),
        "description": frontmatter.get("description", ""),
        "model": frontmatter.get("model"),
        "thinking_level": frontmatter.get("thinking_level"),
        "body": parts[2].strip() if len(parts) > 2 else "",
    }


def resolve_workspace_from_cwd(cwd: str | Path = ".") -> Path | None:
    """Resolve a PyFlue workspace from the current working directory.

    Supports two layouts:
      1. <cwd>/.agents/ - project itself is the workspace
      2. <cwd>/ - use .agents/ subdirectory

    Returns None if neither layout is found.
    """
    cwd = Path(cwd).resolve()

    if (cwd / ".agents").exists():
        return cwd
    if (cwd / "agents").exists():
        return cwd

    return None
