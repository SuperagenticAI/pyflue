"""Sandbox provider registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pyflue.sandboxes.base import SandboxBackend, SandboxPolicy
from pyflue.sandboxes.daytona import DaytonaSandbox
from pyflue.sandboxes.e2b import E2BSandbox
from pyflue.sandboxes.modal import ModalSandbox
from pyflue.sandboxes.runloop import RunloopSandbox
from pyflue.sandboxes.virtual import VirtualSandbox


def create_sandbox(
    name: Any,
    *,
    root: str | Path | None = None,
    policy: SandboxPolicy | None = None,
    env: dict[str, str] | None = None,
    config: dict[str, Any] | None = None,
) -> SandboxBackend:
    """Create a sandbox provider by name, or from a factory callable.

    ``name`` may be a provider string, or a factory callable such as the one
    returned by :func:`pyflue.sandboxes.local`, which builds a host-bound
    sandbox with explicit env exposure.
    """
    if callable(name):
        return name(root=root, policy=policy, env=env, config=config)
    env = env or {}
    config = config or {}
    normalized = name.replace("_", "-").lower()
    if normalized in {"virtual", "local"}:
        return VirtualSandbox(root=root, policy=policy, env=dict(config.get("env", {})))
    if normalized == "daytona":
        return DaytonaSandbox(
            api_key=env.get("DAYTONA_API_KEY"),
            policy=policy,
            workspace=str(config.get("workspace", "/workspace")),
            options=dict(config.get("options", {})),
        )
    if normalized == "e2b":
        return E2BSandbox(
            api_key=env.get("E2B_API_KEY"),
            policy=policy,
            workspace=str(config.get("workspace", "/workspace")),
            options=dict(config.get("options", {})),
        )
    if normalized == "modal":
        return ModalSandbox(
            policy=policy,
            workspace=str(config.get("workspace", "/workspace")),
            options=dict(config.get("options", {})),
        )
    if normalized == "runloop":
        return RunloopSandbox(
            api_key=env.get("RUNLOOP_API_KEY"),
            policy=policy,
            workspace=str(config.get("workspace", "/workspace")),
            options=dict(config.get("options", {})),
        )
    raise ValueError(f"Unknown sandbox provider: {name}")
