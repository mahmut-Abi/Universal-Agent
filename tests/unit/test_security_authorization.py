"""Authorization (RBAC) unit tests (Phase 1)."""

from __future__ import annotations

import pytest

from universal_agent.security import (
    AuthorizationEvaluator,
    RequestPrincipal,
    Role,
    RoleBinding,
    Scope,
    Tenant,
    UserPrincipal,
    method_is_read,
    role_scope,
)


def _principal(*, role: Role, scope: Scope, tenant_id: str = "acme") -> RequestPrincipal:
    return RequestPrincipal(
        user=UserPrincipal(user_id="u-1"),
        tenant=Tenant(tenant_id=tenant_id, name="Acme"),
        role=role,
        scope=scope,
    )


def test_method_is_read() -> None:
    for method in ("GET", "get", "HEAD", "OPTIONS"):
        assert method_is_read(method) is True
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert method_is_read(method) is False


def test_role_scope_mapping() -> None:
    assert role_scope(Role.ADMIN) is Scope.READ_WRITE
    assert role_scope(Role.OPERATOR) is Scope.READ_WRITE
    assert role_scope(Role.READ_ONLY) is Scope.READ_ONLY


def test_read_requests_allowed_for_every_role() -> None:
    evaluator = AuthorizationEvaluator()
    for sc in (Scope.READ_WRITE, Scope.READ_ONLY):
        decision = evaluator.authorize_request(
            _principal(role=Role.READ_ONLY, scope=sc), method="GET"
        )
        assert decision.allowed is True
        assert decision.reason is None


def test_read_only_role_denies_write_with_rbac_reason() -> None:
    evaluator = AuthorizationEvaluator()
    decision = evaluator.authorize_request(
        _principal(role=Role.READ_ONLY, scope=Scope.READ_ONLY), method="POST"
    )
    assert decision.allowed is False
    assert decision.reason == "rbac"
    assert decision.message is not None


def test_read_write_role_allows_write() -> None:
    evaluator = AuthorizationEvaluator()
    for role in (Role.ADMIN, Role.OPERATOR):
        decision = evaluator.authorize_request(
            _principal(role=role, scope=Scope.READ_WRITE), method="DELETE"
        )
        assert decision.allowed is True
        assert decision.reason is None


def test_tenant_match_allows() -> None:
    evaluator = AuthorizationEvaluator()
    decision = evaluator.authorize_tenant(
        _principal(role=Role.ADMIN, scope=Scope.READ_WRITE), "acme"
    )
    assert decision.allowed is True


def test_cross_tenant_is_denied_with_reason() -> None:
    evaluator = AuthorizationEvaluator()
    decision = evaluator.authorize_tenant(
        _principal(role=Role.ADMIN, scope=Scope.READ_WRITE, tenant_id="acme"),
        "other",
    )
    assert decision.allowed is False
    assert decision.reason == "cross_tenant"


def test_role_binding_constructs_and_defaults_to_operator() -> None:
    binding = RoleBinding(tenant_id="acme", user_id="u-1")
    assert binding.role is Role.OPERATOR


def test_role_binding_rejects_blank() -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        RoleBinding(tenant_id="", user_id="u-1")
