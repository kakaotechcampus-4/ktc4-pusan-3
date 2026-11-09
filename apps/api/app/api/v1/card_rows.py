"""카드에 실을 행 — 조회 · 삭제 제안 카드 (#535).

러너(SSE `found_tabs` · `delete_proposal`)와 지난 대화가 같은 함수를 지난다 — 대화 중에 본 카드와
지난 대화에서 다시 연 카드가 같은 모양이 된다. 관찰은 07 기록 탭(`ObservationOut`), 일정은 09
캘린더(`CalendarEventOut`)와 같은 모양이다.

- 키는 `(kind, id)` — kind 는 `Ref.kind` 와 같다(`observation_food` … · `event`).
- 지운 · 없는 · 다른 아이의 행은 결과에 없다. 카드는 그 행을 빼고 그린다.
- 일정의 알림(`reminder`)은 그 보호자 본인의 것만 싣는다 (캘린더와 같다).
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.observation_outs import to_observation_outs
from app.api.v1.reminder_check import reminder_out
from app.api.v1.schemas.cards import KIND_ORDER, CardTabOut, ProposalTabOut
from app.api.v1.schemas.observations import ObservationHealthOut, ObservationOut
from app.api.v1.schemas.suggestions import CalendarEventOut, EventItemOut, EventSeriesOut
from app.domains.memory.observation.repository import ObservationDomain, find_observations
from app.domains.schedule.models import Event, EventItem, EventSeries, Reminder
from app.domains.schedule.repository import find_events, list_items_by_event, list_my_reminders
from app.domains.schedule.series import series_by_id

CardRow = ObservationOut | ObservationHealthOut | CalendarEventOut
CardRows = dict[tuple[str, str], CardRow]

EVENT_KIND = "event"
_OBSERVATION_PREFIX = "observation_"


def event_out(
    event: Event,
    items: list[EventItem],
    reminder: Reminder | None,
    series: EventSeries | None,
) -> CalendarEventOut:
    """일정 행 → 09 캘린더 모양. 캘린더 하루 보기와 카드가 같이 쓴다.

    반복 묶음의 회차면 `series` 를 싣는다 (#738) — 카드에서도 그 일정이 반복인지 보여야 한다.
    """
    return CalendarEventOut(
        id=str(event.id),
        title=event.title,
        event_type=str(event.event_type),
        starts_at=event.starts_at.isoformat(),
        ends_at=event.ends_at.isoformat() if event.ends_at else None,
        all_day=event.all_day,
        category=str(event.category),
        created_by=str(event.created_by),
        source_notice_id=str(event.source_notice_id) if event.source_notice_id else None,
        source_refs=event.source_refs or [],
        items=[
            EventItemOut(
                item_id=str(i.item_id),
                item_name=i.item_name,
                is_prepared=i.is_prepared,
                prepared_at=i.prepared_at.isoformat() if i.prepared_at else None,
            )
            for i in items
        ],
        reminder=reminder_out(reminder),
        series=EventSeriesOut.of(series),
    )


class _HasKind(Protocol):
    kind: str


def in_kind_order[T: _HasKind](tabs: Iterable[T]) -> list[T]:
    """탭을 화면 순서(`KIND_ORDER`)로. 모르는 종류는 뺀다.

    모델이 조회한 순서가 화면 배치를 정하지 않게 한다 — 화면의 확인 문장 · 요약은 같은 고정 순서로
    종류를 읽는다(웹 `KIND_ORDER`, #714 리뷰). 모르는 종류(지난 대화에 남은 옛 값 등)는 예전처럼 그
    탭만 빠진다 — 터뜨리면 run 이 실패하고 지난 대화가 계속 500 이다.
    """
    known = [tab for tab in tabs if tab.kind in KIND_ORDER]
    return sorted(known, key=lambda tab: KIND_ORDER.index(tab.kind))


class _CardSource(Protocol):
    kind: str
    truncated: bool
    ids: Sequence[str]


class _ProposalSource(_CardSource, Protocol):
    picked: Sequence[str]


def card_tabs(
    tabs: Iterable[_CardSource], rows: Mapping[tuple[str, str], CardRow]
) -> list[CardTabOut]:
    """조회 카드의 탭 전부 — 화면 순서로, 행이 없는 탭은 빼고. 러너와 지난 대화가 같이 쓴다."""
    return [
        out
        for tab in in_kind_order(tabs)
        if (out := card_tab(tab.kind, tab.truncated, tab.ids, rows)) is not None
    ]


def proposal_tabs(tabs: Iterable[_ProposalSource], rows: Mapping[tuple[str, str], CardRow]) -> list:
    """삭제 카드의 탭 전부 — 화면 순서로, 행이 하나도 없는 탭은 빼고."""
    return [
        out
        for tab in in_kind_order(tabs)
        if (out := proposal_tab(tab.kind, tab.truncated, tab.ids, tab.picked, rows)) is not None
    ]


def _uuid(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


async def read_card_rows(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    parent_id: uuid.UUID,
    refs: Iterable[tuple[str, str]],
    today: date,
) -> CardRows:
    """refs 의 행을 화면 모양으로 읽는다. 표마다 한 번씩 읽는다 — 행마다 조회하지 않는다."""
    observation_refs: set[tuple[ObservationDomain, uuid.UUID]] = set()
    event_ids: set[uuid.UUID] = set()
    for kind, raw in refs:
        oid = _uuid(raw)
        if oid is None:
            continue
        if kind == EVENT_KIND:
            event_ids.add(oid)
        elif kind.startswith(_OBSERVATION_PREFIX):
            try:
                domain = ObservationDomain(kind.removeprefix(_OBSERVATION_PREFIX))
            except ValueError:
                continue
            observation_refs.add((domain, oid))

    rows: CardRows = {}
    records = await find_observations(session, child_id=child_id, refs=observation_refs)
    for out in await to_observation_outs(session, list(records.values()), today=today):
        rows[(out.kind, out.id)] = out
    events = await find_events(session, child_id=child_id, event_ids=event_ids)
    if events:
        ids = list(events)
        items = await list_items_by_event(session, event_ids=ids)
        reminders = await list_my_reminders(session, parent_id=parent_id, event_ids=ids)
        series = await series_by_id(session, series_ids={e.series_id for e in events.values()})
        for eid, event in events.items():
            rows[(EVENT_KIND, str(eid))] = event_out(
                event, items.get(eid, []), reminders.get(eid), series.get(event.series_id)
            )
    return rows


def _split(
    kind: str, ids: Sequence[str], rows: Mapping[tuple[str, str], CardRow]
) -> tuple[list[str], list, list]:
    """(남은 id, 관찰 칸, 일정 칸). 종류에 따라 한쪽 칸만 찬다."""
    present = [oid for oid in ids if rows.get((kind, oid)) is not None]
    present_rows = [rows[(kind, oid)] for oid in present]
    if kind == EVENT_KIND:
        return present, [], present_rows
    return present, present_rows, []


def card_tab(
    kind: str, truncated: bool, ids: Sequence[str], rows: Mapping[tuple[str, str], CardRow]
) -> CardTabOut | None:
    """탭 하나. 읽어 둔 행이 하나도 없으면 None — 빈 탭은 그리지 않는다."""
    present, records, events = _split(kind, ids, rows)
    if not present:
        return None
    return CardTabOut(kind=kind, truncated=truncated, records=records, events=events)


def proposal_tab(
    kind: str,
    truncated: bool,
    ids: Sequence[str],
    picked: Sequence[str],
    rows: Mapping[tuple[str, str], CardRow],
) -> ProposalTabOut | None:
    """삭제 제안 탭 하나. 기본 체크는 지금 남아 있는 후보 안에서만."""
    present, records, events = _split(kind, ids, rows)
    if not present:
        return None
    kept = set(present)
    return ProposalTabOut(
        kind=kind,
        truncated=truncated,
        records=records,
        events=events,
        picked_ids=[oid for oid in picked if oid in kept],
    )
