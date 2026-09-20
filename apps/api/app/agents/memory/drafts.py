"""보호자 승인 앞에 놓을 일정 초안.

시각 해석과 구간 검증을 통과한 값을 초안으로 만들어
run 단위 버퍼(DraftBook)에 모아 두고, pipeline이 run 끝에 한 번 내보낸다.
저장은 보호자가 초안을 제출한 뒤 백엔드가 한다.

to_payload() 는 SSE로 나갈 JSON 모양을 정한다.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

DraftOp = Literal["create", "update"]


@dataclass(frozen=True)
class DraftItem:
    """초안에 딸린 준비물. 아직 저장 전인 준비물은 item_id가 None이다."""

    item_id: str | None
    item_name: str
    is_prepared: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "item_name": self.item_name,
            "is_prepared": self.is_prepared,
        }


@dataclass(frozen=True)
class EventDraft:
    """일정 하나의 제출 전 상태로, 준비물까지 포함한다.

    changed는 현재 값과 실제로 달라진 필드 이름이고 op="update" 에서만 채운다.
    """

    op: DraftOp
    event_id: str | None  # create면 None
    title: str
    starts_at: datetime
    ends_at: datetime | None
    all_day: bool
    event_type: str
    category: str
    items: tuple[DraftItem, ...] = ()
    changed: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "event_id": self.event_id,
            "event": {
                "title": self.title,
                "starts_at": self.starts_at.isoformat(),
                "ends_at": self.ends_at.isoformat() if self.ends_at is not None else None,
                "all_day": self.all_day,
                "event_type": self.event_type,
                "category": self.category,
            },
            "items": [item.to_payload() for item in self.items],
            "changed": list(self.changed),
        }


class DraftBook:
    """한 run이 만든 초안 모음. AgentContext에 실린다.

    같은 event_id로 두 번째 호출이 오면 앞선 초안을 대신한다. tool은 DB 현재 값이
    아니라 get(event_id)로 꺼낸 초안 위에 쌓는다. 한 응답에서 update_event와
    create_event_item이 같은 일정에 오면 나중 것이 앞의 것을 덮기 때문이다.
    all()은 넣은 순서를 지킨다. 보호자 화면의 초안 순서가 발화 순서와 같아야 한다.
    """

    def __init__(self) -> None:
        self._drafts: dict[tuple[str, str | int], EventDraft] = {}
        self._creates = 0

    def put(self, draft: EventDraft) -> None:
        if draft.event_id is None:
            # 새 일정은 아직 id가 없어 합칠 기준이 없다. 호출마다 따로 쌓는다
            self._creates += 1
            self._drafts[("create", self._creates)] = draft
            return
        self._drafts[("update", draft.event_id)] = draft

    def get(self, event_id: str) -> EventDraft | None:
        return self._drafts.get(("update", event_id))

    def drop(self, event_id: str) -> None:
        self._drafts.pop(("update", event_id), None)

    def all(self) -> tuple[EventDraft, ...]:
        return tuple(self._drafts.values())
