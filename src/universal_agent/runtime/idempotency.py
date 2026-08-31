from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, NewType, Protocol

from universal_agent.core import SessionId, dumps_json, to_json_value

IdempotencyKey = NewType("IdempotencyKey", str)


def compute_idempotency_key(
    *,
    session_id: SessionId,
    capability: str,
    arguments: Mapping[str, Any],
) -> IdempotencyKey:
    payload = {
        "session_id": session_id,
        "capability": capability,
        "arguments": to_json_value(dict(arguments), fallback_to_string=True),
    }
    normalized = dumps_json(payload)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return IdempotencyKey(digest)


class DuplicateActionError(RuntimeError):
    def __init__(self, key: IdempotencyKey) -> None:
        self.key = key
        super().__init__(f"duplicate idempotent action: {key}")


class IdempotencyStore(Protocol):
    def record(self, key: IdempotencyKey) -> bool: ...

    def seen(self, key: IdempotencyKey) -> bool: ...

    def forget(self, key: IdempotencyKey) -> None: ...

    def clear(self) -> None: ...


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._seen: set[IdempotencyKey] = set()

    def record(self, key: IdempotencyKey) -> bool:
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    def seen(self, key: IdempotencyKey) -> bool:
        return key in self._seen

    def forget(self, key: IdempotencyKey) -> None:
        self._seen.discard(key)

    def clear(self) -> None:
        self._seen.clear()
