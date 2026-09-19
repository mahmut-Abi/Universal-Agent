"""Workspace Domain tool implementations (read-only + sandboxed mutations)."""

from __future__ import annotations

import re
from pathlib import Path

from universal_agent.core import (
    JsonMapping,
    JsonValue,
    RiskLevel,
    SideEffect,
    ToolDefinition,
    immutable_json,
)
from universal_agent.domains.workspace.names import (
    CREATE_FILE_CAPABILITY,
    DELETE_FILE_CAPABILITY,
    INSPECT_FILE_CAPABILITY,
    INSPECT_WORKSPACE_CAPABILITY,
    MODIFY_FILE_CAPABILITY,
    SEARCH_FILES_CAPABILITY,
    WORKSPACE_CREATE_FILE_TOOL,
    WORKSPACE_DELETE_FILE_TOOL,
    WORKSPACE_INSPECT_TOOL,
    WORKSPACE_MODIFY_FILE_TOOL,
    WORKSPACE_READ_FILE_TOOL,
    WORKSPACE_SEARCH_TOOL,
)

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


