"""Health 결과 봉투 — 라벨 경로와 `run()` 이 같이 쓴다.

`agent.py` 가 라벨 경로(`labels/`)를 import 하므로, 라벨 경로가 `agent.py` 에서 가져오면
순환이다. 그래서 봉투 만드는 함수만 따로 둔다.
"""

from app.agents.common.readout import Readout
from app.agents.common.result import (
    AgentStatus,
    DomainAgentResult,
    DomainPending,
    EventRequest,
    MedicationDraft,
)
from app.agents.health.schemas.task import HealthTaskType

AGENT = "health"


def result(
    task_type: HealthTaskType | None,
    status: AgentStatus,
    *,
    readouts: tuple[Readout, ...] = (),
    question: str | None = None,
    medication_drafts: tuple[MedicationDraft, ...] = (),
    event_requests: tuple[EventRequest, ...] = (),
    pending: DomainPending | None = None,
    model_calls: int = 0,
) -> DomainAgentResult:
    return DomainAgentResult(
        agent=AGENT,
        task_type=task_type.value if task_type is not None else None,
        status=status,
        readouts=readouts,
        needs_observation=(question,) if question else (),
        medication_drafts=medication_drafts,
        event_requests=event_requests,
        pending=pending,
        model_calls=model_calls,
    )
