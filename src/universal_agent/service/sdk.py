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
from universal_agent.core.config_validation import (
    duplicate_values,
    parse_json_value,
    parse_non_empty_string,
    parse_non_empty_string_sequence,
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
        _non_empty_string(self.key, "success criterion key")
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
        _non_empty_string(self.description, "goal description")
        if not self.success_criteria:
            raise RuntimeSDKError("goal success criteria must not be empty")
        duplicates = duplicate_values(item.key for item in self.success_criteria)
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
        _non_empty_string(self.description, "task description")
        _non_empty_string_sequence(
            self.required_criteria,
            "task required criteria",
            empty_template="task required criteria must not include empty values",
            item_type_template="task required criteria must not include empty values",
        )

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
        compile_goal: bool = False,
    ) -> SDKRunResult:
        sdk_goal = _coerce_goal(goal, success_criteria)
        self._validate_profile(profile)
        if compile_goal:
            if task is not None:
                raise RuntimeSDKError("task must not be provided when compile_goal is true")
            return SDKRunResult.from_runtime(
                await self._service.run_compiled_goal(sdk_goal.to_runtime())
            )
        sdk_task = _coerce_task(task, required_criteria=sdk_goal.required_criteria)
        return SDKRunResult.from_runtime(
            await self._service.run_goal(sdk_goal.to_runtime(), sdk_task.to_runtime())
        )

    async def submit_compiled_goal(
        self,
        goal: SDKGoal | str,
        *,
        success_criteria: Mapping[str, JsonValue] | None = None,
        profile: str | None = None,
    ) -> SDKRunResult:
        return await self.submit_goal(
            goal,
            success_criteria=success_criteria,
            profile=profile,
            compile_goal=True,
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
    try:
        return parse_json_value(value, field)
    except ValueError as exc:
        raise RuntimeSDKError(str(exc)) from exc


def _non_empty_string(value: object, field: str) -> str:
    try:
        return parse_non_empty_string(value, field)
    except ValueError as exc:
        raise RuntimeSDKError(str(exc)) from exc


def _non_empty_string_sequence(
    value: object,
    field: str,
    *,
    empty_template: str,
    item_type_template: str,
) -> tuple[str, ...]:
    try:
        return parse_non_empty_string_sequence(
            value,
            field,
            empty_template=empty_template,
            item_type_template=item_type_template,
        )
    except ValueError as exc:
        raise RuntimeSDKError(str(exc)) from exc


__all__ = [
    "RuntimeSDKError",
    "SDKGoal",
    "SDKRunResult",
    "SDKSuccessCriterion",
    "SDKTask",
    "UniversalAgentRuntime",
]
