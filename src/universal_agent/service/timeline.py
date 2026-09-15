"""Session event timeline projection for operator drill-down surfaces.

Builds a correlated timeline view from runtime events so the web console and
TUI can answer "why did the agent do this?" without a second event store:
steps group related events by stable identifiers (goal/task/action), link
evidence and observations to their originating action, and surface decision
payloads that were already redacted at emission time.

This module is a pure projection: it consumes ``RuntimeEventView`` objects
and returns JSON-safe payloads. It performs no I/O and owns no state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from universal_agent.core import JsonMapping, JsonValue, to_json_object
from universal_agent.runtime import RuntimeEventView

__all__ = [
    "TIMELINE_STEP_TYPES",
    "TimelineStepView",
    "timeline_body",
    "timeline_steps",
]

#: Event types that open a new timeline step (work units). Goal/task
#: lifecycle events are context: they attach chronologically to the current
#: step instead of opening one.
TIMELINE_STEP_TYPES = (
    "DecisionGenerated",
    "ActionStarted",
)


@dataclass(frozen=True, slots=True)
class TimelineStepView:
    """One correlated execution step in a session timeline."""

    step_id: str
    label: str
    event_types: tuple[str, ...]
    goal_id: str
    task_id: str
    action_id: str | None
    observation_id: str | None
    evidence_ids: tuple[str, ...]
    policy_effect: str | None
    decision: JsonMapping | None
    events: tuple[JsonMapping, ...]


def _event_body(view: RuntimeEventView) -> JsonMapping:
    return cast(JsonMapping, to_json_object(view, fallback_to_string=True))


def _event_data(body: JsonMapping) -> JsonMapping:
    data = body.get("data")
    return data if isinstance(data, dict) else cast(JsonMapping, {})


def _event_action_id(view: RuntimeEventView) -> str | None:
    return None if view.action_id is None else str(view.action_id)


def _step_label(event_type: str, data: JsonMapping) -> str:
    if event_type == "DecisionGenerated":
        decision_type = data.get("decision_type")
        capability = data.get("capability")
        if capability:
            return f"Decision: {capability} ({decision_type})"
        return f"Decision: {decision_type}"
    if event_type == "ActionStarted":
        return f"Action: {data.get('tool_name') or data.get('capability')}"
    if event_type == "PolicyChecked":
        return f"Policy: {data.get('policy')} -> {data.get('effect')}"
    if event_type == "ObservationReceived":
        return f"Observation: {data.get('status')}"
    if event_type == "EvidenceRecorded":
        return f"Evidence: {data.get('claim')}"
    return event_type


def _decision_payload(data: JsonMapping) -> JsonMapping | None:
    """Return the redacted decision payload carried by DecisionGenerated."""
    if "decision_type" not in data:
        return None
    decision: dict[str, JsonValue] = {
        "type": data.get("decision_type"),
        "reason": data.get("reason"),
        "arguments": data.get("arguments"),
        "expected_observations": data.get("expected_observations"),
    }
    if data.get("capability") is not None:
        decision["capability"] = data["capability"]
    if data.get("target") is not None:
        decision["target"] = data["target"]
    return cast(JsonMapping, decision)


class _StepAccumulator:
    """Mutable builder for one timeline step (module-internal only)."""

    def __init__(self, view: RuntimeEventView) -> None:
        self.goal_id = str(view.goal_id)
        self.task_id = str(view.task_id)
        self.action_id = _event_action_id(view)
        self.observation_id: str | None = None
        self.evidence_ids: list[str] = []
        self.policy_effect: str | None = None
        self.decision: JsonMapping | None = None
        self.event_types: list[str] = []
        self.event_bodies: list[JsonMapping] = []
        self.add(view)

    def add(self, view: RuntimeEventView) -> None:
        """Append one event; called exactly once per (step, event) pair."""
        body = _event_body(view)
        data = _event_data(body)
        event_type = str(body["type"])
        if event_type == "ObservationReceived" and "observation_id" in data:
            self.observation_id = str(data["observation_id"])
        elif event_type == "EvidenceRecorded" and "evidence_id" in data:
            self.evidence_ids.append(str(data["evidence_id"]))
        elif event_type == "PolicyChecked" and "effect" in data:
            self.policy_effect = str(data["effect"])
        elif event_type == "DecisionGenerated":
            self.decision = _decision_payload(data)
        self.event_types.append(event_type)
        self.event_bodies.append(body)

    def freeze(self, step_id: str) -> TimelineStepView:
        first_type = self.event_types[0] if self.event_types else ""
        first_data = _event_data(self.event_bodies[0]) if self.event_bodies else None
        return TimelineStepView(
            step_id=step_id,
            label=_step_label(first_type, first_data or cast(JsonMapping, {})),
            event_types=tuple(self.event_types),
            goal_id=self.goal_id,
            task_id=self.task_id,
            action_id=self.action_id,
            observation_id=self.observation_id,
            evidence_ids=tuple(self.evidence_ids),
            policy_effect=self.policy_effect,
            decision=self.decision,
            events=tuple(self.event_bodies),
        )


def timeline_steps(events: tuple[RuntimeEventView, ...]) -> tuple[TimelineStepView, ...]:
    """Group a session's runtime events into correlated timeline steps.

    A new step opens on each goal/task boundary or decision/action start;
    subsequent related events (policy check, tool result, observation,
    evidence) attach to the open step. Events carrying an ``action_id`` attach
    to the step with the same action; everything else appends chronologically.
    """

    steps: list[_StepAccumulator] = []
    by_action: dict[str, _StepAccumulator] = {}
    for view in events:
        action_id = _event_action_id(view)
        target: _StepAccumulator | None = None
        if action_id is not None and action_id in by_action:
            target = by_action[action_id]
            target.add(view)
        elif view.type in TIMELINE_STEP_TYPES:
            target = _StepAccumulator(view)
            steps.append(target)
            if action_id is not None:
                by_action[action_id] = target
        else:
            target = steps[-1] if steps else None
            if target is None:
                target = _StepAccumulator(view)
                steps.append(target)
            else:
                target.add(view)
    return tuple(step.freeze(f"step-{index + 1}") for index, step in enumerate(steps))


def timeline_body(events: tuple[RuntimeEventView, ...]) -> JsonMapping:
    """JSON-safe timeline payload for console/TUI session drill-down."""

    steps = timeline_steps(events)
    return cast(
        JsonMapping,
        to_json_object(
            {
                "step_count": len(steps),
                "steps": [
                    {
                        "step_id": step.step_id,
                        "label": step.label,
                        "event_types": list(step.event_types),
                        "goal_id": step.goal_id,
                        "task_id": step.task_id,
                        "action_id": step.action_id,
                        "observation_id": step.observation_id,
                        "evidence_ids": list(step.evidence_ids),
                        "policy_effect": step.policy_effect,
                        "decision": step.decision,
                        "event_count": len(step.events),
                        "events": [dict(body) for body in step.events],
                    }
                    for step in steps
                ],
            },
            fallback_to_string=True,
        ),
    )
