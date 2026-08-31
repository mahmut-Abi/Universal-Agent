from __future__ import annotations

import json
import os
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, cast

import pytest

from universal_agent.cli import run_cli
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import write_kubernetes_live_contract_artifact

PROFILE_CONFIG_ENV = "UNIVERSAL_AGENT_LIVE_KUBERNETES_PROFILE"
PROFILE_NAME_ENV = "UNIVERSAL_AGENT_LIVE_KUBERNETES_PROFILE_NAME"
WORKLOAD_ENV = "UNIVERSAL_AGENT_LIVE_KUBERNETES_WORKLOAD"
NAMESPACE_ENV = "UNIVERSAL_AGENT_LIVE_KUBERNETES_NAMESPACE"
RUN_ENV = "UNIVERSAL_AGENT_LIVE_KUBERNETES_RUN"
ARTIFACT_DIR_ENV = "UNIVERSAL_AGENT_LIVE_KUBERNETES_ARTIFACT_DIR"


@dataclass(frozen=True, slots=True)
class LiveKubernetesConfig:
    profile_config: str
    profile: str
    workload: str
    namespace: str | None


pytestmark = pytest.mark.live


@pytest.mark.asyncio
@pytest.mark.unit
async def test_live_kubernetes_check_contract_is_production_ready() -> None:
    config = _live_config()
    output = StringIO()

    status = await run_cli(_kubernetes_args(config, "check"), stdout=output)
    payload = _read_json(output.getvalue())
    _write_live_artifact("check", status, payload, config)

    assert status == 0
    assert payload["status"] == "ok"
    assert payload["model_probe"]["status"] == "ok"
    assert payload["preflight"]["status"] == "ok"
    assert payload["contract"]["status"] == "ok"
    assert payload["contract"]["failed_check_count"] == 0
    assert payload["contract"]["warning_check_count"] == 0


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_live_kubernetes_run_reaches_completion_or_confirmation_boundary() -> None:
    if os.environ.get(RUN_ENV) != "true":
        pytest.skip(f"set {RUN_ENV}=true to execute the live Kubernetes remediation run")
    config = _live_config()
    output = StringIO()

    status = await run_cli(_kubernetes_args(config, "run"), stdout=output)
    payload = _read_json(output.getvalue())
    _write_live_artifact("run", status, payload, config)
    run = _object(payload["run"])
    run_result = _object(run["result"])
    run_session = _object(run["session"])
    contract = _object(payload["contract"])
    contract_checks = {str(item["name"]): item for item in _objects(contract["checks"])}

    assert status == 0
    assert payload["status"] in {"completed", "waiting"}
    assert run_result["status"] == payload["status"]
    if payload["status"] == "completed":
        assert contract["status"] == "ok"
        assert contract_checks["completion_verification"]["status"] == "ok"
        return

    pending_action = _object(run_session["pending_action"])
    assert pending_action["capability"] == "scale_workload"
    assert contract["status"] == "attention"
    assert contract_checks["confirmation_boundary"]["status"] == "ok"
    assert contract_checks["completion_verification"]["status"] == "skipped"


def _kubernetes_args(config: LiveKubernetesConfig, command: str) -> list[str]:
    args = [
        "--profile-config",
        config.profile_config,
        "kubernetes",
        command,
        config.profile,
        "--workload",
        config.workload,
    ]
    if config.namespace is not None:
        args.extend(("--namespace", config.namespace))
    return args


def _live_config() -> LiveKubernetesConfig:
    profile_config = os.environ.get(PROFILE_CONFIG_ENV)
    if not profile_config:
        pytest.skip(f"set {PROFILE_CONFIG_ENV} to run live Kubernetes operator tests")

    workload = os.environ.get(WORKLOAD_ENV)
    if not workload:
        pytest.skip(f"set {WORKLOAD_ENV} to run live Kubernetes operator tests")

    return LiveKubernetesConfig(
        profile_config=profile_config,
        profile=os.environ.get(PROFILE_NAME_ENV, "production-operator"),
        workload=workload,
        namespace=os.environ.get(NAMESPACE_ENV),
    )


def _write_live_artifact(
    name: str,
    status: int,
    payload: dict[str, Any],
    config: LiveKubernetesConfig,
) -> None:
    artifact_dir = os.environ.get(ARTIFACT_DIR_ENV)
    if not artifact_dir:
        return
    write_kubernetes_live_contract_artifact(
        Path(artifact_dir),
        name=name,
        status=status,
        payload=cast(JsonMapping, payload),
        environment=cast(
            JsonMapping,
            {
                "profile": config.profile,
                "workload": config.workload,
                "namespace": config.namespace or "",
                "run_enabled": os.environ.get(RUN_ENV) == "true",
            },
        ),
    )


def _read_json(raw: str) -> dict[str, Any]:
    loaded: object = json.loads(raw)
    assert isinstance(loaded, dict)
    return cast(dict[str, Any], loaded)


def _object(value: object) -> dict[str, Any]:
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _objects(value: object) -> list[dict[str, Any]]:
    assert isinstance(value, list)
    for item in value:
        assert isinstance(item, dict)
    return cast(list[dict[str, Any]], value)
