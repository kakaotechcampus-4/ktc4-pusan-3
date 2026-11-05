"""복약 저장소 계약 시나리오를 DB 어댑터로 돌린다. Postgres 필요.

시나리오는 tests/unit/agents/health/medication_store_contract.py. 여기서 실패하고 단위 쪽은
통과하면 두 저장소가 다르게 동작하는 것이다 — 그 모듈 머리말의 규칙대로 처리한다.
"""

from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.health.store.ports import MedicationCourse
from app.agents.health.store.ports import MedicationDose as DoseRow
from app.domains.child.models import Child, ParentChildRelation
from app.domains.child.repository import create_child
from app.domains.identity.models import Parent
from app.domains.medication.agent_reader import DbMedicationStore
from app.domains.medication.models import (
    MedicationDose,
    MedicationForm,
    MedicationSchedule,
    MedicationStatus,
    MedicationStorage,
    TimingAnchor,
)
from tests.unit.agents.health.medication_store_contract import CASES, Scenario


async def new_child(session: AsyncSession, nickname: str = "첫째") -> Child:
    owner = Parent()
    session.add(owner)
    await session.flush()
    return await create_child(
        session,
        owner_parent_id=owner.id,
        nickname=nickname,
        birth_date=date(2023, 1, 1),
        relation=ParentChildRelation.MOTHER,
    )


def db_store(session: AsyncSession) -> DbMedicationStore:
    @asynccontextmanager
    async def open_session():
        yield session  # 테스트 트랜잭션 안에서 읽는다 — 바깥 rollback 이 지운다

    return DbMedicationStore(open_session)


async def seed_course(
    session: AsyncSession, child: Child, course: MedicationCourse, doses: Sequence[DoseRow]
) -> None:
    """포트 모양의 코스와 회차를 실제 두 표에 넣는다. 멈춘 코스는 `updated_at` 이 중단 시각이다."""
    values = {}
    if course.stopped_at is not None:
        values["updated_at"] = course.stopped_at
    session.add(
        MedicationSchedule(
            id=course.id,
            child_id=child.id,
            title=course.title,
            dosage=course.dosage,
            indication_text=course.indication_text,
            symptom_codes=list(course.symptom_codes),
            storage=MedicationStorage(course.storage) if course.storage else None,
            med_form=MedicationForm(course.med_form) if course.med_form else None,
            starts_on=course.starts_on,
            ends_on=course.ends_on,
            status=MedicationStatus(course.status),
            note=course.note,
            created_by="caregiver",
            **values,
        )
    )
    await session.flush()
    slots = {slot.scheduled_time: slot for slot in course.pattern}
    session.add_all(
        MedicationDose(
            id=dose.id,
            schedule_id=course.id,
            dose_date=dose.dose_date,
            timing_anchor=TimingAnchor(slots[dose.scheduled_time].timing_anchor),
            offset_minutes=slots[dose.scheduled_time].offset_minutes,
            fixed_time=slots[dose.scheduled_time].fixed_time,
            checked_at=dose.checked_at,
        )
        for dose in doses
    )
    await session.flush()


class DbHarness:
    def __init__(self, session: AsyncSession, child: Child) -> None:
        self._session = session
        self._child = child
        self.child_id = child.id
        self.store = db_store(session)

    async def seed(self, course: MedicationCourse, doses: Sequence[DoseRow]) -> None:
        await seed_course(self._session, self._child, course, doses)


@pytest.fixture
async def child(session: AsyncSession) -> Child:
    return await new_child(session)


@pytest.mark.parametrize("scenario", CASES, ids=lambda scenario: scenario.__name__)
async def test_복약_저장소_계약(session: AsyncSession, child: Child, scenario: Scenario) -> None:
    await scenario(DbHarness(session, child))
