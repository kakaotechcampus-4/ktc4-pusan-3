"""반복 묶음을 읽고 지우는 store 계약 (#738) — 인메모리 구현.

DB 구현(`DbMemoryStore`)은 event_series 테이블이 생기는 BE 작업이 맡는다. 같은 검사를 그쪽이
넘겨야 하므로 케이스는 포트의 말로만 쓴다.

2026-11-09 는 월요일이다. 묶음은 월 · 수, 11/9 ~ 11/18 (11/9 · 11/11 · 11/16 · 11/18).
"""

from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.memory.store import InMemoryStore
from app.agents.memory.store.ports import SeriesRow, supports_series

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID(int=1)
NOW = datetime(2026, 11, 5, 9, 0, tzinfo=KST)


@pytest.fixture
async def seeded():
    store = InMemoryStore(now=NOW)
    series, events = await store.create_series(
        child_id=CHILD,
        title="등원",
        weekdays=(1, 3),
        starts_on=date(2026, 11, 9),
        until=date(2026, 11, 18),
        start_time=time(8, 30),
        end_time=None,
        tz=KST,
        fields={"event_type": "core", "category": "institution"},
        item_names=("물통",),
    )
    return store, series, events


async def test_seeded_events_share_a_series(seeded):
    store, series, events = seeded

    assert [e.starts_at.date() for e in events] == [
        date(2026, 11, 9),
        date(2026, 11, 11),
        date(2026, 11, 16),
        date(2026, 11, 18),
    ]
    assert {e.starts_at.time() for e in events} == {time(8, 30)}
    got = await store.get_event(event_id=events[1].id)
    assert got is not None and got.series == series
    assert series == SeriesRow(
        id=series.id, weekdays=(1, 3), starts_on=date(2026, 11, 9), until=date(2026, 11, 18)
    )


async def test_series_is_none_for_a_plain_event():
    store = InMemoryStore(now=NOW)
    row = await store.create_event(
        child_id=CHILD,
        title="운동회",
        starts_at=datetime(2026, 11, 20, 10, 0, tzinfo=KST),
        ends_at=None,
        all_day=False,
        fields={},
    )
    assert row.series is None
    assert "series" not in row.to_summary()


async def test_summary_names_the_series_with_its_label(seeded):
    _, series, events = seeded

    assert events[0].to_summary()["series"] == {
        "id": series.id,
        "label": "11월 18일까지 매주 월, 수",
    }


async def test_get_series(seeded):
    store, series, _ = seeded

    assert await store.get_series(series_id=series.id) == series
    assert await store.get_series(series_id="series-404") is None


async def test_list_series_events_from_a_date_in_order(seeded):
    store, series, events = seeded

    rows = await store.list_series_events(series_id=series.id, date_from=date(2026, 11, 11))

    assert [r.id for r in rows] == [events[1].id, events[2].id, events[3].id]
    assert await store.list_series_events(series_id=series.id, date_from=date(2026, 11, 19)) == []
    assert await store.list_series_events(series_id="series-404", date_from=date(2026, 11, 1)) == []


async def test_updating_one_event_keeps_it_in_the_series(seeded):
    store, series, events = seeded

    updated = await store.update_event(event_id=events[1].id, fields={"title": "등원 준비"})

    assert updated is not None and updated.series == series


def test_supports_series_is_declared_by_the_store():
    # 반복 묶음을 읽고 지울 수 있는 store 만 true 를 단다. 없는 store(DB 어댑터가 아직 구현하기
    # 전)는 모르는 것으로 본다 — 이 값이 거짓이면 Agent 는 반복 일정을 만들지 않는다
    assert supports_series(InMemoryStore(now=NOW)) is True
    assert supports_series(object()) is False


def test_supports_series_is_off_unless_it_is_exactly_true():
    class Odd:
        supports_series = "yes"

    assert supports_series(Odd()) is False
