from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast

from universal_agent.core import (
    CapabilityCategory,
    CapabilitySummary,
    Decision,
    DecisionContext,
    DecisionType,
    Goal,
    JsonMapping,
    JsonValue,
    SessionId,
    Task,
    immutable_json,
    to_json_object,
    validate_argument_contract,
)
from universal_agent.domains.kubernetes.backend import KubernetesBackend
from universal_agent.domains.kubernetes.cli_runtime import (
    PREFLIGHT_CAPABILITIES,
    DefaultKubernetesCliBackend,
    ModelAdapterBuilder,
    configured_kubernetes_backend,
)
from universal_agent.domains.kubernetes.diagnosis import (
    view_evidence,
)
from universal_agent.domains.kubernetes.production_contract import (
    kubernetes_production_contract_report,
)
from universal_agent.domains.kubernetes.production_evidence import (
    kubernetes_production_evidence_gate_report,
)
from universal_agent.domains.kubernetes.report_views import (
    KubernetesOperation,
    _model_probe_failure_next_step,
    decision_body,
    kubernetes_check_failure_body,
    kubernetes_evidence_next_step,
    kubernetes_initial_task_required_criteria,
    kubernetes_model_config_body,
    kubernetes_remediation_goal_description,
    kubernetes_remediation_success_criteria,
    kubernetes_remediation_task_description,
    kubernetes_run_body,
    kubernetes_run_model_probe_failed_body,
    kubernetes_run_preflight_failed_body,
    kubernetes_workload_arguments,
    kubernetes_workload_name,
    kubernetes_workload_resource,
)
from universal_agent.domains.kubernetes.report_views import (
    kubernetes_run_next_step as kubernetes_run_next_step,
)
from universal_agent.domains.kubernetes.report_views import (
    optional_kubernetes_namespace as optional_kubernetes_namespace,
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
    if command == "evidence":
        evidence_report = await kubernetes_evidence_gate_report(
            args,
            service,
            model_adapter_builder=model_adapter_builder,
            backend_builder=backend_builder,
        )
        return KubernetesCliResult(
            evidence_report,
            1 if evidence_report["status"] == "failed" else 0,
        )
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
        explorer = await service.session_explorer(run.result.session_id)
        return KubernetesCliResult(
            kubernetes_run_body(
                args,
                run,
                preflight_report,
                model_probe_report,
                evidence=tuple(view_evidence(item) for item in explorer.evidence),
                environment=service.config().environment,
            )
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
    operation = KubernetesOperation.from_args(args)
    if not service.accepts_profile(operation.profile):
        raise ValueError(f"unknown profile: {operation.profile}")
    # The probe validates the model configured in the profile config file; the
    # report must reflect that model, not whatever model the serving runtime
    # happens to be built with (they differ for remote/embedded dispatch).
    profile_config_path = cast(str | None, getattr(args, "profile_config", None))
    config = (
        ProfileConfig.from_json_file(profile_config_path).to_profile().runtime
        if profile_config_path is not None
        else service.config()
    )
    context = kubernetes_model_probe_context(service, operation.workload, operation.namespace)
    try:
        model = kubernetes_model_probe_adapter(
            args,
            operation.workload,
            operation.namespace,
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
                "operation": operation.to_json(),
                "model": kubernetes_model_config_body(config.model),
                "capability_count": len(context.capabilities),
                "error": {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                },
                "next_step": _model_probe_failure_next_step(exc),
            }
        )
    return immutable_json(
        {
            "status": "ok",
            "operation": operation.to_json(),
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
    operation = KubernetesOperation.from_args(args)
    model_probe = await kubernetes_model_probe_report(
        args,
        service,
        model_adapter_builder=model_adapter_builder,
    )
    if model_probe["status"] == "failed":
        return kubernetes_check_failure_body(
            operation,
            model_probe,
            preflight=None,
            next_step={
                "type": "fix_model_provider",
                "message": "Fix model probe failure before Kubernetes preflight or remediation.",
            },
        )
    preflight = await kubernetes_preflight_report(
        args,
        service,
        backend_builder=backend_builder,
    )
    if preflight["status"] == "failed":
        return kubernetes_check_failure_body(
            operation,
            model_probe,
            preflight=preflight,
            next_step={
                "type": "fix_preflight",
                "message": "Resolve failed Kubernetes preflight checks before remediation.",
            },
        )
    operation_body = operation.to_json()
    return immutable_json(
        {
            "status": "ok",
            "operation": operation_body,
            "model_probe": dict(model_probe),
            "preflight": dict(preflight),
            "contract": dict(
                kubernetes_production_contract_report(
                    operation=operation_body,
                    model_probe=model_probe,
                    preflight=preflight,
                    run=None,
                    include_runtime=False,
                )
            ),
            "next_step": {
                "type": "run_kubernetes_remediation",
                "message": "Model and Kubernetes preflight checks passed; run remediation next.",
            },
        }
    )


async def kubernetes_evidence_gate_report(
    args: argparse.Namespace,
    service: RuntimeService,
    *,
    model_adapter_builder: ModelAdapterBuilder = build_configured_model_adapter,
    backend_builder: KubernetesPreflightBackendBuilder | None = None,
) -> JsonMapping:
    operation = KubernetesOperation.from_args(args)
    operation_body = operation.to_json()
    submit_run = cast(bool, getattr(args, "submit_run", False))
    skip_cluster = cast(bool, getattr(args, "skip_cluster", False))

    model_probe = await kubernetes_model_probe_report(
        args,
        service,
        model_adapter_builder=model_adapter_builder,
    )
    preflight: JsonMapping | None = None
    runtime_run: RuntimeRun | None = None
    run_body: dict[str, JsonValue] | None = None
    if model_probe["status"] == "ok":
        preflight = await kubernetes_preflight_report(
            args,
            service,
            backend_builder=backend_builder,
        )
        if submit_run and preflight["status"] == "ok":
            runtime_run = await run_kubernetes_remediation(args, service)
            run_body = to_json_object(runtime_run, fallback_to_string=True)

    contract = kubernetes_production_contract_report(
        operation=operation_body,
        model_probe=model_probe,
        preflight=preflight,
        run=run_body,
        include_runtime=submit_run,
    )
    gate = kubernetes_production_evidence_gate_report(
        model_probe=model_probe,
        preflight=preflight,
        run=run_body,
        contract=contract,
        submit_run=submit_run,
        skip_cluster=skip_cluster,
    )
    return immutable_json(
        {
            "status": gate["status"],
            "passed": gate["passed"],
            "operation": operation_body,
            "model_probe": dict(model_probe),
            "preflight": None if preflight is None else dict(preflight),
            "run": run_body,
            "contract": dict(contract),
            "evidence_gate": dict(gate),
            "next_step": kubernetes_evidence_next_step(
                args,
                model_probe,
                preflight,
                runtime_run,
                submit_run=submit_run,
            ),
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
        kubernetes_initial_task_required_criteria(namespace),
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
    # The probe invokes the model directly, so missing credentials must fail
    # fast here at the command boundary (the runtime itself assembles lazily
    # so read-only surfaces can serve without secrets — UA-LIVE-2026-09-21 P1).
    from universal_agent.host import model_credentials_missing_reason

    # Credential fail-fast applies only when the effective adapter is the real
    # configured one. A caller-supplied adapter (e.g. tests injecting a fake
    # model, or a transport-backed model) already takes responsibility for
    # resolution, so the env-credential guard must not bypass it and spuriously
    # fail the probe (UA-LIVE-2026-09-21 P1).
    if model_adapter_builder is build_configured_model_adapter:
        missing = model_credentials_missing_reason(profile.runtime, EnvSecretProvider())
        if missing is not None:
            raise ValueError(missing)
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
    if not secret.available or secret.status in {"missing_required", "missing_optional"}:
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
    operation = KubernetesOperation.from_args(args)
    if not service.accepts_profile(operation.profile):
        raise ValueError(f"unknown profile: {operation.profile}")
    criteria = kubernetes_remediation_success_criteria(operation.workload, operation.namespace)
    goal = Goal(
        kubernetes_remediation_goal_description(operation.workload, operation.namespace),
        criteria,
    )
    task = Task(
        kubernetes_remediation_task_description(operation.workload, operation.namespace),
        kubernetes_initial_task_required_criteria(operation.namespace),
    )
    return await service.run_goal(goal, task, read_only=dry_run_requested(args))


def dry_run_requested(args: argparse.Namespace) -> bool:
    """Whether the operator asked for a read-only dry run (spec P1 section 13)."""
    return bool(getattr(args, "dry_run", False))
