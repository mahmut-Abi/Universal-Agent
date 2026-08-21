from __future__ import annotations

from typing import Protocol

from universal_agent.core import JsonMapping


class KubernetesBackend(Protocol):
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping: ...


class KubernetesMutationBackend(Protocol):
    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping: ...
