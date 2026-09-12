"""Pure report rendering and payload helpers for the Kubernetes CLI.

Separated from :mod:`cli_reports` (operator flow: dispatch, probe, preflight)
so payload/next-step rendering can evolve without touching flow control.
All names are re-exported from ``cli_reports`` for compatibility.
"""

from __future__ import annotations

import argparse
import shlex
from dataclasses import dataclass
from typing import cast

from universal_agent.core import (
    Decision,
    ExecutionStatus,
    JsonMapping,
    JsonValue,
    SuccessCriterion,
    immutable_json,
    to_json_object,
)
from universal_agent.domains.kubernetes.production_contract import (
    kubernetes_production_contract_report,
)
from universal_agent.runtime import RuntimeRun


def _model_probe_failure_next_step(exc: Exception) -> dict[str, JsonValue]:
    message = str(exc)
    suggestions: list[JsonValue] = [
        "Verify the configured model endpoint and API key secret resolve.",
        (
            "For OpenAI-compatible Chat Completions providers, try "
            "`--model-response-format prompt_json`."
        ),
        "Increase `--model-timeout-seconds` for slow reasoning models.",
        (
            "Use `--model-header KEY=VALUE` for provider-required organization "
            "or compatibility headers."
        ),
    ]
    lowered = message.lower()
    if "response_format" in lowered or "json" in lowered or "schema" in lowered:
        suggestions.insert(
            0,
            "Switch response format: json_schema -> json_object -> prompt_json, then re-run probe.",
        )
    if "timeout" in lowered:
        suggestions.insert(0, "Increase `--model-timeout-seconds` and retry the probe.")
    if "api key" in lowered or "secret" in lowered or "credential" in lowered:
        suggestions.insert(0, "Set the API key env/file secret declared in the profile.")
    return {
        "type": "fix_model_provider",
        "message": (
            "Fix the profile model provider, credentials, response_format, headers, timeout, "
            "or returned Decision JSON before running Kubernetes remediation."
        ),
        "try": suggestions,
    }


@dataclass(frozen=True, slots=True)
class KubernetesOperation:
    profile: str
    workload: str
    namespace: str | None = None

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> KubernetesOperation:
        return cls(
            profile=cast(str, args.profile),
            workload=kubernetes_workload_resource(cast(str, args.workload)),
            namespace=optional_kubernetes_namespace(cast(str | None, args.namespace)),
        )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "profile": self.profile,
            "workload": self.workload,
            "namespace": self.namespace or "",
        }


def kubernetes_check_failure_body(
    operation: KubernetesOperation,
    model_probe: JsonMapping,
    *,
    preflight: JsonMapping | None,
    next_step: JsonMapping,
) -> JsonMapping:
    operation_body = operation.to_json()
    return immutable_json(
        {
            "status": "failed",
            "operation": operation_body,
            "model_probe": dict(model_probe),
            "preflight": None if preflight is None else dict(preflight),
            "contract": dict(
                kubernetes_production_contract_report(
                    operation=operation_body,
                    model_probe=model_probe,
                    preflight=preflight,
                    run=None,
                    include_runtime=False,
                )
            ),
            "next_step": dict(next_step),
        }
    )


def kubernetes_evidence_next_step(
    args: argparse.Namespace,
    model_probe: JsonMapping,
    preflight: JsonMapping | None,
    run: RuntimeRun | None,
    *,
    submit_run: bool,
) -> JsonValue:
    if model_probe["status"] == "failed":
        return {
            "type": "fix_model_provider",
            "message": "Fix model probe failure before collecting production evidence.",
        }
    if preflight is None or preflight["status"] == "failed":
        return {
            "type": "fix_preflight",
            "message": "Resolve Kubernetes preflight before collecting production evidence.",
        }
    if not submit_run:
        return {
            "type": "submit_runtime_run",
            "message": "Re-run evidence with --submit-run to observe the runtime boundary.",
        }
    if run is None:
        return {
            "type": "inspect_failure",
            "message": "Runtime submission was requested but no run body was produced.",
        }
    return kubernetes_run_next_step(run, cast(str | None, args.profile_config))


def kubernetes_model_config_body(model: object) -> dict[str, JsonValue]:
    provider = getattr(model, "provider", "scripted")
    name = getattr(model, "name", "scripted")
    endpoint = getattr(model, "endpoint", None)
    api_key_secret = getattr(model, "api_key_secret", None)
    timeout_seconds = getattr(model, "timeout_seconds", 30.0)
    response_format = getattr(model, "response_format", None)
    body: dict[str, JsonValue] = {
        "provider": str(provider),
        "name": str(name),
        "endpoint": None if endpoint is None else str(endpoint),
        "api_key_secret": None if api_key_secret is None else str(api_key_secret),
        "timeout_seconds": (
            timeout_seconds + 0.0 if isinstance(timeout_seconds, int | float) else 30.0
        ),
    }
    if response_format is not None:
        body["response_format"] = str(response_format)
    return body


def decision_body(decision: Decision) -> dict[str, JsonValue]:
    return {
        "type": decision.type.value,
        "reason": decision.reason,
        "capability": decision.capability,
        "target": decision.target,
        "arguments": dict(decision.arguments),
        "expected_observations": list(decision.expected_observations),
        "message": decision.message,
    }


def kubernetes_workload_arguments(workload: str, namespace: str | None) -> JsonMapping:
    if not workload.strip():
        raise ValueError("kubernetes preflight workload must not be empty")
    arguments: dict[str, JsonValue] = {"name": workload}
    if namespace is not None:
        if not namespace.strip():
            raise ValueError("kubernetes preflight namespace must not be empty")
        arguments["namespace"] = namespace
    return immutable_json(arguments)


def kubernetes_run_body(
    args: argparse.Namespace,
    run: RuntimeRun,
    preflight: JsonMapping | None,
    model_probe: JsonMapping | None,
) -> JsonMapping:
    profile_config = cast(str | None, args.profile_config)
    operation = KubernetesOperation.from_args(args).to_json()
    run_body = to_json_object(run, fallback_to_string=True)
    return immutable_json(
        {
            "status": run.result.status.value,
            "operation": operation,
            "model_probe": None if model_probe is None else dict(model_probe),
            "preflight": None if preflight is None else dict(preflight),
            "run": run_body,
            "contract": dict(
                kubernetes_production_contract_report(
                    operation=operation,
                    model_probe=model_probe,
                    preflight=preflight,
                    run=run_body,
                    include_runtime=True,
                )
            ),
            "next_step": kubernetes_run_next_step(run, profile_config),
        }
    )


def kubernetes_run_model_probe_failed_body(
    args: argparse.Namespace,
    model_probe: JsonMapping,
) -> JsonMapping:
    operation = KubernetesOperation.from_args(args).to_json()
    return immutable_json(
        {
            "status": "failed",
            "operation": operation,
            "model_probe": dict(model_probe),
            "preflight": None,
            "run": None,
            "contract": dict(
                kubernetes_production_contract_report(
                    operation=operation,
                    model_probe=model_probe,
                    preflight=None,
                    run=None,
                    include_runtime=True,
                )
            ),
            "next_step": {
                "type": "fix_model_provider",
                "message": "Fix model probe failure before Kubernetes preflight or remediation.",
            },
        }
    )


def kubernetes_run_preflight_failed_body(
    args: argparse.Namespace,
    preflight: JsonMapping,
    model_probe: JsonMapping | None,
) -> JsonMapping:
    operation = KubernetesOperation.from_args(args).to_json()
    return immutable_json(
        {
            "status": "failed",
            "operation": operation,
            "model_probe": None if model_probe is None else dict(model_probe),
            "preflight": dict(preflight),
            "run": None,
            "contract": dict(
                kubernetes_production_contract_report(
                    operation=operation,
                    model_probe=model_probe,
                    preflight=preflight,
                    run=None,
                    include_runtime=True,
                )
            ),
            "next_step": {
                "type": "fix_preflight",
                "message": "Resolve failed Kubernetes preflight checks before running remediation.",
            },
        }
    )


def kubernetes_run_next_step(
    run: RuntimeRun,
    profile_config: str | None,
) -> JsonValue:
    if run.session.pending_action is not None:
        command = ["python", "-m", "universal_agent_cli"]
        if profile_config is not None:
            command.extend(("--profile-config", profile_config))
        command.extend(("session", "resume", str(run.result.session_id), "--confirmed", "true"))
        return {
            "type": "confirm_pending_action",
            "message": (
                "Review pending_action before confirming the policy-gated Kubernetes mutation."
            ),
            "command": shlex.join(command),
        }
    if run.result.status is ExecutionStatus.FAILED:
        command = ["python", "-m", "universal_agent_cli"]
        if profile_config is not None:
            command.extend(("--profile-config", profile_config))
        command.extend(("session", "diagnostics", str(run.result.session_id)))
        return {
            "type": "inspect_failure",
            "message": "Inspect session diagnostics before retrying the Kubernetes operation.",
            "command": shlex.join(command),
        }
    return None


def kubernetes_workload_resource(workload: str) -> str:
    normalized = workload.strip()
    if not normalized:
        raise ValueError("kubernetes workload must not be empty")
    if "/" in normalized:
        return normalized
    return f"deployment/{normalized}"


def kubernetes_workload_name(workload: str) -> str:
    resource = kubernetes_workload_resource(workload)
    if "/" not in resource:
        return resource
    return resource.split("/", 1)[1]


def optional_kubernetes_namespace(namespace: str | None) -> str | None:
    if namespace is None:
        return None
    normalized = namespace.strip()
    if not normalized:
        raise ValueError("kubernetes namespace must not be empty")
    return normalized


def kubernetes_remediation_success_criteria(
    workload: str,
    namespace: str | None,
) -> tuple[SuccessCriterion, ...]:
    criteria = [
        SuccessCriterion("healthy", True),
        SuccessCriterion("resource", workload),
    ]
    if namespace is not None:
        criteria.append(SuccessCriterion("namespace", namespace))
    return tuple(criteria)


def kubernetes_initial_task_required_criteria(namespace: str | None) -> tuple[str, ...]:
    required = ["resource"]
    if namespace is not None:
        required.append("namespace")
    return tuple(required)


def kubernetes_remediation_goal_description(workload: str, namespace: str | None) -> str:
    scope = workload if namespace is None else f"{workload} in namespace {namespace}"
    return (
        f"Restore Kubernetes workload {scope} to healthy state. "
        "Inspect, diagnose, apply only policy-allowed safe remediation, "
        "and verify fresh health evidence."
    )


def kubernetes_remediation_task_description(workload: str, namespace: str | None) -> str:
    scope = workload if namespace is None else f"{workload} in namespace {namespace}"
    return f"Inspect Kubernetes workload {scope} and determine whether remediation is required."
