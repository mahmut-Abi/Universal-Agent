from __future__ import annotations

from collections.abc import Callable

from prometheus_client import CollectorRegistry, Gauge, generate_latest

from universal_agent.operations.views import RuntimeMetricsView

MetricValue = int | float
MetricDescriptor = tuple[str, str, Callable[[RuntimeMetricsView], MetricValue]]

_METRICS: tuple[MetricDescriptor, ...] = (
    (
        "sessions",
        "Runtime sessions listed by the session store",
        lambda metrics: metrics.session_count,
    ),
    (
        "active_sessions",
        "Runtime sessions currently active",
        lambda metrics: metrics.active_session_count,
    ),
    (
        "waiting_sessions",
        "Runtime sessions waiting for user/runtime input",
        lambda metrics: metrics.waiting_session_count,
    ),
    (
        "completed_goals",
        "Runtime goals completed",
        lambda metrics: metrics.completed_goal_count,
    ),
    (
        "failed_goals",
        "Runtime goals failed",
        lambda metrics: metrics.failed_goal_count,
    ),
    (
        "cancelled_goals",
        "Runtime goals cancelled",
        lambda metrics: metrics.cancelled_goal_count,
    ),
    ("events", "Runtime events recorded", lambda metrics: metrics.event_count),
    (
        "actions_started",
        "Runtime actions started",
        lambda metrics: metrics.action_started_count,
    ),
    (
        "actions_completed",
        "Runtime actions completed",
        lambda metrics: metrics.action_completed_count,
    ),
    (
        "decisions_generated",
        "Runtime model decisions generated",
        lambda metrics: metrics.decision_generated_count,
    ),
    (
        "decisions_validated",
        "Runtime decisions accepted by deterministic validation",
        lambda metrics: metrics.decision_validated_count,
    ),
    (
        "decisions_rejected",
        "Runtime decisions rejected by deterministic validation",
        lambda metrics: metrics.decision_rejected_count,
    ),
    (
        "tool_failures",
        "Runtime tool failures observed",
        lambda metrics: metrics.tool_failure_count,
    ),
    (
        "policy_denials",
        "Runtime policy denials observed",
        lambda metrics: metrics.policy_denial_count,
    ),
    (
        "confirmations_required",
        "Runtime confirmations required",
        lambda metrics: metrics.confirmation_required_count,
    ),
    (
        "recoveries_planned",
        "Runtime recovery plans created",
        lambda metrics: metrics.recovery_planned_count,
    ),
    (
        "recoveries_exhausted",
        "Runtime recovery paths exhausted",
        lambda metrics: metrics.recovery_exhausted_count,
    ),
    (
        "human_interventions",
        "Runtime human interventions required",
        lambda metrics: metrics.human_intervention_count,
    ),
    (
        "resource_locks_acquired",
        "Runtime resource locks acquired",
        lambda metrics: metrics.resource_lock_acquired_count,
    ),
    (
        "resource_locks_released",
        "Runtime resource locks released",
        lambda metrics: metrics.resource_lock_released_count,
    ),
    (
        "resource_conflicts",
        "Runtime resource lock conflicts detected",
        lambda metrics: metrics.resource_conflict_count,
    ),
    (
        "active_resource_locks",
        "Runtime resource locks without a matching release event",
        lambda metrics: metrics.active_resource_lock_count,
    ),
    (
        "model_calls",
        "Runtime model calls recorded",
        lambda metrics: metrics.model_call_count,
    ),
    (
        "model_input_tokens",
        "Runtime model input tokens recorded",
        lambda metrics: metrics.model_input_token_count,
    ),
    (
        "model_output_tokens",
        "Runtime model output tokens recorded",
        lambda metrics: metrics.model_output_token_count,
    ),
    (
        "model_total_tokens",
        "Runtime model total tokens recorded",
        lambda metrics: metrics.model_total_token_count,
    ),
    (
        "model_estimated_cost_micros",
        "Runtime model estimated cost in micros",
        lambda metrics: metrics.model_estimated_cost_micros,
    ),
)


def build_prometheus_metrics_export(
    metrics: RuntimeMetricsView,
    *,
    prefix: str = "universal_agent_runtime",
) -> str:
    """Project runtime metrics into Prometheus text exposition format.

    This is a product adapter over RuntimeMetricsView. It does not add a metrics
    storage backend or change how runtime metrics are derived from events.
    """
    registry = CollectorRegistry()
    for suffix, help_text, value_factory in _METRICS:
        Gauge(_prometheus_metric_name(prefix, suffix), help_text, registry=registry).set(
            value_factory(metrics)
        )
    return generate_latest(registry).decode("utf-8")


def _prometheus_metric_name(prefix: str, suffix: str) -> str:
    name = f"{_prometheus_name_fragment(prefix)}_{_prometheus_name_fragment(suffix)}"
    if name[0].isdigit():
        return f"_{name}"
    return name


def _prometheus_name_fragment(value: str) -> str:
    cleaned = "".join(
        character if character.isascii() and (character.isalnum() or character == "_") else "_"
        for character in value.strip()
    ).strip("_")
    return cleaned or "metric"
