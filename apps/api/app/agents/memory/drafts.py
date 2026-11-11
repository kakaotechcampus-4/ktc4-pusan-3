"""보호자 승인 앞에 놓을 일정 초안.

시각 해석과 구간 검증을 통과한 값을 초안으로 만들어
run 단위 버퍼(DraftBook)에 모아 두고, pipeline이 run 끝에 한 번 내보낸다.
저장은 보호자가 초안을 제출한 뒤 백엔드가 한다.

to_payload() 는 SSE로 나갈 JSON 을 만든다. 모양의 정본은 app/core/event_draft.py 다.
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, time
from typing import Any, Literal, TypeVar
from uuid import uuid4

from app.agents.common.result import EventRequest
from app.core.event_draft import (
    CreateEventDraft,
    CreateRepeatEventDraft,
    DraftItemPayload,
    EventBefore,
    EventBody,
    RepeatPayload,
    SeriesBefore,
    SeriesEventBody,
    UpdateEventDraft,
    UpdateSeriesDraft,
)
from app.rules.recurrence import repeat_label

DraftOp = Literal["create", "update"]


@dataclass(frozen=True)
class DraftItem:
    """초안에 딸린 준비물. 아직 저장 전인 준비물은 item_id가 None이다."""

    item_id: str | None
    item_name: str
    is_prepared: bool = False
    """payload 에는 싣지 않는다.

    체크 상태는 PATCH /event-items/{iid}의 몫이다. items가 최종 목록이라 초안에
    실어 보내면 SSE 뒤에 보호자가 누른 체크가 제출하는 순간 풀린다.
    필드는 남겨 _diff 가 items를 통째로 비교해 "바뀐 게 없으면 초안을 만들지
    않는다"를 판정하고, _sync_checked가 이 값을 맞춘다.
    """


@dataclass(frozen=True)
class RepeatRule:
    """요일 반복 규칙 (#738). 어느 날이 회차인지는 `rules/recurrence` 가 이 값으로 정한다."""

    weekdays: tuple[int, ...]  # ISO 요일, 1=월 … 7=일
    until: date  # 마지막 날 (포함)

    def to_payload(self) -> RepeatPayload:
        return RepeatPayload(
            weekdays=list(self.weekdays),
            until=self.until,
            label=repeat_label(self.weekdays, self.until),
        )


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
    #   - 2단계  (두 경우 모두) item_id null → INSERT / non-null → UPDATE
    #            배열에 없는 기존 item_id → DELETE
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
    before: EventSnapshot | None = None  # create면 None
    draft_id: str | None = None  # DraftBook이 uuid로 채운다. 아이의 모든 초안 사이에서 유일
    repeat: RepeatRule | None = None
    """요일 반복. create 에서만 쓴다. starts_at 은 첫 회차의 시각이고, 승인할 때 회차로 펼쳐진다."""

    def to_payload(self) -> dict[str, Any]:
        """op에 따라 모양이 갈린다. create는 POST, update는 PATCH.

        모양의 정본은 app/core/event_draft.py 다. 여기서 dict를 직접 만들지 않는다.
        op는 두 모양 모두에 남기고, 화면이 이 값으로 부를 엔드포인트를 고른다.
        """
        event = EventBody(
            title=self.title,
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            all_day=self.all_day,
            event_type=self.event_type,
            category=self.category,
        )
        items = _item_payloads(self.items)
        draft: CreateEventDraft | CreateRepeatEventDraft | UpdateEventDraft
        if self.op == "create" and self.repeat is not None:
            draft = CreateRepeatEventDraft(
                draft_id=self.draft_id,
                op="create",
                event=event,
                items=items,
                repeat=self.repeat.to_payload(),
            )
        elif self.op == "create":
            # TODO: 제안 경로가 일정 초안을 만들 때 source 키 추가. Memory 는 언제나 null
            draft = CreateEventDraft(draft_id=self.draft_id, op="create", event=event, items=items)
        else:
            draft = UpdateEventDraft(
                draft_id=self.draft_id,
                op="update",
                event_id=self.event_id,
                event=event,
                before=_before_payload(self.before) if self.before else None,
                items=items,
            )
        return draft.model_dump(mode="json")


@dataclass(frozen=True)
class SeriesSnapshot:
    """고치기 전 묶음. 화면이 "오전 8:30 → 오전 9:00" 를 보여주는 데 쓴다 (#738)."""

    title: str
    starts_time: time | None  # 종일이면 None
    ends_time: time | None
    all_day: bool
    event_type: str
    category: str
    repeat: RepeatRule
    items: tuple[str, ...]  # 준비물 이름


@dataclass(frozen=True)
class SeriesDraft:
    """반복 묶음을 오늘 이후 회차 전체에 고치는 제출 전 상태 (#738).

    일정 하나가 아니라 묶음 하나의 초안이다. 날짜는 없다 — 회차 날짜는 반복 규칙이 정하고,
    제출하면 백엔드가 오늘 이후 회차에만 적용한다. 준비물은 이름으로 맞춘다.
    changed 는 `before` 와 달라진 필드 이름이다 (payload 에는 싣지 않는다).
    """

    series_id: str
    title: str
    starts_time: time | None
    ends_time: time | None
    all_day: bool
    event_type: str
    category: str
    repeat: RepeatRule
    items: tuple[str, ...]
    changed: tuple[str, ...] = ()
    before: SeriesSnapshot | None = None
    draft_id: str | None = None  # DraftBook이 uuid로 채운다
    renames: tuple[tuple[str, str], ...] = ()
    """준비물 (처음 이름, 지금 이름). payload 에는 싣지 않는다.

    저장된 준비물의 이름은 run 안에서 바뀌지 않아서, 같은 준비물을 두 번 고치려면 처음
    이름에서 지금 이름을 따라가야 한다.
    """

    def to_payload(self) -> dict[str, Any]:
        """모양의 정본은 app/core/event_draft.py 의 UpdateSeriesDraft 다."""
        return UpdateSeriesDraft(
            draft_id=self.draft_id,
            op="update_series",
            series_id=self.series_id,
            event=_series_body(self),
            repeat=self.repeat.to_payload(),
            before=_series_before(self.before) if self.before is not None else None,
            items=list(self.items),
        ).model_dump(mode="json")


AnyEventDraft = EventDraft | SeriesDraft
_D = TypeVar("_D", EventDraft, SeriesDraft)


def _clock(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value is not None else None


def _series_body(source: "SeriesDraft | SeriesSnapshot") -> SeriesEventBody:
    return SeriesEventBody(
        title=source.title,
        starts_time=_clock(source.starts_time),
        ends_time=_clock(source.ends_time),
        all_day=source.all_day,
        event_type=source.event_type,
        category=source.category,
    )


def _series_before(before: SeriesSnapshot) -> SeriesBefore:
    return SeriesBefore(
        **_series_body(before).model_dump(),
        repeat=before.repeat.to_payload(),
        items=list(before.items),
    )


def _before_payload(before: EventSnapshot) -> EventBefore:
    return EventBefore(
        title=before.title,
        starts_at=before.starts_at,
        ends_at=before.ends_at,
        all_day=before.all_day,
        event_type=before.event_type,
        category=before.category,
        items=_item_payloads(before.items),
    )


def _item_payloads(items: tuple[DraftItem, ...]) -> list[DraftItemPayload]:
    """is_prepared는 싣지 않는다."""
    return [DraftItemPayload(item_id=item.item_id, item_name=item.item_name) for item in items]


def draft_from_request(request: EventRequest) -> EventDraft:
    """도메인 Agent 가 정한 일정 요청을 새 일정 초안으로 옮긴다.

    `create_event` tool 은 날짜 표현을 푸는 자리인데, 요청은 시각까지 확정된 값이라 그 길을 타지
    않고 같은 `EventDraft` 를 만든다. 저장하지 않는다 — 보호자가 제출해야 일정이 생긴다.
    draft_id 는 DraftBook 에 넣을 때 붙는다.
    """
    return EventDraft(
        op="create",
        event_id=None,
        title=request.title,
        starts_at=request.starts_at,
        ends_at=request.ends_at,
        all_day=request.all_day,
        event_type="episodic",
        category=request.category,
    )


class DraftBook:
    """한 run이 만든 초안 모음. AgentContext에 실린다.

    같은 event_id로 두 번째 호출이 오면 앞선 초안을 대신한다. tool은 DB 현재 값이
    아니라 get(event_id)로 꺼낸 초안 위에 쌓는다. 한 응답에서 update_event와
    create_event_item이 같은 일정에 오면 나중 것이 앞의 것을 덮기 때문이다.
    all()은 넣은 순서를 지킨다. 보호자 화면의 초안 순서가 발화 순서와 같아야 한다.

    draft_id 는 여기서 붙인다. 화면이 초안을 가리키는 키다(제출 요청에는 싣지 않는다).
    배열 인덱스로는 안 된다 — 세 장 중 한 장만 제출하면 나머지 인덱스가 밀린다.
    같은 일정에 두 번째 호출이 와도 id 는 유지한다.
    아이의 모든 초안 사이에서 유일해야 해서 uuid 로 붙인다. 화면은 run 을 가리지 않고
    draft_id 가 같으면 같은 장으로 갈아 끼우는데, DraftBook 은 run 마다 새로 생겨서
    run 안에서만 세면 앞 run 의 안 넣은 초안이 덮인다(#250). 제안 초안(`_next_draft_id`)과 같다.
    """

    def __init__(self) -> None:
        self._drafts: dict[tuple[str, str | int], AnyEventDraft] = {}
        self._creates = 0

    def put(self, draft: EventDraft) -> EventDraft:
        """초안을 넣고 draft_id 가 붙은 값을 돌려준다."""
        if draft.event_id is None:
            # 새 일정은 아직 id가 없어 합칠 기준이 없다. 호출마다 따로 쌓는다
            self._creates += 1
            key: tuple[str, str | int] = ("create", self._creates)
        else:
            key = ("update", draft.event_id)
        return self._keep(key, draft)

    def _keep(self, key: tuple[str, str | int], draft: _D) -> _D:
        """같은 키의 앞선 초안이 있으면 그 draft_id 를 이어받아 대신 넣고, 없으면 uuid 를 붙인다."""
        kept = self._drafts.get(key)
        stored = replace(draft, draft_id=kept.draft_id if kept is not None else str(uuid4()))
        self._drafts[key] = stored
        return stored

    def get(self, event_id: str) -> EventDraft | None:
        draft = self._drafts.get(("update", event_id))
        return draft if isinstance(draft, EventDraft) else None

    def drop(self, event_id: str) -> None:
        self._drafts.pop(("update", event_id), None)

    def put_series(self, draft: SeriesDraft) -> SeriesDraft:
        """묶음 초안을 넣고 draft_id 가 붙은 값을 돌려준다. 같은 묶음이면 앞의 초안을 대신한다."""
        return self._keep(("series", draft.series_id), draft)

    def get_series(self, series_id: str) -> SeriesDraft | None:
        draft = self._drafts.get(("series", series_id))
        return draft if isinstance(draft, SeriesDraft) else None

    def drop_series(self, series_id: str) -> None:
        self._drafts.pop(("series", series_id), None)

    def all(self) -> tuple[AnyEventDraft, ...]:
        return tuple(self._drafts.values())
