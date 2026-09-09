"""Architecture boundary tests for the client/server package split.

The Universal Agent runtime is being split so that client packages (SDK, CLI,
TUI, Web) can be extracted to their own repository and the in-process packages
keep the design-document layering intact. The boundary rules:

- ``universal_agent_api`` (client SDK) must not import the kernel
  (``universal_agent``) — clients talk to the runtime only over its HTTP API.
- The kernel and its agentd server must not import client packages — the
  dependency direction is server -> kernel only.
- Domain/runtime/service packages must not import ``universal_agent.agentd`` —
  agentd is an application adapter over RuntimeService, not a shared
  serialization or domain utility layer.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"

CLIENT_PACKAGES = (
    "universal_agent_api",
    "universal_agent_cli",
    "universal_agent_tui",
    "universal_agent_web",
)
KERNEL_PACKAGE = "universal_agent"
_IMPORT_RE = re.compile(r"^\s*import\s+(.+)$")
_FROM_IMPORT_RE = re.compile(r"^\s*from\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s+import\b")


def _absolute_imports(path: Path) -> list[str]:
    imported: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        source = line.split("#", 1)[0].strip()
        if not source:
            continue
        if match := _FROM_IMPORT_RE.match(source):
            imported.append(match.group(1))
            continue
        if match := _IMPORT_RE.match(source):
            imported.extend(_import_targets(match.group(1)))
    return imported


def _import_targets(value: str) -> list[str]:
    return [
        target for item in value.split(",") if (target := item.strip().split(" as ", 1)[0].strip())
    ]


def _imports(package: str) -> set[str]:
    """Collect top-level import targets of every module inside a package."""

    root = SRC / package
    imported: set[str] = set()
    if not root.exists():
        pytest.fail(f"package directory is missing: {root}")
    for path in root.rglob("*.py"):
        imported.update(module.split(".")[0] for module in _absolute_imports(path))
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
        for module in _absolute_imports(path):
            if module.split(".")[0] in CLIENT_PACKAGES:
                violations.append(relative)
    assert violations == [], (
        "universal_agent must not import client packages "
        f"(found: {sorted(set(violations))}); the dependency direction is "
        "client -> SDK -> HTTP -> kernel"
    )


def test_runtime_layers_do_not_import_agentd_adapter() -> None:
    """Core layers must not depend on the agentd application adapter."""

    guarded_roots = (
        SRC / KERNEL_PACKAGE / "domain",
        SRC / KERNEL_PACKAGE / "domains",
        SRC / KERNEL_PACKAGE / "runtime",
        SRC / KERNEL_PACKAGE / "service",
        SRC / KERNEL_PACKAGE / "evaluation",
        SRC / KERNEL_PACKAGE / "ecosystem",
    )
    violations: list[str] = []
    for root in guarded_roots:
        if not root.exists():
            pytest.fail(f"package directory is missing: {root}")
        for path in root.rglob("*.py"):
            relative = path.relative_to(SRC).as_posix()
            for module in _absolute_imports(path):
                if module == "universal_agent.agentd" or module.startswith(
                    "universal_agent.agentd."
                ):
                    violations.append(relative)
    assert violations == [], (
        "domain/runtime/service/evaluation/ecosystem layers must not import "
        "universal_agent.agentd application adapters "
        f"(found: {sorted(set(violations))})"
    )
