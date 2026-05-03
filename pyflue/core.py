"""PyFlue core agent and session API."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from collections.abc import AsyncIterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import aiosqlite
from pydantic import BaseModel, TypeAdapter

from pyflue.code.base import PythonRunResult
from pyflue.code.registry import create_python_backend
from pyflue.config import load_config
from pyflue.harnesses.registry import create_backend
from pyflue.sandbox import SandboxPolicy
from pyflue.sandboxes.registry import create_sandbox
from pyflue.skills import (
    load_project_instructions,
    load_roles,
    load_skills,
    render_skill_prompt,
)
from pyflue.types import HarnessResult, PyFlueConfig, PyFlueEvent, Role

RESULT_START = "---RESULT_START---"
RESULT_END = "---RESULT_END---"
_RESULT_RE = re.compile(
    rf"{RESULT_START}\s*\n(?P<body>[\s\S]*?)\n?{RESULT_END}",
    re.MULTILINE,
)


async def init(
    *,
    model: str | None = None,
    harness: str | None = None,
    sandbox: str | None = None,
    python_backend: str | None = None,
    skills_dir: str | Path | None = None,
    config_path: str | Path | None = None,
    env: dict[str, str] | None = None,
    allow_write: bool = False,
    allow_shell: bool = False,
    allowed_commands: tuple[str, ...] | list[str] | None = None,
    allow_compound_commands: bool | None = None,
) -> PyFlueAgent:
    """Initialize a PyFlue agent."""
    config = load_config(config_path or "pyflue.toml")
    if model is not None:
        config.model = model
    if harness is not None:
        config.harness = harness
    if sandbox is not None:
        config.sandbox = sandbox
    if python_backend is not None:
        config.python_backend = python_backend
    if skills_dir is not None:
        path = Path(skills_dir).expanduser()
        config.skills_dir = path if path.is_absolute() else config.root / path
    if env:
        config.env.update({str(key): str(value) for key, value in env.items()})
    if allowed_commands is not None:
        config.allowed_commands = tuple(str(item) for item in allowed_commands)
    if allow_compound_commands is not None:
        config.allow_compound_commands = allow_compound_commands
    return PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(
            allow_write=allow_write,
            allow_shell=allow_shell,
            allowed_commands=config.allowed_commands,
            allow_compound_commands=config.allow_compound_commands,
        ),
    )


class PyFlueAgent:
    """Factory for stateful PyFlue sessions."""

    def __init__(
        self,
        *,
        config: PyFlueConfig,
        sandbox_policy: SandboxPolicy | None = None,
    ):
        self.config = config
        self.backend = create_backend(config.harness)
        self.instructions = load_project_instructions(config.root)
        self.skills = load_skills(config.root, config.skills_dir)
        self.roles = load_roles(config.root, config.roles_dir)
        self.sandbox_policy = sandbox_policy or SandboxPolicy()
        self.state_dir = config.state_dir or config.root / ".pyflue" / "sessions"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.sandbox_state_dir = config.root / ".pyflue" / "sandboxes"
        self.sandbox_state_dir.mkdir(parents=True, exist_ok=True)
        self.sessions = PyFlueSessions(self)

    async def session(
        self,
        session_id: str | None = None,
        *,
        role: str | None = None,
    ) -> PyFlueSession:
        """Open or create a persistent session."""
        sid = session_id or "default"
        session = PyFlueSession(agent=self, session_id=sid, role=role)
        await session._ensure_store()
        return session


class PyFlueSessions:
    """Explicit session lifecycle helper exposed as `agent.sessions`."""

    def __init__(self, agent: PyFlueAgent):
        self.agent = agent

    async def get(
        self,
        session_id: str | None = None,
        *,
        role: str | None = None,
    ) -> PyFlueSession:
        """Load an existing session."""
        sid = session_id or "default"
        session = PyFlueSession(agent=self.agent, session_id=sid, role=role)
        if not session.db_path.exists():
            raise KeyError(f"Session does not exist: {sid}")
        await session._ensure_store()
        return session

    async def create(
        self,
        session_id: str | None = None,
        *,
        role: str | None = None,
    ) -> PyFlueSession:
        """Create a new session and fail if it already exists."""
        sid = session_id or "default"
        session = PyFlueSession(agent=self.agent, session_id=sid, role=role)
        if session.db_path.exists():
            raise FileExistsError(f"Session already exists: {sid}")
        await session._ensure_store()
        return session

    async def delete(self, session_id: str | None = None) -> None:
        """Delete one session's persisted state and sandbox files."""
        sid = session_id or "default"
        session = PyFlueSession(agent=self.agent, session_id=sid)
        if session.db_path.exists():
            session.db_path.unlink()
        sandbox_root = self.agent.sandbox_state_dir / session.safe_id
        if sandbox_root.exists():
            shutil.rmtree(sandbox_root)


class PyFlueSession:
    """One persistent PyFlue conversation."""

    def __init__(self, *, agent: PyFlueAgent, session_id: str, role: str | None = None):
        self.agent = agent
        self.session_id = session_id
        self.session_role = role
        self.deleted = False
        self.active_operation: str | None = None
        safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", session_id)
        self.safe_id = safe_id
        self.db_path = self.agent.state_dir / f"{safe_id}.sqlite3"
        self.sandbox = create_sandbox(
            self.agent.config.sandbox,
            root=self.agent.sandbox_state_dir / safe_id,
            policy=self.agent.sandbox_policy,
            env=self.agent.config.env,
            config=dict(self.agent.config.harness_config.get("sandbox", {})),
        )
        self.python_backend = create_python_backend(
            self.agent.config.python_backend,
            sandbox=self.sandbox,
        )

    async def prompt(
        self,
        text: str,
        *,
        result: type[BaseModel] | Any | None = None,
        role: str | None = None,
        model: str | None = None,
        retries: int | None = None,
        stream: bool = False,
        secrets: list[str] | tuple[str, ...] | None = None,
        commands: list[str] | tuple[str, ...] | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
    ) -> HarnessResult | Any:
        """Run one prompt turn."""
        self._assert_active()
        prompt = self._build_prompt(text, result=result, role=role)
        await self._append("user", text)
        history = await self._history_prompt(prompt)
        with self._grant_secrets(secrets), self._scope_commands(commands):
            output = await self._run_backend(
                history,
                model=model,
                role=role,
                tools=tools,
                stream=stream,
            )
            await self._append("assistant", output.text)
            if result is not None:
                return await self._parse_with_retry(
                    output,
                    result,
                    original_prompt=history,
                    model=model,
                    role=role,
                    tools=tools,
                    retries=self.agent.config.typed_retries if retries is None else retries,
                    stream=stream,
                )
        return output

    async def stream(
        self,
        text: str,
        *,
        role: str | None = None,
        model: str | None = None,
        secrets: list[str] | tuple[str, ...] | None = None,
        commands: list[str] | tuple[str, ...] | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
    ) -> AsyncIterator[PyFlueEvent]:
        """Stream normalized PyFlue events for one prompt turn."""
        self._assert_active()
        yield PyFlueEvent("start", {"session_id": self.session_id})
        try:
            prompt = self._build_prompt(text, role=role)
            await self._append("user", text)
            history = await self._history_prompt(prompt)
            chunks: list[str] = []
            final_text = ""
            with self._grant_secrets(secrets), self._scope_commands(commands):
                async for event in self._stream_backend(
                    history,
                    model=model,
                    role=role,
                    tools=tools,
                ):
                    if event.type == "delta":
                        text_delta = str(event.data.get("text", ""))
                        chunks.append(text_delta)
                    elif event.type == "end":
                        final_text = str(event.data.get("text", ""))
                    yield event
            text_out = "".join(chunks) or final_text
            if text_out:
                await self._append("assistant", text_out)
        except Exception as exc:
            yield PyFlueEvent("error", {"message": str(exc), "type": type(exc).__name__})
            raise

    async def skill(
        self,
        name: str,
        *,
        args: dict[str, Any] | None = None,
        result: type[BaseModel] | Any | None = None,
        role: str | None = None,
        model: str | None = None,
        retries: int | None = None,
        stream: bool = False,
        secrets: list[str] | tuple[str, ...] | None = None,
        commands: list[str] | tuple[str, ...] | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
    ) -> HarnessResult | Any:
        """Run a Markdown-defined skill."""
        skill = self.agent.skills.get(name)
        if skill is None:
            available = ", ".join(sorted(self.agent.skills)) or "(none)"
            raise KeyError(f"Unknown skill '{name}'. Available skills: {available}")
        prompt = render_skill_prompt(skill, args=args)
        return await self.prompt(
            prompt,
            result=result,
            role=role,
            model=model,
            retries=retries,
            stream=stream,
            secrets=secrets,
            commands=commands,
            tools=tools,
        )

    async def subagent(
        self,
        prompt: str,
        *,
        result: type[BaseModel] | Any | None = None,
        role: str | None = None,
        model: str | None = None,
        secrets: list[str] | tuple[str, ...] | None = None,
        commands: list[str] | tuple[str, ...] | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
    ) -> HarnessResult | Any:
        """Run a child session with isolated history and shared sandbox."""
        output = await self.task(
            prompt,
            result=result,
            role=role,
            model=model,
            secrets=secrets,
            commands=commands,
            tools=tools,
        )
        return output

    async def task(
        self,
        prompt: str,
        *,
        result: type[BaseModel] | Any | None = None,
        role: str | None = None,
        model: str | None = None,
        task_id: str | None = None,
        secrets: list[str] | tuple[str, ...] | None = None,
        commands: list[str] | tuple[str, ...] | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
    ) -> HarnessResult | Any:
        """Run a Flue-style child task with shared sandbox and isolated history."""
        self._assert_active()
        child_id = task_id or f"{self.session_id}:task:{uuid.uuid4().hex[:10]}"
        child = await self.agent.session(child_id, role=role or self.session_role)
        child.sandbox = self.sandbox
        child.python_backend = self.python_backend
        output = await child.prompt(
            prompt,
            result=result,
            role=role,
            model=model,
            secrets=secrets,
            commands=commands,
            tools=tools,
        )
        await self._append("assistant", f"Subagent {child_id} completed.")
        return output

    async def run_python(
        self,
        code: str,
        *,
        inputs: dict[str, Any] | None = None,
        external_functions: dict[str, Any] | None = None,
        result: type[BaseModel] | Any | None = None,
        type_check: bool = False,
        type_check_stubs: str | None = None,
        restart: bool = False,
        timeout: float | None = 5.0,
        resource_limits: dict[str, Any] | None = None,
        mount: Any | None = None,
    ) -> PythonRunResult | Any:
        """Run Python code through the configured Python backend."""
        if self.python_backend is None:
            raise RuntimeError(
                "No Python backend configured. Use init(python_backend='monty') "
                "or set python_backend = 'monty' in pyflue.toml."
            )
        output = await self.python_backend.run(
            code,
            inputs=inputs,
            external_functions=external_functions,
            type_check=type_check,
            type_check_stubs=type_check_stubs,
            restart=restart,
            timeout=timeout,
            resource_limits=resource_limits,
            mount=mount,
        )
        if result is not None:
            return TypeAdapter(result).validate_python(output.result)
        return output

    async def start_python(
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
        """Start snapshot-driven Python execution when the backend supports it."""
        if self.python_backend is None or not hasattr(self.python_backend, "start"):
            raise RuntimeError("The configured Python backend does not support start_python().")
        return await self.python_backend.start(
            code,
            inputs=inputs,
            type_check=type_check,
            type_check_stubs=type_check_stubs,
            timeout=timeout,
            resource_limits=resource_limits,
            mount=mount,
        )

    def dump_python_state(self) -> bytes | None:
        """Serialize Python backend state when supported."""
        return self.python_backend.dump() if self.python_backend is not None else None

    def load_python_state(self, data: bytes) -> None:
        """Restore Python backend state when supported."""
        if self.python_backend is None:
            raise RuntimeError("No Python backend configured.")
        self.python_backend.load(data)

    def register_python_dataclass(self, cls: type[Any]) -> None:
        """Register a dataclass with the Python backend when supported."""
        if self.python_backend is None:
            raise RuntimeError("No Python backend configured.")
        self.python_backend.register_dataclass(cls)

    async def shell(
        self,
        command: str,
        *,
        timeout: int | None = 120,
        secrets: list[str] | tuple[str, ...] | None = None,
        commands: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Run a shell command through the configured sandbox."""
        self._assert_active()
        with self._grant_secrets(secrets), self._scope_commands(commands):
            output = await self.agent.backend.shell(
                command,
                sandbox=self.sandbox,
                timeout=timeout,
            )
        await self._append("tool", json.dumps(output, sort_keys=True))
        return output

    async def read_file(self, path: str) -> str:
        """Read a file from the session sandbox."""
        self._assert_active()
        return self.sandbox.read_file(path)

    async def write_file(self, path: str, content: str) -> str:
        """Write a file into the session sandbox."""
        self._assert_active()
        return self.sandbox.write_file(path, content)

    async def compact(
        self,
        *,
        keep_recent: int = 6,
        model: str | None = None,
    ) -> HarnessResult:
        """Summarize older conversation state and keep recent turns verbatim."""
        self._assert_active()
        rows = await self._all_messages()
        if len(rows) <= keep_recent:
            return HarnessResult(
                text="No compaction needed.",
                metadata={"harness": self.agent.backend.name, "compacted": False},
            )
        split = max(len(rows) - max(keep_recent, 0), 0)
        older = rows[:split]
        recent = rows[split:]
        transcript = "\n\n".join(f"{role}: {content}" for role, content in older)
        prompt = (
            "Summarize this conversation history for future continuation. "
            "Preserve user goals, decisions, tool results, files changed, open tasks, "
            "and any constraints. Return only the summary.\n\n"
            f"{transcript}"
        )
        summary = await self._run_backend(
            prompt,
            model=model,
            role=None,
            tools=None,
            stream=False,
        )
        await self._replace_messages(
            [("summary", summary.text.strip()), *recent],
        )
        summary.metadata["compacted"] = True
        summary.metadata["messages_before"] = len(rows)
        summary.metadata["messages_after"] = len(recent) + 1
        return summary

    def close(self) -> None:
        """Mark this session handle closed."""
        self.deleted = True

    async def delete(self) -> None:
        """Delete this session's persisted state and sandbox files."""
        self.close()
        await self.agent.sessions.delete(self.session_id)

    async def _ensure_store(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "create table if not exists messages "
                "(id integer primary key autoincrement, role text not null, content text not null)"
            )
            await db.commit()

    async def _append(self, role: str, content: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "insert into messages(role, content) values (?, ?)",
                (role, content),
            )
            await db.commit()

    async def _messages(self) -> list[tuple[str, str]]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "select role, content from messages order by id desc limit 12"
            )
            rows = await cursor.fetchall()
        return list(reversed([(str(role), str(content)) for role, content in rows]))

    async def _all_messages(self) -> list[tuple[str, str]]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("select role, content from messages order by id")
            rows = await cursor.fetchall()
        return [(str(role), str(content)) for role, content in rows]

    async def _replace_messages(self, rows: list[tuple[str, str]]) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("delete from messages")
            await db.executemany(
                "insert into messages(role, content) values (?, ?)",
                rows,
            )
            await db.commit()

    async def _history_prompt(self, prompt: str) -> str:
        rows = await self._messages()
        if not rows:
            return prompt
        history = "\n\n".join(f"{role}: {content}" for role, content in rows)
        return f"Conversation so far:\n{history}\n\nNext:\n{prompt}"

    async def _run_backend(
        self,
        prompt: str,
        *,
        model: str | None,
        role: str | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
        stream: bool,
    ) -> HarnessResult:
        config = self.agent.config
        effective_role = self._effective_role(role)
        resolved_model = model or (effective_role.model if effective_role else None)
        if resolved_model is not None:
            config = PyFlueConfig(**{**config.__dict__, "model": resolved_model})
        return await self.agent.backend.run(
            prompt=prompt,
            system_prompt=self.agent.instructions,
            config=config,
            skills=self.agent.skills,
            sandbox=self.sandbox,
            session_id=self.session_id,
            python_backend=self.python_backend,
            tools=tools,
            stream=stream,
        )

    async def _stream_backend(
        self,
        prompt: str,
        *,
        model: str | None,
        role: str | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
    ) -> AsyncIterator[PyFlueEvent]:
        config = self.agent.config
        effective_role = self._effective_role(role)
        resolved_model = model or (effective_role.model if effective_role else None)
        if resolved_model is not None:
            config = PyFlueConfig(**{**config.__dict__, "model": resolved_model})
        async for event in self.agent.backend.stream(
            prompt=prompt,
            system_prompt=self.agent.instructions,
            config=config,
            skills=self.agent.skills,
            sandbox=self.sandbox,
            session_id=self.session_id,
            python_backend=self.python_backend,
            tools=tools,
        ):
            yield event

    @contextmanager
    def _grant_secrets(self, names: list[str] | tuple[str, ...] | None):
        if not names:
            yield
            return
        missing = [name for name in names if name not in self.agent.config.env]
        if missing:
            raise KeyError(f"Unknown secret(s): {', '.join(missing)}")
        env = getattr(self.sandbox, "env", None)
        if not isinstance(env, dict):
            yield
            return
        previous = dict(env)
        try:
            env.update({name: self.agent.config.env[name] for name in names})
            yield
        finally:
            env.clear()
            env.update(previous)

    @contextmanager
    def _scope_commands(self, commands: list[str] | tuple[str, ...] | None):
        if not commands:
            yield
            return
        previous = self.sandbox.policy
        merged = tuple(dict.fromkeys([*previous.allowed_commands, *(str(item) for item in commands)]))
        try:
            self.sandbox.policy = replace(previous, allowed_commands=merged)
            yield
        finally:
            self.sandbox.policy = previous

    async def _parse_with_retry(
        self,
        output: HarnessResult,
        result: Any,
        *,
        original_prompt: str,
        model: str | None,
        role: str | None,
        tools: list[Any] | tuple[Any, ...] | None,
        retries: int,
        stream: bool,
    ) -> Any:
        last_error: Exception | None = None
        current = output
        for attempt in range(max(retries, 0) + 1):
            try:
                return _parse_typed_result(current.text, result)
            except Exception as exc:
                last_error = exc
                if attempt >= retries:
                    break
                schema = TypeAdapter(result).json_schema()
                repair_prompt = (
                    f"{original_prompt}\n\n"
                    "The previous response failed structured output validation.\n"
                    f"Validation error: {exc}\n\n"
                    "Return only valid JSON between the required result delimiters "
                    "that satisfies this schema:\n"
                    f"{json.dumps(schema, indent=2, sort_keys=True)}"
                )
                current = await self._run_backend(
                    repair_prompt,
                    model=model,
                    role=role,
                    tools=tools,
                    stream=stream,
                )
                await self._append("assistant", current.text)
        raise ValueError(f"Structured output validation failed: {last_error}") from last_error

    def _build_prompt(
        self,
        text: str,
        *,
        result: Any | None = None,
        role: str | None = None,
    ) -> str:
        parts = [
            "You are running inside PyFlue, a headless Python agent harness.",
        ]
        selected = self._effective_role(role)
        if selected:
            parts.append(f"Role: {selected.name}\n{selected.instructions}")
        parts.append(text.strip())
        if result is not None:
            schema = TypeAdapter(result).json_schema()
            parts.extend(
                [
                    "Return the final structured result between these exact delimiters:",
                    RESULT_START,
                    json.dumps(schema, indent=2, sort_keys=True),
                    RESULT_END,
                ]
            )
        return "\n\n".join(parts)

    def _effective_role(self, role: str | None) -> Role | None:
        role_name = role or self.session_role
        if not role_name:
            return None
        selected = self.agent.roles.get(role_name)
        if selected is None:
            available = ", ".join(sorted(self.agent.roles)) or "(none)"
            raise KeyError(f"Unknown role '{role_name}'. Available roles: {available}")
        return selected

    def _assert_active(self) -> None:
        if self.deleted:
            raise RuntimeError(f"Session is closed: {self.session_id}")


def _parse_typed_result(text: str, result: Any) -> Any:
    matches = list(_RESULT_RE.finditer(text or ""))
    raw = matches[-1].group("body").strip() if matches else text.strip()
    value: Any = raw
    if raw.startswith("{") or raw.startswith("["):
        value = json.loads(raw)
    return TypeAdapter(result).validate_python(value)
