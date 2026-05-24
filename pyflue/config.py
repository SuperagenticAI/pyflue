"""PyFlue configuration loading."""

from __future__ import annotations

import importlib.util
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


def define_config(config: dict[str, Any] | PyFlueConfig) -> dict[str, Any] | PyFlueConfig:
    """Identity helper for authoring ``pyflue.config.py`` with editor hints."""
    return config


def load_config(path: str | Path = "pyflue.toml") -> PyFlueConfig:
    """Load PyFlue configuration from TOML or ``pyflue.config.py``."""
    config_path = _resolve_config_path(path)
    root = config_path.parent.resolve() if config_path and config_path.exists() else Path.cwd()
    data: dict[str, Any] = {}
    if config_path and config_path.exists() and config_path.suffix == ".py":
        data = _load_python_config(config_path)
    elif config_path and config_path.exists():
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    return _config_from_data(data, root=root, config_path=config_path)


def _config_from_data(
    data: dict[str, Any],
    *,
    root: Path,
    config_path: Path | None,
) -> PyFlueConfig:
    agent = data.get("agent", {}) if isinstance(data.get("agent"), dict) else {}
    harness = str(agent.get("harness", "deepagents") or "deepagents")
    sandbox = str(agent.get("sandbox", "virtual") or "virtual")
    thinking_level = agent.get("thinking_level")
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
        thinking_level=str(thinking_level) if thinking_level else None,
        harness=harness,
        sandbox=sandbox,
        python_backend=str(python_backend) if python_backend else None,
        root=root,
        config_path=config_path.resolve() if config_path and config_path.exists() else None,
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


def _resolve_config_path(path: str | Path) -> Path | None:
    config_path = Path(path).expanduser()
    if config_path.exists():
        return config_path
    if str(path) == "pyflue.toml":
        python_config = Path("pyflue.config.py")
        if python_config.exists():
            return python_config
    return config_path


def _load_python_config(path: Path) -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location("pyflue_user_config", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load config file: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = (
        getattr(module, "config", None)
        or getattr(module, "CONFIG", None)
        or getattr(module, "default", None)
    )
    if isinstance(raw, PyFlueConfig):
        return _data_from_pyflue_config(raw)
    if not isinstance(raw, dict):
        raise TypeError(
            f"{path} must define `config`, `CONFIG`, or `default` as a dict or PyFlueConfig."
        )
    return raw


def _data_from_pyflue_config(config: PyFlueConfig) -> dict[str, Any]:
    data: dict[str, Any] = {
        "agent": {
            "model": config.model,
            "thinking_level": config.thinking_level,
            "harness": config.harness,
            "sandbox": config.sandbox,
            "python_backend": config.python_backend,
            "skills_dir": str(config.skills_dir) if config.skills_dir else None,
            "roles_dir": str(config.roles_dir) if config.roles_dir else None,
            "agents_dir": str(config.agents_dir) if config.agents_dir else None,
            "state_dir": str(config.state_dir) if config.state_dir else None,
            "allowed_commands": list(config.allowed_commands),
            "allow_compound_commands": config.allow_compound_commands,
            "max_task_depth": config.max_task_depth,
            "typed_retries": config.typed_retries,
        },
        "providers": {
            name: {
                "base_url": settings.base_url,
                "headers": settings.headers,
                "api_key": settings.api_key,
                "store_responses": settings.store_responses,
            }
            for name, settings in config.providers.providers.items()
        },
        "compaction": {
            "enabled": config.compaction.enabled,
            "context_window_tokens": config.compaction.context_window_tokens,
            "reserve_tokens": config.compaction.reserve_tokens,
            "keep_recent_tokens": config.compaction.keep_recent_tokens,
        },
        "mcp": {
            "servers": config.mcp.servers,
            "mode": config.mcp.mode,
            "search_limit": config.mcp.search_limit,
            "search_backend": config.mcp.search_backend,
        } if config.mcp else None,
    }
    return _drop_none(data)


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_none(item) for item in value if item is not None]
    return value


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
                store_responses=bool(raw.get("store_responses", False)),
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
