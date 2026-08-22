from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field

from universal_agent.capability import (
    CapabilityUnavailableError,
    UnknownCapabilityError,
)
from universal_agent.core import (
    ActionId,
    Decision,
    ErrorCode,
    JsonMapping,
    JsonValue,
    Observation,
    PendingAction,
    PolicyContext,
    PolicyEffect,
    ToolCall,
    new_action_id,
)
from universal_agent.domain import RuntimeComponents
from universal_agent.observation import ObservationFactory
from universal_agent.runtime.session import SessionRuntimeState
from universal_agent.tools import ToolRuntime

EmitFn = Callable[[str, ActionId | None, dict[str, object]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ActionRejected:
    """The action never reached a tool: unknown capability, policy deny, or drift."""

    error_code: ErrorCode
    reason: str


@dataclass(frozen=True, slots=True)
class ConfirmationRequired:
    pending: PendingAction
    reason: str


@dataclass(frozen=True, slots=True)
class ActionObserved:
    pending: PendingAction
    observation: Observation


ActionOutcome = ActionRejected | ConfirmationRequired | ActionObserved


@dataclass(slots=True)
class ActionExecutor:
    """Turns a decision into an observation.

    Owns capability resolution, policy checks and tool invocation. It never
    decides whether the goal ends; it reports what happened and lets the runtime
    choose the transition.
    """

    components: RuntimeComponents
    environment: JsonMapping
    _tool_runtime: ToolRuntime = field(init=False)
    _observations: ObservationFactory = field(init=False)

    def __post_init__(self) -> None:
        self._tool_runtime = ToolRuntime(self.components.tools)
        self._observations = ObservationFactory()

    async def prepare(
        self,
        session: SessionRuntimeState,
        decision: Decision,
        emit: EmitFn,
    ) -> ActionOutcome:
        try:
            resolution = self.components.resolver.resolve_registration(decision.capability or "")
        except UnknownCapabilityError as exc:
            return ActionRejected(ErrorCode.UNKNOWN_CAPABILITY, str(exc))
        except CapabilityUnavailableError as exc:
            return ActionRejected(ErrorCode.NO_CAPABILITY_TOOL, str(exc))
        capability = resolution.capability
        tool = resolution.tool
        domain_name = resolution.capability_domain.name if resolution.capability_domain else ""
        domain_version = (
            resolution.capability_domain.version if resolution.capability_domain else ""
        )
        parameters_hash = _action_parameters_hash(decision)
        pending = PendingAction(
            action_id=new_action_id(),
            capability=capability.name,
            tool_name=tool.definition.name,
            target=decision.target,
            arguments=decision.arguments,
            domain_name=domain_name,
            domain_version=domain_version,
            idempotency_key=_idempotency_key(
                session.state.session_id,
                session.state.current_task.id,
                parameters_hash,
            ),
            parameters_hash=parameters_hash,
            attempt=1,
        )
        await emit(
            "CapabilityResolved",
            pending.action_id,
            {
                "capability": capability.name,
                "tool_name": tool.definition.name,
                "domain": domain_name,
                "domain_version": domain_version,
                "idempotency_key": pending.idempotency_key,
                "parameters_hash": pending.parameters_hash,
                "attempt": pending.attempt,
            },
        )
        return await self.execute(session, pending, emit, confirmed=False)

    async def execute(
        self,
        session: SessionRuntimeState,
        pending: PendingAction,
        emit: EmitFn,
        *,
        confirmed: bool,
    ) -> ActionOutcome:
        state = session.state
        try:
            resolution = self.components.resolver.resolve_registration(pending.capability)
        except (UnknownCapabilityError, CapabilityUnavailableError) as exc:
            return ActionRejected(ErrorCode.INVALID_STATE, str(exc))
        capability = resolution.capability
        tool = resolution.tool
        if tool.definition.name != pending.tool_name:
            return ActionRejected(
                ErrorCode.INVALID_STATE,
                "pending action tool resolution changed",
            )
        domain = resolution.capability_domain
        if domain is not None and (
            pending.domain_name != domain.name or pending.domain_version != domain.version
        ):
            return ActionRejected(
                ErrorCode.INVALID_STATE,
                "pending action domain resolution changed",
            )
        policy_result = self.components.policy_engine.check(
            PolicyContext(
                session_id=state.session_id,
                goal_id=state.goal.id,
                task_id=state.current_task.id,
                action_id=pending.action_id,
                capability=capability,
                tool=tool.definition,
                target=pending.target,
                arguments=pending.arguments,
                environment=self.environment,
                confirmed=confirmed,
            )
        )
        await emit(
            "PolicyChecked",
            pending.action_id,
            {
                "effect": policy_result.effect.value,
                "policy": policy_result.policy_name,
                "capability": capability.name,
                "tool_name": tool.definition.name,
                "side_effect": tool.definition.side_effect.value,
                "risk": tool.definition.risk.value,
            },
        )
        if policy_result.effect is PolicyEffect.DENY:
            return ActionRejected(ErrorCode.POLICY_DENIED, policy_result.reason)
        if policy_result.effect is PolicyEffect.REQUIRE_CONFIRMATION:
            state.pending_action = pending
            return ConfirmationRequired(pending, policy_result.reason)
        state.pending_action = None
        return await self._invoke(session, pending, emit)

    async def _invoke(
        self,
        session: SessionRuntimeState,
        pending: PendingAction,
        emit: EmitFn,
    ) -> ActionObserved:
        state = session.state
        tool = self.components.tools.resolve(pending.tool_name)
        call = ToolCall(
            action_id=pending.action_id,
            tool_name=pending.tool_name,
            capability=pending.capability,
            arguments=pending.arguments,
            target=pending.target,
            domain_name=pending.domain_name,
            domain_version=pending.domain_version,
            idempotency_key=pending.idempotency_key,
            parameters_hash=pending.parameters_hash,
            attempt=pending.attempt,
        )
        await emit(
            "ActionStarted",
            call.action_id,
            {
                "tool_name": call.tool_name,
                "capability": call.capability,
                "side_effect": tool.definition.side_effect.value,
                "risk": tool.definition.risk.value,
                "domain": call.domain_name,
                "domain_version": call.domain_version,
                "idempotency_key": call.idempotency_key,
                "parameters_hash": call.parameters_hash,
                "attempt": call.attempt,
            },
        )
        tool_result = await self._tool_runtime.execute(call)
        await emit(
            "ActionCompleted",
            call.action_id,
            {
                "status": tool_result.status.value,
                "error_code": (
                    None if tool_result.error_code is None else tool_result.error_code.value
                ),
            },
        )
        observation = self._observations.from_tool_result(
            task_id=state.current_task.id,
            call=call,
            result=tool_result,
        )
        state.observations.append(observation)
        await emit(
            "ObservationReceived",
            observation.action_id,
            {"observation_id": observation.id, "status": observation.status.value},
        )
        return ActionObserved(pending, observation)


def _action_parameters_hash(decision: Decision) -> str:
    payload = {
        "arguments": _canonical_json(decision.arguments),
        "capability": decision.capability,
        "target": decision.target,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _idempotency_key(session_id: object, task_id: object, parameters_hash: str) -> str:
    return f"{session_id}:{task_id}:{parameters_hash[:16]}"


def _canonical_json(value: JsonValue | object) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        return {str(key): _canonical_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_canonical_json(item) for item in value]
    return str(value)
