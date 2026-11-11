"""복약 저장소 계약 시나리오를 in-memory 저장소로 돌린다. DB 를 쓰지 않는다.

시나리오는 medication_store_contract.py. 같은 목록을 DB 어댑터로 돌리는 쪽은
tests/integration/agents/health/test_medication_store_contract_db.py.
"""

from collections.abc import Sequence

import pytest

from app.agents.health.store.inmemory import InMemoryMedicationStore
from app.agents.health.store.ports import MedicationCourse, MedicationDose
from tests.unit.agents.health.medication_store_contract import CASES, Scenario
from tests.unit.agents.health.support import CHILD


class InMemoryHarness:
    def __init__(self) -> None:
        self.child_id = CHILD
        self.store = InMemoryMedicationStore()

    async def seed(self, course: MedicationCourse, doses: Sequence[MedicationDose]) -> None:
        self.store._courses[course.id] = course
        self.store._doses.extend(doses)


@pytest.mark.parametrize("scenario", CASES, ids=lambda scenario: scenario.__name__)
async def test_복약_저장소_계약(scenario: Scenario) -> None:
    await scenario(InMemoryHarness())
