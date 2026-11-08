"""요일 반복 묶음 (#738) — 회차 펼치기와 묶음 고치기.

회차는 날짜마다 따로인 `event` 행이다. 어느 날이 회차인지는 `app/rules/recurrence.py` 가 정하고,
여기서는 그 날짜에 행을 만들고 고친다.

🚨 "오늘" 은 서버가 정한다 — 부르는 쪽이 서버 시계의 KST 날짜와 지금 시각을 넘긴다. 묶음 고치기는
   아직 시작하지 않은 회차만 바꾸고 지난 회차는 그대로 둔다. 시간 일정은 시작이 지금 이후인 회차,
   종일 일정은 오늘(그날 포함) 이후 회차다 (#761). Agent 가 초안의 기준 값(`before`)을 만들 때 읽는
   범위도 이 기준이어야 초안과 제출이 같은 회차를 본다.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.schedule import repository as schedule_repo
from app.domains.schedule.models import (
    Event,
    EventCategory,
    EventCreatedBy,
    EventItem,
    EventSeries,
    EventType,
    Reminder,
)
from app.rules.recurrence import occurrence_dates
from app.rules.reminder import ReminderLead, for_kind, remind_at

KST = ZoneInfo("Asia/Seoul")


class NoFutureOccurrence(ValueError):
    """고친 규칙으로는 오늘 이후 회차가 하나도 남지 않는다."""


@dataclass(frozen=True)
class SeriesValues:
    """묶음 고치기의 한 벌 값. 시각은 KST 벽시계이고 종일이면 둘 다 None."""

    title: str
    starts_time: time | None
    ends_time: time | None
    all_day: bool
    event_type: EventType
    category: EventCategory

    def clock(self) -> tuple[time | None, time | None, bool]:
        """시각 · 종일을 한 칸으로. 종일이면 시각은 뜻이 없어 버린다."""
        if self.all_day:
            return None, None, True
        return self.starts_time, self.ends_time, False

    def when(self, day: date) -> tuple[datetime, datetime | None]:
        if self.all_day:
            return midnight(day), None
        starts = datetime.combine(day, self.starts_time or time.min, tzinfo=KST)
        ends = datetime.combine(day, self.ends_time, tzinfo=KST) if self.ends_time else None
        return starts, ends


def midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=KST)


def kst_day(moment: datetime) -> date:
    return moment.astimezone(KST).date()


# ── 읽기 ──────────────────────────────────────────────────────────────────


async def find_series(
    session: AsyncSession, *, child_id: uuid.UUID, series_id: uuid.UUID, lock: bool = False
) -> EventSeries | None:
    stmt = select(EventSeries).where(EventSeries.child_id == child_id, EventSeries.id == series_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(stmt)


async def series_by_id(
    session: AsyncSession, *, series_ids: set[uuid.UUID | None]
) -> dict[uuid.UUID, EventSeries]:
    """일정 여러 건의 묶음을 한 번에. 반복이 아닌 일정(None)은 건너뛴다."""
    ids = {sid for sid in series_ids if sid is not None}
    if not ids:
        return {}
    rows = await session.scalars(select(EventSeries).where(EventSeries.id.in_(ids)))
    return {row.id: row for row in rows}


async def list_series_events(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    series_id: uuid.UUID,
    date_from: date,
    lock: bool = False,
) -> list[Event]:
    """`date_from`(KST, 그날 포함) 이후 회차를 날짜순으로."""
    stmt = (
        select(Event)
        .where(
            Event.child_id == child_id,
            Event.series_id == series_id,
            Event.starts_at >= midnight(date_from),
        )
        .order_by(Event.starts_at, Event.id)
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return list((await session.scalars(stmt)).all())


# ── 만들기 ────────────────────────────────────────────────────────────────


Occurrence = tuple[datetime, datetime | None]
"""회차 하나의 (시작, 끝)."""


def plan_occurrences(
    *,
    starts_at: datetime,
    ends_at: datetime | None,
    all_day: bool,
    weekdays: list[int],
    until: date,
) -> list[Occurrence]:
    """첫 회차의 시각(KST 벽시계)과 길이를 모든 회차에 쓴다. 쓰기 전에 검사하게 DB 를 안 본다.

    회차 날짜가 성립하지 않으면 `RepeatError`, 한 회차가 그날(KST) 안에서 끝나지 않으면 ValueError.
    🚨 자정을 넘는 회차(23:00 ~ 다음 날 01:00)는 받지 않는다 — 묶음 고치기는 하루의 시각만
       (`starts_time` · `ends_time`, 끝 > 시작) 말해서, 만들 수는 있어도 영영 고칠 수 없게 된다.
       종일 일정만 다음 날 00:00 끝을 허용한다 (고치기에서 종일은 시각을 보지 않는다).
    """
    first = starts_at.astimezone(KST)
    length = ends_at - starts_at if ends_at is not None else None
    next_day = midnight(first.date() + timedelta(days=1))
    if ends_at is not None and not (
        kst_day(ends_at) == first.date() or (all_day and ends_at == next_day)
    ):
        msg = "반복 일정의 한 회차는 그날 안에서 끝나야 해요"
        raise ValueError(msg)
    plan = []
    for day in occurrence_dates(first.date(), weekdays, until):
        starts = datetime.combine(day, first.time(), tzinfo=KST)
        plan.append((starts, starts + length if length is not None else None))
    return plan


async def create_series(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    plan: list[Occurrence],
    weekdays: list[int],
    until: date,
    title: str,
    event_type: EventType,
    all_day: bool,
    category: EventCategory,
    created_by: EventCreatedBy,
) -> tuple[EventSeries, list[Event]]:
    """`plan_occurrences` 의 회차마다 일정 행을 만든다."""
    series = EventSeries(
        child_id=child_id,
        weekdays=sorted(set(weekdays)),
        starts_on=kst_day(plan[0][0]),
        until=until,
    )
    session.add(series)
    await session.flush()
    events = [
        await schedule_repo.create_event(
            session,
            child_id=child_id,
            title=title,
            event_type=event_type,
            starts_at=starts,
            ends_at=ends,
            all_day=all_day,
            category=category,
            created_by=created_by,
            series_id=series.id,
        )
        for starts, ends in plan
    ]
    return series, events


async def put_reminders(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    parent_id: uuid.UUID,
    events: list[Event],
    lead: ReminderLead,
    now: datetime,
) -> dict[uuid.UUID, Reminder]:
    """회차마다 같은 알림을 건다. 울릴 시각이 `now` 이전인 회차는 건너뛴다 (지난 알림은 못 울린다).

    lead 가 회차 종류에 맞는지는 부르는 쪽이 본다 (`check_reminder`).
    """
    put: dict[uuid.UUID, Reminder] = {}
    for event in events:
        if remind_at(lead, starts_at=event.starts_at, all_day=event.all_day) <= now:
            continue
        row = await schedule_repo.put_reminder(
            session, child_id=child_id, event_id=event.id, parent_id=parent_id, lead=lead
        )
        if row is not None:
            put[event.id] = row
    return put


# ── 묶음 고치기 ───────────────────────────────────────────────────────────


async def update_series(
    session: AsyncSession,
    *,
    series: EventSeries,
    values: SeriesValues,
    weekdays: list[int],
    until: date,
    before: SeriesValues | None,
    before_items: list[str] | None,
    items: list[str],
    today: date,
    now: datetime,
) -> int:
    """오늘(그날 포함) 이후 회차를 고친다. 지난 회차는 건드리지 않는다.

    고친 뒤 오늘 이후 회차 수를 낸다.
    - `before` 와 달라진 칸만 쓴다 — 한 회차만 따로 고쳐 둔 다른 칸이 "전부 고치기" 에 덮이지
      않는다. `before` 가 None 이면 기준을 몰라 모든 칸을 쓴다. 시각 · 종일은 한 칸으로 본다.
    - 요일 · 끝 날짜가 바뀌면 다시 펼친다: 새 규칙에 없는 날의 회차는 지우고, **옛 규칙에 없던 날**
      에만 새 회차를 만든다 — 한 회차만 옮기거나 지워 둔 날이 되살아나지 않는다.
      90일은 `starts_on` 이후 새 요일과 처음 맞는 날에서 센다(`occurrence_dates`, Agent 와 같다).
      성립하지 않으면 `RepeatError`, 오늘 이후 회차가 남지 않으면 `NoFutureOccurrence` —
      묶음을 지우는 길은 삭제 제안이다 (event-draft-flow §6).
    - 준비물은 이름으로 맞춘다. 남는 이름은 그 회차의 챙김 표시를 지킨다.
    부르는 쪽이 묶음 행을 잠가(`find_series(lock=True)`) 넘긴다.
    """
    from_today = await list_series_events(
        session, child_id=series.child_id, series_id=series.id, date_from=today, lock=True
    )
    # 🚨 시간 일정은 시작이 지난 오늘 회차를 뺀다 — 옮기면 `_follow_event` 가 `sent_at` 을 비워
    #    이미 다녀온 회차의 알림이 다시 울린다. 종일은 오늘 회차까지 고친다
    events = [e for e in from_today if e.all_day or e.starts_at > now]
    keep = list(dict.fromkeys(items))

    new_events: list[Event] = []
    rule = (sorted(set(weekdays)), until)
    if rule != (sorted(series.weekdays), series.until):
        target = [
            day
            for day in occurrence_dates(series.starts_on, weekdays, until)
            if day > today or (day == today and (values.all_day or values.when(day)[0] > now))
        ]
        if not target:
            msg = "오늘 이후에 남는 회차가 없어요"
            raise NoFutureOccurrence(msg)
        old = set(occurrence_dates(series.starts_on, series.weekdays, series.until))
        # 🚨 회차를 지우기 전에 읽는다 — 지우면 그 회차의 알림도 같이 지워져, 오늘 이후 회차가
        #    전부 바뀌는 요일 변경에서 새 회차에 걸 알림이 사라진다
        leads = await _reminder_leads(session, series.id, all_day=values.all_day)
        # 규칙에서 빠진 날 · 새 끝 날짜 뒤만 지운다 — 규칙 밖 요일로 옮겨 둔 회차는 남긴다 (#761)
        # ponytail: 회차의 원래 날짜를 저장하지 않아, 빠진 요일로 옮겨 둔 회차도 지워진다.
        #           문제가 되면 event 에 원래 날짜 컬럼을 둔다
        dropped = old - set(target)
        for event in [
            e for e in events if kst_day(e.starts_at) in dropped or kst_day(e.starts_at) > until
        ]:
            await session.delete(event)
            events.remove(event)
            from_today.remove(event)
        # 시작이 지난 오늘 회차도 센다 — 그날에 회차를 하나 더 만들지 않는다
        have = {kst_day(e.starts_at) for e in from_today}
        new_events = await _add_occurrences(
            session,
            series,
            values,
            [d for d in target if d not in old and d not in have],
            keep,
            leads,
            now,
        )
        series.weekdays, series.until = rule
        await session.flush()

    # 🚨 고칠 회차가 하나도 없으면 200 · 0건이 아니라 422 — 규칙이 그대로여도 마지막 회차가 이미
    #    시작했으면 여기로 온다 (08:00 초안을 14:00 에 승인). 앞의 쓰기는 부르는 쪽이 commit 하지
    #    않아 되돌려진다
    if not events and not new_events:
        msg = "오늘 이후에 남는 회차가 없어요"
        raise NoFutureOccurrence(msg)
    when_changed = before is None or before.clock() != values.clock()
    fields = [
        name
        for name in ("title", "event_type", "category")
        if before is None or getattr(before, name) != getattr(values, name)
    ]
    for event in events:
        if when_changed or fields:
            starts, ends = (
                values.when(kst_day(event.starts_at))
                if when_changed
                else (event.starts_at, event.ends_at)
            )
            await schedule_repo.update_event(
                session,
                child_id=series.child_id,
                event_id=event.id,
                title=values.title if "title" in fields else event.title,
                event_type=values.event_type if "event_type" in fields else event.event_type,
                starts_at=starts,
                ends_at=ends,
                all_day=values.all_day if when_changed else event.all_day,
                category=values.category if "category" in fields else event.category,
                now=now,
            )
    await _match_items(session, [e.id for e in events], keep, before_items)
    return len(events) + len(new_events)


async def _reminder_leads(
    session: AsyncSession, series_id: uuid.UUID, *, all_day: bool
) -> list[tuple[uuid.UUID, ReminderLead]]:
    """보호자마다 이 묶음의 가장 늦은 회차에 건 알림 값. 새 회차에 그대로 건다."""
    rows = await session.execute(
        select(Reminder.parent_id, Reminder.lead)
        .join(Event, Event.id == Reminder.event_id)
        .where(Event.series_id == series_id)
        .order_by(Reminder.parent_id, Event.starts_at.desc())
        .distinct(Reminder.parent_id)
    )
    return [(parent_id, for_kind(lead, all_day=all_day)) for parent_id, lead in rows]


async def _add_occurrences(
    session: AsyncSession,
    series: EventSeries,
    values: SeriesValues,
    days: list[date],
    items: list[str],
    leads: list[tuple[uuid.UUID, ReminderLead]],
    now: datetime,
) -> list[Event]:
    """새 규칙으로 생긴 날에 회차를 만든다. 알림은 `_reminder_leads` 값을 건다."""
    if not days:
        return []
    created = []
    for day in days:
        starts, ends = values.when(day)
        event = await schedule_repo.create_event(
            session,
            child_id=series.child_id,
            title=values.title,
            event_type=values.event_type,
            starts_at=starts,
            ends_at=ends,
            all_day=values.all_day,
            category=values.category,
            created_by=EventCreatedBy.CAREGIVER,
            series_id=series.id,
        )
        session.add_all(EventItem(event_id=event.id, item_name=name) for name in items)
        created.append(event)
    for parent_id, lead in leads:
        # ponytail: 다른 보호자의 연결을 일정 행 잠금 뒤에 잡는다 — 그 보호자의 끊기와 겹치면 교착을
        # Postgres 가 끊어 한쪽이 실패한다. 드물어서 재시도에 맡긴다
        await put_reminders(
            session,
            child_id=series.child_id,
            parent_id=parent_id,
            events=created,
            lead=lead,
            now=now,
        )
    await session.flush()
    return created


async def _match_items(
    session: AsyncSession,
    event_ids: list[uuid.UUID],
    items: list[str],
    before_items: list[str] | None,
) -> None:
    """회차마다 준비물을 이름으로 맞춘다.

    `before_items` 에만 있는 이름은 빼고, `items` 에만 있는 이름은 더한다. 기준을 모르면(None)
    `items` 에 없는 이름은 전부 뺀다. 양쪽에 있는 이름은 행을 그대로 둬 챙김 표시가 남는다.
    """
    wanted = set(items)
    dropped = None if before_items is None else set(before_items) - wanted
    added = items if before_items is None else [n for n in items if n not in set(before_items)]
    current = await schedule_repo.list_items_by_event(session, event_ids=event_ids)
    for event_id in event_ids:
        have: set[str] = set()
        for item in current.get(event_id, []):
            drop = item.item_name not in wanted if dropped is None else item.item_name in dropped
            if drop:
                await session.delete(item)
            else:
                have.add(item.item_name)
        session.add_all(
            EventItem(event_id=event_id, item_name=name) for name in added if name not in have
        )
    await session.flush()
