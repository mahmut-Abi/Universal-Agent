from __future__ import annotations

import argparse
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast

from universal_agent.agentd.representations import runtime_run_body
from universal_agent.core import (
    CapabilityCategory,
    CapabilitySummary,
    Decision,
    DecisionContext,
    DecisionType,
    ExecutionStatus,
    Goal,
    JsonMapping,
    JsonValue,
    SessionId,
    SuccessCriterion,
    Task,
    immutable_json,
    validate_argument_contract,
)
from universal_agent.domains.kubernetes.backend import KubernetesBackend
from universal_agent.domains.kubernetes.cli_runtime import (
    PREFLIGHT_CAPABILITIES,
    DefaultKubernetesCliBackend,
    ModelAdapterBuilder,
    configured_kubernetes_backend,
)
from universal_agent.host import build_configured_model_adapter
from universal_agent.model import ModelAdapter, ScriptedModelAdapter
from universal_agent.profile import ProfileConfig
from universal_agent.runtime import RuntimeRun
from universal_agent.security import EnvSecretProvider
from universal_agent.service import RuntimeConfigDomainView, RuntimeConfigView, RuntimeService


@dataclass(frozen=True, slots=True)
class KubernetesCliResult:
    payload: JsonMapping
    status: int = 0


KubernetesPreflightBackendBuilder = Callable[[str | None], KubernetesBackend]


async def dispatch_kubernetes(
    args: argparse.Namespace,
    service: RuntimeService,
    *,
    model_adapter_builder: ModelAdapterBuilder = build_configured_model_adapter,
    preflight_backend_builder: KubernetesPreflightBackendBuilder | None = None,
) -> KubernetesCliResult:
    backend_builder = preflight_backend_builder or kubernetes_preflight_backend
    command = cast(str, args.kubernetes_command)
    if command == "preflight":
        standalone_preflight = await kubernetes_preflight_report(
            args,
            service,
            backend_builder=backend_builder,
        )
        return KubernetesCliResult(
            standalone_preflight,
            1 if standalone_preflight["status"] == "failed" else 0,
        )
    if command == "model-probe":
        model_probe = await kubernetes_model_probe_report(
            args,
            service,
            model_adapter_builder=model_adapter_builder,
        )
        return KubernetesCliResult(model_probe, 1 if model_probe["status"] == "failed" else 0)
    if command == "check":
        check = await kubernetes_check_report(
            args,
            service,
            model_adapter_builder=model_adapter_builder,
            backend_builder=backend_builder,
        )
        return KubernetesCliResult(check, 1 if check["status"] == "failed" else 0)
    if command == "run":
        model_probe_report: JsonMapping | None = None
        preflight_report: JsonMapping | None = None
        if not cast(bool, args.skip_preflight):
            if not cast(bool, args.skip_model_probe):
                model_probe_report = await kubernetes_model_probe_report(
                    args,
                    service,
                    model_adapter_builder=model_adapter_builder,
                )
                if model_probe_report["status"] == "failed":
                    return KubernetesCliResult(
                        kubernetes_run_model_probe_failed_body(args, model_probe_report),
                        1,
                    )
            preflight_report = await kubernetes_preflight_report(
                args,
                service,
                backend_builder=backend_builder,
            )
            if preflight_report["status"] == "failed":
                return KubernetesCliResult(
                    kubernetes_run_preflight_failed_body(
                        args,
                        preflight_report,
                        model_probe_report,
                    ),
                    1,
                )
        run = await run_kubernetes_remediation(args, service)
        return KubernetesCliResult(
            kubernetes_run_body(args, run, preflight_report, model_probe_report)
        )
    raise ValueError(f"unknown kubernetes command: {command}")


async def kubernetes_preflight_report(
    args: argparse.Namespace,
    service: RuntimeService,
    *,
    backend_builder: KubernetesPreflightBackendBuilder | None = None,
) -> JsonMapping:
    build_backend = backend_builder or kubernetes_preflight_backend
    config = service.config()
    checks: list[JsonValue] = []
    observations: dict[str, JsonValue] = {}
    profile_config = cast(str | None, args.profile_config)
    domain = primary_kubernetes_config_domain(config.domains)
    append_preflight_check(
        checks,
        "kubernetes_domain",
        "ok" if domain is not None else "failed",
        "kubernetes domain is active" if domain is not None else "kubernetes domain is not active",
    )
    backend_name = "unknown" if domain is None else domain.backend or "fake"
    if backend_name == "fake":
        append_preflight_check(
            checks,
            "kubernetes_backend",
            "warn",
            "fake backend is active; no real cluster will be contacted",
            {"backend": backend_name},
        )
    else:
        append_preflight_check(
            checks,
            "kubernetes_backend",
            "ok",
            "real Kubernetes backend is configured",
            {"backend": backend_name},
        )
    append_model_secret_preflight_check(checks, config)
    append_capability_preflight_check(checks, service)

    if domain is not None and not cast(bool, args.skip_cluster):
        backend = build_backend(profile_config)
        await append_backend_observation_check(
            checks,
            observations,
            backend,
            "cluster_inspection",
            "inspect_cluster",
            immutable_json(),
        )
        workload = cast(str | None, args.workload)
        if workload is not None:
            await append_backend_observation_check(
                checks,
                observations,
                backend,
                "workload_inspection",
                "inspect_workload",
                kubernetes_workload_arguments(workload, cast(str | None, args.namespace)),
            )
    elif cast(bool, args.skip_cluster):
        append_preflight_check(
            checks,
            "cluster_inspection",
            "skipped",
            "cluster inspection skipped by request",
        )

    status = "failed" if preflight_failed(checks) else "ok"
    domain_body: dict[str, JsonValue] | None = None
    if domain is not None:
        domain_body = {
            "name": domain.name,
            "version": domain.version,
            "backend": domain.backend or "fake",
            "settings": dict(domain.settings),
        }
    return immutable_json(
        {
            "status": status,
            "profile_config": profile_config or "",
            "domain": domain_body,
            "model": {
                "provider": config.model.provider,
                "name": config.model.name,
                "api_key_secret": config.model.api_key_secret or "",
            },
            "checks": checks,
            "observations": observations,
        }
    )


async def kubernetes_model_probe_report(
    args: argparse.Namespace,
    service: RuntimeService,
    *,
    model_adapter_builder: ModelAdapterBuilder = build_configured_model_adapter,
) -> JsonMapping:
    profile = cast(str, args.profile)
    if not service.accepts_profile(profile):
        raise ValueError(f"unknown profile: {profile}")
    workload = kubernetes_workload_resource(cast(str, args.workload))
    namespace = optional_kubernetes_namespace(cast(str | None, args.namespace))
    config = service.config()
    context = kubernetes_model_probe_context(service, workload, namespace)
    try:
        model = kubernetes_model_probe_adapter(
            args,
            workload,
            namespace,
            model_adapter_builder=model_adapter_builder,
        )
        decision = await model.decide(context)
        decision.validate()
        validation_error = validate_probe_decision(decision, context)
        if validation_error is not None:
            raise ValueError(validation_error)
    except Exception as exc:
        return immutable_json(
            {
                "status": "failed",
                "operation": kubernetes_operation_body(profile, workload, namespace),
                "model": kubernetes_model_config_body(config.model),
                "capability_count": len(context.capabilities),
                "error": {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                },
                "next_step": {
                    "type": "fix_model_provider",
                    "message": (
                        "Fix the profile model provider, credentials, response_format, "
                        "or returned Decision JSON before running Kubernetes remediation."
                    ),
                },
            }
        )
    return immutable_json(
        {
            "status": "ok",
            "operation": kubernetes_operation_body(profile, workload, namespace),
            "model": kubernetes_model_config_body(config.model),
            "capability_count": len(context.capabilities),
            "decision": decision_body(decision),
            "next_step": {
                "type": "run_kubernetes_preflight",
                "message": "Model probe passed; run Kubernetes preflight before remediation.",
            },
        }
    )


async def kubernetes_check_report(
    args: argparse.Namespace,
    service: RuntimeService,
    *,
    model_adapter_builder: ModelAdapterBuilder = build_configured_model_adapter,
    backend_builder: KubernetesPreflightBackendBuilder | None = None,
) -> JsonMapping:
    profile = cast(str, args.profile)
    workload = kubernetes_workload_resource(cast(str, args.workload))
    namespace = optional_kubernetes_namespace(cast(str | None, args.namespace))
    model_probe = await kubernetes_model_probe_report(
        args,
        service,
        model_adapter_builder=model_adapter_builder,
    )
    if model_probe["status"] == "failed":
        return immutable_json(
            {
                "status": "failed",
                "operation": kubernetes_operation_body(profile, workload, namespace),
                "model_probe": dict(model_probe),
                "preflight": None,
                "next_step": {
                    "type": "fix_model_provider",
                    "message": (
                        "Fix model probe failure before Kubernetes preflight or remediation."
                    ),
                },
            }
        )
    preflight = await kubernetes_preflight_report(
        args,
        service,
        backend_builder=backend_builder,
    )
    if preflight["status"] == "failed":
        return immutable_json(
            {
                "status": "failed",
                "operation": kubernetes_operation_body(profile, workload, namespace),
                "model_probe": dict(model_probe),
                "preflight": dict(preflight),
                "next_step": {
                    "type": "fix_preflight",
                    "message": "Resolve failed Kubernetes preflight checks before remediation.",
                },
            }
        )
    return immutable_json(
        {
            "status": "ok",
            "operation": kubernetes_operation_body(profile, workload, namespace),
            "model_probe": dict(model_probe),
            "preflight": dict(preflight),
            "next_step": {
                "type": "run_kubernetes_remediation",
                "message": "Model and Kubernetes preflight checks passed; run remediation next.",
            },
        }
    )


def kubernetes_model_probe_context(
    service: RuntimeService,
    workload: str,
    namespace: str | None,
) -> DecisionContext:
    goal = Goal(
        kubernetes_remediation_goal_description(workload, namespace),
        kubernetes_remediation_success_criteria(workload, namespace),
    )
    task = Task(
        kubernetes_remediation_task_description(workload, namespace),
        tuple(item.key for item in goal.success_criteria),
    )
    return DecisionContext(
        session_id=SessionId("probe-session"),
        goal_id=goal.id,
        goal_description=goal.description,
        task_id=task.id,
        task_description=task.description,
        iteration=1,
        satisfied_criteria=immutable_json(),
        latest_observation=None,
        capabilities=kubernetes_probe_capabilities(service),
        goal_success_criteria=goal.success_criteria,
        current_task_required_criteria=task.required_criteria,
        policy_summary=tuple(policy.description for policy in service.policies()),
    )


def kubernetes_probe_capabilities(service: RuntimeService) -> tuple[CapabilitySummary, ...]:
    return tuple(
        CapabilitySummary(
            capability.name,
            capability.description,
            capability.category,
            capability.risk,
            required_arguments=capability.required_arguments,
            argument_schema=capability.argument_schema,
        )
        for capability in service.capabilities()
        if capability.domain_name == "kubernetes"
    )


def kubernetes_model_probe_adapter(
    args: argparse.Namespace,
    workload: str,
    namespace: str | None,
    *,
    model_adapter_builder: ModelAdapterBuilder = build_configured_model_adapter,
) -> ModelAdapter:
    profile_config = cast(str | None, args.profile_config)
    scripted = (kubernetes_probe_decision(workload, namespace),)
    if profile_config is None:
        return ScriptedModelAdapter(scripted)
    profile = ProfileConfig.from_json_file(profile_config).to_profile()
    return model_adapter_builder(
        profile.runtime,
        scripted_decisions=scripted,
        secret_provider=EnvSecretProvider(),
    )


def kubernetes_probe_decision(workload: str, namespace: str | None) -> Decision:
    arguments: dict[str, JsonValue] = {"name": kubernetes_workload_name(workload)}
    expected_observations: list[str] = ["healthy", "resource"]
    if namespace is not None:
        arguments["namespace"] = namespace
        expected_observations.append("namespace")
    return Decision(
        DecisionType.EXECUTE,
        "Probe Kubernetes model decision contract with workload inspection.",
        capability="inspect_workload",
        target=workload,
        arguments=immutable_json(arguments),
        expected_observations=tuple(expected_observations),
    )


def validate_probe_decision(decision: Decision, context: DecisionContext) -> str | None:
    if decision.type is not DecisionType.EXECUTE:
        return "Kubernetes model probe must return an execute Decision"
    capability = decision.capability or ""
    capabilities = {item.name: item for item in context.capabilities}
    summary = capabilities.get(capability)
    if summary is None:
        return f"capability is not available in Kubernetes probe context: {capability}"
    if summary.category is not CapabilityCategory.OBSERVATION:
        return "Kubernetes model probe must use a read-only inspection capability"
    if capability != "inspect_workload":
        return "Kubernetes model probe must start with inspect_workload"
    argument_error = validate_argument_contract(
        required_arguments=summary.required_arguments,
        argument_schema=summary.argument_schema,
        arguments=decision.arguments,
    )
    if argument_error is not None:
        return f"arguments for capability {capability}: {argument_error}"
    expected_workload = expected_success_criterion(context, "resource")
    if expected_workload is not None:
        target = decision.target
        if isinstance(target, str) and target.strip():
            target_resource = normal_probe_workload(target, "target")
            if target_resource != expected_workload:
                return (
                    "Kubernetes model probe target is outside the requested workload scope: "
                    f"{target_resource}"
                )
        name = decision.arguments.get("name")
        if not isinstance(name, str) or not name.strip():
            return "Kubernetes model probe inspect_workload requires a name argument"
        argument_resource = normal_probe_workload(name, "arguments.name")
        if argument_resource != expected_workload:
            return (
                "Kubernetes model probe name is outside the requested workload scope: "
                f"{argument_resource}"
            )
    expected_namespace = expected_success_criterion(context, "namespace")
    if expected_namespace is not None:
        namespace = decision.arguments.get("namespace")
        if namespace != expected_namespace:
            return (
                "Kubernetes model probe namespace is outside the requested workload scope: "
                f"{namespace}"
            )
    return None


def expected_success_criterion(context: DecisionContext, key: str) -> str | None:
    for criterion in context.goal_success_criteria:
        if criterion.key != key:
            continue
        if isinstance(criterion.expected, str) and criterion.expected.strip():
            return criterion.expected.strip()
    return None


def normal_probe_workload(value: str, field_name: str) -> str:
    try:
        return kubernetes_workload_resource(value)
    except ValueError as exc:
        raise ValueError(f"Kubernetes model probe {field_name} is invalid: {exc}") from exc


def kubernetes_operation_body(
    profile: str,
    workload: str,
    namespace: str | None,
) -> dict[str, JsonValue]:
    return {
        "profile": profile,
        "workload": workload,
        "namespace": namespace or "",
    }


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
            float(timeout_seconds) if isinstance(timeout_seconds, int | float) else 30.0
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


def primary_kubernetes_config_domain(
    domains: tuple[RuntimeConfigDomainView, ...],
) -> RuntimeConfigDomainView | None:
    for domain in domains:
        if domain.name == "kubernetes" and domain.primary:
            return domain
    for domain in domains:
        if domain.name == "kubernetes":
            return domain
    return None


def append_model_secret_preflight_check(
    checks: list[JsonValue],
    config: RuntimeConfigView,
) -> None:
    secret_name = config.model.api_key_secret
    if secret_name is None:
        append_preflight_check(
            checks,
            "model_secret",
            "ok",
            "model provider does not require an API key secret",
            {"provider": config.model.provider},
        )
        return
    secret = next((item for item in config.secrets if item.name == secret_name), None)
    if secret is None:
        append_preflight_check(
            checks,
            "model_secret",
            "failed",
            "model api_key_secret is not declared",
            {"secret": secret_name},
        )
        return
    if secret.available is False or secret.status in {"missing_required", "missing_optional"}:
        append_preflight_check(
            checks,
            "model_secret",
            "failed" if secret.required else "warn",
            "model API key secret is unavailable",
            {"secret": secret.name, "status": secret.status or "unknown"},
        )
        return
    append_preflight_check(
        checks,
        "model_secret",
        "ok",
        "model API key secret is available",
        {"secret": secret.name, "source": secret.source},
    )


def append_capability_preflight_check(
    checks: list[JsonValue],
    service: RuntimeService,
) -> None:
    available = {
        capability.name
        for capability in service.capabilities()
        if capability.domain_name == "kubernetes"
    }
    missing = tuple(
        capability for capability in PREFLIGHT_CAPABILITIES if capability not in available
    )
    missing_values: list[JsonValue] = list(missing)
    available_values: list[JsonValue] = list(sorted(available))
    append_preflight_check(
        checks,
        "kubernetes_capabilities",
        "ok" if not missing else "failed",
        "kubernetes runtime exposes expected capabilities"
        if not missing
        else "kubernetes runtime is missing expected capabilities",
        {"missing": missing_values, "available": available_values},
    )


def kubernetes_preflight_backend(profile_config: str | None) -> KubernetesBackend:
    if profile_config is None:
        return cast(KubernetesBackend, DefaultKubernetesCliBackend())
    profile = ProfileConfig.from_json_file(profile_config).to_profile()
    return cast(
        KubernetesBackend,
        configured_kubernetes_backend(
            profile.runtime.configured_domains() or (profile.domain,),
            config=profile.runtime,
            secret_provider=EnvSecretProvider(),
        ),
    )


async def append_backend_observation_check(
    checks: list[JsonValue],
    observations: dict[str, JsonValue],
    backend: KubernetesBackend,
    check_name: str,
    capability: str,
    arguments: JsonMapping,
) -> None:
    try:
        observation = await backend.inspect(capability, arguments)
    except Exception as exc:
        append_preflight_check(
            checks,
            check_name,
            "failed",
            f"{capability} failed",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
        )
        return
    observations[check_name] = dict(observation)
    append_preflight_check(
        checks,
        check_name,
        "ok",
        f"{capability} succeeded",
        {"resource": str(observation.get("resource", ""))},
    )


def kubernetes_workload_arguments(workload: str, namespace: str | None) -> JsonMapping:
    if not workload.strip():
        raise ValueError("kubernetes preflight workload must not be empty")
    arguments: dict[str, JsonValue] = {"name": workload}
    if namespace is not None:
        if not namespace.strip():
            raise ValueError("kubernetes preflight namespace must not be empty")
        arguments["namespace"] = namespace
    return immutable_json(arguments)


def append_preflight_check(
    checks: list[JsonValue],
    name: str,
    status: str,
    message: str,
    details: Mapping[str, JsonValue] | None = None,
) -> None:
    body: dict[str, JsonValue] = {
        "name": name,
        "status": status,
        "message": message,
    }
    if details is not None:
        body["details"] = dict(details)
    checks.append(body)


def preflight_failed(checks: list[JsonValue]) -> bool:
    for check in checks:
        if isinstance(check, Mapping) and check.get("status") == "failed":
            return True
    return False


async def run_kubernetes_remediation(
    args: argparse.Namespace,
    service: RuntimeService,
) -> RuntimeRun:
    profile = cast(str, args.profile)
    if not service.accepts_profile(profile):
        raise ValueError(f"unknown profile: {profile}")
    workload = kubernetes_workload_resource(cast(str, args.workload))
    namespace = optional_kubernetes_namespace(cast(str | None, args.namespace))
    criteria = kubernetes_remediation_success_criteria(workload, namespace)
    goal = Goal(
        kubernetes_remediation_goal_description(workload, namespace),
        criteria,
    )
    task = Task(
        kubernetes_remediation_task_description(workload, namespace),
        tuple(item.key for item in criteria),
    )
    return await service.run_goal(goal, task)


def kubernetes_run_body(
    args: argparse.Namespace,
    run: RuntimeRun,
    preflight: JsonMapping | None,
    model_probe: JsonMapping | None,
) -> JsonMapping:
    profile_config = cast(str | None, args.profile_config)
    return immutable_json(
        {
            "status": run.result.status.value,
            "operation": {
                "profile": cast(str, args.profile),
                "workload": kubernetes_workload_resource(cast(str, args.workload)),
                "namespace": optional_kubernetes_namespace(cast(str | None, args.namespace)) or "",
            },
            "model_probe": None if model_probe is None else dict(model_probe),
            "preflight": None if preflight is None else dict(preflight),
            "run": dict(runtime_run_body(run)),
            "next_step": kubernetes_run_next_step(run, profile_config),
        }
    )


def kubernetes_run_model_probe_failed_body(
    args: argparse.Namespace,
    model_probe: JsonMapping,
) -> JsonMapping:
    return immutable_json(
        {
            "status": "failed",
            "operation": {
                "profile": cast(str, args.profile),
                "workload": kubernetes_workload_resource(cast(str, args.workload)),
                "namespace": optional_kubernetes_namespace(cast(str | None, args.namespace)) or "",
            },
            "model_probe": dict(model_probe),
            "preflight": None,
            "run": None,
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
    return immutable_json(
        {
            "status": "failed",
            "operation": {
                "profile": cast(str, args.profile),
                "workload": kubernetes_workload_resource(cast(str, args.workload)),
                "namespace": optional_kubernetes_namespace(cast(str | None, args.namespace)) or "",
            },
            "model_probe": None if model_probe is None else dict(model_probe),
            "preflight": dict(preflight),
            "run": None,
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
        command = ["python", "-m", "universal_agent.cli"]
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
        command = ["python", "-m", "universal_agent.cli"]
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
