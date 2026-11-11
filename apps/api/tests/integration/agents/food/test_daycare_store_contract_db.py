"""급식 저장소 계약 시나리오를 DB 어댑터(`DbDaycareMeals`)로 돌린다. Postgres 필요.

시나리오는 tests/unit/agents/food/daycare_store_contract.py. 여기서 실패하고 단위 쪽은 통과하면
두 저장소가 다르게 동작하는 것이다 — 그 모듈 머리말의 규칙대로 처리한다(메모리를 DB 에 맞춘다).
메뉴 · 날짜는 전부 가짜 값이다.
"""

from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.food.store import DaycareLookupError, DaycareMealRow
from app.domains.child.models import ParentChildRelation
from app.domains.child.repository import create_child
from app.domains.daycare_meal.agent_reader import DbDaycareMeals
from app.domains.daycare_meal.models import DaycareMeal, DaycareOrigin, DaycareSlot
from app.domains.identity.models import Parent
from tests.unit.agents.food.daycare_store_contract import CASES, Scenario


def db_store(session: AsyncSession) -> DbDaycareMeals:
    @asynccontextmanager
    async def open_session():
        yield session  # 테스트 트랜잭션 안에서 읽는다 — 바깥 rollback 이 지운다

    return DbDaycareMeals(open_session)


class DbHarness:
    def __init__(self, session: AsyncSession, child_id: UUID, other_child_id: UUID) -> None:
        self._session = session
        self.child_id = child_id
        self.other_child_id = other_child_id
        self.store = db_store(session)

    async def seed(self, *rows: DaycareMealRow) -> None:
        for row in rows:
            self._session.add(
                DaycareMeal(
                    id=row.id,
                    child_id=row.child_id,
                    serve_date=row.serve_date,
                    meal_slot=DaycareSlot(row.meal_slot),
                    menu_keys=list(row.menu_keys),
                    amount_factor=Decimal(str(row.amount_factor)),
                    amount_known=row.amount_known,
                    caregiver_checked=row.caregiver_checked,
                    origin=DaycareOrigin(row.origin),
                )
            )
        await self._session.flush()


@pytest.fixture
async def harness(session: AsyncSession) -> DbHarness:
    children = []
    for nickname in ("첫째", "둘째"):
        owner = Parent()  # 보호자 한 명에 아이 한 명이다(uq_parent_child_parent_id)
        session.add(owner)
        await session.flush()
        children.append(
            await create_child(
                session,
                owner_parent_id=owner.id,
                nickname=nickname,
                birth_date=date(2023, 1, 1),
                relation=ParentChildRelation.MOTHER,
            )
        )
    return DbHarness(session, children[0].id, children[1].id)


@pytest.mark.parametrize("scenario", CASES, ids=lambda scenario: scenario.__name__)
async def test_급식_저장소_계약_DB(harness: DbHarness, scenario: Scenario) -> None:
    await scenario(harness)


@pytest.mark.parametrize("call", ["rows", "has_rows"])
async def test_읽기_실패는_빈_값이_아니라_DaycareLookupError_다(call: str) -> None:
    @asynccontextmanager
    async def broken():
        raise ConnectionError("db")
        yield

    store = DbDaycareMeals(broken)

    with pytest.raises(DaycareLookupError):
        if call == "rows":
            await store.rows(
                child_id=uuid4(), date_from=date(2026, 10, 1), date_to=date(2026, 10, 2)
            )
        else:
            await store.has_rows(child_id=uuid4())
