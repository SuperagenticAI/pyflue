from __future__ import annotations

import pytest

from pyflue.sandbox import SandboxPolicy, VirtualSandbox


def test_virtual_sandbox_path_boundary(tmp_path):
    sandbox = VirtualSandbox(tmp_path)

    with pytest.raises(ValueError):
        sandbox.resolve("../outside.txt", must_exist=False)


def test_virtual_sandbox_write_and_shell_policy(tmp_path):
    sandbox = VirtualSandbox(tmp_path)

    with pytest.raises(PermissionError):
        sandbox.write_file("x.txt", "x")
    with pytest.raises(PermissionError):
        sandbox.shell("echo hi")

    enabled = VirtualSandbox(
        tmp_path,
        SandboxPolicy(allow_write=True, allow_shell=True),
    )
    enabled.write_file("x.txt", "hello")
    assert enabled.read_file("x.txt") == "hello"
    assert enabled.shell("cat x.txt")["stdout"] == "hello"


def test_virtual_sandbox_rejects_compound_commands_by_default(tmp_path):
    sandbox = VirtualSandbox(tmp_path, SandboxPolicy(allow_shell=True))

    with pytest.raises(PermissionError, match="Compound shell syntax"):
        sandbox.shell("echo hi && echo bye")


def test_virtual_sandbox_allowed_commands_use_shell_parsing(tmp_path):
    sandbox = VirtualSandbox(
        tmp_path,
        SandboxPolicy(allow_shell=True, allowed_commands=("python",)),
    )

    result = sandbox.shell("python -c 'print(42)'")

    assert result["stdout"].strip() == "42"


def test_virtual_sandbox_grep_glob_and_edit(tmp_path):
    sandbox = VirtualSandbox(tmp_path, SandboxPolicy(allow_write=True))
    sandbox.write_file("src/app.py", "print('needle')\n")

    assert "src/app.py" in sandbox.glob("**/*.py")
    assert "needle" in sandbox.grep("needle", include="*.py")
    assert "Edited" in sandbox.edit_file("src/app.py", "needle", "value")
    assert "value" in sandbox.read_file("src/app.py")


def test_virtual_sandbox_filesystem_api(tmp_path):
    sandbox = VirtualSandbox(tmp_path, SandboxPolicy(allow_write=True))

    assert not sandbox.exists("data")
    assert sandbox.mkdir("data/nested") == "Created data/nested"
    assert sandbox.exists("data/nested")

    assert sandbox.write_bytes("data/nested/blob.bin", b"\x00pyflue") == "Wrote data/nested/blob.bin"
    assert sandbox.read_bytes("data/nested/blob.bin") == b"\x00pyflue"

    file_info = sandbox.stat("data/nested/blob.bin")
    assert file_info.path == "/data/nested/blob.bin"
    assert file_info.is_file is True
    assert file_info.is_dir is False
    assert file_info.size == 7
    assert file_info.mtime is not None

    dir_info = sandbox.stat("data/nested")
    assert dir_info.path == "/data/nested"
    assert dir_info.is_dir is True
    assert dir_info.is_file is False

    listed = {entry.path: entry for entry in sandbox.list_files("data/nested")}
    assert listed["/data/nested/blob.bin"].is_file is True

    assert sandbox.rm("data/nested/blob.bin") == "Removed data/nested/blob.bin"
    assert not sandbox.exists("data/nested/blob.bin")
    with pytest.raises(IsADirectoryError):
        sandbox.rm("data")
    assert sandbox.rm("data", recursive=True) == "Removed data"
    assert sandbox.rm("data", recursive=True, force=True) == "Removed data"
