from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pyflue.sandboxes import (
    DaytonaSandbox,
    E2BSandbox,
    ModalSandbox,
    RunloopSandbox,
    SandboxPolicy,
    create_sandbox,
)


def test_create_sandbox_virtual(tmp_path):
    sandbox = create_sandbox(
        "virtual",
        root=tmp_path,
        policy=SandboxPolicy(allow_write=True),
    )

    sandbox.write_file("hello.txt", "hello")

    assert sandbox.provider == "virtual"
    assert sandbox.read_file("hello.txt") == "hello"


def test_daytona_shell_backed_file_helpers(tmp_path):
    fake = _FakeDaytonaSandbox(cwd=tmp_path)
    sandbox = DaytonaSandbox(
        sandbox=fake,
        workspace=str(tmp_path),
        policy=SandboxPolicy(allow_write=True, allow_shell=True),
    )

    sandbox.write_file("notes/a.txt", "hello provider")

    assert sandbox.read_file("notes/a.txt") == "hello provider"
    assert "notes/a.txt" in sandbox.glob("**/*.txt")
    assert "hello provider" in sandbox.grep("provider")
    assert sandbox.shell("cat notes/a.txt")["stdout"] == "hello provider"


def test_shell_backed_filesystem_api(tmp_path):
    sandbox = DaytonaSandbox(
        sandbox=_FakeDaytonaSandbox(cwd=tmp_path),
        workspace=str(tmp_path),
        policy=SandboxPolicy(allow_write=True, allow_shell=True),
    )

    assert sandbox.mkdir("data/nested") == "Created /data/nested"
    assert sandbox.exists("data/nested") is True
    assert sandbox.write_bytes("data/nested/blob.bin", b"\x00remote") == "Wrote /data/nested/blob.bin"
    assert sandbox.read_bytes("data/nested/blob.bin") == b"\x00remote"

    file_info = sandbox.stat("data/nested/blob.bin")
    assert file_info.path == "/data/nested/blob.bin"
    assert file_info.is_file is True
    assert file_info.size == 7

    listed = {entry.path: entry for entry in sandbox.list_files("data/nested")}
    assert listed["/data/nested/blob.bin"].is_file is True

    assert sandbox.rm("data", recursive=True) == "Removed /data"
    assert sandbox.exists("data") is False


def test_e2b_provider_normalizes_command_results(tmp_path):
    fake = _FakeE2BSandbox(cwd=tmp_path)
    sandbox = E2BSandbox(
        sandbox=fake,
        workspace=str(tmp_path),
        policy=SandboxPolicy(allow_shell=True),
    )

    result = sandbox.shell("printf e2b")

    assert result == {"stdout": "e2b", "stderr": "", "exit_code": 0}


def test_modal_provider_normalizes_command_results():
    sandbox = ModalSandbox(
        sandbox=_FakeModalSandbox(),
        policy=SandboxPolicy(allow_shell=True),
    )

    result = sandbox.shell("ignored")

    assert result == {"stdout": "modal", "stderr": "", "exit_code": 0}


def test_runloop_provider_normalizes_command_results(tmp_path):
    sandbox = RunloopSandbox(
        devbox=_FakeRunloopDevbox(cwd=tmp_path),
        workspace=str(tmp_path),
        policy=SandboxPolicy(allow_shell=True),
    )

    result = sandbox.shell("printf runloop")

    assert result == {"stdout": "runloop", "stderr": "", "exit_code": 0}


def test_remote_sandbox_respects_shell_policy(tmp_path):
    sandbox = E2BSandbox(sandbox=_FakeE2BSandbox(cwd=tmp_path), workspace=str(tmp_path))

    with pytest.raises(PermissionError):
        sandbox.shell("echo blocked")


class _FakeDaytonaProcess:
    def __init__(self, cwd: Path):
        self.cwd = cwd

    def exec(self, command: str, timeout: int | None = None):
        return _run_local(command, self.cwd, timeout=timeout)


class _FakeDaytonaSandbox:
    id = "daytona-test"

    def __init__(self, cwd: Path):
        self.process = _FakeDaytonaProcess(cwd)


class _FakeE2BCommands:
    def __init__(self, cwd: Path):
        self.cwd = cwd

    def run(self, command: str, timeout: int | None = None):
        return _run_local(command, self.cwd, timeout=timeout)


class _FakeE2BSandbox:
    sandbox_id = "e2b-test"

    def __init__(self, cwd: Path):
        self.commands = _FakeE2BCommands(cwd)


class _FakeModalProcess:
    stdout = "modal"
    stderr = ""

    def wait(self):
        return 0


class _FakeModalSandbox:
    object_id = "modal-test"

    def exec(self, *args, **kwargs):
        return _FakeModalProcess()


class _FakeRunloopDevbox:
    id = "runloop-test"

    def __init__(self, cwd: Path):
        self.cwd = cwd

    def execute(self, command: str, timeout: int | None = None):
        return _run_local(command, self.cwd, timeout=timeout)


def _run_local(command: str, cwd: Path, timeout: int | None = None):
    completed = subprocess.run(
        command,
        cwd=cwd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "exit_code": completed.returncode,
    }
