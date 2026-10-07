"""Search the web and optionally fetch a page through PyFlue's MCP client."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from pyflue.mcp import MCPClient

SERVERS = {
    "parallel": {
        "url": "https://search.parallel.ai/mcp",
        "transport": "streamable-http",
        "headers": {"User-Agent": "pyflue-parallel-search-example/1.0"},
    }
}


async def run(query: str, url: str | None = None) -> None:
    client = MCPClient(SERVERS)
    tools = await client.list_tools_async()
    available = {tool.original_name for tool in tools}
    required = {"web_search", "web_fetch"} if url else {"web_search"}
    if missing := required - available:
        raise RuntimeError(f"Parallel MCP is missing tools: {', '.join(sorted(missing))}")

    session_id = uuid.uuid4().hex
    calls = [
        ("web_search", {"objective": query, "search_queries": [query]}),
    ]
    if url:
        calls.append(("web_fetch", {"urls": [url], "search_queries": [query]}))

    for tool, arguments in calls:
        result = await client.call_tool_async(
            server="parallel",
            tool=tool,
            arguments={**arguments, "session_id": session_id},
        )
        if result.get("isError"):
            raise RuntimeError(f"{tool} failed: {json.dumps(result)}")
        print(f"{tool}:\n{json.dumps(result, indent=2)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="A focused search query, ideally 3–6 words")
    parser.add_argument("--url", help="An HTTP(S) page to fetch after searching")
    args = parser.parse_args()
    asyncio.run(run(args.query, args.url))


if __name__ == "__main__":
    main()
