from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from pyflue.client import PyFlueClient, create_flue_client, createFlueClient


class _Result(BaseModel):
    summary: str


def test_create_flue_client_factory_aliases():
    from pyflue import create_flue_client as root_create_flue_client_snake
    from pyflue import createFlueClient as root_create_flue_client

    client = create_flue_client("http://test", adminBasePath="/ops")
    try:
        assert isinstance(client, PyFlueClient)
        assert client.base_url == "http://test"
        assert client.admin_base_path == "/ops"
    finally:
        client._owns_client = False

    camel = createFlueClient(baseUrl="http://camel")
    try:
        assert camel.base_url == "http://camel"
    finally:
        camel._owns_client = False

    assert root_create_flue_client is create_flue_client
    assert root_create_flue_client_snake is create_flue_client


@pytest.mark.asyncio
async def test_create_flue_client_defaults_to_flue_agent_response_shape():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-webhook") == "true":
            return httpx.Response(
                202,
                json={"status": "accepted", "run_id": "run_1", "runId": "run_1"},
            )
        return httpx.Response(
            200,
            json={"ok": True, "_meta": {"run_id": "run_2", "runId": "run_2"}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        client = create_flue_client("http://test", client=http)
        sync_response = await client.agents.invoke("default", "abc", {"mode": "sync"})
        webhook_response = await client.agents.invoke("default", "abc", {"mode": "webhook"})

    assert sync_response == {"result": {"ok": True}, "runId": "run_2"}
    assert webhook_response == {"runId": "run_1"}


@pytest.mark.asyncio
async def test_create_flue_client_shapes_persistent_agent_response_without_run_id():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"result": {"text": "hello", "usage": {}, "model": {"id": "test"}}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        client = create_flue_client("http://test", client=http)
        response = await client.agents.invoke("assistant", "thread-1", {"mode": "sync"})

    assert response == {"result": {"text": "hello", "usage": {}, "model": {"id": "test"}}}


@pytest.mark.asyncio
async def test_agent_response_shape_can_be_overridden_to_raw():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "_meta": {"run_id": "run_2", "runId": "run_2"}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        client = create_flue_client("http://test", client=http)
        raw = await client.agents.invoke("default", "abc", {"mode": "sync", "responseFormat": "raw"})

    assert raw == {"ok": True, "_meta": {"run_id": "run_2", "runId": "run_2"}}


@pytest.mark.asyncio
async def test_client_prompt_and_typed_result():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/prompt/s1"
        return httpx.Response(
            200,
            json={
                "text": '---RESULT_START---\n{"summary": "ok"}\n---RESULT_END---',
                "metadata": {"harness": "test"},
                "usage": {"input": 2, "output": 3, "total_tokens": 5, "cost": {"total": 0.01}},
                "model": {"id": "test-model"},
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        client = PyFlueClient("http://test", client=http)
        result = await client.prompt("hello", session_id="s1", result=_Result)

    assert result.summary == "ok"
    assert result.result.summary == "ok"
    assert result.usage.total_tokens == 5
    assert result.model.id == "test-model"


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
async def test_client_agent_namespace_supports_invoke_modes():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/agents/default/abc" and request.headers.get("x-webhook") == "true":
            return httpx.Response(202, json={"status": "accepted", "run_id": "run_1", "runId": "run_1"})
        if request.url.path == "/agents/default/abc":
            return httpx.Response(200, json={"ok": True, "_meta": {"run_id": "run_2", "runId": "run_2"}})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        client = PyFlueClient("http://test", client=http)
        sync_response = await client.agents.invoke("default", "abc", payload={"x": 1})
        webhook_response = await client.agents.invoke("default", "abc", mode="webhook")

    assert sync_response["ok"] is True
    assert webhook_response["status"] == "accepted"
    assert requests[-1].headers["x-webhook"] == "true"


@pytest.mark.asyncio
async def test_client_agent_namespace_supports_flue_options_dict():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/agents/default/abc" and request.headers.get("x-webhook") == "true":
            return httpx.Response(202, json={"status": "accepted", "run_id": "run_1", "runId": "run_1"})
        if request.url.path == "/agents/default/abc":
            return httpx.Response(200, json={"ok": True, "_meta": {"run_id": "run_2", "runId": "run_2"}})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        client = PyFlueClient("http://test", client=http)
        sync_response = await client.agents.invoke(
            "default",
            "abc",
            {"mode": "sync", "payload": {"x": 1}},
        )
        webhook_response = await client.agents.invoke("default", "abc", {"mode": "webhook"})

    assert sync_response["ok"] is True
    assert json.loads(requests[0].content) == {"payload": {"x": 1}}
    assert webhook_response["runId"] == "run_1"
    assert requests[-1].headers["x-webhook"] == "true"


@pytest.mark.asyncio
async def test_client_agent_namespace_streams_route_events():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agents/default/abc"
        assert request.headers["accept"] == "text/event-stream"
        content = (
            'id: 1\nevent: run_start\ndata: {"type": "run_start", "run_id": "run_1"}\n\n'
            'id: 2\nevent: run_end\ndata: {"type": "run_end", "run_id": "run_1"}\n\n'
        )
        return httpx.Response(200, content=content)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        client = PyFlueClient("http://test", client=http)
        events = [event async for event in client.agents.stream("default", "abc")]

    assert [event.type for event in events] == ["run_start", "run_end"]
    assert events[0].data["run_id"] == "run_1"


@pytest.mark.asyncio
async def test_client_agent_invoke_stream_options_dict_is_async_iterable():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agents/default/abc"
        assert request.headers["accept"] == "text/event-stream"
        assert json.loads(request.content) == {"payload": {"x": 1}}
        content = (
            'id: 1\nevent: run_start\ndata: {"type": "run_start", "run_id": "run_1"}\n\n'
            'id: 2\nevent: run_end\ndata: {"type": "run_end", "run_id": "run_1"}\n\n'
        )
        return httpx.Response(200, content=content)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        client = PyFlueClient("http://test", client=http)
        events = [
            event
            async for event in client.agents.invoke(
                "default",
                "abc",
                {"mode": "stream", "payload": {"x": 1}},
            )
        ]

    assert [event.type for event in events] == ["run_start", "run_end"]


@pytest.mark.asyncio
async def test_client_runs_namespace_fetches_and_streams():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/runs/run_1":
            return httpx.Response(200, json={"run_id": "run_1"})
        if request.url.path == "/runs/run_1/events":
            assert request.url.params["types"] == "log,run_end"
            return httpx.Response(200, json={"events": []})
        if request.url.path == "/runs/run_1/stream":
            return httpx.Response(200, content='event: run_end\ndata: {"type": "run_end"}\n\n')
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        client = PyFlueClient("http://test", client=http)
        run = await client.runs.get("run_1")
        events = await client.runs.events("run_1", types=["log", "run_end"])
        streamed = [event async for event in client.runs.stream("run_1")]

    assert run["run_id"] == "run_1"
    assert events["events"] == []
    assert streamed[0].type == "run_end"


@pytest.mark.asyncio
async def test_client_admin_namespace_uses_flue_style_paths():
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/admin/agents":
            assert request.url.params.get("limit") is None
            return httpx.Response(200, json={"items": [{"name": "hello"}], "nextCursor": None})
        if request.url.path == "/admin/agents/hello/instances":
            assert request.url.params.get("cursor") is None
            return httpx.Response(200, json={"items": [{"instanceId": "abc"}], "nextCursor": None})
        if request.url.path == "/admin/runs":
            assert request.url.params["limit"] == "10"
            assert request.url.params["cursor"] == "20"
            return httpx.Response(200, json={"items": [{"runId": "run_1"}], "nextCursor": None})
        if request.url.path == "/admin/agents/hello/instances/abc/runs":
            assert request.url.params["status"] == "errored"
            return httpx.Response(200, json={"items": [{"runId": "run_2"}], "nextCursor": None})
        if request.url.path == "/admin/runs/run_1":
            return httpx.Response(200, json={"runId": "run_1"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        client = PyFlueClient("http://test", client=http)
        agents = await client.admin.agents.list()
        instances = await client.admin.instances.list("hello")
        runs = await client.admin.runs.list(limit=10, cursor="20")
        instance_runs = await client.admin.runs.list_for_instance("hello", "abc", status="errored")
        run = await client.admin.runs.get("run_1")

    assert agents["items"][0]["name"] == "hello"
    assert instances["items"][0]["instanceId"] == "abc"
    assert runs["items"][0]["runId"] == "run_1"
    assert instance_runs["items"][0]["runId"] == "run_2"
    assert run["runId"] == "run_1"
    assert seen[0].startswith("http://test/admin/agents")


@pytest.mark.asyncio
async def test_client_admin_base_path_can_be_empty():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agents"
        return httpx.Response(200, json={"items": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        client = PyFlueClient("http://test", client=http, admin_base_path="")
        agents = await client.admin.agents.list()

    assert agents["items"] == []


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
