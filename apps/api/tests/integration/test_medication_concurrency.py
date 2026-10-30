"""복약 동시 요청 — 진짜 트랜잭션 두 개로 확인한다 (#577).

다른 DB 테스트는 세션 하나를 바깥 트랜잭션에 묶고 롤백하지만, 동시성은 그렇게 볼 수 없다 — 한 세션은
다른 세션의 commit 전 쓰기를 못 보는 것이 문제의 핵심이다. 그래서 이 파일은 **별도 세션으로 실제로
commit 하고**, 끝나면 만든 보호자 · 아이를 지운다 (코스 · 회차는 아이를 따라 ON DELETE CASCADE).

겹치는 순간은 sleep 으로 만들고, 기대 결과는 어느 쪽이 먼저 가든 같게 적는다.
"""

import asyncio
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import delete, func, select

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.medication import service
from app.domains.medication.models import (
    MedicationCreatedBy,
    MedicationSchedule,
    MedicationSource,
    TimingAnchor,
)
from app.domains.medication.repository import NewDose, add_course, list_doses
from app.domains.medication.service import (
    CourseAlreadyExists,
    CourseFields,
    CourseInput,
    DoseNotFound,
    DosePattern,
    create_course,
    revise_course,
    set_dose_checked,
)
from app.infra.db.session import async_session_factory

TODAY = date(2026, 10, 27)


async def _new_child() -> tuple:
    async with async_session_factory() as s:
        owner = Parent()
        s.add(owner)
        await s.flush()
        child = Child(owner_parent_id=owner.id, nickname="test", birth_date=date(2023, 1, 1))
        s.add(child)
        await s.commit()
        return owner.id, child.id


@pytest.fixture
async def make_child():
    """commit 된 아이를 만들고, 테스트가 끝나면 지운다."""
    made: list[tuple] = []

    async def make() -> object:
        owner_id, child_id = await _new_child()
        made.append((owner_id, child_id))
        return child_id

    yield make

    async with async_session_factory() as s:
        for owner_id, child_id in made:
            await s.execute(delete(Child).where(Child.id == child_id))
            await s.execute(delete(Parent).where(Parent.id == owner_id))
        await s.commit()


def course_input() -> CourseInput:
    return CourseInput(
        title="아목시실린",
        starts_on=TODAY,
        ends_on=TODAY,
        source=MedicationSource.UTTERANCE,
        doses=[DosePattern(TimingAnchor.BREAKFAST, 30, None)],
    )


async def saved_courses(child_id) -> int:
    async with async_session_factory() as s:
        return await s.scalar(
            select(func.count())
            .select_from(MedicationSchedule)
            .where(MedicationSchedule.child_id == child_id)
        )


async def register(child_id, *, hold: float = 0.3) -> str:
    """등록하고, 검사와 commit 사이에 다른 요청이 끼어들 틈(hold)을 준 뒤 commit 한다."""
    async with async_session_factory() as s:
        try:
            await create_course(s, child_id=child_id, data=course_input(), today=TODAY)
        except CourseAlreadyExists:
            await s.rollback()
            return "중복"
        await asyncio.sleep(hold)
        await s.commit()
        return "저장"


async def test_같은_코스를_동시에_등록하면_하나만_저장되고_나머지는_중복이다(make_child):
    child_id = await make_child()

    results = await asyncio.gather(register(child_id), register(child_id))

    assert sorted(results) == ["저장", "중복"]
    assert await saved_courses(child_id) == 1


async def test_세_요청이_동시에_와도_하나만_저장된다(make_child):
    child_id = await make_child()

    results = await asyncio.gather(*(register(child_id, hold=0.2) for _ in range(3)))

    assert sorted(results) == ["저장", "중복", "중복"]
    assert await saved_courses(child_id) == 1


async def test_다른_아이의_등록은_서로_기다리지_않는다(make_child):
    first, second = await make_child(), await make_child()
    async with async_session_factory() as s1, async_session_factory() as s2:
        await create_course(s1, child_id=first, data=course_input(), today=TODAY)  # commit 전

        # 다른 아이라면 잠금이 겹치지 않아야 한다 — 기다리면 시간 초과다
        await asyncio.wait_for(
            create_course(s2, child_id=second, data=course_input(), today=TODAY), timeout=3
        )
        await s1.commit()
        await s2.commit()

    assert await saved_courses(first) == await saved_courses(second) == 1


# ── 체크 · 수정이 같은 회차를 두고 겹칠 때 ─────────────────────────────

NOW = datetime(2026, 10, 27, 0, 40, tzinfo=UTC)  # 한국 시간 09:40


async def seed_evening_dose(child_id) -> tuple:
    """오늘 19:30 미체크 회차 하나를 가진 코스 — commit 까지 한다."""
    async with async_session_factory() as s:
        course = await add_course(
            s,
            child_id=child_id,
            title="약",
            starts_on=TODAY,
            ends_on=TODAY,
            source=MedicationSource.UTTERANCE,
            created_by=MedicationCreatedBy.CAREGIVER,
            doses=[NewDose(TODAY, TimingAnchor.FIXED, 0, time(19, 30))],
        )
        await s.commit()
        (dose,) = await list_doses(s, schedule_ids=[course.id])
        return course.id, dose.id


async def dose_rows(course_id) -> list[tuple[time, bool]]:
    async with async_session_factory() as s:
        doses = await list_doses(s, schedule_ids=[course_id])
        return [(d.scheduled_time, d.checked_at is not None) for d in doses]


def to_evening_20() -> CourseFields:
    return CourseFields(
        title="약",
        starts_on=TODAY,
        ends_on=TODAY,
        doses=[DosePattern(TimingAnchor.FIXED, 0, time(20, 0))],
    )


async def test_체크하는_동안_수정이_그_회차를_지우면_체크는_찾지_못함이다(make_child):
    child_id = await make_child()
    course_id, dose_id = await seed_evening_dose(child_id)
    s1, s2 = async_session_factory(), async_session_factory()
    try:
        # 수정이 19:30 회차를 지운다 — 아직 commit 전
        await revise_course(
            s1, child_id=child_id, course_id=course_id, data=to_evening_20(), today=TODAY, now=NOW
        )

        async def check() -> str:
            try:
                await set_dose_checked(
                    s2, child_id=child_id, dose_id=dose_id, checked=True, today=TODAY, now=NOW
                )
                await s2.commit()
                return "체크됨"
            except DoseNotFound:
                await s2.rollback()
                return "찾지 못함"

        task = asyncio.create_task(check())
        await asyncio.sleep(0.4)  # 체크가 지워지는 회차를 잡고 기다릴 틈
        await s1.commit()
        outcome = await asyncio.wait_for(task, timeout=5)
    finally:
        await s1.close()
        await s2.close()

    assert outcome == "찾지 못함"  # 500 이 아니다
    assert await dose_rows(course_id) == [(time(20, 0), False)]


async def test_체크가_먼저_회차를_잡았으면_수정은_그_회차를_지우지_않는다(make_child):
    child_id = await make_child()
    course_id, dose_id = await seed_evening_dose(child_id)
    s1, s2 = async_session_factory(), async_session_factory()
    try:
        await set_dose_checked(
            s1, child_id=child_id, dose_id=dose_id, checked=True, today=TODAY, now=NOW
        )  # 아직 commit 전

        task = asyncio.create_task(
            revise_course(
                s2,
                child_id=child_id,
                course_id=course_id,
                data=to_evening_20(),
                today=TODAY,
                now=NOW,
            )
        )
        await asyncio.sleep(0.4)  # 수정이 체크한 회차 앞에서 기다릴 틈
        await s1.commit()
        await asyncio.wait_for(task, timeout=5)
        await s2.commit()
    finally:
        await s1.close()
        await s2.close()

    # 체크한 19:30 은 기록으로 남고, 새 패턴 20:00 만 더해진다
    assert await dose_rows(course_id) == [(time(19, 30), True), (time(20, 0), False)]


async def test_수정이_남는_회차를_검사하는_사이에_체크가_끼어들_수_없다(make_child, monkeypatch):
    """오늘 코스를 내일 시작으로 고치는 도중에 오늘 회차를 체크해도, 기간 밖에 기록이 남지 않는다.

    검사(`has_kept_outside`)는 그 회차가 곧 지워질 것이라 통과시킨다. 검사와 삭제 사이에 체크가
    commit 되면 삭제가 그 회차를 건너뛰어 새 기간 밖에 체크한 회차가 남는다.
    """
    child_id = await make_child()
    course_id, dose_id = await seed_evening_dose(child_id)
    tomorrow = TODAY + timedelta(days=1)
    to_tomorrow = CourseFields(
        title="약",
        starts_on=tomorrow,
        ends_on=tomorrow + timedelta(days=1),
        doses=[DosePattern(TimingAnchor.FIXED, 0, time(20, 0))],
    )
    real = service.has_kept_outside
    in_gap = asyncio.Event()

    async def slow(*args, **kwargs):
        outside = await real(*args, **kwargs)
        in_gap.set()
        await asyncio.sleep(0.5)  # 검사와 삭제 사이의 틈
        return outside

    monkeypatch.setattr(service, "has_kept_outside", slow)
    s1, s2 = async_session_factory(), async_session_factory()

    async def revise() -> None:
        await revise_course(
            s1, child_id=child_id, course_id=course_id, data=to_tomorrow, today=TODAY, now=NOW
        )
        await s1.commit()

    async def check() -> str:
        await in_gap.wait()
        try:
            await set_dose_checked(
                s2, child_id=child_id, dose_id=dose_id, checked=True, today=TODAY, now=NOW
            )
            await s2.commit()
            return "체크됨"
        except DoseNotFound:
            await s2.rollback()
            return "찾지 못함"

    try:
        _, outcome = await asyncio.wait_for(asyncio.gather(revise(), check()), timeout=10)
    finally:
        await s1.close()
        await s2.close()

    assert outcome == "찾지 못함"  # 수정이 끝난 뒤에야 체크가 지나가고, 그 회차는 이미 없다
    async with async_session_factory() as s:
        doses = await list_doses(s, schedule_ids=[course_id])
    assert [(d.dose_date, d.checked_at is not None) for d in doses] == [
        (tomorrow, False),
        (tomorrow + timedelta(days=1), False),
    ]


async def test_두_코스를_동시에_같은_이름_기간으로_수정하면_하나만_된다(make_child):
    child_id = await make_child()
    ids = []
    for title in ("가", "나"):
        async with async_session_factory() as s:
            data = course_input()
            course = await create_course(
                s,
                child_id=child_id,
                data=CourseInput(
                    title=title,
                    starts_on=data.starts_on,
                    ends_on=data.ends_on,
                    source=data.source,
                    doses=data.doses,
                ),
                today=TODAY,
            )
            await s.commit()
            ids.append(course.id)

    async def rename(course_id) -> str:
        async with async_session_factory() as s:
            try:
                await revise_course(
                    s,
                    child_id=child_id,
                    course_id=course_id,
                    data=CourseFields(
                        title="같은 이름",
                        starts_on=TODAY,
                        ends_on=TODAY,
                        doses=[DosePattern(TimingAnchor.FIXED, 0, time(20, 0))],
                    ),
                    today=TODAY,
                    now=NOW,
                )
            except CourseAlreadyExists:
                await s.rollback()
                return "중복"
            await asyncio.sleep(0.3)
            await s.commit()
            return "수정"

    results = await asyncio.gather(rename(ids[0]), rename(ids[1]))

    assert sorted(results) == ["수정", "중복"]
