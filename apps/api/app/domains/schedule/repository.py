"""승인된 일정·준비물·캘린더 기록의 최소 DB 접근 함수.

초안은 현재 스키마에 저장하지 않는다. 승인 게이트는 호출 계층에서 검증한다.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.child.models import Child, ParentChild
from app.domains.identity.models import Parent
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
from app.rules.reminder import ReminderLead, fits, for_kind, remind_at, starts_window


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
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    event_id: uuid.UUID,
    lock: Literal["share", "update"] | None = None,
) -> Event | None:
    """lock 이면 그 일정 행을 잡고 읽는다. 다른 쪽이 먼저 잡고 있으면 그 커밋을 기다렸다가
    바뀐 값을 읽는다.

    - "share"(FOR SHARE) — 읽고 검사만 하는 쪽(알림 걸기). 일정 수정(`update_event`)이 기다린다.
    - "update"(FOR UPDATE) — 읽은 뒤 일정을 고칠 쪽(PATCH). 🚨 고칠 쪽이 SHARE 로 읽으면 PATCH
      두 건이 둘 다 SHARE 를 쥔 채 UPDATE 로 올리려다 서로 기다린다.
    """
    stmt = select(Event).where(Event.child_id == child_id, Event.id == event_id)
    if lock is not None:
        stmt = stmt.with_for_update(read=lock == "share").execution_options(populate_existing=True)
    return await session.scalar(stmt)


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
    now: datetime,
) -> Event | None:
    """일정 수정은 시간 구간을 한 묶음으로 받아 일부 시간만 어긋나지 않게 한다.

    걸린 알림(#594)도 여기서 맞춘다 — 일정을 고치는 길이 이 함수 하나다 (`_follow_event`).
    🚨 같은 요청에서 알림을 걸 거라면 이 함수보다 먼저 `lock_link` 를 부른다. 여기서 알림 행을
       잡은 뒤에 연결을 잠그면, 연결 → 알림 순으로 지우는 끊기(`unlink_parent`)와 교착이 된다.
    """
    before = (
        await session.execute(
            select(Event.starts_at, Event.all_day)
            .where(Event.child_id == child_id, Event.id == event_id)
            .with_for_update()
        )
    ).one_or_none()
    if before is None:
        return None
    if before.all_day != all_day or before.starts_at != starts_at:
        await _follow_event(
            session,
            event_id=event_id,
            starts_at=starts_at,
            all_day=all_day,
            kind_changed=before.all_day != all_day,
            now=now,
        )
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
        # 부르는 쪽이 이 일정을 먼저 읽었으면 그 객체에 새 값을 덮는다 — 아니면 응답이
        # 고치기 전 값이다
        .execution_options(populate_existing=True)
    )
    return await session.scalar(stmt)


async def _follow_event(
    session: AsyncSession,
    *,
    event_id: uuid.UUID,
    starts_at: datetime,
    all_day: bool,
    kind_changed: bool,
    now: datetime,
) -> None:
    """일정의 시작 · 종류가 바뀔 때 모든 보호자의 알림을 따라가게 한다.

    - 종일 ↔ 시간 있는 일정이 바뀌면 날 수를 지켜 가까운 값으로 옮긴다(`for_kind`, #603 PM 결정).
      옮긴 값의 울릴 시각이 이미 지났으면 그 알림만 지운다.
    - 시작만 바뀌면 값은 그대로다.
    - 새 울릴 시각이 아직 오지 않은 알림만 "울렸음"(`sent_at`)을 비워 다시 울린다. 이미 지난
      시각으로 옮긴 알림은 비우지 않는다 — 비우면 이미 울린 알림이 발송 배치에서 곧바로 한 번 더
      울린다.
    알림 행을 잡고 읽는다(FOR UPDATE). 발송 배치가 그사이 맡아 `sent_at` 을 찍는 것과 엇갈리지 않게.
    """
    rows = await session.scalars(
        select(Reminder)
        .where(Reminder.event_id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    for row in rows.all():
        lead = for_kind(row.lead, all_day=all_day)
        if remind_at(lead, starts_at=starts_at, all_day=all_day) > now:
            row.lead, row.sent_at = lead, None
        elif kind_changed:
            await session.delete(row)
    await session.flush()


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


async def put_reminder(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    event_id: uuid.UUID,
    parent_id: uuid.UUID,
    lead: ReminderLead,
) -> Reminder | None:
    """그 보호자의 이 일정 알림을 lead 로 둔다. 이미 있으면 바꾸고, 울렸던 기록은 지운다.

    일정이 이 아이 것이 아니거나 보호자가 이 아이와 연결돼 있지 않으면 쓰지 않고 None.
    일정 종류에 맞는 lead 인지 · 이미 지난 시각인지는 부르는 쪽이 `app/rules/reminder.py` 로 본다.
    🚨 일기(`put_diary`)와 같은 이유로 연결을 잠근 뒤 확인한다 (#427) — 끊기와 동시에 오면
       끊긴 보호자에게 알림이 남는다.
    🚨 저장된 일정을 읽어 lead 를 검사했다면, 부르는 쪽이 `lock_link` → `find_event(lock=...)`
       순으로 먼저 잡고 검사한다. 잠그지 않고 읽은 값으로 검사하면 그사이 종일로 바뀐 일정에
       시간 있는 일정의 lead 가 저장된다. 여기서 다시 잡는 것은 이미 쥔 잠금이라 기다리지 않는다.
    """
    if not await lock_link(session, child_id=child_id, parent_id=parent_id):
        return None
    if await find_event(session, child_id=child_id, event_id=event_id, lock="share") is None:
        return None
    stmt = (
        pg_insert(Reminder)
        .values(event_id=event_id, parent_id=parent_id, lead=lead)
        .on_conflict_do_update(
            constraint="uq_reminder_event_id_parent_id",
            set_={"lead": lead, "sent_at": None},
        )
        .returning(Reminder)
        .execution_options(populate_existing=True)
    )
    return await session.scalar(stmt)


async def delete_reminder(
    session: AsyncSession, *, child_id: uuid.UUID, event_id: uuid.UUID, parent_id: uuid.UUID
) -> None:
    """그 보호자 본인의 이 일정 알림만 지운다. 없으면 아무것도 하지 않는다."""
    await session.execute(
        delete(Reminder).where(
            Reminder.parent_id == parent_id,
            Reminder.event_id.in_(
                select(Event.id).where(Event.child_id == child_id, Event.id == event_id)
            ),
        )
    )


async def list_my_reminders(
    session: AsyncSession, *, parent_id: uuid.UUID, event_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Reminder]:
    """여러 일정에서 그 보호자 본인의 알림만. 다른 보호자의 알림은 보이지 않는다."""
    if not event_ids:
        return {}
    stmt = select(Reminder).where(Reminder.parent_id == parent_id, Reminder.event_id.in_(event_ids))
    return {row.event_id: row for row in await session.scalars(stmt)}


@dataclass(frozen=True)
class DueReminder:
    """지금 울릴 알림 1건. 발송(`workers/reminder_dispatch`)이 문구와 받는 사람을 고른다."""

    reminder_id: uuid.UUID
    parent_id: uuid.UUID
    child_id: uuid.UUID
    event_id: uuid.UUID
    lead: ReminderLead
    starts_at: datetime
    all_day: bool


async def claim_due_reminders(
    session: AsyncSession, *, now: datetime, stale_after: timedelta
) -> list[DueReminder]:
    """울릴 시각이 (now - stale_after, now] 인 알림을 맡고 `sent_at` 을 찍는다. 커밋은 부르는 쪽이.

    - 보내기 **전에** 찍는다. 보내다 죽으면 그 알림은 다시 보내지 않는다 — 같은 알림이 두 번
      울리는 것보다 한 번 빠지는 쪽을 고른다.
    - `stale_after` 보다 오래 지난 알림은 맡지 않는다. 서버가 멈췄다 살아났거나, 일정을 고쳐 울릴
      시각이 이미 지난 경우 뒤늦게 울리지 않는다. 그 행은 `sent_at` 이 빈 채로 남는다.
    - `SKIP LOCKED` — 배치가 겹쳐 돌아도 같은 알림을 둘이 맡지 않고, 일정 수정이 쥔 알림은 다음
      차례로 미룬다(기다리지 않는다).
    """
    earliest, latest = starts_window(now - stale_after, now)
    rows = await session.execute(
        select(
            Reminder.id,
            Reminder.parent_id,
            Event.child_id,
            Event.id,
            Reminder.lead,
            Event.starts_at,
            Event.all_day,
        )
        .join(Event, Event.id == Reminder.event_id)
        .where(Reminder.sent_at.is_(None), Event.starts_at.between(earliest, latest))
        .with_for_update(of=Reminder, skip_locked=True)
    )
    due = []
    for row in (DueReminder(*r) for r in rows):
        # 종류가 맞지 않는 값은 저장 길이 막는다. 그래도 남아 있으면 계산할 수 없으니 건너뛴다
        if not fits(row.lead, all_day=row.all_day):
            continue
        rings = remind_at(row.lead, starts_at=row.starts_at, all_day=row.all_day)
        if now - stale_after < rings <= now:
            due.append(row)
    if due:
        await session.execute(
            update(Reminder)
            .where(Reminder.id.in_([r.reminder_id for r in due]))
            .values(sent_at=now)
        )
    return due


async def lock_link(session: AsyncSession, *, child_id: uuid.UUID, parent_id: uuid.UUID) -> bool:
    """보호자 → 아이 → 연결 행을 FOR KEY SHARE 로 잡고, 연결이 있는지 돌려준다 (#427).

    - 라우터의 접근 확인(`AccessibleChild`)은 잠그지 않는다. 그 뒤에 연결 끊기(`unlink_parent`)가
      연결과 개인 기록을 지우고 커밋하면 여기서 쓴 행이 끊긴 보호자에게 남는다. 잡아 두면 끊기가
      먼저면 연결이 없어 False, 여기가 먼저면 끊기가 커밋을 기다렸다가 이 행까지 지운다.
    - 순서는 탈퇴(`withdraw_parent`)와 같다. 연결 행만 잡으면 그것을 쥔 채 탈퇴가 잠근 보호자 ·
      아이를 기다리고, 탈퇴는 CASCADE 로 그 연결 행을 기다려 교착이 된다.
      같은 이유로 일정 · 알림 행보다 먼저 잡는다 — 끊기는 연결을 지운 뒤 알림을 지운다.
    - 세 행 모두 고치지 않고 지워지지만 않으면 되니 가장 약한 KEY SHARE 다. DELETE 만 막고
      관계 바꾸기(`set_relation`) 같은 일반 수정과 같은 보호자의 다른 쓰기는 막지 않는다.
    """
    key_share = {"read": True, "key_share": True}
    await session.execute(
        select(Parent.id).where(Parent.id == parent_id).with_for_update(**key_share)
    )
    await session.execute(select(Child.id).where(Child.id == child_id).with_for_update(**key_share))
    linked = await session.scalar(
        select(ParentChild.id)
        .where(ParentChild.child_id == child_id, ParentChild.parent_id == parent_id)
        .with_for_update(**key_share)
    )
    return linked is not None


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
) -> DiaryEntry | None:
    """작성자·아이·날짜별 한 행. 동시 재요청도 중복 행/unique 오류 없이 갱신한다.

    작성자가 이 아이와 연결돼 있지 않으면 쓰지 않고 None.
    🚨 보호자 → 아이 → 연결 행을 FOR KEY SHARE 로 잡은 뒤 연결을 확인한다 (#427, `lock_link`).
    """
    if not await lock_link(session, child_id=child_id, parent_id=parent_id):
        return None
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
