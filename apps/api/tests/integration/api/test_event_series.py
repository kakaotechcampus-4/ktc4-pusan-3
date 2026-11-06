"""요일 반복 일정 — 회차 펼치기 · 묶음 고치기 · 캘린더 표시 (#738).

"오늘" 은 서버 시계의 KST 날짜다. 날짜는 전부 오늘에서 상대로 잡는다 — 지난 회차 · 오늘 이후 회차를
가르는 것이 이 기능의 핵심이라 고정 날짜(2099 등)로는 지난 회차를 만들 수 없다.
"""

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import idempotency
from app.api.v1.routers import suggestions as suggestions_router
from app.domains.memory.store.db_store import DbMemoryStore
from app.domains.schedule.models import Event, EventItem, Reminder
from tests.integration.api.conftest import Bearer

KST = ZoneInfo("Asia/Seoul")
EVERY_DAY = [1, 2, 3, 4, 5, 6, 7]


def _today() -> date:
    return datetime.now(KST).date()


def _day(offset: int) -> date:
    return _today() + timedelta(days=offset)


def _at(offset: int, clock: str) -> str:
    return datetime.combine(_day(offset), time.fromisoformat(clock), tzinfo=KST).isoformat()


def _clock(clock: str) -> datetime:
    return datetime.combine(_today(), time.fromisoformat(clock), tzinfo=KST)


@pytest.fixture(autouse=True)
def now(monkeypatch) -> dict:
    """서버 시계를 오늘 00:00:01 로 둔다 — 오늘 08:30 회차가 늘 아직 시작 전이라, 테스트를 돌리는
    시각에 결과가 달라지지 않는다. 시작이 지난 오늘 회차를 볼 때는 `now["at"]` 을 옮긴다."""
    frozen = {"at": _clock("00:00:01")}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen["at"].astimezone(tz)

    monkeypatch.setattr(suggestions_router, "datetime", Clock)
    return frozen


EVENT = {
    "title": "등원",
    "ends_at": None,
    "all_day": False,
    "event_type": "core",
    "category": "institution",
}


async def _submit(
    client: AsyncClient,
    cid: uuid.UUID,
    headers: dict,
    *,
    first: int,
    until: int,
    weekdays=EVERY_DAY,
    **extra,
):
    idempotency.clear()
    return await client.post(
        f"/api/v1/children/{cid}/events",
        json={
            "event": {**EVENT, "starts_at": _at(first, "08:30")},
            "items": [{"item_id": None, "item_name": "물통"}],
            "repeat": {"weekdays": weekdays, "until": _day(until).isoformat()},
            **extra,
        },
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )


async def _patch(client: AsyncClient, cid: uuid.UUID, sid: str, headers: dict, body: dict):
    idempotency.clear()
    return await client.patch(
        f"/api/v1/children/{cid}/event-series/{sid}",
        json=body,
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )


def _series_body(*, until: int, weekdays=EVERY_DAY, items=("물통",), **event) -> dict:
    values = {
        "title": "등원",
        "starts_time": "08:30",
        "ends_time": None,
        "all_day": False,
        "event_type": "core",
        "category": "institution",
    }
    return {
        **values,
        **event,
        "repeat_": {"weekdays": weekdays, "until": _day(until).isoformat()},
        "items_": list(items),
    }


def _patch_body(before: dict, after: dict) -> dict:
    def split(values: dict) -> tuple[dict, dict, list]:
        rest = {k: v for k, v in values.items() if k not in ("repeat_", "items_")}
        return rest, values["repeat_"], values["items_"]

    event, repeat, items = split(after)
    b_event, b_repeat, b_items = split(before)
    return {
        "event": event,
        "repeat": repeat,
        "before": {**b_event, "repeat": b_repeat, "items": b_items},
        "items": items,
    }


async def _occurrences(session: AsyncSession, series_id: str) -> dict[date, Event]:
    rows = await session.scalars(
        select(Event)
        .where(Event.series_id == uuid.UUID(series_id))
        .execution_options(populate_existing=True)
    )
    return {row.starts_at.astimezone(KST).date(): row for row in rows}


async def _items(session: AsyncSession, event_id: uuid.UUID) -> dict[str, bool]:
    rows = await session.scalars(
        select(EventItem)
        .where(EventItem.event_id == event_id)
        .execution_options(populate_existing=True)
    )
    return {row.item_name: row.is_prepared for row in rows}


async def _seed(
    client, session, cid, headers, *, first=-3, until=6, weekdays=EVERY_DAY, **extra
) -> str:
    resp = await _submit(client, cid, headers, first=first, until=until, weekdays=weekdays, **extra)
    assert resp.status_code == 201, resp.text
    return resp.json()["event"]["series"]["id"]


# ── 만들기 ────────────────────────────────────────────────────────────────


async def test_repeat_expands_to_one_row_per_day_with_own_items_and_reminders(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """회차마다 일정 · 준비물 · 알림 행이 따로 생긴다. 울릴 시각이 지난 회차의 알림은 건너뛴다."""
    headers, _ = bearer
    resp = await _submit(db_client, cid, headers, first=-7, until=6, reminder={"lead": "1h"})

    assert resp.status_code == 201, resp.text
    event = resp.json()["event"]
    assert event["starts_at"] == _at(-7, "08:30")  # 응답은 첫 회차
    assert event["series"]["weekdays"] == EVERY_DAY
    assert event["series"]["label"].endswith("매일")
    assert event["reminder"] is None  # 첫 회차는 이미 지나 알림을 걸지 않았다

    occurrences = await _occurrences(session, event["series"]["id"])
    assert sorted(occurrences) == [_day(n) for n in range(-7, 7)]
    assert {e.starts_at.astimezone(KST).time() for e in occurrences.values()} == {time(8, 30)}
    for row in occurrences.values():
        assert await _items(session, row.id) == {"물통": False}
    reminded = set(
        await session.scalars(
            select(Reminder.event_id).where(
                Reminder.event_id.in_([e.id for e in occurrences.values()])
            )
        )
    )
    assert all(occurrences[_day(n)].id not in reminded for n in range(-7, 0))
    assert all(occurrences[_day(n)].id in reminded for n in range(1, 7))


async def test_repeat_is_always_core(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """반복 일정은 일상이라 core 다. episodic 이 와도 서버가 정한다 — 만들 때도, 고칠 때도."""
    headers, _ = bearer
    idempotency.clear()
    resp = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={
            "event": {**EVENT, "event_type": "episodic", "starts_at": _at(1, "08:30")},
            "repeat": {"weekdays": EVERY_DAY, "until": _day(3).isoformat()},
        },
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 201, resp.text
    sid = resp.json()["event"]["series"]["id"]
    before = _series_body(until=3)

    await _patch(
        db_client, cid, sid, headers, _patch_body(before, {**before, "event_type": "episodic"})
    )

    assert {str(e.event_type) for e in (await _occurrences(session, sid)).values()} == {"core"}


async def test_repeat_longer_than_90_days_is_refused(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    resp = await _submit(db_client, cid, headers, first=1, until=91)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_repeat"
    assert await session.scalar(select(func.count()).select_from(Event)) == 0


async def test_repeat_with_only_past_reminders_is_refused(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """회차 전부의 알림이 이미 지났으면 단건 제출처럼 422 — 걸리는 알림이 하나도 없다."""
    headers, _ = bearer
    resp = await _submit(db_client, cid, headers, first=-5, until=-2, reminder={"lead": "1h"})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_reminder"
    assert await session.scalar(select(func.count()).select_from(Event)) == 0


# ── 묶음 고치기 ───────────────────────────────────────────────────────────


async def test_time_change_applies_from_today_and_leaves_past_occurrences(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    sid = await _seed(db_client, session, cid, headers)
    before = _series_body(until=6)

    resp = await _patch(
        db_client, cid, sid, headers, _patch_body(before, {**before, "starts_time": "09:00"})
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["occurrence_count"] == 7  # 오늘 포함 7회
    occurrences = await _occurrences(session, sid)
    for offset in range(-3, 7):
        expected = time(8, 30) if offset < 0 else time(9, 0)
        assert occurrences[_day(offset)].starts_at.astimezone(KST).time() == expected, offset


async def test_only_fields_that_differ_from_before_are_written(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """제목만 바꾸면 그 회차만 10시로 옮겨 둔 시각은 그대로다 — 기준과 같은 칸은 건드리지 않는다."""
    headers, _ = bearer
    sid = await _seed(db_client, session, cid, headers)
    moved = (await _occurrences(session, sid))[_day(2)]
    await session.execute(
        update(Event)
        .where(Event.id == moved.id)
        .values(starts_at=datetime.combine(_day(2), time(10), tzinfo=KST))
    )
    before = _series_body(until=6)

    resp = await _patch(
        db_client, cid, sid, headers, _patch_body(before, {**before, "title": "어린이집 등원"})
    )

    assert resp.status_code == 200, resp.text
    occurrences = await _occurrences(session, sid)
    assert occurrences[_day(2)].starts_at.astimezone(KST).time() == time(10)
    assert occurrences[_day(2)].title == "어린이집 등원"
    assert occurrences[_day(-1)].title == "등원"


async def test_items_match_by_name_and_keep_prepared_marks(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    sid = await _seed(db_client, session, cid, headers)
    tomorrow = (await _occurrences(session, sid))[_day(1)]
    await session.execute(
        update(EventItem).where(EventItem.event_id == tomorrow.id).values(is_prepared=True)
    )
    before = _series_body(until=6)

    resp = await _patch(
        db_client,
        cid,
        sid,
        headers,
        _patch_body(before, {**before, "items_": ["물통", "실내화"]}),
    )

    assert resp.status_code == 200, resp.text
    occurrences = await _occurrences(session, sid)
    assert await _items(session, occurrences[_day(1)].id) == {"물통": True, "실내화": False}
    assert await _items(session, occurrences[_day(2)].id) == {"물통": False, "실내화": False}
    assert await _items(session, occurrences[_day(-1)].id) == {"물통": False}


async def test_repeat_change_re_expands_only_future_days(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """요일을 빼면 오늘 이후 그 요일 회차만 지워지고, 끝 날짜를 늘리면 늘어난 날에 회차 · 준비물 ·
    알림이 생긴다. 지난 회차는 빠진 요일이어도 남는다."""
    headers, parent_id = bearer
    sid = await _seed(db_client, session, cid, headers, first=-7, reminder={"lead": "1h"})
    dropped = _day(1).isoweekday()
    weekdays = [d for d in EVERY_DAY if d != dropped]
    before = _series_body(until=6)

    resp = await _patch(
        db_client,
        cid,
        sid,
        headers,
        _patch_body(before, _series_body(until=9, weekdays=weekdays)),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["series"]["weekdays"] == weekdays
    assert resp.json()["series"]["until"] == _day(9).isoformat()
    occurrences = await _occurrences(session, sid)
    expected = [_day(n) for n in range(-7, 10) if n < 0 or _day(n).isoweekday() != dropped]
    assert sorted(occurrences) == expected
    assert _day(-6) in occurrences  # 빠진 요일이지만 지난 회차라 남는다
    for offset in (7, 8, 9):
        if _day(offset).isoweekday() == dropped:
            continue
        added = occurrences[_day(offset)]
        assert await _items(session, added.id) == {"물통": False}
        lead = await session.scalar(
            select(Reminder.lead).where(
                Reminder.event_id == added.id, Reminder.parent_id == parent_id
            )
        )
        assert lead == "1h"


async def test_all_day_change_ignores_clock_times(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """종일로 바꾸면 시각이 같이 와도 버리고 그날 00:00 으로 둔다 — 422 로 막지 않는다."""
    headers, _ = bearer
    sid = await _seed(db_client, session, cid, headers)
    before = _series_body(until=6)

    resp = await _patch(
        db_client, cid, sid, headers, _patch_body(before, {**before, "all_day": True})
    )

    assert resp.status_code == 200, resp.text
    tomorrow = (await _occurrences(session, sid))[_day(1)]
    assert tomorrow.all_day is True
    assert tomorrow.starts_at.astimezone(KST).time() == time(0)


async def test_rule_change_replacing_every_future_occurrence_keeps_reminders(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """요일을 통째로 바꿔 오늘 이후 회차가 전부 새 회차가 돼도 알림이 따라간다.

    옛 회차를 지우면 그 알림도 지워지므로, 걸어 둔 값을 지우기 전에 읽어야 한다 (#761 리뷰).
    """
    headers, parent_id = bearer
    old_day, new_day = _day(1).isoweekday(), _day(2).isoweekday()
    sid = await _seed(
        db_client,
        session,
        cid,
        headers,
        first=1,
        until=21,
        weekdays=[old_day],
        reminder={"lead": "1h"},
    )
    before = _series_body(until=21, weekdays=[old_day])

    resp = await _patch(
        db_client,
        cid,
        sid,
        headers,
        _patch_body(before, _series_body(until=21, weekdays=[new_day])),
    )

    assert resp.status_code == 200, resp.text
    occurrences = await _occurrences(session, sid)
    assert sorted(occurrences) == [_day(2), _day(9), _day(16)]
    leads = list(
        await session.scalars(
            select(Reminder.lead).where(
                Reminder.event_id.in_([e.id for e in occurrences.values()]),
                Reminder.parent_id == parent_id,
            )
        )
    )
    assert leads == ["1h", "1h", "1h"]


async def test_overnight_repeat_is_refused_but_all_day_is_not(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """자정을 넘는 회차는 만들지 않는다 — 묶음 고치기가 "끝 > 시작" 인 하루 시각만 받아서
    만들어 두면 영영 고칠 수 없다 (#761 리뷰). 종일의 다음 날 00:00 끝은 된다."""
    headers, _ = bearer

    async def post(event: dict):
        idempotency.clear()
        return await db_client.post(
            f"/api/v1/children/{cid}/events",
            json={
                "event": {**EVENT, **event},
                "items": [],
                "repeat": {"weekdays": EVERY_DAY, "until": _day(3).isoformat()},
            },
            headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
        )

    overnight = await post({"starts_at": _at(1, "23:00"), "ends_at": _at(2, "01:00")})
    assert overnight.status_code == 422
    assert overnight.json()["error"]["code"] == "invalid_repeat"
    assert await session.scalar(select(func.count()).select_from(Event)) == 0

    all_day = await post(
        {"starts_at": _at(1, "00:00"), "ends_at": _at(2, "00:00"), "all_day": True}
    )
    assert all_day.status_code == 201, all_day.text


async def test_re_expansion_does_not_revive_moved_or_deleted_occurrences(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """끝 날짜를 늘리면 늘어난 날에만 회차가 생긴다. 한 회차를 다른 날로 옮겼거나 지워 둔 날은
    옛 규칙에 있던 날이라 되살리지 않는다 (#761 리뷰)."""
    headers, _ = bearer
    sid = await _seed(db_client, session, cid, headers, first=-1, until=4)
    occurrences = await _occurrences(session, sid)
    await session.execute(
        update(Event)
        .where(Event.id == occurrences[_day(2)].id)
        .values(starts_at=datetime.combine(_day(3), time(10), tzinfo=KST))
    )
    await session.delete(occurrences[_day(4)])
    await session.flush()
    before = _series_body(until=4)

    resp = await _patch(db_client, cid, sid, headers, _patch_body(before, _series_body(until=6)))

    assert resp.status_code == 200, resp.text
    rows = await session.scalars(
        select(Event.starts_at)
        .where(Event.series_id == uuid.UUID(sid))
        .execution_options(populate_existing=True)
    )
    days = sorted(r.astimezone(KST).date() for r in rows)
    assert days == [_day(-1), _day(0), _day(1), _day(3), _day(3), _day(5), _day(6)]


async def test_occurrence_moved_off_the_rule_survives_extending_until(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """두 요일 반복에서 한 회차를 규칙 밖 요일로 옮겨 두고 끝 날짜만 늘려도 그 회차가 남는다.

    지우는 대상은 규칙에서 빠진 날 · 새 끝 날짜 뒤뿐이다 (#761 리뷰). 옮기기 전 날은 옛 규칙에
    있던 날이라 되살리지 않는다.
    """
    headers, _ = bearer
    weekdays = sorted({_day(1).isoweekday(), _day(3).isoweekday()})
    sid = await _seed(db_client, session, cid, headers, first=1, until=10, weekdays=weekdays)
    occurrences = await _occurrences(session, sid)
    assert sorted(occurrences) == [_day(1), _day(3), _day(8), _day(10)]
    await session.execute(
        update(Event)
        .where(Event.id == occurrences[_day(3)].id)
        .values(starts_at=datetime.combine(_day(4), time(8, 30), tzinfo=KST))
    )
    before = _series_body(until=10, weekdays=weekdays)

    after = _series_body(until=17, weekdays=weekdays, title="수영", starts_time="09:00")

    resp = await _patch(db_client, cid, sid, headers, _patch_body(before, after))

    assert resp.status_code == 200, resp.text
    occurrences = await _occurrences(session, sid)
    assert sorted(occurrences) == [_day(1), _day(4), _day(8), _day(10), _day(15), _day(17)]
    moved = occurrences[_day(4)]  # 옮긴 날 그대로, 바뀐 제목 · 시각은 따라간다
    assert moved.title == "수영"
    assert moved.starts_at.astimezone(KST).time() == time(9)
    assert await _items(session, moved.id) == {"물통": False}


async def test_today_occurrence_that_already_started_is_left_alone(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    now: dict,
):
    """시간 일정은 시작이 지난 오늘 회차를 지난 회차로 본다 — 옮기면 그 회차 알림이 다시 울린다
    (#761 리뷰). 다시 펼칠 때도 오늘 이미 지난 시각에 새 회차를 만들지 않는다."""
    headers, _ = bearer
    sid = await _seed(db_client, session, cid, headers)
    now["at"] = _clock("12:00")
    before = _series_body(until=6)

    resp = await _patch(
        db_client, cid, sid, headers, _patch_body(before, {**before, "starts_time": "18:00"})
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["occurrence_count"] == 6  # 내일부터 6회
    occurrences = await _occurrences(session, sid)
    assert occurrences[_day(0)].starts_at.astimezone(KST).time() == time(8, 30)
    assert occurrences[_day(1)].starts_at.astimezone(KST).time() == time(18)

    today = _day(0).isoweekday()
    others = [d for d in EVERY_DAY if d != today]
    # 첫 회차가 오늘보다 앞이어야 오늘이 새 규칙의 회차 날짜가 된다
    other_sid = await _seed(db_client, session, cid, headers, first=-3, until=13, weekdays=others)
    other_before = _series_body(until=13, weekdays=others)
    resp = await _patch(
        db_client,
        cid,
        other_sid,
        headers,
        _patch_body(other_before, _series_body(until=13, weekdays=EVERY_DAY)),
    )
    assert resp.status_code == 200, resp.text
    days = await _occurrences(session, other_sid)
    assert _day(0) not in days  # 08:30 은 이미 지났다
    assert _day(7) in days


async def test_shortening_until_removes_occurrences_after_it_even_if_moved(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """끝 날짜를 당기면 새 끝 날짜 뒤 회차를 지운다. 옛 끝 날짜보다 뒤로 옮겨 둔 회차는 규칙에서
    빠진 날이 아니라서, 이 조건으로만 지워진다."""
    headers, _ = bearer
    sid = await _seed(db_client, session, cid, headers)
    await session.execute(
        update(Event)
        .where(Event.id == (await _occurrences(session, sid))[_day(2)].id)
        .values(starts_at=datetime.combine(_day(8), time(8, 30), tzinfo=KST))
    )
    before = _series_body(until=6)

    resp = await _patch(db_client, cid, sid, headers, _patch_body(before, _series_body(until=4)))

    assert resp.status_code == 200, resp.text
    days = sorted(await _occurrences(session, sid))
    assert days == [_day(n) for n in (-3, -2, -1, 0, 1, 3, 4)]


async def test_today_occurrence_that_already_started_survives_dropping_its_weekday(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    now: dict,
):
    """오늘 요일을 빼도 시작이 지난 오늘 회차는 지난 회차라 남는다. 다음 주 같은 요일은 지운다."""
    headers, _ = bearer
    sid = await _seed(db_client, session, cid, headers, until=13)
    now["at"] = _clock("12:00")
    weekdays = [d for d in EVERY_DAY if d != _day(0).isoweekday()]
    before = _series_body(until=13)

    resp = await _patch(
        db_client, cid, sid, headers, _patch_body(before, _series_body(until=13, weekdays=weekdays))
    )

    assert resp.status_code == 200, resp.text
    occurrences = await _occurrences(session, sid)
    assert occurrences[_day(0)].starts_at.astimezone(KST).time() == time(8, 30)
    assert _day(7) not in occurrences


async def test_rule_leaving_only_started_occurrences_is_refused(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    now: dict,
):
    """남는 회차 판정도 시각 기준이다 — 12시에 끝 날짜를 오늘로 당기면 오늘 08:30 은 이미 지나서
    남는 회차가 없다. 422 이고 회차는 그대로다."""
    headers, _ = bearer
    sid = await _seed(db_client, session, cid, headers)
    now["at"] = _clock("12:00")
    before = _series_body(until=6)

    resp = await _patch(db_client, cid, sid, headers, _patch_body(before, _series_body(until=0)))

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_repeat"
    assert len(await _occurrences(session, sid)) == 10


async def test_all_day_series_still_changes_today(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    now: dict,
):
    """종일 일정은 오늘이 끝나기 전까지 오늘 회차도 고친다."""
    headers, _ = bearer
    idempotency.clear()
    resp = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={
            "event": {**EVENT, "starts_at": _at(-1, "00:00"), "all_day": True},
            "repeat": {"weekdays": EVERY_DAY, "until": _day(3).isoformat()},
        },
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 201, resp.text
    sid = resp.json()["event"]["series"]["id"]
    now["at"] = _clock("12:00")
    before = _series_body(until=3, all_day=True, starts_time=None)

    resp = await _patch(
        db_client, cid, sid, headers, _patch_body(before, {**before, "title": "소풍"})
    )

    assert resp.status_code == 200, resp.text
    occurrences = await _occurrences(session, sid)
    assert occurrences[_day(0)].title == "소풍"
    assert occurrences[_day(-1)].title == "등원"


async def test_rule_leaving_no_future_occurrence_is_refused(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """끝 날짜를 어제로 당겨 오늘 이후 회차가 남지 않으면 422 — 묶음을 지우는 길은 삭제
    제안이고, 묶음 고치기로 일괄 삭제를 하지 않는다 (#761 리뷰). 회차는 그대로다."""
    headers, _ = bearer
    sid = await _seed(db_client, session, cid, headers)
    before = _series_body(until=6)

    resp = await _patch(db_client, cid, sid, headers, _patch_body(before, _series_body(until=-1)))

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_repeat"
    assert len(await _occurrences(session, sid)) == 10


async def test_90_days_count_from_the_first_day_matching_the_new_weekdays(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """다시 펼칠 때 90일은 첫 회차(starts_on) 뒤 새 요일과 처음 맞는 날부터 센다 — Agent 가
    쓰는 `occurrence_dates` 와 같은 기준이다 (event-draft-flow §4-6)."""
    headers, _ = bearer
    sid = await _seed(db_client, session, cid, headers, first=1, until=8)
    weekdays = [d for d in EVERY_DAY if d != _day(1).isoweekday()]
    before = _series_body(until=8)

    ok = await _patch(
        db_client, cid, sid, headers, _patch_body(before, _series_body(until=91, weekdays=weekdays))
    )
    assert ok.status_code == 200, ok.text  # 처음 맞는 날(day 2)부터 90일째

    too_long = await _patch(
        db_client,
        cid,
        sid,
        headers,
        _patch_body(
            _series_body(until=91, weekdays=weekdays), _series_body(until=92, weekdays=weekdays)
        ),
    )
    assert too_long.status_code == 422


async def test_series_of_unknown_id_is_not_found(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    before = _series_body(until=6)
    resp = await _patch(db_client, cid, str(uuid.uuid4()), headers, _patch_body(before, before))
    assert resp.status_code == 404


async def test_series_patch_requires_idempotency_key(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    sid = await _seed(db_client, session, cid, headers)
    before = _series_body(until=6)
    resp = await db_client.patch(
        f"/api/v1/children/{cid}/event-series/{sid}",
        json=_patch_body(before, before),
        headers=headers,
    )
    assert resp.status_code == 400


# ── 읽기 ──────────────────────────────────────────────────────────────────


async def test_calendar_and_memory_store_see_the_series(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    sid = await _seed(db_client, session, cid, headers)

    day = await db_client.get(f"/api/v1/children/{cid}/calendar/{_day(1)}", headers=headers)
    [event] = day.json()["events"]
    assert event["series"]["id"] == sid
    assert event["series"]["label"].endswith("매일")

    plain = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={"event": {**EVENT, "starts_at": _at(1, "15:00")}, "items": []},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert "series" not in plain.json()["event"]  # 반복이 아니면 키가 없다 (웹 `series?`)
    day = await db_client.get(f"/api/v1/children/{cid}/calendar/{_day(1)}", headers=headers)
    assert ["series" in e for e in day.json()["events"]] == [True, False]

    store = DbMemoryStore(session, child_id=cid)
    rows = await store.list_series_events(series_id=sid, date_from=_today())
    assert [r.starts_at.date() for r in rows] == [_day(n) for n in range(0, 7)]
    got = await store.get_event(event_id=rows[0].id)
    assert got is not None and got.series is not None
    assert got.series == await store.get_series(series_id=sid)
    assert got.series.starts_on == _day(-3)
    assert await store.get_series(series_id="not-a-uuid") is None
