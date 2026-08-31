from __future__ import annotations

import pytest

from universal_agent.core import immutable_json
from universal_agent.domains.kubernetes import KubectlBackend, KubernetesApiBackend
from universal_agent.domains.kubernetes.cli_runtime import configured_kubernetes_backend
from universal_agent.host import DomainConfig, RuntimeConfig, SecretRef
from universal_agent.security import EnvSecretProvider


@pytest.mark.unit
def test_configured_kubernetes_backend_parses_kubectl_settings_with_pydantic() -> None:
    backend = configured_kubernetes_backend(
        (
            DomainConfig(
                "kubernetes",
                "0.2.0",
                "kubectl",
                immutable_json(
                    {
                        "default_namespace": "prod",
                        "context": "prod-cluster",
                        "kubeconfig": "/tmp/kubeconfig",
                        "timeout_seconds": 4,
                    }
                ),
            ),
        )
    )

    assert isinstance(backend, KubectlBackend)
    assert backend._default_namespace == "prod"
    assert backend._context == "prod-cluster"
    assert backend._kubeconfig == "/tmp/kubeconfig"
    assert backend._timeout_seconds == 4.0


@pytest.mark.unit
def test_configured_kubernetes_backend_parses_api_settings_and_secret_with_pydantic() -> None:
    backend = configured_kubernetes_backend(
        (
            DomainConfig(
                "kubernetes",
                "0.2.0",
                "kubernetes_api",
                immutable_json(
                    {
                        "api_server": "https://cluster.example.test",
                        "default_namespace": "prod",
                        "bearer_token_secret": "kubernetes_api_token",
                        "timeout_seconds": 4.5,
                    }
                ),
            ),
        ),
        config=RuntimeConfig(
            secrets=(SecretRef.env("kubernetes_api_token", "KUBERNETES_API_TOKEN"),)
        ),
        secret_provider=EnvSecretProvider({"KUBERNETES_API_TOKEN": "fixture-token"}),
    )

    assert isinstance(backend, KubernetesApiBackend)
    assert backend._default_namespace == "prod"
    assert backend._timeout_seconds == 4.5


@pytest.mark.unit
def test_configured_kubernetes_backend_rejects_invalid_settings_through_pydantic() -> None:
    with pytest.raises(ValueError, match="invalid Kubernetes domain settings"):
        configured_kubernetes_backend(
            (
                DomainConfig(
                    "kubernetes",
                    "0.2.0",
                    "kubectl",
                    immutable_json({"default_namespace": " ", "timeout_seconds": True}),
                ),
            )
        )


@pytest.mark.unit
def test_configured_kubernetes_api_backend_requires_api_server_after_settings_parse() -> None:
    with pytest.raises(ValueError, match="domain setting api_server must be a non-empty string"):
        configured_kubernetes_backend(
            (
                DomainConfig(
                    "kubernetes",
                    "0.2.0",
                    "kubernetes_api",
                    immutable_json({"default_namespace": "prod"}),
                ),
            )
        )
