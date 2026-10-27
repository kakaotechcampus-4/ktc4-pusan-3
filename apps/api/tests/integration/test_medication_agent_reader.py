"""복약 어댑터 — 실제 DB 에서만 볼 수 있는 것.

in-memory 는 아이 하나 몫이라 다른 아이를 가르지 못한다. 저장소 동작 전체는
tests/integration/agents/health/test_medication_store_contract_db.py 의 계약 시나리오가 본다.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select, update

from app.agents.health.store.inmemory import course_with_doses
from app.agents.health.store.ports import DoseSlot
from app.domains.medication.models import (
    MedicationDose,
    MedicationSchedule,
    MedicationStatus,
    TimingAnchor,
)
from tests.integration.agents.health.test_medication_store_contract_db import (
    db_store,
    new_child,
    seed_course,
)

TODAY = date(2026, 9, 28)
NOW = datetime(2026, 9, 27, 16, 0, tzinfo=UTC)  # KST 9/28 01:00
BREAKFAST = DoseSlot.of("breakfast", 30)
LUNCH = DoseSlot.of("lunch", 30)


@pytest.fixture
async def two_children(session):
    return await new_child(session, "첫째"), await new_child(session, "둘째")


async def seeded(session, child, slots=(BREAKFAST, LUNCH), **kwargs):
    course, doses = course_with_doses(
        "항생제", slots, TODAY - timedelta(days=1), TODAY + timedelta(days=1), **kwargs
    )
    await seed_course(session, child, course, doses)
    return course


async def stored(session, course) -> MedicationSchedule:
    row = await session.scalar(
        select(MedicationSchedule)
        .where(MedicationSchedule.id == course.id)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


async def test_다른_아이의_코스는_읽히지_않는다(session, two_children):
    mine, other = two_children
    theirs = await seeded(session, other)
    store = db_store(session)

    active = await store.active_courses(child_id=mine.id, today=TODAY)
    overlapping = await store.courses_overlapping(child_id=mine.id, date_from=TODAY, date_to=TODAY)

    assert active == [] and overlapping == []
    assert [c.id for c in await store.active_courses(child_id=other.id, today=TODAY)] == [theirs.id]


@pytest.mark.parametrize("status", ["active", "stopped"])
async def test_다른_아이의_코스는_멈추지_않고_missing(session, two_children, status):
    mine, other = two_children
    stopped_at = NOW - timedelta(days=1) if status == "stopped" else None
    theirs = await seeded(session, other, status=status, stopped_at=stopped_at)
    before = (await stored(session, theirs)).updated_at

    result = await db_store(session).stop_course(
        child_id=mine.id, schedule_id=theirs.id, stopped_at=NOW
    )

    row = await stored(session, theirs)
    assert result == "missing"  # 이미 멈춘 코스여도 "already" 로 있다는 것을 알리지 않는다
    assert (row.status.value, row.updated_at) == (status, before)


async def test_중단은_updated_at_에_넘긴_시각을_쓴다(session, two_children):
    mine, _ = two_children
    course = await seeded(session, mine)

    result = await db_store(session).stop_course(
        child_id=mine.id, schedule_id=course.id, stopped_at=NOW
    )

    row = await stored(session, course)
    assert result == "stopped"
    assert (row.status, row.updated_at) == (MedicationStatus.STOPPED, NOW)


async def test_진행_중_코스의_stopped_at_은_None(session, two_children):
    mine, _ = two_children
    course = await seeded(session, mine)
    assert (await stored(session, course)).updated_at is not None  # 값이 있어도

    [read] = await db_store(session).active_courses(child_id=mine.id, today=TODAY)

    assert read.stopped_at is None


async def test_pattern_은_기준일_이후_회차에서_모은다(session, two_children):
    # 수정으로 앞으로의 시각이 바뀐 코스 — 어제 회차는 옛 시각(아침 +30)이다
    mine, _ = two_children
    old, old_doses = course_with_doses(
        "항생제", (BREAKFAST,), TODAY - timedelta(days=1), TODAY - timedelta(days=1)
    )
    await seed_course(session, mine, old, old_doses)
    # 같은 코스에 오늘부터의 회차(아침 +60)를 더한다
    session.add_all(
        MedicationDose(
            schedule_id=old.id,
            dose_date=TODAY + timedelta(days=n),
            timing_anchor=TimingAnchor.BREAKFAST,
            offset_minutes=60,
        )
        for n in range(2)
    )
    await session.execute(
        update(MedicationSchedule)
        .where(MedicationSchedule.id == old.id)
        .values(ends_on=TODAY + timedelta(days=1))
    )
    await session.flush()

    [read] = await db_store(session).active_courses(child_id=mine.id, today=TODAY)

    assert read.pattern == (DoseSlot.of("breakfast", 60),)
    assert [slot.scheduled_time for slot in read.pattern] == [time(9, 0)]
