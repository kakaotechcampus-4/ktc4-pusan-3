"""복약 코스 등록 서비스 — `service.create_course` (#577).

검증 → (기간 전체가 과거인지) → 중복 → 회차 펼치기 → 저장 순서로 한 번에 처리한다.
HTTP 를 모른다. 틀린 입력은 `CourseInvalid(fields)`, 이미 있는 코스는 `CourseAlreadyExists` 이고
라우터가 400 · 409 로 바꾼다. 어느 오류든 저장은 0건이고 commit 은 부르는 쪽이 한다.

provisional — #563 에서 기준을 정하지 못한 것: 오늘 이미 지난 시각의 회차를 만드는지, 기간 전체가
과거인 코스를 거절하는지, 중복의 기준(약 이름 · 기간 일치).
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select, text

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.medication.models import (
    MedicationCreatedBy,
    MedicationDose,
    MedicationForm,
    MedicationSchedule,
    MedicationSource,
    MedicationStorage,
    TimingAnchor,
)
from app.domains.medication.repository import list_doses, stop_schedule
from app.domains.medication.service import (
    COURSE_LOCK_NS,
    CourseAlreadyExists,
    CourseInput,
    CourseInvalid,
    DosePattern,
    course_lock_key,
    create_course,
)

TODAY = date(2026, 10, 27)
STARTS = date(2026, 10, 28)  # 내일부터
ENDS = date(2026, 10, 30)


@pytest.fixture
async def child(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return child


def pattern(
    anchor: TimingAnchor = TimingAnchor.BREAKFAST, offset: int = 30, fixed: time | None = None
) -> DosePattern:
    return DosePattern(timing_anchor=anchor, offset_minutes=offset, fixed_time=fixed)


MEALS_AFTER_30 = [
    pattern(TimingAnchor.BREAKFAST),
    pattern(TimingAnchor.LUNCH),
    pattern(TimingAnchor.DINNER),
]


def course(**over) -> CourseInput:
    values = {
        "title": "아목시실린",
        "starts_on": STARTS,
        "ends_on": ENDS,
        "source": MedicationSource.UTTERANCE,
        "doses": MEALS_AFTER_30,
    }
    return CourseInput(**values | over)


async def create(session, child, *, today: date = TODAY, **over) -> MedicationSchedule:
    return await create_course(session, child_id=child.id, data=course(**over), today=today)


async def count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


async def invalid_fields(session, child, **over) -> list[str]:
    with pytest.raises(CourseInvalid) as raised:
        await create(session, child, **over)
    return raised.value.fields


async def nothing_saved(session) -> bool:
    return (
        await count(session, MedicationSchedule) == 0 and await count(session, MedicationDose) == 0
    )


# ── 1. 정상 ──────────────────────────────────────────────────────────────


async def test_하루_세_번_3일이면_코스_1행과_회차_9행(session, child):
    saved = await create(session, child)

    doses = await list_doses(session, schedule_ids=[saved.id])
    assert len(doses) == 9
    assert [d.scheduled_time for d in doses] == [time(8, 30), time(12, 30), time(19, 30)] * 3
    assert [d.dose_date for d in doses[::3]] == [STARTS, STARTS + timedelta(days=1), ENDS]
    assert await count(session, MedicationSchedule) == 1


@pytest.mark.parametrize("source", list(MedicationSource))
async def test_코스는_보호자가_만든_것으로_저장되고_출처가_남는다(session, child, source):
    saved = await create(session, child, source=source)

    assert saved.child_id == child.id
    assert saved.created_by == MedicationCreatedBy.CAREGIVER
    assert saved.source == source


async def test_받은_값은_그대로_저장되고_증상_코드도_남는다(session, child):
    saved = await create(
        session,
        child,
        title="  아목시실린 ",
        dosage="5ml",
        indication_text="중이염",
        note="식후 30분",
        symptom_codes=["fever"],
        storage=MedicationStorage.FRIDGE,
        med_form=MedicationForm.LIQUID,
    )

    assert saved.title == "  아목시실린 "
    assert (saved.dosage, saved.indication_text, saved.note) == ("5ml", "중이염", "식후 30분")
    assert saved.symptom_codes == ["fever"]
    assert (saved.storage, saved.med_form) == (MedicationStorage.FRIDGE, MedicationForm.LIQUID)
    assert (saved.starts_on, saved.ends_on) == (STARTS, ENDS)


async def test_빈_문자열_선택_칸은_null_로_저장한다(session, child):
    saved = await create(session, child, dosage="", indication_text="", note="")

    assert (saved.dosage, saved.indication_text, saved.note) == (None, None, None)


# ── 2. 오늘 · 과거 ───────────────────────────────────────────────────────


async def test_오늘_시작하면_오늘_회차까지_만든다_provisional(session, child):
    # 오늘 이미 지난 시각의 회차도 만든다 — 방금 먹인 약을 등록하는 경우가 흔해서
    saved = await create(session, child, starts_on=TODAY, ends_on=TODAY)

    doses = await list_doses(session, schedule_ids=[saved.id])
    assert [d.dose_date for d in doses] == [TODAY] * 3


async def test_시작일이_과거여도_코스는_그대로_저장하고_지난_날짜_회차는_만들지_않는다(
    session, child
):
    saved = await create(
        session, child, starts_on=TODAY - timedelta(days=2), ends_on=TODAY + timedelta(days=1)
    )

    assert saved.starts_on == TODAY - timedelta(days=2)
    doses = await list_doses(session, schedule_ids=[saved.id])
    assert sorted({d.dose_date for d in doses}) == [TODAY, TODAY + timedelta(days=1)]
    assert len(doses) == 6


async def test_기간_전체가_과거면_거절하고_저장하지_않는다_provisional(session, child):
    fields = await invalid_fields(
        session, child, starts_on=TODAY - timedelta(days=3), ends_on=TODAY - timedelta(days=1)
    )

    assert fields == ["schedule.ends_on"]
    assert await nothing_saved(session)


# ── 3. 칸 검증 ───────────────────────────────────────────────────────────


async def test_틀린_칸의_이름을_모아_거절하고_저장하지_않는다(session, child):
    fields = await invalid_fields(
        session, child, title="  ", ends_on=STARTS + timedelta(days=90), doses=[]
    )

    assert fields == ["schedule.title", "schedule.starts_on", "schedule.ends_on", "doses"]
    assert await nothing_saved(session)


async def test_기간이_90일이면_저장되고_91일이면_거절한다(session, child):
    await create(session, child, ends_on=STARTS + timedelta(days=89))
    other = {"title": "타이레놀", "ends_on": STARTS + timedelta(days=90)}

    assert await invalid_fields(session, child, **other) == [
        "schedule.starts_on",
        "schedule.ends_on",
    ]


# ── 4. 시각 패턴 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        pattern(TimingAnchor.FIXED, offset=0, fixed=None),  # fixed 인데 시각이 없다
        pattern(TimingAnchor.BREAKFAST, offset=30, fixed=time(8, 0)),  # fixed 가 아닌데 시각이 있다
        pattern(TimingAnchor.FIXED, offset=30, fixed=time(21, 0)),  # fixed 는 offset 이 0
        pattern(TimingAnchor.LUNCH, offset=-121),
        pattern(TimingAnchor.LUNCH, offset=181),
    ],
)
async def test_시각_패턴이_틀리면_doses_로_거절한다(session, child, bad):
    assert await invalid_fields(session, child, doses=[bad]) == ["doses"]
    assert await nothing_saved(session)


async def test_anchor_가_달라도_알림_시각이_같으면_거절한다(session, child):
    same_time = [
        pattern(TimingAnchor.BREAKFAST, offset=30),
        pattern(TimingAnchor.FIXED, offset=0, fixed=time(8, 30)),
    ]

    assert await invalid_fields(session, child, doses=same_time) == ["doses"]


async def test_같은_패턴을_두_번_보내면_거절한다(session, child):
    twice = [pattern(TimingAnchor.LUNCH), pattern(TimingAnchor.LUNCH)]

    assert await invalid_fields(session, child, doses=twice) == ["doses"]


@pytest.mark.parametrize("offset", [-120, 180])
async def test_offset_경계_안은_저장된다(session, child, offset):
    saved = await create(session, child, doses=[pattern(TimingAnchor.LUNCH, offset=offset)])

    (first, *_) = await list_doses(session, schedule_ids=[saved.id])
    assert first.offset_minutes == offset


async def test_하루_24개_패턴까지_저장되고_25개는_거절한다(session, child):
    day = [pattern(TimingAnchor.FIXED, offset=0, fixed=time(hour, 0)) for hour in range(24)]
    saved = await create(session, child, ends_on=STARTS, doses=day)

    assert len(await list_doses(session, schedule_ids=[saved.id])) == 24
    extra = [*day, pattern(TimingAnchor.FIXED, offset=0, fixed=time(0, 30))]
    assert await invalid_fields(session, child, title="타이레놀", doses=extra) == ["doses"]


# ── 5. 중복 ──────────────────────────────────────────────────────────────


async def test_같은_코스를_다시_등록하면_거절하고_늘리지_않는다(session, child):
    await create(session, child)

    with pytest.raises(CourseAlreadyExists):
        await create(session, child)

    assert await count(session, MedicationSchedule) == 1
    assert await count(session, MedicationDose) == 9


async def test_멈춘_뒤에는_같은_코스를_다시_등록할_수_있다(session, child):
    first = await create(session, child)
    await stop_schedule(
        session, child_id=child.id, schedule_id=first.id, stopped_at=datetime.now(UTC)
    )

    again = await create(session, child)

    assert again.id != first.id


async def test_다른_아이는_같은_코스를_등록할_수_있다(session, child):
    other = Child(
        owner_parent_id=child.owner_parent_id, nickname="other", birth_date=date(2022, 1, 1)
    )
    session.add(other)
    await session.flush()
    await create(session, child)

    again = await create(session, other)

    assert again.child_id == other.id


async def test_칸_오류가_중복보다_먼저_거절된다(session, child):
    await create(session, child)

    # 같은 코스지만 회차 패턴이 비어 있다 — 409 가 아니라 400 이다
    assert await invalid_fields(session, child, doses=[]) == ["doses"]


# ── 6. 트랜잭션 ──────────────────────────────────────────────────────────


async def test_저장이_성공해도_commit_은_부른_쪽이_해서_롤백하면_사라진다(session, child):
    await session.commit()

    await create(session, child)
    await session.rollback()

    assert await nothing_saved(session)


# ── 7. 동시 요청 — 아이별 잠금 ───────────────────────────────────────────


async def held_locks(session, child_id) -> int:
    """이 트랜잭션이 그 아이의 복약 잠금을 쥐고 있는 개수."""
    return await session.scalar(
        text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid() "
            "AND classid = :ns AND objid::bigint = :key AND objsubid = 2"
        ),
        {"ns": COURSE_LOCK_NS, "key": course_lock_key(child_id)},
    )


async def test_등록은_그_아이의_잠금을_잡아_동시에_온_같은_코스를_직렬화한다(session, child):
    assert await held_locks(session, child.id) == 0

    await create(session, child)

    assert await held_locks(session, child.id) == 1  # commit 이나 롤백 때 풀린다


async def test_중복으로_거절된_요청도_검사하는_동안_잠금을_쥔다(session, child):
    await create(session, child)

    with pytest.raises(CourseAlreadyExists):
        await create(session, child)

    assert await held_locks(session, child.id) == 1


async def test_칸이_틀린_요청은_잠금을_잡지_않는다(session, child):
    with pytest.raises(CourseInvalid):
        await create(session, child, title=" ")

    assert await held_locks(session, child.id) == 0


def test_잠금_키는_int4_범위이고_다른_기능의_잠금과_이름공간이_다르다():
    import uuid

    assert 0 <= course_lock_key(uuid.uuid4()) < 2**31
    assert COURSE_LOCK_NS == 2  # 1 은 Curator 가 쓴다 (domains/memory/curator/trigger.py)
