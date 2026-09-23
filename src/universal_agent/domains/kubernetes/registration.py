"""Kubernetes Domain contribution to the CLI surface (extension point).

Owns everything Kubernetes-specific about the CLI: the ``kubernetes``
operator command (parser + embedded dispatch + remote thin-client
forwarding), the ``agent init`` Kubernetes backend options, and the
probe/embedded-serve wiring.

The CLI shell discovers this module through the
``universal_agent.cli_contributions`` entry-point group; it never names a
concrete domain.
"""

from __future__ import annotations

import argparse
from typing import cast

from universal_agent.core import JsonValue
from universal_agent.host_contracts import (
    CliDomainContribution,
    CommandOutcome,
    InitDomainOutcome,
    RemoteAgentdClient,
    add_secret,
    single_secret_source,
)
from universal_agent.service import RuntimeService

_KUBERNETES_INIT_BACKENDS = ("kubectl", "kubernetes_api")

_KUBERNETES_REMOTE_OPERATIONS = {
    "preflight": "preflight",
    "model-probe": "model-probe",
    "check": "check",
    "run": "run",
    "evidence": "evidence",
}


def _add_init_arguments(group: argparse._ArgumentGroup) -> None:
    group.add_argument("--kubectl-namespace", default="default")
    group.add_argument("--kubectl-context")
    group.add_argument("--kubectl-kubeconfig")
    group.add_argument("--kubectl-timeout-seconds", type=float, default=10.0)
    group.add_argument("--kubernetes-api-server")
    group.add_argument("--kubernetes-api-namespace", default="default")
    group.add_argument("--kubernetes-api-token-env")
    group.add_argument("--kubernetes-api-token-file")
    group.add_argument("--kubernetes-api-token-secret", default="kubernetes_api_token")
    group.add_argument("--kubernetes-api-timeout-seconds", type=float, default=10.0)
    group.add_argument(
        "--kubernetes-api-ca-bundle",
        help="Path to a CA bundle for the API server TLS certificate.",
    )
    group.add_argument(
        "--kubernetes-api-insecure-skip-tls-verify",
        action="store_true",
        help="Skip API server TLS verification (testing only).",
    )


def _resolve_init_domain(args: argparse.Namespace) -> InitDomainOutcome | None:
    backend = cast("str | None", getattr(args, "domain_backend", None))
    if backend not in _KUBERNETES_INIT_BACKENDS:
        return None

    from universal_agent.domains.kubernetes.cli_runtime import profile_domain_config

    token_source = single_secret_source(
        "--kubernetes-api-token",
        env_key=cast("str | None", getattr(args, "kubernetes_api_token_env", None)),
        file_path=cast("str | None", getattr(args, "kubernetes_api_token_file", None)),
    )
    secrets: dict[str, dict[str, object]] = {}
    if token_source is not None:
        add_secret(
            secrets,
            cast(str, args.kubernetes_api_token_secret),
            token_source,
        )
    return InitDomainOutcome(
        domain_name="kubernetes",
        domain_config=profile_domain_config(
            domain_backend=backend,
            kubectl_namespace=cast(str, args.kubectl_namespace),
            kubectl_context=cast("str | None", args.kubectl_context),
            kubectl_kubeconfig=cast("str | None", args.kubectl_kubeconfig),
            kubectl_timeout_seconds=cast(float, args.kubectl_timeout_seconds),
            kubernetes_api_server=cast("str | None", args.kubernetes_api_server),
            kubernetes_api_namespace=cast(str, args.kubernetes_api_namespace),
            kubernetes_api_token_secret=cast(
                "str | None", getattr(args, "kubernetes_api_token_secret", None)
            ),
            kubernetes_api_timeout_seconds=cast(float, args.kubernetes_api_timeout_seconds),
            kubernetes_api_ca_bundle=cast(
                "str | None", getattr(args, "kubernetes_api_ca_bundle", None)
            ),
            kubernetes_api_insecure_skip_tls_verify=cast(
                bool, getattr(args, "kubernetes_api_insecure_skip_tls_verify", False)
            ),
        ),
        secrets=secrets,
    )


async def _dispatch(args: argparse.Namespace, service: RuntimeService) -> CommandOutcome:
    from universal_agent.domains.kubernetes.cli_reports import dispatch_kubernetes
    from universal_agent.host import build_configured_model_adapter

    result = await dispatch_kubernetes(
        args,
        service,
        model_adapter_builder=build_configured_model_adapter,
    )
    return CommandOutcome(result.payload, result.status)


async def _dispatch_remote(args: argparse.Namespace, client: RemoteAgentdClient) -> CommandOutcome:
    kubernetes_command = cast(str, args.kubernetes_command)
    operation = _KUBERNETES_REMOTE_OPERATIONS[kubernetes_command]
    body: dict[str, JsonValue] = {}
    workload = cast("str | None", getattr(args, "workload", None))
    if workload is not None:
        body["workload"] = workload
    # preflight has no profile positional; the operator commands do.
    profile = cast("str | None", getattr(args, "profile", None))
    if profile is not None:
        body["profile"] = profile
    # The profile config path is resolved on the agentd host (same machine for
    # the embedded runtime), so model/secret semantics match the local path.
    profile_config = cast("str | None", getattr(args, "profile_config", None))
    if profile_config is not None:
        body["profile_config"] = profile_config
    namespace = cast("str | None", args.namespace)
    if namespace is not None:
        body["namespace"] = namespace
    if kubernetes_command in {"preflight", "check", "run"}:
        body["skip_preflight"] = bool(getattr(args, "skip_preflight", False))
    if kubernetes_command in {"check", "run"}:
        body["skip_model_probe"] = bool(getattr(args, "skip_model_probe", False))
    if kubernetes_command in {"preflight", "check", "run", "evidence"}:
        body["skip_cluster"] = bool(getattr(args, "skip_cluster", False))
    if kubernetes_command == "evidence":
        body["submit_run"] = bool(getattr(args, "submit_run", False))
    if kubernetes_command == "run" and bool(getattr(args, "dry_run", False)):
        body["read_only"] = True

    payload = await client.post_json(f"/v1/kubernetes/{operation}", body=body)
    status = 1 if str(payload.get("status")) == "failed" else 0
    return CommandOutcome(payload, status)


def _build_probe_service(profile_config: str) -> RuntimeService:
    from universal_agent.domains.kubernetes.cli_runtime import build_configured_probe_service

    return build_configured_probe_service(profile_config)


def _is_probe_service_command(args: argparse.Namespace) -> bool:
    from universal_agent.domains.kubernetes.cli_parser import is_kubernetes_probe_service_command

    return is_kubernetes_probe_service_command(args)


def kubernetes_cli_contribution() -> CliDomainContribution:
    """Entry-point factory for the kubernetes CLI contribution."""

    from universal_agent.domains.kubernetes.cli_parser import (
        LOCAL_PROFILE_NAME,
        add_kubernetes_command,
    )

    return CliDomainContribution(
        domain="kubernetes",
        command_name="kubernetes",
        add_command=add_kubernetes_command,
        dispatch=_dispatch,
        remote_dispatch=_dispatch_remote,
        long_running_command=True,
        is_probe_service_command=_is_probe_service_command,
        uses_embedded_default_service=True,
        build_probe_service=_build_probe_service,
        init_backends=_KUBERNETES_INIT_BACKENDS,
        init_add_arguments=_add_init_arguments,
        init_resolve_domain=_resolve_init_domain,
        local_profile_name=LOCAL_PROFILE_NAME,
    )


__all__ = ["kubernetes_cli_contribution"]
