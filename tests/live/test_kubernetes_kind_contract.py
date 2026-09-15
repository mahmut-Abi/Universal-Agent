from __future__ import annotations

import json
import os
import shutil
import subprocess
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest

from universal_agent_cli import run_cli

ENABLE_ENV = "UNIVERSAL_AGENT_LIVE_LIKE_KUBERNETES"
CONTEXT_ENV = "UNIVERSAL_AGENT_LIVE_LIKE_KUBERNETES_CONTEXT"
RUN_VALUES = {"1", "true", "yes"}

pytestmark = pytest.mark.live


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_kind_or_minikube_unhealthy_workload_reaches_confirmation(
    tmp_path: Path,
) -> None:
    """Live-like contract: real kubectl + local cluster, no real model or cloud cluster.

    The test provisions a deliberately unhealthy deployment (0 replicas) in a
    temporary namespace, runs the Runtime through the kubectl backend, and
    verifies it reaches the production policy confirmation boundary before any
    mutation is applied. This exercises real Kubernetes discovery/inspection,
    runtime evidence/evaluation, and policy gating while remaining safe and
    reproducible on kind/minikube.
    """

    context = _local_kubernetes_context()
    namespace = f"ua-live-like-{uuid4().hex[:8]}"
    profile_path = tmp_path / "profile.json"
    run_output = StringIO()

    _kubectl(context, "create", "namespace", namespace)
    try:
        _kubectl(
            context,
            "create",
            "deployment",
            "ua-unhealthy",
            "--image=registry.k8s.io/pause:3.9",
            "--replicas=0",
            "--namespace",
            namespace,
        )
        init_status = await run_cli(
            [
                "init",
                "--output-format",
                "json",
                "--output",
                str(profile_path),
                "--profile",
                "local-kubernetes",
                "--environment",
                "production",
                "--domain-backend",
                "kubectl",
                "--kubectl-context",
                context,
                "--kubectl-namespace",
                namespace,
                "--kubectl-timeout-seconds",
                "15",
            ],
            stdout=StringIO(),
        )
        run_status = await run_cli(
            [
                "--profile-config",
                str(profile_path),
                "kubernetes",
                "run",
                "local-kubernetes",
                "--workload",
                "deployment/ua-unhealthy",
                "--namespace",
                namespace,
                "--skip-model-probe",
                "--skip-preflight",
            ],
            stdout=run_output,
        )
        payload = _read_json(run_output)
        run = payload["run"]
        assert isinstance(run, dict)
        result = run["result"]
        session = run["session"]
        contract = payload["contract"]
        assert isinstance(result, dict)
        assert isinstance(session, dict)
        assert isinstance(contract, dict)

        assert init_status == 0
        assert run_status == 0
        assert payload["status"] == "waiting"
        assert result["status"] == "waiting"
        assert session["pending_action"] is not None
        pending_action = session["pending_action"]
        assert isinstance(pending_action, dict)
        assert pending_action["capability"] == "scale_workload"
        assert contract["status"] == "attention"
        checks = {str(item["name"]): item for item in contract["checks"] if isinstance(item, dict)}
        assert checks["confirmation_boundary"]["status"] == "ok"
    finally:
        _kubectl(context, "delete", "namespace", namespace, "--ignore-not-found=true")


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_kind_or_minikube_unhealthy_workload_completes_fresh_verification(
    tmp_path: Path,
) -> None:
    """Live-like staging contract: real kubectl remediation reaches completion.

    Unlike the production-boundary test, this uses ``staging`` so the policy
    allows the bounded scale action. The contract must then include fresh
    verification evidence rather than stopping with completion verification
    skipped at the confirmation boundary.
    """

    context = _local_kubernetes_context()
    namespace = f"ua-live-like-{uuid4().hex[:8]}"
    profile_path = tmp_path / "profile.json"
    run_output = StringIO()

    _kubectl(context, "create", "namespace", namespace)
    try:
        _kubectl(
            context,
            "create",
            "deployment",
            "ua-unhealthy",
            "--image=registry.k8s.io/pause:3.9",
            "--replicas=0",
            "--namespace",
            namespace,
        )
        init_status = await run_cli(
            [
                "init",
                "--output-format",
                "json",
                "--output",
                str(profile_path),
                "--profile",
                "local-kubernetes",
                "--environment",
                "staging",
                "--domain-backend",
                "kubectl",
                "--kubectl-context",
                context,
                "--kubectl-namespace",
                namespace,
                "--kubectl-timeout-seconds",
                "20",
            ],
            stdout=StringIO(),
        )
        run_status = await run_cli(
            [
                "--profile-config",
                str(profile_path),
                "kubernetes",
                "run",
                "local-kubernetes",
                "--workload",
                "deployment/ua-unhealthy",
                "--namespace",
                namespace,
                "--skip-model-probe",
                "--skip-preflight",
            ],
            stdout=run_output,
        )
        payload = _read_json(run_output)
        run = payload["run"]
        contract = payload["contract"]
        assert isinstance(run, dict)
        assert isinstance(contract, dict)
        result = run["result"]
        session = run["session"]
        assert isinstance(result, dict)
        assert isinstance(session, dict)

        assert init_status == 0
        assert run_status == 0
        assert payload["status"] == "completed"
        assert result["status"] == "completed"
        assert session["pending_action"] is None
        assert session["satisfied_criteria"]["healthy"] is True
        assert session["satisfied_criteria"]["resource"] == "deployment/ua-unhealthy"
        assert session["satisfied_criteria"]["namespace"] == namespace
        # Skipped model-probe and preflight trigger "attention" (not "ok"):
        # the critical completion-verification and confirmation-boundary
        # checks are still expected to pass.
        assert contract["status"] == "attention"
        checks = {str(item["name"]): item for item in contract["checks"] if isinstance(item, dict)}
        assert checks["completion_verification"]["status"] == "ok"
        assert checks["confirmation_boundary"]["status"] == "ok"
    finally:
        _kubectl(context, "delete", "namespace", namespace, "--ignore-not-found=true")


def _local_kubernetes_context() -> str:
    if os.environ.get(ENABLE_ENV, "").lower() not in RUN_VALUES:
        pytest.skip(f"set {ENABLE_ENV}=true to run the live-like kind/minikube contract")
    if shutil.which("kubectl") is None:
        pytest.skip("kubectl is not installed")
    context = os.environ.get(CONTEXT_ENV) or _kubectl(None, "config", "current-context").strip()
    return context


def _kubectl(context: str | None, *args: str) -> str:
    command = ["kubectl"]
    if context:
        command.extend(["--context", context])
    command.extend(args)
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
    return result.stdout


def _read_json(output: StringIO) -> dict[str, object]:
    loaded = json.loads(output.getvalue())
    assert isinstance(loaded, dict)
    return loaded
