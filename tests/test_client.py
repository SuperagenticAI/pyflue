from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from pyflue.client import PyFlueClient


class _Result(BaseModel):
    summary: str


@pytest.mark.asyncio
async def test_client_prompt_and_typed_result():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/prompt/s1"
        return httpx.Response(
            200,
            json={
                "text": '---RESULT_START---\n{"summary": "ok"}\n---RESULT_END---',
                "metadata": {"harness": "test"},
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        client = PyFlueClient("http://test", client=http)
        result = await client.prompt("hello", session_id="s1", result=_Result)

    assert result.summary == "ok"


@pytest.mark.asyncio
async def test_client_lists_agents_and_calls_agent_route():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agents" and request.method == "GET":
            return httpx.Response(200, json={"agents": [{"name": "default"}]})
        if request.url.path == "/agents/default/abc" and request.method == "POST":
            return httpx.Response(200, json={"ok": True, "payload": json.loads(request.content)})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        client = PyFlueClient("http://test", client=http)
        agents = await client.agents()
        response = await client.agent("default", "abc", payload={"x": 1})

    assert agents == [{"name": "default"}]
    assert response["ok"] is True
    assert response["payload"] == {"payload": {"x": 1}}


@pytest.mark.asyncio
async def test_client_stream_parses_sse_events():
    async def handler(request: httpx.Request) -> httpx.Response:
        content = (
            'event: start\ndata: {"session_id": "s1"}\n\n'
            'event: delta\ndata: {"text": "hi"}\n\n'
        )
        return httpx.Response(200, content=content)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        client = PyFlueClient("http://test", client=http)
        events = [event async for event in client.stream("hello", session_id="s1")]

    assert [event.type for event in events] == ["start", "delta"]
    assert events[1].data["text"] == "hi"
