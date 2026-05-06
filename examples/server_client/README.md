# Server And Client Smoke Demo

This example verifies the deployed-server path without requiring a model key.
It creates a temporary PyFlue workspace, mounts the FastAPI app in-process, and
calls it through `PyFlueClient`.

Run from the repository root:

```bash
uv run python examples/server_client/run_smoke.py
```

Expected output:

```text
health.ok=True
agents=default
agent.message=hello from client
```

