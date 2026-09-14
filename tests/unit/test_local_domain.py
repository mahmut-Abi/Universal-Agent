"""Behavior tests for the Local (Golden Path) Domain.

The local domain is the default profile's domain: every `ua run` on a fresh
install executes through it. These tests pin its read-only contract — exactly
one observation capability, no mutations, and typed evidence/world/context
projection — at the component level.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from universal_agent.core import (
    ActionId,
    AgentState,
    CapabilityCategory,
    EvaluationContext,
    EvaluationStatus,
    Goal,
    GoalId,
    JsonValue,
    Observation,
    ObservationStatus,
    PolicyContext,
    PolicyEffect,
    RiskLevel,
    SessionId,
    SuccessCriterion,
    Task,
    TaskId,
    immutable_json,
    new_action_id,
    new_observation_id,
)
from universal_agent.domains.local.domain import (
    LOCAL_DOMAIN_NAME,
    LOCAL_POLICY_NAME,
    WORKSPACE_CAPABILITY,
    LocalDomain,
    WorkspaceContextProvider,
    WorkspaceEvidenceExtractor,
    WorkspaceHealthEvaluator,
    WorkspaceInspectionTool,
    WorkspaceTaskExpander,
    WorkspaceWorldUpdater,
    inspect_workspace,
    local_identity,
)
from universal_agent.evidence import Evidence, EvidenceContext
from universal_agent.policy import PolicyEngine
from universal_agent.tasks import TaskExpansionContext
from universal_agent.world import InMemoryWorldModel


def _succeeded_observation(task: Task, data: dict[str, JsonValue]) -> Observation:
    return Observation(
        new_observation_id(),
        new_action_id(),
        task.id,
        WORKSPACE_CAPABILITY,
        ObservationStatus.SUCCEEDED,
        immutable_json(data),
        datetime(2026, 1, 1, tzinfo=UTC),
    )


def _workspace_data(tmp_path: Path) -> dict[str, JsonValue]:
    summary = inspect_workspace(tmp_path)
    assert summary["readable"] is True
    return dict(summary)


def _policy_context(capability_name: str, category: CapabilityCategory) -> PolicyContext:
    from universal_agent.core import CapabilityDefinition, ToolDefinition

    capability = CapabilityDefinition(
        capability_name,
        f"{capability_name} capability",
        category,
        RiskLevel.LOW,
    )
    tool = ToolDefinition(
        "workspace-tool", "workspace tool", (capability_name,), risk=RiskLevel.LOW
    )
    return PolicyContext(
        SessionId("session-local"),
        GoalId("goal-local"),
        TaskId("task-local"),
        ActionId("action-local"),
        capability,
        tool,
        None,
        immutable_json(),
    )


@pytest.mark.behavior
def test_inspect_workspace_counts_files_and_detects_project_markers(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()

    summary = inspect_workspace(tmp_path)

    assert summary["healthy"] is True
    assert summary["resource"] == "workspace"
    assert summary["file_count"] == 2
    assert summary["directory_count"] == 1
    assert summary["project_markers"] == ["pyproject.toml", "README.md"]


@pytest.mark.behavior
def test_inspect_workspace_reports_unreadable_root_instead_of_raising(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    summary = inspect_workspace(missing)

    assert summary["healthy"] is False
    assert summary["readable"] is False
    assert "error" in summary


@pytest.mark.behavior
@pytest.mark.asyncio
async def test_workspace_tool_execution_is_read_only_summary(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("", encoding="utf-8")
    tool = WorkspaceInspectionTool(tmp_path)

    result = await tool.execute(immutable_json())

    assert result["root"] == str(tmp_path)
    assert result["file_count"] == 1
    assert tool.definition.side_effect.value == "none"
    assert tool.definition.capabilities == (WORKSPACE_CAPABILITY,)


@pytest.mark.behavior
def test_workspace_evaluator_completes_when_goal_and_task_criteria_match() -> None:
    goal = Goal("Inspect", (SuccessCriterion("healthy", True),))
    task = Task("Inspect workspace", ("healthy",))
    observation = _succeeded_observation(task, {"healthy": True})

    result = WorkspaceHealthEvaluator().evaluate(
        EvaluationContext(goal, task, observation, immutable_json({"healthy": True}))
    )

    assert result.status is EvaluationStatus.COMPLETED
    assert result.task_completed is True
    assert result.goal_completed is True


@pytest.mark.behavior
def test_workspace_evaluator_stays_incomplete_when_criterion_missing() -> None:
    goal = Goal("Inspect", (SuccessCriterion("healthy", True),))
    task = Task("Inspect workspace", ("healthy",))
    observation = _succeeded_observation(task, {"healthy": True})

    result = WorkspaceHealthEvaluator().evaluate(
        EvaluationContext(goal, task, observation, immutable_json())
    )

    assert result.status is EvaluationStatus.INCOMPLETE
    assert result.task_completed is False
    assert result.goal_completed is False


@pytest.mark.behavior
def test_workspace_evaluator_rejects_mismatched_expected_value() -> None:
    goal = Goal("Inspect", (SuccessCriterion("healthy", True),))
    task = Task("Inspect workspace", ("healthy",))
    observation = _succeeded_observation(task, {"healthy": False})

    result = WorkspaceHealthEvaluator().evaluate(
        EvaluationContext(goal, task, observation, immutable_json({"healthy": False}))
    )

    assert result.status is EvaluationStatus.INCOMPLETE


@pytest.mark.behavior
def test_workspace_evidence_extractor_projects_claims_and_markers(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("", encoding="utf-8")
    task = Task("Inspect workspace", ())
    observation = _succeeded_observation(task, _workspace_data(tmp_path))

    extracted = WorkspaceEvidenceExtractor().extract(
        EvidenceContext(SessionId("session-local"), task, observation)
    )

    claims = {item.claim: item.value for item in extracted}
    assert claims["healthy"] is True
    assert claims["readable"] is True
    assert claims["file_count"] == 1
    assert claims["directory_count"] == 0
    assert claims["project_marker"] == "README.md"
    assert {item.subject for item in extracted} == {"workspace"}
    assert {item.source for item in extracted} == {"workspace-evidence"}


@pytest.mark.behavior
def test_workspace_evidence_extractor_ignores_failed_observations() -> None:
    task = Task("Inspect workspace", ())
    observation = Observation(
        new_observation_id(),
        new_action_id(),
        task.id,
        WORKSPACE_CAPABILITY,
        ObservationStatus.FAILED,
        immutable_json({"healthy": False}),
        datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert (
        WorkspaceEvidenceExtractor().extract(
            EvidenceContext(SessionId("session-local"), task, observation)
        )
        == ()
    )


@pytest.mark.behavior
def test_workspace_task_expander_never_adds_tasks() -> None:
    task = Task("Inspect workspace", ())
    state = AgentState(
        session_id=SessionId("session-local"),
        goal=Goal("Inspect", ()),
        current_task=task,
    )

    expanded = WorkspaceTaskExpander().expand(
        TaskExpansionContext(
            state.current_task, (), InMemoryWorldModel().snapshot(state.session_id)
        )
    )

    assert expanded == ()


@pytest.mark.behavior
def test_local_domain_policy_allows_inspection_and_denies_mutations() -> None:
    engine = PolicyEngine(LocalDomain().policies())

    allowed = engine.check(_policy_context(WORKSPACE_CAPABILITY, CapabilityCategory.OBSERVATION))
    denied = engine.check(_policy_context("delete_workload", CapabilityCategory.MUTATION))

    assert allowed.effect is PolicyEffect.ALLOW
    assert allowed.policy_name == LOCAL_POLICY_NAME
    assert denied.effect is PolicyEffect.DENY
    assert denied.policy_name == f"{LOCAL_POLICY_NAME}-mutations-denied"


@pytest.mark.behavior
def test_local_domain_exposes_single_read_only_capability_surface() -> None:
    domain = LocalDomain()

    assert domain.identity == local_identity()
    assert domain.identity.name == LOCAL_DOMAIN_NAME
    assert [capability.name for capability in domain.capabilities()] == [WORKSPACE_CAPABILITY]
    assert domain.capabilities()[0].category is CapabilityCategory.OBSERVATION
    assert [tool.definition.name for tool in domain.tools()] == ["local_inspect_workspace"]
    assert domain.manifest.capability_names == (WORKSPACE_CAPABILITY,)
    assert ("Workspace", "ProjectMarker") == domain.manifest.ontology
    assert (WorkspaceHealthEvaluator.name,) == domain.manifest.evaluator_names


@pytest.mark.behavior
def test_local_domain_wires_every_runtime_component_seam() -> None:
    domain = LocalDomain()
    state = AgentState(
        session_id=SessionId("session-local"),
        goal=Goal("Inspect", ()),
        current_task=Task("Inspect workspace", ()),
    )

    assert len(domain.evaluators()) == 1
    assert [
        fragment.key
        for provider in domain.context_providers()
        for fragment in provider.provide(state)
    ] == ["workspace-context"]
    assert len(domain.evidence_extractors()) == 1
    assert len(domain.world_updaters()) == 1
    assert len(domain.task_expanders()) == 1


@pytest.mark.behavior
def test_workspace_world_updater_mirrors_known_claims_only() -> None:
    task = Task("Inspect workspace", ())
    model = InMemoryWorldModel()
    session_id = SessionId("session-local")
    updater = WorkspaceWorldUpdater()

    def evidence_for(claim: str, value: JsonValue) -> Evidence:
        return Evidence(
            session_id=session_id,
            task_id=task.id,
            action_id=new_action_id(),
            observation_id=new_observation_id(),
            subject="workspace",
            claim=claim,
            value=value,
            source="workspace-evidence",
        )

    assert updater.apply(model, evidence_for("file_count", 3)) is True
    assert updater.apply(model, evidence_for("healthy", True)) is True
    assert updater.apply(model, evidence_for("not_a_workspace_claim", "x")) is False
    assert updater.apply(model, evidence_for("file_count", 3)) is True  # distinct fact ids


@pytest.mark.behavior
def test_workspace_context_provider_summarizes_current_workspace() -> None:
    state = AgentState(
        session_id=SessionId("session-local"),
        goal=Goal("Inspect", ()),
        current_task=Task("Inspect workspace", ()),
    )

    fragments = WorkspaceContextProvider().provide(state)

    assert len(fragments) == 1
    assert fragments[0].key == "workspace-context"
    assert "files" in fragments[0].content
