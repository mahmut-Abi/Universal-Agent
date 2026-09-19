"""Workspace Domain — sandboxed file operations for testing the full loop.

Submodules: names (constants), tools, policy, workflow, domain (assembly),
plus host-surface contributions (cli_runtime, registration, agentd_routes,
eval_suite).
"""

from universal_agent.domains.workspace.agentd_routes import workspace_agentd_contribution
from universal_agent.domains.workspace.cli_runtime import (
    WorkspaceDecisionAdapter,
    build_default_workspace_service,
    build_workspace_profile_service,
    build_workspace_service,
    workspace_domain_config,
    workspace_domain_config_from_payload,
)
from universal_agent.domains.workspace.domain import (
    WorkspaceDomain,
    workspace_identity,
)
from universal_agent.domains.workspace.eval_suite import build_workspace_evaluation_suite
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
    WORKSPACE_DELETE_FILE_TOOL,
    WORKSPACE_DOMAIN_NAME,
    WORKSPACE_DOMAIN_VERSION,
    WORKSPACE_MEMORY_SUBJECT,
)
from universal_agent.domains.workspace.policy import SensitivePathPolicy
from universal_agent.domains.workspace.registration import workspace_cli_contribution
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

__all__ = [
    "ALL_CAPABILITIES",
    "CREATE_FILE_CAPABILITY",
    "DELETE_FILE_CAPABILITY",
    "INSPECT_FILE_CAPABILITY",
    "INSPECT_WORKSPACE_CAPABILITY",
    "MODIFY_FILE_CAPABILITY",
    "SEARCH_FILES_CAPABILITY",
    "WORKSPACE_ALLOW_MUTATE",
    "WORKSPACE_ALLOW_READ",
    "WORKSPACE_COMPLETION_EVALUATOR",
    "WORKSPACE_CONFIRM_DELETE",
    "WORKSPACE_DELETE_FILE_TOOL",
    "WORKSPACE_DOMAIN_NAME",
    "WORKSPACE_DOMAIN_VERSION",
    "WORKSPACE_MEMORY_SUBJECT",
    "SensitivePathPolicy",
    "WorkspaceCompletionEvaluator",
    "WorkspaceContextProvider",
    "WorkspaceCreateFileTool",
    "WorkspaceDecisionAdapter",
    "WorkspaceDeleteFileTool",
    "WorkspaceDomain",
    "WorkspaceEvidenceExtractor",
    "WorkspaceInspectTool",
    "WorkspaceModifyFileTool",
    "WorkspaceReadFileTool",
    "WorkspaceRecoveryRule",
    "WorkspaceSearchTool",
    "WorkspaceTaskExpander",
    "WorkspaceWorldUpdater",
    "build_default_workspace_service",
    "build_workspace_evaluation_suite",
    "build_workspace_profile_service",
    "build_workspace_service",
    "workspace_agentd_contribution",
    "workspace_cli_contribution",
    "workspace_domain_config",
    "workspace_domain_config_from_payload",
    "workspace_identity",
]
