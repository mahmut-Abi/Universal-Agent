from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from universal_agent.core import JsonMapping, JsonValue, immutable_json
from universal_agent.domains.kubernetes.production_contract import (
    kubernetes_production_contract_report,
)


def test_kubernetes_production_contract_uses_structured_payloads_for_preflight_checks() -> None:
    report = kubernetes_production_contract_report(
        operation=immutable_json({"workload": "deployment/api", "namespace": "prod"}),
        model_probe=immutable_json(
            {
                "status": "ok",
                "decision": {
                    "capability": "inspect_workload",
                    "target": "api",
                    "arguments": {"name": "api", "namespace": "prod"},
                },
            }
        ),
        preflight=immutable_json(
            {
                "status": "failed",
                "checks": [
                    "ignored",
                    {"name": "model_secret", "status": "failed"},
                    {"name": 123, "status": "failed"},
                    {"name": "cluster_inspection", "status": "warn"},
                ],
            }
        ),
        run=None,
        include_runtime=True,
    )
    checks = _checks(report)

    assert report["status"] == "failed"
    assert report["failed_check_count"] == 2
    assert report["warning_check_count"] == 1
    assert checks["model_probe_scope"]["status"] == "ok"
    assert checks["preflight_failures"]["details"] == {"checks": ["model_secret"]}
    assert checks["preflight_warnings"]["details"] == {"checks": ["cluster_inspection"]}
    assert checks["runtime_submission"]["status"] == "skipped"


def test_kubernetes_production_contract_payloads_keep_malformed_probe_tolerant() -> None:
    report = kubernetes_production_contract_report(
        operation=immutable_json({"workload": "deployment/api", "namespace": "prod"}),
        model_probe=immutable_json(
            {
                "status": "ok",
                "decision": {"capability": 42, "arguments": []},
            }
        ),
        preflight=None,
        run=None,
        include_runtime=False,
    )
    checks = _checks(report)

    assert report["status"] == "failed"
    assert checks["model_probe"]["details"] == {"capability": ""}
    assert checks["model_probe_scope"]["message"] == (
        "model probe decision did not start with inspect_workload"
    )


def _checks(report: JsonMapping) -> dict[str, Mapping[str, JsonValue]]:
    raw_checks = cast(list[JsonValue], report["checks"])
    return {str(check["name"]): check for check in raw_checks if isinstance(check, Mapping)}
