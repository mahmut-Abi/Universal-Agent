from __future__ import annotations

from universal_agent.core import (
    JsonMapping,
    RiskLevel,
    SideEffect,
    ToolDefinition,
    immutable_json,
)
from universal_agent.domains.kubernetes.backend import KubernetesMutationBackend


class KubernetesScaleTool:
    def __init__(self, backend: KubernetesMutationBackend) -> None:
        self.definition = ToolDefinition(
            name="kubernetes_scale_workload",
            description="Scale a Kubernetes workload within policy bounds",
            capabilities=("scale_workload",),
            required_arguments=("name", "namespace", "replicas"),
            side_effect=SideEffect.REVERSIBLE,
            risk=RiskLevel.MEDIUM,
            argument_schema=immutable_json(
                {
                    "required": ["name", "namespace", "replicas"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "namespace": {"type": "string", "minLength": 1},
                        "replicas": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": False,
                }
            ),
        )
        self._backend = backend

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        result = await self._backend.mutate("scale_workload", arguments)
        return immutable_json(result)
