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


@pytest.mark.asyncio
async def test_route_context_init_reuses_python_config_path(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "default.py").write_text(
        "async def default(context):\n"
        "    agent = await context.init()\n"
        "    return {'harness': agent.config.harness, 'config_path': str(agent.config.config_path)}\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "pyflue.config.py"
    config_path.write_text(
        "config = {'agent': {'harness': 'pydanticai'}}\n",
        encoding="utf-8",
    )

    routes = discover_agent_routes(tmp_path)
    result = await invoke_route(routes["default"], agent_id="abc", config_path=config_path)

    assert result["harness"] == "pydanticai"
    assert result["config_path"] == str(config_path.resolve())
