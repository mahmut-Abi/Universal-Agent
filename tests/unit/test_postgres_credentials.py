"""PostgresCredentialStore tests (Phase 2).

Resolution and provisioning run against the schema v3 principal tables. The
full loop is a gated integration test (see `docker compose --profile postgres`).
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, make_url, text

from universal_agent.persistence.credentials import PostgresCredentialStore
from universal_agent.security import (
    PrincipalAlreadyExistsError,
    PrincipalNotFoundError,
    Role,
    RoleBinding,
    Scope,
)


def test_postgres_credential_store_requires_url_or_engine() -> None:
    with pytest.raises(ValueError, match="requires a URL or engine"):
        PostgresCredentialStore()


def _require_pg() -> str:
    dsn = os.environ.get("UA_TEST_PG_URL") or os.environ.get("AGENTD_PG_URL")
    if not dsn:
        pytest.skip("no Postgres DSN set (UA_TEST_PG_URL or AGENTD_PG_URL)")
    return dsn


def _fresh_database(admin_dsn: str) -> str:
    url = make_url(admin_dsn)
    dbname = f"ua_test_creds_{uuid.uuid4().hex[:8]}"
    admin = create_engine(admin_dsn, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {dbname}"))
    admin.dispose()
    return str(url.set(database=dbname))


def _drop_database(admin_dsn: str, target: str) -> None:
    dbname = make_url(target).database
    admin = create_engine(admin_dsn, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {dbname}"))
    admin.dispose()


@pytest.mark.integration
def test_postgres_credential_provision_resolve_revoke_loop() -> None:
    dsn = _require_pg()
    target = _fresh_database(dsn)
    store = PostgresCredentialStore(url=target)
    try:
        store.create_tenant(tenant_id="acme", name="Acme Corp")
        store.create_user(user_id="alice", email="alice@acme.test", display_name="Alice")
        store.set_role(tenant_id="acme", user_id="alice", role=Role.READ_ONLY)

        raw, cred = store.issue_credential(user_id="alice", tenant_id="acme", role=Role.READ_ONLY)
        principal = store.resolve(raw)
        assert principal is not None
        assert principal.user_id == "alice"
        assert principal.tenant_id == "acme"
        assert principal.role is Role.READ_ONLY
        assert principal.scope is Scope.READ_ONLY

        # Role authority lives in the membership binding: promoting the member
        # upgrades the same credential without re-issue.
        store.set_role(tenant_id="acme", user_id="alice", role=Role.OPERATOR)
        promoted = store.resolve(raw)
        assert promoted is not None
        assert promoted.role is Role.OPERATOR
        assert promoted.scope is Scope.READ_WRITE

        members = store.list_members("acme")
        assert members == (RoleBinding(tenant_id="acme", user_id="alice", role=Role.OPERATOR),)

        # Revocation kills resolution.
        assert store.revoke_credential(cred.credential_id) is True
        assert store.resolve(raw) is None
        assert store.revoke_credential(cred.credential_id) is False

        # Unknown tokens never resolve.
        assert store.resolve("not-issued") is None

        # Duplicates and missing principals raise typed errors.
        with pytest.raises(PrincipalAlreadyExistsError):
            store.create_tenant(tenant_id="acme", name="Acme 2")
        with pytest.raises(PrincipalAlreadyExistsError):
            store.create_user(user_id="alice", email="other@acme.test")
        with pytest.raises(PrincipalNotFoundError):
            store.issue_credential(user_id="ghost", tenant_id="acme", role=Role.OPERATOR)
        with pytest.raises(PrincipalNotFoundError):
            store.issue_credential(user_id="alice", tenant_id="missing", role=Role.OPERATOR)

        # Disabled principals stop resolving.
        raw_bob, _ = _issue_for_disabled(store)
        assert store.resolve(raw_bob) is None
    finally:
        store._engine.dispose()
        _drop_database(dsn, target)


def _issue_for_disabled(store: PostgresCredentialStore) -> tuple[str, object]:
    from universal_agent.security import Role as _Role

    store.create_user(user_id="bob", email="bob@acme.test")
    store.set_role(tenant_id="acme", user_id="bob", role=_Role.OPERATOR)
    raw, cred = store.issue_credential(user_id="bob", tenant_id="acme", role=_Role.OPERATOR)
    assert store.resolve(raw) is not None
    # Disable bob directly in storage (admin disable API is a later phase).
    from sqlalchemy import update

    from universal_agent.persistence.credentials import _USERS

    with store._engine.begin() as conn:
        conn.execute(update(_USERS).where(_USERS.c.user_id == "bob").values(status="disabled"))
    return raw, cred
