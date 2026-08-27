# Domain SDK

The Domain SDK is the P7 authoring surface for adding a Domain Runtime without
modifying the Kernel.

Domain code owns domain-specific ontology, capabilities, tools, policies,
evaluators, context providers, Evidence extractors, World updaters, task
expanders, recovery rules and prior memory. The Kernel only consumes those
objects through the `DomainRuntime` interface and validates them through
`DomainLoader`.

## Authoring Options

Use `BaseDomainRuntime` when a Domain needs custom methods or richer internal
composition:

```python
class WidgetDomain(BaseDomainRuntime):
    manifest = DomainManifest(...)

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return ...

    def tools(self) -> tuple[Tool, ...]:
        return ...

    def evaluators(self) -> tuple[Evaluator, ...]:
        return ...
```

Use `DomainRuntimeSpec` when the Domain can be described by declared runtime
objects and the manifest should be derived from those declarations:

```python
spec = DomainRuntimeSpec(
    name="widget",
    version="1.0.0",
    description="Widget inspection Domain",
    ontology=("Widget",),
    capabilities=(inspect_widget_capability,),
    tools=(inspect_widget_tool,),
    evaluators=(CriteriaEvaluator(),),
)
runtime = build_domain_runtime(spec)
active = DomainLoader().load(runtime)
```

`DomainRuntimeSpec` derives manifest capability and evaluator names from the
concrete runtime objects. `DomainLoader` still performs cross-reference
validation, including tool capability references and evaluator registration.

## Package Metadata

Domain package registry operations are metadata-only. Installing or discovering
a package must not import Domain code or mutate Kernel runtime state. Package
roots may use `manifest.json`, `manifest.yaml` or `manifest.yml`; scaffolding
continues to write `manifest.json` by default for backward compatibility.

Use `domain_package_scaffold_spec_from_runtime_spec` when a package scaffold
should stay aligned with a declarative runtime spec:

```python
scaffold_spec = domain_package_scaffold_spec_from_runtime_spec(
    spec,
    author="Runtime Team",
    resources=("resources/runbook.md",),
    required_tools=("widget_api",),
    tags=("sdk",),
)
scaffold_domain_package(Path(".tmp/widget-domain"), scaffold_spec)
```

The scaffold creates the standard package directories from the architecture
spec, including `ontology/`, `capabilities/`, `tools/`, `policies/`,
`procedures/`, `knowledge/`, `evaluators/`, `context_providers/`, `prompts/`,
`resources/` and `tests/`.

Set `DomainPackageScaffoldSpec.runtime_stub=True`, or pass CLI
`--runtime-stub`, only when the scaffold should also write starter Python
Domain code at the declared entrypoint. The stub is still inert metadata until
`load_domain_package_runtime` is called explicitly.

Explicit runtime activation remains a separate seam:

```python
activation = load_domain_package_runtime(Path(".tmp/widget-domain"))
```

That call imports the declared entrypoint, validates it through `DomainLoader`
and rejects identity, capability, tool or evaluator drift between package
metadata and runtime code.

## CLI Checks

Useful local commands:

```bash
.venv/bin/python examples/p7_domain_sdk_base_runtime.py
.venv/bin/python examples/p7_domain_sdk_runtime_spec.py
.venv/bin/python -m universal_agent.cli domain-packages scaffold widget --description "Widget Domain" --output .tmp/widget-domain --capability inspect_widget --tool inspect_widget --evaluator criteria --runtime-stub
.venv/bin/python -m universal_agent.cli domain-packages verify --local-paths
.venv/bin/python -m universal_agent.cli domain-packages load-runtime .tmp/widget-domain
```

Run these quality gates before committing Domain SDK changes:

```bash
.venv/bin/python -m ruff format --check src tests examples
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
.venv/bin/python -m pytest tests/unit/test_domain_sdk.py tests/unit/test_domain_package.py -q
```

## Invariants

- Domain SDK helpers must not add domain-specific branches to Kernel code.
- Package registry install/discovery remains metadata-only.
- Runtime activation must go through `DomainLoader`.
- Tool success is not task success; Domain evaluators still decide completion.
- Evidence and World Model updates remain runtime-owned and replayable.
