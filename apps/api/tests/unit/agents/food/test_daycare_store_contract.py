"""급식 저장소 계약 시나리오를 메모리 구현으로 돌린다. DB 를 쓰지 않는다.

시나리오는 daycare_store_contract.py. 같은 목록을 DB 어댑터로 돌리는 쪽은
tests/integration/agents/food/test_daycare_store_contract_db.py.
"""

from uuid import UUID

import pytest

from app.agents.food.store import DaycareLookupError, DaycareMealRow, InMemoryDaycareMeals
from tests.unit.agents.food.daycare_store_contract import BASE_DAY, CASES, Scenario, meal
from tests.unit.agents.food.support import CHILD

OTHER = UUID(int=99)


class InMemoryHarness:
    def __init__(self) -> None:
        self.child_id = CHILD
        self.other_child_id = OTHER
        self.store = InMemoryDaycareMeals()

    async def seed(self, *rows: DaycareMealRow) -> None:
        for row in rows:
            self.store._rows[row.id] = row


@pytest.mark.parametrize("scenario", CASES, ids=lambda scenario: scenario.__name__)
async def test_급식_저장소_계약(scenario: Scenario) -> None:
    await scenario(InMemoryHarness())


async def test_읽기_실패를_주입하면_rows_와_has_rows_가_DaycareLookupError_다() -> None:
    h = InMemoryHarness()
    store = InMemoryDaycareMeals([meal(h, "밥")], fail_reads=True)

    with pytest.raises(DaycareLookupError):
        await store.has_rows(child_id=CHILD)
    with pytest.raises(DaycareLookupError):
        await store.rows(child_id=CHILD, date_from=BASE_DAY, date_to=BASE_DAY)
