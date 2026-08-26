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


def is_kubernetes_probe_service_command(args: argparse.Namespace) -> bool:
    return (
        cast(str | None, getattr(args, "command", None)) == "kubernetes"
        and cast(str | None, getattr(args, "kubernetes_command", None))
        in {"model-probe", "check"}
    )
