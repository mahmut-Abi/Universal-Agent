from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from universal_agent.core import RiskLevel, SideEffect
from universal_agent.security.trust import (
    TrustBoundary,
    TrustVerdict,
    check_trust,
)
from universal_agent.tools import Tool


class SandboxViolation(RuntimeError):
    def __init__(self, verdict: TrustVerdict) -> None:
        self.verdict = verdict
        super().__init__("; ".join(verdict.reasons) or "sandbox violation")


@dataclass(frozen=True, slots=True)
class SandboxActionContext:
    risk: RiskLevel
    side_effect: SideEffect
    network: str | None = None
    path: str | None = None
    env: tuple[str, ...] | None = None
    tool: Tool | None = None
    arguments: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SandboxResult:
    permitted: bool
    reason: str
    executed: bool
    output: str | None = None
    error: str | None = None


@runtime_checkable
class SandboxExecutor(Protocol):
    def run(
        self,
        action: SandboxActionContext,
        *,
        boundary: TrustBoundary,
    ) -> SandboxResult: ...


class NoOpSandboxExecutor:
    def run(
        self,
        action: SandboxActionContext,
        *,
        boundary: TrustBoundary,
    ) -> SandboxResult:
        return SandboxResult(True, "isolation not enforced", False)


class DenySandboxExecutor:
    def run(
        self,
        action: SandboxActionContext,
        *,
        boundary: TrustBoundary,
    ) -> SandboxResult:
        raise SandboxViolation(TrustVerdict(False, ("sandbox denies all execution",)))


class LocalRestrictedSandbox:
    def run(
        self,
        action: SandboxActionContext,
        *,
        boundary: TrustBoundary,
    ) -> SandboxResult:
        verdict = check_trust(
            boundary,
            risk=action.risk,
            side_effect=action.side_effect,
            network=action.network,
            path=action.path,
            env=action.env,
        )
        if not verdict.permitted:
            raise SandboxViolation(verdict)
        return SandboxResult(True, "executed within trust boundary", False)


@dataclass(frozen=True, slots=True)
class Sandbox:
    boundary: TrustBoundary
    executor: SandboxExecutor

    def guard(self, action: SandboxActionContext) -> SandboxResult:
        return self.executor.run(action, boundary=self.boundary)
