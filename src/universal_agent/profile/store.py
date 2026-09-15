"""Directory-backed profile config store (config management write surface).

Profiles are stored as one JSON document per profile inside a dedicated
profiles directory. Every write validates the full profile config through the
same :class:`ProfileConfig` parsing the runtime uses, BEFORE persisting — the
store can never hold an invalid profile.

Every mutation appends a record to a JSONL config audit log (who did what and
when). Audit records contain configuration field names and the acting
principal; credential *values* never appear because profile configs only
reference secrets by environment-variable name.

The store manages persisted configuration: the running runtime keeps its
loaded profiles until restart/reload. This keeps the write surface honest
(no partial hot-reload semantics in v1).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError as PydanticValidationError

from universal_agent.core import JsonMapping, JsonValue, utc_now
from universal_agent.profile import ProfileConfig

__all__ = [
    "ConfigAuditRecord",
    "ProfileAlreadyExistsError",
    "ProfileBuiltinError",
    "ProfileNotFoundError",
    "ProfileStore",
    "ProfileStoreValidationError",
]

PROFILE_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
AUDIT_FILE_NAME = "config-audit.jsonl"


class ProfileNotFoundError(LookupError):
    pass


class ProfileAlreadyExistsError(LookupError):
    pass


class ProfileBuiltinError(ValueError):
    """Refusal to mutate a built-in profile via the config API."""


@dataclass(frozen=True, slots=True)
class ProfileStoreValidationError(ValueError):
    """Structured validation failure: one entry per invalid field."""

    errors: tuple[dict[str, str], ...]

    def __str__(self) -> str:
        first = self.errors[0] if self.errors else {}
        return f"profile config validation failed: {first.get('message', 'invalid')}"


@dataclass(frozen=True, slots=True)
class ConfigAuditRecord:
    """One configuration-change audit record (see ProfileStore audit log)."""

    record_id: str
    occurred_at: str
    actor: str
    action: str
    resource: str
    details: JsonMapping


def _validate_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or not set(cleaned) <= PROFILE_NAME_CHARS:
        raise ValueError("profile name must be non-empty and use only letters, digits, '-' or '_'")
    return cleaned


def _deep_merge(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """RFC 7386 JSON Merge Patch: objects merge recursively, ``null`` deletes."""

    result = dict(base)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            nested = result[key]
            result[key] = _deep_merge(dict(nested), value)
        else:
            result[key] = value
    return result


def _validation_errors(error: PydanticValidationError) -> tuple[dict[str, str], ...]:
    errors: list[dict[str, str]] = []
    for item in error.errors(include_url=False):
        loc = ".".join(str(part) for part in item.get("loc", ()) if part != "__root__")
        errors.append({"path": loc or "<root>", "message": str(item.get("msg", ""))})
    if not errors:
        errors.append({"path": "<root>", "message": str(error)})
    return tuple(errors)


def _as_payload(value: JsonValue | None) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        return {}
    return dict(value)


def _domain_list(value: JsonValue | None) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


class ProfileStore:
    """CRUD + validation + audit for persisted profile configs."""

    def __init__(
        self,
        root: str | Path,
        *,
        actor: str = "api",
        audit_path: str | Path | None = None,
    ) -> None:
        self._root = Path(root)
        self._actor = actor
        if audit_path is not None:
            self._audit_path = Path(audit_path)
        else:
            self._audit_path = self._root / AUDIT_FILE_NAME

    @property
    def root(self) -> Path:
        return self._root

    # -- reads ----------------------------------------------------------

    def names(self) -> tuple[str, ...]:
        if not self._root.is_dir():
            return ()
        return tuple(sorted(path.stem for path in self._root.glob("*.json")))

    def load(self, name: str) -> dict[str, JsonValue]:
        path = self._path_for(name)
        if not path.is_file():
            raise ProfileNotFoundError(f"profile config not found: {name}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProfileStoreValidationError(
                ({"path": "<root>", "message": f"invalid JSON: {exc}"},)
            ) from exc
        if not isinstance(payload, dict):
            raise ProfileStoreValidationError(
                ({"path": "<root>", "message": "profile config must be a JSON object"},)
            )
        return payload

    # -- writes ---------------------------------------------------------

    def create(
        self, payload: Mapping[str, Any], *, actor: str | None = None
    ) -> dict[str, JsonValue]:
        name = _validate_name(str(payload.get("name", "")))
        path = self._path_for(name)
        if path.exists():
            raise ProfileAlreadyExistsError(f"profile config already exists: {name}")
        validated = self._validated(payload)
        self._write(name, validated)
        self._audit(actor or self._actor, "created", name, {"profile": name})
        return validated

    def update(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
    ) -> dict[str, JsonValue]:
        self._require_exists(name)
        self._refuse_builtin(name)
        validated = self._validated({**payload, "name": name})
        self._write(name, validated)
        self._audit(actor or self._actor, "updated", name, {"profile": name})
        return validated

    def patch(
        self,
        name: str,
        partial: Mapping[str, Any],
        *,
        actor: str | None = None,
    ) -> dict[str, JsonValue]:
        """RFC 7386 JSON Merge Patch over the stored config, then validate."""

        requested_name = partial.get("name")
        if requested_name is not None and str(requested_name) != name:
            raise ValueError(
                f"profile name cannot be changed via patch (requested {requested_name!r})"
            )
        current = self.load(name)
        merged = _deep_merge(current, partial)
        merged["name"] = name  # the resource name is immutable
        return self.update(name, merged, actor=actor)

    def delete(self, name: str, *, actor: str | None = None) -> None:
        path = self._path_for(name)
        if not path.is_file():
            raise ProfileNotFoundError(f"profile config not found: {name}")
        self._refuse_builtin(name)
        path.unlink()
        self._audit(actor or self._actor, "deleted", name, {"profile": name})

    def set_domain_binding(
        self,
        name: str,
        domain_name: str,
        *,
        bound: bool,
        version: str = "0.1.0",
        settings: JsonMapping | None = None,
        actor: str | None = None,
    ) -> dict[str, JsonValue]:
        """Bind or unbind one domain on a stored profile config.

        Binding appends a domain entry to the profile's ``domains`` list (the
        primary ``runtime.domain`` is untouched); unbinding removes a matching
        entry and refuses to unbind a profile's primary domain.
        """

        current = self.load(name)
        domain_name = _validate_name(domain_name)
        primary_domain = _as_payload(current.get("domain"))
        primary = str(primary_domain.get("name", ""))

        if not bound and primary == domain_name:
            raise ValueError(
                f"cannot unbind the primary domain {domain_name!r} of profile "
                f"{name!r}; patch the profile's runtime.domain instead"
            )

        # ProfileConfig requires the top-level domains list to match the
        # runtime's configured domains, so binding updates BOTH lists. The
        # primary domain always stays first in the runtime list.
        domains = _domain_list(current.get("domains"))
        if not domains and primary:
            domains = [dict(primary_domain)]
        runtime_cfg = _as_payload(current.get("runtime"))
        runtime_domains = _domain_list(runtime_cfg.get("domains"))
        if not runtime_domains and primary:
            runtime_domains = [dict(primary_domain)]

        entry: dict[str, JsonValue] = {"name": domain_name, "version": version}
        if settings is not None:
            entry["settings"] = dict(settings)

        if bound:
            domains = [item for item in domains if str(item.get("name")) != domain_name]
            domains.append(dict(entry))
            runtime_domains = [
                item for item in runtime_domains if str(item.get("name")) != domain_name
            ]
            runtime_domains.append(dict(entry))
        else:
            domains = [item for item in domains if str(item.get("name")) != domain_name]
            runtime_domains = [
                item for item in runtime_domains if str(item.get("name")) != domain_name
            ]

        merged = _deep_merge(current, {"domains": domains, "runtime": {"domains": runtime_domains}})
        merged["name"] = name
        validated = self.update(name, merged, actor=actor)
        self._audit(
            actor or self._actor,
            "domain_bound" if bound else "domain_unbound",
            name,
            {"domain": domain_name, "profile": name},
        )
        return validated

    def validate(self, payload: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
        """Validate a profile config payload without persisting it.

        Returns an empty tuple when the payload is valid; otherwise one
        structured entry per invalid field.
        """

        try:
            self._validated(payload)
        except ProfileStoreValidationError as exc:
            return exc.errors
        return ()

    # -- audit ----------------------------------------------------------

    def audit_records(self, *, limit: int | None = None) -> tuple[dict[str, JsonValue], ...]:
        if not self._audit_path.is_file():
            return ()
        records: list[dict[str, JsonValue]] = []
        for line in self._audit_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if limit is not None and limit > 0:
            records = records[-limit:]
        return tuple(reversed(records))

    # -- internals ------------------------------------------------------

    def _path_for(self, name: str) -> Path:
        return self._root / f"{_validate_name(name)}.json"

    def _require_exists(self, name: str) -> None:
        if not self._path_for(name).is_file():
            raise ProfileNotFoundError(f"profile config not found: {name}")

    def _refuse_builtin(self, name: str) -> None:
        """Built-in profiles (builtin=true) are deployment-owned: the config
        API refuses destructive mutations with a 409-class error."""

        if self._path_for(name).is_file():
            payload = self.load(name)
            builtin = payload.get("builtin")
            if isinstance(builtin, bool) and builtin:
                raise ProfileBuiltinError(
                    f"profile {name!r} is built-in and cannot be modified or "
                    "deleted through the config API"
                )

    def _validated(self, payload: Mapping[str, Any]) -> dict[str, JsonValue]:
        materialized: dict[str, Any] = dict(payload)
        try:
            ProfileConfig.from_mapping(materialized)
        except PydanticValidationError as exc:
            raise ProfileStoreValidationError(_validation_errors(exc)) from exc
        except ValueError as exc:
            raise ProfileStoreValidationError(({"path": "<root>", "message": str(exc)},)) from exc
        return {key: value for key, value in materialized.items()}

    def _write(self, name: str, payload: Mapping[str, Any]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._path_for(name)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _audit(self, actor: str, action: str, resource: str, details: Mapping[str, Any]) -> None:
        record: dict[str, Any] = {
            "record_id": str(uuid4()),
            "occurred_at": utc_now().isoformat(),
            "actor": actor,
            "action": action,
            "resource": resource,
            "details": dict(details),
        }
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self._audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
