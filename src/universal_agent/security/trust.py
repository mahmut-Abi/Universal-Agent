from __future__ import annotations

from dataclasses import dataclass

from universal_agent.core import RiskLevel, SideEffect


@dataclass(frozen=True, slots=True)
class TrustBoundary:
    allowed_networks: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    allowed_env: tuple[str, ...] = ()
    allow_side_effects: bool = True
    deny_risky: bool = False


@dataclass(frozen=True, slots=True)
class TrustVerdict:
    permitted: bool
    reasons: tuple[str, ...]

    @property
    def denied(self) -> bool:
        return not self.permitted


def check_trust(
    boundary: TrustBoundary,
    *,
    risk: RiskLevel,
    side_effect: SideEffect,
    network: str | None = None,
    path: str | None = None,
    env: tuple[str, ...] | None = None,
) -> TrustVerdict:
    reasons: list[str] = []
    if boundary.deny_risky and risk is RiskLevel.HIGH:
        reasons.append("high risk action rejected by boundary")
    if not boundary.allow_side_effects and side_effect is not SideEffect.NONE:
        reasons.append("side effect rejected by boundary")
    if (
        boundary.allowed_networks
        and network is not None
        and network not in boundary.allowed_networks
    ):
        reasons.append(f"network '{network}' not in boundary allowlist")
    if boundary.allowed_paths and path is not None and path not in boundary.allowed_paths:
        reasons.append(f"path '{path}' not in boundary allowlist")
    if boundary.allowed_env and env is not None:
        forbidden = tuple(item for item in env if item not in boundary.allowed_env)
        if forbidden:
            reasons.append(f"environment variables {forbidden} not in boundary allowlist")
    if reasons:
        return TrustVerdict(False, tuple(reasons))
    return TrustVerdict(True, ())
