from __future__ import annotations

import pytest

from universal_agent.context import DomainContextProvider
from universal_agent.core import (
    AgentState,
    CapabilityCategory,
    CapabilityDefinition,
    ContextFragment,
    DomainManifest,
    DomainMetadata,
    EvaluationContext,
    Goal,
    JsonMapping,
    ObservationStatus,
    SuccessCriterion,
    Task,
    ToolCall,
    ToolDefinition,
    ToolResult,
    immutable_json,
    new_action_id,
    new_session_id,
)
from universal_agent.domain import (
    AmbiguousDomainError,
    DomainComposition,
    DomainLoader,
    DomainManager,
    DomainNotFoundError,
    DomainValidationError,
    RuntimeBuilder,
)
from universal_agent.evaluation import CriteriaEvaluator, Evaluator
from universal_agent.evidence import EvidenceExtractor, InMemoryEvidenceStore
from universal_agent.memory import MemoryRecord
from universal_agent.observation import ObservationFactory
from universal_agent.policy import Policy
from universal_agent.recovery import RecoveryRule
from universal_agent.tasks import TaskExpander
from universal_agent.tools import Tool
from universal_agent.world import InMemoryWorldModel, WorldUpdater


class TestTool:
    definition = ToolDefinition("inspect", "Inspect", ("inspect",))

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return immutable_json()


class TestDomain:
    manifest = DomainManifest(
        "agent.nantian.dev/v1alpha1",
        "Domain",
        DomainMetadata("test", "1.0.0", "Test"),
        ("Thing",),
        ("inspect",),
        ("criteria",),
    )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (CapabilityDefinition("inspect", "Inspect", CapabilityCategory.OBSERVATION),)

    def tools(self) -> tuple[Tool, ...]:
        return (TestTool(),)

    def policies(self) -> tuple[Policy, ...]:
        return ()

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (CriteriaEvaluator(),)

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return ()

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
        return ()

    def world_updaters(self) -> tuple[WorldUpdater, ...]:
        return ()

    def task_expanders(self) -> tuple[TaskExpander, ...]:
        return ()

    def recovery_rules(self) -> tuple[RecoveryRule, ...]:
        return ()

    def memories(self) -> tuple[MemoryRecord, ...]:
        return ()


class NamedTool:
    def __init__(self, name: str, capability: str) -> None:
        self.definition = ToolDefinition(name, name, (capability,))

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return immutable_json()


class NamedContextProvider:
    name = "context"

    def provide(self, state: AgentState) -> tuple[ContextFragment, ...]:
        return (ContextFragment("scope", "shared key", 10),)


class NamedDomain:
    def __init__(
        self,
        name: str,
        capability: str,
        tool_name: str,
        version: str = "1.0.0",
    ) -> None:
        self.manifest = DomainManifest(
            "agent.nantian.dev/v1alpha1",
            "Domain",
            DomainMetadata(name, version, name),
            ("Thing",),
            (capability,),
            ("criteria",),
        )
        self._capability = capability
        self._tool_name = tool_name

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition(
                self._capability,
                self._capability,
                CapabilityCategory.OBSERVATION,
            ),
        )

    def tools(self) -> tuple[Tool, ...]:
        return (NamedTool(self._tool_name, self._capability),)

    def policies(self) -> tuple[Policy, ...]:
        return ()

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (CriteriaEvaluator(),)

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return (NamedContextProvider(),)

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
        return ()

    def world_updaters(self) -> tuple[WorldUpdater, ...]:
        return ()

    def task_expanders(self) -> tuple[TaskExpander, ...]:
        return ()

    def recovery_rules(self) -> tuple[RecoveryRule, ...]:
        return ()

    def memories(self) -> tuple[MemoryRecord, ...]:
        return ()


def test_domain_loader_activates_structured_domain() -> None:
    active = DomainLoader().load(TestDomain())
    assert active.manifest.metadata.name == "test"
    assert active.capabilities[0].name == "inspect"


def test_domain_loader_rejects_invalid_capability_reference() -> None:
    domain = TestDomain()
    domain.manifest = DomainManifest(
        domain.manifest.api_version,
        domain.manifest.kind,
        domain.manifest.metadata,
        domain.manifest.ontology,
        ("missing",),
        domain.manifest.evaluator_names,
    )
    with pytest.raises(DomainValidationError, match="capability references"):
        DomainLoader().load(domain)


def test_domain_loader_rejects_empty_evaluator_set() -> None:
    class NoEvaluatorDomain(TestDomain):
        manifest = DomainManifest(
            "agent.nantian.dev/v1alpha1",
            "Domain",
            DomainMetadata("no-evaluator", "1.0.0", "No evaluator"),
            ("Thing",),
            ("inspect",),
            (),
        )

        def evaluators(self) -> tuple[Evaluator, ...]:
            return ()

    with pytest.raises(DomainValidationError, match="requires at least one evaluator"):
        DomainLoader().load(NoEvaluatorDomain())


def test_criteria_evaluator_requires_matching_observation_state() -> None:
    goal = Goal("Verify", (SuccessCriterion("healthy", True),))
    task = Task("Inspect", ("healthy",))
    observation = ObservationFactory().from_tool_result(
        task_id=task.id,
        call=ToolCall(new_action_id(), "inspect", "inspect", immutable_json()),
        result=ToolResult(ObservationStatus.SUCCEEDED, immutable_json({"healthy": False})),
    )
    result = CriteriaEvaluator().evaluate(
        EvaluationContext(goal, task, observation, immutable_json({"healthy": False}))
    )
    assert result.status.value == "incomplete"


def test_runtime_builder_isolates_stores_unless_they_are_injected() -> None:
    """Two runtimes may share one store, but must not do so by accident.

    The default is isolation: that is what forces a cross-runtime test to
    recover through the session snapshot rather than through a store both
    runtimes happen to hold a reference to.
    """
    domain = DomainLoader().load(TestDomain())

    default_first = RuntimeBuilder().build(domain)
    default_second = RuntimeBuilder().build(domain)
    assert default_first.evidence_store is not default_second.evidence_store
    assert default_first.world_model is not default_second.world_model

    evidence = InMemoryEvidenceStore()
    world = InMemoryWorldModel()
    builder = RuntimeBuilder(
        evidence_store_factory=lambda: evidence,
        world_model_factory=lambda: world,
    )
    shared_first = builder.build(domain)
    shared_second = builder.build(domain)
    assert shared_first.evidence_store is shared_second.evidence_store is evidence
    assert shared_first.world_model is shared_second.world_model is world


def test_runtime_builder_composes_multiple_domains() -> None:
    loader = DomainLoader()
    alpha = loader.load(NamedDomain("alpha", "inspect_alpha", "alpha_inspect"))
    beta = loader.load(NamedDomain("beta", "inspect_beta", "beta_inspect"))

    components = RuntimeBuilder().build(DomainComposition((alpha, beta)))

    assert components.active_domain is alpha
    assert components.domain_composition.identities[0].name == "alpha"
    assert [item.name for item in components.capabilities.all()] == [
        "inspect_alpha",
        "inspect_beta",
    ]
    assert components.resolver.resolve("inspect_beta")[1].definition.name == "beta_inspect"
    assert components.memory_scope is None

    state = AgentState(
        session_id=new_session_id(),
        goal=Goal("g", ()),
        current_task=Task("t", ()),
    )
    fragments = tuple(
        fragment
        for provider in components.context_providers
        for fragment in provider.provide(state)
    )
    assert [fragment.key for fragment in fragments] == ["alpha.scope", "beta.scope"]


def test_domain_manager_registers_and_activates_domains_in_order() -> None:
    manager = DomainManager(
        (
            NamedDomain("alpha", "inspect_alpha", "alpha_inspect"),
            NamedDomain("beta", "inspect_beta", "beta_inspect"),
        )
    )

    activation = manager.activate()

    assert [identity.name for identity in manager.identities()] == ["alpha", "beta"]
    assert [identity.name for identity in activation.identities] == ["alpha", "beta"]
    assert activation.primary.identity.name == "alpha"


def test_domain_manager_activates_explicit_identity_order() -> None:
    manager = DomainManager(
        (
            NamedDomain("alpha", "inspect_alpha", "alpha_inspect"),
            NamedDomain("beta", "inspect_beta", "beta_inspect"),
        )
    )

    activation = manager.activate_by_name(("beta", "alpha"))

    assert [identity.name for identity in activation.identities] == ["beta", "alpha"]
    assert activation.primary.identity.name == "beta"


def test_domain_manager_reports_missing_and_ambiguous_domains() -> None:
    manager = DomainManager((NamedDomain("alpha", "inspect_alpha", "alpha_inspect"),))

    with pytest.raises(DomainNotFoundError, match="domain not registered: beta"):
        manager.activate_by_name(("beta",))

    manager.register(NamedDomain("alpha", "inspect_alpha_v2", "alpha_inspect_v2", version="2.0.0"))

    with pytest.raises(AmbiguousDomainError, match="multiple registered versions"):
        manager.activate_by_name(("alpha",))


def test_domain_manager_rejects_duplicate_registration() -> None:
    domain = NamedDomain("alpha", "inspect_alpha", "alpha_inspect")
    manager = DomainManager((domain,))

    with pytest.raises(DomainValidationError, match="domain already registered"):
        manager.register(domain)


def test_domain_composition_rejects_duplicate_capabilities() -> None:
    loader = DomainLoader()
    alpha = loader.load(NamedDomain("alpha", "inspect", "alpha_inspect"))
    beta = loader.load(NamedDomain("beta", "inspect", "beta_inspect"))

    with pytest.raises(DomainValidationError, match="duplicate capabilities"):
        DomainComposition((alpha, beta))


def test_domain_composition_rejects_duplicate_tools() -> None:
    loader = DomainLoader()
    alpha = loader.load(NamedDomain("alpha", "inspect_alpha", "inspect"))
    beta = loader.load(NamedDomain("beta", "inspect_beta", "inspect"))

    with pytest.raises(DomainValidationError, match="duplicate tools"):
        DomainComposition((alpha, beta))
