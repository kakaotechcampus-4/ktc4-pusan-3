"""캘린더 달 · 하루 보기 — #318 (09 캘린더).

인증은 router.py 의 protected_router 가 건다. 아이 주소는 `AccessibleChild` 가 본문보다 먼저
확인한다 — 연결된 보호자가 아니면 403 (deps/child.py).

날짜는 한국 시간이다. 하루는 그날 00:00 부터 다음 날 00:00 전까지다.

🚨 관찰은 기록 탭 목록과 같은 기준이다 — 같은 표(`VISIBLE_DOMAINS`) · 같은 상태(`LISTED_STATUSES`).
   달 보기의 숫자와 하루 보기의 목록이 어긋나면 "3건" 을 눌렀는데 2건이 나온다.
"""

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps.auth import CurrentParent
from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.quota import KST, today_kst
from app.api.v1.routers.observations import (
    LISTED_STATUSES,
    VISIBLE_DOMAINS,
    to_observation_outs,
)
from app.api.v1.schemas.calendar import (
    CalendarDayOut,
    CalendarDayResponse,
    CalendarMonthResponse,
    MyDiaryOut,
    SharedPhotoOut,
)
from app.api.v1.schemas.suggestions import CalendarEventOut, EventItemOut
from app.domains.memory.observation.repository import (
    ObservationRecord,
    count_observations_by_day,
    page_observations,
)
from app.domains.schedule.models import Event, EventItem
from app.domains.schedule.repository import (
    find_diary,
    list_diary_dates,
    list_events,
    list_items_by_event,
    list_shared_photo_dates,
    list_shared_photos,
)

router = APIRouter()

_PAGE_LIMIT = 100
# 받는 날짜 범위 — 정책이 아니라 계산 한계다. 끝 연도(1 · 9999년)에서는 다음 날 · KST 자정
# 계산이 넘쳐 500 이 되므로 DB 조회 전에 막는다 (#318 리뷰). Agent 날짜 규칙
# (agents/common/datetime_rules.py 의 _MIN_YEAR · _MAX_YEAR)과 같은 범위라, Agent 가 저장할 수
# 있는 일정은 모두 캘린더에서 열린다. agents 내부는 가져올 수 없어 값을 따로 둔다.
# 상태 코드는 형식 오류와 같다 — 쿼리의 달은 400, 경로의 날짜는 422 (errors.py · 명세 §8-1).
_FIRST_DAY = dt.date(dt.MINYEAR + 1, 1, 1)
_LAST_DAY = dt.date(dt.MAXYEAR - 1, 12, 31)


@router.get(
    "/children/{cid}/calendar",
    responses={status: {"model": ErrorEnvelope} for status in (400, 403)},
)
async def get_calendar_month(
    child: AccessibleChild,
    parent: CurrentParent,
    session: SessionDep,
    month: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", examples=["2026-10"])],
) -> CalendarMonthResponse:
    """달 보기 — 날짜마다 일정 · 내 일기 · 공동 사진 · 기록 수."""
    year, mon = map(int, month.split("-"))
    if not _FIRST_DAY.year <= year <= _LAST_DAY.year:
        raise _out_of_range(400)
    start = dt.date(year, mon, 1)
    end = dt.date(year + mon // 12, mon % 12 + 1, 1)

    events = await list_events(
        session, child_id=child.child_id, starts_before=_midnight(end), ends_after=_midnight(start)
    )
    diary_dates = await list_diary_dates(
        session, child_id=child.child_id, parent_id=parent.parent_id, start=start, end=end
    )
    photo_dates = await list_shared_photo_dates(
        session, child_id=child.child_id, start=start, end=end
    )
    counts = await count_observations_by_day(
        session,
        child_id=child.child_id,
        start=start,
        end=end,
        domains=VISIBLE_DOMAINS,
        statuses=LISTED_STATUSES,
    )

    days = []
    for offset in range((end - start).days):
        day = start + dt.timedelta(days=offset)
        out = CalendarDayOut(
            date=day,
            has_event=any(_overlaps_day(e, day) for e in events),
            has_my_diary=day in diary_dates,
            has_shared_photo=day in photo_dates,
            observation_count=counts.get(day, 0),
        )
        if out.has_event or out.has_my_diary or out.has_shared_photo or out.observation_count:
            days.append(out)
    return CalendarMonthResponse(days=days)


@router.get(
    "/children/{cid}/calendar/{date}",
    responses={status: {"model": ErrorEnvelope} for status in (403, 422)},
)
async def get_calendar_day(
    child: AccessibleChild,
    parent: CurrentParent,
    session: SessionDep,
    date: dt.date,
) -> CalendarDayResponse:
    """하루 보기 — 내 일기 · 공동 사진 · 일정(준비물 포함) · 기록."""
    if not _FIRST_DAY <= date <= _LAST_DAY:
        raise _out_of_range(422)
    diary = await find_diary(session, child_id=child.child_id, parent_id=parent.parent_id, day=date)
    photos = await list_shared_photos(session, child_id=child.child_id, day=date)
    next_day = date + dt.timedelta(days=1)
    events = await list_events(
        session,
        child_id=child.child_id,
        starts_before=_midnight(next_day),
        ends_after=_midnight(date),
    )
    items = await list_items_by_event(session, event_ids=[e.id for e in events])
    records = await _observations_on(session, child_id=child.child_id, day=date)

    return CalendarDayResponse(
        my_diary=MyDiaryOut(content=diary.content, updated_at=diary.updated_at) if diary else None,
        shared_photos=[
            SharedPhotoOut(id=str(p.id), image_url=p.image_url, created_at=p.created_at)
            for p in photos
        ],
        events=[_event_out(e, items.get(e.id, [])) for e in events],
        observations=await to_observation_outs(session, records, today=today_kst()),
    )


def _out_of_range(status: int) -> ApiError:
    return ApiError(status, "validation_failed", "날짜가 올바르지 않아요")


def _midnight(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time.min, tzinfo=KST)


def _overlaps_day(event: Event, day: dt.date) -> bool:
    """list_events 와 같은 겹침 규칙을 하루에 건다 — 달 보기 표시와 하루 보기 목록이 같다."""
    day_start, day_end = _midnight(day), _midnight(day + dt.timedelta(days=1))
    return event.starts_at < day_end and (
        (event.ends_at is not None and event.ends_at > day_start) or event.starts_at >= day_start
    )


async def _observations_on(
    session: SessionDep, *, child_id: uuid.UUID, day: dt.date
) -> list[ObservationRecord]:
    """그날과 기간이 겹치는 기록 전부. 하루 기록은 한 쪽에 들어가지만, 넘치면 끝까지 넘긴다."""
    records: list[ObservationRecord] = []
    cursor = None
    while True:
        page = await page_observations(
            session,
            child_id=child_id,
            domains=VISIBLE_DOMAINS,
            date_from=day,
            date_to=day,
            statuses=LISTED_STATUSES,
            cursor=cursor,
            limit=_PAGE_LIMIT,
        )
        records.extend(page.items)
        if page.next_cursor is None:
            return records
        cursor = page.next_cursor


def _event_out(event: Event, items: list[EventItem]) -> CalendarEventOut:
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
                prepared_at=i.prepared_at,
            )
            for i in items
        ],
    )
