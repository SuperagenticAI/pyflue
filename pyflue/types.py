"""Shared PyFlue types."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

SandboxName = Literal["virtual", "local", "daytona", "e2b", "modal", "runloop"]


@dataclass(frozen=True)
class Skill:
    """Markdown-defined reusable agent workflow."""

    name: str
    description: str = ""
    instructions: str = ""
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    path: Path | None = None


@dataclass(frozen=True)
class Role:
    """Markdown-defined scoped behavior for a prompt or child task."""

    name: str
    instructions: str
    description: str = ""
    model: str | None = None
    path: Path | None = None


@dataclass
class ProviderSettings:
    """Runtime settings for a model provider.

    Useful for API gateways, LiteLLM-style proxies, or enterprise-managed endpoints.
    """

    base_url: str | None = None
    headers: dict[str, str] | None = None
    api_key: str | None = None


@dataclass
class CompactionConfig:
    """Configuration for session history compaction.

    Controls when and how conversation history is summarized to save context tokens.
    """

    enabled: bool = True
    context_window_tokens: int = 128000
    reserve_tokens: int = 16384
    keep_recent_tokens: int = 20000


@dataclass
class ProvidersConfig:
    """Map of provider name to provider settings."""

    providers: dict[str, ProviderSettings] = field(default_factory=dict)

    def get(self, provider: str) -> ProviderSettings | None:
        """Get settings for a specific provider."""
        return self.providers.get(provider)

    def set(self, provider: str, settings: ProviderSettings) -> None:
        """Set settings for a provider."""
        self.providers[provider] = settings


@dataclass
class PyFlueConfig:
    """Runtime configuration for one PyFlue agent."""

    model: str | None = None
    harness: str = "deepagents"
    sandbox: str = "virtual"
    python_backend: str | None = None
    root: Path = field(default_factory=Path.cwd)
    skills_dir: Path | None = None
    roles_dir: Path | None = None
    agents_dir: Path | None = None
    state_dir: Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    allowed_commands: tuple[str, ...] = ()
    allow_compound_commands: bool = False
    typed_retries: int = 3
    harness_config: dict[str, Any] = field(default_factory=dict)
    providers: ProvidersConfig = field(default_factory=ProvidersConfig)
    compaction: CompactionConfig = field(default_factory=CompactionConfig)
    mcp: McpConfig | None = None


@dataclass
class HarnessResult:
    """Normalized response from a harness backend."""

    text: str
    raw: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PyFlueEvent:
    """Normalized event emitted by PyFlue streaming APIs."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)


PyFlueEventCallback = Callable[[PyFlueEvent], Any]


@dataclass(frozen=True)
class PyFlueCommand:
    """Reusable command exposed to prompts as a named tool.

    A command can wrap a shell command string or a Python callable. Shell
    commands run through the active session sandbox and policy. Callable
    commands receive keyword arguments from the harness.
    """

    name: str
    description: str = ""
    command: str | None = None
    callable: Callable[..., Any] | None = None
    schema: dict[str, Any] | None = None
    cwd: str | None = None
    env: dict[str, str] | None = None
    timeout: int | None = 120


@dataclass
class McpServerConfig:
    """Configuration for an MCP server connection."""

    name: str
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    transport: Literal["streamable-http", "sse"] = "streamable-http"
    headers: dict[str, str] | None = None


McpMode = Literal["direct", "search_execute"]


@dataclass
class AgentInfo:
    """Discovered agent information from the workspace."""

    name: str
    file_path: Path
    triggers: dict[str, bool | str] = field(default_factory=dict)


@dataclass
class BuildContext:
    """Context passed to build plugins."""

    agents: list[AgentInfo]
    roles: dict[str, Role]
    workspace_dir: Path
    output_dir: Path
    config: PyFlueConfig


class BuildPlugin:
    """Plugin for generating deployment artifacts.

    Similar to Flue's BuildPlugin but adapted for Python deployments.
    """

    name: str

    def generate_entry_point(self, ctx: BuildContext) -> str:
        """Generate the main server entry point code."""
        raise NotImplementedError

    def additional_outputs(self, ctx: BuildContext) -> dict[str, str]:
        """Generate additional files (Dockerfile, config, etc.)."""
        return {}

    def esbuild_options(self, ctx: BuildContext) -> dict[str, Any]:
        """Options for bundling (if applicable)."""
        return {}


@dataclass
class McpConfig:
    """MCP configuration for the agent."""

    servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    mode: McpMode = "direct"
    search_limit: int = 10
    search_backend: Literal["bm25", "semantic"] = "bm25"


@dataclass
class BuildOptions:
    """Options for building a PyFlue workspace."""

    workspace_dir: str | Path
    output_dir: str | Path
    target: Literal["uvicorn", "lambda", "docker", "cloudrun"] | None = None
    plugin: BuildPlugin | None = None
    config_path: str | Path | None = None
