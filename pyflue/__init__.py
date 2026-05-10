"""PyFlue public API."""

__version__ = "0.1.4"

from pyflue.client import PyFlueClient
from pyflue.core import PyFlueAgent, PyFlueSession, PyFlueSessions, init
from pyflue.harnesses.registry import register_harness
from pyflue.mcp import (
    MCPClient,
    McpServerConnection,
    McpStdioServerOptions,
    connect_mcp_server,
    connect_mcp_server_stdio,
)
from pyflue.routing import AgentRoute, PyFlueContext, discover_agent_routes
from pyflue.search import BM25Search, SemanticSearch
from pyflue.skills import Role, Skill, load_roles, load_skills
from pyflue.types import (
    AgentInfo,
    BuildContext,
    BuildOptions,
    BuildPlugin,
    CompactionConfig,
    McpConfig,
    McpMode,
    McpServerConfig,
    PromptCost,
    PromptImage,
    PromptModel,
    PromptResultResponse,
    PromptUsage,
    ProvidersConfig,
    ProviderSettings,
    PyFlueCommand,
    PyFlueConfig,
    PyFlueEvent,
    PyFlueEventCallback,
    ThinkingLevel,
    define_command,
)

__all__ = [
    "AgentInfo",
    "BuildContext",
    "BuildOptions",
    "BuildPlugin",
    "MCPClient",
    "McpConfig",
    "McpMode",
    "McpServerConfig",
    "McpServerConnection",
    "McpStdioServerOptions",
    "PyFlueAgent",
    "PyFlueClient",
    "PyFlueCommand",
    "PyFlueContext",
    "PyFlueEvent",
    "PyFlueEventCallback",
    "PyFlueSession",
    "PyFlueSessions",
    "AgentRoute",
    "BM25Search",
    "CompactionConfig",
    "ProviderSettings",
    "ProvidersConfig",
    "PromptCost",
    "PromptImage",
    "PromptModel",
    "PromptResultResponse",
    "PromptUsage",
    "PyFlueConfig",
    "SemanticSearch",
    "Role",
    "Skill",
    "ThinkingLevel",
    "connect_mcp_server",
    "connect_mcp_server_stdio",
    "discover_agent_routes",
    "define_command",
    "init",
    "load_roles",
    "load_skills",
    "register_harness",
]
