from __future__ import annotations

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
from universal_agent.domains.kubernetes.backend import KubernetesBackend, KubernetesMutationBackend
from universal_agent.domains.kubernetes.policy import KubernetesScalePolicy
from universal_agent.domains.kubernetes.tools import KubernetesScaleTool
from universal_agent.domains.kubernetes.workflow import KubernetesRemediationExpander
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import Evidence, EvidenceContext, EvidenceExtractor
from universal_agent.memory import MemoryKind, MemoryRecord
from universal_agent.policy import Policy, PolicyRule
from universal_agent.recovery import (
    FailureCategory,
    RecoveryRule,
    RecoveryStrategy,
)
from universal_agent.tasks import TaskExpander, TaskExpansionContext, TaskSpec
from universal_agent.tools import Tool
from universal_agent.world import FactWorldUpdater, WorldUpdater


class KubernetesInspectTool:
    def __init__(self, capability: str, backend: KubernetesBackend) -> None:
        self.definition = ToolDefinition(
            name=f"kubernetes_{capability}",
            description=f"Kubernetes implementation for {capability}",
            capabilities=(capability,),
            required_arguments=("name",),
            argument_schema=immutable_json(
                {
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "namespace": {"type": "string", "minLength": 1},
                    },
                    "additionalProperties": True,
                }
            ),
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
    _inspection_capability_names = (
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
            capability_names=self._inspection_capability_names,
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
            for name in self._inspection_capability_names
        )

    def tools(self) -> tuple[Tool, ...]:
        return tuple(
            KubernetesInspectTool(capability, self._backend)
            for capability in self._inspection_capability_names
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
                match_capabilities=self._inspection_capability_names,
            ),
        )

    def memories(self) -> tuple[MemoryRecord, ...]:
        return (
            MemoryRecord(
                MemoryKind.PROCEDURAL,
                "unhealthy workload triage",
                "When a workload is unhealthy, inspect pods before reading logs so "
                "the failing container is identified before its output is searched.",
                scope="kubernetes",
                confidence=0.9,
            ),
            MemoryRecord(
                MemoryKind.SEMANTIC,
                "kubernetes readiness",
                "A Kubernetes workload is considered healthy when its ready replicas "
                "match the desired replicas and no pods are in a crash loop.",
                scope="kubernetes",
                confidence=0.95,
            ),
        )


class KubernetesRemediationContextProvider:
    name = "kubernetes-remediation-context"

    def provide(self, state: AgentState) -> tuple[ContextFragment, ...]:
        return (
            ContextFragment(
                "kubernetes.scope",
                "Operate on Kubernetes resources using inspection and policy-gated "
                "workload scaling.",
                10,
            ),
        )


class KubernetesRemediationDomain(KubernetesDomain):
    _capability_names = (*KubernetesDomain._inspection_capability_names, "scale_workload")

    def __init__(
        self,
        inspection_backend: KubernetesBackend,
        mutation_backend: KubernetesMutationBackend,
    ) -> None:
        super().__init__(inspection_backend)
        self._mutation_backend = mutation_backend

    @property
    def manifest(self) -> DomainManifest:
        return DomainManifest(
            api_version="agent.nantian.dev/v1alpha1",
            kind="Domain",
            metadata=DomainMetadata(
                "kubernetes",
                "0.2.0",
                "Kubernetes inspection with policy-gated workload remediation",
            ),
            ontology=("Cluster", "Node", "Namespace", "Pod", "Deployment", "Service"),
            capability_names=self._capability_names,
            evaluator_names=(WorkloadHealthEvaluator.name,),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            *super().capabilities(),
            CapabilityDefinition(
                "scale_workload",
                "Scale workload replicas",
                CapabilityCategory.MUTATION,
                RiskLevel.MEDIUM,
            ),
        )

    def tools(self) -> tuple[Tool, ...]:
        return (*super().tools(), KubernetesScaleTool(self._mutation_backend))

    def policies(self) -> tuple[Policy, ...]:
        return (*super().policies(), KubernetesScalePolicy())

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return (KubernetesRemediationContextProvider(),)

    def task_expanders(self) -> tuple[TaskExpander, ...]:
        return (KubernetesRemediationExpander(),)
