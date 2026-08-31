from __future__ import annotations

from universal_agent.core import RiskLevel, SideEffect
from universal_agent.security.sandbox import (
    DenySandboxExecutor,
    LocalRestrictedSandbox,
    NoOpSandboxExecutor,
    Sandbox,
    SandboxActionContext,
    SandboxViolation,
)
from universal_agent.security.trust import (
    TrustBoundary,
    TrustVerdict,
    check_trust,
)


def test_high_risk_denied_when_deny_risky() -> None:
    boundary = TrustBoundary(deny_risky=True)
    verdict = check_trust(
        boundary,
        risk=RiskLevel.HIGH,
        side_effect=SideEffect.NONE,
    )
    assert not verdict.permitted
    assert verdict.denied


def test_high_risk_allowed_when_not_deny_risky() -> None:
    boundary = TrustBoundary(deny_risky=False)
    verdict = check_trust(
        boundary,
        risk=RiskLevel.HIGH,
        side_effect=SideEffect.NONE,
    )
    assert verdict.permitted


def test_network_not_in_allowlist_denied() -> None:
    boundary = TrustBoundary(allowed_networks=("internal",))
    verdict = check_trust(
        boundary,
        risk=RiskLevel.LOW,
        side_effect=SideEffect.NONE,
        network="public",
    )
    assert not verdict.permitted
    assert any("network" in reason for reason in verdict.reasons)


def test_network_in_allowlist_permitted() -> None:
    boundary = TrustBoundary(allowed_networks=("internal",))
    verdict = check_trust(
        boundary,
        risk=RiskLevel.LOW,
        side_effect=SideEffect.NONE,
        network="internal",
    )
    assert verdict.permitted


def test_path_not_in_allowlist_denied() -> None:
    boundary = TrustBoundary(allowed_paths=("/var/run",))
    verdict = check_trust(
        boundary,
        risk=RiskLevel.LOW,
        side_effect=SideEffect.NONE,
        path="/etc/secrets",
    )
    assert not verdict.permitted
    assert any("path" in reason for reason in verdict.reasons)


def test_side_effect_denied_when_disallowed() -> None:
    boundary = TrustBoundary(allow_side_effects=False)
    verdict = check_trust(
        boundary,
        risk=RiskLevel.LOW,
        side_effect=SideEffect.DESTRUCTIVE,
    )
    assert not verdict.permitted


def test_env_not_in_allowlist_denied() -> None:
    boundary = TrustBoundary(allowed_env=("HOME",))
    verdict = check_trust(
        boundary,
        risk=RiskLevel.LOW,
        side_effect=SideEffect.NONE,
        env=("HOME", "AWS_SECRET_ACCESS_KEY"),
    )
    assert not verdict.permitted


def test_permissive_boundary_allows() -> None:
    boundary = TrustBoundary()
    verdict = check_trust(
        boundary,
        risk=RiskLevel.HIGH,
        side_effect=SideEffect.DESTRUCTIVE,
        network="public",
        path="/any",
    )
    assert verdict.permitted


def test_noop_sandbox_always_permits_not_executed() -> None:
    executor = NoOpSandboxExecutor()
    action = SandboxActionContext(risk=RiskLevel.HIGH, side_effect=SideEffect.DESTRUCTIVE)
    result = executor.run(action, boundary=TrustBoundary(deny_risky=True))
    assert result.permitted
    assert not result.executed


def test_deny_sandbox_always_raises() -> None:
    executor = DenySandboxExecutor()
    action = SandboxActionContext(risk=RiskLevel.LOW, side_effect=SideEffect.NONE)
    try:
        executor.run(action, boundary=TrustBoundary())
    except SandboxViolation as exc:
        assert isinstance(exc.verdict, TrustVerdict)
    else:
        raise AssertionError("expected SandboxViolation")


def test_local_restricted_permits_within_boundary() -> None:
    executor = LocalRestrictedSandbox()
    action = SandboxActionContext(
        risk=RiskLevel.LOW,
        side_effect=SideEffect.NONE,
        network="internal",
    )
    result = executor.run(action, boundary=TrustBoundary(allowed_networks=("internal",)))
    assert result.permitted
    assert not result.executed


def test_local_restricted_raises_outside_boundary() -> None:
    executor = LocalRestrictedSandbox()
    action = SandboxActionContext(
        risk=RiskLevel.LOW,
        side_effect=SideEffect.NONE,
        network="public",
    )
    try:
        executor.run(action, boundary=TrustBoundary(allowed_networks=("internal",)))
    except SandboxViolation as exc:
        assert not exc.verdict.permitted
    else:
        raise AssertionError("expected SandboxViolation")


def test_local_restricted_denies_high_risk_when_deny_risky() -> None:
    executor = LocalRestrictedSandbox()
    action = SandboxActionContext(risk=RiskLevel.HIGH, side_effect=SideEffect.NONE)
    try:
        executor.run(action, boundary=TrustBoundary(deny_risky=True))
    except SandboxViolation:
        pass
    else:
        raise AssertionError("expected SandboxViolation")


def test_sandbox_guard_composes_boundary_and_executor() -> None:
    sandbox = Sandbox(
        boundary=TrustBoundary(allowed_networks=("internal",)),
        executor=LocalRestrictedSandbox(),
    )
    allowed = sandbox.guard(
        SandboxActionContext(risk=RiskLevel.LOW, side_effect=SideEffect.NONE, network="internal")
    )
    assert allowed.permitted
    denied_action = SandboxActionContext(
        risk=RiskLevel.LOW, side_effect=SideEffect.NONE, network="public"
    )
    try:
        sandbox.guard(denied_action)
    except SandboxViolation:
        pass
    else:
        raise AssertionError("expected SandboxViolation")
