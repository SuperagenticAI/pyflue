"""Composable agent definitions: ``create_agent`` and agent profiles.

This mirrors the TypeScript Flue reference (``packages/runtime/src/agent-definition.ts``):

* ``create_agent(initialize)`` returns a deferred, composable agent *spec*, a
  frozen :class:`CreatedAgent` holding an ``initialize`` factory. The factory
  receives an :class:`AgentCreateContext` (``id`` / ``env`` / ``payload``) and
  returns an :class:`AgentRuntimeConfig` (or a plain mapping).
* ``define_agent_profile(profile)`` validates and returns reusable behaviour
  shared across agents and workflows.
* ``init_agent(created_agent, ...)`` resolves a created agent into a live
  :class:`~pyflue.core.PyFlueAgent`.

``init()`` (the eager constructor) is unchanged and fully compatible; a created
agent is the composable layer on top of it.

Both snake_case and camelCase aliases are exported, matching pyflue's existing
convention (``create_tools`` / ``createTools``).
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_args

from pyflue.types import (
    CompactionConfig,
    PyFlueEventCallback,
    Role,
    Skill,
    ThinkingLevel,
)

if TYPE_CHECKING:
    from pyflue.core import PyFlueAgent

_AGENT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_VALID_THINKING_LEVELS: tuple[str, ...] = get_args(ThinkingLevel)

# Field name sets, mirroring AGENT_PROFILE_FIELDS / AGENT_RUNTIME_FIELDS.
_PROFILE_FIELDS = {
    "name",
    "description",
    "model",
    "instructions",
    "skills",
    "tools",
    "subagents",
    "thinking_level",
    "compaction",
}
_RUNTIME_EXTRA_FIELDS = {"profile", "cwd", "sandbox", "persist"}
_RUNTIME_FIELDS = _PROFILE_FIELDS | _RUNTIME_EXTRA_FIELDS

_COMPACTION_FIELDS = {
    "enabled",
    "context_window_tokens",
    "reserve_tokens",
    "keep_recent_tokens",
    "model",
}


@dataclass(frozen=True)
class AgentProfile:
    """Reusable, model-facing agent behaviour shared across agents/workflows.

    A profile carries no runtime/environment concerns (sandbox, cwd,
    persistence). Those live on :class:`AgentRuntimeConfig`.
    """

    name: str | None = None
    description: str | None = None
    model: str | Literal[False] | None = None
    instructions: str | None = None
    thinking_level: ThinkingLevel | None = None
    skills: Sequence[Skill] | None = None
    tools: Sequence[Any] | None = None
    subagents: Sequence[AgentProfile] | None = None
    compaction: CompactionConfig | Literal[False] | None = None


@dataclass(frozen=True)
class AgentRuntimeConfig(AgentProfile):
    """Profile fields plus runtime/environment concerns.

    Returned by a ``create_agent`` initializer. ``profile`` supplies a baseline
    that inline fields override (scalars) or extend (skills/tools/subagents).
    """

    profile: AgentProfile | None = None
    cwd: str | None = None
    sandbox: Any | None = None
    persist: Any | None = None


@dataclass(frozen=True)
class AgentCreateContext:
    """Context passed to a ``create_agent`` initializer."""

    id: str = "default"
    env: dict[str, str] = field(default_factory=dict)
    payload: Any | None = None


@dataclass(frozen=True)
class CreatedAgent:
    """A composable, deferred agent specification.

    Produced by :func:`create_agent`. Resolve it into a live agent with
    :func:`init_agent`, or initialize it from a workflow with ``ctx.init(agent)``.
    """

    initialize: Callable[[AgentCreateContext], Any]
    __pyflue_created_agent__: bool = True


def is_created_agent(value: Any) -> bool:
    """Return True if ``value`` is a :class:`CreatedAgent`."""
    return isinstance(value, CreatedAgent) or getattr(
        value, "__pyflue_created_agent__", False
    ) is True


def create_agent(
    initialize: Callable[[AgentCreateContext], Any],
) -> CreatedAgent:
    """Define a composable agent from an initializer function.

    The initializer receives an :class:`AgentCreateContext` and returns an
    :class:`AgentRuntimeConfig` or a plain mapping of the same fields. It may be
    sync or async, and may accept zero arguments when it ignores the context::

        agent = create_agent(lambda ctx: {"model": "anthropic/claude-haiku-4-5"})
        agent = create_agent(lambda: AgentRuntimeConfig(model="openai:gpt-5.5"))
    """
    if not callable(initialize):
        raise ValueError("[pyflue] create_agent() requires an initializer function.")
    return CreatedAgent(initialize=initialize)


def define_agent_profile(profile: AgentProfile | dict[str, Any]) -> AgentProfile:
    """Validate and return a reusable agent profile."""
    resolved = _coerce_profile(profile, "define_agent_profile()")
    _assert_agent_profile(resolved, "define_agent_profile()", set())
    return resolved


def resolve_agent_profile(config: AgentRuntimeConfig) -> AgentProfile:
    """Merge a runtime config's ``profile`` baseline with its inline overrides.

    Scalars: an inline value (including ``model=False`` / ``compaction=False``)
    overrides the profile; ``None`` means "fall back to the profile".
    Arrays (skills/tools/subagents): profile entries followed by inline entries.
    """
    base = config.profile

    def pick(name: str) -> Any:
        own = getattr(config, name)
        if own is not None:
            return own
        return getattr(base, name) if base is not None else None

    return AgentProfile(
        name=pick("name"),
        description=pick("description"),
        model=pick("model"),
        instructions=pick("instructions"),
        thinking_level=pick("thinking_level"),
        compaction=pick("compaction"),
        skills=_merge_arrays(_seq(base, "skills"), config.skills),
        tools=_merge_arrays(_seq(base, "tools"), config.tools),
        subagents=_merge_arrays(_seq(base, "subagents"), config.subagents),
    )


def extend_agent_profile(
    profile: AgentProfile,
    *,
    skills: Sequence[Skill] | None = None,
    tools: Sequence[Any] | None = None,
    subagents: Sequence[AgentProfile] | None = None,
) -> AgentProfile:
    """Return a copy of ``profile`` with extra skills/tools/subagents appended."""
    from dataclasses import replace

    return replace(
        profile,
        skills=_merge_arrays(profile.skills, skills),
        tools=_merge_arrays(profile.tools, tools),
        subagents=_merge_arrays(profile.subagents, subagents),
    )


def profile_to_role(profile: AgentProfile) -> Role:
    """Adapt an :class:`AgentProfile` to a Markdown-style :class:`Role`.

    Bridges the new programmatic subagent model to pyflue's existing role
    machinery (instructions + model + reasoning level).
    """
    return Role(
        name=profile.name or "subagent",
        instructions=profile.instructions or "",
        description=profile.description or "",
        model=profile.model if isinstance(profile.model, str) else None,
        thinking_level=profile.thinking_level,
    )


def role_to_profile(role: Role) -> AgentProfile:
    """Adapt a Markdown :class:`Role` to an :class:`AgentProfile`.

    Lets existing `.agents/roles/*.md` roles be used as subagent profiles.
    """
    return AgentProfile(
        name=role.name,
        description=role.description or None,
        instructions=role.instructions or None,
        model=role.model,
        thinking_level=role.thinking_level,
    )


async def init_agent(
    agent: CreatedAgent,
    *,
    id: str = "default",
    payload: Any = None,
    env: dict[str, str] | None = None,
    name: str = "default",
    tools: Sequence[Any] | None = None,
    skills: Sequence[Skill] | None = None,
    subagents: Sequence[AgentProfile] | None = None,
    config_path: str | Path | None = None,
    on_event: PyFlueEventCallback | None = None,
) -> PyFlueAgent:
    """Resolve a :class:`CreatedAgent` into a live :class:`~pyflue.core.PyFlueAgent`.

    ``tools`` / ``skills`` / ``subagents`` are *added* to those configured by the
    created agent (matching the reference ``init(agent, { tools, skills, ... })``
    augmentation). ``name`` selects the initialized environment identity.
    """
    from pyflue.core import init  # lazy import to avoid an import cycle

    if not is_created_agent(agent):
        raise ValueError(
            "[pyflue] init_agent() requires a created agent from create_agent()."
        )

    ctx = AgentCreateContext(id=id, env=dict(env or {}), payload=payload)
    raw = _call_initializer(agent.initialize, ctx)
    if inspect.isawaitable(raw):
        raw = await raw

    runtime = _coerce_runtime_config(raw)
    _assert_runtime_config(runtime)
    profile = resolve_agent_profile(runtime)

    merged_tools = _merge_arrays(profile.tools, tools)
    merged_skills = _merge_arrays(profile.skills, skills)
    merged_subagents = _merge_arrays(profile.subagents, subagents)

    init_kwargs: dict[str, Any] = {}
    if isinstance(profile.model, str) and profile.model:
        init_kwargs["model"] = profile.model
    if profile.thinking_level is not None:
        init_kwargs["thinking_level"] = profile.thinking_level
    if isinstance(runtime.sandbox, str):
        init_kwargs["sandbox"] = runtime.sandbox
    if config_path is not None:
        init_kwargs["config_path"] = config_path
    if env:
        init_kwargs["env"] = dict(env)
    if on_event is not None:
        init_kwargs["on_event"] = on_event
    if merged_tools:
        init_kwargs["tools"] = list(merged_tools)

    compaction = profile.compaction
    compaction_model: str | None = None
    if compaction is False:
        init_kwargs["compaction_enabled"] = False
    elif isinstance(compaction, CompactionConfig):
        init_kwargs["compaction_enabled"] = compaction.enabled
        init_kwargs["compaction_context_window_tokens"] = compaction.context_window_tokens
        init_kwargs["compaction_reserve_tokens"] = compaction.reserve_tokens
        init_kwargs["compaction_keep_recent_tokens"] = compaction.keep_recent_tokens
        compaction_model = compaction.model

    pyflue_agent = await init(**init_kwargs)

    # Attach resolved composition for later phases (persistent instances,
    # subagent selection) and apply the pieces init() already supports.
    pyflue_agent.profile = profile
    pyflue_agent.created_agent = agent
    pyflue_agent.agent_name = profile.name
    pyflue_agent.instance_id = id
    pyflue_agent.environment_name = name
    pyflue_agent.cwd = runtime.cwd
    pyflue_agent.persist = runtime.persist
    pyflue_agent.subagents = list(merged_subagents or ())

    if profile.instructions:
        pyflue_agent.instructions = _combine_instructions(
            profile.instructions, getattr(pyflue_agent, "instructions", "")
        )
    if merged_skills:
        _attach_skills(pyflue_agent, merged_skills)
    if compaction_model is not None:
        pyflue_agent.config.compaction.model = compaction_model

    return pyflue_agent


# ── internal helpers ────────────────────────────────────────────────────────


def _seq(profile: AgentProfile | None, name: str) -> Sequence[Any] | None:
    return getattr(profile, name) if profile is not None else None


def _merge_arrays(
    base: Sequence[Any] | None, additions: Sequence[Any] | None
) -> tuple[Any, ...] | None:
    if base is None and additions is None:
        return None
    return tuple(base or ()) + tuple(additions or ())


def _combine_instructions(primary: str | None, secondary: str | None) -> str:
    parts = [p for p in (primary, secondary) if p and p.strip()]
    return "\n\n".join(parts)


def _attach_skills(pyflue_agent: PyFlueAgent, skills: Sequence[Skill]) -> None:
    existing = getattr(pyflue_agent, "skills", None)
    if isinstance(existing, dict):
        for skill in skills:
            existing[skill.name] = skill
    elif isinstance(existing, list):
        existing.extend(skills)
    else:
        pyflue_agent.skills = list(skills)


def _call_initializer(initialize: Callable[..., Any], ctx: AgentCreateContext) -> Any:
    try:
        sig = inspect.signature(initialize)
    except (TypeError, ValueError):
        return initialize(ctx)
    positional = [
        p
        for p in sig.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    has_var_positional = any(
        p.kind == p.VAR_POSITIONAL for p in sig.parameters.values()
    )
    if not positional and not has_var_positional:
        return initialize()
    return initialize(ctx)


def _coerce_compaction(value: Any, label: str) -> CompactionConfig | Literal[False] | None:
    if value is None or value is False or isinstance(value, CompactionConfig):
        return value
    if isinstance(value, dict):
        unknown = set(value) - _COMPACTION_FIELDS
        if unknown:
            raise ValueError(
                f"[pyflue] {label} compaction received unknown field "
                f"{sorted(unknown)[0]!r}."
            )
        return CompactionConfig(**value)
    raise ValueError(
        f"[pyflue] {label} compaction must be a CompactionConfig, mapping, or False."
    )


def _coerce_profile(value: Any, label: str) -> AgentProfile:
    if is_created_agent(value):
        raise ValueError(
            f"[pyflue] {label} expected an agent profile, not a created agent."
        )
    if isinstance(value, AgentRuntimeConfig):
        raise ValueError(
            f"[pyflue] {label} expected an agent profile, not a runtime config."
        )
    if isinstance(value, AgentProfile):
        return value
    if isinstance(value, dict):
        unknown = set(value) - _PROFILE_FIELDS
        if unknown:
            raise ValueError(
                f"[pyflue] {label} received unknown agent profile field "
                f"{sorted(unknown)[0]!r}."
            )
        data = dict(value)
        if "compaction" in data:
            data["compaction"] = _coerce_compaction(data["compaction"], label)
        if data.get("subagents") is not None:
            data["subagents"] = tuple(
                _coerce_profile(item, f"{label} subagent") for item in data["subagents"]
            )
        return AgentProfile(**data)
    raise ValueError(
        f"[pyflue] {label} must be an AgentProfile or a mapping of profile fields."
    )


def _coerce_runtime_config(value: Any) -> AgentRuntimeConfig:
    if isinstance(value, AgentRuntimeConfig):
        return value
    if isinstance(value, AgentProfile):
        return AgentRuntimeConfig(
            **{f.name: getattr(value, f.name) for f in fields(AgentProfile)}
        )
    if isinstance(value, dict):
        unknown = set(value) - _RUNTIME_FIELDS
        if unknown:
            raise ValueError(
                "[pyflue] create_agent() initializer returned unknown runtime "
                f"config field {sorted(unknown)[0]!r}."
            )
        data = dict(value)
        if data.get("profile") is not None:
            data["profile"] = _coerce_profile(data["profile"], "create_agent() profile")
        if "compaction" in data:
            data["compaction"] = _coerce_compaction(data["compaction"], "create_agent()")
        if data.get("subagents") is not None:
            data["subagents"] = tuple(
                _coerce_profile(item, "create_agent() subagent")
                for item in data["subagents"]
            )
        return AgentRuntimeConfig(**data)
    raise ValueError(
        "[pyflue] create_agent() initializer must return an AgentRuntimeConfig "
        "or a mapping of runtime config fields."
    )


def _assert_runtime_config(config: AgentRuntimeConfig) -> None:
    if config.profile is not None:
        _assert_agent_profile(config.profile, "create_agent() profile", set())
    # Validate the inline profile-shaped fields of the runtime config itself.
    inline = AgentProfile(
        name=config.name,
        description=config.description,
        model=config.model,
        instructions=config.instructions,
        thinking_level=config.thinking_level,
        skills=config.skills,
        tools=config.tools,
        subagents=config.subagents,
        compaction=config.compaction,
    )
    _assert_agent_profile(inline, "create_agent()", set())


def _assert_agent_profile(profile: AgentProfile, label: str, active: set[int]) -> None:
    marker = id(profile)
    if marker in active:
        raise ValueError(f"[pyflue] {label} must not contain circular subagents.")
    active.add(marker)
    try:
        if profile.name is not None:
            _assert_agent_name(profile.name, f"{label} name")
        if profile.description is not None:
            _assert_non_empty_string(profile.description, f"{label} description")
        if profile.instructions is not None:
            _assert_non_empty_string(profile.instructions, f"{label} instructions")
        _assert_model(profile.model, label)
        _assert_thinking_level(profile.thinking_level, label)
        _assert_compaction(profile.compaction, label)
        _assert_tools(profile.tools, label)
        _assert_skills(profile.skills, label)
        _assert_subagents(profile.subagents, label, active)
        _assert_unique_names(profile.tools, f"{label} tools", "tool")
        _assert_unique_names(profile.skills, f"{label} skills", "skill")
        _assert_unique_names(profile.subagents, f"{label} subagents", "subagent")
    finally:
        active.discard(marker)


def _assert_model(value: Any, label: str) -> None:
    if value is None or value is False:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"[pyflue] {label} model must be a non-empty string or False."
        )


def _assert_thinking_level(value: Any, label: str) -> None:
    if value is not None and value not in _VALID_THINKING_LEVELS:
        raise ValueError(
            f"[pyflue] {label} thinking_level must be one of: "
            f"{', '.join(_VALID_THINKING_LEVELS)}."
        )


def _assert_compaction(value: Any, label: str) -> None:
    if value is None or value is False:
        return
    if not isinstance(value, CompactionConfig):
        raise ValueError(
            f"[pyflue] {label} compaction must be a CompactionConfig or False."
        )
    for token_field in ("reserve_tokens", "keep_recent_tokens", "context_window_tokens"):
        token = getattr(value, token_field)
        if not isinstance(token, int) or isinstance(token, bool) or token < 0:
            raise ValueError(
                f"[pyflue] {label} compaction.{token_field} must be a "
                "non-negative integer."
            )
    if value.model is not None and not isinstance(value.model, str):
        raise ValueError(f"[pyflue] {label} compaction.model must be a string.")


def _assert_tools(values: Sequence[Any] | None, label: str) -> None:
    for index, value in enumerate(values or ()):
        name = getattr(value, "name", None)
        description = getattr(value, "description", None)
        _assert_non_empty_string(name, f"{label} tools[{index}].name")
        _assert_non_empty_string(description, f"{label} tools[{index}].description")
        execute = getattr(value, "execute", None)
        if execute is not None and not callable(execute):
            raise ValueError(
                f"[pyflue] {label} tools[{index}].execute must be callable."
            )


def _assert_skills(values: Sequence[Any] | None, label: str) -> None:
    for index, value in enumerate(values or ()):
        _assert_non_empty_string(
            getattr(value, "name", None), f"{label} skills[{index}].name"
        )
        _assert_non_empty_string(
            getattr(value, "description", None), f"{label} skills[{index}].description"
        )


def _assert_subagents(
    values: Sequence[Any] | None, label: str, active: set[int]
) -> None:
    for index, value in enumerate(values or ()):
        if not isinstance(value, AgentProfile):
            raise ValueError(
                f"[pyflue] {label} subagents[{index}] must be an AgentProfile "
                "(use define_agent_profile())."
            )
        _assert_agent_name(value.name, f"{label} subagents[{index}].name")
        _assert_agent_profile(value, f"{label} subagents[{index}]", active)


def _assert_agent_name(value: Any, label: str) -> None:
    _assert_non_empty_string(value, label)
    if not _AGENT_NAME_RE.match(value):
        raise ValueError(
            f"[pyflue] {label} must start with a letter and contain only "
            'letters, numbers, "_", or "-".'
        )


def _assert_non_empty_string(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"[pyflue] {label} must be a non-empty string.")


def _assert_unique_names(
    values: Sequence[Any] | None, label: str, kind: str
) -> None:
    if not values:
        return
    seen: set[str] = set()
    for value in values:
        name = getattr(value, "name", None)
        if not name:
            continue
        if name in seen:
            raise ValueError(
                f"[pyflue] {label} must not contain duplicate {kind} name {name!r}."
            )
        seen.add(name)


# camelCase aliases, matching pyflue's existing dual-naming convention.
createAgent = create_agent
defineAgentProfile = define_agent_profile

__all__ = [
    "AgentCreateContext",
    "AgentProfile",
    "AgentRuntimeConfig",
    "CreatedAgent",
    "create_agent",
    "createAgent",
    "define_agent_profile",
    "defineAgentProfile",
    "extend_agent_profile",
    "init_agent",
    "is_created_agent",
    "profile_to_role",
    "resolve_agent_profile",
    "role_to_profile",
]
