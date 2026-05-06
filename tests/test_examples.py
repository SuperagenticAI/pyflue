from __future__ import annotations

import runpy


def test_server_client_smoke_example(capsys):
    runpy.run_path("examples/server_client/run_smoke.py", run_name="__main__")

    output = capsys.readouterr().out
    assert "health.ok=True" in output
    assert "agents=default" in output
    assert "agent.message=hello from client" in output
