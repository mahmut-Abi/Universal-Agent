from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import NewType
from uuid import uuid4

from universal_agent.core import JsonValue, immutable_json, utc_now
from universal_agent.core.models import TaskId
from universal_agent.multi_agent.contracts import (
    AgentTaskId,
    AgentTaskRequest,
    AgentTaskResult,
    AgentTaskResultStatus,
)
from universal_agent.multi_agent.ledger import (
    DelegationEvent,
    DelegationLedger,
    InMemoryDelegationLedger,
)
from universal_agent.multi_agent.registry import AgentId

DelegationId = NewType("DelegationId", str)


class DelegationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REVOKED = "revoked"


class InvalidDelegationTransition(ValueError):
    pass


_ALLOWED_TRANSITIONS: Mapping[DelegationStatus, frozenset[DelegationStatus]] = {
    DelegationStatus.PENDING: frozenset(
        {
            DelegationStatus.RUNNING,
            DelegationStatus.FAILED,
            DelegationStatus.REVOKED,
        }
    ),
    DelegationStatus.RUNNING: frozenset(
        {
            DelegationStatus.COMPLETED,
            DelegationStatus.FAILED,
            DelegationStatus.REVOKED,
        }
    ),
    DelegationStatus.COMPLETED: frozenset(),
    DelegationStatus.FAILED: frozenset(),
    DelegationStatus.REVOKED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Delegation:
    delegation_id: DelegationId
    task_id: AgentTaskId
    from_agent: AgentId
    to_agent: AgentId
    contract: AgentTaskRequest
    status: DelegationStatus
    result: AgentTaskResult | None
    created_at: datetime
    updated_at: datetime
    fallback_agent: AgentId | None = None


class DelegationManager:
    def __init__(
        self,
        *,
        id_factory: Callable[[str], str] | None = None,
        ledger: DelegationLedger | None = None,
    ) -> None:
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{uuid4()}")
        self._ledger = ledger or InMemoryDelegationLedger()
        self._delegations: dict[DelegationId, Delegation] = {}

    def create_delegation(
        self,
        from_agent: AgentId,
        to_agent: AgentId,
        contract: AgentTaskRequest,
        *,
        fallback_agent: AgentId | None = None,
        delegation_id: DelegationId | None = None,
    ) -> Delegation:
        now = utc_now()
        delegation_id = delegation_id or DelegationId(self._id_factory("delegation"))
        delegation = Delegation(
            delegation_id=delegation_id,
            task_id=contract.task_id,
            from_agent=from_agent,
            to_agent=to_agent,
            contract=contract,
            status=DelegationStatus.PENDING,
            result=None,
            created_at=now,
            updated_at=now,
            fallback_agent=fallback_agent,
        )
        self._delegations[delegation.delegation_id] = delegation
        self._record("DelegationCreated", delegation)
        return delegation

    @property
    def ledger(self) -> DelegationLedger:
        return self._ledger

    def get(self, delegation_id: DelegationId) -> Delegation:
        try:
            return self._delegations[delegation_id]
        except KeyError as exc:
            raise KeyError(f"delegation not found: {delegation_id}") from exc

    def start(self, delegation_id: DelegationId) -> Delegation:
        return self._transition(delegation_id, DelegationStatus.RUNNING)

    def complete(
        self,
        delegation_id: DelegationId,
        result: AgentTaskResult,
    ) -> Delegation:
        current = self.get(delegation_id)
        if DelegationStatus.COMPLETED not in _ALLOWED_TRANSITIONS[current.status]:
            raise InvalidDelegationTransition(
                f"cannot complete delegation in status {current.status.value}"
            )
        if result.task_id != current.task_id:
            raise InvalidDelegationTransition(
                f"result task_id {result.task_id} does not match delegation "
                f"task_id {current.task_id}"
            )
        updated = self._apply(
            current,
            replace(
                current,
                status=DelegationStatus.COMPLETED,
                result=result,
                updated_at=utc_now(),
            ),
        )
        self._record("DelegationCompleted", updated)
        return updated

    def fail(self, delegation_id: DelegationId, error: str) -> Delegation:
        current = self.get(delegation_id)
        if DelegationStatus.FAILED not in _ALLOWED_TRANSITIONS[current.status]:
            raise InvalidDelegationTransition(
                f"cannot fail delegation in status {current.status.value}"
            )
        result = AgentTaskResult(
            current.task_id,
            AgentTaskResultStatus.FAILED,
            reason=error,
        )
        if current.fallback_agent is not None:
            updated = self._apply(
                current,
                replace(
                    current,
                    to_agent=current.fallback_agent,
                    status=DelegationStatus.PENDING,
                    result=result,
                    updated_at=utc_now(),
                ),
            )
            self._record("DelegationFallbackQueued", updated)
            return updated
        updated = self._apply(
            current,
            replace(
                current,
                status=DelegationStatus.FAILED,
                result=result,
                updated_at=utc_now(),
            ),
        )
        self._record("DelegationFailed", updated)
        return updated

    def revoke(self, delegation_id: DelegationId) -> Delegation:
        return self._transition(delegation_id, DelegationStatus.REVOKED)

    def active_delegations(self) -> tuple[Delegation, ...]:
        return tuple(
            delegation
            for delegation in self._delegations.values()
            if delegation.status in (DelegationStatus.PENDING, DelegationStatus.RUNNING)
        )

    def by_task(self, task_id: AgentTaskId | TaskId) -> tuple[Delegation, ...]:
        return tuple(
            delegation for delegation in self._delegations.values() if delegation.task_id == task_id
        )

    def _transition(
        self,
        delegation_id: DelegationId,
        target: DelegationStatus,
    ) -> Delegation:
        current = self.get(delegation_id)
        if target not in _ALLOWED_TRANSITIONS[current.status]:
            raise InvalidDelegationTransition(
                f"cannot transition delegation from {current.status.value} to {target.value}"
            )
        updated = self._apply(
            current,
            replace(current, status=target, updated_at=utc_now()),
        )
        self._record(_event_type_for_status(target), updated)
        return updated

    def _apply(
        self,
        previous: Delegation,
        updated: Delegation,
    ) -> Delegation:
        self._delegations[updated.delegation_id] = updated
        return updated

    def _record(self, event_type: str, delegation: Delegation) -> None:
        result = delegation.result
        data: dict[str, JsonValue] = {}
        if result is not None:
            data["result_status"] = result.status.value
            data["reason"] = result.reason
            if result.session_id is not None:
                data["session_id"] = str(result.session_id)
        self._ledger.append(
            DelegationEvent(
                event_type=event_type,
                delegation_id=str(delegation.delegation_id),
                task_id=delegation.task_id,
                from_agent=delegation.from_agent,
                to_agent=delegation.to_agent,
                status=delegation.status.value,
                data=immutable_json(data),
            )
        )


def _event_type_for_status(status: DelegationStatus) -> str:
    return {
        DelegationStatus.RUNNING: "DelegationStarted",
        DelegationStatus.COMPLETED: "DelegationCompleted",
        DelegationStatus.FAILED: "DelegationFailed",
        DelegationStatus.REVOKED: "DelegationRevoked",
        DelegationStatus.PENDING: "DelegationPending",
    }[status]
