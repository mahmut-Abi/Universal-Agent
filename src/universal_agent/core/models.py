from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NewType
from uuid import uuid4

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from universal_agent.evidence import Evidence
    from universal_agent.world import WorldSnapshot

SessionId = NewType("SessionId", str)
GoalId = NewType("GoalId", str)
TaskId = NewType("TaskId", str)
ActionId = NewType("ActionId", str)
ObservationId = NewType("ObservationId", str)
EventId = NewType("EventId", str)

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
JsonMapping = Mapping[str, JsonValue]
RuntimeClock = Callable[[], datetime]
RuntimeIdFactory = Callable[[str], str]

_runtime_clock: ContextVar[RuntimeClock | None] = ContextVar(
    "universal_agent_runtime_clock",
    default=None,
)
_runtime_id_factory: ContextVar[RuntimeIdFactory | None] = ContextVar(
    "universal_agent_runtime_id_factory",
    default=None,
)


@contextmanager
def runtime_primitives(
    *,
    clock: RuntimeClock | None = None,
    id_factory: RuntimeIdFactory | None = None,
) -> Iterator[None]:
    clock_token = _runtime_clock.set(clock)
    id_factory_token = _runtime_id_factory.set(id_factory)
    try:
        yield
    finally:
        _runtime_id_factory.reset(id_factory_token)
        _runtime_clock.reset(clock_token)


def utc_now() -> datetime:
    clock = _runtime_clock.get()
    if clock is not None:
        return clock()
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    id_factory = _runtime_id_factory.get()
    if id_factory is not None:
        return id_factory(prefix)
    return f"{prefix}-{uuid4()}"


def new_session_id() -> SessionId:
    return SessionId(_new_id("session"))


def new_goal_id() -> GoalId:
    return GoalId(_new_id("goal"))


def new_task_id() -> TaskId:
    return TaskId(_new_id("task"))


def new_action_id() -> ActionId:
    return ActionId(_new_id("action"))


def new_observation_id() -> ObservationId:
    return ObservationId(_new_id("observation"))


def new_event_id() -> EventId:
    return EventId(_new_id("event"))


def immutable_json(values: Mapping[str, JsonValue] | None = None) -> JsonMapping:
    return MappingProxyType(dict(values or {}))


class GoalStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DecisionType(StrEnum):
    EXECUTE = "execute"
    WAIT = "wait"
    ASK_USER = "ask_user"
    FINISH = "finish"


class ObservationStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"


class ExecutionStatus(StrEnum):
    COMPLETED = "completed"
    WAITING = "waiting"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CapabilityCategory(StrEnum):
    OBSERVATION = "observation"
    MUTATION = "mutation"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SideEffect(StrEnum):
    NONE = "none"
    REVERSIBLE = "reversible"
    DESTRUCTIVE = "destructive"


class PolicyEffect(StrEnum):
    ALLOW = "allow"
    REQUIRE_CONFIRMATION = "require_confirmation"
    DENY = "deny"


class EvaluationStatus(StrEnum):
    INCOMPLETE = "incomplete"
    COMPLETED = "completed"
    FAILED = "failed"


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "validation_error"
    UNKNOWN_CAPABILITY = "unknown_capability"
    NO_CAPABILITY_TOOL = "no_capability_tool"
    UNKNOWN_TOOL = "unknown_tool"
    TOOL_FAILURE = "tool_failure"
    UNKNOWN_EXECUTION = "unknown_execution"
    TIMEOUT = "timeout"
    INVALID_STATE = "invalid_state"
    ITERATION_LIMIT = "iteration_limit"
    MODEL_FAILURE = "model_failure"
    POLICY_DENIED = "policy_denied"
    CONFIRMATION_REJECTED = "confirmation_rejected"
    EVALUATION_FAILED = "evaluation_failed"
    DOMAIN_VALIDATION_FAILED = "domain_validation_failed"
    RESOURCE_CONFLICT = "resource_conflict"


@dataclass(frozen=True, slots=True)
class DomainIdentity:
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class SuccessCriterion:
    key: str
    expected: JsonValue


@dataclass(slots=True)
class Goal:
    description: str
    success_criteria: tuple[SuccessCriterion, ...]
    id: GoalId = field(default_factory=new_goal_id)
    status: GoalStatus = GoalStatus.PENDING
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class Task:
    description: str
    required_criteria: tuple[str, ...]
    id: TaskId = field(default_factory=new_task_id)
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class Decision:
    type: DecisionType
    reason: str
    capability: str | None = None
    target: str | None = None
    arguments: JsonMapping = field(default_factory=immutable_json)
    expected_observations: tuple[str, ...] = ()
    message: str | None = None

    def validate(self) -> None:
        if not self.reason.strip():
            raise ValueError("decision reason must not be empty")
        if self.type is DecisionType.EXECUTE:
            if not self.capability:
                raise ValueError("execute decision requires capability")
            if not self.expected_observations:
                raise ValueError("execute decision requires expected_observations")
        elif self.capability is not None or self.arguments or self.target is not None:
            raise ValueError(f"{self.type.value} decision cannot include an action")
        if self.type is DecisionType.ASK_USER and not self.message:
            raise ValueError("ask_user decision requires message")


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    name: str
    description: str
    category: CapabilityCategory
    risk: RiskLevel = RiskLevel.LOW


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    capabilities: tuple[str, ...]
    required_arguments: tuple[str, ...] = ()
    side_effect: SideEffect = SideEffect.NONE
    risk: RiskLevel = RiskLevel.LOW
    timeout_seconds: float = 10.0
    priority: int = 100


@dataclass(frozen=True, slots=True)
class ToolCall:
    action_id: ActionId
    tool_name: str
    capability: str
    arguments: JsonMapping
    target: str | None = None
    domain_name: str = ""
    domain_version: str = ""
    idempotency_key: str = ""
    parameters_hash: str = ""
    attempt: int = 1
    resource_key: str = ""
    resource_version: str | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    status: ObservationStatus
    output: JsonMapping = field(default_factory=immutable_json)
    error: str | None = None
    error_code: ErrorCode | None = None


@dataclass(frozen=True, slots=True)
class Observation:
    id: ObservationId
    action_id: ActionId
    task_id: TaskId
    source: str
    status: ObservationStatus
    data: JsonMapping
    observed_at: datetime
    error: str | None = None
    error_code: ErrorCode | None = None


@dataclass(frozen=True, slots=True)
class CapabilitySummary:
    name: str
    description: str
    category: CapabilityCategory
    risk: RiskLevel


@dataclass(frozen=True, slots=True)
class ContextFragment:
    key: str
    content: str
    priority: int = 100


@dataclass(frozen=True, slots=True)
class DecisionContext:
    session_id: SessionId
    goal_id: GoalId
    goal_description: str
    task_id: TaskId
    task_description: str
    iteration: int
    satisfied_criteria: JsonMapping
    latest_observation: Observation | None
    capabilities: tuple[CapabilitySummary, ...]
    domain_context: tuple[ContextFragment, ...] = ()
    world_context: tuple[ContextFragment, ...] = ()
    evidence_context: tuple[ContextFragment, ...] = ()
    task_context: tuple[ContextFragment, ...] = ()
    memory_context: tuple[ContextFragment, ...] = ()
    policy_summary: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PolicyContext:
    session_id: SessionId
    goal_id: GoalId
    task_id: TaskId
    action_id: ActionId
    capability: CapabilityDefinition
    tool: ToolDefinition
    target: str | None
    arguments: JsonMapping
    environment: JsonMapping = field(default_factory=immutable_json)
    confirmed: bool = False


@dataclass(frozen=True, slots=True)
class PolicyResult:
    effect: PolicyEffect
    reason: str
    policy_name: str


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    """What an evaluator is allowed to judge a task on.

    Evidence and world are forward references: they live in packages that
    depend on core, so the annotations stay strings and only resolve for type
    checkers.
    """

    goal: Goal
    task: Task
    observation: Observation
    satisfied_criteria: JsonMapping
    evidence: tuple[Evidence, ...] = ()
    world: WorldSnapshot | None = None


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    status: EvaluationStatus
    reason: str
    evaluator_name: str
    matched_criteria: JsonMapping = field(default_factory=immutable_json)
    task_completed: bool = False
    goal_completed: bool = False


@dataclass(frozen=True, slots=True)
class PendingAction:
    action_id: ActionId
    capability: str
    tool_name: str
    target: str | None
    arguments: JsonMapping
    domain_name: str = ""
    domain_version: str = ""
    idempotency_key: str = ""
    parameters_hash: str = ""
    attempt: int = 1
    resource_key: str = ""
    resource_version: str | None = None


@dataclass(frozen=True, slots=True)
class DomainMetadata:
    name: str
    version: str
    description: str


@dataclass(frozen=True, slots=True)
class DomainManifest:
    api_version: str
    kind: str
    metadata: DomainMetadata
    ontology: tuple[str, ...]
    capability_names: tuple[str, ...]
    evaluator_names: tuple[str, ...]


@dataclass(slots=True)
class AgentState:
    session_id: SessionId
    goal: Goal
    current_task: Task
    iteration: int = 0
    satisfied_criteria: dict[str, JsonValue] = field(default_factory=dict)
    observations: list[Observation] = field(default_factory=list)
    latest_evaluation: EvaluationResult | None = None
    pending_action: PendingAction | None = None
    tasks: list[Task] = field(default_factory=list)
    recovery_attempts: dict[str, int] = field(default_factory=dict)
    termination_reason: str | None = None
    error_code: ErrorCode | None = None

    @property
    def latest_observation(self) -> Observation | None:
        return self.observations[-1] if self.observations else None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    status: ExecutionStatus
    session_id: SessionId
    goal_id: GoalId
    task_id: TaskId
    iterations: int
    reason: str
    error_code: ErrorCode | None = None
    user_message: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    type: str
    session_id: SessionId
    goal_id: GoalId
    task_id: TaskId
    id: EventId = field(default_factory=new_event_id)
    action_id: ActionId | None = None
    data: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=utc_now)
