"""memory_bridge 가 DbMemoryStore 를 읽을 때도 status 를 단위 테스트와 같이 거르는지 (#257).

단위 쪽은 tests/unit/agents/test_memory_bridge.py (InMemoryStore).
"""

from datetime import date, timedelta
from uuid import UUID

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common.datetime_rules import DateRange
from app.agents.memory_bridge import StoreActivityMemory, StoreFoodMemory
from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ObservationActivity,
    ObservationFood,
    ObservationStatus,
)
from app.domains.memory.store.db_store import DbMemoryStore

DAY = date(2026, 10, 1)


@pytest.fixture
async def family(session: AsyncSession):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="다리", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return owner, child


async def _save(
    store: DbMemoryStore, owner: Parent, child: Child, domain: str, subject: str
) -> str:
    fields = {"subject": subject, "polarity": 1, "confidence_source": "parent_direct"}
    if domain == "activity":
        fields["activity"] = f"{subject} 놀이"
    row = await store.create_observation(
        domain=domain,
        child_id=child.id,
        source_writer=owner.id,
        raw_text="원문",
        observed_on=DAY,
        observed_range=DateRange(start=DAY, end=DAY + timedelta(days=1)),
        fields=fields,
    )
    return row.id


async def _four_statuses(
    session: AsyncSession,
    store: DbMemoryStore,
    owner: Parent,
    child: Child,
    domain: str,
    model: type,
) -> dict[str, str]:
    """active · stand_alone · inactive · deleted 관찰을 하나씩. subject 가 곧 status 이름이다."""
    names = ("active", "stand_alone", "inactive", "deleted")
    ids = {name: await _save(store, owner, child, domain, name) for name in names}
    for name, status in (
        ("stand_alone", ObservationStatus.STAND_ALONE),
        ("inactive", ObservationStatus.INACTIVE),
    ):
        await session.execute(
            update(model).where(model.id == UUID(ids[name])).values(status=status)
        )
    assert await store.delete_observation(domain=domain, observation_id=ids["deleted"])
    return ids


async def test_DB_store_에서도_Food_는_stand_alone_까지만_읽는다(
    session: AsyncSession, family: tuple
) -> None:
    owner, child = family
    store = DbMemoryStore(session, child_id=child.id)
    ids = await _four_statuses(session, store, owner, child, "food", ObservationFood)

    got = await StoreFoodMemory(store).observations(child_id=child.id, date_from=DAY, date_to=DAY)

    assert {o.subject for o in got} == {"active", "stand_alone"}
    # DB id 는 UUID 라 그대로 간다 — InMemory id 처럼 uuid5 로 바꾸지 않는다
    assert {o.id for o in got} == {UUID(ids["active"]), UUID(ids["stand_alone"])}


async def test_DB_store_에서도_Activity_는_active_만_읽는다(
    session: AsyncSession, family: tuple
) -> None:
    owner, child = family
    store = DbMemoryStore(session, child_id=child.id)
    ids = await _four_statuses(session, store, owner, child, "activity", ObservationActivity)

    got = await StoreActivityMemory(store).observations(
        child_id=child.id, date_from=DAY, date_to=DAY
    )

    assert [(o.id, o.subject) for o in got] == [(UUID(ids["active"]), "active")]
