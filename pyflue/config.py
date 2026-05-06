"""pyflue.toml loading."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pyflue.types import (
    CompactionConfig,
    McpConfig,
    ProvidersConfig,
    ProviderSettings,
    PyFlueConfig,
)


def load_config(path: str | Path = "pyflue.toml") -> PyFlueConfig:
    """Load PyFlue configuration from TOML."""
    config_path = Path(path).expanduser()
    root = config_path.parent.resolve() if config_path.exists() else Path.cwd()
    data: dict[str, Any] = {}
    if config_path.exists():
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))

    agent = data.get("agent", {}) if isinstance(data.get("agent"), dict) else {}
    harness = str(agent.get("harness", "deepagents") or "deepagents")
    sandbox = str(agent.get("sandbox", "virtual") or "virtual")
    python_backend = agent.get("python_backend")
    skills_dir = agent.get("skills_dir")
    roles_dir = agent.get("roles_dir")
    agents_dir = agent.get("agents_dir")
    state_dir = agent.get("state_dir")
    allowed_commands = agent.get("allowed_commands", ())
    allow_compound_commands = bool(agent.get("allow_compound_commands", False))
    max_task_depth = int(agent.get("max_task_depth", 8) or 0)
    typed_retries = int(agent.get("typed_retries", 3) or 0)
    providers = _parse_providers(data.get("providers"))
    compaction = _parse_compaction(data.get("compaction"))
    mcp = _parse_mcp(data.get("mcp"))

    return PyFlueConfig(
        model=agent.get("model"),
        harness=harness,
        sandbox=sandbox,
        python_backend=str(python_backend) if python_backend else None,
        root=root,
        skills_dir=(root / skills_dir).resolve() if skills_dir else None,
        roles_dir=(root / roles_dir).resolve() if roles_dir else None,
        agents_dir=(root / agents_dir).resolve() if agents_dir else None,
        state_dir=(root / state_dir).resolve() if state_dir else None,
        allowed_commands=tuple(str(item) for item in allowed_commands),
        allow_compound_commands=allow_compound_commands,
        max_task_depth=max_task_depth,
        typed_retries=typed_retries,
        harness_config={
            key: value
            for key, value in data.items()
            if key not in {"agent", "deployment", "providers", "compaction", "mcp"}
        },
        providers=providers,
        compaction=compaction,
        mcp=mcp,
    )


def _parse_providers(value: Any) -> ProvidersConfig:
    config = ProvidersConfig()
    if not isinstance(value, dict):
        return config
    for name, raw in value.items():
        if not isinstance(raw, dict):
            continue
        headers = raw.get("headers")
        config.set(
            str(name),
            ProviderSettings(
                base_url=str(raw["base_url"]) if raw.get("base_url") else None,
                headers={str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else None,
                api_key=str(raw["api_key"]) if raw.get("api_key") else None,
            ),
        )
    return config


def _parse_compaction(value: Any) -> CompactionConfig:
    if not isinstance(value, dict):
        return CompactionConfig()
    return CompactionConfig(
        enabled=bool(value.get("enabled", True)),
        context_window_tokens=int(value.get("context_window_tokens", 128000) or 0),
        reserve_tokens=int(value.get("reserve_tokens", 16384) or 0),
        keep_recent_tokens=int(value.get("keep_recent_tokens", 20000) or 0),
    )


def _parse_mcp(value: Any) -> McpConfig | None:
    if not isinstance(value, dict):
        return None
    servers = value.get("servers", {})
    return McpConfig(
        servers={str(k): dict(v) for k, v in servers.items() if isinstance(v, dict)}
        if isinstance(servers, dict)
        else {},
        mode=value.get("mode", "direct"),
        search_limit=int(value.get("search_limit", 10) or 0),
        search_backend=value.get("search_backend", "bm25"),
    )
