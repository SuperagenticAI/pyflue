"""Monty Python execution backend."""

from __future__ import annotations

from typing import Any

from pyflue.code.base import PythonRunResult
from pyflue.sandboxes.base import SandboxBackend
from pyflue.sandboxes.virtual import VirtualSandbox


class MontyPythonBackend:
    """Safe Python execution backend powered by pydantic-monty."""

    name = "monty"

    def __init__(self, *, sandbox: SandboxBackend | None = None):
        self.sandbox = sandbox
        self._repl: Any | None = None
        self._dataclass_registry: dict[str, type[Any]] = {}

    async def run(
        self,
        code: str,
        *,
        inputs: dict[str, Any] | None = None,
        external_functions: dict[str, Any] | None = None,
        type_check: bool = False,
        type_check_stubs: str | None = None,
        restart: bool = False,
        timeout: float | None = 5.0,
        resource_limits: dict[str, Any] | None = None,
        mount: Any | None = None,
    ) -> PythonRunResult:
        legacy_api = False
        try:
            from pydantic_monty import MontyRepl, OSAccess, ResourceLimits
        except Exception:
            try:
                from pydantic_monty import Monty, OSAccess, ResourceLimits
            except Exception as exc:
                raise ImportError(
                    "Monty Python backend requires pydantic-monty. "
                    "Install with: pip install 'pyflue[monty]'"
                ) from exc
            MontyRepl = Monty
            legacy_api = True

        if restart or self._repl is None:
            limits = _resource_limits(ResourceLimits, timeout=timeout, values=resource_limits)
            if legacy_api:
                input_names = list(inputs.keys()) if inputs else None
                self._repl = MontyRepl(
                    code,
                    inputs=input_names,
                    type_check=type_check,
                    type_check_stubs=type_check_stubs,
                    dataclass_registry=self._dataclass_registry or None,
                )
            else:
                self._repl = MontyRepl(
                    limits=limits,
                    type_check=type_check,
                    type_check_stubs=type_check_stubs,
                    dataclass_registry=self._dataclass_registry or None,
                )

        stdout: list[str] = []

        def capture(_: str, content: str) -> None:
            stdout.append(content)

        os_access = _os_access_for_sandbox(self.sandbox, OSAccess)
        limits = _resource_limits(ResourceLimits, timeout=timeout, values=resource_limits)

        if legacy_api:
            run_inputs = inputs if inputs else None
            result = self._repl.run(
                inputs=run_inputs,
                limits=limits,
                external_functions=external_functions or {},
                print_callback=capture,
                os=os_access,
            )
        else:
            result = await self._repl.feed_run_async(
                code,
                inputs=inputs or {},
                external_functions=external_functions or {},
                print_callback=capture,
                os=os_access,
                skip_type_check=not type_check,
            )
        return PythonRunResult(
            result=result,
            stdout="".join(stdout),
            backend=self.name,
            metadata={"type_check": type_check, "restart": restart},
        )

    async def start(
        self,
        code: str,
        *,
        inputs: dict[str, Any] | None = None,
        type_check: bool = False,
        type_check_stubs: str | None = None,
        timeout: float | None = 5.0,
        resource_limits: dict[str, Any] | None = None,
        mount: Any | None = None,
    ) -> Any:
        """Start Monty's snapshot-driven execution API."""
        try:
            from pydantic_monty import Monty, OSAccess, ResourceLimits
        except Exception as exc:
            raise ImportError(
                "Monty Python backend requires pydantic-monty. "
                "Install with: pip install 'pyflue[monty]'"
            ) from exc
        monty = Monty(
            code,
            inputs=inputs or {},
            type_check=type_check,
            type_check_stubs=type_check_stubs,
            dataclass_registry=self._dataclass_registry or None,
        )
        return monty.start(
            inputs=inputs or {},
            limits=_resource_limits(ResourceLimits, timeout=timeout, values=resource_limits),
            mount=mount,
            os=_os_access_for_sandbox(self.sandbox, OSAccess),
        )

    def dump(self) -> bytes | None:
        """Serialize the Monty REPL state."""
        if self._repl is None:
            return None
        try:
            return self._repl.dump()
        except AttributeError:
            return None

    def load(self, data: bytes) -> None:
        """Restore a serialized Monty REPL state."""
        try:
            from pydantic_monty import MontyRepl
        except Exception:
            try:
                from pydantic_monty import Monty
            except Exception as exc:
                raise ImportError(
                    "Monty Python backend requires pydantic-monty. "
                    "Install with: pip install 'pyflue[monty]'"
                ) from exc
            MontyRepl = Monty
        self._repl = MontyRepl.load(
            data,
            dataclass_registry=self._dataclass_registry or None,
        )

    def register_dataclass(self, cls: type[Any]) -> None:
        """Register a dataclass with Monty's dataclass registry."""
        key = f"{cls.__module__}.{cls.__qualname__}"
        self._dataclass_registry[key] = cls
        if self._repl is not None:
            self._repl.register_dataclass(cls)


class _SandboxOSAccess:
    """Minimal OSAccess bridge backed by a PyFlue sandbox."""

    def __init__(self, sandbox: SandboxBackend):
        self.sandbox = sandbox

    def get_environ(self) -> dict[str, str]:
        return {}

    def getenv(self, key: str, default: str | None = None) -> str | None:
        return default

    def path_exists(self, path: Any) -> bool:
        try:
            self.sandbox.list_files(str(path))
        except Exception:
            return False
        return True

    def path_is_file(self, path: Any) -> bool:
        try:
            entries = self.sandbox.list_files(str(path))
        except Exception:
            return False
        return len(entries) == 1 and not entries[0].is_dir

    def path_is_dir(self, path: Any) -> bool:
        try:
            entries = self.sandbox.list_files(str(path))
        except Exception:
            return False
        return bool(entries) and entries[0].is_dir

    def path_iterdir(self, path: Any) -> list[Any]:
        from pathlib import PurePosixPath

        return [PurePosixPath(item.path) for item in self.sandbox.list_files(str(path))]

    def path_read_text(self, path: Any) -> str:
        return self.sandbox.read_file(str(path))

    def path_read_bytes(self, path: Any) -> bytes:
        return self.sandbox.read_file(str(path)).encode()

    def path_write_text(self, path: Any, data: str) -> int:
        self.sandbox.write_file(str(path), data)
        return len(data)

    def path_write_bytes(self, path: Any, data: bytes) -> int:
        self.sandbox.write_file(str(path), data.decode("utf-8", errors="replace"))
        return len(data)

    def path_mkdir(self, path: Any, parents: bool, exist_ok: bool) -> None:
        command = "mkdir "
        if parents:
            command += "-p "
        command += _quote(str(path))
        if exist_ok:
            command += " || true"
        self.sandbox.shell(command)

    def path_unlink(self, path: Any) -> None:
        self.sandbox.shell(f"rm {_quote(str(path))}")

    def path_rmdir(self, path: Any) -> None:
        self.sandbox.shell(f"rmdir {_quote(str(path))}")

    def path_resolve(self, path: Any) -> str:
        return str(path)

    def path_absolute(self, path: Any) -> str:
        return str(path)

    def path_is_symlink(self, path: Any) -> bool:
        return False

    def path_rename(self, path: Any, target: Any) -> None:
        content = self.sandbox.read_file(str(path))
        self.sandbox.write_file(str(target), content)
        self.sandbox.shell(f"rm {_quote(str(path))}")


def _quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def _resource_limits(
    resource_limits_type: Any,
    *,
    timeout: float | None,
    values: dict[str, Any] | None,
) -> Any:
    kwargs = dict(values or {})
    if timeout is not None:
        kwargs.setdefault("max_duration_secs", timeout)
    return resource_limits_type(**kwargs) if kwargs else None


def _os_access_for_sandbox(sandbox: SandboxBackend | None, os_access_type: Any) -> Any:
    if sandbox is None:
        return os_access_type()
    if isinstance(sandbox, VirtualSandbox):
        return _virtual_os_access(sandbox)
    return _SandboxOSAccess(sandbox)


def _virtual_os_access(sandbox: VirtualSandbox) -> Any:
    from pydantic_monty import MemoryFile, OSAccess

    files = []
    for item in sandbox.root.rglob("*"):
        if item.is_file() and not item.is_symlink():
            try:
                path = "/" + sandbox.relative(item)
            except ValueError:
                continue
            files.append(MemoryFile(path, item.read_bytes()))
    return OSAccess(files)
