"""Workspace Domain — file-operation domain for testing the full agent runtime loop.

This domain exercises every runtime extension point:
- Multiple capabilities (observation + mutation)
- Dynamic task expansion
- Evidence collection
- World model updates
- Policy enforcement
- Evaluation
- Recovery rules
- Context providers
- Memory records
"""

from universal_agent.domains.workspace.domain import (
    ALL_CAPABILITIES,
    CREATE_FILE_CAPABILITY,
    DELETE_FILE_CAPABILITY,
    INSPECT_FILE_CAPABILITY,
    INSPECT_WORKSPACE_CAPABILITY,
    MODIFY_FILE_CAPABILITY,
    SEARCH_FILES_CAPABILITY,
    WORKSPACE_CONFIRM_DELETE,
    WORKSPACE_DELETE_FILE_TOOL,
    WORKSPACE_DOMAIN_NAME,
    WORKSPACE_DOMAIN_VERSION,
    SensitivePathPolicy,
    WorkspaceCompletionEvaluator,
    WorkspaceContextProvider,
    WorkspaceDeleteFileTool,
    WorkspaceDomain,
    WorkspaceEvidenceExtractor,
    WorkspaceRecoveryRule,
    WorkspaceTaskExpander,
    WorkspaceWorldUpdater,
    workspace_identity,
)
from universal_agent.domains.workspace.agentd_routes import workspace_agentd_contribution
from universal_agent.domains.workspace.eval_suite import build_workspace_evaluation_suite
from universal_agent.domains.workspace.registration import workspace_cli_contribution

__all__ = [
    "ALL_CAPABILITIES",
    "CREATE_FILE_CAPABILITY",
    "DELETE_FILE_CAPABILITY",
    "INSPECT_FILE_CAPABILITY",
    "INSPECT_WORKSPACE_CAPABILITY",
    "MODIFY_FILE_CAPABILITY",
    "SEARCH_FILES_CAPABILITY",
    "WORKSPACE_CONFIRM_DELETE",
    "WORKSPACE_DELETE_FILE_TOOL",
    "WORKSPACE_DOMAIN_NAME",
    "WORKSPACE_DOMAIN_VERSION",
    "SensitivePathPolicy",
    "WorkspaceCompletionEvaluator",
    "WorkspaceContextProvider",
    "WorkspaceDeleteFileTool",
    "WorkspaceDomain",
    "WorkspaceEvidenceExtractor",
    "WorkspaceRecoveryRule",
    "WorkspaceTaskExpander",
    "WorkspaceWorldUpdater",
    "build_workspace_evaluation_suite",
    "workspace_identity",
]
