"""같은 run 의 Memory store 를 도메인 Agent 의 기억 포트로 읽는다.

방금 저장한 관찰이 같은 run 의 추천 근거로 잡혀야 혼합형이 성립한다 (루트 §4).
store 종류는 모른다 — MemoryStore Protocol 로만 읽는다. 운영에서는 러너가 넘긴 DbMemoryStore,
테스트 · eval 에서는 InMemoryStore 다.

🚨 DbMemoryStore 를 읽으면 러너 세션 하나를 Memory 와 같이 쓴다. AsyncSession 은 쿼리 둘을 동시에
   못 돌리므로, 이 포트를 읽는 task 둘이 동시에 돌면 깨진다. Food 포트를 DB 로 붙일 때는 이 다리를
   쓰지 않고 포트 호출마다 짧은 트랜잭션으로 읽는다(10-05 목표, 러너 task 별 세션 작업). Memory 는
   도메인 단계 전에 commit 되므로 따로 연 세션에서도 방금 저장한 관찰이 보인다.

Agent 패키지는 서로 import 하지 않는다. Memory store 와 Food · Activity
포트를 둘 다 아는 곳은 pipeline · entrypoint 층이라 여기 둔다.
Curator 는 이 다리를 쓰지 않고 DbCuratorStore 를 직접 쓴다 (domains/memory/curator/trigger.py).

profile_affinity 는 Memory store 에 없다. affinities() 는 빈 목록이다.
"""

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


def _uuid(row_id: str) -> UUID:
    """InMemory id(observation_food-1)는 UUID 가 아니다. 같은 id 는 늘 같은 UUID 로 바꾼다."""
    try:
        return UUID(row_id)
    except ValueError:
        return uuid5(NAMESPACE_URL, f"inmemory:{row_id}")


async def _rows(
    store: MemoryStore,
    domain: str,
    *,
    child_id: UUID,
    date_from: date,
    date_to: date,
    statuses: frozenset[str],
) -> list[ObservationRow]:
    rows = await store.query_observations(
        domain=domain, child_id=child_id, date_from=date_from, date_to=date_to
    )
    # status가 없으면 읽지 않는다
    return [row for row in rows if row.fields.get("status") in statuses]


class StoreFoodMemory:
    """`FoodMemoryReader` 구현. `observation_food`를 같은 run 의 store 에서 읽는다."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def affinities(self, *, child_id: UUID) -> list[AffinityRow]:
        return []

    async def observations(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[FoodObservation]:
        rows = await _rows(
            self._store,
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


class StoreActivityMemory:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def affinities(self, *, child_id: UUID) -> list[AffinityRow]:
        return []

    async def observations(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[ActivityObservation]:
        rows = await _rows(
            self._store,
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
