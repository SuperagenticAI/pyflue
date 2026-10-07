# Parallel Search MCP

Run anonymous web search and page extraction through PyFlue's supported
`MCPClient`, using Streamable HTTP. No Parallel API key or model key is needed:
this example calls tools programmatically and prints their MCP responses.
It does not run an LLM or change PyFlue's defaults.

From the repository root, install PyFlue in a fresh environment and run:

```bash
uv venv /tmp/pyflue-parallel
uv pip install --python /tmp/pyflue-parallel/bin/python .
/tmp/pyflue-parallel/bin/python examples/parallel_search/search.py \
  "Python asyncio task cancellation" --url https://docs.python.org/3/library/asyncio-task.html
```

`web_search` returns source URLs and excerpts. The optional `--url` calls
`web_fetch` on a page you choose; it does not automatically select a search result.
Omit it for search only. The example discovers tools before calling them, reuses
one session ID across search and fetch, and reports MCP tool errors as failures.

The endpoint is `https://search.parallel.ai/mcp`. Requests identify this example
with `User-Agent: pyflue-parallel-search-example/1.0` and send no authorization
header. Anonymous access has lower rate limits; free access is not unlimited.
If you later attach MCP tools to a model, model inference is separate.
See the [Parallel Search MCP documentation](https://docs.parallel.ai/integrations/mcp/search-mcp)
for current schemas and service limits.
