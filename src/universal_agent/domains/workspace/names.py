"""Workspace Domain name constants — single source of truth.

Every module in the package imports names from here so the manifest,
tools, policies and tests can never drift apart.
"""

from __future__ import annotations

# ── Names ────────────────────────────────────────────────────

WORKSPACE_DOMAIN_NAME = "workspace"
WORKSPACE_DOMAIN_VERSION = "0.1.0"

# ─── Capability Names ───────────────────────────────────────────────────────

INSPECT_WORKSPACE_CAPABILITY = "inspect_workspace"
INSPECT_FILE_CAPABILITY = "inspect_file"
SEARCH_FILES_CAPABILITY = "search_files"
CREATE_FILE_CAPABILITY = "create_file"
MODIFY_FILE_CAPABILITY = "modify_file"
DELETE_FILE_CAPABILITY = "delete_file"

ALL_CAPABILITIES = (
    INSPECT_WORKSPACE_CAPABILITY,
    INSPECT_FILE_CAPABILITY,
    SEARCH_FILES_CAPABILITY,
    CREATE_FILE_CAPABILITY,
    MODIFY_FILE_CAPABILITY,
    DELETE_FILE_CAPABILITY,
)

# ─── Tool Names ─────────────────────────────────────────────────────────────

WORKSPACE_INSPECT_TOOL = "workspace_inspect"
WORKSPACE_READ_FILE_TOOL = "workspace_read_file"
WORKSPACE_SEARCH_TOOL = "workspace_search"
WORKSPACE_CREATE_FILE_TOOL = "workspace_create_file"
WORKSPACE_MODIFY_FILE_TOOL = "workspace_modify_file"
WORKSPACE_DELETE_FILE_TOOL = "workspace_delete_file"

# ─── Policy Names ───────────────────────────────────────────────────────────

WORKSPACE_ALLOW_READ = "workspace-allow-read"
WORKSPACE_ALLOW_MUTATE = "workspace-allow-mutate"
WORKSPACE_CONFIRM_DELETE = "workspace-confirm-delete"

# ─── Evaluator Names ────────────────────────────────────────────────────────

WORKSPACE_COMPLETION_EVALUATOR = "workspace-completion"

# ─── Evidence Extractor Names ───────────────────────────────────────────────

WORKSPACE_EVIDENCE_EXTRACTOR = "workspace-evidence"

# ─── World Updater Names ────────────────────────────────────────────────────

WORKSPACE_WORLD_UPDATER = "workspace-world"

# ─── Task Expander Names ────────────────────────────────────────────────────

WORKSPACE_TASK_EXPANDER = "workspace-workflow"

# ─── Recovery Rule Names ────────────────────────────────────────────────────

WORKSPACE_RECOVERY_RULE = "workspace-recovery"

# ─── Context Provider Names ─────────────────────────────────────────────────

WORKSPACE_CONTEXT_PROVIDER = "workspace-context"

# ─── Memory Names ───────────────────────────────────────────────────────────

WORKSPACE_MEMORY_SUBJECT = "workspace-knowledge"
