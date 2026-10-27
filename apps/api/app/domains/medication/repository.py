"""복약 두 표의 쿼리. commit 은 부르는 쪽이 한다.

읽는 쿼리는 `populate_existing` 이다 — 같은 세션에 이미 올라온 행이 있어도 DB 의 지금 값을 읽는다.
진행 중 판정은 `models.in_progress` 한 곳에서만 하고, 여기서 같은 조건을 다시 쓰지 않는다.
"""

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal

from sqlalchemy import ColumnElement, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.medication.models import MedicationDose, MedicationSchedule, MedicationStatus


def overlaps(date_from: date, date_to: date) -> ColumnElement[bool]:
    """기간이 [date_from, date_to] 와 겹치는 코스 — status 는 보지 않는다."""
    return (MedicationSchedule.starts_on <= date_to) & (MedicationSchedule.ends_on >= date_from)


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

    `Timestamps.onupdate` 는 ORM flush 때만 채워지므로 시각을 직접 쓴다. 다른 아이의 코스는 있는지도
    알리지 않고 `missing` 이다. 이미 멈춘 코스는 아무것도 바꾸지 않는다.
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
