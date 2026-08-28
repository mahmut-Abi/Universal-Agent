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
    _empty_paragraph,
    _link,
    _raw_table_cell,
    _section,
    _table_from_cells,
    _table_section,
)


def _domains(snapshot: WebConsoleSnapshot) -> str:
    return _table_section(
        "Active Domains",
        ("Domain", "Primary", "Capabilities", "Evaluators"),
        (
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
            for domain in snapshot.domains
        ),
        empty_message="No active domains",
    )


def _domain_details(domain: DomainView | None) -> str:
    if domain is None:
        return _section("Domain", _empty_paragraph("No selected domain"))
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
    return _table_section(
        "Configured Domains",
        ("Domain", "Version", "Primary", "Backend", "Settings"),
        (
            (
                domain.name,
                domain.version,
                "yes" if domain.primary else "no",
                domain.backend or "default",
                _value_text(domain.settings) if domain.settings else "none",
            )
            for domain in snapshot.config.domains
        ),
        empty_message="No configured domains",
    )


def _runtime_secrets(snapshot: WebConsoleSnapshot) -> str:
    return _table_section(
        "Runtime Secrets",
        ("Name", "Source", "Key", "Required", "Status"),
        (
            (
                secret.name,
                secret.source,
                secret.key,
                "yes" if secret.required else "no",
                _secret_status_text(secret.available, secret.status),
            )
            for secret in snapshot.config.secrets
        ),
        empty_message="No runtime secrets",
    )


def _environment(snapshot: WebConsoleSnapshot) -> str:
    return _table_section(
        "Environment",
        ("Key", "Value"),
        ((key, _value_text(value)) for key, value in sorted(snapshot.config.environment.items())),
        empty_message="No environment settings",
    )


def _profiles(profiles: tuple[ProfileView, ...]) -> str:
    return _table_section(
        "Profile Catalog",
        ("Profile", "Version", "Primary Domain", "Domains", "Description"),
        (
            (
                profile.name,
                profile.version,
                f"{profile.domain_name}@{profile.domain_version}",
                _profile_domain_text(profile),
                profile.description,
            )
            for profile in profiles
        ),
        empty_message="No profiles",
    )


def _domain_packages(packages: tuple[DomainPackageView, ...]) -> str:
    return _table_section(
        "Domain Package Catalog",
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
        (
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
            for package in packages
        ),
        empty_message="No domain packages",
    )


def _capabilities(capabilities: tuple[CapabilityView, ...]) -> str:
    return _table_section(
        "Capability Catalog",
        ("Capability", "Category", "Risk", "Domain", "Tools", "Description"),
        (
            (
                capability.name,
                capability.category.value,
                capability.risk.value,
                f"{capability.domain_name}@{capability.domain_version}",
                ", ".join(capability.tool_names),
                capability.description,
            )
            for capability in capabilities
        ),
        empty_message="No capabilities",
    )


def _tools(tools: tuple[ToolView, ...]) -> str:
    return _table_section(
        "Tool Catalog",
        ("Tool", "Side Effect", "Risk", "Capabilities", "Required Args", "Timeout", "Domain"),
        (
            (
                tool.name,
                tool.side_effect.value,
                tool.risk.value,
                ", ".join(tool.capabilities),
                ", ".join(tool.required_arguments),
                f"{tool.timeout_seconds:g}s",
                f"{tool.domain_name}@{tool.domain_version}",
            )
            for tool in tools
        ),
        empty_message="No tools",
    )


def _policies(policies: tuple[PolicyView, ...]) -> str:
    return _table_section(
        "Policy Catalog",
        ("Policy", "Type", "Effect", "Categories", "Risks", "Capabilities", "Domain", "Reason"),
        (
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
            for policy in policies
        ),
        empty_message="No policies",
    )


def _evaluators(evaluators: tuple[EvaluatorView, ...]) -> str:
    return _table_section(
        "Evaluator Catalog",
        ("Evaluator", "Type", "Domain"),
        (
            (
                evaluator.name,
                evaluator.evaluator_type,
                f"{evaluator.domain_name}@{evaluator.domain_version}",
            )
            for evaluator in evaluators
        ),
        empty_message="No evaluators",
    )


def _memory(memories: tuple[MemoryView, ...]) -> str:
    return _table_section(
        "Memory Catalog",
        ("Memory", "Kind", "Subject", "Scope", "Confidence", "Source Session", "Content"),
        (
            (
                memory.memory_id,
                memory.kind.value,
                memory.subject,
                memory.scope or "global",
                f"{memory.confidence:.2f}",
                memory.source_session_id or "none",
                memory.content,
            )
            for memory in memories
        ),
        empty_message="No memory",
    )


def _domain_package_details(package: DomainPackageView | None) -> str:
    if package is None:
        return _section("Domain Package", _empty_paragraph("No selected domain package"))
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
    resources = () if package is None else package.resource_names
    return _table_section(
        "Package Resources",
        ("Resource",),
        ((resource,) for resource in resources),
        empty_message="No package resources",
    )


def _domain_package_security(package: DomainPackageView | None) -> str:
    if package is None:
        return _section("Package Security", _empty_paragraph("No package security metadata"))
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
        return _section("Matching Active Domains", _empty_paragraph("No selected package"))
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
        return _section("Matching Profiles", _empty_paragraph("No selected package"))
    matches = tuple(
        profile
        for profile in snapshot.profiles
        if any(
            identity.name == package.name and identity.version == package.version
            for identity in profile.domains
        )
    )
    return _table_section(
        "Matching Profiles",
        ("Profile", "Version", "Primary Domain", "Domains", "Description"),
        (
            (
                profile.name,
                profile.version,
                f"{profile.domain_name}@{profile.domain_version}",
                _profile_domain_text(profile),
                profile.description,
            )
            for profile in matches
        ),
        empty_message="No matching profiles",
    )


def _domain_rows(domains: tuple[DomainView, ...]) -> str:
    return _table_from_cells(
        ("Domain", "Primary", "Capabilities", "Evaluators"),
        (
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
            for domain in domains
        ),
        empty_message="No matching active domains",
    )
