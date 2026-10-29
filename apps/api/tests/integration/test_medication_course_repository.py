"""복약 코스 저장 — `repository.add_course` (#577).

repository 는 flush 까지만 하고 commit 은 부르는 쪽이 한다. 입력 검증(길이 · 기간 · 증상 코드)과
회차 펼치기는 서비스 몫이라 여기서는 이미 만들어진 회차 행을 받는다. DB 가 막는 것만 확인한다.

CHECK 위반도 ORM 으로 넣는다 — 이 표의 CHECK 가 막는 값(offset 범위 · fixed 조합)은 enum 이 아니라
SQLAlchemy 가 먼저 거절하지 않는다.
"""

from datetime import date, time, timedelta

import pytest
from sqlalchemy import func, select, true
from sqlalchemy.exc import IntegrityError

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.medication.models import (
    MedicationCreatedBy,
    MedicationDose,
    MedicationSchedule,
    MedicationSource,
    MedicationStatus,
    TimingAnchor,
)
from app.domains.medication.repository import NewDose, add_course, list_courses, list_doses

STARTS = date(2026, 10, 27)
ENDS = date(2026, 10, 29)


@pytest.fixture
async def child(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return child


def dose(
    day: date,
    anchor: TimingAnchor = TimingAnchor.BREAKFAST,
    offset: int = 30,
    fixed: time | None = None,
) -> NewDose:
    return NewDose(dose_date=day, timing_anchor=anchor, offset_minutes=offset, fixed_time=fixed)


def meals_after_30(starts: date, days: int) -> list[NewDose]:
    meals = (TimingAnchor.BREAKFAST, TimingAnchor.LUNCH, TimingAnchor.DINNER)
    return [dose(starts + timedelta(days=d), meal) for d in range(days) for meal in meals]


async def add(session, child, **over) -> MedicationSchedule:
    values = {
        "child_id": child.id,
        "title": "아목시실린",
        "starts_on": STARTS,
        "ends_on": ENDS,
        "source": MedicationSource.UTTERANCE,
        "created_by": MedicationCreatedBy.CAREGIVER,
        "doses": meals_after_30(STARTS, 3),
    }
    return await add_course(session, **values | over)


async def count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


# ── 1. 저장되는 것 ───────────────────────────────────────────────────────


async def test_코스_1행과_회차_9행이_저장되고_회차는_그_코스를_가리킨다(session, child):
    course = await add(session, child)

    doses = await list_doses(session, schedule_ids=[course.id])
    assert len(doses) == 9
    assert {d.schedule_id for d in doses} == {course.id}
    assert all(d.checked_at is None for d in doses)
    assert await count(session, MedicationSchedule) == 1


async def test_코스는_받은_아이에게만_붙고_다른_아이의_목록에는_안_보인다(session, child):
    other = Child(
        owner_parent_id=child.owner_parent_id, nickname="other", birth_date=date(2022, 1, 1)
    )
    session.add(other)
    await session.flush()

    course = await add(session, child)

    assert course.child_id == child.id
    mine = await list_courses(session, child_id=child.id, where=true())
    theirs = await list_courses(session, child_id=other.id, where=true())
    assert [c.id for c in mine] == [course.id]
    assert theirs == []


async def test_받은_값을_그대로_저장한다_앞뒤_공백도_지우지_않는다(session, child):
    course = await add(
        session,
        child,
        title="  아목시실린 ",
        dosage="5ml",
        indication_text="중이염",
        note="식후 30분",
        storage="fridge",
        med_form="liquid",
        symptom_codes=["fever"],
    )

    assert course.title == "  아목시실린 "
    assert (course.dosage, course.indication_text, course.note) == ("5ml", "중이염", "식후 30분")
    assert (course.storage, course.med_form) == ("fridge", "liquid")
    assert course.symptom_codes == ["fever"]
    assert (course.starts_on, course.ends_on) == (STARTS, ENDS)
    assert course.status == MedicationStatus.ACTIVE
    assert course.created_by == MedicationCreatedBy.CAREGIVER
    assert course.source == MedicationSource.UTTERANCE


async def test_선택_칸을_생략하면_null_이고_증상_코드는_빈_목록(session, child):
    course = await add(session, child)

    assert course.dosage is None
    assert course.indication_text is None
    assert course.note is None
    assert course.storage is None
    assert course.med_form is None
    assert course.symptom_codes == []


# ── 2. DB 가 계산하는 시각 ───────────────────────────────────────────────


async def test_식후_30분_세_번이면_알림_시각은_0830_1230_1930(session, child):
    course = await add(session, child)

    doses = await list_doses(session, schedule_ids=[course.id])
    assert [d.scheduled_time for d in doses] == [time(8, 30), time(12, 30), time(19, 30)] * 3


async def test_fixed_회차는_고른_시각이_그대로_알림_시각(session, child):
    course = await add(
        session,
        child,
        doses=[dose(STARTS, TimingAnchor.FIXED, offset=0, fixed=time(21, 0))],
    )

    (only,) = await list_doses(session, schedule_ids=[course.id])
    assert only.scheduled_time == time(21, 0)
    assert only.fixed_time == time(21, 0)


# ── 3. 경계값 · DB 제약 ──────────────────────────────────────────────────


@pytest.mark.parametrize("offset", [-120, 180])
async def test_offset_경계_안은_저장된다(session, child, offset):
    course = await add(session, child, doses=[dose(STARTS, offset=offset)])

    (only,) = await list_doses(session, schedule_ids=[course.id])
    assert only.offset_minutes == offset


@pytest.mark.parametrize("offset", [-121, 181])
async def test_offset_경계_밖은_DB_가_거절한다(session, child, offset):
    with pytest.raises(IntegrityError):
        await add(session, child, doses=[dose(STARTS, offset=offset)])


async def test_fixed_인데_fixed_time_이_없으면_거절한다(session, child):
    with pytest.raises(IntegrityError):
        await add(session, child, doses=[dose(STARTS, TimingAnchor.FIXED, offset=0, fixed=None)])


async def test_fixed_인데_offset_이_0_이_아니면_거절한다(session, child):
    with pytest.raises(IntegrityError):
        await add(
            session,
            child,
            doses=[dose(STARTS, TimingAnchor.FIXED, offset=30, fixed=time(21, 0))],
        )


async def test_같은_날_같은_알림_시각이_둘이면_거절한다(session, child):
    # 아침 + 30분 과 fixed 08:30 은 anchor 가 달라도 알림 시각이 같다
    same_time = [
        dose(STARTS, TimingAnchor.BREAKFAST, offset=30),
        dose(STARTS, TimingAnchor.FIXED, offset=0, fixed=time(8, 30)),
    ]
    with pytest.raises(IntegrityError):
        await add(session, child, doses=same_time)


async def test_없는_아이의_코스는_저장하지_않는다(session, child):
    import uuid

    with pytest.raises(IntegrityError):
        await add(session, child, child_id=uuid.uuid4())


# ── 4. 트랜잭션 ──────────────────────────────────────────────────────────


async def test_회차가_거절되면_부른_쪽이_롤백해서_코스도_회차도_남지_않는다(session, child):
    # repository 가 내부에서 commit 하면 코스 행이 남아 이 테스트가 깨진다
    child_id = child.id  # rollback 뒤에는 객체가 만료돼 속성을 읽을 수 없다
    await session.commit()  # 아이는 남기고, 아래 쓰기만 되돌아가는지 본다

    with pytest.raises(IntegrityError):
        await add(session, child, doses=[dose(STARTS, offset=181)])
    await session.rollback()

    assert await count(session, MedicationSchedule) == 0
    assert await count(session, MedicationDose) == 0
    assert await session.get(Child, child_id) is not None


async def test_저장이_성공해도_commit_은_부른_쪽이_해서_롤백하면_사라진다(session, child):
    await session.commit()

    await add(session, child)
    await session.rollback()

    assert await count(session, MedicationSchedule) == 0
    assert await count(session, MedicationDose) == 0


# ── 5. 크기 · 비어 있음 ──────────────────────────────────────────────────


async def test_90일_하루_세_번_270행을_한_번에_저장한다(session, child):
    starts = date(2026, 11, 1)
    course = await add(
        session,
        child,
        starts_on=starts,
        ends_on=starts + timedelta(days=89),
        doses=meals_after_30(starts, 90),
    )

    assert (
        await session.scalar(
            select(func.count())
            .select_from(MedicationDose)
            .where(MedicationDose.schedule_id == course.id)
        )
        == 270
    )


async def test_회차가_0개여도_코스는_저장된다_provisional(session, child):
    # 기간 전체가 과거인 코스를 거절할지는 미정이다. 거절한다면 서비스 단계에서 막는다
    course = await add(session, child, doses=[])

    assert await list_doses(session, schedule_ids=[course.id]) == []
    assert await count(session, MedicationSchedule) == 1
