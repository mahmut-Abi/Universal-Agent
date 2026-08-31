from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from universal_agent.core import (
    ActionId,
    AgentState,
    DomainIdentity,
    ErrorCode,
    EvaluationResult,
    EvaluationStatus,
    EventId,
    Goal,
    GoalId,
    GoalStatus,
    Observation,
    ObservationId,
    ObservationStatus,
    PendingAction,
    RuntimeEvent,
    SessionId,
    SuccessCriterion,
    Task,
    TaskId,
    TaskStatus,
    immutable_json,
)
from universal_agent.evidence import Evidence, EvidenceId
from universal_agent.persistence import (
    decode_runtime_event,
    decode_session_snapshot,
    encode_runtime_event,
    encode_session_snapshot,
)
from universal_agent.state import SessionSnapshot
from universal_agent.tasks import TaskGraphSnapshot, TaskNodeSnapshot


@pytest.mark.behavior
def test_session_snapshot_codec_preserves_rebuildable_runtime_state() -> None:
    observed_at = datetime(2026, 8, 22, 10, 30, tzinfo=UTC)
    session_id = SessionId("session-persist")
    root = Task(
        "Inspect workload",
        ("healthy",),
        TaskId("task-root"),
        TaskStatus.WAITING,
        observed_at,
    )
    diagnose = Task(
        "Diagnose workload",
        ("root_cause",),
        TaskId("task-diagnose"),
        TaskStatus.PENDING,
        observed_at,
    )
    goal = Goal(
        "Restore workload",
        (SuccessCriterion("healthy", True),),
        GoalId("goal-persist"),
        GoalStatus.WAITING,
        observed_at,
    )
    observation = Observation(
        ObservationId("observation-1"),
        ActionId("action-1"),
        root.id,
        "kubernetes_inspect_workload",
        ObservationStatus.SUCCEEDED,
        immutable_json({"healthy": False, "resource": "deployment/example"}),
        observed_at,
    )
    state = AgentState(
        session_id=session_id,
        goal=goal,
        current_task=root,
        iteration=3,
        satisfied_criteria={"healthy": False},
        observations=[observation],
        latest_evaluation=EvaluationResult(
            EvaluationStatus.INCOMPLETE,
            "workload remains unverified",
            "workload-health",
            immutable_json({"healthy": False}),
            task_completed=True,
            goal_completed=False,
        ),
        pending_action=PendingAction(
            ActionId("action-2"),
            "scale_workload",
            "kubernetes_scale_workload",
            "deployment/example",
            immutable_json({"name": "example", "namespace": "default", "replicas": 3}),
            "kubernetes",
            "0.2.0",
            "session-1:task-root:abc123",
            "abc123def456",
            2,
        ),
        tasks=[root, diagnose],
        recovery_attempts={"task-root:timeout:kubernetes-timeout-retry": 1},
        termination_reason="production workload scaling requires confirmation",
        error_code=ErrorCode.TIMEOUT,
    )
    evidence = Evidence(
        session_id,
        root.id,
        ActionId("action-1"),
        ObservationId("observation-1"),
        "deployment/example",
        "healthy",
        False,
        "kubernetes_inspect_workload",
        0.99,
        EvidenceId("evidence-1"),
        observed_at,
        "kubernetes",
        "0.2.0",
    )
    snapshot = SessionSnapshot(
        state,
        TaskGraphSnapshot(
            (
                TaskNodeSnapshot("root", root, ()),
                TaskNodeSnapshot("diagnose", diagnose, (root.id,)),
            ),
            root.id,
        ),
        (evidence,),
        "kubernetes",
        "0.2.0",
        (
            DomainIdentity("kubernetes", "0.2.0"),
            DomainIdentity("observability", "0.1.0"),
        ),
    )

    encoded = encode_session_snapshot(snapshot)
    restored = decode_session_snapshot(encoded)

    assert restored.domain_name == "kubernetes"
    assert restored.domain_version == "0.2.0"
    assert encoded["domains"] == [
        {"name": "kubernetes", "version": "0.2.0"},
        {"name": "observability", "version": "0.1.0"},
    ]
    assert restored.domains == (
        DomainIdentity("kubernetes", "0.2.0"),
        DomainIdentity("observability", "0.1.0"),
    )
    assert restored.state.session_id == session_id
    assert restored.state.current_task.id == root.id
    assert restored.state.current_task is restored.task_graph.nodes[0].task
    assert restored.state.pending_action is not None
    assert restored.state.pending_action.capability == "scale_workload"
    assert restored.state.latest_evaluation is not None
    assert restored.state.latest_evaluation.evaluator_name == "workload-health"
    assert restored.state.recovery_attempts == {"task-root:timeout:kubernetes-timeout-retry": 1}
    assert restored.task_graph.nodes[1].depends_on == (root.id,)
    encoded_evidence = encoded["evidence"]
    assert isinstance(encoded_evidence, list)
    encoded_evidence_item = encoded_evidence[0]
    assert isinstance(encoded_evidence_item, dict)
    assert encoded_evidence_item["domain_name"] == "kubernetes"
    assert encoded_evidence_item["domain_version"] == "0.2.0"
    assert restored.evidence[0].id == evidence.id
    assert restored.evidence[0].value is False
    assert restored.evidence[0].domain_name == "kubernetes"
    assert restored.evidence[0].domain_version == "0.2.0"


@pytest.mark.behavior
def test_session_snapshot_codec_defaults_legacy_pending_action_metadata() -> None:
    observed_at = datetime(2026, 8, 22, 10, 30, tzinfo=UTC)
    task = Task("Inspect", (), TaskId("task-legacy"), TaskStatus.WAITING, observed_at)
    state = AgentState(
        session_id=SessionId("session-legacy"),
        goal=Goal("Legacy", (), GoalId("goal-legacy"), GoalStatus.WAITING, observed_at),
        current_task=task,
        pending_action=PendingAction(
            ActionId("action-legacy"),
            "scale_workload",
            "kubernetes_scale_workload",
            "deployment/example",
            immutable_json({"name": "example"}),
            "kubernetes",
            "0.2.0",
            "session-legacy:task-legacy:abc123",
            "abc123",
            2,
        ),
        tasks=[task],
    )
    snapshot = SessionSnapshot(
        state,
        TaskGraphSnapshot((TaskNodeSnapshot("task-legacy", task),), task.id),
        (),
        "kubernetes",
        "0.2.0",
    )
    encoded = encode_session_snapshot(snapshot)
    payload_state = encoded["state"]
    assert isinstance(payload_state, dict)
    pending_action = payload_state["pending_action"]
    assert isinstance(pending_action, dict)
    del pending_action["idempotency_key"]
    del pending_action["parameters_hash"]
    del pending_action["attempt"]

    restored = decode_session_snapshot(encoded)

    assert restored.state.pending_action is not None
    assert restored.state.pending_action.idempotency_key == ""
    assert restored.state.pending_action.parameters_hash == ""
    assert restored.state.pending_action.attempt == 1


@pytest.mark.behavior
def test_session_snapshot_codec_defaults_legacy_evidence_domain_metadata() -> None:
    observed_at = datetime(2026, 8, 22, 10, 30, tzinfo=UTC)
    task = Task("Inspect", (), TaskId("task-legacy"), TaskStatus.WAITING, observed_at)
    evidence = Evidence(
        SessionId("session-legacy"),
        task.id,
        ActionId("action-legacy"),
        ObservationId("observation-legacy"),
        "deployment/example",
        "healthy",
        True,
        "legacy-source",
        0.9,
        EvidenceId("evidence-legacy"),
        observed_at,
        "kubernetes",
        "0.2.0",
    )
    snapshot = SessionSnapshot(
        AgentState(
            session_id=SessionId("session-legacy"),
            goal=Goal("Legacy", (), GoalId("goal-legacy"), GoalStatus.WAITING, observed_at),
            current_task=task,
            tasks=[task],
        ),
        TaskGraphSnapshot((TaskNodeSnapshot("task-legacy", task),), task.id),
        (evidence,),
        "kubernetes",
        "0.2.0",
    )
    encoded = encode_session_snapshot(snapshot)
    payload_evidence = encoded["evidence"]
    assert isinstance(payload_evidence, list)
    legacy_evidence = payload_evidence[0]
    assert isinstance(legacy_evidence, dict)
    del legacy_evidence["domain_name"]
    del legacy_evidence["domain_version"]

    restored = decode_session_snapshot(encoded)

    assert restored.evidence[0].domain_name == ""
    assert restored.evidence[0].domain_version == ""


@pytest.mark.contract
def test_session_snapshot_codec_accepts_legacy_single_domain_payload() -> None:
    snapshot = SessionSnapshot(
        AgentState(
            session_id=SessionId("session-legacy"),
            goal=Goal("Legacy", (), GoalId("goal-legacy")),
            current_task=Task("Inspect", (), TaskId("task-legacy")),
        ),
        TaskGraphSnapshot(
            (TaskNodeSnapshot("root", Task("Inspect", (), TaskId("task-legacy")), ()),),
            TaskId("task-legacy"),
        ),
        (),
        "kubernetes",
        "0.2.0",
    )
    encoded = encode_session_snapshot(snapshot)
    encoded.pop("domains")

    restored = decode_session_snapshot(encoded)

    assert restored.domains == (DomainIdentity("kubernetes", "0.2.0"),)


@pytest.mark.contract
def test_runtime_event_codec_preserves_json_safe_event_data() -> None:
    event = RuntimeEvent(
        type="DomainActivated",
        session_id=SessionId("session-events"),
        goal_id=GoalId("goal-events"),
        task_id=TaskId("task-events"),
        id=EventId("event-1"),
        action_id=None,
        data={
            "domains": ("kubernetes@0.2.0", "observability@0.1.0"),
            "emitted_at": datetime(2026, 8, 22, 10, 31, tzinfo=UTC),
        },
        occurred_at=datetime(2026, 8, 22, 10, 32, tzinfo=UTC),
    )

    restored = decode_runtime_event(encode_runtime_event(event))

    assert restored.id == event.id
    assert restored.session_id == event.session_id
    assert restored.action_id is None
    assert restored.data["domains"] == ["kubernetes@0.2.0", "observability@0.1.0"]
    assert restored.data["emitted_at"] == "2026-08-22T10:31:00+00:00"
    assert restored.occurred_at == event.occurred_at


@pytest.mark.behavior
def test_persistence_codec_accepts_z_suffix_datetime_payloads() -> None:
    observed_at = datetime(2026, 8, 22, 10, 30, tzinfo=UTC)
    session_id = SessionId("session-z")
    task = Task("Inspect", (), TaskId("task-z"), TaskStatus.WAITING, observed_at)
    observation = Observation(
        ObservationId("observation-z"),
        ActionId("action-z"),
        task.id,
        "probe",
        ObservationStatus.SUCCEEDED,
        immutable_json({"healthy": True}),
        observed_at,
    )
    snapshot = SessionSnapshot(
        AgentState(
            session_id=session_id,
            goal=Goal("Restore", (), GoalId("goal-z"), GoalStatus.WAITING, observed_at),
            current_task=task,
            observations=[observation],
            tasks=[task],
        ),
        TaskGraphSnapshot((TaskNodeSnapshot("root", task),), task.id),
        (
            Evidence(
                session_id,
                task.id,
                ActionId("action-z"),
                ObservationId("observation-z"),
                "deployment/example",
                "healthy",
                True,
                "probe",
                0.99,
                EvidenceId("evidence-z"),
                observed_at,
            ),
        ),
        "kubernetes",
        "0.2.0",
    )
    encoded = encode_session_snapshot(snapshot)
    state_payload = cast(dict[str, object], encoded["state"])
    goal_payload = cast(dict[str, object], state_payload["goal"])
    observations = cast(list[object], state_payload["observations"])
    observation_payload = cast(dict[str, object], observations[0])
    task_graph_payload = cast(dict[str, object], encoded["task_graph"])
    task_nodes = cast(list[object], task_graph_payload["nodes"])
    task_node = cast(dict[str, object], task_nodes[0])
    task_payload = cast(dict[str, object], task_node["task"])
    evidence_items = cast(list[object], encoded["evidence"])
    evidence_payload = cast(dict[str, object], evidence_items[0])
    goal_payload["created_at"] = "2026-08-22T10:30:00Z"
    task_payload["created_at"] = "2026-08-22T10:30:00Z"
    observation_payload["observed_at"] = "2026-08-22T10:30:00Z"
    evidence_payload["observed_at"] = "2026-08-22T10:30:00Z"

    restored = decode_session_snapshot(encoded)

    assert restored.state.goal.created_at.isoformat() == "2026-08-22T10:30:00+00:00"
    assert restored.state.current_task.created_at.isoformat() == "2026-08-22T10:30:00+00:00"
    assert restored.state.observations[0].observed_at.isoformat() == "2026-08-22T10:30:00+00:00"
    assert restored.evidence[0].observed_at.isoformat() == "2026-08-22T10:30:00+00:00"

    event_payload = encode_runtime_event(
        RuntimeEvent(
            type="EventWithZ",
            session_id=session_id,
            goal_id=GoalId("goal-z"),
            task_id=task.id,
            id=EventId("event-z"),
            action_id=None,
            data={},
            occurred_at=observed_at,
        )
    )
    event_payload["occurred_at"] = "2026-08-22T10:30:00Z"

    assert decode_runtime_event(event_payload).occurred_at.isoformat() == (
        "2026-08-22T10:30:00+00:00"
    )


@pytest.mark.behavior
def test_persistence_codec_rejects_invalid_persisted_types_without_coercion() -> None:
    observed_at = datetime(2026, 8, 22, 10, 30, tzinfo=UTC)
    task = Task("Inspect", (), TaskId("task-invalid"), TaskStatus.WAITING, observed_at)
    snapshot = SessionSnapshot(
        AgentState(
            session_id=SessionId("session-invalid"),
            goal=Goal("Invalid", (), GoalId("goal-invalid"), GoalStatus.WAITING, observed_at),
            current_task=task,
            tasks=[task],
        ),
        TaskGraphSnapshot((TaskNodeSnapshot("task-invalid", task),), task.id),
        (),
        "kubernetes",
        "0.2.0",
    )
    encoded = encode_session_snapshot(snapshot)
    state = encoded["state"]
    assert isinstance(state, dict)
    state["iteration"] = "1"

    with pytest.raises(ValueError, match=r"state\.iteration must be an integer"):
        decode_session_snapshot(encoded)

    encoded = encode_session_snapshot(snapshot)
    state = encoded["state"]
    assert isinstance(state, dict)
    state["error_code"] = "not_an_error_code"

    with pytest.raises(ValueError, match=r"state\.error_code must be one of"):
        decode_session_snapshot(encoded)

    encoded = encode_session_snapshot(snapshot)
    state = encoded["state"]
    assert isinstance(state, dict)
    goal_payload = state["goal"]
    assert isinstance(goal_payload, dict)
    goal_payload["status"] = "not_a_goal_status"

    with pytest.raises(ValueError, match=r"goal\.status must be one of"):
        decode_session_snapshot(encoded)

    event = RuntimeEvent(
        type="InvalidEvent",
        session_id=SessionId("session-invalid"),
        goal_id=GoalId("goal-invalid"),
        task_id=TaskId("task-invalid"),
        id=EventId("event-invalid"),
        action_id=None,
        data={},
        occurred_at=observed_at,
    )
    event_payload = encode_runtime_event(event)
    event_payload["data"] = []

    with pytest.raises(ValueError, match="data must be an object"):
        decode_runtime_event(event_payload)
