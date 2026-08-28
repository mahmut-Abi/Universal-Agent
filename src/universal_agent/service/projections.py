from __future__ import annotations

from copy import deepcopy

from universal_agent.core import JsonValue, immutable_json
from universal_agent.domain import ActiveDomain, DomainPackage
from universal_agent.evidence import Evidence
from universal_agent.memory import MemoryRecord
from universal_agent.multi_agent import AgentInstanceRecord, AgentProfileRecord
from universal_agent.policy import Policy, PolicyRule
from universal_agent.profile import AgentProfile
from universal_agent.runtime import EvidenceView
from universal_agent.service.views import (
    DomainPackageView,
    DomainView,
    EvaluatorView,
    MemoryView,
    MultiAgentInstanceView,
    MultiAgentProfileView,
    PolicyView,
    ProfileView,
    WorldEntityView,
    WorldFactEvidenceView,
    WorldFactHistoryView,
    WorldFactView,
    WorldNeighborhoodView,
    WorldRelationView,
)
from universal_agent.world import (
    WorldEntity,
    WorldFact,
    WorldFactEvidence,
    WorldFactHistory,
    WorldNeighborhood,
    WorldRelation,
    WorldSnapshot,
)


def domain_view(domain: ActiveDomain, *, primary: bool) -> DomainView:
    metadata = domain.manifest.metadata
    return DomainView(
        name=metadata.name,
        version=metadata.version,
        description=metadata.description,
        primary=primary,
        ontology=domain.manifest.ontology,
        capability_names=domain.manifest.capability_names,
        evaluator_names=domain.manifest.evaluator_names,
    )


def domain_package_view(package: DomainPackage) -> DomainPackageView:
    manifest = package.manifest
    return DomainPackageView(
        name=manifest.name,
        version=manifest.version,
        description=manifest.description,
        author=manifest.author,
        entrypoint=manifest.entrypoint,
        tags=manifest.tags,
        ontology=manifest.ontology,
        capability_names=manifest.capabilities,
        tool_names=manifest.tools,
        policy_names=manifest.policies,
        procedure_names=manifest.procedures,
        knowledge_names=manifest.knowledge,
        evaluator_names=manifest.evaluators,
        context_provider_names=manifest.context_providers,
        prompt_names=manifest.prompts,
        dependencies=manifest.dependencies,
        required_tools=manifest.required_tools,
        runtime_api_compatibility=manifest.compatibility.runtime_api,
        domain_api_compatibility=manifest.compatibility.domain_api,
        security=manifest.security,
        root_path=str(package.root_path),
        manifest_path=str(package.manifest_path),
        resource_names=manifest.resources,
    )


def profile_view(profile: AgentProfile) -> ProfileView:
    assert profile.domain.name is not None
    assert profile.domain.version is not None
    return ProfileView(
        name=profile.name,
        version=profile.version,
        description=profile.description,
        domain_name=profile.domain.name,
        domain_version=profile.domain.version,
        domains=tuple(domain.identity() for domain in profile.configured_domains()),
    )


def multi_agent_profile_view(profile: AgentProfileRecord) -> MultiAgentProfileView:
    return MultiAgentProfileView(
        name=profile.name,
        version=profile.version,
        domains=profile.domains,
        permissions=profile.permissions,
        capabilities=profile.capabilities,
        description=profile.description,
    )


def multi_agent_instance_view(instance: AgentInstanceRecord) -> MultiAgentInstanceView:
    return MultiAgentInstanceView(
        agent_id=str(instance.agent_id),
        profile_name=instance.profile_name,
        profile_version=instance.profile_version,
        status=instance.status,
        session_id=instance.session_id,
        endpoint=instance.endpoint,
    )


def policy_view(policy: Policy, domain: ActiveDomain) -> PolicyView:
    if isinstance(policy, PolicyRule):
        return PolicyView(
            name=policy.name,
            description=policy.reason,
            policy_type=type(policy).__name__,
            effect=policy.effect,
            capability_names=policy.capabilities,
            categories=policy.categories,
            risks=policy.risks,
            domain_name=domain.identity.name,
            domain_version=domain.identity.version,
        )
    description = getattr(policy, "description", "")
    if not isinstance(description, str):
        description = ""
    return PolicyView(
        name=policy.name,
        description=description,
        policy_type=type(policy).__name__,
        effect=None,
        capability_names=(),
        categories=(),
        risks=(),
        domain_name=domain.identity.name,
        domain_version=domain.identity.version,
    )


def evaluator_view(evaluator: object, domain: ActiveDomain) -> EvaluatorView:
    name = getattr(evaluator, "name", "")
    return EvaluatorView(
        name=name if isinstance(name, str) else "",
        evaluator_type=type(evaluator).__name__,
        domain_name=domain.identity.name,
        domain_version=domain.identity.version,
    )


def memory_view(record: MemoryRecord) -> MemoryView:
    return MemoryView(
        memory_id=str(record.id),
        kind=record.kind,
        subject=record.subject,
        content=record.content,
        scope=record.scope,
        confidence=record.confidence,
        source_session_id=record.source_session_id,
        created_at=record.created_at,
    )


def world_fact_view(fact: WorldFact) -> WorldFactView:
    return WorldFactView(
        fact.subject,
        fact.claim,
        copy_json_value(fact.value),
        fact.confidence,
        fact.observed_at,
        tuple(str(item) for item in fact.evidence_ids),
    )


def world_fact_evidence_view(evidence: WorldFactEvidence) -> WorldFactEvidenceView:
    return WorldFactEvidenceView(
        str(evidence.evidence_id),
        copy_json_value(evidence.value),
        evidence.confidence,
        evidence.observed_at,
        evidence.source,
    )


def world_fact_history_view(history: WorldFactHistory) -> WorldFactHistoryView:
    return WorldFactHistoryView(
        history.subject,
        history.claim,
        world_fact_view(history.current),
        tuple(world_fact_evidence_view(item) for item in history.candidates),
        history.conflicting,
    )


def world_entity_view(entity: WorldEntity) -> WorldEntityView:
    return WorldEntityView(
        str(entity.id),
        entity.kind,
        immutable_json({key: copy_json_value(value) for key, value in entity.attributes.items()}),
        tuple(str(item) for item in entity.evidence_ids),
    )


def world_relation_view(relation: WorldRelation) -> WorldRelationView:
    return WorldRelationView(
        str(relation.source),
        relation.relation,
        str(relation.target),
        tuple(str(item) for item in relation.evidence_ids),
    )


def world_neighborhood_view(neighborhood: WorldNeighborhood) -> WorldNeighborhoodView:
    return WorldNeighborhoodView(
        None if neighborhood.root is None else world_entity_view(neighborhood.root),
        tuple(world_fact_view(item) for item in neighborhood.facts),
        tuple(world_relation_view(item) for item in neighborhood.outgoing_relations),
        tuple(world_relation_view(item) for item in neighborhood.incoming_relations),
        tuple(world_entity_view(item) for item in neighborhood.related_entities),
    )


def world_projection_views_from_snapshot(
    snapshot: WorldSnapshot,
) -> tuple[
    tuple[WorldFactView, ...],
    tuple[WorldFactHistoryView, ...],
    tuple[WorldEntityView, ...],
    tuple[WorldRelationView, ...],
]:
    return (
        tuple(world_fact_view(item) for item in snapshot.facts),
        tuple(world_fact_history_view(item) for item in snapshot.fact_histories),
        tuple(world_entity_view(item) for item in snapshot.entities),
        tuple(world_relation_view(item) for item in snapshot.relations),
    )


def evidence_from_view(view: EvidenceView) -> Evidence:
    return Evidence(
        view.session_id,
        view.task_id,
        view.action_id,
        view.observation_id,
        view.subject,
        view.claim,
        copy_json_value(view.value),
        view.source,
        view.confidence,
        view.evidence_id,
        view.observed_at,
    )


def copy_json_value(value: JsonValue) -> JsonValue:
    return deepcopy(value)
