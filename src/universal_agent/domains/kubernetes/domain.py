from __future__ import annotations

from typing import Protocol

from universal_agent.context import DomainContextProvider
from universal_agent.core import (
    AgentState,
    CapabilityCategory,
    CapabilityDefinition,
    ContextFragment,
    DomainManifest,
    DomainMetadata,
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    JsonMapping,
    ObservationStatus,
    PolicyEffect,
    RiskLevel,
    ToolDefinition,
    immutable_json,
)
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import Evidence, EvidenceContext, EvidenceExtractor
from universal_agent.policy import Policy, PolicyRule
from universal_agent.recovery import (
    FailureCategory,
    RecoveryRule,
    RecoveryStrategy,
)
from universal_agent.tasks import TaskExpander, TaskExpansionContext, TaskSpec
from universal_agent.world import FactWorldUpdater, WorldUpdater


class KubernetesBackend(Protocol):
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping: ...


class KubernetesInspectTool:
    def __init__(self, capability: str, backend: KubernetesBackend) -> None:
        self.definition = ToolDefinition(
            name=f"kubernetes_{capability}",
            description=f"Kubernetes implementation for {capability}",
            capabilities=(capability,),
            required_arguments=("name",),
        )
        self._capability = capability
        self._backend = backend

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return await self._backend.inspect(self._capability, arguments)


class KubernetesContextProvider:
    name = "kubernetes-context"

    def provide(self, state: AgentState) -> tuple[ContextFragment, ...]:
        return (
            ContextFragment(
                "kubernetes.scope",
                "Operate on Kubernetes resources using read-only inspection capabilities.",
                10,
            ),
        )


class KubernetesEvidenceExtractor:
    name = "kubernetes-evidence"

    def extract(self, context: EvidenceContext) -> tuple[Evidence, ...]:
        if context.observation.status is not ObservationStatus.SUCCEEDED:
            return ()
        subject = str(context.observation.data.get("resource") or context.observation.source)
        return tuple(
            Evidence(
                context.session_id,
                context.task.id,
                context.observation.action_id,
                context.observation.id,
                subject,
                key,
                value,
                context.observation.source,
                0.99,
                observed_at=context.observation.observed_at,
            )
            for key, value in context.observation.data.items()
        )


class KubernetesDiagnosticExpander:
    name = "kubernetes-diagnostics"
    capability_names = ("inspect_pod", "inspect_events")

    def expand(self, context: TaskExpansionContext) -> tuple[TaskSpec, ...]:
        healthy = context.world.value_for("healthy")
        resource = context.world.value_for("resource")
        diagnosed = context.world.value_for("root_cause")
        if healthy is False and resource is not None and diagnosed is None:
            return (
                TaskSpec(
                    "diagnose-unhealthy-workload",
                    "Diagnose unhealthy Kubernetes workload",
                    ("root_cause",),
                    (context.task.id,),
                ),
            )
        return ()


class WorkloadHealthEvaluator:
    name = "workload-health"

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        expected = {
            criterion.key: criterion.expected for criterion in context.goal.success_criteria
        }
        relevant = set(expected) | set(context.task.required_criteria)
        matched = {
            key: value
            for key, value in context.satisfied_criteria.items()
            if key in relevant and (key not in expected or value == expected[key])
        }
        task_complete = set(context.task.required_criteria).issubset(matched)
        goal_complete = set(expected).issubset(matched)
        complete = task_complete and goal_complete
        return EvaluationResult(
            EvaluationStatus.COMPLETED if complete else EvaluationStatus.INCOMPLETE,
            "workload health criteria satisfied" if complete else "workload remains unverified",
            self.name,
            immutable_json(matched),
            task_complete,
            goal_complete,
        )


class KubernetesDomain:
    _capability_names = (
        "inspect_cluster",
        "inspect_workload",
        "inspect_pod",
        "inspect_logs",
        "inspect_events",
    )

    def __init__(self, backend: KubernetesBackend) -> None:
        self._backend = backend

    @property
    def manifest(self) -> DomainManifest:
        return DomainManifest(
            api_version="agent.nantian.dev/v1alpha1",
            kind="Domain",
            metadata=DomainMetadata(
                "kubernetes",
                "0.1.0",
                "Read-only Kubernetes domain skeleton",
            ),
            ontology=("Cluster", "Node", "Namespace", "Pod", "Deployment", "Service"),
            capability_names=self._capability_names,
            evaluator_names=(WorkloadHealthEvaluator.name,),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return tuple(
            CapabilityDefinition(
                name,
                name.replace("_", " ").capitalize(),
                CapabilityCategory.OBSERVATION,
                RiskLevel.LOW,
            )
            for name in self._capability_names
        )

    def tools(self) -> tuple[KubernetesInspectTool, ...]:
        return tuple(
            KubernetesInspectTool(capability, self._backend)
            for capability in self._capability_names
        )

    def policies(self) -> tuple[Policy, ...]:
        return (
            PolicyRule(
                "kubernetes-read-only",
                PolicyEffect.ALLOW,
                "read-only Kubernetes inspection allowed",
                categories=(CapabilityCategory.OBSERVATION,),
            ),
        )

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (WorkloadHealthEvaluator(),)

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return (KubernetesContextProvider(),)

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
        return (KubernetesEvidenceExtractor(),)

    def world_updaters(self) -> tuple[WorldUpdater, ...]:
        return (FactWorldUpdater(),)

    def task_expanders(self) -> tuple[TaskExpander, ...]:
        return (KubernetesDiagnosticExpander(),)

    def recovery_rules(self) -> tuple[RecoveryRule, ...]:
        return (
            RecoveryRule(
                "kubernetes-timeout-retry",
                (FailureCategory.TIMEOUT,),
                RecoveryStrategy.RETRY_ACTION,
                max_attempts=2,
                priority=10,
            ),
        )
