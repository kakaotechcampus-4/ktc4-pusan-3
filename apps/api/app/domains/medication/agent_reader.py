"""Health 의 복약 포트를 `medication_schedule` · `medication_dose` 로 구현한다.

`MedicationReader` 와 `MedicationWriter` 를 한 객체(`DbMedicationStore`)가 한다.
Agent 는 DB 에 직접 닿지 않는다. 러너가 이 어댑터를 만들어 entrypoint 로 넘긴다. 포트 모양
(`MedicationCourse` 등)은 agents 가 정한 계약이라 여기서 import 한다 — 안전 어댑터
(`domains/safety/agent_reader.py`)와 같은 예외 경로다(apps/api/CLAUDE.md "저장 계약").

- 🚨 진행 중 판정은 `models.in_progress(today)` 하나만 쓴다. 조회 API 와 같은 조건이어야 화면과
  Agent 가 같은 코스를 "복용 중" 이라 말한다. `today` 는 KST 날짜를 받는다 — DB 의 CURRENT_DATE 는
  UTC 라 자정 부근에 하루 어긋난다.
- 🚨 읽기에 실패하면 예외를 그대로 올린다. 빈 목록이면 응급 카드에 "복용 중인 약 없음" 이 찍힌다.
- 🚨 Agent 가 쓰는 것은 확인받은 중단 하나(`stop_course`)다. 다른 쓰기 메서드를 두지 않는다.
  다른 아이의 코스는 읽히지도 멈춰지지도 않는다 — 멈추려 하면 `missing` 이다.
- 중단 시각은 시간대가 붙은 값만 받는다. 시간대 없는 값은 asyncpg 가 프로세스 TZ 로 해석해서 같은
  시각이 환경마다 다르게 저장된다.
- `MedicationCourse.pattern` 은 기준일 다음 날부터 첫 날의 회차 행에서 읽는다(`pattern_of`).
  수정으로 앞으로의 시각이 바뀐 코스가 지난 회차의 옛 시각으로 나오지 않게 하려는 것이다.
- 호출마다 짧은 세션을 연다. run 세션을 도메인 task 들이 같이 타지 않게.
"""

from collections import defaultdict
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import date, datetime, time
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.health.store.ports import DoseSlot, MedicationCourse, MedicationDose
from app.domains.medication import models as tables
from app.domains.medication import repository
from app.domains.medication.models import MedicationSchedule, MedicationStatus, in_progress

# 호출마다 새 세션을 여는 함수. 러너가 `background_session` 으로 만든다
SessionOpener = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def pattern_of(rows: Sequence[tables.MedicationDose], *, basis: date) -> tuple[DoseSlot, ...]:
    """하루 시각 패턴. 기준일 **다음 날부터** 첫 날의 회차 행에서 읽고, 예정 시각 순이다.

    기준일(오늘)은 건너뛴다 — 오늘 고친 코스는 지난 회차(옛 시각)가 남고 남은 회차만 새 시각이라
    오늘 행을 섞으면 칸이 늘어난다. 다음 날이 없으면(오늘 끝나거나 이미 끝났다) 마지막 날의 행에서
    읽는다. 행이 없으면 빈 튜플이다.
    """
    if not rows:
        return ()
    later = [row.dose_date for row in rows if row.dose_date > basis]
    day = min(later) if later else max(row.dose_date for row in rows)
    slots: dict[time, DoseSlot] = {}
    for row in rows:
        if row.dose_date != day:
            continue
        scheduled = _scheduled_time(row)
        slots.setdefault(
            scheduled,
            DoseSlot(
                timing_anchor=row.timing_anchor.value,
                offset_minutes=row.offset_minutes,
                fixed_time=row.fixed_time,
                scheduled_time=scheduled,
            ),
        )
    return tuple(slots[at] for at in sorted(slots))


def _scheduled_time(row: tables.MedicationDose) -> time:
    if row.scheduled_time is None:
        # CHECK 가 NULL 이 되는 입력을 막는다 — 여기까지 오면 어댑터나 표의 버그다
        raise ValueError("회차 행의 scheduled_time 이 비어 있다")
    return row.scheduled_time


def _course(schedule: MedicationSchedule, pattern: tuple[DoseSlot, ...]) -> MedicationCourse:
    stopped = schedule.status == MedicationStatus.STOPPED
    return MedicationCourse(
        id=schedule.id,
        title=schedule.title,
        dosage=schedule.dosage,
        indication_text=schedule.indication_text,
        storage=schedule.storage.value if schedule.storage is not None else None,
        med_form=schedule.med_form.value if schedule.med_form is not None else None,
        starts_on=schedule.starts_on,
        ends_on=schedule.ends_on,
        status=schedule.status.value,
        note=schedule.note,
        pattern=pattern,
        symptom_codes=tuple(schedule.symptom_codes),
        stopped_at=schedule.updated_at if stopped else None,
    )


def _dose(row: tables.MedicationDose) -> MedicationDose:
    return MedicationDose(
        id=row.id,
        schedule_id=row.schedule_id,
        dose_date=row.dose_date,
        scheduled_time=_scheduled_time(row),
        checked_at=row.checked_at,
    )


class DbMedicationStore:
    """`MedicationReader` 와 `MedicationWriter` 를 한 객체가 한다."""

    def __init__(self, open_session: SessionOpener) -> None:
        self._open = open_session

    async def active_courses(self, *, child_id: UUID, today: date) -> list[MedicationCourse]:
        return await self._courses(child_id, where=in_progress(today), basis=today)

    async def courses_overlapping(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[MedicationCourse]:
        # 이 메서드는 오늘을 받지 않는다. 호출부가 모두 date_to 를 오늘로 넘겨서 같은 뜻이다
        return await self._courses(
            child_id, where=repository.overlaps(date_from, date_to), basis=date_to
        )

    async def doses(
        self, *, schedule_ids: Sequence[UUID], date_from: date, date_to: date
    ) -> list[MedicationDose]:
        async with self._open() as session:
            rows = await repository.list_doses(
                session, schedule_ids=schedule_ids, date_from=date_from, date_to=date_to
            )
        return [_dose(row) for row in rows]

    async def stop_course(
        self, *, child_id: UUID, schedule_id: UUID, stopped_at: datetime
    ) -> Literal["stopped", "already", "missing"]:
        if stopped_at.tzinfo is None:
            raise ValueError("stopped_at 은 시간대가 붙은 값이어야 한다")
        async with self._open() as session:
            outcome = await repository.stop_schedule(
                session, child_id=child_id, schedule_id=schedule_id, stopped_at=stopped_at
            )
            await session.commit()
        return outcome

    async def _courses(self, child_id: UUID, *, where, basis: date) -> list[MedicationCourse]:
        async with self._open() as session:
            schedules = await repository.list_courses(session, child_id=child_id, where=where)
            rows = await repository.list_doses(session, schedule_ids=[s.id for s in schedules])
        by_schedule: dict[UUID, list[tables.MedicationDose]] = defaultdict(list)
        for row in rows:
            by_schedule[row.schedule_id].append(row)
        return [
            _course(schedule, pattern_of(by_schedule[schedule.id], basis=basis))
            for schedule in schedules
        ]
