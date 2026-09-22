"""도메인 Agent 가 밖으로 내보내는 것 전부.

출력 채널 다섯. 채널마다 저장 여부와 승인 여부가 다르다
(docs/agents/shared/Agent_공통규약.md §3).

    suggestions        suggestion draft +24h · 요청 1건당 정확히 3개   Food · Activity · Growth
    readouts           저장 안 함 (세션 한정)                          전부
    event_requests     저장 안 함 — 초안 payload, 제출 시 저장         Health
    needs_observation  저장 안 함 — 화면이 한 줄로 묻는다              전부
    medication_drafts  저장 안 함 — 제출 시 백엔드가 INSERT/UPDATE     Health

`status` 와 근거 모드(`personalized`/`general`)는 **직교한다.** 날씨 조회는 실패했지만
개인화 근거는 있는 run 이 `degraded` + `personalized` 다.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.agents.common.readout import Readout
from app.agents.common.suggestion import SuggestionDraft

AgentStatus = Literal["completed", "blocked", "unsupported", "failed", "degraded"]
EventCategory = Literal["institution", "health", "activity", "etc"]


@dataclass(frozen=True)
class EventRequest:
    """Memory 의 일정 tool 로 넘길 값. 값이 이미 확정이라 모델을 다시 부르지 않는다.

    **저장하지 않는다.** pipeline 이 Memory tool 을 코드로 부르면 초안 버퍼에 쌓이고,
    보호자가 제출할 때 `event` 행이 생긴다.
    """

    title: str
    starts_at: datetime
    category: EventCategory
    all_day: bool = False
    ends_at: datetime | None = None


@dataclass(frozen=True)
class MedicationDraft:
    """복약 코스 생성·수정 초안. DB 를 건드리지 않고 payload 로만 나간다.

    `suggestion` 과 다르다 — 추천은 만들자마자 `draft` 행이 되지만, 복약은 **행이 있다는 것
    자체가 승인의 증거**다. 승인 전 행이 있으면 발송 잡이 그것을 걸러야 한다.
    """

    draft_id: str  # 화면이 제출 요청에 되돌려 보내는 키. 배열 인덱스로는 안 된다
    op: Literal["create", "update"]
    source: Literal["utterance", "prescription_ocr"]
    schedule: dict[str, Any]
    doses: tuple[dict[str, Any], ...]
    notice_times: tuple[str, ...]  # 필수 — 보호자가 승인하는 것은 실제로 울릴 시각이다
    missing: tuple[str, ...] = ()  # 비어 있는 NOT NULL 칸. 비지 않으면 화면이 제출을 막는다
    schedule_id: UUID | None = None  # update 만
    before: dict[str, Any] | None = None  # update 만 — 화면이 "18:30 → 19:00" 을 보여준다

    def __post_init__(self) -> None:
        if not self.notice_times:
            raise ValueError("복약 초안에 실제 알림 시각이 없다")
        if self.op == "update" and self.schedule_id is None:
            raise ValueError("수정 초안에 대상 schedule_id 가 없다")


@dataclass(frozen=True)
class DomainAgentResult:
    agent: str
    task_type: str | None
    status: AgentStatus
    suggestions: tuple[SuggestionDraft, ...] = ()
    readouts: tuple[Readout, ...] = ()
    event_requests: tuple[EventRequest, ...] = ()
    needs_observation: tuple[str, ...] = field(default_factory=tuple)
    medication_drafts: tuple[MedicationDraft, ...] = ()
    model_calls: int = 0

    def __post_init__(self) -> None:
        if len(self.needs_observation) > 1:
            # 되묻기는 한 번에 하나. 후보가 여럿이면 순위가 가장 높은 하나만 낸다
            raise ValueError(f"되묻기는 하나만 낸다. 받은 것: {len(self.needs_observation)}개")
