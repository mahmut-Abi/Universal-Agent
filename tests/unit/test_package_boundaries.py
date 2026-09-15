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


def test_kernel_imports_only_the_domains_composition_root() -> None:
    """The kernel/agentd may reference the domains package only via its
    composition root (`universal_agent.domains.profile_service`) — never a
    concrete domain module."""

    kernel_root = SRC / KERNEL_PACKAGE
    allowed = "universal_agent.domains.profile_service"
    violations: list[str] = []
    for path in kernel_root.rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        if relative.startswith("universal_agent/domains/"):
            continue
        for module in _absolute_imports(path):
            if module.startswith("universal_agent.domains") and module != allowed:
                violations.append(f"{relative}: {module}")
    assert violations == [], (
        "kernel modules must not import concrete domain modules; route them "
        f"through the domains composition root ({allowed}) "
        f"(found: {sorted(set(violations))})"
    )


def test_cli_shell_does_not_import_domain_packages() -> None:
    """The CLI shell consumes domains only through entry-point contributions
    and the kernel facade — never by importing `universal_agent.domains.*`."""

    violations: list[str] = []
    for path in (SRC / "universal_agent_cli").rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        for module in _absolute_imports(path):
            if module.startswith("universal_agent.domains"):
                violations.append(f"{relative}: {module}")
    assert violations == [], (
        "the CLI shell must not import domain packages; consume domain "
        f"features via entry-point contributions (found: {sorted(set(violations))})"
    )


# ---------------------------------------------------------------------------
# Kernel-internal layering (strict: any new upward import must either fix the
# layering or be added here as an explicit, documented seam).
# ---------------------------------------------------------------------------

_LAYER_RANKS = {
    "core": 0,
    "security": 1,
    "tasks": 1,
    "evidence": 1,
    "policy": 1,
    "state": 1,
    "memory": 1,
    "context": 1,
    "tools": 1,
    "world": 1,
    "model": 1,
    "recovery": 1,
    "persistence": 1,
    "observation": 1,
    "profile": 1,
    "goals": 1,
    "capability": 1,
    "coordination": 2,
    "domain": 2,
    "runtime": 3,
    "evaluation": 4,
    "operations": 4,
    "distributed": 4,
    "multi_agent": 5,
    "host": 5,
    "service": 5,
    "ecosystem": 5,
    "web": 6,
    "agentd": 7,
}

# Sanctioned upward seams, each with its rationale. Keep minimal.
_SANCTIONED_UPWARD = {
    # UA-D2 resolved (2026-09-15): event-stream helpers extracted to the
    # neutral universal_agent.eventstream module; persistence imports it.
    # Resource-lock/idempotency registries consumed by the action executor.
    ("runtime", "coordination"),
    # RuntimeBuilder assembles evaluators and lock registries into components.
    ("domain", "coordination"),
    ("domain", "evaluation"),
    # domain package re-exports the root SDK facade names in the scaffold
    # stub template (text, not a runtime import) and runtime.py re-exports
    # evaluator contracts.
    ("domain", "<root>"),
    # RuntimeHost is the assembly layer: it wires services and distributed
    # coordinators.
    ("host", "service"),
    ("host", "distributed"),
    # RuntimeService aggregates the optional multi-agent read models.
    ("service", "multi_agent"),
    ("service", "distributed"),
    ("service", "domain"),
    ("service", "operations"),
    ("service", "profile"),
    # evaluation/dispatch.py is CLI-glue that drives suites against a live
    # RuntimeService (application-adapter seam inside the evaluation package).
    ("evaluation", "service"),
    # core TYPE_CHECKING-only cycle guards (evidence/world model types).
    ("core", "evidence"),
    ("core", "world"),
}


def _kernel_subpackage_imports() -> dict[str, set[str]]:
    """Map each kernel subpackage to the sibling subpackages it imports."""

    root = SRC / KERNEL_PACKAGE
    graph: dict[str, set[str]] = {}
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        parts = relative.split("/")
        sub = parts[0] if len(parts) > 1 else "<root>"
        if sub in {"domains"}:  # composition layer + concrete domains: skip
            continue
        graph.setdefault(sub, set())
        for module in _absolute_imports(path):
            if module == KERNEL_PACKAGE:
                graph[sub].add("<root>")
            elif module.startswith(f"{KERNEL_PACKAGE}."):
                target = module.split(".")[1]
                if target != sub:
                    graph[sub].add(target)
    return graph


def test_kernel_layering_has_no_new_upward_imports() -> None:
    """Every kernel-internal upward import must be in the sanctioned seam set."""

    graph = _kernel_subpackage_imports()
    violations: list[str] = []
    for sub, targets in sorted(graph.items()):
        rank = _LAYER_RANKS.get(sub)
        if rank is None:
            continue
        for target in sorted(targets):
            target_rank = _LAYER_RANKS.get(target)
            if target_rank is None or target_rank <= rank:
                continue
            if (sub, target) in _SANCTIONED_UPWARD:
                continue
            violations.append(f"{sub} -> {target}")
    assert violations == [], (
        "new kernel-internal upward imports detected; fix the layering or "
        f"add an explicit sanctioned seam with rationale (found: {violations})"
    )


def test_sanctioned_layering_seams_still_exist() -> None:
    """Sanctioned seams are intentional: fail when one disappears so the
    table stays truthful (and debts become visible when fixed)."""

    graph = _kernel_subpackage_imports()
    stale: list[str] = []
    for sub, target in sorted(_SANCTIONED_UPWARD):
        if target not in graph.get(sub, set()):
            stale.append(f"{sub} -> {target}")
    assert stale == [], (
        f"sanctioned seams no longer exist; remove them from the table (resolved: {stale})"
    )
