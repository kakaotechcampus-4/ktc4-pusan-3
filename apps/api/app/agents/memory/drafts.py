"""보호자 승인 앞에 놓을 일정 초안.

시각 해석과 구간 검증을 통과한 값을 초안으로 만들어
run 단위 버퍼(DraftBook)에 모아 두고, pipeline이 run 끝에 한 번 내보낸다.
저장은 보호자가 초안을 제출한 뒤 백엔드가 한다.

to_payload() 는 SSE로 나갈 JSON 모양을 정한다.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal

DraftOp = Literal["create", "update"]


def _moment(value: datetime | None) -> str | None:
    """before와 event의 시각 직렬화를 한 곳에서 맞춘다."""
    return value.isoformat() if value is not None else None


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
class EventSnapshot:
    """수정 전 원본. 화면이 "오후 3시 → 오후 5시" 를 보여주는 데 사용."""

    title: str
    starts_at: datetime
    ends_at: datetime | None
    all_day: bool
    event_type: str
    category: str
    items: tuple[DraftItem, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "starts_at": _moment(self.starts_at),
            "ends_at": _moment(self.ends_at),
            "all_day": self.all_day,
            "event_type": self.event_type,
            "category": self.category,
            "items": [item.to_payload() for item in self.items],
        }


@dataclass(frozen=True)
class EventDraft:
    """일정 하나의 제출 전 상태로, 준비물까지 포함한다.

    changed는 현재 값과 실제로 달라진 필드 이름이고 op="update" 에서만 채운다.
    payload에는 싣지 않지만, tool 결과와 "바뀐 게 없으면 초안을 만들지 않는다" 판정에는 그대로 쓴다.

    # TODO: 제출 API는 items를 최종 목록으로 읽고, 배열에 없는 기존 item_id는 삭제.
    #   event_id 는 event 행을 INSERT(null)할지 UPDATE(non-null)할지만 정한다.
    #   items 순회는 두 경우 모두에서 돌아야 한다. 순회를 event_id 분기 안에 넣으면
    #   새 일정의 준비물이 통째로 빠진다.
    #   - 1단계  event_id null → INSERT event / non-null → UPDATE event
    #   - 2단계  (두 경우 모두) item_id null → INSERT / non-null → UPDATE / 배열에 없는 기존 item_id → DELETE
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
    before: EventSnapshot | None = None  # create 면 None
    draft_id: str | None = None  # DraftBook 이 채운다

    def to_payload(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "op": self.op,
            "event_id": self.event_id,
            "event": {
                "title": self.title,
                "starts_at": _moment(self.starts_at),
                "ends_at": _moment(self.ends_at),
                "all_day": self.all_day,
                "event_type": self.event_type,
                "category": self.category,
            },
            "before": self.before.to_payload() if self.before is not None else None,
            "items": [item.to_payload() for item in self.items],
        }


class DraftBook:
    """한 run이 만든 초안 모음. AgentContext에 실린다.

    같은 event_id로 두 번째 호출이 오면 앞선 초안을 대신한다. tool은 DB 현재 값이
    아니라 get(event_id)로 꺼낸 초안 위에 쌓는다. 한 응답에서 update_event와
    create_event_item이 같은 일정에 오면 나중 것이 앞의 것을 덮기 때문이다.
    all()은 넣은 순서를 지킨다. 보호자 화면의 초안 순서가 발화 순서와 같아야 한다.

    draft_id 는 여기서 붙인다. 화면이 초안을 가리키고 제출 요청에 되돌려 보낼 키다.
    배열 인덱스로는 안 된다 — 세 장 중 한 장만 제출하면 나머지 인덱스가 밀린다.
    같은 일정에 두 번째 호출이 와도 id 는 유지한다.
    """

    def __init__(self) -> None:
        self._drafts: dict[tuple[str, str | int], EventDraft] = {}
        self._creates = 0
        self._issued = 0

    def put(self, draft: EventDraft) -> EventDraft:
        """초안을 넣고 draft_id 가 붙은 값을 돌려준다."""
        if draft.event_id is None:
            # 새 일정은 아직 id가 없어 합칠 기준이 없다. 호출마다 따로 쌓는다
            self._creates += 1
            key: tuple[str, str | int] = ("create", self._creates)
        else:
            key = ("update", draft.event_id)

        kept = self._drafts.get(key)
        self._issued += 1 if kept is None else 0
        draft_id = kept.draft_id if kept is not None else f"d{self._issued}"
        stored = replace(draft, draft_id=draft_id)
        self._drafts[key] = stored
        return stored

    def get(self, event_id: str) -> EventDraft | None:
        return self._drafts.get(("update", event_id))

    def drop(self, event_id: str) -> None:
        self._drafts.pop(("update", event_id), None)

    def all(self) -> tuple[EventDraft, ...]:
        return tuple(self._drafts.values())
