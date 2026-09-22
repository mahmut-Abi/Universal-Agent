"""Observability domain CLI contribution.

Registers the `prometheus` init backend so `agent init --domain-backend
prometheus` creates an observability profile (UA-LIVE-2026-09-21 R6-1).
VictoriaMetrics exposes the same `/api/v1/query*` surface, so any
Prometheus-compatible query API works.
"""

from __future__ import annotations

import argparse
from typing import cast

from universal_agent.host_contracts import (
    CliDomainContribution,
    InitDomainOutcome,
    add_secret,
    single_secret_source,
)

_OBSERVABILITY_INIT_BACKENDS = ("prometheus",)


def _add_init_arguments(group: argparse._ArgumentGroup) -> None:
    group.add_argument(
        "--observability-endpoint",
        help="Base URL of the Prometheus/VictoriaMetrics query API "
        "(e.g. https://victoria-metrics.example.com or "
        "http://vmselect.monitoring.svc:8481/select/0/prometheus).",
    )
    group.add_argument(
        "--observability-token-env",
        help="Environment variable holding a bearer token for the query API.",
    )
    group.add_argument(
        "--observability-token-secret",
        default="observability_api_token",
        help="Secret reference name for the bearer token.",
    )
    group.add_argument(
        "--observability-timeout-seconds",
        type=float,
        default=15.0,
        help="Per-query timeout in seconds.",
    )


def _resolve_init_domain(args: argparse.Namespace) -> InitDomainOutcome | None:
    backend = cast("str | None", getattr(args, "domain_backend", None))
    if backend not in _OBSERVABILITY_INIT_BACKENDS:
        return None

    from universal_agent.domains.observability.cli_runtime import profile_domain_config

    token_source = single_secret_source(
        "--observability-token",
        env_key=cast("str | None", getattr(args, "observability_token_env", None)),
        file_path=None,
    )
    token_secret = cast("str | None", getattr(args, "observability_token_secret", None))
    secrets: dict[str, dict[str, object]] = {}
    if token_source is not None and token_secret is not None:
        add_secret(secrets, token_secret, token_source)
    return InitDomainOutcome(
        domain_name="observability",
        domain_config=profile_domain_config(
            domain_backend=backend,
            endpoint=cast("str | None", getattr(args, "observability_endpoint", None)),
            bearer_token_secret=token_secret if token_source is not None else None,
            timeout_seconds=cast(float, getattr(args, "observability_timeout_seconds", 15.0)),
        ),
        secrets=secrets,
    )


def observability_cli_contribution() -> CliDomainContribution:
    return CliDomainContribution(
        domain="observability",
        init_backends=_OBSERVABILITY_INIT_BACKENDS,
        init_add_arguments=_add_init_arguments,
        init_resolve_domain=_resolve_init_domain,
    )


__all__ = ["observability_cli_contribution"]
