from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DomainLoader,
    Goal,
    InMemoryEventSink,
    InMemoryStateStore,
    RuntimeBuilder,
    ScriptedModelAdapter,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core import (
    CapabilityCategory,
    CapabilityDefinition,
    DomainManifest,
    DomainMetadata,
    ExecutionStatus,
    JsonMapping,
    PolicyEffect,
    ToolDefinition,
)
from universal_agent.domain import DomainRuntime
from universal_agent.evaluation import CriteriaEvaluator, Evaluator
from universal_agent.evidence import Evidence, EvidenceContext, EvidenceExtractor
from universal_agent.policy import PolicyRule
from universal_agent.tasks import TaskExpander, TaskExpansionContext, TaskSpec
from universal_agent.tools import Tool
from universal_agent.world import FactWorldUpdater, WorldUpdater


class StaticTool:
    def __init__(self, name: str, capability: str, output: JsonMapping) -> None:
        self.definition = ToolDefinition(name, name, (capability,))
        self._output = output

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return self._output


class ObservationEvidenceExtractor:
    """Turns each observation payload key into a typed Evidence fact."""

    name = "observation-evidence"

    def extract(self, context: EvidenceContext) -> tuple[Evidence, ...]:
        observation = context.observation
        payload = observation.data if isinstance(observation.data, Mapping) else {}
        facts: list[Evidence] = [
            Evidence(
                context.session_id,
                context.task.id,
                observation.action_id,
                observation.id,
                "deployment/example",
                "observed",
                True,
                "inspect",
            )
        ]
        for key, value in payload.items():
            facts.append(
                Evidence(
                    context.session_id,
                    context.task.id,
                    observation.action_id,
                    observation.id,
                    "deployment/example",
                    key,
                    value,
                    "inspect",
                )
            )
        return tuple(facts)


class SymptomExpander:
    """Proposes a remediation sub-task only when a symptom was observed.

    This is the runtime-level proof of AGENTS.md 4.10: tasks are discovered as
    information becomes available, not pre-planned before execution.
    """

    name = "symptom-expander"
    capability_names = ("inspect",)

    def expand(self, context: TaskExpansionContext) -> tuple[TaskSpec, ...]:
        if any(fact.claim == "symptom" for fact in context.evidence):
            return (TaskSpec("remediate", "Remediate the crashloop symptom", ("fixed",)),)
        return ()


class ExpandDomain:
    def __init__(self, inspect_output: JsonMapping, remediate_output: JsonMapping) -> None:
        self._evaluator = CriteriaEvaluator()
        self._inspect = StaticTool("inspect_tool", "inspect", inspect_output)
        self._remediate = StaticTool("remediate_tool", "remediate", remediate_output)
        self.manifest = DomainManifest(
            "agent.nantian.dev/v1alpha1",
            "Domain",
            DomainMetadata("expand", "1.0.0", "dynamic task expansion demo"),
            ("Thing",),
            ("inspect", "remediate"),
            (self._evaluator.name,),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition("inspect", "Inspect", CapabilityCategory.OBSERVATION),
            CapabilityDefinition("remediate", "Remediate", CapabilityCategory.MUTATION),
        )

    def tools(self) -> tuple[Tool, ...]:
        return (self._inspect, self._remediate)

    def policies(self) -> tuple[PolicyRule, ...]:
        return (
            PolicyRule(
                "allow-remediate",
                PolicyEffect.ALLOW,
                "remediation mutations are allowed in this scenario",
                capabilities=("remediate",),
            ),
        )

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (self._evaluator,)

    def context_providers(self) -> tuple[object, ...]:
        return ()

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
        return (ObservationEvidenceExtractor(),)

    def world_updaters(self) -> tuple[WorldUpdater, ...]:
        return (FactWorldUpdater(),)

    def task_expanders(self) -> tuple[TaskExpander, ...]:
        return (SymptomExpander(),)

    def recovery_rules(self) -> tuple[object, ...]:
        return ()

    def memories(self) -> tuple[object, ...]:
        return ()


def _build(domain: object, decisions: list[Decision]) -> tuple[AgentRuntime, InMemoryStateStore]:
    active = DomainLoader().load(cast(DomainRuntime, domain))
    components = RuntimeBuilder().build(active)
    model = ScriptedModelAdapter(decisions)
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=model,
        state_store=store,
        components=components,
        event_sink=events,
    )
    return runtime, store


def _execute(capability: str, expected: str) -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        reason=f"Act on {capability}",
        capability=capability,
        target="deployment/example",
        arguments=immutable_json({}),
        expected_observations=(expected,),
    )


def _finish() -> Decision:
    return Decision(DecisionType.FINISH, reason="Goal criteria satisfied")


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_loop_expands_new_task_when_observation_reveals_symptom() -> None:
    domain = ExpandDomain(
        inspect_output={"symptom": "crashloop"},
        remediate_output={"fixed": True},
    )
    runtime, store = _build(
        domain,
        [_execute("inspect", "observed"), _execute("remediate", "fixed"), _finish()],
    )
    goal = Goal(
        "Fix crashloop deployment",
        (SuccessCriterion("observed", True), SuccessCriterion("fixed", True)),
    )
    task = Task("Inspect workload", ("observed",))

    result = await runtime.run(goal, task)

    assert result.status is ExecutionStatus.COMPLETED
    snapshot = await store.load_session(result.session_id)
    keys = {node.key for node in snapshot.task_graph.nodes}
    assert "remediate" in keys
    assert len(snapshot.task_graph.nodes) >= 2


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_loop_does_not_expand_without_supporting_evidence() -> None:
    domain = ExpandDomain(
        inspect_output={"other": 1},
        remediate_output={"fixed": True},
    )
    runtime, store = _build(domain, [_execute("inspect", "observed"), _finish()])
    goal = Goal("Inspect only", (SuccessCriterion("observed", True),))
    task = Task("Inspect workload", ("observed",))

    result = await runtime.run(goal, task)

    assert result.status is ExecutionStatus.COMPLETED
    snapshot = await store.load_session(result.session_id)
    keys = {node.key for node in snapshot.task_graph.nodes}
    assert keys == {"root"}
