"""Workspace Domain policies: sensitive-path denial (deterministic)."""

from __future__ import annotations

from universal_agent.core import (
    PolicyContext,
    PolicyEffect,
    PolicyResult,
)
from universal_agent.domains.workspace.names import (
    CREATE_FILE_CAPABILITY,
    DELETE_FILE_CAPABILITY,
    INSPECT_FILE_CAPABILITY,
    MODIFY_FILE_CAPABILITY,
)

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


