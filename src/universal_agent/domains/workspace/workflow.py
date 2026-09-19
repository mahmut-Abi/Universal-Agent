"""Workspace Domain runtime workflow components.

Evaluator, evidence extractor, world updater, task expander, recovery
rules and the context provider — the domain's semantic hooks.
"""

from __future__ import annotations

from pathlib import Path

from universal_agent.core import (
    AgentState,
    ContextFragment,
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    JsonMapping,
    JsonValue,
    ObservationStatus,
    immutable_json,
)
from universal_agent.domains.workspace.names import (
    CREATE_FILE_CAPABILITY,
    INSPECT_FILE_CAPABILITY,
    INSPECT_WORKSPACE_CAPABILITY,
    MODIFY_FILE_CAPABILITY,
    WORKSPACE_COMPLETION_EVALUATOR,
    WORKSPACE_CONTEXT_PROVIDER,
    WORKSPACE_EVIDENCE_EXTRACTOR,
    WORKSPACE_RECOVERY_RULE,
    WORKSPACE_TASK_EXPANDER,
    WORKSPACE_WORLD_UPDATER,
)
from universal_agent.evidence import Evidence, EvidenceContext
from universal_agent.recovery import FailureCategory, RecoveryRule, RecoveryStrategy
from universal_agent.tasks import TaskExpansionContext, TaskSpec
from universal_agent.world import WorldModel

# ─── Evaluator ──────────────────────────────────────────────────────────────


class WorkspaceCompletionEvaluator:
    """Evaluates whether workspace operation goals are met."""

    name = WORKSPACE_COMPLETION_EVALUATOR

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
            "workspace operations satisfied the goal criteria"
            if complete
            else "workspace operations incomplete",
            self.name,
            immutable_json(matched),
            task_complete,
            goal_complete,
        )


# ─── Evidence Extractor ─────────────────────────────────────────────────────


class WorkspaceEvidenceExtractor:
    """Extract typed evidence from workspace operation observations."""

    name = WORKSPACE_EVIDENCE_EXTRACTOR

    def extract(self, context: EvidenceContext) -> tuple[Evidence, ...]:
        if context.observation.status is not ObservationStatus.SUCCEEDED:
            return ()
        data = context.observation.data
        subject = str(data.get("resource") or "workspace")
        claims: list[tuple[str, JsonValue]] = []
        claim_keys = (
            "healthy",
            "readable",
            "created",
            "modified",
            "deleted",
            "file_count",
            "directory_count",
            "line_count",
            "size_bytes",
            "match_count",
        )
        for key in claim_keys:
            val = data.get(key)
            if val is not None:
                claims.append((key, val))
        evidence: list[Evidence] = []
        for claim, value in claims:
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
        # Also extract file existence claims
        if data.get("created"):
            evidence.append(
                Evidence(
                    session_id=context.session_id,
                    task_id=context.task.id,
                    action_id=context.observation.action_id,
                    observation_id=context.observation.id,
                    subject=subject,
                    claim="exists",
                    value=True,
                    source=self.name,
                )
            )
        if data.get("deleted"):
            evidence.append(
                Evidence(
                    session_id=context.session_id,
                    task_id=context.task.id,
                    action_id=context.observation.action_id,
                    observation_id=context.observation.id,
                    subject=subject,
                    claim="exists",
                    value=False,
                    source=self.name,
                )
            )
        # A failed read is world knowledge too: the file does not exist (yet).
        # Feeding exists=False + target_file into the world model lets the
        # task expander plan the create step instead of the model guessing.
        if data.get("readable") is False and data.get("error") == "not a file":
            evidence.append(
                Evidence(
                    session_id=context.session_id,
                    task_id=context.task.id,
                    action_id=context.observation.action_id,
                    observation_id=context.observation.id,
                    subject=subject,
                    claim="exists",
                    value=False,
                    source=self.name,
                )
            )
            evidence.append(
                Evidence(
                    session_id=context.session_id,
                    task_id=context.task.id,
                    action_id=context.observation.action_id,
                    observation_id=context.observation.id,
                    subject=subject,
                    claim="target_file",
                    value=subject,
                    source=self.name,
                )
            )
        return tuple(evidence)


# ─── World Updater ──────────────────────────────────────────────────────────


class WorkspaceWorldUpdater:
    """Mirror workspace evidence into the session World Model."""

    name = WORKSPACE_WORLD_UPDATER

    def apply(self, model: WorldModel, evidence: Evidence) -> bool:
        known_claims = {
            "healthy",
            "readable",
            "created",
            "modified",
            "deleted",
            "exists",
            "target_file",
            "file_count",
            "directory_count",
            "line_count",
            "size_bytes",
            "match_count",
        }
        if evidence.claim not in known_claims:
            return False
        return model.apply_fact(evidence)


# ─── Task Expander ──────────────────────────────────────────────────────────


class WorkspaceTaskExpander:
    """Dynamic task expansion driven by world-model state.

    A failed file read records ``exists=False`` + ``target_file`` evidence;
    when the goal also requires the file to exist (a ``created`` criterion),
    the expander plans the create step as its own tracked task instead of
    leaving the recovery implicit in the model's next decision.
    """

    name = WORKSPACE_TASK_EXPANDER
    capability_names = (
        INSPECT_WORKSPACE_CAPABILITY,
        INSPECT_FILE_CAPABILITY,
        CREATE_FILE_CAPABILITY,
    )

    def expand(self, context: TaskExpansionContext) -> tuple[TaskSpec, ...]:
        facts = {fact.claim: fact.value for fact in context.world.facts}
        current_criteria = set(context.task.required_criteria)
        depends_on = (context.task.id,)
        specs: list[TaskSpec] = []

        if (
            facts.get("exists") is False
            and isinstance(facts.get("target_file"), str)
            and "created" in current_criteria
            and "created" not in facts
        ):
            target = str(facts["target_file"])
            specs.append(
                TaskSpec(
                    f"create-{target}",
                    f"Create file {target}",
                    ("created",),
                    depends_on,
                )
            )

        return tuple(specs)


# ─── Recovery Rules ─────────────────────────────────────────────────────────


class WorkspaceRecoveryRule:
    """Recovery rules for workspace operations."""

    name = WORKSPACE_RECOVERY_RULE

    @property
    def rules(self) -> tuple[RecoveryRule, ...]:
        return (
            RecoveryRule(
                name="workspace-timeout-retry",
                categories=(FailureCategory.TIMEOUT,),
                strategy=RecoveryStrategy.RETRY_ACTION,
                max_attempts=2,
                match_capabilities=(CREATE_FILE_CAPABILITY, MODIFY_FILE_CAPABILITY),
                priority=10,
            ),
            RecoveryRule(
                name="workspace-file-exists",
                categories=(FailureCategory.VALIDATION,),
                strategy=RecoveryStrategy.ALTERNATIVE_CAPABILITY,
                max_attempts=1,
                capability=MODIFY_FILE_CAPABILITY,
                match_capabilities=(CREATE_FILE_CAPABILITY,),
                priority=20,
            ),
            RecoveryRule(
                name="workspace-file-not-found",
                categories=(FailureCategory.VALIDATION,),
                strategy=RecoveryStrategy.ALTERNATIVE_CAPABILITY,
                max_attempts=1,
                capability=CREATE_FILE_CAPABILITY,
                match_capabilities=(MODIFY_FILE_CAPABILITY,),
                priority=25,
            ),
        )


# ─── Context Provider ───────────────────────────────────────────────────────


class WorkspaceContextProvider:
    """Provide workspace state as context fragments."""

    name = WORKSPACE_CONTEXT_PROVIDER

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    def provide(self, state: AgentState) -> tuple[ContextFragment, ...]:
        summary = _inspect_workspace_sync(self._workspace)
        files = summary.get("file_count", 0)
        markers = summary.get("project_markers", [])
        marker_list = markers if isinstance(markers, list) else []
        text = (
            f"Workspace: {summary.get('root')} | "
            f"Files: {files} | "
            f"Directories: {summary.get('directory_count', 0)} | "
            f"Markers: {marker_list}"
        )
        return (ContextFragment(key=self.name, content=text),)


def _inspect_workspace_sync(workspace: Path) -> JsonMapping:
    """Synchronous workspace inspection for context providers."""
    file_count = 0
    dir_count = 0
    try:
        for entry in workspace.iterdir():
            if entry.is_dir():
                dir_count += 1
            else:
                file_count += 1
    except OSError:
        pass
    markers: list[JsonValue] = [
        m
        for m in ("pyproject.toml", "README.md", "package.json", "Cargo.toml", "go.mod")
        if (workspace / m).is_file()
    ]
    return immutable_json(
        {
            "resource": "workspace",
            "root": str(workspace),
            "readable": True,
            "healthy": True,
            "file_count": file_count,
            "directory_count": dir_count,
            "project_markers": markers,
        }
    )
