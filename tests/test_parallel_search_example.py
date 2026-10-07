from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest


@pytest.mark.parametrize("fetch,error", [(False, False), (True, False), (False, True)])
async def test_parallel_example_uses_anonymous_http_mcp(monkeypatch, capsys, fetch, error):
    spec = importlib.util.spec_from_file_location(
        "parallel_search_example", Path("examples/parallel_search/search.py")
    )
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    requests = []

    async def handle(request):
        assert str(request.url) == "https://search.parallel.ai/mcp"
        assert request.headers["User-Agent"] == "pyflue-parallel-search-example/1.0"
        assert "Authorization" not in request.headers
        if request.method == "GET":
            return httpx.Response(405)
        body = json.loads(request.content)
        requests.append(body)
        if "id" not in body:
            return httpx.Response(202)
        if body["method"] == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-parallel", "version": "1"},
            }
        elif body["method"] == "tools/list":
            result = {"tools": [
                {"name": name, "inputSchema": {"type": "object"}}
                for name in ("web_search", "web_fetch")
            ]}
        else:
            assert body["method"] == "tools/call"
            result = {
                "content": [{"type": "text", "text": "source URL and useful excerpt"}],
                "isError": error,
            }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    class TestClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", TestClient)
    if error:
        with pytest.raises(RuntimeError, match="web_search failed"):
            await example.run("Python asyncio cancellation")
        return
    url = "https://docs.python.org/3/library/asyncio-task.html" if fetch else None
    await example.run("Python asyncio cancellation", url)
    calls = [r["params"] for r in requests if r["method"] == "tools/call"]
    assert calls[0]["name"] == "web_search"
    assert calls[0]["arguments"]["search_queries"] == ["Python asyncio cancellation"]
    if fetch:
        assert calls[1]["name"] == "web_fetch"
        assert calls[1]["arguments"]["urls"] == [url]
        assert calls[1]["arguments"]["session_id"] == calls[0]["arguments"]["session_id"]
    else:
        assert len(calls) == 1
    assert "useful excerpt" in capsys.readouterr().out
