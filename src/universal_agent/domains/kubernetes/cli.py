from __future__ import annotations

import argparse
from typing import cast

from universal_agent.domains.kubernetes.cli_reports import dispatch_kubernetes
from universal_agent.domains.kubernetes.cli_runtime import (
    LOCAL_PROFILE_NAME,
    build_configured_probe_service,
    build_configured_service,
    build_default_service,
    profile_domain_config,
)

__all__ = [
    "LOCAL_PROFILE_NAME",
    "add_kubernetes_command",
    "build_configured_probe_service",
    "build_configured_service",
    "build_default_service",
    "dispatch_kubernetes",
    "is_kubernetes_probe_service_command",
    "profile_domain_config",
]


def add_kubernetes_command(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    kubernetes = commands.add_parser("kubernetes")
    kubernetes_commands = kubernetes.add_subparsers(dest="kubernetes_command", required=True)
    kubernetes_preflight = kubernetes_commands.add_parser("preflight")
    kubernetes_preflight.add_argument("--workload")
    kubernetes_preflight.add_argument("--namespace")
    kubernetes_preflight.add_argument("--skip-cluster", action="store_true")
    kubernetes_model_probe = kubernetes_commands.add_parser("model-probe")
    kubernetes_model_probe.add_argument("profile")
    kubernetes_model_probe.add_argument("--workload", required=True)
    kubernetes_model_probe.add_argument("--namespace")
    kubernetes_check = kubernetes_commands.add_parser("check")
    kubernetes_check.add_argument("profile")
    kubernetes_check.add_argument("--workload", required=True)
    kubernetes_check.add_argument("--namespace")
    kubernetes_check.add_argument("--skip-cluster", action="store_true")
    kubernetes_run = kubernetes_commands.add_parser("run")
    kubernetes_run.add_argument("profile")
    kubernetes_run.add_argument("--workload", required=True)
    kubernetes_run.add_argument("--namespace")
    kubernetes_run.add_argument("--skip-preflight", action="store_true")
    kubernetes_run.add_argument("--skip-model-probe", action="store_true")
    kubernetes_run.add_argument("--skip-cluster", action="store_true")
    kubernetes_evidence = kubernetes_commands.add_parser("evidence")
    kubernetes_evidence.add_argument("profile")
    kubernetes_evidence.add_argument("--workload", required=True)
    kubernetes_evidence.add_argument("--namespace")
    kubernetes_evidence.add_argument("--skip-cluster", action="store_true")
    kubernetes_evidence.add_argument(
        "--submit-run",
        action="store_true",
        help=(
            "Submit the Runtime-owned remediation goal after model probe and preflight pass. "
            "Without this flag the command proves only the pre-run production gate."
        ),
    )


def is_kubernetes_probe_service_command(args: argparse.Namespace) -> bool:
    return cast(str | None, getattr(args, "command", None)) == "kubernetes" and cast(
        str | None, getattr(args, "kubernetes_command", None)
    ) in {"model-probe", "check"}
