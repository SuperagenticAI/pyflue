"""Tests for subagent profiles + Role bridge (parity item 8)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pyflue import (
    AgentProfile,
    create_agent,
    define_agent_profile,
    init_agent,
    profile_to_role,
    role_to_profile,
)
from pyflue.harnesses.base import HarnessBackend
from pyflue.types import HarnessResult, PromptModel, PromptUsage, Role


class _FakeBackend(HarnessBackend):
    name = "fake"

    def __init__(self, responses=None):
        self.calls: list[dict] = []
        self.responses = responses or ["ok"]

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return HarnessResult(
            text=self.responses[min(len(self.calls) - 1, len(self.responses) - 1)],
            raw=SimpleNamespace(),
            metadata={},
            usage=PromptUsage(total_tokens=1),
            model=PromptModel(id=kwargs.get("config").model),
        )


# ── Role <-> AgentProfile bridge ──────────────────────────────────────────────


def test_profile_to_role_and_back():
    profile = define_agent_profile(
        AgentProfile(name="reviewer", description="d", instructions="Be strict.", model="m", thinking_level="high")
    )
    role = profile_to_role(profile)
    assert isinstance(role, Role)
    assert role.name == "reviewer"
    assert role.instructions == "Be strict."
    assert role.model == "m"
    assert role.thinking_level == "high"

    back = role_to_profile(role)
    assert back.name == "reviewer"
    assert back.instructions == "Be strict."
    assert back.model == "m"


def test_profile_to_role_handles_model_false():
    role = profile_to_role(AgentProfile(name="x", model=False))
    assert role.model is None  # `model: False` has no usable string default


# ── task(agent="name") selection ──────────────────────────────────────────────


async def _agent_with_reviewer(monkeypatch, tmp_path, backend):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("pyflue.core.create_backend", lambda harness: backend)
    reviewer = define_agent_profile(
        AgentProfile(name="reviewer", instructions="Be a strict reviewer.", model="reviewer-model")
    )
    return await init_agent(
        create_agent(lambda: {"model": "base-model", "subagents": [reviewer]})
    )


@pytest.mark.asyncio
async def test_task_selects_subagent_profile(tmp_path, monkeypatch):
    fake = _FakeBackend(responses=["reviewed"])
    agent = await _agent_with_reviewer(monkeypatch, tmp_path, fake)
    session = await agent.session("s")

    await session.task("Review the change.", agent="reviewer")

    call = fake.calls[-1]
    assert "Be a strict reviewer." in call["prompt"]  # profile instructions used
    assert call["config"].model == "reviewer-model"  # profile model selected


@pytest.mark.asyncio
async def test_task_level_model_overrides_profile(tmp_path, monkeypatch):
    fake = _FakeBackend(responses=["ok"])
    agent = await _agent_with_reviewer(monkeypatch, tmp_path, fake)
    session = await agent.session("s")

    await session.task("Review.", agent="reviewer", model="override-model")

    assert fake.calls[-1]["config"].model == "override-model"


@pytest.mark.asyncio
async def test_task_unknown_subagent_raises(tmp_path, monkeypatch):
    fake = _FakeBackend()
    agent = await _agent_with_reviewer(monkeypatch, tmp_path, fake)
    session = await agent.session("s")

    with pytest.raises(KeyError, match="Unknown subagent"):
        await session.task("Do it.", agent="missing")


@pytest.mark.asyncio
async def test_subagent_method_forwards_agent(tmp_path, monkeypatch):
    fake = _FakeBackend(responses=["ok"])
    agent = await _agent_with_reviewer(monkeypatch, tmp_path, fake)
    session = await agent.session("s")

    await session.subagent("Review.", agent="reviewer")
    assert fake.calls[-1]["config"].model == "reviewer-model"
