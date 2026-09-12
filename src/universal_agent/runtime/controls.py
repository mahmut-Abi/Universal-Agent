"""Session control and capability-constraint helpers for the runtime loop.

These are internal seams extracted from ``runtime.agent`` so the kernel loop
stays focused on orchestration. The public ``AgentRuntime`` methods are
unchanged; only module placement moved.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from universal_agent.core import (
    AgentState,
    CapabilityCategory,
    CapabilityDefinition,
    CapabilityInputContract,
    Decision,
    DecisionType,
    ErrorCode,
)

_RISK_RANK = {
    "low": 0,
    "medium": 1,
    "high": 2,
}


@dataclass(slots=True)
class _SessionControl:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active_task: asyncio.Task[Any] | None = None
    cancel_requested: bool = False
    cancel_reason: str | None = None
    pause_requested: bool = False
    pause_reason: str | None = None


def _constrain_capability_context(
    state: AgentState,
    capabilities: tuple[CapabilityDefinition, ...],
    input_contracts: tuple[CapabilityInputContract, ...],
) -> tuple[tuple[CapabilityDefinition, ...], tuple[CapabilityInputContract, ...]]:
    if not state.read_only:
        return capabilities, input_contracts
    allowed_capability_names = {
        capability.name
        for capability in capabilities
        if capability.category is not CapabilityCategory.MUTATION
    }
    return (
        tuple(
            capability for capability in capabilities if capability.name in allowed_capability_names
        ),
        tuple(
            contract
            for contract in input_contracts
            if contract.capability in allowed_capability_names
        ),
    )


def _validate_session_constraints(
    state: AgentState,
    decision: Decision,
    capabilities: tuple[CapabilityDefinition, ...],
) -> tuple[ErrorCode, str] | None:
    if not state.read_only or decision.type is not DecisionType.EXECUTE:
        return None
    capability_name = decision.capability or ""
    capability = next((item for item in capabilities if item.name == capability_name), None)
    if capability is None or capability.category is not CapabilityCategory.MUTATION:
        return None
    return (
        ErrorCode.POLICY_DENIED,
        f"read-only session cannot execute mutation capability: {capability.name}",
    )


__all__ = [
    "_RISK_RANK",
    "_SessionControl",
    "_constrain_capability_context",
    "_validate_session_constraints",
]
