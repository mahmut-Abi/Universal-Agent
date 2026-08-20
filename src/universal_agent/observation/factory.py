from __future__ import annotations

from universal_agent.core import (
    Observation,
    TaskId,
    ToolCall,
    ToolResult,
    new_observation_id,
    utc_now,
)


class ObservationFactory:
    def from_tool_result(
        self,
        *,
        task_id: TaskId,
        call: ToolCall,
        result: ToolResult,
    ) -> Observation:
        return Observation(
            id=new_observation_id(),
            action_id=call.action_id,
            task_id=task_id,
            source=f"{call.capability}:{call.tool_name}",
            status=result.status,
            data=result.output,
            observed_at=utc_now(),
            error=result.error,
            error_code=result.error_code,
        )
