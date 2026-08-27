from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from universal_agent.core import JsonMapping, JsonValue, immutable_json


@dataclass(frozen=True, slots=True)
class KubernetesProductionContractCheck:
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


def kubernetes_production_contract_report(
    *,
    operation: JsonMapping,
    model_probe: JsonMapping | None,
    preflight: JsonMapping | None,
    run: JsonMapping | None,
    include_runtime: bool,
) -> JsonMapping:
    checks: list[KubernetesProductionContractCheck] = []
    checks.extend(_model_probe_checks(operation, model_probe))
    checks.extend(_preflight_checks(preflight))
    if include_runtime:
        checks.extend(_runtime_checks(operation, run))

    failed_count = _status_count(checks, "failed")
    warning_count = _status_count(checks, "warn")
    skipped_count = _status_count(checks, "skipped")
    status = "failed" if failed_count else "attention" if warning_count or skipped_count else "ok"
    return immutable_json(
        {
            "status": status,
            "passed": status == "ok",
            "check_count": len(checks),
            "failed_check_count": failed_count,
            "warning_check_count": warning_count,
            "skipped_check_count": skipped_count,
            "checks": [check.to_json() for check in checks],
        }
    )


def _model_probe_checks(
    operation: JsonMapping,
    model_probe: JsonMapping | None,
) -> tuple[KubernetesProductionContractCheck, ...]:
    if model_probe is None:
        return (
            KubernetesProductionContractCheck(
                "model_probe",
                "warn",
                "model probe was skipped before Kubernetes remediation",
            ),
            KubernetesProductionContractCheck(
                "model_probe_scope",
                "skipped",
                "model probe scope cannot be verified without a probe decision",
            ),
        )

    if _text(model_probe.get("status")) != "ok":
        return (
            KubernetesProductionContractCheck(
                "model_probe",
                "failed",
                "model probe failed before Kubernetes preflight or remediation",
                _error_details(model_probe),
            ),
            KubernetesProductionContractCheck(
                "model_probe_scope",
                "skipped",
                "model probe scope cannot be trusted after probe failure",
            ),
        )

    decision = _object(model_probe.get("decision"))
    scope_error = _probe_scope_error(operation, decision)
    return (
        KubernetesProductionContractCheck(
            "model_probe",
            "ok",
            "model returned a validated Kubernetes inspection decision",
            immutable_json({"capability": _text(decision.get("capability"))}),
        ),
        KubernetesProductionContractCheck(
            "model_probe_scope",
            "ok" if scope_error is None else "failed",
            "model probe decision is scoped to the requested workload"
            if scope_error is None
            else scope_error,
        ),
    )


def _preflight_checks(
    preflight: JsonMapping | None,
) -> tuple[KubernetesProductionContractCheck, ...]:
    if preflight is None:
        return (
            KubernetesProductionContractCheck(
                "kubernetes_preflight",
                "warn",
                "Kubernetes preflight was skipped before remediation",
            ),
            KubernetesProductionContractCheck(
                "preflight_failures",
                "skipped",
                "preflight failures cannot be inspected without a preflight report",
            ),
            KubernetesProductionContractCheck(
                "preflight_warnings",
                "skipped",
                "preflight warnings cannot be inspected without a preflight report",
            ),
        )

    failed_names = _preflight_check_names(preflight, "failed")
    warning_names = _preflight_check_names(preflight, "warn")
    return (
        KubernetesProductionContractCheck(
            "kubernetes_preflight",
            "ok" if _text(preflight.get("status")) == "ok" else "failed",
            "Kubernetes preflight completed successfully"
            if _text(preflight.get("status")) == "ok"
            else "Kubernetes preflight failed",
        ),
        KubernetesProductionContractCheck(
            "preflight_failures",
            "ok" if not failed_names else "failed",
            "preflight has no failed checks" if not failed_names else "preflight has failed checks",
            immutable_json({"checks": list(failed_names)}) if failed_names else None,
        ),
        KubernetesProductionContractCheck(
            "preflight_warnings",
            "ok" if not warning_names else "warn",
            "preflight has no warning checks"
            if not warning_names
            else "preflight has warning checks",
            immutable_json({"checks": list(warning_names)}) if warning_names else None,
        ),
    )


def _runtime_checks(
    operation: JsonMapping,
    run: JsonMapping | None,
) -> tuple[KubernetesProductionContractCheck, ...]:
    if run is None:
        return (
            KubernetesProductionContractCheck(
                "runtime_submission",
                "skipped",
                "runtime submission did not occur",
            ),
            KubernetesProductionContractCheck(
                "runtime_result",
                "skipped",
                "runtime result is unavailable before session submission",
            ),
            KubernetesProductionContractCheck(
                "completion_verification",
                "skipped",
                "completion verification is unavailable before session submission",
            ),
            KubernetesProductionContractCheck(
                "confirmation_boundary",
                "skipped",
                "no pending action was produced before session submission",
            ),
        )

    result = _object(run.get("result"))
    session = _object(run.get("session"))
    run_status = _text(result.get("status"))
    pending_action = _object_or_none(session.get("pending_action"))
    return (
        KubernetesProductionContractCheck(
            "runtime_submission",
            "ok",
            "runtime session was submitted through RuntimeService",
            immutable_json({"session_id": _text(result.get("session_id"))}),
        ),
        KubernetesProductionContractCheck(
            "runtime_result",
            _runtime_result_status(run_status, pending_action),
            _runtime_result_message(run_status, pending_action),
        ),
        _completion_verification_check(operation, run_status, session, pending_action),
        _confirmation_boundary_check(pending_action),
    )


def _completion_verification_check(
    operation: JsonMapping,
    run_status: str,
    session: JsonMapping,
    pending_action: JsonMapping | None,
) -> KubernetesProductionContractCheck:
    if run_status == "waiting" and pending_action is not None:
        return KubernetesProductionContractCheck(
            "completion_verification",
            "skipped",
            "fresh verification is pending explicit mutation confirmation",
        )
    if run_status != "completed":
        return KubernetesProductionContractCheck(
            "completion_verification",
            "failed" if run_status == "failed" else "skipped",
            "runtime did not reach a completed state with fresh verification evidence",
        )

    satisfied = _object(session.get("satisfied_criteria"))
    healthy = satisfied.get("healthy") is True
    resource_matches = satisfied.get("resource") == operation.get("workload")
    namespace = _text(operation.get("namespace"))
    namespace_matches = not namespace or satisfied.get("namespace") == namespace
    verified = healthy and resource_matches and namespace_matches
    return KubernetesProductionContractCheck(
        "completion_verification",
        "ok" if verified else "failed",
        "completed run includes fresh workload health criteria"
        if verified
        else "completed run is missing expected workload health criteria",
        immutable_json(
            {
                "healthy": bool(healthy),
                "resource_matches": bool(resource_matches),
                "namespace_matches": bool(namespace_matches),
            }
        ),
    )


def _confirmation_boundary_check(
    pending_action: JsonMapping | None,
) -> KubernetesProductionContractCheck:
    if pending_action is None:
        return KubernetesProductionContractCheck(
            "confirmation_boundary",
            "ok",
            "no mutation is pending confirmation",
        )
    return KubernetesProductionContractCheck(
        "confirmation_boundary",
        "ok",
        "policy-gated mutation is paused for explicit confirmation",
        immutable_json(
            {
                "capability": _text(pending_action.get("capability")),
                "action_id": _text(pending_action.get("action_id")),
            }
        ),
    )


def _runtime_result_status(
    run_status: str,
    pending_action: JsonMapping | None,
) -> str:
    if run_status == "completed":
        return "ok"
    if run_status == "waiting" and pending_action is not None:
        return "ok"
    if run_status == "failed":
        return "failed"
    return "warn"


def _runtime_result_message(
    run_status: str,
    pending_action: JsonMapping | None,
) -> str:
    if run_status == "completed":
        return "runtime completed the Kubernetes remediation flow"
    if run_status == "waiting" and pending_action is not None:
        return "runtime stopped at the confirmation boundary"
    if run_status == "failed":
        return "runtime failed the Kubernetes remediation flow"
    return f"runtime ended with status: {run_status or 'unknown'}"


def _probe_scope_error(operation: JsonMapping, decision: JsonMapping) -> str | None:
    if decision.get("capability") != "inspect_workload":
        return "model probe decision did not start with inspect_workload"

    expected_workload = _text(operation.get("workload"))
    target = _text(decision.get("target"))
    if target and _normal_workload(target) != expected_workload:
        return "model probe target does not match requested workload"

    arguments = _object(decision.get("arguments"))
    name = _text(arguments.get("name"))
    if not name or _normal_workload(name) != expected_workload:
        return "model probe name argument does not match requested workload"

    expected_namespace = _text(operation.get("namespace"))
    if expected_namespace and arguments.get("namespace") != expected_namespace:
        return "model probe namespace argument does not match requested namespace"
    return None


def _preflight_check_names(report: JsonMapping, status: str) -> tuple[str, ...]:
    raw_checks = report.get("checks")
    if not isinstance(raw_checks, list):
        return ()
    names: list[str] = []
    for raw_check in raw_checks:
        check = _object(raw_check)
        if check.get("status") == status:
            name = _text(check.get("name"))
            if name:
                names.append(name)
    return tuple(names)


def _error_details(report: JsonMapping) -> JsonMapping | None:
    error = _object(report.get("error"))
    if not error:
        return None
    body: dict[str, JsonValue] = {}
    error_type = _text(error.get("type"))
    message = _text(error.get("message"))
    if error_type:
        body["type"] = error_type
    if message:
        body["message"] = message
    return immutable_json(body) if body else None


def _normal_workload(value: str) -> str:
    normalized = value.strip()
    if "/" in normalized:
        return normalized
    return f"deployment/{normalized}"


def _object(value: object) -> JsonMapping:
    if isinstance(value, Mapping):
        return cast(JsonMapping, value)
    return immutable_json()


def _object_or_none(value: object) -> JsonMapping | None:
    if isinstance(value, Mapping):
        return cast(JsonMapping, value)
    return None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _status_count(
    checks: list[KubernetesProductionContractCheck],
    status: str,
) -> int:
    return sum(1 for check in checks if check.status == status)
