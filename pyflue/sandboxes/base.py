"""Sandbox provider interface."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class SandboxPolicy:
    """Controls model-callable filesystem and shell capabilities."""

    allow_write: bool = False
    allow_shell: bool = False
    allowed_commands: tuple[str, ...] = ()
    allow_compound_commands: bool = False


@dataclass(frozen=True)
class SandboxFileInfo:
    """Normalized file listing entry returned by sandbox providers."""

    path: str
    is_dir: bool = False
    is_file: bool = False
    size: int = 0
    mtime: float | None = None


class SandboxBackend(Protocol):
    """Common interface for local and remote PyFlue sandboxes."""

    provider: str
    policy: SandboxPolicy

    @property
    def id(self) -> str:
        """Stable provider-specific sandbox id."""

    def list_files(self, path: str = ".") -> list[SandboxFileInfo]:
        """List files at a path."""

    def stat(self, path: str) -> SandboxFileInfo:
        """Return normalized metadata for a file or directory."""

    def exists(self, path: str) -> bool:
        """Return whether a path exists inside the sandbox."""

    def read_file(self, path: str, *, offset: int = 1, limit: int | None = None) -> str:
        """Read a file."""

    def read_bytes(self, path: str) -> bytes:
        """Read a file as bytes."""

    def write_file(self, path: str, content: str) -> str:
        """Write a file."""

    def write_bytes(self, path: str, content: bytes) -> str:
        """Write bytes to a file."""

    def mkdir(self, path: str, *, recursive: bool = True) -> str:
        """Create a directory."""

    def rm(self, path: str, *, recursive: bool = False, force: bool = False) -> str:
        """Remove a file or directory."""

    def edit_file(self, path: str, old: str, new: str, *, replace_all: bool = False) -> str:
        """Edit a file by replacing text."""

    def grep(self, pattern: str, *, path: str = ".", include: str | None = None) -> str:
        """Search files."""

    def glob(self, pattern: str) -> str:
        """Find files by glob pattern."""

    def shell(
        self,
        command: str,
        *,
        timeout: int | None = 120,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Run a shell command."""


def require_write(policy: SandboxPolicy) -> None:
    if not policy.allow_write:
        raise PermissionError("Write access is disabled for this sandbox.")


def require_shell(policy: SandboxPolicy, command: str) -> None:
    if not policy.allow_shell:
        raise PermissionError("Shell execution is disabled for this sandbox.")
    _reject_unsafe_shell(command, policy)
    if policy.allowed_commands:
        executable = _first_executable(command)
        if executable not in policy.allowed_commands:
            raise PermissionError(f"Command is not allowed: {executable}")


def _reject_unsafe_shell(command: str, policy: SandboxPolicy) -> None:
    if policy.allow_compound_commands:
        return
    if "`" in command or "$(" in command or "\n" in command:
        raise PermissionError(
            "Compound shell syntax is disabled for this sandbox. "
            "Enable allow_compound_commands only for trusted workflows."
        )
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise PermissionError(f"Invalid shell command: {exc}") from exc
    banned = {"&&", "||", ";", "|", ">", "<", ">>", "<<"}
    for token in tokens:
        if token in banned or set(token) <= {"&", "|", ";", ">", "<"}:
            raise PermissionError(
                "Compound shell syntax is disabled for this sandbox. "
                "Enable allow_compound_commands only for trusted workflows."
            )


def _first_executable(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        raise PermissionError(f"Invalid shell command: {exc}") from exc
    return parts[0] if parts else ""
