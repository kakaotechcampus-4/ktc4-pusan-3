"""복약 두 표의 쿼리. commit 은 부르는 쪽이 한다.

읽는 쿼리는 `populate_existing` 이다 — 같은 세션에 이미 올라온 행이 있어도 DB 의 지금 값을 읽는다.
진행 중 판정은 `models.in_progress` 한 곳에서만 하고, 여기서 같은 조건을 다시 쓰지 않는다.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal

from sqlalchemy import ColumnElement, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.medication.models import (
    MedicationCreatedBy,
    MedicationDose,
    MedicationForm,
    MedicationSchedule,
    MedicationSource,
    MedicationStatus,
    MedicationStorage,
    TimingAnchor,
)


@dataclass(frozen=True)
class NewDose:
    """저장할 회차 1행. 날짜가 붙은 행이고, `scheduled_time` 은 DB 가 계산한다."""

    dose_date: date
    timing_anchor: TimingAnchor
    offset_minutes: int
    fixed_time: time | None


def overlaps(date_from: date, date_to: date) -> ColumnElement[bool]:
    """기간이 [date_from, date_to] 와 겹치는 코스 — status 는 보지 않는다."""
    return (MedicationSchedule.starts_on <= date_to) & (MedicationSchedule.ends_on >= date_from)


async def add_course(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    title: str,
    starts_on: date,
    ends_on: date,
    source: MedicationSource,
    created_by: MedicationCreatedBy,
    doses: Sequence[NewDose],
    dosage: str | None = None,
    indication_text: str | None = None,
    symptom_codes: Sequence[str] = (),
    storage: MedicationStorage | None = None,
    med_form: MedicationForm | None = None,
    note: str | None = None,
) -> MedicationSchedule:
    """코스 1행과 회차 행을 넣는다 (flush 까지). 받은 값을 고치지 않는다 — 검증은 서비스 몫이다.

    CHECK · UNIQUE · FK 위반은 `IntegrityError` 로 그대로 올라간다. 코스만 남는 일이 없도록
    부르는 쪽이 예외에서 rollback 한다.
    """
    course = MedicationSchedule(
        child_id=child_id,
        title=title,
        dosage=dosage,
        indication_text=indication_text,
        symptom_codes=list(symptom_codes),
        storage=storage,
        med_form=med_form,
        starts_on=starts_on,
        ends_on=ends_on,
        note=note,
        created_by=created_by,
        source=source,
    )
    session.add(course)
    await session.flush()
    session.add_all(
        MedicationDose(
            schedule_id=course.id,
            dose_date=d.dose_date,
            timing_anchor=d.timing_anchor,
            offset_minutes=d.offset_minutes,
            fixed_time=d.fixed_time,
        )
        for d in doses
    )
    await session.flush()
    return course


async def has_active_duplicate(
    session: AsyncSession, *, child_id: uuid.UUID, title: str, starts_on: date, ends_on: date
) -> bool:
    """같은 아이 · 같은 약 이름 · 같은 기간의 `active` 코스가 있는가. 멈춘 코스는 세지 않는다.

    약 이름은 받은 그대로 비교한다 (공백 · 대소문자 정규화 없음). 기간은 겹치기만 해서는 안 되고
    시작일과 끝일이 모두 같아야 한다. 동시에 온 두 요청은 이 검사만으로는 막지 못한다.
    """
    found = await session.scalar(
        select(MedicationSchedule.id)
        .where(
            MedicationSchedule.child_id == child_id,
            MedicationSchedule.title == title,
            MedicationSchedule.starts_on == starts_on,
            MedicationSchedule.ends_on == ends_on,
            MedicationSchedule.status == MedicationStatus.ACTIVE,
        )
        .limit(1)
    )
    return found is not None


async def list_courses(
    session: AsyncSession, *, child_id: uuid.UUID, where: ColumnElement[bool]
) -> list[MedicationSchedule]:
    """그 아이의 코스. 순서는 시작일 → 만든 순으로 고정한다 — 읽을 때마다 같은 순서로."""
    stmt = (
        select(MedicationSchedule)
        .where(MedicationSchedule.child_id == child_id, where)
        .order_by(
            MedicationSchedule.starts_on, MedicationSchedule.created_at, MedicationSchedule.id
        )
        .execution_options(populate_existing=True)
    )
    return list((await session.scalars(stmt)).all())


async def list_doses(
    session: AsyncSession,
    *,
    schedule_ids: Sequence[uuid.UUID],
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[MedicationDose]:
    """코스들의 회차 행. 날짜를 주면 `dose_date` 로 자르고, (날짜, 예정 시각) 순이다."""
    if not schedule_ids:
        return []
    stmt = select(MedicationDose).where(MedicationDose.schedule_id.in_(schedule_ids))
    if date_from is not None:
        stmt = stmt.where(MedicationDose.dose_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(MedicationDose.dose_date <= date_to)
    stmt = stmt.order_by(MedicationDose.dose_date, MedicationDose.scheduled_time).execution_options(
        populate_existing=True
    )
    return list((await session.scalars(stmt)).all())


async def stop_schedule(
    session: AsyncSession, *, child_id: uuid.UUID, schedule_id: uuid.UUID, stopped_at: datetime
) -> Literal["stopped", "already", "missing"]:
    """`active` 코스를 `stopped` 로 바꾸고 `updated_at` 에 중단 시각을 쓴다.

    `updated_at` 이 곧 중단 시각이라 run 의 now 로 직접 쓴다. 비워 두면 `onupdate` 가 DB 의 now() 로
    채워서 run 의 시각과 어긋난다. 같은 이유로 멈춘 코스에 다른 UPDATE 를 내면 중단 시각이 밀린다.
    다른 아이의 코스는 있는지도 알리지 않고 `missing` 이다. 이미 멈춘 코스는 아무것도 바꾸지 않는다.
    """
    stopped = await session.scalar(
        update(MedicationSchedule)
        .where(
            MedicationSchedule.id == schedule_id,
            MedicationSchedule.child_id == child_id,
            MedicationSchedule.status == MedicationStatus.ACTIVE,
        )
        .values(status=MedicationStatus.STOPPED, updated_at=stopped_at)
        .returning(MedicationSchedule.id)
    )
    if stopped is not None:
        return "stopped"
    exists = await session.scalar(
        select(MedicationSchedule.id).where(
            MedicationSchedule.id == schedule_id, MedicationSchedule.child_id == child_id
        )
    )
    return "already" if exists is not None else "missing"
