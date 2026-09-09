"""Unit coverage for the kubernetes CLI report dispatch internals.

These exercise the model-probe failure paths (out-of-scope decisions, missing
model secrets) directly against the kernel dispatch functions — they cannot go
through the HTTP surface because the faulty model is injected at the host
boundary, not through the API.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
from pathlib import Path

from universal_agent import (
    AgentRuntime,
    InMemoryEventSink,
    RuntimeAPI,
    RuntimeService,
)
from universal_agent.core import (
    Decision,
    DecisionType,
    JsonMapping,
    JsonValue,
    immutable_json,
)
from universal_agent.domain import DomainLoader, RuntimeBuilder
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.domains.kubernetes.cli_reports import (
    KubernetesCliResult,
    dispatch_kubernetes,
    kubernetes_model_probe_report,
)
from universal_agent.domains.kubernetes.cli_runtime import ModelAdapterBuilder, default_profile
from universal_agent.model import ModelAdapter, ScriptedModelAdapter
from universal_agent.state import InMemoryStateStore


class Backend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        body: dict[str, JsonValue] = {
            "resource": "deployment/example",
            "kind": "Deployment",
            "healthy": True,
        }
        namespace = arguments.get("namespace")
        if isinstance(namespace, str):
            body["namespace"] = namespace
        return immutable_json(body)

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"resource": "deployment/example", "scaled": True})


def build_service(decisions: tuple[Decision, ...] = ()) -> RuntimeService:
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(Backend(), Backend()))
    )
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=store,
        components=components,
        event_sink=events,
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
        profiles=(default_profile(),),
    )


def probe_args(profile_config: str | None) -> argparse.Namespace:
    return argparse.Namespace(
        kubernetes_command="model-probe",
        profile="local-kubernetes",
        profile_config=profile_config,
        workload="deployment/api",
        namespace="prod",
        skip_preflight=False,
        skip_model_probe=False,
        skip_cluster=False,
    )


def run_body_args(profile_config: str) -> argparse.Namespace:
    return argparse.Namespace(
        kubernetes_command="run",
        profile="local-kubernetes",
        profile_config=profile_config,
        workload="deployment/api",
        namespace="prod",
        skip_preflight=False,
        skip_model_probe=False,
        skip_cluster=False,
    )


def evidence_args(*, submit_run: bool = False, skip_cluster: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        kubernetes_command="evidence",
        profile="local-kubernetes",
        profile_config=None,
        workload="deployment/example",
        namespace="prod",
        skip_preflight=False,
        skip_model_probe=False,
        skip_cluster=skip_cluster,
        submit_run=submit_run,
    )


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Health verified")


def scoped_inspection() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect requested workload.",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "prod"}),
        expected_observations=("healthy", "resource", "namespace"),
    )


def out_of_scope_model() -> ModelAdapter:
    bad_decision = Decision(
        DecisionType.EXECUTE,
        "Return a valid but out-of-scope workload inspection.",
        capability="inspect_workload",
        target="deployment/other",
        arguments=immutable_json({"name": "other", "namespace": "prod"}),
        expected_observations=("healthy", "resource", "namespace"),
    )
    return ScriptedModelAdapter([bad_decision])


def test_model_probe_rejects_out_of_scope_decision(tmp_path: Path) -> None:
    import json

    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "name": "production-operator",
                "version": "1.0.0",
                "domain": {"name": "kubernetes", "version": "0.2.0"},
                "runtime": {"domain": {"name": "kubernetes", "version": "0.2.0"}},
            }
        ),
        encoding="utf-8",
    )
    payload = asyncio_run_probe(
        str(profile_path), model_adapter_builder=lambda *a, **k: out_of_scope_model()
    )

    assert payload["status"] == "failed"
    error = _mapping(payload["error"])
    next_step = _mapping(payload["next_step"])
    assert error["type"] == "ValueError"
    assert "target is outside the requested workload scope" in str(error["message"])
    assert next_step["type"] == "fix_model_provider"


def test_run_stops_before_preflight_when_model_probe_fails(tmp_path: Path) -> None:
    import json

    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "name": "production-operator",
                "version": "1.0.0",
                "domain": {"name": "kubernetes", "version": "0.2.0"},
                "runtime": {"domain": {"name": "kubernetes", "version": "0.2.0"}},
            }
        ),
        encoding="utf-8",
    )
    service = build_service()
    args = run_body_args(str(profile_path))
    result = asyncio_run_dispatch(
        args, service, model_adapter_builder=lambda *a, **k: out_of_scope_model()
    )

    assert result.status == 1
    payload = result.payload
    assert payload["status"] == "failed"
    model_probe = _mapping(payload["model_probe"])
    assert model_probe["status"] == "failed"
    assert payload["preflight"] is None
    assert payload["run"] is None
    next_step = _mapping(payload["next_step"])
    assert next_step["type"] == "fix_model_provider"


def test_evidence_gate_collects_pre_run_contract_without_runtime_submission() -> None:
    service = build_service()
    result = asyncio_run_dispatch(
        evidence_args(),
        service,
        model_adapter_builder=lambda *a, **k: ScriptedModelAdapter(()),
    )

    assert result.status == 0
    payload = result.payload
    assert payload["status"] == "attention"
    assert payload["passed"] is False
    assert payload["run"] is None
    contract = _mapping(payload["contract"])
    assert contract["status"] == "attention"
    gate = _mapping(payload["evidence_gate"])
    assert gate["evidence_level"] == "local_or_fixture"
    assert gate["live_runtime_path_observed"] is False
    next_step = _mapping(payload["next_step"])
    assert next_step["type"] == "submit_runtime_run"


def test_evidence_gate_can_submit_runtime_run() -> None:
    service = build_service((scoped_inspection(), finish()))
    result = asyncio_run_dispatch(
        evidence_args(submit_run=True),
        service,
        model_adapter_builder=lambda *a, **k: ScriptedModelAdapter(()),
    )

    assert result.status == 0
    payload = result.payload
    assert payload["status"] == "attention"
    assert payload["run"] is not None
    run = _mapping(payload["run"])
    run_result = _mapping(run["result"])
    assert run_result["status"] == "completed"
    gate = _mapping(payload["evidence_gate"])
    assert gate["live_runtime_path_observed"] is False
    assert payload["next_step"] is None


def asyncio_run_probe(
    profile_config: str,
    model_adapter_builder: ModelAdapterBuilder,
) -> dict[str, object]:
    async def main() -> dict[str, object]:
        service = build_service()

        payload = await kubernetes_model_probe_report(
            probe_args(profile_config),
            service,
            model_adapter_builder=model_adapter_builder,
        )
        assert isinstance(payload, Mapping)
        return dict(payload)

    return asyncio.run(main())


def asyncio_run_dispatch(
    args: argparse.Namespace,
    service: RuntimeService,
    model_adapter_builder: ModelAdapterBuilder,
) -> KubernetesCliResult:
    async def main() -> KubernetesCliResult:
        return await dispatch_kubernetes(args, service, model_adapter_builder=model_adapter_builder)

    return asyncio.run(main())
