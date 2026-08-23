from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent import (
    AgentProfile,
    Decision,
    DecisionType,
    DomainConfig,
    Goal,
    ProfileConfig,
    RuntimeConfig,
    RuntimeHost,
    ScriptedModelAdapter,
    StoreConfig,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core import ExecutionStatus, GoalStatus, JsonMapping, SessionId
from universal_agent.distributed import DistributedLockOwnerId, WorkerId, WorkItemStatus
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class HostRemediationBackend:
    def __init__(self) -> None:
        self.inspect_calls: list[str] = []
        self.mutation_calls = 0
        self._scaled = False

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls.append(capability)
        if capability == "inspect_workload":
            return immutable_json(
                {
                    "resource": "deployment/example",
                    "healthy": self._scaled,
                    "desired_replicas": 3,
                    "ready_replicas": 3 if self._scaled else 1,
                    "verification_observed": self._scaled,
                }
            )
        if capability == "inspect_pod":
            return immutable_json(
                {
                    "resource": "pod/example-123",
                    "root_cause": "under_replicated",
                }
            )
        raise AssertionError(f"unexpected capability: {capability}")

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        self.mutation_calls += 1
        self._scaled = True
        return immutable_json(
            {
                "resource": "deployment/example",
                "mutation_applied": True,
                "replicas": 3,
            }
        )


def inspect_workload(*expected: str) -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload through RuntimeHost",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=expected,
    )


def inspect_pod() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect pod through RuntimeHost",
        capability="inspect_pod",
        target="pod/example-123",
        arguments=immutable_json({"name": "example-123"}),
        expected_observations=("root_cause",),
    )


def scale_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Scale workload through RuntimeHost",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 3}),
        expected_observations=("mutation_applied",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "RuntimeHost verified workload health")


def remediation_goal_task() -> tuple[Goal, Task]:
    return (
        Goal(
            "Restore workload health through configured host", (SuccessCriterion("healthy", True),)
        ),
        Task("Inspect workload", ()),
    )


def configured_host(
    path: Path,
    backend: HostRemediationBackend,
    decisions: list[Decision],
    *,
    store: StoreConfig | None = None,
) -> RuntimeHost:
    return RuntimeHost.build(
        config=RuntimeConfig(
            environment=immutable_json({"environment": "production"}),
            store=StoreConfig.file(str(path)) if store is None else store,
            domain=DomainConfig("kubernetes", "0.2.0"),
        ),
        model=ScriptedModelAdapter(decisions),
        domain=KubernetesRemediationDomain(backend, backend),
    )


@pytest.mark.asyncio
async def test_runtime_host_assembles_configured_sqlite_backed_service(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    backend = HostRemediationBackend()
    first = configured_host(
        db_path,
        backend,
        [inspect_workload("healthy"), inspect_pod(), scale_workload()],
        store=StoreConfig.sqlite(str(db_path)),
    )

    waiting = await first.service.run_goal(*remediation_goal_task())

    assert waiting.result.status is ExecutionStatus.WAITING
    assert waiting.session.pending_action is not None
    assert db_path.exists()

    second = configured_host(
        db_path,
        backend,
        [inspect_workload("verification_observed", "healthy"), finish()],
        store=StoreConfig.sqlite(str(db_path)),
    )
    completed = await second.runtime_api.resume_session(waiting.result.session_id, confirmed=True)
    events = await second.service.list_events(waiting.result.session_id)

    assert completed.result.status is ExecutionStatus.COMPLETED
    assert completed.session.goal_status is GoalStatus.COMPLETED
    assert [event.type for event in events][-1] == "GoalCompleted"


def production_profile(path: Path) -> ProfileConfig:
    return ProfileConfig.from_mapping(
        {
            "name": "production-operator",
            "version": "1.0.0",
            "description": "Production Kubernetes operator",
            "domain": {"name": "kubernetes", "version": "0.2.0"},
            "runtime": {
                "environment": {"environment": "production"},
                "store": {"backend": "file", "path": str(path)},
                "domain": {"name": "kubernetes", "version": "0.2.0"},
            },
        }
    )


@pytest.mark.asyncio
async def test_runtime_host_assembles_configured_file_backed_service(tmp_path: Path) -> None:
    backend = HostRemediationBackend()
    first = configured_host(
        tmp_path,
        backend,
        [inspect_workload("healthy"), inspect_pod(), scale_workload()],
    )

    ready = first.service.ready()
    waiting = await first.service.run_goal(*remediation_goal_task())

    assert ready.ready

    distributed_snapshot = first.service.distributed_snapshot()
    distributed_health = first.service.distributed_health()

    assert first.distributed_coordinator is not None
    assert distributed_snapshot is not None
    assert distributed_health is not None
    assert distributed_snapshot.work_queue.total_count == 0
    assert distributed_health.status.value == "ok"
    assert first.domain_identity.name == "kubernetes"
    assert waiting.result.status is ExecutionStatus.WAITING
    assert waiting.session.pending_action is not None
    assert backend.mutation_calls == 0
    assert list((tmp_path / "sessions").glob("*.json"))
    assert (tmp_path / "events.jsonl").exists()

    second = configured_host(
        tmp_path,
        backend,
        [inspect_workload("verification_observed", "healthy"), finish()],
    )
    completed = await second.runtime_api.resume_session(waiting.result.session_id, confirmed=True)
    events = await second.service.list_events(waiting.result.session_id)

    assert completed.result.status is ExecutionStatus.COMPLETED
    assert completed.session.goal_status is GoalStatus.COMPLETED
    assert completed.session.pending_action is None
    assert backend.mutation_calls == 1
    assert [event.type for event in events][-1] == "GoalCompleted"


def test_runtime_host_rejects_configured_domain_mismatch(tmp_path: Path) -> None:
    backend = HostRemediationBackend()

    with pytest.raises(ValueError, match="configured domain coding does not match kubernetes"):
        RuntimeHost.build(
            config=RuntimeConfig(
                store=StoreConfig.file(str(tmp_path)),
                domain=DomainConfig("coding", "0.2.0"),
            ),
            model=ScriptedModelAdapter([]),
            domain=KubernetesRemediationDomain(backend, backend),
        )


def test_runtime_host_uses_configured_file_backed_distributed_queue(tmp_path: Path) -> None:
    backend = HostRemediationBackend()
    queue_path = tmp_path / "coordination" / "work-queue.json"
    config = RuntimeConfig(
        environment=immutable_json({"environment": "production"}),
        distributed_queue=StoreConfig.file(str(queue_path)),
        domain=DomainConfig("kubernetes", "0.2.0"),
    )
    first = RuntimeHost.build(
        config=config,
        model=ScriptedModelAdapter([]),
        domain=KubernetesRemediationDomain(backend, backend),
    )

    first.service.distributed_schedule_session(SessionId("session-1"), priority=7)
    second = RuntimeHost.build(
        config=config,
        model=ScriptedModelAdapter([]),
        domain=KubernetesRemediationDomain(backend, backend),
    )
    snapshot = second.service.distributed_snapshot()

    assert queue_path.exists()
    assert snapshot is not None
    assert snapshot.work_queue.queued_count == 1
    assert snapshot.work_queue.items[0].status is WorkItemStatus.QUEUED
    assert snapshot.work_queue.items[0].session_id == SessionId("session-1")
    assert snapshot.work_queue.items[0].priority == 7


def test_runtime_host_uses_configured_file_backed_distributed_locks(tmp_path: Path) -> None:
    backend = HostRemediationBackend()
    locks_path = tmp_path / "coordination" / "distributed-locks.json"
    config = RuntimeConfig(
        environment=immutable_json({"environment": "production"}),
        distributed_locks=StoreConfig.file(str(locks_path)),
        domain=DomainConfig("kubernetes", "0.2.0"),
    )
    first = RuntimeHost.build(
        config=config,
        model=ScriptedModelAdapter([]),
        domain=KubernetesRemediationDomain(backend, backend),
    )

    first.distributed_coordinator.acquire_lock(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
    )
    second = RuntimeHost.build(
        config=config,
        model=ScriptedModelAdapter([]),
        domain=KubernetesRemediationDomain(backend, backend),
    )
    snapshot = second.service.distributed_snapshot()

    assert locks_path.exists()
    assert snapshot is not None
    assert len(snapshot.locks) == 1
    assert snapshot.locks[0].lock_key == "session/session-1"
    assert snapshot.locks[0].owner_id == DistributedLockOwnerId("worker-a")


def test_runtime_host_uses_configured_file_backed_worker_registry(tmp_path: Path) -> None:
    backend = HostRemediationBackend()
    workers_path = tmp_path / "coordination" / "workers.json"
    config = RuntimeConfig(
        environment=immutable_json({"environment": "production"}),
        distributed_workers=StoreConfig.file(str(workers_path)),
        domain=DomainConfig("kubernetes", "0.2.0"),
    )
    first = RuntimeHost.build(
        config=config,
        model=ScriptedModelAdapter([]),
        domain=KubernetesRemediationDomain(backend, backend),
    )

    first.distributed_coordinator.register_worker(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
    )
    second = RuntimeHost.build(
        config=config,
        model=ScriptedModelAdapter([]),
        domain=KubernetesRemediationDomain(backend, backend),
    )
    snapshot = second.service.distributed_snapshot()

    assert workers_path.exists()
    assert snapshot is not None
    assert snapshot.workers.total_count == 1
    assert snapshot.workers.workers[0].worker_id == WorkerId("worker-a")
    assert snapshot.workers.workers[0].capabilities == ("agent_session",)


def test_runtime_host_from_profile_exposes_profile_catalog(tmp_path: Path) -> None:
    backend = HostRemediationBackend()
    profile = production_profile(tmp_path).to_profile()

    host = RuntimeHost.from_profile(
        profile=profile,
        model=ScriptedModelAdapter([]),
        domain=KubernetesRemediationDomain(backend, backend),
    )

    profiles = host.service.profiles()

    assert host.profile == profile
    assert host.config == profile.runtime
    assert profiles[0].name == "production-operator"
    assert profiles[0].domain_name == "kubernetes"


def test_runtime_host_rejects_profile_domain_mismatch(tmp_path: Path) -> None:
    backend = HostRemediationBackend()
    profile = AgentProfile(
        "production-operator",
        "1.0.0",
        "",
        DomainConfig("coding", "0.2.0"),
        RuntimeConfig(
            store=StoreConfig.file(str(tmp_path)),
            domain=DomainConfig("kubernetes", "0.2.0"),
        ),
        (DomainConfig("coding", "0.2.0"),),
    )

    with pytest.raises(
        ValueError,
        match=r"profile domains coding@0\.2\.0 do not match kubernetes@0\.2\.0",
    ):
        RuntimeHost.from_profile(
            profile=profile,
            model=ScriptedModelAdapter([]),
            domain=KubernetesRemediationDomain(backend, backend),
        )
