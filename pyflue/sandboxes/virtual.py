"""Virtual local sandbox."""

from __future__ import annotations

import glob as globlib
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pyflue.sandboxes.base import (
    SandboxFileInfo,
    SandboxPolicy,
    require_shell,
    require_write,
)


class VirtualSandbox:
    """Zero-config local workspace sandbox with path boundary checks."""

    provider = "virtual"

    def __init__(
        self,
        root: str | Path | None = None,
        policy: SandboxPolicy | None = None,
        env: dict[str, str] | None = None,
    ):
        self._owned_tmp: tempfile.TemporaryDirectory[str] | None = None
        if root is None:
            self._owned_tmp = tempfile.TemporaryDirectory(prefix="pyflue-")
            root = self._owned_tmp.name
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.policy = policy or SandboxPolicy()
        self.env = dict(env or {})

    @property
    def id(self) -> str:
        return f"pyflue-virtual:{self.root}"

    def list_files(self, path: str = ".") -> list[SandboxFileInfo]:
        target = self.resolve(path)
        paths = [target] if target.is_file() else sorted(target.iterdir())
        return [
            SandboxFileInfo(
                path=self.to_backend_path(child),
                is_dir=child.is_dir(),
                size=0 if child.is_dir() else child.stat().st_size,
            )
            for child in paths
        ]

    def read_file(self, path: str, *, offset: int = 1, limit: int | None = None) -> str:
        target = self.resolve(path)
        if target.is_dir():
            return "\n".join(sorted(child.name + ("/" if child.is_dir() else "") for child in target.iterdir()))
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(offset - 1, 0)
        selected = lines[start : start + limit if limit else None]
        return "\n".join(selected)

    def write_file(self, path: str, content: str) -> str:
        require_write(self.policy)
        target = self.resolve(path, must_exist=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {self.relative(target)}"

    def edit_file(self, path: str, old: str, new: str, *, replace_all: bool = False) -> str:
        require_write(self.policy)
        target = self.resolve(path)
        content = target.read_text(encoding="utf-8", errors="replace")
        count = content.count(old)
        if count == 0:
            raise ValueError(f"Text not found in {path}")
        updated = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        target.write_text(updated, encoding="utf-8")
        return f"Edited {self.relative(target)} ({count if replace_all else 1} replacement)"

    def grep(self, pattern: str, *, path: str = ".", include: str | None = None) -> str:
        root = self.resolve(path)
        files = root.rglob(include or "*") if root.is_dir() else [root]
        regex = re.compile(pattern)
        matches: list[str] = []
        for file_path in files:
            if not file_path.is_file():
                continue
            for index, line in enumerate(file_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if regex.search(line):
                    matches.append(f"{self.relative(file_path)}:{index}:{line}")
        return "\n".join(matches)

    def glob(self, pattern: str) -> str:
        search = str(self.resolve(pattern, must_exist=False))
        paths = sorted(globlib.glob(search, recursive=True))
        return "\n".join(self.relative(Path(path)) for path in paths)

    def shell(
        self,
        command: str,
        *,
        timeout: int | None = 120,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        require_shell(self.policy, command)
        workdir = self.resolve(cwd or ".")
        if not workdir.is_dir():
            raise NotADirectoryError(cwd or ".")
        completed = subprocess.run(
            command,
            cwd=workdir,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, **self.env, **(env or {})},
        )
        return {
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "exit_code": completed.returncode,
        }

    def resolve(self, path: str, *, must_exist: bool = True) -> Path:
        raw = str(path or ".")
        if raw in {"/", "/workspace"}:
            raw = "."
        elif raw.startswith("/workspace/"):
            raw = raw.removeprefix("/workspace/")
        elif raw.startswith("/"):
            raw = raw[1:]
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = self.root / target
        target = target.resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"Path escapes sandbox root: {path}") from exc
        if must_exist and not target.exists():
            raise FileNotFoundError(path)
        return target

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def to_backend_path(self, path: Path) -> str:
        rel = self.relative(path)
        return "/" if not rel else "/" + rel
