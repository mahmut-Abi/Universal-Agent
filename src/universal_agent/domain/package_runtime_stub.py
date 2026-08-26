from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any


def runtime_stub_source(manifest: Any, factory_name: str) -> str:
    return f"""from __future__ import annotations

from universal_agent import BaseDomainRuntime, immutable_json
from universal_agent.core import (
    CapabilityCategory,
    CapabilityDefinition,
    DomainManifest,
    DomainMetadata,
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    JsonMapping,
    ToolDefinition,
)
from universal_agent.evaluation import Evaluator
from universal_agent.tools import Tool


class _ScaffoldTool:
    def __init__(self, definition: ToolDefinition) -> None:
        self.definition = definition

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({{"scaffold": True, "arguments": dict(arguments)}})


class _ScaffoldEvaluator:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        return EvaluationResult(
            EvaluationStatus.INCOMPLETE,
            "scaffold evaluator requires implementation",
            self._name,
            immutable_json(),
            False,
            False,
        )


class ScaffoldDomain(BaseDomainRuntime):
    manifest = DomainManifest(
        {_py_string(manifest.api_version)},
        "Domain",
        DomainMetadata(
            {_py_string(manifest.name)},
            {_py_string(manifest.version)},
            {_py_string(manifest.description)},
        ),
        {_py_string_tuple(manifest.ontology)},
        {_py_string_tuple(manifest.capabilities)},
        {_py_string_tuple(manifest.evaluators)},
    )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
{_runtime_stub_capabilities(manifest)}
        )

    def tools(self) -> tuple[Tool, ...]:
        return (
{_runtime_stub_tools(manifest)}
        )

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (
{_runtime_stub_evaluators(manifest)}
        )


def {factory_name}() -> ScaffoldDomain:
    return ScaffoldDomain()
"""


def _runtime_stub_capabilities(manifest: Any) -> str:
    return "\n".join(
        (
            "            CapabilityDefinition("
            f"{_py_string(capability)}, "
            f"{_py_string(f'Scaffold capability: {capability}')}, "
            "CapabilityCategory.OBSERVATION),"
        )
        for capability in manifest.capabilities
    )


def _runtime_stub_tools(manifest: Any) -> str:
    return "\n".join(
        (
            "            _ScaffoldTool(ToolDefinition("
            f"{_py_string(tool)}, "
            f"{_py_string(f'Scaffold tool: {tool}')}, "
            f"{_py_string_tuple(manifest.capabilities)})),"
        )
        for tool in manifest.tools
    )


def _runtime_stub_evaluators(manifest: Any) -> str:
    return "\n".join(
        f"            _ScaffoldEvaluator({_py_string(evaluator)}),"
        for evaluator in manifest.evaluators
    )


def _py_string(value: str) -> str:
    return json.dumps(value)


def _py_string_tuple(values: Sequence[str]) -> str:
    if not values:
        return "()"
    if len(values) == 1:
        return f"({_py_string(values[0])},)"
    return "(" + ", ".join(_py_string(value) for value in values) + ")"
