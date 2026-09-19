"""Workspace Domain — a file-operation domain for testing the full agent runtime loop.

This Domain exercises every runtime extension point:
- Multiple capabilities (observation + mutation)
- Dynamic task expansion
- Evidence collection
- World model updates
- Policy enforcement (allow/deny/confirm)
- Evaluation
- Recovery rules
- Context providers
- Memory records

It operates on a sandboxed workspace directory, providing file operations
that the LLM can use to accomplish goals like "create a Python project skeleton"
or "find and fix a bug in the codebase".
"""

from __future__ import annotations

import re
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
    PolicyContext,
    PolicyEffect,
    PolicyResult,
    RiskLevel,
    SideEffect,
    ToolDefinition,
    immutable_json,
)
from universal_agent.domain import BaseDomainRuntime
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import Evidence, EvidenceContext, EvidenceExtractor
from universal_agent.memory import MemoryKind, MemoryRecord
from universal_agent.policy import Policy, PolicyRule
from universal_agent.recovery import FailureCategory, RecoveryRule, RecoveryStrategy
from universal_agent.tasks import TaskExpander, TaskExpansionContext, TaskSpec
from universal_agent.tools import Tool
from universal_agent.world import WorldModel, WorldUpdater

# ─── Domain Identity ────────────────────────────────────────────────────────

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
WORKSPACE_DENY_SENSITIVE = "workspace-deny-sensitive"

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


def workspace_identity() -> DomainIdentity:
    return DomainIdentity(WORKSPACE_DOMAIN_NAME, WORKSPACE_DOMAIN_VERSION)


# Observation payloads flow into evidence and then into the model context, so
# reads and searches are bounded to keep the context budget predictable.
_MAX_READ_CHARS = 20_000
_SEARCH_SKIP_DIRS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", "dist", "build"}
)


# ─── Tool Implementations ───────────────────────────────────────────────────


def _safe_path(path: str, workspace: Path) -> Path | None:
    """Resolve a path within the workspace, returning None if it escapes."""
    try:
        resolved = (workspace / path).resolve()
        if resolved.is_relative_to(workspace.resolve()):
            return resolved
    except (OSError, ValueError):
        pass
    return None


class WorkspaceInspectTool:
    """Read-only tool: summarize the workspace."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self.definition = ToolDefinition(
            WORKSPACE_INSPECT_TOOL,
            "Summarize the workspace: file count, directory count, project markers.",
            (INSPECT_WORKSPACE_CAPABILITY,),
            side_effect=SideEffect.NONE,
            risk=RiskLevel.LOW,
            priority=1,
        )

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        root = self._workspace
        file_count = 0
        dir_count = 0
        files: list[JsonValue] = []
        try:
            for entry in root.iterdir():
                if entry.is_dir():
                    dir_count += 1
                else:
                    file_count += 1
                    files.append(entry.name)
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
        markers: list[JsonValue] = [
            m
            for m in ("pyproject.toml", "README.md", "package.json", "Cargo.toml", "go.mod")
            if (root / m).is_file()
        ]
        return immutable_json(
            {
                "resource": "workspace",
                "root": str(root),
                "readable": True,
                "healthy": True,
                "file_count": file_count,
                "directory_count": dir_count,
                "files": list(files[:50]),
                "project_markers": markers,
            }
        )


class WorkspaceReadFileTool:
    """Read-only tool: read file content."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self.definition = ToolDefinition(
            WORKSPACE_READ_FILE_TOOL,
            "Read the content of a file in the workspace.",
            (INSPECT_FILE_CAPABILITY,),
            required_arguments=("path",),
            side_effect=SideEffect.NONE,
            risk=RiskLevel.LOW,
            priority=2,
        )

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        path_str = str(arguments.get("path", ""))
        resolved = _safe_path(path_str, self._workspace)
        if resolved is None:
            return immutable_json(
                {
                    "resource": path_str,
                    "readable": False,
                    "error": "path escapes workspace or is invalid",
                }
            )
        if not resolved.is_file():
            return immutable_json(
                {
                    "resource": path_str,
                    "readable": False,
                    "error": "not a file",
                }
            )
        try:
            content = resolved.read_text(encoding="utf-8")
            lines = content.split("\n")
            # Truncate huge files: observation payloads flow into evidence and
            # then into the model context, so an unbounded read would blow the
            # context budget. Keep the head plus an explicit truncation marker.
            truncated = len(content) > _MAX_READ_CHARS
            if truncated:
                content = content[:_MAX_READ_CHARS]
                lines = content.split("\n")
            payload: dict[str, JsonValue] = {
                "resource": path_str,
                "readable": True,
                "healthy": True,
                "content": content,
                "line_count": len(lines),
                "size_bytes": resolved.stat().st_size,
            }
            if truncated:
                payload["truncated"] = True
            return immutable_json(payload)
        except OSError as exc:
            return immutable_json(
                {
                    "resource": path_str,
                    "readable": False,
                    "error": str(exc),
                }
            )


class WorkspaceSearchTool:
    """Read-only tool: search file content with regex."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self.definition = ToolDefinition(
            WORKSPACE_SEARCH_TOOL,
            "Search for a pattern in workspace files. Returns matching lines.",
            (SEARCH_FILES_CAPABILITY,),
            required_arguments=("pattern",),
            side_effect=SideEffect.NONE,
            risk=RiskLevel.LOW,
            priority=3,
        )

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        pattern_str = str(arguments.get("pattern", ""))
        glob = str(arguments.get("glob", "*.py"))
        try:
            max_results = int(str(arguments.get("max_results", "50")))
        except (ValueError, TypeError):
            max_results = 50
        try:
            regex = re.compile(pattern_str)
        except re.error as exc:
            return immutable_json(
                {
                    "pattern": pattern_str,
                    "error": f"invalid regex: {exc}",
                    "matches": [],
                }
            )
        matches: list[dict[str, JsonValue]] = []
        for path in self._workspace.rglob(glob):
            if not path.is_file() or len(matches) >= max_results:
                break
            # Skip dependency/build noise: huge trees that never hold source.
            if _SEARCH_SKIP_DIRS.intersection(path.relative_to(self._workspace).parts):
                continue
            try:
                if path.stat().st_size > _MAX_READ_CHARS:
                    continue
                lines = path.read_text(encoding="utf-8").split("\n")
                for i, line in enumerate(lines, 1):
                    if regex.search(line):
                        match: dict[str, JsonValue] = {
                            "file": str(path.relative_to(self._workspace)),
                            "line": i,
                            "content": line.strip()[:200],
                        }
                        matches.append(match)
                        if len(matches) >= max_results:
                            break
            except (OSError, UnicodeDecodeError):
                continue
        return immutable_json(
            {
                "pattern": pattern_str,
                "glob": glob,
                "match_count": len(matches),
                "matches": list(matches),
            }
        )


class WorkspaceCreateFileTool:
    """Mutation tool: create a new file."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self.definition = ToolDefinition(
            WORKSPACE_CREATE_FILE_TOOL,
            "Create a new file in the workspace with given content.",
            (CREATE_FILE_CAPABILITY,),
            required_arguments=("path", "content"),
            side_effect=SideEffect.REVERSIBLE,
            risk=RiskLevel.MEDIUM,
            priority=10,
        )

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        path_str = str(arguments.get("path", ""))
        content = str(arguments.get("content", ""))
        resolved = _safe_path(path_str, self._workspace)
        if resolved is None:
            return immutable_json(
                {
                    "resource": path_str,
                    "created": False,
                    "error": "path escapes workspace or is invalid",
                }
            )
        if resolved.exists():
            return immutable_json(
                {
                    "resource": path_str,
                    "created": False,
                    "error": "file already exists",
                }
            )
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            return immutable_json(
                {
                    "resource": path_str,
                    "created": True,
                    "healthy": True,
                    "size_bytes": resolved.stat().st_size,
                    "line_count": len(content.split("\n")),
                }
            )
        except OSError as exc:
            return immutable_json(
                {
                    "resource": path_str,
                    "created": False,
                    "error": str(exc),
                }
            )


class WorkspaceModifyFileTool:
    """Mutation tool: modify file content (overwrite)."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self.definition = ToolDefinition(
            WORKSPACE_MODIFY_FILE_TOOL,
            "Overwrite an existing file with new content.",
            (MODIFY_FILE_CAPABILITY,),
            required_arguments=("path", "content"),
            side_effect=SideEffect.REVERSIBLE,
            risk=RiskLevel.MEDIUM,
            priority=11,
        )

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        path_str = str(arguments.get("path", ""))
        content = str(arguments.get("content", ""))
        resolved = _safe_path(path_str, self._workspace)
        if resolved is None:
            return immutable_json(
                {
                    "resource": path_str,
                    "modified": False,
                    "error": "path escapes workspace or is invalid",
                }
            )
        if not resolved.is_file():
            return immutable_json(
                {
                    "resource": path_str,
                    "modified": False,
                    "error": "file does not exist",
                }
            )
        try:
            old_size = resolved.stat().st_size
            resolved.write_text(content, encoding="utf-8")
            new_size = resolved.stat().st_size
            return immutable_json(
                {
                    "resource": path_str,
                    "modified": True,
                    "healthy": True,
                    "old_size_bytes": old_size,
                    "new_size_bytes": new_size,
                    "line_count": len(content.split("\n")),
                }
            )
        except OSError as exc:
            return immutable_json(
                {
                    "resource": path_str,
                    "modified": False,
                    "error": str(exc),
                }
            )


class WorkspaceDeleteFileTool:
    """Destructive tool: delete a file. Requires user confirmation via policy."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self.definition = ToolDefinition(
            WORKSPACE_DELETE_FILE_TOOL,
            "Delete an existing file. Destructive: requires user confirmation.",
            (DELETE_FILE_CAPABILITY,),
            required_arguments=("path",),
            side_effect=SideEffect.DESTRUCTIVE,
            risk=RiskLevel.HIGH,
            priority=12,
        )

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        path_str = str(arguments.get("path", ""))
        resolved = _safe_path(path_str, self._workspace)
        if resolved is None:
            return immutable_json(
                {
                    "resource": path_str,
                    "deleted": False,
                    "error": "path escapes workspace or is invalid",
                }
            )
        if not resolved.is_file():
            return immutable_json(
                {
                    "resource": path_str,
                    "deleted": False,
                    "error": "file does not exist",
                }
            )
        try:
            size = resolved.stat().st_size
            resolved.unlink()
            return immutable_json(
                {
                    "resource": path_str,
                    "deleted": True,
                    "healthy": True,
                    "size_bytes": size,
                }
            )
        except OSError as exc:
            return immutable_json(
                {
                    "resource": path_str,
                    "deleted": False,
                    "error": str(exc),
                }
            )


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


# ─── Sensitive Path Policy ──────────────────────────────────────────────────

# Basenames that must never be created, modified, read or deleted through
# the workspace domain. Enforced by the deterministic Policy engine (before
# any tool runs), not by prompt text (AGENTS.md §4.3).
_SENSITIVE_BASENAMES = frozenset(
    {
        ".env",
        "secrets.json",
        "credentials.json",
        "id_rsa",
        "id_ed25519",
        ".npmrc",
        ".pypirc",
    }
)

# Wildcard suffix patterns (fnmatch, case-insensitive) for credential file
# families: dotfile env variants (.env.local, .env.production.local, ...),
# backup copies (secrets.json.bak), and key material by extension.
_SENSITIVE_PATTERNS = (
    ".env*",
    "*.env",
    "*.key",
    "*.pem",
    "*.p12",
    "*.pfx",
    "*secret*",
    "*credential*",
    "*_rsa",
)


def _is_sensitive_path(path: str) -> bool:
    """Case-insensitive basename match across POSIX and Windows separators.

    On POSIX a Windows-style path keeps its backslashes, so both derivations
    must be checked: ``..\\env`` style payloads must not slip through the
    POSIX reading of the string. Exact names plus fnmatch-style wildcard
    families (``.env*``, ``*.key``, ``*secret*``, ...) are matched against
    each derivation.
    """
    from fnmatch import fnmatch
    from pathlib import PurePosixPath, PureWindowsPath

    names = {PurePosixPath(path).name.lower(), PureWindowsPath(path).name.lower()}
    for name in names:
        if name in _SENSITIVE_BASENAMES:
            return True
        if any(fnmatch(name, pattern) for pattern in _SENSITIVE_PATTERNS):
            return True
    return False


class SensitivePathPolicy:
    """Deny any path-addressed operation whose target is a sensitive file.

    A custom Policy (not a static PolicyRule) because the decision depends on
    the action's ``path`` argument, which declarative rules cannot match.
    Inspect/read and all mutation capabilities are covered: secrets must be
    neither exfiltrated nor tampered with.
    """

    name = "workspace-sensitive-paths"

    def __init__(self, capabilities: tuple[str, ...] = ()) -> None:
        self._capabilities = capabilities or (
            INSPECT_FILE_CAPABILITY,
            CREATE_FILE_CAPABILITY,
            MODIFY_FILE_CAPABILITY,
            DELETE_FILE_CAPABILITY,
        )

    def evaluate(self, context: PolicyContext) -> PolicyResult | None:
        if context.capability.name not in self._capabilities:
            return None
        raw_path = str(context.arguments.get("path", ""))
        if not raw_path:
            return None
        if _is_sensitive_path(raw_path):
            return PolicyResult(
                PolicyEffect.DENY,
                f"{raw_path} is a sensitive file: operations on it are denied",
                self.name,
            )
        return None


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
