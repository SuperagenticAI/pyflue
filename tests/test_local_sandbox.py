"""Tests for the host-bound local() sandbox (parity item 7)."""

from __future__ import annotations

import pytest

from pyflue import init, local
from pyflue.sandboxes import LocalSandbox, create_sandbox
from pyflue.sandboxes.base import SandboxPolicy


def test_create_sandbox_accepts_factory(tmp_path):
    sandbox = create_sandbox(local(cwd=tmp_path), policy=SandboxPolicy())
    assert isinstance(sandbox, LocalSandbox)
    assert sandbox.provider == "local"


def test_local_sandbox_uses_real_filesystem(tmp_path):
    sandbox = local(cwd=tmp_path)(policy=SandboxPolicy(allow_write=True))
    sandbox.write_file("notes/todo.txt", "hello")
    # The file really exists on disk (not in an isolated in-memory box).
    assert (tmp_path / "notes" / "todo.txt").read_text() == "hello"
    assert sandbox.read_file("notes/todo.txt") == "hello"
    assert sandbox.exists("notes/todo.txt")


def test_local_sandbox_shell_env_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("PYFLUE_TEST_SECRET", "leak")

    # By default the host secret is NOT exposed to the shell.
    sandbox = local(cwd=tmp_path)(policy=SandboxPolicy(allow_shell=True))
    out = sandbox.shell("echo value=$PYFLUE_TEST_SECRET")
    assert out["exit_code"] == 0
    assert "leak" not in out["stdout"]

    # Explicit opt-in exposes it.
    opted = local(cwd=tmp_path, env={"PYFLUE_TEST_SECRET": "ok"})(
        policy=SandboxPolicy(allow_shell=True)
    )
    assert "ok" in opted.shell("echo value=$PYFLUE_TEST_SECRET")["stdout"]


def test_local_sandbox_shell_respects_policy(tmp_path):
    sandbox = local(cwd=tmp_path)(policy=SandboxPolicy(allow_shell=False))
    with pytest.raises(PermissionError):
        sandbox.shell("echo hi")


@pytest.mark.asyncio
async def test_init_with_local_sandbox_writes_real_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = await init(sandbox=local(cwd=tmp_path), allow_write=True)
    session = await agent.session("s")
    await session.write_file("out.txt", "data")
    assert (tmp_path / "out.txt").read_text() == "data"
    assert agent.config.sandbox.__name__ == "local"  # factory carried through
