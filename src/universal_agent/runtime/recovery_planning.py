"""Recovery planning for the agent run loop.

Extracted from AgentRuntime (UA-P0-003) so recovery orchestration lives in one
focused module. The planner is pure orchestration over the recovery manager,
the event emitter and the settle boundary: every dependency is an explicit
parameter, so recovery behavior is testable without the runtime loop.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from universal_agent.core import (
    Decision,
    DecisionType,
    ErrorCode,
    ExecutionResult,
    PendingAction,
)
from universal_agent.domain import RuntimeComponents
from universal_agent.recovery import Failure, RecoveryStrategy, classify_failure
from universal_agent.runtime.actions import ActionObserved
from universal_agent.runtime.emission import EventEmitter
from universal_agent.runtime.session import SessionRuntimeState
from universal_agent.runtime.transitions import (
    Transition,
    fail,
)
from universal_agent.runtime.transitions import (
    pause as pause_transition,
)

SettleFn = Callable[[SessionRuntimeState, Transition], Awaitable[ExecutionResult]]

__all__ = ["plan_recovery", "plan_recovery_for_failure", "plan_recovery_for_pending"]


async def plan_recovery(
    components: RuntimeComponents,
    events: EventEmitter,
    settle: SettleFn,
    session: SessionRuntimeState,
    outcome: ActionObserved,
) -> ExecutionResult | Decision:
    """Plan recovery from a failed action observation."""

    state = session.state
    pending = outcome.pending
    observation = outcome.observation
    error_code = observation.error_code or ErrorCode.TOOL_FAILURE
    return await plan_recovery_for_failure(
        components,
        events,
        settle,
        session,
        outcome,
        Failure(
            state.current_task.id,
            error_code,
            classify_failure(error_code),
            observation.error or "tool execution failed",
            pending.capability,
            pending.arguments,
            pending.target,
        ),
    )


async def plan_recovery_for_failure(
    components: RuntimeComponents,
    events: EventEmitter,
    settle: SettleFn,
    session: SessionRuntimeState,
    outcome: ActionObserved,
    failure: Failure,
) -> ExecutionResult | Decision:
    """Plan recovery from an explicit failure."""

    return await plan_recovery_for_pending(
        components,
        events,
        settle,
        session,
        outcome.pending,
        failure,
    )


async def plan_recovery_for_pending(
    components: RuntimeComponents,
    events: EventEmitter,
    settle: SettleFn,
    session: SessionRuntimeState,
    pending: PendingAction,
    failure: Failure,
) -> ExecutionResult | Decision:
    """Decide and record the recovery step for a pending action's failure."""

    state = session.state
    recovery, key = components.recovery_manager.decide(failure, state.recovery_attempts)
    if key:
        state.recovery_attempts[key] = recovery.attempt
    # Persist the spent budget before retrying so a crash cannot reset it.
    await events.commit_session_event(
        session,
        events.runtime_event(
            state,
            "RecoveryExhausted" if recovery.exhausted else "RecoveryPlanned",
            action_id=pending.action_id,
            data={"strategy": recovery.strategy.value, "rule": recovery.rule_name},
        ),
    )
    if recovery.strategy in {
        RecoveryStrategy.RETRY_ACTION,
        RecoveryStrategy.REOBSERVE,
        RecoveryStrategy.ALTERNATIVE_CAPABILITY,
    }:
        return Decision(
            DecisionType.EXECUTE,
            f"recovery via {recovery.strategy.value}",
            capability=recovery.capability or pending.capability,
            target=pending.target,
            arguments=pending.arguments,
            expected_observations=("recovery",),
        )
    if recovery.strategy is RecoveryStrategy.ASK_USER:
        return await settle(
            session,
            pause_transition(
                session,
                failure.reason,
                user_message=f"Recovery requires user input: {failure.reason}",
            ),
        )
    return await settle(session, fail(session, failure.error_code, failure.reason))
