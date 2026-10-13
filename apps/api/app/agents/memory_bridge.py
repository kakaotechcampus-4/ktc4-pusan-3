"""같은 run 의 Memory store 를 도메인 Agent 의 기억 포트로 읽는다.

방금 저장한 관찰이 같은 run 의 추천 근거로 잡혀야 혼합형이 성립한다 (루트 §4).
store 종류는 모른다 — MemoryStore Protocol 로만 읽는다.

읽을 store 를 받는 길이 둘이다.
    StoreFoodMemory(store)          이미 열린 store 를 그대로 읽는다. 테스트 · eval 의 InMemoryStore
    StoreFoodMemory(opener=opener)  호출마다 opener 로 store 를 새로 연다. 운영에서 러너가 준다

🚨 운영에서는 opener 로 간다. 러너의 run 세션(Memory 가 쓰는 세션)을 도메인 task 둘이 같이
   타면 AsyncSession 이 쿼리 둘을 동시에 못 돌려 깨진다. 러너는 호출마다 짧은 세션을 따로 열어
   주고(#257), Memory 는 도메인 단계 전에 commit 되므로 따로 연 세션에서도 방금 저장한
   관찰이 보인다.

Agent 패키지는 서로 import 하지 않는다. Memory store 와 Food · Activity
포트를 둘 다 아는 곳은 pipeline · entrypoint 층이라 여기 둔다.
Curator 는 이 다리를 쓰지 않고 DbCuratorStore 를 직접 쓴다 (domains/memory/curator/trigger.py).

profile_affinity 는 Memory store 에 없다. affinities() 는 빈 목록이다.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import date
from uuid import NAMESPACE_URL, UUID, uuid5

from app.agents.activity.store.ports import ActivityObservation
from app.agents.common.evidence import AffinityRow
from app.agents.food.store.ports import FoodObservation
from app.agents.memory.store.ports import MemoryStore, ObservationRow

# 검색에 남는 status. stand_alone 은 Correction once_only("이번만 그랬어요") 라
# Curator 집계에서만 빠진다 (data_model §observation). inactive · deleted 는 읽지 않는다
FOOD_STATUSES = frozenset({"active", "stand_alone"})
# Activity는 active만 읽는다. Activity 포트(ActivityObservation 주석)와 activity-agent-v1
#   A-10 이 stand_alone을 근거에서 뺀다 — "이번만 그랬어요" 로 고친 기록이 근거로 돌아오지 않게
ACTIVITY_STATUSES = frozenset({"active"})


# 호출 한 번에 쓸 store 를 여는 함수. 러너가 짧은 DB 세션으로 만든다
StoreOpener = Callable[[], AbstractAsyncContextManager[MemoryStore]]


def same_store(store: MemoryStore) -> StoreOpener:
    """이미 열린 store 를 그대로 돌려주는 opener. 세션이 없는 InMemoryStore 용."""

    @asynccontextmanager
    async def opener() -> AsyncIterator[MemoryStore]:
        yield store

    return opener


class _StoreReader:
    def __init__(self, store: MemoryStore | None = None, *, opener: StoreOpener | None = None):
        if (store is None) == (opener is None):
            raise TypeError("store 와 opener 중 하나만 넘긴다")
        self._open: StoreOpener = opener or same_store(store)  # type: ignore[arg-type]


def _uuid(row_id: str) -> UUID:
    """InMemory id(observation_food-1)는 UUID 가 아니다. 같은 id 는 늘 같은 UUID 로 바꾼다."""
    try:
        return UUID(row_id)
    except ValueError:
        return uuid5(NAMESPACE_URL, f"inmemory:{row_id}")


async def _rows(
    open_store: StoreOpener,
    domain: str,
    *,
    child_id: UUID,
    date_from: date,
    date_to: date,
    statuses: frozenset[str],
) -> list[ObservationRow]:
    async with open_store() as store:
        rows = await store.query_observations(
            domain=domain, child_id=child_id, date_from=date_from, date_to=date_to
        )
    # status가 없으면 읽지 않는다
    return [row for row in rows if row.fields.get("status") in statuses]


class StoreFoodMemory(_StoreReader):
    """`FoodMemoryReader` 구현. `observation_food`를 Memory store 에서 읽는다."""

    async def affinities(self, *, child_id: UUID) -> list[AffinityRow]:
        return []

    async def observations(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[FoodObservation]:
        rows = await _rows(
            self._open,
            "food",
            child_id=child_id,
            date_from=date_from,
            date_to=date_to,
            statuses=FOOD_STATUSES,
        )
        return [
            FoodObservation(
                id=_uuid(row.id),
                child_id=child_id,
                observed_on=row.observed_on,
                subject=row.fields["subject"],
                amount_text=row.fields.get("amount"),
                polarity=int(row.fields.get("polarity") or 0),
                updated_at=row.created_at,
                confidence_source=row.fields.get("confidence_source"),
                source_notice_id=row.fields.get("source_notice_id"),
            )
            for row in rows
        ]


class StoreActivityMemory(_StoreReader):
    """`ActivityMemoryReader` 구현. `observation_activity`를 Memory store 에서 읽는다."""

    async def affinities(self, *, child_id: UUID) -> list[AffinityRow]:
        return []

    async def observations(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[ActivityObservation]:
        rows = await _rows(
            self._open,
            "activity",
            child_id=child_id,
            date_from=date_from,
            date_to=date_to,
            statuses=ACTIVITY_STATUSES,
        )
        return [
            ActivityObservation(
                id=_uuid(row.id),
                child_id=child_id,
                observed_on=row.observed_on,
                subject=row.fields["subject"],
                activity=row.fields["activity"],
                polarity=int(row.fields.get("polarity") or 0),
            )
            for row in rows
        ]
