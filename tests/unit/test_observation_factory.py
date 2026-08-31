from __future__ import annotations

from universal_agent.core import (
    ActionId,
    ErrorCode,
    Observation,
    ObservationStatus,
    TaskId,
    ToolCall,
    ToolResult,
    immutable_json,
)
from universal_agent.observation import ObservationFactory


def make_call(capability: str = "inspect_pod", tool_name: str = "kubectl_get") -> ToolCall:
    return ToolCall(
        action_id=ActionId("action-1"),
        tool_name=tool_name,
        capability=capability,
        arguments=immutable_json({"name": "dify-api"}),
    )


def test_factory_maps_source_to_capability_and_tool_name() -> None:
    factory = ObservationFactory()
    call = make_call("inspect_pod", "kubectl_get")
    result = ToolResult(status=ObservationStatus.SUCCEEDED, output=immutable_json({"pods": 3}))

    observation = factory.from_tool_result(task_id=TaskId("task-1"), call=call, result=result)

    assert isinstance(observation, Observation)
    assert observation.source == "inspect_pod:kubectl_get"


def test_factory_maps_succeeded_result_fields() -> None:
    factory = ObservationFactory()
    call = make_call()
    result = ToolResult(
        status=ObservationStatus.SUCCEEDED,
        output=immutable_json({"ready": 2}),
    )

    observation = factory.from_tool_result(task_id=TaskId("task-1"), call=call, result=result)

    assert observation.task_id == TaskId("task-1")
    assert observation.action_id == ActionId("action-1")
    assert observation.status == ObservationStatus.SUCCEEDED
    assert observation.data == immutable_json({"ready": 2})
    assert observation.error is None
    assert observation.error_code is None


def test_factory_maps_failed_result_fields_and_error() -> None:
    factory = ObservationFactory()
    call = make_call()
    result = ToolResult(
        status=ObservationStatus.FAILED,
        output=immutable_json({}),
        error="connection refused",
        error_code=ErrorCode.TOOL_FAILURE,
    )

    observation = factory.from_tool_result(task_id=TaskId("task-1"), call=call, result=result)

    assert observation.status == ObservationStatus.FAILED
    assert observation.error == "connection refused"
    assert observation.error_code == ErrorCode.TOOL_FAILURE


def test_factory_assigns_fresh_observation_id_and_observed_at() -> None:
    factory = ObservationFactory()
    call = make_call()
    result = ToolResult(status=ObservationStatus.SUCCEEDED)

    first = factory.from_tool_result(task_id=TaskId("task-1"), call=call, result=result)
    second = factory.from_tool_result(task_id=TaskId("task-1"), call=call, result=result)

    assert first.id != second.id
    assert first.observed_at is not None
    assert second.observed_at is not None
