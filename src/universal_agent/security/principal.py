"""Principal model: the identity / tenant / status types that underpin
multi-user and multi-tenant isolation.

Phase 0 ("Principal model" foundations) ships only the pure, IO-free type
layer. Authentication (OIDC / credential→principal resolution) and
authorization (RBAC) are designed but not implemented here — see
docs/multitenancy-user-management-design.md and docs/phase0-principal-implementation.md.

No secrets, no network, no storage: these are frozen dataclasses validated on
construction, consistent with the rest of the kernel.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from universal_agent.core import DEFAULT_TENANT_ID
from universal_agent.core.config_validation import parse_non_empty_string


class TenantStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class UserAccountStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class Role(StrEnum):
    """Per-tenant role granted through a membership binding.

    Roles are the input to RBAC: they decide *whether a user may request* a
    capability or operation. They do NOT decide whether an action is safe to
    execute — that stays the job of the runtime ``Policy``.
    """

    ADMIN = "admin"
    OPERATOR = "operator"
    READ_ONLY = "read_only"


class Scope(StrEnum):
    """Effective request scope derived from a role (or a credential).

    ``READ_WRITE`` permits mutating operations; ``READ_ONLY`` only reads.
    """

    READ_WRITE = "read_write"
    READ_ONLY = "read_only"


@dataclass(frozen=True, slots=True)
class Tenant:
    """A data-isolation and organizational boundary.

    A Tenant is a first-class identity — distinct from a Session, and never
    derived from one. Its ``tenant_id`` is the value persisted in every store
    partition (``ua_*`` tables carry ``tenant_id``), so the identifier must be
    stable and non-empty.
    """

    tenant_id: str
    name: str
    status: TenantStatus = TenantStatus.ACTIVE

    def __post_init__(self) -> None:
        parse_non_empty_string(self.tenant_id, "tenant_id")
        parse_non_empty_string(self.name, "tenant name")


@dataclass(frozen=True, slots=True)
class UserPrincipal:
    """A logical subject / identity (a human or service account).

    Belongs to a Tenant through a separate membership binding (RBAC role).
    ``user_id`` is the stable logical identifier, not an OS user or a display
    name; external identities map onto it at authentication time.
    """

    user_id: str
    display_name: str | None = None
    status: UserAccountStatus = UserAccountStatus.ACTIVE

    def __post_init__(self) -> None:
        parse_non_empty_string(self.user_id, "user_id")
        if self.display_name is not None:
            parse_non_empty_string(self.display_name, "display_name")

    @property
    def subject(self) -> str:
        """Canonical subject key used for ownership columns and audit."""

        return self.user_id


@dataclass(frozen=True, slots=True)
class RoleBinding:
    """A user's membership and role within a tenant.

    The canonical RBAC edge: (tenant_id, user_id) -> role. A user may hold a
    binding in many tenants; each binding is independent.
    """

    tenant_id: str
    user_id: str
    role: Role = Role.OPERATOR

    def __post_init__(self) -> None:
        parse_non_empty_string(self.tenant_id, "role binding tenant_id")
        parse_non_empty_string(self.user_id, "role binding user_id")


@dataclass(frozen=True, slots=True)
class RequestPrincipal:
    """A fully-resolved subject for a single request: who + which tenant +
    which role/scope they act under.

    Produced by a CredentialStore at authentication time and carried through
    authorization. Not persisted itself — it is derived from credentials and
    memberships on every request.
    """

    user: UserPrincipal
    tenant: Tenant
    role: Role = Role.OPERATOR
    scope: Scope = Scope.READ_WRITE

    @property
    def user_id(self) -> str:
        return self.user.user_id

    @property
    def tenant_id(self) -> str:
        return self.tenant.tenant_id

    @property
    def subject(self) -> str:
        return self.user.subject

    @property
    def is_read_only(self) -> bool:
        return self.scope is Scope.READ_ONLY


def default_tenant() -> Tenant:
    """The canonical single-tenant fallback (implicit tenancy).

    Mirrors ``core.DEFAULT_TENANT_ID`` so the type layer and the persistence
    layer agree on the identifier for legacy / single-tenant deployments.
    """

    return Tenant(tenant_id=DEFAULT_TENANT_ID, name="default")
