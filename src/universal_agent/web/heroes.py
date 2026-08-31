from __future__ import annotations

from universal_agent.distributed import DistributedHealthReport
from universal_agent.operations import DoctorReportView
from universal_agent.runtime import SessionView
from universal_agent.service import DomainPackageView, DomainView
from universal_agent.web.catalog import _catalog_title
from universal_agent.web.helpers import _distributed_status, _ready_text
from universal_agent.web.types import WebCatalogPage, WebConsoleSnapshot
from universal_agent.web.ui import _hero_block, _HeroLink, _HeroPill, _status_class


def _hero(snapshot: WebConsoleSnapshot) -> str:
    return _hero_block(
        "Runtime Console",
        detail=(
            f"store={snapshot.config.store_backend} "
            f"queue={snapshot.config.distributed_queue_backend} "
            f"locks={snapshot.config.distributed_locks_backend} "
            f"workers={snapshot.config.distributed_workers_backend} "
            f"max_iterations={snapshot.config.max_iterations} "
            f"max_recovery_steps={snapshot.config.max_recovery_steps}"
        ),
        links=_nav(
            ("Sessions", "/console/sessions"),
            ("Packages", "/console/domain-packages"),
            ("Profiles", "/console/profiles"),
            ("Multi-Agent", "/console/multi-agent"),
            ("Doctor", "/console/doctor"),
            ("Distributed", "/console/distributed"),
            ("Evaluations", "/console/evaluations"),
            ("Settings", "/console/settings"),
        ),
        pills=_runtime_pills(snapshot),
    )


def _settings_hero(snapshot: WebConsoleSnapshot) -> str:
    return _hero_block(
        "Settings",
        detail=(
            f"store={snapshot.config.store_backend} "
            f"path={snapshot.config.store_path or 'memory'} "
            f"queue={snapshot.config.distributed_queue_backend} "
            f"locks={snapshot.config.distributed_locks_backend} "
            f"workers={snapshot.config.distributed_workers_backend}"
        ),
        links=_nav(
            ("Console", "/console"),
            ("Sessions", "/console/sessions"),
            ("Profiles", "/console/profiles"),
            ("Doctor", "/console/doctor"),
            ("Distributed", "/console/distributed"),
            ("Multi-Agent", "/console/multi-agent"),
            ("Evaluations", "/console/evaluations"),
        ),
        pills=_runtime_pills(snapshot),
    )


def _domain_hero(snapshot: WebConsoleSnapshot, domain: DomainView | None) -> str:
    domain_text = "No selected domain"
    if domain is not None:
        domain_text = f"{domain.name}@{domain.version}"
    return _hero_block(
        "Domain Manager",
        detail=f"domain={domain_text}",
        links=_nav(
            ("Console", "/console"),
            ("Sessions", "/console/sessions"),
            ("Profiles", "/console/profiles"),
            ("Doctor", "/console/doctor"),
            ("Distributed", "/console/distributed"),
            ("Evaluations", "/console/evaluations"),
        ),
        pills=_runtime_pills(snapshot),
    )


def _profile_hero(snapshot: WebConsoleSnapshot) -> str:
    return _hero_block(
        "Profile Catalog",
        detail=(
            f"profiles={len(snapshot.profiles)} "
            f"domains={len(snapshot.domains)} "
            f"store={snapshot.config.store_backend}"
        ),
        links=_nav(
            ("Console", "/console"),
            ("Sessions", "/console/sessions"),
            ("Doctor", "/console/doctor"),
            ("Distributed", "/console/distributed"),
            ("Evaluations", "/console/evaluations"),
            ("Settings", "/console/settings"),
        ),
        pills=_runtime_pills(snapshot),
    )


def _domain_package_hero(
    snapshot: WebConsoleSnapshot,
    package: DomainPackageView | None,
) -> str:
    package_text = "No selected package"
    if package is not None:
        package_text = f"{package.name}@{package.version}"
    return _hero_block(
        "Domain Package",
        detail=f"package={package_text}",
        links=_nav(
            ("Console", "/console"),
            ("Packages", "/console/domain-packages"),
            ("Domains", "/console/domains"),
            ("Profiles", "/console/profiles"),
            ("Doctor", "/console/doctor"),
            ("Settings", "/console/settings"),
        ),
        pills=_runtime_pills(snapshot),
    )


def _catalog_hero(snapshot: WebConsoleSnapshot, catalog: WebCatalogPage) -> str:
    return _hero_block(
        _catalog_title(catalog),
        detail=(
            f"catalog={catalog.value} "
            f"domains={len(snapshot.domains)} "
            f"store={snapshot.config.store_backend}"
        ),
        links=_nav(
            ("Console", "/console"),
            ("Sessions", "/console/sessions"),
            ("Profiles", "/console/profiles"),
            ("Doctor", "/console/doctor"),
            ("Distributed", "/console/distributed"),
            ("Evaluations", "/console/evaluations"),
            ("Settings", "/console/settings"),
        ),
        pills=_runtime_pills(snapshot),
    )


def _sessions_hero(snapshot: WebConsoleSnapshot) -> str:
    return _hero_block(
        "Sessions",
        detail=(
            f"sessions={snapshot.metrics.session_count} "
            f"active={snapshot.metrics.active_session_count} "
            f"waiting={snapshot.metrics.waiting_session_count}"
        ),
        links=_nav(
            ("Console", "/console"),
            ("Profiles", "/console/profiles"),
            ("Doctor", "/console/doctor"),
            ("Distributed", "/console/distributed"),
            ("Evaluations", "/console/evaluations"),
            ("Settings", "/console/settings"),
        ),
        pills=_runtime_pills(snapshot),
    )


def _doctor_hero(snapshot: WebConsoleSnapshot, doctor: DoctorReportView) -> str:
    return _hero_block(
        "Runtime Doctor",
        detail=f"status={doctor.status} checks={len(doctor.checks)}",
        links=_nav(
            ("Console", "/console"),
            ("Sessions", "/console/sessions"),
            ("Distributed", "/console/distributed"),
            ("Multi-Agent", "/console/multi-agent"),
            ("Settings", "/console/settings"),
            ("Evaluations", "/console/evaluations"),
        ),
        pills=(
            _HeroPill("Doctor", doctor.status, _status_class(doctor.status)),
            *_runtime_pills(snapshot),
        ),
    )


def _distributed_hero(
    snapshot: WebConsoleSnapshot,
    health: DistributedHealthReport | None,
) -> str:
    distributed_status = _distributed_status(health)
    return _hero_block(
        "Distributed Runtime",
        detail=f"status={distributed_status}",
        links=_nav(
            ("Console", "/console"),
            ("Sessions", "/console/sessions"),
            ("Doctor", "/console/doctor"),
            ("Settings", "/console/settings"),
        ),
        pills=(
            _HeroPill(
                "Distributed",
                distributed_status,
                _status_class(distributed_status),
            ),
            *_runtime_pills(snapshot),
        ),
    )


def _multi_agent_hero(snapshot: WebConsoleSnapshot) -> str:
    status = "enabled" if snapshot.multi_agent.enabled else "not configured"
    return _hero_block(
        "Multi-Agent",
        detail=f"status={status}",
        links=_nav(
            ("Console", "/console"),
            ("Sessions", "/console/sessions"),
            ("Distributed", "/console/distributed"),
            ("Doctor", "/console/doctor"),
            ("Settings", "/console/settings"),
        ),
        pills=(
            _HeroPill("Multi-Agent", status, _status_class(status)),
            *_runtime_pills(snapshot),
        ),
    )


def _session_scoped_hero(snapshot: WebConsoleSnapshot, title: str) -> str:
    selected = snapshot.selected_session
    session_text = "No selected session"
    goal_text = "No selected goal"
    if selected is not None:
        session_text = str(selected.session_id)
        goal_text = selected.goal_description
    return _hero_block(
        title,
        detail=f"session={session_text} goal={goal_text}",
        links=_session_nav(snapshot.selected_session),
        pills=_runtime_pills(snapshot),
    )


def _runtime_pills(snapshot: WebConsoleSnapshot) -> tuple[_HeroPill, ...]:
    ready_class = "ok" if snapshot.ready.ready else "warn"
    return (
        _HeroPill("Health", snapshot.health.status),
        _HeroPill("Ready", _ready_text(snapshot), ready_class),
    )


def _nav(*items: tuple[str, str]) -> tuple[_HeroLink, ...]:
    return tuple(_HeroLink(label, href) for label, href in items)


def _session_nav(session: SessionView | None) -> tuple[_HeroLink, ...]:
    links = [
        _HeroLink("Console", "/console"),
        _HeroLink("Sessions", "/console/sessions"),
        _HeroLink("Doctor", "/console/doctor"),
        _HeroLink("Distributed", "/console/distributed"),
    ]
    if session is not None:
        session_id = str(session.session_id)
        links.extend(
            (
                _HeroLink("Detail", f"/console/sessions/{session_id}"),
                _HeroLink("Evidence", f"/console/sessions/{session_id}/evidence"),
                _HeroLink("World", f"/console/sessions/{session_id}/world"),
            )
        )
    return tuple(links)
