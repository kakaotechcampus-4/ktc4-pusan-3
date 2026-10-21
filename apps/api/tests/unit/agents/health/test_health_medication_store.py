"""복약 포트와 in-memory 저장소 — 회차 1행 · 체크는 읽기만 · Agent 가 쓰는 것은 중단 하나뿐.

DB 어댑터(연결 이슈)가 같은 테스트를 통과해야 한다. 포트 칸 이름은 `medication_schedule` ·
`medication_dose` 스키마와 같다.

    - 회차 행은 기간 × 하루 시각 패턴만큼 있다 (3일 × 3 = 9행)
    - `doses` 는 날짜 구간으로 자른다
    - `ends_on` 이 지난 코스는 status 가 `active` 여도 진행 중이 아니다 — 끝남은 날짜로 본다
    - 중단하면 진행 중에서 빠지고 중단 시각이 남는다. 이미 멈춘 코스는 시각이 안 바뀐다
"""

from datetime import date, datetime, time, timedelta

from app.agents.health.store.inmemory import (
    InMemoryChildBasic,
    InMemoryMedicationStore,
    course_with_doses,
    in_memory_ports,
)
from app.agents.health.store.ports import ChildBasic, DoseSlot
from tests.unit.agents.health.support import BIRTH_48, CHILD, KST, NOW, TODAY

BREAKFAST = DoseSlot.of("breakfast", 30)
LUNCH = DoseSlot.of("lunch", 30)
DINNER = DoseSlot.of("dinner", 30)
PATTERN = (BREAKFAST, LUNCH, DINNER)


def antibiotic(starts_on: date, ends_on: date, **kwargs):
    return course_with_doses("항생제", PATTERN, starts_on, ends_on, **kwargs)


def test_회차_행은_기간_곱하기_패턴():
    course, doses = antibiotic(date(2026, 9, 26), date(2026, 9, 28))

    assert len(doses) == 9
    assert {(dose.dose_date, dose.scheduled_time) for dose in doses} == {
        (date(2026, 9, day), time(hour, 30)) for day in (26, 27, 28) for hour in (8, 12, 17)
    }
    assert len({dose.id for dose in doses}) == 9
    assert all(dose.schedule_id == course.id and dose.checked_at is None for dose in doses)
    assert (course.pattern, course.status) == (PATTERN, "active")


def test_체크한_회차만_checked_at_이_있다():
    checked_at = datetime(2026, 9, 28, 8, 40, tzinfo=KST)

    _, doses = antibiotic(TODAY, TODAY, checked={(TODAY, time(8, 30)): checked_at})

    assert {dose.scheduled_time: dose.checked_at for dose in doses} == {
        time(8, 30): checked_at,
        time(12, 30): None,
        time(17, 30): None,
    }


async def test_doses_는_기간으로_자른다():
    course, doses = antibiotic(TODAY - timedelta(days=2), TODAY + timedelta(days=2))
    other, other_doses = course_with_doses("시럽", (BREAKFAST,), TODAY, TODAY)
    store = InMemoryMedicationStore((course, other), (*doses, *other_doses))

    today_rows = await store.doses(schedule_ids=[course.id], date_from=TODAY, date_to=TODAY)
    span_rows = await store.doses(
        schedule_ids=[course.id, other.id], date_from=TODAY - timedelta(days=1), date_to=TODAY
    )

    assert [(row.dose_date, row.scheduled_time) for row in today_rows] == [
        (TODAY, time(8, 30)),
        (TODAY, time(12, 30)),
        (TODAY, time(17, 30)),
    ]
    assert len(span_rows) == 3 + 3 + 1  # 어제 3 · 오늘 3 · 다른 코스 오늘 1
    assert [row.dose_date for row in span_rows] == sorted(row.dose_date for row in span_rows)


async def test_ends_on_이_지난_코스는_active_에_없고_overlapping_에는_있다():
    course, doses = antibiotic(TODAY - timedelta(days=5), TODAY - timedelta(days=1))
    store = InMemoryMedicationStore((course,), doses)

    active = await store.active_courses(child_id=CHILD, today=TODAY)
    overlapping = await store.courses_overlapping(
        child_id=CHILD, date_from=TODAY - timedelta(days=3), date_to=TODAY
    )
    later = await store.courses_overlapping(
        child_id=CHILD, date_from=TODAY, date_to=TODAY + timedelta(days=3)
    )

    assert active == []
    assert [c.id for c in overlapping] == [course.id]
    assert overlapping[0].status == "active"  # 끝났다고 status 를 바꾸지 않는다
    assert later == []


async def test_시작_전_코스는_active_에_없다():
    course, doses = antibiotic(TODAY + timedelta(days=1), TODAY + timedelta(days=3))
    store = InMemoryMedicationStore((course,), doses)

    assert await store.active_courses(child_id=CHILD, today=TODAY) == []


async def test_오늘이_ends_on_이면_아직_진행_중():
    course, doses = antibiotic(TODAY - timedelta(days=2), TODAY)
    store = InMemoryMedicationStore((course,), doses)

    assert [c.id for c in await store.active_courses(child_id=CHILD, today=TODAY)] == [course.id]


async def test_중단하면_active_에서_빠지고_시각이_남는다():
    checked_at = datetime(2026, 9, 28, 8, 40, tzinfo=KST)
    course, doses = antibiotic(
        TODAY - timedelta(days=1),
        TODAY + timedelta(days=1),
        checked={(TODAY, time(8, 30)): checked_at},
    )
    store = InMemoryMedicationStore((course,), doses)

    result = await store.stop_course(schedule_id=course.id, stopped_at=NOW)

    assert result == "stopped"
    assert await store.active_courses(child_id=CHILD, today=TODAY) == []
    assert store.stopped == {course.id: NOW}
    assert store.writes == [(course.id, NOW)]
    # 행이 남는다 — 회차와 체크는 그대로, 끝난 뒤에도 겹치는 구간에서 읽힌다
    kept = await store.courses_overlapping(child_id=CHILD, date_from=TODAY, date_to=TODAY)
    assert [(c.id, c.status) for c in kept] == [(course.id, "stopped")]
    rows = await store.doses(
        schedule_ids=[course.id],
        date_from=TODAY - timedelta(days=1),
        date_to=TODAY + timedelta(days=1),
    )
    assert len(rows) == 9
    assert [row.checked_at for row in rows if row.checked_at] == [checked_at]


async def test_이미_멈춘_코스는_시각이_안_바뀐다():
    course, doses = antibiotic(TODAY, TODAY + timedelta(days=1))
    store = InMemoryMedicationStore((course,), doses)
    await store.stop_course(schedule_id=course.id, stopped_at=NOW)

    again = await store.stop_course(schedule_id=course.id, stopped_at=NOW + timedelta(minutes=2))

    assert again == "already"
    assert store.stopped == {course.id: NOW}
    assert len(store.writes) == 1


async def test_처음부터_멈춰_있던_코스도_already():
    course, doses = antibiotic(TODAY, TODAY + timedelta(days=1), status="stopped")
    store = InMemoryMedicationStore((course,), doses)

    assert await store.stop_course(schedule_id=course.id, stopped_at=NOW) == "already"
    assert store.stopped == {} and store.writes == []


async def test_없는_코스는_missing():
    course, doses = antibiotic(TODAY, TODAY)
    store = InMemoryMedicationStore((course,), doses)

    other = course_with_doses("시럽", (BREAKFAST,), TODAY, TODAY)[0]

    assert await store.stop_course(schedule_id=other.id, stopped_at=NOW) == "missing"
    assert store.writes == []


async def test_pattern_은_남은_회차의_시각():
    # 어제까지 지난 회차가 있어도 pattern 은 오늘 이후 회차 행의 시각이다
    course, doses = antibiotic(TODAY - timedelta(days=2), TODAY + timedelta(days=2))
    store = InMemoryMedicationStore((course,), doses)

    [read] = await store.active_courses(child_id=CHILD, today=TODAY)

    remaining = {dose.scheduled_time for dose in doses if dose.dose_date >= TODAY}
    assert {slot.scheduled_time for slot in read.pattern} == remaining
    assert [slot.timing_anchor for slot in read.pattern] == ["breakfast", "lunch", "dinner"]


async def test_in_memory_ports_에_복약_포트가_붙는다():
    store = InMemoryMedicationStore()

    ports = in_memory_ports(CHILD, BIRTH_48, consent=True, medication=store, nickname="별이")

    assert ports.medication is store and ports.medication_writer is store
    assert await ports.child.basic(child_id=CHILD) == ChildBasic("별이", BIRTH_48)


async def test_복약_포트를_안_주면_빈_저장소다():
    ports = in_memory_ports(CHILD, BIRTH_48, consent=True)

    assert await ports.medication.active_courses(child_id=CHILD, today=TODAY) == []
    assert isinstance(ports.child, InMemoryChildBasic)
