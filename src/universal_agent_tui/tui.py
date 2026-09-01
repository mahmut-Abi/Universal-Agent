from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from universal_agent.core import DomainIdentity, SessionId, dumps_json
from universal_agent.operations import AuditRecordView
from universal_agent.runtime import RuntimeEventView, SessionSummaryView, SessionView
from universal_agent.service import (
    CapabilityView,
    DomainPackageView,
    DomainView,
    EvaluatorView,
    MemoryView,
    MultiAgentView,
    PolicyView,
    ProfileView,
    ReadyView,
    RuntimeConfigDomainView,
    RuntimeSecretRefView,
    RuntimeService,
    SessionExplorerView,
    ToolView,
)
from universal_agent_tui.console import RuntimeConsoleSnapshot, build_runtime_console_snapshot
from universal_agent_tui.render import render_terminal_lines

TuiSnapshot = RuntimeConsoleSnapshot
_TUI_SECTION_TITLES = frozenset(
    {
        "Universal Agent Runtime TUI",
        "Operational Diagnostics",
        "Runtime Doctor",
        "Distributed Runtime",
        "Multi-Agent",
        "Configured Domains",
        "Runtime Secrets",
        "Active Domains",
        "Domain Packages",
        "Agent Profiles",
        "Capabilities",
        "Tools",
        "Policies",
        "Evaluators",
        "Memory",
        "Sessions",
        "Selected Session",
        "Task Timeline",
        "World Facts",
        "World Fact History",
        "World Entities",
        "World Relations",
        "Session Evidence",
        "Recent Events",
        "Audit",
    }
)


async def build_tui_snapshot(
    service: RuntimeService,
    *,
    session_id: SessionId | None = None,
    session_limit: int = 5,
    event_limit: int = 12,
) -> TuiSnapshot:
    """Build a read-only TUI snapshot from RuntimeService-facing projections."""

    return await build_runtime_console_snapshot(
        service,
        session_id=session_id,
        session_limit=session_limit,
        event_limit=event_limit,
    )


def render_tui_snapshot(snapshot: TuiSnapshot) -> str:
    lines = [
        "Universal Agent Runtime TUI",
        _rule(),
        f"Health: {snapshot.health.status} | Ready: {_ready_text(snapshot.ready)}",
        (
            f"Runtime: store={snapshot.config.store_backend}"
            f" model={snapshot.config.model.provider}/{snapshot.config.model.name}"
            f" queue={snapshot.config.distributed_queue_backend}"
            f" locks={snapshot.config.distributed_locks_backend}"
            f" workers={snapshot.config.distributed_workers_backend}"
            f" state_event_commit={_state_event_commit_text(snapshot)}"
            f" retention={_retention_text(snapshot.config.distributed_terminal_retention_seconds)}"
            f" max_iterations={snapshot.config.max_iterations}"
            f" max_recovery_steps={snapshot.config.max_recovery_steps}"
        ),
        (
            f"Metrics: sessions={snapshot.metrics.session_count}"
            f" active={snapshot.metrics.active_session_count}"
            f" waiting={snapshot.metrics.waiting_session_count}"
            f" events={snapshot.metrics.event_count}"
            " actions="
            f"{snapshot.metrics.action_started_count}/"
            f"{snapshot.metrics.action_completed_count}"
            f" decisions_rejected={snapshot.metrics.decision_rejected_count}"
            f" policy_denials={snapshot.metrics.policy_denial_count}"
            f" recoveries={snapshot.metrics.recovery_planned_count}"
        ),
        (
            f"Cost: calls={snapshot.cost.model_call_count}"
            f" tokens={snapshot.cost.total_tokens}"
            f" estimated_cost_micros={snapshot.cost.estimated_cost_micros}"
            f" currency={snapshot.cost.currency}"
        ),
        "",
        "Operational Diagnostics",
        _rule(),
    ]
    lines.extend(_operational_diagnostic_lines(snapshot))
    lines.extend(("", "Runtime Doctor", _rule()))
    lines.extend(_doctor_lines(snapshot))
    lines.extend(("", "Distributed Runtime", _rule()))
    lines.extend(_distributed_runtime_lines(snapshot))
    lines.extend(("", "Multi-Agent", _rule()))
    lines.extend(_multi_agent_lines(snapshot.multi_agent))
    lines.extend(("", "Configured Domains", _rule()))
    lines.extend(_configured_domain_lines(snapshot.config.domains))
    lines.extend(("", "Runtime Secrets", _rule()))
    lines.extend(_runtime_secret_lines(snapshot.config.secrets))
    lines.extend(("", "Active Domains", _rule()))
    lines.extend(_domain_lines(snapshot.domains))
    lines.extend(("", "Domain Packages", _rule()))
    lines.extend(_domain_package_lines(snapshot.domain_packages))
    lines.extend(("", "Agent Profiles", _rule()))
    lines.extend(_profile_lines(snapshot.profiles))
    lines.extend(("", "Capabilities", _rule()))
    lines.extend(_capability_lines(snapshot.capabilities))
    lines.extend(("", "Tools", _rule()))
    lines.extend(_tool_lines(snapshot.tools))
    lines.extend(("", "Policies", _rule()))
    lines.extend(_policy_lines(snapshot.policies))
    lines.extend(("", "Evaluators", _rule()))
    lines.extend(_evaluator_lines(snapshot.evaluators))
    lines.extend(("", "Memory", _rule()))
    lines.extend(_memory_lines(snapshot.memories))
    lines.extend(("", "Sessions", _rule()))
    lines.extend(_session_lines(snapshot.sessions))
    lines.extend(("", "Selected Session", _rule()))
    lines.extend(_selected_session_lines(snapshot.selected_session))
    lines.extend(("", "Task Timeline", _rule()))
    lines.extend(_task_timeline_lines(snapshot.selected_session))
    lines.extend(("", "World Facts", _rule()))
    lines.extend(_world_fact_lines(snapshot.session_explorer))
    lines.extend(("", "World Fact History", _rule()))
    lines.extend(_world_fact_history_lines(snapshot.session_explorer))
    lines.extend(("", "World Entities", _rule()))
    lines.extend(_world_entity_lines(snapshot.session_explorer))
    lines.extend(("", "World Relations", _rule()))
    lines.extend(_world_relation_lines(snapshot.session_explorer))
    lines.extend(("", "Session Evidence", _rule()))
    lines.extend(_evidence_lines(snapshot.session_explorer))
    lines.extend(("", "Recent Events", _rule()))
    lines.extend(_event_lines(snapshot.events))
    lines.extend(("", "Audit", _rule()))
    lines.extend(_audit_lines(snapshot.audit_records))
    return _render_lines(lines)


def _render_lines(lines: list[str]) -> str:
    return render_terminal_lines(lines, styler=_line_style)


def _line_style(line: str) -> str | None:
    if line in _TUI_SECTION_TITLES:
        return "bold"
    if line.startswith("- error"):
        return "red"
    if line.startswith("- warn"):
        return "yellow"
    if line.startswith("- ok"):
        return "green"
    return None


def _operational_diagnostic_lines(snapshot: TuiSnapshot) -> list[str]:
    metrics = snapshot.metrics
    lines: list[str] = []
    if not snapshot.ready.ready:
        lines.append(f"- error ready=no reason={snapshot.ready.reason}")
    if metrics.failed_goal_count:
        lines.append(f"- error failed_goals={metrics.failed_goal_count}")
    if metrics.tool_failure_count:
        lines.append(f"- error tool_failures={metrics.tool_failure_count}")
    if metrics.decision_rejected_count:
        lines.append(f"- error decisions_rejected={metrics.decision_rejected_count}")
    if metrics.recovery_exhausted_count:
        lines.append(f"- error recovery_exhausted={metrics.recovery_exhausted_count}")
    if metrics.policy_denial_count:
        lines.append(f"- warn policy_denials={metrics.policy_denial_count}")
    if metrics.confirmation_required_count:
        lines.append(f"- warn confirmations_required={metrics.confirmation_required_count}")
    if metrics.human_intervention_count:
        lines.append(f"- warn human_interventions={metrics.human_intervention_count}")
    if metrics.resource_conflict_count:
        lines.append(f"- warn resource_conflicts={metrics.resource_conflict_count}")
    if metrics.active_resource_lock_count:
        lines.append(f"- warn active_resource_locks={metrics.active_resource_lock_count}")
    if metrics.waiting_session_count:
        lines.append(f"- info waiting_sessions={metrics.waiting_session_count}")
    if metrics.recovery_planned_count:
        lines.append(f"- info recoveries_planned={metrics.recovery_planned_count}")
    if not lines:
        return ["- ok no active operational issues"]
    return lines


def _doctor_lines(snapshot: TuiSnapshot) -> list[str]:
    doctor = snapshot.doctor
    lines = [f"- status={doctor.status} checks={len(doctor.checks)}"]
    lines.extend(f"- {check.status} {check.name}: {check.message}" for check in doctor.checks)
    return lines


def _distributed_runtime_lines(snapshot: TuiSnapshot) -> list[str]:
    distributed = snapshot.distributed_snapshot
    health = snapshot.distributed_health
    if distributed is None or health is None:
        return ["- not configured"]
    lines = [
        (
            f"- status={health.status.value}"
            f" checks={len(health.checks)}"
            f" recommendations={len(health.recommendations)}"
        ),
        (
            f"- queue total={distributed.work_queue.total_count}"
            f" queued={distributed.work_queue.queued_count}"
            f" leased={distributed.work_queue.leased_count}"
            f" completed={distributed.work_queue.completed_count}"
            f" failed={distributed.work_queue.failed_count}"
            f" cancelled={distributed.work_queue.cancelled_count}"
        ),
        (
            f"- workers total={distributed.workers.total_count}"
            f" online={distributed.workers.online_count}"
            f" draining={distributed.workers.draining_count}"
            f" offline={distributed.workers.offline_count}"
            f" lost={distributed.workers.lost_count}"
        ),
        f"- locks active={len(distributed.locks)}",
    ]
    lines.extend(
        f"- check {check.name}={check.status.value}: {check.message}" for check in health.checks
    )
    lines.extend(
        f"- recommendation {item.code}={item.severity.value}: {item.message}"
        for item in health.recommendations
    )
    return lines


def _multi_agent_lines(multi_agent: MultiAgentView) -> list[str]:
    if not multi_agent.enabled:
        return ["- not configured"]
    lines = [
        (
            f"- profiles={multi_agent.profile_count}"
            f" instances={multi_agent.instance_count}"
            f" ready={multi_agent.ready_instance_count}"
            f" busy={multi_agent.busy_instance_count}"
            f" draining={multi_agent.draining_instance_count}"
            f" offline={multi_agent.offline_instance_count}"
            f" delegation_tasks={multi_agent.delegation_task_count}"
        )
    ]
    lines.extend(
        (
            f"- profile {profile.name}@{profile.version}"
            f" domains={_identity_tuple_text(profile.domains)}"
            f" permissions={_tuple_text(profile.permissions)}"
            f" capabilities={_tuple_text(profile.capabilities)}"
            f" :: {profile.description}"
        )
        for profile in multi_agent.profiles
    )
    lines.extend(
        (
            f"- instance {instance.agent_id}"
            f" profile={instance.profile_name}@{instance.profile_version}"
            f" status={instance.status.value}"
            f" session={instance.session_id or 'none'}"
            f" endpoint={instance.endpoint or 'none'}"
        )
        for instance in multi_agent.instances
    )
    lines.extend(
        (
            f"- delegation_task {task.task_id}"
            f" children={task.child_count}"
            f" depth={task.delegation_depth if task.delegation_depth is not None else 'unknown'}"
        )
        for task in multi_agent.delegation_tasks
    )
    return lines


def _state_event_commit_text(snapshot: TuiSnapshot) -> str:
    supported = snapshot.config.state_event_commit_supported
    strategy = snapshot.config.state_event_commit_strategy or "unknown"
    shared_store = snapshot.config.state_event_commit_shared_store
    if supported is None:
        return "unknown"
    status = "enabled" if supported and shared_store else "split"
    return f"{status}/{strategy}"


def _configured_domain_lines(domains: tuple[RuntimeConfigDomainView, ...]) -> list[str]:
    if not domains:
        return ["- none"]
    return [
        (
            f"- {'*' if domain.primary else ' '} {domain.name}@{domain.version}"
            f" backend={domain.backend or 'default'}"
            f" settings={_value_text(domain.settings) if domain.settings else 'none'}"
        )
        for domain in domains
    ]


def _runtime_secret_lines(secrets: tuple[RuntimeSecretRefView, ...]) -> list[str]:
    if not secrets:
        return ["- none"]
    return [
        (
            f"- {secret.name}"
            f" source={secret.source}"
            f" key={secret.key}"
            f" required={'yes' if secret.required else 'no'}"
            f" status={_secret_status_text(secret.available, secret.status)}"
        )
        for secret in secrets
    ]


def _secret_status_text(available: bool | None, status: str | None) -> str:
    if status is not None:
        return status
    if available is None:
        return "unknown"
    return "available" if available else "missing"


def _domain_lines(domains: tuple[DomainView, ...]) -> list[str]:
    if not domains:
        return ["- none"]
    return [
        (
            f"- {'*' if domain.primary else ' '} {domain.name}@{domain.version}"
            f" capabilities={len(domain.capability_names)}"
            f" evaluators={len(domain.evaluator_names)}"
        )
        for domain in domains
    ]


def _profile_lines(profiles: tuple[ProfileView, ...]) -> list[str]:
    if not profiles:
        return ["- none"]
    return [
        (
            f"- {profile.name}@{profile.version}"
            f" primary={profile.domain_name}@{profile.domain_version}"
            f" domains={_profile_domain_text(profile)}"
            f" :: {profile.description}"
        )
        for profile in profiles
    ]


def _domain_package_lines(packages: tuple[DomainPackageView, ...]) -> list[str]:
    if not packages:
        return ["- none"]
    return [
        (
            f"- {package.name}@{package.version}"
            f" capabilities={_tuple_text(package.capability_names)}"
            f" tools={_tuple_text(package.tool_names)}"
            f" dependencies={_identity_tuple_text(package.dependencies)}"
            f" resources={_tuple_text(package.resource_names)}"
            f" entrypoint={package.entrypoint or 'none'}"
            f" :: {package.description}"
        )
        for package in packages
    ]


def _capability_lines(capabilities: tuple[CapabilityView, ...]) -> list[str]:
    if not capabilities:
        return ["- none"]
    return [
        (
            f"- {capability.name}"
            f" category={capability.category.value}"
            f" risk={capability.risk.value}"
            f" domain={capability.domain_name}@{capability.domain_version}"
            f" tools={_tuple_text(capability.tool_names)}"
            f" :: {capability.description}"
        )
        for capability in capabilities
    ]


def _tool_lines(tools: tuple[ToolView, ...]) -> list[str]:
    if not tools:
        return ["- none"]
    return [
        (
            f"- {tool.name}"
            f" side_effect={tool.side_effect.value}"
            f" risk={tool.risk.value}"
            f" capabilities={_tuple_text(tool.capabilities)}"
            f" required_args={_tuple_text(tool.required_arguments)}"
            f" timeout={tool.timeout_seconds:g}s"
            f" domain={tool.domain_name}@{tool.domain_version}"
        )
        for tool in tools
    ]


def _policy_lines(policies: tuple[PolicyView, ...]) -> list[str]:
    if not policies:
        return ["- none"]
    return [
        (
            f"- {policy.name}"
            f" type={policy.policy_type}"
            f" effect={'n/a' if policy.effect is None else policy.effect.value}"
            f" categories={_enum_tuple_text(policy.categories)}"
            f" risks={_enum_tuple_text(policy.risks)}"
            f" capabilities={_tuple_text(policy.capability_names)}"
            f" domain={policy.domain_name}@{policy.domain_version}"
            f" :: {policy.description}"
        )
        for policy in policies
    ]


def _evaluator_lines(evaluators: tuple[EvaluatorView, ...]) -> list[str]:
    if not evaluators:
        return ["- none"]
    return [
        (
            f"- {evaluator.name}"
            f" type={evaluator.evaluator_type}"
            f" domain={evaluator.domain_name}@{evaluator.domain_version}"
        )
        for evaluator in evaluators
    ]


def _memory_lines(memories: tuple[MemoryView, ...]) -> list[str]:
    if not memories:
        return ["- none"]
    return [
        (
            f"- {memory.memory_id}"
            f" kind={memory.kind.value}"
            f" subject={memory.subject}"
            f" scope={memory.scope or 'global'}"
            f" confidence={memory.confidence:.2f}"
            f" source_session={memory.source_session_id or 'none'}"
            f" :: {memory.content}"
        )
        for memory in memories
    ]


def _session_lines(sessions: tuple[SessionSummaryView, ...]) -> list[str]:
    if not sessions:
        return ["- none"]
    return [
        (
            f"- {session.session_id} goal={session.goal_status.value}"
            f" task={session.current_task_status.value}"
            f" iter={session.iteration}"
            f" pending_action={session.pending_action}"
            f" domain={session.domain_name}@{session.domain_version}"
            f" :: {session.goal_description}"
        )
        for session in sessions
    ]


def _selected_session_lines(session: SessionView | None) -> list[str]:
    if session is None:
        return ["- none"]
    lines = [
        f"Session: {session.session_id}",
        f"Goal: {session.goal_status.value} :: {session.goal_description}",
        f"Current Task: {session.current_task_status.value} :: {session.current_task_description}",
        f"Iteration: {session.iteration} | Tasks: {len(session.tasks)}",
        f"Domain: {session.domain_name}@{session.domain_version}",
        "Satisfied Criteria: " + _mapping_text(session.satisfied_criteria),
        "Pending Action: " + _pending_action_text(session),
        "Latest Evaluation: " + _evaluation_text(session),
    ]
    if session.termination_reason is not None:
        lines.append(f"Termination: {session.termination_reason}")
    if session.error_code is not None:
        lines.append(f"Error: {session.error_code.value}")
    return lines


def _task_timeline_lines(session: SessionView | None) -> list[str]:
    if session is None or not session.tasks:
        return ["- none"]
    return [
        (
            f"- {task.task_id}"
            f" status={task.status.value}"
            f" required={_tuple_text(task.required_criteria)}"
            f" depends_on={_task_id_tuple_text(task.depends_on)}"
            f" :: {task.description}"
        )
        for task in session.tasks
    ]


def _world_fact_lines(explorer: SessionExplorerView | None) -> list[str]:
    if explorer is None or not explorer.world_facts:
        return ["- none"]
    return [
        (
            f"- {fact.subject} {fact.claim}={_value_text(fact.value)}"
            f" confidence={fact.confidence:.2f}"
            f" evidence={_tuple_text(fact.evidence_ids)}"
        )
        for fact in explorer.world_facts
    ]


def _world_fact_history_lines(explorer: SessionExplorerView | None) -> list[str]:
    if explorer is None or not explorer.world_fact_histories:
        return ["- none"]
    return [
        (
            f"- {history.subject} {history.claim}"
            f" current={_value_text(history.current.value)}"
            f" conflicting={'yes' if history.conflicting else 'no'}"
            f" candidates={_fact_history_candidates_text(history.candidates)}"
        )
        for history in explorer.world_fact_histories
    ]


def _world_entity_lines(explorer: SessionExplorerView | None) -> list[str]:
    if explorer is None or not explorer.world_entities:
        return ["- none"]
    return [
        (
            f"- {entity.entity_id} kind={entity.kind}"
            f" attributes={_value_text(entity.attributes)}"
            f" evidence={_tuple_text(entity.evidence_ids)}"
        )
        for entity in explorer.world_entities
    ]


def _world_relation_lines(explorer: SessionExplorerView | None) -> list[str]:
    if explorer is None or not explorer.world_relations:
        return ["- none"]
    return [
        (
            f"- {relation.source} -[{relation.relation}]-> {relation.target}"
            f" evidence={_tuple_text(relation.evidence_ids)}"
        )
        for relation in explorer.world_relations
    ]


def _evidence_lines(explorer: SessionExplorerView | None) -> list[str]:
    if explorer is None or not explorer.evidence:
        return ["- none"]
    return [
        (
            f"- {item.evidence_id}"
            f" subject={item.subject}"
            f" claim={item.claim}"
            f" value={_value_text(item.value)}"
            f" source={item.source}"
            f" confidence={item.confidence:.2f}"
        )
        for item in explorer.evidence
    ]


def _event_lines(events: tuple[RuntimeEventView, ...]) -> list[str]:
    if not events:
        return ["- none"]
    return [
        (
            f"- {event.occurred_at.isoformat()} {event.type}"
            f" task={event.task_id}"
            f" action={event.action_id or '-'}"
            f" {_event_detail(event.data)}"
        ).rstrip()
        for event in events
    ]


def _audit_lines(records: tuple[AuditRecordView, ...]) -> list[str]:
    if not records:
        return ["- none"]
    return [
        (
            f"- {record.occurred_at.isoformat()} capability={record.capability}"
            f" tool={record.tool_name}"
            f" policy={record.policy_effect}:{record.policy_name}"
            f" status={record.status}"
        )
        for record in records
    ]


def _pending_action_text(session: SessionView) -> str:
    action = session.pending_action
    if action is None:
        return "none"
    return (
        f"{action.action_id} capability={action.capability}"
        f" tool={action.tool_name}"
        f" resource={action.resource_key}"
        f" attempt={action.attempt}"
    )


def _evaluation_text(session: SessionView) -> str:
    evaluation = session.latest_evaluation
    if evaluation is None:
        return "none"
    return (
        f"{evaluation.status.value}"
        f" task_completed={evaluation.task_completed}"
        f" goal_completed={evaluation.goal_completed}"
        f" evaluator={evaluation.evaluator_name}"
        f" reason={evaluation.reason}"
    )


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


def _profile_domain_text(profile: ProfileView) -> str:
    if not profile.domains:
        return "none"
    return ", ".join(f"{identity.name}@{identity.version}" for identity in profile.domains)


def _identity_tuple_text(values: tuple[DomainIdentity, ...]) -> str:
    if not values:
        return "none"
    return ", ".join(f"{identity.name}@{identity.version}" for identity in values)


def _tuple_text(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "none"


def _task_id_tuple_text(values: tuple[Any, ...]) -> str:
    return ", ".join(str(value) for value in values) if values else "none"


def _retention_text(seconds: float | None) -> str:
    if seconds is None:
        return "disabled"
    return f"{seconds:g}s"


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


def _fact_history_candidates_text(candidates: tuple[Any, ...]) -> str:
    if not candidates:
        return "none"
    return "; ".join(
        (
            f"{candidate.evidence_id}:"
            f"value={_value_text(candidate.value)}"
            f" confidence={candidate.confidence:.2f}"
            f" source={candidate.source}"
        )
        for candidate in candidates
    )


def _ready_text(ready: ReadyView) -> str:
    return "yes" if ready.ready else f"no ({ready.reason})"


def _rule() -> str:
    return "-" * 32


__all__ = ["TuiSnapshot", "build_tui_snapshot", "render_tui_snapshot"]
