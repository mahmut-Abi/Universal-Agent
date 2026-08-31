from __future__ import annotations

import pytest

from universal_agent.context import DomainContextProvider
from universal_agent.core import (
    AgentState,
    CapabilityCategory,
    CapabilityDefinition,
    ContextFragment,
    DomainIdentity,
    DomainManifest,
    DomainMetadata,
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    Goal,
    JsonMapping,
    ObservationStatus,
    PendingAction,
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
    BaseDomainRuntime,
    DomainComposition,
    DomainLoader,
    DomainManager,
    DomainNotFoundError,
    DomainValidationError,
    RuntimeBuilder,
)
from universal_agent.evaluation import CriteriaEvaluator, Evaluator
from universal_agent.evidence import (
    Evidence,
    EvidenceContext,
    EvidenceExtractor,
    InMemoryEvidenceStore,
)
from universal_agent.memory import MemoryRecord
from universal_agent.observation import ObservationFactory
from universal_agent.policy import Policy
from universal_agent.recovery import RecoveryRule
from universal_agent.runtime.processing import ObservationProcessor
from universal_agent.runtime.session import hydrate_session, start_session
from universal_agent.state import SessionSnapshot
from universal_agent.tasks import (
    TaskExpander,
    TaskExpansionContext,
    TaskGraphSnapshot,
    TaskNodeSnapshot,
    TaskSpec,
)
from universal_agent.tools import Tool
from universal_agent.world import InMemoryWorldModel, WorldModel, WorldUpdater


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


class MinimalSDKDomain(BaseDomainRuntime):
    manifest = DomainManifest(
        "agent.nantian.dev/v1alpha1",
        "Domain",
        DomainMetadata("sdk", "1.0.0", "SDK domain"),
        ("Thing",),
        ("inspect",),
        ("criteria",),
    )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (CapabilityDefinition("inspect", "Inspect", CapabilityCategory.OBSERVATION),)

    def tools(self) -> tuple[Tool, ...]:
        return (TestTool(),)

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (CriteriaEvaluator(),)


class IncompleteSDKDomain(BaseDomainRuntime):
    pass


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


class RoutedExtractor:
    def __init__(self, owner: str, calls: list[str]) -> None:
        self.name = f"{owner}-extractor"
        self._owner = owner
        self._calls = calls

    def extract(self, context: EvidenceContext) -> tuple[Evidence, ...]:
        self._calls.append(self._owner)
        return (
            Evidence(
                context.session_id,
                context.task.id,
                context.observation.action_id,
                context.observation.id,
                f"{self._owner}/subject",
                f"{self._owner}_seen",
                True,
                self.name,
                observed_at=context.observation.observed_at,
            ),
        )


class RoutedWorldUpdater:
    def __init__(self, owner: str, calls: list[str]) -> None:
        self.name = f"{owner}-world"
        self._owner = owner
        self._calls = calls

    def apply(self, model: WorldModel, evidence: Evidence) -> bool:
        self._calls.append(f"{self._owner}:{evidence.claim}")
        return model.apply_fact(evidence)


class RoutedTaskExpander:
    def __init__(self, owner: str, capability: str, calls: list[str]) -> None:
        self.name = f"{owner}-expander"
        self.capability_names = (capability,)
        self._owner = owner
        self._calls = calls

    def expand(self, context: TaskExpansionContext) -> tuple[TaskSpec, ...]:
        self._calls.append(self._owner)
        if context.world.value_for(f"{self._owner}_seen") is not True:
            return ()
        return (
            TaskSpec(
                f"{self._owner}-follow-up",
                f"{self._owner} follow-up",
                (),
                (context.task.id,),
            ),
        )


class RoutedEvaluator:
    def __init__(self, owner: str) -> None:
        self.name = f"{owner}-evaluator"
        self._owner = owner

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        key = f"{self._owner}_seen"
        complete = context.satisfied_criteria.get(key) is True
        return EvaluationResult(
            EvaluationStatus.COMPLETED if complete else EvaluationStatus.INCOMPLETE,
            f"{self._owner} evidence present" if complete else f"{self._owner} evidence missing",
            self.name,
            immutable_json({key: complete} if complete else {}),
            complete,
            complete,
        )


class RoutedDomain:
    def __init__(
        self,
        owner: str,
        *,
        extractor_calls: list[str],
        updater_calls: list[str],
        expander_calls: list[str],
    ) -> None:
        self._owner = owner
        self._capability = f"inspect_{owner}"
        self._tool_name = f"{owner}_tool"
        self._extractor = RoutedExtractor(owner, extractor_calls)
        self._updater = RoutedWorldUpdater(owner, updater_calls)
        self._expander = RoutedTaskExpander(owner, self._capability, expander_calls)
        self._evaluator = RoutedEvaluator(owner)
        self.manifest = DomainManifest(
            "agent.nantian.dev/v1alpha1",
            "Domain",
            DomainMetadata(owner, "1.0.0", owner),
            ("Thing",),
            (self._capability,),
            (self._evaluator.name,),
        )

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
        return (self._evaluator,)

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return ()

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
        return (self._extractor,)

    def world_updaters(self) -> tuple[WorldUpdater, ...]:
        return (self._updater,)

    def task_expanders(self) -> tuple[TaskExpander, ...]:
        return (self._expander,)

    def recovery_rules(self) -> tuple[RecoveryRule, ...]:
        return ()

    def memories(self) -> tuple[MemoryRecord, ...]:
        return ()


@pytest.mark.unit
def test_base_domain_runtime_defaults_optional_extension_hooks() -> None:
    loaded = DomainLoader().load(MinimalSDKDomain())

    assert loaded.identity == DomainIdentity("sdk", "1.0.0")
    assert loaded.policies == ()
    assert loaded.context_providers == ()
    assert loaded.evidence_extractors == ()
    assert loaded.world_updaters == ()
    assert loaded.task_expanders == ()
    assert loaded.recovery_rules == ()
    assert loaded.action_argument_providers == ()
    assert loaded.memories == ()


@pytest.mark.unit
def test_base_domain_runtime_requires_core_contract_methods() -> None:
    with pytest.raises(NotImplementedError, match="manifest"):
        _ = IncompleteSDKDomain().manifest


@pytest.mark.unit
def test_domain_loader_activates_structured_domain() -> None:
    active = DomainLoader().load(TestDomain())
    assert active.manifest.metadata.name == "test"
    assert active.capabilities[0].name == "inspect"


@pytest.mark.unit
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


@pytest.mark.behavior
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


@pytest.mark.behavior
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


@pytest.mark.behavior
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


@pytest.mark.behavior
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


@pytest.mark.behavior
def test_observation_processor_routes_owned_domain_processing_components() -> None:
    extractor_calls: list[str] = []
    updater_calls: list[str] = []
    expander_calls: list[str] = []
    loader = DomainLoader()
    alpha = loader.load(
        RoutedDomain(
            "alpha",
            extractor_calls=extractor_calls,
            updater_calls=updater_calls,
            expander_calls=expander_calls,
        )
    )
    beta = loader.load(
        RoutedDomain(
            "beta",
            extractor_calls=extractor_calls,
            updater_calls=updater_calls,
            expander_calls=expander_calls,
        )
    )
    components = RuntimeBuilder().build(DomainComposition((alpha, beta)))
    task = Task("Inspect beta", ("beta_seen",))
    state = AgentState(
        session_id=new_session_id(),
        goal=Goal("Verify beta", (SuccessCriterion("beta_seen", True),)),
        current_task=task,
    )
    session = start_session(state, components)
    action_id = new_action_id()
    observation = ObservationFactory().from_tool_result(
        task_id=task.id,
        call=ToolCall(
            action_id,
            "beta_tool",
            "inspect_beta",
            immutable_json(),
            domain_name="beta",
            domain_version="1.0.0",
        ),
        result=ToolResult(ObservationStatus.SUCCEEDED, immutable_json({"raw": True})),
    )
    action = PendingAction(
        action_id,
        "inspect_beta",
        "beta_tool",
        "beta/subject",
        immutable_json(),
        "beta",
        "1.0.0",
    )

    processed = ObservationProcessor(components).process(session, observation, action=action)

    assert extractor_calls == ["beta"]
    assert updater_calls == ["beta:beta_seen"]
    assert expander_calls == ["beta"]
    assert [item.domain_name for item in processed.evidence] == ["beta"]
    assert [item.domain_version for item in processed.evidence] == ["1.0.0"]
    assert session.world().value_for("beta_seen", subject="beta/subject") is True
    assert session.world().value_for("alpha_seen", subject="alpha/subject") is None
    assert [item.description for item in processed.created_tasks] == ["beta follow-up"]
    assert processed.evaluation is not None
    assert processed.evaluation.evaluator_name == "beta-evaluator"


@pytest.mark.behavior
def test_hydrate_session_replays_world_updates_using_evidence_owner() -> None:
    extractor_calls: list[str] = []
    updater_calls: list[str] = []
    expander_calls: list[str] = []
    loader = DomainLoader()
    alpha = loader.load(
        RoutedDomain(
            "alpha",
            extractor_calls=extractor_calls,
            updater_calls=updater_calls,
            expander_calls=expander_calls,
        )
    )
    beta = loader.load(
        RoutedDomain(
            "beta",
            extractor_calls=extractor_calls,
            updater_calls=updater_calls,
            expander_calls=expander_calls,
        )
    )
    components = RuntimeBuilder().build(DomainComposition((alpha, beta)))
    task = Task("Inspect beta", ("beta_seen",))
    state = AgentState(
        session_id=new_session_id(),
        goal=Goal("Verify beta", (SuccessCriterion("beta_seen", True),)),
        current_task=task,
    )
    action_id = new_action_id()
    observation = ObservationFactory().from_tool_result(
        task_id=task.id,
        call=ToolCall(action_id, "beta_tool", "inspect_beta", immutable_json()),
        result=ToolResult(ObservationStatus.SUCCEEDED, immutable_json({"raw": True})),
    )
    evidence = Evidence(
        state.session_id,
        task.id,
        action_id,
        observation.id,
        "beta/subject",
        "beta_seen",
        True,
        "beta-extractor",
        observed_at=observation.observed_at,
        domain_name="beta",
        domain_version="1.0.0",
    )
    snapshot = SessionSnapshot(
        state,
        TaskGraphSnapshot((TaskNodeSnapshot("root", task, ()),), task.id),
        (evidence,),
        "alpha",
        "1.0.0",
        components.domain_composition.identities,
    )

    session = hydrate_session(snapshot, components)

    assert updater_calls == ["beta:beta_seen"]
    assert session.world().value_for("beta_seen", subject="beta/subject") is True
    assert session.world().value_for("alpha_seen", subject="alpha/subject") is None


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
def test_domain_manager_reports_missing_and_ambiguous_domains() -> None:
    manager = DomainManager((NamedDomain("alpha", "inspect_alpha", "alpha_inspect"),))

    with pytest.raises(DomainNotFoundError, match="domain not registered: beta"):
        manager.activate_by_name(("beta",))

    manager.register(NamedDomain("alpha", "inspect_alpha_v2", "alpha_inspect_v2", version="2.0.0"))

    with pytest.raises(AmbiguousDomainError, match="multiple registered versions"):
        manager.activate_by_name(("alpha",))


@pytest.mark.unit
def test_domain_manager_rejects_duplicate_registration() -> None:
    domain = NamedDomain("alpha", "inspect_alpha", "alpha_inspect")
    manager = DomainManager((domain,))

    with pytest.raises(DomainValidationError, match="domain already registered"):
        manager.register(domain)


@pytest.mark.unit
def test_domain_composition_rejects_duplicate_capabilities() -> None:
    loader = DomainLoader()
    alpha = loader.load(NamedDomain("alpha", "inspect", "alpha_inspect"))
    beta = loader.load(NamedDomain("beta", "inspect", "beta_inspect"))

    with pytest.raises(DomainValidationError, match="duplicate capabilities"):
        DomainComposition((alpha, beta))


@pytest.mark.unit
def test_domain_composition_rejects_duplicate_tools() -> None:
    loader = DomainLoader()
    alpha = loader.load(NamedDomain("alpha", "inspect_alpha", "inspect"))
    beta = loader.load(NamedDomain("beta", "inspect_beta", "inspect"))

    with pytest.raises(DomainValidationError, match="duplicate tools"):
        DomainComposition((alpha, beta))
