"""복약 포트와 in-memory 저장소 — 회차 1행 · 체크는 읽기만 · Agent 가 쓰는 것은 중단 하나뿐.

저장소 동작(기간 · 진행 중 · 중단)은 medication_store_contract.py 의 시나리오로 DB 어댑터와
같이 본다. 여기는 in-memory 만의 장치(쓰기 카운터)와 헬퍼를 본다.
포트 칸 이름은 `medication_schedule` · `medication_dose` 스키마와 같다.

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
        (date(2026, 9, day), time(hour, 30)) for day in (26, 27, 28) for hour in (8, 12, 19)
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
        time(19, 30): None,
    }


async def test_중단은_쓰기_카운터에_한_번만_남는다():
    course, doses = antibiotic(TODAY, TODAY + timedelta(days=1))
    stopped_before, stopped_rows = antibiotic(TODAY, TODAY, status="stopped")
    store = InMemoryMedicationStore((course, stopped_before), (*doses, *stopped_rows))

    assert (
        await store.stop_course(child_id=CHILD, schedule_id=course.id, stopped_at=NOW) == "stopped"
    )
    again = NOW + timedelta(minutes=2)
    assert (
        await store.stop_course(child_id=CHILD, schedule_id=course.id, stopped_at=again)
        == "already"
    )
    assert (
        await store.stop_course(child_id=CHILD, schedule_id=stopped_before.id, stopped_at=NOW)
        == "already"
    )
    other = course_with_doses("시럽", (BREAKFAST,), TODAY, TODAY)[0]
    assert (
        await store.stop_course(child_id=CHILD, schedule_id=other.id, stopped_at=NOW) == "missing"
    )

    assert store.writes == [(course.id, NOW)]
    assert store.stopped == {course.id: NOW}


async def test_in_memory_ports_에_복약_포트가_붙는다():
    store = InMemoryMedicationStore()

    ports = in_memory_ports(CHILD, BIRTH_48, consent=True, medication=store, nickname="별이")

    assert ports.medication is store and ports.medication_writer is store
    assert await ports.child.basic(child_id=CHILD) == ChildBasic("별이", BIRTH_48)


async def test_복약_포트를_안_주면_빈_저장소다():
    ports = in_memory_ports(CHILD, BIRTH_48, consent=True)

    assert await ports.medication.active_courses(child_id=CHILD, today=TODAY) == []
    assert isinstance(ports.child, InMemoryChildBasic)
