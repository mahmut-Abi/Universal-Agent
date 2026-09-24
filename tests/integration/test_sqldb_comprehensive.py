"""Comprehensive sqldb domain tests: CLI pipeline, composition, error paths,
tools, context provider, recovery rules, and multi-domain composition.

Covers paths NOT tested in test_sqldb_domain.py (which focuses on backend +
policy + evidence extractor unit tests).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from universal_agent.core import (
    ActionId,
    ObservationStatus,
    SessionId,
    TaskId,
    immutable_json,
)
from universal_agent.domains.sqldb.backend import (
    SqliteSqlBackend,
    SqlValidationError,
)
from universal_agent.domains.sqldb.cli_runtime import (
    build_sqldb_domain,
    build_sqldb_profile_service,
    profile_domain_config,
)
from universal_agent.domains.sqldb.domain import (
    SqldbContextProvider,
    SqldbDomain,
    SqldbEvidenceExtractor,
    SqlInspectTablesTool,
    SqlQueryRowsTool,
)
from universal_agent.domains.sqldb.registration import sqldb_cli_contribution
from universal_agent.evidence import EvidenceContext

# --- fixtures ---------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    p = tmp_path / "ops.db"
    conn = sqlite3.connect(str(p))
    conn.execute("CREATE TABLE deployments (name TEXT, namespace TEXT, replicas INT)")
    conn.execute("INSERT INTO deployments VALUES ('nginx', 'ua-live', 2)")
    conn.execute("INSERT INTO deployments VALUES ('redis', 'default', 1)")
    conn.commit()
    conn.close()
    return str(p)


@pytest.fixture()
def profile_with_sqldb(db_path: str, tmp_path: Path) -> Path:
    store = tmp_path / "store"
    store.mkdir()
    config = {
        "name": "sql-test",
        "version": "0.1.0",
        "description": "sqldb test profile",
        "domain": {
            "name": "sqldb",
            "version": "0.1.0",
            "backend": "sqlite",
            "settings": {"path": db_path, "timeout_seconds": 10.0},
        },
        "runtime": {
            "environment": {"environment": "staging"},
            "model": {"provider": "scripted", "name": "scripted", "timeout_seconds": 30.0},
            "store": {"backend": "file", "path": str(store)},
            "domain": {
                "name": "sqldb",
                "version": "0.1.0",
                "backend": "sqlite",
                "settings": {"path": db_path, "timeout_seconds": 10.0},
            },
        },
    }
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return p


# --- cli_runtime: profile_domain_config ---------------------------------------


def test_profile_domain_config_sqlite_ok() -> None:
    result = profile_domain_config(
        domain_backend="sqlite",
        sqldb_path="/tmp/test.db",
        timeout_seconds=10.0,
    )

    assert result["name"] == "sqldb"
    assert result["backend"] == "sqlite"
    settings = result["settings"]
    assert isinstance(settings, dict)
    assert settings["path"] == "/tmp/test.db"
    assert settings["timeout_seconds"] == 10.0


def test_profile_domain_config_sqlite_requires_path() -> None:
    with pytest.raises(ValueError, match="requires --sqldb-path"):
        profile_domain_config(
            domain_backend="sqlite",
            sqldb_path=None,
            timeout_seconds=10.0,
        )


def test_profile_domain_config_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="unsupported sqldb domain backend"):
        profile_domain_config(
            domain_backend="postgres",
            sqldb_path="/tmp/test.db",
            timeout_seconds=10.0,
        )


# --- cli_runtime: build_sqldb_domain -------------------------------------------


def test_build_sqldb_domain_returns_sqldb_domain(profile_with_sqldb: Path) -> None:
    from universal_agent.profile import ProfileConfig

    config = ProfileConfig.from_json_file(profile_with_sqldb)
    domain = build_sqldb_domain(config)

    assert domain.manifest.metadata.name == "sqldb"
    assert domain.manifest.metadata.version == "0.1.0"


def test_build_sqldb_domain_missing_path_raises(tmp_path: Path) -> None:
    from universal_agent.profile import ProfileConfig

    config = {
        "name": "test",
        "version": "0.1.0",
        "domain": {
            "name": "sqldb",
            "version": "0.1.0",
            "backend": "sqlite",
            "settings": {},
        },
        "runtime": {
            "environment": {"environment": "staging"},
            "model": {"provider": "scripted", "name": "scripted"},
            "domain": {
                "name": "sqldb",
                "version": "0.1.0",
                "backend": "sqlite",
                "settings": {},
            },
        },
    }
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(config), encoding="utf-8")
    profile_config = ProfileConfig.from_json_file(p)

    with pytest.raises(ValueError, match="non-empty 'path'"):
        build_sqldb_domain(profile_config)


# --- cli_runtime: build_sqldb_profile_service -----------------------------------


def test_build_sqldb_profile_service_returns_service(profile_with_sqldb: Path) -> None:
    from universal_agent.service import RuntimeService

    service = build_sqldb_profile_service(profile_with_sqldb)
    assert isinstance(service, RuntimeService)

    capabilities = {c.name for c in service.capabilities()}
    assert {"query_rows", "inspect_tables"} <= capabilities
    assert service.accepts_profile("sql-test")


def test_build_sqldb_profile_service_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="not found"):
        build_sqldb_profile_service(tmp_path / "nonexistent.json")


# --- registration: sqldb_cli_contribution ----------------------------------------


def test_sqldb_contribution_resolves_init_domain() -> None:
    args = argparse_namespace(
        domain_backend="sqlite",
        sqldb_path="/tmp/test.db",
        sqldb_timeout_seconds=10.0,
    )
    contribution = sqldb_cli_contribution()
    resolve = contribution.init_resolve_domain
    assert resolve is not None

    outcome = resolve(args)

    assert outcome is not None
    assert outcome.domain_name == "sqldb"
    assert outcome.domain_config["backend"] == "sqlite"


def test_sqldb_contribution_ignores_other_backends() -> None:
    args = argparse_namespace(domain_backend="kubernetes")
    contribution = sqldb_cli_contribution()
    resolve = contribution.init_resolve_domain
    assert resolve is not None
    assert resolve(args) is None


# --- domain: tools execute ---------------------------------------------------------


@pytest.mark.asyncio
async def test_sql_query_rows_tool_executes(db_path: str) -> None:
    backend = SqliteSqlBackend(db_path)
    tool = SqlQueryRowsTool(backend)

    assert tool.definition.name == "sql_query_rows"
    assert tool.definition.capabilities == ("query_rows",)
    assert tool.definition.side_effect.value == "none"

    result = await tool.execute(immutable_json({"sql": "SELECT * FROM deployments"}))
    assert result["row_count"] == 2


@pytest.mark.asyncio
async def test_sql_inspect_tables_tool_executes(db_path: str) -> None:
    backend = SqliteSqlBackend(db_path)
    tool = SqlInspectTablesTool(backend)

    assert tool.definition.name == "sql_inspect_tables"
    assert tool.definition.capabilities == ("inspect_tables",)
    assert tool.definition.side_effect.value == "none"

    result = await tool.execute(immutable_json({}))
    assert result["table_count"] >= 1


# --- domain: context provider -------------------------------------------------------


def test_sqldb_context_provider_returns_fragment() -> None:
    provider = SqldbContextProvider()

    assert provider.name == "sqldb-context"

    state = object()  # provide() doesn't actually use state
    fragments = provider.provide(state)  # type: ignore[arg-type]

    assert len(fragments) == 1


# --- domain: recovery rules -----------------------------------------------------------


def test_sqldb_recovery_rules_exist() -> None:
    domain = SqldbDomain(SqliteSqlBackend(":memory:"))

    rules = domain.recovery_rules()
    assert any(r.name == "sqldb-timeout-retry" for r in rules)


# --- domain: evidence extractor with observation ---------------------------------------


def test_sqldb_evidence_extractor_empty_on_failure() -> None:
    from datetime import UTC, datetime

    from universal_agent.core import Observation, Task

    task = Task(description="query", required_criteria=("sql_query_ok",))
    observation = Observation(
        id=TaskId("obs-1"),
        action_id=ActionId("action-1"),
        task_id=TaskId("task-1"),
        source="sqldb-evidence",
        status=ObservationStatus.FAILED,
        data=immutable_json({}),
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    context = EvidenceContext(
        session_id=SessionId("session-1"),
        task=task,
        observation=observation,
    )

    extractor = SqldbEvidenceExtractor()
    assert extractor.extract(context) == ()


# --- domain: full pipeline through DomainLoader ---------------------------------------


def test_sqldb_domain_through_domain_loader(db_path: str) -> None:
    """Verify the sqldb domain passes DomainLoader validation end-to-end."""
    from universal_agent.domain import DomainLoader

    domain = SqldbDomain(SqliteSqlBackend(db_path))
    runtime = DomainLoader().load(domain)

    assert runtime.manifest.metadata.name == "sqldb"
    caps = {c.name for c in runtime.capabilities}
    assert {"query_rows", "inspect_tables"} <= caps


# --- multi-domain: kubernetes + sqldb composition ---------------------------------------


def test_kubernetes_sqldb_composition_service(profile_with_sqldb: Path) -> None:
    """Verify that a profile with both kubernetes and sqldb domains composes
    into a single RuntimeService with 11 capabilities (9 k8s + 2 sqldb)."""
    from universal_agent.domains.profile_service import (
        _build_composed_kubernetes_sqldb_service,
    )

    # Create combined profile
    config = json.loads(profile_with_sqldb.read_text(encoding="utf-8"))
    k8s_domain = {
        "name": "kubernetes",
        "version": "0.2.0",
        "backend": "fake",
        "settings": {"default_namespace": "default"},
    }
    config["domains"] = [k8s_domain, config["domain"]]
    config["runtime"]["domains"] = [k8s_domain, config["domain"]]
    combined_path = profile_with_sqldb.parent / "combined.json"
    combined_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    service = _build_composed_kubernetes_sqldb_service(combined_path)

    caps = {c.name for c in service.capabilities()}
    assert "inspect_workload" in caps  # kubernetes
    assert "query_rows" in caps  # sqldb
    assert len(caps) >= 11


# --- profile add-domain with sqldb -----------------------------------------------------


def test_profile_add_domain_with_sqldb(profile_with_sqldb: Path) -> None:
    """Verify that `profile add-domain --domain-backend sqlite` correctly adds
    the sqldb domain to a single-domain profile."""
    from universal_agent_cli.profile_domains import _merge_domain

    payload: dict[str, object] = {
        "name": "test",
        "version": "0.1.0",
        "domain": {
            "name": "kubernetes",
            "version": "0.2.0",
            "backend": "fake",
        },
        "runtime": {
            "domain": {
                "name": "kubernetes",
                "version": "0.2.0",
                "backend": "fake",
            },
        },
    }

    domain_config: dict[str, object] = {
        "name": "sqldb",
        "version": "0.1.0",
        "backend": "sqlite",
        "settings": {"path": "/tmp/test.db"},
    }

    result = _merge_domain(payload, "sqldb", domain_config, {})

    assert result is True
    domains = payload.get("domains")
    assert isinstance(domains, list)
    names = [d.get("name") for d in domains if isinstance(d, dict)]
    assert "kubernetes" in names  # migrated from primary
    assert "sqldb" in names  # added

    runtime = payload.get("runtime")
    assert isinstance(runtime, dict)
    runtime_domains = runtime.get("domains")
    assert isinstance(runtime_domains, list)
    runtime_names = [d.get("name") for d in runtime_domains if isinstance(d, dict)]
    assert "kubernetes" in runtime_names
    assert "sqldb" in runtime_names


def test_profile_add_domain_rejects_duplicate(profile_with_sqldb: Path) -> None:
    from universal_agent_cli.profile_domains import _merge_domain

    payload: dict[str, object] = {
        "name": "test",
        "version": "0.1.0",
        "domain": {"name": "sqldb", "version": "0.1.0", "backend": "sqlite"},
        "runtime": {
            "domain": {"name": "sqldb", "version": "0.1.0", "backend": "sqlite"},
        },
    }

    domain_config: dict[str, object] = {
        "name": "sqldb",
        "version": "0.1.0",
        "backend": "sqlite",
        "settings": {"path": "/tmp/test.db"},
    }

    result = _merge_domain(payload, "sqldb", domain_config, {})
    assert result is False  # already present


# --- error paths -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sqldb_backend_empty_database_query_fails(tmp_path: Path) -> None:
    """A query referencing a table that doesn't exist should fail."""
    empty_db = tmp_path / "empty.db"
    connection = sqlite3.connect(str(empty_db))
    connection.commit()
    connection.close()

    backend = SqliteSqlBackend(str(empty_db))

    with pytest.raises(SqlValidationError, match="no such table"):
        await backend.query_rows(immutable_json({"sql": "SELECT * FROM deployments"}))


@pytest.mark.asyncio
async def test_sqldb_backend_invalid_sql_syntax(db_path: str) -> None:
    backend = SqliteSqlBackend(db_path)

    with pytest.raises(SqlValidationError, match="syntax error|no such"):
        await backend.query_rows(immutable_json({"sql": "SELECT not_a_column FROM deployments"}))


@pytest.mark.asyncio
async def test_sqldb_backend_query_nonexistent_table(db_path: str) -> None:
    backend = SqliteSqlBackend(db_path)

    with pytest.raises(SqlValidationError, match="no such table"):
        await backend.query_rows(immutable_json({"sql": "SELECT * FROM nonexistent_table"}))


# --- helpers --------------------------------------------------------------------------


def argparse_namespace(**kwargs: object) -> Any:
    import argparse

    return argparse.Namespace(**kwargs)


# Make json available for the composition test

# Make Any available
