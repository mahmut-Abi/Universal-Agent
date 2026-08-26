from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from universal_agent.core import (
    EventId,
    Goal,
    JsonValue,
    SessionId,
    SuccessCriterion,
    Task,
)
from universal_agent.runtime import RuntimeEventBatch, RuntimeRun, SessionView
from universal_agent.service import RuntimeService


class RuntimeSDKError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SDKSuccessCriterion:
    key: str
    expected: JsonValue

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise RuntimeSDKError("success criterion key must not be empty")
        _json_value(self.expected, f"success_criteria.{self.key}")

    def to_runtime(self) -> SuccessCriterion:
        return SuccessCriterion(
            self.key,
            _json_value(self.expected, f"success_criteria.{self.key}"),
        )


@dataclass(frozen=True, slots=True)
class SDKGoal:
    description: str
    success_criteria: tuple[SDKSuccessCriterion, ...]

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise RuntimeSDKError("goal description must not be empty")
        if not self.success_criteria:
            raise RuntimeSDKError("goal success criteria must not be empty")
        duplicates = _duplicates(tuple(item.key for item in self.success_criteria))
        if duplicates:
            raise RuntimeSDKError("duplicate goal success criteria: " + ", ".join(duplicates))

    @classmethod
    def from_mapping(cls, description: str, criteria: Mapping[str, JsonValue]) -> SDKGoal:
        return cls(
            description,
            tuple(
                SDKSuccessCriterion(key, _json_value(value, f"success_criteria.{key}"))
                for key, value in criteria.items()
            ),
        )

    @property
    def required_criteria(self) -> tuple[str, ...]:
        return tuple(item.key for item in self.success_criteria)

    def to_runtime(self) -> Goal:
        return Goal(
            self.description,
            tuple(criterion.to_runtime() for criterion in self.success_criteria),
        )


@dataclass(frozen=True, slots=True)
class SDKTask:
    description: str
    required_criteria: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise RuntimeSDKError("task description must not be empty")
        _validate_strings("task required criteria", self.required_criteria)

    def to_runtime(self) -> Task:
        return Task(self.description, self.required_criteria)


@dataclass(frozen=True, slots=True)
class SDKRunResult:
    session_id: str
    status: str
    reason: str
    goal_status: str
    current_task_status: str
    iteration: int
    error_code: str | None = None
    user_message: str | None = None

    @classmethod
    def from_runtime(cls, run: RuntimeRun) -> SDKRunResult:
        return cls(
            session_id=str(run.result.session_id),
            status=run.result.status.value,
            reason=run.result.reason,
            goal_status=run.session.goal_status.value,
            current_task_status=run.session.current_task_status.value,
            iteration=run.session.iteration,
            error_code=None if run.result.error_code is None else run.result.error_code.value,
            user_message=run.result.user_message,
        )


class UniversalAgentRuntime:
    """Public embedding SDK over RuntimeService projections and lifecycle calls."""

    def __init__(
        self,
        service: RuntimeService,
        *,
        default_profile: str | None = None,
    ) -> None:
        self._service = service
        self._default_profile = default_profile

    async def submit_goal(
        self,
        goal: SDKGoal | str,
        *,
        success_criteria: Mapping[str, JsonValue] | None = None,
        task: SDKTask | str | None = None,
        profile: str | None = None,
    ) -> SDKRunResult:
        sdk_goal = _coerce_goal(goal, success_criteria)
        sdk_task = _coerce_task(task, required_criteria=sdk_goal.required_criteria)
        self._validate_profile(profile)
        return SDKRunResult.from_runtime(
            await self._service.run_goal(sdk_goal.to_runtime(), sdk_task.to_runtime())
        )

    async def resume_session(
        self,
        session_id: str,
        *,
        confirmed: bool | None = None,
    ) -> SDKRunResult:
        return SDKRunResult.from_runtime(
            await self._service.resume_session(SessionId(session_id), confirmed=confirmed)
        )

    async def pause_session(
        self,
        session_id: str,
        *,
        reason: str = "session paused",
    ) -> SDKRunResult:
        return SDKRunResult.from_runtime(
            await self._service.pause_session(SessionId(session_id), reason=reason)
        )

    async def cancel_session(
        self,
        session_id: str,
        *,
        reason: str = "session cancelled",
    ) -> SDKRunResult:
        return SDKRunResult.from_runtime(
            await self._service.cancel_session(SessionId(session_id), reason=reason)
        )

    async def get_session(self, session_id: str) -> SessionView:
        return await self._service.get_session(SessionId(session_id))

    async def stream_events(
        self,
        session_id: str,
        *,
        after_event_id: str | None = None,
        limit: int | None = None,
    ) -> RuntimeEventBatch:
        return await self._service.stream_events(
            SessionId(session_id),
            after_event_id=None if after_event_id is None else EventId(after_event_id),
            limit=limit,
        )

    def _validate_profile(self, profile: str | None) -> None:
        selected = profile if profile is not None else self._default_profile
        if selected is None:
            return
        error = self._service.profile_selection_error(selected)
        if error is not None:
            raise RuntimeSDKError(error)


def _coerce_goal(
    goal: SDKGoal | str,
    success_criteria: Mapping[str, JsonValue] | None,
) -> SDKGoal:
    if isinstance(goal, SDKGoal):
        if success_criteria is not None:
            raise RuntimeSDKError("success_criteria must not be provided with SDKGoal")
        return goal
    if success_criteria is None:
        raise RuntimeSDKError("success_criteria is required when goal is a string")
    return SDKGoal.from_mapping(goal, success_criteria)


def _coerce_task(task: SDKTask | str | None, *, required_criteria: tuple[str, ...]) -> SDKTask:
    if isinstance(task, SDKTask):
        return task
    if isinstance(task, str):
        return SDKTask(task, required_criteria)
    return SDKTask("Run goal", required_criteria)


def _json_value(value: JsonValue, field: str) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list):
        return [_json_value(item, f"{field}[]") for item in value]
    if isinstance(value, dict):
        payload: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise RuntimeSDKError(f"{field} keys must be strings")
            payload[key] = _json_value(item, f"{field}.{key}")
        return payload
    raise RuntimeSDKError(f"{field} must be JSON-compatible")


def _validate_strings(label: str, values: tuple[str, ...]) -> None:
    for value in values:
        if not value.strip():
            raise RuntimeSDKError(f"{label} must not include empty values")


def _duplicates(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return tuple(sorted(duplicates))


__all__ = [
    "RuntimeSDKError",
    "SDKGoal",
    "SDKRunResult",
    "SDKSuccessCriterion",
    "SDKTask",
    "UniversalAgentRuntime",
]
