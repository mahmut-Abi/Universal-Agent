from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from universal_agent.core import JsonMapping, JsonValue, immutable_json


@dataclass(frozen=True, slots=True)
class KubernetesProductionEvidenceCheck:
    name: str
    status: str
    message: str
    details: JsonMapping | None = None

    def to_json(self) -> dict[str, JsonValue]:
        body: dict[str, JsonValue] = {
            "name": self.name,
            "status": self.status,
            "message": self.message,
        }
        if self.details is not None:
            body["details"] = dict(self.details)
        return body


def kubernetes_production_evidence_gate_report(
    *,
    model_probe: JsonMapping | None,
    preflight: JsonMapping | None,
    run: JsonMapping | None,
    contract: JsonMapping,
    submit_run: bool,
    skip_cluster: bool,
) -> JsonMapping:
    checks = [
        _model_provider_check(model_probe),
        _kubernetes_backend_check(preflight),
        _cluster_inspection_check(preflight, skip_cluster=skip_cluster),
        _runtime_run_check(run, submit_run=submit_run),
        _contract_check(contract),
    ]
    failed_count = _status_count(checks, "failed")
    warning_count = _status_count(checks, "warn")
    skipped_count = _status_count(checks, "skipped")
    status = "failed" if failed_count else "attention" if warning_count or skipped_count else "ok"
    live_path_observed = (
        _check_status(checks, "model_provider") == "ok"
        and _check_status(checks, "kubernetes_backend") == "ok"
        and _check_status(checks, "cluster_inspection") == "ok"
        and _check_status(checks, "runtime_run") == "ok"
    )
    return immutable_json(
        {
            "status": status,
            "passed": status == "ok",
            "evidence_level": _evidence_level(checks, failed_count),
            "live_runtime_path_observed": live_path_observed,
            "check_count": len(checks),
            "failed_check_count": failed_count,
            "warning_check_count": warning_count,
            "skipped_check_count": skipped_count,
            "checks": [check.to_json() for check in checks],
        }
    )


def _model_provider_check(
    model_probe: JsonMapping | None,
) -> KubernetesProductionEvidenceCheck:
    if model_probe is None:
        return KubernetesProductionEvidenceCheck(
            "model_provider",
            "skipped",
            "model probe did not run, so the model provider was not observed",
        )
    if model_probe.get("status") != "ok":
        return KubernetesProductionEvidenceCheck(
            "model_provider",
            "failed",
            "model probe failed before a scoped Kubernetes decision was observed",
        )
    model = _mapping(model_probe.get("model"))
    provider = "" if model is None else _text(model.get("provider"))
    if provider == "scripted":
        return KubernetesProductionEvidenceCheck(
            "model_provider",
            "warn",
            "scripted model proves contract shape only, not a live provider",
            immutable_json({"provider": provider}),
        )
    return KubernetesProductionEvidenceCheck(
        "model_provider",
        "ok",
        "model probe used a configured non-scripted provider",
        immutable_json({"provider": provider or "unknown"}),
    )


def _kubernetes_backend_check(
    preflight: JsonMapping | None,
) -> KubernetesProductionEvidenceCheck:
    if preflight is None:
        return KubernetesProductionEvidenceCheck(
            "kubernetes_backend",
            "skipped",
            "Kubernetes preflight did not run, so the backend was not observed",
        )
    domain = _mapping(preflight.get("domain"))
    backend = "" if domain is None else _text(domain.get("backend"))
    if backend == "fake":
        return KubernetesProductionEvidenceCheck(
            "kubernetes_backend",
            "warn",
            "fake backend proves CLI wiring only, not live cluster access",
            immutable_json({"backend": backend}),
        )
    if not backend:
        return KubernetesProductionEvidenceCheck(
            "kubernetes_backend",
            "failed",
            "Kubernetes backend could not be identified from preflight",
        )
    return KubernetesProductionEvidenceCheck(
        "kubernetes_backend",
        "ok",
        "preflight used a real Kubernetes backend adapter",
        immutable_json({"backend": backend}),
    )


def _cluster_inspection_check(
    preflight: JsonMapping | None,
    *,
    skip_cluster: bool,
) -> KubernetesProductionEvidenceCheck:
    if skip_cluster:
        return KubernetesProductionEvidenceCheck(
            "cluster_inspection",
            "skipped",
            "cluster inspection was skipped by request",
        )
    if preflight is None:
        return KubernetesProductionEvidenceCheck(
            "cluster_inspection",
            "skipped",
            "Kubernetes preflight did not run",
        )
    checks = _preflight_checks(preflight)
    failed = (
        _named_check_status(checks, "cluster_inspection") != "ok"
        or _named_check_status(
            checks,
            "workload_inspection",
        )
        != "ok"
    )
    return KubernetesProductionEvidenceCheck(
        "cluster_inspection",
        "failed" if failed else "ok",
        "preflight observed cluster and workload state"
        if not failed
        else "preflight did not observe both cluster and workload state",
    )


def _runtime_run_check(
    run: JsonMapping | None,
    *,
    submit_run: bool,
) -> KubernetesProductionEvidenceCheck:
    if not submit_run:
        return KubernetesProductionEvidenceCheck(
            "runtime_run",
            "skipped",
            "runtime remediation submission was not requested",
        )
    if run is None:
        return KubernetesProductionEvidenceCheck(
            "runtime_run",
            "failed",
            "runtime remediation submission did not produce a run body",
        )
    result = _mapping(run.get("result"))
    status = "" if result is None else _text(result.get("status"))
    if status in {"completed", "waiting"}:
        return KubernetesProductionEvidenceCheck(
            "runtime_run",
            "ok",
            "runtime reached completion or an explicit confirmation boundary",
            immutable_json({"status": status}),
        )
    return KubernetesProductionEvidenceCheck(
        "runtime_run",
        "failed",
        "runtime did not reach completion or a confirmation boundary",
        immutable_json({"status": status or "unknown"}),
    )


def _contract_check(contract: JsonMapping) -> KubernetesProductionEvidenceCheck:
    status = _text(contract.get("status"))
    if status == "ok":
        return KubernetesProductionEvidenceCheck(
            "production_contract",
            "ok",
            "production contract checks passed",
        )
    if status == "failed":
        return KubernetesProductionEvidenceCheck(
            "production_contract",
            "failed",
            "production contract checks failed",
        )
    return KubernetesProductionEvidenceCheck(
        "production_contract",
        "warn",
        "production contract needs operator attention",
        immutable_json({"status": status or "unknown"}),
    )


def _evidence_level(
    checks: list[KubernetesProductionEvidenceCheck],
    failed_count: int,
) -> str:
    if failed_count:
        return "insufficient"
    if (
        _check_status(checks, "model_provider") == "ok"
        and _check_status(checks, "kubernetes_backend") == "ok"
        and _check_status(checks, "cluster_inspection") == "ok"
    ):
        if _check_status(checks, "runtime_run") == "ok":
            return "live_runtime_boundary"
        return "live_preflight"
    return "local_or_fixture"


def _preflight_checks(preflight: JsonMapping) -> tuple[Mapping[str, JsonValue], ...]:
    value = preflight.get("checks")
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _named_check_status(
    checks: tuple[Mapping[str, JsonValue], ...],
    name: str,
) -> str:
    for check in checks:
        if check.get("name") == name:
            return _text(check.get("status"))
    return ""


def _check_status(
    checks: list[KubernetesProductionEvidenceCheck],
    name: str,
) -> str:
    for check in checks:
        if check.name == name:
            return check.status
    return ""


def _mapping(value: JsonValue | None) -> Mapping[str, JsonValue] | None:
    if not isinstance(value, Mapping):
        return None
    return value


def _text(value: JsonValue | None) -> str:
    return value if isinstance(value, str) else ""


def _status_count(
    checks: list[KubernetesProductionEvidenceCheck],
    status: str,
) -> int:
    return sum(1 for check in checks if check.status == status)
