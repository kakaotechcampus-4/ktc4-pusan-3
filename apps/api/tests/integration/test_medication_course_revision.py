"""복약 코스 수정 — `service.revise_course` (#577).

수정은 보호자가 확인한 **최종 상태 전체**를 받는다. 복용 기록(체크)은 지우지 않는다.

    · 체크한 회차와 이미 지난 회차는 그대로 둔다 — 새 패턴에서 빠져도 기록은 남는다
    · 미체크이면서 예정 시각이 지금 이후인 회차만 지우고 새 패턴으로 다시 만든다 (id 가 바뀐다)
    · 다시 만들 때 지금 이전의 시각은 만들지 않는다 (등록은 오늘 회차를 통째로 만든다 — 다르다)
    · 체크한 회차가 새 기간 밖에 남게 되는 수정은 거절한다

시각을 정확히 놓으려고 회차는 fixed 시각으로 만든다. 지금은 한국 시간 09:40 이다.
provisional — #563 에서 정하지 못한 것: 다른 진행 코스와 같아지는 수정의 거절, 기간 전체가 과거인
수정의 거절.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import text, update

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.medication.models import (
    MedicationCreatedBy,
    MedicationDose,
    MedicationForm,
    MedicationSource,
    MedicationStatus,
    MedicationStorage,
    TimingAnchor,
)
from app.domains.medication.repository import NewDose, add_course, list_doses, stop_schedule
from app.domains.medication.service import (
    COURSE_LOCK_NS,
    CourseAlreadyExists,
    CourseFields,
    CourseInvalid,
    CourseNotFound,
    DosePattern,
    course_lock_key,
    revise_course,
)

TODAY = date(2026, 10, 27)
NOW = datetime(2026, 10, 27, 0, 40, tzinfo=UTC)  # 한국 시간 09:40
FIXED = TimingAnchor.FIXED


@pytest.fixture
async def child(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return child


def day(offset: int) -> date:
    return TODAY + timedelta(days=offset)


def fx(offset: int, hour: int, minute: int = 0) -> NewDose:
    return NewDose(day(offset), FIXED, 0, time(hour, minute))


def pat(hour: int, minute: int = 0) -> DosePattern:
    return DosePattern(FIXED, 0, time(hour, minute))


async def seed(session, child, doses, *, starts=-2, ends=2, title="약", **over):
    return await add_course(
        session,
        child_id=child.id,
        title=title,
        starts_on=day(starts),
        ends_on=day(ends),
        source=MedicationSource.PRESCRIPTION_OCR,
        created_by=MedicationCreatedBy.AGENT,
        doses=doses,
        **over,
    )


def daily(hour: int, first: int, last: int) -> list[NewDose]:
    return [fx(n, hour) for n in range(first, last + 1)]


async def revise(session, child, course, *, starts=None, ends=None, patterns=None, now=NOW, **over):
    data = CourseFields(
        **{
            "title": course.title,
            "starts_on": course.starts_on if starts is None else day(starts),
            "ends_on": course.ends_on if ends is None else day(ends),
            "doses": [pat(9)] if patterns is None else patterns,
        }
        | over
    )
    return await revise_course(
        session, child_id=child.id, course_id=course.id, data=data, today=TODAY, now=now
    )


async def rows(session, course) -> list[tuple[date, time, bool]]:
    doses = await list_doses(session, schedule_ids=[course.id])
    return [(d.dose_date, d.scheduled_time, d.checked_at is not None) for d in doses]


async def check(session, course, offset: int, hour: int, minute: int = 0) -> None:
    await session.execute(
        update(MedicationDose)
        .where(
            MedicationDose.schedule_id == course.id,
            MedicationDose.dose_date == day(offset),
            MedicationDose.scheduled_time == time(hour, minute),
        )
        .values(checked_at=NOW)
    )


async def ids(session, course) -> dict[tuple[date, time], object]:
    doses = await list_doses(session, schedule_ids=[course.id])
    return {(d.dose_date, d.scheduled_time): d.id for d in doses}


# ── 1. 코스 칸 ───────────────────────────────────────────────────────────


async def test_내용과_기간을_새_값으로_바꾸고_출처와_상태는_그대로다(session, child):
    course = await seed(session, child, daily(9, -2, 2), symptom_codes=["fever"])

    saved = await revise(
        session,
        child,
        course,
        title="  타이레놀 ",
        dosage="3ml",
        indication_text="열",
        note="식후",
        storage=MedicationStorage.FRIDGE,
        med_form=MedicationForm.DROPS,
        symptom_codes=["cough"],
        ends=3,
    )

    assert saved.id == course.id
    assert saved.title == "  타이레놀 "  # 받은 그대로
    assert (saved.dosage, saved.indication_text, saved.note) == ("3ml", "열", "식후")
    assert (saved.storage, saved.med_form) == (MedicationStorage.FRIDGE, MedicationForm.DROPS)
    assert saved.symptom_codes == ["cough"]
    assert saved.ends_on == day(3)
    assert saved.status == MedicationStatus.ACTIVE
    assert saved.created_by == MedicationCreatedBy.AGENT
    assert saved.source == MedicationSource.PRESCRIPTION_OCR


async def test_빈_문자열_선택_칸은_null_로_바꾼다(session, child):
    course = await seed(session, child, daily(9, -2, 2), dosage="5ml", note="x")

    saved = await revise(session, child, course, dosage="", indication_text="", note="")

    assert (saved.dosage, saved.indication_text, saved.note) == (None, None, None)


# ── 2. 회차: 지키는 것과 다시 만드는 것 ──────────────────────────────────


async def test_체크한_회차는_새_패턴에서_빠져도_그대로_남는다(session, child):
    course = await seed(session, child, daily(9, -2, 0))
    await check(session, course, -1, 9)
    before = await ids(session, course)

    await revise(session, child, course, patterns=[pat(20)], ends=0)

    kept = await ids(session, course)
    assert kept[(day(-1), time(9))] == before[(day(-1), time(9))]  # id 가 그대로다
    assert (day(-1), time(9), True) in await rows(session, course)


async def test_이미_지난_미체크_회차도_남는다(session, child):
    course = await seed(session, child, daily(9, -2, 0))

    await revise(session, child, course, patterns=[pat(20)], ends=0)

    assert {(d, t) for d, t, _ in await rows(session, course)} >= {
        (day(-2), time(9)),
        (day(-1), time(9)),
    }


async def test_미체크이고_예정_시각이_지금_이후인_회차는_새_패턴으로_다시_만든다(session, child):
    course = await seed(session, child, daily(9, 1, 3), starts=1, ends=3)
    before = await ids(session, course)

    await revise(session, child, course, patterns=[pat(20)])

    assert {(d, t) for d, t, _ in await rows(session, course)} == {
        (day(n), time(20)) for n in (1, 2, 3)
    }
    assert not set(before.values()) & set((await ids(session, course)).values())  # id 가 바뀌었다


async def test_다시_만든_회차는_미체크다(session, child):
    course = await seed(session, child, daily(9, 1, 2), starts=1, ends=2)

    await revise(session, child, course, patterns=[pat(20), pat(21)])

    assert all(not checked for _, _, checked in await rows(session, course))


# ── 3. 오늘 ──────────────────────────────────────────────────────────────


async def test_오늘은_지금_이후의_미체크만_다시_만들고_지금과_그_이전은_남긴다(session, child):
    doses = [fx(0, 9, 0), fx(0, 9, 40), fx(0, 9, 41), fx(0, 19, 0)]
    course = await seed(session, child, doses, starts=0, ends=0)

    await revise(session, child, course, patterns=[pat(20)])

    # 09:00 · 09:40(정각) 은 남고, 09:41 · 19:00 은 지워지고, 20:00 이 새로 생긴다
    assert [(t, c) for _, t, c in await rows(session, course)] == [
        (time(9, 0), False),
        (time(9, 40), False),
        (time(20, 0), False),
    ]


async def test_이미_지난_시각은_새_패턴에_있어도_만들지_않는다(session, child):
    course = await seed(session, child, [fx(0, 19, 0)], starts=0, ends=0)

    await revise(session, child, course, patterns=[pat(9, 30), pat(9, 40), pat(9, 41)])

    assert [t for _, t, _ in await rows(session, course)] == [time(9, 41)]  # 09:30 · 09:40 은 없다


async def test_오늘_이미_체크한_미래_시각은_남기고_같은_시각을_또_만들지_않는다(session, child):
    course = await seed(session, child, [fx(0, 19, 0), fx(0, 20, 0)], starts=0, ends=0)
    await check(session, course, 0, 19)
    kept_id = (await ids(session, course))[(day(0), time(19))]

    await revise(session, child, course, patterns=[pat(19), pat(21)])

    assert (await ids(session, course))[(day(0), time(19))] == kept_id
    assert [(t, c) for _, t, c in await rows(session, course)] == [
        (time(19), True),
        (time(21), False),
    ]


async def test_다른_날의_같은_시각은_영향받지_않는다(session, child):
    course = await seed(session, child, [fx(0, 19, 0), fx(1, 19, 0)], starts=0, ends=1)
    await check(session, course, 0, 19)

    await revise(session, child, course, patterns=[pat(19)])

    assert [(d, t, c) for d, t, c in await rows(session, course)] == [
        (day(0), time(19), True),
        (day(1), time(19), False),
    ]


# ── 4. 기간 ──────────────────────────────────────────────────────────────


async def test_끝일을_늘리면_새_날짜의_회차가_생긴다(session, child):
    course = await seed(session, child, daily(9, 1, 2), starts=1, ends=2)

    await revise(session, child, course, patterns=[pat(9)], ends=4)

    assert [d for d, _, _ in await rows(session, course)] == [day(1), day(2), day(3), day(4)]


async def test_끝일을_줄이면_새_끝일_뒤의_미체크_회차가_사라진다(session, child):
    course = await seed(session, child, daily(9, 1, 4), starts=1, ends=4)

    await revise(session, child, course, patterns=[pat(9)], ends=2)

    assert [d for d, _, _ in await rows(session, course)] == [day(1), day(2)]


async def test_시작일을_오늘로_당겨도_이미_지난_미체크_회차는_남는다(session, child):
    course = await seed(session, child, daily(9, -2, 1), starts=-2, ends=1)

    await revise(session, child, course, patterns=[pat(9)], starts=0)

    assert [d for d, _, _ in await rows(session, course)][:2] == [day(-2), day(-1)]


async def test_체크한_회차가_새_기간_밖에_남으면_거절하고_아무것도_바꾸지_않는다(session, child):
    course = await seed(session, child, daily(9, -2, 2))
    await check(session, course, -2, 9)
    before = await rows(session, course)

    with pytest.raises(CourseInvalid) as raised:
        await revise(session, child, course, patterns=[pat(20)], starts=0)  # 체크한 -2 가 밖이다

    assert raised.value.fields == ["schedule.starts_on", "schedule.ends_on"]
    assert await rows(session, course) == before
    assert course.starts_on == day(-2)


async def test_체크한_회차가_새_끝일_뒤에_있어도_거절한다(session, child):
    # 체크는 오늘까지만 되므로 보통 일어나지 않는다 — 데이터가 어긋나 있어도 기록을 잃지 않는 방어다
    course = await seed(session, child, daily(9, 0, 2), starts=0, ends=2)
    await check(session, course, 2, 9)
    before = await rows(session, course)

    with pytest.raises(CourseInvalid) as raised:
        await revise(session, child, course, patterns=[pat(9)], starts=0, ends=1)

    assert raised.value.fields == ["schedule.starts_on", "schedule.ends_on"]
    assert await rows(session, course) == before


async def test_체크한_회차가_새_기간_안이면_허용한다(session, child):
    course = await seed(session, child, daily(9, -2, 2))
    await check(session, course, -1, 9)

    saved = await revise(session, child, course, patterns=[pat(9)], starts=-1, ends=2)

    assert saved.starts_on == day(-1)


# ── 5. 대상 ──────────────────────────────────────────────────────────────


async def test_없는_코스는_찾지_못한다(session, child):
    import uuid

    ghost = await seed(session, child, daily(9, 1, 1), starts=1, ends=1)
    data = CourseFields(title="약", starts_on=day(1), ends_on=day(1), doses=[pat(9)])

    with pytest.raises(CourseNotFound):
        await revise_course(
            session, child_id=child.id, course_id=uuid.uuid4(), data=data, today=TODAY, now=NOW
        )
    assert ghost.title == "약"


async def test_다른_아이의_코스는_찾지_못하고_바꾸지_않는다(session, child):
    other = Child(
        owner_parent_id=child.owner_parent_id, nickname="other", birth_date=date(2022, 1, 1)
    )
    session.add(other)
    await session.flush()
    course = await seed(session, other, daily(9, 1, 2), starts=1, ends=2)
    before = await rows(session, course)

    with pytest.raises(CourseNotFound):
        await revise(session, child, course, patterns=[pat(20)], title="바꿈")

    assert await rows(session, course) == before
    assert course.title == "약"


async def test_멈춘_코스는_찾지_못한다(session, child):
    course = await seed(session, child, daily(9, 1, 2), starts=1, ends=2)
    await stop_schedule(
        session, child_id=child.id, schedule_id=course.id, stopped_at=datetime.now(UTC)
    )

    with pytest.raises(CourseNotFound):
        await revise(session, child, course, patterns=[pat(20)])


# ── 6. 검증 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("over", "fields"),
    [
        ({"title": "  "}, ["schedule.title"]),
        ({"title": "가" * 201}, ["schedule.title"]),
        ({"dosage": "가" * 201}, ["schedule.dosage"]),
        ({"patterns": []}, ["doses"]),
        ({"patterns": [pat(9), pat(9)]}, ["doses"]),
        ({"patterns": [DosePattern(FIXED, 0, None)]}, ["doses"]),
        ({"patterns": [DosePattern(TimingAnchor.LUNCH, 181, None)]}, ["doses"]),
        ({"ends": 100}, ["schedule.starts_on", "schedule.ends_on"]),
    ],
)
async def test_틀린_칸이_있으면_거절하고_아무것도_바꾸지_않는다(session, child, over, fields):
    course = await seed(session, child, daily(9, -2, 2))
    before = await rows(session, course)

    with pytest.raises(CourseInvalid) as raised:
        await revise(session, child, course, **over)

    assert raised.value.fields == fields
    assert await rows(session, course) == before
    assert course.title == "약"


async def test_새_기간_전체가_과거면_거절한다_provisional(session, child):
    course = await seed(session, child, daily(9, -1, 1), starts=-1, ends=1)

    with pytest.raises(CourseInvalid) as raised:
        await revise(session, child, course, patterns=[pat(9)], starts=-5, ends=-3)

    assert raised.value.fields == ["schedule.ends_on"]


async def test_이미_끝난_코스는_찾지_못한다(session, child):
    # 조회는 끝난 코스를 싣지 않는다 — 수정도 진행 중인 코스만 다룬다. 400 으로 내면 고치지 않은
    # `ends_on` 이 틀렸다고 나온다
    course = await seed(session, child, daily(9, -5, -1), starts=-5, ends=-1)
    before = await rows(session, course)

    with pytest.raises(CourseNotFound):
        await revise(session, child, course, patterns=[pat(9)], title="바꿈")

    assert await rows(session, course) == before
    assert course.title == "약"


async def test_끝일이_오늘인_코스는_아직_고칠_수_있다(session, child):
    course = await seed(session, child, daily(9, -1, 0), starts=-1, ends=0)

    saved = await revise(session, child, course, patterns=[pat(20)], title="바꿈")

    assert saved.title == "바꿈"


async def test_시작_전인_코스도_고칠_수_있다(session, child):
    course = await seed(session, child, daily(9, 1, 2), starts=1, ends=2)

    saved = await revise(session, child, course, patterns=[pat(20)], title="바꿈")

    assert saved.title == "바꿈"


# ── 7. 중복 ──────────────────────────────────────────────────────────────


async def test_다른_진행_코스와_이름과_기간이_같아지면_거절한다_provisional(session, child):
    await seed(session, child, daily(9, 1, 2), starts=1, ends=2, title="아목시실린")
    other = await seed(session, child, daily(9, 1, 2), starts=1, ends=2, title="타이레놀")

    with pytest.raises(CourseAlreadyExists):
        await revise(session, child, other, patterns=[pat(9)], title="아목시실린")

    assert other.title == "타이레놀"


async def test_자기_자신과는_같아도_된다(session, child):
    course = await seed(session, child, daily(9, 1, 2), starts=1, ends=2)

    saved = await revise(session, child, course, patterns=[pat(20)])  # 이름 · 기간 그대로

    assert saved.id == course.id


async def test_멈춘_코스와는_같아도_된다(session, child):
    stopped = await seed(session, child, daily(9, 1, 2), starts=1, ends=2, title="아목시실린")
    await stop_schedule(
        session, child_id=child.id, schedule_id=stopped.id, stopped_at=datetime.now(UTC)
    )
    other = await seed(session, child, daily(9, 1, 2), starts=1, ends=2, title="타이레놀")

    saved = await revise(session, child, other, patterns=[pat(9)], title="아목시실린")

    assert saved.title == "아목시실린"


# ── 8. 순서 · 멱등 · 트랜잭션 ───────────────────────────────────────────


async def test_찾지_못함이_칸_오류보다_먼저다(session, child):
    import uuid

    data = CourseFields(title="  ", starts_on=day(1), ends_on=day(1), doses=[])

    with pytest.raises(CourseNotFound):
        await revise_course(
            session, child_id=child.id, course_id=uuid.uuid4(), data=data, today=TODAY, now=NOW
        )


async def test_칸_오류가_중복보다_먼저다(session, child):
    await seed(session, child, daily(9, 1, 2), starts=1, ends=2, title="아목시실린")
    other = await seed(session, child, daily(9, 1, 2), starts=1, ends=2, title="타이레놀")

    with pytest.raises(CourseInvalid):
        await revise(session, child, other, patterns=[], title="아목시실린")


async def test_같은_수정을_두_번_해도_결과가_같다(session, child):
    course = await seed(session, child, daily(9, -1, 3))
    await check(session, course, -1, 9)

    await revise(session, child, course, patterns=[pat(20)], ends=3)
    first = await rows(session, course)
    await revise(session, child, course, patterns=[pat(20)], ends=3)

    assert await rows(session, course) == first


async def test_다른_코스의_회차는_건드리지_않는다(session, child):
    mine = await seed(session, child, daily(9, 1, 2), starts=1, ends=2, title="수정할 약")
    other = await seed(session, child, daily(9, 1, 2), starts=1, ends=2, title="다른 약")
    before = await ids(session, other)

    await revise(session, child, mine, patterns=[pat(20)])

    assert await ids(session, other) == before


async def test_서비스는_commit_하지_않아_롤백하면_원래대로_돌아간다(session, child):
    course = await seed(session, child, daily(9, 1, 2), starts=1, ends=2)
    course_id = course.id
    before = await rows(session, course)
    await session.commit()

    await revise(session, child, course, patterns=[pat(20)], title="바꿈")
    await session.rollback()

    reloaded = await list_doses(session, schedule_ids=[course_id])
    assert [(d.dose_date, d.scheduled_time, d.checked_at is not None) for d in reloaded] == before


# ── 9. 동시 요청 — 아이별 잠금 ───────────────────────────────────────────


async def held_locks(session, child_id) -> int:
    return await session.scalar(
        text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid() "
            "AND classid = :ns AND objid::bigint = :key AND objsubid = 2"
        ),
        {"ns": COURSE_LOCK_NS, "key": course_lock_key(child_id)},
    )


async def test_수정은_그_아이의_잠금을_잡는다(session, child):
    course = await seed(session, child, daily(9, 1, 2), starts=1, ends=2)

    await revise(session, child, course, patterns=[pat(20)])

    assert await held_locks(session, child.id) == 1


async def test_칸이_틀리거나_코스를_못_찾는_수정은_잠금을_잡지_않는다(session, child):
    import uuid

    course = await seed(session, child, daily(9, 1, 2), starts=1, ends=2)
    with pytest.raises(CourseInvalid):
        await revise(session, child, course, patterns=[], title="바꿈")
    data = CourseFields(title="약", starts_on=day(1), ends_on=day(2), doses=[pat(9)])
    with pytest.raises(CourseNotFound):
        await revise_course(
            session, child_id=child.id, course_id=uuid.uuid4(), data=data, today=TODAY, now=NOW
        )

    assert await held_locks(session, child.id) == 0
