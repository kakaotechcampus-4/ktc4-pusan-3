"""InMemory 전용. 같은 run의 Memory store를 도메인 Agent의 기억 포트로 읽는다.

방금 저장한 관찰이 같은 run 의 추천 근거로 잡혀야 혼합형이 성립한다.
DB 가 붙으면 도메인 Agent 는 백엔드 조회 함수로 읽는다. 이 파일은 그 전까지,
그리고 테스트 · eval 이 DB 없이 이 흐름을 볼 때 쓴다.

Agent 패키지는 서로 import 하지 않는다. Memory store 와 Food · Activity
포트를 둘 다 아는 곳은 pipeline · entrypoint 층이라 여기 둔다.

profile_affinity 는 Memory store 에 없다. affinities() 는 빈 목록이다.
"""

from datetime import date
from uuid import NAMESPACE_URL, UUID, uuid5

from app.agents.activity.store.ports import ActivityObservation, AffinityRecord
from app.agents.common.evidence import AffinityRow
from app.agents.food.store.ports import FoodObservation
from app.agents.memory.store.ports import MemoryStore, ObservationRow

# 검색에 남는 status. stand_alone 은 Correction once_only("이번만 그랬어요") 라
# Curator 집계에서만 빠진다 (data_model §observation). inactive · deleted 는 읽지 않는다
FOOD_STATUSES = frozenset({"active", "stand_alone"})
# CHECK(추가) 이슈 본문은 Activity 도 active + stand_alone 이라고 적었다. Activity 포트
#   (ActivityObservation 주석)와 activity-agent-v1 A-10 은 stand_alone 을 근거에서 뺀다
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
            )
            for row in rows
        ]


class StoreActivityMemory:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def affinities(self, *, child_id: UUID) -> list[AffinityRecord]:
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
                updated_at=row.created_at,
            )
            for row in rows
        ]
