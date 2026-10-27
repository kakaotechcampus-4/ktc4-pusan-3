"""복약 저장소 계약 시나리오. in-memory 저장소와 DB 어댑터가 같은 결과를 내는지 본다.

시나리오는 어느 저장소인지 모르고 Harness 만 받는다.

    단위  tests/unit/agents/health/test_medication_store_contract.py     in-memory · DB 없음
    통합  tests/integration/agents/health/test_medication_store_contract_db.py
          DB 어댑터 · session 픽스처

in-memory 가 DB 와 다르게 나오면 in-memory 를 DB 에 맞춘다 — 단위 테스트가 실제 저장소와 같은 것을
보게 하려는 것이다. DB 쪽이 틀리면 통합 쪽에서 그 케이스만 xfail(strict) 로 두고 이유를 적는다.

in-memory 는 아이 하나 몫이라 다른 아이를 가르는 케이스는 여기 없다 — DB 쪽에서만 본다.
쓰기 횟수(`writes` · `stopped`)도 in-memory 만의 장치라 여기서 보지 않는다.
"""

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from uuid import UUID

from app.agents.common.datetime_rules import today_of
from app.agents.health.store.inmemory import course_with_doses
from app.agents.health.store.ports import (
    DoseSlot,
    MedicationCourse,
    MedicationDose,
    MedicationReader,
    MedicationWriter,
)
from tests.unit.agents.health.support import KST, NOW, TODAY

BREAKFAST = DoseSlot.of("breakfast", 30)
LUNCH = DoseSlot.of("lunch", 30)
DINNER = DoseSlot.of("dinner", 30)
PATTERN = (BREAKFAST, LUNCH, DINNER)


class Store(MedicationReader, MedicationWriter, Protocol):
    """한 객체가 읽기와 중단 쓰기를 같이 한다."""


class Harness(Protocol):
    """시나리오가 저장소 종류를 모르고 쓰는 것."""

    child_id: UUID
    store: Store

    async def seed(self, course: MedicationCourse, doses: Sequence[MedicationDose]) -> None:
        """`course_with_doses` 가 만든 코스와 회차를 넣는다.

        회차의 기준 시점은 `course.pattern` 에서 같은 `scheduled_time` 의 칸을 찾는다.
        `course.status == "stopped"` 면 `course.stopped_at` 이 중단 시각이다.
        """
        ...


Scenario = Callable[[Harness], Awaitable[None]]


def antibiotic(starts_on: date, ends_on: date, **kwargs: Any):
    return course_with_doses("항생제", PATTERN, starts_on, ends_on, **kwargs)


async def 코스_칸을_그대로_읽는다(h: Harness) -> None:
    course, doses = course_with_doses(
        "항생제",
        PATTERN,
        TODAY - timedelta(days=1),
        TODAY + timedelta(days=1),
        dosage="5ml",
        indication_text="중이염",
        storage="fridge",
        med_form="liquid",
        note="흔들어서",
        symptom_codes=("fever", "cough"),
    )
    await h.seed(course, doses)

    assert await h.store.active_courses(child_id=h.child_id, today=TODAY) == [course]


async def 회차를_체크_칸까지_그대로_읽는다(h: Harness) -> None:
    # KST 08:00 은 UTC 로 전날 23:00 이다. UTC 날짜로 읽으면 하루 어긋난다
    checked_at = datetime(2026, 9, 28, 8, 0, tzinfo=KST)
    course, doses = antibiotic(TODAY, TODAY, checked={(TODAY, time(8, 30)): checked_at})
    await h.seed(course, doses)

    rows = await h.store.doses(schedule_ids=[course.id], date_from=TODAY, date_to=TODAY)

    assert [(row.scheduled_time, row.checked_at) for row in rows] == [
        (time(8, 30), checked_at),
        (time(12, 30), None),
        (time(19, 30), None),
    ]
    assert rows[0].checked_at.tzinfo is not None
    assert rows[0].checked_at.astimezone(KST).date() == TODAY
    assert all(row.schedule_id == course.id and row.dose_date == TODAY for row in rows)


async def doses_는_기간으로_자르고_날짜_시각_순이다(h: Harness) -> None:
    course, doses = antibiotic(TODAY - timedelta(days=2), TODAY + timedelta(days=2))
    other, other_doses = course_with_doses("시럽", (BREAKFAST,), TODAY, TODAY)
    await h.seed(course, doses)
    await h.seed(other, other_doses)

    today_rows = await h.store.doses(schedule_ids=[course.id], date_from=TODAY, date_to=TODAY)
    span_rows = await h.store.doses(
        schedule_ids=[course.id, other.id], date_from=TODAY - timedelta(days=1), date_to=TODAY
    )

    assert [(row.dose_date, row.scheduled_time) for row in today_rows] == [
        (TODAY, time(8, 30)),
        (TODAY, time(12, 30)),
        (TODAY, time(19, 30)),
    ]
    assert len(span_rows) == 3 + 3 + 1  # 어제 3 · 오늘 3 · 다른 코스 오늘 1
    assert [(row.dose_date, row.scheduled_time) for row in span_rows] == sorted(
        (row.dose_date, row.scheduled_time) for row in span_rows
    )


async def ends_on_이_지난_코스는_active_에_없고_overlapping_에는_있다(h: Harness) -> None:
    course, doses = antibiotic(TODAY - timedelta(days=5), TODAY - timedelta(days=1))
    await h.seed(course, doses)

    active = await h.store.active_courses(child_id=h.child_id, today=TODAY)
    overlapping = await h.store.courses_overlapping(
        child_id=h.child_id, date_from=TODAY - timedelta(days=3), date_to=TODAY
    )
    later = await h.store.courses_overlapping(
        child_id=h.child_id, date_from=TODAY, date_to=TODAY + timedelta(days=3)
    )

    assert active == []
    assert [c.id for c in overlapping] == [course.id]
    assert overlapping[0].status == "active"  # 끝났다고 status 를 바꾸지 않는다
    assert later == []


async def 시작_전_코스는_active_에_없다(h: Harness) -> None:
    course, doses = antibiotic(TODAY + timedelta(days=1), TODAY + timedelta(days=3))
    await h.seed(course, doses)

    assert await h.store.active_courses(child_id=h.child_id, today=TODAY) == []


async def 오늘이_ends_on_이면_아직_진행_중(h: Harness) -> None:
    course, doses = antibiotic(TODAY - timedelta(days=2), TODAY)
    await h.seed(course, doses)

    active = await h.store.active_courses(child_id=h.child_id, today=TODAY)

    assert [c.id for c in active] == [course.id]


async def KST_00시30분에_어제_끝난_코스는_진행_중이_아니다(h: Harness) -> None:
    # UTC 9/27 15:30 = KST 9/28 00:30. DB 의 CURRENT_DATE 는 UTC 라 여기서 하루 어긋난다
    now = datetime(2026, 9, 27, 15, 30, tzinfo=UTC)
    today = today_of(now, KST)
    ended, ended_doses = antibiotic(date(2026, 9, 25), date(2026, 9, 27))
    last_day, last_doses = course_with_doses("시럽", (BREAKFAST,), date(2026, 9, 25), today)
    await h.seed(ended, ended_doses)
    await h.seed(last_day, last_doses)

    active = await h.store.active_courses(child_id=h.child_id, today=today)

    assert today == date(2026, 9, 28)
    assert [c.id for c in active] == [last_day.id]


async def 중단하면_active_에서_빠지고_시각이_남는다(h: Harness) -> None:
    checked_at = datetime(2026, 9, 28, 8, 40, tzinfo=KST)
    course, doses = antibiotic(
        TODAY - timedelta(days=1),
        TODAY + timedelta(days=1),
        checked={(TODAY, time(8, 30)): checked_at},
    )
    await h.seed(course, doses)

    result = await h.store.stop_course(child_id=h.child_id, schedule_id=course.id, stopped_at=NOW)

    assert result == "stopped"
    assert await h.store.active_courses(child_id=h.child_id, today=TODAY) == []
    # 행이 남는다 — 회차와 체크는 그대로, 끝난 뒤에도 겹치는 구간에서 읽힌다
    [kept] = await h.store.courses_overlapping(child_id=h.child_id, date_from=TODAY, date_to=TODAY)
    assert (kept.id, kept.status, kept.stopped_at) == (course.id, "stopped", NOW)
    rows = await h.store.doses(
        schedule_ids=[course.id],
        date_from=TODAY - timedelta(days=1),
        date_to=TODAY + timedelta(days=1),
    )
    assert len(rows) == 9
    assert [row.checked_at for row in rows if row.checked_at] == [checked_at]


async def 이미_멈춘_코스는_시각이_안_바뀐다(h: Harness) -> None:
    course, doses = antibiotic(TODAY, TODAY + timedelta(days=1))
    await h.seed(course, doses)
    await h.store.stop_course(child_id=h.child_id, schedule_id=course.id, stopped_at=NOW)

    again = await h.store.stop_course(
        child_id=h.child_id, schedule_id=course.id, stopped_at=NOW + timedelta(minutes=2)
    )

    [kept] = await h.store.courses_overlapping(child_id=h.child_id, date_from=TODAY, date_to=TODAY)
    assert again == "already"
    assert kept.stopped_at == NOW


async def 처음부터_멈춰_있던_코스도_already(h: Harness) -> None:
    stopped_at = NOW - timedelta(days=1)
    course, doses = antibiotic(
        TODAY, TODAY + timedelta(days=1), status="stopped", stopped_at=stopped_at
    )
    await h.seed(course, doses)

    result = await h.store.stop_course(child_id=h.child_id, schedule_id=course.id, stopped_at=NOW)

    [kept] = await h.store.courses_overlapping(child_id=h.child_id, date_from=TODAY, date_to=TODAY)
    assert result == "already"
    assert kept.stopped_at == stopped_at


async def 없는_코스는_missing(h: Harness) -> None:
    course, doses = antibiotic(TODAY, TODAY)
    await h.seed(course, doses)
    other = course_with_doses("시럽", (BREAKFAST,), TODAY, TODAY)[0]

    result = await h.store.stop_course(child_id=h.child_id, schedule_id=other.id, stopped_at=NOW)

    assert result == "missing"
    assert [c.id for c in await h.store.active_courses(child_id=h.child_id, today=TODAY)] == [
        course.id
    ]


async def pattern_은_남은_회차의_시각(h: Harness) -> None:
    # 어제까지 지난 회차가 있어도 pattern 은 오늘 이후 회차 행의 시각이다
    course, doses = antibiotic(TODAY - timedelta(days=2), TODAY + timedelta(days=2))
    await h.seed(course, doses)

    [read] = await h.store.active_courses(child_id=h.child_id, today=TODAY)

    remaining = {dose.scheduled_time for dose in doses if dose.dose_date >= TODAY}
    assert {slot.scheduled_time for slot in read.pattern} == remaining
    assert [slot.timing_anchor for slot in read.pattern] == ["breakfast", "lunch", "dinner"]


async def 남은_회차가_없으면_마지막_날의_시각(h: Harness) -> None:
    course, doses = antibiotic(TODAY - timedelta(days=3), TODAY - timedelta(days=1))
    await h.seed(course, doses)

    [read] = await h.store.courses_overlapping(
        child_id=h.child_id, date_from=TODAY - timedelta(days=1), date_to=TODAY
    )

    assert read.pattern == PATTERN


CASES: list[Scenario] = [
    코스_칸을_그대로_읽는다,
    회차를_체크_칸까지_그대로_읽는다,
    doses_는_기간으로_자르고_날짜_시각_순이다,
    ends_on_이_지난_코스는_active_에_없고_overlapping_에는_있다,
    시작_전_코스는_active_에_없다,
    오늘이_ends_on_이면_아직_진행_중,
    KST_00시30분에_어제_끝난_코스는_진행_중이_아니다,
    중단하면_active_에서_빠지고_시각이_남는다,
    이미_멈춘_코스는_시각이_안_바뀐다,
    처음부터_멈춰_있던_코스도_already,
    없는_코스는_missing,
    pattern_은_남은_회차의_시각,
    남은_회차가_없으면_마지막_날의_시각,
]
