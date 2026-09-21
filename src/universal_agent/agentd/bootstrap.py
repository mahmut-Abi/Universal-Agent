"""Shared agentd server bootstrap helpers.

Used by both ``python -m universal_agent.agentd`` and the CLI ``agent serve``
command so the admin plane and audit sink are configured identically on both
launch paths (UA-LIVE-2026-09-21 Q9).
"""

from __future__ import annotations

import os

from universal_agent.security import AuditRecorder, CredentialAdminStore


def build_admin_store(
    store: str | None,
    url_env: str | None,
) -> CredentialAdminStore | None:
    """Resolve the credential admin store from ``--admin-store`` options.

    memory: in-process, not durable (dev/test). postgres: durable schema v3
    principal tables; the DSN is read from the environment variable named by
    ``url_env`` (never from config files or argv).
    """

    if store is None:
        if url_env is not None:
            raise ValueError("--admin-store-url-env requires --admin-store postgres")
        return None
    if store == "memory":
        from universal_agent.security import InMemoryCredentialStore

        return InMemoryCredentialStore()
    if store == "postgres":
        if url_env is None:
            raise ValueError("--admin-store postgres requires --admin-store-url-env")
        url = os.environ.get(url_env)
        if not url:
            raise ValueError(
                f"admin store url_env {url_env!r} is not set in the environment; "
                "export the Postgres DSN"
            )
        try:
            from universal_agent.persistence.credentials import PostgresCredentialStore
        except ImportError as exc:
            raise ValueError(
                "--admin-store postgres requires the optional 'postgres' extra: "
                "pip install 'universal-agent-runtime[postgres]'"
            ) from exc
        return PostgresCredentialStore(url)
    raise ValueError(f"unsupported admin store backend: {store}")


def build_audit_recorder(
    audit_log: str | None,
    *,
    admin_store: CredentialAdminStore | None,
) -> AuditRecorder | None:
    """Resolve the audit sink: JSONL file when configured, otherwise an
    in-memory ring when the admin plane is enabled, else disabled."""

    if audit_log is not None:
        from universal_agent.security import FileAuditRecorder

        return FileAuditRecorder(audit_log)
    if admin_store is not None:
        from universal_agent.security import InMemoryAuditRecorder

        return InMemoryAuditRecorder()
    return None
