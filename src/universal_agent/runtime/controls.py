"""Session control and capability-constraint helpers for the runtime loop.

These are runtime-loop seams extracted from ``runtime.agent`` so the kernel
loop stays focused on orchestration. ``AgentRuntime`` composes them; other
kernel modules may import them as part of the runtime package's internal
contract. The public ``AgentRuntime`` methods are unchanged.
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

RISK_RANK = {
    "low": 0,
    "medium": 1,
    "high": 2,
}
"""Ordering rank per capability risk level; higher values are riskier."""


@dataclass(slots=True)
class SessionControl:
    """Per-session coordination state for cancel/pause/resume transitions.

    One instance exists per running session; ``AgentRuntime`` owns the
    lifecycle and uses the lock to serialize control transitions against the
    execution loop.
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active_task: asyncio.Task[Any] | None = None
    cancel_requested: bool = False
    cancel_reason: str | None = None
    pause_requested: bool = False
    pause_reason: str | None = None


def constrain_capability_context(
    state: AgentState,
    capabilities: tuple[CapabilityDefinition, ...],
    input_contracts: tuple[CapabilityInputContract, ...],
) -> tuple[tuple[CapabilityDefinition, ...], tuple[CapabilityInputContract, ...]]:
    """Drop mutation capabilities and their contracts for read-only sessions."""
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


def validate_session_constraints(
    state: AgentState,
    decision: Decision,
    capabilities: tuple[CapabilityDefinition, ...],
) -> tuple[ErrorCode, str] | None:
    """Return a policy denial for mutation decisions in read-only sessions.

    This is a deterministic kernel-side guard that runs before the policy
    engine so a read-only session can never execute a mutation capability,
    regardless of what the model proposed.
    """
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
    "RISK_RANK",
    "SessionControl",
    "constrain_capability_context",
    "validate_session_constraints",
]
