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
