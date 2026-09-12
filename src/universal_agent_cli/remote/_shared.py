"""Shared helpers for remote (agentd thin-client) command modules."""

from __future__ import annotations

from universal_agent.core import JsonValue, SuccessCriterion

REMOTE_STATIC_JSON_ROUTES: dict[str, str] = {
    "health": "/health",
    "ready": "/ready",
    "cost": "/v1/cost",
    "logs": "/v1/logs",
    "doctor": "/v1/doctor",
    "audit": "/v1/audit",
    "multi-agent": "/v1/multi-agent",
}

REMOTE_LIST_ROUTES: dict[str, str] = {
    "domain": "/v1/domains",
    "capabilities": "/v1/capabilities",
    "tools": "/v1/tools",
    "policies": "/v1/policies",
    "evaluators": "/v1/evaluators",
    "memory": "/v1/memory",
}


def success_criteria_body(criteria: tuple[SuccessCriterion, ...]) -> list[JsonValue]:
    return [{"key": item.key, "expected": item.expected} for item in criteria]
