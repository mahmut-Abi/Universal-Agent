from __future__ import annotations

from universal_agent import DomainLoader, DomainRuntimeSpec, RuntimeBuilder, build_domain_runtime
from universal_agent.core import (
    CapabilityCategory,
    CapabilityDefinition,
    JsonMapping,
    ToolDefinition,
    immutable_json,
)
from universal_agent.evaluation import CriteriaEvaluator


class InspectWidgetTool:
    definition = ToolDefinition("inspect_widget", "Inspect widget state", ("inspect_widget",))

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"healthy": True})


def main() -> None:
    runtime = build_domain_runtime(
        DomainRuntimeSpec(
            name="widget",
            version="1.0.0",
            description="Widget inspection Domain",
            ontology=("Widget",),
            capabilities=(
                CapabilityDefinition(
                    "inspect_widget",
                    "Inspect widget health",
                    CapabilityCategory.OBSERVATION,
                ),
            ),
            tools=(InspectWidgetTool(),),
            evaluators=(CriteriaEvaluator(),),
        )
    )
    domain = DomainLoader().load(runtime)
    components = RuntimeBuilder().build(domain)

    print(f"domain={domain.identity.name}@{domain.identity.version}")
    print(f"capabilities={len(domain.capabilities)}")
    print(f"tools={len(domain.tools)}")
    print(f"evaluators={len(domain.evaluators)}")
    print(f"registered_tools={len(components.tools.all())}")


if __name__ == "__main__":
    main()
