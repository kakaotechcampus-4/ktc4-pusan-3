"""복약 알림 발송이 맡을 회차 고르기 (#749) — `claim_due_doses` · `still_due_for`.

예정 시각이 지난 10분 안이고, 체크하지 않았고, 코스가 멈추지 않았고, 예정 시각 전에 켠 구독자가
있는 회차만 맡는다. 맡으면서 `notice_sent_at` 을 찍어 두 번 맡지 않는다.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from app.domains.child.models import ParentChild, ParentChildRelation
from app.domains.identity.models import Parent
from app.domains.medication.models import (
    MedicationCreatedBy,
    MedicationDose,
    MedicationSchedule,
    MedicationSource,
    MedicationStatus,
    TimingAnchor,
)
from app.domains.medication.repository import (
    NewDose,
    add_course,
    add_subscription,
    claim_due_doses,
    delete_subscription,
    list_doses,
    still_due_for,
)

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 27, 9, 35, tzinfo=KST).astimezone(UTC)
WINDOW = timedelta(minutes=10)


async def course_at(session, child, *times: datetime, title="약"):
    """`times`(한국 시각)마다 회차 하나인 코스와 그 회차들."""
    days = sorted({t.astimezone(KST).date() for t in times})
    course = await add_course(
        session,
        child_id=child.id,
        title=title,
        starts_on=days[0],
        ends_on=days[-1],
        source=MedicationSource.UTTERANCE,
        created_by=MedicationCreatedBy.CAREGIVER,
        doses=[
            NewDose(t.astimezone(KST).date(), TimingAnchor.FIXED, 0, t.astimezone(KST).time())
            for t in times
        ],
    )
    return course, await list_doses(session, schedule_ids=[course.id])


async def helper_of(session, child) -> Parent:
    parent = Parent()
    session.add(parent)
    await session.flush()
    session.add(
        ParentChild(parent_id=parent.id, child_id=child.id, relation=ParentChildRelation.OTHER)
    )
    await session.flush()
    return parent


async def subscribe(session, course, parent_id, at: datetime) -> None:
    await add_subscription(session, schedule_id=course.id, parent_id=parent_id, now=at)


async def sent_at(session, dose) -> datetime | None:
    return await session.scalar(
        select(MedicationDose.notice_sent_at)
        .where(MedicationDose.id == dose.id)
        .execution_options(populate_existing=True)
    )


async def test_예정_시각_전에_켠_구독자에게_한_번만_맡는다(session, child):
    course, [dose] = await course_at(session, child, NOW - timedelta(minutes=5))
    late = await helper_of(session, child)
    await subscribe(session, course, child.owner_parent_id, NOW - timedelta(days=1))
    await subscribe(session, course, late.id, NOW - timedelta(minutes=1))  # 예정 시각 뒤에 켰다

    [due] = await claim_due_doses(session, now=NOW, stale_after=WINDOW)

    assert (due.dose_id, due.child_id, due.dose_date) == (dose.id, child.id, date(2026, 10, 27))
    assert due.parent_ids == (child.owner_parent_id,)
    assert await sent_at(session, dose) == NOW
    assert await claim_due_doses(session, now=NOW + timedelta(minutes=1), stale_after=WINDOW) == []


async def test_맡지_않는_회차(session, child):
    """체크함 · 멈춘 코스 · 10분보다 오래 지남 · 아직 안 옴 · 예정 시각 전에 켠 구독자 없음."""
    owner = child.owner_parent_id
    due = NOW - timedelta(minutes=3)
    checked, [dose] = await course_at(session, child, due, title="체크함")
    stopped, _ = await course_at(session, child, due, title="멈춤")
    stale, _ = await course_at(session, child, NOW - timedelta(minutes=10), title="창 밖")
    coming, _ = await course_at(session, child, NOW + timedelta(minutes=1), title="아직")
    late, _ = await course_at(session, child, due, title="늦게 켬")
    for course in (checked, stopped, stale, coming):
        await subscribe(session, course, owner, NOW - timedelta(days=1))
    await subscribe(session, late, owner, NOW - timedelta(minutes=1))
    await session.execute(
        update(MedicationDose).where(MedicationDose.id == dose.id).values(checked_at=NOW)
    )
    await session.execute(
        update(MedicationSchedule)
        .where(MedicationSchedule.id == stopped.id)
        .values(status=MedicationStatus.STOPPED)
    )

    assert await claim_due_doses(session, now=NOW, stale_after=WINDOW) == []


async def test_창이_자정을_걸치면_전날_회차도_맡는다(session, child):
    now = datetime(2026, 10, 28, 0, 5, tzinfo=KST).astimezone(UTC)
    course, [dose] = await course_at(session, child, datetime(2026, 10, 27, 23, 58, tzinfo=KST))
    await subscribe(session, course, child.owner_parent_id, now - timedelta(days=1))

    [due] = await claim_due_doses(session, now=now, stale_after=WINDOW)

    assert (due.dose_id, due.dose_date) == (dose.id, date(2026, 10, 27))


async def test_보내기_직전에_구독이_지워졌거나_체크됐으면_보내지_않는다(session, child):
    course, [dose] = await course_at(session, child, NOW - timedelta(minutes=1))
    helper = await helper_of(session, child)
    for parent_id in (child.owner_parent_id, helper.id):
        await subscribe(session, course, parent_id, NOW - timedelta(days=1))

    assert await still_due_for(session, dose_id=dose.id, parent_id=helper.id)

    await delete_subscription(
        session, child_id=child.id, schedule_id=course.id, parent_id=helper.id
    )
    assert not await still_due_for(session, dose_id=dose.id, parent_id=helper.id)

    await session.execute(
        update(MedicationDose)
        .where(MedicationDose.id == dose.id)
        .values(checked_at=NOW, checked_by=child.owner_parent_id)
    )
    assert not await still_due_for(session, dose_id=dose.id, parent_id=child.owner_parent_id)
