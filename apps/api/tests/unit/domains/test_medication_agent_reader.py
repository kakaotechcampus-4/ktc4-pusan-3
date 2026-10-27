"""medication 두 표 → Health 복약 포트 어댑터. DB 없이 도는 쪽.

저장소 동작 전체는 계약 시나리오(통합)가 실제 DB 로 본다. 여기는 DB 없이 말할 수 있는 것만 둔다.
"""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy.exc import OperationalError

from app.agents.health.store.ports import DoseSlot
from app.domains.medication.agent_reader import DbMedicationStore, pattern_of
from app.domains.medication.models import TimingAnchor

CHILD = UUID(int=1)
SCHEDULE = UUID(int=2)
DAY = date(2026, 9, 28)


def dose(
    day: date, anchor: str, offset: int = 0, fixed: time | None = None, at: time | None = None
):
    slot = DoseSlot.of(anchor, offset, fixed_time=fixed)
    return SimpleNamespace(
        dose_date=day,
        timing_anchor=TimingAnchor(anchor),
        offset_minutes=offset,
        fixed_time=fixed,
        scheduled_time=at or slot.scheduled_time,
    )


def opener(calls: list[int] | None = None, *, fail: Exception | None = None):
    @asynccontextmanager
    async def open_session():
        if calls is not None:
            calls.append(1)
        if fail is not None:
            raise fail
        yield object()

    return open_session


def test_pattern_은_기준일_이후_회차에서_모은다():
    rows = [
        dose(date(2026, 9, 27), "breakfast", 30),  # 지난 회차 — 옛 시각이다
        dose(date(2026, 9, 27), "dinner", 30),
        dose(DAY, "breakfast", 60),
        dose(date(2026, 9, 29), "breakfast", 60),
    ]

    assert pattern_of(rows, basis=DAY) == (DoseSlot.of("breakfast", 60),)


def test_pattern_은_시각_순이고_겹치는_시각은_한_번만():
    rows = [
        dose(DAY, "dinner", 30),
        dose(DAY, "breakfast", 30),
        dose(date(2026, 9, 29), "dinner", 30),
        dose(date(2026, 9, 29), "breakfast", 30),
    ]

    assert pattern_of(rows, basis=DAY) == (DoseSlot.of("breakfast", 30), DoseSlot.of("dinner", 30))


def test_남은_회차가_없으면_마지막_날에서():
    rows = [
        dose(date(2026, 9, 25), "breakfast", 60),
        dose(date(2026, 9, 26), "breakfast", 30),
        dose(date(2026, 9, 26), "lunch", 30),
    ]

    assert pattern_of(rows, basis=DAY) == (DoseSlot.of("breakfast", 30), DoseSlot.of("lunch", 30))


def test_회차가_없으면_빈_패턴():
    assert pattern_of([], basis=DAY) == ()


def test_fixed_시각은_fixed_time_과_함께_옮긴다():
    rows = [dose(DAY, "fixed", 0, fixed=time(14, 15))]

    assert pattern_of(rows, basis=DAY) == (DoseSlot.of("fixed", 0, fixed_time=time(14, 15)),)


def test_쓰기_메서드는_중단_하나뿐():
    public = {name for name in vars(DbMedicationStore) if not name.startswith("_")}

    assert public == {"active_courses", "courses_overlapping", "doses", "stop_course"}


async def test_시간대_없는_중단_시각은_받지_않는다():
    opened: list[int] = []
    store = DbMedicationStore(opener(opened))

    with pytest.raises(ValueError):
        await store.stop_course(
            child_id=CHILD, schedule_id=SCHEDULE, stopped_at=datetime(2026, 9, 28, 8, 0)
        )

    assert opened == []  # 세션도 열지 않았다


async def test_시간대가_붙은_중단_시각은_세션을_연다():
    opened: list[int] = []
    store = DbMedicationStore(opener(opened, fail=OperationalError("UPDATE", {}, Exception("x"))))

    with pytest.raises(OperationalError):
        await store.stop_course(
            child_id=CHILD, schedule_id=SCHEDULE, stopped_at=datetime(2026, 9, 28, tzinfo=UTC)
        )

    assert opened == [1]


@pytest.mark.parametrize("read", ["active", "overlapping", "doses"])
async def test_읽기_실패는_빈_목록이_아니라_예외로_올린다(read):
    down = OperationalError("SELECT ...", {}, Exception("password=secret host=db"))
    store = DbMedicationStore(opener(fail=down))

    with pytest.raises(OperationalError):
        if read == "active":
            await store.active_courses(child_id=CHILD, today=DAY)
        elif read == "overlapping":
            await store.courses_overlapping(child_id=CHILD, date_from=DAY, date_to=DAY)
        else:
            await store.doses(schedule_ids=[SCHEDULE], date_from=DAY, date_to=DAY)
