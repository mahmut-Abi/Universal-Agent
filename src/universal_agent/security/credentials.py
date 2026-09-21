"""Credential→principal resolution (authentication).

A credential is a high-entropy secret known only to its bearer; only its hash
is ever stored (mirrors the ``ua_credentials.token_hash`` storage rule — never
raw tokens, never in logs/events). Resolving a bearer token produces a fully
populated ``RequestPrincipal`` (user + tenant + role + scope), or ``None`` when
the token is unknown, revoked, or its user/tenant is disabled.

Phase 1 ships the hashing primitive, the ``CredentialStore`` protocol, and an
in-memory implementation used by tests and single-process deployments. A
Postgres-backed store (schema v3) is the storage seam (see Phase 1 of
docs/multitenancy-user-management-design.md).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from universal_agent.core import utc_now
from universal_agent.security.authorization import role_scope
from universal_agent.security.principal import (
    RequestPrincipal,
    Role,
    RoleBinding,
    Scope,
    Tenant,
    TenantStatus,
    UserAccountStatus,
    UserPrincipal,
)


def hash_credential(token: str) -> str:
    """Deterministic hash of a credential token (SHA-256 hex).

    Tokens are high-entropy secrets, so a plain keyed-equivalent digest is a
    reasonable password-equivalent hash here. Never log the input token.
    """

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class PrincipalAlreadyExistsError(ValueError):
    """A user/tenant with the same identifier already exists."""


class PrincipalNotFoundError(ValueError):
    """A referenced user/tenant/membership does not exist."""


@dataclass(frozen=True, slots=True)
class UserAccount:
    """A stored user record (adds the email the principal model omits)."""

    user_id: str
    email: str
    display_name: str | None = None
    status: UserAccountStatus = UserAccountStatus.ACTIVE
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Credential:
    credential_id: str
    tenant_id: str
    user_id: str
    token_hash: str
    scope: Scope = Scope.READ_WRITE
    role: Role = Role.OPERATOR
    revoked_at: datetime | None = None
    created_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


class CredentialStore(Protocol):
    """Resolves a raw bearer token to a request principal.

    Implementations (in-memory, Postgres v3) may add provisioning methods; the
    resolution contract must never leak the raw token or its hash outward.
    """

    def resolve(
        self,
        raw_token: str,
        *,
        now: datetime | None = None,
    ) -> RequestPrincipal | None:
        """Return the principal for a valid, non-revoked credential, or None."""
        ...


class CredentialAdminStore(CredentialStore, Protocol):
    """Provisioning surface behind the admin plane (user/tenant/role/credential
    management). Backed by the same storage as credential resolution.

    The raw token returned by ``issue_credential`` is shown exactly once —
    only its hash is retained.
    """

    def create_tenant(self, *, tenant_id: str, name: str) -> Tenant:
        """Register a tenant; raises PrincipalAlreadyExistsError on duplicate."""
        ...

    def create_user(
        self,
        *,
        user_id: str,
        email: str,
        display_name: str | None = None,
    ) -> UserPrincipal:
        """Register a user; raises PrincipalAlreadyExistsError on duplicate."""
        ...

    def set_role(self, *, tenant_id: str, user_id: str, role: Role) -> None:
        """Create or update a tenant membership binding."""
        ...

    def issue_credential(
        self,
        *,
        user_id: str,
        tenant_id: str,
        role: Role,
    ) -> tuple[str, Credential]:
        """Issue a credential for an existing member; (raw_token, Credential)."""
        ...

    def revoke_credential(self, credential_id: str) -> bool:
        """Revoke a credential; True when an active credential was revoked."""
        ...

    def list_members(self, tenant_id: str) -> tuple[RoleBinding, ...]:
        """List a tenant's membership bindings."""
        ...

    def list_users(self) -> tuple[UserAccount, ...]:
        """List stored user accounts."""
        ...

    def set_user_status(
        self,
        *,
        user_id: str,
        status: UserAccountStatus,
    ) -> UserAccount:
        """Enable or disable a user; disabled users stop authenticating."""
        ...

    def list_tenants(self) -> tuple[Tenant, ...]:
        """List stored tenants."""
        ...

    def set_tenant_status(
        self,
        *,
        tenant_id: str,
        status: TenantStatus,
    ) -> Tenant:
        """Enable or disable a tenant; disabled tenants stop authenticating."""
        ...

    def list_credentials(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> tuple[Credential, ...]:
        """List credential metadata (never token hashes' inputs)."""
        ...

    def remove_member(self, *, tenant_id: str, user_id: str) -> bool:
        """Remove a membership binding; the user's credentials for that
        tenant stop resolving. True when a binding was removed."""
        ...

    def has_any_admin(self) -> bool:
        """Whether any active admin membership exists (fresh-install
        bootstrap detection: the legacy shared token bootstrap window closes
        as soon as an admin exists)."""
        ...

    def has_any_credential(self) -> bool:
        """Whether any credential (revoked or not) has ever been issued.

        Bootstrap-window companion to :meth:`has_any_admin`: the shared-token
        bootstrap window must stay open until the first credential exists,
        otherwise provisioning an admin membership before issuing the
        credential locks every authenticator out (UA-LIVE-2026-09-21 Q12).
        """
        ...


class InMemoryCredentialStore:
    """Trivial in-memory credential registry for tests and embedded use.

    Not durable: it is a building block and a reference implementation, not a
    multi-process credential authority.
    """

    def __init__(self) -> None:
        self._users: dict[str, UserPrincipal] = {}
        self._emails: dict[str, str] = {}
        self._email_by_user: dict[str, str] = {}
        self._user_created: dict[str, datetime] = {}
        self._tenants: dict[str, Tenant] = {}
        self._memberships: dict[tuple[str, str], Role] = {}
        self._by_hash: dict[str, Credential] = {}
        self._by_id: dict[str, Credential] = {}

    # -- admin plane -------------------------------------------------------

    def create_tenant(self, *, tenant_id: str, name: str) -> Tenant:
        if tenant_id in self._tenants:
            raise PrincipalAlreadyExistsError(f"tenant already exists: {tenant_id}")
        tenant = Tenant(tenant_id=tenant_id, name=name)
        self._tenants[tenant_id] = tenant
        return tenant

    def create_user(
        self,
        *,
        user_id: str,
        email: str,
        display_name: str | None = None,
    ) -> UserPrincipal:
        if user_id in self._users:
            raise PrincipalAlreadyExistsError(f"user already exists: {user_id}")
        if email in self._emails:
            raise PrincipalAlreadyExistsError(f"user email already exists: {email}")
        principal = UserPrincipal(user_id=user_id, display_name=display_name)
        self._users[user_id] = principal
        self._emails[email] = user_id
        self._email_by_user[user_id] = email
        self._user_created[user_id] = utc_now()
        return principal

    def set_role(self, *, tenant_id: str, user_id: str, role: Role) -> None:
        if tenant_id not in self._tenants:
            raise PrincipalNotFoundError(f"tenant not found: {tenant_id}")
        if user_id not in self._users:
            raise PrincipalNotFoundError(f"user not found: {user_id}")
        self._memberships[(tenant_id, user_id)] = role

    def issue_credential(
        self,
        *,
        user_id: str,
        tenant_id: str,
        role: Role,
    ) -> tuple[str, Credential]:
        if tenant_id not in self._tenants:
            raise PrincipalNotFoundError(f"tenant not found: {tenant_id}")
        if user_id not in self._users:
            raise PrincipalNotFoundError(f"user not found: {user_id}")
        return self.issue(user_id=user_id, tenant_id=tenant_id, role=role)

    def revoke_credential(self, credential_id: str) -> bool:
        return self.revoke(credential_id)

    def list_members(self, tenant_id: str) -> tuple[RoleBinding, ...]:
        if tenant_id not in self._tenants:
            raise PrincipalNotFoundError(f"tenant not found: {tenant_id}")
        return tuple(
            RoleBinding(tenant_id=t_id, user_id=u_id, role=r)
            for (t_id, u_id), r in sorted(self._memberships.items())
            if t_id == tenant_id
        )

    def list_users(self) -> tuple[UserAccount, ...]:
        return tuple(
            UserAccount(
                user_id=user_id,
                email=self._email_by_user.get(user_id, ""),
                display_name=principal.display_name,
                status=principal.status,
                created_at=self._user_created.get(user_id),
            )
            for user_id, principal in sorted(self._users.items())
        )

    def set_user_status(
        self,
        *,
        user_id: str,
        status: UserAccountStatus,
    ) -> UserAccount:
        principal = self._users.get(user_id)
        if principal is None:
            raise PrincipalNotFoundError(f"user not found: {user_id}")
        updated = UserPrincipal(user_id=user_id, display_name=principal.display_name, status=status)
        self._users[user_id] = updated
        return UserAccount(
            user_id=user_id,
            email=self._email_by_user.get(user_id, ""),
            display_name=principal.display_name,
            status=status,
            created_at=self._user_created.get(user_id),
        )

    def list_tenants(self) -> tuple[Tenant, ...]:
        return tuple(self._tenants[t] for t in sorted(self._tenants))

    def set_tenant_status(self, *, tenant_id: str, status: TenantStatus) -> Tenant:
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            raise PrincipalNotFoundError(f"tenant not found: {tenant_id}")
        updated = Tenant(tenant_id=tenant_id, name=tenant.name, status=status)
        self._tenants[tenant_id] = updated
        return updated

    def list_credentials(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> tuple[Credential, ...]:
        selected = [
            c
            for c in self._by_id.values()
            if (user_id is None or c.user_id == user_id)
            and (tenant_id is None or c.tenant_id == tenant_id)
        ]
        return tuple(sorted(selected, key=lambda c: c.credential_id))

    def remove_member(self, *, tenant_id: str, user_id: str) -> bool:
        if tenant_id not in self._tenants:
            raise PrincipalNotFoundError(f"tenant not found: {tenant_id}")
        if user_id not in self._users:
            raise PrincipalNotFoundError(f"user not found: {user_id}")
        return self._memberships.pop((tenant_id, user_id), None) is not None

    def has_any_admin(self) -> bool:
        return any(role is Role.ADMIN for role in self._memberships.values())

    def has_any_credential(self) -> bool:
        return bool(self._by_id)

    # -- registration helpers (convenience for tests/embedded use) ---------

    def _ensure_user(self, user_id: str, display_name: str | None = None) -> UserPrincipal:
        existing = self._users.get(user_id)
        if existing is not None:
            return existing
        principal = UserPrincipal(user_id=user_id, display_name=display_name)
        self._users[user_id] = principal
        return principal

    def _ensure_tenant(self, tenant_id: str, name: str | None = None) -> Tenant:
        existing = self._tenants.get(tenant_id)
        if existing is not None:
            return existing
        tenant = Tenant(tenant_id=tenant_id, name=name or tenant_id)
        self._tenants[tenant_id] = tenant
        return tenant

    def register_user(self, principal: UserPrincipal) -> None:
        self._users[principal.user_id] = principal

    def register_tenant(self, tenant: Tenant) -> None:
        self._tenants[tenant.tenant_id] = tenant

    def issue(
        self,
        *,
        user_id: str,
        tenant_id: str,
        role: Role = Role.OPERATOR,
        scope: Scope | None = None,
        display_name: str | None = None,
        tenant_name: str | None = None,
    ) -> tuple[str, Credential]:
        """Issue a new credential, returning (raw_token, Credential).

        Auto-registers unknown users/tenants (convenience path); the admin
        plane uses ``issue_credential`` which requires existing principals.
        """

        self._ensure_user(user_id, display_name)
        self._ensure_tenant(tenant_id, tenant_name)
        raw_token = secrets.token_urlsafe(32)
        token_hash = hash_credential(raw_token)
        credential = Credential(
            credential_id=f"cred_{secrets.token_hex(6)}",
            tenant_id=tenant_id,
            user_id=user_id,
            token_hash=token_hash,
            scope=scope or role_scope(role),
            role=role,
        )
        self._by_hash[token_hash] = credential
        self._by_id[credential.credential_id] = credential
        self._memberships[(tenant_id, user_id)] = role
        return raw_token, credential

    def revoke(self, credential_id: str, *, now: datetime | None = None) -> bool:
        credential = self._by_id.get(credential_id)
        if credential is None:
            return False
        revoked = Credential(
            credential_id=credential.credential_id,
            tenant_id=credential.tenant_id,
            user_id=credential.user_id,
            token_hash=credential.token_hash,
            scope=credential.scope,
            role=credential.role,
            revoked_at=now or utc_now(),
        )
        self._by_id[credential_id] = revoked
        self._by_hash[credential.token_hash] = revoked
        return True

    def resolve(
        self,
        raw_token: str,
        *,
        now: datetime | None = None,
    ) -> RequestPrincipal | None:
        timestamp = now or utc_now()
        credential = self._by_hash.get(hash_credential(raw_token))
        if credential is None or not credential.active:
            return None
        if credential.revoked_at is not None and credential.revoked_at <= timestamp:
            return None
        user = self._users.get(credential.user_id)
        tenant = self._tenants.get(credential.tenant_id)
        if user is None or user.status is UserAccountStatus.DISABLED:
            return None
        if tenant is None or tenant.status is TenantStatus.DISABLED:
            return None
        # Role authority lives in the tenant membership binding, mirroring the
        # ua_tenant_memberships table; a credential without a membership no
        # longer resolves.
        role = self._memberships.get((credential.tenant_id, credential.user_id))
        if role is None:
            return None
        return RequestPrincipal(
            user=user,
            tenant=tenant,
            role=role,
            scope=role_scope(role),
        )
