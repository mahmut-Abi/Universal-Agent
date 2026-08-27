from __future__ import annotations

from universal_agent.operations.views import RuntimeMetricsView


def build_prometheus_metrics_export(
    metrics: RuntimeMetricsView,
    *,
    prefix: str = "universal_agent_runtime",
) -> str:
    """Project runtime metrics into Prometheus text exposition format.

    This is a product adapter over RuntimeMetricsView. It does not add a metrics
    backend dependency or change how runtime metrics are derived from events.
    """
    metric_values = (
        ("sessions", "Runtime sessions listed by the session store", metrics.session_count),
        ("active_sessions", "Runtime sessions currently active", metrics.active_session_count),
        (
            "waiting_sessions",
            "Runtime sessions waiting for user/runtime input",
            metrics.waiting_session_count,
        ),
        ("completed_goals", "Runtime goals completed", metrics.completed_goal_count),
        ("failed_goals", "Runtime goals failed", metrics.failed_goal_count),
        ("cancelled_goals", "Runtime goals cancelled", metrics.cancelled_goal_count),
        ("events", "Runtime events recorded", metrics.event_count),
        ("actions_started", "Runtime actions started", metrics.action_started_count),
        ("actions_completed", "Runtime actions completed", metrics.action_completed_count),
        (
            "decisions_generated",
            "Runtime model decisions generated",
            metrics.decision_generated_count,
        ),
        (
            "decisions_validated",
            "Runtime decisions accepted by deterministic validation",
            metrics.decision_validated_count,
        ),
        (
            "decisions_rejected",
            "Runtime decisions rejected by deterministic validation",
            metrics.decision_rejected_count,
        ),
        ("tool_failures", "Runtime tool failures observed", metrics.tool_failure_count),
        ("policy_denials", "Runtime policy denials observed", metrics.policy_denial_count),
        (
            "confirmations_required",
            "Runtime confirmations required",
            metrics.confirmation_required_count,
        ),
        ("recoveries_planned", "Runtime recovery plans created", metrics.recovery_planned_count),
        (
            "recoveries_exhausted",
            "Runtime recovery paths exhausted",
            metrics.recovery_exhausted_count,
        ),
        (
            "human_interventions",
            "Runtime human interventions required",
            metrics.human_intervention_count,
        ),
        (
            "resource_locks_acquired",
            "Runtime resource locks acquired",
            metrics.resource_lock_acquired_count,
        ),
        (
            "resource_locks_released",
            "Runtime resource locks released",
            metrics.resource_lock_released_count,
        ),
        (
            "resource_conflicts",
            "Runtime resource lock conflicts detected",
            metrics.resource_conflict_count,
        ),
        (
            "active_resource_locks",
            "Runtime resource locks without a matching release event",
            metrics.active_resource_lock_count,
        ),
        ("model_calls", "Runtime model calls recorded", metrics.model_call_count),
        (
            "model_input_tokens",
            "Runtime model input tokens recorded",
            metrics.model_input_token_count,
        ),
        (
            "model_output_tokens",
            "Runtime model output tokens recorded",
            metrics.model_output_token_count,
        ),
        (
            "model_total_tokens",
            "Runtime model total tokens recorded",
            metrics.model_total_token_count,
        ),
        (
            "model_estimated_cost_micros",
            "Runtime model estimated cost in micros",
            metrics.model_estimated_cost_micros,
        ),
    )
    lines: list[str] = []
    for suffix, help_text, value in metric_values:
        name = _prometheus_metric_name(prefix, suffix)
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"


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
