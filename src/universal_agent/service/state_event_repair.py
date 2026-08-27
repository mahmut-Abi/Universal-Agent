from __future__ import annotations

from universal_agent.core import GoalStatus, RuntimeEvent
from universal_agent.runtime import RuntimeEventView, SessionSummaryView, event_view
from universal_agent.service.views import (
    StateEventRepairSkipView,
    StateEventRepairView,
)

_TERMINAL_EVENT_BY_GOAL_STATUS = {
    GoalStatus.COMPLETED: "GoalCompleted",
    GoalStatus.FAILED: "GoalFailed",
    GoalStatus.CANCELLED: "GoalCancelled",
}


def unrepairable_state_event_items(
    sessions: tuple[SessionSummaryView, ...],
    events: tuple[RuntimeEventView, ...],
) -> tuple[StateEventRepairSkipView, ...]:
    session_ids = frozenset(session.session_id for session in sessions)
    return tuple(
        StateEventRepairSkipView(
            event.session_id,
            event.event_id,
            f"orphan event cannot be repaired automatically: {event.event_id}",
        )
        for event in events
        if event.session_id not in session_ids
    )


def missing_terminal_state_events(
    sessions: tuple[SessionSummaryView, ...],
    events: tuple[RuntimeEventView, ...],
) -> tuple[RuntimeEvent, ...]:
    events_by_session: dict[str, set[str]] = {}
    for event in events:
        events_by_session.setdefault(event.session_id, set()).add(event.type)

    repairs: list[RuntimeEvent] = []
    for session in sessions:
        expected = _TERMINAL_EVENT_BY_GOAL_STATUS.get(session.goal_status)
        if expected is None:
            continue
        if expected in events_by_session.get(session.session_id, set()):
            continue
        repairs.append(_terminal_repair_event(session, expected))
    return tuple(repairs)


def state_event_repair_view(event: RuntimeEventView) -> StateEventRepairView:
    return StateEventRepairView(
        event,
        "synthesized missing terminal event from authoritative session state",
    )


def planned_state_event_repair_view(event: RuntimeEvent) -> StateEventRepairView:
    return StateEventRepairView(
        event_view(event),
        "would synthesize missing terminal event from authoritative session state",
    )


def _terminal_repair_event(
    session: SessionSummaryView,
    event_type: str,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type,
        session.session_id,
        session.goal_id,
        session.current_task_id,
        data={
            "repair_source": "state_event_consistency",
            "repair_reason": "missing_terminal_event",
            "goal_status": session.goal_status.value,
            "termination_reason": session.termination_reason,
            "error_code": None if session.error_code is None else session.error_code.value,
        },
    )
