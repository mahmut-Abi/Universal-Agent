"""`agent profile add-domain`: add a secondary domain to an existing profile.

Reads the profile config, resolves the new domain through the same
contribution-based init machinery as `agent init`, and merges the domain
config + secrets into both the top-level `domains` list and the runtime
`domains` list (UA-LIVE-2026-09-21 R6-1: multi-domain without hand-editing
two JSON lists).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TextIO, cast

from universal_agent_cli.contributions import load_cli_contributions


def _resolve_added_domain(
    args: argparse.Namespace,
) -> tuple[str, dict[str, object], dict[str, dict[str, object]]]:
    """Resolve the secondary domain via contributions (same two-pass order
    as `agent init`)."""

    contributions = [
        item for item in load_cli_contributions() if item.init_resolve_domain is not None
    ]
    requested_backend = cast("str | None", getattr(args, "domain_backend", None))
    claimed = [
        item
        for item in contributions
        if requested_backend is not None and requested_backend in item.init_backends
    ]
    for contribution in [*claimed, *(item for item in contributions if item not in claimed)]:
        resolve = contribution.init_resolve_domain
        if resolve is None:
            continue
        outcome = resolve(args)
        if outcome is not None:
            return (
                outcome.domain_name,
                outcome.domain_config,
                {name: dict(spec) for name, spec in outcome.secrets.items()},
            )
    return "local", {"name": "local", "version": "0.1.0"}, {}


def _merge_domain(
    payload: dict[str, object],
    domain_name: str,
    domain_config: dict[str, object],
    secrets: dict[str, dict[str, object]],
) -> bool:
    """Merge one domain into both domain lists + secrets. Returns False when
    the domain is already present."""

    domain_lists: list[list[dict[str, object]]] = []
    runtime_map = (
        cast("dict[str, object]", payload["runtime"])
        if isinstance(payload.get("runtime"), dict)
        else None
    )
    primary = payload.get("domain")
    runtime_primary = runtime_map.get("domain") if runtime_map is not None else None

    # Collect/normalize the two domain lists (top-level + runtime). A
    # single-domain profile keeps its primary domain in the singular
    # `domain` field — migrate it into the list so the secondary domain
    # does not silently replace it (R6-1).
    top_level_value = payload.get("domains")
    if isinstance(top_level_value, list):
        domain_lists.append(cast("list[dict[str, object]]", top_level_value))
    else:
        seeded_top: list[dict[str, object]] = []
        if isinstance(primary, dict) and primary.get("name"):
            seeded_top.append(primary)
        payload["domains"] = seeded_top
        domain_lists.append(seeded_top)
    if runtime_map is not None:
        runtime_value = runtime_map.get("domains")
        if isinstance(runtime_value, list):
            domain_lists.append(cast("list[dict[str, object]]", runtime_value))
        else:
            seeded_runtime: list[dict[str, object]] = []
            if isinstance(runtime_primary, dict) and runtime_primary.get("name"):
                seeded_runtime.append(runtime_primary)
            runtime_map["domains"] = seeded_runtime
            domain_lists.append(seeded_runtime)

    for domain_list in domain_lists:
        for existing in domain_list:
            if isinstance(existing, dict) and existing.get("name") == domain_name:
                return False
    for domain_list in domain_lists:
        domain_list.append(domain_config)

    runtime_secrets = (
        cast("dict[str, object]", runtime_map["secrets"])
        if runtime_map is not None and isinstance(runtime_map.get("secrets"), dict)
        else {}
    )
    if runtime_map is not None:
        runtime_map["secrets"] = runtime_secrets
    for name, spec in secrets.items():
        if name not in payload and (runtime_secrets is None or name not in runtime_secrets):
            runtime_secrets[name] = spec
    return True


def dispatch_profile_add_domain(args: argparse.Namespace, out: TextIO) -> None:
    profile_path = Path(cast(str, args.profile_config))
    if not profile_path.is_file():
        raise ValueError(f"profile config not found: {profile_path}")
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"profile config is not a JSON object: {profile_path}")

    domain_name, domain_config, secrets = _resolve_added_domain(args)
    if not _merge_domain(payload, domain_name, domain_config, secrets):
        raise ValueError(f"domain already present in profile: {domain_name}")

    profile_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    from universal_agent_cli.io import _write_json

    _write_json(
        out,
        {
            "status": "added",
            "domain": domain_name,
            "profile_config": str(profile_path),
        },
    )


def add_add_domain_arguments(parser: argparse.ArgumentParser) -> None:
    """Flags for `agent profile add-domain` (delegating to the same
    contribution-based resolution as `agent init`)."""

    parser.add_argument(
        "--domain-backend",
        required=True,
        help="Backend of the domain to add (e.g. prometheus, workspace).",
    )
    parser.add_argument(
        "--observability-endpoint",
        help="Base URL of the Prometheus/VictoriaMetrics query API.",
    )
    parser.add_argument("--observability-token-env")
    parser.add_argument(
        "--observability-token-secret",
        default="observability_api_token",
    )
    parser.add_argument(
        "--observability-timeout-seconds",
        type=float,
        default=15.0,
    )
