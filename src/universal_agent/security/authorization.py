"""Authorization (RBAC) evaluation for request-level control.

This is the "may this subject request this" layer, deliberately separate from
the runtime ``Policy`` which decides "is this action safe in context". They
compose as: ``allowed = RBAC.allows(user, op) and Policy.decides(action)``.

Phase 1 ships a role/scope evaluator plus a tenant-sanity check. It is pure
and IO-free; it never resolves credentials (see ``security.credentials``).
"""

from __future__ import annotations

from dataclasses import dataclass

from universal_agent.security.principal import RequestPrincipal, Role, Scope


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """Outcome of a single authorization check.

    ``reason`` is an auditable, stable string so downstream code distinguishes
    an RBAC denial from a runtime Policy denial and from a cross-tenant
    rejection:
      - ``None``        -> allowed
      - ``"rbac"``      -> denied by role/scope (subject may not request this)
      - ``"cross_tenant"`` -> denied because the subject's tenant does not
                              match the store/request tenant
    """

    allowed: bool
    reason: str | None = None
    message: str | None = None


READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def method_is_read(method: str) -> bool:
    return method.upper() in READ_METHODS


def role_scope(role: Role) -> Scope:
    """Map a role to its effective request scope."""

    if role in {Role.ADMIN, Role.OPERATOR}:
        return Scope.READ_WRITE
    return Scope.READ_ONLY


class AuthorizationEvaluator:
    """Deterministic, side-effect-free RBAC checks over a resolved request
    principal. To be effective, the runtime must call it on every request."""

    def authorize_request(
        self,
        principal: RequestPrincipal,
        *,
        method: str,
    ) -> AuthorizationDecision:
        """Decide whether the principal's role/scope may perform ``method``.

        Reads are allowed for every role; mutating methods require a
        READ_WRITE scope.
        """

        if method_is_read(method):
            return AuthorizationDecision(allowed=True)
        if principal.scope is Scope.READ_WRITE:
            return AuthorizationDecision(allowed=True)
        return AuthorizationDecision(
            allowed=False,
            reason="rbac",
            message=(
                f"subject {principal.subject} has {principal.scope.value} scope; "
                "mutating operations require read_write"
            ),
        )

    def authorize_tenant(
        self,
        principal: RequestPrincipal,
        expected_tenant_id: str,
    ) -> AuthorizationDecision:
        """Cross-tenant guard: the subject's tenant must match the tenant the
        request targets (the service's store scope). Never trusts a request
        header as the boundary."""

        if principal.tenant_id == expected_tenant_id:
            return AuthorizationDecision(allowed=True)
        return AuthorizationDecision(
            allowed=False,
            reason="cross_tenant",
            message=(
                f"subject {principal.subject} belongs to tenant "
                f"{principal.tenant_id}, not the request tenant {expected_tenant_id}"
            ),
        )
