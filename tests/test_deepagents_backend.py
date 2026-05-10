from __future__ import annotations

import sys
from types import ModuleType

from pyflue.harnesses.deepagents import (
    _create_agent_call,
    _DeepAgentsSandboxBackend,
    _extract_tool_event,
    _permissions,
    _resolve_model,
)
from pyflue.sandbox import SandboxPolicy, VirtualSandbox
from pyflue.types import ProviderSettings, PyFlueConfig


def test_deepagents_backend_upload_download_and_execute(monkeypatch, tmp_path):
    _install_fake_deepagents_protocol(monkeypatch)
    sandbox = VirtualSandbox(
        tmp_path,
        SandboxPolicy(allow_write=True, allow_shell=True),
    )
    backend = _DeepAgentsSandboxBackend(sandbox)

    upload = backend.upload_files([("/AGENTS.md", b"instructions")])
    download = backend.download_files(["/AGENTS.md"])
    execute = backend.execute("cat AGENTS.md")

    assert getattr(upload[0], "error", None) is None
    assert download[0].content == b"instructions"
    assert execute.output == "instructions"
    assert execute.exit_code == 0


def test_deepagents_backend_advertises_execution_support(tmp_path):
    from deepagents.backends.protocol import BackendProtocol, SandboxBackendProtocol

    backend = _DeepAgentsSandboxBackend(VirtualSandbox(tmp_path))

    assert isinstance(backend, BackendProtocol)
    assert isinstance(backend, SandboxBackendProtocol)


def test_deepagents_backend_write_and_edit_match_deepagents_contract(tmp_path):
    sandbox = VirtualSandbox(
        tmp_path,
        SandboxPolicy(allow_write=True),
    )
    backend = _DeepAgentsSandboxBackend(sandbox)

    created = backend.write("/notes.txt", "alpha\nbeta\nalpha\n")
    duplicate_write = backend.write("/notes.txt", "replace")
    ambiguous_edit = backend.edit("/notes.txt", "alpha", "gamma")
    replace_all = backend.edit("/notes.txt", "alpha", "gamma", replace_all=True)

    assert created.error is None
    assert "already exists" in duplicate_write.error
    assert "2 occurrences" in ambiguous_edit.error
    assert replace_all.error is None
    assert replace_all.occurrences == 2
    assert sandbox.read_file("notes.txt") == "gamma\nbeta\ngamma"


def test_deepagents_agent_call_omits_permissions_for_executable_backend(monkeypatch, tmp_path):
    captured = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)

        class FakeAgent:
            def invoke(self, *_args, **_kwargs):
                return "ok"

        return FakeAgent()

    fake_deepagents = ModuleType("deepagents")
    fake_deepagents.create_deep_agent = fake_create_deep_agent
    monkeypatch.setitem(sys.modules, "deepagents", fake_deepagents)

    sandbox = VirtualSandbox(
        tmp_path,
        SandboxPolicy(allow_write=True, allow_shell=False),
    )
    _create_agent_call(
        prompt="hello",
        system_prompt="",
        config=PyFlueConfig(root=tmp_path),
        skills={},
        sandbox=sandbox,
        session_id="s1",
        python_backend=None,
        tools=None,
        harness_name="deepagents",
        stream=False,
    )

    assert captured["permissions"] is None


def test_deepagents_permissions_mirror_sandbox_policy(monkeypatch, tmp_path):
    class _Permission:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_deepagents = ModuleType("deepagents")
    fake_deepagents.FilesystemPermission = _Permission
    monkeypatch.setitem(sys.modules, "deepagents", fake_deepagents)

    readonly = _permissions(VirtualSandbox(tmp_path))
    writable = _permissions(
        VirtualSandbox(tmp_path, SandboxPolicy(allow_write=True)),
    )

    assert readonly[0].kwargs == {
        "operations": ["write"],
        "paths": ["/**"],
        "mode": "deny",
    }
    assert writable[0].kwargs == {
        "operations": ["read", "write"],
        "paths": ["/**"],
    }


def test_deepagents_resolve_model_applies_store_responses(monkeypatch, tmp_path):
    captured = {}

    def fake_init_chat_model(model, **kwargs):
        captured["model"] = model
        captured["kwargs"] = kwargs
        return "model"

    fake_langchain = ModuleType("langchain.chat_models")
    fake_langchain.init_chat_model = fake_init_chat_model
    monkeypatch.setitem(sys.modules, "langchain.chat_models", fake_langchain)

    config = PyFlueConfig(root=tmp_path, model="openai:gpt-4o")
    config.providers.set("openai", ProviderSettings(store_responses=True))

    assert _resolve_model(config) == "model"
    assert captured["kwargs"]["store"] is True


def test_deepagents_tool_events_are_normalized():
    start = _extract_tool_event(
        {
            "event": "on_tool_start",
            "name": "read",
            "run_id": "run-1",
            "data": {"input": {"path": "README.md"}},
        }
    )
    end = _extract_tool_event(
        {
            "event": "on_tool_end",
            "name": "read",
            "run_id": "run-1",
            "data": {"output": "content"},
        }
    )

    assert start is not None
    assert start.type == "tool_start"
    assert start.data["toolName"] == "read"
    assert start.data["toolCallId"] == "run-1"
    assert start.data["args"] == {"path": "README.md"}
    assert end is not None
    assert end.type == "tool_end"
    assert end.data["toolName"] == "read"
    assert end.data["toolCallId"] == "run-1"
    assert end.data["isError"] is False
    assert end.data["result"] == "content"


def _install_fake_deepagents_protocol(monkeypatch):
    protocol = ModuleType("deepagents.backends.protocol")

    class _Result:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    protocol.EditResult = _Result
    protocol.ExecuteResponse = _Result
    protocol.FileDownloadResponse = _Result
    protocol.FileUploadResponse = _Result
    protocol.GlobResult = _Result
    protocol.GrepResult = _Result
    protocol.LsResult = _Result
    protocol.ReadResult = _Result
    protocol.WriteResult = _Result

    backends = ModuleType("deepagents.backends")
    backends.protocol = protocol
    monkeypatch.setitem(sys.modules, "deepagents.backends", backends)
    monkeypatch.setitem(sys.modules, "deepagents.backends.protocol", protocol)
