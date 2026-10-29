"""복약 코스 등록 — 검증 · 중복 · 회차 펼치기 · 저장을 한 번에 한다 (#577).

HTTP 를 모른다. 틀린 입력은 `CourseInvalid`, 이미 있는 코스는 `CourseAlreadyExists` 로 올리고
라우터가 400 · 409 로 바꾼다. 오류가 나면 저장하지 않는다. commit 은 부르는 쪽이 한다.

보호자가 카드에서 확인하고 제출한 것만 들어온다(게이트 ㉡) — Agent 는 초안까지만 만든다.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.medication_draft import OFFSET_MAX, OFFSET_MIN, scheduled_time_of
from app.domains.medication.models import (
    MedicationCreatedBy,
    MedicationForm,
    MedicationSchedule,
    MedicationSource,
    MedicationStorage,
    TimingAnchor,
)
from app.domains.medication.repository import NewDose, add_course, has_active_duplicate
from app.rules.medication_course import course_field_errors, dose_dates


class CourseInvalid(Exception):
    """틀린 칸이 있다. `fields` 는 400 `detail.fields` 에 그대로 실린다."""

    def __init__(self, fields: list[str]) -> None:
        super().__init__(", ".join(fields))
        self.fields = fields


class CourseAlreadyExists(Exception):
    """같은 아이 · 같은 약 이름 · 같은 기간의 진행 중인 코스가 이미 있다."""


@dataclass(frozen=True)
class DosePattern:
    """하루 시각 패턴 한 칸. 날짜가 없고, 기간의 날마다 펼쳐진다."""

    timing_anchor: TimingAnchor
    offset_minutes: int
    fixed_time: time | None


@dataclass(frozen=True)
class CourseInput:
    title: str
    starts_on: date
    ends_on: date
    source: MedicationSource
    doses: Sequence[DosePattern]
    dosage: str | None = None
    indication_text: str | None = None
    symptom_codes: Sequence[str] = ()
    storage: MedicationStorage | None = None
    med_form: MedicationForm | None = None
    note: str | None = None


def _doses_are_valid(patterns: Sequence[DosePattern]) -> bool:
    """`DoseDraft` 와 같은 규칙 — fixed 조합, offset 범위, 같은 알림 시각 금지."""
    times: set[time] = set()
    for p in patterns:
        if (p.timing_anchor == TimingAnchor.FIXED) != (p.fixed_time is not None):
            return False
        if p.timing_anchor == TimingAnchor.FIXED and p.offset_minutes != 0:
            return False
        if not OFFSET_MIN <= p.offset_minutes <= OFFSET_MAX:
            return False
        times.add(scheduled_time_of(p.timing_anchor, p.offset_minutes, p.fixed_time))
    return len(times) == len(patterns)


async def create_course(
    session: AsyncSession, *, child_id: uuid.UUID, data: CourseInput, today: date
) -> MedicationSchedule:
    """코스 1행과 회차 행을 저장한다. `today` 는 KST 오늘이다.

    검사 순서는 칸 → 기간 전체가 과거인지 → 중복이다. 칸이 틀렸으면 중복은 묻지 않는다.
    """
    fields = course_field_errors(
        title=data.title,
        dosage=data.dosage,
        indication_text=data.indication_text,
        note=data.note,
        starts_on=data.starts_on,
        ends_on=data.ends_on,
        dose_count=len(data.doses),
    )
    if "doses" not in fields and not _doses_are_valid(data.doses):
        fields.append("doses")
    if fields:
        raise CourseInvalid(fields)
    # provisional — 기간 전체가 과거인 코스를 거절할지는 미정이다 (#563)
    if data.ends_on < today:
        raise CourseInvalid(["schedule.ends_on"])
    if await has_active_duplicate(
        session,
        child_id=child_id,
        title=data.title,
        starts_on=data.starts_on,
        ends_on=data.ends_on,
    ):
        raise CourseAlreadyExists

    return await add_course(
        session,
        child_id=child_id,
        title=data.title,
        dosage=data.dosage or None,  # 빈 문자열은 null 로
        indication_text=data.indication_text or None,
        symptom_codes=data.symptom_codes,
        storage=data.storage,
        med_form=data.med_form,
        note=data.note or None,
        starts_on=data.starts_on,
        ends_on=data.ends_on,
        source=data.source,
        created_by=MedicationCreatedBy.CAREGIVER,
        doses=[
            NewDose(day, p.timing_anchor, p.offset_minutes, p.fixed_time)
            for day in dose_dates(data.starts_on, data.ends_on, today=today)
            for p in data.doses
        ],
    )
