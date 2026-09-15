"""Deployment config store: declarative policies + preferences (UA-CS-007).

A deployment-level JSON config file (``deployment.json`` in the data dir)
holds config-declared policies (name/effect/reason/capabilities/categories/
risks — the same shape as ``PolicyRule``) and deployment preferences. The
agentd server reads it at assembly time and threads the policies into the
runtime's PolicyEngine as ``extra_policies``.

The file is managed via the config-management API (PUT /v1/config); every
write validates the policy specs by constructing PolicyRule objects.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from universal_agent.core import JsonValue, utc_now
from universal_agent.policy import PolicyRule

__all__ = ["DeploymentConfigStore", "DeploymentConfigValidationError"]

PREFERENCES_KEYS = ("default_profile", "detail_level")


class DeploymentConfigValidationError(ValueError):
    """Structured validation failure for the deployment config."""

    def __init__(self, errors: tuple[dict[str, str], ...]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{e.get('path', '')}: {e.get('message', '')}" for e in errors))


def _policy_from_config(payload: Mapping[str, Any]) -> PolicyRule:
    """Construct a PolicyRule from a config-declared policy dict."""

    from universal_agent.core import CapabilityCategory, PolicyEffect, RiskLevel

    name = payload.get("name")
    effect = payload.get("effect")
    if not name or not isinstance(name, str):
        raise ValueError("policy name is required")
    if not effect or not isinstance(effect, str):
        raise ValueError("policy effect is required")
    effect_enum = PolicyEffect(effect)
    reason = str(payload.get("reason") or f"config-declared policy {name}")
    capabilities = tuple(str(c) for c in payload.get("capabilities", []))
    categories = tuple(CapabilityCategory(c) for c in payload.get("categories", []))
    risks = tuple(RiskLevel(r) for r in payload.get("risks", []))
    return PolicyRule(
        name=name,
        effect=effect_enum,
        reason=reason,
        capabilities=capabilities,
        categories=categories,
        risks=risks,
    )


class DeploymentConfigStore:
    """Reads and writes the deployment config JSON file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, JsonValue]:
        if not self._path.is_file():
            return {"policies": [], "preferences": {}}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"policies": [], "preferences": {}}
        if not isinstance(raw, dict):
            return {"policies": [], "preferences": {}}
        return raw

    def load_policies(self) -> tuple[PolicyRule, ...]:
        """Construct PolicyRule objects from the stored policy specs."""

        raw = self.load()
        policies = raw.get("policies")
        if not isinstance(policies, list):
            return ()
        return tuple(_policy_from_config(p) for p in policies if isinstance(p, dict))

    def save(
        self,
        policies: list[dict[str, Any]],
        preferences: dict[str, Any],
        *,
        actor: str = "api",
    ) -> dict[str, JsonValue]:
        """Validate and persist the deployment config (policies + preferences)."""

        errors: list[dict[str, str]] = []
        # Validate policies by constructing PolicyRule objects.
        for i, p in enumerate(policies):
            try:
                _policy_from_config(p)
            except (ValueError, KeyError) as exc:
                errors.append({"path": f"policies[{i}]", "message": str(exc)})
        if errors:
            raise DeploymentConfigValidationError(tuple(errors))

        payload: dict[str, Any] = {"policies": policies, "preferences": preferences}
        payload["_updated_at"] = utc_now().isoformat()
        payload["_updated_by"] = actor
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "status": "ok",
            "policy_count": len(policies),
            "updated_at": payload["_updated_at"],
            "updated_by": actor,
        }
