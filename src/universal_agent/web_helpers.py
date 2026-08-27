from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from universal_agent.core import DomainIdentity, dumps_json
from universal_agent.distributed import DistributedHealthReport, DistributedRuntimeSnapshot
from universal_agent.operations import DoctorReportView
from universal_agent.runtime import SessionView
from universal_agent.service import (
    CapabilityView,
    DomainPackageView,
    DomainView,
    EvaluatorView,
    MemoryView,
    PolicyView,
    ProfileView,
    SessionExplorerView,
    ToolView,
)
from universal_agent.web_types import WebConsoleSnapshot


def _action_count(snapshot: WebConsoleSnapshot) -> str:
    return f"{snapshot.metrics.action_started_count}/{snapshot.metrics.action_completed_count}"


def _ready_text(snapshot: WebConsoleSnapshot) -> str:
    if snapshot.ready.ready:
        return "yes"
    return "no: " + snapshot.ready.reason


def _event_detail(data: Mapping[str, Any]) -> str:
    keys = (
        "decision_type",
        "capability",
        "tool_name",
        "effect",
        "status",
        "error_code",
        "observation_id",
        "evidence_id",
        "claim",
        "reason",
    )
    parts = [f"{key}={data[key]}" for key in keys if key in data]
    return " ".join(parts)


def _mapping_text(values: Mapping[str, Any]) -> str:
    if not values:
        return "none"
    return ", ".join(f"{key}={values[key]}" for key in sorted(values))


def _string_tuple_text(values: tuple[object, ...]) -> str:
    if not values:
        return "none"
    return ", ".join(str(value) for value in values)


def _selected_iteration(session: SessionView | None) -> str:
    if session is None:
        return "none"
    return str(session.iteration)


def _selected_task_count(session: SessionView | None) -> int:
    if session is None:
        return 0
    return len(session.tasks)


def _selected_evidence_count(explorer: SessionExplorerView | None) -> int:
    if explorer is None:
        return 0
    return len(explorer.evidence)


def _selected_world_fact_count(explorer: SessionExplorerView | None) -> int:
    if explorer is None:
        return 0
    return len(explorer.world_facts)


def _selected_conflicting_world_fact_count(explorer: SessionExplorerView | None) -> int:
    if explorer is None:
        return 0
    return sum(1 for history in explorer.world_fact_histories if history.conflicting)


def _selected_world_entity_count(explorer: SessionExplorerView | None) -> int:
    if explorer is None:
        return 0
    return len(explorer.world_entities)


def _selected_world_relation_count(explorer: SessionExplorerView | None) -> int:
    if explorer is None:
        return 0
    return len(explorer.world_relations)


def _doctor_check_count(doctor: DoctorReportView, status: str) -> int:
    return sum(1 for check in doctor.checks if check.status == status)


def _distributed_status(health: DistributedHealthReport | None) -> str:
    if health is None:
        return "not configured"
    return health.status.value


def _distributed_work_item_count(distributed: DistributedRuntimeSnapshot | None) -> int:
    if distributed is None:
        return 0
    return distributed.work_queue.total_count


def _distributed_queued_count(distributed: DistributedRuntimeSnapshot | None) -> int:
    if distributed is None:
        return 0
    return distributed.work_queue.queued_count


def _distributed_leased_count(distributed: DistributedRuntimeSnapshot | None) -> int:
    if distributed is None:
        return 0
    return distributed.work_queue.leased_count


def _distributed_completed_count(distributed: DistributedRuntimeSnapshot | None) -> int:
    if distributed is None:
        return 0
    return distributed.work_queue.completed_count


def _distributed_failed_count(distributed: DistributedRuntimeSnapshot | None) -> int:
    if distributed is None:
        return 0
    return distributed.work_queue.failed_count


def _distributed_cancelled_count(distributed: DistributedRuntimeSnapshot | None) -> int:
    if distributed is None:
        return 0
    return distributed.work_queue.cancelled_count


def _distributed_worker_count(distributed: DistributedRuntimeSnapshot | None) -> int:
    if distributed is None:
        return 0
    return distributed.workers.total_count


def _distributed_lock_count(distributed: DistributedRuntimeSnapshot | None) -> int:
    if distributed is None:
        return 0
    return len(distributed.locks)


def _retention_text(seconds: float | None) -> str:
    if seconds is None:
        return "disabled"
    return f"{seconds:g}s"


def _risk_count(items: tuple[CapabilityView, ...] | tuple[ToolView, ...], risk: str) -> int:
    return sum(1 for item in items if item.risk.value == risk)


def _side_effect_count(tools: tuple[ToolView, ...], side_effect: str) -> int:
    return sum(1 for tool in tools if tool.side_effect.value == side_effect)


def _policy_effect_count(policies: tuple[PolicyView, ...], effect: str) -> int:
    return sum(
        1 for policy in policies if policy.effect is not None and policy.effect.value == effect
    )


def _global_memory_count(memories: tuple[MemoryView, ...]) -> int:
    return sum(1 for memory in memories if not memory.scope)


def _scoped_memory_count(memories: tuple[MemoryView, ...]) -> int:
    return sum(1 for memory in memories if memory.scope)


def _selected_domain(
    snapshot: WebConsoleSnapshot,
    domain_name: str,
    domain_version: str | None,
) -> DomainView | None:
    matches = tuple(
        domain
        for domain in snapshot.domains
        if domain.name == domain_name
        and (domain_version is None or domain.version == domain_version)
    )
    if len(matches) != 1:
        return None
    return matches[0]


def _domain_profiles(
    snapshot: WebConsoleSnapshot,
    domain: DomainView | None,
) -> tuple[ProfileView, ...]:
    if domain is None:
        return ()
    return tuple(
        profile
        for profile in snapshot.profiles
        if (profile.domain_name, profile.domain_version) == (domain.name, domain.version)
        or any(
            identity.name == domain.name and identity.version == domain.version
            for identity in profile.domains
        )
    )


def _domain_capabilities(
    snapshot: WebConsoleSnapshot,
    domain: DomainView | None,
) -> tuple[CapabilityView, ...]:
    if domain is None:
        return ()
    return tuple(
        item
        for item in snapshot.capabilities
        if (item.domain_name, item.domain_version) == (domain.name, domain.version)
    )


def _domain_tools(
    snapshot: WebConsoleSnapshot,
    domain: DomainView | None,
) -> tuple[ToolView, ...]:
    if domain is None:
        return ()
    return tuple(
        item
        for item in snapshot.tools
        if (item.domain_name, item.domain_version) == (domain.name, domain.version)
    )


def _domain_policies(
    snapshot: WebConsoleSnapshot,
    domain: DomainView | None,
) -> tuple[PolicyView, ...]:
    if domain is None:
        return ()
    return tuple(
        item
        for item in snapshot.policies
        if (item.domain_name, item.domain_version) == (domain.name, domain.version)
    )


def _domain_evaluators(
    snapshot: WebConsoleSnapshot,
    domain: DomainView | None,
) -> tuple[EvaluatorView, ...]:
    if domain is None:
        return ()
    return tuple(
        item
        for item in snapshot.evaluators
        if (item.domain_name, item.domain_version) == (domain.name, domain.version)
    )


def _domain_memories(
    snapshot: WebConsoleSnapshot,
    domain: DomainView | None,
) -> tuple[MemoryView, ...]:
    if domain is None:
        return ()
    return tuple(item for item in snapshot.memories if item.scope == domain.name)


def _domain_capability_count(snapshot: WebConsoleSnapshot, domain: DomainView | None) -> int:
    return len(_domain_capabilities(snapshot, domain))


def _domain_tool_count(snapshot: WebConsoleSnapshot, domain: DomainView | None) -> int:
    return len(_domain_tools(snapshot, domain))


def _domain_policy_count(snapshot: WebConsoleSnapshot, domain: DomainView | None) -> int:
    return len(_domain_policies(snapshot, domain))


def _domain_evaluator_count(snapshot: WebConsoleSnapshot, domain: DomainView | None) -> int:
    return len(_domain_evaluators(snapshot, domain))


def _domain_memory_count(snapshot: WebConsoleSnapshot, domain: DomainView | None) -> int:
    return len(_domain_memories(snapshot, domain))


def _profile_domain_text(profile: ProfileView) -> str:
    if not profile.domains:
        return "none"
    return ", ".join(f"{identity.name}@{identity.version}" for identity in profile.domains)


def _domain_package_dependencies(package: DomainPackageView) -> str:
    if not package.dependencies:
        return "none"
    return ", ".join(f"{identity.name}@{identity.version}" for identity in package.dependencies)


def _identity_tuple_text(values: tuple[DomainIdentity, ...]) -> str:
    if not values:
        return "none"
    return ", ".join(f"{identity.name}@{identity.version}" for identity in values)


def _domain_package_dependency_count(snapshot: WebConsoleSnapshot) -> int:
    return sum(len(package.dependencies) for package in snapshot.domain_packages)


def _package_capability_count(package: DomainPackageView | None) -> int:
    return 0 if package is None else len(package.capability_names)


def _package_tool_count(package: DomainPackageView | None) -> int:
    return 0 if package is None else len(package.tool_names)


def _package_policy_count(package: DomainPackageView | None) -> int:
    return 0 if package is None else len(package.policy_names)


def _package_evaluator_count(package: DomainPackageView | None) -> int:
    return 0 if package is None else len(package.evaluator_names)


def _package_resource_count(package: DomainPackageView | None) -> int:
    return 0 if package is None else len(package.resource_names)


def _package_dependency_count(package: DomainPackageView | None) -> int:
    return 0 if package is None else len(package.dependencies)


def _domain_package_required_tool_count(snapshot: WebConsoleSnapshot) -> int:
    required_tools = {
        tool_name for package in snapshot.domain_packages for tool_name in package.required_tools
    }
    return len(required_tools)


def _domain_package_resource_count(snapshot: WebConsoleSnapshot) -> int:
    resources = {
        resource for package in snapshot.domain_packages for resource in package.resource_names
    }
    return len(resources)


def _selected_domain_package(
    snapshot: WebConsoleSnapshot,
    package_name: str,
    package_version: str | None,
) -> DomainPackageView | None:
    matches = tuple(
        package
        for package in snapshot.domain_packages
        if package.name == package_name
        and (package_version is None or package.version == package_version)
    )
    if len(matches) != 1:
        return None
    return matches[0]


def _enum_tuple_text(values: tuple[Any, ...]) -> str:
    if not values:
        return "none"
    return ", ".join(str(getattr(value, "value", value)) for value in values)


def _value_text(value: object) -> str:
    if isinstance(value, Mapping):
        return dumps_json(dict(value))
    if isinstance(value, list):
        return dumps_json(value)
    return str(value)


def _secret_status_text(available: bool | None, status: str | None) -> str:
    if status is not None:
        return status
    if available is None:
        return "unknown"
    return "available" if available else "missing"
