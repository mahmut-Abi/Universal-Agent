from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from universal_agent.core import (
    Decision,
    DecisionContext,
    DecisionType,
    ErrorCode,
    ExecutionStatus,
    Goal,
    GoalId,
    RuntimeEvent,
    SessionId,
    SuccessCriterion,
    Task,
    TaskId,
    immutable_json,
)
from universal_agent.persistence.codec import decode_session_snapshot
from universal_agent.runtime.agent import AgentRuntime
from universal_agent.state import SessionSnapshot
from universal_agent.state.event_store import EventStore


class ReplayModelAdapter(Protocol):
    """Model adapter that replays recorded decisions in order."""

    async def decide(self, context: DecisionContext) -> Decision: ...


@dataclass(frozen=True, slots=True)
class RecordedDecision:
    """A decision recorded during the original execution."""

    decision: Decision
    context_snapshot: DecisionContext


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """Result of a replay execution."""

    session_id: SessionId
    goal_id: GoalId
    original_status: ExecutionStatus
    replay_status: ExecutionStatus
    original_error_code: ErrorCode | None
    replay_error_code: ErrorCode | None
    decisions_replayed: int
    decisions_matched: int
    diverged_at: int | None
    events: tuple[str, ...]


class RuntimeReplayEngine:
    """Replays an execution from its event journal.

    Loads a session's events, extracts recorded decisions,
    and re-runs the loop with a ReplayModelAdapter that returns
    the recorded decisions in order.
    """

    def __init__(
        self,
        runtime: AgentRuntime,
        event_store: EventStore,
    ) -> None:
        self._runtime = runtime
        self._event_store = event_store

    async def replay(self, session_id: SessionId) -> ReplayResult:
        """Replay a session from its event journal."""
        events = self._event_store.events_for(session_id)
        if not events:
            raise ValueError(f"no events found for session {session_id}")

        # Extract recorded decisions from DecisionGenerated events
        recorded_decisions = self._extract_decisions(events)

        if not recorded_decisions:
            # No decisions to replay - just verify terminal status matches
            original_events = self._event_store.events_for(session_id)
            goal_created = next((e for e in original_events if e.type == "GoalCreated"), None)
            return ReplayResult(
                session_id=session_id,
                goal_id=(GoalId("unknown") if goal_created is None else goal_created.goal_id),
                original_status=self._extract_terminal_status(original_events),
                replay_status=self._extract_terminal_status(original_events),
                original_error_code=self._extract_error_code(original_events),
                replay_error_code=self._extract_error_code(original_events),
                decisions_replayed=0,
                decisions_matched=0,
                diverged_at=None,
                events=tuple(e.type for e in original_events),
            )

        # Run replay with recorded decisions
        return await self._run_replay(session_id, recorded_decisions)

    def _extract_decisions(
        self,
        events: tuple[RuntimeEvent, ...],
    ) -> tuple[RecordedDecision, ...]:
        """Extract DecisionGenerated events as replayable decisions."""
        recorded: list[RecordedDecision] = []
        for event in events:
            if event.type == "DecisionGenerated":
                try:
                    decision_type = event.data.get("decision_type", DecisionType.EXECUTE.value)
                    reason = event.data.get("reason", "")
                    capability = event.data.get("capability")
                    target = event.data.get("target")
                    raw_arguments = event.data.get("arguments", {})
                    raw_expected = event.data.get("expected_observations", ())
                    decision = Decision(
                        type=DecisionType(decision_type),
                        reason=reason if isinstance(reason, str) else "",
                        capability=capability if isinstance(capability, str) else None,
                        target=target if isinstance(target, str) else None,
                        arguments=(
                            immutable_json(raw_arguments)
                            if isinstance(raw_arguments, Mapping)
                            else immutable_json()
                        ),
                        expected_observations=(
                            tuple(item for item in raw_expected if isinstance(item, str))
                            if isinstance(raw_expected, (list, tuple))
                            else ()
                        ),
                        message=(
                            event.data.get("message")
                            if isinstance(event.data.get("message"), str)
                            else None
                        ),
                    )
                    recorded.append(
                        RecordedDecision(
                            decision=decision,
                            context_snapshot=DecisionContext(
                                session_id=event.session_id,
                                goal_id=event.goal_id,
                                goal_description="",
                                task_id=event.task_id,
                                task_description="",
                                iteration=0,
                                satisfied_criteria=immutable_json({}),
                                latest_observation=None,
                                capabilities=(),
                                goal_success_criteria=(),
                                current_task_required_criteria=(),
                                domain_context=(),
                                world_context=(),
                                evidence_context=(),
                                task_context=(),
                                memory_context=(),
                                policy_summary=(),
                            ),
                        )
                    )
                except ValueError:
                    pass
        return tuple(recorded)

    async def _run_replay(
        self,
        session_id: SessionId,
        recorded_decisions: tuple[RecordedDecision, ...],
    ) -> ReplayResult:
        """Run the replay loop with recorded decisions."""
        adapter = _SequentialReplayAdapter(recorded_decisions)

        original_model = self._runtime._model
        self._runtime._model = adapter

        try:
            # Find the original goal and task from events
            events = self._event_store.events_for(session_id)
            goal_created = next((e for e in events if e.type == "GoalCreated"), None)
            task_created = next((e for e in events if e.type == "TaskCreated"), None)

            if not goal_created:
                raise ValueError("no GoalCreated event found")

            snapshot = self._latest_snapshot(events)
            goal_description = goal_created.data.get("description")
            if not isinstance(goal_description, str):
                goal_description = "" if snapshot is None else snapshot.state.goal.description
            criteria = _success_criteria(goal_created.data.get("success_criteria"))
            if not criteria and snapshot is not None:
                criteria = snapshot.state.goal.success_criteria
            goal = Goal(goal_description, criteria, id=goal_created.goal_id)

            task_description = "Replay task"
            task_criteria: tuple[str, ...] = ()
            task_id = TaskId("replay-task")
            if task_created is not None:
                candidate_description = task_created.data.get("description")
                if isinstance(candidate_description, str):
                    task_description = candidate_description
                candidate_criteria = task_created.data.get("required_criteria")
                if isinstance(candidate_criteria, (list, tuple)):
                    task_criteria = tuple(
                        item for item in candidate_criteria if isinstance(item, str)
                    )
                task_id = task_created.task_id
            elif snapshot is not None:
                task_description = snapshot.state.current_task.description
                task_criteria = snapshot.state.current_task.required_criteria
                task_id = snapshot.state.current_task.id
            task = Task(
                task_description,
                task_criteria,
                id=task_id,
            )

            # Run with replay adapter
            result = await self._runtime.run(goal, task)

            original_events = self._event_store.events_for(session_id)
            return ReplayResult(
                session_id=session_id,
                goal_id=goal.id,
                original_status=self._extract_terminal_status(original_events),
                replay_status=result.status,
                original_error_code=self._extract_error_code(original_events),
                replay_error_code=result.error_code,
                decisions_replayed=len(recorded_decisions),
                decisions_matched=adapter.decisions_used,
                diverged_at=None,
                events=tuple(e.type for e in original_events),
            )
        finally:
            self._runtime._model = original_model

    @staticmethod
    def _latest_snapshot(events: tuple[RuntimeEvent, ...]) -> SessionSnapshot | None:
        for event in reversed(events):
            if event.type != "SessionStateCommitted":
                continue
            payload = event.data.get("snapshot")
            if not isinstance(payload, Mapping):
                continue
            try:
                return decode_session_snapshot(payload)
            except Exception:
                continue
        return None

    def _extract_terminal_status(self, events: tuple[RuntimeEvent, ...]) -> ExecutionStatus:
        for event in reversed(events):
            if event.type == "GoalCompleted":
                return ExecutionStatus("completed")
            if event.type == "GoalFailed":
                return ExecutionStatus("failed")
            if event.type == "GoalCancelled":
                return ExecutionStatus("cancelled")
            if event.type in {"GoalWaiting", "ConfirmationRequired"}:
                return ExecutionStatus("waiting")
        # No terminal event found - session is still in progress
        return ExecutionStatus("waiting")

    def _extract_error_code(self, events: tuple[RuntimeEvent, ...]) -> ErrorCode | None:
        for event in reversed(events):
            if event.type in {"GoalFailed", "GoalCancelled"}:
                code = event.data.get("error_code")
                if code:
                    try:
                        return ErrorCode(code)
                    except ValueError:
                        return None
        return None


def _success_criteria(value: object) -> tuple[SuccessCriterion, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    criteria: list[SuccessCriterion] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        key = item.get("key")
        if isinstance(key, str) and key:
            criteria.append(SuccessCriterion(key, item.get("expected")))
    return tuple(criteria)


class _SequentialReplayAdapter:
    """Model adapter that returns recorded decisions sequentially."""

    def __init__(self, recorded_decisions: tuple[RecordedDecision, ...]) -> None:
        self._decisions = list(recorded_decisions)
        self.decisions_used = 0

    async def decide(self, context: DecisionContext) -> Decision:
        if self.decisions_used >= len(self._decisions):
            return Decision(DecisionType.FINISH, "replay exhausted recorded decisions")

        recorded = self._decisions[self.decisions_used]
        self.decisions_used += 1
        return recorded.decision


# Public API function
async def replay_session(
    runtime: AgentRuntime,
    event_store: EventStore,
    session_id: SessionId,
) -> ReplayResult:
    """Convenience function to replay a session."""
    engine = RuntimeReplayEngine(runtime, event_store)
    return await engine.replay(session_id)
