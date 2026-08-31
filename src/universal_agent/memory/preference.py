from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import NewType
from uuid import uuid4

from universal_agent.core import JsonMapping, immutable_json, utc_now

PreferenceId = NewType("PreferenceId", str)


def new_preference_id() -> PreferenceId:
    return PreferenceId(f"pref-{uuid4()}")


@dataclass(frozen=True, slots=True)
class UserPreference:
    """A user preference learned from interactions or explicitly set."""

    id: PreferenceId = field(default_factory=lambda: PreferenceId(f"pref-{uuid4()}"))
    # The preference key (e.g., "default_region", "preferred_editor")
    key: str = ""
    # The preference value
    value: JsonMapping = field(default_factory=dict)
    # Domain this preference applies to (empty = global)
    domain: str = ""
    # Confidence in this preference (0-1)
    confidence: float = 1.0
    # Number of times this preference was confirmed
    confirmation_count: int = 0
    # Last time this preference was updated
    updated_at: datetime = field(default_factory=utc_now)
    # Source of this preference: "explicit" (user-set), "inferred" (learned), "default"
    source: str = "inferred"
    # Tags for categorization
    tags: tuple[str, ...] = ()
    # Metadata
    metadata: JsonMapping = field(default_factory=immutable_json)
    created_at: datetime = field(default_factory=utc_now)


class PreferenceMemory:
    """Stores and retrieves user preferences."""

    def __init__(self) -> None:
        self._preferences: dict[PreferenceId, UserPreference] = {}
        # Secondary index: key -> preference_id (for fast lookup by key)
        self._key_index: dict[str, PreferenceId] = {}

    def set_preference(
        self,
        key: str,
        value: JsonMapping,
        *,
        domain: str = "",
        confidence: float = 1.0,
        source: str = "inferred",
        tags: tuple[str, ...] = (),
        metadata: JsonMapping | None = None,
    ) -> UserPreference:
        """Set or update a preference. Returns the created/updated preference."""
        # Check if preference already exists
        existing_id = self._key_index.get(f"{domain}:{key}")
        if existing_id and existing_id in self._preferences:
            existing = self._preferences[existing_id]
            # Update existing preference
            updated = UserPreference(
                id=existing.id,
                key=existing.key,
                value=value,
                domain=existing.domain,
                confidence=min(1.0, existing.confidence + 0.05),
                confirmation_count=existing.confirmation_count + 1,
                updated_at=utc_now(),
                source=source,
                tags=existing.tags,
                metadata={**(existing.metadata or {}), **(metadata or {})},
                created_at=existing.created_at,
            )
            self._preferences[existing_id] = updated
            return updated

        # Create new preference
        pref_id = PreferenceId(f"pref-{uuid4()}")
        preference = UserPreference(
            id=pref_id,
            key=key,
            value=value,
            domain=domain,
            confidence=confidence,
            source=source,
            tags=tags,
            metadata=metadata or immutable_json(),
        )
        self._preferences[pref_id] = preference
        self._key_index[f"{domain}:{key}"] = pref_id
        return preference

    def get_preference(self, key: str, domain: str = "") -> UserPreference | None:
        """Get a preference by key and domain."""
        pref_id = self._key_index.get(f"{domain}:{key}")
        if pref_id:
            return self._preferences.get(pref_id)
        return None

    def get_all(self, domain: str = "") -> tuple[UserPreference, ...]:
        """Get all preferences, optionally filtered by domain."""
        if domain:
            return tuple(p for p in self._preferences.values() if p.domain == domain)
        return tuple(self._preferences.values())

    def delete_preference(self, key: str, domain: str = "") -> bool:
        """Delete a preference by key and domain."""
        key_idx = f"{domain}:{key}"
        pref_id = self._key_index.pop(key_idx, None)
        if pref_id and pref_id in self._preferences:
            del self._preferences[pref_id]
            return True
        return False

    def get_by_tag(self, tag: str, domain: str = "") -> tuple[UserPreference, ...]:
        """Get preferences matching a tag, optionally filtered by domain."""
        results = []
        for pref in self._preferences.values():
            if tag in pref.tags:
                if not domain or pref.domain == domain:
                    results.append(pref)
        return tuple(results)

    def __len__(self) -> int:
        return len(self._preferences)
