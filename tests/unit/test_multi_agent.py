from __future__ import annotations

import asyncio
from typing import cast

import pytest

from universal_agent.core import DomainIdentity, ErrorCode, SessionId, immutable_json
from universal_agent.evidence import EvidenceId
from universal_agent.multi_agent import (
    AGENT_TASK_API_VERSION,
    AgentDelegationBatchResult,
    AgentDelegationBatchStatus,
    AgentDelegationDependencyError,
    AgentDelegationLimitError,
    AgentDelegationSpec,
    AgentExecutorNotRegisteredError,
    AgentExpectedOutput,
    AgentId,
    AgentInstanceRecord,
    AgentInstanceStatus,
    AgentOrchestrator,
    AgentProfileNotRegisteredError,
    AgentProfileRecord,
    AgentRegistry,
    AgentRegistryError,
    AgentTaskConstraints,
    AgentTaskId,
    AgentTaskRequest,
    AgentTaskResult,
    AgentTaskResultStatus,
    NoEligibleAgentError,
    agent_delegation_batch_result_payload,
    agent_delegation_spec_payload,
    agent_task_request_payload,
    agent_task_result_payload,
    decode_agent_delegation_batch_result,
    decode_agent_delegation_spec,
    decode_agent_task_request,
    decode_agent_task_result,
    rejected_agent_task_result,
)


class RecordingExecutor:
    def __init__(self) -> None:
        self.requests: list[AgentTaskRequest] = []

    async def execute_agent_task(self, request: AgentTaskRequest) -> AgentTaskResult:
        self.requests.append(request)
        return AgentTaskResult(
            task_id=request.task_id,
            status=AgentTaskResultStatus.COMPLETED,
            result=immutable_json({"risk_level": "medium"}),
            evidence_ids=(),
            reason="completed by test executor",
            session_id=SessionId("session-child"),
        )


class StatusExecutor:
    def __init__(
        self,
        status: AgentTaskResultStatus,
        events: list[str] | None = None,
        label: str = "executor",
    ) -> None:
        self.status = status
        self.events = events
        self.label = label

    async def execute_agent_task(self, request: AgentTaskRequest) -> AgentTaskResult:
        if self.events is not None:
            self.events.append(f"start:{self.label}")
        await asyncio.sleep(0)
        if self.events is not None:
            self.events.append(f"finish:{self.label}")
        return AgentTaskResult(
            task_id=request.task_id,
            status=self.status,
            result=immutable_json({"label": self.label}),
            reason=f"{self.label} settled",
            error_code=None
            if self.status is AgentTaskResultStatus.COMPLETED
            else ErrorCode.TOOL_FAILURE,
        )


class RaisingExecutor:
    async def execute_agent_task(self, request: AgentTaskRequest) -> AgentTaskResult:
        raise RuntimeError(f"executor unavailable for {request.task_id}")


class SlowExecutor:
    async def execute_agent_task(self, request: AgentTaskRequest) -> AgentTaskResult:
        await asyncio.sleep(1)
        return AgentTaskResult(
            task_id=request.task_id,
            status=AgentTaskResultStatus.COMPLETED,
            result=immutable_json({"slow": True}),
            reason="slow executor completed",
        )


class LifecycleRecordingExecutor:
    def __init__(
        self,
        registry: AgentRegistry,
        agent_id: AgentId,
        statuses: list[AgentInstanceStatus],
    ) -> None:
        self.registry = registry
        self.agent_id = agent_id
        self.statuses = statuses

    async def execute_agent_task(self, request: AgentTaskRequest) -> AgentTaskResult:
        self.statuses.append(self.registry.instance(self.agent_id).status)
        await asyncio.sleep(0)
        return AgentTaskResult(
            task_id=request.task_id,
            status=AgentTaskResultStatus.COMPLETED,
            result=immutable_json({"observed_status": self.statuses[-1].value}),
            reason="lifecycle observed",
        )


def output() -> AgentExpectedOutput:
    return AgentExpectedOutput("security_report")


def profile(
    name: str = "security-auditor",
    *,
    permissions: tuple[str, ...] = ("read_only", "security_review"),
) -> AgentProfileRecord:
    return AgentProfileRecord(
        name=name,
        version="1.0.0",
        domains=(DomainIdentity("kubernetes", "0.2.0"),),
        permissions=permissions,
        capabilities=("inspect_workload",),
    )


def instance(agent_id: str = "agent-1", *, name: str = "security-auditor") -> AgentInstanceRecord:
    return AgentInstanceRecord(AgentId(agent_id), name, "1.0.0")


def request(
    *,
    constraints: AgentTaskConstraints | None = None,
    parent_task_id: AgentTaskId | None = None,
) -> AgentTaskRequest:
    return AgentTaskRequest(
        goal="Audit deployment security",
        input=immutable_json({"resource": "deployment/example"}),
        constraints=constraints or AgentTaskConstraints(read_only=True),
        expected_output=output(),
        task_id=AgentTaskId("agent-task-1"),
        parent_task_id=parent_task_id,
    )


def test_agent_task_contract_is_structured_and_json_safe() -> None:
    task = request(
        constraints=AgentTaskConstraints(
            read_only=True,
            max_depth=2,
            max_children=3,
            allowed_profiles=("security-auditor",),
            required_permissions=("security_review",),
        )
    )
    payload = agent_task_request_payload(task)

    assert payload["api_version"] == AGENT_TASK_API_VERSION
    assert payload["task_id"] == "agent-task-1"
    assert payload["constraints"] == {
        "read_only": True,
        "max_depth": 2,
        "max_children": 3,
        "max_duration_seconds": None,
        "max_cost": None,
        "allowed_profiles": ["security-auditor"],
        "required_permissions": ["security_review"],
    }
    decoded = decode_agent_task_request(payload)
    assert decoded.goal == task.goal
    assert decoded.task_id == task.task_id
    assert decoded.constraints.read_only is True
    assert decoded.constraints.max_depth == 2
    assert decoded.constraints.allowed_profiles == ("security-auditor",)
    assert decoded.expected_output.type == "security_report"

    with pytest.raises(TypeError):
        cast(dict[str, object], task.input)["resource"] = "deployment/other"


def test_agent_task_contract_rejects_invalid_requests_and_results() -> None:
    with pytest.raises(ValueError, match="goal must not be empty"):
        AgentTaskRequest(goal=" ", expected_output=output())

    with pytest.raises(ValueError, match="max_children must be positive"):
        AgentTaskConstraints(max_children=0)

    with pytest.raises(ValueError, match="unsupported agent task api version"):
        AgentTaskRequest(
            goal="Audit",
            expected_output=output(),
            api_version="agent.nantian.dev/v2",
        )

    with pytest.raises(ValueError, match="non-completed agent task result requires reason"):
        AgentTaskResult(AgentTaskId("agent-task-1"), AgentTaskResultStatus.FAILED)


def test_agent_task_result_payload_preserves_evidence_contract() -> None:
    result = AgentTaskResult(
        task_id=AgentTaskId("agent-task-1"),
        status=AgentTaskResultStatus.REJECTED,
        evidence_ids=(EvidenceId("evidence-denied"),),
        reason="policy denied",
        error_code=ErrorCode.POLICY_DENIED,
    )

    payload = agent_task_result_payload(result)
    decoded = decode_agent_task_result(payload)

    assert payload["status"] == "rejected"
    assert payload["evidence"] == ["evidence-denied"]
    assert payload["error_code"] == "policy_denied"
    assert decoded.task_id == result.task_id
    assert decoded.status is AgentTaskResultStatus.REJECTED
    assert decoded.evidence_ids == (EvidenceId("evidence-denied"),)
    assert decoded.error_code is ErrorCode.POLICY_DENIED


def test_agent_task_decoders_reject_invalid_payload_values() -> None:
    with pytest.raises(ValueError, match="unsupported agent task result status"):
        decode_agent_task_result(
            immutable_json(
                {
                    "task_id": "agent-task-1",
                    "status": "missing",
                    "reason": "bad status",
                }
            )
        )

    with pytest.raises(ValueError, match=r"constraints.max_children must be an integer"):
        decode_agent_task_request(
            immutable_json(
                {
                    "goal": "Audit",
                    "constraints": {"max_children": "many"},
                    "expected_output": {"type": "security_report"},
                }
            )
        )


def test_agent_registry_distinguishes_profiles_from_instances() -> None:
    registry = AgentRegistry((profile(),), (instance(),))

    assert registry.profile("security-auditor", "1.0.0").permissions == (
        "read_only",
        "security_review",
    )
    assert registry.instance(AgentId("agent-1")).profile_name == "security-auditor"
    assert registry.snapshot().profiles[0].name == "security-auditor"
    assert registry.snapshot().instances[0].agent_id == AgentId("agent-1")


def test_agent_registry_rejects_unknown_profile_and_duplicate_instances() -> None:
    registry = AgentRegistry((profile(),))

    with pytest.raises(AgentProfileNotRegisteredError):
        registry.register_instance(AgentInstanceRecord(AgentId("agent-2"), "missing", "1.0.0"))

    registry.register_instance(instance())
    with pytest.raises(AgentRegistryError, match="duplicate agent instance"):
        registry.register_instance(instance())


def test_agent_registry_filters_eligible_instances_by_constraints() -> None:
    registry = AgentRegistry(
        (
            profile("security-auditor"),
            profile("operator", permissions=("mutation",)),
        ),
        (
            instance("agent-1", name="security-auditor"),
            instance("agent-2", name="operator"),
            AgentInstanceRecord(
                AgentId("agent-3"),
                "security-auditor",
                "1.0.0",
                status=AgentInstanceStatus.OFFLINE,
            ),
        ),
    )

    eligible = registry.eligible_instances(
        request(
            constraints=AgentTaskConstraints(
                read_only=True,
                allowed_profiles=("security-auditor",),
                required_permissions=("security_review",),
            )
        )
    )

    assert [item.agent_id for item in eligible] == [AgentId("agent-1")]


def test_agent_registry_updates_instance_status() -> None:
    registry = AgentRegistry((profile(),), (instance(),))

    updated = registry.update_instance_status(AgentId("agent-1"), AgentInstanceStatus.DRAINING)

    assert updated.status is AgentInstanceStatus.DRAINING
    assert registry.instance(AgentId("agent-1")).status is AgentInstanceStatus.DRAINING
    assert registry.eligible_instances(request()) == ()


@pytest.mark.asyncio
async def test_agent_orchestrator_delegates_to_eligible_executor() -> None:
    registry = AgentRegistry((profile(),), (instance(),))
    executor = RecordingExecutor()
    orchestrator = AgentOrchestrator(registry, {AgentId("agent-1"): executor})

    result = await orchestrator.delegate(request())

    assert result.status is AgentTaskResultStatus.COMPLETED
    assert result.result["risk_level"] == "medium"
    assert executor.requests[0].goal == "Audit deployment security"
    assert registry.instance(AgentId("agent-1")).status is AgentInstanceStatus.READY


@pytest.mark.asyncio
async def test_agent_orchestrator_marks_instance_busy_during_execution() -> None:
    registry = AgentRegistry((profile(),), (instance(),))
    statuses: list[AgentInstanceStatus] = []
    orchestrator = AgentOrchestrator(
        registry,
        {AgentId("agent-1"): LifecycleRecordingExecutor(registry, AgentId("agent-1"), statuses)},
    )

    result = await orchestrator.delegate(request())

    assert result.status is AgentTaskResultStatus.COMPLETED
    assert statuses == [AgentInstanceStatus.BUSY]
    assert registry.instance(AgentId("agent-1")).status is AgentInstanceStatus.READY


@pytest.mark.asyncio
async def test_agent_orchestrator_rejects_ineligible_explicit_instance() -> None:
    registry = AgentRegistry(
        (profile("operator", permissions=("mutation",)),), (instance(name="operator"),)
    )
    orchestrator = AgentOrchestrator(registry, {AgentId("agent-1"): RecordingExecutor()})

    with pytest.raises(NoEligibleAgentError):
        await orchestrator.delegate(request(), agent_id=AgentId("agent-1"))


@pytest.mark.asyncio
async def test_agent_orchestrator_rejects_offline_explicit_instance() -> None:
    registry = AgentRegistry(
        (profile(),),
        (
            AgentInstanceRecord(
                AgentId("agent-1"),
                "security-auditor",
                "1.0.0",
                status=AgentInstanceStatus.OFFLINE,
            ),
        ),
    )
    orchestrator = AgentOrchestrator(registry, {AgentId("agent-1"): RecordingExecutor()})

    with pytest.raises(NoEligibleAgentError, match="not ready"):
        await orchestrator.delegate(request(), agent_id=AgentId("agent-1"))


@pytest.mark.asyncio
async def test_agent_orchestrator_enforces_parent_child_limit() -> None:
    registry = AgentRegistry((profile(),), (instance(),))
    orchestrator = AgentOrchestrator(registry, {AgentId("agent-1"): RecordingExecutor()})
    parent = AgentTaskId("agent-task-parent")
    constraints = AgentTaskConstraints(read_only=True, max_children=1)

    await orchestrator.delegate(request(constraints=constraints, parent_task_id=parent))

    with pytest.raises(AgentDelegationLimitError, match="max_children exceeded"):
        await orchestrator.delegate(request(constraints=constraints, parent_task_id=parent))


@pytest.mark.asyncio
async def test_agent_orchestrator_missing_executor_does_not_consume_child_limit() -> None:
    registry = AgentRegistry((profile(),), (instance(),))
    parent = AgentTaskId("agent-task-parent")
    constraints = AgentTaskConstraints(read_only=True, max_children=1)
    orchestrator = AgentOrchestrator(registry)

    with pytest.raises(AgentExecutorNotRegisteredError):
        await orchestrator.delegate(request(constraints=constraints, parent_task_id=parent))

    orchestrator.register_executor(AgentId("agent-1"), RecordingExecutor())
    result = await orchestrator.delegate(request(constraints=constraints, parent_task_id=parent))

    assert result.status is AgentTaskResultStatus.COMPLETED


@pytest.mark.asyncio
async def test_agent_orchestrator_enforces_duration_limit() -> None:
    registry = AgentRegistry((profile(),), (instance(),))
    orchestrator = AgentOrchestrator(registry, {AgentId("agent-1"): SlowExecutor()})

    result = await orchestrator.delegate(
        request(
            constraints=AgentTaskConstraints(
                read_only=True,
                max_duration_seconds=0.001,
            )
        )
    )

    assert result.status is AgentTaskResultStatus.FAILED
    assert result.error_code is ErrorCode.TIMEOUT
    assert result.reason == "agent task exceeded max_duration_seconds=0.001"
    assert registry.instance(AgentId("agent-1")).status is AgentInstanceStatus.READY


@pytest.mark.asyncio
async def test_agent_orchestrator_restores_instance_after_executor_failure() -> None:
    registry = AgentRegistry((profile(),), (instance(),))
    orchestrator = AgentOrchestrator(registry, {AgentId("agent-1"): RaisingExecutor()})

    with pytest.raises(RuntimeError, match="executor unavailable"):
        await orchestrator.delegate(request())

    assert registry.instance(AgentId("agent-1")).status is AgentInstanceStatus.READY


def test_rejected_agent_task_result_uses_policy_denied_by_default() -> None:
    result = rejected_agent_task_result(request(), "delegation policy denied")

    assert result.status is AgentTaskResultStatus.REJECTED
    assert result.error_code is ErrorCode.POLICY_DENIED


def batch_request(task_id: str, *, parent_task_id: AgentTaskId | None = None) -> AgentTaskRequest:
    return AgentTaskRequest(
        goal=f"Run {task_id}",
        input=immutable_json({"task": task_id}),
        constraints=AgentTaskConstraints(read_only=True, max_children=3),
        expected_output=output(),
        task_id=AgentTaskId(task_id),
        parent_task_id=parent_task_id,
    )


def test_agent_delegation_spec_payload_round_trips_dependencies() -> None:
    spec = AgentDelegationSpec(
        batch_request("child"),
        agent_id=AgentId("agent-1"),
        depends_on=(AgentTaskId("parent"),),
    )

    decoded = decode_agent_delegation_spec(agent_delegation_spec_payload(spec))

    assert decoded.request.task_id == AgentTaskId("child")
    assert decoded.agent_id == AgentId("agent-1")
    assert decoded.depends_on == (AgentTaskId("parent"),)


def test_agent_delegation_batch_result_payload_round_trips_result_collector_state() -> None:
    batch = AgentDelegationBatchResult(
        status=AgentDelegationBatchStatus.PARTIAL,
        results=(
            AgentTaskResult(
                AgentTaskId("agent-task-a"),
                AgentTaskResultStatus.COMPLETED,
                result=immutable_json({"done": True}),
                evidence_ids=(EvidenceId("evidence-a"),),
            ),
            AgentTaskResult(
                AgentTaskId("agent-task-b"),
                AgentTaskResultStatus.REJECTED,
                reason="dependency did not complete",
                error_code=ErrorCode.INVALID_STATE,
            ),
        ),
        skipped_task_ids=(AgentTaskId("agent-task-b"),),
        reason="some delegated agent tasks did not complete",
    )

    decoded = decode_agent_delegation_batch_result(agent_delegation_batch_result_payload(batch))

    assert decoded.status is AgentDelegationBatchStatus.PARTIAL
    assert decoded.skipped_task_ids == (AgentTaskId("agent-task-b"),)
    assert [item.task_id for item in decoded.results] == [
        AgentTaskId("agent-task-a"),
        AgentTaskId("agent-task-b"),
    ]
    assert decoded.results[0].evidence_ids == (EvidenceId("evidence-a"),)
    assert decoded.results[1].error_code is ErrorCode.INVALID_STATE


def test_agent_delegation_batch_result_decoder_rejects_invalid_status() -> None:
    with pytest.raises(ValueError, match="unsupported agent delegation batch status"):
        decode_agent_delegation_batch_result(
            immutable_json({"status": "missing", "results": [], "reason": "bad status"})
        )


@pytest.mark.asyncio
async def test_agent_orchestrator_delegates_many_ready_tasks_in_parallel() -> None:
    events: list[str] = []
    registry = AgentRegistry(
        (profile(),),
        (
            instance("agent-1"),
            instance("agent-2"),
        ),
    )
    orchestrator = AgentOrchestrator(
        registry,
        {
            AgentId("agent-1"): StatusExecutor(AgentTaskResultStatus.COMPLETED, events, "a"),
            AgentId("agent-2"): StatusExecutor(AgentTaskResultStatus.COMPLETED, events, "b"),
        },
    )

    result = await orchestrator.delegate_many(
        (
            AgentDelegationSpec(batch_request("agent-task-a"), agent_id=AgentId("agent-1")),
            AgentDelegationSpec(batch_request("agent-task-b"), agent_id=AgentId("agent-2")),
        )
    )

    assert result.status is AgentDelegationBatchStatus.COMPLETED
    assert [item.task_id for item in result.results] == [
        AgentTaskId("agent-task-a"),
        AgentTaskId("agent-task-b"),
    ]
    assert events.index("start:b") < events.index("finish:a")


@pytest.mark.asyncio
async def test_agent_orchestrator_delegates_many_rejects_busy_instance_reuse() -> None:
    events: list[str] = []
    registry = AgentRegistry((profile(),), (instance(),))
    orchestrator = AgentOrchestrator(
        registry,
        {
            AgentId("agent-1"): StatusExecutor(
                AgentTaskResultStatus.COMPLETED,
                events,
                "shared",
            )
        },
    )

    result = await orchestrator.delegate_many(
        (
            AgentDelegationSpec(batch_request("agent-task-a"), agent_id=AgentId("agent-1")),
            AgentDelegationSpec(batch_request("agent-task-b"), agent_id=AgentId("agent-1")),
        )
    )

    assert result.status is AgentDelegationBatchStatus.PARTIAL
    assert result.results[0].status is AgentTaskResultStatus.COMPLETED
    assert result.results[1].status is AgentTaskResultStatus.REJECTED
    assert result.results[1].error_code is ErrorCode.INVALID_STATE
    assert result.results[1].reason == "agent instance is not ready: agent-1"
    assert registry.instance(AgentId("agent-1")).status is AgentInstanceStatus.READY
    assert events == ["start:shared", "finish:shared"]


@pytest.mark.asyncio
async def test_agent_orchestrator_delegates_many_respects_dependencies() -> None:
    events: list[str] = []
    registry = AgentRegistry((profile(),), (instance("agent-1"), instance("agent-2")))
    orchestrator = AgentOrchestrator(
        registry,
        {
            AgentId("agent-1"): StatusExecutor(AgentTaskResultStatus.COMPLETED, events, "parent"),
            AgentId("agent-2"): StatusExecutor(AgentTaskResultStatus.COMPLETED, events, "child"),
        },
    )

    result = await orchestrator.delegate_many(
        (
            AgentDelegationSpec(batch_request("parent"), agent_id=AgentId("agent-1")),
            AgentDelegationSpec(
                batch_request("child"),
                agent_id=AgentId("agent-2"),
                depends_on=(AgentTaskId("parent"),),
            ),
        )
    )

    assert result.completed
    assert events.index("finish:parent") < events.index("start:child")


@pytest.mark.asyncio
async def test_agent_orchestrator_delegates_many_skips_failed_dependencies() -> None:
    registry = AgentRegistry(
        (profile(),), (instance("agent-1"), instance("agent-2"), instance("agent-3"))
    )
    orchestrator = AgentOrchestrator(
        registry,
        {
            AgentId("agent-1"): StatusExecutor(AgentTaskResultStatus.FAILED, label="parent"),
            AgentId("agent-2"): StatusExecutor(AgentTaskResultStatus.COMPLETED, label="child"),
            AgentId("agent-3"): StatusExecutor(AgentTaskResultStatus.COMPLETED, label="grandchild"),
        },
    )

    result = await orchestrator.delegate_many(
        (
            AgentDelegationSpec(batch_request("parent"), agent_id=AgentId("agent-1")),
            AgentDelegationSpec(
                batch_request("child"),
                agent_id=AgentId("agent-2"),
                depends_on=(AgentTaskId("parent"),),
            ),
            AgentDelegationSpec(
                batch_request("grandchild"),
                agent_id=AgentId("agent-3"),
                depends_on=(AgentTaskId("child"),),
            ),
        )
    )

    assert result.status is AgentDelegationBatchStatus.FAILED
    assert result.skipped_task_ids == (AgentTaskId("child"), AgentTaskId("grandchild"))
    assert [item.status for item in result.results] == [
        AgentTaskResultStatus.FAILED,
        AgentTaskResultStatus.REJECTED,
        AgentTaskResultStatus.REJECTED,
    ]


@pytest.mark.asyncio
async def test_agent_orchestrator_delegates_many_collects_executor_failures() -> None:
    registry = AgentRegistry(
        (profile(),),
        (instance("agent-1"), instance("agent-2"), instance("agent-3")),
    )
    orchestrator = AgentOrchestrator(
        registry,
        {
            AgentId("agent-1"): RaisingExecutor(),
            AgentId("agent-2"): StatusExecutor(AgentTaskResultStatus.COMPLETED, label="child"),
            AgentId("agent-3"): StatusExecutor(
                AgentTaskResultStatus.COMPLETED, label="independent"
            ),
        },
    )

    result = await orchestrator.delegate_many(
        (
            AgentDelegationSpec(batch_request("parent"), agent_id=AgentId("agent-1")),
            AgentDelegationSpec(
                batch_request("child"),
                agent_id=AgentId("agent-2"),
                depends_on=(AgentTaskId("parent"),),
            ),
            AgentDelegationSpec(batch_request("independent"), agent_id=AgentId("agent-3")),
        )
    )

    assert result.status is AgentDelegationBatchStatus.PARTIAL
    assert result.skipped_task_ids == (AgentTaskId("child"),)
    assert result.results[0].status is AgentTaskResultStatus.FAILED
    assert result.results[0].error_code is ErrorCode.UNKNOWN_EXECUTION
    assert result.results[1].status is AgentTaskResultStatus.REJECTED
    assert result.results[2].status is AgentTaskResultStatus.COMPLETED


def test_agent_orchestrator_delegates_many_rejects_invalid_dependencies() -> None:
    with pytest.raises(ValueError, match="cannot depend on itself"):
        AgentDelegationSpec(
            batch_request("agent-task-a"),
            depends_on=(AgentTaskId("agent-task-a"),),
        )

    registry = AgentRegistry((profile(),), (instance("agent-1"), instance("agent-2")))
    orchestrator = AgentOrchestrator(registry)

    with pytest.raises(ValueError, match="duplicate agent delegation task ids"):
        asyncio.run(
            orchestrator.delegate_many(
                (
                    AgentDelegationSpec(batch_request("agent-task-a")),
                    AgentDelegationSpec(batch_request("agent-task-a")),
                )
            )
        )

    with pytest.raises(ValueError, match="unknown agent delegation dependencies"):
        asyncio.run(
            orchestrator.delegate_many(
                (
                    AgentDelegationSpec(
                        batch_request("agent-task-a"),
                        depends_on=(AgentTaskId("missing"),),
                    ),
                )
            )
        )


@pytest.mark.asyncio
async def test_agent_orchestrator_delegates_many_detects_cycles() -> None:
    registry = AgentRegistry((profile(),), (instance("agent-1"), instance("agent-2")))
    orchestrator = AgentOrchestrator(
        registry,
        {
            AgentId("agent-1"): RecordingExecutor(),
            AgentId("agent-2"): RecordingExecutor(),
        },
    )

    with pytest.raises(AgentDelegationDependencyError):
        await orchestrator.delegate_many(
            (
                AgentDelegationSpec(
                    batch_request("agent-task-a"),
                    agent_id=AgentId("agent-1"),
                    depends_on=(AgentTaskId("agent-task-b"),),
                ),
                AgentDelegationSpec(
                    batch_request("agent-task-b"),
                    agent_id=AgentId("agent-2"),
                    depends_on=(AgentTaskId("agent-task-a"),),
                ),
            )
        )
