"""Tests for create_agent() + agent profiles (parity item 1)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pyflue import (
    AgentCreateContext,
    AgentProfile,
    AgentRuntimeConfig,
    CreatedAgent,
    create_agent,
    createAgent,
    define_agent_profile,
    defineAgentProfile,
    extend_agent_profile,
    init_agent,
    is_created_agent,
    resolve_agent_profile,
)
from pyflue.harnesses.base import HarnessBackend
from pyflue.types import (
    CompactionConfig,
    HarnessResult,
    PromptModel,
    PromptUsage,
    Skill,
    ToolDef,
)


def _tool(name: str):
    return ToolDef(name=name, description=f"{name} desc", execute=lambda args: None)


class _FakeBackend(HarnessBackend):
    name = "fake"

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or ["ok"]

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return HarnessResult(
            text=self.responses[min(len(self.calls) - 1, len(self.responses) - 1)],
            raw=SimpleNamespace(),
            metadata={"harness": "fake"},
            usage=PromptUsage(input=1, output=2, total_tokens=3),
            model=PromptModel(id=kwargs.get("config").model),
        )


# ── create_agent / is_created_agent ─────────────────────────────────────────


def test_create_agent_returns_created_agent():
    agent = create_agent(lambda ctx: {"model": "m"})
    assert isinstance(agent, CreatedAgent)
    assert is_created_agent(agent)
    assert not is_created_agent({"model": "m"})


def test_create_agent_rejects_non_callable():
    with pytest.raises(ValueError, match="initializer function"):
        create_agent({"model": "m"})  # type: ignore[arg-type]


def test_camelcase_aliases_are_identical():
    assert createAgent is create_agent
    assert defineAgentProfile is define_agent_profile


# ── define_agent_profile validation ──────────────────────────────────────────


def test_profile_accepts_valid_definition_and_dict():
    p = define_agent_profile(
        AgentProfile(name="reviewer", description="Reviews code", model="m", thinking_level="high")
    )
    assert p.name == "reviewer"
    d = define_agent_profile({"name": "from_dict", "model": False})
    assert d.model is False


def test_profile_rejects_bad_name():
    with pytest.raises(ValueError, match="name"):
        define_agent_profile(AgentProfile(name="1bad"))


def test_profile_rejects_empty_description():
    with pytest.raises(ValueError, match="description"):
        define_agent_profile(AgentProfile(name="ok", description="   "))


def test_profile_rejects_bad_thinking_level():
    with pytest.raises(ValueError, match="thinking_level"):
        define_agent_profile(AgentProfile(thinking_level="extreme"))  # type: ignore[arg-type]


def test_profile_rejects_unknown_dict_field():
    with pytest.raises(ValueError, match="unknown agent profile field"):
        define_agent_profile({"modle": "typo"})


def test_profile_rejects_duplicate_tool_names():
    with pytest.raises(ValueError, match="duplicate tool name"):
        define_agent_profile(AgentProfile(tools=[_tool("t"), _tool("t")]))


def test_profile_rejects_duplicate_subagent_names():
    a = define_agent_profile(AgentProfile(name="dup", instructions="x"))
    b = define_agent_profile(AgentProfile(name="dup", instructions="y"))
    with pytest.raises(ValueError, match="duplicate subagent name"):
        define_agent_profile(AgentProfile(name="coordinator", subagents=[a, b]))


def test_profile_rejects_non_profile_subagent():
    with pytest.raises(ValueError, match="must be an AgentProfile"):
        define_agent_profile(AgentProfile(name="c", subagents=[{"name": "x"}]))  # type: ignore[list-item]


def test_profile_rejects_bad_compaction_tokens():
    with pytest.raises(ValueError, match="reserve_tokens"):
        define_agent_profile(AgentProfile(compaction=CompactionConfig(reserve_tokens=-1)))


def test_profile_accepts_compaction_false_and_skills():
    p = define_agent_profile(
        AgentProfile(compaction=False, skills=[Skill(name="s", description="d")])
    )
    assert p.compaction is False


# ── resolve_agent_profile (merge precedence) ─────────────────────────────────


def test_resolve_inline_overrides_and_merges_arrays():
    base = define_agent_profile(
        AgentProfile(name="base", model="m1", instructions="be base", tools=[_tool("t1")])
    )
    cfg = AgentRuntimeConfig(profile=base, model="m2", tools=[_tool("t2")])
    resolved = resolve_agent_profile(cfg)
    assert resolved.model == "m2"  # inline scalar wins
    assert resolved.instructions == "be base"  # falls back to profile
    assert [t.name for t in resolved.tools] == ["t1", "t2"]  # profile then inline


def test_resolve_model_false_overrides_profile():
    base = define_agent_profile(AgentProfile(name="base", model="m1"))
    resolved = resolve_agent_profile(AgentRuntimeConfig(profile=base, model=False))
    assert resolved.model is False


def test_resolve_without_profile_returns_inline():
    resolved = resolve_agent_profile(AgentRuntimeConfig(model="solo", instructions="hi"))
    assert resolved.model == "solo"
    assert resolved.instructions == "hi"
    assert resolved.tools is None


def test_extend_agent_profile_appends():
    base = define_agent_profile(AgentProfile(name="b", tools=[_tool("t1")]))
    extended = extend_agent_profile(base, tools=[_tool("t2")])
    assert [t.name for t in extended.tools] == ["t1", "t2"]
    assert base.tools is not None and len(base.tools) == 1  # original untouched


# ── init_agent (resolves a CreatedAgent into a live PyFlueAgent) ─────────────


@pytest.mark.asyncio
async def test_init_agent_resolves_and_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    captured: dict = {}

    def initialize(ctx: AgentCreateContext):
        captured["id"] = ctx.id
        captured["payload"] = ctx.payload
        return {"model": "fake-model", "instructions": "be helpful"}

    agent = create_agent(initialize)
    pf = await init_agent(agent, id="inst-1", payload={"k": "v"})

    assert captured["id"] == "inst-1"
    assert captured["payload"] == {"k": "v"}
    assert pf.instance_id == "inst-1"
    assert pf.environment_name == "default"
    assert pf.profile.model == "fake-model"
    assert "be helpful" in pf.instructions
    assert pf.config.model == "fake-model"

    pf.backend = _FakeBackend(responses=["done"])
    result = await (await pf.session("s")).prompt("hi")
    assert result.text == "done"


@pytest.mark.asyncio
async def test_init_agent_async_initializer_and_augmentation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def initialize(ctx):
        return AgentRuntimeConfig(model="fake-model", tools=[_tool("base_tool")])

    agent = create_agent(initialize)
    pf = await init_agent(agent, tools=[_tool("added_tool")], subagents=[
        define_agent_profile(AgentProfile(name="helper", instructions="assist"))
    ])
    tool_names = {t.name for t in pf.tools}
    assert {"base_tool", "added_tool"} <= tool_names
    assert [s.name for s in pf.subagents] == ["helper"]


@pytest.mark.asyncio
async def test_init_agent_zero_arg_initializer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = create_agent(lambda: {"model": "fake-model"})
    pf = await init_agent(agent)
    assert pf.profile.model == "fake-model"
    assert pf.instance_id == "default"


@pytest.mark.asyncio
async def test_init_agent_compaction_false_and_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = create_agent(
        lambda: {"model": "m", "compaction": CompactionConfig(reserve_tokens=999, model="haiku")}
    )
    pf = await init_agent(agent)
    assert pf.config.compaction.reserve_tokens == 999
    assert pf.config.compaction.model == "haiku"

    disabled = await init_agent(create_agent(lambda: {"model": "m", "compaction": False}))
    assert disabled.config.compaction.enabled is False


@pytest.mark.asyncio
async def test_init_agent_rejects_plain_function(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="requires a created agent"):
        await init_agent(lambda: {"model": "m"})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_init_agent_rejects_unknown_runtime_field(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = create_agent(lambda: {"model": "m", "sandboxx": "typo"})
    with pytest.raises(ValueError, match="unknown runtime config field"):
        await init_agent(agent)
