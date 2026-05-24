"""FastAPI response schemas for PyFlue HTTP surfaces."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ExtraModel(BaseModel):
    """Base schema that allows backward-compatible extra fields."""

    model_config = ConfigDict(extra="allow")


class ErrorBody(ExtraModel):
    type: str
    message: str
    details: str = ""
    dev: str | None = None
    meta: dict[str, Any] | None = None


class ErrorEnvelope(ExtraModel):
    error: ErrorBody


class HealthResponse(ExtraModel):
    ok: bool
    framework: str


class AgentManifestEntry(ExtraModel):
    name: str
    path: str | None = None
    triggers: dict[str, Any] = {}


class AgentListResponse(ExtraModel):
    agents: list[AgentManifestEntry]


class AdminAgentEntry(ExtraModel):
    name: str
    triggers: dict[str, Any] | None = None


class InstanceSummary(ExtraModel):
    agent_id: str | None = None
    instanceId: str


class RunRecordResponse(ExtraModel):
    run_id: str
    runId: str
    agent: str
    agentName: str
    agent_id: str
    instanceId: str
    started_at: float
    startedAt: str
    ended_at: float | None = None
    endedAt: str | None = None
    durationMs: int | None = None
    status: str
    is_error: bool
    isError: bool
    error: dict[str, Any] | None = None
    result: Any = None
    event_count: int


class RunEventResponse(ExtraModel):
    run_id: str
    runId: str
    event_index: int
    eventIndex: int
    type: str
    timestamp: float
    data: dict[str, Any]


class RunEventListResponse(ExtraModel):
    run_id: str
    runId: str | None = None
    events: list[RunEventResponse]
    next_after: int
    nextAfter: int | None = None


class AdminAgentsResponse(ExtraModel):
    agents: list[AdminAgentEntry]
    items: list[AdminAgentEntry]
    nextCursor: str | None = None


class AdminInstancesResponse(ExtraModel):
    agent: str
    agentName: str
    instances: list[InstanceSummary]
    items: list[InstanceSummary]
    nextCursor: str | None = None


class AdminRunsResponse(ExtraModel):
    runs: list[RunRecordResponse]
    items: list[RunRecordResponse]
    nextCursor: str | None = None


class WebhookAcceptedResponse(ExtraModel):
    status: str
    run_id: str
    runId: str


class PromptCostResponse(ExtraModel):
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


class PromptUsageResponse(ExtraModel):
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total_tokens: int = 0
    cost: PromptCostResponse


class PromptModelResponse(ExtraModel):
    id: str | None = None


class PromptResponse(ExtraModel):
    text: str
    metadata: dict[str, Any]
    usage: PromptUsageResponse
    model: PromptModelResponse


class AbortSessionResponse(ExtraModel):
    aborted: bool
    session_id: str


class DevRouteStatus(ExtraModel):
    name: str
    path: str
    source: str
    mtime: float | None = None
    triggers: dict[str, Any]


class MarkdownStatus(ExtraModel):
    name: str
    path: str
    mtime: float | None = None


class ActiveSessionStatus(ExtraModel):
    session_id: str
    operation: str


class DevStatusResponse(ExtraModel):
    ok: bool
    generated_at: float
    root: str
    config_path: str
    config_mtime: float | None = None
    harness: str
    sandbox: str
    route_count: int
    routes: list[DevRouteStatus]
    agents: list[str]
    skills: list[MarkdownStatus]
    roles: list[MarkdownStatus]
    active_sessions: list[ActiveSessionStatus]
