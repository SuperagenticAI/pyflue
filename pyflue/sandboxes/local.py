"""Host-bound ``local()`` sandbox factory (parity item 7).

The reference's ``local()`` (from ``@flue/runtime/node``) binds an agent
directly to the host machine: file operations hit the real filesystem and the
shell runs through a real subprocess. Crucially, environment exposure is
**opt-in** — only a small allowlist of shell essentials is inherited from the
host; secrets and tokens must be passed explicitly via ``env=``, keeping them
out of the agent's ``bash`` tool by default.

    from pyflue import init
    from pyflue.sandboxes import local

    agent = await init(
        sandbox=local(cwd="/srv/repo", env={"GH_TOKEN": os.environ["GH_TOKEN"]}),
        allow_shell=True,
    )

This differs from the in-memory ``"virtual"`` sandbox (zero-config, isolated,
boundary-checked) and from the legacy ``sandbox="local"`` string, which remains
an alias of the virtual sandbox for backward compatibility.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from pyflue.sandboxes.base import SandboxPolicy, require_shell
from pyflue.sandboxes.virtual import VirtualSandbox

# Shell essentials inherited from the host. Everything else (API keys, tokens)
# must be passed explicitly via local(env=...). Mirrors the reference allowlist.
_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "HOSTNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TERM",
    "TMPDIR",
    "TMP",
    "TEMP",
)


def _allowlisted_env() -> dict[str, str]:
    return {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}


class LocalSandbox(VirtualSandbox):
    """A host-bound sandbox: real filesystem + subprocess shell, no boundary box.

    Relative paths resolve against ``root`` (the working directory); absolute
    host paths are allowed. The shell inherits only the env passed to it (the
    allowlist plus explicit opt-ins), not the full host environment.
    """

    provider = "local"

    @property
    def id(self) -> str:
        return f"pyflue-local:{self.root}"

    def resolve(self, path: str, *, must_exist: bool = True) -> Path:
        target = Path(str(path or ".")).expanduser()
        if not target.is_absolute():
            target = self.root / target
        target = target.resolve()
        if must_exist and not target.exists():
            raise FileNotFoundError(path)
        return target

    def relative(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError:
            return str(resolved)

    def to_backend_path(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            rel = resolved.relative_to(self.root).as_posix()
        except ValueError:
            return str(resolved)
        return "/" if not rel else "/" + rel

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
        # Only the configured (allowlisted + explicit) env is exposed — NOT the
        # full host environment, unlike the virtual sandbox.
        completed = subprocess.run(
            command,
            cwd=workdir,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**self.env, **(env or {})},
        )
        return {
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "exit_code": completed.returncode,
        }


def local(*, cwd: str | Path | None = None, env: dict[str, str] | None = None) -> Any:
    """Return a host-bound sandbox factory for ``init(sandbox=...)``.

    ``cwd`` defaults to the current working directory. ``env`` opts specific
    host variables (e.g. credentials) into the agent's shell, on top of the
    shell-essentials allowlist.
    """
    bound_cwd = cwd
    extra_env = dict(env or {})

    def factory(
        *,
        root: str | Path | None = None,
        policy: SandboxPolicy | None = None,
        env: dict[str, str] | None = None,
        config: dict[str, Any] | None = None,
    ) -> LocalSandbox:
        base = bound_cwd or os.getcwd()
        resolved_env = _allowlisted_env()
        resolved_env.update(extra_env)
        return LocalSandbox(root=base, policy=policy, env=resolved_env)

    factory.__pyflue_sandbox_factory__ = True
    factory.__name__ = "local"
    return factory


__all__ = ["LocalSandbox", "local"]
