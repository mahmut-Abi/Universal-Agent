"""Kubernetes Domain CLI parser registration (argparse-only, import-light).

Split from :mod:`universal_agent.domains.kubernetes.cli` so the CLI parser can
register the ``kubernetes`` subcommand without pulling the dispatch/runtime
stacks; ``universal_agent.domains.kubernetes.cli`` re-exports both functions.
"""

from __future__ import annotations

import argparse
from typing import cast

__all__ = [
    "LOCAL_PROFILE_NAME",
    "add_kubernetes_command",
    "is_kubernetes_probe_service_command",
]

# CLI-layer default profile name for the Kubernetes Domain. Canonical value
# lives in this import-light parser module; ``cli_runtime`` re-imports it so
# the CLI parser does not need the runtime stacks to read a constant.
LOCAL_PROFILE_NAME = "local-kubernetes"


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
    kubernetes_run.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run the investigation in read-only mode: evidence and diagnosis are "
            "collected but mutation capabilities are unavailable."
        ),
    )
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
