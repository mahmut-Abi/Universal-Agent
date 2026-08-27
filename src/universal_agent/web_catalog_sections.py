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
from universal_agent.web_ui import _attr, _html, _section, _table


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

def _configured_domains(snapshot: WebConsoleSnapshot) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(domain.name)}</td>",
                f"<td>{_html(domain.version)}</td>",
                f"<td>{'yes' if domain.primary else 'no'}</td>",
                f"<td>{_html(domain.backend or 'default')}</td>",
                f"<td>{_html(_value_text(domain.settings) if domain.settings else 'none')}</td>",
                "</tr>",
            )
        )
        for domain in snapshot.config.domains
    ]
    if not rows:
        rows.append('<tr><td colspan="5">No configured domains</td></tr>')
    return _section(
        "Configured Domains",
        _table(("Domain", "Version", "Primary", "Backend", "Settings"), tuple(rows)),
    )

def _runtime_secrets(snapshot: WebConsoleSnapshot) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(secret.name)}</td>",
                f"<td>{_html(secret.source)}</td>",
                f"<td>{_html(secret.key)}</td>",
                f"<td>{'yes' if secret.required else 'no'}</td>",
                f"<td>{_html(_secret_status_text(secret.available, secret.status))}</td>",
                "</tr>",
            )
        )
        for secret in snapshot.config.secrets
    ]
    if not rows:
        rows.append('<tr><td colspan="5">No runtime secrets</td></tr>')
    return _section(
        "Runtime Secrets",
        _table(("Name", "Source", "Key", "Required", "Status"), tuple(rows)),
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

def _domain_packages(packages: tuple[DomainPackageView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                (
                    '<td><a href="/console/domain-packages/'
                    f'{_attr(package.name)}/{_attr(package.version)}">'
                    f"{_html(package.name)}@{_html(package.version)}</a></td>"
                ),
                f"<td>{_html(package.entrypoint or 'none')}</td>",
                f"<td>{_html(', '.join(package.capability_names) or 'none')}</td>",
                f"<td>{_html(_domain_package_dependencies(package))}</td>",
                f"<td>{_html(', '.join(package.required_tools) or 'none')}</td>",
                f"<td>{_html(', '.join(package.resource_names) or 'none')}</td>",
                f"<td>{_html(_value_text(package.security))}</td>",
                f"<td>{_html(package.manifest_path)}</td>",
                "</tr>",
            )
        )
        for package in packages
    ]
    if not rows:
        rows.append('<tr><td colspan="8">No domain packages</td></tr>')
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
    return _section(
        "Domain Package",
        '<dl class="details">'
        + "".join(f"<dt>{_html(label)}</dt><dd>{_html(value)}</dd>" for label, value in items)
        + "</dl>",
    )

def _domain_package_resources(package: DomainPackageView | None) -> str:
    rows = []
    if package is not None:
        rows = [
            "\n".join(("<tr>", f"<td>{_html(resource)}</td>", "</tr>"))
            for resource in package.resource_names
        ]
    if not rows:
        rows.append("<tr><td>No package resources</td></tr>")
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
    return _section(
        "Package Security",
        '<dl class="details">'
        + "".join(f"<dt>{_html(label)}</dt><dd>{_html(value)}</dd>" for label, value in items)
        + "</dl>",
    )

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
        for profile in matches
    ]
    if not rows:
        rows.append('<tr><td colspan="5">No matching profiles</td></tr>')
    return _section(
        "Matching Profiles",
        _table(("Profile", "Version", "Primary Domain", "Domains", "Description"), tuple(rows)),
    )

def _domain_rows(domains: tuple[DomainView, ...]) -> str:
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
        for domain in domains
    ]
    if not rows:
        rows.append('<tr><td colspan="4">No matching active domains</td></tr>')
    return _table(("Domain", "Primary", "Capabilities", "Evaluators"), tuple(rows))

