from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated

from pydantic import BeforeValidator, Field

from universal_agent.core import JsonMapping, JsonValue, immutable_json
from universal_agent.core.config_validation import ConfigPayload, PydanticJsonValue, parse_payload


def _text_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def _mapping_or_empty(value: object) -> object:
    return dict(value) if isinstance(value, Mapping) else {}


def _mapping_list_or_empty(value: object) -> object:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


_Text = Annotated[str, BeforeValidator(_text_or_empty)]
_JsonObject = Annotated[dict[str, PydanticJsonValue], BeforeValidator(_mapping_or_empty)]


class _OperationPayload(ConfigPayload):
    workload: _Text = ""
    namespace: _Text = ""


class _DecisionPayload(ConfigPayload):
    capability: _Text = ""
    target: _Text = ""
    arguments: _JsonObject = Field(default_factory=dict)


class _WorkloadArgumentsPayload(ConfigPayload):
    name: _Text = ""
    namespace: _Text = ""


class _ModelProbePayload(ConfigPayload):
    status: _Text = ""
    decision: _JsonObject = Field(default_factory=dict)
    error: _JsonObject = Field(default_factory=dict)


class _PreflightCheckPayload(ConfigPayload):
    name: _Text = ""
    status: _Text = ""


class _PreflightPayload(ConfigPayload):
    status: _Text = ""
    checks: Annotated[
        list[_PreflightCheckPayload],
        BeforeValidator(_mapping_list_or_empty),
    ] = Field(default_factory=list)


class _RuntimeResultPayload(ConfigPayload):
    status: _Text = ""
    session_id: _Text = ""


class _PendingActionPayload(ConfigPayload):
    capability: _Text = ""
    action_id: _Text = ""


class _SessionPayload(ConfigPayload):
    pending_action: _JsonObject | None = None
    satisfied_criteria: _JsonObject = Field(default_factory=dict)


class _RunPayload(ConfigPayload):
    result: _JsonObject = Field(default_factory=dict)
    session: _JsonObject = Field(default_factory=dict)


class _ErrorPayload(ConfigPayload):
    type: _Text = ""
    message: _Text = ""


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

    operation_payload = _payload(_OperationPayload, operation)
    probe = _payload(_ModelProbePayload, model_probe)
    if probe.status != "ok":
        return (
            KubernetesProductionContractCheck(
                "model_probe",
                "failed",
                "model probe failed before Kubernetes preflight or remediation",
                _error_details(probe.error),
            ),
            KubernetesProductionContractCheck(
                "model_probe_scope",
                "skipped",
                "model probe scope cannot be trusted after probe failure",
            ),
        )

    decision = _payload(_DecisionPayload, probe.decision)
    scope_error = _probe_scope_error(operation_payload, decision)
    return (
        KubernetesProductionContractCheck(
            "model_probe",
            "ok",
            "model returned a validated Kubernetes inspection decision",
            immutable_json({"capability": decision.capability}),
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

    report = _payload(_PreflightPayload, preflight)
    failed_names = _preflight_check_names(report, "failed")
    warning_names = _preflight_check_names(report, "warn")
    preflight_ok = report.status == "ok"
    return (
        KubernetesProductionContractCheck(
            "kubernetes_preflight",
            "ok" if preflight_ok else "failed",
            "Kubernetes preflight completed successfully"
            if preflight_ok
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

    operation_payload = _payload(_OperationPayload, operation)
    payload = _payload(_RunPayload, run)
    result = _payload(_RuntimeResultPayload, payload.result)
    session = _payload(_SessionPayload, payload.session)
    run_status = result.status
    pending_action = _payload_or_none(_PendingActionPayload, session.pending_action)
    return (
        KubernetesProductionContractCheck(
            "runtime_submission",
            "ok",
            "runtime session was submitted through RuntimeService",
            immutable_json({"session_id": result.session_id}),
        ),
        KubernetesProductionContractCheck(
            "runtime_result",
            _runtime_result_status(run_status, pending_action),
            _runtime_result_message(run_status, pending_action),
        ),
        _completion_verification_check(operation_payload, run_status, session, pending_action),
        _confirmation_boundary_check(pending_action),
    )


def _completion_verification_check(
    operation: _OperationPayload,
    run_status: str,
    session: _SessionPayload,
    pending_action: _PendingActionPayload | None,
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

    satisfied = session.satisfied_criteria
    healthy = satisfied.get("healthy") is True
    resource_matches = satisfied.get("resource") == operation.workload
    namespace = operation.namespace
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
    pending_action: _PendingActionPayload | None,
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
                "capability": pending_action.capability,
                "action_id": pending_action.action_id,
            }
        ),
    )


def _runtime_result_status(
    run_status: str,
    pending_action: _PendingActionPayload | None,
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
    pending_action: _PendingActionPayload | None,
) -> str:
    if run_status == "completed":
        return "runtime completed the Kubernetes remediation flow"
    if run_status == "waiting" and pending_action is not None:
        return "runtime stopped at the confirmation boundary"
    if run_status == "failed":
        return "runtime failed the Kubernetes remediation flow"
    return f"runtime ended with status: {run_status or 'unknown'}"


def _probe_scope_error(
    operation: _OperationPayload,
    decision: _DecisionPayload,
) -> str | None:
    if decision.capability != "inspect_workload":
        return "model probe decision did not start with inspect_workload"

    expected_workload = operation.workload
    target = decision.target
    if target and _normal_workload(target) != expected_workload:
        return "model probe target does not match requested workload"

    arguments = _payload(_WorkloadArgumentsPayload, decision.arguments)
    name = arguments.name
    if not name or _normal_workload(name) != expected_workload:
        return "model probe name argument does not match requested workload"

    expected_namespace = operation.namespace
    if expected_namespace and arguments.namespace != expected_namespace:
        return "model probe namespace argument does not match requested namespace"
    return None


def _preflight_check_names(report: _PreflightPayload, status: str) -> tuple[str, ...]:
    return tuple(check.name for check in report.checks if check.status == status and check.name)


def _error_details(value: object) -> JsonMapping | None:
    error = _payload(_ErrorPayload, value)
    body: dict[str, JsonValue] = {}
    if error.type:
        body["type"] = error.type
    if error.message:
        body["message"] = error.message
    return immutable_json(body) if body else None


def _normal_workload(value: str) -> str:
    normalized = value.strip()
    if "/" in normalized:
        return normalized
    return f"deployment/{normalized}"


def _payload[T: ConfigPayload](model_type: type[T], value: object) -> T:
    if not isinstance(value, Mapping):
        return model_type()
    try:
        return parse_payload(model_type, value)
    except ValueError:
        return model_type()


def _payload_or_none[T: ConfigPayload](model_type: type[T], value: object) -> T | None:
    if not isinstance(value, Mapping):
        return None
    return _payload(model_type, value)


def _status_count(
    checks: list[KubernetesProductionContractCheck],
    status: str,
) -> int:
    return sum(1 for check in checks if check.status == status)
