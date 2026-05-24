"""PyFlue core agent and session API."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import shlex
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
from pyflue.sandboxes.virtual import VirtualSandbox
from pyflue.session_history import SessionHistory
from pyflue.skills import (
    load_project_instructions,
    load_roles,
    load_skill_by_path,
    load_skills,
    parse_skill_text,
    render_skill_prompt,
)
from pyflue.tools import to_callable_tool
from pyflue.types import (
    HarnessResult,
    PromptResultResponse,
    PyFlueCommand,
    PyFlueConfig,
    PyFlueEvent,
    PyFlueEventCallback,
    Role,
)

RESULT_START = "---RESULT_START---"
RESULT_END = "---RESULT_END---"
_RESULT_RE = re.compile(
    rf"{RESULT_START}\s*\n(?P<body>[\s\S]*?)\n?{RESULT_END}",
    re.MULTILINE,
)
MAX_TOOL_OUTPUT_CHARS = 50 * 1024
MAX_TOOL_OUTPUT_LINES = 2000
BUILTIN_TOOL_NAMES = {
    "read",
    "write",
    "edit",
    "stat",
    "exists",
    "mkdir",
    "rm",
    "bash",
    "grep",
    "glob",
    "task",
}


def _estimate_tokens(text: str) -> int:
    """Estimate token count for text.

    Uses a simple approximation: 1 token ≈ 4 characters.
    This is a rough estimate - actual tokenization varies by model.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def _mcp_tool_to_callable(tool: Any) -> Any:
    """Convert an MCP tool definition to a callable for the harness."""

    async def call(**kwargs: Any) -> str:
        return await tool.execute(kwargs)

    call.__name__ = tool.name
    call.__doc__ = tool.description
    return call


async def init(
    *,
    model: str | None = None,
    thinking_level: str | None = None,
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
    max_task_depth: int | None = None,
    mcp_servers: dict[str, dict[str, Any]] | None = None,
    mcp_mode: str | None = None,
    mcp_search_limit: int | None = None,
    mcp_search_backend: str | None = None,
    providers: dict[str, dict[str, Any]] | None = None,
    compaction_enabled: bool | None = None,
    compaction_context_window_tokens: int | None = None,
    compaction_reserve_tokens: int | None = None,
    compaction_keep_recent_tokens: int | None = None,
    tools: list[Any] | tuple[Any, ...] | None = None,
    commands: list[str | PyFlueCommand] | tuple[str | PyFlueCommand, ...] | None = None,
    on_event: PyFlueEventCallback | None = None,
) -> PyFlueAgent:
    """Initialize a PyFlue agent."""
    config = load_config(config_path or "pyflue.toml")
    if model is not None:
        config.model = model
    if thinking_level is not None:
        config.thinking_level = thinking_level
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
    if max_task_depth is not None:
        config.max_task_depth = max_task_depth

    if providers:
        from pyflue.types import ProviderSettings

        for name, settings in providers.items():
            config.providers.set(name, ProviderSettings(
                base_url=settings.get("base_url"),
                headers=settings.get("headers"),
                api_key=settings.get("api_key"),
                store_responses=bool(settings.get("store_responses", False)),
            ))

    if (
        compaction_enabled is not None
        or compaction_context_window_tokens is not None
        or compaction_reserve_tokens is not None
        or compaction_keep_recent_tokens is not None
    ):
        from pyflue.types import CompactionConfig

        config.compaction = CompactionConfig(
            enabled=compaction_enabled if compaction_enabled is not None else config.compaction.enabled,
            context_window_tokens=compaction_context_window_tokens or config.compaction.context_window_tokens,
            reserve_tokens=compaction_reserve_tokens or config.compaction.reserve_tokens,
            keep_recent_tokens=compaction_keep_recent_tokens or config.compaction.keep_recent_tokens,
        )

    mcp_config = None
    if mcp_servers:
        from pyflue.types import McpConfig

        mcp_config = McpConfig(
            servers=mcp_servers,
            mode=mcp_mode or "direct",
            search_limit=mcp_search_limit or 10,
            search_backend=mcp_search_backend or "bm25",
        )
    elif config.mcp is not None:
        mcp_config = replace(config.mcp)
        if mcp_mode is not None:
            mcp_config.mode = mcp_mode
        if mcp_search_limit is not None:
            mcp_config.search_limit = mcp_search_limit
        if mcp_search_backend is not None:
            mcp_config.search_backend = mcp_search_backend

    return PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(
            allow_write=allow_write,
            allow_shell=allow_shell,
            allowed_commands=config.allowed_commands,
            allow_compound_commands=config.allow_compound_commands,
        ),
        mcp_config=mcp_config,
        tools=tools,
        commands=commands,
        on_event=on_event,
    )


class PyFlueAgent:
    """Factory for stateful PyFlue sessions."""

    def __init__(
        self,
        *,
        config: PyFlueConfig,
        sandbox_policy: SandboxPolicy | None = None,
        mcp_config: Any | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
        commands: list[str | PyFlueCommand] | tuple[str | PyFlueCommand, ...] | None = None,
        on_event: PyFlueEventCallback | None = None,
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
        self.fs = _AgentFlueFs(self)
        self.tools = list(tools or ())
        self.commands: tuple[str, ...] = tuple(
            str(item) for item in commands or () if not isinstance(item, PyFlueCommand)
        )
        self.command_tools: list[PyFlueCommand] = [
            item for item in commands or () if isinstance(item, PyFlueCommand)
        ]
        self.on_event = on_event
        self._active_operations: dict[str, str] = {}
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}
        self._active_child_sessions: dict[str, set[str]] = {}

        self._mcp_config = mcp_config
        self._mcp_servers_config = mcp_config.servers if mcp_config else {}
        self._mcp_mode = mcp_config.mode if mcp_config else "direct"
        self._mcp_client: Any = None
        self._mcp_connections: dict[str, Any] = {}
        self._mcp_tools: list[Any] = []
        self._mcp_loaded = False

    async def _ensure_mcp_servers(self) -> None:
        """Load MCP server connections and tools (lazy loading)."""
        if self._mcp_loaded or not self._mcp_servers_config:
            return

        if self._mcp_mode == "search_execute":
            await self._setup_search_execute_mode()
            return

        try:
            from pyflue.mcp import (
                McpServerOptions,
                McpStdioServerOptions,
                connect_mcp_server,
                connect_mcp_server_stdio,
            )
        except ImportError:
            self._mcp_loaded = True
            return

        for name, server_config in self._mcp_servers_config.items():
            if "command" in server_config:
                options = McpStdioServerOptions(
                    command=server_config["command"],
                    args=server_config.get("args"),
                    env=server_config.get("env"),
                )
                conn = await connect_mcp_server_stdio(name, options)
            elif "url" in server_config:
                options = McpServerOptions(
                    url=server_config["url"],
                    transport=server_config.get("transport", "streamable-http"),
                    headers=server_config.get("headers"),
                )
                conn = await connect_mcp_server(name, options)
            else:
                continue

            self._mcp_connections[name] = conn
            for tool in conn.tools:
                self._mcp_tools.append(_mcp_tool_to_callable(tool))

        self._mcp_loaded = True

    async def _setup_search_execute_mode(self) -> None:
        """Setup MCP client with search and execute tools."""
        try:
            from pyflue.mcp import MCPClient
        except ImportError:
            self._mcp_loaded = True
            return

        self._mcp_client = MCPClient(self._mcp_servers_config)
        await self._mcp_client.load_index()

        async def mcp_search(query: str, limit: int = 10) -> str:
            """Search for relevant MCP tools."""
            tools = self._mcp_client.search_tools(
                query=query,
                limit=limit,
                use_bm25=(self._mcp_config.search_backend == "bm25" if self._mcp_config else True),
            )
            results = []
            for t in tools:
                results.append({
                    "server": t.server,
                    "tool": t.original_name,
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    "score": t.score,
                })
            return json.dumps(results, indent=2)

        async def mcp_execute(
            server: str,
            tool: str,
            arguments: dict[str, Any],
        ) -> str:
            """Execute an MCP tool on a specific server."""
            result = await self._mcp_client.call_tool_async(
                server=server,
                tool=tool,
                arguments=arguments,
            )
            return json.dumps(result, indent=2)

        mcp_search.__name__ = "mcp_search"
        mcp_search.__doc__ = (
            "Search for relevant MCP tools by query. "
            "Returns list of matching tools with server, name, description, and schema. "
            "Use this to find the right tool before calling mcp_execute."
        )

        mcp_execute.__name__ = "mcp_execute"
        mcp_execute.__doc__ = (
            "Execute an MCP tool on a specific server. "
            "Requires server name, tool name, and arguments. "
            "Use mcp_search first to find the right tool and server."
        )

        self._mcp_tools = [mcp_search, mcp_execute]
        self._mcp_loaded = True

    async def close_mcp_servers(self) -> None:
        """Close all MCP server connections."""
        for conn in self._mcp_connections.values():
            await conn.close()
        self._mcp_connections.clear()
        self._mcp_tools.clear()
        self._mcp_loaded = False

    async def destroy(self) -> None:
        """Release agent-level resources."""
        await self.close_mcp_servers()

    async def _abort_session_tree(self, session_id: str, *, seen: set[str] | None = None) -> bool:
        seen = seen or set()
        if session_id in seen:
            return False
        seen.add(session_id)
        aborted = False
        for child_id in list(self._active_child_sessions.get(session_id, set())):
            aborted = await self._abort_session_tree(child_id, seen=seen) or aborted
        task = self._active_tasks.get(session_id)
        if task is not None and not task.done():
            task.cancel()
            session = PyFlueSession(agent=self, session_id=session_id)
            await session._emit_event(
                "abort_requested",
                operation=self._active_operations.get(session_id),
            )
            aborted = True
        return aborted

    async def shell(
        self,
        command: str,
        *,
        session_id: str | None = None,
        timeout: int | None = 120,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        secrets: list[str] | tuple[str, ...] | None = None,
        commands: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Run a shell command using the default or named session."""
        session = await self.session(session_id)
        return await session.shell(
            command,
            timeout=timeout,
            cwd=cwd,
            env=env,
            secrets=secrets,
            commands=commands,
        )

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
        """Delete one session's persisted state, child tasks, and sandbox files."""
        sid = session_id or "default"
        await self._delete_tree(sid, seen=set())

    async def _delete_tree(self, session_id: str, *, seen: set[str]) -> None:
        if session_id in seen:
            return
        seen.add(session_id)
        session = PyFlueSession(agent=self.agent, session_id=session_id)
        metadata = await session._read_persisted_metadata()
        for child_id in list(metadata.get("children") or []):
            await self._delete_tree(str(child_id), seen=seen)
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
        self.metadata: dict[str, Any] = {
            "session_id": session_id,
            "parent_session_id": None,
            "task_id": None,
            "role": role,
            "cwd": None,
            "children": [],
            "task_depth": 0,
        }
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
        self.fs = PyFlueFs(self)
        self.context_root = self.agent.config.root
        self.instructions = self.agent.instructions
        self.skills = self.agent.skills
        self.roles = self.agent.roles

    async def prompt(
        self,
        text: str,
        *,
        result: type[BaseModel] | Any | None = None,
        role: str | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
        retries: int | None = None,
        stream: bool = False,
        images: list[Any] | tuple[Any, ...] | None = None,
        secrets: list[str] | tuple[str, ...] | None = None,
        commands: list[str] | tuple[str, ...] | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
        _source: str = "prompt",
    ) -> HarnessResult | Any:
        """Run one prompt turn."""
        self._begin_operation("prompt")
        try:
            await self._refresh_context_from_sandbox()
            await self._maybe_auto_compact(model=model)
            prompt = self._build_prompt(text, result=result, role=role)
            await self._append("user", text, source=_source)
            with self._grant_secrets(secrets), self._scope_commands(commands):
                await self._emit_event("agent_start")
                overflow_retried = False
                while True:
                    history = await self._history_prompt(prompt)
                    try:
                        output = await self._run_backend(
                            history,
                            model=model,
                            role=role,
                            thinking_level=thinking_level,
                            images=images,
                            tools=tools,
                            stream=stream,
                        )
                        break
                    except Exception as exc:
                        if overflow_retried or not _is_context_overflow_error(exc):
                            raise
                        overflow_retried = True
                        await self._compact_unchecked(model=model, reason="overflow")
                await self._append("assistant", output.text, source=_source)
                if result is not None:
                    return await self._parse_with_retry(
                        output,
                        result,
                        original_prompt=history,
                        model=model,
                        role=role,
                        thinking_level=thinking_level,
                        images=images,
                        tools=tools,
                        retries=self.agent.config.typed_retries if retries is None else retries,
                        stream=stream,
                    )
            await self._emit_event("turn_end")
            return output
        except asyncio.CancelledError:
            await self._emit_event("aborted", operation="prompt")
            raise
        except Exception as exc:
            await self._emit_event("error", error=str(exc))
            raise
        finally:
            await self._emit_event("idle")
            self._end_operation()

    async def stream(
        self,
        text: str,
        *,
        role: str | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
        images: list[Any] | tuple[Any, ...] | None = None,
        secrets: list[str] | tuple[str, ...] | None = None,
        commands: list[str] | tuple[str, ...] | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
    ) -> AsyncIterator[PyFlueEvent]:
        """Stream normalized PyFlue events for one prompt turn."""
        self._begin_operation("stream")
        try:
            yield PyFlueEvent("start", {"session_id": self.session_id})
            await self._refresh_context_from_sandbox()
            await self._maybe_auto_compact(model=model)
            await self._emit_event("agent_start")
            prompt = self._build_prompt(text, role=role)
            await self._append("user", text, source="prompt")
            history = await self._history_prompt(prompt)
            chunks: list[str] = []
            final_text = ""
            with self._grant_secrets(secrets), self._scope_commands(commands):
                async for event in self._stream_backend(
                    history,
                    model=model,
                    role=role,
                    thinking_level=thinking_level,
                    images=images,
                    tools=tools,
                ):
                    if event.type == "delta":
                        text_delta = str(event.data.get("text", ""))
                        chunks.append(text_delta)
                        await self._emit_event("text_delta", text=text_delta)
                    elif event.type == "tool_start":
                        await self._emit_event(
                            "tool_start",
                            toolName=event.data.get("toolName"),
                            toolCallId=event.data.get("toolCallId"),
                            args=event.data.get("args"),
                        )
                    elif event.type == "tool_end":
                        await self._emit_event(
                            "tool_end",
                            toolName=event.data.get("toolName"),
                            toolCallId=event.data.get("toolCallId"),
                            isError=bool(event.data.get("isError")),
                            result=event.data.get("result"),
                        )
                    elif event.type == "end":
                        final_text = str(event.data.get("text", ""))
                    yield event
            text_out = "".join(chunks) or final_text
            if text_out:
                await self._append("assistant", text_out, source="prompt")
            await self._emit_event("turn_end")
        except asyncio.CancelledError:
            await self._emit_event("aborted", operation="stream")
            raise
        except Exception as exc:
            await self._emit_event("error", error=str(exc))
            yield PyFlueEvent("error", {"message": str(exc), "type": type(exc).__name__})
            raise
        finally:
            await self._emit_event("idle")
            self._end_operation()

    async def skill(
        self,
        name: str,
        *,
        args: dict[str, Any] | None = None,
        result: type[BaseModel] | Any | None = None,
        role: str | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
        retries: int | None = None,
        stream: bool = False,
        images: list[Any] | tuple[Any, ...] | None = None,
        secrets: list[str] | tuple[str, ...] | None = None,
        commands: list[str] | tuple[str, ...] | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
        cwd: str | None = None,
    ) -> HarnessResult | Any:
        """Run a Markdown-defined skill."""
        await self._refresh_context_from_sandbox()
        skill = self.skills.get(name)
        if skill is None and _looks_like_skill_path(name):
            skill = await self._load_skill_by_path(name)
        if skill is None:
            available = ", ".join(sorted(self.skills)) or "(none)"
            raise KeyError(f"Unknown skill '{name}'. Available skills: {available}")
        prompt = render_skill_prompt(skill, args=args)
        return await self.prompt(
            prompt,
            result=result,
            role=role,
            model=model,
            thinking_level=thinking_level,
            retries=retries,
            stream=stream,
            images=images,
            secrets=secrets,
            commands=commands,
            tools=tools,
            _source="skill",
        )

    async def subagent(
        self,
        prompt: str,
        *,
        result: type[BaseModel] | Any | None = None,
        role: str | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
        images: list[Any] | tuple[Any, ...] | None = None,
        secrets: list[str] | tuple[str, ...] | None = None,
        commands: list[str] | tuple[str, ...] | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
        cwd: str | None = None,
    ) -> HarnessResult | Any:
        """Run a child session with isolated history and shared sandbox."""
        output = await self.task(
            prompt,
            result=result,
            role=role,
            model=model,
            thinking_level=thinking_level,
            images=images,
            secrets=secrets,
            commands=commands,
            tools=tools,
            cwd=cwd,
        )
        return output

    async def task(
        self,
        prompt: str,
        *,
        result: type[BaseModel] | Any | None = None,
        role: str | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
        task_id: str | None = None,
        images: list[Any] | tuple[Any, ...] | None = None,
        secrets: list[str] | tuple[str, ...] | None = None,
        commands: list[str] | tuple[str, ...] | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
        cwd: str | None = None,
    ) -> HarnessResult | Any:
        """Run a child task with shared sandbox and isolated history."""
        self._begin_operation("task")
        child_id = task_id or f"{self.session_id}:task:{uuid.uuid4().hex[:10]}"
        try:
            await self._emit_event(
                "task_start",
                taskId=child_id,
                prompt=prompt,
                role=role or self.session_role,
                cwd=cwd,
                parentSessionId=self.session_id,
            )
            result_value = await self._task_unchecked(
                prompt,
                result=result,
                role=role,
                model=model,
                thinking_level=thinking_level,
                images=images,
                task_id=child_id,
                secrets=secrets,
                commands=commands,
                tools=tools,
                cwd=cwd,
            )
            await self._emit_event("task_end", taskId=child_id, isError=False, result=result_value)
            return result_value
        except asyncio.CancelledError:
            await self._emit_event("task_end", taskId=child_id, isError=True, result="aborted")
            await self._emit_event("aborted", operation="task")
            raise
        except Exception as exc:
            await self._emit_event("task_end", taskId=child_id, isError=True, result=str(exc))
            await self._emit_event("error", error=str(exc))
            raise
        finally:
            await self._emit_event("idle")
            self._end_operation()

    async def _task_unchecked(
        self,
        prompt: str,
        *,
        result: type[BaseModel] | Any | None = None,
        role: str | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
        task_id: str | None = None,
        images: list[Any] | tuple[Any, ...] | None = None,
        secrets: list[str] | tuple[str, ...] | None = None,
        commands: list[str] | tuple[str, ...] | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
        cwd: str | None = None,
    ) -> HarnessResult | Any:
        child_id = task_id or f"{self.session_id}:task:{uuid.uuid4().hex[:10]}"
        task_depth = await self._task_depth()
        max_depth = self.agent.config.max_task_depth
        if max_depth >= 0 and task_depth >= max_depth:
            raise RuntimeError(
                f'Max task depth exceeded for session "{self.session_id}". '
                f"Configured max_task_depth={max_depth}."
            )
        child = await self.agent.session(child_id, role=role or self.session_role)
        await child._set_task_metadata(
            parent_session_id=self.session_id,
            task_id=child_id,
            role=role or self.session_role,
            cwd=cwd,
            task_depth=task_depth + 1,
        )
        await self._add_child_session(child_id)
        child.sandbox = _scope_sandbox(self.sandbox, cwd)
        child.python_backend = self.python_backend
        child._set_context_root(_context_root_for_sandbox(child.sandbox) or self.context_root)
        self.agent._active_child_sessions.setdefault(self.session_id, set()).add(child_id)
        try:
            output = await child.prompt(
                prompt,
                result=result,
                role=role,
                model=model,
                thinking_level=thinking_level,
                images=images,
                secrets=secrets,
                commands=commands,
                tools=tools,
            )
        finally:
            children = self.agent._active_child_sessions.get(self.session_id)
            if children is not None:
                children.discard(child_id)
                if not children:
                    self.agent._active_child_sessions.pop(self.session_id, None)
        await self._append("assistant", f"Subagent {child_id} completed.", source="task")
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
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        secrets: list[str] | tuple[str, ...] | None = None,
        commands: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Run a shell command through the configured sandbox."""
        self._begin_operation("shell")
        command_name, args = _shell_command_parts(command)
        tool_call_id = uuid.uuid4().hex
        try:
            await self._emit_event("command_start", command=command_name, args=args)
            with self._grant_secrets(secrets), self._scope_commands(commands):
                output = await self.agent.backend.shell(
                    command,
                    sandbox=self.sandbox,
                    timeout=timeout,
                    cwd=cwd,
                    env=env,
                )
            await self._append_shell_transcript(
                tool_call_id=tool_call_id,
                command=command,
                cwd=cwd,
                env=env,
                output=output,
                is_error=False,
            )
            await self._emit_event(
                "command_end",
                command=command_name,
                exitCode=int(output.get("exit_code", 0) or 0),
            )
            return output
        except asyncio.CancelledError:
            await self._emit_event("aborted", operation="shell")
            raise
        except Exception as exc:
            await self._append_shell_transcript(
                tool_call_id=tool_call_id,
                command=command,
                cwd=cwd,
                env=env,
                output={"stdout": "", "stderr": str(exc), "exit_code": -1},
                is_error=True,
            )
            await self._emit_event("error", error=str(exc))
            raise
        finally:
            await self._emit_event("idle")
            self._end_operation()

    async def read_file(self, path: str) -> str:
        """Read a file from the session sandbox."""
        self._assert_active()
        return self.sandbox.read_file(path)

    async def read_bytes(self, path: str) -> bytes:
        """Read a file from the session sandbox as bytes."""
        self._assert_active()
        return self.sandbox.read_bytes(path)

    async def write_file(self, path: str, content: str) -> str:
        """Write a file into the session sandbox."""
        self._assert_active()
        return self.sandbox.write_file(path, content)

    async def write_bytes(self, path: str, content: bytes) -> str:
        """Write bytes into the session sandbox."""
        self._assert_active()
        return self.sandbox.write_bytes(path, content)

    async def stat_file(self, path: str) -> dict[str, Any]:
        """Return normalized metadata for a sandbox path."""
        self._assert_active()
        return _file_info_dict(self.sandbox.stat(path))

    async def exists(self, path: str) -> bool:
        """Return whether a path exists in the session sandbox."""
        self._assert_active()
        return bool(self.sandbox.exists(path))

    async def mkdir(self, path: str, *, recursive: bool = True) -> str:
        """Create a directory in the session sandbox."""
        self._assert_active()
        return self.sandbox.mkdir(path, recursive=recursive)

    async def rm(self, path: str, *, recursive: bool = False, force: bool = False) -> str:
        """Remove a file or directory from the session sandbox."""
        self._assert_active()
        return self.sandbox.rm(path, recursive=recursive, force=force)

    async def compact(
        self,
        *,
        keep_recent: int | None = None,
        model: str | None = None,
    ) -> HarnessResult:
        """Summarize older conversation state using token-based compaction.

        By default uses token-based compaction from config:
        - reserve_tokens: tokens to keep free in context (default: 16384)
        - keep_recent_tokens: recent tokens to preserve verbatim (default: 20000)

        Use keep_recent parameter for legacy message-count based compaction.
        """
        self._begin_operation("compact")
        try:
            return await self._compact_unchecked(keep_recent=keep_recent, model=model)
        except Exception as exc:
            await self._emit_event("error", error=str(exc))
            raise
        finally:
            await self._emit_event("idle")
            self._end_operation()

    async def _compact_unchecked(
        self,
        *,
        keep_recent: int | None = None,
        model: str | None = None,
        reason: str = "threshold",
    ) -> HarnessResult:
        compaction_config = self.agent.config.compaction

        if not compaction_config.enabled:
            return HarnessResult(
                text="Compaction is disabled.",
                metadata={"harness": self.agent.backend.name, "compacted": False},
            )

        entries = await self._active_message_entries()
        rows = [(str(entry["role"]), str(entry["content"])) for entry in entries]
        if not rows:
            return HarnessResult(
                text="No messages to compact.",
                metadata={"harness": self.agent.backend.name, "compacted": False},
        )

        if keep_recent is not None:
            return await self._compact_message_based(entries, keep_recent, model, reason)

        return await self._compact_token_based(entries, compaction_config, model, reason)

    async def _compact_token_based(
        self,
        entries: list[dict[str, str]],
        config,
        model: str | None,
        reason: str,
    ) -> HarnessResult:
        """Token-based compaction using reserve_tokens and keep_recent_tokens."""
        rows = [(str(entry["role"]), str(entry["content"])) for entry in entries]
        total_tokens = sum(_estimate_tokens(content) for _, content in rows)
        keep_tokens = config.keep_recent_tokens

        if total_tokens <= keep_tokens:
            return HarnessResult(
                text="No compaction needed (within keep_recent_tokens).",
                metadata={
                    "harness": self.agent.backend.name,
                    "compacted": False,
                    "total_tokens": total_tokens,
                },
            )

        keep_recent = config.keep_recent_tokens
        reserve_tokens = config.reserve_tokens

        tokens_to_summarize = total_tokens - keep_recent
        if tokens_to_summarize <= 0:
            return HarnessResult(
                text="No compaction needed.",
                metadata={"harness": self.agent.backend.name, "compacted": False},
            )

        recent_reversed: list[dict[str, str]] = []
        running_tokens = 0

        for entry in reversed(entries):
            content = str(entry["content"])
            content_tokens = _estimate_tokens(content)
            if running_tokens + content_tokens <= keep_recent:
                recent_reversed.append(entry)
                running_tokens += content_tokens
            else:
                break

        recent = list(reversed(recent_reversed))
        older = entries[: len(entries) - len(recent)]

        if not older:
            return HarnessResult(
                text="No compaction needed (nothing to summarize).",
                metadata={"harness": self.agent.backend.name, "compacted": False},
            )

        await self._emit_event(
            "compaction_start",
            reason=reason,
            estimatedTokens=total_tokens,
        )
        transcript = "\n\n".join(f"{entry['role']}: {entry['content']}" for entry in older)
        prompt = (
            f"Summarize this conversation history for future continuation. "
            f"Total tokens to summarize: ~{_estimate_tokens(transcript)}. "
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

        if recent:
            await self._append_compaction(
                summary.text.strip(),
                first_kept_entry_id=str(recent[0]["id"]),
                tokens_before=total_tokens,
            )
        else:
            await self._replace_messages([("summary", summary.text.strip())])

        summary.metadata["compacted"] = True
        summary.metadata["messages_before"] = len(rows)
        summary.metadata["messages_after"] = len(recent) + 1
        summary.metadata["total_tokens"] = total_tokens
        summary.metadata["reserve_tokens"] = reserve_tokens
        summary.metadata["keep_recent_tokens"] = keep_recent

        await self._emit_event(
            "compaction_end",
            messagesBefore=len(rows),
            messagesAfter=len(recent) + 1,
        )
        return summary

    async def _compact_message_based(
        self,
        entries: list[dict[str, str]],
        keep_recent: int,
        model: str | None,
        reason: str,
    ) -> HarnessResult:
        """Legacy message-count based compaction."""
        rows = [(str(entry["role"]), str(entry["content"])) for entry in entries]
        if len(rows) <= keep_recent:
            return HarnessResult(
                text="No compaction needed.",
                metadata={"harness": self.agent.backend.name, "compacted": False},
            )
        split = max(len(rows) - max(keep_recent, 0), 0)
        older = entries[:split]
        recent = entries[split:]
        estimated_tokens = sum(_estimate_tokens(content) for _, content in rows)
        await self._emit_event(
            "compaction_start",
            reason=reason,
            estimatedTokens=estimated_tokens,
        )
        transcript = "\n\n".join(f"{entry['role']}: {entry['content']}" for entry in older)
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
        if recent:
            await self._append_compaction(
                summary.text.strip(),
                first_kept_entry_id=str(recent[0]["id"]),
                tokens_before=estimated_tokens,
            )
        else:
            await self._replace_messages([("summary", summary.text.strip())])
        summary.metadata["compacted"] = True
        summary.metadata["messages_before"] = len(rows)
        summary.metadata["messages_after"] = len(recent) + 1
        await self._emit_event(
            "compaction_end",
            messagesBefore=len(rows),
            messagesAfter=len(recent) + 1,
        )
        return summary

    async def _maybe_auto_compact(self, *, model: str | None) -> None:
        config = self.agent.config.compaction
        if not config.enabled or config.context_window_tokens <= 0:
            return
        rows = await self._all_messages()
        if not rows:
            return
        estimated_tokens = sum(_estimate_tokens(content) for _, content in rows)
        threshold = max(config.context_window_tokens - config.reserve_tokens, 0)
        if estimated_tokens <= threshold:
            return
        await self._compact_unchecked(model=model, reason="threshold")

    def close(self) -> None:
        """Mark this session handle closed."""
        self.deleted = True

    async def abort(self) -> bool:
        """Cancel the active operation and active child tasks for this session."""
        return await self.agent._abort_session_tree(self.session_id)

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
            await db.execute(
                "create table if not exists session_state "
                "(id integer primary key check (id = 1), data text not null)"
            )
            await db.commit()

    async def _append(
        self,
        role: str,
        content: str,
        *,
        source: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            history = await self._load_history(db)
            history.append_message(role, content, source=source)
            await db.execute(
                "insert into messages(role, content) values (?, ?)",
                (role, content),
            )
            await self._save_history(db, history)
            await db.commit()

    async def _append_shell_transcript(
        self,
        *,
        tool_call_id: str,
        command: str,
        cwd: str | None,
        env: dict[str, str] | None,
        output: dict[str, Any],
        is_error: bool,
    ) -> None:
        args: dict[str, Any] = {"command": command}
        if cwd is not None:
            args["cwd"] = cwd
        if env is not None:
            args["env"] = env
        assistant = {
            "type": "toolCall",
            "id": tool_call_id,
            "name": "bash",
            "arguments": args,
        }
        tool_result = {
            "toolCallId": tool_call_id,
            "toolName": "bash",
            "isError": is_error,
            "content": output,
        }
        await self._append(
            "user",
            f"Run this shell command:\n\n```bash\n{command}\n```",
            source="shell",
        )
        await self._append("assistant", json.dumps(assistant, sort_keys=True), source="shell")
        await self._append("toolResult", json.dumps(tool_result, sort_keys=True), source="shell")

    async def _messages(self) -> list[tuple[str, str]]:
        rows = await self._all_messages()
        return rows[-12:]

    async def _all_messages(self) -> list[tuple[str, str]]:
        async with aiosqlite.connect(self.db_path) as db:
            history = await self._load_history(db)
        return history.build_context()

    async def _replace_messages(self, rows: list[tuple[str, str]]) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            history = SessionHistory.from_rows(rows)
            await db.execute("delete from messages")
            await db.executemany(
                "insert into messages(role, content) values (?, ?)",
                rows,
            )
            await self._save_history(db, history)
            await db.commit()

    async def _active_message_entries(self) -> list[dict[str, str]]:
        async with aiosqlite.connect(self.db_path) as db:
            history = await self._load_history(db)
        entries: list[dict[str, str]] = []
        for entry in history.message_entries():
            message = entry.get("message") or {}
            entries.append(
                {
                    "id": str(entry.get("id")),
                    "role": str(message.get("role") or "user"),
                    "content": str(message.get("content") or ""),
                }
            )
        return entries

    async def _append_compaction(
        self,
        summary: str,
        *,
        first_kept_entry_id: str,
        tokens_before: int,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            history = await self._load_history(db)
            history.append_compaction(
                summary=summary,
                first_kept_entry_id=first_kept_entry_id,
                tokens_before=tokens_before,
            )
            context = history.build_context()
            await db.execute("delete from messages")
            await db.executemany(
                "insert into messages(role, content) values (?, ?)",
                context,
            )
            await self._save_history(db, history)
            await db.commit()

    async def _load_history(self, db: aiosqlite.Connection) -> SessionHistory:
        cursor = await db.execute("select data from session_state where id = 1")
        row = await cursor.fetchone()
        if row is not None:
            data = json.loads(str(row[0]))
            self.metadata.update(data.get("metadata") or {})
            return SessionHistory.from_data(data)

        cursor = await db.execute("select role, content from messages order by id")
        rows = [(str(role), str(content)) for role, content in await cursor.fetchall()]
        history = SessionHistory.from_rows(rows)
        await self._save_history(db, history)
        return history

    async def _save_history(
        self,
        db: aiosqlite.Connection,
        history: SessionHistory,
    ) -> None:
        metadata = {**self.metadata, "session_id": self.session_id}
        data = history.to_data(metadata=metadata)
        await db.execute(
            "insert or replace into session_state(id, data) values (1, ?)",
            (json.dumps(data, sort_keys=True),),
        )

    async def _read_persisted_metadata(self) -> dict[str, Any]:
        if not self.db_path.exists():
            return dict(self.metadata)
        async with aiosqlite.connect(self.db_path) as db:
            try:
                cursor = await db.execute("select data from session_state where id = 1")
                row = await cursor.fetchone()
            except aiosqlite.OperationalError:
                return dict(self.metadata)
        if row is None:
            return dict(self.metadata)
        data = json.loads(str(row[0]))
        metadata = data.get("metadata") if isinstance(data, dict) else None
        return {**self.metadata, **metadata} if isinstance(metadata, dict) else dict(self.metadata)

    async def _write_metadata(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            history = await self._load_history(db)
            await self._save_history(db, history)
            await db.commit()

    async def _set_task_metadata(
        self,
        *,
        parent_session_id: str,
        task_id: str,
        role: str | None,
        cwd: str | None,
        task_depth: int,
    ) -> None:
        self.metadata.update(
            {
                "parent_session_id": parent_session_id,
                "task_id": task_id,
                "role": role,
                "cwd": cwd,
                "task_depth": task_depth,
            }
        )
        await self._write_metadata()

    async def _task_depth(self) -> int:
        metadata = await self._read_persisted_metadata()
        return int(metadata.get("task_depth") or 0)

    async def _add_child_session(self, child_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            history = await self._load_history(db)
            children = list(self.metadata.get("children") or [])
            if child_id not in children:
                children.append(child_id)
            self.metadata["children"] = children
            await self._save_history(db, history)
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
        thinking_level: str | None = None,
        images: list[Any] | tuple[Any, ...] | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
        stream: bool,
    ) -> HarnessResult:
        config = self.agent.config
        effective_role = self._effective_role(role)
        resolved_model = model or (effective_role.model if effective_role else None)
        resolved_thinking_level = (
            thinking_level
            or (effective_role.thinking_level if effective_role else None)
            or config.thinking_level
        )
        if self.context_root != config.root:
            config = PyFlueConfig(
                **{
                    **config.__dict__,
                    "root": self.context_root,
                    "skills_dir": None,
                    "roles_dir": None,
                }
            )
        if resolved_model is not None:
            config = PyFlueConfig(**{**config.__dict__, "model": resolved_model})
        if resolved_thinking_level is not None:
            config = PyFlueConfig(**{**config.__dict__, "thinking_level": resolved_thinking_level})
        merged_tools = await self._merge_tools(tools)
        return await self.agent.backend.run(
            prompt=prompt,
            system_prompt=self.instructions,
            config=config,
            skills=self.skills,
            sandbox=self.sandbox,
            session_id=self.session_id,
            python_backend=self.python_backend,
            tools=merged_tools,
            images=images,
            stream=stream,
        )

    async def _stream_backend(
        self,
        prompt: str,
        *,
        model: str | None,
        role: str | None = None,
        thinking_level: str | None = None,
        images: list[Any] | tuple[Any, ...] | None = None,
        tools: list[Any] | tuple[Any, ...] | None = None,
    ) -> AsyncIterator[PyFlueEvent]:
        config = self.agent.config
        effective_role = self._effective_role(role)
        resolved_model = model or (effective_role.model if effective_role else None)
        resolved_thinking_level = (
            thinking_level
            or (effective_role.thinking_level if effective_role else None)
            or config.thinking_level
        )
        if self.context_root != config.root:
            config = PyFlueConfig(
                **{
                    **config.__dict__,
                    "root": self.context_root,
                    "skills_dir": None,
                    "roles_dir": None,
                }
            )
        if resolved_model is not None:
            config = PyFlueConfig(**{**config.__dict__, "model": resolved_model})
        if resolved_thinking_level is not None:
            config = PyFlueConfig(**{**config.__dict__, "thinking_level": resolved_thinking_level})
        merged_tools = await self._merge_tools(tools)
        async for event in self.agent.backend.stream(
            prompt=prompt,
            system_prompt=self.instructions,
            config=config,
            skills=self.skills,
            sandbox=self.sandbox,
            session_id=self.session_id,
            python_backend=self.python_backend,
            tools=merged_tools,
            images=images,
        ):
            yield event

    async def _merge_tools(self, extra_tools: list[Any] | tuple[Any, ...] | None) -> list[Any]:
        """Merge built-in, agent-level MCP, and per-call tools."""
        await self.agent._ensure_mcp_servers()
        result = self._builtin_tools()
        command_tools = self._command_tools()
        agent_tools = [to_callable_tool(tool) for tool in self.agent.tools]
        extra_tool_list = [to_callable_tool(tool) for tool in list(extra_tools or ())]
        self._validate_tool_names(
            builtin_tools=result,
            agent_tools=[*agent_tools, *command_tools],
            mcp_tools=self.agent._mcp_tools,
            extra_tools=extra_tool_list,
        )
        result.extend(agent_tools)
        result.extend(command_tools)
        result.extend(self.agent._mcp_tools)
        result.extend(extra_tool_list)
        return result

    def _command_tools(self) -> list[Any]:
        tools: list[Any] = []
        for command in self.agent.command_tools:
            tools.append(_command_to_tool(command, self))
        return tools

    def _builtin_tools(self) -> list[Any]:
        session = self

        async def read(
            path: str,
            offset: int | None = None,
            limit: int | None = None,
        ) -> str:
            """Read a file or list a directory in the session sandbox."""
            content = session.sandbox.read_file(path, offset=offset or 1, limit=limit)
            return _truncate_tool_text(
                content,
                label=f"Read output for {path}",
                continuation_hint="Use offset and limit to read the next section.",
            )

        async def write(path: str, content: str) -> str:
            """Write content to a file in the session sandbox."""
            return session.sandbox.write_file(path, content)

        async def edit(
            path: str,
            old_text: str,
            new_text: str,
            replace_all: bool = False,
        ) -> str:
            """Edit a file by replacing exact text in the session sandbox."""
            return session.sandbox.edit_file(
                path,
                old_text,
                new_text,
                replace_all=replace_all,
            )

        async def stat(path: str) -> dict[str, Any]:
            """Return file or directory metadata from the session sandbox."""
            return _file_info_dict(session.sandbox.stat(path))

        async def exists(path: str) -> bool:
            """Return whether a path exists in the session sandbox."""
            return bool(session.sandbox.exists(path))

        async def mkdir(path: str, recursive: bool = True) -> str:
            """Create a directory in the session sandbox."""
            return session.sandbox.mkdir(path, recursive=recursive)

        async def rm(path: str, recursive: bool = False, force: bool = False) -> str:
            """Remove a file or directory in the session sandbox."""
            return session.sandbox.rm(path, recursive=recursive, force=force)

        async def bash(
            command: str,
            timeout: int | None = 120,
            cwd: str | None = None,
            env: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            """Execute a shell command in the session sandbox."""
            command_name, args = _shell_command_parts(command)
            await session._emit_event("command_start", command=command_name, args=args)
            try:
                output = await session.agent.backend.shell(
                    command,
                    sandbox=session.sandbox,
                    timeout=timeout,
                    cwd=cwd,
                    env=env,
                )
                await session._emit_event(
                    "command_end",
                    command=command_name,
                    exitCode=int(output.get("exit_code", 0) or 0),
                )
                return _truncate_shell_output(output)
            except Exception as exc:
                await session._emit_event("command_end", command=command_name, exitCode=-1)
                await session._emit_event("error", error=str(exc))
                raise

        async def grep(
            pattern: str,
            path: str = ".",
            include: str | None = None,
        ) -> str:
            """Search files in the session sandbox using a regular expression."""
            return session.sandbox.grep(pattern, path=path, include=include)

        async def glob(pattern: str) -> str:
            """Find files in the session sandbox by glob pattern."""
            return session.sandbox.glob(pattern)

        async def task(
            prompt: str,
            description: str | None = None,
            role: str | None = None,
            cwd: str | None = None,
        ) -> str:
            """Delegate a focused task to a child session with isolated history."""
            del description
            child_id = f"{session.session_id}:task:{uuid.uuid4().hex[:10]}"
            await session._emit_event(
                "task_start",
                taskId=child_id,
                prompt=prompt,
                role=role or session.session_role,
                cwd=cwd,
                parentSessionId=session.session_id,
            )
            try:
                result = await session._task_unchecked(
                    prompt,
                    role=role,
                    task_id=child_id,
                    cwd=cwd,
                )
                text = result.text if isinstance(result, HarnessResult) else str(result)
                await session._emit_event("task_end", taskId=child_id, isError=False, result=text)
                return text
            except Exception as exc:
                await session._emit_event("task_end", taskId=child_id, isError=True, result=str(exc))
                await session._emit_event("error", error=str(exc))
                raise

        return [read, write, edit, stat, exists, mkdir, rm, bash, grep, glob, task]

    def _validate_tool_names(
        self,
        *,
        builtin_tools: list[Any],
        agent_tools: list[Any],
        mcp_tools: list[Any],
        extra_tools: list[Any],
    ) -> None:
        names = {_tool_name(tool) for tool in builtin_tools}
        for tool in [*agent_tools, *mcp_tools, *extra_tools]:
            name = _tool_name(tool)
            if not name:
                continue
            if name in BUILTIN_TOOL_NAMES and tool in [*agent_tools, *extra_tools]:
                raise ValueError(
                    f'Custom tool "{name}" conflicts with a built-in tool. '
                    f"Built-in tools: {', '.join(sorted(BUILTIN_TOOL_NAMES))}"
                )
            if name in names:
                raise ValueError(f'Duplicate tool name "{name}". Tool names must be unique.')
            names.add(name)

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
        scoped = tuple(str(item) for item in [*self.agent.commands, *(commands or ())])
        if not scoped:
            yield
            return
        previous = self.sandbox.policy
        merged = tuple(dict.fromkeys([*previous.allowed_commands, *scoped]))
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
        thinking_level: str | None,
        images: list[Any] | tuple[Any, ...] | None,
        tools: list[Any] | tuple[Any, ...] | None,
        retries: int,
        stream: bool,
    ) -> Any:
        last_error: Exception | None = None
        current = output
        for attempt in range(max(retries, 0) + 1):
            try:
                parsed = _parse_typed_result(current.text, result)
                return PromptResultResponse(
                    result=parsed,
                    text=current.text,
                    usage=current.usage,
                    model=current.model,
                    raw=current.raw,
                    metadata=dict(current.metadata),
                )
            except Exception as exc:
                last_error = exc
                if attempt >= retries:
                    break
                schema = TypeAdapter(result).json_schema()
                repair_prompt = (
                    f"{original_prompt}\n\n"
                    "The previous response failed structured output validation.\n"
                    f"Validation error: {exc}\n\n"
                    "Extract the intended answer and return a corrected JSON value. "
                    "Do not include Markdown, commentary, or extra keys. Return only "
                    "valid JSON between the required result delimiters.\n\n"
                    f"{RESULT_START}\n"
                    "{...valid JSON...}\n"
                    f"{RESULT_END}\n\n"
                    "Required JSON schema:\n"
                    f"{json.dumps(schema, indent=2, sort_keys=True)}"
                )
                current = await self._run_backend(
                    repair_prompt,
                    model=model,
                    role=role,
                    thinking_level=thinking_level,
                    images=images,
                    tools=tools,
                    stream=stream,
                )
                await self._append("assistant", current.text, source="retry")
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
        selected = self.roles.get(role_name)
        if selected is None:
            available = ", ".join(sorted(self.roles)) or "(none)"
            raise KeyError(f"Unknown role '{role_name}'. Available roles: {available}")
        return selected

    def _assert_active(self) -> None:
        if self.deleted:
            raise RuntimeError(f"Session is closed: {self.session_id}")

    def _set_context_root(self, root: Path) -> None:
        self.context_root = root
        self.instructions = load_project_instructions(root)
        self.skills = load_skills(root, None)
        self.roles = load_roles(root, None)

    async def _refresh_context_from_sandbox(self) -> None:
        discovered = _discover_sandbox_context(self.sandbox)
        if discovered is None:
            return
        instructions, skills = discovered
        if instructions:
            self.instructions = instructions
        if skills:
            self.skills = {**self.skills, **skills}

    async def _load_skill_by_path(self, name: str) -> Any | None:
        sandbox_skill = _load_sandbox_skill_by_path(self.sandbox, name)
        if sandbox_skill is not None:
            return sandbox_skill
        return load_skill_by_path(self.context_root, name)

    async def _emit_event(self, event_type: str, **data: Any) -> None:
        callback = self.agent.on_event
        if callback is None:
            return
        payload = {"session_id": self.session_id, **data}
        result = callback(PyFlueEvent(event_type, payload))
        if inspect.isawaitable(result):
            await result

    def _begin_operation(self, operation: str) -> None:
        self._assert_active()
        active_operation = self.agent._active_operations.get(self.session_id) or self.active_operation
        if active_operation is not None:
            raise RuntimeError(
                f'Session "{self.session_id}" is already running {active_operation}. '
                "Start another session for parallel conversation branches."
            )
        self.active_operation = operation
        self.agent._active_operations[self.session_id] = operation
        task = asyncio.current_task()
        if task is not None:
            self.agent._active_tasks[self.session_id] = task

    def _end_operation(self) -> None:
        self.active_operation = None
        task = asyncio.current_task()
        if self.agent._active_tasks.get(self.session_id) is task:
            self.agent._active_tasks.pop(self.session_id, None)
        self.agent._active_operations.pop(self.session_id, None)


def _parse_typed_result(text: str, result: Any) -> Any:
    raw = _extract_structured_text(text or "")
    value: Any = raw
    if raw.startswith("{") or raw.startswith("["):
        value = json.loads(raw)
    try:
        return TypeAdapter(result).validate_python(value)
    except Exception as exc:
        raise ValueError(
            "Structured result did not match the requested schema. "
            f"Extracted value: {raw[:500]}"
        ) from exc


def _extract_structured_text(text: str) -> str:
    matches = list(_RESULT_RE.finditer(text or ""))
    if matches:
        return matches[-1].group("body").strip()
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return stripped
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", stripped, flags=re.IGNORECASE)
    for candidate in reversed(fenced):
        candidate = candidate.strip()
        if candidate.startswith("{") or candidate.startswith("["):
            return candidate
    for opener, closer in (("{", "}"), ("[", "]")):
        start = stripped.find(opener)
        end = stripped.rfind(closer)
        if start != -1 and end > start:
            candidate = stripped[start : end + 1].strip()
            try:
                json.loads(candidate)
            except Exception:
                continue
            return candidate
    return stripped


def _command_to_tool(command: PyFlueCommand, session: PyFlueSession) -> Any:
    async def run(**kwargs: Any) -> Any:
        if command.callable is not None:
            try:
                result = command.callable(**kwargs)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                return _normalize_command_result(exc)
            return _normalize_command_result(result)
        if command.command is None:
            raise RuntimeError(f'Command "{command.name}" has no implementation.')
        result = await session.shell(
            command.command,
            timeout=command.timeout,
            cwd=command.cwd,
            env=command.env,
            commands=[_shell_command_parts(command.command)[0]],
        )
        return _normalize_command_result(result)

    run.__name__ = command.name
    run.__doc__ = command.description or command.command or f"Run {command.name}."
    if command.schema:
        run.__pyflue_schema__ = command.schema
    return run


def _normalize_command_result(result: Any) -> Any:
    if result is None:
        return ""
    if isinstance(result, Exception):
        return {
            "error": str(result),
            "type": type(result).__name__,
        }
    if isinstance(result, BaseModel):
        return result.model_dump()
    if isinstance(result, (str, int, float, bool, list, tuple, dict)):
        return result
    return str(result)


def _truncate_shell_output(output: dict[str, Any]) -> dict[str, Any]:
    truncated = False
    result = dict(output)
    for key in ("stdout", "stderr"):
        value = result.get(key)
        if not isinstance(value, str):
            continue
        text, was_truncated = _truncate_tool_text_parts(
            value,
            label=f"Command {key}",
            continuation_hint="Run a narrower command or redirect output to a file and read it in sections.",
        )
        result[key] = text
        truncated = truncated or was_truncated
    if truncated:
        result["truncated"] = True
    return result


def _truncate_tool_text(
    text: str,
    *,
    label: str,
    continuation_hint: str,
) -> str:
    truncated, _ = _truncate_tool_text_parts(
        text,
        label=label,
        continuation_hint=continuation_hint,
    )
    return truncated


def _truncate_tool_text_parts(
    text: str,
    *,
    label: str,
    continuation_hint: str,
) -> tuple[str, bool]:
    lines = text.splitlines()
    too_many_lines = len(lines) > MAX_TOOL_OUTPUT_LINES
    too_many_chars = len(text) > MAX_TOOL_OUTPUT_CHARS
    if not too_many_lines and not too_many_chars:
        return text, False
    selected = lines[:MAX_TOOL_OUTPUT_LINES]
    truncated = "\n".join(selected)
    if len(truncated) > MAX_TOOL_OUTPUT_CHARS:
        truncated = truncated[:MAX_TOOL_OUTPUT_CHARS]
    omitted_lines = max(len(lines) - len(selected), 0)
    omitted_chars = max(len(text) - len(truncated), 0)
    message = (
        f"\n\n[{label} truncated: "
        f"{omitted_lines} line(s) and {omitted_chars} character(s) omitted. "
        f"{continuation_hint}]"
    )
    return truncated + message, True


def _shell_command_parts(command: str) -> tuple[str, list[str]]:
    try:
        parts = shlex.split(command)
    except ValueError:
        return command, []
    if not parts:
        return "", []
    return parts[0], parts[1:]


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "__name__", None) or getattr(tool, "name", "") or "")


def _discover_sandbox_context(sandbox: Any) -> tuple[str, dict[str, Any]] | None:
    instructions = _read_sandbox_instructions(sandbox)
    skills = _discover_sandbox_skills(sandbox)
    if not instructions and not skills:
        return None
    listing = _sandbox_directory_listing(sandbox)
    if listing:
        instructions = "\n\n".join(
            part
            for part in [
                instructions,
                "Directory structure:\n" + "\n".join(listing),
            ]
            if part
        )
    return instructions, skills


def _read_sandbox_instructions(sandbox: Any) -> str:
    parts: list[str] = []
    for filename in ("AGENTS.md", "CLAUDE.md"):
        try:
            content = sandbox.read_file(filename)
        except Exception:
            continue
        if content.strip():
            parts.append(content.strip())
    return "\n\n".join(parts)


def _discover_sandbox_skills(sandbox: Any) -> dict[str, Any]:
    skills: dict[str, Any] = {}
    for path in _iter_sandbox_markdown_paths(sandbox, ".agents/skills"):
        try:
            skill = _parse_sandbox_skill(sandbox, path)
        except Exception:
            continue
        skills[skill.name] = skill
    return skills


def _iter_sandbox_markdown_paths(sandbox: Any, root: str) -> list[str]:
    pending = [root]
    seen: set[str] = set()
    markdown: list[str] = []
    while pending:
        current = pending.pop(0)
        if current in seen:
            continue
        seen.add(current)
        try:
            entries = sandbox.list_files(current)
        except Exception:
            continue
        for entry in entries:
            path = str(getattr(entry, "path", ""))
            if not path:
                continue
            normalized = path.removeprefix("/")
            if bool(getattr(entry, "is_dir", False)):
                pending.append(normalized)
            elif normalized.lower().endswith((".md", ".markdown")):
                markdown.append(normalized)
    return sorted(markdown)


def _parse_sandbox_skill(sandbox: Any, path: str) -> Any:
    content = sandbox.read_file(path)
    parts = Path(path).parts
    default_name = Path(path).stem
    if Path(path).name.upper() == "SKILL.MD" and len(parts) >= 2:
        default_name = parts[-2]
    return parse_skill_text(content, default_name=default_name)


def _load_sandbox_skill_by_path(sandbox: Any, rel_path: str) -> Any | None:
    normalized = str(Path(rel_path).as_posix()).lstrip("/")
    if normalized.startswith(".."):
        raise ValueError(f"Skill path escapes skills directory: {rel_path}")
    path = f".agents/skills/{normalized}"
    try:
        return _parse_sandbox_skill(sandbox, path)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _sandbox_directory_listing(sandbox: Any) -> list[str]:
    try:
        entries = sandbox.list_files(".")
    except Exception:
        return []
    return sorted(str(getattr(entry, "path", "")).removeprefix("/") for entry in entries)


def _looks_like_skill_path(name: str) -> bool:
    return "/" in name or name.lower().endswith((".md", ".markdown"))


def _scope_sandbox(sandbox: Any, cwd: str | None) -> Any:
    if not cwd:
        return sandbox
    if isinstance(sandbox, VirtualSandbox):
        root = sandbox.resolve(cwd)
        if not root.is_dir():
            raise NotADirectoryError(cwd)
        return VirtualSandbox(root=root, policy=sandbox.policy, env=getattr(sandbox, "env", None))
    return _ScopedSandbox(sandbox, cwd)


def _context_root_for_sandbox(sandbox: Any) -> Path | None:
    root = getattr(sandbox, "root", None)
    return root if isinstance(root, Path) else None


def _file_info_dict(info: Any) -> dict[str, Any]:
    return {
        "path": info.path,
        "is_dir": bool(info.is_dir),
        "is_file": bool(getattr(info, "is_file", False)),
        "is_directory": bool(info.is_dir),
        "is_symbolic_link": bool(getattr(info, "is_symbolic_link", False)),
        "isDirectory": bool(info.is_dir),
        "isFile": bool(getattr(info, "is_file", False)),
        "isSymbolicLink": bool(getattr(info, "is_symbolic_link", False)),
        "size": int(info.size or 0),
        "mtime": getattr(info, "mtime", None),
    }


class PyFlueFs:
    """Out-of-band filesystem access for a PyFlue session sandbox.

    This mirrors Flue's `session.fs` surface while keeping PyFlue's existing
    direct session helpers (`read_file`, `write_file`, etc.) available.
    """

    def __init__(self, session: PyFlueSession):
        self._session = session

    async def read_file(self, path: str) -> str:
        """Read a UTF-8 file from the session sandbox."""
        self._session._assert_active()
        return self._session.sandbox.read_file(path)

    async def read_bytes(self, path: str) -> bytes:
        """Read a file as raw bytes from the session sandbox."""
        self._session._assert_active()
        return self._session.sandbox.read_bytes(path)

    async def read_file_buffer(self, path: str) -> bytes:
        """Alias for Flue's `readFileBuffer`."""
        return await self.read_bytes(path)

    async def write_file(self, path: str, content: str | bytes) -> None:
        """Write text or bytes to the session sandbox."""
        self._session._assert_active()
        if isinstance(content, bytes):
            self._session.sandbox.write_bytes(path, content)
        else:
            self._session.sandbox.write_file(path, content)

    async def stat(self, path: str) -> dict[str, Any]:
        """Return normalized metadata for a sandbox path."""
        self._session._assert_active()
        return _file_info_dict(self._session.sandbox.stat(path))

    async def readdir(self, path: str) -> list[str]:
        """List directory entry names."""
        self._session._assert_active()
        entries = self._session.sandbox.list_files(path)
        prefix = str(_file_info_dict(self._session.sandbox.stat(path))["path"]).rstrip("/")
        names: list[str] = []
        for entry in entries:
            entry_path = str(getattr(entry, "path", ""))
            name = entry_path.removeprefix(prefix).strip("/")
            names.append(name or entry_path.strip("/"))
        return sorted(names)

    async def exists(self, path: str) -> bool:
        """Return whether a file or directory exists."""
        self._session._assert_active()
        return bool(self._session.sandbox.exists(path))

    async def mkdir(self, path: str, *, recursive: bool = True) -> None:
        """Create a directory in the session sandbox."""
        self._session._assert_active()
        self._session.sandbox.mkdir(path, recursive=recursive)

    async def rm(self, path: str, *, recursive: bool = False, force: bool = False) -> None:
        """Remove a file or directory from the session sandbox."""
        self._session._assert_active()
        self._session.sandbox.rm(path, recursive=recursive, force=force)

    readFile = read_file
    readBytes = read_bytes
    readFileBuffer = read_file_buffer
    writeFile = write_file


class _AgentFlueFs:
    """Default-session filesystem access for a PyFlue agent."""

    def __init__(self, agent: PyFlueAgent):
        self._agent = agent

    async def _fs(self) -> PyFlueFs:
        return (await self._agent.session()).fs

    async def read_file(self, path: str) -> str:
        return await (await self._fs()).read_file(path)

    async def read_bytes(self, path: str) -> bytes:
        return await (await self._fs()).read_bytes(path)

    async def read_file_buffer(self, path: str) -> bytes:
        return await (await self._fs()).read_file_buffer(path)

    async def write_file(self, path: str, content: str | bytes) -> None:
        await (await self._fs()).write_file(path, content)

    async def stat(self, path: str) -> dict[str, Any]:
        return await (await self._fs()).stat(path)

    async def readdir(self, path: str) -> list[str]:
        return await (await self._fs()).readdir(path)

    async def exists(self, path: str) -> bool:
        return await (await self._fs()).exists(path)

    async def mkdir(self, path: str, *, recursive: bool = True) -> None:
        await (await self._fs()).mkdir(path, recursive=recursive)

    async def rm(self, path: str, *, recursive: bool = False, force: bool = False) -> None:
        await (await self._fs()).rm(path, recursive=recursive, force=force)

    readFile = read_file
    readBytes = read_bytes
    readFileBuffer = read_file_buffer
    writeFile = write_file


class _ScopedSandbox:
    """Path-scoped sandbox view for child tasks on non-local providers."""

    provider = "scoped"

    def __init__(self, base: Any, cwd: str):
        self.base = base
        self.cwd = cwd.strip("/") or "."

    @property
    def id(self) -> str:
        return f"{getattr(self.base, 'id', 'sandbox')}:{self.cwd}"

    @property
    def policy(self) -> Any:
        return self.base.policy

    @policy.setter
    def policy(self, value: Any) -> None:
        self.base.policy = value

    @property
    def env(self) -> Any:
        return getattr(self.base, "env", None)

    def list_files(self, path: str = ".") -> Any:
        return self.base.list_files(self._path(path))

    def stat(self, path: str) -> Any:
        return self.base.stat(self._path(path))

    def exists(self, path: str) -> bool:
        return bool(self.base.exists(self._path(path)))

    def read_file(self, path: str, *, offset: int = 1, limit: int | None = None) -> str:
        return self.base.read_file(self._path(path), offset=offset, limit=limit)

    def read_bytes(self, path: str) -> bytes:
        return self.base.read_bytes(self._path(path))

    def write_file(self, path: str, content: str) -> str:
        return self.base.write_file(self._path(path), content)

    def write_bytes(self, path: str, content: bytes) -> str:
        return self.base.write_bytes(self._path(path), content)

    def mkdir(self, path: str, *, recursive: bool = True) -> str:
        return self.base.mkdir(self._path(path), recursive=recursive)

    def rm(self, path: str, *, recursive: bool = False, force: bool = False) -> str:
        return self.base.rm(self._path(path), recursive=recursive, force=force)

    def edit_file(self, path: str, old: str, new: str, *, replace_all: bool = False) -> str:
        return self.base.edit_file(self._path(path), old, new, replace_all=replace_all)

    def grep(self, pattern: str, *, path: str = ".", include: str | None = None) -> str:
        return self.base.grep(pattern, path=self._path(path), include=include)

    def glob(self, pattern: str) -> str:
        return self.base.glob(self._path(pattern))

    def shell(
        self,
        command: str,
        *,
        timeout: int | None = 120,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self.base.shell(command, timeout=timeout, cwd=self._path(cwd or "."), env=env)

    def _path(self, path: str) -> str:
        raw = str(path or ".")
        if raw in {".", "/", "/workspace"}:
            return self.cwd
        if raw.startswith("/workspace/"):
            raw = raw.removeprefix("/workspace/")
        elif raw.startswith("/"):
            raw = raw[1:]
        return f"{self.cwd.rstrip('/')}/{raw}"


def _is_context_overflow_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = [
        "context length",
        "context window",
        "maximum context",
        "token limit",
        "too many tokens",
        "context_length_exceeded",
    ]
    return any(marker in text for marker in markers)
