from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent import (
    AgentProfile,
    Decision,
    DecisionType,
    DomainConfig,
    Goal,
    ModelConfig,
    ProfileConfig,
    RuntimeConfig,
    RuntimeHost,
    ScriptedModelAdapter,
    SecretRef,
    StoreConfig,
    SuccessCriterion,
    Task,
    build_configured_model_adapter,
    immutable_json,
)
from universal_agent.core import (
    CapabilityCategory,
    CapabilitySummary,
    DecisionContext,
    ExecutionStatus,
    GoalId,
    GoalStatus,
    JsonMapping,
    RiskLevel,
    SessionId,
    TaskId,
)
from universal_agent.distributed import (
    DistributedLockOwnerId,
    WorkerId,
    WorkerRunStatus,
    WorkItemStatus,
)
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.security import EnvSecretProvider


class HostModelTransport:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.payloads: list[JsonMapping] = []

    async def post_json(
        self,
        url: str,
        *,
        headers: JsonMapping,
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping:
        self.headers = {key: str(value) for key, value in headers.items()}
        self.payloads.append(payload)
        return immutable_json(
            {
                "decision": {"type": "finish", "reason": "host model completed"},
                "usage": {"input_tokens": 5, "output_tokens": 2},
            }
        )


class OpenAIHostModelTransport:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.payloads: list[JsonMapping] = []

    async def post_json(
        self,
        url: str,
        *,
        headers: JsonMapping,
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping:
        self.headers = {key: str(value) for key, value in headers.items()}
        self.payloads.append(payload)
        return immutable_json(
            {
                "status": "completed",
                "output_text": (
                    '{"arguments":{},"capability":null,"expected_observations":[],'
                    '"message":null,"reason":"openai host model completed",'
                    '"target":null,"type":"finish"}'
                ),
                "usage": {"input_tokens": 7, "output_tokens": 3},
            }
        )


class OpenAIChatHostModelTransport:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.payloads: list[JsonMapping] = []

    async def post_json(
        self,
        url: str,
        *,
        headers: JsonMapping,
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping:
        self.headers = {key: str(value) for key, value in headers.items()}
        self.payloads.append(payload)
        return immutable_json(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": (
                                '{"arguments":{},"capability":null,'
                                '"expected_observations":[],"message":null,'
                                '"reason":"openai chat host model completed",'
                                '"target":null,"type":"finish"}'
                            ),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4},
            }
        )


def model_context() -> DecisionContext:
    return DecisionContext(
        session_id=SessionId("session-model"),
        goal_id=GoalId("goal-model"),
        goal_description="Verify model config",
        task_id=TaskId("task-model"),
        task_description="Ask configured model",
        iteration=1,
        satisfied_criteria=immutable_json(),
        latest_observation=None,
        capabilities=(
            CapabilitySummary(
                "inspect_workload",
                "Inspect workload",
                CapabilityCategory.OBSERVATION,
                RiskLevel.LOW,
            ),
        ),
    )


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


@pytest.mark.asyncio
async def test_runtime_host_builds_json_http_model_from_config_secret_ref() -> None:
    transport = HostModelTransport()
    config = RuntimeConfig(
        secrets=(SecretRef.env("openai_api_key", "OPENAI_API_KEY"),),
        model=ModelConfig.json_http(
            name="runtime-decider",
            endpoint="https://models.example.test/decide",
            api_key_secret="openai_api_key",
            timeout_seconds=4.5,
            headers={"X-Agent-Runtime": "host-test"},
        ),
    )
    adapter = build_configured_model_adapter(
        config,
        secret_provider=EnvSecretProvider({"OPENAI_API_KEY": "secret-value"}),
        json_http_transport=transport,
    )

    decision = await adapter.decide(model_context())

    assert decision.reason == "host model completed"
    assert transport.headers["Authorization"] == "Bearer secret-value"
    assert transport.headers["X-Agent-Runtime"] == "host-test"
    assert transport.payloads[0]["model"] == "runtime-decider"
    assert "secret-value" not in str(config)


@pytest.mark.asyncio
async def test_runtime_host_builds_json_http_model_from_file_secret_ref(
    tmp_path: Path,
) -> None:
    secret_path = tmp_path / "model-api-key"
    secret_path.write_text("file-secret-value\n", encoding="utf-8")
    transport = HostModelTransport()
    config = RuntimeConfig(
        secrets=(SecretRef.file("openai_api_key", str(secret_path)),),
        model=ModelConfig.json_http(
            name="runtime-decider",
            endpoint="https://models.example.test/decide",
            api_key_secret="openai_api_key",
        ),
    )
    adapter = build_configured_model_adapter(
        config,
        secret_provider=EnvSecretProvider({}),
        json_http_transport=transport,
    )

    decision = await adapter.decide(model_context())

    assert decision.reason == "host model completed"
    assert transport.headers["Authorization"] == "Bearer file-secret-value"
    assert "file-secret-value" not in str(config)


@pytest.mark.asyncio
async def test_runtime_host_builds_openai_chat_completions_model_from_config_secret_ref() -> None:
    transport = OpenAIChatHostModelTransport()
    config = RuntimeConfig(
        secrets=(SecretRef.env("openai_api_key", "OPENAI_API_KEY"),),
        model=ModelConfig.openai_chat_completions(
            name="gpt-runtime",
            api_key_secret="openai_api_key",
            timeout_seconds=4.5,
            headers={"OpenAI-Organization": "org-test"},
            response_format="json_object",
        ),
    )
    adapter = build_configured_model_adapter(
        config,
        secret_provider=EnvSecretProvider({"OPENAI_API_KEY": "secret-value"}),
        json_http_transport=transport,
    )

    decision = await adapter.decide(model_context())

    assert decision.reason == "openai chat host model completed"
    assert transport.headers["Authorization"] == "Bearer secret-value"
    assert transport.headers["OpenAI-Organization"] == "org-test"
    assert transport.payloads[0]["model"] == "gpt-runtime"
    assert "messages" in transport.payloads[0]
    assert transport.payloads[0]["response_format"] == {"type": "json_object"}
    assert "store" not in transport.payloads[0]
    assert "secret-value" not in str(config)


@pytest.mark.asyncio
async def test_runtime_host_builds_openai_responses_model_from_config_secret_ref() -> None:
    transport = OpenAIHostModelTransport()
    config = RuntimeConfig(
        secrets=(SecretRef.env("openai_api_key", "OPENAI_API_KEY"),),
        model=ModelConfig.openai_responses(
            name="gpt-runtime",
            api_key_secret="openai_api_key",
            timeout_seconds=4.5,
            headers={"OpenAI-Organization": "org-test"},
        ),
    )
    adapter = build_configured_model_adapter(
        config,
        secret_provider=EnvSecretProvider({"OPENAI_API_KEY": "secret-value"}),
        json_http_transport=transport,
    )

    decision = await adapter.decide(model_context())

    assert decision.reason == "openai host model completed"
    assert transport.headers["Authorization"] == "Bearer secret-value"
    assert transport.headers["OpenAI-Organization"] == "org-test"
    assert transport.payloads[0]["model"] == "gpt-runtime"
    assert transport.payloads[0]["store"] is False
    assert "secret-value" not in str(config)


def test_runtime_host_builds_scripted_model_from_default_config() -> None:
    adapter = build_configured_model_adapter(
        RuntimeConfig(),
        scripted_decisions=[Decision(DecisionType.FINISH, "scripted done")],
    )

    assert adapter is not None


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


def test_runtime_host_secret_resolution_drives_readiness_without_values() -> None:
    backend = HostRemediationBackend()
    config = RuntimeConfig(
        environment=immutable_json({"environment": "production"}),
        secrets=(
            SecretRef.env("openai_api_key", "OPENAI_API_KEY"),
            SecretRef.env("optional_token", "OPTIONAL_TOKEN", required=False),
        ),
        domain=DomainConfig("kubernetes", "0.2.0"),
    )

    missing = RuntimeHost.build(
        config=config,
        model=ScriptedModelAdapter([]),
        domain=KubernetesRemediationDomain(backend, backend),
        secret_provider=EnvSecretProvider({}),
    )
    available = RuntimeHost.build(
        config=config,
        model=ScriptedModelAdapter([]),
        domain=KubernetesRemediationDomain(backend, backend),
        secret_provider=EnvSecretProvider({"OPENAI_API_KEY": "secret-value"}),
    )

    missing_ready = missing.service.ready()
    available_ready = available.service.ready()
    projected = available.service.config()

    assert missing_ready.ready is False
    assert missing_ready.reason == "missing required secrets: openai_api_key"
    assert available_ready.ready is True
    assert available.secret_resolution.passed is True
    assert projected.secrets[0].status == "available"
    assert projected.secrets[0].available is True
    assert projected.secrets[1].status == "missing_optional"
    assert projected.secrets[1].available is False
    assert "secret-value" not in str(projected)


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


def test_runtime_host_uses_configured_sqlite_backed_distributed_queue(tmp_path: Path) -> None:
    backend = HostRemediationBackend()
    queue_path = tmp_path / "coordination" / "runtime.sqlite3"
    config = RuntimeConfig(
        environment=immutable_json({"environment": "production"}),
        distributed_queue=StoreConfig.sqlite(str(queue_path)),
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


def test_runtime_host_uses_configured_sqlite_backed_distributed_locks(tmp_path: Path) -> None:
    backend = HostRemediationBackend()
    locks_path = tmp_path / "coordination" / "distributed-locks.sqlite3"
    config = RuntimeConfig(
        environment=immutable_json({"environment": "production"}),
        distributed_locks=StoreConfig.sqlite(str(locks_path)),
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


def test_runtime_host_uses_configured_sqlite_backed_worker_registry(tmp_path: Path) -> None:
    backend = HostRemediationBackend()
    workers_path = tmp_path / "coordination" / "workers.sqlite3"
    config = RuntimeConfig(
        environment=immutable_json({"environment": "production"}),
        distributed_workers=StoreConfig.sqlite(str(workers_path)),
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


@pytest.mark.asyncio
async def test_runtime_host_file_backed_coordination_resumes_scheduled_session(
    tmp_path: Path,
) -> None:
    backend = HostRemediationBackend()
    store_path = tmp_path / "runtime-store"
    queue_path = tmp_path / "coordination" / "work-queue.json"
    locks_path = tmp_path / "coordination" / "distributed-locks.json"
    workers_path = tmp_path / "coordination" / "workers.json"
    config = RuntimeConfig(
        environment=immutable_json({"environment": "production"}),
        store=StoreConfig.file(str(store_path)),
        distributed_queue=StoreConfig.file(str(queue_path)),
        distributed_locks=StoreConfig.file(str(locks_path)),
        distributed_workers=StoreConfig.file(str(workers_path)),
        domain=DomainConfig("kubernetes", "0.2.0"),
    )
    first = RuntimeHost.build(
        config=config,
        model=ScriptedModelAdapter([Decision(DecisionType.WAIT, "pause for file worker")]),
        domain=KubernetesRemediationDomain(backend, backend),
    )

    waiting = await first.service.run_goal(*remediation_goal_task())
    scheduled = first.service.distributed_schedule_session(waiting.result.session_id, priority=9)
    backend._scaled = True
    second = RuntimeHost.build(
        config=config,
        model=ScriptedModelAdapter([inspect_workload("healthy"), finish()]),
        domain=KubernetesRemediationDomain(backend, backend),
    )
    worker_result = await second.service.distributed_run_worker_once(WorkerId("worker-a"))
    third = RuntimeHost.build(
        config=config,
        model=ScriptedModelAdapter([]),
        domain=KubernetesRemediationDomain(backend, backend),
    )
    snapshot = third.service.distributed_snapshot()
    completed = await third.service.get_session(waiting.result.session_id)

    assert waiting.result.status is ExecutionStatus.WAITING
    assert scheduled is not None
    assert scheduled.scheduled_work_item.status is WorkItemStatus.QUEUED
    assert worker_result is not None
    assert worker_result.status is WorkerRunStatus.COMPLETED
    assert worker_result.work_item is not None
    assert worker_result.work_item.status is WorkItemStatus.COMPLETED
    assert completed.goal_status is GoalStatus.COMPLETED
    assert snapshot is not None
    assert snapshot.work_queue.completed_count == 1
    assert snapshot.work_queue.items[0].priority == 9
    assert snapshot.locks == ()
    assert snapshot.workers.total_count == 1
    assert snapshot.workers.workers[0].worker_id == WorkerId("worker-a")
    assert queue_path.exists()
    assert locks_path.exists()
    assert workers_path.exists()


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
