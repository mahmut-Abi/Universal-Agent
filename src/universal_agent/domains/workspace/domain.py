"""Workspace Domain — assembly of the sandboxed file-operation runtime.

The semantic pieces live in focused modules: ``names`` (constants),
``tools`` (file operations), ``policy`` (sensitive-path denial) and
``workflow`` (evaluator, evidence, world, expansion, recovery, context).
This module only assembles them into the DomainRuntime the kernel loads.
"""

from __future__ import annotations

from pathlib import Path

from universal_agent.context import DomainContextProvider
from universal_agent.core import (
    CapabilityCategory,
    CapabilityDefinition,
    DomainIdentity,
    DomainManifest,
    DomainMetadata,
    PolicyEffect,
    RiskLevel,
)
from universal_agent.domain import BaseDomainRuntime
from universal_agent.domains.workspace.names import (
    ALL_CAPABILITIES,
    CREATE_FILE_CAPABILITY,
    DELETE_FILE_CAPABILITY,
    INSPECT_FILE_CAPABILITY,
    INSPECT_WORKSPACE_CAPABILITY,
    MODIFY_FILE_CAPABILITY,
    SEARCH_FILES_CAPABILITY,
    WORKSPACE_ALLOW_MUTATE,
    WORKSPACE_ALLOW_READ,
    WORKSPACE_COMPLETION_EVALUATOR,
    WORKSPACE_CONFIRM_DELETE,
    WORKSPACE_DOMAIN_NAME,
    WORKSPACE_DOMAIN_VERSION,
    WORKSPACE_MEMORY_SUBJECT,
)
from universal_agent.domains.workspace.policy import SensitivePathPolicy
from universal_agent.domains.workspace.tools import (
    WorkspaceCreateFileTool,
    WorkspaceDeleteFileTool,
    WorkspaceInspectTool,
    WorkspaceModifyFileTool,
    WorkspaceReadFileTool,
    WorkspaceSearchTool,
)
from universal_agent.domains.workspace.workflow import (
    WorkspaceCompletionEvaluator,
    WorkspaceContextProvider,
    WorkspaceEvidenceExtractor,
    WorkspaceRecoveryRule,
    WorkspaceTaskExpander,
    WorkspaceWorldUpdater,
)
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import EvidenceExtractor
from universal_agent.memory import MemoryKind, MemoryRecord
from universal_agent.policy import Policy, PolicyRule
from universal_agent.recovery import RecoveryRule
from universal_agent.tasks import TaskExpander
from universal_agent.tools import Tool
from universal_agent.world import WorldUpdater


def workspace_identity() -> DomainIdentity:
    return DomainIdentity(WORKSPACE_DOMAIN_NAME, WORKSPACE_DOMAIN_VERSION)


# ─── Domain Assembly ────────────────────────────────────────────────────────


class WorkspaceDomain(BaseDomainRuntime):
    """File-operation domain for testing the full agent runtime loop.

    Provides observation + mutation capabilities that exercise:
    - Multiple capability types
    - Dynamic task expansion
    - Evidence collection
    - World model updates
    - Policy enforcement
    - Recovery rules
    """

    def __init__(self, workspace: Path | None = None) -> None:
        self._workspace = workspace or Path.cwd()
        self._tools = (
            WorkspaceInspectTool(self._workspace),
            WorkspaceReadFileTool(self._workspace),
            WorkspaceSearchTool(self._workspace),
            WorkspaceCreateFileTool(self._workspace),
            WorkspaceModifyFileTool(self._workspace),
            WorkspaceDeleteFileTool(self._workspace),
        )
        self._context_provider = WorkspaceContextProvider(self._workspace)

    @property
    def identity(self) -> DomainIdentity:
        return workspace_identity()

    @property
    def manifest(self) -> DomainManifest:
        return DomainManifest(
            api_version="agent.nantian.dev/v1alpha1",
            kind="Domain",
            metadata=DomainMetadata(
                name=WORKSPACE_DOMAIN_NAME,
                version=WORKSPACE_DOMAIN_VERSION,
                description="File-operation domain for testing the full agent runtime loop.",
            ),
            ontology=("Workspace", "File", "Directory"),
            capability_names=ALL_CAPABILITIES,
            evaluator_names=(WORKSPACE_COMPLETION_EVALUATOR,),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition(
                INSPECT_WORKSPACE_CAPABILITY,
                "Summarize the workspace: file count, directories, project markers.",
                CapabilityCategory.OBSERVATION,
                RiskLevel.LOW,
            ),
            CapabilityDefinition(
                INSPECT_FILE_CAPABILITY,
                "Read the content of a file in the workspace.",
                CapabilityCategory.OBSERVATION,
                RiskLevel.LOW,
            ),
            CapabilityDefinition(
                SEARCH_FILES_CAPABILITY,
                "Search for a pattern in workspace files.",
                CapabilityCategory.OBSERVATION,
                RiskLevel.LOW,
            ),
            CapabilityDefinition(
                CREATE_FILE_CAPABILITY,
                "Create a new file in the workspace.",
                CapabilityCategory.MUTATION,
                RiskLevel.MEDIUM,
            ),
            CapabilityDefinition(
                MODIFY_FILE_CAPABILITY,
                "Overwrite an existing file with new content.",
                CapabilityCategory.MUTATION,
                RiskLevel.MEDIUM,
            ),
            CapabilityDefinition(
                DELETE_FILE_CAPABILITY,
                "Delete an existing file. Destructive; requires confirmation.",
                CapabilityCategory.MUTATION,
                RiskLevel.HIGH,
            ),
        )

    def tools(self) -> tuple[Tool, ...]:
        return self._tools

    def policies(self) -> tuple[Policy, ...]:
        return (
            PolicyRule(
                WORKSPACE_ALLOW_READ,
                PolicyEffect.ALLOW,
                "all workspace read operations are allowed",
                capabilities=(
                    INSPECT_WORKSPACE_CAPABILITY,
                    INSPECT_FILE_CAPABILITY,
                    SEARCH_FILES_CAPABILITY,
                ),
            ),
            PolicyRule(
                WORKSPACE_ALLOW_MUTATE,
                PolicyEffect.ALLOW,
                "safe workspace mutations are allowed",
                capabilities=(CREATE_FILE_CAPABILITY, MODIFY_FILE_CAPABILITY),
            ),
            PolicyRule(
                WORKSPACE_CONFIRM_DELETE,
                PolicyEffect.REQUIRE_CONFIRMATION,
                "file deletion is destructive and requires user confirmation",
                capabilities=(DELETE_FILE_CAPABILITY,),
            ),
            SensitivePathPolicy(),
        )

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (WorkspaceCompletionEvaluator(),)

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return (self._context_provider,)

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
        return (WorkspaceEvidenceExtractor(),)

    def world_updaters(self) -> tuple[WorldUpdater, ...]:
        return (WorkspaceWorldUpdater(),)

    def task_expanders(self) -> tuple[TaskExpander, ...]:
        return (WorkspaceTaskExpander(),)

    def recovery_rules(self) -> tuple[RecoveryRule, ...]:
        return WorkspaceRecoveryRule().rules

    def memories(self) -> tuple[MemoryRecord, ...]:
        return (
            MemoryRecord(
                kind=MemoryKind.SEMANTIC,
                subject=WORKSPACE_MEMORY_SUBJECT,
                content="File operations in workspace: inspect, read, search, create, modify.",
                scope="workspace",
                confidence=1.0,
                source="domain",
                tags=("workspace", "file-operations"),
            ),
            MemoryRecord(
                kind=MemoryKind.PROCEDURAL,
                subject="workspace-create-pattern",
                content="To create a file: inspect workspace, then create_file.",
                scope="workspace",
                confidence=1.0,
                source="domain",
                tags=("workspace", "procedure"),
            ),
        )


__all__ = [
    "ALL_CAPABILITIES",
    "CREATE_FILE_CAPABILITY",
    "INSPECT_FILE_CAPABILITY",
    "INSPECT_WORKSPACE_CAPABILITY",
    "MODIFY_FILE_CAPABILITY",
    "SEARCH_FILES_CAPABILITY",
    "WORKSPACE_DOMAIN_NAME",
    "WORKSPACE_DOMAIN_VERSION",
    "WorkspaceCompletionEvaluator",
    "WorkspaceContextProvider",
    "WorkspaceDomain",
    "WorkspaceEvidenceExtractor",
    "WorkspaceRecoveryRule",
    "WorkspaceTaskExpander",
    "WorkspaceWorldUpdater",
    "workspace_identity",
]
