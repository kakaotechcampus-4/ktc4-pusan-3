"""복약 알림 구독 — 등록 때 기본 구독 · 켜기 · 끄기 · 연결 끊기 정리 (#749).

구독은 보호자 개인 것이다. 등록한 보호자만 기본으로 켜지고, 다른 보호자는 각자 켠다. 행이 있으면
받고 끄면 지운다. `subscribed_at` 은 켠 시각이라 이보다 앞선 회차는 발송이 보내지 않는다.
commit 은 부르는 쪽이 한다.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.domains.child.models import ParentChild, ParentChildRelation
from app.domains.child.parents import unlink_parent
from app.domains.identity.models import Parent
from app.domains.medication.models import (
    MedicationNoticeSubscription,
    MedicationSchedule,
    MedicationSource,
    MedicationStatus,
    TimingAnchor,
)
from app.domains.medication.service import (
    CourseFields,
    CourseInput,
    CourseNotFound,
    DosePattern,
    NotLinked,
    create_course,
    revise_course,
    subscribe,
    unsubscribe,
)

TODAY = date(2026, 10, 27)
NOW = datetime(2026, 10, 27, 0, 40, tzinfo=UTC)  # 한국 시간 09:40
LATER = NOW + timedelta(hours=3)
DOSES = [DosePattern(TimingAnchor.BREAKFAST, 30, None)]


@pytest.fixture
async def helper(session, child):
    """같은 아이에 연결된 다른 보호자."""
    parent = Parent()
    session.add(parent)
    await session.flush()
    session.add(
        ParentChild(parent_id=parent.id, child_id=child.id, relation=ParentChildRelation.OTHER)
    )
    await session.flush()
    return parent


async def register(session, child, *, starts_on=TODAY, ends_on=TODAY + timedelta(days=2)):
    """아이를 등록한 보호자가 코스를 등록한다."""
    return await create_course(
        session,
        child_id=child.id,
        parent_id=child.owner_parent_id,
        data=CourseInput(
            title="약",
            starts_on=starts_on,
            ends_on=ends_on,
            source=MedicationSource.UTTERANCE,
            doses=DOSES,
        ),
        today=TODAY,
        now=NOW,
    )


async def subscriptions(session, course_id) -> dict:
    """{보호자 id: 켠 시각}."""
    rows = await session.execute(
        select(MedicationNoticeSubscription.parent_id, MedicationNoticeSubscription.subscribed_at)
        .where(MedicationNoticeSubscription.schedule_id == course_id)
        .execution_options(populate_existing=True)
    )
    return dict(rows.all())


async def turn_on(session, child, course, parent, *, now=NOW):
    await subscribe(
        session,
        child_id=child.id,
        course_id=course.id,
        parent_id=parent.id,
        today=TODAY,
        now=now,
    )


# ── 1. 등록 ──────────────────────────────────────────────────────────────


async def test_등록하면_등록한_보호자만_등록한_시각으로_구독된다(session, child, helper):
    course = await register(session, child)

    assert await subscriptions(session, course.id) == {child.owner_parent_id: NOW}


async def test_연결되지_않은_보호자는_등록하지_못한다(session, child, helper):
    """접근 확인 뒤 그사이 연결이 끊긴 경우 — 저장 전에 `NotLinked` 다(API 는 403)."""
    await unlink_parent(session, child_id=child.id, parent_id=helper.id)

    with pytest.raises(NotLinked):
        await create_course(
            session,
            child_id=child.id,
            parent_id=helper.id,
            data=CourseInput(
                title="약",
                starts_on=TODAY,
                ends_on=TODAY,
                source=MedicationSource.UTTERANCE,
                doses=DOSES,
            ),
            today=TODAY,
            now=NOW,
        )


async def test_코스를_수정해도_구독은_그대로다(session, child, helper):
    course = await register(session, child)
    await turn_on(session, child, course, helper, now=LATER)

    await revise_course(
        session,
        child_id=child.id,
        course_id=course.id,
        data=CourseFields(
            title="약",
            starts_on=TODAY,
            ends_on=TODAY + timedelta(days=3),
            doses=[DosePattern(TimingAnchor.LUNCH, 30, None)],
        ),
        today=TODAY,
        now=LATER,
    )

    assert await subscriptions(session, course.id) == {child.owner_parent_id: NOW, helper.id: LATER}


# ── 2. 켜기 · 끄기 ───────────────────────────────────────────────────────


async def test_다른_보호자가_켜면_켠_시각으로_구독되고_이미_켜져_있으면_처음_시각이_남는다(
    session, child, helper
):
    course = await register(session, child)

    await turn_on(session, child, course, helper, now=NOW)
    await turn_on(session, child, course, helper, now=LATER)

    assert (await subscriptions(session, course.id))[helper.id] == NOW


async def test_끄면_내_구독만_지워지고_다시_켜면_새_시각이다(session, child, helper):
    course = await register(session, child)
    await turn_on(session, child, course, helper)

    await unsubscribe(session, child_id=child.id, course_id=course.id, parent_id=helper.id)
    assert await subscriptions(session, course.id) == {child.owner_parent_id: NOW}

    await turn_on(session, child, course, helper, now=LATER)
    assert (await subscriptions(session, course.id))[helper.id] == LATER


async def test_켜져_있지_않은_구독을_꺼도_오류가_아니다(session, child, helper):
    course = await register(session, child)

    await unsubscribe(session, child_id=child.id, course_id=course.id, parent_id=helper.id)

    assert await subscriptions(session, course.id) == {child.owner_parent_id: NOW}


async def test_아직_시작하지_않은_코스도_켤_수_있다(session, child, helper):
    course = await register(session, child, starts_on=TODAY + timedelta(days=1))

    await turn_on(session, child, course, helper)

    assert helper.id in await subscriptions(session, course.id)


@pytest.mark.parametrize("state", ["stopped", "ended"])
async def test_멈추거나_끝난_코스는_켤_수_없다(session, child, helper, state):
    course = await register(session, child)
    values = (
        {"status": MedicationStatus.STOPPED}
        if state == "stopped"
        else {"ends_on": TODAY - timedelta(days=1), "starts_on": TODAY - timedelta(days=2)}
    )
    await session.execute(
        update(MedicationSchedule).where(MedicationSchedule.id == course.id).values(**values)
    )

    with pytest.raises(CourseNotFound):
        await turn_on(session, child, course, helper)

    assert helper.id not in await subscriptions(session, course.id)


async def test_다른_아이의_코스는_켜지도_끄지도_못한다(session, child, other_child):
    course = await register(session, child)
    stranger = await session.get(Parent, other_child.owner_parent_id)

    with pytest.raises(CourseNotFound):
        await turn_on(session, other_child, course, stranger)
    await unsubscribe(
        session, child_id=other_child.id, course_id=course.id, parent_id=child.owner_parent_id
    )

    assert await subscriptions(session, course.id) == {child.owner_parent_id: NOW}


async def test_연결되지_않은_보호자는_켤_수_없다(session, child, other_child):
    course = await register(session, child)
    stranger = await session.get(Parent, other_child.owner_parent_id)

    with pytest.raises(NotLinked):
        await turn_on(session, child, course, stranger)


# ── 3. 연결 끊기 ─────────────────────────────────────────────────────────


async def test_연결을_끊으면_그_보호자의_이_아이_구독만_지워진다(session, child, helper):
    course = await register(session, child)
    await turn_on(session, child, course, helper)

    assert await unlink_parent(session, child_id=child.id, parent_id=helper.id)

    assert await subscriptions(session, course.id) == {child.owner_parent_id: NOW}
