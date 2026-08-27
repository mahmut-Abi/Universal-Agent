from __future__ import annotations

from universal_agent.distributed import DistributedHealthReport
from universal_agent.operations import DoctorReportView
from universal_agent.runtime import SessionView
from universal_agent.service import DomainPackageView, DomainView
from universal_agent.web_catalog import _catalog_title
from universal_agent.web_helpers import _distributed_status, _ready_text
from universal_agent.web_types import WebCatalogPage, WebConsoleSnapshot
from universal_agent.web_ui import _attr, _html, _status_class


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
            '<a class="pill link" href="/console/domain-packages">Packages</a>',
            '<a class="pill link" href="/console/profiles">Profiles</a>',
            '<a class="pill link" href="/console/multi-agent">Multi-Agent</a>',
            '<a class="pill link" href="/console/doctor">Doctor</a>',
            '<a class="pill link" href="/console/distributed">Distributed</a>',
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
            '<a class="pill link" href="/console/distributed">Distributed</a>',
            '<a class="pill link" href="/console/multi-agent">Multi-Agent</a>',
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
            '<a class="pill link" href="/console/distributed">Distributed</a>',
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
            '<a class="pill link" href="/console/distributed">Distributed</a>',
            '<a class="pill link" href="/console/evaluations">Evaluations</a>',
            '<a class="pill link" href="/console/settings">Settings</a>',
            f'<span class="pill ok">Health: {_html(snapshot.health.status)}</span>',
            f'<span class="pill {ready_class}">Ready: {_ready_text(snapshot)}</span>',
            "</div>",
            "</section>",
        )
    )

def _domain_package_hero(
    snapshot: WebConsoleSnapshot,
    package: DomainPackageView | None,
) -> str:
    ready_class = "ok" if snapshot.ready.ready else "warn"
    package_text = "No selected package"
    if package is not None:
        package_text = f"{package.name}@{package.version}"
    return "\n".join(
        (
            '<section class="hero">',
            "<div>",
            "<p>Universal Agent Runtime</p>",
            "<h1>Domain Package</h1>",
            f"<span>package={_html(package_text)}</span>",
            "</div>",
            '<div class="status">',
            '<a class="pill link" href="/console">Console</a>',
            '<a class="pill link" href="/console/domain-packages">Packages</a>',
            '<a class="pill link" href="/console/domains">Domains</a>',
            '<a class="pill link" href="/console/profiles">Profiles</a>',
            '<a class="pill link" href="/console/doctor">Doctor</a>',
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
            '<a class="pill link" href="/console/distributed">Distributed</a>',
            '<a class="pill link" href="/console/evaluations">Evaluations</a>',
            '<a class="pill link" href="/console/settings">Settings</a>',
            f'<span class="pill ok">Health: {_html(snapshot.health.status)}</span>',
            f'<span class="pill {ready_class}">Ready: {_ready_text(snapshot)}</span>',
            "</div>",
            "</section>",
        )
    )

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
            '<a class="pill link" href="/console/distributed">Distributed</a>',
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
            '<a class="pill link" href="/console/distributed">Distributed</a>',
            '<a class="pill link" href="/console/multi-agent">Multi-Agent</a>',
            '<a class="pill link" href="/console/settings">Settings</a>',
            '<a class="pill link" href="/console/evaluations">Evaluations</a>',
            f'<span class="pill {doctor_class}">Doctor: {_html(doctor.status)}</span>',
            f'<span class="pill ok">Health: {_html(snapshot.health.status)}</span>',
            f'<span class="pill {ready_class}">Ready: {_ready_text(snapshot)}</span>',
            "</div>",
            "</section>",
        )
    )

def _distributed_hero(
    snapshot: WebConsoleSnapshot,
    health: DistributedHealthReport | None,
) -> str:
    ready_class = "ok" if snapshot.ready.ready else "warn"
    distributed_status = _distributed_status(health)
    return "\n".join(
        (
            '<section class="hero">',
            "<div>",
            "<p>Universal Agent Runtime</p>",
            "<h1>Distributed Runtime</h1>",
            f"<span>status={_html(distributed_status)}</span>",
            "</div>",
            '<div class="status">',
            '<a class="pill link" href="/console">Console</a>',
            '<a class="pill link" href="/console/sessions">Sessions</a>',
            '<a class="pill link" href="/console/doctor">Doctor</a>',
            '<a class="pill link" href="/console/settings">Settings</a>',
            f'<span class="pill {_status_class(distributed_status)}">'
            f"Distributed: {_html(distributed_status)}</span>",
            f'<span class="pill ok">Health: {_html(snapshot.health.status)}</span>',
            f'<span class="pill {ready_class}">Ready: {_ready_text(snapshot)}</span>',
            "</div>",
            "</section>",
        )
    )

def _multi_agent_hero(snapshot: WebConsoleSnapshot) -> str:
    ready_class = "ok" if snapshot.ready.ready else "warn"
    status = "enabled" if snapshot.multi_agent.enabled else "not configured"
    return "\n".join(
        (
            '<section class="hero">',
            "<div>",
            "<p>Universal Agent Runtime</p>",
            "<h1>Multi-Agent</h1>",
            f"<span>status={_html(status)}</span>",
            "</div>",
            '<div class="status">',
            '<a class="pill link" href="/console">Console</a>',
            '<a class="pill link" href="/console/sessions">Sessions</a>',
            '<a class="pill link" href="/console/distributed">Distributed</a>',
            '<a class="pill link" href="/console/doctor">Doctor</a>',
            '<a class="pill link" href="/console/settings">Settings</a>',
            f'<span class="pill {_status_class(status)}">Multi-Agent: {_html(status)}</span>',
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
        '<a class="pill link" href="/console/distributed">Distributed</a>',
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

