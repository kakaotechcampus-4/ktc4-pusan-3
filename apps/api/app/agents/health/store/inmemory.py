"""Health 포트의 인메모리 구현. 테스트·eval이 DB 없이 HealthPorts를 주입할 때 쓴다.

실패를 흉내 낼 때는 `fail=True` — 빈 결과와 구분해야 한다 (루트 §2).

🚨 **동의는 기본값이 없다.** 어느 아이가 동의했는지 생성자에서 명시해야 하고, 적지 않은 아이는
`KeyError` 다. 기본값이 True 면 동의를 철회한 아이의 건강정보가 테스트에서 아무렇지 않게 읽힌다.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Literal
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from app.agents.health.store.ports import (
    ChildBasic,
    DoseSlot,
    HealthObservation,
    HealthPorts,
    HealthSafetyEntry,
    MealRecord,
    MedicationCourse,
    MedicationDose,
    SafetyLookupError,
)

_KST = ZoneInfo("Asia/Seoul")


class InMemoryProfile:
    def __init__(self, birth_dates: dict[UUID, date] | None = None) -> None:
        self._birth_dates = dict(birth_dates or {})

    async def birth_date(self, *, child_id: UUID) -> date:
        return self._birth_dates[child_id]


class InMemoryConsent:
    def __init__(self, granted: dict[UUID, bool]) -> None:
        self._granted = dict(granted)

    async def child_health_granted(self, *, child_id: UUID) -> bool:
        if child_id not in self._granted:
            raise KeyError(f"동의 여부를 지정하지 않은 아이: {child_id}")
        return self._granted[child_id]


class InMemoryHealthSafety:
    def __init__(self, entries: Sequence[HealthSafetyEntry] = (), *, fail: bool = False) -> None:
        self._entries = list(entries)
        self._fail = fail
        self.calls = 0  # 동의가 없거나 안 쓰는 라벨이면 아예 읽지 않는지 테스트가 본다

    async def health_safety(self, *, child_id: UUID) -> list[HealthSafetyEntry]:
        self.calls += 1
        if self._fail:
            raise SafetyLookupError("health_safety 조회 실패 (테스트 주입)")
        return list(self._entries)


class InMemoryHealthMemory:
    def __init__(
        self, observations: Sequence[HealthObservation] = (), meals: Sequence[MealRecord] = ()
    ) -> None:
        self._observations = list(observations)
        self._meals = list(meals)
        self.calls = 0  # 동의가 없으면 아예 읽지 않는지 테스트가 본다
        self.requested: list[tuple[date, date]] = []  # 요청한 구간

    async def health_observations(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[HealthObservation]:
        self.calls += 1
        self.requested.append((date_from, date_to))
        # 기간 기록은 구간과 겹치면 읽는다
        return [
            row
            for row in self._observations
            if row.observed_to >= date_from and row.observed_from <= date_to
        ]

    async def meals(self, *, child_id: UUID, date_from: date, date_to: date) -> list[MealRecord]:
        self.calls += 1
        return [row for row in self._meals if date_from <= row.observed_on <= date_to]


class InMemoryChildBasic:
    def __init__(self, basics: dict[UUID, ChildBasic]) -> None:
        self._basics = dict(basics)

    async def basic(self, *, child_id: UUID) -> ChildBasic:
        return self._basics[child_id]


class InMemoryMedicationStore:
    """복약 읽기와 중단 쓰기를 한 객체가 한다. 아이 하나 몫이라 `child_id` 는 보지 않는다.

    `.writes` 가 쓰기를 센다 — 묻는 run · 초안 · "먹였어" 뒤에 0 인지 테스트가 본다.
    DB 어댑터(연결 이슈)가 같은 테스트를 통과해야 한다.
    """

    def __init__(
        self, courses: Sequence[MedicationCourse] = (), doses: Sequence[MedicationDose] = ()
    ) -> None:
        self._courses = {course.id: course for course in courses}
        self._doses = list(doses)
        self.writes: list[tuple[UUID, datetime]] = []  # (schedule_id, stopped_at) — 중단만 쓴다
        self.stopped: dict[UUID, datetime] = {}  # 이 저장소로 멈춘 코스의 중단 시각

    async def active_courses(self, *, child_id: UUID, today: date) -> list[MedicationCourse]:
        return [
            course
            for course in self._courses.values()
            if course.status == "active" and course.starts_on <= today <= course.ends_on
        ]

    async def courses_overlapping(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[MedicationCourse]:
        return [
            course
            for course in self._courses.values()
            if course.starts_on <= date_to and course.ends_on >= date_from
        ]

    async def doses(
        self, *, schedule_ids: Sequence[UUID], date_from: date, date_to: date
    ) -> list[MedicationDose]:
        wanted = set(schedule_ids)
        rows = [
            row
            for row in self._doses
            if row.schedule_id in wanted and date_from <= row.dose_date <= date_to
        ]
        return sorted(rows, key=lambda row: (row.dose_date, row.scheduled_time))

    async def stop_course(
        self, *, schedule_id: UUID, stopped_at: datetime
    ) -> Literal["stopped", "already", "missing"]:
        course = self._courses.get(schedule_id)
        if course is None:
            return "missing"
        if course.status == "stopped":
            return "already"
        self._courses[schedule_id] = replace(course, status="stopped", stopped_at=stopped_at)
        self.stopped[schedule_id] = stopped_at
        self.writes.append((schedule_id, stopped_at))
        return "stopped"


def course_with_doses(
    title: str,
    pattern: Sequence[DoseSlot],
    starts_on: date,
    ends_on: date,
    *,
    dosage: str | None = None,
    indication_text: str | None = None,
    storage: Literal["room", "fridge"] | None = None,
    med_form: Literal["liquid", "powder", "tablet", "ointment", "drops"] | None = None,
    note: str | None = None,
    status: Literal["active", "stopped"] = "active",
    checked: Collection[tuple[date, time]] | Mapping[tuple[date, time], datetime] = (),
    course_id: UUID | None = None,
    symptom_codes: Sequence[str] = (),
    stopped_at: datetime | None = None,
) -> tuple[MedicationCourse, list[MedicationDose]]:
    """코스와 그 회차 행. 기간 × 패턴만큼 행을 만든다 — 백엔드 제출이 만들 모양과 같다.

    `checked` 에 든 (날짜, 시각) 회차는 체크된 행이다. 값을 주지 않으면(집합) 체크 시각은 회차
    예정 시각 5분 뒤다 — 몰아서 누르면 달라지는 경우는 시각을 직접 준다(딕셔너리).
    """
    course = MedicationCourse(
        id=course_id or uuid4(),
        title=title,
        dosage=dosage,
        indication_text=indication_text,
        storage=storage,
        med_form=med_form,
        starts_on=starts_on,
        ends_on=ends_on,
        status=status,
        note=note,
        pattern=tuple(pattern),
        symptom_codes=tuple(symptom_codes),
        stopped_at=stopped_at,
    )
    if isinstance(checked, Mapping):
        checked_at = dict(checked)
    else:
        checked_at = {
            key: datetime.combine(*key, tzinfo=_KST) + timedelta(minutes=5) for key in checked
        }
    doses = [
        MedicationDose(
            id=uuid5(course.id, f"{day}-{slot.scheduled_time}"),
            schedule_id=course.id,
            dose_date=day,
            scheduled_time=slot.scheduled_time,
            checked_at=checked_at.get((day, slot.scheduled_time)),
        )
        for day in (starts_on + timedelta(days=n) for n in range((ends_on - starts_on).days + 1))
        for slot in pattern
    ]
    return course, doses


def in_memory_ports(
    child_id: UUID,
    birth_date: date,
    *,
    consent: bool,
    safety: InMemoryHealthSafety | None = None,
    memory: InMemoryHealthMemory | None = None,
    medication: InMemoryMedicationStore | None = None,
    nickname: str = "별이",
) -> HealthPorts:
    """아이 하나 몫의 포트 묶음. `consent` 는 꼭 적는다 — 기본값이 없다."""
    store = medication or InMemoryMedicationStore()
    return HealthPorts(
        profile=InMemoryProfile({child_id: birth_date}),
        consent=InMemoryConsent({child_id: consent}),
        safety=safety or InMemoryHealthSafety(),
        memory=memory or InMemoryHealthMemory(),
        medication=store,
        medication_writer=store,
        child=InMemoryChildBasic({child_id: ChildBasic(nickname, birth_date)}),
    )
