from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypedDict

from jinja2 import Environment

from universal_agent.core import dumps_json

_TEMPLATE_ENV = Environment(autoescape=False, lstrip_blocks=True)

_RUNTIME_STUB_TEMPLATE = _TEMPLATE_ENV.from_string(
    """from __future__ import annotations

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
        return immutable_json({"scaffold": True, "arguments": dict(arguments)})


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
        {{ api_version }},
        "Domain",
        DomainMetadata(
            {{ name }},
            {{ version }},
            {{ description }},
        ),
        {{ ontology }},
        {{ capabilities }},
        {{ evaluators }},
    )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
{% for capability in capability_rows %}
            CapabilityDefinition(
                {{ capability.name }},
                {{ capability.description }},
                CapabilityCategory.OBSERVATION,
            ),
{% endfor %}
        )

    def tools(self) -> tuple[Tool, ...]:
        return (
{% for tool in tool_rows %}
            _ScaffoldTool(
                ToolDefinition(
                    {{ tool.name }},
                    {{ tool.description }},
                    {{ tool.capabilities }},
                )
            ),
{% endfor %}
        )

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (
{% for evaluator in evaluator_rows %}
            _ScaffoldEvaluator({{ evaluator.name }}),
{% endfor %}
        )


def {{ factory_name }}() -> ScaffoldDomain:
    return ScaffoldDomain()
"""
)


class _RuntimeStubCapability(TypedDict):
    name: str
    description: str


class _RuntimeStubTool(TypedDict):
    name: str
    description: str
    capabilities: str


class _RuntimeStubEvaluator(TypedDict):
    name: str


def runtime_stub_source(manifest: Any, factory_name: str) -> str:
    return _RUNTIME_STUB_TEMPLATE.render(
        api_version=_py_string(manifest.api_version),
        name=_py_string(manifest.name),
        version=_py_string(manifest.version),
        description=_py_string(manifest.description),
        ontology=_py_string_tuple(manifest.ontology),
        capabilities=_py_string_tuple(manifest.capabilities),
        evaluators=_py_string_tuple(manifest.evaluators),
        capability_rows=_runtime_stub_capabilities(manifest),
        tool_rows=_runtime_stub_tools(manifest),
        evaluator_rows=_runtime_stub_evaluators(manifest),
        factory_name=factory_name,
    )


def _runtime_stub_capabilities(manifest: Any) -> tuple[_RuntimeStubCapability, ...]:
    return tuple(
        {
            "name": _py_string(capability),
            "description": _py_string(f"Scaffold capability: {capability}"),
        }
        for capability in manifest.capabilities
    )


def _runtime_stub_tools(manifest: Any) -> tuple[_RuntimeStubTool, ...]:
    return tuple(
        {
            "name": _py_string(tool),
            "description": _py_string(f"Scaffold tool: {tool}"),
            "capabilities": _py_string_tuple(manifest.capabilities),
        }
        for tool in manifest.tools
    )


def _runtime_stub_evaluators(manifest: Any) -> tuple[_RuntimeStubEvaluator, ...]:
    return tuple({"name": _py_string(evaluator)} for evaluator in manifest.evaluators)


def _py_string(value: str) -> str:
    return dumps_json(value)


def _py_string_tuple(values: Sequence[str]) -> str:
    if not values:
        return "()"
    if len(values) == 1:
        return f"({_py_string(values[0])},)"
    return "(" + ", ".join(_py_string(value) for value in values) + ")"
