"""복약 코스 등록 — 검증 · 중복 · 회차 펼치기 · 저장을 한 번에 한다 (#577).

HTTP 를 모른다. 틀린 입력은 `CourseInvalid`, 이미 있는 코스는 `CourseAlreadyExists` 로 올리고
라우터가 400 · 409 로 바꾼다. 오류가 나면 저장하지 않는다. commit 은 부르는 쪽이 한다.

보호자가 카드에서 확인하고 제출한 것만 들어온다(게이트 ㉡) — Agent 는 초안까지만 만든다.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.medication_draft import OFFSET_MAX, OFFSET_MIN, scheduled_time_of
from app.domains.medication.models import (
    MedicationCreatedBy,
    MedicationDose,
    MedicationForm,
    MedicationSchedule,
    MedicationSource,
    MedicationStatus,
    MedicationStorage,
    TimingAnchor,
    in_progress,
)
from app.domains.medication.repository import (
    NewDose,
    add_course,
    add_doses,
    delete_future_unchecked,
    existing_dose_keys,
    find_course,
    find_dose,
    has_active_duplicate,
    has_checked_outside,
    list_courses,
    list_doses,
    write_dose_check,
)
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


@dataclass(frozen=True, kw_only=True)
class CourseFields:
    """코스의 내용과 기간, 하루 시각 패턴. 등록 · 수정이 같이 쓴다."""

    title: str
    starts_on: date
    ends_on: date
    doses: Sequence[DosePattern]
    dosage: str | None = None
    indication_text: str | None = None
    symptom_codes: Sequence[str] = ()
    storage: MedicationStorage | None = None
    med_form: MedicationForm | None = None
    note: str | None = None


@dataclass(frozen=True, kw_only=True)
class CourseInput(CourseFields):
    """등록 입력. 출처는 등록 때만 정해진다 — 수정은 `CourseFields` 만 받는다."""

    source: MedicationSource


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


COURSE_LOCK_NS = 2
"""pg_advisory_xact_lock(int4, int4) 의 첫 번째 인자.
1 은 Curator 가 쓴다 (memory/curator/trigger.py)."""


def course_lock_key(child_id: uuid.UUID) -> int:
    return child_id.int % (2**31)


async def _lock_child_courses(session: AsyncSession, child_id: uuid.UUID) -> None:
    """같은 아이의 코스 등록 · 수정을 한 줄로 세운다. 트랜잭션이 끝나면 풀린다.

    중복 검사와 INSERT 사이에 다른 요청이 끼면 같은 코스가 둘 생긴다 — 코드의 검사만으로는 두
    트랜잭션이 서로의 commit 전 쓰기를 못 본다. 아이별이라 다른 아이의 요청은 기다리지 않는다.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :key)"),
        {"ns": COURSE_LOCK_NS, "key": course_lock_key(child_id)},
    )


def _validate(data: CourseFields, *, today: date) -> None:
    """칸 → 기간 전체가 과거인지 순으로 본다. 틀리면 `CourseInvalid`."""
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


async def create_course(
    session: AsyncSession, *, child_id: uuid.UUID, data: CourseInput, today: date
) -> MedicationSchedule:
    """코스 1행과 회차 행을 저장한다. `today` 는 KST 오늘이다.

    검사 순서는 칸 → 기간 전체가 과거인지 → 중복이다. 칸이 틀렸으면 중복은 묻지 않는다.
    """
    _validate(data, today=today)
    await _lock_child_courses(session, child_id)  # 틀린 요청은 잠금 없이 위에서 걸러진다
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


@dataclass(frozen=True)
class CourseView:
    """화면에 내보낼 코스 한 건 — 알림 시각과 **오늘까지**의 회차를 같이 든다."""

    course: MedicationSchedule
    notice_times: list[str]  # 하루에 울릴 시각 "HH:MM" 오름차순 — 마지막 날의 패턴
    doses: list[MedicationDose]  # 오늘까지의 회차, (날짜, 시각) 순


def _notice_times(doses: Sequence[MedicationDose]) -> list[str]:
    """하루에 울릴 시각 — **가장 마지막 날**의 회차 시각이다.

    수정하면 이미 지난 날의 회차는 옛 패턴으로 남는다. 모든 회차에서 모으면 옛 시각이 섞이므로,
    언제나 새 패턴으로 다시 만들어지는 마지막 날을 본다. `doses` 는 (날짜, 시각) 순이다.
    """
    if not doses:
        return []
    last = doses[-1].dose_date
    return [f"{d.scheduled_time:%H:%M}" for d in doses if d.dose_date == last]


async def course_views(
    session: AsyncSession, courses: Sequence[MedicationSchedule], *, today: date
) -> list[CourseView]:
    """코스마다 알림 시각과 오늘까지의 회차를 붙인다. 코스 순서를 그대로 지킨다.

    회차는 한 번에 읽는다. 알림 시각은 아직 오지 않은 날의 회차까지 보고 정하므로 날짜를 자르지 않고
    읽은 뒤 오늘까지만 남긴다 — 내일부터 먹는 코스도 알림 시각은 안다(마지막 날의 패턴).
    """
    rows = await list_doses(session, schedule_ids=[c.id for c in courses])
    by_course: dict[uuid.UUID, list[MedicationDose]] = {c.id: [] for c in courses}
    for dose in rows:
        by_course[dose.schedule_id].append(dose)
    return [
        CourseView(
            course=c,
            notice_times=_notice_times(by_course[c.id]),
            doses=[d for d in by_course[c.id] if d.dose_date <= today],
        )
        for c in courses
    ]


async def list_in_progress(
    session: AsyncSession, *, child_id: uuid.UUID, today: date
) -> list[CourseView]:
    """그 아이의 진행 중인 코스. 멈춘 · 끝난 · 시작 전 코스는 싣지 않는다 (알려진 한계)."""
    courses = await list_courses(session, child_id=child_id, where=in_progress(today))
    return await course_views(session, courses, today=today)


class DoseNotFound(Exception):
    """그 아이의 진행 중인 코스에 그런 회차가 없다 (없는 회차 · 남의 아이 · 멈춘 코스)."""


class DoseNotDue(Exception):
    """아직 오지 않은 날의 회차다. 체크도 해제도 못 한다."""


async def set_dose_checked(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    dose_id: uuid.UUID,
    checked: bool,
    today: date,
    now: datetime,
) -> MedicationDose:
    """회차를 체크하거나 해제한다. `today` 는 KST 오늘, `now` 는 체크 시각이다.

    되돌릴 수 있어서 승인 게이트가 아니다. 같은 상태를 두 번 보내도 결과가 같고, 이미 체크된
    회차에 또 체크하면 처음 시각이 남는다. 찾지 못함 → 아직 오지 않음 순서로 검사한다.
    """
    dose = await find_dose(session, child_id=child_id, dose_id=dose_id)
    if dose is None:
        raise DoseNotFound
    if dose.dose_date > today:
        raise DoseNotDue
    await write_dose_check(session, dose_id=dose.id, checked_at=now if checked else None)
    await session.refresh(dose)
    return dose


KST = ZoneInfo("Asia/Seoul")


class CourseNotFound(Exception):
    """그 아이의 진행 중인 코스가 아니다 (없는 코스 · 남의 아이 · 멈춘 코스)."""


async def revise_course(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    course_id: uuid.UUID,
    data: CourseFields,
    today: date,
    now: datetime,
) -> MedicationSchedule:
    """코스를 보호자가 확인한 최종 상태로 고친다. `today` 는 KST 오늘, `now` 는 지금이다.

    복용 기록은 지우지 않는다. 체크한 회차와 이미 지난 회차는 그대로 두고, 미체크이면서 예정 시각이
    지금 이후인 회차만 지운 뒤 새 패턴으로 다시 만든다(id 가 바뀐다). 다시 만들 때 지금 이전의
    시각은 만들지 않는다 — 등록이 오늘 회차를 통째로 만드는 것과 다르다. 체크한 회차가 새 기간
    밖에 남게 되면 거절한다. 검사 순서는 대상 → 칸 → 체크한 회차 → 중복이다.
    """
    course = await find_course(session, child_id=child_id, course_id=course_id)
    if course is None or course.status != MedicationStatus.ACTIVE:
        raise CourseNotFound
    _validate(data, today=today)
    if await has_checked_outside(
        session, schedule_id=course.id, starts_on=data.starts_on, ends_on=data.ends_on
    ):
        raise CourseInvalid(["schedule.starts_on", "schedule.ends_on"])
    if await has_active_duplicate(
        session,
        child_id=child_id,
        title=data.title,
        starts_on=data.starts_on,
        ends_on=data.ends_on,
        exclude_id=course.id,
    ):
        raise CourseAlreadyExists

    course.title = data.title
    course.dosage = data.dosage or None  # 빈 문자열은 null 로
    course.indication_text = data.indication_text or None
    course.note = data.note or None
    course.symptom_codes = list(data.symptom_codes)
    course.storage = data.storage
    course.med_form = data.med_form
    course.starts_on = data.starts_on
    course.ends_on = data.ends_on

    now_time = now.astimezone(KST).time()
    await delete_future_unchecked(session, schedule_id=course.id, today=today, now_time=now_time)
    kept = await existing_dose_keys(session, schedule_id=course.id)
    fresh: list[NewDose] = []
    for day in dose_dates(data.starts_on, data.ends_on, today=today):
        for p in data.doses:
            when = scheduled_time_of(p.timing_anchor, p.offset_minutes, p.fixed_time)
            if (day, when) > (today, now_time) and (day, when) not in kept:
                fresh.append(NewDose(day, p.timing_anchor, p.offset_minutes, p.fixed_time))
    await add_doses(session, schedule_id=course.id, doses=fresh)
    await session.flush()
    return course
