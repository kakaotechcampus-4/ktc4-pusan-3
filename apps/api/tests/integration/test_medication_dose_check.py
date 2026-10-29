"""회차 체크 · 해제 — `service.set_dose_checked` (#577).

체크 칸(`checked_at`)이 곧 복용 기록이다. 해제로 되돌릴 수 있어서 승인 게이트가 아니고, 같은 상태를
두 번 보내도 결과가 같다 — 이미 체크된 회차에 다시 체크해도 **처음 체크 시각이 남는다**.
아직 오지 않은 날의 회차는 서버가 막는다(화면이 날짜를 계산하지 않는다). commit 은 부르는 쪽이 한다.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.medication.models import (
    MedicationCreatedBy,
    MedicationDose,
    MedicationSource,
    TimingAnchor,
)
from app.domains.medication.repository import NewDose, add_course, list_doses, stop_schedule
from app.domains.medication.service import DoseNotDue, DoseNotFound, set_dose_checked

TODAY = date(2026, 10, 27)
NOW = datetime(2026, 10, 27, 0, 40, tzinfo=UTC)  # 한국 시간 09:40
LATER = NOW + timedelta(hours=3)


@pytest.fixture
async def child(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return child


async def make_dose(session, child, *, days_from_today=0, anchor=TimingAnchor.BREAKFAST):
    """그 날짜 회차 하나를 가진 코스를 만들고 회차를 돌려준다."""
    when = TODAY + timedelta(days=days_from_today)
    course = await add_course(
        session,
        child_id=child.id,
        title="약",
        starts_on=min(when, TODAY),
        ends_on=max(when, TODAY),
        source=MedicationSource.UTTERANCE,
        created_by=MedicationCreatedBy.CAREGIVER,
        doses=[NewDose(when, anchor, 30, None)],
    )
    (dose,) = await list_doses(session, schedule_ids=[course.id])
    return course, dose


async def check(session, child, dose, checked=True, *, now=NOW, today=TODAY):
    return await set_dose_checked(
        session, child_id=child.id, dose_id=dose.id, checked=checked, today=today, now=now
    )


async def stored(session, dose_id) -> datetime | None:
    row = await session.scalar(
        select(MedicationDose.checked_at)
        .where(MedicationDose.id == dose_id)
        .execution_options(populate_existing=True)
    )
    return row


# ── 1. 체크 · 해제 ───────────────────────────────────────────────────────


async def test_체크하면_체크_시각이_지금으로_기록된다(session, child):
    _, dose = await make_dose(session, child)

    result = await check(session, child, dose)

    assert result.id == dose.id
    assert result.checked_at == NOW
    assert await stored(session, dose.id) == NOW


async def test_해제하면_체크_시각이_비워진다(session, child):
    _, dose = await make_dose(session, child)
    await check(session, child, dose)

    result = await check(session, child, dose, checked=False)

    assert result.checked_at is None
    assert await stored(session, dose.id) is None


async def test_해제한_뒤_다시_체크하면_새_시각이다(session, child):
    _, dose = await make_dose(session, child)
    await check(session, child, dose)
    await check(session, child, dose, checked=False)

    result = await check(session, child, dose, now=LATER)

    assert result.checked_at == LATER


async def test_이미_체크된_회차에_다시_체크해도_처음_시각이_남는다(session, child):
    _, dose = await make_dose(session, child)
    await check(session, child, dose, now=NOW)

    result = await check(session, child, dose, now=LATER)

    assert result.checked_at == NOW
    assert await stored(session, dose.id) == NOW


async def test_체크하지_않은_회차를_해제해도_오류가_아니다(session, child):
    _, dose = await make_dose(session, child)

    result = await check(session, child, dose, checked=False)

    assert result.checked_at is None


async def test_같은_회차만_바뀌고_다른_회차는_그대로다(session, child):
    course = await add_course(
        session,
        child_id=child.id,
        title="약",
        starts_on=TODAY,
        ends_on=TODAY,
        source=MedicationSource.UTTERANCE,
        created_by=MedicationCreatedBy.CAREGIVER,
        doses=[
            NewDose(TODAY, TimingAnchor.BREAKFAST, 30, None),
            NewDose(TODAY, TimingAnchor.LUNCH, 30, None),
        ],
    )
    first, second = await list_doses(session, schedule_ids=[course.id])

    await check(session, child, first)

    assert await stored(session, first.id) == NOW
    assert await stored(session, second.id) is None


# ── 2. 날짜 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("days", [-3, -1, 0])
async def test_오늘까지의_회차는_체크할_수_있다(session, child, days):
    _, dose = await make_dose(session, child, days_from_today=days)

    assert (await check(session, child, dose)).checked_at == NOW


@pytest.mark.parametrize("days", [1, 7])
async def test_아직_오지_않은_날의_회차는_막고_값을_바꾸지_않는다(session, child, days):
    _, dose = await make_dose(session, child, days_from_today=days)

    with pytest.raises(DoseNotDue):
        await check(session, child, dose)

    assert await stored(session, dose.id) is None


async def test_아직_오지_않은_날의_회차는_해제도_막는다(session, child):
    _, dose = await make_dose(session, child, days_from_today=1)

    with pytest.raises(DoseNotDue):
        await check(session, child, dose, checked=False)


async def test_날짜가_바뀌면_어제_내일이_달라진다(session, child):
    _, dose = await make_dose(session, child, days_from_today=1)

    with pytest.raises(DoseNotDue):
        await check(session, child, dose)  # 오늘 기준으로는 내일

    assert (await check(session, child, dose, today=TODAY + timedelta(days=1))).checked_at == NOW


# ── 3. 대상 ──────────────────────────────────────────────────────────────


async def test_없는_회차는_찾지_못한다(session, child):
    import uuid

    ghost = MedicationDose(id=uuid.uuid4())

    with pytest.raises(DoseNotFound):
        await check(session, child, ghost)


async def test_다른_아이의_회차는_찾지_못하고_값을_바꾸지_않는다(session, child):
    other = Child(
        owner_parent_id=child.owner_parent_id, nickname="other", birth_date=date(2022, 1, 1)
    )
    session.add(other)
    await session.flush()
    _, dose = await make_dose(session, other)

    with pytest.raises(DoseNotFound):
        await check(session, child, dose)

    assert await stored(session, dose.id) is None


async def test_멈춘_코스의_회차는_체크도_해제도_찾지_못한다(session, child):
    course, dose = await make_dose(session, child)
    await check(session, child, dose)
    await stop_schedule(
        session, child_id=child.id, schedule_id=course.id, stopped_at=datetime.now(UTC)
    )

    with pytest.raises(DoseNotFound):
        await check(session, child, dose, checked=False)
    with pytest.raises(DoseNotFound):
        await check(session, child, dose, now=LATER)

    assert await stored(session, dose.id) == NOW  # 멈춘 뒤에도 복용 기록은 그대로 남는다


async def test_찾지_못함이_아직_오지_않음보다_먼저다(session, child):
    other = Child(
        owner_parent_id=child.owner_parent_id, nickname="other", birth_date=date(2022, 1, 1)
    )
    session.add(other)
    await session.flush()
    _, dose = await make_dose(session, other, days_from_today=3)

    with pytest.raises(DoseNotFound):  # 남의 아이 회차는 날짜가 미래여도 422 가 아니라 404 다
        await check(session, child, dose)


# ── 4. 트랜잭션 ──────────────────────────────────────────────────────────


async def test_서비스는_commit_하지_않아_롤백하면_체크가_사라진다(session, child):
    _, dose = await make_dose(session, child)
    dose_id = dose.id
    await session.commit()

    await check(session, child, dose)
    await session.rollback()

    assert await stored(session, dose_id) is None


async def test_회차_시각은_바뀌지_않는다(session, child):
    _, dose = await make_dose(session, child)

    result = await check(session, child, dose)

    assert (result.dose_date, result.scheduled_time) == (TODAY, time(8, 30))
