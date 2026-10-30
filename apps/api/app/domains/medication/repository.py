"""복약 두 표의 쿼리. commit 은 부르는 쪽이 한다.

읽는 쿼리는 `populate_existing` 이다 — 같은 세션에 이미 올라온 행이 있어도 DB 의 지금 값을 읽는다.
진행 중 판정은 `models.in_progress` 한 곳에서만 하고, 여기서 같은 조건을 다시 쓰지 않는다.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal

from sqlalchemy import ColumnElement, and_, delete, exists, or_, select, update
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
    await add_doses(session, schedule_id=course.id, doses=doses)
    return course


async def add_doses(
    session: AsyncSession, *, schedule_id: uuid.UUID, doses: Sequence[NewDose]
) -> None:
    """코스에 회차 행을 더한다 (flush 까지). 같은 날 같은 알림 시각이 있으면 `IntegrityError`."""
    session.add_all(
        MedicationDose(
            schedule_id=schedule_id,
            dose_date=d.dose_date,
            timing_anchor=d.timing_anchor,
            offset_minutes=d.offset_minutes,
            fixed_time=d.fixed_time,
        )
        for d in doses
    )
    await session.flush()


async def has_active_duplicate(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    title: str,
    starts_on: date,
    ends_on: date,
    exclude_id: uuid.UUID | None = None,
) -> bool:
    """같은 아이 · 같은 약 이름 · 같은 기간의 `active` 코스가 있는가. 멈춘 코스는 세지 않는다.

    약 이름은 받은 그대로 비교한다 (공백 · 대소문자 정규화 없음). 기간은 겹치기만 해서는 안 되고
    시작일과 끝일이 모두 같아야 한다. 동시에 온 두 요청은 이 검사만으로는 막지 못한다.
    `exclude_id` 는 수정할 때 자기 자신을 빼는 데 쓴다.
    """
    stmt = select(MedicationSchedule.id).where(
        MedicationSchedule.child_id == child_id,
        MedicationSchedule.title == title,
        MedicationSchedule.starts_on == starts_on,
        MedicationSchedule.ends_on == ends_on,
        MedicationSchedule.status == MedicationStatus.ACTIVE,
    )
    if exclude_id is not None:
        stmt = stmt.where(MedicationSchedule.id != exclude_id)
    return await session.scalar(stmt.limit(1)) is not None


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


async def find_dose(
    session: AsyncSession, *, child_id: uuid.UUID, dose_id: uuid.UUID
) -> MedicationDose | None:
    """그 아이의 멈추지 않은(`active`) 코스에 속한 회차. 다른 아이 · 멈춘 코스의 회차는 `None` 이다.

    다른 아이의 회차는 있는지도 알리지 않는다. 멈춘 코스의 회차는 체크 · 해제할 수 없다.
    날짜는 보지 않는다 — 마지막 날이 지난 코스의 회차도 체크 · 해제된다. 조회가 그 코스를 싣지
    않아도 id 를 아는 화면(마지막 날 밤에 열어 둔 화면)은 자정을 넘어서도 쓸 수 있다.

    회차 행을 잠그고 읽는다 — 체크하는 동안 수정이 그 회차를 지우면(미체크 미래 회차) 읽은 뒤
    쓸 행이 사라진다. 잠그면 수정의 DELETE 가 기다렸다가, 체크된 회차는 건너뛴다
    (`checked_at IS NULL`). 반대로 수정이 먼저 지웠다면 여기서 기다렸다가 `None` 을 받는다.
    """
    stmt = (
        select(MedicationDose)
        .join(MedicationSchedule, MedicationSchedule.id == MedicationDose.schedule_id)
        .where(
            MedicationDose.id == dose_id,
            MedicationSchedule.child_id == child_id,
            MedicationSchedule.status == MedicationStatus.ACTIVE,
        )
        .with_for_update(of=MedicationDose)
        .execution_options(populate_existing=True)
    )
    return await session.scalar(stmt)


async def write_dose_check(
    session: AsyncSession, *, dose_id: uuid.UUID, checked_at: datetime | None
) -> None:
    """체크 시각을 쓴다. `None` 이면 비운다(해제).

    체크는 **아직 비어 있을 때만** 쓴다 — 이미 체크된 회차에 또 체크해도 처음 시각이 남는다.
    조건을 UPDATE 에 걸어서 동시에 온 두 요청도 먼저 쓴 쪽이 이긴다.
    """
    stmt = update(MedicationDose).where(MedicationDose.id == dose_id)
    if checked_at is not None:
        stmt = stmt.where(MedicationDose.checked_at.is_(None))
    await session.execute(stmt.values(checked_at=checked_at))


async def find_course(
    session: AsyncSession, *, child_id: uuid.UUID, course_id: uuid.UUID
) -> MedicationSchedule | None:
    """그 아이의 코스를 잠그고 읽는다 (상태는 보지 않는다 — 멈춘 코스인지는 부르는 쪽이 본다).

    다른 아이의 코스는 있는지도 알리지 않고 `None` 이다. 수정 중에 다른 요청이 같은 코스의 회차를
    지우고 만드는 일이 겹치지 않도록 행을 잠근다.
    """
    stmt = (
        select(MedicationSchedule)
        .where(MedicationSchedule.id == course_id, MedicationSchedule.child_id == child_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return await session.scalar(stmt)


async def has_kept_outside(
    session: AsyncSession,
    *,
    schedule_id: uuid.UUID,
    starts_on: date,
    ends_on: date,
    today: date,
    now_time: time,
) -> bool:
    """수정 뒤에도 남는 회차가 [starts_on, ends_on] 밖에 있는가 — 있으면 그 기간으로 못 바꾼다.

    남는 회차 = 체크한 회차 + 이미 지난 회차(체크하지 않았어도 지우지 않는다 — 복용 기록이다).
    그대로 두면 `dose_date ∈ [starts_on, ends_on]` 불변식(medication-tables-v1.md §5)이 깨진다.
    아직 안 온 미체크 회차는 수정이 어차피 지우므로 세지 않는다.
    """
    kept = or_(
        MedicationDose.checked_at.is_not(None),
        MedicationDose.dose_date < today,
        and_(MedicationDose.dose_date == today, MedicationDose.scheduled_time <= now_time),
    )
    outside = or_(MedicationDose.dose_date < starts_on, MedicationDose.dose_date > ends_on)
    stmt = select(exists().where(MedicationDose.schedule_id == schedule_id, kept, outside))
    return bool(await session.scalar(stmt))


async def delete_future_unchecked(
    session: AsyncSession, *, schedule_id: uuid.UUID, today: date, now_time: time
) -> None:
    """미체크이면서 예정 시각이 **지금 이후**인 회차를 지운다.

    체크한 회차와 이미 지난 회차는 지우지 않는다 — 복용 기록이다. 오늘은 `now_time` 보다 늦은
    시각만 지운다(정각은 지난 것으로 본다).
    """
    await session.execute(
        delete(MedicationDose).where(
            MedicationDose.schedule_id == schedule_id,
            MedicationDose.checked_at.is_(None),
            or_(
                MedicationDose.dose_date > today,
                and_(MedicationDose.dose_date == today, MedicationDose.scheduled_time > now_time),
            ),
        )
    )


async def existing_dose_keys(
    session: AsyncSession, *, schedule_id: uuid.UUID
) -> set[tuple[date, time]]:
    """코스에 이미 있는 (날짜, 알림 시각). 같은 키를 또 넣으면 UNIQUE 에 걸린다."""
    rows = await session.execute(
        select(MedicationDose.dose_date, MedicationDose.scheduled_time).where(
            MedicationDose.schedule_id == schedule_id
        )
    )
    return {(row.dose_date, row.scheduled_time) for row in rows}
