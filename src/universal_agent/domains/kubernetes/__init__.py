"""Kubernetes Domain package facade.

All public names resolve lazily through module ``__getattr__`` so importing
``universal_agent.domains.kubernetes`` (for example from the CLI parser) does
not pull the API/kubectl/live-contract stacks until they are actually used.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Static surface only: resolved lazily at runtime via __getattr__.
    from universal_agent.domains.kubernetes.api import (
        HttpxKubernetesApiTransport,
        KubernetesApiBackend,
        KubernetesApiConflictError,
        KubernetesApiError,
        KubernetesApiResponse,
        KubernetesApiTransport,
        UrllibKubernetesApiTransport,
    )
    from universal_agent.domains.kubernetes.backend import (
        KubernetesBackend,
        KubernetesMutationBackend,
    )
    from universal_agent.domains.kubernetes.domain import (
        KubernetesDomain,
        KubernetesRemediationDomain,
        WorkloadHealthEvaluator,
    )
    from universal_agent.domains.kubernetes.kubectl import (
        KubectlBackend,
        KubectlCommandError,
        KubectlCommandRunner,
        KubectlResult,
        SubprocessKubectlRunner,
    )
    from universal_agent.domains.kubernetes.live_contract import (
        KubernetesLiveContractArtifactError,
        KubernetesLiveContractArtifactWrite,
        kubernetes_live_contract_artifact,
        write_kubernetes_live_contract_artifact,
    )

__all__ = [
    "HttpxKubernetesApiTransport",
    "KubectlBackend",
    "KubectlCommandError",
    "KubectlCommandRunner",
    "KubectlResult",
    "KubernetesApiBackend",
    "KubernetesApiConflictError",
    "KubernetesApiError",
    "KubernetesApiResponse",
    "KubernetesApiTransport",
    "KubernetesBackend",
    "KubernetesDomain",
    "KubernetesLiveContractArtifactError",
    "KubernetesLiveContractArtifactWrite",
    "KubernetesMutationBackend",
    "KubernetesRemediationDomain",
    "SubprocessKubectlRunner",
    "UrllibKubernetesApiTransport",
    "WorkloadHealthEvaluator",
    "kubernetes_live_contract_artifact",
    "write_kubernetes_live_contract_artifact",
]

_EXPORTS: dict[str, str] = {
    "HttpxKubernetesApiTransport": (
        "universal_agent.domains.kubernetes.api.HttpxKubernetesApiTransport"
    ),
    "KubectlBackend": "universal_agent.domains.kubernetes.kubectl.KubectlBackend",
    "KubectlCommandError": "universal_agent.domains.kubernetes.kubectl.KubectlCommandError",
    "KubectlCommandRunner": "universal_agent.domains.kubernetes.kubectl.KubectlCommandRunner",
    "KubectlResult": "universal_agent.domains.kubernetes.kubectl.KubectlResult",
    "KubernetesApiBackend": "universal_agent.domains.kubernetes.api.KubernetesApiBackend",
    "KubernetesApiConflictError": (
        "universal_agent.domains.kubernetes.api.KubernetesApiConflictError"
    ),
    "KubernetesApiError": "universal_agent.domains.kubernetes.api.KubernetesApiError",
    "KubernetesApiResponse": "universal_agent.domains.kubernetes.api.KubernetesApiResponse",
    "KubernetesApiTransport": "universal_agent.domains.kubernetes.api.KubernetesApiTransport",
    "KubernetesBackend": "universal_agent.domains.kubernetes.backend.KubernetesBackend",
    "KubernetesDomain": "universal_agent.domains.kubernetes.domain.KubernetesDomain",
    "KubernetesLiveContractArtifactError": (
        "universal_agent.domains.kubernetes.live_contract.KubernetesLiveContractArtifactError"
    ),
    "KubernetesLiveContractArtifactWrite": (
        "universal_agent.domains.kubernetes.live_contract.KubernetesLiveContractArtifactWrite"
    ),
    "KubernetesMutationBackend": (
        "universal_agent.domains.kubernetes.backend.KubernetesMutationBackend"
    ),
    "KubernetesRemediationDomain": (
        "universal_agent.domains.kubernetes.domain.KubernetesRemediationDomain"
    ),
    "SubprocessKubectlRunner": "universal_agent.domains.kubernetes.kubectl.SubprocessKubectlRunner",
    "UrllibKubernetesApiTransport": (
        "universal_agent.domains.kubernetes.api.UrllibKubernetesApiTransport"
    ),
    "WorkloadHealthEvaluator": "universal_agent.domains.kubernetes.domain.WorkloadHealthEvaluator",
    "kubernetes_live_contract_artifact": (
        "universal_agent.domains.kubernetes.live_contract.kubernetes_live_contract_artifact"
    ),
    "write_kubernetes_live_contract_artifact": (
        "universal_agent.domains.kubernetes.live_contract.write_kubernetes_live_contract_artifact"
    ),
}


def __getattr__(name: str) -> Any:
    path = _EXPORTS.get(name)
    if path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module_path, _, attribute = path.rpartition(".")
    value = getattr(importlib.import_module(module_path), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
