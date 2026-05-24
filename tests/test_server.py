from __future__ import annotations

import httpx
import pytest

from pyflue.server import create_app


async def _client(root):
    app = create_app(root / "pyflue.toml")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_server_returns_typed_error_for_unknown_agent(tmp_path):
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")

    async with await _client(tmp_path) as client:
        response = await client.post("/agents/missing/demo", json={"payload": {}})

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["type"] == "agent_not_found"
    assert body["error"]["message"] == 'Agent "missing" is not registered.'


@pytest.mark.asyncio
async def test_server_rejects_non_json_body(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "default.py").write_text(
        "async def default(context):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")

    async with await _client(tmp_path) as client:
        response = await client.post(
            "/agents/default/demo",
            content="not json",
            headers={"content-type": "text/plain"},
        )

    assert response.status_code == 415
    assert response.json()["error"]["type"] == "unsupported_media_type"


@pytest.mark.asyncio
async def test_server_rejects_method_with_typed_error(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "default.py").write_text(
        "async def default(context):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")

    async with await _client(tmp_path) as client:
        response = await client.get("/agents/default/demo")

    assert response.status_code == 405
    assert response.json()["error"]["type"] == "method_not_allowed"


@pytest.mark.asyncio
async def test_server_rejects_non_webhook_agent(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "default.py").write_text(
        "triggers = {'webhook': False}\n"
        "async def default(context):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")

    async with await _client(tmp_path) as client:
        response = await client.post("/agents/default/demo", json={"payload": {}})

    assert response.status_code == 404
    assert response.json()["error"]["type"] == "agent_not_webhook"


@pytest.mark.asyncio
async def test_server_agent_route_webhook_mode_returns_accepted_run_id(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "default.py").write_text(
        "triggers = {'webhook': True}\n"
        "async def default(context):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")

    async with await _client(tmp_path) as client:
        response = await client.post(
            "/agents/default/demo",
            json={"payload": {}},
            headers={"x-webhook": "true"},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["run_id"].startswith("run_")
    assert body["runId"] == body["run_id"]
    assert response.headers["X-Flue-Run-Id"] == body["run_id"]


@pytest.mark.asyncio
async def test_server_agent_route_streams_run_events(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "default.py").write_text(
        "triggers = {'webhook': True}\n"
        "async def default(context):\n"
        "    await context.log.info('started')\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")

    client = await _client(tmp_path)
    async with client, client.stream(
        "POST",
        "/agents/default/demo",
        json={"payload": {}},
        headers={"accept": "text/event-stream"},
    ) as response:
        body = await response.aread()

    assert response.status_code == 200
    assert response.headers["X-Flue-Run-Id"].startswith("run_")
    text = body.decode()
    assert "event: run_start" in text
    assert "event: log" in text
    assert "event: run_end" in text


@pytest.mark.asyncio
async def test_server_mounts_admin_api(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "default.py").write_text(
        "triggers = {'webhook': True}\n"
        "async def default(context):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")

    async with await _client(tmp_path) as client:
        invoke = await client.post("/agents/default/demo", json={"payload": {}})
        assert invoke.status_code == 200
        assert invoke.headers["X-Flue-Run-Id"] == invoke.json()["_meta"]["run_id"]
        response = await client.get("/admin/runs")

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["runId"] == invoke.json()["_meta"]["run_id"]


@pytest.mark.asyncio
async def test_server_openapi_documents_public_routes(tmp_path):
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")

    async with await _client(tmp_path) as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    spec = response.json()
    assert spec["info"]["title"] == "PyFlue Agent Server"
    assert "HealthResponse" in spec["components"]["schemas"]
    assert "RunRecordResponse" in spec["components"]["schemas"]
    assert "ErrorEnvelope" in spec["components"]["schemas"]
    agent_post = spec["paths"]["/agents/{name}/{agent_id}"]["post"]
    assert "202" in agent_post["responses"]
    assert "text/event-stream" in agent_post["responses"]["200"]["content"]
    assert spec["paths"]["/runs/{run_id}/events"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("/RunEventListResponse")


@pytest.mark.asyncio
async def test_server_exposes_mounted_admin_openapi(tmp_path):
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")

    async with await _client(tmp_path) as client:
        response = await client.get("/admin/openapi.json")

    assert response.status_code == 200
    spec = response.json()
    assert spec["info"]["title"] == "PyFlue Admin"
    assert "AdminRunsResponse" in spec["components"]["schemas"]
    assert "RunRecordResponse" in spec["components"]["schemas"]
    assert "ErrorEnvelope" in spec["components"]["schemas"]
    assert "/runs" in spec["paths"]


@pytest.mark.asyncio
async def test_server_status_includes_route_and_workspace_details(tmp_path):
    agents = tmp_path / "agents"
    skills = tmp_path / ".agents" / "skills"
    agents.mkdir()
    skills.mkdir(parents=True)
    (agents / "default.py").write_text(
        "triggers = {'webhook': True}\n"
        "async def default(context):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (skills / "review.md").write_text("---\nname: review\n---\nReview.", encoding="utf-8")
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")

    async with await _client(tmp_path) as client:
        response = await client.get("/__pyflue/status")

    assert response.status_code == 200
    body = response.json()
    assert body["route_count"] == 1
    assert body["routes"][0]["name"] == "default"
    assert body["routes"][0]["mtime"] is not None
    assert body["skills"][0]["name"] == "review"
    assert body["config_mtime"] is not None
    assert body["active_sessions"] == []
