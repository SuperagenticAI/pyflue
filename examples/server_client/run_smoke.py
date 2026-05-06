from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import httpx

from pyflue import PyFlueClient
from pyflue.server import create_app

AGENT_CODE = """triggers = {"webhook": True}


async def default(context):
    return {
        "agent_id": context.agent_id,
        "message": context.payload.get("message"),
    }
"""


async def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "agents").mkdir()
        (root / "agents" / "default.py").write_text(AGENT_CODE, encoding="utf-8")
        (root / "pyflue.toml").write_text(
            '[agent]\nharness = "deepagents"\nsandbox = "virtual"\n',
            encoding="utf-8",
        )

        app = create_app(root / "pyflue.toml")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            client = PyFlueClient("http://test", client=http)
            health = await client.health()
            agents = await client.agents()
            result = await client.agent(
                "default",
                "demo",
                payload={"message": "hello from client"},
            )

        names = ",".join(agent["name"] for agent in agents)
        print(f"health.ok={health['ok']}")
        print(f"agents={names}")
        print(f"agent.message={result['message']}")


if __name__ == "__main__":
    asyncio.run(main())
