from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from html import escape
from typing import Any

from universal_agent.console import RuntimeConsoleSnapshot, build_runtime_console_snapshot
from universal_agent.core import SessionId
from universal_agent.operations import AuditRecordView, DoctorReportView
from universal_agent.runtime import RuntimeEventView, SessionSummaryView, SessionView
from universal_agent.service import (
    CapabilityView,
    DomainView,
    EvaluatorView,
    MemoryView,
    PolicyView,
    ProfileView,
    RuntimeService,
    SessionExplorerView,
    ToolView,
)

WebConsoleSnapshot = RuntimeConsoleSnapshot


class WebCatalogPage(StrEnum):
    DOMAINS = "domains"
    CAPABILITIES = "capabilities"
    TOOLS = "tools"
    POLICIES = "policies"
    EVALUATORS = "evaluators"
    MEMORY = "memory"


async def build_web_console_snapshot(
    service: RuntimeService,
    *,
    session_id: SessionId | None = None,
    session_limit: int = 10,
    event_limit: int = 20,
) -> WebConsoleSnapshot:
    """Build a read-only Web Console snapshot from RuntimeService projections."""

    return await build_runtime_console_snapshot(
        service,
        session_id=session_id,
        session_limit=session_limit,
        event_limit=event_limit,
    )


def render_web_console(snapshot: WebConsoleSnapshot) -> str:
    title = "Universal Agent Runtime Console"
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{_html(title)}</title>",
            f"<style>{_stylesheet()}</style>",
            "</head>",
            "<body>",
            '<main class="shell">',
            _hero(snapshot),
            '<section class="grid cards" aria-label="Runtime summary">',
            _metric_card("Sessions", snapshot.metrics.session_count),
            _metric_card("Active", snapshot.metrics.active_session_count),
            _metric_card("Events", snapshot.metrics.event_count),
            _metric_card("Actions", _action_count(snapshot)),
            _metric_card("Tokens", snapshot.cost.total_tokens),
            _metric_card("Cost micros", snapshot.cost.estimated_cost_micros),
            "</section>",
            _operational_diagnostics(snapshot),
            _domains(snapshot),
            _profiles(snapshot.profiles),
            _capabilities(snapshot.capabilities),
            _tools(snapshot.tools),
            _policies(snapshot.policies),
            _evaluators(snapshot.evaluators),
            _memory(snapshot.memories),
            _sessions(snapshot.sessions),
            _selected_session(snapshot.selected_session),
            _world_facts(snapshot.session_explorer),
            _world_entities(snapshot.session_explorer),
            _world_relations(snapshot.session_explorer),
            _evidence(snapshot.session_explorer),
            _events(snapshot.events),
            _audit(snapshot.audit_records),
            "</main>",
            "</body>",
            "</html>",
        )
    )


def render_web_session_detail(snapshot: WebConsoleSnapshot) -> str:
    title = "Universal Agent Runtime Session Detail"
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{_html(title)}</title>",
            f"<style>{_stylesheet()}</style>",
            "</head>",
            "<body>",
            '<main class="shell">',
            _session_scoped_hero(snapshot, "Session Detail"),
            '<section class="grid cards" aria-label="Session summary">',
            _metric_card("Iteration", _selected_iteration(snapshot.selected_session)),
            _metric_card("Tasks", _selected_task_count(snapshot.selected_session)),
            _metric_card("Events", len(snapshot.events)),
            _metric_card("Evidence", _selected_evidence_count(snapshot.session_explorer)),
            _metric_card("World Facts", _selected_world_fact_count(snapshot.session_explorer)),
            _metric_card("World Entities", _selected_world_entity_count(snapshot.session_explorer)),
            _metric_card("Audit", len(snapshot.audit_records)),
            "</section>",
            _selected_session(snapshot.selected_session),
            _task_timeline(snapshot.selected_session),
            _world_facts(snapshot.session_explorer),
            _world_entities(snapshot.session_explorer),
            _world_relations(snapshot.session_explorer),
            _evidence(snapshot.session_explorer),
            _events(snapshot.events),
            _audit(snapshot.audit_records),
            "</main>",
            "</body>",
            "</html>",
        )
    )


def render_web_sessions(snapshot: WebConsoleSnapshot) -> str:
    title = "Universal Agent Runtime Sessions"
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{_html(title)}</title>",
            f"<style>{_stylesheet()}</style>",
            "</head>",
            "<body>",
            '<main class="shell">',
            _sessions_hero(snapshot),
            '<section class="grid cards" aria-label="Session summary">',
            _metric_card("Sessions", snapshot.metrics.session_count),
            _metric_card("Active", snapshot.metrics.active_session_count),
            _metric_card("Waiting", snapshot.metrics.waiting_session_count),
            _metric_card("Completed", snapshot.metrics.completed_goal_count),
            _metric_card("Failed", snapshot.metrics.failed_goal_count),
            _metric_card("Cancelled", snapshot.metrics.cancelled_goal_count),
            "</section>",
            _sessions(snapshot.sessions),
            _selected_session(snapshot.selected_session),
            _events(snapshot.events),
            _audit(snapshot.audit_records),
            "</main>",
            "</body>",
            "</html>",
        )
    )


def render_web_doctor(snapshot: WebConsoleSnapshot, doctor: DoctorReportView) -> str:
    title = "Universal Agent Runtime Doctor"
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{_html(title)}</title>",
            f"<style>{_stylesheet()}</style>",
            "</head>",
            "<body>",
            '<main class="shell">',
            _doctor_hero(snapshot, doctor),
            '<section class="grid cards" aria-label="Doctor summary">',
            _metric_card("Status", doctor.status),
            _metric_card("Checks", len(doctor.checks)),
            _metric_card("Errors", _doctor_check_count(doctor, "error")),
            _metric_card("Warnings", _doctor_check_count(doctor, "warn")),
            _metric_card("OK", _doctor_check_count(doctor, "ok")),
            _metric_card("Ready", _ready_text(snapshot)),
            "</section>",
            _doctor_checks(doctor),
            _operational_diagnostics(snapshot),
            _runtime_settings(snapshot),
            "</main>",
            "</body>",
            "</html>",
        )
    )


def render_web_evidence_explorer(snapshot: WebConsoleSnapshot) -> str:
    title = "Universal Agent Runtime Evidence Explorer"
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{_html(title)}</title>",
            f"<style>{_stylesheet()}</style>",
            "</head>",
            "<body>",
            '<main class="shell">',
            _session_scoped_hero(snapshot, "Evidence Explorer"),
            '<section class="grid cards" aria-label="Evidence summary">',
            _metric_card("Evidence", _selected_evidence_count(snapshot.session_explorer)),
            _metric_card("World Facts", _selected_world_fact_count(snapshot.session_explorer)),
            _metric_card("World Entities", _selected_world_entity_count(snapshot.session_explorer)),
            _metric_card("Events", len(snapshot.events)),
            _metric_card("Audit", len(snapshot.audit_records)),
            "</section>",
            _selected_session(snapshot.selected_session),
            _evidence(snapshot.session_explorer),
            _world_facts(snapshot.session_explorer),
            _world_entities(snapshot.session_explorer),
            _world_relations(snapshot.session_explorer),
            _events(snapshot.events),
            "</main>",
            "</body>",
            "</html>",
        )
    )


def render_web_world_model_explorer(snapshot: WebConsoleSnapshot) -> str:
    title = "Universal Agent Runtime World Model Explorer"
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{_html(title)}</title>",
            f"<style>{_stylesheet()}</style>",
            "</head>",
            "<body>",
            '<main class="shell">',
            _session_scoped_hero(snapshot, "World Model Explorer"),
            '<section class="grid cards" aria-label="World model summary">',
            _metric_card("World Facts", _selected_world_fact_count(snapshot.session_explorer)),
            _metric_card("World Entities", _selected_world_entity_count(snapshot.session_explorer)),
            _metric_card(
                "World Relations", _selected_world_relation_count(snapshot.session_explorer)
            ),
            _metric_card("Evidence", _selected_evidence_count(snapshot.session_explorer)),
            _metric_card("Events", len(snapshot.events)),
            _metric_card("Audit", len(snapshot.audit_records)),
            "</section>",
            _selected_session(snapshot.selected_session),
            _world_facts(snapshot.session_explorer),
            _world_entities(snapshot.session_explorer),
            _world_relations(snapshot.session_explorer),
            _evidence(snapshot.session_explorer),
            _events(snapshot.events),
            "</main>",
            "</body>",
            "</html>",
        )
    )


def render_web_domain_detail(
    snapshot: WebConsoleSnapshot,
    *,
    domain_name: str,
    domain_version: str | None = None,
) -> str:
    domain = _selected_domain(snapshot, domain_name, domain_version)
    title = "Universal Agent Runtime Domain Manager"
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{_html(title)}</title>",
            f"<style>{_stylesheet()}</style>",
            "</head>",
            "<body>",
            '<main class="shell">',
            _domain_hero(snapshot, domain),
            '<section class="grid cards" aria-label="Domain summary">',
            _metric_card("Capabilities", _domain_capability_count(snapshot, domain)),
            _metric_card("Tools", _domain_tool_count(snapshot, domain)),
            _metric_card("Policies", _domain_policy_count(snapshot, domain)),
            _metric_card("Evaluators", _domain_evaluator_count(snapshot, domain)),
            _metric_card("Memories", _domain_memory_count(snapshot, domain)),
            "</section>",
            _domain_details(domain),
            _profiles(_domain_profiles(snapshot, domain)),
            _capabilities(_domain_capabilities(snapshot, domain)),
            _tools(_domain_tools(snapshot, domain)),
            _policies(_domain_policies(snapshot, domain)),
            _evaluators(_domain_evaluators(snapshot, domain)),
            _memory(_domain_memories(snapshot, domain)),
            "</main>",
            "</body>",
            "</html>",
        )
    )


def render_web_profile_catalog(snapshot: WebConsoleSnapshot) -> str:
    title = "Universal Agent Runtime Profile Catalog"
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{_html(title)}</title>",
            f"<style>{_stylesheet()}</style>",
            "</head>",
            "<body>",
            '<main class="shell">',
            _profile_hero(snapshot),
            '<section class="grid cards" aria-label="Profile summary">',
            _metric_card("Profiles", len(snapshot.profiles)),
            _metric_card("Active Domains", len(snapshot.domains)),
            _metric_card("Configured Domains", len(snapshot.config.domains)),
            _metric_card("Capabilities", len(snapshot.capabilities)),
            _metric_card("Tools", len(snapshot.tools)),
            _metric_card("Ready", _ready_text(snapshot)),
            "</section>",
            _profiles(snapshot.profiles),
            _domains(snapshot),
            _configured_domains(snapshot),
            _capabilities(snapshot.capabilities),
            "</main>",
            "</body>",
            "</html>",
        )
    )


def render_web_catalog(snapshot: WebConsoleSnapshot, catalog: WebCatalogPage) -> str:
    title = f"Universal Agent Runtime {_catalog_title(catalog)}"
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{_html(title)}</title>",
            f"<style>{_stylesheet()}</style>",
            "</head>",
            "<body>",
            '<main class="shell">',
            _catalog_hero(snapshot, catalog),
            '<section class="grid cards" aria-label="Catalog summary">',
            *_catalog_metrics(snapshot, catalog),
            "</section>",
            *_catalog_sections(snapshot, catalog),
            "</main>",
            "</body>",
            "</html>",
        )
    )


def render_web_settings(snapshot: WebConsoleSnapshot) -> str:
    title = "Universal Agent Runtime Settings"
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{_html(title)}</title>",
            f"<style>{_stylesheet()}</style>",
            "</head>",
            "<body>",
            '<main class="shell">',
            _settings_hero(snapshot),
            '<section class="grid cards" aria-label="Settings summary">',
            _metric_card("Store", snapshot.config.store_backend),
            _metric_card("Queue", snapshot.config.distributed_queue_backend),
            _metric_card("Locks", snapshot.config.distributed_locks_backend),
            _metric_card("Workers", snapshot.config.distributed_workers_backend),
            _metric_card("Domains", len(snapshot.config.domains)),
            _metric_card("Max Iterations", snapshot.config.max_iterations),
            _metric_card("Recovery Steps", snapshot.config.max_recovery_steps),
            "</section>",
            _operational_diagnostics(snapshot),
            _runtime_settings(snapshot),
            _configured_domains(snapshot),
            _environment(snapshot),
            "</main>",
            "</body>",
            "</html>",
        )
    )


def _hero(snapshot: WebConsoleSnapshot) -> str:
    ready_class = "ok" if snapshot.ready.ready else "warn"
    return "\n".join(
        (
            '<section class="hero">',
            "<div>",
            "<p>Universal Agent Runtime</p>",
            "<h1>Runtime Console</h1>",
            (
                "<span>"
                f"store={_html(snapshot.config.store_backend)} "
                f"queue={_html(snapshot.config.distributed_queue_backend)} "
                f"locks={_html(snapshot.config.distributed_locks_backend)} "
                f"workers={_html(snapshot.config.distributed_workers_backend)} "
                f"max_iterations={snapshot.config.max_iterations} "
                f"max_recovery_steps={snapshot.config.max_recovery_steps}"
                "</span>"
            ),
            "</div>",
            '<div class="status">',
            '<a class="pill link" href="/console/sessions">Sessions</a>',
            '<a class="pill link" href="/console/profiles">Profiles</a>',
            '<a class="pill link" href="/console/doctor">Doctor</a>',
            '<a class="pill link" href="/console/evaluations">Evaluations</a>',
            '<a class="pill link" href="/console/settings">Settings</a>',
            f'<span class="pill ok">Health: {_html(snapshot.health.status)}</span>',
            f'<span class="pill {ready_class}">Ready: {_ready_text(snapshot)}</span>',
            "</div>",
            "</section>",
        )
    )


def _settings_hero(snapshot: WebConsoleSnapshot) -> str:
    ready_class = "ok" if snapshot.ready.ready else "warn"
    return "\n".join(
        (
            '<section class="hero">',
            "<div>",
            "<p>Universal Agent Runtime</p>",
            "<h1>Settings</h1>",
            (
                "<span>"
                f"store={_html(snapshot.config.store_backend)} "
                f"path={_html(snapshot.config.store_path or 'memory')} "
                f"queue={_html(snapshot.config.distributed_queue_backend)} "
                f"locks={_html(snapshot.config.distributed_locks_backend)} "
                f"workers={_html(snapshot.config.distributed_workers_backend)}"
                "</span>"
            ),
            "</div>",
            '<div class="status">',
            '<a class="pill link" href="/console">Console</a>',
            '<a class="pill link" href="/console/sessions">Sessions</a>',
            '<a class="pill link" href="/console/profiles">Profiles</a>',
            '<a class="pill link" href="/console/doctor">Doctor</a>',
            '<a class="pill link" href="/console/evaluations">Evaluations</a>',
            f'<span class="pill ok">Health: {_html(snapshot.health.status)}</span>',
            f'<span class="pill {ready_class}">Ready: {_ready_text(snapshot)}</span>',
            "</div>",
            "</section>",
        )
    )


def _domain_hero(snapshot: WebConsoleSnapshot, domain: DomainView | None) -> str:
    ready_class = "ok" if snapshot.ready.ready else "warn"
    domain_text = "No selected domain"
    if domain is not None:
        domain_text = f"{domain.name}@{domain.version}"
    return "\n".join(
        (
            '<section class="hero">',
            "<div>",
            "<p>Universal Agent Runtime</p>",
            "<h1>Domain Manager</h1>",
            f"<span>domain={_html(domain_text)}</span>",
            "</div>",
            '<div class="status">',
            '<a class="pill link" href="/console">Console</a>',
            '<a class="pill link" href="/console/sessions">Sessions</a>',
            '<a class="pill link" href="/console/profiles">Profiles</a>',
            '<a class="pill link" href="/console/doctor">Doctor</a>',
            '<a class="pill link" href="/console/evaluations">Evaluations</a>',
            f'<span class="pill ok">Health: {_html(snapshot.health.status)}</span>',
            f'<span class="pill {ready_class}">Ready: {_ready_text(snapshot)}</span>',
            "</div>",
            "</section>",
        )
    )


def _profile_hero(snapshot: WebConsoleSnapshot) -> str:
    ready_class = "ok" if snapshot.ready.ready else "warn"
    return "\n".join(
        (
            '<section class="hero">',
            "<div>",
            "<p>Universal Agent Runtime</p>",
            "<h1>Profile Catalog</h1>",
            (
                "<span>"
                f"profiles={len(snapshot.profiles)} "
                f"domains={len(snapshot.domains)} "
                f"store={_html(snapshot.config.store_backend)}"
                "</span>"
            ),
            "</div>",
            '<div class="status">',
            '<a class="pill link" href="/console">Console</a>',
            '<a class="pill link" href="/console/sessions">Sessions</a>',
            '<a class="pill link" href="/console/doctor">Doctor</a>',
            '<a class="pill link" href="/console/evaluations">Evaluations</a>',
            '<a class="pill link" href="/console/settings">Settings</a>',
            f'<span class="pill ok">Health: {_html(snapshot.health.status)}</span>',
            f'<span class="pill {ready_class}">Ready: {_ready_text(snapshot)}</span>',
            "</div>",
            "</section>",
        )
    )


def _catalog_hero(snapshot: WebConsoleSnapshot, catalog: WebCatalogPage) -> str:
    ready_class = "ok" if snapshot.ready.ready else "warn"
    return "\n".join(
        (
            '<section class="hero">',
            "<div>",
            "<p>Universal Agent Runtime</p>",
            f"<h1>{_html(_catalog_title(catalog))}</h1>",
            (
                "<span>"
                f"catalog={_html(catalog.value)} "
                f"domains={len(snapshot.domains)} "
                f"store={_html(snapshot.config.store_backend)}"
                "</span>"
            ),
            "</div>",
            '<div class="status">',
            '<a class="pill link" href="/console">Console</a>',
            '<a class="pill link" href="/console/sessions">Sessions</a>',
            '<a class="pill link" href="/console/profiles">Profiles</a>',
            '<a class="pill link" href="/console/doctor">Doctor</a>',
            '<a class="pill link" href="/console/evaluations">Evaluations</a>',
            '<a class="pill link" href="/console/settings">Settings</a>',
            f'<span class="pill ok">Health: {_html(snapshot.health.status)}</span>',
            f'<span class="pill {ready_class}">Ready: {_ready_text(snapshot)}</span>',
            "</div>",
            "</section>",
        )
    )


def _catalog_title(catalog: WebCatalogPage) -> str:
    titles = {
        WebCatalogPage.DOMAINS: "Domain Catalog",
        WebCatalogPage.CAPABILITIES: "Capability Catalog",
        WebCatalogPage.TOOLS: "Tool Catalog",
        WebCatalogPage.POLICIES: "Policy Catalog",
        WebCatalogPage.EVALUATORS: "Evaluator Catalog",
        WebCatalogPage.MEMORY: "Memory Catalog",
    }
    return titles[catalog]


def _catalog_metrics(snapshot: WebConsoleSnapshot, catalog: WebCatalogPage) -> tuple[str, ...]:
    if catalog is WebCatalogPage.DOMAINS:
        return (
            _metric_card("Active Domains", len(snapshot.domains)),
            _metric_card("Configured Domains", len(snapshot.config.domains)),
            _metric_card("Profiles", len(snapshot.profiles)),
            _metric_card("Capabilities", len(snapshot.capabilities)),
            _metric_card("Evaluators", len(snapshot.evaluators)),
            _metric_card("Ready", _ready_text(snapshot)),
        )
    if catalog is WebCatalogPage.CAPABILITIES:
        return (
            _metric_card("Capabilities", len(snapshot.capabilities)),
            _metric_card("High Risk", _risk_count(snapshot.capabilities, "high")),
            _metric_card("Medium Risk", _risk_count(snapshot.capabilities, "medium")),
            _metric_card("Tools", len(snapshot.tools)),
            _metric_card("Domains", len(snapshot.domains)),
            _metric_card("Ready", _ready_text(snapshot)),
        )
    if catalog is WebCatalogPage.TOOLS:
        return (
            _metric_card("Tools", len(snapshot.tools)),
            _metric_card("No Side Effect", _side_effect_count(snapshot.tools, "none")),
            _metric_card("Reversible", _side_effect_count(snapshot.tools, "reversible")),
            _metric_card("Destructive", _side_effect_count(snapshot.tools, "destructive")),
            _metric_card("High Risk", _risk_count(snapshot.tools, "high")),
        )
    if catalog is WebCatalogPage.POLICIES:
        return (
            _metric_card("Policies", len(snapshot.policies)),
            _metric_card("Allow", _policy_effect_count(snapshot.policies, "allow")),
            _metric_card(
                "Confirm",
                _policy_effect_count(snapshot.policies, "require_confirmation"),
            ),
            _metric_card("Deny", _policy_effect_count(snapshot.policies, "deny")),
            _metric_card("Domains", len(snapshot.domains)),
            _metric_card("Ready", _ready_text(snapshot)),
        )
    if catalog is WebCatalogPage.EVALUATORS:
        return (
            _metric_card("Evaluators", len(snapshot.evaluators)),
            _metric_card("Domains", len(snapshot.domains)),
            _metric_card("Capabilities", len(snapshot.capabilities)),
            _metric_card("Sessions", len(snapshot.sessions)),
            _metric_card("Events", len(snapshot.events)),
            _metric_card("Ready", _ready_text(snapshot)),
        )
    return (
        _metric_card("Memories", len(snapshot.memories)),
        _metric_card("Global", _global_memory_count(snapshot.memories)),
        _metric_card("Scoped", _scoped_memory_count(snapshot.memories)),
        _metric_card("Profiles", len(snapshot.profiles)),
        _metric_card("Domains", len(snapshot.domains)),
        _metric_card("Ready", _ready_text(snapshot)),
    )


def _catalog_sections(snapshot: WebConsoleSnapshot, catalog: WebCatalogPage) -> tuple[str, ...]:
    if catalog is WebCatalogPage.DOMAINS:
        return (_domains(snapshot), _configured_domains(snapshot), _profiles(snapshot.profiles))
    if catalog is WebCatalogPage.CAPABILITIES:
        return (_capabilities(snapshot.capabilities), _domains(snapshot), _tools(snapshot.tools))
    if catalog is WebCatalogPage.TOOLS:
        return (_tools(snapshot.tools), _capabilities(snapshot.capabilities), _domains(snapshot))
    if catalog is WebCatalogPage.POLICIES:
        return (
            _policies(snapshot.policies),
            _capabilities(snapshot.capabilities),
            _domains(snapshot),
        )
    if catalog is WebCatalogPage.EVALUATORS:
        return (_evaluators(snapshot.evaluators), _domains(snapshot), _sessions(snapshot.sessions))
    return (_memory(snapshot.memories), _domains(snapshot), _profiles(snapshot.profiles))


def _sessions_hero(snapshot: WebConsoleSnapshot) -> str:
    ready_class = "ok" if snapshot.ready.ready else "warn"
    return "\n".join(
        (
            '<section class="hero">',
            "<div>",
            "<p>Universal Agent Runtime</p>",
            "<h1>Sessions</h1>",
            (
                "<span>"
                f"sessions={snapshot.metrics.session_count} "
                f"active={snapshot.metrics.active_session_count} "
                f"waiting={snapshot.metrics.waiting_session_count}"
                "</span>"
            ),
            "</div>",
            '<div class="status">',
            '<a class="pill link" href="/console">Console</a>',
            '<a class="pill link" href="/console/profiles">Profiles</a>',
            '<a class="pill link" href="/console/doctor">Doctor</a>',
            '<a class="pill link" href="/console/evaluations">Evaluations</a>',
            '<a class="pill link" href="/console/settings">Settings</a>',
            f'<span class="pill ok">Health: {_html(snapshot.health.status)}</span>',
            f'<span class="pill {ready_class}">Ready: {_ready_text(snapshot)}</span>',
            "</div>",
            "</section>",
        )
    )


def _doctor_hero(snapshot: WebConsoleSnapshot, doctor: DoctorReportView) -> str:
    ready_class = "ok" if snapshot.ready.ready else "warn"
    doctor_class = _status_class(doctor.status)
    return "\n".join(
        (
            '<section class="hero">',
            "<div>",
            "<p>Universal Agent Runtime</p>",
            "<h1>Runtime Doctor</h1>",
            f"<span>status={_html(doctor.status)} checks={len(doctor.checks)}</span>",
            "</div>",
            '<div class="status">',
            '<a class="pill link" href="/console">Console</a>',
            '<a class="pill link" href="/console/sessions">Sessions</a>',
            '<a class="pill link" href="/console/settings">Settings</a>',
            '<a class="pill link" href="/console/evaluations">Evaluations</a>',
            f'<span class="pill {doctor_class}">Doctor: {_html(doctor.status)}</span>',
            f'<span class="pill ok">Health: {_html(snapshot.health.status)}</span>',
            f'<span class="pill {ready_class}">Ready: {_ready_text(snapshot)}</span>',
            "</div>",
            "</section>",
        )
    )


def _session_scoped_hero(snapshot: WebConsoleSnapshot, title: str) -> str:
    ready_class = "ok" if snapshot.ready.ready else "warn"
    selected = snapshot.selected_session
    session_text = "No selected session"
    goal_text = "No selected goal"
    if selected is not None:
        session_text = str(selected.session_id)
        goal_text = selected.goal_description
    return "\n".join(
        (
            '<section class="hero">',
            "<div>",
            "<p>Universal Agent Runtime</p>",
            f"<h1>{_html(title)}</h1>",
            (f"<span>session={_html(session_text)} goal={_html(goal_text)}</span>"),
            "</div>",
            '<div class="status">',
            _session_nav(snapshot.selected_session),
            f'<span class="pill ok">Health: {_html(snapshot.health.status)}</span>',
            f'<span class="pill {ready_class}">Ready: {_ready_text(snapshot)}</span>',
            "</div>",
            "</section>",
        )
    )


def _session_nav(session: SessionView | None) -> str:
    links = [
        '<a class="pill link" href="/console">Console</a>',
        '<a class="pill link" href="/console/sessions">Sessions</a>',
        '<a class="pill link" href="/console/doctor">Doctor</a>',
    ]
    if session is not None:
        session_id = _attr(session.session_id)
        links.extend(
            (
                f'<a class="pill link" href="/console/sessions/{session_id}">Detail</a>',
                f'<a class="pill link" href="/console/sessions/{session_id}/evidence">Evidence</a>',
                f'<a class="pill link" href="/console/sessions/{session_id}/world">World</a>',
            )
        )
    return "".join(links)


def _domains(snapshot: WebConsoleSnapshot) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                (
                    '<td><a href="/console/domains/'
                    f'{_attr(domain.name)}/{_attr(domain.version)}">'
                    f"{_html(domain.name)}@{_html(domain.version)}</a></td>"
                ),
                f"<td>{'yes' if domain.primary else 'no'}</td>",
                f"<td>{len(domain.capability_names)}</td>",
                f"<td>{len(domain.evaluator_names)}</td>",
                "</tr>",
            )
        )
        for domain in snapshot.domains
    ]
    if not rows:
        rows.append('<tr><td colspan="4">No active domains</td></tr>')
    return _section(
        "Active Domains",
        _table(
            ("Domain", "Primary", "Capabilities", "Evaluators"),
            tuple(rows),
        ),
    )


def _domain_details(domain: DomainView | None) -> str:
    if domain is None:
        return _section("Domain", '<p class="empty">No selected domain</p>')
    items = (
        ("Domain", f"{domain.name}@{domain.version}"),
        ("Primary", "yes" if domain.primary else "no"),
        ("Description", domain.description),
        ("Ontology", _string_tuple_text(domain.ontology)),
        ("Capabilities", _string_tuple_text(domain.capability_names)),
        ("Evaluators", _string_tuple_text(domain.evaluator_names)),
    )
    return _section(
        "Domain",
        '<dl class="details">'
        + "".join(f"<dt>{_html(label)}</dt><dd>{_html(value)}</dd>" for label, value in items)
        + "</dl>",
    )


def _runtime_settings(snapshot: WebConsoleSnapshot) -> str:
    items = (
        ("Store Backend", snapshot.config.store_backend),
        ("Store Path", snapshot.config.store_path or "memory"),
        ("Distributed Queue Backend", snapshot.config.distributed_queue_backend),
        ("Distributed Queue Path", snapshot.config.distributed_queue_path or "memory"),
        ("Distributed Locks Backend", snapshot.config.distributed_locks_backend),
        ("Distributed Locks Path", snapshot.config.distributed_locks_path or "memory"),
        ("Distributed Workers Backend", snapshot.config.distributed_workers_backend),
        ("Distributed Workers Path", snapshot.config.distributed_workers_path or "memory"),
        ("Max Iterations", str(snapshot.config.max_iterations)),
        ("Max Recovery Steps", str(snapshot.config.max_recovery_steps)),
        ("Health", snapshot.health.status),
        ("Ready", _ready_text(snapshot)),
    )
    return _section(
        "Runtime Configuration",
        '<dl class="details">'
        + "".join(f"<dt>{_html(label)}</dt><dd>{_html(value)}</dd>" for label, value in items)
        + "</dl>",
    )


def _doctor_checks(doctor: DoctorReportView) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(check.name)}</td>",
                (
                    '<td><span class="severity '
                    f'{_status_class(check.status)}">{_html(check.status)}</span></td>'
                ),
                f"<td>{_html(check.message)}</td>",
                "</tr>",
            )
        )
        for check in doctor.checks
    ]
    if not rows:
        rows.append('<tr><td colspan="3">No doctor checks</td></tr>')
    return _section(
        "Doctor Checks",
        _table(("Check", "Status", "Message"), tuple(rows)),
    )


def _operational_diagnostics(snapshot: WebConsoleSnapshot) -> str:
    rows = tuple(_operational_diagnostic_rows(snapshot))
    if not rows:
        rows = (
            _diagnostic_row(
                "ok",
                "runtime",
                "operational",
                "No active operational issues",
            ),
        )
    return _section(
        "Operational Diagnostics",
        _table(("Severity", "Signal", "Value", "Reason"), rows),
    )


def _operational_diagnostic_rows(snapshot: WebConsoleSnapshot) -> list[str]:
    metrics = snapshot.metrics
    rows: list[str] = []
    if not snapshot.ready.ready:
        rows.append(_diagnostic_row("error", "ready", "no", snapshot.ready.reason))
    if metrics.failed_goal_count:
        rows.append(_diagnostic_row("error", "failed_goals", metrics.failed_goal_count))
    if metrics.tool_failure_count:
        rows.append(_diagnostic_row("error", "tool_failures", metrics.tool_failure_count))
    if metrics.recovery_exhausted_count:
        rows.append(
            _diagnostic_row("error", "recovery_exhausted", metrics.recovery_exhausted_count)
        )
    if metrics.policy_denial_count:
        rows.append(_diagnostic_row("warn", "policy_denials", metrics.policy_denial_count))
    if metrics.confirmation_required_count:
        rows.append(
            _diagnostic_row("warn", "confirmations_required", metrics.confirmation_required_count)
        )
    if metrics.human_intervention_count:
        rows.append(
            _diagnostic_row("warn", "human_interventions", metrics.human_intervention_count)
        )
    if metrics.resource_conflict_count:
        rows.append(_diagnostic_row("warn", "resource_conflicts", metrics.resource_conflict_count))
    if metrics.active_resource_lock_count:
        rows.append(
            _diagnostic_row("warn", "active_resource_locks", metrics.active_resource_lock_count)
        )
    if metrics.waiting_session_count:
        rows.append(_diagnostic_row("info", "waiting_sessions", metrics.waiting_session_count))
    if metrics.recovery_planned_count:
        rows.append(_diagnostic_row("info", "recoveries_planned", metrics.recovery_planned_count))
    return rows


def _diagnostic_row(
    severity: str,
    signal: str,
    value: object,
    reason: str = "",
) -> str:
    return "\n".join(
        (
            "<tr>",
            f'<td><span class="severity {severity}">{_html(severity)}</span></td>',
            f"<td>{_html(signal)}</td>",
            f"<td>{_html(value)}</td>",
            f"<td>{_html(reason)}</td>",
            "</tr>",
        )
    )


def _configured_domains(snapshot: WebConsoleSnapshot) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(domain.name)}</td>",
                f"<td>{_html(domain.version)}</td>",
                f"<td>{'yes' if domain.primary else 'no'}</td>",
                "</tr>",
            )
        )
        for domain in snapshot.config.domains
    ]
    if not rows:
        rows.append('<tr><td colspan="3">No configured domains</td></tr>')
    return _section(
        "Configured Domains",
        _table(("Domain", "Version", "Primary"), tuple(rows)),
    )


def _environment(snapshot: WebConsoleSnapshot) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(key)}</td>",
                f"<td>{_html(_value_text(value))}</td>",
                "</tr>",
            )
        )
        for key, value in sorted(snapshot.config.environment.items())
    ]
    if not rows:
        rows.append('<tr><td colspan="2">No environment settings</td></tr>')
    return _section("Environment", _table(("Key", "Value"), tuple(rows)))


def _profiles(profiles: tuple[ProfileView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(profile.name)}</td>",
                f"<td>{_html(profile.version)}</td>",
                f"<td>{_html(profile.domain_name)}@{_html(profile.domain_version)}</td>",
                f"<td>{_html(_profile_domain_text(profile))}</td>",
                f"<td>{_html(profile.description)}</td>",
                "</tr>",
            )
        )
        for profile in profiles
    ]
    if not rows:
        rows.append('<tr><td colspan="5">No profiles</td></tr>')
    return _section(
        "Profile Catalog",
        _table(("Profile", "Version", "Primary Domain", "Domains", "Description"), tuple(rows)),
    )


def _capabilities(capabilities: tuple[CapabilityView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(capability.name)}</td>",
                f"<td>{_html(capability.category.value)}</td>",
                f"<td>{_html(capability.risk.value)}</td>",
                (f"<td>{_html(capability.domain_name)}@{_html(capability.domain_version)}</td>"),
                f"<td>{_html(', '.join(capability.tool_names))}</td>",
                f"<td>{_html(capability.description)}</td>",
                "</tr>",
            )
        )
        for capability in capabilities
    ]
    if not rows:
        rows.append('<tr><td colspan="6">No capabilities</td></tr>')
    return _section(
        "Capability Catalog",
        _table(("Capability", "Category", "Risk", "Domain", "Tools", "Description"), tuple(rows)),
    )


def _tools(tools: tuple[ToolView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(tool.name)}</td>",
                f"<td>{_html(tool.side_effect.value)}</td>",
                f"<td>{_html(tool.risk.value)}</td>",
                f"<td>{_html(', '.join(tool.capabilities))}</td>",
                f"<td>{_html(', '.join(tool.required_arguments))}</td>",
                f"<td>{tool.timeout_seconds:g}s</td>",
                f"<td>{_html(tool.domain_name)}@{_html(tool.domain_version)}</td>",
                "</tr>",
            )
        )
        for tool in tools
    ]
    if not rows:
        rows.append('<tr><td colspan="7">No tools</td></tr>')
    return _section(
        "Tool Catalog",
        _table(
            ("Tool", "Side Effect", "Risk", "Capabilities", "Required Args", "Timeout", "Domain"),
            tuple(rows),
        ),
    )


def _policies(policies: tuple[PolicyView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(policy.name)}</td>",
                f"<td>{_html(policy.policy_type)}</td>",
                f"<td>{_html('n/a' if policy.effect is None else policy.effect.value)}</td>",
                f"<td>{_html(_enum_tuple_text(policy.categories))}</td>",
                f"<td>{_html(_enum_tuple_text(policy.risks))}</td>",
                f"<td>{_html(', '.join(policy.capability_names))}</td>",
                f"<td>{_html(policy.domain_name)}@{_html(policy.domain_version)}</td>",
                f"<td>{_html(policy.description)}</td>",
                "</tr>",
            )
        )
        for policy in policies
    ]
    if not rows:
        rows.append('<tr><td colspan="8">No policies</td></tr>')
    return _section(
        "Policy Catalog",
        _table(
            ("Policy", "Type", "Effect", "Categories", "Risks", "Capabilities", "Domain", "Reason"),
            tuple(rows),
        ),
    )


def _evaluators(evaluators: tuple[EvaluatorView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(evaluator.name)}</td>",
                f"<td>{_html(evaluator.evaluator_type)}</td>",
                f"<td>{_html(evaluator.domain_name)}@{_html(evaluator.domain_version)}</td>",
                "</tr>",
            )
        )
        for evaluator in evaluators
    ]
    if not rows:
        rows.append('<tr><td colspan="3">No evaluators</td></tr>')
    return _section(
        "Evaluator Catalog",
        _table(("Evaluator", "Type", "Domain"), tuple(rows)),
    )


def _memory(memories: tuple[MemoryView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(memory.memory_id)}</td>",
                f"<td>{_html(memory.kind.value)}</td>",
                f"<td>{_html(memory.subject)}</td>",
                f"<td>{_html(memory.scope or 'global')}</td>",
                f"<td>{memory.confidence:.2f}</td>",
                f"<td>{_html(memory.source_session_id or 'none')}</td>",
                f"<td>{_html(memory.content)}</td>",
                "</tr>",
            )
        )
        for memory in memories
    ]
    if not rows:
        rows.append('<tr><td colspan="7">No memory</td></tr>')
    return _section(
        "Memory Catalog",
        _table(
            ("Memory", "Kind", "Subject", "Scope", "Confidence", "Source Session", "Content"),
            tuple(rows),
        ),
    )


def _sessions(sessions: tuple[SessionSummaryView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                (
                    '<td><a href="/console/sessions/'
                    f'{_attr(session.session_id)}">{_html(session.session_id)}</a></td>'
                ),
                f"<td>{_html(session.goal_status.value)}</td>",
                f"<td>{_html(session.current_task_status.value)}</td>",
                f"<td>{session.iteration}</td>",
                f"<td>{_html(session.domain_name)}@{_html(session.domain_version)}</td>",
                f"<td>{_html(session.goal_description)}</td>",
                "</tr>",
            )
        )
        for session in sessions
    ]
    if not rows:
        rows.append('<tr><td colspan="6">No sessions</td></tr>')
    return _section(
        "Sessions",
        _table(
            ("Session", "Goal", "Task", "Iter", "Domain", "Description"),
            tuple(rows),
        ),
    )


def _selected_session(session: SessionView | None) -> str:
    if session is None:
        return _section("Selected Session", '<p class="empty">No selected session</p>')
    pending = "none"
    if session.pending_action is not None:
        pending = (
            f"{session.pending_action.capability} "
            f"tool={session.pending_action.tool_name} "
            f"attempt={session.pending_action.attempt}"
        )
    latest = "none"
    if session.latest_evaluation is not None:
        latest = (
            f"{session.latest_evaluation.status.value} "
            f"task_completed={session.latest_evaluation.task_completed} "
            f"goal_completed={session.latest_evaluation.goal_completed} "
            f"reason={session.latest_evaluation.reason}"
        )
    items = (
        ("Session", str(session.session_id)),
        ("Goal", f"{session.goal_status.value}: {session.goal_description}"),
        (
            "Current Task",
            f"{session.current_task_status.value}: {session.current_task_description}",
        ),
        ("Iteration", str(session.iteration)),
        ("Domain", f"{session.domain_name}@{session.domain_version}"),
        ("Satisfied Criteria", _mapping_text(session.satisfied_criteria)),
        ("Pending Action", pending),
        ("Latest Evaluation", latest),
    )
    return _section(
        "Selected Session",
        '<dl class="details">'
        + "".join(f"<dt>{_html(label)}</dt><dd>{_html(value)}</dd>" for label, value in items)
        + "</dl>",
    )


def _task_timeline(session: SessionView | None) -> str:
    rows = []
    if session is not None:
        rows = [
            "\n".join(
                (
                    "<tr>",
                    f"<td>{_html(task.task_id)}</td>",
                    f"<td>{_html(task.status.value)}</td>",
                    f"<td>{_html(task.description)}</td>",
                    f"<td>{_html(_string_tuple_text(task.required_criteria))}</td>",
                    f"<td>{_html(_string_tuple_text(task.depends_on))}</td>",
                    "</tr>",
                )
            )
            for task in session.tasks
        ]
    if not rows:
        rows.append('<tr><td colspan="5">No tasks</td></tr>')
    return _section(
        "Task Timeline",
        _table(("Task", "Status", "Description", "Required Criteria", "Depends On"), tuple(rows)),
    )


def _world_facts(explorer: SessionExplorerView | None) -> str:
    rows = []
    if explorer is not None:
        rows = [
            "\n".join(
                (
                    "<tr>",
                    f"<td>{_html(fact.subject)}</td>",
                    f"<td>{_html(fact.claim)}</td>",
                    f"<td>{_html(_value_text(fact.value))}</td>",
                    f"<td>{fact.confidence:.2f}</td>",
                    f"<td>{_html(', '.join(fact.evidence_ids))}</td>",
                    "</tr>",
                )
            )
            for fact in explorer.world_facts
        ]
    if not rows:
        rows.append('<tr><td colspan="5">No world facts</td></tr>')
    return _section(
        "World Facts",
        _table(("Subject", "Claim", "Value", "Confidence", "Evidence"), tuple(rows)),
    )


def _world_entities(explorer: SessionExplorerView | None) -> str:
    rows = []
    if explorer is not None:
        rows = [
            "\n".join(
                (
                    "<tr>",
                    f"<td>{_html(entity.entity_id)}</td>",
                    f"<td>{_html(entity.kind)}</td>",
                    f"<td>{_html(_value_text(entity.attributes))}</td>",
                    f"<td>{_html(', '.join(entity.evidence_ids))}</td>",
                    "</tr>",
                )
            )
            for entity in explorer.world_entities
        ]
    if not rows:
        rows.append('<tr><td colspan="4">No world entities</td></tr>')
    return _section(
        "World Entities",
        _table(("Entity", "Kind", "Attributes", "Evidence"), tuple(rows)),
    )


def _world_relations(explorer: SessionExplorerView | None) -> str:
    rows = []
    if explorer is not None:
        rows = [
            "\n".join(
                (
                    "<tr>",
                    f"<td>{_html(relation.source)}</td>",
                    f"<td>{_html(relation.relation)}</td>",
                    f"<td>{_html(relation.target)}</td>",
                    f"<td>{_html(', '.join(relation.evidence_ids))}</td>",
                    "</tr>",
                )
            )
            for relation in explorer.world_relations
        ]
    if not rows:
        rows.append('<tr><td colspan="4">No world relations</td></tr>')
    return _section(
        "World Relations",
        _table(("Source", "Relation", "Target", "Evidence"), tuple(rows)),
    )


def _evidence(explorer: SessionExplorerView | None) -> str:
    rows = []
    if explorer is not None:
        rows = [
            "\n".join(
                (
                    "<tr>",
                    f"<td>{_html(item.evidence_id)}</td>",
                    f"<td>{_html(item.subject)}</td>",
                    f"<td>{_html(item.claim)}</td>",
                    f"<td>{_html(_value_text(item.value))}</td>",
                    f"<td>{_html(item.source)}</td>",
                    f"<td>{item.confidence:.2f}</td>",
                    "</tr>",
                )
            )
            for item in explorer.evidence
        ]
    if not rows:
        rows.append('<tr><td colspan="6">No evidence</td></tr>')
    return _section(
        "Session Evidence",
        _table(("Evidence", "Subject", "Claim", "Value", "Source", "Confidence"), tuple(rows)),
    )


def _events(events: tuple[RuntimeEventView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(event.occurred_at.isoformat())}</td>",
                f"<td>{_html(event.type)}</td>",
                f"<td>{_html(event.task_id)}</td>",
                f"<td>{_html(event.action_id or '-')}</td>",
                f"<td>{_html(_event_detail(event.data))}</td>",
                "</tr>",
            )
        )
        for event in events
    ]
    if not rows:
        rows.append('<tr><td colspan="5">No events</td></tr>')
    return _section(
        "Recent Events",
        _table(("Time", "Type", "Task", "Action", "Detail"), tuple(rows)),
    )


def _audit(records: tuple[AuditRecordView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(record.occurred_at.isoformat())}</td>",
                f"<td>{_html(record.capability)}</td>",
                f"<td>{_html(record.tool_name)}</td>",
                f"<td>{_html(record.policy_effect)}:{_html(record.policy_name)}</td>",
                f"<td>{_html(record.status)}</td>",
                "</tr>",
            )
        )
        for record in records
    ]
    if not rows:
        rows.append('<tr><td colspan="5">No audit records</td></tr>')
    return _section(
        "Audit",
        _table(("Time", "Capability", "Tool", "Policy", "Status"), tuple(rows)),
    )


def _section(title: str, body: str) -> str:
    return "\n".join(
        (
            '<section class="panel">',
            f"<h2>{_html(title)}</h2>",
            body,
            "</section>",
        )
    )


def _metric_card(label: str, value: object) -> str:
    return "\n".join(
        (
            '<article class="card">',
            f"<span>{_html(label)}</span>",
            f"<strong>{_html(value)}</strong>",
            "</article>",
        )
    )


def _table(headers: tuple[str, ...], rows: tuple[str, ...]) -> str:
    header = "".join(f"<th>{_html(item)}</th>" for item in headers)
    return (
        '<div class="table-wrap"><table><thead><tr>'
        + header
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


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


def _enum_tuple_text(values: tuple[Any, ...]) -> str:
    if not values:
        return "none"
    return ", ".join(str(getattr(value, "value", value)) for value in values)


def _value_text(value: object) -> str:
    if isinstance(value, Mapping):
        return json.dumps(dict(value), sort_keys=True)
    if isinstance(value, list):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _html(value: object) -> str:
    return escape(str(value), quote=False)


def _attr(value: object) -> str:
    return escape(str(value), quote=True)


def _status_class(status: str) -> str:
    if status == "error":
        return "error"
    if status == "warn":
        return "warn"
    return "ok"


def _stylesheet() -> str:
    return """
:root {
  color-scheme: light;
  font-family:
    Inter,
    ui-sans-serif,
    system-ui,
    -apple-system,
    BlinkMacSystemFont,
    "Segoe UI",
    sans-serif;
  background: #f5f7fb;
  color: #172033;
}
* {
  box-sizing: border-box;
}
body {
  margin: 0;
}
.shell {
  width: min(1180px, calc(100vw - 40px));
  margin: 0 auto;
  padding: 28px 0 40px;
}
.hero, .panel, .card {
  background: #ffffff;
  border: 1px solid #d9e1ec;
  border-radius: 8px;
  box-shadow: 0 1px 2px rgba(23, 32, 51, 0.05);
}
.hero {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  align-items: flex-start;
  padding: 24px;
  border-top: 4px solid #0f766e;
}
.hero p, .hero h1 {
  margin: 0;
}
.hero p {
  color: #5d6b82;
  font-size: 13px;
  font-weight: 700;
  text-transform: uppercase;
}
.hero h1 {
  margin-top: 4px;
  font-size: 30px;
  line-height: 1.1;
}
.hero span {
  display: inline-block;
  margin-top: 10px;
  color: #5d6b82;
  font-size: 14px;
}
.status {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
.pill {
  border-radius: 999px;
  padding: 7px 10px;
  font-size: 13px;
  font-weight: 700;
}
.severity {
  display: inline-block;
  min-width: 56px;
  border-radius: 999px;
  padding: 4px 8px;
  text-align: center;
  font-size: 12px;
  font-weight: 700;
}
.ok {
  background: #dcfce7;
  color: #166534;
}
.warn {
  background: #fff7ed;
  color: #9a3412;
}
.error {
  background: #fee2e2;
  color: #991b1b;
}
.grid {
  display: grid;
  gap: 12px;
}
.cards {
  grid-template-columns: repeat(6, minmax(0, 1fr));
  margin: 16px 0;
}
.card {
  min-height: 82px;
  padding: 16px;
}
.card span {
  display: block;
  color: #5d6b82;
  font-size: 13px;
}
.card strong {
  display: block;
  margin-top: 8px;
  font-size: 24px;
}
.panel {
  margin-top: 16px;
  padding: 20px;
}
.panel h2 {
  margin: 0 0 14px;
  font-size: 18px;
}
.table-wrap {
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th, td {
  border-bottom: 1px solid #e6ebf2;
  padding: 10px 8px;
  text-align: left;
  vertical-align: top;
  font-size: 13px;
}
th {
  color: #5d6b82;
  font-size: 12px;
  text-transform: uppercase;
}
a {
  color: #0f766e;
  font-weight: 700;
  text-decoration: none;
}
.link {
  background: #ecfeff;
  color: #0f766e;
}
.details {
  display: grid;
  grid-template-columns: 170px minmax(0, 1fr);
  gap: 10px 14px;
  margin: 0;
}
.details dt {
  color: #5d6b82;
  font-weight: 700;
}
.details dd {
  margin: 0;
}
.empty {
  color: #5d6b82;
  margin: 0;
}
@media (max-width: 860px) {
  .shell {
    width: min(100vw - 24px, 1180px);
    padding-top: 16px;
  }
  .hero {
    display: block;
  }
  .status {
    justify-content: flex-start;
    margin-top: 16px;
  }
  .cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .details {
    grid-template-columns: 1fr;
  }
}
""".strip()


__all__ = [
    "WebCatalogPage",
    "WebConsoleSnapshot",
    "build_web_console_snapshot",
    "render_web_catalog",
    "render_web_console",
    "render_web_doctor",
    "render_web_domain_detail",
    "render_web_evidence_explorer",
    "render_web_profile_catalog",
    "render_web_session_detail",
    "render_web_sessions",
    "render_web_settings",
    "render_web_world_model_explorer",
]
