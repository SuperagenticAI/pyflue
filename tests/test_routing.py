from __future__ import annotations

import pytest

from pyflue.routing import discover_agent_routes, invoke_route


@pytest.mark.asyncio
async def test_file_based_agent_route_invokes_handler(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "hello.py").write_text(
        "triggers = {'webhook': True}\n"
        "async def default(context):\n"
        "    return {'agent_id': context.agent_id, 'message': context.payload['message']}\n",
        encoding="utf-8",
    )

    routes = discover_agent_routes(tmp_path)
    result = await invoke_route(
        routes["hello"],
        agent_id="abc",
        payload={"message": "hi"},
        config_path=tmp_path / "missing.toml",
    )

    assert result["agent_id"] == "abc"
    assert result["message"] == "hi"
    assert result["_meta"]["run_id"].startswith("run_")
    assert routes["hello"].triggers == {"webhook": True}
