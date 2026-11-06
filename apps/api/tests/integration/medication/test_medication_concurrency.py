"""복약 동시 요청 — 진짜 트랜잭션 두 개로 확인한다 (#577).

다른 DB 테스트는 세션 하나를 바깥 트랜잭션에 묶고 롤백하지만, 동시성은 그렇게 볼 수 없다 — 한 세션은
다른 세션의 commit 전 쓰기를 못 보는 것이 문제의 핵심이다. 그래서 이 파일은 **별도 세션으로 실제로
commit 하고**, 끝나면 만든 보호자 · 아이를 지운다 (코스 · 회차는 아이를 따라 ON DELETE CASCADE).

겹치는 순간은 sleep 으로 만들거나, 상대가 실제로 잠금에서 기다리는지 DB 로 본다
(`wait_for_lock_wait`). 기대 결과는 어느 쪽이 먼저 가든 같게 적는다.
"""

import asyncio
import contextlib
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import delete, func, select

from app.domains.child.models import Child, ParentChild, ParentChildRelation
from app.domains.child.parents import unlink_parent
from app.domains.identity.models import Parent
from app.domains.identity.withdraw import withdraw_parent
from app.domains.medication import service
from app.domains.medication.models import (
    MedicationCreatedBy,
    MedicationNoticeSubscription,
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
from tests.integration.api.conftest import backend_pid, wait_for_lock_wait

TODAY = date(2026, 10, 27)


async def _new_child() -> tuple:
    async with async_session_factory() as s:
        owner = Parent()
        s.add(owner)
        await s.flush()
        child = Child(owner_parent_id=owner.id, nickname="test", birth_date=date(2023, 1, 1))
        s.add(child)
        await s.flush()
        s.add(
            ParentChild(parent_id=owner.id, child_id=child.id, relation=ParentChildRelation.OTHER)
        )
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
            await create_course(
                s,
                child_id=child_id,
                parent_id=await owner_of(child_id),
                data=course_input(),
                today=TODAY,
                now=NOW,
            )
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
        await create_course(
            s1,
            child_id=first,
            parent_id=await owner_of(first),
            data=course_input(),
            today=TODAY,
            now=NOW,
        )  # commit 전

        # 다른 아이라면 잠금이 겹치지 않아야 한다 — 기다리면 시간 초과다
        await asyncio.wait_for(
            create_course(
                s2,
                child_id=second,
                parent_id=await owner_of(second),
                data=course_input(),
                today=TODAY,
                now=NOW,
            ),
            timeout=3,
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


async def owner_of(child_id):
    """아이를 등록한 보호자 — 체크하는 보호자로 쓴다."""
    async with async_session_factory() as s:
        return await s.scalar(select(Child.owner_parent_id).where(Child.id == child_id))


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
                    s2,
                    child_id=child_id,
                    dose_id=dose_id,
                    parent_id=await owner_of(child_id),
                    checked=True,
                    today=TODAY,
                    now=NOW,
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
            s1,
            child_id=child_id,
            dose_id=dose_id,
            parent_id=await owner_of(child_id),
            checked=True,
            today=TODAY,
            now=NOW,
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
                s2,
                child_id=child_id,
                dose_id=dose_id,
                parent_id=await owner_of(child_id),
                checked=True,
                today=TODAY,
                now=NOW,
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
                parent_id=await owner_of(child_id),
                now=NOW,
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


# ── 체크 · 탈퇴가 같은 보호자를 두고 겹칠 때 (#749) ─────────────────────


async def test_체크가_회차를_잡은_사이에_같은_보호자가_탈퇴해도_교착이_없다(
    make_child, monkeypatch
):
    """탈퇴는 보호자 → 회차 순으로 잠근다(보호자를 잡고 아이 · 회차를 지운다).

    체크가 회차를 먼저 잡고 `checked_by` 를 쓰면 FK 검사가 보호자를 기다려 순서가 뒤집힌다. 체크는
    보호자를 먼저 잡으므로, 회차를 잡은 체크가 끝날 때까지 탈퇴가 기다렸다가 지나간다.
    """
    child_id = await make_child()
    _, dose_id = await seed_evening_dose(child_id)
    owner_id = await owner_of(child_id)
    real = service.find_dose
    holding = asyncio.Event()

    async def slow(*args, **kwargs):
        dose = await real(*args, **kwargs)
        holding.set()
        await asyncio.sleep(0.5)  # 회차를 잡은 채로 탈퇴가 들어올 틈
        return dose

    monkeypatch.setattr(service, "find_dose", slow)
    s1, s2 = async_session_factory(), async_session_factory()

    async def check() -> None:
        await set_dose_checked(
            s1,
            child_id=child_id,
            dose_id=dose_id,
            parent_id=owner_id,
            checked=True,
            today=TODAY,
            now=NOW,
        )
        await s1.commit()

    async def withdraw() -> None:
        await holding.wait()
        await withdraw_parent(s2, parent_id=owner_id, now=NOW)
        await s2.commit()

    try:
        # 교착이면 PG 가 한쪽을 실패시킨다 — 예외를 모아 두 쪽이 다 끝난 뒤에 본다
        results = await asyncio.wait_for(
            asyncio.gather(check(), withdraw(), return_exceptions=True), timeout=10
        )
    finally:
        await s1.close()
        await s2.close()

    assert results == [None, None]
    async with async_session_factory() as s:
        assert await s.get(Parent, owner_id) is None  # 탈퇴가 체크 뒤에 지나갔다


# ── 등록 · 연결 끊기가 같은 보호자를 두고 겹칠 때 (#749) ───────────────


async def _settle(task: asyncio.Task | None) -> None:
    """기다리던 상대 작업을 정리한다 — 단언이 먼저 실패해도 세션을 닫기 전에 끝나게."""
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


async def test_등록이_연결을_잡은_사이에_끊기가_오면_구독까지_지워진다(make_child):
    """함께 보는 보호자가 등록하는 중에 그 보호자의 연결이 끊긴다 (등록 보호자는 끊을 수 없다).

    등록은 연결을 먼저 잡고 등록한 보호자의 구독을 만든다. 끊기가 겹치면 등록 커밋을
    기다렸다가 그 구독까지 지운다 — 잡지 않으면 끊기가 아직 안 보이는 구독을 지나쳐 끊긴 보호자의
    구독이 남는다.
    """
    child_id = await make_child()
    async with async_session_factory() as s:
        helper = Parent()
        s.add(helper)
        await s.flush()
        s.add(
            ParentChild(parent_id=helper.id, child_id=child_id, relation=ParentChildRelation.OTHER)
        )
        await s.commit()
        helper_id = helper.id
    s1, s2 = async_session_factory(), async_session_factory()
    task = None
    try:
        await create_course(
            s1,
            child_id=child_id,
            parent_id=helper_id,
            data=course_input(),
            today=TODAY,
            now=NOW,
        )  # 아직 commit 전

        async def unlink() -> None:
            assert await unlink_parent(s2, child_id=child_id, parent_id=helper_id)
            await s2.commit()

        task = asyncio.create_task(unlink())
        # 끊기가 등록이 잡은 연결 앞에서 실제로 기다리는지 DB 로 본다 — 잡지 않으면 끊기가 기다리지
        # 않고 먼저 끝나 여기서 시간이 다 된다
        await wait_for_lock_wait(blocker=await backend_pid(s1))
        await s1.commit()
        await asyncio.wait_for(task, timeout=5)

        async with async_session_factory() as s:
            left = await s.scalar(
                select(func.count())
                .select_from(MedicationNoticeSubscription)
                .where(MedicationNoticeSubscription.parent_id == helper_id)
            )
        assert left == 0
    finally:
        await _settle(task)
        await s1.close()
        await s2.close()
        async with async_session_factory() as s:
            await s.execute(delete(Parent).where(Parent.id == helper_id))
            await s.commit()


async def test_등록하는_사이_같은_보호자가_탈퇴하면_등록_커밋을_기다렸다가_코스와_구독까지_지운다(
    make_child,
):
    """탈퇴는 보호자 → 아이를 FOR UPDATE 로 잡는데, 등록이 이미 쥔 KEY SHARE 를 기다린다.

    등록 커밋 뒤에 지나가 아이와 함께 코스 · 구독까지 지운다. 순서를 증명하는 테스트는 아니다 —
    `lock_link` 가 없어도 코스 · 구독 INSERT 의 FK 검사가 같은 행을 잡아 이 겹침에서는 결과가 같다.
    여기서는 이번에 등록 경로에 생긴 잠금이 탈퇴와 겹쳐도 멈추지 않고 남는 것이 없다는 것을 본다.
    """
    child_id = await make_child()
    owner_id = await owner_of(child_id)
    s1, s2 = async_session_factory(), async_session_factory()
    task = None
    try:
        await create_course(
            s1,
            child_id=child_id,
            parent_id=owner_id,
            data=course_input(),
            today=TODAY,
            now=NOW,
        )  # 아직 commit 전

        async def withdraw() -> None:
            await withdraw_parent(s2, parent_id=owner_id, now=NOW)
            await s2.commit()

        task = asyncio.create_task(withdraw())
        await wait_for_lock_wait(blocker=await backend_pid(s1))  # 탈퇴가 등록을 기다린다
        await s1.commit()
        await asyncio.wait_for(task, timeout=5)
    finally:
        await _settle(task)
        await s1.close()
        await s2.close()

    async with async_session_factory() as s:
        assert await s.get(Parent, owner_id) is None
        assert await saved_courses(child_id) == 0
        left = await s.scalar(
            select(func.count())
            .select_from(MedicationNoticeSubscription)
            .where(MedicationNoticeSubscription.parent_id == owner_id)
        )
    assert left == 0
