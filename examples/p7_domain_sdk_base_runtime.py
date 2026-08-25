from __future__ import annotations

from universal_agent import BaseDomainRuntime, DomainLoader, RuntimeBuilder, immutable_json
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


class InspectTool:
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
        return (InspectTool(),)

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (CriteriaEvaluator(),)


def main() -> None:
    domain = DomainLoader().load(WidgetDomain())
    components = RuntimeBuilder().build(domain)

    print(f"domain={domain.identity.name}@{domain.identity.version}")
    print(f"capabilities={len(domain.capabilities)}")
    print(f"tools={len(domain.tools)}")
    print(f"optional_hooks={len(domain.context_providers) + len(domain.world_updaters)}")
    print(f"registered_tools={len(components.tools.all())}")


if __name__ == "__main__":
    main()
