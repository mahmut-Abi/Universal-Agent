from __future__ import annotations

import pytest

from universal_agent import (
    DomainLoader,
    DomainRuntimeSpec,
    DomainValidationError,
    build_domain_runtime,
    immutable_json,
)
from universal_agent.core import (
    CapabilityCategory,
    CapabilityDefinition,
    JsonMapping,
    ToolDefinition,
)
from universal_agent.evaluation import CriteriaEvaluator


class InspectWidgetTool:
    definition = ToolDefinition(
        "inspect_widget",
        "Inspect widget state",
        ("inspect_widget",),
        required_arguments=("name",),
    )

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"name": arguments["name"], "healthy": True})


class UnknownCapabilityTool:
    definition = ToolDefinition(
        "inspect_widget",
        "Inspect widget state",
        ("unknown_capability",),
    )

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"healthy": True})


def inspect_capability() -> CapabilityDefinition:
    return CapabilityDefinition(
        "inspect_widget",
        "Inspect widget health",
        CapabilityCategory.OBSERVATION,
    )


def test_domain_runtime_spec_builds_loader_compatible_runtime() -> None:
    spec = DomainRuntimeSpec(
        name="widget",
        version="1.0.0",
        description="Widget inspection Domain",
        ontology=("Widget",),
        capabilities=(inspect_capability(),),
        tools=(InspectWidgetTool(),),
        evaluators=(CriteriaEvaluator(),),
    )

    active = DomainLoader().load(build_domain_runtime(spec))

    assert spec.identity.name == "widget"
    assert spec.capability_names == ("inspect_widget",)
    assert spec.tool_names == ("inspect_widget",)
    assert spec.evaluator_names == ("criteria",)
    assert spec.manifest.metadata.name == "widget"
    assert active.identity == spec.identity
    assert active.manifest.capability_names == ("inspect_widget",)
    assert active.manifest.evaluator_names == ("criteria",)
    assert active.tools[0].definition.required_arguments == ("name",)


def test_domain_runtime_spec_rejects_duplicate_declarations() -> None:
    with pytest.raises(DomainValidationError, match="duplicate capabilities"):
        DomainRuntimeSpec(
            name="widget",
            version="1.0.0",
            description="Widget inspection Domain",
            capabilities=(inspect_capability(), inspect_capability()),
            tools=(InspectWidgetTool(),),
            evaluators=(CriteriaEvaluator(),),
        )


def test_domain_runtime_spec_still_uses_loader_for_cross_reference_validation() -> None:
    runtime = build_domain_runtime(
        DomainRuntimeSpec(
            name="widget",
            version="1.0.0",
            description="Widget inspection Domain",
            capabilities=(inspect_capability(),),
            tools=(UnknownCapabilityTool(),),
            evaluators=(CriteriaEvaluator(),),
        )
    )

    with pytest.raises(DomainValidationError, match="references unknown capabilities"):
        DomainLoader().load(runtime)
