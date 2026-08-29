from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from universal_agent.core import (
    ActionId,
    ErrorCode,
    GoalId,
    JsonMapping,
    SessionId,
    TaskId,
)


@dataclass(frozen=True, slots=True)
class RuntimeMetricsView:
    session_count: int
    active_session_count: int
    waiting_session_count: int
    completed_goal_count: int
    failed_goal_count: int
    cancelled_goal_count: int
    event_count: int
    action_started_count: int
    action_completed_count: int
    tool_failure_count: int
    policy_denial_count: int
    confirmation_required_count: int
    recovery_planned_count: int
    recovery_exhausted_count: int
    human_intervention_count: int
    resource_lock_acquired_count: int
    resource_lock_released_count: int
    resource_conflict_count: int
    active_resource_lock_count: int
    decision_generated_count: int = 0
    decision_validated_count: int = 0
    decision_rejected_count: int = 0
    policy_checked_count: int = 0
    evaluation_count: int = 0
    evaluation_success_count: int = 0
    evaluation_failure_count: int = 0
    current_task_completed_count: int = 0
    goal_completion_rate: float = 0.0
    task_success_rate: float = 0.0
    action_success_rate: float = 0.0
    tool_failure_rate: float = 0.0
    policy_denial_rate: float = 0.0
    recovery_rate: float = 0.0
    human_intervention_rate: float = 0.0
    verification_success_rate: float = 0.0
    model_call_count: int = 0
    model_input_token_count: int = 0
    model_output_token_count: int = 0
    model_total_token_count: int = 0
    model_estimated_cost_micros: int = 0


@dataclass(frozen=True, slots=True)
class ModelCostBreakdownView:
    provider: str
    model: str
    call_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_micros: int
    currency: str


@dataclass(frozen=True, slots=True)
class RuntimeCostView:
    model_call_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_micros: int
    currency: str
    by_model: tuple[ModelCostBreakdownView, ...]


@dataclass(frozen=True, slots=True)
class DoctorCheckView:
    name: str
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class DoctorReportView:
    status: str
    checks: tuple[DoctorCheckView, ...]


@dataclass(frozen=True, slots=True)
class AuditRecordView:
    record_id: str
    session_id: SessionId
    goal_id: GoalId
    task_id: TaskId
    action_id: ActionId | None
    capability: str
    tool_name: str
    side_effect: str
    risk: str
    policy_effect: str
    policy_name: str
    status: str
    occurred_at: datetime
    completed_at: datetime | None = None
    error_code: ErrorCode | None = None


@dataclass(frozen=True, slots=True)
class RuntimeLogRecordView:
    log_id: str
    level: str
    message: str
    event_type: str
    session_id: SessionId
    goal_id: GoalId
    task_id: TaskId
    action_id: ActionId | None
    data: JsonMapping
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RuntimeTraceSpanView:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: str
    status: str
    session_id: SessionId
    goal_id: GoalId
    task_id: TaskId
    action_id: ActionId | None
    start_time: datetime
    end_time: datetime
    duration_ms: float
    attributes: JsonMapping
