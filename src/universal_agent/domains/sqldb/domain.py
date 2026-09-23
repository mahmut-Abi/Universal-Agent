"""Read-only SQL query domain (`sqldb`).

Lets the Agent run SELECT queries and inspect tables against a configured
database (SQLite first; PostgreSQL follows the same capability surface).
Every observation field becomes evidence via the shared extractor, and the
domain policy denies anything that is not a read-only query.
"""

from __future__ import annotations

from universal_agent.context import DomainContextProvider
from universal_agent.core import (
    AgentState,
    CapabilityCategory,
    CapabilityDefinition,
    ContextFragment,
    DomainManifest,
    DomainMetadata,
    JsonMapping,
    PolicyContext,
    PolicyEffect,
    PolicyResult,
    RiskLevel,
    ToolDefinition,
    immutable_json,
)
from universal_agent.domains.sqldb.backend import SqlBackend
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import Evidence, EvidenceContext, EvidenceExtractor
from universal_agent.memory import MemoryRecord
from universal_agent.policy import Policy, PolicyRule
from universal_agent.recovery import FailureCategory, RecoveryRule, RecoveryStrategy
from universal_agent.tasks import TaskExpander
from universal_agent.tools import Tool
from universal_agent.world import WorldUpdater


class SqlQueryRowsTool:
    def __init__(self, backend: SqlBackend) -> None:
        self.definition = ToolDefinition(
            name="sql_query_rows",
            description="Run a read-only SELECT query and return up to 200 rows",
            capabilities=("query_rows",),
            required_arguments=("sql",),
            side_effect=__import__("universal_agent.core", fromlist=["SideEffect"]).SideEffect.NONE,
            risk=RiskLevel.LOW,
            argument_schema=immutable_json(
                {
                    "required": ["sql"],
                    "properties": {
                        "sql": {
                            "type": "string",
                            "minLength": 1,
                            "description": "A single SELECT statement",
                        },
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    },
                    "additionalProperties": False,
                }
            ),
        )
        self._backend = backend

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return await self._backend.query_rows(arguments)


class SqlInspectTablesTool:
    def __init__(self, backend: SqlBackend) -> None:
        self.definition = ToolDefinition(
            name="sql_inspect_tables",
            description="List tables and views in the configured database",
            capabilities=("inspect_tables",),
            required_arguments=(),
            side_effect=__import__("universal_agent.core", fromlist=["SideEffect"]).SideEffect.NONE,
            risk=RiskLevel.LOW,
            argument_schema=immutable_json({"properties": {}, "additionalProperties": False}),
        )
        self._backend = backend

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return await self._backend.inspect_tables(arguments)


class SqlReadOnlyPolicy:
    """Deny any statement that is not a SELECT (defense in depth: the backend
    also validates and opens read-only connections)."""

    name = "sqldb-read-only"

    def evaluate(self, context: PolicyContext) -> PolicyResult | None:
        if context.capability.name != "query_rows":
            return None
        sql = context.arguments.get("sql")
        lowered = str(sql).strip().lower() if isinstance(sql, str) else ""
        first_word = lowered.split()[0] if lowered.split() else ""
        if first_word and first_word != "select":
            return PolicyResult(
                PolicyEffect.DENY,
                f"sqldb is read-only: {first_word} statements are not allowed",
                self.name,
            )
        return None


class SqldbEvidenceExtractor(EvidenceExtractor):
    name = "sqldb-evidence"

    def extract(self, context: EvidenceContext) -> tuple[Evidence, ...]:
        if context.observation.status.value != "succeeded":
            return ()
        subject = "sqldb"
        return tuple(
            Evidence(
                context.session_id,
                context.task.id,
                context.observation.action_id,
                context.observation.id,
                subject,
                key,
                value,
                self.name,
                0.9,
                observed_at=context.observation.observed_at,
            )
            for key, value in context.observation.data.items()
            if key != "subject"
        )


class SqldbContextProvider(DomainContextProvider):
    name = "sqldb-context"

    def provide(self, state: AgentState) -> tuple[ContextFragment, ...]:
        return (
            ContextFragment(
                "sqldb.scope",
                "Run read-only SELECT queries against the configured database.",
                10,
            ),
        )


class SqldbDomain:
    """Read-only SQL query domain (SQLite backend; PostgreSQL planned)."""

    def __init__(self, backend: SqlBackend) -> None:
        self._backend = backend

    @property
    def manifest(self) -> DomainManifest:
        return DomainManifest(
            api_version="agent.nantian.dev/v1alpha1",
            kind="Domain",
            metadata=DomainMetadata(
                "sqldb",
                "0.1.0",
                "Read-only SQL query domain (SELECT + table inspection)",
            ),
            ontology=("Database", "Table", "Row"),
            capability_names=("query_rows", "inspect_tables"),
            evaluator_names=("criteria",),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition(
                "query_rows",
                "Run a read-only SELECT query against the database",
                CapabilityCategory.OBSERVATION,
                RiskLevel.LOW,
            ),
            CapabilityDefinition(
                "inspect_tables",
                "List tables and views in the database",
                CapabilityCategory.OBSERVATION,
                RiskLevel.LOW,
            ),
        )

    def tools(self) -> tuple[Tool, ...]:
        return (
            SqlQueryRowsTool(self._backend),
            SqlInspectTablesTool(self._backend),
        )

    def policies(self) -> tuple[Policy, ...]:
        return (
            PolicyRule(
                "sqldb-read-only",
                PolicyEffect.ALLOW,
                "read-only SELECT queries allowed",
                categories=(CapabilityCategory.OBSERVATION,),
            ),
        )

    def evaluators(self) -> tuple[Evaluator, ...]:
        from universal_agent.evaluation import CriteriaEvaluator

        return (CriteriaEvaluator(),)

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return (SqldbContextProvider(),)

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
        return (SqldbEvidenceExtractor(),)

    def task_expanders(self) -> tuple[TaskExpander, ...]:
        return ()

    def world_updaters(self) -> tuple[WorldUpdater, ...]:
        from universal_agent.world import FactWorldUpdater

        return (FactWorldUpdater(),)

    def memories(self) -> tuple[MemoryRecord, ...]:
        return ()

    def recovery_rules(self) -> tuple[RecoveryRule, ...]:
        return (
            RecoveryRule(
                "sqldb-timeout-retry",
                (FailureCategory.TIMEOUT,),
                RecoveryStrategy.RETRY_ACTION,
                max_attempts=1,
                priority=10,
                match_capabilities=("query_rows", "inspect_tables"),
            ),
        )
