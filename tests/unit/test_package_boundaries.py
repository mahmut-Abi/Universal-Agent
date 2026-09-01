"""Architecture boundary tests for the client/server package split.

The Universal Agent runtime is being split so that client packages (SDK, CLI,
TUI, Web) can be extracted to their own repository. The boundary rules:

- ``universal_agent_api`` (client SDK) must not import the kernel
  (``universal_agent``) — clients talk to the runtime only over its HTTP API.
- The kernel and its agentd server must not import client packages — the
  dependency direction is server -> kernel only.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"

CLIENT_PACKAGES = ("universal_agent_api",)
KERNEL_PACKAGE = "universal_agent"


def _imports(package: str) -> set[str]:
    """Collect top-level import targets of every module inside a package."""

    roots: list[Path] = []
    if package == KERNEL_PACKAGE:
        roots.append(SRC / package)
    else:
        roots.append(SRC / package)
    imported: set[str] = set()
    for root in roots:
        if not root.exists():
            pytest.fail(f"package directory is missing: {root}")
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported.add(node.module.split(".")[0])
    return imported


def test_client_sdk_does_not_import_kernel() -> None:
    """The client SDK ships standalone: no kernel imports anywhere."""

    kernel_imports = sorted(
        name for name in _imports("universal_agent_api") if name == KERNEL_PACKAGE
    )
    assert kernel_imports == [], (
        "universal_agent_api must not import the universal_agent kernel "
        f"(found: {kernel_imports}); clients depend only on the HTTP API"
    )


def test_kernel_does_not_import_client_packages() -> None:
    """The kernel (and agentd) must not depend on client packages."""

    kernel_root = SRC / KERNEL_PACKAGE
    violations: list[str] = []
    for path in kernel_root.rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Import):
                name = next((alias.name.split(".")[0] for alias in node.names), None)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                name = node.module.split(".")[0]
            if name in CLIENT_PACKAGES:
                violations.append(relative)
    assert violations == [], (
        "universal_agent must not import client packages "
        f"(found: {sorted(set(violations))}); the dependency direction is "
        "client -> SDK -> HTTP -> kernel"
    )
