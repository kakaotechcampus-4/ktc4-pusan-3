"""승인된 일정·준비물·캘린더 기록의 최소 DB 접근 함수.

초안은 현재 스키마에 저장하지 않는다. 승인 게이트는 호출 계층에서 검증한다.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import delete, func, select, update
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
    """캘린더 기간과 겹치는 확정 일정. 종료가 없으면 시작 시각을 사용한다."""
    stmt = (
        select(Event)
        .where(
            Event.child_id == child_id,
            Event.starts_at < starts_before,
            (Event.ends_at >= ends_after)
            | (Event.ends_at.is_(None) & (Event.starts_at >= ends_after)),
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


async def create_event_item(
    session: AsyncSession, *, child_id: uuid.UUID, event_id: uuid.UUID, item_name: str
) -> EventItem | None:
    if await find_event(session, child_id=child_id, event_id=event_id) is None:
        return None
    row = EventItem(event_id=event_id, item_name=item_name)
    session.add(row)
    await session.flush()
    return row


async def set_event_item_prepared(
    session: AsyncSession, *, child_id: uuid.UUID, item_id: uuid.UUID, is_prepared: bool
) -> EventItem | None:
    """prepared_at은 DB 시각으로 기록한다. 다른 아이의 준비물은 바꾸지 않는다."""
    stmt = (
        update(EventItem)
        .where(
            EventItem.item_id == item_id,
            EventItem.event_id.in_(select(Event.id).where(Event.child_id == child_id)),
        )
        .values(is_prepared=is_prepared, prepared_at=func.now() if is_prepared else None)
        .returning(EventItem)
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


async def list_shared_photos(
    session: AsyncSession, *, child_id: uuid.UUID, day: date
) -> list[SharedPhoto]:
    stmt = (
        select(SharedPhoto)
        .where(SharedPhoto.child_id == child_id, SharedPhoto.date == day)
        .order_by(SharedPhoto.created_at, SharedPhoto.id)
    )
    return list((await session.scalars(stmt)).all())


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
