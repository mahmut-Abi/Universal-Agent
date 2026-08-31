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
    JsonValue,
    PolicyEffect,
    RiskLevel,
    ToolDefinition,
    immutable_json,
)
from universal_agent.domains.observability.backend import MetricsBackend
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import Evidence, EvidenceContext, EvidenceExtractor
from universal_agent.memory import MemoryKind, MemoryRecord
from universal_agent.policy import Policy, PolicyRule
from universal_agent.recovery import FailureCategory, RecoveryRule, RecoveryStrategy
from universal_agent.tasks import TaskExpander
from universal_agent.tools import Tool
from universal_agent.world import FactWorldUpdater, WorldUpdater


class ObservabilityQueryMetricsTool:
    def __init__(self, backend: MetricsBackend) -> None:
        self.definition = ToolDefinition(
            name="observability_query_metrics",
            description="Run a read-only metrics query against the observability backend",
            capabilities=("query_metrics",),
            required_arguments=("query",),
            argument_schema=immutable_json(
                {
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                        "subject": {"type": "string", "minLength": 1},
                        "resource": {"type": "string", "minLength": 1},
                        "service": {"type": "string", "minLength": 1},
                    },
                    "additionalProperties": True,
                }
            ),
        )
        self._backend = backend

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return await self._backend.query(arguments)


class ObservabilityContextProvider:
    name = "observability-context"

    def provide(self, state: AgentState) -> tuple[ContextFragment, ...]:
        return (
            ContextFragment(
                "observability.scope",
                "Use read-only metrics queries to add current telemetry evidence.",
                10,
            ),
        )


class ObservabilityEvidenceExtractor:
    name = "observability-evidence"

    def extract(self, context: EvidenceContext) -> tuple[Evidence, ...]:
        if context.observation.status.value != "succeeded":
            return ()
        subject = _subject(context.observation.data)
        return tuple(
            _evidence(context, subject, key, value)
            for key, value in context.observation.data.items()
            if key != "subject"
        )


class MetricsHealthEvaluator:
    name = "metrics-health"

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
            "metrics criteria satisfied" if complete else "metrics criteria are not yet satisfied",
            self.name,
            immutable_json(matched),
            task_complete,
            goal_complete,
        )


class ObservabilityDomain:
    def __init__(self, backend: MetricsBackend) -> None:
        self._backend = backend

    @property
    def manifest(self) -> DomainManifest:
        return DomainManifest(
            api_version="agent.nantian.dev/v1alpha1",
            kind="Domain",
            metadata=DomainMetadata(
                "observability",
                "0.1.0",
                "Read-only metrics and telemetry domain",
            ),
            ontology=("MetricSeries", "Service", "Workload"),
            capability_names=("query_metrics",),
            evaluator_names=(MetricsHealthEvaluator.name,),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition(
                "query_metrics",
                "Query current metrics from an observability backend",
                CapabilityCategory.OBSERVATION,
                RiskLevel.LOW,
            ),
        )

    def tools(self) -> tuple[Tool, ...]:
        return (ObservabilityQueryMetricsTool(self._backend),)

    def policies(self) -> tuple[Policy, ...]:
        return (
            PolicyRule(
                "observability-read-only",
                PolicyEffect.ALLOW,
                "read-only metrics queries allowed",
                categories=(CapabilityCategory.OBSERVATION,),
            ),
        )

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (MetricsHealthEvaluator(),)

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return (ObservabilityContextProvider(),)

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
        return (ObservabilityEvidenceExtractor(),)

    def world_updaters(self) -> tuple[WorldUpdater, ...]:
        return (FactWorldUpdater(),)

    def task_expanders(self) -> tuple[TaskExpander, ...]:
        return ()

    def recovery_rules(self) -> tuple[RecoveryRule, ...]:
        return (
            RecoveryRule(
                "observability-timeout-retry",
                (FailureCategory.TIMEOUT,),
                RecoveryStrategy.RETRY_ACTION,
                max_attempts=2,
                priority=10,
                match_capabilities=("query_metrics",),
            ),
        )

    def memories(self) -> tuple[MemoryRecord, ...]:
        return (
            MemoryRecord(
                MemoryKind.PROCEDURAL,
                "metrics triage",
                "When infrastructure state is ambiguous, query metrics before proposing mutation.",
                scope="observability",
                confidence=0.9,
            ),
        )


def _subject(data: JsonMapping) -> str:
    subject = data.get("subject") or data.get("resource") or data.get("service")
    if isinstance(subject, str) and subject.strip():
        return subject.strip()
    query = data.get("query")
    return query.strip() if isinstance(query, str) and query.strip() else "metrics"


def _evidence(
    context: EvidenceContext,
    subject: str,
    claim: str,
    value: JsonValue,
) -> Evidence:
    return Evidence(
        context.session_id,
        context.task.id,
        context.observation.action_id,
        context.observation.id,
        subject,
        claim,
        value,
        context.observation.source,
        0.95,
        observed_at=context.observation.observed_at,
    )
