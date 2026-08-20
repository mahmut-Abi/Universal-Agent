from __future__ import annotations

from dataclasses import dataclass

from universal_agent.core import AgentState, TaskStatus
from universal_agent.domain import RuntimeComponents
from universal_agent.evidence import Evidence, EvidenceQuery, EvidenceStore
from universal_agent.state import SessionSnapshot
from universal_agent.tasks import TaskManager
from universal_agent.world import WorldModel, WorldSnapshot


class DomainMismatchError(ValueError):
    pass


@dataclass(slots=True)
class SessionRuntimeState:
    """Session-scoped runtime state: task graph plus its evidence and world view.

    The world is a derived projection. It is never restored directly from a
    snapshot; it is replayed from evidence so that every fact keeps provenance.
    """

    state: AgentState
    tasks: TaskManager
    evidence_store: EvidenceStore
    world_model: WorldModel
    domain_name: str
    domain_version: str

    def record(self, evidence: Evidence) -> bool:
        return self.evidence_store.add(evidence)

    def apply(self, evidence: Evidence, components: RuntimeComponents) -> None:
        for updater in components.world_updaters:
            updater.apply(self.world_model, evidence)

    def query(self, *, task_scoped: bool = True, limit: int | None = None) -> tuple[Evidence, ...]:
        return self.evidence_store.query(
            EvidenceQuery(
                self.state.session_id,
                task_id=self.state.current_task.id if task_scoped else None,
                limit=limit,
            )
        )

    def world(self) -> WorldSnapshot:
        return self.world_model.snapshot(self.state.session_id)

    def sync_current_task(self) -> None:
        """Align AgentState with the task graph, which owns current-task identity."""
        self.state.current_task = self.tasks.current
        self.state.tasks = list(self.tasks.all())

    def snapshot(self) -> SessionSnapshot:
        self.sync_current_task()
        return SessionSnapshot(
            self.state,
            self.tasks.snapshot(),
            self.evidence_store.export(self.state.session_id),
            self.domain_name,
            self.domain_version,
        )


def start_session(
    state: AgentState,
    components: RuntimeComponents,
) -> SessionRuntimeState:
    metadata = components.active_domain.manifest.metadata
    session = SessionRuntimeState(
        state,
        TaskManager(state.current_task),
        components.evidence_store,
        components.world_model,
        metadata.name,
        metadata.version,
    )
    session.evidence_store.replace(state.session_id, ())
    session.world_model.forget(state.session_id)
    return session


def hydrate_session(
    snapshot: SessionSnapshot,
    components: RuntimeComponents,
) -> SessionRuntimeState:
    metadata = components.active_domain.manifest.metadata
    if snapshot.domain_name and snapshot.domain_name != metadata.name:
        raise DomainMismatchError(
            f"session domain {snapshot.domain_name} does not match {metadata.name}"
        )
    if snapshot.domain_version and snapshot.domain_version != metadata.version:
        raise DomainMismatchError(
            f"session domain version {snapshot.domain_version} does not match {metadata.version}"
        )
    tasks = TaskManager.from_snapshot(snapshot.task_graph)
    components.evidence_store.replace(snapshot.state.session_id, snapshot.evidence)
    components.world_model.rebuild(
        snapshot.state.session_id,
        snapshot.evidence,
        components.world_updaters,
    )
    session = SessionRuntimeState(
        snapshot.state,
        tasks,
        components.evidence_store,
        components.world_model,
        metadata.name,
        metadata.version,
    )
    session.sync_current_task()
    return session


def complete_current_task(session: SessionRuntimeState) -> None:
    session.tasks.complete_current()
    session.sync_current_task()


def start_next_task(session: SessionRuntimeState) -> None:
    started = session.tasks.start_next()
    if started is not None:
        session.state.current_task = started
    session.sync_current_task()


def mark_current_task(session: SessionRuntimeState, status: TaskStatus) -> None:
    session.tasks.current.status = status
    session.sync_current_task()
