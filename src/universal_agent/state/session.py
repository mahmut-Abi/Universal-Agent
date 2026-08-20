from __future__ import annotations

from dataclasses import dataclass

from universal_agent.core import AgentState
from universal_agent.evidence import Evidence
from universal_agent.tasks import TaskGraphSnapshot


@dataclass(slots=True)
class SessionSnapshot:
    state: AgentState
    task_graph: TaskGraphSnapshot
    evidence: tuple[Evidence, ...]
    domain_name: str
    domain_version: str
