# MCP (Model Context Protocol) Support

PyFlue provides comprehensive MCP support, enabling agents to connect to external tools and services through the Model Context Protocol.

## Overview

MCP servers provide tools that agents can use to perform actions. When an MCP server exposes many tools, it can consume significant context tokens. PyFlue addresses this with two operation modes.

## Modes

### Direct Mode (Default)

In direct mode, all MCP tools are exposed directly to the agent. Each tool becomes available to the LLM with its name, description, and parameters.

```python
from pyflue import init

agent = await init(
    mcp_servers={
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        }
    }
)
```

**Use Direct mode when:**
- The MCP server exposes fewer than 50 tools
- You need simple, predictable tool access
- Context token usage is not a concern

### Search + Execute Mode (Opt-in)

In search_execute mode, PyFlue exposes only two tools that give the agent the ability to dynamically discover and call any MCP tool:

- **`mcp_search(query)`** - Search for relevant MCP tools using keywords or semantic similarity
- **`mcp_execute(server, tool, arguments)`** - Execute a specific tool on a specific server

The agent first searches to find the right tool, then executes it. This approach keeps context usage fixed at ~2 tools regardless of how many tools the MCP server actually provides.

```python
from pyflue import init

agent = await init(
    mcp_servers={
        "my-api": {
            "url": "http://localhost:3000/mcp",
            "transport": "streamable-http"
        }
    },
    mcp_mode="search_execute",
    mcp_search_limit=10,
    mcp_search_backend="bm25"
)
```

**Use Search + Execute mode when:**
- The MCP server exposes many tools (100+)
- You connect to multiple MCP servers
- Context window limits are a concern
- You want progressive tool discovery

## Search Backends

### BM25 (Default)

BM25 is a keyword-based ranking algorithm that scores tools based on term frequency and document length. It requires no external dependencies and works out of the box.

```python
agent = await init(
    mcp_servers={"...": "..."},
    mcp_mode="search_execute",
    mcp_search_backend="bm25"
)
```

### Semantic Search

Semantic search uses embeddings to find tools that are conceptually similar to the query, even if they don't share exact keywords. Requires the `sentence-transformers` package:

```bash
pip install sentence-transformers
```

```python
agent = await init(
    mcp_servers={"...": "..."},
    mcp_mode="search_execute",
    mcp_search_backend="semantic"
)
```

## MCP Server Configuration

### Stdio Servers

Run MCP servers as local processes:

```python
mcp_servers={
    "local-tools": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        "env": {"NODE_ENV": "production"},
        "transport": "stdio"
    }
}
```

The same configuration can be written in `pyflue.toml`:

```toml
[mcp]
mode = "direct"

[mcp.servers.local-tools]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
env = { NODE_ENV = "production" }
```

### HTTP Servers (streamable-http)

Connect to MCP servers over HTTP using the modern streamable-http transport:

```python
mcp_servers={
    "remote-api": {
        "url": "http://localhost:3000/mcp",
        "transport": "streamable-http",
        "headers": {"Authorization": "Bearer token123"}
    }
}
```

TOML form:

```toml
[mcp.servers.remote-api]
url = "http://localhost:3000/mcp"
transport = "streamable-http"

[mcp.servers.remote-api.headers]
Authorization = "Bearer token123"
```

### SSE Servers (Legacy)

For older MCP servers that use Server-Sent Events:

```python
mcp_servers={
    "legacy-server": {
        "url": "http://localhost:8080/sse",
        "transport": "sse"
    }
}
```

## Connecting One Server

For Flue-style code that connects one remote MCP server and attaches its tools
to an agent, use `connect_mcp_server(...)`. The camelCase alias
`connectMcpServer(...)` is also exported.

```python
from pyflue import McpServerOptions, connect_mcp_server, init

inventory = await connect_mcp_server(
    "inventory",
    McpServerOptions(
        url="https://example.com/mcp",
        headers={"Authorization": "Bearer token"},
    ),
)

try:
    agent = await init(tools=inventory.tools)
finally:
    await inventory.close()
```

## Using MCPClient Directly

For programmatic access to MCP servers, use the `MCPClient` class directly:

```python
from pyflue.mcp import MCPClient

# Create client with server configuration
client = MCPClient({
    "my-server": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    }
})

# List all available tools
tools = client.list_tools()
print(f"Found {len(tools)} tools")

# Search for relevant tools using BM25
results = client.search_tools(query="read file contents", limit=5)
for tool in results:
    print(f"{tool.name} (score: {tool.score})")

# Call a specific tool
result = client.call_tool(
    server="my-server",
    tool="read_file",
    arguments={"path": "/tmp/test.txt"}
)
```

## API Reference

### `init()` Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `mcp_servers` | `dict` | Map of server name to server configuration |
| `mcp_mode` | `"direct"` or `"search_execute"` | How to expose MCP tools |
| `mcp_search_limit` | `int` | Max tools to return in search (default: 10) |
| `mcp_search_backend` | `"bm25"` or `"semantic"` | Search algorithm to use |

Call `await agent.destroy()` when your process is shutting down to close direct
MCP connections cleanly.

### MCPClient Methods

| Method | Description |
|--------|-------------|
| `list_tools()` | Get all available tools from all servers |
| `list_tools_async()` | Async version of list_tools |
| `search_tools(query, limit, server)` | Search tools by query |
| `call_tool(server, tool, arguments)` | Execute a tool |
| `call_tool_async()` | Async version of call_tool |
| `load_index()` | Pre-load tool index for faster searches |

## Choosing a Mode

| Scenario | Mode |
|----------|------|
| MCP server with <50 tools | Direct |
| MCP server with 100+ tools | Search + Execute |
| Multiple MCP servers | Search + Execute |
| Simple, predictable tool access | Direct |
| Limited context window | Search + Execute |
| Need semantic tool matching | Search + Execute + semantic backend |
