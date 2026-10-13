"""Memory store 계약 시나리오를 InMemoryStore 로 돌린다. DB 를 쓰지 않는다 (#79 단위 job).

시나리오는 store_contract.py. 같은 목록을 DbMemoryStore 로 돌리는 쪽은
tests/integration/agents/memory/test_store_contract_db.py.
"""

from typing import Any
from uuid import UUID

import pytest

from app.agents.memory.context import AgentContext
from app.agents.memory.drafts import EventDraft
from app.agents.memory.store import InMemoryStore
from tests.unit.agents.memory.store_contract import CASES, KST, NOW, Scenario


class InMemoryHarness:
    def __init__(self) -> None:
        self.store = InMemoryStore(now=NOW)
        self.context = AgentContext(
            child_id=UUID(int=1), source_writer=UUID(int=2), now=NOW, timezone=KST, store=self.store
        )

    async def seed_event(self, draft: EventDraft) -> str:
        row = await self.store.create_event(
            child_id=self.context.child_id,
            title=draft.title,
            starts_at=draft.starts_at,
            ends_at=draft.ends_at,
            all_day=draft.all_day,
            fields={"event_type": draft.event_type, "category": draft.category},
        )
        return row.id

    async def stored(self, domain: str, observation_id: str) -> tuple[str, str]:
        row = self.store._observations[observation_id]
        return row.fields["status"], row.raw_text


@pytest.fixture
def harness() -> InMemoryHarness:
    return InMemoryHarness()


@pytest.mark.parametrize(("scenario", "params"), CASES)
async def test_store_계약(
    harness: InMemoryHarness, scenario: Scenario, params: dict[str, Any]
) -> None:
    await scenario(harness, **params)
