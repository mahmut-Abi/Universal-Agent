from __future__ import annotations

from collections.abc import Awaitable, Callable
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
            capability, tool = self.components.resolver.resolve(decision.capability or "")
        except UnknownCapabilityError as exc:
            return ActionRejected(ErrorCode.UNKNOWN_CAPABILITY, str(exc))
        except CapabilityUnavailableError as exc:
            return ActionRejected(ErrorCode.NO_CAPABILITY_TOOL, str(exc))
        pending = PendingAction(
            action_id=new_action_id(),
            capability=capability.name,
            tool_name=tool.definition.name,
            target=decision.target,
            arguments=decision.arguments,
        )
        await emit(
            "CapabilityResolved",
            pending.action_id,
            {"capability": capability.name, "tool_name": tool.definition.name},
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
        capability, tool = self.components.resolver.resolve(pending.capability)
        if tool.definition.name != pending.tool_name:
            return ActionRejected(
                ErrorCode.INVALID_STATE,
                "pending action tool resolution changed",
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
            {"effect": policy_result.effect.value, "policy": policy_result.policy_name},
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
        call = ToolCall(
            action_id=pending.action_id,
            tool_name=pending.tool_name,
            capability=pending.capability,
            arguments=pending.arguments,
            target=pending.target,
        )
        await emit(
            "ActionStarted",
            call.action_id,
            {"tool_name": call.tool_name, "capability": call.capability},
        )
        tool_result = await self._tool_runtime.execute(call)
        await emit("ActionCompleted", call.action_id, {"status": tool_result.status.value})
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
