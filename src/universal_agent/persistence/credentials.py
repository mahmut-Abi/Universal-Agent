"""Durable credential store over the schema v3 principal tables.

Implements the ``security`` contracts (``CredentialStore`` resolution plus the
``CredentialAdminStore`` provisioning surface) against ``ua_users`` /
``ua_tenants`` / ``ua_tenant_memberships`` / ``ua_credentials`` — the same
"persistence implements a sibling contract" pattern as the state store.

Storage rules: only the SHA-256 hash of a credential is stored; the raw token
is returned exactly once at issuance and never persisted or logged. Role
authority lives in the membership binding, mirroring the in-memory store.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, create_engine, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Row
from sqlalchemy.exc import IntegrityError

from universal_agent.core import utc_now
from universal_agent.persistence.postgres import (
    apply_postgres_migrations,
    postgres_principal_tables,
)
from universal_agent.security.authorization import role_scope
from universal_agent.security.credentials import (
    Credential,
    PrincipalAlreadyExistsError,
    PrincipalNotFoundError,
    UserAccount,
    hash_credential,
)
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

_USERS, _TENANTS, _MEMBERSHIPS, _CREDENTIALS = postgres_principal_tables()

_ACTIVE = "active"


class PostgresCredentialStore:
    """Durable credential + membership store for the agentd auth plane."""

    def __init__(
        self,
        url: str | None = None,
        *,
        engine: Engine | None = None,
        auto_migrate: bool = True,
    ) -> None:
        if url is None and engine is None:
            raise ValueError("postgres credential store requires a URL or engine")
        self._engine = engine if engine is not None else create_engine(url)  # type: ignore[arg-type]
        if auto_migrate:
            apply_postgres_migrations(self._engine)

    @contextmanager
    def _connect(self) -> Iterator[Connection]:
        with self._engine.begin() as connection:
            yield connection

    # -- resolution ----------------------------------------------------------

    def resolve(
        self,
        raw_token: str,
        *,
        now: datetime | None = None,
    ) -> RequestPrincipal | None:
        del now  # revoked_at filtering is storage-side; kept for protocol parity
        token_hash = hash_credential(raw_token)
        with self._connect() as connection:
            row = connection.execute(
                select(
                    _CREDENTIALS.c.revoked_at,
                    _MEMBERSHIPS.c.role,
                    _USERS.c.status.label("user_status"),
                    _USERS.c.display_name,
                    _TENANTS.c.status.label("tenant_status"),
                    _TENANTS.c.name.label("tenant_name"),
                )
                .join(
                    _MEMBERSHIPS,
                    (_MEMBERSHIPS.c.tenant_id == _CREDENTIALS.c.tenant_id)
                    & (_MEMBERSHIPS.c.user_id == _CREDENTIALS.c.user_id),
                )
                .join(_USERS, _USERS.c.user_id == _CREDENTIALS.c.user_id)
                .join(_TENANTS, _TENANTS.c.tenant_id == _CREDENTIALS.c.tenant_id)
                .where(_CREDENTIALS.c.token_hash == token_hash)
            ).first()
        if row is None:
            return None
        mapping = cast_row_mapping(row)
        if mapping["revoked_at"] is not None:
            return None
        if mapping["user_status"] != _ACTIVE or mapping["tenant_status"] != _ACTIVE:
            return None
        try:
            role = Role(str(mapping["role"]))
        except ValueError:
            return None
        return RequestPrincipal(
            user=UserPrincipal(
                user_id=str(mapping["user_id"]),
                display_name=_optional(mapping["display_name"]),
            ),
            tenant=Tenant(
                tenant_id=str(mapping["tenant_id"]),
                name=str(mapping["tenant_name"]),
            ),
            role=role,
            scope=role_scope(role),
        )

    # -- admin plane ----------------------------------------------------------

    def create_tenant(self, *, tenant_id: str, name: str) -> Tenant:
        try:
            with self._connect() as connection:
                connection.execute(
                    insert(_TENANTS).values(
                        tenant_id=tenant_id,
                        name=name,
                        status=_ACTIVE,
                        created_at=utc_now(),
                    )
                )
        except IntegrityError as exc:
            raise PrincipalAlreadyExistsError(f"tenant already exists: {tenant_id}") from exc
        return Tenant(tenant_id=tenant_id, name=name)

    def create_user(
        self,
        *,
        user_id: str,
        email: str,
        display_name: str | None = None,
    ) -> UserPrincipal:
        try:
            with self._connect() as connection:
                connection.execute(
                    insert(_USERS).values(
                        user_id=user_id,
                        email=email,
                        display_name=display_name,
                        status=_ACTIVE,
                        created_at=utc_now(),
                    )
                )
        except IntegrityError as exc:
            raise PrincipalAlreadyExistsError(f"user already exists: {user_id}") from exc
        return UserPrincipal(user_id=user_id, display_name=display_name)

    def set_role(self, *, tenant_id: str, user_id: str, role: Role) -> None:
        with self._connect() as connection:
            self._require_member_principals(connection, tenant_id=tenant_id, user_id=user_id)
            connection.execute(
                pg_insert(_MEMBERSHIPS)
                .values(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    role=role.value,
                    created_at=utc_now(),
                )
                .on_conflict_do_update(
                    index_elements=["tenant_id", "user_id"],
                    set_={"role": role.value},
                )
            )

    def issue_credential(
        self,
        *,
        user_id: str,
        tenant_id: str,
        role: Role,
    ) -> tuple[str, Credential]:
        raw_token = secrets.token_urlsafe(32)
        credential_id = f"cred_{secrets.token_hex(6)}"
        with self._connect() as connection:
            self._require_member_principals(connection, tenant_id=tenant_id, user_id=user_id)
            connection.execute(
                insert(_CREDENTIALS).values(
                    credential_id=credential_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    token_hash=hash_credential(raw_token),
                    scope=role_scope(role).value,
                    created_at=utc_now(),
                )
            )
            connection.execute(
                pg_insert(_MEMBERSHIPS)
                .values(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    role=role.value,
                    created_at=utc_now(),
                )
                .on_conflict_do_update(
                    index_elements=["tenant_id", "user_id"],
                    set_={"role": role.value},
                )
            )
        issued_at = utc_now()
        return raw_token, Credential(
            credential_id=credential_id,
            tenant_id=tenant_id,
            user_id=user_id,
            token_hash=hash_credential(raw_token),
            scope=role_scope(role),
            role=role,
            created_at=issued_at,
        )

    def revoke_credential(self, credential_id: str) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                update(_CREDENTIALS)
                .where(_CREDENTIALS.c.credential_id == credential_id)
                .where(_CREDENTIALS.c.revoked_at.is_(None))
                .values(revoked_at=utc_now())
            )
            return int(result.rowcount or 0) == 1

    def list_members(self, tenant_id: str) -> tuple[RoleBinding, ...]:
        with self._connect() as connection:
            self._require_tenant(connection, tenant_id)
            rows = connection.execute(
                select(_MEMBERSHIPS.c.user_id, _MEMBERSHIPS.c.role)
                .where(_MEMBERSHIPS.c.tenant_id == tenant_id)
                .order_by(_MEMBERSHIPS.c.user_id)
            ).all()
        return tuple(
            RoleBinding(tenant_id=tenant_id, user_id=str(row.user_id), role=Role(str(row.role)))
            for row in rows
        )

    def list_users(self) -> tuple[UserAccount, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                select(
                    _USERS.c.user_id,
                    _USERS.c.email,
                    _USERS.c.display_name,
                    _USERS.c.status,
                    _USERS.c.created_at,
                ).order_by(_USERS.c.user_id)
            ).all()
        return tuple(
            UserAccount(
                user_id=str(row.user_id),
                email=str(row.email),
                display_name=row.display_name,
                status=UserAccountStatus(str(row.status)),
                created_at=row.created_at,
            )
            for row in rows
        )

    def set_user_status(self, *, user_id: str, status: UserAccountStatus) -> UserAccount:
        with self._connect() as connection:
            result = connection.execute(
                update(_USERS)
                .where(_USERS.c.user_id == user_id)
                .values(status=status.value)
            )
            if int(result.rowcount or 0) != 1:
                raise PrincipalNotFoundError(f"user not found: {user_id}")
            row = connection.execute(
                select(
                    _USERS.c.email,
                    _USERS.c.display_name,
                    _USERS.c.created_at,
                ).where(_USERS.c.user_id == user_id)
            ).one()
        return UserAccount(
            user_id=user_id,
            email=str(row.email),
            display_name=row.display_name,
            status=status,
            created_at=row.created_at,
        )

    def list_tenants(self) -> tuple[Tenant, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                select(
                    _TENANTS.c.tenant_id,
                    _TENANTS.c.name,
                    _TENANTS.c.status,
                ).order_by(_TENANTS.c.tenant_id)
            ).all()
        return tuple(
            Tenant(
                tenant_id=str(row.tenant_id),
                name=str(row.name),
                status=TenantStatus(str(row.status)),
            )
            for row in rows
        )

    def set_tenant_status(self, *, tenant_id: str, status: TenantStatus) -> Tenant:
        with self._connect() as connection:
            result = connection.execute(
                update(_TENANTS)
                .where(_TENANTS.c.tenant_id == tenant_id)
                .values(status=status.value)
            )
            if int(result.rowcount or 0) != 1:
                raise PrincipalNotFoundError(f"tenant not found: {tenant_id}")
            row = connection.execute(
                select(_TENANTS.c.name).where(_TENANTS.c.tenant_id == tenant_id)
            ).one()
        return Tenant(tenant_id=tenant_id, name=str(row.name), status=status)

    def list_credentials(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> tuple[Credential, ...]:
        statement = select(
            _CREDENTIALS.c.credential_id,
            _CREDENTIALS.c.tenant_id,
            _CREDENTIALS.c.user_id,
            _CREDENTIALS.c.scope,
            _CREDENTIALS.c.created_at,
            _CREDENTIALS.c.revoked_at,
        )
        if user_id is not None:
            statement = statement.where(_CREDENTIALS.c.user_id == user_id)
        if tenant_id is not None:
            statement = statement.where(_CREDENTIALS.c.tenant_id == tenant_id)
        with self._connect() as connection:
            rows = connection.execute(
                statement.order_by(_CREDENTIALS.c.credential_id)
            ).all()
        return tuple(
            Credential(
                credential_id=str(row.credential_id),
                tenant_id=str(row.tenant_id),
                user_id=str(row.user_id),
                token_hash="",  # never surfaced through the listing API
                scope=Scope(str(row.scope)),
                revoked_at=row.revoked_at,
                created_at=row.created_at,
            )
            for row in rows
        )

    def remove_member(self, *, tenant_id: str, user_id: str) -> bool:
        from sqlalchemy import delete

        with self._connect() as connection:
            self._require_member_principals(connection, tenant_id=tenant_id, user_id=user_id)
            result = connection.execute(
                delete(_MEMBERSHIPS)
                .where(_MEMBERSHIPS.c.tenant_id == tenant_id)
                .where(_MEMBERSHIPS.c.user_id == user_id)
            )
            return int(result.rowcount or 0) == 1

    def has_any_admin(self) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                select(_MEMBERSHIPS.c.user_id)
                .where(_MEMBERSHIPS.c.role == Role.ADMIN.value)
                .limit(1)
            ).first()
        return row is not None

    # -- internal guards ------------------------------------------------------

    @staticmethod
    def _require_tenant(connection: Connection, tenant_id: str) -> None:
        row = connection.execute(
            select(_TENANTS.c.tenant_id).where(_TENANTS.c.tenant_id == tenant_id)
        ).first()
        if row is None:
            raise PrincipalNotFoundError(f"tenant not found: {tenant_id}")

    @staticmethod
    def _require_member_principals(
        connection: Connection,
        *,
        tenant_id: str,
        user_id: str,
    ) -> None:
        PostgresCredentialStore._require_tenant(connection, tenant_id)
        row = connection.execute(
            select(_USERS.c.user_id).where(_USERS.c.user_id == user_id)
        ).first()
        if row is None:
            raise PrincipalNotFoundError(f"user not found: {user_id}")


def cast_row_mapping(row: Row[Any]) -> dict[str, object]:
    """Typed access to a SQLAlchemy Row's column mapping."""

    return dict(row._mapping)


def _optional(value: object) -> str | None:
    return value if isinstance(value, str) else None
