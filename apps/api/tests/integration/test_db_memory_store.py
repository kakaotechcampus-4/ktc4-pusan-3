"""DbMemoryStore child_id isolation tests.

child_a's observation id로 child_b의 store에서 get/update/delete하면 차단되는지 검증한다.
"""

from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common.datetime_rules import DateRange
from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.store.db_store import DbMemoryStore


@pytest.fixture
async def family(session: AsyncSession):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child_a = Child(owner_parent_id=owner.id, nickname="아이A", birth_date=date(2023, 1, 1))
    child_b = Child(owner_parent_id=owner.id, nickname="아이B", birth_date=date(2023, 6, 1))
    session.add_all([child_a, child_b])
    await session.flush()
    return owner, child_a, child_b


@pytest.fixture
async def obs_of_a(session: AsyncSession, family: tuple) -> str:
    """child_a의 food observation을 만들고 id를 반환한다."""
    owner, child_a, _ = family
    store_a = DbMemoryStore(session, child_id=child_a.id)
    row = await store_a.create_observation(
        domain="food",
        child_id=child_a.id,
        source_writer=owner.id,
        raw_text="사과 잘 먹었어",
        observed_on=date(2026, 10, 1),
        observed_range=DateRange(start=date(2026, 10, 1), end=date(2026, 10, 2)),
        fields={"subject": "사과", "polarity": 1, "confidence_source": "parent_direct"},
    )
    return row.id


class TestChildIdIsolation:
    async def test_get_observation_of_other_child_returns_none(
        self, session: AsyncSession, family: tuple, obs_of_a: str
    ) -> None:
        _, _, child_b = family
        store_b = DbMemoryStore(session, child_id=child_b.id)
        result = await store_b.get_observation(domain="food", observation_id=obs_of_a)
        assert result is None

    async def test_update_observation_of_other_child_returns_none(
        self, session: AsyncSession, family: tuple, obs_of_a: str
    ) -> None:
        _, _, child_b = family
        store_b = DbMemoryStore(session, child_id=child_b.id)
        result = await store_b.update_observation(
            domain="food", observation_id=obs_of_a, fields={"subject": "바나나"}
        )
        assert result is None

    async def test_delete_observation_of_other_child_returns_false(
        self, session: AsyncSession, family: tuple, obs_of_a: str
    ) -> None:
        _, _, child_b = family
        store_b = DbMemoryStore(session, child_id=child_b.id)
        result = await store_b.delete_observation(domain="food", observation_id=obs_of_a)
        assert result is False

    async def test_own_child_get_works(
        self, session: AsyncSession, family: tuple, obs_of_a: str
    ) -> None:
        """Sanity check: child_a's store can still read its own observation."""
        _, child_a, _ = family
        store_a = DbMemoryStore(session, child_id=child_a.id)
        result = await store_a.get_observation(domain="food", observation_id=obs_of_a)
        assert result is not None
        assert result.id == obs_of_a
