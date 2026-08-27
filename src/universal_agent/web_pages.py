from __future__ import annotations

from universal_agent.distributed import DistributedHealthReport, DistributedRuntimeSnapshot
from universal_agent.operations import DoctorReportView
from universal_agent.web_catalog import _catalog_metrics, _catalog_sections, _catalog_title
from universal_agent.web_catalog_sections import (
    _capabilities,
    _configured_domains,
    _domain_details,
    _domain_package_active_domains,
    _domain_package_details,
    _domain_package_profiles,
    _domain_package_resources,
    _domain_package_security,
    _domain_packages,
    _domains,
    _environment,
    _evaluators,
    _memory,
    _policies,
    _profiles,
    _runtime_secrets,
    _tools,
)
from universal_agent.web_helpers import (
    _action_count,
    _distributed_cancelled_count,
    _distributed_completed_count,
    _distributed_failed_count,
    _distributed_leased_count,
    _distributed_lock_count,
    _distributed_queued_count,
    _distributed_status,
    _distributed_work_item_count,
    _distributed_worker_count,
    _doctor_check_count,
    _domain_capabilities,
    _domain_capability_count,
    _domain_evaluator_count,
    _domain_evaluators,
    _domain_memories,
    _domain_memory_count,
    _domain_policies,
    _domain_policy_count,
    _domain_profiles,
    _domain_tool_count,
    _domain_tools,
    _package_capability_count,
    _package_dependency_count,
    _package_evaluator_count,
    _package_policy_count,
    _package_resource_count,
    _package_tool_count,
    _ready_text,
    _retention_text,
    _selected_conflicting_world_fact_count,
    _selected_domain,
    _selected_domain_package,
    _selected_evidence_count,
    _selected_iteration,
    _selected_task_count,
    _selected_world_entity_count,
    _selected_world_fact_count,
    _selected_world_relation_count,
)
from universal_agent.web_heroes import (
    _catalog_hero,
    _distributed_hero,
    _doctor_hero,
    _domain_hero,
    _domain_package_hero,
    _hero,
    _multi_agent_hero,
    _profile_hero,
    _session_scoped_hero,
    _sessions_hero,
    _settings_hero,
)
from universal_agent.web_operational_sections import (
    _distributed_health_checks,
    _distributed_locks,
    _distributed_not_configured,
    _distributed_recommendations,
    _distributed_work_queue,
    _distributed_workers,
    _doctor_checks,
    _multi_agent,
    _operational_diagnostics,
    _runtime_settings,
)
from universal_agent.web_session_sections import (
    _audit,
    _events,
    _selected_session,
    _sessions,
    _task_timeline,
)
from universal_agent.web_types import WebCatalogPage, WebConsoleSnapshot
from universal_agent.web_ui import _metric_card, _metric_grid, _page
from universal_agent.web_world_sections import (
    _evidence,
    _world_entities,
    _world_fact_history,
    _world_facts,
    _world_neighborhood,
    _world_relations,
)


def render_web_console(snapshot: WebConsoleSnapshot) -> str:
    title = "Universal Agent Runtime Console"
    return _page(
        title,
        (
            _hero(snapshot),
            _metric_grid(
                "Runtime summary",
                (
                    _metric_card("Sessions", snapshot.metrics.session_count),
                    _metric_card("Active", snapshot.metrics.active_session_count),
                    _metric_card("Events", snapshot.metrics.event_count),
                    _metric_card("Actions", _action_count(snapshot)),
                    _metric_card("Rejected Decisions", snapshot.metrics.decision_rejected_count),
                    _metric_card("Tokens", snapshot.cost.total_tokens),
                    _metric_card("Cost micros", snapshot.cost.estimated_cost_micros),
                ),
            ),
            _operational_diagnostics(snapshot),
            _domains(snapshot),
            _domain_packages(snapshot.domain_packages),
            _profiles(snapshot.profiles),
            _multi_agent(snapshot.multi_agent),
            _capabilities(snapshot.capabilities),
            _tools(snapshot.tools),
            _policies(snapshot.policies),
            _evaluators(snapshot.evaluators),
            _memory(snapshot.memories),
            _sessions(snapshot.sessions),
            _selected_session(snapshot.selected_session),
            _world_facts(snapshot.session_explorer),
            _world_fact_history(snapshot.session_explorer),
            _world_entities(snapshot.session_explorer),
            _world_relations(snapshot.session_explorer),
            _evidence(snapshot.session_explorer),
            _events(snapshot.events),
            _audit(snapshot.audit_records),
        ),
    )


def render_web_session_detail(snapshot: WebConsoleSnapshot) -> str:
    title = "Universal Agent Runtime Session Detail"
    return _page(
        title,
        (
            _session_scoped_hero(snapshot, "Session Detail"),
            _metric_grid(
                "Session summary",
                (
                    _metric_card("Iteration", _selected_iteration(snapshot.selected_session)),
                    _metric_card("Tasks", _selected_task_count(snapshot.selected_session)),
                    _metric_card("Events", len(snapshot.events)),
                    _metric_card("Evidence", _selected_evidence_count(snapshot.session_explorer)),
                    _metric_card(
                        "World Facts",
                        _selected_world_fact_count(snapshot.session_explorer),
                    ),
                    _metric_card(
                        "World Entities",
                        _selected_world_entity_count(snapshot.session_explorer),
                    ),
                    _metric_card("Audit", len(snapshot.audit_records)),
                ),
            ),
            _selected_session(snapshot.selected_session),
            _task_timeline(snapshot.selected_session),
            _world_facts(snapshot.session_explorer),
            _world_fact_history(snapshot.session_explorer),
            _world_entities(snapshot.session_explorer),
            _world_relations(snapshot.session_explorer),
            _evidence(snapshot.session_explorer),
            _events(snapshot.events),
            _audit(snapshot.audit_records),
        ),
    )


def render_web_sessions(snapshot: WebConsoleSnapshot) -> str:
    title = "Universal Agent Runtime Sessions"
    return _page(
        title,
        (
            _sessions_hero(snapshot),
            _metric_grid(
                "Session summary",
                (
                    _metric_card("Sessions", snapshot.metrics.session_count),
                    _metric_card("Active", snapshot.metrics.active_session_count),
                    _metric_card("Waiting", snapshot.metrics.waiting_session_count),
                    _metric_card("Completed", snapshot.metrics.completed_goal_count),
                    _metric_card("Failed", snapshot.metrics.failed_goal_count),
                    _metric_card("Cancelled", snapshot.metrics.cancelled_goal_count),
                ),
            ),
            _sessions(snapshot.sessions),
            _selected_session(snapshot.selected_session),
            _events(snapshot.events),
            _audit(snapshot.audit_records),
        ),
    )


def render_web_doctor(snapshot: WebConsoleSnapshot, doctor: DoctorReportView) -> str:
    title = "Universal Agent Runtime Doctor"
    return _page(
        title,
        (
            _doctor_hero(snapshot, doctor),
            _metric_grid(
                "Doctor summary",
                (
                    _metric_card("Status", doctor.status),
                    _metric_card("Checks", len(doctor.checks)),
                    _metric_card("Errors", _doctor_check_count(doctor, "error")),
                    _metric_card("Warnings", _doctor_check_count(doctor, "warn")),
                    _metric_card("OK", _doctor_check_count(doctor, "ok")),
                    _metric_card("Ready", _ready_text(snapshot)),
                ),
            ),
            _doctor_checks(doctor),
            _operational_diagnostics(snapshot),
            _runtime_settings(snapshot),
        ),
    )


def render_web_distributed(
    snapshot: WebConsoleSnapshot,
    distributed: DistributedRuntimeSnapshot | None,
    health: DistributedHealthReport | None,
) -> str:
    title = "Universal Agent Runtime Distributed"
    return _page(
        title,
        (
            _distributed_hero(snapshot, health),
            _metric_grid(
                "Distributed summary",
                (
                    _metric_card("Status", _distributed_status(health)),
                    _metric_card("Work Items", _distributed_work_item_count(distributed)),
                    _metric_card("Queued", _distributed_queued_count(distributed)),
                    _metric_card("Leased", _distributed_leased_count(distributed)),
                    _metric_card("Completed", _distributed_completed_count(distributed)),
                    _metric_card("Failed", _distributed_failed_count(distributed)),
                    _metric_card("Cancelled", _distributed_cancelled_count(distributed)),
                    _metric_card("Workers", _distributed_worker_count(distributed)),
                    _metric_card("Locks", _distributed_lock_count(distributed)),
                ),
            ),
            _distributed_not_configured(distributed),
            _distributed_health_checks(health),
            _distributed_recommendations(health),
            _distributed_work_queue(distributed),
            _distributed_workers(distributed),
            _distributed_locks(distributed),
        ),
    )


def render_web_multi_agent(snapshot: WebConsoleSnapshot) -> str:
    title = "Universal Agent Runtime Multi-Agent"
    multi_agent = snapshot.multi_agent
    return _page(
        title,
        (
            _multi_agent_hero(snapshot),
            _metric_grid(
                "Multi-Agent summary",
                (
                    _metric_card("Status", "enabled" if multi_agent.enabled else "not configured"),
                    _metric_card("Profiles", multi_agent.profile_count),
                    _metric_card("Instances", multi_agent.instance_count),
                    _metric_card("Ready", multi_agent.ready_instance_count),
                    _metric_card("Busy", multi_agent.busy_instance_count),
                    _metric_card("Delegation Tasks", multi_agent.delegation_task_count),
                ),
            ),
            _multi_agent(multi_agent),
        ),
    )


def render_web_evidence_explorer(snapshot: WebConsoleSnapshot) -> str:
    title = "Universal Agent Runtime Evidence Explorer"
    return _page(
        title,
        (
            _session_scoped_hero(snapshot, "Evidence Explorer"),
            _metric_grid(
                "Evidence summary",
                (
                    _metric_card("Evidence", _selected_evidence_count(snapshot.session_explorer)),
                    _metric_card(
                        "World Facts",
                        _selected_world_fact_count(snapshot.session_explorer),
                    ),
                    _metric_card(
                        "World Entities",
                        _selected_world_entity_count(snapshot.session_explorer),
                    ),
                    _metric_card("Events", len(snapshot.events)),
                    _metric_card("Audit", len(snapshot.audit_records)),
                ),
            ),
            _selected_session(snapshot.selected_session),
            _evidence(snapshot.session_explorer),
            _world_facts(snapshot.session_explorer),
            _world_fact_history(snapshot.session_explorer),
            _world_entities(snapshot.session_explorer),
            _world_relations(snapshot.session_explorer),
            _events(snapshot.events),
        ),
    )


def render_web_world_model_explorer(snapshot: WebConsoleSnapshot) -> str:
    title = "Universal Agent Runtime World Model Explorer"
    return _page(
        title,
        (
            _session_scoped_hero(snapshot, "World Model Explorer"),
            _metric_grid(
                "World model summary",
                (
                    _metric_card(
                        "World Facts",
                        _selected_world_fact_count(snapshot.session_explorer),
                    ),
                    _metric_card(
                        "Conflicts",
                        _selected_conflicting_world_fact_count(snapshot.session_explorer),
                    ),
                    _metric_card(
                        "World Entities",
                        _selected_world_entity_count(snapshot.session_explorer),
                    ),
                    _metric_card(
                        "World Relations",
                        _selected_world_relation_count(snapshot.session_explorer),
                    ),
                    _metric_card("Evidence", _selected_evidence_count(snapshot.session_explorer)),
                    _metric_card("Events", len(snapshot.events)),
                    _metric_card("Audit", len(snapshot.audit_records)),
                ),
            ),
            _selected_session(snapshot.selected_session),
            _world_facts(snapshot.session_explorer),
            _world_fact_history(snapshot.session_explorer),
            _world_neighborhood(snapshot.world_neighborhood),
            _world_entities(snapshot.session_explorer),
            _world_relations(snapshot.session_explorer),
            _evidence(snapshot.session_explorer),
            _events(snapshot.events),
        ),
    )


def render_web_domain_detail(
    snapshot: WebConsoleSnapshot,
    *,
    domain_name: str,
    domain_version: str | None = None,
) -> str:
    domain = _selected_domain(snapshot, domain_name, domain_version)
    title = "Universal Agent Runtime Domain Manager"
    return _page(
        title,
        (
            _domain_hero(snapshot, domain),
            _metric_grid(
                "Domain summary",
                (
                    _metric_card("Capabilities", _domain_capability_count(snapshot, domain)),
                    _metric_card("Tools", _domain_tool_count(snapshot, domain)),
                    _metric_card("Policies", _domain_policy_count(snapshot, domain)),
                    _metric_card("Evaluators", _domain_evaluator_count(snapshot, domain)),
                    _metric_card("Memories", _domain_memory_count(snapshot, domain)),
                ),
            ),
            _domain_details(domain),
            _profiles(_domain_profiles(snapshot, domain)),
            _capabilities(_domain_capabilities(snapshot, domain)),
            _tools(_domain_tools(snapshot, domain)),
            _policies(_domain_policies(snapshot, domain)),
            _evaluators(_domain_evaluators(snapshot, domain)),
            _memory(_domain_memories(snapshot, domain)),
        ),
    )


def render_web_domain_package_detail(
    snapshot: WebConsoleSnapshot,
    *,
    package_name: str,
    package_version: str | None = None,
) -> str:
    package = _selected_domain_package(snapshot, package_name, package_version)
    title = "Universal Agent Runtime Domain Package"
    return _page(
        title,
        (
            _domain_package_hero(snapshot, package),
            _metric_grid(
                "Domain package summary",
                (
                    _metric_card("Capabilities", _package_capability_count(package)),
                    _metric_card("Tools", _package_tool_count(package)),
                    _metric_card("Policies", _package_policy_count(package)),
                    _metric_card("Evaluators", _package_evaluator_count(package)),
                    _metric_card("Resources", _package_resource_count(package)),
                    _metric_card("Dependencies", _package_dependency_count(package)),
                ),
            ),
            _domain_package_details(package),
            _domain_package_resources(package),
            _domain_package_security(package),
            _domain_package_active_domains(snapshot, package),
            _domain_package_profiles(snapshot, package),
        ),
    )


def render_web_profile_catalog(snapshot: WebConsoleSnapshot) -> str:
    title = "Universal Agent Runtime Profile Catalog"
    return _page(
        title,
        (
            _profile_hero(snapshot),
            _metric_grid(
                "Profile summary",
                (
                    _metric_card("Profiles", len(snapshot.profiles)),
                    _metric_card("Active Domains", len(snapshot.domains)),
                    _metric_card("Configured Domains", len(snapshot.config.domains)),
                    _metric_card("Capabilities", len(snapshot.capabilities)),
                    _metric_card("Tools", len(snapshot.tools)),
                    _metric_card("Ready", _ready_text(snapshot)),
                ),
            ),
            _profiles(snapshot.profiles),
            _domains(snapshot),
            _configured_domains(snapshot),
            _capabilities(snapshot.capabilities),
        ),
    )


def render_web_catalog(snapshot: WebConsoleSnapshot, catalog: WebCatalogPage) -> str:
    title = f"Universal Agent Runtime {_catalog_title(catalog)}"
    return _page(
        title,
        (
            _catalog_hero(snapshot, catalog),
            _metric_grid("Catalog summary", _catalog_metrics(snapshot, catalog)),
            *_catalog_sections(snapshot, catalog),
        ),
    )


def render_web_settings(snapshot: WebConsoleSnapshot) -> str:
    title = "Universal Agent Runtime Settings"
    return _page(
        title,
        (
            _settings_hero(snapshot),
            _metric_grid(
                "Settings summary",
                (
                    _metric_card("Store", snapshot.config.store_backend),
                    _metric_card("Queue", snapshot.config.distributed_queue_backend),
                    _metric_card("Locks", snapshot.config.distributed_locks_backend),
                    _metric_card("Workers", snapshot.config.distributed_workers_backend),
                    _metric_card(
                        "Retention",
                        _retention_text(snapshot.config.distributed_terminal_retention_seconds),
                    ),
                    _metric_card("Domains", len(snapshot.config.domains)),
                    _metric_card("Max Iterations", snapshot.config.max_iterations),
                    _metric_card("Recovery Steps", snapshot.config.max_recovery_steps),
                ),
            ),
            _operational_diagnostics(snapshot),
            _runtime_settings(snapshot),
            _configured_domains(snapshot),
            _runtime_secrets(snapshot),
            _environment(snapshot),
        ),
    )
