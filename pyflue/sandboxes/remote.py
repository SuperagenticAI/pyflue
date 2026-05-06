"""Utilities for shell-backed remote sandbox providers."""

from __future__ import annotations

import base64
import json
import shlex
from typing import Any

from pyflue.sandboxes.base import (
    SandboxFileInfo,
    SandboxPolicy,
    require_shell,
    require_write,
)


class ShellSandboxMixin:
    """Implements filesystem helpers through a provider's shell command API."""

    provider = "remote"
    workspace = "/workspace"

    def __init__(self, *, policy: SandboxPolicy | None = None):
        self.policy = policy or SandboxPolicy()

    def list_files(self, path: str = ".") -> list[SandboxFileInfo]:
        script = (
            "python - <<'PY'\n"
            "import json, os, sys\n"
            f"path = {self._remote_expr(path)!r}\n"
            "if os.path.isdir(path):\n"
            "    paths = [os.path.join(path, item) for item in sorted(os.listdir(path))]\n"
            "else:\n"
            "    paths = [path]\n"
            "entries = []\n"
            "for item in paths:\n"
            "    stat = os.stat(item)\n"
            "    is_dir = os.path.isdir(item)\n"
            "    entries.append({\n"
            "        'path': item,\n"
            "        'is_dir': is_dir,\n"
            "        'is_file': os.path.isfile(item),\n"
            "        'size': 0 if is_dir else stat.st_size,\n"
            "        'mtime': stat.st_mtime,\n"
            "    })\n"
            "print(json.dumps(entries))\n"
            "PY"
        )
        result = self._run_unchecked(script)
        if result["exit_code"] != 0:
            raise RuntimeError(result["stderr"] or result["stdout"])
        return [
            SandboxFileInfo(
                path=self._public_path(str(item["path"])),
                is_dir=bool(item.get("is_dir")),
                is_file=bool(item.get("is_file")),
                size=int(item.get("size") or 0),
                mtime=float(item["mtime"]) if item.get("mtime") is not None else None,
            )
            for item in json.loads(result["stdout"] or "[]")
        ]

    def stat(self, path: str) -> SandboxFileInfo:
        script = (
            "python - <<'PY'\n"
            "import json, os, sys\n"
            f"path = {self._remote_path(path)!r}\n"
            "stat = os.stat(path)\n"
            "is_dir = os.path.isdir(path)\n"
            "print(json.dumps({\n"
            "    'path': path,\n"
            "    'is_dir': is_dir,\n"
            "    'is_file': os.path.isfile(path),\n"
            "    'size': 0 if is_dir else stat.st_size,\n"
            "    'mtime': stat.st_mtime,\n"
            "}))\n"
            "PY"
        )
        result = self._run_unchecked(script)
        if result["exit_code"] != 0:
            raise RuntimeError(result["stderr"] or result["stdout"])
        item = json.loads(result["stdout"])
        return SandboxFileInfo(
            path=self._public_path(str(item["path"])),
            is_dir=bool(item.get("is_dir")),
            is_file=bool(item.get("is_file")),
            size=int(item.get("size") or 0),
            mtime=float(item["mtime"]) if item.get("mtime") is not None else None,
        )

    def exists(self, path: str) -> bool:
        script = (
            "python - <<'PY'\n"
            "import os, sys\n"
            f"path = {self._remote_path(path)!r}\n"
            "sys.exit(0 if os.path.exists(path) else 1)\n"
            "PY"
        )
        result = self._run_unchecked(script)
        return result["exit_code"] == 0

    def read_file(self, path: str, *, offset: int = 1, limit: int | None = None) -> str:
        command = f"cat {shlex.quote(self._remote_path(path))}"
        result = self._run_unchecked(command)
        if result["exit_code"] != 0:
            raise RuntimeError(result["stderr"] or result["stdout"])
        lines = result["stdout"].splitlines()
        start = max(offset - 1, 0)
        selected = lines[start : start + limit if limit else None]
        return "\n".join(selected)

    def read_bytes(self, path: str) -> bytes:
        command = (
            "python - <<'PY'\n"
            "import base64\n"
            f"path = {self._remote_path(path)!r}\n"
            "with open(path, 'rb') as file:\n"
            "    print(base64.b64encode(file.read()).decode('ascii'))\n"
            "PY"
        )
        result = self._run_unchecked(command)
        if result["exit_code"] != 0:
            raise RuntimeError(result["stderr"] or result["stdout"])
        return base64.b64decode(result["stdout"])

    def write_file(self, path: str, content: str) -> str:
        require_write(self.policy)
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        remote = self._remote_path(path)
        command = (
            f"mkdir -p {shlex.quote(_dirname(remote))} && "
            f"printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(remote)}"
        )
        result = self._run_unchecked(command)
        if result["exit_code"] != 0:
            raise RuntimeError(result["stderr"] or result["stdout"])
        return f"Wrote {self._public_path(remote)}"

    def write_bytes(self, path: str, content: bytes) -> str:
        require_write(self.policy)
        encoded = base64.b64encode(content).decode("ascii")
        remote = self._remote_path(path)
        command = (
            f"mkdir -p {shlex.quote(_dirname(remote))} && "
            f"printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(remote)}"
        )
        result = self._run_unchecked(command)
        if result["exit_code"] != 0:
            raise RuntimeError(result["stderr"] or result["stdout"])
        return f"Wrote {self._public_path(remote)}"

    def mkdir(self, path: str, *, recursive: bool = True) -> str:
        require_write(self.policy)
        remote = self._remote_path(path)
        command = f"mkdir {'-p ' if recursive else ''}{shlex.quote(remote)}"
        result = self._run_unchecked(command)
        if result["exit_code"] != 0:
            raise RuntimeError(result["stderr"] or result["stdout"])
        return f"Created {self._public_path(remote)}"

    def rm(self, path: str, *, recursive: bool = False, force: bool = False) -> str:
        require_write(self.policy)
        remote = self._remote_path(path)
        flags = ""
        if recursive:
            flags += "r"
        if force:
            flags += "f"
        flag_arg = f" -{flags}" if flags else ""
        command = f"rm{flag_arg} {shlex.quote(remote)}"
        result = self._run_unchecked(command)
        if result["exit_code"] != 0:
            raise RuntimeError(result["stderr"] or result["stdout"])
        return f"Removed {self._public_path(remote)}"

    def edit_file(self, path: str, old: str, new: str, *, replace_all: bool = False) -> str:
        require_write(self.policy)
        content = self.read_file(path)
        count = content.count(old)
        if count == 0:
            raise ValueError(f"Text not found in {path}")
        updated = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        self.write_file(path, updated)
        return f"Edited {path} ({count if replace_all else 1} replacement)"

    def grep(self, pattern: str, *, path: str = ".", include: str | None = None) -> str:
        root = self._remote_path(path)
        command = f"grep -Rni {shlex.quote(pattern)} {shlex.quote(root)}"
        if include:
            command += f" --include {shlex.quote(include)}"
        command += " || true"
        result = self._run_unchecked(command)
        if result["exit_code"] != 0:
            raise RuntimeError(result["stderr"] or result["stdout"])
        return "\n".join(self._public_grep_line(line) for line in result["stdout"].splitlines())

    def glob(self, pattern: str) -> str:
        command = (
            "python - <<'PY'\n"
            "import glob\n"
            f"for item in sorted(glob.glob({self._remote_expr(pattern)!r}, recursive=True)):\n"
            "    print(item)\n"
            "PY"
        )
        result = self._run_unchecked(command)
        if result["exit_code"] != 0:
            raise RuntimeError(result["stderr"] or result["stdout"])
        return "\n".join(self._public_path(line) for line in result["stdout"].splitlines())

    def shell(
        self,
        command: str,
        *,
        timeout: int | None = 120,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        require_shell(self.policy, command)
        return self._run_provider(self._shell_command(command, cwd=cwd, env=env), timeout=timeout)

    def _run_unchecked(self, command: str, *, timeout: int | None = 120) -> dict[str, Any]:
        return self._run_provider(command, timeout=timeout)

    def _run_provider(self, command: str, *, timeout: int | None = 120) -> dict[str, Any]:
        raise NotImplementedError

    def _shell_command(
        self,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> str:
        parts = []
        if cwd:
            parts.append(f"cd {shlex.quote(self._remote_path(cwd))}")
        if env:
            exports = " ".join(
                f"{shlex.quote(str(key))}={shlex.quote(str(value))}"
                for key, value in env.items()
            )
            parts.append(f"env {exports} {command}")
        else:
            parts.append(command)
        return " && ".join(parts)

    def _remote_path(self, path: str) -> str:
        raw = str(path or ".")
        if raw in {"/", "."}:
            return self.workspace
        if raw.startswith("/workspace"):
            return raw
        if raw.startswith("/"):
            return raw
        return f"{self.workspace.rstrip('/')}/{raw}"

    def _remote_expr(self, path: str) -> str:
        raw = str(path or ".")
        if any(char in raw for char in "*?["):
            if raw.startswith("/"):
                return raw
            return f"{self.workspace.rstrip('/')}/{raw}"
        return self._remote_path(raw)

    def _public_path(self, path: str) -> str:
        raw = str(path)
        if raw == self.workspace:
            return "/"
        if raw.startswith(self.workspace.rstrip("/") + "/"):
            return "/" + raw.removeprefix(self.workspace.rstrip("/") + "/")
        return raw

    def _public_grep_line(self, line: str) -> str:
        file_path, sep, rest = line.partition(":")
        return f"{self._public_path(file_path)}{sep}{rest}" if sep else line


def _dirname(path: str) -> str:
    if "/" not in path.rstrip("/"):
        return "."
    return path.rstrip("/").rsplit("/", 1)[0] or "/"
