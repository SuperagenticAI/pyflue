"""Typed errors and HTTP request helpers for PyFlue servers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class PyFlueError(Exception):
    """Base error with a stable wire shape."""

    type: str
    message: str
    details: str = ""
    status: int = 500
    dev: str = ""
    meta: dict[str, Any] | None = None


class MethodNotAllowedError(PyFlueError):
    """Raised when an endpoint receives an unsupported method."""

    def __init__(self, method: str, allowed: list[str]):
        super().__init__(
            type="method_not_allowed",
            message=f"Method {method} is not allowed.",
            details=f"Use one of: {', '.join(allowed)}.",
            status=405,
            meta={"allowed": allowed},
        )


class UnsupportedMediaTypeError(PyFlueError):
    """Raised when a request body is not JSON."""

    def __init__(self, received: str | None):
        super().__init__(
            type="unsupported_media_type",
            message="Request body must use application/json.",
            details="Send a JSON body or omit the body entirely.",
            status=415,
            meta={"received": received},
        )


class InvalidJsonError(PyFlueError):
    """Raised when a JSON request body cannot be parsed."""

    def __init__(self, parse_error: str):
        super().__init__(
            type="invalid_json",
            message="Request body is not valid JSON.",
            details="Send a valid JSON object with an optional payload field.",
            status=400,
            dev=parse_error,
        )


class InvalidRequestError(PyFlueError):
    """Raised when a request shape is invalid."""

    def __init__(self, reason: str, *, status: int = 400):
        super().__init__(
            type="invalid_request",
            message="Request is invalid.",
            details=reason,
            status=status,
        )


class AgentNotFoundError(PyFlueError):
    """Raised when a route name is not registered."""

    def __init__(self, name: str, available: list[str]):
        super().__init__(
            type="agent_not_found",
            message=f'Agent "{name}" is not registered.',
            details="Verify the agent name is correct.",
            status=404,
            dev=f"Available agents: {', '.join(available) or '(none)'}.",
        )


class AgentNotWebhookError(PyFlueError):
    """Raised when a route exists but webhook access is disabled."""

    def __init__(self, name: str):
        super().__init__(
            type="agent_not_webhook",
            message=f'Agent "{name}" is not webhook-enabled.',
            details="This agent cannot be invoked through the HTTP webhook endpoint.",
            status=404,
        )


def error_envelope(error: PyFlueError, *, dev: bool = False) -> dict[str, Any]:
    """Return the stable JSON error envelope."""
    payload: dict[str, Any] = {
        "type": error.type,
        "message": error.message,
        "details": error.details,
    }
    if dev and error.dev:
        payload["dev"] = error.dev
    if error.meta:
        payload["meta"] = error.meta
    return {"error": payload}


async def parse_json_payload(request: Any) -> dict[str, Any]:
    """Parse a FastAPI request body into a payload dictionary."""
    content_type = request.headers.get("content-type")
    body = await request.body()
    if not body:
        return {}
    if not content_type or "application/json" not in content_type.lower():
        raise UnsupportedMediaTypeError(content_type)
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise InvalidJsonError(str(exc)) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise InvalidRequestError("The request body must be a JSON object.")
    payload = data.get("payload", data)
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise InvalidRequestError("The payload field must be a JSON object.")
    return payload


def validate_agent_request(
    *,
    name: str,
    agent_id: str,
    registered_agents: list[str],
    webhook_agents: list[str],
) -> None:
    """Validate route shape and webhook availability."""
    if not name.strip() or not agent_id.strip():
        raise InvalidRequestError(
            "Webhook URLs must have the shape /agents/{name}/{agent_id} with non-empty segments."
        )
    if name not in registered_agents:
        raise AgentNotFoundError(name, registered_agents)
    if name not in webhook_agents:
        raise AgentNotWebhookError(name)
