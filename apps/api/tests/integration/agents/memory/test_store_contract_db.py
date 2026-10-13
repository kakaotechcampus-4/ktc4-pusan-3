"""Memory store 계약 시나리오를 DbMemoryStore 로 돌린다 (#257). Postgres 필요.

시나리오는 tests/unit/agents/memory/store_contract.py. 여기서 실패하고 단위 쪽은 통과하면
두 store 가 다르게 동작하는 것이다 — 그 모듈 머리말의 규칙대로 처리한다.
"""

from datetime import date
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.memory.context import AgentContext
from app.agents.memory.drafts import EventDraft
from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ObservationActivity,
    ObservationEducation,
    ObservationFood,
    ObservationHealth,
    ObservationRoutine,
)
from app.domains.memory.store.db_store import DbMemoryStore
from app.domains.schedule.models import EventCreatedBy
from app.domains.schedule.repository import create_event
from tests.unit.agents.memory.store_contract import CASES, KST, NOW, Scenario

_MODELS = {
    "food": ObservationFood,
    "health": ObservationHealth,
    "education": ObservationEducation,
    "activity": ObservationActivity,
    "routine": ObservationRoutine,
}


class DbHarness:
    def __init__(self, session: AsyncSession, *, child_id: UUID, parent_id: UUID) -> None:
        self._session = session
        self.store = DbMemoryStore(session, child_id=child_id)
        self.context = AgentContext(
            child_id=child_id, source_writer=parent_id, now=NOW, timezone=KST, store=self.store
        )

    async def seed_event(self, draft: EventDraft) -> str:
        row = await create_event(
            self._session,
            child_id=self.context.child_id,
            title=draft.title,
            event_type=draft.event_type,
            starts_at=draft.starts_at,
            ends_at=draft.ends_at,
            all_day=draft.all_day,
            category=draft.category,
            created_by=EventCreatedBy.CAREGIVER,
        )
        return str(row.id)

    async def stored(self, domain: str, observation_id: str) -> tuple[str, str]:
        model = _MODELS[domain]
        row = await self._session.scalar(
            select(model)
            .where(model.id == UUID(observation_id))
            .execution_options(populate_existing=True)
        )
        assert row is not None
        return str(row.status), row.raw_text


@pytest.fixture
async def harness(session: AsyncSession) -> DbHarness:
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="계약", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return DbHarness(session, child_id=child.id, parent_id=owner.id)


@pytest.mark.parametrize(("scenario", "params"), CASES)
async def test_store_계약_DB(
    harness: DbHarness, scenario: Scenario, params: dict[str, Any]
) -> None:
    await scenario(harness, **params)
