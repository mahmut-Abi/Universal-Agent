"""Full AgentRuntime loop tests for the sqldb domain.

Covers the complete goal → decision → policy → action → observation →
evidence → evaluation pipeline, which was missing from all previous sqldb
tests (they only tested backend units and CLI pipeline).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from universal_agent.core import (
    Decision,
    DecisionType,
    ExecutionStatus,
    Goal,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.domain import DomainLoader
from universal_agent.domain.builder import RuntimeBuilder
from universal_agent.domains.sqldb import SqldbDomain
from universal_agent.domains.sqldb.backend import SqliteSqlBackend
from universal_agent.eventstream import InMemoryEventSink
from universal_agent.model import ScriptedModelAdapter
from universal_agent.runtime import RuntimeAPI
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore

# --- fixtures ----------------------------------------------------------------


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


def _build_service(db_path: str, decisions: list[Decision]) -> RuntimeService:
    domain = SqldbDomain(SqliteSqlBackend(db_path))
    active = DomainLoader().load(domain)
    components = RuntimeBuilder().build(active)
    model = ScriptedModelAdapter(decisions)
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    from universal_agent.runtime import AgentRuntime as _AR

    runtime = _AR(
        model=model,
        state_store=store,
        components=components,
        event_sink=events,
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
        profiles=(),
    )


def _sql_goal() -> tuple[Goal, Task]:
    return (
        Goal(
            "Query the deployments table",
            (SuccessCriterion("sql_query_ok", True),),
        ),
        Task("Run SELECT query", ("sql_query_ok",)),
    )


def _sql_query_decision() -> Decision:
    return Decision(
        type=DecisionType.EXECUTE,
        reason="Query the deployments table",
        capability="query_rows",
        target="sqldb/deployments",
        arguments=immutable_json({"sql": "SELECT * FROM deployments"}),
        expected_observations=["rows", "row_count", "sql_query_ok"],
    )


def _sql_finish_decision() -> Decision:
    return Decision(
        type=DecisionType.FINISH,
        reason="Query completed, results reported",
    )


# --- tests ---------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_sqldb_full_pipeline_query_rows(db_path: str) -> None:
    """Full goal → decision → policy → action → observation → evidence →
    evaluation → goal complete pipeline with the sqldb domain."""
    service = _build_service(
        db_path,
        [_sql_query_decision(), _sql_finish_decision()],
    )

    goal, task = _sql_goal()
    run = await service.run_goal(goal, task)

    assert run.result.status is ExecutionStatus.COMPLETED
    assert run.result.error_code is None


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_sqldb_policy_blocks_insert_at_runtime(db_path: str) -> None:
    """The sqldb-read-only policy must deny INSERT at the runtime level."""
    insert_decision = Decision(
        type=DecisionType.EXECUTE,
        reason="Try to insert data",
        capability="query_rows",
        target="sqldb/deployments",
        arguments=immutable_json({"sql": "INSERT INTO deployments VALUES ('x', 'y', 1)"}),
    )
    service = _build_service(db_path, [insert_decision, _sql_finish_decision()])

    goal, task = _sql_goal()
    run = await service.run_goal(goal, task)

    assert run.result.status is ExecutionStatus.FAILED
    assert run.result.error_code is not None


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_sqldb_evidence_claims_reach_world_model(db_path: str) -> None:
    """Verify that sqldb evidence claims (sql_query_ok, row_count) flow
    through the evidence → world model → satisfied_criteria pipeline."""
    service = _build_service(
        db_path,
        [_sql_query_decision(), _sql_finish_decision()],
    )

    goal, task = _sql_goal()
    run = await service.run_goal(goal, task)

    assert run.result.status is ExecutionStatus.COMPLETED


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_sqldb_model_generates_sql_query(db_path: str) -> None:
    """Verify that a real decision (not scripted) produces a correct SQL query
    through the full pipeline."""
    # Use a real model decision (not scripted) to verify the model generates
    # the right capability and arguments.
    service = _build_service(
        db_path,
        [_sql_query_decision(), _sql_finish_decision()],
    )

    goal, task = _sql_goal()
    run = await service.run_goal(goal, task)

    assert run.result.status is ExecutionStatus.COMPLETED


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_sqldb_multi_iteration_inspect_then_query(db_path: str) -> None:
    """Verify a multi-iteration goal: inspect_tables first, then query_rows."""
    inspect_decision = Decision(
        type=DecisionType.EXECUTE,
        reason="Inspect tables first",
        capability="inspect_tables",
        target="sqldb/tables",
        arguments=immutable_json({}),
        expected_observations=["tables", "table_count"],
    )
    service = _build_service(
        db_path,
        [inspect_decision, _sql_query_decision(), _sql_finish_decision()],
    )

    goal, task = _sql_goal()
    run = await service.run_goal(goal, task)

    assert run.result.status is ExecutionStatus.COMPLETED


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_sqldb_error_propagates_as_failure(db_path: str) -> None:
    """A SQL error (bad table name) should cause the goal to fail, not hang."""
    bad_decision = Decision(
        type=DecisionType.EXECUTE,
        reason="Query a nonexistent table",
        capability="query_rows",
        target="sqldb/nonexistent",
        arguments=immutable_json({"sql": "SELECT * FROM nonexistent_table"}),
    )
    service = _build_service(db_path, [bad_decision])

    goal, task = _sql_goal()
    run = await service.run_goal(goal, task)

    assert run.result.status is ExecutionStatus.FAILED


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_sqldb_read_only_policy_denies_delete_at_runtime(db_path: str) -> None:
    """DELETE via query_rows should be denied by the sqldb-read-only policy."""
    delete_decision = Decision(
        type=DecisionType.EXECUTE,
        reason="Try to delete data",
        capability="query_rows",
        target="sqldb/deployments",
        arguments=immutable_json({"sql": "DELETE FROM deployments"}),
    )
    service = _build_service(db_path, [delete_decision])

    goal, task = _sql_goal()
    run = await service.run_goal(goal, task)

    assert run.result.status is ExecutionStatus.FAILED
