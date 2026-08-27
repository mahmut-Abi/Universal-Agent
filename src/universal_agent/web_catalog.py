from __future__ import annotations

from universal_agent.web_catalog_sections import (
    _capabilities,
    _configured_domains,
    _domain_packages,
    _domains,
    _evaluators,
    _memory,
    _policies,
    _profiles,
    _tools,
)
from universal_agent.web_helpers import (
    _domain_package_dependency_count,
    _domain_package_required_tool_count,
    _domain_package_resource_count,
    _global_memory_count,
    _policy_effect_count,
    _ready_text,
    _risk_count,
    _scoped_memory_count,
    _side_effect_count,
)
from universal_agent.web_session_sections import _sessions
from universal_agent.web_types import WebCatalogPage, WebConsoleSnapshot
from universal_agent.web_ui import _metric_card


def _catalog_title(catalog: WebCatalogPage) -> str:
    titles = {
        WebCatalogPage.DOMAINS: "Domain Catalog",
        WebCatalogPage.DOMAIN_PACKAGES: "Domain Package Catalog",
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
            _metric_card("Packages", len(snapshot.domain_packages)),
            _metric_card("Profiles", len(snapshot.profiles)),
            _metric_card("Capabilities", len(snapshot.capabilities)),
        )
    if catalog is WebCatalogPage.DOMAIN_PACKAGES:
        return (
            _metric_card("Packages", len(snapshot.domain_packages)),
            _metric_card("Dependencies", _domain_package_dependency_count(snapshot)),
            _metric_card("Required Tools", _domain_package_required_tool_count(snapshot)),
            _metric_card("Resources", _domain_package_resource_count(snapshot)),
            _metric_card("Profiles", len(snapshot.profiles)),
            _metric_card("Active Domains", len(snapshot.domains)),
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
    if catalog is WebCatalogPage.DOMAIN_PACKAGES:
        return (
            _domain_packages(snapshot.domain_packages),
            _domains(snapshot),
            _profiles(snapshot.profiles),
        )
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

