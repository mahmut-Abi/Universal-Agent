from __future__ import annotations

import pytest

from universal_agent.core import (
    CapabilityCategory,
    CapabilityDefinition,
    DomainManifest,
    DomainMetadata,
    EvaluationContext,
    Goal,
    ObservationStatus,
    SuccessCriterion,
    Task,
    ToolCall,
    ToolDefinition,
    ToolResult,
    immutable_json,
    new_action_id,
)
from universal_agent.domain import DomainLoader, DomainValidationError
from universal_agent.evaluation import CriteriaEvaluator
from universal_agent.observation import ObservationFactory


class TestTool:
    definition = ToolDefinition("inspect", "Inspect", ("inspect",))

    async def execute(self, arguments):  # type: ignore[no-untyped-def]
        return immutable_json()


class TestDomain:
    manifest = DomainManifest(
        "agent.nantian.dev/v1alpha1",
        "Domain",
        DomainMetadata("test", "1.0.0", "Test"),
        ("Thing",),
        ("inspect",),
        ("criteria",),
    )

    def capabilities(self):  # type: ignore[no-untyped-def]
        return (CapabilityDefinition("inspect", "Inspect", CapabilityCategory.OBSERVATION),)

    def tools(self):  # type: ignore[no-untyped-def]
        return (TestTool(),)

    def policies(self):  # type: ignore[no-untyped-def]
        return ()

    def evaluators(self):  # type: ignore[no-untyped-def]
        return (CriteriaEvaluator(),)

    def context_providers(self):  # type: ignore[no-untyped-def]
        return ()

    def evidence_extractors(self):  # type: ignore[no-untyped-def]
        return ()

    def world_updaters(self):  # type: ignore[no-untyped-def]
        return ()

    def task_expanders(self):  # type: ignore[no-untyped-def]
        return ()

    def recovery_rules(self):  # type: ignore[no-untyped-def]
        return ()


def test_domain_loader_activates_structured_domain() -> None:
    active = DomainLoader().load(TestDomain())
    assert active.manifest.metadata.name == "test"
    assert active.capabilities[0].name == "inspect"


def test_domain_loader_rejects_invalid_capability_reference() -> None:
    domain = TestDomain()
    domain.manifest = DomainManifest(
        domain.manifest.api_version,
        domain.manifest.kind,
        domain.manifest.metadata,
        domain.manifest.ontology,
        ("missing",),
        domain.manifest.evaluator_names,
    )
    with pytest.raises(DomainValidationError, match="capability references"):
        DomainLoader().load(domain)


def test_criteria_evaluator_requires_matching_observation_state() -> None:
    goal = Goal("Verify", (SuccessCriterion("healthy", True),))
    task = Task("Inspect", ("healthy",))
    observation = ObservationFactory().from_tool_result(
        task_id=task.id,
        call=ToolCall(new_action_id(), "inspect", "inspect", immutable_json()),
        result=ToolResult(ObservationStatus.SUCCEEDED, immutable_json({"healthy": False})),
    )
    result = CriteriaEvaluator().evaluate(
        EvaluationContext(goal, task, observation, immutable_json({"healthy": False}))
    )
    assert result.status.value == "incomplete"
