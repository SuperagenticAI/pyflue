"""Smoke tests for the chat example (parity item 10)."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from pyflue import get_default_dispatch_queue, init_agent
from pyflue.harnesses.base import HarnessBackend
from pyflue.types import HarnessResult, PromptModel, PromptUsage


class _FakeBackend(HarnessBackend):
    name = "fake"

    def __init__(self):
        self.calls: list[dict] = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return HarnessResult(
            text="ok",
            raw=SimpleNamespace(),
            metadata={},
            usage=PromptUsage(total_tokens=1),
            model=PromptModel(id=kwargs.get("config").model),
        )


_EXAMPLE_PATH = Path(__file__).resolve().parent.parent / "examples" / "chat" / "app.py"


def _load_example():
    spec = importlib.util.spec_from_file_location("pyflue_chat_example", _EXAMPLE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_webhook_rejects_bad_signature(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")
    mod = _load_example()
    transport = httpx.ASGITransport(app=mod.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/github",
            content=b"{}",
            headers={"x-hub-signature-256": "sha256=wrong", "content-type": "application/json"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_webhook_dispatches_to_thread_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")
    fake = _FakeBackend()
    monkeypatch.setattr("pyflue.core.create_backend", lambda harness: fake)
    mod = _load_example()

    body = json.dumps({"issue": {"number": 42}, "comment": {"body": "hello bot"}}).encode()
    signature = _sign(mod.WEBHOOK_SECRET, body)
    transport = httpx.ASGITransport(app=mod.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/github",
            content=body,
            headers={"x-hub-signature-256": signature, "content-type": "application/json"},
        )
        assert response.status_code == 202
        assert response.json()["dispatch_id"].startswith("dispatch_")
        await get_default_dispatch_queue().join()

    # The normalized comment reached the thread-scoped agent session (42:42).
    assert any(call["session_id"] == "42:42" for call in fake.calls)
    assert any("hello bot" in call["prompt"] for call in fake.calls)


@pytest.mark.asyncio
async def test_reply_tool_is_scoped_to_thread(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyflue.toml").write_text("[agent]\n", encoding="utf-8")
    monkeypatch.setattr("pyflue.core.create_backend", lambda harness: _FakeBackend())
    mod = _load_example()

    agent = await init_agent(mod.assistant, id="99")
    tool = next(t for t in agent.tools if getattr(t, "name", None) == "reply_to_chat_thread")
    assert tool.execute({"text": "done"}) == "Reply sent."
    # The tool closed over the dispatched thread id, not model-supplied input.
    assert {"thread": "99", "text": "done"} in mod.SENT_REPLIES
