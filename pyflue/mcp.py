"""MCP (Model Context Protocol) client support for PyFlue."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

McpTransport = Literal["streamable-http", "sse"]

if TYPE_CHECKING:
    from mcp import ClientSession
    from mcp.types import Tool as McpTool

    from pyflue.search import BM25Search

def _get_mcp_client():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client as mcp_stdio_client
    return ClientSession, StdioServerParameters, mcp_stdio_client

def _get_mcp_types():
    from mcp.types import Tool as McpTool
    return McpTool

try:
    from pyflue.search import BM25Search
    _HAS_SEARCH = True
except ImportError:
    _HAS_SEARCH = False
    BM25Search = None


@dataclass
class MCPToolMatch:
    """A matched MCP tool with search metadata."""

    server: str
    name: str
    description: str
    input_schema: dict[str, Any]
    transport: str
    original_name: str
    score: float = 0.0


@dataclass
class McpServerOptions:
    """Options for connecting to an MCP server."""

    url: str
    transport: McpTransport = "streamable-http"
    headers: dict[str, str] | None = None
    request_init: dict[str, Any] | None = None
    client_name: str | None = None
    client_version: str | None = None


@dataclass
class McpStdioServerOptions:
    """Options for connecting to an MCP server via stdio."""

    command: str
    args: list[str] | None = None
    env: dict[str, str] | None = None


@dataclass
class McpToolDef:
    """A tool definition exposed to the agent."""

    name: str
    description: str
    parameters: dict[str, Any]
    execute: Any


@dataclass 
class McpServerConnection:
    """A connection to an MCP server with its tools."""

    name: str
    tools: list[McpToolDef]
    close: Any


async def connect_mcp_server(
    name: str,
    options: McpServerOptions,
) -> McpServerConnection:
    """Connect to an MCP server over HTTP (streamable-http or SSE)."""
    import httpx
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamable_http_client

    ClientSession, _, _ = _get_mcp_client()

    stack = AsyncExitStack()
    try:
        if options.transport == "sse":
            read_stream, write_stream = await stack.enter_async_context(
                sse_client(options.url, headers=options.headers or None)
            )
        else:
            client = httpx.AsyncClient(headers=options.headers or {})
            stack.push_async_callback(client.aclose)
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(
                    options.url,
                    http_client=client,
                )
            )
        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()

        result = await session.list_tools()
        tools = _create_mcp_tools(name, session, result.tools)

        return McpServerConnection(
            name=name,
            tools=tools,
            close=stack.aclose,
        )
    except Exception:
        await stack.aclose()
        raise


async def connect_mcp_server_stdio(
    name: str,
    options: McpStdioServerOptions,
) -> McpServerConnection:
    """Connect to an MCP server via stdio."""
    import os

    ClientSession, StdioServerParameters, mcp_stdio_client = _get_mcp_client()

    server_params = StdioServerParameters(
        command=options.command,
        args=options.args or [],
        env=options.env,
    )

    stack = AsyncExitStack()
    try:
        devnull = stack.enter_context(Path(os.devnull).open("w"))  # noqa: SIM115
        read_stream, write_stream = await stack.enter_async_context(
            mcp_stdio_client(server_params, errlog=devnull)
        )
        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()

        result = await session.list_tools()
        tools = _create_mcp_tools(name, session, result.tools)

        return McpServerConnection(
            name=name,
            tools=tools,
            close=stack.aclose,
        )
    except Exception:
        await stack.aclose()
        raise


connectMcpServer = connect_mcp_server
connectMcpServerStdio = connect_mcp_server_stdio


def _create_mcp_tools(server_name: str, session: ClientSession, tools: list[McpTool]) -> list[McpToolDef]:
    """Convert MCP tools to PyFlue tool definitions."""
    names: set[str] = set()

    return [
        _create_mcp_tool(server_name, session, tool, names)
        for tool in tools
    ]


def _create_mcp_tool(
    server_name: str,
    session: ClientSession,
    tool: McpTool,
    names: set[str],
) -> McpToolDef:
    """Convert a single MCP tool to a PyFlue tool definition."""
    tool_name = _create_tool_name(server_name, tool.name)
    if tool_name in names:
        raise ValueError(
            f"[pyflue] MCP tools from server '{server_name}' produced duplicate tool name '{tool_name}'."
        )
    names.add(tool_name)

    return McpToolDef(
        name=tool_name,
        description=_create_tool_description(server_name, tool),
        parameters=_normalize_input_schema(tool.inputSchema),
        execute=_create_execute_fn(session, tool.name),
    )


def _create_tool_name(server_name: str, tool_name: str) -> str:
    """Create a namespaced tool name for MCP tools."""
    return f"mcp__{_sanitize_tool_name(server_name)}__{_sanitize_tool_name(tool_name)}"


def _sanitize_tool_name(value: str) -> str:
    """Sanitize a tool name part."""
    sanitized = re.sub(r"[^A-Za-z0-9_-]", "_", value).strip("_")
    return sanitized or "unnamed"


def _create_tool_description(server_name: str, tool: McpTool) -> str:
    """Create a description for an MCP tool."""
    parts = [f'MCP tool "{tool.name}" from server "{server_name}".']
    if tool.description:
        parts.append(tool.description)
    return " ".join(parts)


def _normalize_input_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize MCP input schema to JSON Schema compatible format."""
    if schema is None:
        return {"type": "object", "properties": {}}
    return {
        "type": schema.get("type", "object"),
        "properties": schema.get("properties", {}),
        "required": schema.get("required"),
    }


def _create_execute_fn(session: ClientSession, tool_name: str):
    """Create an execute function for an MCP tool."""
    async def execute(args: dict[str, Any], signal: Any = None) -> str:
        if signal is not None and hasattr(signal, "aborted") and signal.aborted:
            raise ValueError("Operation aborted")

        result = await session.call_tool(
            tool_name,
            args,
            timeout=None,
        )

        return _format_mcp_result(result)

    return execute


def _format_mcp_result(result: Any) -> str:
    """Format MCP tool result for the LLM."""
    # Handle structured content
    if hasattr(result, "structuredContent") and result.structuredContent:
        import json

        return f"Structured content:\n{json.dumps(result.structuredContent, indent=2)}"

    # Handle content items
    parts: list[str] = []
    content = getattr(result, "content", None) or []

    for item in content:
        if item.type == "text":
            parts.append(item.text or "")
        elif item.type == "image":
            parts.append(f"[Image: {item.mimeType}, {len(item.data)} base64 chars]")
        elif item.type == "audio":
            parts.append(f"[Audio: {item.mimeType}, {len(item.data)} base64 chars]")
        elif item.type == "resource":
            resource = item.resource
            if hasattr(resource, "text"):
                parts.append(f"[Resource: {resource.uri}]\n{resource.text}")
            else:
                parts.append(f"[Resource: {resource.uri}, {len(resource.blob)} base64 chars]")
        elif item.type == "resource_link":
            desc = f" - {item.description}" if item.description else ""
            parts.append(f"[Resource link: {item.name} ({item.uri}){desc}]")

    if parts:
        return "\n\n".join(parts)

    # Fallback
    if hasattr(result, "toolResult"):
        import json

        return json.dumps(result.toolResult, indent=2)

    return "(MCP tool returned no content)"


async def load_mcp_servers(config: dict[str, Any] | None) -> dict[str, McpServerConnection]:
    """Load MCP servers from configuration."""
    if not config:
        return {}

    servers: dict[str, McpServerConnection] = {}

    for name, server_config in config.items():
        if isinstance(server_config, dict):
            if "command" in server_config:
                options = McpStdioServerOptions(
                    command=server_config["command"],
                    args=server_config.get("args"),
                    env=server_config.get("env"),
                )
                servers[name] = await connect_mcp_server_stdio(name, options)
            elif "url" in server_config:
                options = McpServerOptions(
                    url=server_config["url"],
                    transport=server_config.get("transport", "streamable-http"),
                    headers=server_config.get("headers"),
                )
                servers[name] = await connect_mcp_server(name, options)

    return servers


def _schema_to_dict(value: Any) -> dict[str, Any]:
    """Convert schema to dict."""
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _get_attr(item: Any, name: str, default: Any = None) -> Any:
    """Get attribute from dict or object."""
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _normalize_tool_content(result: Any) -> dict[str, Any]:
    """Normalize MCP tool call result."""
    if hasattr(result, "model_dump"):
        dumped = result.model_dump()
        if isinstance(dumped, dict):
            return dumped
    if isinstance(result, dict):
        return result
    content = _get_attr(result, "content", None)
    if content is not None:
        return {"content": content}
    return {"result": str(result)}


def _score_tool(query: str, tool: MCPToolMatch) -> float:
    """Score a tool against a query using keyword matching."""
    terms = {term.lower() for term in query.replace("_", " ").replace("-", " ").split() if term.strip()}
    if not terms:
        return 0.0

    haystack = " ".join([
        tool.server,
        tool.name.replace("_", " ").replace("-", " "),
        tool.description,
        json.dumps(tool.input_schema, sort_keys=True),
    ]).lower()

    score = 0.0
    for term in terms:
        if term == tool.name.lower():
            score += 5.0
        elif term in tool.name.lower().replace("_", " ").replace("-", " "):
            score += 3.0
        elif term in haystack:
            score += 1.0
    return score


class MCPClient:
    """Dynamic MCP client with search and execute capabilities."""

    def __init__(self, servers: dict[str, dict[str, Any]]):
        """Initialize MCP client with server configurations."""
        self._servers = servers
        self._tools_cache: list[MCPToolMatch] = []
        self._sessions: dict[str, Any] = {}

    def _enabled_servers(self) -> dict[str, dict[str, Any]]:
        """Get enabled servers."""
        return {
            name: dict(spec)
            for name, spec in self._servers.items()
            if bool(spec.get("enabled", True))
        }

    @asynccontextmanager
    async def _session(self, name: str, spec: dict[str, Any]):
        """Create a session for an MCP server."""
        transport = str(spec.get("transport") or ("stdio" if spec.get("command") else "streamable-http")).lower()

        if transport == "stdio":
            command_parts = shlex.split(str(spec.get("command") or ""))
            if not command_parts:
                raise RuntimeError(f"MCP stdio server has no command: {name}")

            env = dict(os.environ)
            env.update({str(k): str(v) for k, v in (spec.get("env") or {}).items()})

            ClientSession, StdioServerParameters, mcp_stdio_client = _get_mcp_client()

            args = [*command_parts[1:], *(str(item) for item in spec.get("args", []))]
            params = StdioServerParameters(command=command_parts[0], args=args, env=env)
            with open(os.devnull, "w") as devnull:
                async with mcp_stdio_client(params, errlog=devnull) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        yield session
            return

        headers = {str(k): str(v) for k, v in (spec.get("headers") or {}).items()}
        url = str(spec.get("url") or "")

        if transport in ("http", "streamable-http"):
            import httpx
            from mcp.client.streamable_http import streamable_http_client

            ClientSession, _, _ = _get_mcp_client()

            client = httpx.AsyncClient(headers=headers or {})
            try:
                async with streamable_http_client(url, http_client=client) as (read, write, _), ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
            finally:
                await client.aclose()
            return

        if transport == "sse":
            from mcp.client.sse import sse_client

            ClientSession, _, _ = _get_mcp_client()

            async with sse_client(url, headers=headers or None) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                yield session
            return

        raise RuntimeError(f"Unsupported MCP transport for {name}: {transport}")

    async def list_tools_async(self, server: str | None = None) -> list[MCPToolMatch]:
        """List all available tools from MCP servers."""
        matches: list[MCPToolMatch] = []

        for name, spec in self._enabled_servers().items():
            if server is not None and name != server:
                continue

            async with self._session(name, spec) as session:
                response = await session.list_tools()
                tools = _get_attr(response, "tools", response if isinstance(response, list) else [])

                for tool in tools or []:
                    matches.append(
                        MCPToolMatch(
                            server=name,
                            name=_create_tool_name(name, _get_attr(tool, "name", "")),
                            original_name=str(_get_attr(tool, "name", "")),
                            description=str(_get_attr(tool, "description", "") or ""),
                            input_schema=_schema_to_dict(_get_attr(tool, "inputSchema", _get_attr(tool, "input_schema", {}))),
                            transport=str(spec.get("transport") or ""),
                        )
                    )

        self._tools_cache = matches
        return matches

    def list_tools(self, server: str | None = None) -> list[MCPToolMatch]:
        """Synchronous wrapper for list_tools_async."""
        return asyncio.run(self.list_tools_async(server))

    def search_tools(
        self,
        *,
        query: str,
        limit: int = 10,
        server: str | None = None,
        use_bm25: bool = True,
    ) -> list[MCPToolMatch]:
        """Search tools using keyword matching or BM25."""
        if not self._tools_cache:
            self.list_tools(server=server)

        tools = self._tools_cache
        if server:
            tools = [t for t in tools if t.server == server]

        if use_bm25 and _HAS_SEARCH:
            return BM25Search.search(tools, query, limit)

        for tool in tools:
            tool.score = _score_tool(query, tool)

        if query.strip():
            tools = [tool for tool in tools if tool.score > 0.0]

        tools.sort(key=lambda t: (t.score, t.server, t.name), reverse=True)
        return tools[:max(limit, 0)]

    async def call_tool_async(
        self,
        *,
        server: str,
        tool: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Call a tool on an MCP server."""
        spec = self._enabled_servers().get(server)
        if spec is None:
            raise RuntimeError(f"MCP server is not configured or enabled: {server}")

        async with self._session(server, spec) as session:
            result = await session.call_tool(tool, arguments)
            return _normalize_tool_content(result)

    def call_tool(
        self,
        *,
        server: str,
        tool: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Synchronous wrapper for call_tool_async."""
        return asyncio.run(self.call_tool_async(server=server, tool=tool, arguments=arguments))

    async def load_index(self) -> None:
        """Pre-load tool index for faster searches."""
        await self.list_tools_async()
