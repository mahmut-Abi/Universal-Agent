from __future__ import annotations

from dataclasses import dataclass, field

from universal_agent.core import (
    ActionId,
    AgentState,
    ErrorCode,
    EvaluationStatus,
    ExecutionResult,
    ExecutionStatus,
    GoalStatus,
    TaskStatus,
)
from universal_agent.runtime.session import SessionRuntimeState, mark_current_task


@dataclass(frozen=True, slots=True)
class Transition:
    """A terminal state change plus the event the runtime should emit for it.

    Transitions mutate session state but never persist or publish. The runtime
    owns the store and the event sink so lower layers stay free of both.
    """

    result: ExecutionResult
    event_type: str
    event_data: dict[str, object] = field(default_factory=dict)
    action_id: ActionId | None = None


def build_result(
    state: AgentState,
    status: ExecutionStatus,
    reason: str,
    *,
    error_code: ErrorCode | None = None,
    user_message: str | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        status=status,
        session_id=state.session_id,
        goal_id=state.goal.id,
        task_id=state.current_task.id,
        iterations=state.iteration,
        reason=reason,
        error_code=error_code,
        user_message=user_message,
    )


def pause(
    session: SessionRuntimeState,
    reason: str,
    *,
    user_message: str | None = None,
    event_type: str = "GoalWaiting",
    action_id: ActionId | None = None,
) -> Transition:
    state = session.state
    state.goal.status = GoalStatus.WAITING
    mark_current_task(session, TaskStatus.WAITING)
    state.termination_reason = reason
    return Transition(
        build_result(state, ExecutionStatus.WAITING, reason, user_message=user_message),
        event_type,
        action_id=action_id,
    )


def fail(
    session: SessionRuntimeState,
    error_code: ErrorCode,
    reason: str,
) -> Transition:
    state = session.state
    state.goal.status = GoalStatus.FAILED
    mark_current_task(session, TaskStatus.FAILED)
    state.termination_reason = reason
    state.error_code = error_code
    return Transition(
        build_result(state, ExecutionStatus.FAILED, reason, error_code=error_code),
        "GoalFailed",
        {"error_code": error_code.value, "reason": reason},
    )


def finish(session: SessionRuntimeState) -> Transition:
    """Complete the goal, or fail when the evaluator has not authorised it.

    The model may ask to finish at any time; only evaluator state decides.
    """
    state = session.state
    evaluation = state.latest_evaluation
    if (
        session.tasks.has_unfinished()
        or state.current_task.status is not TaskStatus.COMPLETED
        or evaluation is None
        or evaluation.status is not EvaluationStatus.COMPLETED
    ):
        return fail(
            session,
            ErrorCode.INVALID_STATE,
            "finish rejected because evaluator has not completed the task and goal",
        )
    state.goal.status = GoalStatus.COMPLETED
    state.termination_reason = evaluation.reason
    return Transition(
        build_result(state, ExecutionStatus.COMPLETED, evaluation.reason),
        "GoalCompleted",
    )
