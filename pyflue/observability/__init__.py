"""Observability adapters for PyFlue's event stream."""

from __future__ import annotations

from pyflue.observability.otel import create_opentelemetry_observer

__all__ = ["create_opentelemetry_observer"]
