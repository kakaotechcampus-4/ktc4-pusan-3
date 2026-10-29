"""복약 동시 요청 — 진짜 트랜잭션 두 개로 확인한다 (#577).

다른 DB 테스트는 세션 하나를 바깥 트랜잭션에 묶고 롤백하지만, 동시성은 그렇게 볼 수 없다 — 한 세션은
다른 세션의 commit 전 쓰기를 못 보는 것이 문제의 핵심이다. 그래서 이 파일은 **별도 세션으로 실제로
commit 하고**, 끝나면 만든 보호자 · 아이를 지운다 (코스 · 회차는 아이를 따라 ON DELETE CASCADE).

겹치는 순간은 sleep 으로 만들고, 기대 결과는 어느 쪽이 먼저 가든 같게 적는다.
"""

import asyncio
from datetime import date

import pytest
from sqlalchemy import delete, func, select

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.medication.models import MedicationSchedule, MedicationSource, TimingAnchor
from app.domains.medication.service import (
    CourseAlreadyExists,
    CourseInput,
    DosePattern,
    create_course,
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
