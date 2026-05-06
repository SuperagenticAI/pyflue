from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import aiosqlite
import pytest
from pydantic import BaseModel

from pyflue.core import PyFlueAgent
from pyflue.harnesses.base import HarnessBackend
from pyflue.sandbox import SandboxPolicy
from pyflue.types import (
    CompactionConfig,
    HarnessResult,
    PyFlueCommand,
    PyFlueConfig,
    PyFlueEvent,
    define_command,
)


class _Result(BaseModel):
    summary: str


class _FakeBackend(HarnessBackend):
    name = "fake"

    def __init__(self, responses: list[str] | None = None):
        self.calls = []
        self.responses = responses or ['---RESULT_START---\n{"summary": "ok"}\n---RESULT_END---']

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        text = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(text, Exception):
            raise text
        return HarnessResult(
            text=text,
            raw=SimpleNamespace(),
            metadata={"harness": "fake"},
        )


class _ToolEventBackend(_FakeBackend):
    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        yield PyFlueEvent("tool_start", {"toolName": "read", "toolCallId": "1", "args": {"path": "x"}})
        yield PyFlueEvent("tool_end", {"toolName": "read", "toolCallId": "1", "isError": False, "result": "ok"})
        yield PyFlueEvent("delta", {"text": "done"})
        yield PyFlueEvent("end", {"text": "done", "metadata": {"harness": "fake"}})


class _SlowBackend(_FakeBackend):
    async def run(self, **kwargs):
        self.calls.append(kwargs)
        await asyncio.sleep(10)
        return HarnessResult(text="done", metadata={"harness": "fake"})


async def _session_metadata(session):
    async with aiosqlite.connect(session.db_path) as db:
        cursor = await db.execute("select data from session_state where id = 1")
        row = await cursor.fetchone()
    return json.loads(row[0])["metadata"]


@pytest.mark.asyncio
async def test_session_prompt_persists_and_parses_result(tmp_path):
    (tmp_path / "AGENTS.md").write_text("System", encoding="utf-8")
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config)
    agent.backend = _FakeBackend()

    session = await agent.session("s1")
    result = await session.prompt("hello", result=_Result)

    assert result.summary == "ok"
    assert agent.backend.calls[0]["system_prompt"] == "System"


@pytest.mark.asyncio
async def test_session_skill_uses_markdown_skill(tmp_path):
    skill_dir = tmp_path / ".agents" / "skills"
    skill_dir.mkdir(parents=True)
    (skill_dir / "triage.md").write_text(
        "---\nname: triage\n---\nDo triage.",
        encoding="utf-8",
    )
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config)
    agent.backend = _FakeBackend()

    await (await agent.session("s1")).skill("triage", args={"issue": 1})

    assert "Do triage" in agent.backend.calls[0]["prompt"]
    assert '"issue": 1' in agent.backend.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_session_discovers_context_from_sandbox(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(allow_write=True),
    )
    agent.backend = _FakeBackend(responses=["ok"])
    session = await agent.session("s1")
    await session.write_file("AGENTS.md", "Sandbox instructions")
    await session.write_file(".agents/skills/review/SKILL.md", "Review from sandbox.")

    await session.prompt("hello")

    assert "Sandbox instructions" in agent.backend.calls[0]["system_prompt"]
    assert "Directory structure:" in agent.backend.calls[0]["system_prompt"]
    assert "review" in agent.backend.calls[0]["skills"]


@pytest.mark.asyncio
async def test_session_skill_loads_relative_sandbox_skill_path(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(allow_write=True),
    )
    agent.backend = _FakeBackend(responses=["ok"])
    session = await agent.session("s1")
    await session.write_file(".agents/skills/triage/reproduce.md", "Reproduce from sandbox.")

    await session.skill("triage/reproduce.md")

    assert "Reproduce from sandbox." in agent.backend.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_session_skill_records_skill_source(tmp_path):
    skill_dir = tmp_path / ".agents" / "skills"
    skill_dir.mkdir(parents=True)
    (skill_dir / "triage.md").write_text(
        "---\nname: triage\n---\nDo triage.",
        encoding="utf-8",
    )
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config)
    agent.backend = _FakeBackend()
    session = await agent.session("s1")

    await session.skill("triage")

    async with aiosqlite.connect(session.db_path) as db:
        cursor = await db.execute("select data from session_state where id = 1")
        data = json.loads((await cursor.fetchone())[0])

    assert data["entries"][0]["source"] == "skill"
    assert data["entries"][1]["source"] == "skill"


@pytest.mark.asyncio
async def test_session_prompt_applies_role_and_model_override(tmp_path):
    role_dir = tmp_path / ".agents" / "roles"
    role_dir.mkdir(parents=True)
    (role_dir / "coder.md").write_text(
        "---\nname: coder\n---\nYou review code carefully.",
        encoding="utf-8",
    )
    config = PyFlueConfig(root=tmp_path, harness="deepagents", model="base-model")
    agent = PyFlueAgent(config=config)
    agent.backend = _FakeBackend()

    await (await agent.session("s1")).prompt("hello", role="coder", model="override-model")

    assert "You review code carefully." in agent.backend.calls[0]["prompt"]
    assert agent.backend.calls[0]["config"].model == "override-model"


@pytest.mark.asyncio
async def test_session_role_model_precedence(tmp_path):
    role_dir = tmp_path / ".agents" / "roles"
    role_dir.mkdir(parents=True)
    (role_dir / "coder.md").write_text(
        "---\nname: coder\nmodel: role-model\n---\nYou review code carefully.",
        encoding="utf-8",
    )
    config = PyFlueConfig(root=tmp_path, harness="deepagents", model="base-model")
    agent = PyFlueAgent(config=config)
    agent.backend = _FakeBackend()

    session = await agent.session("s1", role="coder")
    await session.prompt("hello")
    await session.prompt("hello", model="call-model")

    assert agent.backend.calls[0]["config"].model == "role-model"
    assert agent.backend.calls[1]["config"].model == "call-model"


@pytest.mark.asyncio
async def test_agent_sessions_lifecycle_helpers(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config)
    agent.backend = _FakeBackend()

    created = await agent.sessions.create("new")
    loaded = await agent.sessions.get("new")

    assert created.session_id == "new"
    assert loaded.session_id == "new"
    with pytest.raises(FileExistsError):
        await agent.sessions.create("new")

    await loaded.delete()

    with pytest.raises(KeyError):
        await agent.sessions.get("new")
    with pytest.raises(RuntimeError):
        await loaded.prompt("closed")


@pytest.mark.asyncio
async def test_agent_destroy_closes_mcp_connections(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config)
    closed = False

    class _Connection:
        async def close(self):
            nonlocal closed
            closed = True

    agent._mcp_connections["server"] = _Connection()
    agent._mcp_tools.append(object())
    agent._mcp_loaded = True

    await agent.destroy()

    assert closed is True
    assert agent._mcp_connections == {}
    assert agent._mcp_tools == []


@pytest.mark.asyncio
async def test_agent_shell_uses_default_session(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(allow_shell=True, allowed_commands=("printf",)),
    )

    result = await agent.shell("printf agent")

    assert result["stdout"] == "agent"


@pytest.mark.asyncio
async def test_session_rejects_parallel_operations(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config)
    agent.backend = _FakeBackend()
    session = await agent.session("s1")
    session.active_operation = "prompt"

    with pytest.raises(RuntimeError, match="already running prompt"):
        await session.prompt("hello")


@pytest.mark.asyncio
async def test_session_abort_cancels_active_prompt(tmp_path):
    events = []
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config, on_event=events.append)
    agent.backend = _SlowBackend()
    session = await agent.session("s1")

    prompt_task = asyncio.create_task(session.prompt("wait"))
    await asyncio.sleep(0)

    assert await session.abort() is True
    with pytest.raises(asyncio.CancelledError):
        await prompt_task
    assert await session.abort() is False
    assert [event.type for event in events if event.type.startswith("abort") or event.type == "aborted"] == [
        "abort_requested",
        "aborted",
    ]


@pytest.mark.asyncio
async def test_session_prompt_retries_invalid_typed_output(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents", typed_retries=1)
    agent = PyFlueAgent(config=config)
    agent.backend = _FakeBackend(
        responses=[
            "not json",
            '---RESULT_START---\n{"summary": "fixed"}\n---RESULT_END---',
        ]
    )

    result = await (await agent.session("s1")).prompt("hello", result=_Result)

    assert result.summary == "fixed"
    assert len(agent.backend.calls) == 2


@pytest.mark.asyncio
async def test_session_prompt_extracts_typed_json_from_freeform_text(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents", typed_retries=0)
    agent = PyFlueAgent(config=config)
    agent.backend = _FakeBackend(responses=['Here is the result:\n```json\n{"summary": "freeform"}\n```'])

    result = await (await agent.session("s1")).prompt("hello", result=_Result)

    assert result.summary == "freeform"


@pytest.mark.asyncio
async def test_session_task_uses_isolated_child_history(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config)
    agent.backend = _FakeBackend()

    parent = await agent.session("parent")
    await parent.task("child work", task_id="child")

    assert parent.db_path != (await agent.session("child")).db_path
    assert "child work" in agent.backend.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_session_task_cwd_scopes_sandbox_and_context(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(allow_write=True),
    )
    agent.backend = _FakeBackend()
    parent = await agent.session("parent")
    await parent.write_file("packages/api/AGENTS.md", "API instructions")
    await parent.write_file("packages/api/note.txt", "scoped")

    await parent.task("child work", task_id="child", cwd="packages/api")

    assert agent.backend.calls[0]["system_prompt"].startswith("API instructions")
    assert "Directory structure:" in agent.backend.calls[0]["system_prompt"]
    assert agent.backend.calls[0]["sandbox"].read_file("note.txt") == "scoped"
    with pytest.raises(FileNotFoundError):
        agent.backend.calls[0]["sandbox"].read_file("packages/api/note.txt")


@pytest.mark.asyncio
async def test_session_task_records_parent_child_metadata(tmp_path):
    role_dir = tmp_path / ".agents" / "roles"
    role_dir.mkdir(parents=True)
    (role_dir / "coder.md").write_text(
        "---\nname: coder\n---\nCode carefully.",
        encoding="utf-8",
    )
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(allow_write=True),
    )
    agent.backend = _FakeBackend()
    parent = await agent.session("parent", role="coder")
    await parent.write_file("packages/api/.keep", "")
    await parent.write_file(
        "packages/api/.agents/roles/coder.md",
        "---\nname: coder\n---\nScoped code carefully.",
    )

    await parent.task("child work", task_id="child", cwd="packages/api")
    child = await agent.sessions.get("child")

    parent_metadata = await _session_metadata(parent)
    child_metadata = await _session_metadata(child)

    assert parent_metadata["children"] == ["child"]
    assert child_metadata["parent_session_id"] == "parent"
    assert child_metadata["task_id"] == "child"
    assert child_metadata["role"] == "coder"
    assert child_metadata["cwd"] == "packages/api"


@pytest.mark.asyncio
async def test_sessions_delete_removes_child_task_tree(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config)
    agent.backend = _FakeBackend(responses=["ok", "ok"])
    parent = await agent.session("parent")

    await parent.task("child work", task_id="child")
    child = await agent.sessions.get("child")
    await child.task("grandchild work", task_id="grandchild")
    grandchild = await agent.sessions.get("grandchild")
    paths = [parent.db_path, child.db_path, grandchild.db_path]

    await agent.sessions.delete("parent")

    assert all(not path.exists() for path in paths)
    with pytest.raises(KeyError):
        await agent.sessions.get("child")
    with pytest.raises(KeyError):
        await agent.sessions.get("grandchild")


@pytest.mark.asyncio
async def test_session_task_depth_limit(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents", max_task_depth=1)
    agent = PyFlueAgent(config=config)
    agent.backend = _FakeBackend(responses=["ok"])
    parent = await agent.session("parent")

    await parent.task("child work", task_id="child")
    child = await agent.sessions.get("child")

    with pytest.raises(RuntimeError, match="Max task depth exceeded"):
        await child.task("grandchild work", task_id="grandchild")


@pytest.mark.asyncio
async def test_session_abort_propagates_to_active_child_task(tmp_path):
    events: list[PyFlueEvent] = []
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config, on_event=events.append)
    agent.backend = _SlowBackend()
    parent = await agent.session("parent")

    running = asyncio.create_task(parent.task("child work", task_id="child"))
    for _ in range(100):
        if "child" in agent._active_tasks:
            break
        await asyncio.sleep(0.01)
    assert "child" in agent._active_tasks

    assert await parent.abort() is True
    with pytest.raises(asyncio.CancelledError):
        await running

    abort_requests = [
        (event.data["session_id"], event.data.get("operation"))
        for event in events
        if event.type == "abort_requested"
    ]
    assert ("child", "prompt") in abort_requests
    assert ("parent", "task") in abort_requests


@pytest.mark.asyncio
async def test_session_stream_emits_events(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config)
    agent.backend = _FakeBackend(responses=["streamed text"])

    events = [event async for event in (await agent.session("s1")).stream("hello")]

    assert [event.type for event in events] == ["start", "delta", "end"]
    assert events[1].data["text"] == "streamed text"


@pytest.mark.asyncio
async def test_session_prompt_emits_flue_lifecycle_events(tmp_path):
    events = []
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config, on_event=events.append)
    agent.backend = _FakeBackend(responses=["ok"])

    await (await agent.session("s1")).prompt("hello")

    assert [event.type for event in events] == ["agent_start", "turn_end", "idle"]
    assert events[0].data["session_id"] == "s1"


@pytest.mark.asyncio
async def test_session_stream_emits_text_delta_callback(tmp_path):
    events = []
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config, on_event=events.append)
    agent.backend = _FakeBackend(responses=["streamed text"])

    _ = [event async for event in (await agent.session("s1")).stream("hello")]

    assert [event.type for event in events] == [
        "agent_start",
        "text_delta",
        "turn_end",
        "idle",
    ]
    assert events[1].data["text"] == "streamed text"


@pytest.mark.asyncio
async def test_session_stream_emits_tool_callbacks(tmp_path):
    events = []
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config, on_event=events.append)
    agent.backend = _ToolEventBackend()

    streamed = [event async for event in (await agent.session("s1")).stream("hello")]

    assert [event.type for event in streamed] == ["start", "tool_start", "tool_end", "delta", "end"]
    tool_events = [event for event in events if event.type.startswith("tool_")]
    assert [event.type for event in tool_events] == ["tool_start", "tool_end"]
    assert tool_events[0].data["toolName"] == "read"
    assert tool_events[0].data["toolCallId"] == "1"
    assert tool_events[0].data["args"] == {"path": "x"}
    assert tool_events[1].data["isError"] is False
    assert tool_events[1].data["result"] == "ok"


@pytest.mark.asyncio
async def test_session_shell_emits_command_events(tmp_path):
    events = []
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(allow_shell=True, allowed_commands=("printf",)),
        on_event=events.append,
    )

    await (await agent.session("s1")).shell("printf hi")

    assert [event.type for event in events] == ["command_start", "command_end", "idle"]
    assert events[0].data["command"] == "printf"
    assert events[0].data["args"] == ["hi"]
    assert events[1].data["exitCode"] == 0


@pytest.mark.asyncio
async def test_session_task_emits_task_events(tmp_path):
    events = []
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config, on_event=events.append)
    agent.backend = _FakeBackend(responses=["ok"])

    await (await agent.session("parent")).task("child work", task_id="child")

    task_events = [event for event in events if event.type.startswith("task_")]
    assert [event.type for event in task_events] == ["task_start", "task_end"]
    assert task_events[0].data["taskId"] == "child"
    assert task_events[0].data["parentSessionId"] == "parent"
    assert task_events[1].data["isError"] is False


@pytest.mark.asyncio
async def test_session_compact_emits_compaction_events(tmp_path):
    events = []
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config, on_event=events.append)
    agent.backend = _FakeBackend(responses=["summary"])
    session = await agent.session("s1")
    for index in range(4):
        await session._append("user", f"message {index}")

    await session.compact(keep_recent=2)

    compaction_events = [event for event in events if event.type.startswith("compaction_")]
    assert [event.type for event in compaction_events] == ["compaction_start", "compaction_end"]
    assert compaction_events[0].data["reason"] == "threshold"
    assert compaction_events[1].data["messagesBefore"] == 4
    assert compaction_events[1].data["messagesAfter"] == 3


@pytest.mark.asyncio
async def test_session_prompt_auto_compacts_before_turn(tmp_path):
    events = []
    config = PyFlueConfig(
        root=tmp_path,
        harness="deepagents",
        compaction=CompactionConfig(
            context_window_tokens=12,
            reserve_tokens=4,
            keep_recent_tokens=4,
        ),
    )
    agent = PyFlueAgent(config=config, on_event=events.append)
    agent.backend = _FakeBackend(responses=["summary", "ok"])
    session = await agent.session("s1")
    for index in range(4):
        await session._append("user", f"long message {index}")

    await session.prompt("next")

    compaction_events = [event for event in events if event.type.startswith("compaction_")]
    assert [event.type for event in compaction_events] == ["compaction_start", "compaction_end"]
    assert compaction_events[0].data["reason"] == "threshold"
    assert agent.backend.calls[0]["prompt"].startswith("Summarize this conversation history")
    assert agent.backend.calls[1]["prompt"].startswith("Conversation so far:")


@pytest.mark.asyncio
async def test_session_prompt_compacts_and_retries_context_overflow(tmp_path):
    events = []
    config = PyFlueConfig(
        root=tmp_path,
        harness="deepagents",
        compaction=CompactionConfig(keep_recent_tokens=4),
    )
    agent = PyFlueAgent(config=config, on_event=events.append)
    agent.backend = _FakeBackend(
        responses=[
            RuntimeError("context_length_exceeded"),
            "summary",
            "ok",
        ]
    )
    session = await agent.session("s1")
    for index in range(4):
        await session._append("user", f"long message {index}")

    result = await session.prompt("next")

    compaction_events = [event for event in events if event.type.startswith("compaction_")]
    assert result.text == "ok"
    assert compaction_events[0].data["reason"] == "overflow"
    assert len(agent.backend.calls) == 3


@pytest.mark.asyncio
async def test_session_secrets_are_grant_based_for_shell(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents", env={"TOKEN": "secret"})
    agent = PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(
            allow_shell=True,
            allowed_commands=("python",),
        ),
    )
    session = await agent.session("s1")

    without_grant = await session.shell("python -c 'import os; print(os.getenv(\"TOKEN\"))'")
    with_grant = await session.shell(
        "python -c 'import os; print(os.getenv(\"TOKEN\"))'",
        secrets=["TOKEN"],
    )

    assert without_grant["stdout"].strip() == "None"
    assert with_grant["stdout"].strip() == "secret"


@pytest.mark.asyncio
async def test_session_commands_are_scoped_per_call(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(
            allow_shell=True,
            allowed_commands=("python",),
        ),
    )
    session = await agent.session("s1")

    scoped = await session.shell("printf scoped", commands=["printf"])

    assert scoped["stdout"] == "scoped"
    with pytest.raises(PermissionError):
        await session.shell("printf blocked")


@pytest.mark.asyncio
async def test_session_shell_supports_cwd_and_env(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(
            allow_write=True,
            allow_shell=True,
            allowed_commands=("python",),
        ),
    )
    session = await agent.session("s1")
    await session.write_file("pkg/value.txt", "from cwd")

    result = await session.shell(
        "python -c 'import os, pathlib; print(pathlib.Path(\"value.txt\").read_text() + \":\" + os.getenv(\"MODE\"))'",
        cwd="pkg",
        env={"MODE": "test"},
    )

    assert result["stdout"].strip() == "from cwd:test"


@pytest.mark.asyncio
async def test_agent_shell_supports_cwd_and_env(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(
            allow_write=True,
            allow_shell=True,
            allowed_commands=("python",),
        ),
    )
    session = await agent.session("default")
    await session.write_file("pkg/value.txt", "agent")

    result = await agent.shell(
        "python -c 'import os, pathlib; print(pathlib.Path(\"value.txt\").read_text() + \":\" + os.getenv(\"MODE\"))'",
        cwd="pkg",
        env={"MODE": "test"},
    )

    assert result["stdout"].strip() == "agent:test"


@pytest.mark.asyncio
async def test_agent_commands_are_scoped_for_every_call(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(
            allow_shell=True,
            allowed_commands=("python",),
        ),
        commands=["printf"],
    )
    session = await agent.session("s1")

    result = await session.shell("printf agent")

    assert result["stdout"] == "agent"


@pytest.mark.asyncio
async def test_structured_command_objects_are_prompt_tools(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")

    async def echo(value: str) -> str:
        return f"echo:{value}"

    command = PyFlueCommand(
        name="echo_value",
        description="Echo a value.",
        callable=echo,
        schema={"type": "object", "properties": {"value": {"type": "string"}}},
    )
    agent = PyFlueAgent(config=config, commands=[command])
    agent.backend = _FakeBackend(responses=["ok"])
    session = await agent.session("s1")

    await session.prompt("hello")
    tools = {tool.__name__: tool for tool in agent.backend.calls[0]["tools"]}

    assert "echo_value" in tools
    assert await tools["echo_value"](value="x") == "echo:x"
    assert tools["echo_value"].__pyflue_schema__ == command.schema


@pytest.mark.asyncio
async def test_define_command_normalizes_callable_results(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")

    def empty() -> None:
        return None

    def data() -> dict[str, str]:
        return {"value": "ok"}

    def failed() -> Exception:
        return ValueError("bad value")

    agent = PyFlueAgent(
        config=config,
        commands=[
            define_command("empty", empty),
            define_command("data", data),
            define_command("failed", failed),
        ],
    )
    agent.backend = _FakeBackend(responses=["ok"])
    session = await agent.session("s1")

    await session.prompt("hello")
    tools = {tool.__name__: tool for tool in agent.backend.calls[0]["tools"]}

    assert await tools["empty"]() == ""
    assert await tools["data"]() == {"value": "ok"}
    assert await tools["failed"]() == {"error": "bad value", "type": "ValueError"}


@pytest.mark.asyncio
async def test_define_command_accepts_mapping_and_shell_command(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    command = define_command(
        "say_hi",
        {
            "description": "Say hi.",
            "command": "printf hi",
            "timeout": 10,
        },
    )
    agent = PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(allow_shell=True),
        commands=[command],
    )
    agent.backend = _FakeBackend(responses=["ok"])
    session = await agent.session("s1")

    await session.prompt("hello")
    tools = {tool.__name__: tool for tool in agent.backend.calls[0]["tools"]}

    assert (await tools["say_hi"]())["stdout"] == "hi"
    assert tools["say_hi"].__doc__ == "Say hi."


@pytest.mark.asyncio
async def test_shell_command_object_runs_with_policy_grant(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    command = PyFlueCommand(
        name="say_hi",
        description="Say hi.",
        command="printf hi",
    )
    agent = PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(allow_shell=True),
        commands=[command],
    )
    agent.backend = _FakeBackend(responses=["ok"])
    session = await agent.session("s1")

    await session.prompt("hello")
    tools = {tool.__name__: tool for tool in agent.backend.calls[0]["tools"]}

    assert (await tools["say_hi"]())["stdout"] == "hi"


@pytest.mark.asyncio
async def test_session_prompt_passes_scoped_tools(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config)
    agent.backend = _FakeBackend(responses=["ok"])

    async def lookup(value: str) -> str:
        return value

    await (await agent.session("s1")).prompt("hello", tools=[lookup])

    tool_names = [tool.__name__ for tool in agent.backend.calls[0]["tools"]]
    assert tool_names == [
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
        "lookup",
    ]


@pytest.mark.asyncio
async def test_agent_tools_are_available_for_prompt_skill_and_task(tmp_path):
    skill_dir = tmp_path / ".agents" / "skills"
    skill_dir.mkdir(parents=True)
    (skill_dir / "triage.md").write_text(
        "---\nname: triage\n---\nDo triage.",
        encoding="utf-8",
    )
    config = PyFlueConfig(root=tmp_path, harness="deepagents")

    async def lookup(value: str) -> str:
        return value

    agent = PyFlueAgent(config=config, tools=[lookup])
    agent.backend = _FakeBackend(responses=["ok"])
    session = await agent.session("s1")

    await session.prompt("hello")
    await session.skill("triage")
    await session.task("child", task_id="child")

    for call in agent.backend.calls:
        assert [tool.__name__ for tool in call["tools"]][:12] == [
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
            "lookup",
        ]


@pytest.mark.asyncio
async def test_session_builtin_tools_operate_on_sandbox(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(allow_write=True, allow_shell=True, allowed_commands=("printf", "python")),
    )
    agent.backend = _FakeBackend(responses=["ok"])
    session = await agent.session("s1")

    await session.prompt("hello")
    tools = {tool.__name__: tool for tool in agent.backend.calls[0]["tools"]}

    assert await tools["write"]("notes/a.txt", "alpha\nbeta\n") == "Wrote notes/a.txt"
    assert await tools["read"]("notes/a.txt") == "alpha\nbeta"
    stat = await tools["stat"]("notes/a.txt")
    assert stat["path"] == "/notes/a.txt"
    assert stat["is_file"] is True
    assert stat["size"] == len("alpha\nbeta\n")
    assert await tools["exists"]("notes/a.txt") is True
    assert await tools["mkdir"]("notes/tmp") == "Created notes/tmp"
    assert await tools["rm"]("notes/tmp", recursive=True) == "Removed notes/tmp"
    assert await tools["edit"]("notes/a.txt", "beta", "gamma") == "Edited notes/a.txt (1 replacement)"
    assert await tools["grep"]("gamma", path="notes") == "notes/a.txt:2:gamma"
    assert await tools["glob"]("notes/*.txt") == "notes/a.txt"
    assert (await tools["bash"]("printf tool"))["stdout"] == "tool"
    assert (
        await tools["bash"](
            "python -c 'import os, pathlib; print(pathlib.Path(\"a.txt\").read_text().strip() + os.getenv(\"MODE\"))'",
            cwd="notes",
            env={"MODE": ":tool"},
        )
    )["stdout"].strip() == "alpha\ngamma:tool"


@pytest.mark.asyncio
async def test_session_builtin_tools_truncate_large_output(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(allow_write=True, allow_shell=True, allowed_commands=("python",)),
    )
    agent.backend = _FakeBackend(responses=["ok"])
    session = await agent.session("s1")
    await session.write_file("large.txt", "x" * 60000)

    await session.prompt("hello")
    tools = {tool.__name__: tool for tool in agent.backend.calls[0]["tools"]}

    read_output = await tools["read"]("large.txt")
    assert "Read output for large.txt truncated" in read_output

    shell_output = await tools["bash"]("python -c 'print(\"x\" * 60000)'")
    assert shell_output["truncated"] is True
    assert "Command stdout truncated" in shell_output["stdout"]


@pytest.mark.asyncio
async def test_session_rejects_custom_tool_name_conflict(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config)
    agent.backend = _FakeBackend(responses=["ok"])

    async def read(path: str) -> str:
        return path

    with pytest.raises(ValueError, match='Custom tool "read" conflicts'):
        await (await agent.session("s1")).prompt("hello", tools=[read])


@pytest.mark.asyncio
async def test_session_rejects_agent_tool_name_conflict(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")

    async def read(path: str) -> str:
        return path

    agent = PyFlueAgent(config=config, tools=[read])
    agent.backend = _FakeBackend(responses=["ok"])

    with pytest.raises(ValueError, match='Custom tool "read" conflicts'):
        await (await agent.session("s1")).prompt("hello")


@pytest.mark.asyncio
async def test_session_compact_summarizes_older_history(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(config=config)
    agent.backend = _FakeBackend(responses=["summary"])
    session = await agent.session("s1")
    for index in range(4):
        await session._append("user", f"message {index}")

    result = await session.compact(keep_recent=2)
    messages = await session._all_messages()

    assert result.metadata["compacted"] is True
    assert messages == [
        ("summary", "[Context Summary]\n\nsummary"),
        ("user", "message 2"),
        ("user", "message 3"),
    ]


@pytest.mark.asyncio
async def test_session_token_compaction_preserves_newest_messages(tmp_path):
    config = PyFlueConfig(
        root=tmp_path,
        harness="deepagents",
        compaction=CompactionConfig(keep_recent_tokens=4),
    )
    agent = PyFlueAgent(config=config)
    agent.backend = _FakeBackend(responses=["summary"])
    session = await agent.session("s1")
    for index in range(4):
        await session._append("user", f"message {index}")

    result = await session.compact()
    messages = await session._all_messages()

    assert result.metadata["compacted"] is True
    assert messages[-1] == ("user", "message 3")
    assert ("user", "message 0") not in messages


@pytest.mark.asyncio
async def test_virtual_sandbox_persists_for_same_session_id(tmp_path):
    config = PyFlueConfig(root=tmp_path, harness="deepagents")
    agent = PyFlueAgent(
        config=config,
        sandbox_policy=SandboxPolicy(allow_write=True),
    )

    await (await agent.session("stable")).write_file("note.txt", "persisted")
    content = await (await agent.session("stable")).read_file("note.txt")

    assert content == "persisted"
