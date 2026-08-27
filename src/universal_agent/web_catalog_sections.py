from __future__ import annotations

from universal_agent.service import (
    CapabilityView,
    DomainPackageView,
    DomainView,
    EvaluatorView,
    MemoryView,
    PolicyView,
    ProfileView,
    ToolView,
)
from universal_agent.web_helpers import (
    _domain_package_dependencies,
    _enum_tuple_text,
    _profile_domain_text,
    _secret_status_text,
    _string_tuple_text,
    _value_text,
)
from universal_agent.web_types import WebConsoleSnapshot
from universal_agent.web_ui import (
    _detail_list,
    _empty_table_row,
    _link,
    _raw_table_cell,
    _section,
    _table,
    _table_row,
)


def _domains(snapshot: WebConsoleSnapshot) -> str:
    rows = [
        _table_row(
            (
                _raw_table_cell(
                    _link(
                        f"{domain.name}@{domain.version}",
                        f"/console/domains/{domain.name}/{domain.version}",
                    )
                ),
                "yes" if domain.primary else "no",
                len(domain.capability_names),
                len(domain.evaluator_names),
            )
        )
        for domain in snapshot.domains
    ]
    if not rows:
        rows.append(_empty_table_row("No active domains", colspan=4))
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
    return _section("Domain", _detail_list(items))


def _configured_domains(snapshot: WebConsoleSnapshot) -> str:
    rows = [
        _table_row(
            (
                domain.name,
                domain.version,
                "yes" if domain.primary else "no",
                domain.backend or "default",
                _value_text(domain.settings) if domain.settings else "none",
            )
        )
        for domain in snapshot.config.domains
    ]
    if not rows:
        rows.append(_empty_table_row("No configured domains", colspan=5))
    return _section(
        "Configured Domains",
        _table(("Domain", "Version", "Primary", "Backend", "Settings"), tuple(rows)),
    )


def _runtime_secrets(snapshot: WebConsoleSnapshot) -> str:
    rows = [
        _table_row(
            (
                secret.name,
                secret.source,
                secret.key,
                "yes" if secret.required else "no",
                _secret_status_text(secret.available, secret.status),
            )
        )
        for secret in snapshot.config.secrets
    ]
    if not rows:
        rows.append(_empty_table_row("No runtime secrets", colspan=5))
    return _section(
        "Runtime Secrets",
        _table(("Name", "Source", "Key", "Required", "Status"), tuple(rows)),
    )


def _environment(snapshot: WebConsoleSnapshot) -> str:
    rows = [
        _table_row((key, _value_text(value)))
        for key, value in sorted(snapshot.config.environment.items())
    ]
    if not rows:
        rows.append(_empty_table_row("No environment settings", colspan=2))
    return _section("Environment", _table(("Key", "Value"), tuple(rows)))


def _profiles(profiles: tuple[ProfileView, ...]) -> str:
    rows = [
        _table_row(
            (
                profile.name,
                profile.version,
                f"{profile.domain_name}@{profile.domain_version}",
                _profile_domain_text(profile),
                profile.description,
            )
        )
        for profile in profiles
    ]
    if not rows:
        rows.append(_empty_table_row("No profiles", colspan=5))
    return _section(
        "Profile Catalog",
        _table(("Profile", "Version", "Primary Domain", "Domains", "Description"), tuple(rows)),
    )


def _domain_packages(packages: tuple[DomainPackageView, ...]) -> str:
    rows = [
        _table_row(
            (
                _raw_table_cell(
                    _link(
                        f"{package.name}@{package.version}",
                        f"/console/domain-packages/{package.name}/{package.version}",
                    )
                ),
                package.entrypoint or "none",
                ", ".join(package.capability_names) or "none",
                _domain_package_dependencies(package),
                ", ".join(package.required_tools) or "none",
                ", ".join(package.resource_names) or "none",
                _value_text(package.security),
                package.manifest_path,
            )
        )
        for package in packages
    ]
    if not rows:
        rows.append(_empty_table_row("No domain packages", colspan=8))
    return _section(
        "Domain Package Catalog",
        _table(
            (
                "Package",
                "Entrypoint",
                "Capabilities",
                "Dependencies",
                "Required Tools",
                "Resources",
                "Security",
                "Manifest",
            ),
            tuple(rows),
        ),
    )


def _capabilities(capabilities: tuple[CapabilityView, ...]) -> str:
    rows = [
        _table_row(
            (
                capability.name,
                capability.category.value,
                capability.risk.value,
                f"{capability.domain_name}@{capability.domain_version}",
                ", ".join(capability.tool_names),
                capability.description,
            )
        )
        for capability in capabilities
    ]
    if not rows:
        rows.append(_empty_table_row("No capabilities", colspan=6))
    return _section(
        "Capability Catalog",
        _table(("Capability", "Category", "Risk", "Domain", "Tools", "Description"), tuple(rows)),
    )


def _tools(tools: tuple[ToolView, ...]) -> str:
    rows = [
        _table_row(
            (
                tool.name,
                tool.side_effect.value,
                tool.risk.value,
                ", ".join(tool.capabilities),
                ", ".join(tool.required_arguments),
                f"{tool.timeout_seconds:g}s",
                f"{tool.domain_name}@{tool.domain_version}",
            )
        )
        for tool in tools
    ]
    if not rows:
        rows.append(_empty_table_row("No tools", colspan=7))
    return _section(
        "Tool Catalog",
        _table(
            ("Tool", "Side Effect", "Risk", "Capabilities", "Required Args", "Timeout", "Domain"),
            tuple(rows),
        ),
    )


def _policies(policies: tuple[PolicyView, ...]) -> str:
    rows = [
        _table_row(
            (
                policy.name,
                policy.policy_type,
                "n/a" if policy.effect is None else policy.effect.value,
                _enum_tuple_text(policy.categories),
                _enum_tuple_text(policy.risks),
                ", ".join(policy.capability_names),
                f"{policy.domain_name}@{policy.domain_version}",
                policy.description,
            )
        )
        for policy in policies
    ]
    if not rows:
        rows.append(_empty_table_row("No policies", colspan=8))
    return _section(
        "Policy Catalog",
        _table(
            ("Policy", "Type", "Effect", "Categories", "Risks", "Capabilities", "Domain", "Reason"),
            tuple(rows),
        ),
    )


def _evaluators(evaluators: tuple[EvaluatorView, ...]) -> str:
    rows = [
        _table_row(
            (
                evaluator.name,
                evaluator.evaluator_type,
                f"{evaluator.domain_name}@{evaluator.domain_version}",
            )
        )
        for evaluator in evaluators
    ]
    if not rows:
        rows.append(_empty_table_row("No evaluators", colspan=3))
    return _section(
        "Evaluator Catalog",
        _table(("Evaluator", "Type", "Domain"), tuple(rows)),
    )


def _memory(memories: tuple[MemoryView, ...]) -> str:
    rows = [
        _table_row(
            (
                memory.memory_id,
                memory.kind.value,
                memory.subject,
                memory.scope or "global",
                f"{memory.confidence:.2f}",
                memory.source_session_id or "none",
                memory.content,
            )
        )
        for memory in memories
    ]
    if not rows:
        rows.append(_empty_table_row("No memory", colspan=7))
    return _section(
        "Memory Catalog",
        _table(
            ("Memory", "Kind", "Subject", "Scope", "Confidence", "Source Session", "Content"),
            tuple(rows),
        ),
    )


def _domain_package_details(package: DomainPackageView | None) -> str:
    if package is None:
        return _section("Domain Package", '<p class="empty">No selected domain package</p>')
    items = (
        ("Package", f"{package.name}@{package.version}"),
        ("Description", package.description),
        ("Author", package.author or "none"),
        ("Entrypoint", package.entrypoint or "none"),
        ("Tags", _string_tuple_text(package.tags)),
        ("Ontology", _string_tuple_text(package.ontology)),
        ("Capabilities", _string_tuple_text(package.capability_names)),
        ("Tools", _string_tuple_text(package.tool_names)),
        ("Policies", _string_tuple_text(package.policy_names)),
        ("Procedures", _string_tuple_text(package.procedure_names)),
        ("Knowledge", _string_tuple_text(package.knowledge_names)),
        ("Evaluators", _string_tuple_text(package.evaluator_names)),
        ("Context Providers", _string_tuple_text(package.context_provider_names)),
        ("Prompts", _string_tuple_text(package.prompt_names)),
        ("Dependencies", _domain_package_dependencies(package)),
        ("Required Tools", _string_tuple_text(package.required_tools)),
        ("Runtime API Compatibility", package.runtime_api_compatibility or "none"),
        ("Domain API Compatibility", package.domain_api_compatibility or "none"),
        ("Root Path", package.root_path),
        ("Manifest Path", package.manifest_path),
    )
    return _section("Domain Package", _detail_list(items))


def _domain_package_resources(package: DomainPackageView | None) -> str:
    rows = []
    if package is not None:
        rows = [_table_row((resource,)) for resource in package.resource_names]
    if not rows:
        rows.append(_empty_table_row("No package resources", colspan=1))
    return _section("Package Resources", _table(("Resource",), tuple(rows)))


def _domain_package_security(package: DomainPackageView | None) -> str:
    if package is None:
        return _section("Package Security", '<p class="empty">No package security metadata</p>')
    items = (
        ("Security Metadata", _value_text(package.security)),
        ("Required Tools", _string_tuple_text(package.required_tools)),
        ("Runtime API Compatibility", package.runtime_api_compatibility or "none"),
        ("Domain API Compatibility", package.domain_api_compatibility or "none"),
    )
    return _section("Package Security", _detail_list(items))


def _domain_package_active_domains(
    snapshot: WebConsoleSnapshot,
    package: DomainPackageView | None,
) -> str:
    if package is None:
        return _section("Matching Active Domains", '<p class="empty">No selected package</p>')
    matches = tuple(
        domain
        for domain in snapshot.domains
        if domain.name == package.name and domain.version == package.version
    )
    return _section("Matching Active Domains", _domain_rows(matches))


def _domain_package_profiles(
    snapshot: WebConsoleSnapshot,
    package: DomainPackageView | None,
) -> str:
    if package is None:
        return _section("Matching Profiles", '<p class="empty">No selected package</p>')
    matches = tuple(
        profile
        for profile in snapshot.profiles
        if any(
            identity.name == package.name and identity.version == package.version
            for identity in profile.domains
        )
    )
    rows = [
        _table_row(
            (
                profile.name,
                profile.version,
                f"{profile.domain_name}@{profile.domain_version}",
                _profile_domain_text(profile),
                profile.description,
            )
        )
        for profile in matches
    ]
    if not rows:
        rows.append(_empty_table_row("No matching profiles", colspan=5))
    return _section(
        "Matching Profiles",
        _table(("Profile", "Version", "Primary Domain", "Domains", "Description"), tuple(rows)),
    )


def _domain_rows(domains: tuple[DomainView, ...]) -> str:
    rows = [
        _table_row(
            (
                _raw_table_cell(
                    _link(
                        f"{domain.name}@{domain.version}",
                        f"/console/domains/{domain.name}/{domain.version}",
                    )
                ),
                "yes" if domain.primary else "no",
                len(domain.capability_names),
                len(domain.evaluator_names),
            )
        )
        for domain in domains
    ]
    if not rows:
        rows.append(_empty_table_row("No matching active domains", colspan=4))
    return _table(("Domain", "Primary", "Capabilities", "Evaluators"), tuple(rows))
