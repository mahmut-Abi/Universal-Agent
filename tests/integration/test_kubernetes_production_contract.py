from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from universal_agent.core import JsonMapping, JsonValue, immutable_json
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.domains.kubernetes.api import (
    KubernetesApiBackend,
    KubernetesApiResponse,
)
from universal_agent.domains.kubernetes.cli_reports import dispatch_kubernetes
from universal_agent.host import (
    DomainConfig,
    ModelConfig,
    RuntimeConfig,
    RuntimeHost,
    RuntimeLimitsConfig,
    SecretRef,
    StoreConfig,
)
from universal_agent.model import OpenAIChatCompletionsModelAdapter
from universal_agent.profile import AgentProfile
from universal_agent.security import EnvSecretProvider
from universal_agent.service import RuntimeService


@dataclass(frozen=True, slots=True)
class ModelRequest:
    url: str
    headers: Mapping[str, str]
    payload: JsonMapping
    timeout_seconds: float


class QueuedOpenAIChatTransport:
    def __init__(self, decisions: tuple[JsonMapping, ...]) -> None:
        self._decisions = list(decisions)
        self.requests: list[ModelRequest] = []

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping:
        self.requests.append(ModelRequest(url, dict(headers), payload, timeout_seconds))
        if not self._decisions:
            raise AssertionError("model transport received an unexpected request")
        decision = self._decisions.pop(0)
        return immutable_json(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(dict(decision), sort_keys=True),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 128, "completion_tokens": 32},
            }
        )


@dataclass(frozen=True, slots=True)
class KubernetesRequest:
    method: str
    path: str
    query: Mapping[str, str]
    body: JsonMapping | None
    headers: Mapping[str, str]
    timeout_seconds: float | None


class FixtureKubernetesApiTransport:
    def __init__(self) -> None:
        self.requests: list[KubernetesRequest] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: JsonMapping | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> KubernetesApiResponse:
        self.requests.append(
            KubernetesRequest(
                method,
                path,
                dict(query or {}),
                body,
                dict(headers or {}),
                timeout_seconds,
            )
        )
        if method == "GET" and path == "/api/v1/nodes":
            return KubernetesApiResponse(200, {"items": [_ready_node()]})
        if method == "GET" and path == "/api/v1/namespaces":
            return KubernetesApiResponse(200, {"items": [_namespace("prod")]})
        if (
            method == "GET"
            and path == "/apis/apps/v1/namespaces/prod/deployments/api"
        ):
            return KubernetesApiResponse(200, _healthy_deployment())
        return KubernetesApiResponse(
            404,
            text=f"unexpected Kubernetes fixture request: {method} {path}",
        )


@pytest.mark.asyncio
async def test_kubernetes_production_contract_runs_openai_chat_and_api_preflight(
    tmp_path: Path,
) -> None:
    profile_path = _write_profile(tmp_path / "production.profile.json")
    model_transport = QueuedOpenAIChatTransport(
        (
            _inspect_workload_decision(),
            _inspect_workload_decision(),
            _inspect_workload_decision(),
            _finish_decision(),
        )
    )
    kubernetes_transport = FixtureKubernetesApiTransport()
    backend = KubernetesApiBackend(
        api_server="https://cluster.example.test",
        bearer_token="fixture-token",
        transport=kubernetes_transport,
        default_namespace="prod",
    )
    service = _runtime_service(model_transport, backend)

    check = await dispatch_kubernetes(
        _args("check", profile_config=profile_path),
        service,
        model_adapter_builder=lambda *args, **kwargs: _chat_model(model_transport),
        preflight_backend_builder=lambda profile_config: backend,
    )
    run = await dispatch_kubernetes(
        _args("run", profile_config=profile_path),
        service,
        model_adapter_builder=lambda *args, **kwargs: _chat_model(model_transport),
        preflight_backend_builder=lambda profile_config: backend,
    )

    assert check.status == 0
    assert check.payload["status"] == "ok"
    check_model_probe = _object(check.payload["model_probe"])
    check_decision = _object(check_model_probe["decision"])
    check_preflight = _object(check.payload["preflight"])
    check_observations = _object(check_preflight["observations"])
    check_workload = _object(check_observations["workload_inspection"])
    assert check_decision["capability"] == "inspect_workload"
    assert check_workload["healthy"] is True
    assert run.status == 0
    assert run.payload["status"] == "completed"
    run_model_probe = _object(run.payload["model_probe"])
    run_preflight = _object(run.payload["preflight"])
    run_body = _object(run.payload["run"])
    run_result = _object(run_body["result"])
    run_session = _object(run_body["session"])
    assert run_model_probe["status"] == "ok"
    assert run_preflight["status"] == "ok"
    assert run_result["status"] == "completed"
    assert run_session["satisfied_criteria"] == {
        "healthy": True,
        "namespace": "prod",
        "resource": "deployment/api",
    }
    assert all("response_format" not in request.payload for request in model_transport.requests)
    assert [request.path for request in kubernetes_transport.requests].count(
        "/apis/apps/v1/namespaces/prod/deployments/api"
    ) == 3


def _runtime_service(
    model_transport: QueuedOpenAIChatTransport,
    backend: KubernetesApiBackend,
) -> RuntimeService:
    domain = DomainConfig(
        "kubernetes",
        "0.2.0",
        "kubernetes_api",
        immutable_json(
            {
                "api_server": "https://cluster.example.test",
                "default_namespace": "prod",
                "bearer_token_secret": "kubernetes_api_token",
            }
        ),
    )
    config = RuntimeConfig(
        environment=immutable_json({"environment": "production"}),
        secrets=(
            SecretRef.env("openai_api_key", "OPENAI_API_KEY"),
            SecretRef.env("kubernetes_api_token", "KUBERNETES_API_TOKEN"),
        ),
        model=ModelConfig.openai_chat_completions(
            name="gpt-runtime",
            api_key_secret="openai_api_key",
            endpoint="https://models.example.test/v1/chat/completions",
            response_format="prompt_json",
        ),
        store=StoreConfig.memory(),
        limits=RuntimeLimitsConfig(max_iterations=4, max_recovery_steps=2),
        domain=domain,
    )
    profile = AgentProfile(
        "production-operator",
        "1.0.0",
        "Production Kubernetes operator",
        domain,
        config,
        (domain,),
    )
    host = RuntimeHost.from_profile(
        profile=profile,
        model=_chat_model(model_transport),
        domain=KubernetesRemediationDomain(backend, backend),
        secret_provider=EnvSecretProvider(
            {
                "OPENAI_API_KEY": "fixture-openai-key",
                "KUBERNETES_API_TOKEN": "fixture-kubernetes-token",
            }
        ),
    )
    return host.service


def _chat_model(model_transport: QueuedOpenAIChatTransport) -> OpenAIChatCompletionsModelAdapter:
    return OpenAIChatCompletionsModelAdapter(
        "gpt-runtime",
        api_key="fixture-openai-key",
        endpoint="https://models.example.test/v1/chat/completions",
        response_format="prompt_json",
        transport=model_transport,
    )


def _args(command: str, *, profile_config: Path) -> argparse.Namespace:
    return argparse.Namespace(
        profile_config=str(profile_config),
        kubernetes_command=command,
        profile="production-operator",
        workload="deployment/api",
        namespace="prod",
        skip_cluster=False,
        skip_preflight=False,
        skip_model_probe=False,
    )


def _write_profile(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "name": "production-operator",
                "version": "1.0.0",
                "description": "Production Kubernetes operator",
                "domain": _domain_payload(),
                "runtime": {
                    "environment": {"environment": "production"},
                    "secrets": {
                        "openai_api_key": {
                            "source": "env",
                            "key": "OPENAI_API_KEY",
                            "required": True,
                        },
                        "kubernetes_api_token": {
                            "source": "env",
                            "key": "KUBERNETES_API_TOKEN",
                            "required": True,
                        },
                    },
                    "model": {
                        "provider": "openai_chat_completions",
                        "name": "gpt-runtime",
                        "endpoint": "https://models.example.test/v1/chat/completions",
                        "api_key_secret": "openai_api_key",
                        "response_format": "prompt_json",
                    },
                    "store": {"backend": "memory"},
                    "limits": {"max_iterations": 4, "max_recovery_steps": 2},
                    "domain": _domain_payload(),
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _domain_payload() -> dict[str, object]:
    return {
        "name": "kubernetes",
        "version": "0.2.0",
        "backend": "kubernetes_api",
        "settings": {
            "api_server": "https://cluster.example.test",
            "default_namespace": "prod",
            "bearer_token_secret": "kubernetes_api_token",
        },
    }


def _inspect_workload_decision() -> JsonMapping:
    return immutable_json(
        {
            "type": "execute",
            "reason": "Inspect the requested Kubernetes workload before remediation.",
            "capability": "inspect_workload",
            "target": "deployment/api",
            "arguments": {"name": "api", "namespace": "prod"},
            "expected_observations": ["healthy", "resource", "namespace"],
            "message": None,
        }
    )


def _finish_decision() -> JsonMapping:
    return immutable_json(
        {
            "type": "finish",
            "reason": "The requested Kubernetes workload is healthy.",
            "capability": None,
            "target": None,
            "arguments": {},
            "expected_observations": [],
            "message": None,
        }
    )


def _object(value: JsonValue) -> dict[str, Any]:
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _ready_node() -> dict[str, JsonValue]:
    return {
        "metadata": {"name": "node-1"},
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }


def _namespace(name: str) -> dict[str, JsonValue]:
    return {"metadata": {"name": name}}


def _healthy_deployment() -> dict[str, JsonValue]:
    return {
        "metadata": {
            "name": "api",
            "namespace": "prod",
            "generation": 1,
            "resourceVersion": "rv-1",
        },
        "spec": {"replicas": 2},
        "status": {
            "observedGeneration": 1,
            "readyReplicas": 2,
            "availableReplicas": 2,
            "updatedReplicas": 2,
            "conditions": [
                {
                    "type": "Available",
                    "status": "True",
                    "reason": "MinimumReplicasAvailable",
                }
            ],
        },
    }
