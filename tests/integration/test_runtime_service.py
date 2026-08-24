from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DistributedLockOwnerId,
    DistributedRuntimeCoordinator,
    DomainConfig,
    DomainLoader,
    Goal,
    InMemoryEventSink,
    InMemoryStateStore,
    ModelUsage,
    RuntimeAPI,
    RuntimeBuilder,
    RuntimeConfig,
    RuntimeService,
    ScriptedModelAdapter,
    SuccessCriterion,
    Task,
    WorkerId,
    WorkerRunStatus,
    WorkerStatus,
    WorkItemStatus,
    WorkKind,
    immutable_json,
)
from universal_agent.core import (
    ActionId,
    DomainIdentity,
    ExecutionStatus,
    GoalId,
    GoalStatus,
    JsonMapping,
    RiskLevel,
    RuntimeEvent,
    SessionId,
    SideEffect,
    TaskId,
)
from universal_agent.domain import (
    DomainPackage,
    DomainPackageCompatibility,
    DomainPackageManifest,
    DomainPackageRegistry,
    RuntimeComponents,
)
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class ServiceBackend:
    def __init__(self) -> None:
        self.inspect_calls = 0
        self.mutation_calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls += 1
        assert capability == "inspect_workload"
        return immutable_json(
            {
                "resource": "deployment/example",
                "healthy": True,
                "kind": "Deployment",
                "relation:owns": ["pod/example-1"],
            }
        )

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.mutation_calls += 1
        assert capability == "scale_workload"
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def scale_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Scale workload",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 3}),
        expected_observations=("mutation_applied",),
    )


def inspect_named_workload(name: str) -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        f"Inspect workload {name}",
        capability="inspect_workload",
        target=f"deployment/{name}",
        arguments=immutable_json({"name": name}),
        expected_observations=("healthy",),
    )


def scale_named_workload(name: str) -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        f"Scale workload {name}",
        capability="scale_workload",
        target=f"deployment/{name}",
        arguments=immutable_json({"name": name, "namespace": "default", "replicas": 3}),
        expected_observations=("mutation_applied",),
    )


def wait() -> Decision:
    return Decision(DecisionType.WAIT, "pause before distributed runtime service resume")


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Required evidence is present")


def goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )


def build_service(
    decisions: list[Decision],
    *,
    usage: list[ModelUsage] | None = None,
    domain_packages: DomainPackageRegistry | None = None,
    distributed_coordinator: DistributedRuntimeCoordinator | None = None,
    environment: str = "staging",
) -> tuple[RuntimeService, ServiceBackend]:
    backend = ServiceBackend()
    active = DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    components = RuntimeBuilder().build(active)
    api = build_api(components, decisions, usage=usage, environment=environment)
    return RuntimeService(
        runtime_api=api,
        components=components,
        domain_packages=domain_packages,
        distributed_coordinator=distributed_coordinator,
    ), backend


def test_runtime_service_config_redacts_sensitive_environment_values() -> None:
    backend = ServiceBackend()
    active = DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    components = RuntimeBuilder().build(active)
    config = RuntimeConfig(
        environment=immutable_json(
            {
                "environment": "production",
                "api_token": "token-value",
                "nested": {"password": "pw-value", "safe": "visible"},
                "items": [{"secret_key": "secret-value", "name": "kept"}],
            }
        ),
        domain=DomainConfig("kubernetes", "0.2.0"),
    )
    service = RuntimeService(
        runtime_api=build_api(components, []),
        components=components,
        config=config,
    )

    assert service.config().environment == {
        "environment": "production",
        "api_token": "<redacted>",
        "nested": {"password": "<redacted>", "safe": "visible"},
        "items": [{"secret_key": "<redacted>", "name": "kept"}],
    }


def test_runtime_service_config_exposes_domain_backend_settings() -> None:
    backend = ServiceBackend()
    active = DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    components = RuntimeBuilder().build(active)
    config = RuntimeConfig(
        domain=DomainConfig(
            "kubernetes",
            "0.2.0",
            backend="kubectl",
            settings=immutable_json(
                {
                    "default_namespace": "prod",
                    "api_token": "secret-token",
                }
            ),
        ),
    )
    service = RuntimeService(
        runtime_api=build_api(components, []),
        components=components,
        config=config,
    )

    domain = service.config().domains[0]

    assert domain.backend == "kubectl"
    assert domain.settings == {
        "default_namespace": "prod",
        "api_token": "<redacted>",
    }


def package_registry() -> DomainPackageRegistry:
    package = DomainPackage(
        manifest=DomainPackageManifest(
            api_version="agent.nantian.dev/v1alpha1",
            kind="DomainPackage",
            name="kubernetes",
            version="0.2.0",
            description="Packaged Kubernetes runtime metadata",
            author="Runtime Team",
            entrypoint="universal_agent.domains.kubernetes:KubernetesRemediationDomain",
            ontology=("Deployment", "Pod"),
            capabilities=("inspect_workload", "scale_workload"),
            tools=("kubernetes_inspect_workload", "kubernetes_scale_workload"),
            policies=("kubernetes-scale-safety",),
            procedures=("diagnose_unhealthy_workload",),
            knowledge=("kubernetes readiness",),
            evaluators=("workload-health",),
            context_providers=("kubernetes_context",),
            dependencies=(DomainIdentity("observability", "1.0.0"),),
            required_tools=("kubernetes_api",),
            compatibility=DomainPackageCompatibility(
                runtime_api=">=0.1,<1",
                domain_api="agent.nantian.dev/v1alpha1",
            ),
            security=immutable_json({"side_effects": "reversible"}),
            tags=("kubernetes", "ops"),
        ),
        root_path=Path("/domains/kubernetes"),
        manifest_path=Path("/domains/kubernetes/manifest.json"),
    )
    return DomainPackageRegistry((package,))


def build_api(
    components: RuntimeComponents,
    decisions: list[Decision],
    *,
    usage: list[ModelUsage] | None = None,
    environment: str = "staging",
) -> RuntimeAPI:
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions, usage=usage or ()),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": environment}),
    )
    return RuntimeAPI(runtime=runtime, session_store=store, event_reader=events)


def build_api_with_stores(
    components: RuntimeComponents,
    decisions: list[Decision],
    *,
    usage: list[ModelUsage] | None = None,
) -> tuple[RuntimeAPI, InMemoryStateStore, InMemoryEventSink]:
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions, usage=usage or ()),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "staging"}),
    )
    return RuntimeAPI(runtime=runtime, session_store=store, event_reader=events), store, events


def test_runtime_service_exposes_optional_distributed_runtime_views() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service, _ = build_service([])
    coordinator = DistributedRuntimeCoordinator()
    coordinator.scheduler.schedule_session(SessionId("session-1"), available_at=now)
    coordinator.workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=30,
        now=now,
    )
    active = DomainLoader().load(KubernetesRemediationDomain(ServiceBackend(), ServiceBackend()))
    components = RuntimeBuilder().build(active)
    distributed_service = RuntimeService(
        runtime_api=build_api(components, []),
        components=components,
        distributed_coordinator=coordinator,
    )

    assert service.distributed_snapshot() is None
    assert service.distributed_health(now=now) is None
    snapshot = distributed_service.distributed_snapshot()
    health = distributed_service.distributed_health(now=now)

    assert snapshot is not None
    assert health is not None
    assert snapshot.work_queue.queued_count == 1
    assert health.status.value == "ok"


def test_runtime_service_exposes_distributed_session_scheduling() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service, _ = build_service([])
    coordinator = DistributedRuntimeCoordinator()
    coordinator.workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=30,
        now=now,
    )
    active = DomainLoader().load(KubernetesRemediationDomain(ServiceBackend(), ServiceBackend()))
    components = RuntimeBuilder().build(active)
    distributed_service = RuntimeService(
        runtime_api=build_api(components, []),
        components=components,
        distributed_coordinator=coordinator,
    )

    result = distributed_service.distributed_schedule_session(
        SessionId("session-1"),
        payload=immutable_json({"goal": "verify workload health"}),
        priority=3,
        max_attempts=2,
        now=now,
    )

    assert service.distributed_schedule_session(SessionId("session-1")) is None
    assert result is not None
    assert result.scheduled_work_item.session_id == SessionId("session-1")
    assert result.scheduled_work_item.payload["goal"] == "verify workload health"
    assert result.scheduled_work_item.priority == 3
    assert result.snapshot.work_queue.queued_count == 1


def test_runtime_service_exposes_distributed_worker_lifecycle() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service, _ = build_service([])
    coordinator = DistributedRuntimeCoordinator()
    active = DomainLoader().load(KubernetesRemediationDomain(ServiceBackend(), ServiceBackend()))
    components = RuntimeBuilder().build(active)
    distributed_service = RuntimeService(
        runtime_api=build_api(components, []),
        components=components,
        distributed_coordinator=coordinator,
    )

    registered = distributed_service.distributed_register_worker(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        metadata=immutable_json({"host": "local"}),
        ttl_seconds=30,
        now=now,
    )
    heartbeat = distributed_service.distributed_heartbeat_worker(
        WorkerId("worker-a"),
        ttl_seconds=60,
        now=now + timedelta(seconds=5),
    )
    draining = distributed_service.distributed_drain_worker(
        WorkerId("worker-a"),
        reason="finish current lease",
        now=now + timedelta(seconds=6),
    )
    offline = distributed_service.distributed_mark_worker_offline(
        WorkerId("worker-a"),
        reason="shutdown complete",
        now=now + timedelta(seconds=7),
    )

    assert service.distributed_register_worker(WorkerId("worker-a")) is None
    assert registered is not None
    assert heartbeat is not None
    assert draining is not None
    assert offline is not None
    assert registered.worker.metadata["host"] == "local"
    assert heartbeat.worker.lease_expires_at == now + timedelta(seconds=65)
    assert draining.worker.status is WorkerStatus.DRAINING
    assert offline.worker.status is WorkerStatus.OFFLINE
    assert offline.snapshot.workers.offline_count == 1


def test_runtime_service_exposes_distributed_lock_lifecycle() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service, _ = build_service([])
    coordinator = DistributedRuntimeCoordinator()
    active = DomainLoader().load(KubernetesRemediationDomain(ServiceBackend(), ServiceBackend()))
    components = RuntimeBuilder().build(active)
    distributed_service = RuntimeService(
        runtime_api=build_api(components, []),
        components=components,
        distributed_coordinator=coordinator,
    )

    acquired = distributed_service.distributed_acquire_lock(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=30,
        metadata=immutable_json({"reason": "run session"}),
        now=now,
    )
    assert acquired is not None
    renewed = distributed_service.distributed_heartbeat_lock(
        acquired.lock.lease_id,
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=60,
        now=now + timedelta(seconds=5),
    )
    released = distributed_service.distributed_release_lock(
        acquired.lock.lease_id,
        owner_id=DistributedLockOwnerId("worker-a"),
        now=now + timedelta(seconds=6),
    )

    assert (
        service.distributed_acquire_lock(
            lock_key="session/session-1",
            owner_id=DistributedLockOwnerId("worker-a"),
        )
        is None
    )
    assert renewed is not None
    assert released is not None
    assert acquired.lock.metadata["reason"] == "run session"
    assert renewed.lock.lease_expires_at == now + timedelta(seconds=65)
    assert released.snapshot.locks == ()


def test_runtime_service_exposes_distributed_expiry_sweep() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()
    coordinator.queue.enqueue(kind="agent_session", available_at=now)
    leased = coordinator.queue.lease(worker_id=WorkerId("worker-a"), ttl_seconds=1, now=now)
    active = DomainLoader().load(KubernetesRemediationDomain(ServiceBackend(), ServiceBackend()))
    components = RuntimeBuilder().build(active)
    service = RuntimeService(
        runtime_api=build_api(components, []),
        components=components,
        distributed_coordinator=coordinator,
    )

    result = service.distributed_expire(now=now + timedelta(seconds=2))

    assert result is not None
    assert [item.work_item_id for item in result.expired_work_items] == [leased.work_item_id]
    assert result.snapshot.work_queue.queued_count == 1
    second = service.distributed_expire(now=now + timedelta(seconds=3))
    assert second is not None
    assert second.expired_work_items == ()


def test_runtime_service_exposes_distributed_work_item_cancellation() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service, _ = build_service([])
    coordinator = DistributedRuntimeCoordinator()
    scheduled = coordinator.scheduler.schedule_session(SessionId("session-1"), available_at=now)
    coordinator.workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=30,
        now=now,
    )
    active = DomainLoader().load(KubernetesRemediationDomain(ServiceBackend(), ServiceBackend()))
    components = RuntimeBuilder().build(active)
    distributed_service = RuntimeService(
        runtime_api=build_api(components, []),
        components=components,
        distributed_coordinator=coordinator,
    )

    result = distributed_service.distributed_cancel_work_item(
        scheduled.work_item_id,
        reason="operator cancelled distributed work",
        now=now + timedelta(seconds=1),
    )

    assert service.distributed_cancel_work_item(scheduled.work_item_id) is None
    assert result is not None
    assert result.cancelled_work_item.status is WorkItemStatus.CANCELLED
    assert result.cancelled_work_item.last_error == "operator cancelled distributed work"
    assert result.snapshot.work_queue.cancelled_count == 1


@pytest.mark.asyncio
async def test_runtime_service_distributed_worker_resumes_scheduled_session() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, _ = build_service(
        [
            Decision(DecisionType.WAIT, "pause before distributed resume"),
            inspect_workload(),
            finish(),
        ],
        distributed_coordinator=coordinator,
    )

    waiting = await service.run_goal(*goal_task())
    scheduled = service.distributed_schedule_session(waiting.result.session_id)
    worker_result = await service.distributed_run_worker_once(WorkerId("worker-a"))
    completed = await service.get_session(waiting.result.session_id)

    assert waiting.result.status is ExecutionStatus.WAITING
    assert scheduled is not None
    assert worker_result is not None
    assert worker_result.status is WorkerRunStatus.COMPLETED
    assert worker_result.work_item is not None
    assert worker_result.work_item.status is WorkItemStatus.COMPLETED
    assert completed.goal_status is not None
    assert completed.goal_status.value == "completed"
    assert coordinator.workers.get(WorkerId("worker-a")).capabilities == (
        "agent_goal",
        "agent_session",
        "task",
        "tool_action",
    )
    assert coordinator.locks.active() == ()


@pytest.mark.asyncio
async def test_runtime_service_distributed_worker_retries_when_session_lock_is_held() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, _ = build_service(
        [
            Decision(DecisionType.WAIT, "pause before distributed resume"),
            inspect_workload(),
            finish(),
        ],
        distributed_coordinator=coordinator,
    )

    waiting = await service.run_goal(*goal_task())
    coordinator.locks.acquire(
        lock_key=f"session/{waiting.result.session_id}",
        owner_id=DistributedLockOwnerId("worker:other"),
    )
    service.distributed_schedule_session(waiting.result.session_id)
    worker_result = await service.distributed_run_worker_once(WorkerId("worker-a"))
    still_waiting = await service.get_session(waiting.result.session_id)

    assert worker_result is not None
    assert worker_result.status is WorkerRunStatus.RETRYING
    assert worker_result.work_item is not None
    assert worker_result.work_item.status is WorkItemStatus.QUEUED
    assert worker_result.reason.startswith("session execution lock conflict: ")
    assert still_waiting.goal_status is GoalStatus.WAITING


@pytest.mark.asyncio
async def test_runtime_service_distributed_worker_runs_until_idle() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, _ = build_service(
        [
            Decision(DecisionType.WAIT, "first distributed pause"),
            Decision(DecisionType.WAIT, "second distributed pause"),
            inspect_workload(),
            finish(),
            inspect_workload(),
            finish(),
        ],
        distributed_coordinator=coordinator,
    )

    first = await service.run_goal(*goal_task())
    second = await service.run_goal(*goal_task())
    service.distributed_schedule_session(first.result.session_id)
    service.distributed_schedule_session(second.result.session_id)
    results = await service.distributed_run_worker_until_idle(
        WorkerId("worker-a"),
        max_items=5,
    )

    assert results is not None
    assert [result.status for result in results] == [
        WorkerRunStatus.COMPLETED,
        WorkerRunStatus.COMPLETED,
        WorkerRunStatus.NO_WORK,
    ]
    assert coordinator.snapshot().work_queue.completed_count == 2
    assert (await service.get_session(first.result.session_id)).goal_status.value == "completed"
    assert (await service.get_session(second.result.session_id)).goal_status.value == "completed"


@pytest.mark.asyncio
async def test_runtime_service_distributed_worker_runs_scheduled_goal() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, _ = build_service(
        [inspect_workload(), finish()],
        distributed_coordinator=coordinator,
    )

    scheduled = service.distributed_schedule_goal(*goal_task(), priority=4)
    worker_result = await service.distributed_run_worker_once(WorkerId("worker-a"))
    sessions = await service.list_sessions()

    assert scheduled is not None
    assert scheduled.scheduled_work_item.kind == "agent_goal"
    assert worker_result is not None
    assert worker_result.status is WorkerRunStatus.COMPLETED
    assert worker_result.work_item is not None
    assert worker_result.work_item.status is WorkItemStatus.COMPLETED
    assert worker_result.reason.startswith("session completed: ")
    assert len(sessions) == 1
    assert sessions[0].goal_description == "Verify workload health"
    assert sessions[0].goal_status.value == "completed"


@pytest.mark.asyncio
async def test_runtime_service_distributed_worker_resumes_scheduled_task() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, _ = build_service(
        [wait(), inspect_workload(), finish()],
        distributed_coordinator=coordinator,
    )

    waiting = await service.run_goal(*goal_task())
    scheduled = service.distributed_schedule_task(
        waiting.result.session_id,
        waiting.session.current_task_id,
        priority=5,
    )
    worker_result = await service.distributed_run_worker_once(WorkerId("worker-a"))
    completed = await service.get_session(waiting.result.session_id)

    assert waiting.result.status is ExecutionStatus.WAITING
    assert scheduled is not None
    assert scheduled.scheduled_work_item.kind == "task"
    assert scheduled.scheduled_work_item.session_id == waiting.result.session_id
    assert scheduled.scheduled_work_item.task_id == waiting.session.current_task_id
    assert worker_result is not None
    assert worker_result.status is WorkerRunStatus.COMPLETED
    assert worker_result.work_item is not None
    assert worker_result.work_item.status is WorkItemStatus.COMPLETED
    assert worker_result.reason == "distributed task resume settled as completed"
    assert completed.goal_status.value == "completed"


@pytest.mark.asyncio
async def test_runtime_service_distributed_worker_confirms_scheduled_action() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, backend = build_service(
        [scale_workload(), inspect_workload(), finish()],
        distributed_coordinator=coordinator,
        environment="production",
    )

    waiting = await service.run_goal(*goal_task())
    assert waiting.result.status is ExecutionStatus.WAITING
    assert waiting.session.pending_action is not None
    pending = waiting.session.pending_action
    scheduled = service.distributed_schedule_action(
        waiting.result.session_id,
        waiting.session.current_task_id,
        pending.action_id,
        confirmed=True,
        priority=6,
    )
    worker_result = await service.distributed_run_worker_once(WorkerId("worker-a"))
    completed = await service.get_session(waiting.result.session_id)

    assert scheduled is not None
    assert scheduled.scheduled_work_item.kind == "tool_action"
    assert scheduled.scheduled_work_item.session_id == waiting.result.session_id
    assert scheduled.scheduled_work_item.task_id == waiting.session.current_task_id
    assert scheduled.scheduled_work_item.action_id == pending.action_id
    assert scheduled.scheduled_work_item.payload["confirmed"] is True
    assert worker_result is not None
    assert worker_result.status is WorkerRunStatus.COMPLETED
    assert worker_result.work_item is not None
    assert worker_result.work_item.status is WorkItemStatus.COMPLETED
    assert worker_result.reason == "distributed action resume settled as completed"
    assert completed.goal_status.value == "completed"
    assert completed.pending_action is None
    assert coordinator.locks.active() == ()
    assert backend.mutation_calls == 1
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_runtime_service_distributed_schedules_pending_actions_from_sessions() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, backend = build_service(
        [
            scale_named_workload("example-a"),
            scale_named_workload("example-b"),
            inspect_named_workload("example-a"),
            finish(),
            inspect_named_workload("example-b"),
            finish(),
        ],
        distributed_coordinator=coordinator,
        environment="production",
    )

    first = await service.run_goal(*goal_task())
    second = await service.run_goal(*goal_task())
    assert first.session.pending_action is not None
    assert second.session.pending_action is not None
    first_pending = first.session.pending_action
    second_pending = second.session.pending_action
    scheduled = await service.distributed_schedule_pending_actions(
        confirmed=True,
        priority=7,
    )
    duplicate = await service.distributed_schedule_pending_actions(
        confirmed=True,
        priority=1,
    )
    worker_results = await service.distributed_run_worker_until_idle(
        WorkerId("worker-a"),
        max_items=5,
    )
    first_completed = await service.get_session(first.result.session_id)
    second_completed = await service.get_session(second.result.session_id)

    assert scheduled is not None
    assert duplicate is not None
    assert len(scheduled.scheduled_work_items) == 2
    assert [item.priority for item in scheduled.scheduled_work_items] == [7, 7]
    assert {item.action_id for item in scheduled.scheduled_work_items} == {
        first_pending.action_id,
        second_pending.action_id,
    }
    assert [item.work_item_id for item in duplicate.scheduled_work_items] == [
        item.work_item_id for item in scheduled.scheduled_work_items
    ]
    assert scheduled.snapshot.work_queue.queued_count == 2
    assert worker_results is not None
    assert [result.status for result in worker_results] == [
        WorkerRunStatus.COMPLETED,
        WorkerRunStatus.COMPLETED,
        WorkerRunStatus.NO_WORK,
    ]
    assert first_completed.goal_status is GoalStatus.COMPLETED
    assert second_completed.goal_status is GoalStatus.COMPLETED
    assert backend.mutation_calls == 2
    assert backend.inspect_calls == 2


@pytest.mark.asyncio
async def test_runtime_service_distributed_pending_action_sweep_requires_confirmation() -> None:
    service, _ = build_service([], distributed_coordinator=DistributedRuntimeCoordinator())

    with pytest.raises(
        ValueError,
        match="distributed pending-action schedule requires confirmed=true",
    ):
        await service.distributed_schedule_pending_actions(confirmed=False)


@pytest.mark.asyncio
async def test_runtime_service_distributed_worker_rejects_mismatched_action_work() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, backend = build_service(
        [scale_workload(), inspect_workload(), finish()],
        distributed_coordinator=coordinator,
        environment="production",
    )

    waiting = await service.run_goal(*goal_task())
    assert waiting.result.status is ExecutionStatus.WAITING
    assert waiting.session.pending_action is not None
    scheduled = service.distributed_schedule_action(
        waiting.result.session_id,
        waiting.session.current_task_id,
        ActionId("other-action"),
        confirmed=True,
    )
    worker_result = await service.distributed_run_worker_once(WorkerId("worker-a"))
    still_waiting = await service.get_session(waiting.result.session_id)

    assert scheduled is not None
    assert worker_result is not None
    assert worker_result.status is WorkerRunStatus.FAILED
    assert worker_result.work_item is not None
    assert worker_result.work_item.status is WorkItemStatus.FAILED
    assert worker_result.reason.startswith("tool_action work item does not match pending action: ")
    assert still_waiting.goal_status is GoalStatus.WAITING
    assert still_waiting.pending_action is not None
    assert backend.mutation_calls == 0


@pytest.mark.asyncio
async def test_runtime_service_doctor_detects_mismatched_distributed_action_work() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()
    coordinator.workers.register(
        WorkerId("worker-a"),
        capabilities=("tool_action",),
        ttl_seconds=999_999_999,
        now=now,
    )
    service, _ = build_service(
        [scale_workload(), inspect_workload(), finish()],
        distributed_coordinator=coordinator,
        environment="production",
    )

    waiting = await service.run_goal(*goal_task())
    assert waiting.result.status is ExecutionStatus.WAITING
    assert waiting.session.pending_action is not None
    service.distributed_schedule_action(
        waiting.result.session_id,
        waiting.session.current_task_id,
        ActionId("other-action"),
        confirmed=True,
        now=now,
    )

    report = await service.doctor()
    distributed_queue = next(
        check for check in report.checks if check.name == "distributed_work_queue"
    )

    assert report.status == "error"
    assert distributed_queue.status == "error"
    assert distributed_queue.message == "invalid_session_work_items=1"


def test_runtime_service_rejects_unconfirmed_distributed_action_schedule() -> None:
    service, _ = build_service([], distributed_coordinator=DistributedRuntimeCoordinator())

    with pytest.raises(ValueError, match="distributed schedule-action requires confirmed=true"):
        service.distributed_schedule_action(
            SessionId("session-1"),
            TaskId("task-1"),
            ActionId("action-1"),
            confirmed=False,
        )


@pytest.mark.asyncio
async def test_runtime_service_distributed_worker_rejects_non_current_task_work() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, _ = build_service(
        [wait(), inspect_workload(), finish()],
        distributed_coordinator=coordinator,
    )

    waiting = await service.run_goal(*goal_task())
    scheduled = service.distributed_schedule_task(
        waiting.result.session_id,
        TaskId("other-task"),
    )
    worker_result = await service.distributed_run_worker_once(WorkerId("worker-a"))
    still_waiting = await service.get_session(waiting.result.session_id)

    assert scheduled is not None
    assert worker_result is not None
    assert worker_result.status is WorkerRunStatus.FAILED
    assert worker_result.work_item is not None
    assert worker_result.work_item.status is WorkItemStatus.FAILED
    assert worker_result.reason.startswith("task work item does not match current session task: ")
    assert still_waiting.goal_status is GoalStatus.WAITING


@pytest.mark.asyncio
async def test_runtime_service_distributed_worker_rejects_invalid_scheduled_goal_payload() -> None:
    coordinator = DistributedRuntimeCoordinator()
    coordinator.queue.enqueue(
        kind=WorkKind.AGENT_GOAL.value,
        payload=immutable_json({"goal": "not an object"}),
    )
    service, _ = build_service([], distributed_coordinator=coordinator)

    worker_result = await service.distributed_run_worker_once(WorkerId("worker-a"))
    sessions = await service.list_sessions()

    assert worker_result is not None
    assert worker_result.status is WorkerRunStatus.FAILED
    assert worker_result.work_item is not None
    assert worker_result.work_item.status is WorkItemStatus.FAILED
    assert worker_result.reason.startswith("invalid agent_goal work payload: ")
    assert sessions == ()


@pytest.mark.asyncio
async def test_runtime_service_doctor_includes_distributed_health() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()
    coordinator.scheduler.schedule_session(SessionId("session-1"), available_at=now)
    active = DomainLoader().load(KubernetesRemediationDomain(ServiceBackend(), ServiceBackend()))
    components = RuntimeBuilder().build(active)
    service = RuntimeService(
        runtime_api=build_api(components, []),
        components=components,
        distributed_coordinator=coordinator,
    )

    report = await service.doctor()
    distributed = next(check for check in report.checks if check.name == "distributed_runtime")

    assert report.status == "error"
    assert distributed.status == "error"
    assert "capacity_gaps=1" in distributed.message
    assert "recommendations=2" in distributed.message


@pytest.mark.asyncio
async def test_runtime_service_doctor_detects_orphan_events_from_full_event_stream() -> None:
    active = DomainLoader().load(KubernetesRemediationDomain(ServiceBackend(), ServiceBackend()))
    components = RuntimeBuilder().build(active)
    api, _, events = build_api_with_stores(components, [inspect_workload(), finish()])
    service = RuntimeService(runtime_api=api, components=components)

    await service.run_goal(*goal_task())
    await events.emit(
        RuntimeEvent(
            "GoalCreated",
            SessionId("orphan-session"),
            GoalId("orphan-goal"),
            TaskId("orphan-task"),
        )
    )

    report = await service.doctor()
    consistency = next(check for check in report.checks if check.name == "state_event_consistency")

    assert report.status == "error"
    assert consistency.status == "error"
    assert "orphan_events=1" in consistency.message


@pytest.mark.asyncio
async def test_runtime_service_repairs_missing_terminal_event_history() -> None:
    active = DomainLoader().load(KubernetesRemediationDomain(ServiceBackend(), ServiceBackend()))
    components = RuntimeBuilder().build(active)
    api, _, events = build_api_with_stores(components, [inspect_workload(), finish()])
    service = RuntimeService(runtime_api=api, components=components)

    await service.run_goal(*goal_task())
    events.events = [event for event in events.events if event.type != "GoalCompleted"]

    before = await service.doctor()
    planned = await service.repair_state_event_consistency(dry_run=True)
    after_plan = await service.doctor()
    repair = await service.repair_state_event_consistency(confirmed=True)
    after = await service.doctor()
    repaired_event_types = [event.event.type for event in repair.repairs]

    assert before.status == "error"
    assert planned.status == "planned"
    assert planned.repaired_event_count == 1
    assert planned.repairs[0].reason.startswith("would synthesize missing terminal event")
    assert after_plan.status == "error"
    assert repair.status == "repaired"
    assert repair.repaired_event_count == 1
    assert repair.skipped_item_count == 0
    assert repaired_event_types == ["GoalCompleted"]
    assert repair.repairs[0].event.data["repair_source"] == "state_event_consistency"
    assert after.status == "ok"


@pytest.mark.asyncio
async def test_runtime_service_repairs_missing_failed_terminal_event_history() -> None:
    active = DomainLoader().load(KubernetesRemediationDomain(ServiceBackend(), ServiceBackend()))
    components = RuntimeBuilder().build(active)
    api, _, events = build_api_with_stores(components, [finish()])
    service = RuntimeService(runtime_api=api, components=components)

    await service.run_goal(*goal_task())
    events.events = [event for event in events.events if event.type != "GoalFailed"]

    repair = await service.repair_state_event_consistency(confirmed=True)
    after = await service.doctor()

    assert repair.status == "repaired"
    assert [item.event.type for item in repair.repairs] == ["GoalFailed"]
    assert repair.repairs[0].event.data["error_code"] == "invalid_state"
    assert after.status == "ok"


@pytest.mark.asyncio
async def test_runtime_service_repairs_missing_cancelled_terminal_event_history() -> None:
    active = DomainLoader().load(KubernetesRemediationDomain(ServiceBackend(), ServiceBackend()))
    components = RuntimeBuilder().build(active)
    api, _, events = build_api_with_stores(components, [wait()])
    service = RuntimeService(runtime_api=api, components=components)

    waiting = await service.run_goal(*goal_task())
    await service.cancel_session(waiting.result.session_id, reason="fault injection cancel")
    events.events = [event for event in events.events if event.type != "GoalCancelled"]

    repair = await service.repair_state_event_consistency(confirmed=True)
    after = await service.doctor()

    assert repair.status == "repaired"
    assert [item.event.type for item in repair.repairs] == ["GoalCancelled"]
    assert repair.repairs[0].event.data["termination_reason"] == "fault injection cancel"
    assert after.status == "ok"


@pytest.mark.asyncio
async def test_runtime_service_blocks_state_event_repair_when_orphan_events_exist() -> None:
    active = DomainLoader().load(KubernetesRemediationDomain(ServiceBackend(), ServiceBackend()))
    components = RuntimeBuilder().build(active)
    api, _, events = build_api_with_stores(components, [inspect_workload(), finish()])
    service = RuntimeService(runtime_api=api, components=components)

    await service.run_goal(*goal_task())
    await events.emit(
        RuntimeEvent(
            "GoalCreated",
            SessionId("orphan-session"),
            GoalId("orphan-goal"),
            TaskId("orphan-task"),
        )
    )

    repair = await service.repair_state_event_consistency(confirmed=True)

    assert repair.status == "blocked"
    assert repair.repaired_event_count == 0
    assert repair.skipped_item_count == 1
    assert repair.skipped[0].reason.startswith("orphan event cannot be repaired automatically")


@pytest.mark.asyncio
async def test_runtime_service_doctor_detects_distributed_session_work_without_session() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()
    coordinator.workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_session", "task", "tool_action"),
        ttl_seconds=999_999_999,
        now=now,
    )
    coordinator.scheduler.schedule_session(SessionId("missing-session"), available_at=now)
    coordinator.scheduler.schedule_task(
        SessionId("missing-session"),
        TaskId("missing-task"),
        available_at=now,
    )
    coordinator.scheduler.schedule_action(
        SessionId("missing-session"),
        TaskId("missing-task"),
        ActionId("missing-action"),
        available_at=now,
    )
    active = DomainLoader().load(KubernetesRemediationDomain(ServiceBackend(), ServiceBackend()))
    components = RuntimeBuilder().build(active)
    service = RuntimeService(
        runtime_api=build_api(components, []),
        components=components,
        distributed_coordinator=coordinator,
    )

    report = await service.doctor()
    distributed_queue = next(
        check for check in report.checks if check.name == "distributed_work_queue"
    )

    assert report.status == "error"
    assert distributed_queue.status == "error"
    assert distributed_queue.message == "invalid_session_work_items=3"


def test_runtime_service_exposes_agentd_foundation_metadata() -> None:
    service, _ = build_service([])

    health = service.health()
    ready = service.ready()
    domains = service.domains()
    capabilities = service.capabilities()
    tools = service.tools()
    policies = service.policies()
    evaluators = service.evaluators()
    memories = service.memories()

    assert health.status == "ok"
    assert health.service == "universal-agent-runtime"
    assert ready.ready
    assert ready.reason == "ready"
    assert ready.domain_count == 1
    assert ready.capability_count == 6
    assert ready.tool_count == 6
    assert domains[0].name == "kubernetes"
    assert domains[0].version == "0.2.0"
    assert domains[0].primary
    assert "scale_workload" in domains[0].capability_names

    scale = next(item for item in capabilities if item.name == "scale_workload")
    assert scale.domain_name == "kubernetes"
    assert scale.domain_version == "0.2.0"
    assert scale.risk is RiskLevel.MEDIUM
    assert scale.tool_names == ("kubernetes_scale_workload",)

    scale_tool = next(item for item in tools if item.name == "kubernetes_scale_workload")
    assert scale_tool.domain_name == "kubernetes"
    assert scale_tool.capabilities == ("scale_workload",)
    assert scale_tool.required_arguments == ("name", "namespace", "replicas")
    schema_properties = scale_tool.argument_schema["properties"]
    assert isinstance(schema_properties, dict)
    replicas_schema = schema_properties["replicas"]
    assert isinstance(replicas_schema, dict)
    assert replicas_schema["type"] == "integer"
    assert replicas_schema["minimum"] == 0
    current_replicas_schema = schema_properties["current_replicas"]
    assert isinstance(current_replicas_schema, dict)
    assert current_replicas_schema["type"] == "integer"
    assert current_replicas_schema["minimum"] == 0
    resource_version_schema = schema_properties["resource_version"]
    assert isinstance(resource_version_schema, dict)
    assert resource_version_schema["type"] == ["string", "integer"]
    assert scale_tool.side_effect is SideEffect.REVERSIBLE

    scale_policy = next(item for item in policies if item.name == "kubernetes-scale-safety")
    assert scale_policy.domain_name == "kubernetes"
    assert scale_policy.effect is None
    assert scale_policy.policy_type == "KubernetesScalePolicy"

    evaluator = next(item for item in evaluators if item.name == "workload-health")
    assert evaluator.evaluator_type == "WorkloadHealthEvaluator"
    assert evaluator.domain_version == "0.2.0"

    memory_subjects = {item.subject for item in memories}
    assert "kubernetes readiness" in memory_subjects
    assert "unhealthy workload triage" in memory_subjects


def test_runtime_service_exposes_domain_package_catalog_without_activation() -> None:
    service, _ = build_service([], domain_packages=package_registry())

    packages = service.domain_packages()
    filtered = service.domain_packages(tag="ops")
    missing = service.domain_packages(tag="database")
    package = service.domain_package("kubernetes", "0.2.0")

    assert packages == filtered == (package,)
    assert missing == ()
    assert package.name == "kubernetes"
    assert package.version == "0.2.0"
    assert package.description == "Packaged Kubernetes runtime metadata"
    assert package.entrypoint == "universal_agent.domains.kubernetes:KubernetesRemediationDomain"
    assert package.capability_names == ("inspect_workload", "scale_workload")
    assert package.tool_names == ("kubernetes_inspect_workload", "kubernetes_scale_workload")
    assert package.policy_names == ("kubernetes-scale-safety",)
    assert package.dependencies == (DomainIdentity("observability", "1.0.0"),)
    assert package.required_tools == ("kubernetes_api",)
    assert package.runtime_api_compatibility == ">=0.1,<1"
    assert package.domain_api_compatibility == "agent.nantian.dev/v1alpha1"
    assert package.security["side_effects"] == "reversible"
    assert package.root_path == "/domains/kubernetes"
    assert package.manifest_path == "/domains/kubernetes/manifest.json"


@pytest.mark.asyncio
async def test_runtime_service_delegates_execution_to_runtime_api() -> None:
    service, backend = build_service([inspect_workload(), finish()])

    run = await service.run_goal(*goal_task())
    session = await service.get_session(run.result.session_id)
    sessions = await service.list_sessions()
    events = await service.list_events(run.result.session_id)

    assert run.result.status is ExecutionStatus.COMPLETED
    assert session.session_id == run.result.session_id
    assert [item.session_id for item in sessions] == [run.result.session_id]
    assert sessions[0].goal_status is session.goal_status
    assert sessions[0].current_task_status is session.current_task_status
    assert session.latest_evaluation is not None
    assert session.latest_evaluation.goal_completed
    assert [event.type for event in events][-1] == "GoalCompleted"
    assert backend.inspect_calls == 1
    assert backend.mutation_calls == 0


@pytest.mark.asyncio
async def test_runtime_service_builds_session_explorer_projection() -> None:
    service, backend = build_service([inspect_workload(), finish()])

    run = await service.run_goal(*goal_task())
    explorer = await service.session_explorer(run.result.session_id)

    evidence_claims = {item.claim: item for item in explorer.evidence}
    world_claims = {item.claim: item for item in explorer.world_facts}
    assert explorer.session.session_id == run.result.session_id
    assert evidence_claims["healthy"].value is True
    assert world_claims["healthy"].value is True
    assert world_claims["healthy"].subject == "deployment/example"
    assert str(evidence_claims["healthy"].evidence_id) in {
        evidence_id for fact in explorer.world_facts for evidence_id in fact.evidence_ids
    }
    assert explorer.world_entities[0].entity_id == "deployment/example"
    assert explorer.world_entities[0].kind == "Deployment"
    assert explorer.world_entities[0].attributes["healthy"] is True
    assert explorer.world_relations[0].source == "deployment/example"
    assert explorer.world_relations[0].relation == "owns"
    assert explorer.world_relations[0].target == "pod/example-1"
    world = await service.session_world(
        run.result.session_id,
        entity_id="deployment/example",
        relation="owns",
    )
    assert world.session_id == run.result.session_id
    assert world.neighborhood is not None
    assert world.neighborhood.root is not None
    assert world.neighborhood.root.entity_id == "deployment/example"
    assert [item.target for item in world.neighborhood.outgoing_relations] == ["pod/example-1"]
    assert world.neighborhood.incoming_relations == ()
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_runtime_service_derives_metrics_doctor_and_audit_from_events() -> None:
    service, backend = build_service(
        [scale_workload(), inspect_workload(), finish()],
        usage=[
            ModelUsage("scripted", "runtime-test", input_tokens=100, output_tokens=25),
            ModelUsage(
                "scripted",
                "runtime-test",
                input_tokens=50,
                output_tokens=10,
                estimated_cost_micros=12,
            ),
            ModelUsage("scripted", "runtime-test", input_tokens=20, output_tokens=5),
        ],
    )

    run = await service.run_goal(*goal_task())
    metrics = await service.metrics()
    cost = await service.cost(run.result.session_id)
    doctor = await service.doctor()
    logs = await service.logs(run.result.session_id)
    audit = await service.audit_records(run.result.session_id)

    assert run.result.status is ExecutionStatus.COMPLETED
    assert metrics.session_count == 1
    assert metrics.completed_goal_count == 1
    assert metrics.action_started_count == 2
    assert metrics.action_completed_count == 2
    assert metrics.policy_denial_count == 0
    assert metrics.model_call_count == 3
    assert metrics.model_total_token_count == 210
    assert metrics.model_estimated_cost_micros == 12
    assert cost.model_call_count == 3
    assert cost.by_model[0].provider == "scripted"
    assert cost.by_model[0].model == "runtime-test"
    assert cost.by_model[0].total_tokens == 210
    assert doctor.status == "ok"
    assert {check.name for check in doctor.checks} >= {
        "service_health",
        "readiness",
        "event_stream",
        "structured_logs",
        "traces",
        "audit",
        "cost_tracking",
    }
    assert logs[-1].event_type == "GoalCompleted"
    assert any(record.event_type == "ModelUsageRecorded" for record in logs)
    assert len(audit) == 1
    assert audit[0].capability == "scale_workload"
    assert audit[0].tool_name == "kubernetes_scale_workload"
    assert audit[0].side_effect == "reversible"
    assert audit[0].risk == "medium"
    assert audit[0].policy_effect == "allow"
    assert audit[0].status == "succeeded"
    assert backend.inspect_calls == 1
    assert backend.mutation_calls == 1
