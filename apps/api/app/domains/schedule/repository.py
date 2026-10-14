"""승인된 일정·준비물·캘린더 기록의 최소 DB 접근 함수.

초안은 현재 스키마에 저장하지 않는다. 승인 게이트는 호출 계층에서 검증한다.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.child.models import ParentChild
from app.domains.schedule.models import (
    DiaryEntry,
    Event,
    EventCategory,
    EventCreatedBy,
    EventItem,
    EventType,
    Reminder,
    SharedPhoto,
)


async def list_events(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    starts_before: datetime,
    ends_after: datetime,
) -> list[Event]:
    """캘린더 기간 [ends_after, starts_before) 와 겹치는 확정 일정.

    일정도 [starts_at, ends_at) 이다 — 기간이 시작하는 순간에 끝난 일정은 겹치지 않는다.
    🚨 `ends_at >= ends_after` 로 두면 어제 22:00~오늘 00:00 일정이 오늘에도 잡힌다 (#318).
    종료가 없거나 시작과 같은 일정은 시작 시각 한 점이다 — 그 점이 기간 안이면 겹친다.
    """
    stmt = (
        select(Event)
        .where(
            Event.child_id == child_id,
            Event.starts_at < starts_before,
            (Event.ends_at > ends_after) | (Event.starts_at >= ends_after),
        )
        .order_by(Event.starts_at, Event.id)
    )
    return list((await session.scalars(stmt)).all())


async def find_event(
    session: AsyncSession, *, child_id: uuid.UUID, event_id: uuid.UUID
) -> Event | None:
    return await session.scalar(
        select(Event).where(Event.child_id == child_id, Event.id == event_id)
    )


async def create_event(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    title: str,
    event_type: EventType,
    starts_at: datetime,
    ends_at: datetime | None,
    all_day: bool,
    category: EventCategory,
    created_by: EventCreatedBy,
    source_notice_id: uuid.UUID | None = None,
    source_refs: list[dict] | None = None,
) -> Event:
    """승인된 일정만 저장한다. 초안과 승인 판정은 이 함수의 책임이 아니다."""
    row = Event(
        child_id=child_id,
        title=title,
        event_type=EventType(event_type),
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=all_day,
        category=EventCategory(category),
        created_by=EventCreatedBy(created_by),
        source_notice_id=source_notice_id,
        source_refs=source_refs,
    )
    session.add(row)
    await session.flush()
    return row


async def update_event(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    event_id: uuid.UUID,
    title: str,
    event_type: EventType,
    starts_at: datetime,
    ends_at: datetime | None,
    all_day: bool,
    category: EventCategory,
) -> Event | None:
    """일정 수정은 시간 구간을 한 묶음으로 받아 일부 시간만 어긋나지 않게 한다."""
    stmt = (
        update(Event)
        .where(Event.child_id == child_id, Event.id == event_id)
        .values(
            title=title,
            event_type=EventType(event_type),
            starts_at=starts_at,
            ends_at=ends_at,
            all_day=all_day,
            category=EventCategory(category),
        )
        .returning(Event)
    )
    return await session.scalar(stmt)


async def delete_event(session: AsyncSession, *, child_id: uuid.UUID, event_id: uuid.UUID) -> bool:
    stmt = delete(Event).where(Event.child_id == child_id, Event.id == event_id).returning(Event.id)
    return await session.scalar(stmt) is not None


async def list_event_items(
    session: AsyncSession, *, child_id: uuid.UUID, event_id: uuid.UUID
) -> list[EventItem]:
    stmt = (
        select(EventItem)
        .join(Event, Event.id == EventItem.event_id)
        .where(Event.child_id == child_id, Event.id == event_id)
        .order_by(EventItem.created_at, EventItem.item_id)
    )
    return list((await session.scalars(stmt)).all())


async def list_items_by_event(
    session: AsyncSession, *, event_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[EventItem]]:
    """여러 일정의 준비물을 한 번에. 일정마다 조회하지 않는다. 준비물 없는 일정은 키가 없다."""
    if not event_ids:
        return {}
    stmt = (
        select(EventItem)
        .where(EventItem.event_id.in_(event_ids))
        .order_by(EventItem.created_at, EventItem.item_id)
    )
    items: dict[uuid.UUID, list[EventItem]] = {}
    for row in await session.scalars(stmt):
        items.setdefault(row.event_id, []).append(row)
    return items


async def create_event_item(
    session: AsyncSession, *, child_id: uuid.UUID, event_id: uuid.UUID, item_name: str
) -> EventItem | None:
    if await find_event(session, child_id=child_id, event_id=event_id) is None:
        return None
    row = EventItem(event_id=event_id, item_name=item_name)
    session.add(row)
    await session.flush()
    return row


async def replace_event_items(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    event_id: uuid.UUID,
    items: list[dict],
) -> list[EventItem]:
    """items를 최종 목록으로 교체한다. 기존 item_id가 있으면 is_prepared를 보존한다.

    items: [{"item_id": str | None, "item_name": str}, ...]
    item_id가 None이면 새 항목, 있으면 기존 항목 유지.
    새 목록에 없는 기존 항목은 삭제한다.

    다른 일정의 item_id가 오면 ValueError를 던진다.
    이미 삭제된(DB에 없는) item_id는 무시한다 (동시 수정 정상 경로).
    """
    # child_id 스코핑 — 다른 아이의 일정 항목을 조작하지 않는다
    owned = select(Event.id).where(Event.child_id == child_id, Event.id == event_id)
    keep_ids = [uuid.UUID(i["item_id"]) for i in items if i.get("item_id") is not None]

    if keep_ids:
        # item_id와 event_id를 한 번에 가져와서 소속을 판별한다
        rows = (
            await session.execute(
                select(EventItem.item_id, EventItem.event_id).where(EventItem.item_id.in_(keep_ids))
            )
        ).all()
        if any(owner != event_id for _, owner in rows):
            msg = "다른 일정의 준비물 id가 포함돼 있어요"
            raise ValueError(msg)
        # DB에 없는 item_id는 무시 (동시 수정으로 삭제된 정상 경로)
        keep_ids = [item_id for item_id, _ in rows]

    # 새 목록에 없는 기존 item 삭제
    if keep_ids:
        await session.execute(
            delete(EventItem).where(
                EventItem.event_id.in_(owned),
                EventItem.item_id.notin_(keep_ids),
            )
        )
    else:
        await session.execute(delete(EventItem).where(EventItem.event_id.in_(owned)))

    # item_id가 null인 것만 새로 INSERT
    for i in items:
        if i.get("item_id") is None:
            session.add(EventItem(event_id=event_id, item_name=i["item_name"]))

    await session.flush()

    # 최종 목록 반환 (기존 item의 is_prepared 보존된 채로)
    return list(
        (
            await session.scalars(
                select(EventItem)
                .where(EventItem.event_id == event_id)
                .order_by(EventItem.created_at, EventItem.item_id)
            )
        ).all()
    )


async def set_event_item_prepared(
    session: AsyncSession, *, child_id: uuid.UUID, item_id: uuid.UUID, is_prepared: bool
) -> EventItem | None:
    """prepared_at은 DB 시각으로 기록한다. 다른 아이의 준비물은 바꾸지 않는다.

    미체크 → 체크만 지금 시각, 이미 체크된 것을 다시 체크하면 처음 시각을 그대로 둔다.
    해제하면 None. Memory Agent 도 이 함수로 체크한다(DbMemoryStore.set_event_item_prepared)
    — 규칙을 한 곳에 둔다.
    🚨 이전 값을 UPDATE 안에서 본다(CASE 의 is_prepared 는 바꾸기 전 값이다). 먼저 읽고 Python 에서
       정하면, 그 사이에 들어온 해제 · 재체크 요청이 시각을 엉뚱하게 남긴다.
    """
    prepared_at = (
        case((EventItem.is_prepared, EventItem.prepared_at), else_=func.now())
        if is_prepared
        else None
    )
    stmt = (
        update(EventItem)
        .where(
            EventItem.item_id == item_id,
            EventItem.event_id.in_(select(Event.id).where(Event.child_id == child_id)),
        )
        .values(is_prepared=is_prepared, prepared_at=prepared_at)
        .returning(EventItem)
        .execution_options(populate_existing=True)
    )
    return await session.scalar(stmt)


async def create_reminder(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    event_id: uuid.UUID,
    parent_id: uuid.UUID,
    remind_at: datetime,
) -> Reminder | None:
    if await find_event(session, child_id=child_id, event_id=event_id) is None:
        return None
    if (
        await session.scalar(
            select(ParentChild.id).where(
                ParentChild.child_id == child_id, ParentChild.parent_id == parent_id
            )
        )
        is None
    ):
        return None
    row = Reminder(event_id=event_id, parent_id=parent_id, remind_at=remind_at)
    session.add(row)
    await session.flush()
    return row


async def find_diary(
    session: AsyncSession, *, child_id: uuid.UUID, parent_id: uuid.UUID, day: date
) -> DiaryEntry | None:
    return await session.scalar(
        select(DiaryEntry).where(
            DiaryEntry.child_id == child_id,
            DiaryEntry.author_parent_id == parent_id,
            DiaryEntry.date == day,
        )
    )


async def put_diary(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    parent_id: uuid.UUID,
    day: date,
    content: str,
) -> DiaryEntry:
    """작성자·아이·날짜별 한 행. 동시 재요청도 중복 행/unique 오류 없이 갱신한다."""
    stmt = (
        pg_insert(DiaryEntry)
        .values(child_id=child_id, author_parent_id=parent_id, date=day, content=content)
        .on_conflict_do_update(
            constraint="uq_diary_entry_child_author_date",
            set_={"content": content, "updated_at": func.now()},
        )
        .returning(DiaryEntry)
        .execution_options(populate_existing=True)
    )
    return await session.scalar(stmt)


async def delete_diary(
    session: AsyncSession, *, child_id: uuid.UUID, parent_id: uuid.UUID, day: date
) -> None:
    """그 보호자 본인의 그날 일기만 지운다. 없으면 아무것도 하지 않는다."""
    await session.execute(
        delete(DiaryEntry).where(
            DiaryEntry.child_id == child_id,
            DiaryEntry.author_parent_id == parent_id,
            DiaryEntry.date == day,
        )
    )


async def list_shared_photos(
    session: AsyncSession, *, child_id: uuid.UUID, day: date
) -> list[SharedPhoto]:
    stmt = (
        select(SharedPhoto)
        .where(SharedPhoto.child_id == child_id, SharedPhoto.date == day)
        .order_by(SharedPhoto.created_at, SharedPhoto.id)
    )
    return list((await session.scalars(stmt)).all())


async def list_diary_dates(
    session: AsyncSession, *, child_id: uuid.UUID, parent_id: uuid.UUID, start: date, end: date
) -> set[date]:
    """그 보호자가 일기를 쓴 날. 기간은 [start, end) 다. 남의 일기는 세지 않는다."""
    stmt = select(DiaryEntry.date).where(
        DiaryEntry.child_id == child_id,
        DiaryEntry.author_parent_id == parent_id,
        DiaryEntry.date >= start,
        DiaryEntry.date < end,
    )
    return set((await session.scalars(stmt)).all())


async def list_shared_photo_dates(
    session: AsyncSession, *, child_id: uuid.UUID, start: date, end: date
) -> set[date]:
    """공동 사진이 있는 날. 기간은 [start, end) 다."""
    stmt = select(SharedPhoto.date).where(
        SharedPhoto.child_id == child_id,
        SharedPhoto.date >= start,
        SharedPhoto.date < end,
    )
    return set((await session.scalars(stmt)).all())


async def add_shared_photo(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    parent_id: uuid.UUID,
    day: date,
    image_url: str,
) -> SharedPhoto:
    row = SharedPhoto(
        child_id=child_id, uploader_parent_id=parent_id, date=day, image_url=image_url
    )
    session.add(row)
    await session.flush()
    return row
