from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent import (
    DomainLoader,
    DomainPackageCompatibility,
    DomainPackageRegistry,
    DomainRuntimeSpec,
    DomainValidationError,
    build_domain_runtime,
    domain_package_scaffold_spec_from_runtime_spec,
    immutable_json,
    scaffold_domain_package,
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


def test_domain_runtime_spec_projects_to_domain_package_scaffold_metadata(
    tmp_path: Path,
) -> None:
    spec = DomainRuntimeSpec(
        name="widget",
        version="1.0.0",
        description="Widget inspection Domain",
        ontology=("Widget",),
        capabilities=(inspect_capability(),),
        tools=(InspectWidgetTool(),),
        evaluators=(CriteriaEvaluator(),),
    )

    scaffold_spec = domain_package_scaffold_spec_from_runtime_spec(
        spec,
        author="Runtime Team",
        resources=("resources/runbook.md",),
        required_tools=("widget_api",),
        compatibility=DomainPackageCompatibility(domain_api="agent.nantian.dev/v1alpha1"),
        tags=("sdk",),
    )
    package_root = tmp_path / "widget-domain"
    result = scaffold_domain_package(package_root, scaffold_spec)
    package = DomainPackageRegistry().install(package_root)

    assert result.package.identity == spec.identity
    assert package.manifest.entrypoint == "widget.domain:build_domain"
    assert package.manifest.ontology == ("Widget",)
    assert package.manifest.capabilities == ("inspect_widget",)
    assert package.manifest.tools == ("inspect_widget",)
    assert package.manifest.evaluators == ("criteria",)
    assert package.manifest.required_tools == ("widget_api",)
    assert package.manifest.compatibility.domain_api == "agent.nantian.dev/v1alpha1"
    assert package.manifest.tags == ("sdk",)
    assert (package_root / "resources" / "runbook.md").is_file()
