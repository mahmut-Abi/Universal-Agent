from __future__ import annotations

import json
import tempfile
from pathlib import Path

from universal_agent import DomainPackageRegistry, load_domain_package_runtime

DOMAIN_MODULE = """
from __future__ import annotations

from universal_agent import BaseDomainRuntime, immutable_json
from universal_agent.core import (
    CapabilityCategory,
    CapabilityDefinition,
    DomainManifest,
    DomainMetadata,
    JsonMapping,
    ToolDefinition,
)
from universal_agent.evaluation import CriteriaEvaluator, Evaluator
from universal_agent.tools import Tool


class InspectWidgetTool:
    definition = ToolDefinition("inspect_widget", "Inspect widget state", ("inspect_widget",))

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"healthy": True})


class WidgetDomain(BaseDomainRuntime):
    manifest = DomainManifest(
        "agent.nantian.dev/v1alpha1",
        "Domain",
        DomainMetadata("widget", "1.0.0", "Widget inspection Domain"),
        ("Widget",),
        ("inspect_widget",),
        ("criteria",),
    )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition(
                "inspect_widget",
                "Inspect widget health",
                CapabilityCategory.OBSERVATION,
            ),
        )

    def tools(self) -> tuple[Tool, ...]:
        return (InspectWidgetTool(),)

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (CriteriaEvaluator(),)


def build_domain() -> WidgetDomain:
    return WidgetDomain()
"""


def write_widget_package(root: Path) -> None:
    (root / "resources").mkdir(parents=True)
    (root / "resources" / "runbook.md").write_text("Inspect widget health first.\\n")
    (root / "widget_domain.py").write_text(DOMAIN_MODULE.lstrip(), encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "apiVersion": "agent.nantian.dev/v1alpha1",
                "kind": "DomainPackage",
                "metadata": {
                    "name": "widget",
                    "version": "1.0.0",
                    "description": "Widget domain package",
                    "tags": ["sdk"],
                },
                "entrypoint": "widget_domain:build_domain",
                "ontology": ["Widget"],
                "capabilities": ["inspect_widget"],
                "tools": ["inspect_widget"],
                "evaluators": ["criteria"],
                "resources": ["resources/runbook.md"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        package_root = Path(directory) / "widget-domain"
        package_root.mkdir()
        write_widget_package(package_root)

        registry = DomainPackageRegistry()
        package = registry.install(package_root)
        activation = load_domain_package_runtime(package)

        print(f"installed_package={package.identity.name}@{package.identity.version}")
        print(
            "activated_domain="
            f"{activation.active_domain.identity.name}@{activation.active_domain.identity.version}"
        )
        print(f"capabilities={','.join(activation.active_domain.manifest.capability_names)}")
        print(f"tools={','.join(tool.definition.name for tool in activation.active_domain.tools)}")


if __name__ == "__main__":
    main()
