"""Local (Golden Path) Domain — a read-only workspace inspector.

This Domain gives the default `default` Profile a domain-neutral first-run
experience: `agent run "Hello"` inspects the local workspace (file counts,
project markers) and completes, without any Kubernetes vocabulary. It is
read-only by construction: it declares exactly one observation capability, no
mutation capabilities, and a policy that denies anything outside that
capability. Real-world side effects therefore cannot pass policy in this
Domain.
"""

from __future__ import annotations

from pathlib import Path

from universal_agent.context import DomainContextProvider
from universal_agent.core import (
    AgentState,
    CapabilityCategory,
    CapabilityDefinition,
    ContextFragment,
    DomainIdentity,
    DomainManifest,
    DomainMetadata,
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    JsonMapping,
    JsonValue,
    ObservationStatus,
    PolicyEffect,
    RiskLevel,
    SideEffect,
    ToolDefinition,
    immutable_json,
)
from universal_agent.domain import BaseDomainRuntime
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import Evidence, EvidenceContext, EvidenceExtractor
from universal_agent.policy import Policy, PolicyRule
from universal_agent.tasks import TaskExpander, TaskExpansionContext, TaskSpec
from universal_agent.tools import Tool
from universal_agent.world import FactWorldUpdater, WorldModel, WorldUpdater

LOCAL_DOMAIN_NAME = "local"
LOCAL_DOMAIN_VERSION = "0.1.0"
WORKSPACE_CAPABILITY = "inspect_workspace"
WORKSPACE_TOOL = "local_inspect_workspace"
LOCAL_POLICY_NAME = "local-read-only"

_PROJECT_MARKERS = ("pyproject.toml", "README.md", "package.json", "Cargo.toml", "go.mod")


def local_identity() -> DomainIdentity:
    return DomainIdentity(LOCAL_DOMAIN_NAME, LOCAL_DOMAIN_VERSION)


def inspect_workspace(cwd: Path | None = None) -> JsonMapping:
    """Collect a small read-only summary of the workspace at ``cwd``."""

    root = Path(cwd) if cwd is not None else Path.cwd()
    file_count = 0
    directory_count = 0
    try:
        entries = list(root.iterdir())
    except OSError as exc:
        return immutable_json(
            {
                "resource": "workspace",
                "root": str(root),
                "readable": False,
                "healthy": False,
                "error": str(exc),
            }
        )
    for entry in entries:
        if entry.is_dir():
            directory_count += 1
        else:
            file_count += 1
    markers = [marker for marker in _PROJECT_MARKERS if (root / marker).is_file()]
    project_markers: list[JsonValue] = list(markers)
    payload: dict[str, JsonValue] = {
        "resource": "workspace",
        "root": str(root),
        "readable": True,
        "healthy": True,
        "file_count": file_count,
        "directory_count": directory_count,
        "project_markers": project_markers,
    }
    return immutable_json(payload)


class WorkspaceInspectionTool:
    """Read-only local tool behind the ``inspect_workspace`` capability."""

    def __init__(self, cwd: Path | None = None) -> None:
        self._cwd = cwd
        self.definition = ToolDefinition(
            WORKSPACE_TOOL,
            "Summarize the local workspace (read-only).",
            (WORKSPACE_CAPABILITY,),
            side_effect=SideEffect.NONE,
            risk=RiskLevel.LOW,
            priority=1,
        )

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return inspect_workspace(self._cwd)


class WorkspaceHealthEvaluator:
    """Completes the goal once the workspace observation satisfies the criteria."""

    name = "workspace-health"

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
            "workspace inspection satisfied the goal criteria"
            if complete
            else "workspace remains uninspected",
            self.name,
            immutable_json(matched),
            task_complete,
            goal_complete,
        )


class WorkspaceEvidenceExtractor:
    """Project workspace observations into typed Evidence claims."""

    name = "workspace-evidence"

    def extract(self, context: EvidenceContext) -> tuple[Evidence, ...]:
        if context.observation.status is not ObservationStatus.SUCCEEDED:
            return ()
        data = context.observation.data
        subject = str(data.get("resource") or "workspace")
        claims: tuple[tuple[str, JsonValue], ...] = (
            ("healthy", data.get("healthy")),
            ("readable", data.get("readable")),
            ("file_count", data.get("file_count")),
            ("directory_count", data.get("directory_count")),
        )
        evidence: list[Evidence] = []
        for claim, value in claims:
            if value is None:
                continue
            evidence.append(
                Evidence(
                    session_id=context.session_id,
                    task_id=context.task.id,
                    action_id=context.observation.action_id,
                    observation_id=context.observation.id,
                    subject=subject,
                    claim=claim,
                    value=value,
                    source=self.name,
                )
            )
        markers = data.get("project_markers")
        if isinstance(markers, list):
            for marker in markers:
                evidence.append(
                    Evidence(
                        session_id=context.session_id,
                        task_id=context.task.id,
                        action_id=context.observation.action_id,
                        observation_id=context.observation.id,
                        subject=subject,
                        claim="project_marker",
                        value=str(marker),
                        source=self.name,
                    )
                )
        return tuple(evidence)


class WorkspaceTaskExpander:
    """The local flow is single-step; the expander never adds tasks."""

    name = "workspace-static"
    capability_names = (WORKSPACE_CAPABILITY,)

    def expand(self, context: TaskExpansionContext) -> tuple[TaskSpec, ...]:
        return ()


class LocalDomain(BaseDomainRuntime):
    """Read-only Golden Path domain for the default Profile."""

    def __init__(self, cwd: Path | None = None) -> None:
        self._tool = WorkspaceInspectionTool(cwd)

    @property
    def identity(self) -> DomainIdentity:
        return local_identity()

    @property
    def manifest(self) -> DomainManifest:
        return DomainManifest(
            api_version="agent.nantian.dev/v1alpha1",
            kind="Domain",
            metadata=DomainMetadata(
                name=LOCAL_DOMAIN_NAME,
                version=LOCAL_DOMAIN_VERSION,
                description="Read-only local workspace inspection for the default profile.",
            ),
            ontology=("Workspace", "ProjectMarker"),
            capability_names=(WORKSPACE_CAPABILITY,),
            evaluator_names=(WorkspaceHealthEvaluator.name,),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition(
                WORKSPACE_CAPABILITY,
                "Summarize the local workspace (read-only).",
                CapabilityCategory.OBSERVATION,
                RiskLevel.LOW,
            ),
        )

    def tools(self) -> tuple[Tool, ...]:
        return (self._tool,)

    def policies(self) -> tuple[Policy, ...]:
        return (
            PolicyRule(
                LOCAL_POLICY_NAME,
                PolicyEffect.ALLOW,
                "read-only workspace inspection is allowed",
                capabilities=(WORKSPACE_CAPABILITY,),
            ),
            PolicyRule(
                f"{LOCAL_POLICY_NAME}-mutations-denied",
                PolicyEffect.DENY,
                "the local profile allows no mutations",
                categories=(CapabilityCategory.MUTATION,),
            ),
        )

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (WorkspaceHealthEvaluator(),)

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return (WorkspaceContextProvider(),)

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
        return (WorkspaceEvidenceExtractor(),)

    def world_updaters(self) -> tuple[WorldUpdater, ...]:
        return (WorkspaceWorldUpdater(),)

    def task_expanders(self) -> tuple[TaskExpander, ...]:
        return (WorkspaceTaskExpander(),)


class WorkspaceWorldUpdater:
    """Mirror workspace Evidence claims into the session World Model."""

    name = "workspace-world"

    def apply(self, model: WorldModel, evidence: Evidence) -> bool:
        known_claims = {"healthy", "readable", "file_count", "directory_count", "project_marker"}
        if evidence.claim not in known_claims:
            return False
        return model.apply_fact(evidence)


class WorkspaceContextProvider:
    """Give the model a compact workspace summary as context."""

    name = "workspace-context"

    def provide(self, state: AgentState) -> tuple[ContextFragment, ...]:
        summary = inspect_workspace()
        files = summary.get("file_count")
        markers = summary.get("project_markers")
        marker_list = markers if isinstance(markers, list) else []
        text = f"Workspace {summary.get('root')} contains {files} files; markers {marker_list}."
        return (
            ContextFragment(
                key=self.name,
                content=text,
            ),
        )


__all__ = [
    "LOCAL_DOMAIN_NAME",
    "LOCAL_DOMAIN_VERSION",
    "LOCAL_POLICY_NAME",
    "WORKSPACE_CAPABILITY",
    "WORKSPACE_TOOL",
    "FactWorldUpdater",
    "LocalDomain",
    "WorkspaceContextProvider",
    "WorkspaceEvidenceExtractor",
    "WorkspaceHealthEvaluator",
    "WorkspaceInspectionTool",
    "WorkspaceTaskExpander",
    "WorkspaceWorldUpdater",
    "inspect_workspace",
    "local_identity",
]
